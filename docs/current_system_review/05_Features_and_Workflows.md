# 05 — Features and Workflows

**Generated**: 2026-04-09 | **Method**: Code-first inspection of services, tasks, state machine, orchestrators  
**Confidence**: High for core workflows and procurement request/recommendation/validation flows; Medium for posting and dedicated benchmarking internals

---

## 1. Invoice Ingestion

### Entry Points
- **Web Upload** (single): `POST /extraction/` → `DocumentUpload` created → Celery `run_extraction_task` dispatched
- **Bulk Upload**: `POST /extraction/bulk/` → `BulkUploadJob` created → `SystemBulkExtractionIntakeAgent` tracks lifecycle

### Key Business Rules
- File hashed on upload (`SHA-256` in `DocumentUpload.file_hash`)
- Uploaded to Azure Blob Storage at `input/{year}/{month}/{upload_id}_{filename}`
- Only PDF (and likely image) file types accepted (from `Document Intelligence` scope)
- Duplicate file detection via file hash before OCR
- Credit reserved on upload; consumed on OCR success; refunded on OCR failure

---

## 2. Extraction Pipeline (11 Stages)

Executed in `run_extraction_task` (Celery) via `ExtractionAdapter`:

| Stage | Service | Description |
|-------|---------|-------------|
| 1 | OCR | Azure Document Intelligence → structured text blocks |
| 2 | Category Classification | LLM call → `goods` / `service` / `travel` + confidence |
| 3 | Prompt Composition | `InvoicePromptComposer` → base + category + country overlay |
| 4 | LLM Extraction | `InvoiceExtractionAgent` → structured JSON (18+ fields) |
| 5 | Response Repair | `ResponseRepairService` → 5 deterministic rules pre-parser |
| 6 | Parse | `ParserService` → JSON → Python objects |
| 7 | Normalize | `NormalizationService` → dates (dateparser), amounts (Decimal), PO numbers |
| 8 | Validate | `ValidationService` → field presence, format, range checks → `decision_codes` |
| 9 | Duplicate Detection | `DuplicateDetectionService` → invoice_number + vendor + amount deduplication |
| 10 | Persist | `PersistenceService` → `Invoice` + `InvoiceLineItem` records created |
| 11 | Approval Gate | `ApprovalService` → auto-approve (confidence ≥ threshold) or PENDING_APPROVAL |

### Deterministic Response Repair Rules (5)

Applied before JSON parsing to fix common LLM extraction errors:

1. **Invoice number exclusion**: Strip IRN / CART Ref / Hotel Booking ID from invoice_number field
2. **Tax percentage recomputation**: Recompute tax_percentage from tax_amount / subtotal if missing or inconsistent
3. **Subtotal / line reconciliation**: Reconcile header subtotal against sum of line amounts
4. **Line-level tax allocation**: Distribute header tax across line items proportionally
5. **Travel line consolidation**: Merge fragmented hotel/travel lines into single line items

### Decision Codes

`decision_codes.py` in extraction app defines structured codes for validation outcomes (e.g. MISSING_FIELD, AMOUNT_MISMATCH, DUPLICATE_DETECTED). These drive human-readable review flags.

### Extraction Approval Gate

- `ExtractionApproval` model (in extraction app) tracks approval status per extraction run
- `EXTRACTION_AUTO_APPROVE_THRESHOLD = 0.85` (env-configurable)
- `EXTRACTION_AUTO_APPROVE_ENABLED = true` (env-configurable)
- When auto-approved: immediately creates APCase and dispatches reconciliation
- When pending: shows in AP Processor approval queue; field corrections tracked

---

## 3. Case Management

### APCase Creation
- Created by `CaseCreationService` after extraction approval
- `case_number` format: `AP-NNNNNN` (zero-padded sequential)
- One-to-one with `Invoice`; carries FK to `DocumentUpload`, `Vendor`, `PurchaseOrder`, `ReconciliationResult`, `ReviewAssignment`

### Case State Machine

Full transition table from `case_state_machine.py`:

