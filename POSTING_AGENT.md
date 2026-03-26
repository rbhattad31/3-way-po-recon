# Invoice Posting Agent — Architecture & Developer Guide

## Overview

The Invoice Posting Agent is a **Phase 1** implementation that transforms approved invoice extractions into ERP-ready posting proposals. It resolves vendor, item, tax, cost-center, and PO references from **Excel-imported ERP master data**, validates the proposal, scores confidence, routes to review queues when needed, and (mock) submits to ERP.

The system follows the same **two-layer architecture** as the extraction system:

| Layer | Django App | Purpose |
|---|---|---|
| **Business / UI** | `apps/posting/` | Workflow state, user-facing actions, templates, API |
| **Platform / Core** | `apps/posting_core/` | Execution records, mapping engine, validation, ERP reference data |

---

## File Layout

```
apps/posting/                              # Business layer
├── models.py                              # InvoicePosting, InvoicePostingFieldCorrection
├── services/
│   ├── eligibility_service.py             # 7-check eligibility gate
│   ├── posting_orchestrator.py            # Orchestrates prepare_posting lifecycle
│   └── posting_action_service.py          # approve / reject / submit / retry
├── tasks.py                               # Celery: prepare_posting_task, import_reference_excel_task
├── views.py                               # DRF ViewSets + PostingPrepareView
├── serializers.py                         # DRF serializers
├── api_urls.py                            # /api/v1/posting/
├── template_views.py                      # Workbench, detail, import list
├── urls.py                                # /posting/
└── admin.py

apps/posting_core/                         # Platform layer
├── models.py                              # PostingRun, ERP references (15 models)
├── services/
│   ├── import_pipeline/                   # ERP reference import from Excel/CSV
│   │   ├── import_parsers.py              # parse_excel_file, normalize_header
│   │   ├── import_validators.py           # validate_columns, validate_row
│   │   ├── vendor_importer.py             # VendorImporter
│   │   ├── item_importer.py               # ItemImporter
│   │   ├── tax_importer.py                # TaxImporter
│   │   ├── cost_center_importer.py        # CostCenterImporter
│   │   ├── po_importer.py                 # POImporter
│   │   └── excel_import_orchestrator.py   # ExcelImportOrchestrator.run_import()
│   ├── posting_mapping_engine.py          # Core value: resolve ERP mappings
│   ├── posting_pipeline.py                # 9-stage pipeline orchestration
│   ├── posting_snapshot_builder.py        # Capture invoice snapshot as JSON
│   ├── posting_validation.py              # Validate proposal completeness
│   ├── posting_confidence.py              # Weighted confidence scoring
│   ├── posting_review_routing.py          # Review queue assignment
│   ├── posting_governance_trail.py        # Governance mirror writes
│   ├── posting_audit.py                   # Centralized audit logging
│   └── payload_builder.py                 # Build canonical ERP payload
├── views.py                               # DRF: PostingRunViewSet, ERP ref ViewSets
├── serializers.py                         # DRF serializers for all models
├── api_urls.py                            # /api/v1/posting-core/
├── urls.py                                # (empty — all UIs in apps.posting)
└── admin.py
```

---

## Data Model

### Business Layer (`apps/posting/`)

**InvoicePosting** — One-to-one with Invoice. Tracks posting lifecycle.

| Field | Type | Description |
|---|---|---|
| `invoice` | OneToOneField → Invoice | The invoice being posted |
| `extraction_result` | FK → ExtractionResult | Source extraction |
| `extraction_run` | FK → ExtractionRun | Source extraction run |
| `status` | InvoicePostingStatus (11 states) | Current lifecycle state |
| `stage` | PostingStage | Last completed pipeline stage |
| `posting_confidence` | Float | 0.0–1.0 overall confidence |
| `review_queue` | PostingReviewQueue | Assigned review queue |
| `is_touchless` | Boolean | True if no human review needed |
| `mapping_summary_json` | JSON | Summary of mapping results |
| `payload_snapshot_json` | JSON | ERP-ready posting payload |
| `erp_document_number` | CharField | ERP document ID after posting |
| `retry_count` | PositiveInt | Number of retry attempts |

