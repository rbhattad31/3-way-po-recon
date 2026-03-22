# Invoice Extraction Agent — Feature Documentation

> **Module**: `apps/extraction/`  
> **Dependencies**: Azure Document Intelligence (OCR), Azure OpenAI GPT-4o (LLM), Agent Framework (`apps/agents/`)  
> **Status**: Fully implemented with human-in-the-loop approval gate

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Extraction Pipeline](#3-extraction-pipeline)
4. [Data Models](#4-data-models)
5. [Services](#5-services)
6. [Extraction Core — Extended Pipeline](#6-extraction-core--extended-pipeline)
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

The Invoice Extraction Agent converts uploaded invoice documents (PDF, PNG, JPG, TIFF) into structured, normalized data. It uses a two-stage pipeline:

1. **Azure Document Intelligence** — OCR to extract raw text from the document.
2. **Azure OpenAI GPT-4o** — LLM-based structured extraction from OCR text into a typed JSON schema.

After extraction, the data passes through parsing, normalization, validation, and duplicate detection before being persisted. A **human approval gate** ensures every extraction is reviewed (or auto-approved at high confidence) before the invoice enters reconciliation.

---

## 2. Architecture

### Data Flow Diagram

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

### 5.1 InvoiceExtractionAdapter

**File**: `apps/extraction/services/extraction_adapter.py`

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

Parses raw JSON → structured dataclasses:

- **ParsedInvoice**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`, `confidence`, `line_items`
- **ParsedLineItem**: `line_number`, `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`

Flexible field mapping (e.g., accepts both `item_description` and `description`).

### 5.3 NormalizationService

**File**: `apps/extraction/services/normalization_service.py`

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

Returns `DuplicateCheckResult` with `is_duplicate`, `duplicate_invoice_id`, `reason`.

**Detection checks** (in order):
1. **Exact match**: `normalized_invoice_number` + vendor's `normalized_name`
2. **Amount match**: `normalized_invoice_number` + `total_amount`
3. Excludes invoices already marked as duplicates

### 5.6 InvoicePersistenceService

**File**: `apps/extraction/services/persistence_service.py`

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

Persists `ExtractionResult` record with engine metadata (separate from Invoice data).

### 5.8 ExtractionApprovalService

**File**: `apps/extraction/services/approval_service.py`

See [Section 6: Approval Gate](#6-approval-gate).

---

## 6. Extraction Core — Extended Pipeline

The `apps/extraction_core/` app provides an advanced, jurisdiction-aware extraction pipeline with 19 service classes. This extends the base extraction pipeline with document intelligence, multi-page support, schema-driven extraction, confidence scoring, master data enrichment, and review routing.

### Pipeline Steps (ExtractionService.extract())

| Step | Service | Description |
|------|---------|-------------|
| 1 | `JurisdictionResolverService` | Multi-signal jurisdiction detection (GSTIN, TRN, VAT, currency, keywords) |
| 2 | Meta extraction | Build `JurisdictionMeta` from resolution |
| 2b | `DocumentIntelligenceService` | Pre-extraction analysis: document classification, relationship extraction, party extraction |
| 3 | `SchemaRegistryService` | Jurisdiction-aware schema selection |
| 4 | `PromptBuilderService` | Build extraction template from schema |
| 4b | `PageParser` | Multi-page OCR text segmentation, header/footer dedup |
| 5 | Deterministic extraction | Rule-based field extraction from OCR text |
| — | Page evidence | Map extracted fields to source pages |
| 5a | `TableStitcher` + `LineItemExtractor` | Cross-page table reconstruction + schema-driven line item extraction |
| 5b | `LLMExtractionAdapter` | LLM-based extraction for remaining/low-confidence fields |
| 6 | `NormalizationService` | Jurisdiction-driven field normalization (dates, amounts, tax IDs) |
| 7 | `ValidationService` | Jurisdiction-driven validation (mandatory fields, data types, tax rates, amount consistency) |
| **7b** | **`MasterDataEnrichmentService`** | **Post-extraction vendor matching, PO lookup, confidence adjustments** |
| 8 | `ConfidenceScorer` | Multi-dimensional confidence scoring (header/tax/line/jurisdiction) |
| 8b | Metrics | Track extraction performance metrics |
| 9 | Mandatory check | Final mandatory field validation |
| 9b | `ReviewRoutingService` | Confidence-driven review routing decision |
| 10 | Persist | Save `ExtractionDocument` + field results to database |

### Key Dataclasses

**ExtractionResult** (extraction_core):
- `fields` — dict of `FieldResult` per field key
- `line_items` — list of line item dicts
- `jurisdiction` — `JurisdictionMeta` (country_code, regime_code, confidence, source, warning)
- `document_intelligence` — `DocumentIntelligenceResult` (document type, parties, relationships)
- `enrichment` — `EnrichmentResult` (vendor/customer/PO matches, confidence adjustments)
- `page_info` — `ParsedDocument` (page segments, table regions)
- `confidence_breakdown` — `ConfidenceBreakdown` (per-category scores)
- `review_decision` — `ReviewRoutingDecision`
- `validation_issues`, `warnings`, `overall_confidence`, `duration_ms`

### Service Directory (19 services)

| Service | File | Purpose |
|---------|------|---------|
| `ExtractionService` | `extraction_service.py` | Primary pipeline orchestrator |
| `BaseExtractionService` | `base_extraction_service.py` | Schema-driven extraction base class |
| `JurisdictionResolverService` | `jurisdiction_resolver.py` | Multi-signal jurisdiction detection |
| `JurisdictionResolutionService` | `resolution_service.py` | 4-tier jurisdiction resolution cascade |
| `SchemaRegistryService` | `schema_registry.py` | Cached, version-aware schema lookup |
| `PromptBuilderService` | `prompt_builder.py` | Dynamic LLM prompt construction |
| `LLMExtractionAdapter` | `llm_extraction_adapter.py` | LLM client wrapper for schema extraction |
| `NormalizationService` | `normalization_service.py` | Jurisdiction-driven field normalization |
| `ValidationService` | `validation_service.py` | Jurisdiction-driven field validation |
| `PageParser` | `page_parser.py` | Multi-page OCR text segmentation |
| `TableStitcher` | `table_stitcher.py` | Cross-page table continuation |
| `LineItemExtractor` | `line_item_extractor.py` | Schema-driven line item extraction |
| `DocumentTypeClassifier` | `document_classifier.py` | Weighted keyword document classification |
| `RelationshipExtractor` | `relationship_extractor.py` | PO/GRN/contract cross-reference extraction |
| `PartyExtractor` | `party_extractor.py` | Supplier/buyer/ship-to/bill-to extraction |
| `DocumentIntelligenceService` | `document_intelligence.py` | Pre-extraction analysis orchestrator |
| `ConfidenceScorer` | `confidence_scorer.py` | Multi-dimensional confidence scoring |
| `ReviewRoutingService` | `review_routing.py` | Confidence-driven review routing |
| `MasterDataEnrichmentService` | `master_data_enrichment.py` | Post-extraction vendor/PO matching |

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
| `/extraction/` | `extraction_workbench` | GET | `invoices.view` | Main workbench with KPIs |
| `/extraction/upload/` | `extraction_upload` | POST | `invoices.create` | Upload + extract |
| `/extraction/filter/` | `extraction_ajax_filter` | GET | `invoices.view` | AJAX filter results |
| `/extraction/export/` | `extraction_export_csv` | GET | `invoices.view` | CSV export |
| `/extraction/result/<id>/` | `extraction_result_detail` | GET | `invoices.view` | Result detail view |
| `/extraction/result/<id>/json/` | `extraction_result_json` | GET | `invoices.view` | Download raw JSON |
| `/extraction/result/<id>/rerun/` | `extraction_rerun` | POST | `extraction.reprocess` | Re-run extraction |
| `/extraction/result/<id>/edit/` | `extraction_edit_values` | POST | `invoices.create` | Edit extracted values |
| `/extraction/approvals/` | `extraction_approval_queue` | GET | `invoices.view` | Approval queue |
| `/extraction/approvals/<id>/` | `extraction_approval_detail` | GET | `invoices.view` | Approval detail/review |
| `/extraction/approvals/<id>/approve/` | `extraction_approve` | POST | `invoices.create` | Approve extraction |
| `/extraction/approvals/<id>/reject/` | `extraction_reject` | POST | `invoices.create` | Reject extraction |
| `/extraction/console/<id>/` | `extraction_console` | GET | `invoices.view` | Agentic review console |
| `/extraction/approvals/analytics/` | `extraction_approval_analytics` | GET | `invoices.view` | Analytics JSON endpoint |

**API URLs**: `apps/extraction/api_urls.py` — empty (no REST API endpoints yet).

### View Details

**`extraction_workbench`** — Main extraction agent page with:
- KPI stats (total runs, success count, failure count, avg confidence, avg duration)
- Advanced filters (search, status, confidence range, date range)
- Paginated results table (20 per page)
- "Run Agent" file upload modal (PDF, PNG, JPG, TIFF — max 20 MB)

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

**`extraction_approval_queue`** — Approval queue:
- Filter by status (PENDING, APPROVED, AUTO_APPROVED, REJECTED, ALL)
- Search by invoice number or vendor name
- Analytics strip (pending, approved, auto, rejected, touchless rate)
- Most corrected fields table

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
- Permission context (can_approve, can_reprocess, can_escalate)
- Assignable users for escalation
- See [Section 13: Extraction Review Console](#13-extraction-review-console) for full template/layout details.

---

## 12. Templates (UI)

All templates are in `templates/extraction/` and extend `base.html` (Bootstrap 5).

### workbench.html
- KPI stat cards (total, success, failed, avg confidence, avg duration)
- Advanced filter panel (search, status dropdown, confidence presets/slider, date range)
- Results table with AJAX pagination and filtering
- "Run Agent" modal for file upload (drag-and-drop, file validation)

### result_detail.html
- Engine metadata panel (name, version, duration, file info)
- Error message display (if extraction failed)
- Raw vs Normalized comparison table
- Invoice header + line items display
- Validation issues list
- Action buttons: Edit Values, Download JSON, Re-extract, View Full Invoice

### approval_queue.html
- Analytics KPI strip (Pending, Approved, Auto-Approved, Rejected, Touchless Rate)
- Filter controls: status dropdown + search input
- Results table: invoice #, vendor, amount, confidence bar, status badge, corrections count, reviewer, date
- Most Corrected Fields analytics table (field name, correction count)
- Pagination

### approval_detail.html
- Confidence card with percentage + status badge
- Invoice metadata card (vendor, amount, PO, date)
- Validation issues alert banner
- Editable header fields form (text inputs for each correctable field)
- Editable line items table (inline editing)
- Previous corrections table (showing original → corrected values)
- Reject modal with reason textarea
- JavaScript handlers for Approve (AJAX POST) and Reject (modal + AJAX POST)

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
│               action buttons (Approve, Edit, Reprocess,      │
│               Escalate, Comment)                             │
├──────────────────────┬───────────────────────────────────────┤
│  DOCUMENT VIEWER     │  INTELLIGENCE PANEL (5 tabs)          │
│  (col-lg-5)          │  (col-lg-7)                           │
│                      │                                       │
│  • Page navigation   │  Tab 1: Extracted Data                │
│  • Zoom controls     │    - Header Fields table              │
│  • Highlight overlay │    - Parties card                     │
│  • PDF.js canvas     │    - Tax & Jurisdiction card          │
│  • Image fallback    │    - Master Data Matches card         │
│                      │    - Line Items table (expandable)    │
│                      │                                       │
│                      │  Tab 2: Validation                    │
│                      │    - Errors / Warnings / Passed       │
│                      │    - Go-to-field navigation           │
│                      │                                       │
│                      │  Tab 3: Evidence                      │
│                      │    - Field evidence cards             │
│                      │    - Source snippets, page refs       │
│                      │    - Highlight in document            │
│                      │                                       │
│                      │  Tab 4: Agent Reasoning               │
│                      │    - Step-by-step reasoning timeline  │
│                      │    - Decisions, collapsible details   │
│                      │                                       │
│                      │  Tab 5: Audit Trail                   │
│                      │    - Chronological event timeline     │
│                      │    - Actor/role badges                │
│                      │    - Before/after change tracking     │
├──────────────────────┴───────────────────────────────────────┤
│  PIPELINE TIMELINE — Upload → OCR → Jurisdiction → Schema →  │
│  Extraction → Normalize → Validate → Enrich → Confidence →   │
│  Review (state-aware pills)                                  │
└──────────────────────────────────────────────────────────────┘
```

### Template Files (14 files in `templates/extraction/console/`)

| File | Purpose |
|------|---------|
| `console.html` | Main layout — extends `base.html`, includes all partials/modals, loads CSS/JS |
| `_header_bar.html` | Command bar — extraction ID, status/confidence badges, jurisdiction badges, action buttons |
| `_document_viewer.html` | Sticky document viewer — page nav, zoom, highlight overlay, PDF.js canvas |
| `_extracted_data.html` | Tab 1 — Header Fields, Parties, Tax/Jurisdiction, Master Data Matches, Line Items |
| `_confidence_badge.html` | Reusable confidence % indicator (green ≥85%, amber ≥50%, red <50%) |
| `_validation_panel.html` | Tab 2 — Errors/Warnings/Passed grouped by severity, "Go to field" navigation |
| `_evidence_panel.html` | Tab 3 — Evidence cards with source snippets, page references, "Highlight in document" |
| `_reasoning_panel.html` | Tab 4 — Agent reasoning timeline with step indicators, decisions, collapsible details |
| `_audit_trail.html` | Tab 5 — Chronological event timeline with actor/role badges, before/after tracking |
| `_bottom_timeline.html` | Pipeline stage progress bar with state indicators (completed/active/error/skipped/pending) |
| `_approve_modal.html` | Approval modal — warnings summary, notes, review confirmation checkbox |
| `_reprocess_modal.html` | Reprocess modal — reason select, override options (force LLM, override jurisdiction) |
| `_escalate_modal.html` | Escalation modal — severity, assignee select, flagged fields list |
| `_comment_modal.html` | Comment modal — text, related fields, internal toggle |

### Key Features

**Field Filtering**: Toggle buttons for All Fields / Flagged Only / Low Confidence to focus review on problem areas.

**Edit Mode**: Toggle switch enables inline editing on all header and tax fields. Modified fields get visual highlighting (`exc-modified` class). Original values preserved in `data-original` for comparison.

**Go-to-Field Navigation**: Validation issues and evidence cards have clickable field links that switch to the Extracted Data tab and scroll/highlight the target field row.

**Evidence → Document Linking**: Evidence entries with page numbers can navigate the document viewer and highlight bounding box regions.

**Line Item Expand/Flag**: Each line item row has expand (shows all field details) and flag (marks for review) actions.

**Modal Workflows**: All approval actions go through Bootstrap modals with CSRF-protected AJAX POST requests. Toast notifications for success/error feedback.

### Static Assets

**`static/css/extraction_console.css`** (~300 lines):
- `.exc-conf-high/med/low` confidence badge colors
- `.exc-field-table` compact field table styling
- `.exc-field-row.exc-low-confidence` / `.exc-med-confidence` left-border indicators
- `.exc-field-row.exc-editing` edit mode show/hide
- `.exc-source-snippet` evidence source styling
- `.exc-reasoning-step-number` numbered step circles with connectors
- `.exc-audit-dot-*` timeline dot colors per event type
- `.exc-stage-*` pipeline pill state colors
- `.exc-pipeline-timeline` horizontal scrollable timeline
- Responsive breakpoints (≤991px: viewer above panel, reduced heights)

**`static/js/extraction_console.js`** (~280 lines):
- Tab persistence (sessionStorage)
- Field filter toggles (all/flagged/low-confidence)
- Edit mode toggle with modification tracking
- Go-to-field navigation (cross-tab + scroll + highlight animation)
- Evidence field filter dropdown
- Line item expand/collapse and flag toggle
- Document viewer zoom controls
- Bounding box highlight rendering
- AJAX modal submission (approve/reprocess/escalate/comment) with CSRF
- Toast notification system

### View Context

The `extraction_console` view builds the following context for the template:

| Context Variable | Source | Description |
|-----------------|--------|-------------|
| `extraction` | Computed dict | ID, file_name, status, confidence, jurisdiction metadata |
| `header_fields` | Invoice model | Dict of field dicts (display_name, value, raw_value, confidence, method, is_mandatory, evidence) |
| `tax_fields` | Invoice model | Tax-specific field dicts |
| `parties` | `raw_response.document_intelligence.parties` | Supplier/buyer/ship-to/bill-to from document intelligence |
| `enrichment` | `raw_response.enrichment` | Vendor/customer/PO matches from master data enrichment |
| `line_items` | `InvoiceLineItem` queryset | List of dicts with description, qty, price, tax, total, confidence, fields |
| `errors` / `warnings` | Re-run `ValidationService` | Grouped validation issues |
| `validation_field_issues` | Computed | Map of field names with validation issues |
| `pipeline_stages` | Computed | 10-stage pipeline with state indicators |
| `approval` | `ExtractionApproval` | Current approval record (if exists) |
| `permissions` | Request user | `can_approve`, `can_reprocess`, `can_escalate` flags |
| `assignable_users` | `User.objects` | Top 50 active users for escalation |

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
| `EXTRACTION_STARTED` | Task begins |
| `EXTRACTION_COMPLETED` | Pipeline completes successfully |
| `EXTRACTION_FAILED` | Pipeline fails |
| `EXTRACTION_APPROVAL_PENDING` | Approval record created (PENDING) |
| `EXTRACTION_APPROVED` | Human approves extraction |
| `EXTRACTION_AUTO_APPROVED` | System auto-approves extraction |
| `EXTRACTION_REJECTED` | Human rejects extraction |
| `EXTRACTION_FIELD_CORRECTED` | Field correction applied during approval |

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
| `invoices.create` | Upload files, approve/reject extractions, edit values |
| `extraction.reprocess` | Re-run extraction on existing uploads |

### Role Access

| Role | Permissions |
|------|-------------|
| ADMIN | All extraction permissions |
| AP_PROCESSOR | `invoices.view`, `invoices.create` (scoped to own uploads) |
| REVIEWER | `invoices.view` |
| FINANCE_MANAGER | `invoices.view`, `invoices.create` |
| AUDITOR | `invoices.view` |

### Sidebar Navigation

The extraction section in the sidebar (`templates/partials/sidebar.html`) includes:
- **Invoice Extraction Agent** — links to the workbench (`/extraction/`)
- **Extraction Approvals** — links to the approval queue (`/extraction/approvals/`)

Both are gated by `{% has_permission "invoices.view" %}`.

---

## 17. Django Admin

**File**: `apps/extraction/admin.py`

### ExtractionResultAdmin

| Feature | Detail |
|---------|--------|
| List display | ID, upload, invoice, engine, confidence (color-coded), success badge, duration, created_at |
| Filters | success, engine_name, engine_version |
| Search | filename, error_message |
| Fieldsets | Links, Engine, Result, Raw Data (collapsed), Audit (collapsed) |

### ExtractionApprovalAdmin

| Feature | Detail |
|---------|--------|
| List display | ID, invoice, status (color-coded), confidence (color-coded), fields_corrected_count, is_touchless, reviewed_by, reviewed_at |
| Filters | status, is_touchless |
| Search | invoice number, vendor name |
| Inlines | `ExtractionFieldCorrectionInline` (tabular, read-only) |
| Fieldsets | Links, Decision, Metrics, Snapshot (collapsed), Audit (collapsed) |

---

## 18. File Reference

| File | Purpose |
|------|---------|
| `apps/extraction/models.py` | ExtractionResult, ExtractionApproval, ExtractionFieldCorrection models |
| `apps/extraction/tasks.py` | Main extraction pipeline Celery task |
| `apps/extraction/admin.py` | Django admin registrations |
| `apps/extraction/template_views.py` | All template views (workbench, upload, approval queue, etc.) |
| `apps/extraction/urls.py` | URL routing |
| `apps/extraction/api_urls.py` | API URL routing (empty) |
| `apps/extraction/services/extraction_adapter.py` | Azure DI OCR + LLM extraction orchestration |
| `apps/extraction/services/parser_service.py` | JSON → ParsedInvoice dataclass parsing |
| `apps/extraction/services/normalization_service.py` | Field normalization (dates, amounts, strings) |
| `apps/extraction/services/validation_service.py` | Mandatory field validation + confidence check |
| `apps/extraction/services/duplicate_detection_service.py` | Duplicate invoice detection |
| `apps/extraction/services/persistence_service.py` | Invoice + LineItem + ExtractionResult persistence |
| `apps/extraction/services/approval_service.py` | Approval lifecycle (approve/reject/auto-approve + analytics) |
| `apps/agents/services/agent_classes.py` | InvoiceExtractionAgent + InvoiceUnderstandingAgent |
| `apps/agents/services/base_agent.py` | BaseAgent ReAct framework |
| `apps/core/prompt_registry.py` | LLM prompt templates (extraction.invoice_system) |
| `apps/core/enums.py` | InvoiceStatus, ExtractionApprovalStatus, AuditEventType enums |
| `apps/core/utils.py` | Normalization utilities (strings, dates, amounts, PO numbers) |
| `apps/documents/models.py` | DocumentUpload, Invoice, InvoiceLineItem models |
| `config/settings.py` | Azure credentials, thresholds, auto-approve config |
| `templates/extraction/workbench.html` | Extraction workbench UI |
| `templates/extraction/result_detail.html` | Extraction result detail UI |
| `templates/extraction/approval_queue.html` | Approval queue UI |
| `templates/extraction/approval_detail.html` | Approval review UI |
| **Extraction Core Services** | |
| `apps/extraction_core/services/extraction_service.py` | Primary extraction pipeline orchestrator (19-step) |
| `apps/extraction_core/services/base_extraction_service.py` | Schema-driven extraction base class |
| `apps/extraction_core/services/jurisdiction_resolver.py` | Multi-signal jurisdiction detection |
| `apps/extraction_core/services/resolution_service.py` | 4-tier jurisdiction resolution cascade |
| `apps/extraction_core/services/schema_registry.py` | Cached, version-aware schema lookup |
| `apps/extraction_core/services/prompt_builder.py` | Dynamic LLM prompt construction |
| `apps/extraction_core/services/llm_extraction_adapter.py` | LLM client wrapper for schema extraction |
| `apps/extraction_core/services/normalization_service.py` | Jurisdiction-driven field normalization |
| `apps/extraction_core/services/validation_service.py` | Jurisdiction-driven field validation |
| `apps/extraction_core/services/page_parser.py` | Multi-page OCR text segmentation |
| `apps/extraction_core/services/table_stitcher.py` | Cross-page table continuation |
| `apps/extraction_core/services/line_item_extractor.py` | Schema-driven line item extraction |
| `apps/extraction_core/services/document_classifier.py` | Weighted keyword document classification |
| `apps/extraction_core/services/relationship_extractor.py` | PO/GRN/contract cross-reference extraction |
| `apps/extraction_core/services/party_extractor.py` | Supplier/buyer/ship-to/bill-to extraction |
| `apps/extraction_core/services/document_intelligence.py` | Pre-extraction analysis orchestrator |
| `apps/extraction_core/services/confidence_scorer.py` | Multi-dimensional confidence scoring |
| `apps/extraction_core/services/review_routing.py` | Confidence-driven review routing |
| `apps/extraction_core/services/master_data_enrichment.py` | Post-extraction vendor/PO/customer matching |
| **Review Console Templates** | |
| `templates/extraction/console/console.html` | Main review console layout |
| `templates/extraction/console/_header_bar.html` | Command bar (status, confidence, actions) |
| `templates/extraction/console/_document_viewer.html` | Sticky document viewer with zoom/highlight |
| `templates/extraction/console/_extracted_data.html` | Tab 1: Header, Parties, Tax, Enrichment, Line Items |
| `templates/extraction/console/_confidence_badge.html` | Reusable confidence % badge (green/amber/red) |
| `templates/extraction/console/_validation_panel.html` | Tab 2: Errors/Warnings/Passed with go-to-field |
| `templates/extraction/console/_evidence_panel.html` | Tab 3: Evidence cards with source snippets |
| `templates/extraction/console/_reasoning_panel.html` | Tab 4: Agent reasoning timeline |
| `templates/extraction/console/_audit_trail.html` | Tab 5: Chronological audit event timeline |
| `templates/extraction/console/_bottom_timeline.html` | Pipeline stage progress bar |
| `templates/extraction/console/_approve_modal.html` | Approval confirmation modal |
| `templates/extraction/console/_reprocess_modal.html` | Reprocess extraction modal |
| `templates/extraction/console/_escalate_modal.html` | Escalation modal |
| `templates/extraction/console/_comment_modal.html` | Add comment modal |
| **Static Assets** | |
| `static/css/extraction_console.css` | Review console custom styles (~300 lines) |
| `static/js/extraction_console.js` | Review console JavaScript (~280 lines) |
| `templates/partials/sidebar.html` | Navigation sidebar (extraction links) |

---

## Debugging Tips

- **LLM calls failing?** Check `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` env vars.
- **OCR failing?** Check `AZURE_DI_ENDPOINT` and `AZURE_DI_KEY` env vars.
- **Extraction task not running?** On Windows without Redis, ensure `CELERY_TASK_ALWAYS_EAGER=True` (tasks run synchronously).
- **Confidence showing 1%?** `extraction_confidence` is stored as 0.0–1.0 float; templates use `{% widthratio %}` to display as percentage.
- **Auto-approve not working?** Check both `EXTRACTION_AUTO_APPROVE_ENABLED=true` AND `EXTRACTION_AUTO_APPROVE_THRESHOLD` < 1.0 (e.g., 0.95).
- **Agent 400 errors from OpenAI?** Ensure tool-calling messages follow OpenAI format: assistant messages include `tool_calls` array, tool responses include `tool_call_id`.
- **Approval queue empty?** Invoices only appear when `status=PENDING_APPROVAL` — check that the extraction pipeline completed successfully and auto-approve didn't trigger.