```
NEW
  → INTAKE_IN_PROGRESS                         (SYSTEM)
  → EXTRACTION_IN_PROGRESS                     (SYSTEM, AGENT)
  → EXTRACTION_COMPLETED                       (SYSTEM)
  → PENDING_EXTRACTION_APPROVAL                (SYSTEM)
    → PATH_RESOLUTION_IN_PROGRESS              (SYSTEM, HUMAN)
    → EXTRACTION_COMPLETED (retry)             (SYSTEM, HUMAN)
    → REJECTED                                 (SYSTEM)
  → PATH_RESOLUTION_IN_PROGRESS                (SYSTEM)
    → TWO_WAY_IN_PROGRESS                      (DETERMINISTIC)
    → THREE_WAY_IN_PROGRESS                    (DETERMINISTIC)
    → NON_PO_VALIDATION_IN_PROGRESS            (DETERMINISTIC)
    → FAILED                                   (SYSTEM)
  [TWO_WAY/THREE_WAY]
    → EXCEPTION_ANALYSIS_IN_PROGRESS           (DETERMINISTIC, AGENT)
    → CLOSED (auto-close on MATCHED)           (DETERMINISTIC)
  [THREE_WAY only]
    → GRN_ANALYSIS_IN_PROGRESS                 (AGENT)
  [EXCEPTION_ANALYSIS]
    → READY_FOR_REVIEW                         (AGENT, DETERMINISTIC)
    → CLOSED (auto-close safe)                 (AGENT, DETERMINISTIC)
    → ESCALATED                                (AGENT)
  [REVIEW]
    READY_FOR_REVIEW → IN_REVIEW               (HUMAN)
    IN_REVIEW → REVIEW_COMPLETED               (HUMAN)
    IN_REVIEW → ESCALATED                      (HUMAN)
    IN_REVIEW → READY_FOR_REVIEW               (HUMAN — send back)
  [POST-REVIEW]
    REVIEW_COMPLETED → READY_FOR_APPROVAL      (SYSTEM)
    REVIEW_COMPLETED → CLOSED                  (HUMAN)
    REVIEW_COMPLETED → REJECTED                (HUMAN)
  [APPROVAL — future]
    READY_FOR_APPROVAL → APPROVAL_IN_PROGRESS  (SYSTEM)
    APPROVAL_IN_PROGRESS → READY_FOR_GL_CODING (HUMAN)
    APPROVAL_IN_PROGRESS → REJECTED            (HUMAN)
    APPROVAL_IN_PROGRESS → ESCALATED           (HUMAN)
  [GL CODING — future]
    → POSTED → CLOSED                          (inferred)
```

**Trigger types**: SYSTEM, DETERMINISTIC, AGENT, HUMAN — each state transition is typed

---

## 4. Reconciliation Engine

### Mode Resolution (3-tier cascade)
1. **Policy rules** (`ReconciliationPolicy`, ordered by priority): match on vendor/category/location/business_unit
2. **Heuristics** (in `ModeResolver`): service invoice → 2-way; stock/goods → 3-way
3. **Config default** (`ReconciliationConfig.default_reconciliation_mode`): platform default (THREE_WAY)

### Matching Flow

```
ReconciliationRunnerService.run(invoices)
  └── Per invoice:
       ├── POLookupService.lookup(po_number, vendor)
       ├── HeaderMatchService.match(invoice, po)
       ├── LineMatchService.match(invoice_lines, po_lines)
       ├── [if THREE_WAY] GRNMatchService.match(po, grns)
       ├── ToleranceEngine.classify(differences)
       │    ├── Strict band (2%/1%/1%): MATCHED or PARTIAL_MATCH
       │    └── Auto-close band (5%/3%/3%): PARTIAL_MATCH → AUTO_CLOSE
       ├── ClassificationService → MATCHED / PARTIAL_MATCH / UNMATCHED
       └── ExceptionBuilderService → ReconciliationException records
```

### ReconciliationResult Fields (key)
- `match_status`: MATCHED / PARTIAL_MATCH / UNMATCHED / ERROR
- `reconciliation_mode`: TWO_WAY / THREE_WAY / NON_PO
- `vendor_match`, `currency_match`, `po_total_match`: boolean flags
- `total_amount_difference`: Decimal
- `grn_available`, `grn_fully_received`: boolean flags
- `extraction_confidence`, `deterministic_confidence`: float scores
- `requires_review`: boolean
- `summary`: text summary of match result