**InvoicePostingFieldCorrection** — Tracks field corrections during review.

### Platform Layer (`apps/posting_core/`)

**PostingRun** — Authoritative execution record per pipeline invocation (analogous to ExtractionRun).

| Key Children | Description |
|---|---|
| `PostingFieldValue` | Resolved field values with source/confidence |
| `PostingLineItem` | Resolved line items with ERP codes |
| `PostingIssue` | Validation issues (severity, check_type) |
| `PostingEvidence` | Source evidence for resolved values |
| `PostingApprovalRecord` | Governance mirror (1:1) |

**ERP Reference Models** (imported from Excel):

| Model | Key Fields | Purpose |
|---|---|---|
| `ERPReferenceImportBatch` | batch_type, status, row_count | Batch metadata |
| `ERPVendorReference` | vendor_code, vendor_name, normalized | Vendor master |
| `ERPItemReference` | item_code, item_name, uom, tax_code | Item/material master |
| `ERPTaxCodeReference` | tax_code, rate, country_code | Tax code master |
| `ERPCostCenterReference` | cost_center_code, department | Cost center master |
| `ERPPOReference` | po_number, po_line, vendor_code, item_code | Open PO lines |

**Alias & Rules:**

| Model | Purpose |
|---|---|
| `VendorAliasMapping` | Map vendor name variants → ERP vendor code |
| `ItemAliasMapping` | Map item description variants → ERP item code |
| `PostingRule` | Configurable tax/cost-center/line-type rules |

---

## Status Lifecycle

```
                          ┌──────────────────────────────────────────┐
                          │                                          │
NOT_READY ──► READY_FOR_POSTING ──► MAPPING_IN_PROGRESS ─┬──► MAPPING_REVIEW_REQUIRED ──► READY_TO_SUBMIT
                                                          │                                    │
                                                          └──► READY_TO_SUBMIT ◄───────────────┘
                                                                     │
                                                                     ▼
                                                          SUBMISSION_IN_PROGRESS ──► POSTED
                                                                     │
                                                                     ▼
                                                                POST_FAILED ──► RETRY_PENDING ──► (re-enter pipeline)
                                                                     │
                                                                     ▼
                                                                  REJECTED

                                                          SKIPPED (manual skip)
```

**Approval Actions:**

| Action | Allowed From | Transitions To |
|---|---|---|
| `approve` | MAPPING_REVIEW_REQUIRED, READY_TO_SUBMIT | READY_TO_SUBMIT |
| `reject` | MAPPING_REVIEW_REQUIRED, READY_TO_SUBMIT, POST_FAILED | REJECTED |
| `submit` | READY_TO_SUBMIT | POSTED (Phase 1 mock) |
| `retry` | POST_FAILED, RETRY_PENDING, MAPPING_REVIEW_REQUIRED | Re-enters pipeline |

---

## Pipeline Architecture

The posting pipeline executes a 9-stage sequence inside `PostingPipeline.run()`:

```
Invoice (READY_FOR_RECON)
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  PostingPipeline.run(invoice)                                   │
│                                                                 │
│  1. ELIGIBILITY_CHECK    → PostingEligibilityService.check()    │
│  2. SNAPSHOT_BUILD       → PostingSnapshotBuilder.build()       │
│  3. MAPPING              → PostingMappingEngine.resolve()       │
│  4. VALIDATION           → PostingValidationService.validate()  │
│  5. CONFIDENCE           → PostingConfidenceService.calculate() │
│  6. REVIEW_ROUTING       → PostingReviewRoutingService.route()  │
│  7. PAYLOAD_BUILD        → PayloadBuilder.build()               │
│  8. FINALIZATION         → Persist field values, line items,    │
│                            issues, evidence (bulk_create)       │
│  9. STATUS               → Set final PostingRun status          │
│                                                                 │
└────────────────────────────────────────────┬────────────────────┘
                                             │
                                             ▼
                                        PostingRun
                                    (COMPLETED / FAILED)
```

