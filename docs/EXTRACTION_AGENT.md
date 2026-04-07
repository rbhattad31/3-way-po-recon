# Invoice Extraction Agent — Feature Documentation

> **Modules**: `apps/extraction/` (Application Layer — UI, Task, Core Models) + `apps/extraction_core/` (Platform Layer — Configuration, Execution, Governance)
> **Dependencies**: Azure Document Intelligence (OCR), Azure OpenAI GPT-4o (LLM), Agent Framework (`apps/agents/`)
> **Status**: Human-in-the-loop approval gate + multi-country extraction platform + credit-based usage control + OCR cost tracking + Phase 2 modular prompt composition + deterministic response repair + field-level confidence scoring + critical field validation + hard reconciliation math checks + **Phase 2 hardening: decision codes, recovery lane, evidence-aware field confidence, prompt-source audit trail** + **Indian e-invoice QR code decoding (NIC JWT + plain-JSON formats, Azure DI barcodes add-on, OCR plain-text IRN fallback)**. 355 passing, 2 pre-existing failures, 1 skipped (total collected ~358) — see `apps/extraction/tests/`. ERP connectors and Celery Beat schedules are pending.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Extraction Pipeline](#3-extraction-pipeline)
4. [Data Models](#4-data-models)
5. [Services](#5-services)
6. [Extraction Core — Multi-Country Extraction Platform](#6-extraction-core--multi-country-extraction-platform)
7. [Master Data Enrichment](#7-master-data-enrichment)
8. [Approval Gate](#8-approval-gate)
9. [Agent Framework Integration](#9-agent-framework-integration)
10. [LLM Prompt](#10-llm-prompt)
11. [Template Views & URLs](#11-template-views--urls)
12. [Templates (UI)](#12-templates-ui)
13. [Extraction Review Console](#13-extraction-review-console)
14. [Enums & Status Flows](#14-enums--status-flows)
15. [Configuration](#15-configuration)
16. [Permissions & RBAC](#16-permissions--rbac)
17. [Credit System](#17-credit-system)
18. [OCR Cost Tracking](#18-ocr-cost-tracking)
19. [Django Admin](#19-django-admin)
20. [File Reference](#20-file-reference)
21. [Bulk Extraction Intake (Phase 1)](#21-bulk-extraction-intake-phase-1)
22. [Phase 2 Hardening](#22-phase-2-hardening)
23. [Indian e-Invoice QR Code Support](#23-indian-e-invoice-qr-code-support)

---

## 1. Overview

The Invoice Extraction Agent converts uploaded invoice documents (PDF, PNG, JPG, TIFF) into structured, normalized data. The system spans two Django apps:

- **`apps/extraction/`** — Application layer: template views (workbench, console, approval queue, country packs), Celery task, core models (`ExtractionResult`, `ExtractionApproval`, `ExtractionFieldCorrection`), 8 pipeline services, and the human approval gate.
- **`apps/extraction_core/`** — Platform layer: 13 data models, 30 service classes, 60+ API endpoints, multi-country jurisdiction resolution, schema-driven extraction, evidence capture, confidence scoring, review routing, analytics/learning, and country pack governance.

### Base Extraction Pipeline (apps/extraction)

Uses a two-stage pipeline:

1. **Azure Document Intelligence** — OCR to extract raw text from the document.
2. **Azure OpenAI GPT-4o** — LLM-based structured extraction from OCR text into a typed JSON schema.

After extraction, the data passes through parsing, normalization, validation, and duplicate detection before being persisted. A **human approval gate** ensures every extraction is reviewed (or auto-approved at high confidence) before the invoice enters reconciliation.

### Extended Platform Pipeline (apps/extraction_core)

Adds an 11-stage governed pipeline with:

1. **4-tier jurisdiction resolution** — Document declared → entity profile → runtime settings → auto-detect
2. **Schema-driven extraction** — Versioned schemas per jurisdiction + document type
3. **Document intelligence** — Document classification, party extraction, relationship extraction
4. **Multi-page support** — Page segmentation, header/footer dedup, cross-page table stitching
5. **Country-specific normalization & validation** — Jurisdiction-aware rules (IN-GST, AE-VAT, SA-ZATCA)
6. **Evidence capture** — Field provenance with OCR snippets, page numbers, bounding boxes
7. **Confidence scoring** — Multi-dimensional (header, tax, line items, jurisdiction)
8. **Review routing** — Queue-based routing (EXCEPTION_OPS, TAX_REVIEW, VENDOR_OPS)
9. **Master data enrichment** — Vendor matching, PO lookup, confidence adjustments
10. **Analytics/learning** — Correction feedback → ExtractionAnalyticsSnapshot
11. **Country pack governance** — DRAFT → ACTIVE → DEPRECATED lifecycle per jurisdiction

### Cross-Module Integration

Template views in `apps/extraction/` enrich their context with `apps/extraction_core/` models via `ExecutionContext`:
- Workbench uses `get_execution_context()` to load review_queue and source indicator for each result
- Console uses `get_execution_context()` to populate extraction_ctx (review queue, schema, method, source badges) + `ExtractionCorrection` audit trail
- Country packs page queries `CountryPack` with jurisdiction profiles
- Source badge in console header shows **Governed** (green) or **Legacy** (warning) based on `ExecutionContext.source`

### Execution Ownership

**ExtractionRun** (`apps/extraction_core/models.py`) is the **authoritative execution record** — the runtime source of truth. **ExtractionResult** (`apps/extraction/models.py`) is the **UI-facing summary** with an `extraction_run` FK linking back to the governing run.

Views resolve execution data via `ExecutionContext` (`apps/extraction/services/execution_context.py`):
1. Check `extraction_result.extraction_run` FK (direct link)
2. Fall back to `ExtractionRun.objects.filter(document__document_upload_id=...)` (lookup by upload)
3. Return legacy context (all None) if no governed run exists

**Phase 2 hardening fields** (populated on all paths via `_enrich_hardening_fields()` from `raw_response` keys):

| Field | Type | Source |
|-------|------|--------|
| `decision_codes` | `List[str]` | `raw_response["_decision_codes"]` |
| `prompt_source` | `str \| None` | `raw_response["_prompt_meta"]["prompt_source_type"]` |
| `prompt_hash` | `str \| None` | `raw_response["_prompt_meta"]["prompt_hash"]` |
| `recovery_lane_invoked` | `bool` | `raw_response["_recovery"]["invoked"]` |
| `recovery_lane_succeeded` | `bool \| None` | `raw_response["_recovery"]["succeeded"]` (only set when invoked) |

### GovernanceTrailService

`GovernanceTrailService` (`apps/extraction_core/services/governance_trail.py`) is the **sole writer** of `ExtractionApprovalRecord`. Called by:
- `ExtractionApprovalService.approve()` / `.reject()` (legacy flow)
- `ExtractionRunViewSet.approve()` / `.reject()` (governed API)

### Permission Split

- `invoices.create` — upload only (file selection and dispatch to extraction task)
- `extraction.correct` — edit/correct extracted field values (workbench, console, API)
- `extraction.approve` / `extraction.reject` — finalize extraction decisions

---

## 2. Architecture

### Two-App Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  apps/extraction/  (Application Layer)                       │
│                                                              │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ Template Views│  │ Celery Task   │  │ Core Models      │  │
│  │ (15 views)   │  │ (pipeline)    │  │ ExtractionResult │  │
│  │ workbench    │  │               │  │ ExtractionApproval│ │
│  │ console      │  │ 8 services    │  │ FieldCorrection  │  │
│  │ approvals    │  │               │  │                  │  │
│  │ country packs│  │               │  │                  │  │
│  └──────┬───────┘  └───────────────┘  └──────────────────┘  │
│         │ cross-module queries (ExtractionRun, CountryPack)  │
├─────────┼───────────────────────────────────────────────────┤
│  apps/extraction_core/  (Platform Layer)                     │
│                                                              │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ Configuration│  │ Pipeline (30  │  │ Governance       │  │
│  │ Jurisdiction │  │ services)     │  │ CountryPack      │  │
│  │ Schema       │  │ 11-stage      │  │ Analytics        │  │
│  │ Runtime      │  │ orchestrator  │  │ Learning         │  │
│  │ Entity       │  │               │  │ Audit            │  │
│  └──────────────┘  └───────────────┘  └──────────────────┘  │
│                                                              │
│  ┌──────────────┐  ┌───────────────┐                         │
│  │ 60+ API      │  │ 13 Models     │                         │
│  │ endpoints    │  │ ExtractionRun │                         │
│  │ Config +     │  │ FieldValue    │                         │
│  │ Execution    │  │ Evidence ...  │                         │
│  └──────────────┘  └───────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow Diagram (Base Pipeline — Updated)

> **Current flow** includes category classification, modular prompt composition, and deterministic response repair (added in Phase 2 upgrade).

```
User uploads PDF/Image
         │
         ▼
  DocumentUpload record created
         │
         ▼
  process_invoice_upload_task (Celery)
         │
         ▼
  ┌──────────────────────────────────┐
  │  Stage 1: OCR                    │
  │  Azure Document Intelligence     │
  │  features=[BARCODES]             │
  │  → raw OCR text + qr_texts[]     │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 1c: QR Decode             │   ← NEW (Indian e-invoice)
  │  QRCodeDecoderService            │
  │  Strategy 1: Azure DI barcodes   │
  │  Strategy 2: OCR text IRN regex  │
  │  Strategy 3: pyzbar (optional)   │
  │  → QRInvoiceData (or None)       │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 2: Category Classification│   ← NEW
  │  InvoiceCategoryClassifier       │
  │  goods | service | travel        │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 3: Prompt Composition     │   ← NEW
  │  InvoicePromptComposer           │
  │  base + category + country/tax   │
  │  overlays → final system prompt  │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 4: InvoiceExtractionAgent │
  │  GPT-4o → structured JSON        │
  │  (temp=0, json_object mode)      │
  │  Uses composed prompt if provided│
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 5: Response Repair        │   ← NEW
  │  ResponseRepairService           │
  │  invoice# exclusion, tax recomp, │
  │  subtotal align, line tax alloc, │
  │  travel consolidation            │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ExtractionParserService  (JSON → ParsedInvoice)
         │
         ▼
  NormalizationService  (clean, type-cast, standardize)
         │
         ├──► FieldConfidenceService  (per-field scores + evidence_flags)
         ▼
  ValidationService  (mandatory fields, confidence check)
         │
         ├──► ReconciliationValidatorService  (6 math checks)
         ├──► derive_codes()  (machine-readable decision codes)
         ├──► RecoveryLaneService.evaluate()  (policy: named failure modes only)
         │         │ if triggered
         │         └──► InvoiceUnderstandingAgent  (bounded recovery)
         ▼
  DuplicateDetectionService  (vendor + invoice# match)
         │
         ▼
  InvoicePersistenceService  (save Invoice + LineItems)
  ExtractionResultPersistenceService  (save engine metadata)
  → ExtractionResult.raw_response includes:
      _repair, _field_confidence, _validation,
      _prompt_meta, _decision_codes, _recovery, _qr
         │
         ▼
  ┌─────────────────────────────────────────┐
  │             Approval Gate               │
  │                                         │
  │  Auto-approve enabled AND               │
  │  confidence ≥ threshold?                │
  │    YES → AUTO_APPROVED → READY_FOR_RECON│
  │    NO  → PENDING_APPROVAL               │
  │         → Human review in Approval Queue│
  │                                         │
  │  Human approves → READY_FOR_RECON       │
  │  Human rejects  → INVALID (re-extract)  │
  └─────────────────────────────────────────┘
         │
         ▼
  AP Case created immediately after extraction
  (pipeline pauses at EXTRACTION_APPROVAL if
   human approval needed; resumes on approve)
         │
         ▼
  Reconciliation pipeline
```

### Service Architecture

```
InvoiceExtractionAdapter (orchestrates stages 1 + 2)
  ├── Azure Document Intelligence Client (OCR)
  └── InvoiceExtractionAgent (LLM extraction via agent framework)
        ├── LLMClient (Azure OpenAI, temp=0, max_tokens=4096)
        ├── PromptRegistry ("extraction.invoice_system")
        └── AgentRun / AgentMessage (traceability)

ExtractionParserService → NormalizationService → ValidationService
  → DuplicateDetectionService → InvoicePersistenceService
    → ExtractionResultPersistenceService → ExtractionApprovalService
```

---

## 3. Extraction Pipeline

**Task**: `process_invoice_upload_task` in `apps/extraction/tasks.py`  
**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`

> **Execution path**: `ExtractionPipeline` (governed, 11-stage, in `apps/extraction_core`) is the preferred execution path. `ExtractionService` (legacy) remains active for backward compatibility. Step 6 also writes `extraction_run` to `ExtractionResult.extraction_run` FK, linking the UI summary to the authoritative execution record.

### Pipeline Steps

| Step | Service | Description |
|------|---------|-------------|
| 0 | `CreditService.reserve()` | Reserve 1 credit (`ref_type="document_upload"`, `ref_id=upload.pk`). Hard-stop if insufficient. |
| 1 | `InvoiceExtractionAdapter` | OCR (with `features=[AnalysisFeature.BARCODES]`) + LLM extraction → `ExtractionResponse` (includes `_repair`, `_qr` metadata in `raw_json`) |
| 1a | `DocumentTypeClassifier` | Classify OCR text → reject non-invoices (GRN, PO, DELIVERY_NOTE, STATEMENT) with credit refund. Rejection requires `confidence ≥ 0.60` **and** `not is_ambiguous`. |
| 1b | `_run_governed_pipeline()` | Wire governed extraction pipeline (`ExtractionPipeline.run()`) as an enrichment step. Creates `ExtractionDocument` linked to the upload, passes OCR text + invoice reference. Wrapped in try/except for graceful degradation — if the governed pipeline fails, the legacy pipeline continues and the result shows "Legacy" source. |
| 1c | `QRCodeDecoderService` | Decode Indian e-invoice QR (IRN, GSTIN, total, doc type). Three strategies: Azure DI barcodes → OCR text IRN regex → pyzbar pixel decode. Sets `ExtractionResponse.qr_data`; embeds `_qr` in `raw_json`. Fail-silent — `None` when no QR found. |
| 2 | `ExtractionParserService` | Parse raw JSON → `ParsedInvoice` dataclass |
| 3 | `NormalizationService` | Normalize fields (dates, amounts, PO numbers) → `NormalizedInvoice` |
| 3a | `FieldConfidenceService` | Deterministic per-field confidence scoring (0.0–1.0) based on presence, parse success, repair actions. Attaches `field_confidence` dict to `NormalizedInvoice`. Embeds `_field_confidence` in `raw_json` for persistence. |
| 4 | `ValidationService` + `ExtractionConfidenceScorer` | Check mandatory fields, compute deterministic overall confidence. Reads `NormalizedInvoice.field_confidence` to detect low-confidence critical fields → sets `requires_review_override`. |
| 4a | `ReconciliationValidatorService` | 6 deterministic math checks: total consistency, line sum, line math, tax breakdown, tax %, line tax sum. Issues serialised to `raw_json["_validation"]`. Math ERRORs surfaced as validation warnings. |
| 4b | `derive_codes()` | Maps ValidationResult + ReconciliationValidationResult + FieldConfidenceResult + prompt_source_type → list of machine-readable decision codes. Embedded into `raw_json["_decision_codes"]`. |
| 4c | `RecoveryLaneService` | Deterministic policy evaluation against named failure modes. When triggered, invokes `InvoiceUnderstandingAgent` with bounded recovery context. Output embedded into `raw_json["_recovery"]`. Fail-silent. |
| 5 | `DuplicateDetectionService` | Detect re-submitted invoices |
| 6 | `InvoicePersistenceService` + `ExtractionResultPersistenceService` | Persist to database (sets `extraction_run` FK). `ExtractionResult.raw_response` contains `_repair`, `_field_confidence`, `_validation`, `_prompt_meta`, `_decision_codes`, `_recovery`, and `_qr` metadata. |
| 6a | `CreditService.consume()` / `.refund()` | On success → consume; on OCR failure → refund (see §17 decision table) |
| 7 | Approval Gate | Auto-approve or queue for human review. `requires_review_override=True` skips auto-approval entirely (critical field confidence failure). |

### Audit Events

- `EXTRACTION_STARTED` — logged when the task begins
- `EXTRACTION_COMPLETED` — logged on successful extraction + persistence
- `EXTRACTION_FAILED` — logged on any pipeline failure

### Azure Blob Integration

- **Input path**: `input/{year}/{month}/filename`
- **On success**: File moved to `processed/`
- **On failure**: File moved to `exception/`

---

## 4. Data Models

### 4.1 ExtractionResult

**Table**: `extraction_result` | **File**: `apps/extraction/models.py` | **Inherits**: `BaseModel`

UI-facing summary record — **not** the execution source of truth. The authoritative execution record is `ExtractionRun` (apps/extraction_core). This model links to it via `extraction_run` FK.

| Field | Type | Description |
|-------|------|-------------|
| `document_upload` | FK → DocumentUpload | Source file |
| `invoice` | FK → Invoice (nullable) | Linked invoice after persistence |
| `extraction_run` | FK → ExtractionRun (nullable) | Link to authoritative execution record |
| `engine_name` | CharField | Engine identifier (default: `"default"`) |
| `engine_version` | CharField | Engine version string |
| `raw_response` | JSONField (nullable) | Full JSON response from LLM |
| `confidence` | FloatField (nullable) | 0.0–1.0 extraction confidence |
| `duration_ms` | PositiveIntegerField (nullable) | Extraction duration in milliseconds |
| `success` | BooleanField | Whether extraction succeeded |
| `error_message` | TextField | Error details if failed |
| `ocr_page_count` | PositiveIntegerField | Number of pages processed by OCR (default: 0) |
| `ocr_duration_ms` | PositiveIntegerField (nullable) | OCR processing duration in milliseconds |
| `ocr_char_count` | PositiveIntegerField | Number of characters extracted by OCR (default: 0) |

### 4.2 ExtractionApproval

**Table**: `extraction_approval` | **File**: `apps/extraction/models.py` | **Inherits**: `BaseModel`

Tracks human approval/rejection of extraction results and field corrections.

| Field | Type | Description |
|-------|------|-------------|
| `invoice` | OneToOneField → Invoice | Linked invoice |
| `extraction_result` | FK → ExtractionResult (nullable) | Source extraction |
| `status` | CharField | `ExtractionApprovalStatus` enum |
| `reviewed_by` | FK → User (nullable) | Reviewer |
| `reviewed_at` | DateTimeField (nullable) | Review timestamp |
| `rejection_reason` | TextField | Reason for rejection |
| `confidence_at_review` | FloatField (nullable) | Confidence snapshot at approval time |
| `original_values_snapshot` | JSONField | Extracted header + line values pre-correction |
| `fields_corrected_count` | PositiveIntegerField | Number of field corrections made |
| `is_touchless` | BooleanField (indexed) | True if approved without any corrections |

**Indexes**: `status`, `is_touchless`

### 4.3 ExtractionFieldCorrection

**Table**: `extraction_field_correction` | **File**: `apps/extraction/models.py` | **Inherits**: `TimestampMixin`

Records individual field corrections for granular analytics.

| Field | Type | Description |
|-------|------|-------------|
| `approval` | FK → ExtractionApproval | Parent approval |
| `entity_type` | CharField | `'header'` or `'line_item'` |
| `entity_id` | PositiveIntegerField (nullable) | PK of InvoiceLineItem (for line corrections) |
| `field_name` | CharField | Name of the corrected field |
| `original_value` | TextField | Value before correction |
| `corrected_value` | TextField | Value after correction |
| `corrected_by` | FK → User (nullable) | User who made the correction |

### 4.4 Related Document Models

**Invoice** (`documents_invoice`) — stores raw + normalized invoice header fields:

- **Raw fields**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`, `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date`
- **Normalized fields**: `invoice_number`, `normalized_invoice_number`, `invoice_date`, `po_number`, `normalized_po_number`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `due_date` (DateField), `vendor_tax_id` (CharField 100), `buyer_name` (CharField 255), `tax_percentage` (Decimal 7,4), `tax_breakdown` (JSONField `{cgst, sgst, igst, vat}`)
- **Extraction metadata**: `extraction_confidence` (float 0.0–1.0), `extraction_remarks`, `extraction_raw_json`
- **Status**: `status` (InvoiceStatus enum)

> Migration `0009_add_tax_breakdown_vendor_tax_id_buyer_due_date` added the `due_date`, `vendor_tax_id`, `buyer_name`, `tax_percentage`, `tax_breakdown`, `raw_vendor_tax_id`, `raw_buyer_name`, and `raw_due_date` fields.

**InvoiceLineItem** (`documents_invoice_line`) — line items:

- **Raw fields**: `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`
- **Normalized fields**: `description`, `normalized_description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`, `tax_percentage` (Decimal 7,4, nullable)
- **Classification**: `item_category`, `is_service_item`, `is_stock_item`

**DocumentUpload** (`documents_upload`) — file metadata:

- `original_filename`, `file_size`, `file_hash` (SHA-256), `content_type`
- `processing_state` (FileProcessingState enum), `processing_message`
- Azure Blob fields: `blob_path`, `blob_container`, `blob_name`

---

## 5. Services

### 5.0 Observability

All extraction services are decorated with `@observed_service` from `apps/core/decorators.py`. This creates a child trace span, measures duration, writes a `ProcessingLog` entry, and optionally emits an `AuditEvent` for each service method invocation.

#### Langfuse integration

In addition to the Django-native `@observed_service` instrumentation, the
extraction pipeline emits Langfuse traces, generations, and scores at three
specific points. All calls are fail-silent (`try/except`) and never block
extraction.

| Call site | Location | What is emitted |
|---|---|---|
| Agent extraction trace | `InvoiceExtractionAgent.run()` | Root trace `"invoice_extraction"` with `user_id` + `session_id=f"invoice-{invoice_id}"` |
| LLM fallback trace | `InvoiceExtractionAdapter._llm_extract()` | Root trace `"llm_extract_fallback"` + `log_generation` with token counts |
| Extraction approval scores | `ExtractionApprovalService` | `score_trace` calls on auto-approve, human approve, and reject (see below) |

**Approval lifecycle scores** (trace ID: `f"approval-{approval.pk}"`):

| Score name | Value | When |
|---|---|---|
| `extraction_auto_approve_confidence` | 0.0--1.0 | `try_auto_approve()` fires |
| `extraction_approval_decision` | `1.0` (approve) / `0.0` (reject) | Human approve or reject |
| `extraction_approval_confidence` | 0.0--1.0 | Human approve (confidence snapshot) |
| `extraction_corrections_count` | 0.0+ (raw count) | Human approve with corrections |

**Extraction pipeline scores** (`extraction_pipeline.py` Step 9, trace ID: `str(run.pk)`):

| Score name | Value | Meaning |
|---|---|---|
| `extraction_confidence` | 0.0--1.0 | `output.overall_confidence` (guarded with `or 0.0`) |
| `extraction_requires_review` | 0.0 or 1.0 | `routing.needs_review` |

**Bulk extraction user attribution**: `InvoiceExtractionAdapter.extract()` accepts
`actor_user_id` kwarg forwarded from `process_invoice_upload_task`. This ensures
bulk jobs appear under the correct user in the Langfuse Users tab:

```python
adapter.extract(file_path, actor_user_id=upload.uploaded_by_id)
```

Full Langfuse reference: `docs/LANGFUSE_INTEGRATION.md`

### 5.1 InvoiceExtractionAdapter

**File**: `apps/extraction/services/extraction_adapter.py`  
**Decorator**: `@observed_service("extraction.extract", entity_type="DocumentUpload", audit_event="EXTRACTION_STARTED")`

Orchestrates the two-stage extraction pipeline:

**Stage 1 — Text Extraction** (OCR or native, controlled by `ocr_enabled` flag):
```python
ocr_enabled = self._is_ocr_enabled()  # Check ExtractionRuntimeSettings → settings.EXTRACTION_OCR_ENABLED
if ocr_enabled:
    ocr_text, ocr_page_count, ocr_duration_ms, qr_texts = self._ocr_document(file_path)
else:
    ocr_text, ocr_page_count, ocr_duration_ms = self._extract_text_native(file_path)
    qr_texts = []

# Stage 1c — QR decode (after OCR, before category classification)
qr_data = self._decode_qr(file_path, ocr_text, qr_texts)
```

**`_ocr_document(file_path)`** — Azure Document Intelligence:
- Uses `DocumentAnalysisClient` from `azure.ai.formrecognizer` with `prebuilt-read` model and **`features=[AnalysisFeature.BARCODES]`**
- The `features` kwarg is **required** for barcode extraction — without it, `page.barcodes` is always empty even when the document contains QR codes
- Concatenates all pages' text lines; collects `kind="QRCode"` barcode values into `qr_texts`
- Returns `(text, page_count, duration_ms, qr_texts)` **4-tuple** (changed from 3-tuple)
- Credentials: `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`
- Cost: $1.50 per 1,000 pages (barcode add-on is free)

> **Azure DI barcode API notes:**
> - Available from `azure-ai-formrecognizer >= 3.3.0` (API version `2023-07-31`)
> - Barcode `kind` is `"QRCode"` (PascalCase) — the code calls `.upper()` before comparing, making it case-insensitive
> - If `page.barcodes` attribute is absent (older SDK), `getattr(page, "barcodes", [])` returns `[]` safely
> - When Azure DI does not decode the QR (e.g. very small/distorted QR), the pipeline falls through to OCR-text regex and pyzbar strategies automatically
> - The QR value returned by Azure DI is typically a **NIC-signed JWT** (RS256, `iss="NIC"`), not plain JSON. `QRCodeDecoderService.decode_from_texts()` calls `_unwrap_jwt()` before attempting JSON parsing.

**`_extract_text_native(file_path)`** — PyPDF2 fallback (no OCR cost):
- Uses `PyPDF2.PdfReader` to extract embedded text layer from native PDFs
- Returns `(text, page_count, duration_ms)` — same tuple shape
- No Azure DI call — zero OCR cost, near-instant
- Useful for accuracy comparison testing

**`_is_ocr_enabled()`** — Two-tier flag check:
1. `ExtractionRuntimeSettings.get_active().ocr_enabled` (DB, toggleable from Extraction Control Center UI)
2. Fallback: `settings.EXTRACTION_OCR_ENABLED` (env var, default: `True`)

**Stage 2 — LLM Extraction**:
```python
raw_json, agent_run_id = _agent_extract(ocr_text, document_upload_id=document_upload_id)
```
- Instantiates `InvoiceExtractionAgent()`
- Returns JSON + `AgentRun.pk` for traceability
- After the `AgentRun` record is created, the method immediately stamps `AgentRun.document_upload_id` via a targeted `UPDATE` (`AgentRun.objects.filter(pk=...).update(document_upload_id=...)`) so that all runs for a given upload are queryable via `AgentRun.objects.filter(document_upload_id=...)`

**`extract()` signature** (`InvoiceExtractionAdapter.extract`):
```python
def extract(self, file_path: str, document_upload_id: Optional[int] = None) -> ExtractionResponse:
```
The `document_upload_id` parameter is supplied by the Celery task (`process_invoice_upload_task`) and passed through to `_agent_extract()`. It is optional — if `None`, the AgentRun FK is simply not set (backward-compatible).

**Engine name tracking**: `engine_name` is set to `"azure_di_gpt4o_agent"` when OCR is used, or `"native_pdf_gpt4o_agent"` when native extraction is used. This allows filtering and comparing accuracy by extraction method.

**Returns**: `ExtractionResponse` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether extraction succeeded |
| `raw_json` | dict | Extracted JSON data (contains `_repair`, `_qr`, `_prompt_meta` etc.) |
| `confidence` | float | 0.0–1.0 confidence |
| `engine_name` | str | `"azure_di_gpt4o_agent"` (OCR) or `"native_pdf_gpt4o_agent"` (no OCR) |
| `engine_version` | str | `"2.0"` |
| `duration_ms` | int | Extraction duration |
| `error_message` | str | Error details if failed |
| `ocr_text` | str | Raw OCR text |
| `ocr_page_count` | int | Number of pages processed (default: 0) |
| `ocr_duration_ms` | int | OCR processing duration in ms (default: 0) |
| `ocr_char_count` | int | Characters extracted (default: 0) |
| `invoice_category` | str | `"goods"` / `"service"` / `"travel"` from category classifier |
| `category_confidence` | float | Category classification confidence |
| `prompt_components` | dict | Modular prompt component keys used |
| `prompt_hash` | str | SHA-256 of the final composed prompt |
| `was_repaired` | bool | Whether `ResponseRepairService` made any change |
| `repair_actions` | list | List of repair action strings applied |
| `qr_data` | `QRInvoiceData \| None` | Decoded e-invoice QR payload (see §23); `None` when no QR found |

**Fallback**: Direct LLM extraction without agent framework via `_llm_extract(ocr_text)` — uses `response_format={"type": "json_object"}`, temperature=0.0, max_tokens=4096.

### 5.2 ExtractionParserService

**File**: `apps/extraction/services/parser_service.py`  
**Decorator**: `@observed_service("extraction.parse", entity_type="ExtractionResult")`

Parses raw JSON → structured dataclasses:

- **ParsedInvoice**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`, `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date`, `raw_tax_percentage`, `raw_tax_breakdown` (dict), `confidence`, `line_items`
- **ParsedLineItem**: `line_number`, `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`, `raw_tax_percentage`

Flexible field mapping (e.g., accepts both `item_description` and `description`). Validates that `tax_breakdown` is a dict (defaults to `{}` if the LLM returns a non-dict value).

### 5.3 NormalizationService

**File**: `apps/extraction/services/normalization_service.py`  
**Decorator**: `@observed_service("extraction.normalize", entity_type="Invoice")`

Normalizes parsed values to proper types:

| Operation | Detail |
|-----------|--------|
| Vendor name | `normalize_string()` — lowercase, strip, remove diacritics |
| Invoice number | `normalize_invoice_number()` — strip spaces/dashes/special chars |
| PO number | `normalize_po_number()` — same normalization |
| Date | `parse_date()` — flexible parsing (DD/MM/YYYY, YYYY-MM-DD, etc.) — used for both `invoice_date` and `due_date` |
| Currency | `parse_currency()` — fallback to `"USD"` |
| Amounts | `to_decimal()` — parse currency strings to `Decimal` — used for `subtotal`, `tax_amount`, `total_amount`, `tax_percentage`, and line amounts |
| Line items | Same normalization per line (includes `tax_percentage`) |
| Tax breakdown | `_normalize_tax_breakdown(raw)` — coerces `cgst`, `sgst`, `igst`, `vat` keys to `float`; defaults missing keys to `0.0` |

**New fields added to `NormalizedInvoice`**:
- `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date`, `raw_tax_percentage` — raw string carry-throughs
- `raw_tax_breakdown` — raw dict carry-through
- `vendor_tax_id` (str) — passthrough of the GSTIN/VAT identifier
- `buyer_name` (str) — billed-to entity name
- `due_date` (Optional[date]) — parsed payment due date
- `tax_percentage` (Optional[Decimal]) — headline tax rate percentage
- `tax_breakdown` (dict) — cleaned `{cgst, sgst, igst, vat}` dict (all floats, defaults 0.0)

**New fields added to `NormalizedLineItem`**:
- `raw_tax_percentage` (str) — raw string from LLM
- `tax_percentage` (Optional[Decimal]) — parsed line-level tax rate

Utility functions live in `apps/core/utils.py`.

### 5.4 ValidationService

**File**: `apps/extraction/services/validation_service.py`  
**Decorator**: `@observed_service("extraction.validate", entity_type="Invoice")`

Returns `ValidationResult` with `is_valid`, `errors`, and `warnings`.

**Errors** (blocking — marks invoice as INVALID):
- `normalized_invoice_number` missing
- `vendor_name_normalized` missing
- `total_amount` missing or non-numeric
- `tax_percentage` is not a valid Indian GST slab when `tax_breakdown` contains `cgst`/`sgst`/`igst` keys (see GST rate validation below)

**Warnings** (non-blocking):
- `normalized_po_number` missing (will require agent lookup)
- `invoice_date` unparseable
- `subtotal` missing
- No line items extracted
- Low extraction confidence (< `EXTRACTION_CONFIDENCE_THRESHOLD` = 0.75)
- Line item missing quantity / unit_price / description

#### GST Rate Validation

When a GST invoice is detected (any of `cgst`, `sgst`, `igst` keys present in `tax_breakdown`), `tax_percentage` must be one of the recognised Indian GST slabs. Any value outside the valid set causes a blocking **error** (invoice status → INVALID); the user must correct the field manually.

**Standard slabs**: `{0, 3, 5, 12, 18, 28}` percent.

**Special case — 0.25% for precious/semi-precious stones**: The 0.25% slab (GST Schedule I, Chapter 71, HSN headings 7102–7104) is permitted **only** when `_is_precious_stone_invoice()` returns `True`. This helper scans all line item description fields (`description`, `normalized_description`, `raw_description`) and the vendor name for keywords:

> `diamond`, `diamonds`, `gemstone`, `gem stone`, `gems`, `precious stone`, `semi-precious`, `ruby`, `rubies`, `emerald`, `sapphire`, `pearl`, `pearls`, `topaz`, `opal`, `amethyst`, `tanzanite`, `alexandrite`, `spinel`, `tourmaline`, `rough stone`, `rough gem`, and Chapter 71 HSN code substrings `7102`–`7104`.

If 0.25% is present for an invoice that does not match any precious stone keyword, it is still rejected as invalid.

| Invoice type | Valid `tax_percentage` values |
|---|---|
| Standard GST invoice | `{0, 3, 5, 12, 18, 28}` |
| GST invoice with precious stone line items | `{0, 0.25, 3, 5, 12, 18, 28}` |

**Repair service alignment**: `ResponseRepairService._GST_STANDARD_RATES` is `(0, 0.25, 3, 5, 12, 18, 28)` so that `_extract_gst_rate_from_ocr()` and `_repair_tax_percentage()` accept 0.25% as a valid OCR-scanned rate without triggering an additional repair action.

### 5.4a ExtractionConfidenceScorer

**File**: `apps/extraction/services/confidence_scorer.py`  
**Called by**: Pipeline step 4 (after `ValidationService`)

Replaces the LLM's self-reported confidence with a deterministic, auditable score computed from what was actually extracted. Returns a `ConfidenceBreakdown` dataclass with `overall` (0.0–1.0), dimension scores, and a list of penalty reasons.

**Three dimensions (weighted sum)**:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Field coverage | 50% | Were critical/important/optional header fields extracted? |
| Line-item quality | 30% | How complete are the extracted line items? |
| Cross-field consistency | 20% | Do the numbers add up? |

**Field coverage — header field weights** (normalised internally):

| Field | Weight | Notes |
|-------|--------|-------|
| `total_amount` | 5.0 | Critical |
| `invoice_number` | 5.0 | Critical |
| `vendor_name` | 4.0 | Critical |
| `invoice_date` | 3.0 | Important |
| `currency` | 2.0 | Important (USD default gets 50% partial credit) |
| `po_number` | 2.0 | Useful |
| `subtotal` | 1.5 | Useful |
| `tax_amount` | 1.5 | Useful |

Missing fields generate `missing:<field>` penalties.

**Line-item quality — per-line field weights** (normalised internally):

| Field | Weight |
|-------|--------|
| `description` | 3.0 |
| `quantity` | 3.0 |
| `unit_price` | 3.0 |
| `line_amount` | 2.0 |
| `tax_amount` | 1.0 |

Returns average completeness across all lines. Zero line items → `no_line_items` penalty → 0.0 score.

**Cross-field consistency checks**:

| Check | Tolerance | Penalty format |
|-------|-----------|----------------|
| `subtotal + tax_amount ≈ total_amount` | 2% | `total_mismatch:<expected>!=<actual>` |
| `sum(line_amounts) ≈ subtotal` (or total) | 5% | `line_sum_mismatch:<sum>!=<reference>` |
| `qty × unit_price ≈ line_amount` (per line) | 2% | (no per-line penalty to avoid noise) |

If no consistency checks are possible (all values missing), returns 0.5 (neutral).

**Output**: `ConfidenceBreakdown` with `overall`, `field_coverage`, `line_item_quality`, `consistency`, `penalties` list, `llm_original` (preserved for audit comparison). The `overall` score is clamped to [0.0, 1.0] and written to `Invoice.extraction_confidence`.

### 5.4b FieldConfidenceService

**File**: `apps/extraction/services/field_confidence_service.py`
**Called by**: Pipeline step 3a (after `NormalizationService`, before `ValidationService`)

Produces a **per-field confidence map** (0.0–1.0) for every extracted header field and per-line sub-field. Unlike `ExtractionConfidenceScorer` (which produces a single scalar), this service identifies *which* fields are unreliable.

**Scoring bands**:

| Band | Score | Meaning |
|------|-------|---------|
| Explicit + clean | 0.95–1.00 | Field present in LLM output, parsed OK, no repair touching this field |
| Minor repair elsewhere | 0.80–0.94 | Field parsed OK; a repair action ran but did not affect this field |
| Direct repair | 0.60–0.79 | Repair action directly modified this field (e.g., `tax_percentage.recomputed`) |
| Recovered | 0.65 | `invoice_number` recovered from OCR by repair (`invoice_number.recovered_from_ocr`) |
| Suspicious | 0.30–0.59 | Value present but anomalous (zero total, non-3-char currency defaulted) |
| Missing / failed | 0.00–0.29 | Field absent from LLM output or normalization returned None/empty |

**Critical fields** (`CRITICAL_FIELDS`): `invoice_number`, `vendor_name`, `invoice_date`, `currency`, `total_amount`

**Output**: `FieldConfidenceResult` with:
- `header: Dict[str, float]` — per-field score for all header fields
- `lines: List[Dict[str, float]]` — per-line scores (description, line_amount, quantity, unit_price, tax_percentage, tax_amount, line_math)
- `weakest_critical_field: str` — name of the lowest-scoring critical field
- `weakest_critical_score: float` — its score
- `low_confidence_fields: List[str]` — all header fields with score < 0.6
- `evidence_flags: Dict[str, str]` — per-field notes when score was adjusted by evidence (see §5.4e)

**Evidence-aware scoring** (optional params added in Phase 2 hardening):

```python
FieldConfidenceService.score(
    normalized, raw_json, repair_actions,
    ocr_text="...",           # raw OCR text for substring confirmation
    evidence_context={        # extraction evidence hints
        "extraction_method": "repaired",   # explicit|repaired|recovered|derived
        "snippets": {"invoice_number": "INV-001 ..."},
    }
)
```

| Signal | Effect |
|--------|--------|
| `extraction_method=repaired` | Caps critical field scores at 0.78 |
| `extraction_method=recovered` | Caps critical field scores at 0.65 |
| `extraction_method=derived` | Caps critical field scores at 0.55 |
| `extraction_method=explicit` | No cap — baseline scoring applies |
| OCR substring match (≥ 3 chars) | Boosts score by +0.10, capped at 0.95 |
| Evidence snippet present (≥ 2 chars) | Boosts score by +0.05, capped at 0.90 |
| `qr_verified[field]` matches extracted value | Sets score to **0.99**; flag `"qr_confirmed"` |
| `qr_verified[field]` mismatches extracted value | Caps score at **0.40**; flag `"qr_mismatch:extracted=...\|qr=..."` |

**QR verification** (`evidence_context["qr_verified"]` dict, populated from `QRInvoiceData.to_evidence_context()`):
- Applied as step 4 of evidence-aware scoring (after method caps, OCR boost, and snippet boost)
- Comparison is separator-normalised: strips `/`, `-`, and spaces; uppercases both sides before comparing
- Fields verified: `invoice_number`, `invoice_date`, `vendor_tax_id`, `total_amount`
- If extracted value is empty (field absent), QR comparison is **skipped** — the 0.0 score stands
- `QR_MISMATCH` decision code is emitted when any field has `"qr_mismatch"` in its flag (see §5.4e)

**Persistence**: `FieldConfidenceService.to_serializable(result)` is embedded into `raw_response["_field_confidence"]` by the pipeline task before `ExtractionResult` is saved.

**Fail-silent**: Any exception returns an empty `FieldConfidenceResult` and logs a warning. The pipeline continues unchanged.

**Integration with ValidationService**: The result dict is attached to `NormalizedInvoice.field_confidence`. `ValidationService` reads it to detect low-confidence critical fields (see §5.4c).

### 5.4c Critical Field Validation

**File**: `apps/extraction/services/validation_service.py` (extended `ValidationResult`)

`ValidationResult` now carries three additional attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `critical_failures` | `List[str]` | Names of critical fields with `field_confidence < 0.60` |
| `field_review_flags` | `Dict[str, str]` | field → reason string for each failed field |
| `requires_review_override` | `bool` | `True` if any critical field triggered a failure |

When `requires_review_override=True`, the pipeline **skips auto-approval entirely** and routes directly to human review, regardless of the overall `ExtractionConfidenceScorer` score.

**Critical confidence threshold**: 0.60 (hardcoded; critical fields must clear this to avoid forced review).

### 5.4d ReconciliationValidatorService

**File**: `apps/extraction/services/reconciliation_validator.py`
**Called by**: Pipeline step 4a (after `ValidationService`)

Runs 6 deterministic math checks on the normalized invoice. Produces **structured issues** (not just a penalty string) so the UI and audit log can display exactly which math check failed.

| Check | Issue Code | Severity | Tolerance | Condition |
|-------|-----------|---------|-----------|-----------|
| `TOTAL_CHECK` | `TOTAL_MISMATCH` | **ERROR** | 2% | `subtotal + tax_amount ≠ total_amount` |
| `LINE_SUM_CHECK` | `LINE_SUM_MISMATCH` | WARNING | 5% | `Σ line_amounts ≠ subtotal` |
| `LINE_MATH_CHECK` | `LINE_MATH_MISMATCH` | WARNING | 2% per line | `qty × unit_price ≠ line_amount` |
| `TAX_BREAKDOWN_CHECK` | `TAX_BREAKDOWN_MISMATCH` | WARNING | abs 0.50 | `sum(cgst+sgst+igst+vat) ≠ tax_amount` |
| `TAX_PCT_CHECK` | `TAX_PCT_INCONSISTENT` | INFO | 1pp | `(tax_amount/subtotal×100) ≠ tax_percentage` |
| `LINE_TAX_SUM_CHECK` | `LINE_TAX_SUM_MISMATCH` | INFO | 5% | `Σ line.tax_amounts ≠ tax_amount` |

**`is_clean`**: `True` only when no ERROR-severity issues exist (warnings/info are non-blocking).

**Relationship to `ExtractionConfidenceScorer`**: The scorer already performs binary pass/fail consistency checks that feed into the overall confidence score. `ReconciliationValidatorService` is **additive** — it produces granular structured issues without modifying the scorer.

**Persistence**: Serialized via `ReconciliationValidatorService.to_serializable(result)` and embedded into `raw_response["_validation"]`.

**Fail-silent**: Any exception returns an empty `ReconciliationValidationResult(is_clean=True)` and logs a warning.

### 5.4e Decision Codes (`decision_codes.py`)

**File**: `apps/extraction/decision_codes.py`
**Called by**: Pipeline step 4b (after step 4a)

Centralised machine-readable constants + `derive_codes()` helper. Maps pipeline outputs → a list of string codes the routing engine, recovery lane, and audit log can consume without parsing human-readable messages.

**Constants**:

| Code | Trigger |
|------|---------|
| `INV_NUM_UNRECOVERABLE` | `invoice_number` in `critical_failures` |
| `TOTAL_MISMATCH_HARD` | `TOTAL_MISMATCH` in reconciliation issues |
| `LINE_SUM_MISMATCH` | `LINE_SUM_MISMATCH` in reconciliation issues |
| `LINE_TABLE_INCOMPLETE` | > 50% of lines have `line_amount` score < 0.5 |
| `TAX_ALLOC_AMBIGUOUS` | `TAX_BREAKDOWN_MISMATCH` in reconciliation issues |
| `TAX_BREAKDOWN_MISMATCH` | `TAX_BREAKDOWN_MISMATCH` in reconciliation issues |
| `VENDOR_MATCH_LOW` | `vendor_name` in `critical_failures` OR `vendor_name` score < 0.40 |
| `LOW_CONFIDENCE_CRITICAL_FIELD` | Any field in `critical_failures` |
| `PROMPT_COMPOSITION_FALLBACK_USED` | `prompt_source_type` = `"monolithic_fallback"` or `"agent_default"` |
| `RECOVERY_LANE_INVOKED` | Added by task when recovery lane runs |
| `RECOVERY_LANE_SUCCEEDED` | Added by task when recovery lane produces output |
| `RECOVERY_LANE_FAILED` | Added by task when recovery lane errors |
| `QR_IRN_PRESENT` | `qr_data.irn` is a non-empty 64-char string — IRN available for dedup/audit |
| `QR_DATA_VERIFIED` | QR decoded and ≥ 1 field confirmed (no mismatch detected) |
| `QR_MISMATCH` | `"qr_mismatch"` flag in any `evidence_flags` entry — hard review required |
| `IRN_DUPLICATE` | Same IRN seen on a previously processed invoice — hard duplicate |

**`derive_codes(validation_result, recon_val_result, field_conf_result, prompt_source_type, qr_data=None)`**:
- Accepts all five inputs (all optional)
- Returns a deduplicated list of applicable codes in a stable order
- `qr_data` (`QRInvoiceData | None`): emits `QR_IRN_PRESENT` when IRN present; reads `evidence_flags` to choose `QR_DATA_VERIFIED` vs `QR_MISMATCH`
- Fail-silent: returns `[]` on any exception

**`ROUTING_MAP`**: Maps each code → canonical review queue string.

| Code | Queue |
|------|-------|
| `INV_NUM_UNRECOVERABLE`, `TOTAL_MISMATCH_HARD`, `LINE_TABLE_INCOMPLETE`, `IRN_DUPLICATE` | `EXCEPTION_OPS` |
| `TAX_ALLOC_AMBIGUOUS`, `TAX_BREAKDOWN_MISMATCH` | `TAX_REVIEW` |
| `VENDOR_MATCH_LOW` | `MASTER_DATA_REVIEW` |
| `QR_MISMATCH`, `LOW_CONFIDENCE_CRITICAL_FIELD`, `LINE_SUM_MISMATCH`, `PROMPT_COMPOSITION_FALLBACK_USED` | `AP_REVIEW` |

**`HARD_REVIEW_CODES`**: `{INV_NUM_UNRECOVERABLE, TOTAL_MISMATCH_HARD, LINE_TABLE_INCOMPLETE, IRN_DUPLICATE, QR_MISMATCH}` — always require human review regardless of confidence score.

**Persistence**: Embedded into `raw_response["_decision_codes"]` and included in `AuditService` metadata.

### 5.4f RecoveryLaneService

**File**: `apps/extraction/services/recovery_lane_service.py`
**Called by**: Pipeline step 4c (after `derive_codes()`)

Bounded post-extraction anomaly correction via `InvoiceUnderstandingAgent`. Never replaces the original extraction — output is **additive only**.

**Trigger codes** (named failure modes only — generic low confidence does NOT trigger):

```
INV_NUM_UNRECOVERABLE    TOTAL_MISMATCH_HARD    TAX_ALLOC_AMBIGUOUS
VENDOR_MATCH_LOW         LINE_TABLE_INCOMPLETE  PROMPT_COMPOSITION_FALLBACK_USED
```

**API**:

```python
# Step 1 — deterministic policy (no I/O)
decision: RecoveryDecision = RecoveryLaneService.evaluate(decision_codes)
# decision.should_invoke, decision.trigger_codes, decision.recovery_actions

# Step 2 — agent invocation (fail-silent)
result: RecoveryResult = RecoveryLaneService.invoke(
    decision, invoice_id,
    validation_result=..., field_conf_result=..., actor_user_id=...
)
```

**`RecoveryDecision`** (policy output):

| Field | Type | Description |
|-------|------|-------------|
| `should_invoke` | bool | `True` only when a named trigger code is present |
| `trigger_codes` | List[str] | Which codes triggered recovery |
| `recovery_actions` | List[str] | Bounded actions for the agent (e.g., `verify_invoice_number`) |
| `reason` | str | Human-readable explanation |

**`RecoveryResult`** (agent output):

| Field | Type | Description |
|-------|------|-------------|
| `invoked` | bool | Whether the agent was called |
| `succeeded` | bool | Whether agent produced reasoning or evidence |
| `agent_reasoning` | str | Agent's analysis text (truncated to 500 chars in serialization) |
| `agent_confidence` | float | Agent-reported confidence |
| `agent_recommendation` | str | Agent recommendation type |
| `agent_evidence` | dict | Key evidence dict from agent |
| `agent_run_id` | int | FK to `AgentRun` record |
| `error` | str | Empty string if no error; exception message otherwise |

**Recovery action mapping** (per trigger code):

| Code | Actions |
|------|---------|
| `INV_NUM_UNRECOVERABLE` | `verify_invoice_number`, `cross_check_ocr` |
| `TOTAL_MISMATCH_HARD` | `verify_totals`, `recheck_line_sums`, `check_tax` |
| `TAX_ALLOC_AMBIGUOUS` | `verify_tax_breakdown`, `check_tax_type` |
| `VENDOR_MATCH_LOW` | `verify_vendor_name`, `vendor_lookup` |
| `LINE_TABLE_INCOMPLETE` | `verify_line_items`, `recount_lines` |
| `PROMPT_COMPOSITION_FALLBACK_USED` | `full_invoice_review` |

**Persistence**: `RecoveryResult.to_serializable()` embedded into `raw_response["_recovery"]`. `AgentRun.input_payload["_recovery_meta"]` stamped with trigger codes and actions.

**Fail-silent**: Any exception in `invoke()` returns `RecoveryResult(invoked=True, succeeded=False, error=...)` — the pipeline never raises.

### 5.5 DuplicateDetectionService

**File**: `apps/extraction/services/duplicate_detection_service.py`
**Decorator**: `@observed_service("extraction.duplicate_check", entity_type="Invoice")`

Returns `DuplicateCheckResult` with `is_duplicate`, `duplicate_invoice_id`, `reason`.

**Detection checks** (in order):
1. **Exact match**: `normalized_invoice_number` + vendor's `normalized_name`
2. **Amount match**: `normalized_invoice_number` + `total_amount`
3. Excludes invoices already marked as duplicates

### 5.6 InvoicePersistenceService

**File**: `apps/extraction/services/persistence_service.py`  
**Decorator**: `@observed_service("extraction.persist_invoice", entity_type="Invoice", audit_event="INVOICE_PERSISTED")`

Saves normalized invoice + line items to the database.

**Status determination**:
- Invalid validation → `INVALID`
- Valid validation → `VALIDATED`
- No validation → `EXTRACTED`

**Additional logic**:
- Sets `is_duplicate` flag and `duplicate_of_id` if duplicate detected
- **Total reconciliation** (`_reconcile_totals`): Compares line-item sum against extracted header subtotal. Only overrides when line items sum to **more** than the header (indicating the header was misread/truncated). When line items sum to **less**, keeps the original header total (the LLM likely missed some line items). Recomputes `total_amount = new_subtotal + tax_amount`.
- Resolves vendor via `Vendor.normalized_name` or `VendorAlias.normalized_alias`

**New fields persisted** (added in migration `0009_add_tax_breakdown_vendor_tax_id_buyer_due_date`):

*Invoice header fields*:
- `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date` — raw string values from LLM
- `vendor_tax_id` (CharField 100) — GSTIN/VAT/tax registration number
- `buyer_name` (CharField 255) — billed-to entity name
- `due_date` (DateField, nullable) — payment due date parsed from the invoice
- `tax_percentage` (DecimalField 7,4, nullable) — headline tax rate (e.g. 18.0 for 18%)
- `tax_breakdown` (JSONField, default `{}`) — component tax amounts `{cgst, sgst, igst, vat}` as floats

*Line item fields*:
- `tax_percentage` (DecimalField 7,4, nullable) — per-line tax rate percentage

### 5.7 ExtractionResultPersistenceService

**Decorator**: `@observed_service("extraction.persist_result", entity_type="ExtractionResult", audit_event="EXTRACTION_RESULT_PERSISTED")`

Persists `ExtractionResult` record with engine metadata (separate from Invoice data).

**Confidence source**: Prefers `invoice.extraction_confidence` (deterministic score from `ExtractionConfidenceScorer`) over the LLM self-reported `extraction_response.confidence`. Falls back to LLM value only when the deterministic score is unavailable.

**Additional audit events emitted inline**:
- `DUPLICATE_DETECTED` — when `DuplicateCheckResult.is_duplicate` is True
- `VENDOR_RESOLVED` — when vendor is resolved via `Vendor.normalized_name` or `VendorAlias.normalized_alias`

### 5.8 ExtractionApprovalService

**File**: `apps/extraction/services/approval_service.py`  
**Decorators**:
- `create_pending_approval()`: `@observed_service("extraction.create_approval", entity_type="ExtractionApproval", audit_event="EXTRACTION_APPROVAL_PENDING")`
- `try_auto_approve()`: `@observed_service("extraction.try_auto_approve", entity_type="ExtractionApproval")`
- `approve()`: `@observed_service("extraction.approve", entity_type="ExtractionApproval", audit_event="EXTRACTION_APPROVED")`
- `reject()`: `@observed_service("extraction.reject", entity_type="ExtractionApproval", audit_event="EXTRACTION_REJECTED")`

> **Rerun idempotency**: `create_pending_approval()` and `try_auto_approve()` both use `update_or_create(invoice=invoice, defaults={...})` instead of `objects.create()`. This prevents `IntegrityError` on the `OneToOneField` when an invoice is re-extracted — the existing `ExtractionApproval` record is reset to `PENDING` (or `AUTO_APPROVED`) with a fresh data snapshot rather than creating a duplicate row.

See [Section 8: Approval Gate](#8-approval-gate).

### 5.9 UploadService

**File**: `apps/extraction/services/upload_service.py`  
**Decorator**: `@observed_service("extraction.upload", entity_type="DocumentUpload", audit_event="INVOICE_UPLOADED")`

Handles file upload, SHA-256 hash computation, and `DocumentUpload` record creation.

---

## 6. Extraction Core — Multi-Country Extraction Platform

The `apps/extraction_core/` app is a fully governed, multi-country, schema-driven extraction platform. It provides 13 data models, 30 service classes, 60+ API endpoints, and full Django admin coverage. It extends the base extraction pipeline (`apps/extraction/`) with document intelligence, multi-page support, jurisdiction-aware schema-driven extraction, confidence scoring, master data enrichment, review routing, evidence capture, analytics/learning, and country pack governance.

### Architecture

```
                            ┌─────────────────────────────────────┐
                            │    Extraction Core Platform          │
                            │                                      │
  ┌───────────────┐         │  Configuration Layer                 │
  │ TaxJurisdiction│◄────────┤  ├─ TaxJurisdictionProfile          │
  │   Profile      │         │  ├─ ExtractionSchemaDefinition      │
  └───────────────┘         │  ├─ ExtractionRuntimeSettings        │
                            │  └─ EntityExtractionProfile          │
                            │                                      │
                            │  Execution Layer                     │
  ┌───────────────┐         │  ├─ ExtractionRun (tracks pipeline)  │
  │ ExtractionRun  │◄────────┤  ├─ ExtractionFieldValue            │
  │   + children   │         │  ├─ ExtractionLineItem              │
  └───────────────┘         │  ├─ ExtractionEvidence               │
                            │  ├─ ExtractionIssue                  │
                            │  ├─ ExtractionApprovalRecord         │
                            │  └─ ExtractionCorrection             │
                            │                                      │
                            │  Governance Layer                    │
  ┌───────────────┐         │  ├─ CountryPack                      │
  │  CountryPack   │◄────────┤  └─ ExtractionAnalyticsSnapshot     │
  └───────────────┘         └─────────────────────────────────────┘
```

### 4-Tier Jurisdiction Resolution

Resolution follows a strict precedence cascade:

| Tier | Source | Service | When Used |
|------|--------|---------|-----------|
| 1 | Document-level declared | `JurisdictionResolutionService` | Caller provides explicit country/regime |
| 2 | Entity profile | `EntityExtractionProfile` | Vendor has configured extraction preferences |
| 3 | System-level settings | `ExtractionRuntimeSettings` | Global defaults (AUTO/FIXED/HYBRID mode) |
| 4 | Auto-detection fallback | `JurisdictionResolverService` | Multi-signal scoring (GSTIN→IN, TRN→AE, VAT→SA) |

**Modes**: AUTO (always detect), FIXED (use configured), HYBRID (detect + validate + mismatch warnings)

### ExtractionPipeline (11-Stage Governed Pipeline)

**File**: `apps/extraction_core/services/extraction_pipeline.py`  
**Class**: `ExtractionPipeline`

| Stage | Service | Description |
|-------|---------|-------------|
| 1 | `JurisdictionResolutionService` | 4-tier jurisdiction resolution |
| 2 | `SchemaRegistryService` | Jurisdiction-aware schema selection |
| 3 | `PromptBuilderService` | Dynamic prompt from schema + jurisdiction |
| 4 | `PageParser` | Multi-page OCR segmentation, header/footer dedup |
| 5 | Deterministic extraction | Rule-based field extraction from OCR text |
| 5a | `TableStitcher` + `LineItemExtractor` | Cross-page table reconstruction + line item extraction |
| 5b | `LLMExtractionAdapter` | LLM-based extraction for remaining/low-confidence fields |
| 6 | `EnhancedNormalizationService` | Country-specific field normalization (dates, amounts, tax IDs) |
| 7 | `EnhancedValidationService` | Country-aware validation with ExtractionIssue persistence |
| 7b | `MasterDataEnrichmentService` | Post-extraction vendor matching, PO lookup, confidence adjustments |
| 8 | `ConfidenceScorer` | Multi-dimensional confidence scoring (header/tax/line/jurisdiction) |
| 8b | `EvidenceCaptureService` | Capture field provenance (snippets, pages, bounding boxes) |
| 9 | `ReviewRoutingEngine` | Queue-based review routing with priority tiers |
| 10 | Persist | Save `ExtractionRun` + field values + line items + evidence + issues |
| 11 | `ExtractionAuditService` | Emit audit events for each pipeline stage |

Each stage emits a governance audit event (e.g., `JURISDICTION_RESOLVED`, `SCHEMA_SELECTED`, `EVIDENCE_CAPTURED`, `REVIEW_ROUTE_ASSIGNED`).

> **Dataclass naming**: The runtime dataclass is `ExtractionExecutionResult` (in `extraction_service.py`) to avoid collision with the Django model `ExtractionResult` (in `apps/extraction/models.py`). A backward-compatible alias `ExtractionResult = ExtractionExecutionResult` is provided.

### ExtractionService (Legacy Pipeline Orchestrator)

**File**: `apps/extraction_core/services/extraction_service.py`  
**Class**: `ExtractionService`

The original pipeline orchestrator. Coordinates jurisdiction → schema → deterministic extraction → LLM fallback → normalization → validation → enrichment → confidence → routing → persistence.

> The `ExtractionExecutionResult` dataclass returned by this service was previously named `ExtractionResult`. The rename avoids collision with the Django model of the same name in `apps/extraction/models.py`.

### Data Models (13 models)

#### Configuration Models

**TaxJurisdictionProfile** — Tax jurisdiction master data:
- `country_code`, `country_name`, `tax_regime`, `regime_full_name`, `default_currency`
- `tax_id_label`, `tax_id_regex`, `date_formats` (JSON), `locale_code`, `fiscal_year_start_month`
- Unique: (`country_code`, `tax_regime`)

**ExtractionSchemaDefinition** — Versioned extraction schema per jurisdiction:
- `jurisdiction` (FK), `document_type`, `schema_version`, `name`, `description`
- `header_fields_json`, `line_item_fields_json`, `tax_fields_json`, `config_json`
- Unique: (`jurisdiction`, `document_type`, `schema_version`)
- Method: `get_all_field_keys()` returns combined field list

**ExtractionRuntimeSettings** — Singleton system-level configuration:
- `jurisdiction_mode` (AUTO|FIXED|HYBRID), `default_country_code`, `default_regime_code`
- `enable_jurisdiction_detection`, `allow_manual_override`, `confidence_threshold_for_detection`
- `fallback_to_detection_on_schema_miss`
- Classmethod: `get_active()` returns current active record

**EntityExtractionProfile** — Per-vendor extraction preferences:
- `entity` (OneToOne Vendor), `default_country_code`, `default_regime_code`
- `jurisdiction_mode`, `schema_override_code`, `validation_profile_override_code`, `normalization_profile_override_code`

#### Execution/Tracking Models

**ExtractionRun** — Primary execution record (~25 fields):
- Status: PENDING|RUNNING|COMPLETED|FAILED|CANCELLED
- Jurisdiction: `country_code`, `regime_code`, `jurisdiction_source` (FIXED|ENTITY|AUTO_DETECTED), FK to TaxJurisdictionProfile
- Schema: `schema_code`, `schema_version`, FK to ExtractionSchemaDefinition
- Confidence: `overall_confidence`, `header_confidence`, `tax_confidence`, `line_item_confidence`, `jurisdiction_confidence`
- Output: `extracted_data_json`, `extraction_method`, `error_message`
- Review: `review_queue`, `requires_review`, `review_reasons_json`
- Timing: `started_at`, `completed_at`, `duration_ms`
- Metrics: `field_count`, `mandatory_coverage_pct`, `field_coverage_pct`
- Indexes: (`country_code`, `regime_code`), (`status`, `created_at`)

**ExtractionFieldValue** — Per-field result with confidence & correction tracking:
- `extraction_run` (FK), `field_code`, `value`, `normalized_value`, `confidence`
- `extraction_method`, `is_corrected`, `corrected_value`, `category` (HEADER|LINE_ITEM|TAX|PARTY)
- `line_item_index`, `is_valid`, `validation_message`
- Index: (`extraction_run`, `field_code`)

**ExtractionLineItem** — Structured line item record:
- `extraction_run` (FK), `line_index`, `data_json`, `confidence`, `page_number`, `is_valid`
- Unique: (`extraction_run`, `line_index`)

**ExtractionEvidence** — Provenance tracking per field:
- `extraction_run` (FK), `field_code`, `page_number`, `snippet` (OCR text)
- `bounding_box` (JSON coords), `extraction_method`, `confidence`, `line_item_index`

**ExtractionIssue** — Validation/extraction issues:
- `extraction_run` (FK), `severity` (ERROR|WARNING|INFO), `field_code`, `check_type`, `message`, `details_json`

**ExtractionApprovalRecord** — Approval gate for run:
- `extraction_run` (OneToOne), `action` (APPROVE|REJECT|ESCALATE|SEND_BACK)
- `approved_by` (FK User), `comments`, `decided_at`

**ExtractionCorrection** — Field correction audit trail:
- `extraction_run` (FK), `field_code`, `original_value`, `corrected_value`
- `correction_reason`, `corrected_by` (FK User)

#### Governance/Analytics Models

**ExtractionAnalyticsSnapshot** — Learning/analytics data:
- `snapshot_type`, `country_code`, `regime_code`, `period_start`, `period_end`
- `data_json`, `run_count`, `correction_count`, `average_confidence`

**CountryPack** — Country governance record:
- `jurisdiction` (OneToOne TaxJurisdictionProfile), `pack_status` (DRAFT|ACTIVE|DEPRECATED)
- `schema_version`, `validation_profile_version`, `normalization_profile_version`
- `activated_at`, `deactivated_at`, `config_json`, `notes`

### Key Dataclasses

**ExtractionOutputContract** (`output_contract.py`):
- `meta` — `MetaBlock` (extraction_id, document_type, jurisdiction, schema, prompt, method, timestamps, duration)
- `fields` — dict of `FieldValue` (value, normalized, confidence, method, evidence list)
- `parties` — `PartiesBlock` (supplier, buyer, ship_to, bill_to)
- `tax` — `TaxBlock` (tax_id, rates, breakdown, totals)
- `line_items` — list of `LineItemRow`
- `references` — `ReferencesBlock` (po_numbers, grn_refs, contracts, shipments)
- `warnings` — list of `WarningItem`

**ExtractionExecutionResult** (dataclass in `extraction_service.py`, aliased as `ExtractionResult` for backward compatibility):
- `fields`, `line_items`, `jurisdiction` (JurisdictionMeta), `document_intelligence` (DocumentIntelligenceResult)
- `enrichment` (EnrichmentResult), `page_info` (ParsedDocument), `confidence_breakdown` (ConfidenceBreakdown)
- `review_decision` (ReviewRoutingDecision), `validation_issues`, `warnings`, `overall_confidence`, `duration_ms`

> **Naming**: The runtime dataclass is `ExtractionExecutionResult` to distinguish it from the Django model `ExtractionResult` (UI-facing summary). The alias `ExtractionResult = ExtractionExecutionResult` remains for backward compatibility.

### Service Directory (30 services)

#### Core Pipeline & Orchestration

| Service | File | Purpose |
|---------|------|---------|
| `ExtractionPipeline` | `extraction_pipeline.py` | 11-stage governed pipeline orchestrator with audit events |
| `ExtractionService` | `extraction_service.py` | Original pipeline orchestrator |
| `BaseExtractionService` | `base_extraction_service.py` | Schema-driven extraction base class |

#### Jurisdiction Resolution

| Service | File | Purpose |
|---------|------|---------|
| `JurisdictionResolverService` | `jurisdiction_resolver.py` | Multi-signal jurisdiction detection (GSTIN, TRN, VAT) |
| `JurisdictionResolutionService` | `resolution_service.py` | 4-tier precedence cascade (document → entity → system → auto-detect) |

#### Schema & Registry

| Service | File | Purpose |
|---------|------|---------|
| `SchemaRegistryService` | `schema_registry.py` | Cached schema lookup (5-min TTL), version-aware |

#### Document Intelligence (Pre-Extraction)

| Service | File | Purpose |
|---------|------|---------|
| `DocumentTypeClassifier` | `document_classifier.py` | Multilingual keyword classification (EN/AR/HI/FR/DE/ES); types: INVOICE, CREDIT_NOTE, DEBIT_NOTE, GRN, PURCHASE_ORDER, DELIVERY_NOTE, STATEMENT. Includes **negative signals** (−2.0 to −3.0) for report-adjacent terms ("reconciliation", "summary report", "3-way", "matching report", "variance report", "audit report") on GRN/PO/DELIVERY_NOTE to prevent false classification of reconciliation/summary reports. |
| `PartyExtractor` | `party_extractor.py` | Supplier/buyer/ship-to/bill-to extraction |
| `RelationshipExtractor` | `relationship_extractor.py` | PO/GRN/contract/shipment cross-reference extraction |
| `DocumentIntelligenceService` | `document_intelligence.py` | Pre-extraction analysis orchestrator |

#### Field Extraction & Parsing

| Service | File | Purpose |
|---------|------|---------|
| `LineItemExtractor` | `line_item_extractor.py` | Schema-driven line item extraction with column mapping |
| `PageParser` | `page_parser.py` | Multi-page segmentation, header/footer dedup |
| `TableStitcher` | `table_stitcher.py` | Cross-page table continuation detection |

#### Normalization & Validation

| Service | File | Purpose |
|---------|------|---------|
| `NormalizationService` | `normalization_service.py` | Jurisdiction-driven field normalization |
| `EnhancedNormalizationService` | `enhanced_normalization.py` | Country-specific normalization (IN/AE/SA/DE/FR currency/date localization) |
| `ValidationService` | `validation_service.py` | Jurisdiction-driven field validation |
| `EnhancedValidationService` | `enhanced_validation.py` | Country-aware validation with ExtractionIssue persistence |

#### Evidence, Audit & Tracing

| Service | File | Purpose |
|---------|------|---------|
| `EvidenceCaptureService` | `evidence_service.py` | Capture field provenance (snippets, pages, bounding boxes) → ExtractionEvidence records |
| `ExtractionAuditService` | `extraction_audit.py` | Extraction-specific audit logging (8 event types per pipeline stage) |

#### Confidence & Review Routing

| Service | File | Purpose |
|---------|------|---------|
| `ConfidenceScorer` | `confidence_scorer.py` | Multi-dimensional scoring for governed pipeline (header=0.3, tax=0.3, line_item=0.2, jurisdiction=0.2) |
| `ReviewRoutingService` | `review_routing.py` | Confidence-driven review routing with priority tiers |
| `ReviewRoutingEngine` | `review_routing_engine.py` | Queue-based routing (EXCEPTION_OPS, TAX_REVIEW, VENDOR_OPS); thresholds: CRITICAL=0.4, LOW=0.65, TAX=0.6. Extended with optional `decision_codes` param — code-based routing runs first (Rule 0) and can short-circuit confidence rules for `HARD_REVIEW_CODES`. |

#### LLM & Prompts

| Service | File | Purpose |
|---------|------|---------|
| `PromptBuilderService` | `prompt_builder.py` | Dynamic LLM prompt from schema + jurisdiction |
| `PromptBuilderService` | `prompt_builder_service.py` | Enhanced prompt builder (global/country/regime/document/schema/tax/evidence sections) |
| `LLMExtractionAdapter` | `llm_extraction_adapter.py` | LLM client wrapper; retry on parse failures |

#### Master Data & Learning

| Service | File | Purpose |
|---------|------|---------|
| `MasterDataEnrichmentService` | `master_data_enrichment.py` | Post-extraction vendor/PO/customer matching + confidence adjustments |
| `LearningFeedbackService` | `learning_service.py` | Analytics from corrections & failures → ExtractionAnalyticsSnapshot |

#### Country Governance

| Service | File | Purpose |
|---------|------|---------|
| `CountryPackService` | `country_pack_service.py` | Multi-country support lifecycle: DRAFT → ACTIVE → DEPRECATED |

#### Output Contract

| Service | File | Purpose |
|---------|------|---------|
| ExtractionOutputContract | `output_contract.py` | Canonical output shape (MetaBlock, FieldValue, PartiesBlock, TaxBlock, LineItemRow, ReferencesBlock) |

### API Endpoints

**Configuration API** (`/api/v1/extraction-core/`):

| Method | Path | View | Description |
|--------|------|------|-------------|
| GET/POST | `/jurisdictions/` | `TaxJurisdictionProfileViewSet` | List/create tax jurisdictions |
| GET/PUT/DELETE | `/jurisdictions/<id>/` | | Retrieve/update/delete |
| GET/POST | `/schemas/` | `ExtractionSchemaDefinitionViewSet` | List/create schemas |
| GET/PUT/DELETE | `/schemas/<id>/` | | Retrieve/update/delete |
| GET | `/schemas/<id>/field-definitions/` | | Get fields for schema |
| GET | `/schemas/<id>/versions/` | | List schema versions |
| GET/POST | `/runtime-settings/` | `ExtractionRuntimeSettingsViewSet` | List/create settings |
| GET/PUT/DELETE | `/runtime-settings/<id>/` | | Retrieve/update/delete |
| GET | `/runtime-settings/active/` | | Get active runtime settings |
| GET/POST | `/entity-profiles/` | `EntityExtractionProfileViewSet` | List/create vendor profiles |
| GET/PUT/DELETE | `/entity-profiles/<id>/` | | Retrieve/update/delete |
| POST | `/resolve-jurisdiction/` | `JurisdictionResolveView` | Simple jurisdiction resolution |
| POST | `/resolve-jurisdiction-full/` | `JurisdictionResolutionView` | Full 4-tier resolution (jurisdiction + schema + config) |
| POST | `/lookup-schema/` | `SchemaLookupView` | Schema lookup by jurisdiction + doc type |
| POST | `/extract/` | `ExtractionView` | Trigger extraction |

**Execution API** (`/api/v1/extraction-pipeline/`):

| Method | Path | View | Description |
|--------|------|------|-------------|
| POST | `/run/` | `RunPipelineView` | Trigger governed extraction pipeline |
| GET | `/runs/` | `ExtractionRunViewSet` | List runs (filter: country, status, queue, requires_review, document) |
| GET | `/runs/<id>/` | | Run detail |
| GET | `/runs/<id>/summary/` | | Lightweight summary |
| GET | `/runs/<id>/fields/` | | List field values |
| GET | `/runs/<id>/line-items/` | | List line items |
| GET | `/runs/<id>/validation/` | | List issues |
| GET | `/runs/<id>/evidence/` | | List evidence records |
| GET | `/runs/<id>/corrections/` | | List corrections |
| POST | `/runs/<id>/correct-field/` | | Correct a field value |
| POST | `/runs/<id>/approve/` | | Approve extraction |
| POST | `/runs/<id>/reject/` | | Reject extraction |
| POST | `/runs/<id>/reprocess/` | | Reprocess extraction |
| POST | `/runs/<id>/escalate/` | | Escalate to review queue |
| GET | `/analytics/` | `ExtractionAnalyticsViewSet` | List analytics snapshots |
| GET/POST | `/country-packs/` | `CountryPackViewSet` | List/create country packs |

### Serializers (~25 classes)

**Configuration serializers** (`serializers.py`): `TaxJurisdictionProfileSerializer`, `TaxJurisdictionProfileListSerializer`, `ExtractionSchemaDefinitionSerializer`, `ExtractionSchemaDefinitionListSerializer`, `ExtractionRuntimeSettingsSerializer`, `EntityExtractionProfileSerializer`, `EntityExtractionProfileListSerializer`

**Request serializers**: `JurisdictionResolveRequestSerializer`, `JurisdictionResolutionRequestSerializer`, `SchemaLookupRequestSerializer`, `ExtractionRequestSerializer`

**Execution serializers** (`extraction_serializers.py`): `ExtractionRunListSerializer`, `ExtractionRunDetailSerializer`, `ExtractionRunSummarySerializer`, `ExtractionFieldValueSerializer`, `ExtractionLineItemSerializer`, `ExtractionEvidenceSerializer`, `ExtractionIssueSerializer`, `ExtractionApprovalRecordSerializer`, `ExtractionCorrectionSerializer`, `ExtractionAnalyticsSnapshotSerializer`, `CountryPackSerializer`, `ApproveRejectRequestSerializer`, `CorrectFieldRequestSerializer`, `EscalateRequestSerializer`, `RunPipelineRequestSerializer`

### Django Admin (13 models registered)

All models registered in `apps/extraction_core/admin.py` with full admin features:

| Admin Class | List Display Highlights |
|-------------|------------------------|
| `TaxJurisdictionProfileAdmin` | country_code, tax_regime, default_currency, is_active |
| `ExtractionSchemaDefinitionAdmin` | name, jurisdiction, document_type, schema_version, is_active |
| `ExtractionRuntimeSettingsAdmin` | name, jurisdiction_mode, defaults, detection settings |
| `EntityExtractionProfileAdmin` | entity, country_code, regime_code, jurisdiction_mode |
| `ExtractionRunAdmin` | id, document, status, country_code, overall_confidence, review_queue, duration_ms |
| `ExtractionFieldValueAdmin` | extraction_run, field_code, value, confidence, category, is_corrected |
| `ExtractionLineItemAdmin` | extraction_run, line_index, confidence, is_valid |
| `ExtractionEvidenceAdmin` | extraction_run, field_code, page_number, extraction_method |
| `ExtractionIssueAdmin` | extraction_run, severity, field_code, check_type, message |
| `ExtractionApprovalRecordAdmin` | extraction_run, action, approved_by, decided_at |
| `ExtractionCorrectionAdmin` | extraction_run, field_code, original/corrected values, corrected_by |
| `ExtractionAnalyticsSnapshotAdmin` | snapshot_type, country_code, regime_code, run_count, average_confidence |
| `CountryPackAdmin` | jurisdiction, pack_status, schema/validation/normalization versions |

### Migrations

| File | Description |
|------|-------------|
| `0001_initial.py` | Creates initial models |
| `0002_entityextractionprofile_extractionruntimesettings.py` | Adds EntityExtractionProfile + ExtractionRuntimeSettings |
| `0003_add_extraction_run_pipeline_models.py` | Adds ExtractionRun + all pipeline tracking models |

---

## 7. Master Data Enrichment

**File**: `apps/extraction_core/services/master_data_enrichment.py`  
**Pipeline position**: Step 7b (after validation, before confidence scoring)

The Master Data Enrichment Service matches extracted entities against the system's master data (Vendors, VendorAliases, PurchaseOrders) and adjusts extraction confidence based on match quality.

### Matching Tiers

**Vendor Matching** (`_match_vendor()`) — 3-tier cascade:

| Tier | Match Type | Confidence | Description |
|------|-----------|------------|-------------|
| 1 | `EXACT_TAX_ID` | 0.98 | Exact tax ID match against `Vendor.tax_id` |
| 2 | `ALIAS` | 0.95 | Normalized alias match against `VendorAlias.normalized_alias` |
| 3 | `FUZZY` | 0.70–0.95 | SequenceMatcher fuzzy name match (threshold: 0.70, high: 0.85) |

- Scopes vendor candidates by country (if `country_code` provided)
- Limits to 500 candidates for fuzzy matching
- Uses `_normalize_name()` — lowercase, strip company suffixes (Pvt Ltd, GmbH, LLC, etc.), collapse whitespace, remove punctuation

**Customer Matching** (`_match_customer()`):
- Checks `VendorAlias` table first (buyer may appear as alias)
- Falls back to fuzzy match against `PurchaseOrder.buyer_name` values

**PO Lookup** (`_lookup_po()`):
- Exact match on `PurchaseOrder.po_number`
- Falls back to normalized match on `PurchaseOrder.normalized_po_number`
- Uses `_normalize_po_number()` — uppercase, remove separators

### Confidence Adjustments

| Adjustment | Value | Condition |
|-----------|-------|----------|
| `VENDOR_MATCH_BOOST` | +0.05 | Vendor matched (any tier) |
| `VENDOR_MISMATCH_PENALTY` | −0.08 | Tax ID present but no vendor found |
| `PO_MATCH_BOOST` | +0.05 | PO number found in system |
| `PO_VENDOR_MATCH_BOOST` | +0.03 | Cross-validated: PO vendor = matched vendor |

- Warns on PO vendor mismatch (PO belongs to different vendor than matched)
- All adjustments are clamped to 0.0–1.0 range

### Dataclasses

- `MasterDataMatch` — match_type, entity_id, entity_code, entity_name, matched_value, similarity, confidence
- `POLookupResult` — found, po_id, po_number, vendor_id, vendor_name, po_status, total_amount, currency, confidence
- `EnrichmentResult` — vendor_match, customer_match, po_lookup, confidence_adjustments, warnings, duration_ms; properties: `vendor_id`, `customer_id`, `match_confidence`

### Integration

The enrichment result is:
- Stored in `ExtractionResult.enrichment` dataclass field
- Serialized in `to_dict()` for JSON persistence
- Persisted to `extracted_data_json` on `ExtractionDocument`
- Displayed in the Extraction Review Console (Master Data Matches card)

---

## 8. Approval Gate

### Overview

Every extracted invoice must pass through a human approval step before entering reconciliation. This ensures extraction quality while building analytics for future automation.

### Dual-Model Pattern

Approval state is tracked in **two models** serving different purposes:

| Model | App | Owner | Purpose |
|-------|-----|-------|---------|
| `ExtractionApproval` | `apps/extraction` | `ExtractionApprovalService` | **Business state machine** — drives Invoice status transitions, tracks field corrections, computes touchless rate. OneToOne with Invoice. |
| `ExtractionApprovalRecord` | `apps/extraction_core` | `GovernanceTrailService` | **Governance mirror** — immutable audit record per ExtractionRun. Written exclusively by `GovernanceTrailService`. OneToOne with ExtractionRun. |

Both records are created on every approval/rejection:
1. `ExtractionApprovalService.approve()` / `.reject()` updates `ExtractionApproval` (business state) then calls `GovernanceTrailService.record_approval_decision()` to write `ExtractionApprovalRecord` (governance trail).
2. `ExtractionRunViewSet.approve()` / `.reject()` (governed API) delegates entirely to `GovernanceTrailService.record_approval_decision()` — no direct `ExtractionApprovalRecord` writes in the viewset.

**GovernanceTrailService uses `update_or_create(extraction_run=run, defaults={...})`** inside `transaction.atomic()`, so re-decisions (e.g., second approval after reprocess) safely update the existing record rather than violating the OneToOne constraint.

### Approval Flow

```
Extraction Complete
       │
       ▼
  Auto-approve enabled AND confidence ≥ threshold?
       │
  ┌────┴────┐
  YES       NO
  │         │
  ▼         ▼
AUTO_APPROVED  PENDING_APPROVAL
(is_touchless=True)  │
  │         │
  ▼         ▼
READY_FOR_RECON  Approval Queue UI
  │         │
  ▼    ┌────┴─────────┐
AP Case   APPROVE  REJECT  REPROCESS
(already    │       │       │
 exists)    ▼       ▼       ▼
   READY_FOR_RECON  INVALID  New ExtractionRun created
   (case resumes)   (re-extract)  ExtractionApproval reset to PENDING
                                  ExtractionApprovalRecord history retained

   Note: AP Case is created immediately after extraction (before approval).
   The case pipeline pauses at EXTRACTION_APPROVAL stage if the invoice
   needs human approval.  On approve, the existing case resumes from
   PATH_RESOLUTION onward.  On reject, the case remains paused.

   ─────── Both records written on every decision ───────
   ExtractionApproval (business)  ←  ExtractionApprovalService
   ExtractionApprovalRecord (governance)  ←  GovernanceTrailService
```

### Reprocess Behavior

When an extraction is reprocessed:
- A **new** `ExtractionRun` is created (the old run remains for audit history)
- `ExtractionApproval.status` resets to `PENDING` (same record, updated in place)
- The previous `ExtractionApprovalRecord` is retained (immutable history per run)
- A new `ExtractionApprovalRecord` is created for the new run upon the next approval/rejection
- **Credit reserve**: 1 credit is reserved (`reference_type="reprocess"`, `reference_id=upload.pk`) before re-extraction starts
- **Finalization guard**: Reprocess is blocked if the current `ExtractionApprovalRecord` has status `APPROVED` or `AUTO_APPROVED`. Both `extraction_rerun` (template view) and `ExtractionRunViewSet.reprocess()` (API) enforce this — API returns HTTP 409 CONFLICT

### Concurrency & Locking

Approval and rejection operations use row-level locking to prevent race conditions:

- **`ExtractionApprovalService.approve()`** / **`.reject()`**: Re-fetch the `ExtractionApproval` row with `select_for_update()` inside `@transaction.atomic` before checking the `PENDING` precondition. This serializes concurrent approve/reject attempts on the same invoice.
- **CreditService**: All balance-mutating methods (`reserve`, `consume`, `refund`, `allocate`, `adjust`) use `select_for_update()` on `UserCreditAccount` inside `transaction.atomic()`.
- **GovernanceTrailService**: Uses `update_or_create()` inside `transaction.atomic()` — safe against parallel writes to the same ExtractionRun's approval record.

Valid state transitions for `ExtractionApproval.status`:
```
PENDING → APPROVED   (approve)
PENDING → REJECTED   (reject)
PENDING → PENDING    (reprocess resets, then re-enters queue)
AUTO_APPROVED → ×    (terminal state, no further transitions)
```

### Service Methods

**`create_pending_approval(invoice, extraction_result)`**
- Uses `update_or_create(invoice=invoice, defaults={...})` to create or reset the `ExtractionApproval` record
- On first run: creates with `status=PENDING`; on rerun: resets `status=PENDING`, clears `reviewed_by`, `reviewed_at`, `is_touchless=False`
- Snapshots current header + line values as `original_values_snapshot`
- Logs "Created" vs "Reset existing" for observability
- Called when auto-approval is not triggered

**`try_auto_approve(invoice, extraction_result)`**
- Checks `EXTRACTION_AUTO_APPROVE_ENABLED` setting (default: `false`)
- If enabled and confidence >= `EXTRACTION_AUTO_APPROVE_THRESHOLD` (default: `1.1` — effectively disabled):
  - Uses `update_or_create(invoice=invoice, defaults={...})` to create or reset the `ExtractionApproval` record with `status=AUTO_APPROVED, is_touchless=True, reviewed_at=timezone.now()`
  - Sets `invoice.status = READY_FOR_RECON`
  - Returns the approval object
- Otherwise returns `None`

**`approve(approval, user, corrections=None)`**
- Locks the `ExtractionApproval` row with `select_for_update()` and verifies `status == PENDING`
- Applies field corrections to Invoice + LineItems
- Creates `ExtractionFieldCorrection` records for each changed field
- Sets `is_touchless = (len(corrections) == 0)`
- Transitions invoice to `READY_FOR_RECON`
- Logs `EXTRACTION_APPROVED` audit event
- Calls `GovernanceTrailService.record_approval_decision()` to write governance mirror

**`reject(approval, user, reason)`**
- Locks the `ExtractionApproval` row with `select_for_update()` and verifies `status == PENDING`
- Sets `status = REJECTED` with `rejection_reason`
- Transitions invoice to `INVALID`
- Logs `EXTRACTION_REJECTED` audit event
- Calls `GovernanceTrailService.record_approval_decision()` to write governance mirror

**`get_approval_analytics()`**
- Returns analytics dict: `total`, `pending`, `approved`, `auto_approved`, `rejected`, `touchless_count`, `human_corrected_count`, `touchless_rate`, `avg_corrections_per_review`, `most_corrected_fields` (top 10)

### Correctable Fields

| Type | Fields |
|------|--------|
| Header | `invoice_number`, `po_number`, `invoice_date`, `due_date`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `raw_vendor_name`, `vendor_tax_id`, `buyer_name`, `tax_percentage` |
| Line Item | `description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`, `tax_percentage` |

### Auto-Approval Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `EXTRACTION_AUTO_APPROVE_ENABLED` | `false` | Master toggle for auto-approval |
| `EXTRACTION_AUTO_APPROVE_THRESHOLD` | `1.1` | Confidence threshold (1.1 = unreachable = all human) |

**Design rationale**: Auto-approval is deliberately disabled by default so all extractions require human review initially. As the system builds confidence and correction analytics accumulate, the threshold can be lowered (e.g., 0.95) to enable gradual automation.

---

## 9. Agent Framework Integration

### InvoiceExtractionAgent

**File**: `apps/agents/services/agent_classes.py`  
**Type**: `AgentType.INVOICE_EXTRACTION`

A single-shot LLM agent (no tool calls, no ReAct loop) optimized for deterministic JSON extraction.

| Property | Value |
|----------|-------|
| Temperature | 0.0 |
| Max tokens | 4096 |
| Response format | `{"type": "json_object"}` |
| Tools | None (empty list) |
| System prompt | `PromptRegistry.get("extraction.invoice_system")` |

**Execution flow**:
1. Creates `AgentRun` record
2. `_init_messages()` — selects system prompt and **records prompt source**:
   - `ctx.extra["composed_prompt"]` present → uses it, sets `self._prompt_source_type = "composed"`
   - Absent → falls back to `self.system_prompt` (PromptRegistry), sets `self._prompt_source_type = "monolithic_fallback"`
3. Calls LLM with `response_format=json_object`
4. Saves assistant message to `AgentMessage`
5. Parses JSON → `AgentOutput` (with confidence, evidence, reasoning)
6. Finalizes `AgentRun` with output payload + token usage
7. **Persists prompt metadata** to `AgentRun.input_payload["_prompt_meta"]` (fail-silent):

```python
{
    "prompt_source_type": "composed" | "monolithic_fallback",
    "prompt_hash": "abc123...",        # 16-char sha256 from PromptComposition
    "base_prompt_key": "extraction.invoice_base",
    "base_prompt_version": "v3",
    "category_prompt_key": "extraction.goods_overlay",
    "category_prompt_version": "v1",
    "country_prompt_key": "",
    "country_prompt_version": "",
    "invoice_category": "goods",
    "components": {"base": "v3", "goods": "v1"},
}
```

`AgentRun.prompt_version` is set to `prompt_hash[:50]` (or `source_type[:50]` if no hash).
`AgentRun.invocation_reason` is set to `"extraction:<source_type>"`.

**Prompt source precedence**:
1. `ctx.extra["composed_prompt"]` — modular composed prompt from `InvoicePromptComposer`
2. `self.system_prompt` → `PromptRegistry.get("extraction.invoice_system")` — monolithic fallback
3. If PromptRegistry also fails, the agent errors (not a silent fallback)

When path 2 is taken, `PROMPT_COMPOSITION_FALLBACK_USED` decision code is emitted in step 4b.

**Traceability**:
- `AgentRun` — execution metadata, LLM model, token usage, duration, `prompt_version`, `invocation_reason`
- `AgentRun.input_payload["_prompt_meta"]` — full prompt source audit trail
- `AgentMessage` — system, user, and assistant messages
- No `AgentStep` or `ToolCall` records (single-shot, no tool loop)

### InvoiceUnderstandingAgent

**File**: `apps/agents/services/agent_classes.py`  
**Type**: `AgentType.INVOICE_UNDERSTANDING`

A deeper analysis agent that runs after extraction for low-confidence or ambiguous results. Uses the full ReAct loop with tools.

| Property | Value |
|----------|-------|
| System prompt | `PromptRegistry.get("agent.invoice_understanding")` |
| Tools | `invoice_details`, `po_lookup`, `vendor_search` |
| Max iterations | 6 (inherited from `BaseAgent`) |

**When invoked**: Two invocation paths:

1. **Case orchestrator path** (original) — runs for low-confidence extractions or when validation warnings are present during case processing.
2. **Recovery lane path** (Phase 2 hardening) — invoked by `RecoveryLaneService` during the extraction pipeline (step 4c) when named failure modes are detected. In this path, `ctx.reconciliation_result=None` and `ctx.extra` carries `recovery_trigger_codes`, `recovery_actions`, `validation_warnings`, and `low_confidence_fields`. The agent's `AgentRun.input_payload["_recovery_meta"]` is stamped with the trigger context, and `invocation_reason` is set to `"RECOVERY_LANE"`.

---

## 10. LLM Prompts

The prompt layer has three builder strategies. The **modular composition** path is the outer primary pipeline; the **schema-driven v2.0** path runs *inside* it at step 1b as a governed enrichment layer (fail-silent, wrapped in `try/except`). Agent and case prompts are resolved via the monolithic `PromptRegistry` path.

> **Key relationship**: Callers always invoke `InvoiceExtractionAgent` (modular composition). `InvoiceExtractionAgent` internally calls `ExtractionPipeline.run()` at step 1b via `_run_governed_pipeline()`. The schema-driven path is *not* a parallel entry point that callers choose -- it runs automatically inside Path A and produces governance records only. If it fails, Path A continues and labels the result "Legacy source".

| Path | Role | Primary class | File | Used by |
|---|---|---|---|---|
| **Modular composition** | Primary outer pipeline | `InvoicePromptComposer` | `apps/extraction/services/invoice_prompt_composer.py` | `InvoiceExtractionAgent` -- all Celery tasks and direct callers |
| **Schema-driven v2.0** | Step 1b enrichment (fail-silent, inside Path A) | `PromptBuilderService` | `apps/extraction_core/services/prompt_builder_service.py` | `ExtractionPipeline`, `LLMExtractionAdapter` -- called from inside `InvoiceExtractionAgent` |
| **Monolithic** | Agent + case resolution | `PromptRegistry` | `apps/core/prompt_registry.py` | All 8 ReAct agents, case-level calls, `InvoiceUnderstandingAgent` |

---

### 10.1 Monolithic path -- `PromptRegistry`

**File**: `apps/core/prompt_registry.py`

Used by the agent-based extraction path and all 8 ReAct agents. Resolution order (highest to lowest priority):

1. **Langfuse** (name: `extraction-invoice_system`, label `production`, 60s in-process TTL cache)
2. **Database** -- `PromptTemplate` model (slug `extraction.invoice_system`, `is_active=True`)
3. **Hardcoded default** -- `_DEFAULTS["extraction.invoice_system"]` in `prompt_registry.py`

```python
from apps.core.prompt_registry import PromptRegistry

prompt = PromptRegistry.get("extraction.invoice_system")
prompt = PromptRegistry.get("agent.exception_analysis", mode_context="3-WAY ...")
```

**18 managed prompts** total -- 2 extraction + 8 agent + 8 overlay/country prompts. Sync to Langfuse:

```bash
python manage.py push_prompts_to_langfuse            # push all to Langfuse (production label)
python manage.py push_prompts_to_langfuse --slug extraction.invoice_system
python manage.py push_prompts_to_langfuse --label staging   # staging label for testing
python manage.py push_prompts_to_langfuse --purge    # delete all then re-seed (fixes misnamed prompts)

python manage.py seed_prompts          # create DB PromptTemplate records for missing slugs
python manage.py seed_prompts --force  # overwrite existing with hardcoded defaults
```

The Langfuse version is **source of truth in production**. If no Langfuse key is set, falls through to DB then hardcoded default automatically.

---

### 10.2 Schema-driven path -- `PromptBuilderService` v2.0

**File**: `apps/extraction_core/services/prompt_builder_service.py`
**Used by**: `ExtractionPipeline._build_prompt()` (step 3), `LLMExtractionAdapter`

Generates fully dynamic prompts from `ExtractionSchemaDefinition` + `TaxJurisdictionProfile`. Zero hardcoded country-specific text -- everything is derived from schema field definitions and jurisdiction config.

**Version constants**: `PROMPT_VERSION = "2.0"`, `PROMPT_CODE = "extraction_core_v2"`

#### Public API

```python
from apps.extraction_core.services.prompt_builder_service import PromptBuilderService

payload = PromptBuilderService.build(
    country_code="IN",
    regime_code="GST",
    document_type="invoice",
    schema=schema_definition,              # ExtractionSchemaDefinition instance
    jurisdiction_profile=jurisdiction,     # TaxJurisdictionProfile -- optional
    field_definitions=field_defs,          # list[ExtractionFieldDefinition] -- optional
    unresolved_field_keys={"invoice_number", "vendor_name"},  # hybrid mode -- optional
)
# Returns dict:
# {
#     "prompt_code": "extraction_core_v2",
#     "prompt_version": "2.0",
#     "system_message": "<7-section assembled prompt>",
#     "user_message_template": "<template with OCR placeholder>",
#     "expected_schema": {...},   # dynamic JSON schema from schema definition
#     "field_count": 14,          # number of fields requested (reduced in hybrid mode)
# }

user_message = PromptBuilderService.build_user_message(ocr_text)
# Wraps ocr_text[:60000] in a standard extraction request envelope
```

#### 7-section prompt assembly

The system message is assembled from these sections in order; empty sections are silently dropped:

| # | Section method | What it contains |
|---|---|---|
| 1 | `_global_instructions()` | Extraction rules: JSON-only output, null policy, value vs confidence vs evidence envelope, monetary/date formatting, no markdown |
| 2 | `_country_regime_instructions()` | Country code, tax regime label, tax ID label, expected currency, date formats, `extraction_notes` from `jurisdiction_profile.config_json` |
| 3 | `_document_type_instructions()` | Document type label and per-document guidance |
| 4 | `_schema_fields_section()` | Header fields, tax fields, line-item fields from `ExtractionSchemaDefinition`; each annotated with display name, data type, and `[REQUIRED]` flag |
| 5 | `_tax_instructions()` | Regime-specific notes, `tax_id_regex` from jurisdiction profile, list of tax-flagged field keys with display names |
| 6 | `_evidence_confidence_rules()` | Confidence band definitions: 1.0 (unambiguous) / 0.7-0.9 (inference) / 0.3-0.6 (ambiguous) / 0.0 + null (not found); verbatim snippet requirement |
| 7 | `_output_format_section()` | Expected JSON schema rendered inline so the LLM knows the exact output shape |

#### Hybrid mode (`unresolved_field_keys`)

When `unresolved_field_keys` is provided, sections 4 and 7 include only those fields. Used when deterministic extraction already resolved some fields -- the LLM call is scoped to the remainder, reducing token usage and improving focus:

```python
payload = PromptBuilderService.build(
    ...,
    unresolved_field_keys={"invoice_number", "po_number"},
)
# payload["field_count"] == 2
# payload["expected_schema"] contains only those 2 fields
```

#### Schema-driven expected output

`build_expected_schema()` reads `ExtractionSchemaDefinition.header_fields_json`, `tax_fields_json`, and `line_item_fields_json` to produce a dynamic JSON schema. This schema is:
- Embedded in section 7 of the system prompt so the LLM sees the exact required shape
- Used downstream by `LLMExtractionAdapter` to validate and parse the LLM response
- Stored on `ExtractionRun` via `prompt_code` / `prompt_version` for audit traceability

Each extracted field uses this per-field envelope (schema-driven path):

```json
{
  "header_fields": {
    "invoice_number": { "value": "INV-2024-001", "confidence": 0.97, "evidence": "Invoice No. INV-2024-001" },
    "vendor_name":    { "value": "Acme Pvt Ltd",  "confidence": 1.0,  "evidence": "ACME PRIVATE LIMITED" }
  },
  "tax_fields": {
    "tax_amount": { "value": "1800.00", "confidence": 0.95, "evidence": "GST 18% = 1800.00" },
    "cgst":       { "value": "900.00",  "confidence": 0.95, "evidence": "CGST @ 9% = 900.00" }
  },
  "line_items": [
    {
      "item_description": { "value": "Cloud Hosting", "confidence": 1.0,  "evidence": "Cloud Hosting Services" },
      "quantity":         { "value": "1",             "confidence": 0.90, "evidence": "Qty: 1" },
      "unit_price":       { "value": "10000.00",      "confidence": 0.90, "evidence": "Rate: 10000" }
    }
  ]
}
```

Compare with the **monolithic path** flat output (used by `InvoiceExtractionAgent`):

```json
{
  "confidence": 0.94,
  "vendor_name": "Acme Pvt Ltd",
  "invoice_number": "INV-2024-001",
  "total_amount": 11800,
  "line_items": [{ "item_description": "Cloud Hosting", "quantity": 1 }]
}
```

---

### 10.3 `LLMExtractionAdapter`

**File**: `apps/extraction_core/services/llm_extraction_adapter.py`

Wraps `LLMClient` to drive a schema-driven extraction call. Uses `prompt_builder.py` (the earlier `PromptBuilderService` variant that works with `ExtractionTemplate` / `FieldSpec` objects) to build messages, then invokes the LLM with retry on JSON parse failures.

```python
adapter = LLMExtractionAdapter()
results, audit = adapter.extract_fields(
    template=template,           # ExtractionTemplate (header/tax/line-item FieldSpec lists)
    ocr_text=ocr_text,
    jurisdiction_profile=jurisdiction,
    unresolved_field_keys=None,  # None = extract all; set[str] = hybrid mode
)
# results : list[FieldResult]       (value, confidence, evidence, method per field)
# audit   : LLMExtractionAudit      (see fields below)
```

**`LLMExtractionAudit` fields:**

| Field | Type | Description |
|---|---|---|
| `model` | `str` | Deployment name used for the call |
| `prompt_tokens` | `int` | Tokens in the request |
| `completion_tokens` | `int` | Tokens in the response |
| `total_tokens` | `int` | Combined token count |
| `duration_ms` | `int` | Wall-clock latency |
| `attempts` | `int` | LLM calls made (1 + retries on parse failure) |
| `success` | `bool` | True if at least one attempt returned parseable JSON |
| `error_message` | `str` | Last error string if all attempts failed |
| `fields_extracted` | `int` | Number of non-null field results returned |

---

### 10.4 `PromptRegistryService` -- `ExtractionPromptTemplate` lifecycle

**File**: `apps/extraction_core/services/prompt_registry_service.py`
**Model**: `ExtractionPromptTemplate` (in `extraction_core` app, separate from `core.PromptTemplate`)

Manages versioned prompt templates scoped to the extraction schema system.

```python
from apps.extraction_core.services.prompt_registry_service import PromptRegistryService

# List with filters
qs = PromptRegistryService.list_prompts({
    "prompt_code": "extraction_core_v2",
    "country_code": "IN",
    "regime_code": "GST",
    "status": "ACTIVE",
})

# Create
prompt = PromptRegistryService.create_prompt({
    "prompt_code": "extraction_core_v2",
    "prompt_category": "extraction",
    "country_code": "IN",
    "regime_code": "GST",
    "document_type": "invoice",
    "schema_code": "invoice_v1",
    "prompt_text": "...",
    "variables_json": [],
    "effective_from": date.today(),
}, user=request.user)

# Update (diff stored for audit)
PromptRegistryService.update_prompt(prompt.pk, {"prompt_text": "..."}, user=request.user)
```

Filterable fields: `prompt_code`, `prompt_category`, `country_code`, `regime_code`, `document_type`, `schema_code`, `status`, `search`. `update_prompt()` computes and stores a diff of `prompt_text` changes for auditability.

---

### 10.5 How `ExtractionPipeline` wires the prompt layer

`ExtractionPipeline` is the new primary entry point for governed extraction. Step 3 of 12 calls `PromptBuilderService.build()` and persists the prompt identity back to `ExtractionRun`:

```python
# ExtractionPipeline._build_prompt()  (step 3)
prompt_payload = PromptBuilderService.build(
    country_code=resolution.country_code,
    regime_code=resolution.regime_code or "",
    document_type=document_type,
    schema=schema,                           # selected by SchemaRegistryService (step 2)
    jurisdiction_profile=resolution.jurisdiction,
)
run.prompt_code = prompt_payload["prompt_code"]       # "extraction_core_v2"
run.prompt_version = prompt_payload["prompt_version"] # "2.0"
run.save(update_fields=["prompt_code", "prompt_version", "updated_at"])
ExtractionAuditService.log_prompt_selected(...)       # audit event emitted
```

The assembled `system_message` is passed to `LLMClient.chat()` as the system turn. `build_user_message(ocr_text)` provides the user turn (OCR text wrapped in a standard envelope, truncated at 60 000 characters).

`InvoiceExtractionAgent` is the outer orchestrator used by all Celery tasks. It uses `InvoicePromptComposer` overlays for its own primary LLM call, then invokes `ExtractionPipeline.run()` at step 1b as a fail-silent enrichment step that produces the governance records described above. The agent is the outer pipeline; `ExtractionPipeline` is called from inside it, not the other way around.

---

### 10.6 Key extraction rules (monolithic path)

Embedded in `extraction.invoice_system` and applied by `InvoiceExtractionAgent`:

| Rule | Description |
|------|-------------|
| **Pre-extraction analysis** | Mandatory step: identify document type, table vs pricing breakdown structure, tax regime, quantity logic |
| **Label binding** | Values bound to nearest explicit label; no identifier guessing by format alone |
| **Header block recovery** | When label and value are on separate OCR lines, search the nearby header section only |
| **invoice_number sources** | Only from: Invoice Number, Invoice No, Tax Invoice No, Bill No |
| **Reference exclusions** | CART Ref. No., Client Code, IRN, Document No., Booking Confirmation No., Hotel Booking ID, Requisition Number, Passenger Name, Employee Code, Cost Center Code -- **never** used as invoice_number |
| **po_number** | Only when explicitly labeled (PO Number / P.O. No / Purchase Order), else `""` |
| **vendor_name** | English characters only; transliterate/translate if OCR contains Arabic/Urdu/non-English |
| **vendor_tax_id** | GSTIN or VAT registration number of the vendor (not buyer) |
| **buyer_name** | Entity under "Bill To" |
| **due_date** | Extract if present, else `""` |
| **tax_breakdown** | Map CGST->cgst, SGST->sgst, IGST->igst, VAT->vat; default 0 if missing |
| **document_type** | Always `"invoice"` |
| **item_category** | One of: Food, Logistics, Packaging, Maintenance, Utilities, Equipment, Services, Materials, Other |
| **subtotal** | Sum of ALL pre-tax components (base fare, service charges, fees); exclude GST/VAT, roundoff, total |
| **tax_percentage** | Computed: `(tax_amount / subtotal) x 100`; NOT copied from component-level rate |
| **Travel invoice** | Convert pricing breakdown (Base Fare, Service Charges) into line items; consolidate Basic Fare + Hotel Taxes -> Total Fare |
| **Consistency** | `subtotal + tax_amount ~= total_amount` (+-2%); `sum(line_amounts) ~= subtotal` (+-5%); prefer computed if mismatch |
| **Defaults** | Missing text -> `""`; missing numbers -> `0` |

---

## 10a. Invoice Category Classifier

**File**: `apps/extraction_core/services/invoice_category_classifier.py`

Classifies invoice OCR text into one of three categories **before** LLM extraction so the prompt can be tailored:

| Category | Key signals |
|---|---|
| `travel` | hotel, itinerary, passenger name, CART Ref, PNR, booking ID, room rate, fare |
| `goods`  | HSN code, qty/pcs/unit, rate per unit, SKU, batch no, e-way bill |
| `service` | professional fees, SAC, consulting, subscription, maintenance contract, management fee |

**Result dataclass**: `InvoiceCategoryResult`

| Field | Type | Description |
|---|---|---|
| `category` | `str` | `goods` / `service` / `travel` |
| `confidence` | `float` | 0.0–1.0 |
| `signals` | `list[str]` | Matched keyword evidence (max 10) |
| `is_ambiguous` | `bool` | True when top-2 score gap < 0.20 |

**Fallback**: Defaults to `service` when input is empty or confidence < 0.20.

---

## 10b. Modular Prompt Composition

**File**: `apps/extraction/services/invoice_prompt_composer.py`
**Registry**: `apps/core/prompt_registry.py`

### Why prompt overlays instead of multiple agents

A single `InvoiceExtractionAgent` is retained because:
- The extraction schema (JSON output shape) is identical for all invoice types
- Category-specific guidance is additive — overlays append targeted rules to the base
- Fewer agents = simpler failure modes, unified tracing, one Langfuse config

### Registry keys

| Key | Purpose |
|---|---|
| `extraction.invoice_base` | Base extraction prompt (versioned independently of monolithic fallback) |
| `extraction.invoice_system` | Monolithic fallback (unchanged — backward compatible) |
| `extraction.invoice_category_goods` | Goods-specific extraction rules overlay |
| `extraction.invoice_category_service` | Service-specific extraction rules overlay |
| `extraction.invoice_category_travel` | Travel-specific rules (invoice# exclusions, subtotal, line structure) |
| `extraction.country_india_gst` | India GST rules (GSTIN, IRN, CGST/SGST/IGST) |
| `extraction.country_generic_vat` | Generic VAT rules |

All keys are Langfuse-overridable via the normal PromptRegistry resolution chain (Langfuse → DB → hardcoded default).

### Composition result: `PromptComposition`

| Field | Type | Description |
|---|---|---|
| `final_prompt` | `str` | Assembled system prompt sent to the LLM |
| `components` | `dict[str, str]` | `{slug: version}` for each part used |
| `prompt_hash` | `str` | sha256 hex (16 chars) of `final_prompt` — deterministic across runs |

### Backward compatibility / fallback

1. If `extraction.invoice_base` is absent → uses `extraction.invoice_system`
2. If category overlay is absent or empty → skipped (base prompt only)
3. If country overlay is absent → skipped
4. If `InvoicePromptComposer` raises → `InvoiceExtractionAgent` uses its own `system_prompt` property (existing behaviour)

### Langfuse metadata logged per extraction

```
invoice_category, invoice_category_confidence,
base_prompt_key, base_prompt_version,
category_prompt_key, category_prompt_version,
country_prompt_key, country_prompt_version,
prompt_hash, schema_code
```

---

## 10c. Response Repair / Validator

**File**: `apps/extraction/services/response_repair_service.py`

### Why deterministic repair before parsing

The parser (`ExtractionParserService`) is a pure JSON→dataclass mapper. Placing repair upstream means:
- The parser, normalizer, validator, and confidence scorer all receive cleaner data
- Every repair is explicitly recorded in `repair_actions` — auditable
- No silent value invention — repairs only fire when OCR evidence exists

### Phase 1 rules

| Rule | Trigger | Action |
|---|---|---|
| **a. Invoice number exclusion** | `invoice_number` matches CART Ref, Client Code, IRN, Booking ID, Document No., etc. | Attempt OCR recovery for a real invoice-labelled number; clear to `""` if not found |
| **b. Tax % recomputation** | LLM tax_percentage differs >0.5pp from `tax_amount/subtotal×100` | Recompute from amounts |
| **c. Subtotal alignment** | `subtotal` differs >1 unit from sum of pre-tax line amounts (GST/VAT lines excluded) | Align subtotal to line sum |
| **d. Line tax allocation** | Travel/service invoice; single service-charge line; all tax on base/hotel line | Move tax to service-charge line, zero base line tax |
| **e. Travel consolidation** | Basic Fare + Hotel Taxes + Total Fare lines exist; Total Fare ≈ Basic + Taxes | Remove sub-lines, keep Total Fare line |

### Result dataclass: `RepairResult`

| Field | Description |
|---|---|
| `repaired_json` | Modified (or original) JSON dict |
| `repair_actions` | List of human-readable action strings |
| `warnings` | Non-fatal issues (e.g., could not recover invoice number) |
| `was_repaired` | `True` if any action was applied |

### Persistence

Repair metadata is embedded in `ExtractionResult.raw_response` under the `_repair` key (no migration needed):

```json
{
  "vendor_name": "...",
  "invoice_number": "...",
  "_repair": {
    "was_repaired": true,
    "repair_actions": ["invoice_number: replaced CART-9876 with INV-001"],
    "warnings": []
  }
}
```

The parser ignores `_repair` naturally (it only reads known field names).

---

## 11. Template Views & URLs

### URL Routing

**File**: `apps/extraction/urls.py` — all routes are under `/extraction/`

| URL Pattern | View | Method | Permission | Description |
|-------------|------|--------|------------|-------------|
| `/extraction/` | `extraction_workbench` | GET | `invoices.view` | Main workbench with KPIs + approval tab |
| `/extraction/upload/` | `extraction_upload` | POST | `invoices.create` | Upload + extract |
| `/extraction/filter/` | `extraction_ajax_filter` | GET | `invoices.view` | AJAX filter results |
| `/extraction/export/` | `extraction_export_csv` | GET | `invoices.view` | CSV export |
| `/extraction/result/<id>/` | `extraction_result_detail` | GET | `invoices.view` | Result detail view |
| `/extraction/result/<id>/json/` | `extraction_result_json` | GET | `invoices.view` | Download raw JSON |
| `/extraction/result/<id>/rerun/` | `extraction_rerun` | POST | `extraction.reprocess` | Re-run extraction |
| `/extraction/result/<id>/edit/` | `extraction_edit_values` | POST | `extraction.correct` | Edit extracted values |
| `/extraction/approvals/` | `extraction_approval_queue` | GET | `invoices.view` | Redirects to workbench?tab=approvals |
| `/extraction/approvals/<id>/` | `extraction_approval_detail` | GET | `invoices.view` | Approval detail/review |
| `/extraction/approvals/<id>/approve/` | `extraction_approve` | POST | `extraction.approve` | Approve extraction |
| `/extraction/approvals/<id>/reject/` | `extraction_reject` | POST | `extraction.reject` | Reject extraction |
| `/extraction/console/<id>/` | `extraction_console` | GET | `invoices.view` | Agentic review console |
| `/extraction/approvals/analytics/` | `extraction_approval_analytics` | GET | `invoices.view` | Analytics JSON endpoint |
| `/extraction/country-packs/` | `country_pack_list` | GET | `extraction.view` | Country pack governance |

**API URLs**: `apps/extraction/api_urls.py` — empty (no REST API endpoints; all APIs live in `extraction_core`).

### Observability

All 15 template views are decorated with:
- `@login_required` — enforced by `LoginRequiredMiddleware`
- `@permission_required_code("<permission>")` — RBAC permission check
- `@observed_action("<action_name>")` — creates trace span, captures actor identity, role snapshot, permission checked; writes `AuditEvent`

### Data Scoping (AP_PROCESSOR)

AP_PROCESSOR users see only extractions linked to their own uploaded invoices. The `_scope_extractions_for_user(queryset, user)` helper filters by `document_upload__uploaded_by=user` when the user's primary role is `AP_PROCESSOR`. This scoping is applied to:
- Workbench queryset (extraction results list)
- KPI statistics (counts and averages)
- AJAX filter endpoint (filtered results)

### Cross-Module Enrichment (extraction_core integration)

Several template views enrich their context with data from `extraction_core` models:

- **`extraction_workbench`**: Pre-loads `ExtractionRun.review_queue` for each result (bulk query via `document__document_upload_id` mapping). Displays review queue as badge in results table.
- **`extraction_console`**: Loads `ExtractionRun` by `document_upload_id` to enrich context with `review_queue`, `schema_code`, `schema_version`, `extraction_method`, `requires_review`. Loads `ExtractionCorrection` records for corrections tab.
- **`country_pack_list`**: Queries `CountryPack.objects.select_related("jurisdiction")` to display governance table.

All cross-module lookups are wrapped in `try/except` for graceful degradation if extraction_core data isn't populated.

### View Details

**`extraction_workbench`** — Main extraction agent page with three tabs:
- **Agent Runs tab**: KPI stats (total, success, failed, avg confidence, avg duration); advanced filters (search, status, confidence range, date range, review queue); paginated results table (20 per page) with review queue column; "Run Agent" file upload modal (PDF, PNG, JPG, TIFF — max 20 MB)
- **Approvals tab**: Approval queue with filter/search + analytics strip
- **Rejected tab**: Failed/rejected uploads (`DocumentUpload.processing_state=FAILED`). Table with columns: ID, Filename, Rejection Reason, Detected Doc Type, Uploaded timestamp, Uploaded By. Paginated with count badge. Visible when document type classification rejects non-invoice uploads (GRN, PO, etc.).

**`extraction_upload`** — File upload handler:
- Validates file type and size (20 MB max)
- Computes SHA-256 hash
- Creates `DocumentUpload` record
- Runs extraction pipeline (standalone mode — no case creation)
- Optional Azure Blob Storage upload

**`extraction_result_detail`** — Detailed extraction result:
- Engine metadata (name, version, duration, confidence)
- Raw vs normalized invoice data side-by-side
- Validation issues (errors + warnings)
- Line items table with service/stock item badges
- Action buttons: Edit Values, Download JSON, Re-extract, View Full Invoice

**`extraction_edit_values`** — Inline value editing:
- Accepts JSON payload with `header` and `lines` corrections
- Header fields: `invoice_number`, `po_number`, `invoice_date`, `due_date`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `raw_vendor_name`, `vendor_tax_id`, `buyer_name`, `tax_percentage`
- Line fields: `description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`, `tax_percentage`
- Returns changed fields list and count
- Audits changes as `EXTRACTION_COMPLETED` event

**`extraction_approval_queue`** — Backward-compatible redirect to `workbench?tab=approvals`. Forwards query params.

**`extraction_approval_detail`** — Review and approve/reject:
- Confidence and metadata cards
- Validation issues alert
- Editable header fields and line items (read-only if already reviewed)
- Previous corrections history table
- Approve/Reject buttons with AJAX handlers

**`extraction_export_csv`** — CSV export with columns: ID, Filename, Invoice #, Vendor, Currency, Subtotal, Tax, Total, PO, Confidence %, Status, Duration, Engine, Extracted At.

**`extraction_console`** — Agentic deep-dive review console:
- Full context build: header fields, tax fields, parties, enrichment, line items, validation re-run
- Pipeline stages with state tracking (10 stages)
- Approval record lookup
- ExtractionRun enrichment (review queue, schema, method badges in header bar)
- Corrections tab with ExtractionCorrection audit trail
- Permission context (can_approve, can_reprocess, can_escalate)
- Assignable users for escalation
- See [Section 13: Extraction Review Console](#13-extraction-review-console) for full template/layout details.

**`country_pack_list`** — Country pack governance page:
- KPI strip: total, active, draft, deprecated counts
- Governance table: country, regime, status (color-coded badges), schema/validation/normalization versions, activated date, notes
- Gated by `extraction.view` permission

---

## 12. Templates (UI)

All templates are in `templates/extraction/` and extend `base.html` (Bootstrap 5). Total: 19 template files.

### Top-Level Templates

| File | Purpose |
|------|---------|
| `workbench.html` | Main workbench with **3 tabs**: Agent Runs (KPIs, filters, results with review queue column) + Approvals + **Rejected** (failed uploads with rejection reason, doc type, timestamp) |
| `result_detail.html` | Single extraction result detail |
| `approval_detail.html` | Approval review page (approve/reject modals) |
| `approval_queue.html` | Deprecated — redirects to workbench |
| `country_packs.html` | Country pack governance (KPI strip + governance table with status badges) |

### workbench.html
- Three-tab layout: **Agent Runs**, **Approvals**, and **Rejected**
- Agent Runs: KPI stat cards (total, success, failed, avg confidence, avg duration); advanced filter panel (search, status, confidence presets/slider, date range, review queue dropdown); results table with review queue column; "Run Agent" modal for file upload (drag-and-drop, file validation)
- Approvals: Approval queue with filter/search + analytics strip
- Rejected: Failed uploads table (ID, Filename, Reason, Doc Type, Uploaded, By) with pagination + count badge. Shows uploads rejected by document type classification gate.

### result_detail.html
- Engine metadata panel (name, version, duration, file info)
- Error message display (if extraction failed)
- Raw vs Normalized comparison table
- Invoice header + line items display
- Validation issues list
- Action buttons: Edit Values, Download JSON, Re-extract, View Full Invoice

### approval_detail.html
- Confidence card with percentage + status badge
- Invoice metadata card (vendor, amount, PO, date)
- Validation issues alert banner
- Editable header fields form (text inputs for each correctable field)
- Editable line items table (inline editing)
- Previous corrections table (showing original → corrected values)
- Reject modal with reason textarea
- JavaScript handlers for Approve (AJAX POST) and Reject (modal + AJAX POST)

### country_packs.html
- Breadcrumb navigation
- KPI strip: Total Packs, Active, Draft, Deprecated (with color-coded badges)
- Governance table: Country, Regime, Status (ACTIVE=green, DRAFT=amber, DEPRECATED=red), Schema Version, Validation Version, Normalization Version, Activated date, Notes
- Empty state message when no packs exist

---

## 13. Extraction Review Console

### Overview

The Extraction Review Console is an enterprise-grade, agentic deep-dive UI for reviewing individual extraction results. It provides document viewing, 5-tab intelligence panels, approval workflow modals, and a pipeline timeline — all in a single-page Bootstrap 5 layout.

**Route**: `/extraction/console/<id>/` → `extraction_console` view  
**Template**: `templates/extraction/console/console.html`  
**Static**: `static/css/extraction_console.css`, `static/js/extraction_console.js`

### Layout Structure

```
┌──────────────────────────────────────────────────────────────┐
│  HEADER BAR — ID, file, status, confidence, jurisdiction,    │
│               review queue badge, schema badge,              │
│               extraction method badge,                       │
│               action buttons (Approve, Edit, Reprocess,      │
│               Escalate, Comment)                             │
├──────────────────────────────────────────────────────────────┤
│  INTELLIGENCE PANEL (6 tabs, full-width col-12)              │
│                                                              │
│  Tab 1: Extracted Data                                       │
│    - Header Fields table (vendor_name, invoice_number,       │
│      invoice_date, due_date, po_number, vendor_tax_id,       │
│      buyer_name, currency, subtotal, tax_amount,             │
│      tax_percentage, total_amount)                           │
│    - Parties card (exc-supplementary-card)                   │
│    - Tax & Jurisdiction card                                 │
│    - Tax Breakdown card (CGST/SGST/IGST/VAT components;      │
│      only rendered when invoice_tax_breakdown is non-empty)  │
│    - Master Data Matches card (exc-supplementary-card)       │
│    - Line Items table (expandable; Tax % column shown when   │
│      has_line_tax_pct is True)                               │
│                                                              │
│  Tab 2: Validation                                           │
│    - Errors / Warnings / Passed                              │
│    - Go-to-field navigation                                  │
│                                                              │
│  Tab 3: Evidence                                             │
│    - Field evidence cards                                    │
│    - Source snippets, page refs                              │
│                                                              │
│  Tab 4: Agent Reasoning                                      │
│    - Step-by-step reasoning timeline                         │
│    - Decisions, collapsible details                          │
│                                                              │
│  Tab 5: Audit Trail                                          │
│    - Chronological event timeline                            │
│    - Actor/role badges                                       │
│    - Before/after change tracking                            │
│                                                              │
│  Tab 6: Corrections                                          │
│    - Field correction audit trail table                      │
│    - Original → Corrected values with reasons                │
│    - Corrected-by user + timestamp                           │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  PIPELINE TIMELINE — Upload → OCR → Jurisdiction → Schema →  │
│  Extraction → Normalize → Validate → Enrich → Confidence →   │
│  Review (state-aware pills)                                  │
└──────────────────────────────────────────────────────────────┘
```

> **Note**: The document viewer column was removed. The console uses a single-column, full-width layout for the intelligence panel. The `_document_viewer.html` template is no longer included.

### Template Files (16 files in `templates/extraction/console/`)

| File | Purpose |
|------|---------|
| `console.html` | Main layout — extends `base.html`, includes all partials/modals, loads CSS/JS. 6 tab pills. |
| `_header_bar.html` | Command bar — extraction ID, status/confidence badges (uses `{% widthratio %}` for 0–1 → percentage conversion), jurisdiction badges, review queue badge (bg-info-subtle), schema badge (bg-dark-subtle), extraction method badge (conditional: HYBRID=purple, LLM=primary, else=secondary), action buttons |
| `_document_viewer.html` | **Deprecated** — no longer included in layout. File exists but is unused. |
| `_extracted_data.html` | Tab 1 — Header Fields, Parties, Tax/Jurisdiction, Tax Breakdown (CGST/SGST/IGST/VAT; shown only when non-zero), Master Data Matches, Line Items with **summary footer** (summed Qty, Tax Amount, Total across all line items) and optional Tax % column |
| `_confidence_badge.html` | Reusable confidence % indicator (green ≥85%, amber ≥50%, red <50%). Uses `{% widthratio confidence 1 100 %}` to convert 0–1 float to percentage. |
| `_validation_panel.html` | Tab 2 — Errors/Warnings/Passed grouped by severity, "Go to field" navigation |
| `_evidence_panel.html` | Tab 3 — Evidence cards with source snippets and page references |
| `_reasoning_panel.html` | Tab 4 — Agent reasoning timeline with step indicators, decisions, collapsible details |
| `_audit_trail.html` | Tab 5 — Chronological event timeline with actor/role badges, before/after tracking |
| `_corrections_panel.html` | Tab 6 — Field correction audit trail table (columns: Field Code, Original Value (strikethrough), Corrected Value (green), Reason, Corrected By, Date). Empty state with guidance text. |
| `_cost_tokens_panel.html` | Cost & Tokens — 5 KPI cards (Total/LLM/OCR cost, tokens, OCR pages), cost breakdown (LLM vs OCR), token breakdown bar, execution details table |
| `_bottom_timeline.html` | Pipeline stage progress bar with state indicators (completed/active/error/skipped/pending) |
| `_approve_modal.html` | Approval modal — warnings summary, notes, review confirmation checkbox |
| `_reprocess_modal.html` | Reprocess modal — reason select, override options (force LLM, override jurisdiction) |
| `_escalate_modal.html` | Escalation modal — severity, assignee select, flagged fields list |
| `_comment_modal.html` | Comment modal — text, related fields, internal toggle |

### Key Features

**Field Filtering**: Toggle buttons for All Fields / Flagged Only / Low Confidence to focus review on problem areas. "Flagged Only" shows rows with the `exc-flagged` class (fields with validation issues). "Low Confidence" shows rows with `exc-low-confidence` or `exc-med-confidence` classes. Supplementary cards (Parties, Master Data Matches) are hidden when a filter other than "All" is active. An empty state message is displayed when no rows match the selected filter.

**Edit Mode**: Toggle switch enables inline editing on all header and tax fields. Modified fields get visual highlighting (`exc-modified` class). Original values preserved in `data-original` for comparison.

**Go-to-Field Navigation**: Validation issues and evidence cards have clickable field links that switch to the Extracted Data tab and scroll/highlight the target field row.

**Line Item Expand/Flag**: Each line item row has expand (shows all field details) and flag (marks for review) actions.

**Modal Workflows**: All approval actions go through Bootstrap modals with CSRF-protected AJAX POST requests. Toast notifications for success/error feedback.

**Permission-Aware Actions**: Action buttons (Approve, Reprocess, Escalate) are conditionally rendered based on the user's RBAC permissions: `extraction.approve` for approval, `extraction.reprocess` for re-extraction, `cases.escalate` for escalation. Checked via `user.has_permission()` (custom RBAC, not Django's `has_perm()`).

### Static Assets

**`static/css/extraction_console.css`** (~200 lines):
- `.exc-conf-high/med/low` confidence badge colors
- `.exc-field-table` compact field table styling
- `.exc-field-row.exc-low-confidence` / `.exc-med-confidence` left-border indicators
- `.exc-field-row.exc-flagged` left-border indicator for validation-issue fields
- `.exc-field-row.exc-editing` edit mode show/hide
- `.exc-source-snippet` evidence source styling
- `.exc-reasoning-step-number` numbered step circles with connectors
- `.exc-audit-dot-*` timeline dot colors per event type
- `.exc-stage-*` pipeline pill state colors
- `.exc-pipeline-timeline` horizontal scrollable timeline
- `.exc-filter-empty` empty state styling for filter results
- `.exc-supplementary-card` styling for Parties / Enrichment cards
- Responsive breakpoints (≤991px: reduced heights)

**`static/js/extraction_console.js`** (~200 lines):
- Tab persistence (sessionStorage)
- Field filter toggles (all/flagged/low-confidence) with supplementary card visibility
- Filter empty state toggle
- Edit mode toggle with modification tracking
- Go-to-field navigation (cross-tab + scroll + highlight animation)
- Evidence field filter dropdown
- Line item expand/collapse and flag toggle
- AJAX modal submission (approve/reprocess/escalate/comment) with CSRF
- Toast notification system

### View Context

The `extraction_console` view builds the following context for the template:

| Context Variable | Source | Description |
|-----------------|--------|-------------|
| `extraction` | Computed dict | ID, file_name, status, confidence, created_at, resolved_jurisdiction, jurisdiction_source, jurisdiction_confidence, jurisdiction_warning, review_queue, schema_code, schema_version, extraction_method, requires_review |
| `ext` | `ExtractionResult` | Original extraction result record |
| `header_fields` | Invoice model | Dict of field dicts (display_name, value, raw_value, confidence, method, is_mandatory, evidence). Includes: `vendor_name`, `invoice_number`, `invoice_date`, `due_date`, `po_number`, `vendor_tax_id`, `buyer_name`, `currency`, `subtotal`, `tax_amount`, `tax_percentage`, `total_amount` |
| `tax_fields` | Invoice model | Tax-specific field dicts: `tax_amount`, `tax_percentage`, and individual tax breakdown rows (`cgst`, `sgst`, `igst`, `vat` — only non-zero components added) |
| `invoice_tax_breakdown` | `invoice.tax_breakdown` | Raw breakdown dict `{cgst, sgst, igst, vat}` used by the Tax Breakdown card |
| `has_line_tax_pct` | Computed bool | `True` when at least one line item has a non-null `tax_percentage` — controls Tax % column visibility in line items table |
| `parties` | `raw_response.document_intelligence.parties` | Supplier/buyer/ship-to/bill-to from document intelligence; falls back to `invoice.vendor_name` + `invoice.vendor_tax_id` for supplier, and `invoice.buyer_name` for buyer |
| `enrichment` | `raw_response.enrichment` | Vendor/customer/PO matches from master data enrichment |
| `line_items` | `InvoiceLineItem` queryset | List of dicts with description, qty, price, `tax_percentage`, tax, total, confidence, fields |
| `line_items_totals` | Computed | Dict with summed `quantity`, `tax_amount`, `total` across all line items — displayed in table footer |
| `errors` / `warnings` | Re-run `ValidationService` | Grouped validation issues |
| `validation_field_issues` | Computed | Map of field names with validation issues |
| `pipeline_stages` | Computed | 10-stage pipeline with state indicators |
| `approval` | `ExtractionApproval` | Current approval record (if exists) |
| `corrections` | `ExtractionCorrection` queryset | Field correction audit trail from `ExtractionRun` (select_related corrected_by) |
| `correction_count` | int | Count of corrections for badge display |
| `permissions` | Request user | `can_approve` (`extraction.approve`), `can_reprocess` (`extraction.reprocess`), `can_escalate` (`cases.escalate`) — checked via `user.has_permission()` |
| `assignable_users` | `User.objects` | Top 50 active users for escalation |

**ExtractionRun enrichment**: The view calls `get_execution_context(ext)` to populate governed execution metadata. The enriched `ExecutionContext` provides `review_queue`, `schema_code`, `schema_version`, `extraction_method`, `requires_review`, `extraction_run_id`, `country_code`, `regime_code`, `jurisdiction_source`, `overall_confidence`, `review_reasons`, `approval_action`, `approval_decided_at`, and `duration_ms`. These appear as badges and metadata in the header bar and pipeline timeline.

**Query optimization**: The workbench, AJAX filter, and CSV export querysets include `select_related("extraction_run")` to avoid N+1 queries when `get_execution_context()` accesses the FK.

---

## 14. Enums & Status Flows

### InvoiceStatus

```
UPLOADED → EXTRACTION_IN_PROGRESS → EXTRACTED → VALIDATED → PENDING_APPROVAL → READY_FOR_RECON → RECONCILED
                                  ↘ INVALID                ↗ (auto-approve)                    ↘ FAILED
                                                           ↘ INVALID (rejected)
```

| Value | Description |
|-------|-------------|
| `UPLOADED` | File uploaded, awaiting extraction |
| `EXTRACTION_IN_PROGRESS` | Extraction pipeline running |
| `EXTRACTED` | Raw extraction complete (no validation) |
| `VALIDATED` | Extraction passed validation |
| `INVALID` | Validation failed or extraction rejected |
| `PENDING_APPROVAL` | Awaiting human review in approval queue |
| `READY_FOR_RECON` | Approved — ready for reconciliation |
| `RECONCILED` | Reconciliation complete |
| `FAILED` | Pipeline failure |

### ExtractionApprovalStatus

| Value | Description |
|-------|-------------|
| `PENDING` | Awaiting human review |
| `APPROVED` | Human approved (with or without corrections) |
| `REJECTED` | Human rejected (invoice → INVALID) |
| `AUTO_APPROVED` | System auto-approved (high confidence, touchless) |

### FileProcessingState

| Value | Description |
|-------|-------------|
| `QUEUED` | Upload queued for processing |
| `PROCESSING` | Extraction in progress |
| `COMPLETED` | Extraction finished successfully |
| `FAILED` | Extraction failed |

### Extraction Audit Event Types

| Event Type | When Logged |
|------------|-------------|
| `EXTRACTION_STARTED` | Extraction adapter begins OCR + LLM pipeline |
| `EXTRACTION_COMPLETED` | Pipeline completes successfully |
| `EXTRACTION_FAILED` | Pipeline fails |
| `CREDIT_CHECKED` | Pre-flight credit balance/limit check |
| `CREDIT_RESERVED` | Credits reserved for in-progress extraction |
| `CREDIT_CONSUMED` | Credits consumed after successful extraction |
| `CREDIT_REFUNDED` | Credits refunded after extraction failure |
| `CREDIT_ALLOCATION_UPDATED` | Admin allocates or adjusts credits |
| `CREDIT_LIMIT_EXCEEDED` | Credit reservation rejected (insufficient balance or monthly limit) |
| `CREDIT_MONTHLY_RESET` | Monthly usage counter reset |
| `INVOICE_PERSISTED` | Invoice + line items saved to database |
| `EXTRACTION_RESULT_PERSISTED` | ExtractionResult record saved |
| `DUPLICATE_DETECTED` | Duplicate invoice detected during persistence |
| `VENDOR_RESOLVED` | Vendor matched via normalized name or alias during persistence |
| `EXTRACTION_APPROVAL_PENDING` | Approval record created (PENDING) |
| `EXTRACTION_APPROVED` | Human approves extraction |
| `EXTRACTION_AUTO_APPROVED` | System auto-approves extraction |
| `EXTRACTION_REJECTED` | Human rejects extraction |
| `EXTRACTION_FIELD_CORRECTED` | Field correction applied during approval |

### Extraction Platform Governance Event Types

| Event Type | When Logged | Category |
|------------|-------------|----------|
| `JURISDICTION_RESOLVED` | Jurisdiction resolved (tier + country + regime) | governance |
| `SCHEMA_SELECTED` | Schema selected for extraction | governance |
| `PROMPT_SELECTED` | Prompt template selected | governance |
| `NORMALIZATION_COMPLETED` | Country-specific normalization complete | telemetry |
| `VALIDATION_COMPLETED` | Country-specific validation complete | telemetry |
| `EVIDENCE_CAPTURED` | Field evidence captured | telemetry |
| `REVIEW_ROUTE_ASSIGNED` | Review queue assigned | governance |
| `EXTRACTION_REPROCESSED` | Extraction re-run triggered | business |
| `EXTRACTION_ESCALATED` | Extraction escalated to review queue | business |
| `EXTRACTION_COMMENT_ADDED` | Comment added to extraction | business |
| `SETTINGS_UPDATED` | Runtime settings or schema updated | governance |
| `SCHEMA_UPDATED` | Schema definition modified | governance |
| `PROMPT_UPDATED` | Prompt template modified | governance |
| `ROUTING_RULE_UPDATED` | Routing rule modified | governance |
| `ANALYTICS_SNAPSHOT_CREATED` | Analytics snapshot generated | telemetry |

### Event Category Taxonomy

All extraction audit events carry an `event_category` field in metadata (added by `ExtractionAuditService._base_metadata()`):

| Category | Purpose | UI Behavior |
|----------|---------|-------------|
| `business` | User-visible state changes (approve, reject, correct, reprocess, escalate, comment) | Always show in timelines |
| `governance` | Governed pipeline decisions (jurisdiction, schema, review routing, started/completed/failed) | Show in timelines |
| `telemetry` | Low-level pipeline steps (normalization, validation, evidence capture) | Collapse/filter in UI |

---

## 15. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_API_KEY` | `""` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | `""` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_VERSION` | `"2024-02-01"` | OpenAI API version |
| `AZURE_OPENAI_DEPLOYMENT` | `""` | Deployment name |
| `LLM_MODEL_NAME` | `"gpt-4o"` | Model name |
| `AZURE_DI_ENDPOINT` | `""` | Azure Document Intelligence endpoint |
| `AZURE_DI_KEY` | `""` | Azure Document Intelligence key |
| `AZURE_BLOB_CONNECTION_STRING` | `""` | Blob storage connection string |
| `AZURE_BLOB_CONTAINER_NAME` | `"finance-agents"` | Blob container name |
| `EXTRACTION_CONFIDENCE_THRESHOLD` | `0.75` | Confidence below this triggers validation warning |
| `EXTRACTION_AUTO_APPROVE_THRESHOLD` | `1.1` | Confidence threshold for auto-approval (1.1 = disabled) |
| `EXTRACTION_AUTO_APPROVE_ENABLED` | `"false"` | Master toggle for auto-approval |
| `EXTRACTION_OCR_ENABLED` | `"true"` | OCR toggle — `true` uses Azure DI, `false` uses native PDF text extraction (PyPDF2). Runtime override via `ExtractionRuntimeSettings.ocr_enabled`. |

### Settings File

All settings are in `config/settings.py`. Values are loaded from environment variables or `.env` file.

### OCR Mode Configuration

The OCR mode can be controlled at two levels:

1. **Runtime setting** (takes precedence): `ExtractionRuntimeSettings.ocr_enabled` — toggleable from the Extraction Control Center UI without app restart.
2. **Environment variable** (fallback): `EXTRACTION_OCR_ENABLED` — default `true`.

When OCR is disabled, the system uses PyPDF2 to extract the native text layer from PDFs. This is useful for:
- **Accuracy comparison**: Run the same invoice with OCR on vs off to measure LLM extraction quality difference.
- **Cost reduction testing**: Native extraction has zero Azure DI cost ($1.50/1,000 pages saved).
- **Speed testing**: Native extraction is near-instant vs Azure DI latency.

---

## 16. Permissions & RBAC

### Permission Codes

| Permission | Description |
|------------|-------------|
| `invoices.view` | View extraction results, approval queue, analytics |
| `invoices.create` | Upload files (upload only — edit uses `extraction.correct`) |
| `extraction.view` | View extraction platform data (country packs, schemas, settings) |
| `extraction.correct` | Correct/edit extracted field values (workbench + console UI + API) |
| `extraction.approve` | Approve extracted invoice data before reconciliation |
| `extraction.reject` | Reject extracted data and request re-extraction |
| `extraction.reprocess` | Re-run extraction on existing uploads |
| `extraction.escalate` | Escalate extraction to review queue (API) |
| `cases.escalate` | Escalate extraction for case-level review (console UI) |
| `credits.view` | View credit accounts and balances |
| `credits.manage` | Allocate, adjust, and manage user credit accounts |

### Role Access

| Role | Permissions |
|------|-------------|
| ADMIN | All extraction + credit permissions |
| AP_PROCESSOR | `invoices.view`, `invoices.create`, `extraction.correct`, `extraction.approve`, `extraction.reject`, `extraction.reprocess` (scoped to own uploads) |
| REVIEWER | `invoices.view` |
| FINANCE_MANAGER | `invoices.view`, `invoices.create`, `extraction.correct`, `extraction.approve`, `extraction.reject`, `extraction.reprocess`, `credits.view`, `credits.manage` |
| AUDITOR | `invoices.view` |
| SYSTEM_AGENT | `extraction.approve`, `extraction.reject` |

### Data Scoping

AP_PROCESSOR users are scoped to see only extractions linked to their own uploaded invoices. The `_scope_extractions_for_user()` helper in `template_views.py` filters by `document_upload__uploaded_by=user` when the user's primary role (via `UserRole` enum) is `AP_PROCESSOR`. This is applied to:
- Workbench queryset (paginated extraction results)
- KPI statistics (total, success, failed counts and averages)
- AJAX filter endpoint (filtered extraction results)

All other roles see all extractions.

### Permission Enforcement

- **View decorators**: `@permission_required_code("<permission>")` — checks against RBAC Permission model
- **Template checks**: `{% has_permission "extraction.approve" as can_approve %}` — uses RBAC template tags
- **Console permissions**: Checked via `user.has_permission("<code>")` (custom RBAC engine, **not** Django's `has_perm()`)
- **Separation of duties**: Approve and reject use dedicated `extraction.approve` / `extraction.reject` permissions, separate from `invoices.create` (upload/edit)

### Sidebar Navigation

The extraction section in the sidebar (`templates/partials/sidebar.html`) includes:
- **Invoice Extraction Agent** — links to the workbench (`/extraction/`), gated by `{% has_permission "invoices.view" %}`
- **Extraction Control Center** — links to the extraction core overview (`/extraction-control-center/`), gated by `{% has_permission "extraction.view" %}`
- **Credits** — links to credit account management (`/extraction/credits/`), gated by `{% has_permission "credits.manage" %}`, uses `bi-coin` icon. Located in the Admin Console sidebar section. Visible to ADMIN and FINANCE_MANAGER roles.

---

## 17. Credit System

### Overview

A per-user credit-based usage control system for invoice extraction. Every extraction consumes 1 credit. Credits are managed by ADMIN and FINANCE_MANAGER roles.

### Data Models

**UserCreditAccount** (`extraction_usercreditaccount`) — OneToOne per User:

| Field | Type | Description |
|-------|------|-------------|
| `user` | OneToOneField → User | Account owner |
| `balance_credits` | PositiveIntegerField | Available credit balance |
| `reserved_credits` | PositiveIntegerField | Credits reserved for in-progress extractions |
| `monthly_limit` | PositiveIntegerField | Monthly usage cap (0 = unlimited) |
| `monthly_used` | PositiveIntegerField | Credits used this month |
| `is_active` | BooleanField | Whether the account is active |
| `last_reset_at` | DateTimeField | Last monthly reset timestamp |

**Properties**: `available_credits` (balance − reserved), `has_available_credits()`, `can_consume_monthly()`

**CreditTransaction** (`extraction_credittransaction`) — Immutable ledger:

| Field | Type | Description |
|-------|------|-------------|
| `account` | FK → UserCreditAccount | Parent account |
| `transaction_type` | CharField | RESERVE, CONSUME, REFUND, ALLOCATE, ADJUST, MONTHLY_RESET |
| `credits` | IntegerField | Signed credit amount |
| `balance_after` | IntegerField | Snapshot of balance after transaction |
| `reserved_after` | IntegerField | Snapshot of reserved after transaction |
| `monthly_used_after` | IntegerField | Snapshot of monthly_used after transaction |
| `reference_type` | CharField | document_upload, admin, system |
| `reference_id` | CharField | Optional external reference |
| `remarks` | TextField | Mandatory for admin adjustments |
| `created_by` | FK → User | Who performed the action |

### Service: CreditService

**File**: `apps/extraction/services/credit_service.py`

| Method | Purpose | Creates Transaction | Audit Event |
|--------|---------|-------------------|-------------|
| `check_can_reserve(user)` | Pre-flight balance/limit check | No | `CREDIT_CHECKED` |
| `reserve(user, amount)` | Lock credits for upload | RESERVE | `CREDIT_RESERVED` |
| `consume(user, amount)` | Deduct after successful extraction | CONSUME | `CREDIT_CONSUMED` |
| `refund(user, amount)` | Return credits on failure | REFUND | `CREDIT_REFUNDED` |
| `allocate(user, amount)` | Admin add credits (amount > 0) | ALLOCATE | `CREDIT_ALLOCATION_UPDATED` |
| `adjust(user, amount)` | Admin correct (±amount) | ADJUST | `CREDIT_ALLOCATION_UPDATED` |
| `reset_monthly_if_due(account)` | Monthly usage reset | MONTHLY_RESET | `CREDIT_MONTHLY_RESET` |

### Upload Integration

The upload flow checks credits before allowing extraction:
```
User clicks Upload → check_can_reserve() → reserve(1, ref_type="document_upload", ref_id=upload.pk) → run extraction
  → Success: consume(1) — charged for successful extraction
  → OCR Failure: refund(1) — no charge for failed extraction
```

**Reprocess flow**: `extraction_rerun` also reserves 1 credit before re-extraction (`ref_type="reprocess"`, `ref_id=f"reprocess-{upload.pk}-{timestamp}"`). A unique timestamp-based `reference_id` is generated on every reprocess attempt so that the idempotency guard does not block subsequent reprocesses of the same upload. Blocked if the current approval is already finalized (`APPROVED`/`AUTO_APPROVED`).

The task receives `credit_ref_type` and `credit_ref_id` as explicit kwargs and threads them through all four consume/refund call sites (OCR failure refund, pipeline failure consume, success consume, persist failure consume). This ensures the correct unique reference is used regardless of which pipeline branch completes.

### Credit Decision Table — ChargePolicy

All charge/refund decisions are centralized in `ChargePolicy` (`apps/extraction/services/credit_service.py`). Each scenario maps to exactly one of **CONSUME**, **REFUND**, or **NOOP**.

| Scenario | ChargePolicy method | Outcome | reference_type | reference_id |
|----------|-------------------|---------|---------------|-------------|
| Successful extraction (invoice) | `for_extraction_success()` | CONSUME | `document_upload` | `upload.pk` |
| Non-invoice document (classified away) | `for_non_invoice_document()` | REFUND | `document_upload` | `upload.pk` |
| OCR failure (adapter returned error) | `for_ocr_failure()` | REFUND | `document_upload` | `upload.pk` |
| Parse / normalize / validate failure | `for_pipeline_failure()` | REFUND | `document_upload` | `upload.pk` |
| Duplicate invoice detected | `for_duplicate_invoice()` | CONSUME | `document_upload` | `upload.pk` |
| Unsupported jurisdiction / schema | `for_unsupported_jurisdiction()` | REFUND | `document_upload` | `upload.pk` |
| Manual reprocess (re-extraction) | `for_reprocess()` | CONSUME | `reprocess` | `f"reprocess-{upload.pk}-{timestamp}"` (unique per attempt) |
| Rejection after human review | `for_rejection_after_review()` | NOOP | — | — |

### Credit Pipeline Integration

The Celery task (`process_invoice_upload_task`) determines credit outcome by pipeline stage:

| Stage | Outcome | Credit Action | Rationale |
|-------|---------|---------------|-----------|
| Step 0 (reserve) | Insufficient balance | Block upload (no task dispatched) | User sees error, no credit spent |
| Step 1 (OCR) | OCR failure | **Refund** | No meaningful extraction occurred |
| Step 1 (OCR) | OCR success → pipeline continues | — (pending) | Wait for final outcome |
| Step 2–5 (parse/normalize/validate/dedup) | Pipeline error | **Consume** | OCR resources were used |
| Step 6 (persist) | Persistence failure | **Consume** | OCR + LLM resources were used |
| Step 6a | Extraction succeeded | **Consume** | Full pipeline completed |
| Retry | Celery retry triggered | **No-op** | Idempotency prevents duplicate transactions; same `reference_id` |
| Max retries exhausted | Final failure | Last stage outcome applies | If OCR never succeeded → refund; if OCR succeeded → consume |

> **Sync fallback path** (no blob storage): OCR success → consume, extraction failure → refund.

**Idempotency**: `reserve()`, `consume()`, and `refund()` check for existing transactions with the same `reference_type + reference_id` before creating duplicates. This ensures safe retries and Celery retry safety. For initial uploads the `reference_id` is `str(upload.pk)`; for reprocesses it is `f"reprocess-{upload.pk}-{timestamp}"` (unique per attempt) so each reprocess attempt is independently idempotent without blocking subsequent ones.

**Invariant enforcement**: All credit mutations validate `balance_credits >= 0`, `reserved_credits >= 0`, `monthly_used >= 0`, and `balance_credits >= reserved_credits`. Violations raise `CreditAccountingError`.

**Reason codes**: `INSUFFICIENT_BALANCE`, `INACTIVE_ACCOUNT`, `MONTHLY_LIMIT_EXCEEDED`, `OK` — defined as constants in `credit_service.py`.

The workbench UI shows a credit strip with current balance and blocks uploads when credits are insufficient.

### Views

| URL | View | Permission | Description |
|-----|------|------------|-------------|
| `/extraction/credits/` | `credit_account_list` | `credits.view` | All accounts with search/pagination |
| `/extraction/credits/<user_id>/` | `credit_account_detail` | `credits.view` | Account detail + transaction ledger (50 most recent) + adjustment form |
| `/extraction/credits/<user_id>/adjust/` | `credit_account_adjust` | `credits.manage` | POST: add, subtract, set_limit, toggle_active |

### Audit Trail

Every credit operation is recorded in two layers:
- **CreditTransaction** — immutable ledger with balance snapshots, searchable in Django admin and the credit detail page
- **AuditEvent** — 7 event types: `CREDIT_CHECKED`, `CREDIT_RESERVED`, `CREDIT_CONSUMED`, `CREDIT_REFUNDED`, `CREDIT_ALLOCATION_UPDATED`, `CREDIT_LIMIT_EXCEEDED`, `CREDIT_MONTHLY_RESET`

### Management Command

```bash
python manage.py bootstrap_credit_accounts --initial-credits 100 --monthly-limit 50 --force
```
- Creates `UserCreditAccount` for all active users
- `--force` updates existing accounts
- `--initial-credits` sets starting balance (default: 0)
- `--monthly-limit` sets monthly cap (default: 0 = unlimited)

### Sidebar Navigation

**Credits** link in the Admin Console sidebar section, gated by `credits.manage` permission. Uses `bi-coin` icon. Visible to ADMIN and FINANCE_MANAGER roles.

---

## 18. OCR Cost Tracking

### Overview

The extraction console's Cost & Tokens panel tracks both LLM and OCR costs per extraction.

### Cost Calculation

| Component | Pricing | Tracked Fields |
|-----------|---------|----------------|
| **LLM** (GPT-4o) | $5.00/1M input tokens, $15.00/1M output tokens | `prompt_tokens`, `completion_tokens`, `total_tokens` |
| **OCR** (Azure Document Intelligence) | $1.50/1,000 pages | `ocr_page_count` |

**Total cost** = LLM cost + OCR cost

### Data Flow

1. `_ocr_document()` returns `(text, page_count, duration_ms)` — page count and duration captured at OCR time
2. `ExtractionResponse` carries `ocr_page_count`, `ocr_duration_ms`, `ocr_char_count`
3. `ExtractionResultPersistenceService` saves OCR fields to `ExtractionResult` model
4. Console view queries **all** `AgentRun` rows linked to the upload via `AgentRun.objects.filter(document_upload_id=..., agent_type=INVOICE_EXTRACTION)` and aggregates token fields using `SUM()` across every run (initial upload + all reprocesses)
5. Console calculates: `ocr_cost = ocr_pages x $1.50 / 1,000 x run_count` (OCR is re-run on every reprocess) and `llm_cost` from the aggregated token totals

### Multi-Run Aggregation

Each reprocess re-runs the full pipeline (OCR + LLM). `AgentRun.document_upload` (FK, indexed) links every run back to the originating `DocumentUpload`. The console therefore shows **cumulative** token usage and cost across all runs, not just the most recent.

| Data | Source | Behavior |
|------|--------|----------|
| `prompt_tokens`, `completion_tokens`, `total_tokens` | `SUM()` across all `AgentRun` rows for the upload | Cumulative across all runs |
| OCR cost | `ocr_page_count x $1.50/1000 x run_count` | Multiplied by number of extraction runs |
| `run_count` | `AgentRun.objects.filter(document_upload_id=...).count()` | Shown in "Extraction Runs" KPI card |

### Console Cost Panel

**Template**: `templates/extraction/console/_cost_tokens_panel.html`

**KPI Cards** (5):
- Total Cost (LLM + OCR, all runs) — warning color
- LLM Cost — primary color
- OCR Cost — info color
- Total Tokens (all runs) — success color
- Extraction Runs (`run_count`) — secondary color

**Cost Breakdown**: Side-by-side LLM vs OCR bars with dollar amounts and detail (token counts / page+char counts)

**Token Breakdown**: Stacked progress bar (prompt vs completion tokens, summed across all runs)

**Execution Details**: LLM Model, OCR Engine, Agent Type, Status, OCR Duration, LLM Duration, Timestamps, Pricing rates, Agent Run ID

---

## 19. Django Admin

### apps/extraction Admin

**File**: `apps/extraction/admin.py`

#### ExtractionResultAdmin

| Feature | Detail |
|---------|--------|
| List display | ID, upload, invoice, engine, confidence (color-coded), success badge, duration, created_at |
| Filters | success, engine_name, engine_version |
| Search | filename, error_message |
| Fieldsets | Links, Engine, Result, Raw Data (collapsed), Audit (collapsed) |

#### ExtractionApprovalAdmin

| Feature | Detail |
|---------|--------|
| List display | ID, invoice, status (color-coded), confidence (color-coded), fields_corrected_count, is_touchless, reviewed_by, reviewed_at |
| Filters | status, is_touchless |
| Search | invoice number, vendor name |
| Inlines | `ExtractionFieldCorrectionInline` (tabular, read-only) |
| Fieldsets | Links, Decision, Metrics, Snapshot (collapsed), Audit (collapsed) |

### apps/extraction_core Admin

**File**: `apps/extraction_core/admin.py` — 13 models registered

| Admin Class | List Display Highlights |
|-------------|------------------------|
| `TaxJurisdictionProfileAdmin` | country_code, country_name, tax_regime, default_currency, tax_id_label, is_active |
| `ExtractionSchemaDefinitionAdmin` | name, jurisdiction, document_type, schema_version, is_active |
| `ExtractionRuntimeSettingsAdmin` | name, jurisdiction_mode, default_country_code, default_regime_code, is_active |
| `EntityExtractionProfileAdmin` | entity, country_code, regime_code, jurisdiction_mode, is_active |
| `ExtractionRunAdmin` | id, document, status, country_code, overall_confidence, review_queue, requires_review, duration_ms |
| `ExtractionFieldValueAdmin` | extraction_run, field_code, value, confidence, category, is_corrected |
| `ExtractionLineItemAdmin` | extraction_run, line_index, confidence, is_valid |
| `ExtractionEvidenceAdmin` | extraction_run, field_code, page_number, extraction_method, confidence |
| `ExtractionIssueAdmin` | extraction_run, severity, field_code, check_type, message |
| `ExtractionApprovalRecordAdmin` | extraction_run, action, approved_by, decided_at |
| `ExtractionCorrectionAdmin` | extraction_run, field_code, original/corrected values, corrected_by |
| `ExtractionAnalyticsSnapshotAdmin` | snapshot_type, country_code, period, run_count, average_confidence |
| `CountryPackAdmin` | jurisdiction, pack_status, schema/validation/normalization versions, activated_at |

#### UserCreditAccountAdmin

| Feature | Detail |
|---------|--------|
| List display | User email, balance, reserved, available (color-coded), monthly_limit, monthly_used, is_active |
| Filters | is_active |
| Search | user email |
| Inlines | `CreditTransactionInline` (last 50 transactions, read-only) |
| Validation | Manual adjustments require `remarks` field; validates `balance >= reserved` to prevent invariant violation |

#### CreditTransactionAdmin

| Feature | Detail |
|---------|--------|
| List display | Account (email), transaction_type, credits, balance_after, reference_type, created_at |
| Filters | transaction_type, reference_type |
| Search | account email, reference_id, remarks |
| Read-only | All fields (immutable ledger — no add/edit/delete) |

---

## 20. File Reference

### apps/extraction (Application Layer — UI, Task, Core Models)

| File | Purpose |
|------|---------|
| `apps/extraction/models.py` | ExtractionResult, ExtractionApproval, ExtractionFieldCorrection models |
| `apps/extraction/tasks.py` | Main extraction pipeline Celery task |
| `apps/extraction/admin.py` | Django admin registrations (3 models) |
| `apps/extraction/template_views.py` | All 15 template views (workbench, upload, approval queue, console, country packs) |
| `apps/extraction/urls.py` | URL routing (15 routes) |
| `apps/extraction/api_urls.py` | API URL routing (empty) |
| `apps/extraction/services/extraction_adapter.py` | Azure DI OCR (with `features=[BARCODES]`) + QR decode + LLM extraction orchestration |
| `apps/extraction/services/qr_decoder_service.py` | `QRCodeDecoderService` — decode Indian e-invoice QR (4 strategies: `azure_barcode`, `ocr_text`, `ocr_irn_text`, `pyzbar`); `_unwrap_jwt()` for NIC-signed JWT detection; `_PLAIN_IRN_RE` for plain-text `IRN :` label fallback; `QRInvoiceData` dataclass; serialized to `raw_response["_qr"]` |
| `apps/extraction/services/parser_service.py` | JSON → ParsedInvoice dataclass parsing |
| `apps/extraction/services/normalization_service.py` | Field normalization (dates, amounts, strings) |
| `apps/extraction/services/field_confidence_service.py` | Per-field confidence scoring (0.0–1.0) + evidence-aware adjustments (`ocr_text`, `evidence_context`, `qr_verified`); QR match → 0.99, QR mismatch → cap 0.40; serialized to `raw_response["_field_confidence"]` |
| `apps/extraction/services/reconciliation_validator.py` | 6 deterministic math checks; structured issues with severity (ERROR/WARNING/INFO); serialized to `raw_response["_validation"]` |
| `apps/extraction/services/validation_service.py` | Mandatory field validation + deterministic confidence scoring + critical field check (reads `field_confidence`, sets `requires_review_override`) |
| `apps/extraction/decision_codes.py` | Centralized machine-readable decision code constants + `derive_codes()` (accepts `qr_data`) + `ROUTING_MAP` + `HARD_REVIEW_CODES`; includes `QR_DATA_VERIFIED`, `QR_MISMATCH`, `QR_IRN_PRESENT`, `IRN_DUPLICATE`; serialized to `raw_response["_decision_codes"]` |
| `apps/extraction/services/recovery_lane_service.py` | `RecoveryLaneService` — deterministic policy evaluation + fail-silent `InvoiceUnderstandingAgent` invocation; serialized to `raw_response["_recovery"]` |
| `apps/extraction/services/duplicate_detection_service.py` | Duplicate invoice detection |
| `apps/extraction/services/persistence_service.py` | Invoice + LineItem + ExtractionResult persistence |
| `apps/extraction/services/approval_service.py` | Approval lifecycle (approve/reject/auto-approve + analytics) |
| `apps/extraction/services/upload_service.py` | File upload, hash computation, DocumentUpload creation |
| `apps/extraction/services/credit_service.py` | Credit reserve/consume/refund/allocate/adjust service + `ChargePolicy` (centralized charge/refund decisions) + audit events, idempotency, invariant enforcement |
| `apps/extraction/services/confidence_scorer.py` | Deterministic confidence scoring for legacy pipeline (field coverage 50%, line quality 30%, consistency 20%) |
| `apps/extraction/services/execution_context.py` | ExecutionContext dataclass + get_execution_context() — centralized governed/legacy data resolution |
| `apps/extraction/credit_models.py` | UserCreditAccount + CreditTransaction models |
| `apps/extraction/credit_views.py` | Credit account list/detail/adjust views |
| `apps/extraction/forms.py` | CreditAdjustmentForm (add/subtract/set_limit/toggle_active) |
| `apps/extraction/management/commands/bootstrap_credit_accounts.py` | Bootstrap credit accounts for all users |

### apps/extraction_core (Platform Layer — Configuration, Execution, Governance)

| File | Purpose |
|------|---------|
| `apps/extraction_core/models.py` | 13 models (jurisdiction, schema, runtime, entity, run, field, line item, evidence, issue, approval, correction, analytics, country pack) |
| `apps/extraction_core/admin.py` | Django admin registrations (13 models) |
| `apps/extraction_core/views.py` | Configuration API ViewSets (jurisdictions, schemas, settings, entity profiles, resolve/lookup) |
| `apps/extraction_core/extraction_views.py` | Execution API ViewSets (runs, country packs, analytics, pipeline trigger) |
| `apps/extraction_core/serializers.py` | Configuration API serializers |
| `apps/extraction_core/extraction_serializers.py` | Execution API serializers |
| `apps/extraction_core/api_urls.py` | Configuration API URL routing (`/api/v1/extraction-core/`) |
| `apps/extraction_core/extraction_api_urls.py` | Execution API URL routing (`/api/v1/extraction-pipeline/`) |
| **Core Pipeline & Orchestration** | |
| `apps/extraction_core/services/extraction_pipeline.py` | 11-stage governed pipeline orchestrator |
| `apps/extraction_core/services/extraction_service.py` | Original pipeline orchestrator (`ExtractionExecutionResult` dataclass — renamed from `ExtractionResult`; legacy alias emits `DeprecationWarning` via module `__getattr__`, target removal 2026-Q3) |
| `apps/extraction_core/services/base_extraction_service.py` | Schema-driven extraction base class |
| **Jurisdiction Resolution** | |
| `apps/extraction_core/services/jurisdiction_resolver.py` | Multi-signal jurisdiction detection |
| `apps/extraction_core/services/resolution_service.py` | 4-tier resolution cascade |
| **Schema & Registry** | |
| `apps/extraction_core/services/schema_registry.py` | Cached, version-aware schema lookup |
| **Document Intelligence** | |
| `apps/extraction_core/services/document_classifier.py` | Multilingual document type classification |
| `apps/extraction_core/services/relationship_extractor.py` | PO/GRN/contract cross-reference extraction |
| `apps/extraction_core/services/party_extractor.py` | Supplier/buyer/ship-to/bill-to extraction |
| `apps/extraction_core/services/document_intelligence.py` | Pre-extraction analysis orchestrator |
| **Field Extraction & Parsing** | |
| `apps/extraction_core/services/line_item_extractor.py` | Schema-driven line item extraction |
| `apps/extraction_core/services/page_parser.py` | Multi-page OCR text segmentation |
| `apps/extraction_core/services/table_stitcher.py` | Cross-page table continuation |
| **Normalization & Validation** | |
| `apps/extraction_core/services/normalization_service.py` | Jurisdiction-driven field normalization |
| `apps/extraction_core/services/enhanced_normalization.py` | Country-specific normalization (IN/AE/SA/DE/FR) |
| `apps/extraction_core/services/validation_service.py` | Jurisdiction-driven field validation |
| `apps/extraction_core/services/enhanced_validation.py` | Country-aware validation with ExtractionIssue persistence |
| **Evidence, Audit & Tracing** | |
| `apps/extraction_core/services/evidence_service.py` | Field provenance capture → ExtractionEvidence records |
| `apps/extraction_core/services/extraction_audit.py` | Extraction-specific audit logging with `event_category` taxonomy (business/governance/telemetry). NOTE: log_extraction_approved/rejected are deprecated no-ops — use GovernanceTrailService |
| `apps/extraction_core/services/governance_trail.py` | GovernanceTrailService — sole writer of ExtractionApprovalRecord (uses `update_or_create` inside `transaction.atomic`) |
| **Confidence & Review Routing** | |
| `apps/extraction_core/services/confidence_scorer.py` | Multi-dimensional confidence scoring |
| `apps/extraction_core/services/review_routing.py` | Confidence-driven review routing |
| `apps/extraction_core/services/review_routing_engine.py` | Queue-based routing; extended with `decision_codes` param for code-first routing (Rule 0 — highest precedence) |
| **LLM & Prompts** | |
| `apps/extraction_core/services/prompt_builder.py` | Dynamic LLM prompt construction |
| `apps/extraction_core/services/prompt_builder_service.py` | Enhanced data-driven prompt builder |
| `apps/extraction_core/services/llm_extraction_adapter.py` | LLM client wrapper for schema extraction |
| **Master Data & Learning** | |
| `apps/extraction_core/services/master_data_enrichment.py` | Post-extraction vendor/PO/customer matching |
| `apps/extraction_core/services/learning_service.py` | Analytics from corrections/failures → ExtractionAnalyticsSnapshot |
| **Country Governance** | |
| `apps/extraction_core/services/country_pack_service.py` | Country pack lifecycle management |
| **Output Contract** | |
| `apps/extraction_core/services/output_contract.py` | Canonical extraction output contract (MetaBlock, FieldValue, PartiesBlock, TaxBlock, LineItemRow) |

### Agent Framework

| File | Purpose |
|------|---------|
| `apps/agents/services/agent_classes.py` | InvoiceExtractionAgent + InvoiceUnderstandingAgent |
| `apps/agents/services/base_agent.py` | BaseAgent ReAct framework |
| `apps/core/prompt_registry.py` | LLM prompt templates (extraction.invoice_system) |

### Shared Infrastructure

| File | Purpose |
|------|---------|
| `apps/core/enums.py` | InvoiceStatus, ExtractionApprovalStatus, AuditEventType (incl. 15 extraction governance events), DocumentType |
| `apps/core/utils.py` | Normalization utilities (strings, dates, amounts, PO numbers) |
| `apps/documents/models.py` | DocumentUpload, Invoice, InvoiceLineItem models |
| `config/settings.py` | Azure credentials, thresholds, auto-approve config, OCR toggle |

### Templates

| File | Purpose |
|------|---------|
| `templates/extraction/workbench.html` | Extraction workbench UI (Agent Runs + Approvals tabs) |
| `templates/extraction/result_detail.html` | Extraction result detail UI |
| `templates/extraction/approval_detail.html` | Approval review UI |
| `templates/extraction/approval_queue.html` | Deprecated — redirects to workbench |
| `templates/extraction/country_packs.html` | Country pack governance (KPI strip + table) |
| `templates/extraction/credit_account_list.html` | Credit account list with search/pagination |
| `templates/extraction/credit_account_detail.html` | Credit account detail + transaction ledger + adjustment form |
| `templates/extraction/console/console.html` | Main review console layout (6 tabs) |
| `templates/extraction/console/_header_bar.html` | Command bar (status, confidence, review queue, schema, method badges) |
| `templates/extraction/console/_document_viewer.html` | **Deprecated** — no longer included in layout |
| `templates/extraction/console/_extracted_data.html` | Tab 1: Header, Parties, Tax, Enrichment, Line Items |
| `templates/extraction/console/_confidence_badge.html` | Reusable confidence % badge (green/amber/red) |
| `templates/extraction/console/_validation_panel.html` | Tab 2: Errors/Warnings/Passed with go-to-field |
| `templates/extraction/console/_evidence_panel.html` | Tab 3: Evidence cards with source snippets |
| `templates/extraction/console/_reasoning_panel.html` | Tab 4: Agent reasoning timeline |
| `templates/extraction/console/_audit_trail.html` | Tab 5: Chronological audit event timeline |
| `templates/extraction/console/_corrections_panel.html` | Tab 6: Field correction audit trail (original → corrected) |
| `templates/extraction/console/_bottom_timeline.html` | Pipeline stage progress bar |
| `templates/extraction/console/_approve_modal.html` | Approval confirmation modal |
| `templates/extraction/console/_reprocess_modal.html` | Reprocess extraction modal |
| `templates/extraction/console/_escalate_modal.html` | Escalation modal |
| `templates/extraction/console/_cost_tokens_panel.html` | Cost & Tokens panel (LLM + OCR cost breakdown, token usage, execution details) |
| `templates/extraction/console/_comment_modal.html` | Add comment modal |

### Static Assets

| File | Purpose |
|------|---------|
| `static/css/extraction_console.css` | Review console custom styles (~200 lines) |
| `static/js/extraction_console.js` | Review console JavaScript (~200 lines) |
| `templates/partials/sidebar.html` | Navigation sidebar (extraction + country packs + credits links) |

---

## Debugging Tips

- **LLM calls failing?** Check `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` env vars.
- **OCR failing?** Check `AZURE_DI_ENDPOINT` and `AZURE_DI_KEY` env vars.
- **OCR disabled?** Check `ExtractionRuntimeSettings.ocr_enabled` in the Extraction Control Center, or `EXTRACTION_OCR_ENABLED` env var. When disabled, native PDF extraction via PyPDF2 is used (no Azure DI cost).
- **Credits showing 0?** If `bootstrap_credit_accounts` was run before the user existed, use `--force` flag to update existing accounts. Or adjust via `/extraction/credits/<user_id>/`.
- **Upload blocked (insufficient credits)?** Check the user's `UserCreditAccount.balance_credits` and `monthly_used` vs `monthly_limit`. Adjust via credit management UI or Django admin.
- **Extraction task not running?** On Windows without Redis, ensure `CELERY_TASK_ALWAYS_EAGER=True` (tasks run synchronously).
- **Confidence showing 1%?** `extraction_confidence` is stored as 0.0–1.0 float; templates use `{% widthratio %}` to display as percentage.
- **Auto-approve not working?** Check both `EXTRACTION_AUTO_APPROVE_ENABLED=true` AND `EXTRACTION_AUTO_APPROVE_THRESHOLD` < 1.0 (e.g., 0.95).
- **Agent 400 errors from OpenAI?** Ensure tool-calling messages follow OpenAI format: assistant messages include `tool_calls` array, tool responses include `tool_call_id`.
- **Approval queue empty?** Invoices only appear when `status=PENDING_APPROVAL` — check that the extraction pipeline completed successfully and auto-approve didn't trigger.
- **Recovery lane not triggering?** Check `raw_response["_decision_codes"]` — recovery only fires for named codes (`INV_NUM_UNRECOVERABLE`, `TOTAL_MISMATCH_HARD`, etc.), not for generic low confidence.
- **prompt_source shows None in console?** The extraction may predate Phase 2 hardening — `_prompt_meta` is absent from older `raw_response` records. `_enrich_hardening_fields()` handles this gracefully (returns `None`).
- **derive_codes returns empty?** Check that `FieldConfidenceService` and `ReconciliationValidatorService` ran successfully (steps 3a and 4a). If they failed silently, their results are `None` and `derive_codes()` receives no inputs.

---

## 21. Bulk Extraction Intake (Phase 1)

Bulk Extraction Intake allows operators to point the system at a folder or cloud drive, discover all invoice documents, and process them through the existing extraction pipeline in a single job. This is Phase 1 — manual-start, batch-oriented intake.

### 21.1 Overview

- **Manual start only** — no watched folders, no continuous sync.
- **Reuses existing pipeline** — each discovered file goes through the same `DocumentUpload` → `process_invoice_upload_task` → extraction → approval flow.
- **Per-item credit reservation** — one credit reserved and consumed per file; credit-blocked items are skipped without stopping the job.
- **Duplicate protection** — by `source_file_id` within the job and by SHA-256 `file_hash` against existing `DocumentUpload` records.

### 21.2 Supported Sources

| Source Type | Adapter | Auth | Config Keys |
|---|---|---|---|
| `LOCAL_FOLDER` | `LocalFolderBulkSourceAdapter` | Filesystem access | `folder_path` |
| `GOOGLE_DRIVE` | `GoogleDriveBulkSourceAdapter` | Service-account JSON | `service_account_json`, `folder_id` |
| `ONEDRIVE` | `OneDriveBulkSourceAdapter` | Client credentials OAuth2 | `tenant_id`, `client_id`, `client_secret`, `drive_id`, `folder_path` |

Supported file types: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`.

### 21.3 Data Models

All models in `apps/extraction/bulk_models.py`.

| Model | Inherits | Purpose |
|---|---|---|
| `BulkSourceConnection` | `BaseModel` | Reusable source configuration (name, type, `config_json`) |
| `BulkExtractionJob` | `BaseModel` | One batch run — tracks status, counters, timestamps |
| `BulkExtractionItem` | `TimestampMixin` | One file within a job — tracks status, links to `DocumentUpload` and `ExtractionRun` |

**Job status flow:**
```
QUEUED → SCANNING → PROCESSING → COMPLETED | PARTIAL_FAILED | FAILED
```

**Item status flow:**
```
DISCOVERED → REGISTERED → PROCESSING → PROCESSED
           → SKIPPED (unsupported type)
           → DUPLICATE (file hash or source_file_id collision)
           → CREDIT_BLOCKED (insufficient credits)
           → FAILED (download/upload/extraction error)
           → UNSUPPORTED (non-supported extension)
```

### 21.4 Processing Flow

1. User selects a `BulkSourceConnection` and clicks "Start Job" in the UI.
2. `BulkExtractionService.create_job()` creates a `QUEUED` job and logs `BULK_JOB_CREATED` audit event.
3. `run_bulk_job_task` (Celery) calls `BulkExtractionService.run_job()`:
   - **Validate** source connection config.
   - **Scan** — adapter's `list_files()` discovers documents; items are created as `DISCOVERED`.
   - **Process** each item sequentially:
     - Duplicate check (source_file_id within prior items + SHA-256 hash against `DocumentUpload`).
     - Credit reservation via `CreditService.reserve()`.
     - Download via adapter's `download_file()`.
     - Compute SHA-256 hash, re-check for hash duplicates.
     - Create `DocumentUpload` record.
     - Upload to Azure Blob Storage.
     - Run extraction synchronously via `process_invoice_upload_task.run()`.
     - Credit consumption via `CreditService.consume()`.
     - Link `ExtractionRun` to the item.
   - **Finalize** — compute counters, set terminal status.
4. Extracted invoices enter the normal approval queue.

### 21.5 Credit Handling

- Uses the existing `CreditService` from `apps/extraction/services/credit_service.py`.
- **Per-item** reserve → consume lifecycle. If reservation fails, the item is marked `CREDIT_BLOCKED` and the job continues with remaining items.
- On item failure after reservation, credits are refunded via `CreditService.refund()`.
- Reference type: `"bulk_item"`, reference ID: `BulkExtractionItem.id`.

### 21.6 Duplicate Protection

Two layers:
1. **Source-level** — `source_file_id` uniqueness within prior `BulkExtractionItem` records for the same source connection.
2. **Content-level** — SHA-256 file hash checked against `DocumentUpload.file_hash` across the entire system.

Duplicates are marked with `DUPLICATE` status and a descriptive `skip_reason`.

### 21.7 UI & Routes

| URL | View | Permission | Method |
|---|---|---|---|
| `/extraction/bulk/` | `bulk_job_list` | `extraction.bulk_view` | GET |
| `/extraction/bulk/start/` | `bulk_job_start` | `extraction.bulk_create` | POST |
| `/extraction/bulk/<id>/` | `bulk_job_detail` | `extraction.bulk_view` | GET |

Templates: `templates/extraction/bulk_job_list.html`, `templates/extraction/bulk_job_detail.html`.

Sidebar entry: "Bulk Extraction" under AI Agents section, gated by `extraction.bulk_view`.

### 21.8 Permissions

| Code | Roles Granted |
|---|---|
| `extraction.bulk_view` | ADMIN, AP_PROCESSOR, FINANCE_MANAGER, AUDITOR, SYSTEM_AGENT |
| `extraction.bulk_create` | ADMIN, AP_PROCESSOR, FINANCE_MANAGER, SYSTEM_AGENT |

### 21.9 Audit Events

| Event Type | When |
|---|---|
| `BULK_JOB_CREATED` | Job record created |
| `BULK_JOB_STARTED` | Job begins processing |
| `BULK_ITEM_REGISTERED` | Item enters extraction pipeline |
| `BULK_ITEM_SKIPPED` | Item skipped (unsupported/duplicate) |
| `BULK_ITEM_CREDIT_BLOCKED` | Insufficient credits for item |
| `BULK_JOB_COMPLETED` | Job finished (success or partial) |
| `BULK_JOB_FAILED` | Job failed with unrecoverable error |

### 21.10 Phase 1 Limitations

- **Manual start only** — no watched folders, no scheduled polling.
- **Sequential item processing** — items are processed one at a time within a job (no parallel extraction).
- **No re-import** — failed items cannot be retried individually; start a new job.
- **No continuous sync** — no change detection or incremental scanning.
- **Google Drive / OneDrive adapters** require external libraries (`google-api-python-client`, `msal`, `requests`) — not yet in `requirements.txt`.

### 21.11 Files

| File | Purpose |
|---|---|
| `apps/extraction/bulk_models.py` | BulkSourceConnection, BulkExtractionJob, BulkExtractionItem |
| `apps/extraction/services/bulk_source_adapters.py` | Source adapters (Local, Google Drive, OneDrive) + factory |
| `apps/extraction/services/bulk_service.py` | BulkExtractionService orchestrator |
| `apps/extraction/bulk_tasks.py` | Celery task `run_bulk_job_task` |
| `apps/extraction/bulk_views.py` | Template views (list, start, detail) |
| `templates/extraction/bulk_job_list.html` | Job list + start modal |
| `templates/extraction/bulk_job_detail.html` | Job detail + items table |

---

## 22. Langfuse Observability

Full reference: `docs/LANGFUSE_INTEGRATION.md`

### 22.1 Active trace call sites

| # | Name | Location | Trace ID |
|---|---|---|---|
| 1 | `invoice_extraction` | `InvoiceExtractionAgent.run()` — standalone (no pipeline trace) | Django `trace_id` |
| 2 | `llm_extract_fallback` | `InvoiceExtractionAdapter._llm_extract()` — direct Azure OpenAI fallback | `f"inv-{invoice_id}"` or uuid |
| 3 | Extraction pipeline scores | `ExtractionPipeline.run()` Step 9 | `str(run.pk)` |
| 4 | Approval scores | `ExtractionApprovalService` (auto-approve, approve, reject) | `f"approval-{approval.pk}"` |

### 22.2 LLM fallback trace structure

When `InvoiceExtractionAgent` is unavailable and `_llm_extract()` is called
directly, a standalone root trace records the Azure OpenAI call:

```
llm_extract_fallback   (start_trace)
  -- LLM_EXTRACT_FALLBACK   (start_span)
     -- llm_extract_fallback_chat   (log_generation, with token counts)
```

The system prompt is fetched once via `_get_extraction_prompt()` and reused
in both the `client.chat.completions.create()` call and `log_generation`.

### 22.3 Approval score lifecycle

All scores use `f"approval-{approval.pk}"` as trace ID, linking priority,
confidence, and decision scores for the same approval record in Langfuse.

```
approval-42
  score: extraction_auto_approve_confidence = 0.94   (try_auto_approve)
    -- OR --
  score: extraction_approval_decision        = 1.0    (approve)
  score: extraction_approval_confidence      = 0.87   (approve)
  score: extraction_corrections_count        = 3.0    (if corrections made)
    -- OR --
  score: extraction_approval_decision        = 0.0    (reject)
```

### 22.4 Session attribution

Every standalone extraction trace uses `session_id=f"invoice-{invoice_id}"`.
This groups all LLM calls for the same invoice across retries and re-runs
into one Langfuse session. The `user_id` is set to `actor_user_id` (the
Django `User.pk`) so you can filter traces per reviewer in the Users tab.

### 22.5 Known SDK quirk (v4)

Langfuse SDK v4 removed `user_id`/`session_id` from `start_observation()`.
Both are set post-creation as OTel span attributes. Do **not** pass them
directly to `start_observation()` -- this causes a silent `TypeError`
that returns `None` and breaks all traces. See `docs/LANGFUSE_INTEGRATION.md`
Issue 1 for the fix pattern.

---

## 22. Phase 2 Hardening

This section documents the five hardening changes added after Phase 2 (modular prompt composition + response repair + field confidence). All changes are additive and fail-silent — no breaking changes to existing approval, governance, or Langfuse flows.

### 22.1 Decision Codes

**File**: `apps/extraction/decision_codes.py`

Machine-readable string constants for every named failure mode in the pipeline. Replaces ad-hoc string matching in routing and recovery logic.

**`derive_codes(validation_result, recon_val_result, field_conf_result, prompt_source_type, qr_data=None)`**:
- Called at pipeline step 4b (after all validation and math checks)
- Returns a deduplicated, stable-order list of applicable codes
- Embedded into `raw_response["_decision_codes"]` and audit metadata
- `qr_data` (optional `QRInvoiceData`) adds QR-specific codes: `QR_IRN_PRESENT`, `QR_DATA_VERIFIED`, `QR_MISMATCH`
- Fail-silent: returns `[]` on any exception

**Usage in downstream components**:

| Consumer | How it uses codes |
|----------|------------------|
| `RecoveryLaneService.evaluate()` | Checks membership in `RECOVERY_TRIGGER_CODES` |
| `ReviewRoutingEngine.evaluate()` | Maps codes via `ROUTING_MAP` for queue assignment (Rule 0, highest precedence) |
| `ExecutionContext` | Populated from `raw_response["_decision_codes"]` for UI display |
| Audit log | Included in `AuditService` metadata for every `EXTRACTION_COMPLETED` event |

### 22.2 Recovery Lane

**File**: `apps/extraction/services/recovery_lane_service.py`

Post-extraction bounded correction. Triggered **only** by named failure modes — **not** by generic low-confidence scores.

**Design rules**:
- `evaluate()` is a pure deterministic function (no I/O, no DB calls)
- `invoke()` is the only function that touches the database (creates `AgentRun`)
- Output is strictly additive — original extraction is never modified
- Always fail-silent — pipeline never raises due to recovery lane failure
- Agent is `InvoiceUnderstandingAgent` with `reconciliation_result=None` and bounded `ctx.extra`

**Recovery trigger flow**:
```
derive_codes()
    └─ any code in RECOVERY_TRIGGER_CODES?
           │ YES
           ▼
    RecoveryLaneService.evaluate(codes)  →  RecoveryDecision
           │ should_invoke=True
           ▼
    RecoveryLaneService.invoke(decision, invoice_id, ...)
           │
           ▼
    InvoiceUnderstandingAgent.run(ctx)
           │
           ▼
    RecoveryResult  →  raw_response["_recovery"]
                   →  AgentRun.input_payload["_recovery_meta"]
```

**Not triggered by**:
- `LOW_CONFIDENCE_CRITICAL_FIELD` alone
- `LINE_SUM_MISMATCH` alone
- Any confidence score below threshold (only named codes trigger)

### 22.3 Evidence-Aware Field Confidence

**File**: `apps/extraction/services/field_confidence_service.py` (extended)

`FieldConfidenceService.score()` accepts two new optional parameters:

- **`ocr_text: str`** — Raw OCR text. When a critical field's extracted value appears verbatim in the OCR text (≥ 3 chars), its score is boosted by +0.10 (capped at 0.95). Confirmed in `evidence_flags[field] = "... ocr_confirmed"`.

- **`evidence_context: dict`** — Extraction evidence hints:
  - `"extraction_method"`: `"explicit"` | `"repaired"` | `"recovered"` | `"derived"` — caps critical field scores when the overall extraction was not explicit.
  - `"snippets"`: dict mapping field name → raw text snippet from the document. Each present snippet boosts the field score by +0.05 (capped at 0.90).
  - `"qr_verified"`: dict mapping field name → QR ground-truth value (populated by `QRInvoiceData.to_evidence_context()`). QR match → score **0.99**; QR mismatch → score capped at **0.40**. See §23 for full QR verification flow.

**`evidence_flags`** (new field on `FieldConfidenceResult`): records why each adjusted field was modified. Included in `raw_response["_field_confidence"]["evidence_flags"]`. QR-specific flags: `"qr_confirmed"` and `"qr_mismatch:extracted=...|qr=..."`.

**Backward compatible**: Both params are optional; existing call sites without them produce identical results (no `evidence_flags` populated).

### 22.4 Prompt Source Audit Trail

**File**: `apps/agents/services/agent_classes.py` — `InvoiceExtractionAgent`

Previously, the agent silently fell back from composed prompt to monolithic fallback without recording which path was used. Now:

- `_init_messages()` explicitly records `self._prompt_source_type = "composed"` or `"monolithic_fallback"`
- After `_finalise_run()`, the agent persists full prompt metadata to `AgentRun.input_payload["_prompt_meta"]`
- `AgentRun.prompt_version` = `prompt_hash[:50]` (or fallback source string)
- `AgentRun.invocation_reason` = `"extraction:<source_type>"`

**Prompt source precedence** (in order):
1. `ctx.extra["composed_prompt"]` — modular composed prompt from `InvoicePromptComposer` → `"composed"`
2. `PromptRegistry.get("extraction.invoice_system")` — monolithic fallback → `"monolithic_fallback"`

If path 2 is taken, `PROMPT_COMPOSITION_FALLBACK_USED` is emitted in step 4b decision codes.

### 22.5 ExecutionContext Extensions

**File**: `apps/extraction/services/execution_context.py`

Five new fields on `ExecutionContext` (Phase 2 hardening), populated on all resolution paths (governed, legacy lookup, and pure legacy) via `_enrich_hardening_fields()`:

```python
decision_codes: list           # from raw_response["_decision_codes"]
prompt_source: str | None      # from raw_response["_prompt_meta"]["prompt_source_type"]
prompt_hash: str | None        # from raw_response["_prompt_meta"]["prompt_hash"]
recovery_lane_invoked: bool    # from raw_response["_recovery"]["invoked"]
recovery_lane_succeeded: bool | None  # set only when recovery_lane_invoked=True
```

### 22.6 ReviewRoutingEngine — Decision Code Routing (Rule 0)

**File**: `apps/extraction_core/services/review_routing_engine.py`

`ReviewRoutingEngine.evaluate()` extended with optional `decision_codes: List[str]` parameter.

When provided, **Rule 0** runs first via `_apply_decision_codes()`:
- Maps each code to a queue via `ROUTING_MAP`
- Uses a priority ladder: `EXCEPTION_OPS > TAX_REVIEW > MASTER_DATA_REVIEW > AP_REVIEW`
- Sets priority `"CRITICAL"` if any `HARD_REVIEW_CODES` member is present, else `"HIGH"`
- If `EXCEPTION_OPS` with `CRITICAL` priority is set, returns immediately (skips all other rules)
- Falls through to confidence-based rules 1–6 for any remaining routing logic

Fully backward-compatible: `decision_codes=None` (default) skips Rule 0 entirely.

### 22.7 raw_response Key Summary

All Phase 2 hardening outputs are embedded as private keys in `ExtractionResult.raw_response`:

| Key | Set by | Content |
|-----|--------|---------|
| `_repair` | `ResponseRepairService` | Repair actions applied, fields modified |
| `_field_confidence` | `FieldConfidenceService` | Per-field scores + `evidence_flags` (incl. QR match/mismatch flags) |
| `_validation` | `ReconciliationValidatorService` | 6 math check results + severity |
| `_prompt_meta` | `InvoiceExtractionAgent.run()` | Prompt source type, hash, component versions |
| `_decision_codes` | `derive_codes()` | List of machine-readable code strings (incl. QR codes) |
| `_recovery` | `RecoveryLaneService.invoke()` | Agent output, trigger codes, succeeded flag |
| `_qr` | `QRCodeDecoderService` (via adapter) | Decoded e-invoice QR payload: `irn`, `irn_date`, `seller_gstin`, `buyer_gstin`, `doc_number`, `doc_date`, `total_value`, `item_count`, `main_hsn`, `doc_type`, `decode_strategy`, `signature_verified` |

---

## 23. Indian e-Invoice QR Code Support

> **Added**: 2026-03-28

### Background

All B2B invoices from Indian businesses with turnover > ₹5 Cr are mandated under the GST e-invoice scheme (GSTN notification). Before being shared with the buyer, every invoice is registered on the **Invoice Registration Portal (IRP / NIC)**, which:
1. Validates the invoice
2. Assigns an **IRN** (Invoice Reference Number) — a 64-character SHA-256 hash
3. Stamps a **digitally-signed QR code** containing key invoice fields

This QR code is printed on every compliant Indian B2B invoice and is the **highest-confidence source of ground truth** available for extraction — more reliable than OCR text because it is:
- Machine-generated (no OCR errors)
- Cryptographically tied to the invoice via IRP's digital signature
- Canonical (the same values the government's portal accepted)

### QR Payload (GSTN e-Invoice Spec v1.1)

IRP QR payload format — two variants:

**a. Plain JSON** (spec v1.0 / some vendors):
```json
{
  "Version":    "1.1",
  "Irn":        "<64-char sha256 hex>",
  "IrnDt":      "2024-01-15 10:30:00",
  "SellerGstin":"29AAAAA0000A1ZA",
  "BuyerGstin": "07BBBBB0000B1ZD",
  "DocNo":      "INV/2024/001",
  "DocDt":      "15/01/2024",
  "TotInvVal":  11800.00,
  "ItemCnt":    3,
  "MainHsnCode":"8471",
  "DocTyp":     "INV"
}
```

**b. NIC-signed JWT** (spec v1.1 — production standard):
```
<base64url_header>.<base64url_payload>.<signature>
JWT header:   {"alg": "RS256", "typ": "JWT"}
JWT payload:  {"iss": "NIC", "data": "<stringified e-invoice JSON>"}
```
The invoice fields live inside `payload["data"]` as a JSON string. `_unwrap_jwt()` handles detection and unwrapping before `_parse_einvoice_json` is called.

`DocTyp` values: `"INV"` (invoice), `"CRN"` (credit note), `"DBN"` (debit note).

### QRCodeDecoderService

**File**: `apps/extraction/services/qr_decoder_service.py`

Stateless, fail-silent. All methods are `@staticmethod`. Returns `Optional[QRInvoiceData]` — never raises.

**Four decode strategies** (attempted in order, first success wins):

| # | Strategy | Source | Requires |
|---|----------|--------|---------|
| 1 | **Azure DI barcodes** | `qr_texts` list from `_ocr_document()` — `decode_from_texts` calls `_unwrap_jwt` then `_parse_einvoice_json` | `features=[AnalysisFeature.BARCODES]` in API call |
| 2 | **OCR text — JSON inline** | `ocr_text` from Azure DI or native PDF | Nothing extra — `_decode_from_ocr_text` Path A searches for 64-char IRN JSON pattern |
| 3 | **OCR text — plain-text IRN label** | `ocr_text` | Nothing extra — `_decode_from_ocr_text` Path B matches `IRN :` label on invoice face |
| 4 | **pyzbar pixel decode** | Raw image bytes from file | `pip install pyzbar Pillow` (optional) + PyMuPDF or pdf2image for PDFs |

**`_unwrap_jwt(text: str) -> Optional[str]`** — called by `decode_from_texts` before JSON parsing:
- Detects JWT format: text with 3 `.`-separated parts
- Base64url-decodes the middle part (JWT payload)
- Extracts `payload["data"]` (the stringified e-invoice JSON) and returns it for `_parse_einvoice_json`
- If `payload` itself contains `"Irn"` key (no nested `"data"`), serialises payload as JSON
- Returns `None` if text is not a JWT — falls through to direct JSON parse

**`_decode_from_ocr_text` — two paths:**

**Path A** (existing): QR JSON payload appears inline in OCR text. Azure DI sometimes includes decoded barcode text in the OCR output. Searches for `"Irn"\s*:\s*"<64hex>"` pattern.

**Path B** (new): plain-text IRN label detection — fallback when no QR JSON is found in OCR text:
- Pre-processing: joins PDF hyphenated line-breaks (`-\n` followed by hex char → remove hyphen+newline)
- Regex: `_PLAIN_IRN_RE = re.compile(r'\bIRN\b\s*[:\-]?\s*([a-fA-F0-9]{64})', re.IGNORECASE)`
- Builds minimal `QRInvoiceData(irn=..., seller_gstin=..., buyer_gstin=..., decode_strategy="ocr_irn_text")`
- GSTINs harvested from full OCR text via `_GSTIN_RE`
- `doc_number`, `doc_date`, `total_value` are **empty** — only IRN + GSTINs are recoverable this way
- Useful for confirming e-invoice registration when the QR cannot be decoded

**`decode_strategy` field values:**

| Strategy | Description | Fields available |
|----------|-------------|-----------------|
| `azure_barcode` | Azure DI barcodes add-on decoded the QR (JWT or plain JSON) | All fields |
| `ocr_text` | QR JSON found inline in OCR text | All fields |
| `ocr_irn_text` | IRN extracted from plain-text `IRN :` label on invoice face | IRN + GSTINs only; `doc_number`, `doc_date`, `total_value` are empty |
| `pyzbar` | pyzbar pixel-level image decode | All fields |

**Strategy 1 is the primary path** because Azure DI pre-decodes the QR to a text string before passing it to our service. Strategies 2–4 are fallbacks for cases where:
- The Azure DI barcodes API did not decode the QR (very small/distorted QR, or older SDK without features support)
- Native PDF extraction was used instead of Azure DI (OCR disabled)
- `pyzbar` is available for high-accuracy pixel-level decode as a last resort

**What happens when OCR doesn't return the barcode value?**

Azure DI returns barcodes **only when `features=[AnalysisFeature.BARCODES]` is explicitly passed** to `begin_analyze_document()`. Without this flag, `page.barcodes` is always empty. The pipeline handles this gracefully:

```
Azure DI call WITHOUT features=BARCODES
    → page.barcodes = []
    → qr_texts = []
    → Strategy 1: no-op (empty list)
    → Strategy 2: OCR text Path A — IRN JSON regex scan
        → If the QR was decoded into text by Azure DI's text layer
          (unusual but possible for large/clear QR codes): finds JSON
        → Otherwise: no match
    → Strategy 3: OCR text Path B — plain-text IRN label scan
        → Matches "IRN : <64hex>" on invoice face
        → Returns partial QRInvoiceData (IRN + GSTINs only)
    → Strategy 4: pyzbar pixel decode (if installed)
        → Decodes QR from raw image pixels — works regardless of
          what Azure DI returned in text
    → If all strategies fail: qr_data = None
        → pipeline continues without QR data (no degradation)
```

The `features=[AnalysisFeature.BARCODES]` flag is now set in `_ocr_document()`. If the flag is removed or the SDK is downgraded, strategies 2–4 remain as fallbacks.

### QRInvoiceData Dataclass

```python
@dataclass
class QRInvoiceData:
    irn: str              # 64-char IRN (sha256 hex)
    irn_date: str         # "YYYY-MM-DD HH:MM:SS"
    seller_gstin: str     # Supplier's 15-char GSTIN (uppercased)
    buyer_gstin: str      # Buyer's 15-char GSTIN (empty for B2C)
    doc_number: str       # Invoice number as registered on IRP
    doc_date: str         # "DD/MM/YYYY" or "YYYY-MM-DD"
    total_value: Decimal | None
    item_count: int
    main_hsn: str         # HSN/SAC of primary line
    doc_type: str         # "INV" | "CRN" | "DBN"
    decode_strategy: str  # "azure_barcode" | "ocr_text" | "ocr_irn_text" | "pyzbar"
    signature_verified: bool  # Always False (NIC cert verification not implemented)
```

**`to_evidence_context()`** — builds the `evidence_context` dict for `FieldConfidenceService.score()`:
```python
{
    "qr_verified": {
        "invoice_number": qr.doc_number,
        "invoice_date":   qr.doc_date,
        "vendor_tax_id":  qr.seller_gstin,
        "total_amount":   str(qr.total_value),
    },
    "qr_irn":        qr.irn,
    "qr_doc_type":   qr.doc_type,
    "qr_item_count": qr.item_count,
    "qr_buyer_gstin": qr.buyer_gstin,
}
```

### How QR Data Flows Through the Pipeline

```
_ocr_document()
    ├─ features=[AnalysisFeature.BARCODES]
    └─ Returns (ocr_text, page_count, duration_ms, qr_texts)
                                               │
                                               ▼
                                    _decode_qr(file_path, ocr_text, qr_texts)
                                               │
                                               ▼ (fail-silent)
                                    QRInvoiceData or None
                                               │
                  ┌────────────────────────────┤
                  │                            │
                  ▼                            ▼
    raw_json["_qr"] =              ExtractionResponse.qr_data
    qr_data.to_serializable()
                                               │
                                ┌──────────────┤
                                │              │
                                ▼              ▼
            evidence_context =       derive_codes(
            qr_data.to_evidence_context()  qr_data=qr_data)
                                │                   │
                                ▼                   ▼
            FieldConfidenceService     QR_IRN_PRESENT
            .score(... evidence_context)  QR_DATA_VERIFIED
                                │       or QR_MISMATCH
                                ▼
              evidence_flags["invoice_number"] = "qr_confirmed"
              → score 0.99
              OR
              evidence_flags["invoice_number"] = "qr_mismatch:..."
              → score capped at 0.40
```

### Impact on Confidence and Routing

| Scenario | Field Score | Decision Code | Route |
|----------|-------------|---------------|-------|
| QR present, all checked fields match | 0.99 per field | `QR_DATA_VERIFIED`, `QR_IRN_PRESENT` | Normal approval flow |
| QR present, any field mismatches | ≤ 0.40 per mismatched field | `QR_MISMATCH`, `QR_IRN_PRESENT` | Hard review — `AP_REVIEW` queue |
| IRN seen before on another invoice | — | `IRN_DUPLICATE` | `EXCEPTION_OPS` — rejection required |
| No QR found | Unchanged | (no QR codes emitted) | Normal scoring |

`QR_MISMATCH` and `IRN_DUPLICATE` are both in `HARD_REVIEW_CODES` — they bypass auto-approval unconditionally.

### Audit Trail

The QR decode result is included in:
- `ExtractionResult.raw_response["_qr"]` — full serialised `QRInvoiceData`
- `AuditService` metadata on `EXTRACTION_COMPLETED` event: `qr_irn`, `qr_doc_type`, `qr_decode_strategy`
- `raw_response["_decision_codes"]` — QR-specific codes
- `raw_response["_field_confidence"]["evidence_flags"]` — per-field QR match/mismatch detail

### SDK Requirement

| Requirement | Detail |
|-------------|--------|
| SDK package | `azure-ai-formrecognizer >= 3.3.0` (current: 3.3.2) |
| API version | `2023-07-31` or later (barcode add-on feature added) |
| Feature flag | `features=[AnalysisFeature.BARCODES]` in `begin_analyze_document()` |
| Barcode `kind` | `"QRCode"` (PascalCase) — code uses `.upper()` for case-insensitive match |
| Optional deps | `pyzbar`, `Pillow`, PyMuPDF / pdf2image (strategy 3 only) |

### FieldConfidenceService — QR Ground-Truth Comparison Normalisation

When `FieldConfidenceService.score()` compares an extracted field value against the QR ground-truth value, it applies field-type-aware normalisation before comparing (simple separator-stripping is insufficient for production data):

| Field type | Normalisation | Example |
|-----------|---------------|---------|
| Date fields (`"date" in fname`) | `_norm_date()` — tries 6 format patterns, returns `YYYYMMDD` | `"20/09/2025"` and `"2025-09-20"` → both `"20250920"` |
| Amount fields (`"amount" or "total" in fname`) | `_norm_amount()` — strips commas, round-trips through `float(v)` | `"41958"` and `"41958.0"` → both `"41958.0"` |
| All other fields | `_sep_re.sub("", v).upper().strip()` — strip `[\s\-/]`, uppercase | `"VNR/1639/25-26"` → `"VNR163925-26"` |

Supported date formats in `_DATE_FMTS`: `%d/%m/%Y`, `%Y-%m-%d`, `%d-%m-%Y`, `%m/%d/%Y`, `%d/%m/%y`, `%Y/%m/%d`

### Limitations

- **JWT signature not verified** — The NIC digital signature (RS256) is decoded but not cryptographically verified. Fields are used as high-confidence hints, not as a security control. The NIC public certificate is at `https://einvoice1.gst.gov.in/Others/PublicKey`; verification would require the `cryptography` package.
- **`ocr_irn_text` strategy is partial** — Only IRN + GSTINs are available; `doc_number`, `doc_date`, `total_value` cannot be recovered from plain text alone. The QR panel shows a warning and prompts reprocessing with the BARCODES feature.
- **pyzbar not available in this deployment** — Strategy 4 is always skipped. Install `pip install pyzbar Pillow` to enable.
- **B2C invoices** — `BuyerGstin` is empty for B2C (end-consumer) invoices; `buyer_gstin` will be `""`.
- **Credit notes / debit notes** — `DocTyp = "CRN"` / `"DBN"` are handled; `QR_DATA_VERIFIED` is still emitted. The consuming reconciliation flow should check `qr_doc_type` for credit/debit note handling.
- **Older invoices** — Pre-e-invoice mandate invoices (before 2020-10-01 for the first tranche) will not have QR codes; `qr_data = None` is the normal outcome.