### Exception Types (from `ExceptionType` enum in `core/enums.py`)
Inferred: VENDOR_MISMATCH, CURRENCY_MISMATCH, AMOUNT_MISMATCH, QTY_MISMATCH, PRICE_MISMATCH, TAX_MISMATCH, PO_NOT_FOUND, GRN_NOT_FOUND, GRN_INCOMPLETE, LINE_ITEM_NOT_MATCHED, etc.

---

## 5. Agent Analysis Workflow

See [03_Agent_Architecture_and_Execution_Model.md] for full detail.

### Key Agent-Driven Business Rules
- **PO Feedback Loop**: If `PORetrievalAgent` finds a PO not in the original lookup, `AgentFeedbackService.re_reconcile()` re-runs the full match atomically and updates the `ReconciliationResult`
- **GRN-only in 3-way**: `GRNRetrievalAgent.build_user_message()` returns early with a JSON null response if mode is TWO_WAY
- **Reviewer summary**: `ExceptionAnalysisAgent` makes a second LLM call to produce a plain-language reviewer summary with suggested actions

---

## 6. Human Review Workflow

### Auto-Assignment
- `CaseAssignmentService` auto-assigns `REQUIRES_REVIEW` cases to available REVIEWER-role users
- Bulk assignment available via UI (`/reviews/bulk-assign/`)

### Review Actions
Available `ReviewActionType` values (from enums):
- `APPROVE` — approve the invoice as matched
- `APPROVE_WITH_FIXES` — approve with corrections to extracted fields
- `REJECT` — reject the invoice
- `NEEDS_INFO` — request more information (vendor clarification)
- `ESCALATE` — escalate to Finance Manager

### Review Decision Persistence
- `ReviewAssignment.reviewer_summary` — LLM-generated summary from ExceptionAnalysisAgent
- `ReviewAssignment.reviewer_risk_level` — LOW / MEDIUM / HIGH
- `ReviewAssignment.reviewer_recommendation` — agent's recommendation
- `ReviewAssignment.reviewer_suggested_actions` — JSON array of suggested fixes
- `APCaseDecision` — records the final human decision with reason, actor, timestamp

---

## 7. ERP Posting Workflow

### States
`PROPOSED → REVIEW_REQUIRED → READY_TO_SUBMIT → SUBMITTED`

### Mapping Engine
- Resolves: vendor code, GL account, cost center, tax codes from ERP reference data
- Applies confidence scoring per mapped field
- `SystemPostingPreparationAgent` runs to record the posting initiation

---

## 8. Governance and Audit Events

Key `AuditEventType` values (38+ total from README):
- `INVOICE_UPLOADED`, `EXTRACTION_COMPLETED`, `DUPLICATE_FLAGGED`
- `RECONCILIATION_TRIGGERED`, `RECONCILIATION_COMPLETED`, `MODE_RESOLVED`
- `REVIEW_ASSIGNED`, `REVIEW_APPROVED`, `REVIEW_REJECTED`, `FIELD_CORRECTED`
- `OVERRIDE_APPLIED`, `REPROCESS_REQUESTED`, `CASE_REROUTED`, `CASE_CLOSED`
- `ROLE_CHANGED`, `PERMISSION_CHANGED`, `ACCESS_DENIED`

---

## 9. Exception / Review / Escalation Paths

```
UNMATCHED / PARTIAL_MATCH (within auto-close band)
  → AUTO_CLOSE (no agent, no human review)

UNMATCHED / PARTIAL_MATCH (outside auto-close band, OR PO not found)
  → Agent pipeline → ExceptionAnalysisAgent
       ├── HIGH confidence AUTO_CLOSE recommendation
       │    └── AgentGuardrailsService checks recommendations.auto_close permission
       │    └── Case closed without human review (if granted)
       ├── SEND_TO_AP_REVIEW recommendation
       │    └── ReviewAssignment created → REVIEWER queue
       │    └── Human review workflow
       └── ESCALATE_TO_MANAGER recommendation
            └── Case escalated → FINANCE_MANAGER assignment

PO_NOT_FOUND
  → PORetrievalAgent attempts to find PO
       ├── PO found → re-reconcile → normal exception path
       └── PO not found → UNMATCHED → human review
```

---

## 10. Human-in-the-Loop Behaviors