---

## Mapping Engine — The Core Value

`PostingMappingEngine` resolves extracted invoice data to ERP-native codes using **imported reference tables** (never live Excel reads). Each resolution follows a **chain of strategies** that stops at first match:

### Vendor Resolution Chain
```
1. Exact vendor_code match in ERPVendorReference
2. Alias match in VendorAliasMapping (normalized)
3. Exact name match in ERPVendorReference
4. Partial/fuzzy name match (normalized contains)
5. → UNRESOLVED (routes to VENDOR_MAPPING_REVIEW queue)
```

### Item Resolution Chain (per line)
```
1. PO reference lookup → ERPPOReference (if PO number available)
2. Exact item_code match in ERPItemReference
3. Alias match in ItemAliasMapping
4. Name/description match in ERPItemReference
5. PostingRule-based mapping (rule_type=TAX_CODE/COST_CENTER)
6. → UNRESOLVED (routes to ITEM_MAPPING_REVIEW queue)
```

### Tax Code Resolution Chain
```
1. Explicit from extraction (if tax_code field populated)
2. Item default (ERPItemReference.tax_code)
3. Rate match (ERPTaxCodeReference.rate nearest)
4. PostingRule fallback (rule_type=TAX_CODE)
5. → UNRESOLVED (routes to TAX_REVIEW queue)
```

### Cost Center Resolution Chain
```
1. PostingRule match (rule_type=COST_CENTER, condition matches)
2. Exact ERPCostCenterReference lookup
3. → UNRESOLVED (routes to COST_CENTER_REVIEW queue)
```

### Reference Freshness
The engine tracks which `ERPReferenceImportBatch` was used for each resolution. Stale references (older than `POSTING_REFERENCE_FRESHNESS_HOURS`, default 168h / 7 days) generate WARNING issues and reduce confidence.

---

## Confidence Scoring

`PostingConfidenceService` calculates a weighted 0.0–1.0 score across 5 dimensions:

| Dimension | Weight | Calculation |
|---|---|---|
| Header Completeness | 15% | Proportion of required header fields present |
| Vendor Mapping | 25% | Direct vendor confidence from resolution chain |
| Line Mapping | 30% | Average confidence across all resolved lines |
| Tax Completeness | 15% | Proportion of lines with tax_code assigned |
| Reference Freshness | 15% | Inverse of staleness issue count |

---

## Review Queue Routing

`PostingReviewRoutingService.route()` determines whether human review is needed:

| Condition | Queue Assignment | Reason |
|---|---|---|
| No vendor_code resolved | `VENDOR_MAPPING_REVIEW` | "Vendor code not resolved" |
| Line item_code missing + low confidence | `ITEM_MAPPING_REVIEW` | "Item mapping unresolved for line N" |
| Any line missing tax_code | `TAX_REVIEW` | "Tax code not assigned" |
| Any line missing cost_center | `COST_CENTER_REVIEW` | "Cost center not resolved" |
| ERROR-severity issues exist | `POSTING_OPS` | "N blocking issue(s) found" |
| Confidence < 0.7 | `POSTING_OPS` | "Low overall confidence" |

If no conditions trigger → `requires_review=False`, `is_touchless=True` → auto-advances to READY_TO_SUBMIT.

---

## ERP Reference Import Pipeline

### Supported Reference Types

| Batch Type | Model | Required Columns | Purpose |
|---|---|---|---|
| `VENDOR` | ERPVendorReference | vendor_code, vendor_name | Vendor master |
| `ITEM` | ERPItemReference | item_code, item_name | Material/item master |
| `TAX` | ERPTaxCodeReference | tax_code | Tax code catalog |
| `COST_CENTER` | ERPCostCenterReference | cost_center_code, cost_center_name | Org structure |
| `OPEN_PO` | ERPPOReference | po_number | Open PO lines for matching |

### Import Flow

