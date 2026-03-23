# Invoice Extraction Agent — Feature Documentation

> **Modules**: `apps/extraction/` (Application Layer — UI, Task, Core Models) + `apps/extraction_core/` (Platform Layer — Configuration, Execution, Governance)  
> **Dependencies**: Azure Document Intelligence (OCR), Azure OpenAI GPT-4o (LLM), Agent Framework (`apps/agents/`)  
> **Status**: Fully implemented with human-in-the-loop approval gate + multi-country extraction platform

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
17. [Django Admin](#17-django-admin)
18. [File Reference](#18-file-reference)

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

Template views in `apps/extraction/` enrich their context with `apps/extraction_core/` models:
- Workbench loads `ExtractionRun.review_queue` for each result
- Console loads `ExtractionRun` data (review queue, schema, method badges) + `ExtractionCorrection` audit trail
- Country packs page queries `CountryPack` with jurisdiction profiles
- All cross-module lookups use graceful fallbacks via `try/except`

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

### Data Flow Diagram (Base Pipeline)

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
  │  Stage 1: Azure Document Int.    │
  │  OCR → raw text (60K+ chars)     │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 2: InvoiceExtractionAgent │
  │  GPT-4o → structured JSON       │
  │  (temp=0, json_object mode)      │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ExtractionParserService  (JSON → ParsedInvoice)
         │
         ▼
  NormalizationService  (clean, type-cast, standardize)
         │
         ▼
  ValidationService  (mandatory fields, confidence check)
         │
         ▼
  DuplicateDetectionService  (vendor + invoice# match)
         │
         ▼
  InvoicePersistenceService  (save Invoice + LineItems)
  ExtractionResultPersistenceService  (save engine metadata)
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
  AP Case created → Reconciliation pipeline
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

### Pipeline Steps

| Step | Service | Description |
|------|---------|-------------|
| 1 | `InvoiceExtractionAdapter` | OCR + LLM extraction → `ExtractionResponse` |
| 2 | `ExtractionParserService` | Parse raw JSON → `ParsedInvoice` dataclass |
| 3 | `NormalizationService` | Normalize fields (dates, amounts, PO numbers) |
| 4 | `ValidationService` | Check mandatory fields, generate warnings |
| 5 | `DuplicateDetectionService` | Detect re-submitted invoices |
| 6 | `InvoicePersistenceService` + `ExtractionResultPersistenceService` | Persist to database |
| 7 | Approval Gate | Auto-approve or queue for human review |

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

Stores per-extraction-run metadata for audit and reprocessing.

| Field | Type | Description |
|-------|------|-------------|
| `document_upload` | FK → DocumentUpload | Source file |
| `invoice` | FK → Invoice (nullable) | Linked invoice after persistence |
| `engine_name` | CharField | Engine identifier (default: `"default"`) |
| `engine_version` | CharField | Engine version string |
| `raw_response` | JSONField (nullable) | Full JSON response from LLM |
| `confidence` | FloatField (nullable) | 0.0–1.0 extraction confidence |
| `duration_ms` | PositiveIntegerField (nullable) | Extraction duration in milliseconds |
| `success` | BooleanField | Whether extraction succeeded |
| `error_message` | TextField | Error details if failed |

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

- **Raw fields**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`
- **Normalized fields**: `invoice_number`, `normalized_invoice_number`, `invoice_date`, `po_number`, `normalized_po_number`, `currency`, `subtotal`, `tax_amount`, `total_amount`
- **Extraction metadata**: `extraction_confidence` (float 0.0–1.0), `extraction_remarks`, `extraction_raw_json`
- **Status**: `status` (InvoiceStatus enum)

**InvoiceLineItem** (`documents_invoice_line`) — line items:

- **Raw fields**: `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`
- **Normalized fields**: `description`, `normalized_description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`
- **Classification**: `item_category`, `is_service_item`, `is_stock_item`

**DocumentUpload** (`documents_upload`) — file metadata:

- `original_filename`, `file_size`, `file_hash` (SHA-256), `content_type`
- `processing_state` (FileProcessingState enum), `processing_message`
- Azure Blob fields: `blob_path`, `blob_container`, `blob_name`

---

## 5. Services

### 5.0 Observability

All extraction services are decorated with `@observed_service` from `apps/core/decorators.py`. This creates a child trace span, measures duration, writes a `ProcessingLog` entry, and optionally emits an `AuditEvent` for each service method invocation.

### 5.1 InvoiceExtractionAdapter

**File**: `apps/extraction/services/extraction_adapter.py`  
**Decorator**: `@observed_service("extraction.extract", entity_type="DocumentUpload", audit_event="EXTRACTION_STARTED")`

Orchestrates the two-stage extraction pipeline:

**Stage 1 — OCR**:
```python
ocr_text = _ocr_document(file_path)
```
- Uses `DocumentAnalysisClient` from `azure.ai.formrecognizer`
- Concatenates all pages' text lines
- Credentials: `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`

**Stage 2 — LLM Extraction**:
```python
raw_json, agent_run_id = _agent_extract(ocr_text)
```
- Instantiates `InvoiceExtractionAgent()`
- Returns JSON + `AgentRun.pk` for traceability

**Returns**: `ExtractionResponse` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether extraction succeeded |
| `raw_json` | dict | Extracted JSON data |
| `confidence` | float | 0.0–1.0 confidence |
| `engine_name` | str | `"azure_di_gpt4o_agent"` |
| `engine_version` | str | `"2.0"` |
| `duration_ms` | int | Extraction duration |
| `error_message` | str | Error details if failed |
| `ocr_text` | str | Raw OCR text |

**Fallback**: Direct LLM extraction without agent framework via `_llm_extract(ocr_text)` — uses `response_format={"type": "json_object"}`, temperature=0.0, max_tokens=4096.

### 5.2 ExtractionParserService

**File**: `apps/extraction/services/parser_service.py`  
**Decorator**: `@observed_service("extraction.parse", entity_type="ExtractionResult")`

Parses raw JSON → structured dataclasses:

- **ParsedInvoice**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`, `confidence`, `line_items`
- **ParsedLineItem**: `line_number`, `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`

Flexible field mapping (e.g., accepts both `item_description` and `description`).

### 5.3 NormalizationService

**File**: `apps/extraction/services/normalization_service.py`  
**Decorator**: `@observed_service("extraction.normalize", entity_type="Invoice")`

Normalizes parsed values to proper types:

| Operation | Detail |
|-----------|--------|
| Vendor name | `normalize_string()` — lowercase, strip, remove diacritics |
| Invoice number | `normalize_invoice_number()` — strip spaces/dashes/special chars |
| PO number | `normalize_po_number()` — same normalization |
| Date | `parse_date()` — flexible parsing (DD/MM/YYYY, YYYY-MM-DD, etc.) |
| Currency | `parse_currency()` — fallback to `"USD"` |
| Amounts | `to_decimal()` — parse currency strings to `Decimal` |
| Line items | Same normalization per line |

Utility functions live in `apps/core/utils.py`.

### 5.4 ValidationService

**File**: `apps/extraction/services/validation_service.py`  
**Decorator**: `@observed_service("extraction.validate", entity_type="Invoice")`

Returns `ValidationResult` with `is_valid`, `errors`, and `warnings`.

**Errors** (blocking — marks invoice as INVALID):
- `normalized_invoice_number` missing
- `vendor_name_normalized` missing
- `total_amount` missing or non-numeric

**Warnings** (non-blocking):
- `normalized_po_number` missing (will require agent lookup)
- `invoice_date` unparseable
- `subtotal` missing
- No line items extracted
- Low extraction confidence (< `EXTRACTION_CONFIDENCE_THRESHOLD` = 0.75)
- Line item missing quantity / unit_price / description

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
- Recalculates subtotal/total from line items if they differ from extracted header
- Resolves vendor via `Vendor.normalized_name` or `VendorAlias.normalized_alias`

### 5.7 ExtractionResultPersistenceService

**Decorator**: `@observed_service("extraction.persist_result", entity_type="ExtractionResult", audit_event="EXTRACTION_RESULT_PERSISTED")`

Persists `ExtractionResult` record with engine metadata (separate from Invoice data).

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

### ExtractionService (Legacy Pipeline Orchestrator)

**File**: `apps/extraction_core/services/extraction_service.py`  
**Class**: `ExtractionService`

The original pipeline orchestrator. Coordinates jurisdiction → schema → deterministic extraction → LLM fallback → normalization → validation → enrichment → confidence → routing → persistence.

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

**ExtractionResult** (dataclass in `extraction_service.py`):
- `fields`, `line_items`, `jurisdiction` (JurisdictionMeta), `document_intelligence` (DocumentIntelligenceResult)
- `enrichment` (EnrichmentResult), `page_info` (ParsedDocument), `confidence_breakdown` (ConfidenceBreakdown)
- `review_decision` (ReviewRoutingDecision), `validation_issues`, `warnings`, `overall_confidence`, `duration_ms`

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
| `DocumentTypeClassifier` | `document_classifier.py` | Multilingual keyword classification (EN/AR/HI/FR/DE/ES) |
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
| `ConfidenceScorer` | `confidence_scorer.py` | Multi-dimensional scoring (header=0.3, tax=0.3, line_item=0.2, jurisdiction=0.2) |
| `ReviewRoutingService` | `review_routing.py` | Confidence-driven review routing with priority tiers |
| `ReviewRoutingEngine` | `review_routing_engine.py` | Queue-based routing (EXCEPTION_OPS, TAX_REVIEW, VENDOR_OPS); thresholds: CRITICAL=0.4, LOW=0.65, TAX=0.6 |

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
  ▼    ┌────┴────┐
AP Case  APPROVE    REJECT
created  │         │
         ▼         ▼
   READY_FOR_RECON  INVALID
   (case created)   (re-extract)
```

### Service Methods

**`create_pending_approval(invoice, extraction_result)`**
- Creates `ExtractionApproval` with `status=PENDING`
- Snapshots current header + line values as `original_values_snapshot`
- Called when auto-approval is not triggered

**`try_auto_approve(invoice, extraction_result)`**
- Checks `EXTRACTION_AUTO_APPROVE_ENABLED` setting (default: `false`)
- If enabled and confidence ≥ `EXTRACTION_AUTO_APPROVE_THRESHOLD` (default: `1.1` — effectively disabled):
  - Creates `ExtractionApproval(status=AUTO_APPROVED, is_touchless=True)`
  - Sets `invoice.status = READY_FOR_RECON`
  - Returns the approval object
- Otherwise returns `None`

**`approve(approval, user, corrections=None)`**
- Applies field corrections to Invoice + LineItems
- Creates `ExtractionFieldCorrection` records for each changed field
- Sets `is_touchless = (len(corrections) == 0)`
- Transitions invoice to `READY_FOR_RECON`
- Logs `EXTRACTION_APPROVED` audit event

**`reject(approval, user, reason)`**
- Sets `status = REJECTED` with `rejection_reason`
- Transitions invoice to `INVALID`
- Logs `EXTRACTION_REJECTED` audit event

**`get_approval_analytics()`**
- Returns analytics dict: `total`, `pending`, `approved`, `auto_approved`, `rejected`, `touchless_count`, `human_corrected_count`, `touchless_rate`, `avg_corrections_per_review`, `most_corrected_fields` (top 10)

### Correctable Fields

| Type | Fields |
|------|--------|
| Header | `invoice_number`, `po_number`, `invoice_date`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `raw_vendor_name` |
| Line Item | `description`, `quantity`, `unit_price`, `tax_amount`, `line_amount` |

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
2. Builds system prompt + user message (containing OCR text)
3. Calls LLM with `response_format=json_object`
4. Saves assistant message to `AgentMessage`
5. Parses JSON → `AgentOutput` (with confidence, evidence, reasoning)
6. Finalizes `AgentRun` with output payload + token usage

**Traceability**:
- `AgentRun` — execution metadata, LLM model, token usage, duration
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

**When invoked**: Part of the two-agent architecture — `InvoiceExtractionAgent` always runs; `InvoiceUnderstandingAgent` runs additionally for low-confidence extractions or when validation warnings are present.

---

## 10. LLM Prompt

**Registry key**: `extraction.invoice_system`  
**File**: `apps/core/prompt_registry.py` (hardcoded default) + DB-overridable via `PromptTemplate` model

```
You are an expert invoice data extraction system. You will receive OCR text
from an invoice document. Extract ALL relevant fields and return a JSON object
with EXACTLY this structure:

{
  "confidence": <float 0.0-1.0>,
  "vendor_name": "<vendor/supplier company name>",
  "invoice_number": "<invoice number/ID>",
  "invoice_date": "<invoice date in YYYY-MM-DD format>",
  "po_number": "<purchase order number>",
  "currency": "<3-letter ISO currency code, e.g. USD, EUR, INR>",
  "subtotal": "<subtotal amount before tax>",
  "tax_amount": "<total tax amount>",
  "total_amount": "<grand total amount>",
  "line_items": [
    {
      "item_description": "<description>",
      "quantity": "<quantity>",
      "unit_price": "<unit price>",
      "tax_amount": "<tax for this line or 0>",
      "line_amount": "<total for this line>"
    }
  ]
}
```

**Key rules**:
- Extract EVERY line item visible in the invoice
- Preserve values exactly as shown for display fields
- Keep currency symbols with amounts (e.g., $, €, ₹)
- Missing fields → empty string (text) or 0 (numeric)
- Parse dates to YYYY-MM-DD
- Extract PO number from anywhere (header, footer, references)
- Return ONLY valid JSON — no markdown or explanation
- **vendor_name MUST be English-only** — if Arabic/Urdu/non-English script detected, translate/transliterate to official English company name

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
| `/extraction/result/<id>/edit/` | `extraction_edit_values` | POST | `invoices.create` | Edit extracted values |
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

**`extraction_workbench`** — Main extraction agent page with two tabs:
- **Agent Runs tab**: KPI stats (total, success, failed, avg confidence, avg duration); advanced filters (search, status, confidence range, date range, review queue); paginated results table (20 per page) with review queue column; "Run Agent" file upload modal (PDF, PNG, JPG, TIFF — max 20 MB)
- **Approvals tab**: Approval queue with filter/search + analytics strip

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
| `workbench.html` | Main workbench with Agent Runs tab (KPIs, filters, results with review queue column) + Approvals tab |
| `result_detail.html` | Single extraction result detail |
| `approval_detail.html` | Approval review page (approve/reject modals) |
| `approval_queue.html` | Deprecated — redirects to workbench |
| `country_packs.html` | Country pack governance (KPI strip + governance table with status badges) |

### workbench.html
- Two-tab layout: **Agent Runs** and **Approvals**
- Agent Runs: KPI stat cards (total, success, failed, avg confidence, avg duration); advanced filter panel (search, status, confidence presets/slider, date range, review queue dropdown); results table with review queue column; "Run Agent" modal for file upload (drag-and-drop, file validation)
- Approvals: Approval queue with filter/search + analytics strip

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
│    - Header Fields table                                     │
│    - Parties card (exc-supplementary-card)                   │
│    - Tax & Jurisdiction card                                 │
│    - Master Data Matches card (exc-supplementary-card)       │
│    - Line Items table (expandable)                           │
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

### Template Files (15 files in `templates/extraction/console/`)

| File | Purpose |
|------|---------|
| `console.html` | Main layout — extends `base.html`, includes all partials/modals, loads CSS/JS. 6 tab pills. |
| `_header_bar.html` | Command bar — extraction ID, status/confidence badges, jurisdiction badges, review queue badge (bg-info-subtle), schema badge (bg-dark-subtle), extraction method badge (conditional: HYBRID=purple, LLM=primary, else=secondary), action buttons |
| `_document_viewer.html` | **Deprecated** — no longer included in layout. File exists but is unused. |
| `_extracted_data.html` | Tab 1 — Header Fields, Parties, Tax/Jurisdiction, Master Data Matches, Line Items |
| `_confidence_badge.html` | Reusable confidence % indicator (green ≥85%, amber ≥50%, red <50%) |
| `_validation_panel.html` | Tab 2 — Errors/Warnings/Passed grouped by severity, "Go to field" navigation |
| `_evidence_panel.html` | Tab 3 — Evidence cards with source snippets and page references |
| `_reasoning_panel.html` | Tab 4 — Agent reasoning timeline with step indicators, decisions, collapsible details |
| `_audit_trail.html` | Tab 5 — Chronological event timeline with actor/role badges, before/after tracking |
| `_corrections_panel.html` | Tab 6 — Field correction audit trail table (columns: Field Code, Original Value (strikethrough), Corrected Value (green), Reason, Corrected By, Date). Empty state with guidance text. |
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
| `header_fields` | Invoice model | Dict of field dicts (display_name, value, raw_value, confidence, method, is_mandatory, evidence) |
| `tax_fields` | Invoice model | Tax-specific field dicts |
| `parties` | `raw_response.document_intelligence.parties` | Supplier/buyer/ship-to/bill-to from document intelligence |
| `enrichment` | `raw_response.enrichment` | Vendor/customer/PO matches from master data enrichment |
| `line_items` | `InvoiceLineItem` queryset | List of dicts with description, qty, price, tax, total, confidence, fields |
| `errors` / `warnings` | Re-run `ValidationService` | Grouped validation issues |
| `validation_field_issues` | Computed | Map of field names with validation issues |
| `pipeline_stages` | Computed | 10-stage pipeline with state indicators |
| `approval` | `ExtractionApproval` | Current approval record (if exists) |
| `corrections` | `ExtractionCorrection` queryset | Field correction audit trail from `ExtractionRun` (select_related corrected_by) |
| `correction_count` | int | Count of corrections for badge display |
| `permissions` | Request user | `can_approve` (`extraction.approve`), `can_reprocess` (`extraction.reprocess`), `can_escalate` (`cases.escalate`) — checked via `user.has_permission()` |
| `assignable_users` | `User.objects` | Top 50 active users for escalation |

**ExtractionRun enrichment**: The view queries `ExtractionRun` by `document__document_upload_id` to populate `review_queue`, `schema_code`, `schema_version`, `extraction_method`, and `requires_review` in the `extraction` context dict. These appear as badges in the header bar.

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

| Event Type | When Logged |
|------------|-------------|
| `JURISDICTION_RESOLVED` | Jurisdiction resolved (tier + country + regime) |
| `SCHEMA_SELECTED` | Schema selected for extraction |
| `PROMPT_SELECTED` | Prompt template selected |
| `NORMALIZATION_COMPLETED` | Country-specific normalization complete |
| `VALIDATION_COMPLETED` | Country-specific validation complete |
| `EVIDENCE_CAPTURED` | Field evidence captured |
| `REVIEW_ROUTE_ASSIGNED` | Review queue assigned |
| `EXTRACTION_REPROCESSED` | Extraction re-run triggered |
| `EXTRACTION_ESCALATED` | Extraction escalated to review queue |
| `EXTRACTION_COMMENT_ADDED` | Comment added to extraction |
| `SETTINGS_UPDATED` | Runtime settings or schema updated |
| `SCHEMA_UPDATED` | Schema definition modified |
| `PROMPT_UPDATED` | Prompt template modified |
| `ROUTING_RULE_UPDATED` | Routing rule modified |
| `ANALYTICS_SNAPSHOT_CREATED` | Analytics snapshot generated |

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

### Settings File

All settings are in `config/settings.py`. Values are loaded from environment variables or `.env` file.

---

## 16. Permissions & RBAC

### Permission Codes

| Permission | Description |
|------------|-------------|
| `invoices.view` | View extraction results, approval queue, analytics |
| `invoices.create` | Upload files, edit extracted values |
| `extraction.view` | View extraction platform data (country packs, schemas, settings) |
| `extraction.approve` | Approve extracted invoice data before reconciliation |
| `extraction.reject` | Reject extracted data and request re-extraction |
| `extraction.reprocess` | Re-run extraction on existing uploads |
| `extraction.correct` | Correct field values on extraction runs (API) |
| `extraction.escalate` | Escalate extraction to review queue (API) |
| `cases.escalate` | Escalate extraction for case-level review (console UI) |

### Role Access

| Role | Permissions |
|------|-------------|
| ADMIN | All extraction permissions |
| AP_PROCESSOR | `invoices.view`, `invoices.create`, `extraction.approve`, `extraction.reject`, `extraction.reprocess` (scoped to own uploads) |
| REVIEWER | `invoices.view` |
| FINANCE_MANAGER | `invoices.view`, `invoices.create`, `extraction.approve`, `extraction.reject`, `extraction.reprocess` |
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
- **Extraction Approvals** — links to the approval queue (`/extraction/approvals/`), gated by `{% has_permission "invoices.view" %}`
- **Country Packs** — links to country pack governance (`/extraction/country-packs/`), gated by `{% has_permission "extraction.view" %}`, uses `bi-globe2` icon

---

## 17. Django Admin

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

---

## 18. File Reference

### apps/extraction (Application Layer — UI, Task, Core Models)

| File | Purpose |
|------|---------|
| `apps/extraction/models.py` | ExtractionResult, ExtractionApproval, ExtractionFieldCorrection models |
| `apps/extraction/tasks.py` | Main extraction pipeline Celery task |
| `apps/extraction/admin.py` | Django admin registrations (3 models) |
| `apps/extraction/template_views.py` | All 15 template views (workbench, upload, approval queue, console, country packs) |
| `apps/extraction/urls.py` | URL routing (15 routes) |
| `apps/extraction/api_urls.py` | API URL routing (empty) |
| `apps/extraction/services/extraction_adapter.py` | Azure DI OCR + LLM extraction orchestration |
| `apps/extraction/services/parser_service.py` | JSON → ParsedInvoice dataclass parsing |
| `apps/extraction/services/normalization_service.py` | Field normalization (dates, amounts, strings) |
| `apps/extraction/services/validation_service.py` | Mandatory field validation + confidence check |
| `apps/extraction/services/duplicate_detection_service.py` | Duplicate invoice detection |
| `apps/extraction/services/persistence_service.py` | Invoice + LineItem + ExtractionResult persistence |
| `apps/extraction/services/approval_service.py` | Approval lifecycle (approve/reject/auto-approve + analytics) |
| `apps/extraction/services/upload_service.py` | File upload, hash computation, DocumentUpload creation |

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
| `apps/extraction_core/services/extraction_service.py` | Original pipeline orchestrator |
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
| `apps/extraction_core/services/extraction_audit.py` | Extraction-specific audit logging (8 pipeline event types) |
| **Confidence & Review Routing** | |
| `apps/extraction_core/services/confidence_scorer.py` | Multi-dimensional confidence scoring |
| `apps/extraction_core/services/review_routing.py` | Confidence-driven review routing |
| `apps/extraction_core/services/review_routing_engine.py` | Queue-based routing (EXCEPTION_OPS, TAX_REVIEW, VENDOR_OPS) |
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
| `config/settings.py` | Azure credentials, thresholds, auto-approve config |

### Templates

| File | Purpose |
|------|---------|
| `templates/extraction/workbench.html` | Extraction workbench UI (Agent Runs + Approvals tabs) |
| `templates/extraction/result_detail.html` | Extraction result detail UI |
| `templates/extraction/approval_detail.html` | Approval review UI |
| `templates/extraction/approval_queue.html` | Deprecated — redirects to workbench |
| `templates/extraction/country_packs.html` | Country pack governance (KPI strip + table) |
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
| `templates/extraction/console/_comment_modal.html` | Add comment modal |

### Static Assets

| File | Purpose |
|------|---------|
| `static/css/extraction_console.css` | Review console custom styles (~200 lines) |
| `static/js/extraction_console.js` | Review console JavaScript (~200 lines) |
| `templates/partials/sidebar.html` | Navigation sidebar (extraction + country packs links) |

---

## Debugging Tips

- **LLM calls failing?** Check `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` env vars.
- **OCR failing?** Check `AZURE_DI_ENDPOINT` and `AZURE_DI_KEY` env vars.
- **Extraction task not running?** On Windows without Redis, ensure `CELERY_TASK_ALWAYS_EAGER=True` (tasks run synchronously).
- **Confidence showing 1%?** `extraction_confidence` is stored as 0.0–1.0 float; templates use `{% widthratio %}` to display as percentage.
- **Auto-approve not working?** Check both `EXTRACTION_AUTO_APPROVE_ENABLED=true` AND `EXTRACTION_AUTO_APPROVE_THRESHOLD` < 1.0 (e.g., 0.95).
- **Agent 400 errors from OpenAI?** Ensure tool-calling messages follow OpenAI format: assistant messages include `tool_calls` array, tool responses include `tool_call_id`.
- **Approval queue empty?** Invoices only appear when `status=PENDING_APPROVAL` — check that the extraction pipeline completed successfully and auto-approve didn't trigger.