| Decision Point | Who Decides | Bypass Available |
|---------------|------------|-----------------|
| Extraction approval (low confidence) | AP Processor / REVIEWER | Yes (auto-approve if confidence ≥ threshold) |
| Reconciliation exception review | REVIEWER | Yes (auto-close within auto-close band) |
| Escalation resolution | FINANCE_MANAGER | No (requires human decision) |
| Agent recommendation acceptance | System (auto) or REVIEWER | Agent can auto-close if guardrail permits |
| ERP posting approval (REVIEW_REQUIRED state) | Finance role (inferred) | Unknown — needs validation |

---

## 11. Procurement Intelligence Workflows

### Request Intake and PDF-Led Prefill

`ProcurementRequestViewSet` supports both manual creation and document-led request intake:

```
POST /api/v1/procurement/requests/
  └── ProcurementRequestService.create_request()

POST /api/v1/procurement/requests/prefill/
  ├── DocumentUpload created via extraction upload service
  ├── draft ProcurementRequest created with tenant + source_document_type
  └── run_request_prefill_task.delay(...)
         └── RequestDocumentPrefillService.run_prefill()
              ├── OCR / text extraction
              ├── LLM extraction into core_fields + attributes
              └── prefill payload saved for user confirmation
```

Request lifecycle in current code is:
- `PENDING_RFQ` -> `READY_RFQ` -> `COMPLETED` or `FAILED`
- There is no `DRAFT` / `PROCESSING` request status in the current enum set

### Recommendation Workflow

```
AnalysisRun(type=RECOMMENDATION)
  └── run_analysis_task
       ├── AnalysisRunService.start_run()
       ├── AttributeService.get_attributes_dict()
       ├── HVACRulesEngine.evaluate()
       ├── RecommendationGraphService.run() via run_procurement_component_with_tracking()
       │    └── direct HVACRecommendationAgent fallback on graph failure
       ├── MarketIntelligenceService.generate_auto()
       ├── HVAC compliance check + ComplianceAgent augmentation
       ├── RecommendationResult + ComplianceResult upserted
       └── ProcurementRequestService.update_status(... -> PENDING_RFQ or FAILED)
```

Key behaviors verified from code:
- Deterministic HVAC rules run before AI augmentation
- Recommendation path is agent-first but fallbacks to deterministic rule output safely
- Market intelligence is auto-generated for HVAC requests after recommendation runs
- Output is sanitized through `BaseAgent._sanitise_text()` before DB persistence

### Validation Workflow

`ValidationOrchestratorService.run_validation()` executes six deterministic dimensions:
1. Attribute completeness
2. Document completeness
3. Scope coverage
4. Ambiguity detection
5. Commercial completeness
6. Compliance readiness

If `agent_enabled=True` and ambiguity count is at least 3, the ambiguity subset is routed through the procurement agent bridge for augmentation before `ValidationResult` and `ValidationResultItem` records are persisted.

### Quotation Prefill Workflow

```
POST /api/v1/procurement/quotations/prefill/
  └── run_quotation_prefill_task
       └── QuotationDocumentPrefillService.run_prefill()
            ├── OCR / extraction adapter
            ├── GPT-based structured extraction
            ├── AttributeMappingService.map_quotation_fields()
            ├── confidence classification
            └── prefill_payload_json stored with REVIEW_PENDING status

User confirmation
  └── PrefillReviewService.confirm_quotation_prefill()
       ├── SupplierQuotation header fields updated
       └── QuotationLineItem rows bulk-created
```

This is a true two-phase persistence flow: extracted line items are not committed until the user confirms the prefill payload.

### Benchmark Workflow (Current Runtime)

Current BENCHMARK execution is intentionally shallow:

```
AnalysisRun(type=BENCHMARK)
  └── run_analysis_task
       ├── first quotation selected from request.quotations
       └── apps.benchmarking.services.procurement_cost_service.ProcurementCostService.run_cost_analysis()
            ├── creates BenchmarkResult
            ├── sets total_benchmark_amount = total_quoted_amount
            ├── variance_pct = 0
            ├── risk_level = LOW
            └── summary_json = {source: "procurement_cost_service", note: "compatibility bridge"}
```

Implication: benchmark result persistence is wired and the UI/API surface exists, but the live runtime is currently a compatibility placeholder rather than the full should-cost corridor design documented elsewhere.