```
Excel/CSV Upload
       │
       ▼
  parse_excel_file()           # openpyxl for .xlsx, csv for .csv
       │                       # Normalizes headers, computes checksum
       ▼
  validate_columns()           # Checks required columns present
       │
       ▼
  TypeImporter.import_rows()   # Type-specific bulk_create + normalization
       │
       ▼
  ERPReferenceImportBatch      # Status: COMPLETED / PARTIAL / FAILED
       │                       # Tracks row_count, valid_row_count, invalid_row_count
       ▼
  Audit Event                  # ERP_REFERENCE_IMPORT_COMPLETED
```

### Upload API
```
POST /api/v1/posting-core/upload/
Content-Type: multipart/form-data

Fields:
  file:         Excel (.xlsx) or CSV file
  batch_type:   VENDOR | ITEM | TAX | COST_CENTER | OPEN_PO
  source_as_of: (optional) YYYY-MM-DD date of ERP export
```

---

## Integration Points

### Trigger: Extraction Approval → Posting

When extraction is approved (human or auto), the `ExtractionApprovalService` enqueues the posting pipeline:

```python
# In ExtractionApprovalService.approve() and try_auto_approve():
cls._enqueue_posting(invoice, user)  # best-effort, never blocks approval

# _enqueue_posting():
prepare_posting_task.delay(
    invoice_id=invoice.pk,
    user_id=user.pk if user else None,
    trigger="approval" | "auto_approval",
)
```

This is **best-effort** — posting failures never block the extraction approval path.

### Manual Trigger

```
POST /api/v1/posting/prepare/
{
    "invoice_id": 123,
    "trigger": "manual"
}
```

Returns `202 Accepted` — posting preparation runs async via Celery.

---

## API Endpoints

### Posting Business API (`/api/v1/posting/`)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/posting/postings/` | List invoice postings (filter: status, review_queue) |
| GET | `/api/v1/posting/postings/{id}/` | Posting detail with corrections |
| POST | `/api/v1/posting/postings/{id}/approve/` | Approve posting (optional corrections) |
| POST | `/api/v1/posting/postings/{id}/reject/` | Reject posting (reason) |
| POST | `/api/v1/posting/postings/{id}/submit/` | Submit to ERP (Phase 1 mock) |
| POST | `/api/v1/posting/postings/{id}/retry/` | Retry failed posting |
| POST | `/api/v1/posting/prepare/` | Trigger posting for an invoice |

### Posting Core API (`/api/v1/posting-core/`)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/posting-core/runs/` | List posting runs (filter: invoice, status) |
| GET | `/api/v1/posting-core/runs/{id}/` | Run detail (field values, lines, issues) |
| POST | `/api/v1/posting-core/upload/` | Upload ERP reference Excel/CSV |
| GET | `/api/v1/posting-core/import-batches/` | List import batches |
| CRUD | `/api/v1/posting-core/vendors/` | ERP vendor references |
| CRUD | `/api/v1/posting-core/items/` | ERP item references |
| CRUD | `/api/v1/posting-core/tax-codes/` | ERP tax codes |
| CRUD | `/api/v1/posting-core/cost-centers/` | ERP cost centers |
| CRUD | `/api/v1/posting-core/po-refs/` | ERP PO references |
| CRUD | `/api/v1/posting-core/vendor-aliases/` | Vendor alias mappings |
| CRUD | `/api/v1/posting-core/item-aliases/` | Item alias mappings |
| CRUD | `/api/v1/posting-core/rules/` | Posting rules |

### Template Views (`/posting/`)

| URL | View | Description |
|---|---|---|
| `/posting/` | `posting_workbench` | List with KPIs, filters, pagination |
| `/posting/{id}/` | `posting_detail` | Detail with proposal, issues, actions |
| `/posting/{id}/approve/` | `posting_approve` | POST: approve |
| `/posting/{id}/reject/` | `posting_reject` | POST: reject |
| `/posting/{id}/submit/` | `posting_submit` | POST: submit to ERP |
| `/posting/{id}/retry/` | `posting_retry` | POST: retry pipeline |
| `/posting/imports/` | `reference_import_list` | ERP import batch history |

---

## Enum Reference

### InvoicePostingStatus (11 states)
`NOT_READY` · `READY_FOR_POSTING` · `MAPPING_IN_PROGRESS` · `MAPPING_REVIEW_REQUIRED` · `READY_TO_SUBMIT` · `SUBMISSION_IN_PROGRESS` · `POSTED` · `POST_FAILED` · `REJECTED` · `RETRY_PENDING` · `SKIPPED`

### PostingRunStatus (5 states)
`PENDING` · `RUNNING` · `COMPLETED` · `FAILED` · `CANCELLED`

### PostingStage (9 stages)
`ELIGIBILITY_CHECK` · `SNAPSHOT_BUILD` · `MAPPING` · `VALIDATION` · `CONFIDENCE` · `REVIEW_ROUTING` · `PAYLOAD_BUILD` · `SUBMISSION` · `FINALIZATION`

### PostingReviewQueue (6 queues)
`ITEM_MAPPING_REVIEW` · `VENDOR_MAPPING_REVIEW` · `TAX_REVIEW` · `COST_CENTER_REVIEW` · `PO_REVIEW` · `POSTING_OPS`

### ERPReferenceBatchType (5 types)
`VENDOR` · `ITEM` · `TAX` · `COST_CENTER` · `OPEN_PO`

### Audit Events (17 posting-related)
`POSTING_STARTED` · `POSTING_ELIGIBILITY_PASSED` · `POSTING_ELIGIBILITY_FAILED` · `POSTING_MAPPING_COMPLETED` · `POSTING_MAPPING_REVIEW_REQUIRED` · `POSTING_VALIDATION_COMPLETED` · `POSTING_READY_TO_SUBMIT` · `POSTING_SUBMITTED` · `POSTING_SUCCEEDED` · `POSTING_FAILED` · `POSTING_APPROVED` · `POSTING_REJECTED` · `POSTING_FIELD_CORRECTED` · `ERP_REFERENCE_IMPORT_STARTED` · `ERP_REFERENCE_IMPORT_COMPLETED` · `ERP_REFERENCE_IMPORT_FAILED`

---

## Governance & Audit Trail

Every posting operation is fully auditable:

- **PostingRun** preserves complete execution history (snapshots, proposals, payloads)
- **PostingApprovalRecord** mirrors every approve/reject decision (written only by `PostingGovernanceTrailService`)
- **PostingIssue / PostingEvidence** explain validation results and source provenance
- **InvoicePostingFieldCorrection** tracks every manual correction during review
- **AuditEvent** entries logged for all 17 posting event types via `PostingAuditService`
- **ERPReferenceImportBatch** tracks every import (checksums, row counts, errors)
- All service entry points decorated with `@observed_service` for tracing

---

## Configuration

| Setting | Default | Description |
|---|---|---|
| `POSTING_REFERENCE_FRESHNESS_HOURS` | 168 (7 days) | Max age of ERP reference data before staleness warnings |
| `CELERY_TASK_ALWAYS_EAGER` | True (Windows dev) | When True, tasks run synchronously (no Redis required) |

---

## Phase 2+ Extension Points

The system is designed for incremental enhancement:

| Extension | Where | Notes |
|---|---|---|
| **Real ERP submission** | `PostingActionService.submit_posting()` | Replace mock with ERP API connector or RPA bridge |
| **SAP / Oracle connectors** | New `apps/posting_core/connectors/` | Implement per-ERP protocol (BAPI, REST, IDoc) |
| **Auto-submit** | `PostingOrchestrator` | Auto-submit when `is_touchless=True` and confidence ≥ threshold |
| **Feedback learning** | `PostingMappingEngine` | Train alias mappings from accepted corrections |
| **Bulk posting** | `PostingOrchestrator` | Batch multiple invoices into single ERP journal |
| **Scheduled re-import** | Celery Beat | Periodic `import_reference_excel_task` from shared drive |
| **LLM-assisted mapping** | `PostingMappingEngine._resolve_item()` | Use GPT for fuzzy item description matching |
| **Rejection → re-extraction** | `PostingActionService.reject_posting()` | Trigger re-extraction with feedback |
