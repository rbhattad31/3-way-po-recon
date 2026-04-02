# Database Documentation — 3-Way PO Reconciliation Platform

**Database**: MySQL (utf8mb4)
**Generated**: 2026-04-01
**Total tables**: 109 (Django system + application)

---

## Table of Contents

1. [Domain Overview](#1-domain-overview)
2. [Entity Relationship Summary](#2-entity-relationship-summary)
3. [Base Classes](#3-base-classes)
4. [Accounts & RBAC](#4-accounts--rbac)
5. [Documents (Invoice / PO / GRN)](#5-documents-invoice--po--grn)
6. [Vendors](#6-vendors)
7. [Extraction Pipeline](#7-extraction-pipeline)
8. [Extraction Core (Governed)](#8-extraction-core-governed)
9. [Cases](#9-cases)
10. [Reconciliation](#10-reconciliation)
11. [Agents & Tools](#11-agents--tools)
12. [Reviews](#12-reviews)
13. [Invoice Posting](#13-invoice-posting)
14. [ERP Integration](#14-erp-integration)
15. [Procurement](#15-procurement)
16. [Copilot](#16-copilot)
17. [Audit & Observability](#17-audit--observability)
18. [Reports & Integrations](#18-reports--integrations)
19. [Core Shared](#19-core-shared)
20. [Status Enumerations](#20-status-enumerations)
21. [Index Reference](#21-index-reference)

---

## 1. Domain Overview

The platform processes invoices through a 7-stage pipeline:

```
Upload -> Extraction -> Approval -> Reconciliation -> Agent Analysis -> Review -> Posting
```

Each stage produces records in separate apps. The central business object is **APCase** — one per invoice upload — which references all downstream records.

---

## 2. Entity Relationship Summary

```
DocumentUpload
  └── Invoice (1:1 via ap_case)
        ├── InvoiceLineItem (1:N)
        ├── ExtractionResult (1:N, last is canonical)
        ├── ExtractionApproval (1:1)
        │     └── ExtractionFieldCorrection (1:N)
        └── APCase (1:1)
              ├── APCaseStage (1:N)
              ├── APCaseArtifact (1:N)
              ├── APCaseDecision (1:N)
              ├── APCaseSummary (1:1)
              └── ReconciliationResult (FK)
                    ├── ReconciliationResultLine (1:N)
                    ├── ReconciliationException (1:N)
                    ├── AgentOrchestrationRun (1:N)
                    │     └── AgentRun (1:N via orchestration)
                    │           ├── AgentStep (1:N)
                    │           ├── AgentMessage (1:N)
                    │           ├── DecisionLog (1:N)
                    │           ├── AgentRecommendation (1:N)
                    │           └── AgentEscalation (1:N)
                    └── ReviewAssignment (1:1)
                          ├── ReviewComment (1:N)
                          ├── ManualReviewAction (1:N)
                          └── ReviewDecision (1:1)

PurchaseOrder
  ├── PurchaseOrderLineItem (1:N)
  └── GoodsReceiptNote (1:N)
        └── GRNLineItem (1:N)

Vendor
  ├── Invoice (1:N)
  ├── PurchaseOrder (1:N)
  ├── GoodsReceiptNote (1:N)
  └── VendorAliasMapping (1:N via alias_mappings)

InvoicePosting (1:1 Invoice)
  └── PostingRun (1:N)
        ├── PostingFieldValue (1:N)
        ├── PostingLineItem (1:N)
        ├── PostingIssue (1:N)
        ├── PostingEvidence (1:N)
        └── PostingApprovalRecord (1:N)

ERPReferenceImportBatch
  ├── ERPVendorReference (1:N)
  ├── ERPItemReference (1:N)
  ├── ERPTaxCodeReference (1:N)
  ├── ERPCostCenterReference (1:N)
  └── ERPPOReference (1:N)
```

---

## 3. Base Classes

These are abstract Django models — they add fields to every concrete model that inherits from them.

### TimestampMixin (abstract)

| Column | Type | Notes |
|---|---|---|
| `created_at` | DATETIME | Auto-set on INSERT, indexed |
| `updated_at` | DATETIME | Auto-set on UPDATE |

### AuditMixin (abstract)

| Column | Type | Notes |
|---|---|---|
| `created_by_id` | FK -> accounts_user | SET NULL on delete |
| `updated_by_id` | FK -> accounts_user | SET NULL on delete |

### BaseModel (abstract)

Combines `TimestampMixin` + `AuditMixin`. Used by all business entity tables.

### SoftDeleteMixin (abstract)

| Column | Type | Notes |
|---|---|---|
| `is_active` | BOOL | False = soft-deleted. Never hard-delete business entities. |

---

## 4. Accounts & RBAC

### `accounts_user`

Custom user with email login. Current rows: **18**

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | BIGINT | PK | |
| `email` | VARCHAR(254) | UNIQUE, indexed | Login field |
| `first_name` | VARCHAR(150) | | |
| `last_name` | VARCHAR(150) | | |
| `role` | VARCHAR(30) | | Legacy single-role field; RBAC multi-role via `accounts_user_role` |
| `is_active` | BOOL | | |
| `is_staff` | BOOL | | Django admin access |
| `department` | VARCHAR(100) | | |
| `password` | VARCHAR(128) | | Hashed |
| `last_login` | DATETIME | | |
| `created_at` | DATETIME | indexed | |
| `updated_at` | DATETIME | | |

**Indexes**: `idx_user_role` on `(role)`

**Key methods (Python)**:
- `get_primary_role()` — active primary `Role` object
- `has_permission(code)` — ADMIN bypass -> DENY override -> ALLOW override -> role grant
- `get_effective_permissions()` — frozenset of codes (cached per request)

---

### `accounts_role`

RBAC roles. Current rows: **9** (ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR, SYSTEM, SYSTEM_AGENT + 2 custom)

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `code` | VARCHAR(50) | UNIQUE — used in code: `"ADMIN"`, `"AP_PROCESSOR"` etc. |
| `name` | VARCHAR(150) | Display name |
| `description` | TEXT | |
| `is_system_role` | BOOL | System roles (ADMIN, SYSTEM_AGENT) bypass scope checks |
| `is_active` | BOOL | indexed |
| `rank` | INT UNSIGNED | Lower = higher privilege; ADMIN=10, SYSTEM_AGENT=100 |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

---

### `accounts_permission`

Permission catalog. Current rows: **55**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `code` | VARCHAR(100) | UNIQUE — convention: `{module}.{action}` e.g. `invoices.view` |
| `name` | VARCHAR(200) | |
| `module` | VARCHAR(50) | indexed |
| `action` | VARCHAR(50) | |
| `description` | TEXT | |
| `is_active` | BOOL | indexed |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | |

**Indexes**: `idx_perm_module_action` on `(module, action)`

---

### `accounts_role_permission`

M2M join between Role and Permission. Current rows: **184**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `role_id` | FK -> accounts_role | CASCADE |
| `permission_id` | FK -> accounts_permission | CASCADE |
| `is_allowed` | BOOL | False = explicit deny at role level |
| `created_at` / `updated_at` | DATETIME | |

**Constraint**: UNIQUE `(role_id, permission_id)`

---

### `accounts_user_role`

User-to-role assignment with optional expiry. Current rows: **20**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `user_id` | FK -> accounts_user | CASCADE |
| `role_id` | FK -> accounts_role | CASCADE |
| `is_primary` | BOOL | indexed — one primary role per user |
| `assigned_by_id` | FK -> accounts_user | SET NULL |
| `assigned_at` | DATETIME | |
| `expires_at` | DATETIME | NULL = never expires |
| `is_active` | BOOL | indexed |
| `scope_json` | JSON | NULL = unrestricted. Keys: `allowed_business_units` (list[str]), `allowed_vendor_ids` (list[int]) |
| `created_at` / `updated_at` | DATETIME | |

**Constraint**: UNIQUE `(user_id, role_id)`

---

### `accounts_user_permission_override`

Per-user ALLOW/DENY overrides. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `user_id` | FK -> accounts_user | CASCADE |
| `permission_id` | FK -> accounts_permission | CASCADE |
| `override_type` | VARCHAR(10) | `ALLOW` or `DENY` |
| `reason` | TEXT | |
| `assigned_by_id` | FK -> accounts_user | SET NULL |
| `assigned_at` | DATETIME | |
| `expires_at` | DATETIME | NULL = never expires |
| `is_active` | BOOL | indexed |
| `created_at` / `updated_at` | DATETIME | |

**Constraint**: UNIQUE `(user_id, permission_id)`

---

### `accounts_menu_config`

Controls sidebar visibility by permission. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `label` | VARCHAR(100) | |
| `icon_class` | VARCHAR(100) | CSS class |
| `url_name` | VARCHAR(200) | Django URL name |
| `required_permission` | VARCHAR(100) | Permission code required to see this item |
| `parent_id` | FK -> self | SET NULL — for nested menus |
| `order` | INT UNSIGNED | Sort order |
| `is_active` | BOOL | |
| `is_separator` | BOOL | Render as divider |
| `created_at` / `updated_at` | DATETIME | |

---

## 5. Documents (Invoice / PO / GRN)

### `documents_upload`

File upload record. One per uploaded file. Current rows: **4**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `file` | VARCHAR(500) | Relative path under MEDIA_ROOT |
| `original_filename` | VARCHAR(500) | |
| `file_size` | INT UNSIGNED | Bytes |
| `file_hash` | VARCHAR(64) | SHA-256, indexed — used for duplicate detection |
| `content_type` | VARCHAR(100) | MIME type |
| `document_type` | VARCHAR(30) | `INVOICE`, `PO`, `GRN`, `QUOTATION`, `OTHER` |
| `processing_state` | VARCHAR(20) | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED` |
| `processing_message` | TEXT | |
| `blob_path` / `blob_container` / `blob_name` / `blob_url` | VARCHAR | Azure Blob Storage |
| `blob_metadata` | JSON | |
| `blob_uploaded_at` | DATETIME | |
| `uploaded_by_id` | FK -> accounts_user | SET NULL |
| `created_at` / `updated_at` | DATETIME | |
| `created_by_id` / `updated_by_id` | FK -> accounts_user | SET NULL |

---

### `documents_invoice`

Extracted and normalized invoice header. Current rows: **4**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `document_upload_id` | FK -> documents_upload | SET NULL |
| `vendor_id` | FK -> vendors_vendor | SET NULL, blank |
| `raw_vendor_name` | VARCHAR | As extracted from document |
| `raw_vendor_tax_id` | VARCHAR | |
| `raw_buyer_name` | VARCHAR | |
| `raw_invoice_number` | VARCHAR | |
| `raw_invoice_date` | VARCHAR | |
| `raw_due_date` | VARCHAR | |
| `raw_po_number` | VARCHAR | |
| `raw_currency` | VARCHAR | |
| `raw_subtotal` / `raw_tax_amount` / `raw_total_amount` | VARCHAR | Raw string values |
| `invoice_number` | VARCHAR(100) | Normalized, indexed |
| `normalized_invoice_number` | VARCHAR(100) | Uppercase stripped, indexed |
| `invoice_date` | DATE | Nullable |
| `due_date` | DATE | Nullable |
| `po_number` | VARCHAR(100) | indexed |
| `normalized_po_number` | VARCHAR(100) | indexed |
| `currency` | VARCHAR(10) | Default: USD |
| `subtotal` | DECIMAL(18,2) | Nullable |
| `tax_percentage` | DECIMAL(7,4) | Nullable |
| `tax_amount` | DECIMAL(18,2) | Nullable |
| `tax_breakdown` | JSON | `{cgst, sgst, igst, vat}` |
| `total_amount` | DECIMAL(18,2) | Nullable |
| `vendor_tax_id` | VARCHAR(100) | Normalized |
| `buyer_name` | VARCHAR(255) | |
| `status` | VARCHAR(30) | See status enum below, indexed |
| `is_duplicate` | BOOL | indexed |
| `duplicate_of_id` | FK -> self | SET NULL |
| `extraction_confidence` | FLOAT | 0.0-1.0 |
| `extraction_remarks` | TEXT | |
| `extraction_raw_json` | JSON | Full LLM response |
| `reprocessed` | BOOL | |
| `reprocessed_from_id` | FK -> self | SET NULL |
| `created_at` / `updated_at` | DATETIME | |
| `created_by_id` / `updated_by_id` | FK -> accounts_user | SET NULL |

**Invoice Status Flow**:
`UPLOADED` -> `EXTRACTION_IN_PROGRESS` -> `EXTRACTED` -> `VALIDATED` -> `PENDING_APPROVAL` -> `READY_FOR_RECON` -> `RECONCILED` | `FAILED` | `INVALID`

---

### `documents_invoice_line`

Invoice line items. Current rows: **4**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `invoice_id` | FK -> documents_invoice | CASCADE |
| `line_number` | INT UNSIGNED | |
| `raw_description` / `raw_quantity` / `raw_unit_price` / `raw_tax_amount` / `raw_line_amount` | VARCHAR | Raw extracted values |
| `description` | TEXT | Normalized |
| `normalized_description` | TEXT | Lowercase, stripped |
| `quantity` | DECIMAL(18,4) | Nullable |
| `unit_price` | DECIMAL(18,4) | Nullable |
| `tax_percentage` | DECIMAL(7,4) | Nullable |
| `tax_amount` | DECIMAL(18,2) | Nullable |
| `line_amount` | DECIMAL(18,2) | Nullable |
| `extraction_confidence` | FLOAT | Nullable |
| `item_category` | VARCHAR(100) | |
| `is_service_item` | BOOL | Nullable — drives 2-way vs 3-way mode selection |
| `is_stock_item` | BOOL | Nullable |
| `created_at` / `updated_at` | DATETIME | |

---

### `documents_purchase_order`

PO header. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `po_number` | VARCHAR(100) | UNIQUE, indexed |
| `normalized_po_number` | VARCHAR(100) | indexed |
| `vendor_id` | FK -> vendors_vendor | SET NULL |
| `po_date` | DATE | Nullable |
| `currency` | VARCHAR(10) | Default: USD |
| `total_amount` | DECIMAL(18,2) | Nullable |
| `tax_amount` | DECIMAL(18,2) | Nullable |
| `status` | VARCHAR(30) | Default: OPEN, indexed |
| `buyer_name` / `department` | VARCHAR(255) | |
| `notes` | TEXT | Via NotesMixin |
| timestamps + audit FKs | | |

---

### `documents_po_line`

PO line items. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `purchase_order_id` | FK -> documents_purchase_order | CASCADE |
| `line_number` | INT UNSIGNED | |
| `item_code` | VARCHAR(100) | |
| `description` | TEXT | |
| `quantity` | DECIMAL(18,4) | |
| `unit_price` | DECIMAL(18,4) | |
| `tax_amount` | DECIMAL(18,2) | Nullable |
| `line_amount` | DECIMAL(18,2) | |
| `unit_of_measure` | VARCHAR(30) | Default: EA |
| `item_category` | VARCHAR(100) | |
| `is_service_item` | BOOL | Nullable |
| `is_stock_item` | BOOL | Nullable |
| `created_at` / `updated_at` | DATETIME | |

---

### `documents_grn`

Goods Receipt Note header. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `grn_number` | VARCHAR(100) | UNIQUE, indexed |
| `purchase_order_id` | FK -> documents_purchase_order | CASCADE |
| `vendor_id` | FK -> vendors_vendor | SET NULL |
| `receipt_date` | DATE | Nullable |
| `status` | VARCHAR(30) | Default: RECEIVED, indexed |
| `warehouse` | VARCHAR(255) | |
| `receiver_name` | VARCHAR(255) | |
| `notes` | TEXT | Via NotesMixin |
| timestamps + audit FKs | | |

**Indexes**: `idx_grn_number`, `idx_grn_po`, `idx_grn_status`

> GRNs are created via API/Admin or pushed by ERP webhooks. There is no flat-file GRN importer — GRNs are live transactional events, not reference data.

---

### `documents_grn_line`

GRN line items. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `grn_id` | FK -> documents_grn | CASCADE |
| `line_number` | INT UNSIGNED | |
| `po_line_id` | FK -> documents_po_line | SET NULL |
| `item_code` | VARCHAR(100) | |
| `description` | TEXT | |
| `quantity_received` | DECIMAL(18,4) | |
| `quantity_accepted` | DECIMAL(18,4) | Nullable |
| `quantity_rejected` | DECIMAL(18,4) | Nullable |
| `unit_of_measure` | VARCHAR(30) | Default: EA |
| `created_at` / `updated_at` | DATETIME | |

**Indexes**: `idx_grnline_num` on `(grn_id, line_number)`, `idx_grnline_poline`

---

## 6. Vendors

### `vendors_vendor`

Master vendor records. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `code` | VARCHAR(50) | UNIQUE — ERP vendor code |
| `name` | VARCHAR(255) | indexed |
| `normalized_name` | VARCHAR(255) | Lowercase stripped, indexed |
| `tax_id` | VARCHAR(50) | |
| `address` | TEXT | |
| `country` | VARCHAR(100) | |
| `currency` | VARCHAR(10) | Default: USD |
| `payment_terms` | VARCHAR(100) | |
| `contact_email` | VARCHAR(254) | |
| `is_active` | BOOL | Soft-delete via SoftDeleteMixin |
| timestamps + audit FKs | | |

**Indexes**: `idx_vendor_code`, `idx_vendor_norm_name`

---

### `posting_core_vendor_alias`

**Canonical vendor alias table** — bridges vendor name variants to both the `Vendor` master record and the ERP reference snapshot. Used by extraction, reconciliation, cases, and posting. Current rows: **3**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `alias_text` | VARCHAR(500) | Original text form of the alias |
| `normalized_alias` | VARCHAR(500) | `normalize_string(alias_text)`, indexed |
| `vendor_id` | FK -> vendors_vendor | SET NULL — links to stable Vendor master |
| `vendor_reference_id` | FK -> posting_core_erp_vendor_ref | SET NULL — links to ERP snapshot |
| `source` | VARCHAR(50) | `manual`, `erp_import`, `extraction`, `feedback` |
| `confidence` | FLOAT | Default: 1.0 |
| `is_active` | BOOL | |
| `notes` | TEXT | |
| timestamps + audit FKs | | |

> The `vendor` FK is populated automatically when `VendorImporter` imports an ERP reference. Extraction and reconciliation look up via `normalized_alias -> vendor_id`. Posting maps via `normalized_alias -> vendor_reference_id -> ERP vendor code`.

---

### `posting_core_erp_vendor_ref`

ERP vendor master snapshot — imported from Excel/CSV via import batches. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `batch_id` | FK -> posting_core_erp_import_batch | CASCADE |
| `vendor_code` | VARCHAR(50) | indexed |
| `vendor_name` | VARCHAR(500) | |
| `normalized_vendor_name` | VARCHAR(500) | indexed |
| `vendor_group` | VARCHAR(100) | |
| `country_code` | VARCHAR(3) | |
| `is_active` | BOOL | |
| `payment_terms` | VARCHAR(100) | |
| `currency` | VARCHAR(10) | |
| `raw_json` | JSON | Full source row |
| `created_at` / `updated_at` | DATETIME | |

**Indexes**: `idx_vref_code`, `idx_vref_norm_name`

> Re-importable snapshot. Stable `Vendor` records are upserted from this table on every import.

---

## 7. Extraction Pipeline

### `extraction_result`

UI-facing extraction result. Current rows: **4**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `document_upload_id` | FK -> documents_upload | CASCADE |
| `invoice_id` | FK -> documents_invoice | SET NULL |
| `extraction_run_id` | FK -> extraction_core_extraction_run | SET NULL |
| `engine_name` | VARCHAR(100) | Default: `default` |
| `engine_version` | VARCHAR(50) | |
| `raw_response` | JSON | Full LLM response |
| `confidence` | FLOAT | 0.0-1.0 |
| `duration_ms` | INT UNSIGNED | |
| `success` | BOOL | |
| `error_message` | TEXT | |
| `agent_run_id` | BIGINT | indexed |
| `ocr_page_count` / `ocr_duration_ms` / `ocr_char_count` | INT UNSIGNED | |
| `ocr_text` | TEXT | Raw OCR output (truncated at 60K chars) |
| timestamps + audit FKs | | |

---

### `extraction_approval`

Human-in-the-loop gate before reconciliation. Current rows: **4**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `invoice_id` | FK -> documents_invoice | CASCADE, OneToOne |
| `extraction_result_id` | FK -> extraction_result | SET NULL |
| `status` | VARCHAR(20) | `PENDING`, `APPROVED`, `REJECTED`, `AUTO_APPROVED`, indexed |
| `reviewed_by_id` | FK -> accounts_user | SET NULL |
| `reviewed_at` | DATETIME | Nullable |
| `rejection_reason` | TEXT | |
| `confidence_at_review` | FLOAT | Nullable — confidence snapshot at review time |
| `original_values_snapshot` | JSON | Pre-correction field values |
| `fields_corrected_count` | INT UNSIGNED | Number of fields changed during approval |
| `is_touchless` | BOOL | indexed — True if auto-approved without human correction |
| timestamps + audit FKs | | |

---

### `extraction_field_correction`

Per-field correction made during approval. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `approval_id` | FK -> extraction_approval | CASCADE |
| `entity_type` | VARCHAR(20) | `header` or `line_item` |
| `entity_id` | INT UNSIGNED | Nullable — line item PK |
| `field_name` | VARCHAR(100) | e.g. `invoice_date`, `total_amount` |
| `original_value` | TEXT | |
| `corrected_value` | TEXT | |
| `corrected_by_id` | FK -> accounts_user | SET NULL |
| `created_at` / `updated_at` | DATETIME | |

---

### `extraction_user_credit_account`

Per-user extraction credit balance.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `user_id` | FK -> accounts_user | CASCADE, OneToOne |
| `balance_credits` | INT UNSIGNED | Available balance |
| `reserved_credits` | INT UNSIGNED | Held for in-progress jobs |
| `monthly_limit` | INT UNSIGNED | 0 = unlimited |
| `monthly_used` | INT UNSIGNED | Resets monthly |
| `is_active` | BOOL | indexed |
| `last_reset_at` | DATETIME | |
| `created_at` / `updated_at` | DATETIME | |

---

### `extraction_credit_transaction`

Immutable ledger. Never edit or delete rows.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `account_id` | FK -> extraction_user_credit_account | CASCADE |
| `transaction_type` | VARCHAR(30) | `ALLOCATE`, `CONSUME`, `REFUND`, `RESERVE`, `RELEASE`, indexed |
| `credits` | INT | Positive = allocate/refund, negative = consume |
| `balance_after` | INT | |
| `reserved_after` | INT | |
| `monthly_used_after` | INT | |
| `reference_type` / `reference_id` | VARCHAR | Links to document_upload, agent_run etc. |
| `remarks` | TEXT | |
| `created_by_id` | FK -> accounts_user | SET NULL |
| `created_at` | DATETIME | indexed |

---

### `extraction_bulk_job` / `extraction_bulk_item` / `extraction_bulk_source_connection`

Bulk extraction orchestration tables (not detailed here — see `apps/extraction/` bulk models).

---

## 8. Extraction Core (Governed)

These tables back the lower-level governed extraction engine used by `ExtractionRun`.

| Table | Purpose |
|---|---|
| `extraction_core_tax_jurisdiction_profile` | Tax regimes (India-GST, UAE-VAT, etc.) |
| `extraction_core_extraction_schema_definition` | Versioned extraction schemas per jurisdiction+doctype |
| `extraction_core_runtime_settings` | Per-tenant runtime config (model, confidence thresholds) |
| `extraction_core_entity_extraction_profile` | Per-entity-type field extraction instructions |
| `extraction_core_extraction_run` | One execution of the extraction pipeline |
| `extraction_core_extraction_field_value` | Per-field extraction result within a run |
| `extraction_core_country_pack` | Country-specific field configs |
| `extraction_core_extraction_prompt_template` | Versioned LLM prompt templates |
| `extraction_core_review_routing_rule` | Rules for routing to human review |
| `extraction_core_extraction_analytics_snapshot` | Aggregated performance metrics per run |
| `extraction_core_extraction_approval_record` | Governed approval record (mirrors `extraction_approval`) |
| `extraction_core_extraction_correction` | Governed field correction record |
| `extraction_core_extraction_evidence` | Evidence items per extraction |
| `extraction_core_extraction_issue` | Validation issues found during extraction |
| `extraction_core_extraction_line_item` | Line items extracted in governed run |

---

## 9. Cases

### `cases_apcase`

Central case record. One per invoice upload. Current rows: **3**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `case_number` | VARCHAR(50) | UNIQUE — format `AP-YYMMDD-NNNN` |
| `invoice_id` | FK -> documents_invoice | PROTECT, OneToOne |
| `vendor_id` | FK -> vendors_vendor | SET NULL |
| `purchase_order_id` | FK -> documents_purchase_order | SET NULL |
| `reconciliation_result_id` | FK -> reconciliation_result | SET NULL |
| `review_assignment_id` | FK -> reviews_assignment | SET NULL |
| `source_channel` | VARCHAR(30) | `UPLOAD`, `EMAIL`, `API`, `EDI`, `INTEGRATION` |
| `invoice_type` | VARCHAR(20) | `STANDARD`, `NON_PO`, `CONSOLIDATED`, `CREDIT_NOTE`, `PROFORMA` |
| `processing_path` | VARCHAR(20) | `TWO_WAY`, `THREE_WAY`, `NON_PO`, indexed |
| `status` | VARCHAR(50) | See CaseStatus enum, indexed |
| `current_stage` | VARCHAR(50) | Current pipeline stage |
| `priority` | VARCHAR(20) | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`, indexed |
| `risk_score` / `extraction_confidence` | FLOAT | Nullable |
| `requires_human_review` / `requires_approval` | BOOL | |
| `eligible_for_posting` / `duplicate_risk_flag` | BOOL | |
| `assigned_to_id` | FK -> accounts_user | SET NULL |
| `assigned_role` | VARCHAR(30) | |
| `reconciliation_mode` | VARCHAR(20) | Copy of resolved recon mode |
| `budget_check_status` / `coding_status` | VARCHAR(30) | |
| `is_active` | BOOL | Soft-delete |
| timestamps + audit FKs | | |

**Indexes**: `(status, processing_path)`, `(priority, -created_at)`, `(assigned_to_id, status)`

---

### `cases_apcasestage`

Pipeline stage execution records per case. Current rows: **21**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `case_id` | FK -> cases_apcase | CASCADE |
| `stage_name` | VARCHAR(50) | See CaseStageType enum |
| `stage_status` | VARCHAR(30) | `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, `SKIPPED` |
| `performed_by_type` | VARCHAR(30) | `HUMAN`, `AGENT`, `SYSTEM` |
| `performed_by_agent_id` | FK -> agents_run | SET NULL |
| `started_at` / `completed_at` | DATETIME | Nullable |
| `duration_ms` / `retry_count` | INT UNSIGNED | |
| `input_payload` / `output_payload` | JSON | |
| `notes` | TEXT | |
| `trace_id` / `span_id` / `parent_span_id` | VARCHAR | Distributed tracing |
| `error_code` / `error_message` | VARCHAR / TEXT | |
| `config_snapshot_json` | JSON | Config active at execution time |
| `created_at` / `updated_at` | DATETIME | |

**Constraint**: UNIQUE `(case_id, stage_name, retry_count)`

**Stage types**: `UPLOAD`, `EXTRACTION`, `VALIDATION`, `APPROVAL`, `RECONCILIATION`, `AGENT_ANALYSIS`, `REVIEW`, `POSTING`, `CASE_SUMMARY`, `NON_PO_VALIDATION`, etc.

---

### Other case tables

| Table | Purpose | Notes |
|---|---|---|
| `cases_apcaseartifact` | Versioned payload snapshots per case stage | `artifact_type`: `EXTRACTION_RESULT`, `VALIDATION_RESULT`, `RECONCILIATION_RESULT`, `AGENT_OUTPUT`, `POSTING_DATA` |
| `cases_apcasedecision` | Recorded decisions with confidence + rationale | `decision_type`: `ROUTE`, `APPROVE`, `REJECT`, `ESCALATE`, `AUTO_CLOSE` |
| `cases_apcaseassignment` | Queue/user assignments per case | |
| `cases_apcasesummary` | Latest LLM-generated summary (1:1 with APCase) | |
| `cases_apcasecomment` | Human comments on a case | |
| `cases_apcaseactivity` | Activity feed entries | |

---

## 10. Reconciliation

### `reconciliation_config`

Single global tolerance configuration. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `name` | VARCHAR(100) | |
| `price_tolerance_pct` | DECIMAL(5,2) | Strict band — default 2% |
| `qty_tolerance_pct` | DECIMAL(5,2) | Strict band — default 1% |
| `amount_tolerance_pct` | DECIMAL(5,2) | Strict band — default 1% |
| `auto_close_price_tolerance_pct` | DECIMAL(5,2) | Wider band — default 3% |
| `auto_close_qty_tolerance_pct` | DECIMAL(5,2) | Default 5% |
| `auto_close_amount_tolerance_pct` | DECIMAL(5,2) | Default 3% |
| `default_reconciliation_mode` | VARCHAR(20) | `TWO_WAY` or `THREE_WAY` |
| `mode_resolver_enabled` | BOOL | Enable policy/heuristic mode resolution |
| `is_active` | BOOL | |
| timestamps + audit FKs | | |

---

### `reconciliation_policy`

Vendor/category/location-based mode overrides. Current rows: **16**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `name` | VARCHAR(200) | |
| `vendor_id` | FK -> vendors_vendor | SET NULL |
| `item_category` | VARCHAR(100) | |
| `location_code` | VARCHAR(50) | |
| `business_unit` | VARCHAR(100) | |
| `is_service_invoice` | BOOL | Nullable — match only when True |
| `is_stock_invoice` | BOOL | Nullable |
| `reconciliation_mode` | VARCHAR(20) | Mode to apply when this policy matches |
| `priority` | INT | Lower = higher priority |
| `is_active` | BOOL | |
| timestamps + audit FKs | | |

---

### `reconciliation_run`

Top-level reconciliation execution for a batch (legacy). Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `run_date` | DATETIME | |
| `run_by_id` | FK -> accounts_user | SET NULL |
| `status` | VARCHAR(20) | `PENDING`, `RUNNING`, `COMPLETED`, `FAILED` |
| `total_invoices` / `matched` / `partial` / `unmatched` / `errors` | INT | |
| `config_snapshot` | JSON | Config at run time |
| `trace_id` | VARCHAR(64) | indexed |
| timestamps + audit FKs | | |

---

### `reconciliation_result`

Match result per invoice. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `reconciliation_run_id` | FK -> reconciliation_run | SET NULL |
| `invoice_id` | FK -> documents_invoice | CASCADE |
| `purchase_order_id` | FK -> documents_purchase_order | SET NULL |
| `grn_id` | FK -> documents_grn | SET NULL |
| `match_status` | VARCHAR(30) | `MATCHED`, `PARTIAL_MATCH`, `UNMATCHED`, `REQUIRES_REVIEW`, `ERROR`, indexed |
| `reconciliation_mode` | VARCHAR(20) | `TWO_WAY` or `THREE_WAY` |
| `mode_resolved_by` | VARCHAR(20) | `policy`, `heuristic`, `default` |
| `confidence_score` | FLOAT | 0.0-1.0 |
| `summary` | TEXT | LLM-generated plain-text summary |
| `match_details` | JSON | Per-field comparison breakdown |
| `price_variance_pct` / `qty_variance_pct` / `amount_variance_pct` | FLOAT | Nullable |
| `within_auto_close_band` | BOOL | True if PARTIAL_MATCH within wider tolerance |
| `created_at` / `updated_at` | DATETIME | |

**Match Status values**: `MATCHED` (all lines within strict tolerance), `PARTIAL_MATCH` (some lines out), `UNMATCHED` (no PO found), `REQUIRES_REVIEW` (exceptions need human), `ERROR` (processing failure)

---

### `reconciliation_result_line`

Per-line comparison detail.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `reconciliation_result_id` | FK -> reconciliation_result | CASCADE |
| `invoice_line_id` | FK -> documents_invoice_line | SET NULL |
| `po_line_id` | FK -> documents_po_line | SET NULL |
| `grn_line_id` | FK -> documents_grn_line | SET NULL |
| `match_status` | VARCHAR(30) | Line-level match |
| `price_match` / `qty_match` / `amount_match` | BOOL | Nullable |
| `invoice_qty` / `po_qty` / `grn_qty` | DECIMAL | Nullable |
| `invoice_price` / `po_price` | DECIMAL | Nullable |
| `invoice_amount` / `po_amount` / `grn_amount` | DECIMAL | Nullable |
| `price_variance_pct` / `qty_variance_pct` / `amount_variance_pct` | FLOAT | Nullable |
| `notes` | TEXT | |
| `created_at` / `updated_at` | DATETIME | |

---

### `reconciliation_exception`

Exception items requiring attention.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `reconciliation_result_id` | FK -> reconciliation_result | CASCADE |
| `exception_type` | VARCHAR(50) | `PRICE_MISMATCH`, `QTY_MISMATCH`, `MISSING_GRN`, `DUPLICATE_INVOICE`, `PO_NOT_FOUND`, etc. |
| `severity` | VARCHAR(20) | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `field_name` | VARCHAR(100) | Affected field |
| `invoice_value` / `po_value` / `grn_value` | TEXT | Nullable |
| `variance_pct` | FLOAT | Nullable |
| `message` | TEXT | Human-readable description |
| `applies_to_mode` | VARCHAR(20) | `TWO_WAY`, `THREE_WAY`, or blank (both) |
| `created_at` / `updated_at` | DATETIME | |

---

## 11. Agents & Tools

### `agents_definition`

Agent contract catalog. Current rows: **8**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `agent_type` | VARCHAR(40) | UNIQUE — `PO_RETRIEVAL`, `GRN_RETRIEVAL`, `VENDOR_VALIDATION`, `EXCEPTION_ANALYSIS`, `DUPLICATE_DETECTION`, `COMPLIANCE_CHECK`, `ESCALATION_DECISION`, `RECONCILIATION_SUMMARY` |
| `name` / `description` | VARCHAR / TEXT | |
| `enabled` | BOOL | indexed |
| `llm_model` | VARCHAR(100) | e.g. `gpt-4o` |
| `system_prompt` | TEXT | |
| `max_retries` / `timeout_seconds` | INT UNSIGNED | |
| `config_json` | JSON | `allowed_tools` list |
| `purpose` / `entry_conditions` / `success_criteria` | TEXT | Contract fields |
| `prohibited_actions` | JSON | |
| `requires_tool_grounding` | BOOL | Require at least N tool calls |
| `min_tool_calls` | INT UNSIGNED | |
| `tool_failure_confidence_cap` | FLOAT | Cap on confidence if tools fail |
| `allowed_recommendation_types` | JSON | |
| `default_fallback_recommendation` | VARCHAR(60) | |
| `output_schema_name` / `output_schema_version` | VARCHAR | |
| `lifecycle_status` | VARCHAR(20) | `draft`, `active`, `deprecated` |
| `owner_team` | VARCHAR(100) | |
| `capability_tags` / `domain_tags` | JSON | |
| `human_review_required_conditions` | TEXT | |
| timestamps + audit FKs | | |

---

### `agents_orchestration_run`

Top-level pipeline invocation. One per `ReconciliationResult` processing attempt. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `reconciliation_result_id` | FK -> reconciliation_result | CASCADE |
| `status` | VARCHAR(20) | `PLANNED`, `RUNNING`, `COMPLETED`, `PARTIAL`, `FAILED` |
| `plan_source` | VARCHAR(20) | `deterministic` or `llm` |
| `plan_confidence` | FLOAT | Nullable |
| `planned_agents` / `executed_agents` | JSON | |
| `final_recommendation` | VARCHAR(60) | |
| `final_confidence` | FLOAT | |
| `skip_reason` / `error_message` | VARCHAR / TEXT | |
| `actor_user_id` | INT UNSIGNED | |
| `trace_id` | VARCHAR(64) | indexed |
| `started_at` / `completed_at` | DATETIME | |
| `duration_ms` | INT UNSIGNED | |
| timestamps + audit FKs | | |

> Acts as a duplicate-run guard: a RUNNING record blocks re-entry for the same `reconciliation_result`.

---

### `agents_run`

Individual agent execution. Current rows: **18**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `agent_definition_id` | FK -> agents_definition | SET NULL |
| `agent_type` | VARCHAR(40) | indexed |
| `document_upload_id` | FK -> documents_upload | SET NULL |
| `reconciliation_result_id` | FK -> reconciliation_result | CASCADE |
| `status` | VARCHAR(20) | `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED` |
| `input_payload` / `output_payload` | JSON | |
| `summarized_reasoning` | TEXT | ASCII-only (sanitized before save) |
| `confidence` | FLOAT | |
| `started_at` / `completed_at` | DATETIME | |
| `duration_ms` | INT UNSIGNED | |
| `error_message` | TEXT | |
| `trace_id` / `span_id` | VARCHAR | |
| `invocation_reason` | VARCHAR(500) | |
| `prompt_version` | VARCHAR(50) | |
| `actor_user_id` | INT UNSIGNED | |
| `cost_estimate` | DECIMAL(10,6) | |
| `llm_model_used` | VARCHAR(100) | |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | INT | |
| `actor_primary_role` | VARCHAR(50) | RBAC snapshot |
| `actor_roles_snapshot_json` | JSON | |
| `permission_source` | VARCHAR(50) | |
| `access_granted` | BOOL | |
| `handed_off_to_id` | FK -> self | SET NULL |
| timestamps + audit FKs | | |

---

### `agents_step` / `agents_message` / `agents_decision_log` / `agents_recommendation` / `agents_escalation`

| Table | Purpose | Key columns |
|---|---|---|
| `agents_step` | ReAct loop iteration | `agent_run_id`, `step_number`, `action`, `input_data`, `output_data`, `success` |
| `agents_message` | LLM conversation messages | `agent_run_id`, `role` (system/user/assistant/tool), `content`, `token_count` |
| `agents_decision_log` | Recorded decisions with evidence | `agent_run_id`, `decision_type`, `decision`, `confidence`, `recommendation_type` |
| `agents_recommendation` | Actionable recommendations | `agent_run_id`, `reconciliation_result_id`, `recommendation_type`, `confidence`, `accepted` (null=pending) |
| `agents_escalation` | Escalation records | `agent_run_id`, `severity`, `reason`, `suggested_assignee_role`, `resolved` |

**Recommendation types**: `APPROVE_AND_CLOSE`, `SEND_TO_AP_REVIEW`, `REQUEST_CREDIT_NOTE`, `ESCALATE_TO_FINANCE`, `AUTO_CLOSE_PARTIAL`, `REJECT_INVOICE`, `REPROCESS`

---

### `tools_definition`

Registered tool catalog. Current rows: **6**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `name` | VARCHAR(100) | UNIQUE — `po_lookup`, `grn_lookup`, `vendor_search`, `invoice_details`, `exception_list`, `reconciliation_summary` |
| `display_name` | VARCHAR(200) | |
| `description` | TEXT | Shown to LLM |
| `tool_schema` | JSON | OpenAI-format function schema |
| `required_permission` | VARCHAR(100) | e.g. `purchase_orders.view` |
| `is_active` | BOOL | |
| `version` | VARCHAR(20) | |
| timestamps + audit FKs | | |

---

### `tools_call`

Per-invocation tool call log. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `agent_run_id` | FK -> agents_run | CASCADE |
| `tool_definition_id` | FK -> tools_definition | SET NULL |
| `tool_name` | VARCHAR(100) | |
| `input_payload` / `output_payload` | JSON | |
| `status` | VARCHAR(20) | `SUCCESS`, `ERROR`, `TIMEOUT` |
| `duration_ms` | INT UNSIGNED | |
| `error_message` | TEXT | |
| `trace_id` / `span_id` | VARCHAR | |
| timestamps + audit FKs | | |

---

## 12. Reviews

### `reviews_assignment`

Human review assignment. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `reconciliation_result_id` | FK -> reconciliation_result | CASCADE |
| `invoice_id` | FK -> documents_invoice | SET NULL |
| `assigned_to_id` | FK -> accounts_user | SET NULL |
| `assigned_by_id` | FK -> accounts_user | SET NULL |
| `status` | VARCHAR(20) | `PENDING`, `ASSIGNED`, `IN_REVIEW`, `APPROVED`, `REJECTED`, `REPROCESSED` |
| `priority` | INT UNSIGNED | |
| `queue_name` | VARCHAR(100) | |
| `due_date` | DATE | |
| `reviewer_summary` | TEXT | ASCII-only (sanitized) |
| `exception_summary` | TEXT | |
| timestamps + audit FKs | | |

### `reviews_comment` / `reviews_action` / `reviews_decision`

| Table | Purpose |
|---|---|
| `reviews_comment` | Reviewer comments on assignment |
| `reviews_action` | Manual actions taken (APPROVE_LINE, OVERRIDE_PRICE, etc.) |
| `reviews_decision` | Final decision (OneToOne with assignment) |

---

## 13. Invoice Posting

### `posting_invoice_posting`

1:1 with Invoice. Posting lifecycle state machine. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `invoice_id` | FK -> documents_invoice | CASCADE, OneToOne |
| `status` | VARCHAR(30) | See posting status lifecycle below |
| `review_queue` | VARCHAR(50) | `VENDOR_MAPPING_REVIEW`, `ITEM_MAPPING_REVIEW`, `TAX_REVIEW`, `COST_CENTER_REVIEW`, `PO_REVIEW`, `POSTING_OPS` |
| `erp_document_number` | VARCHAR(200) | Set after successful ERP submission |
| `submitted_at` | DATETIME | |
| `submitted_by_id` | FK -> accounts_user | SET NULL |
| `rejection_reason` | TEXT | |
| `payload_snapshot_json` | JSON | Final payload sent to ERP |
| `is_touchless` | BOOL | True = no human review needed |
| timestamps + audit FKs | | |

**Posting Status Lifecycle**:
`NOT_READY` -> `READY_FOR_POSTING` -> `MAPPING_IN_PROGRESS` -> `MAPPING_REVIEW_REQUIRED` | `READY_TO_SUBMIT` -> `SUBMISSION_IN_PROGRESS` -> `POSTED` | `POST_FAILED` -> `RETRY_PENDING` | `REJECTED` | `SKIPPED`

---

### `posting_core_posting_run`

One execution of the 9-stage posting pipeline. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `invoice_posting_id` | FK -> posting_invoice_posting | CASCADE |
| `invoice_id` | FK -> documents_invoice | SET NULL |
| `run_number` | INT UNSIGNED | Increments on retry |
| `status` | VARCHAR(30) | Run-level status |
| `stage_reached` | VARCHAR(50) | Last stage completed |
| `confidence_score` | FLOAT | 5-dimensional weighted score |
| `is_touchless` | BOOL | |
| `error_code` / `error_message` | VARCHAR / TEXT | |
| `erp_source_metadata_json` | JSON | Per-field ERP resolution provenance |
| `vendor_code_resolved` | VARCHAR(50) | |
| `trace_id` | VARCHAR(64) | |
| timestamps + audit FKs | | |

---

### Supporting posting tables

| Table | Purpose |
|---|---|
| `posting_core_field_value` | Resolved field values per posting run |
| `posting_core_line_item` | Mapped line items per posting run |
| `posting_core_issue` | Pipeline issues with severity (ERROR/WARN/INFO) |
| `posting_core_evidence` | Evidence items supporting mapping decisions |
| `posting_core_approval_record` | Governance mirror — PostingApprovalRecord |
| `posting_field_correction` | Per-field corrections during posting review |

---

## 14. ERP Integration

### `posting_core_erp_import_batch`

Import execution record. Current rows: **5**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `batch_type` | VARCHAR(20) | `VENDOR`, `ITEM`, `TAX`, `COST_CENTER`, `OPEN_PO` |
| `source_file_name` | VARCHAR(500) | |
| `source_file_path` | VARCHAR(1000) | |
| `source_as_of` | DATETIME | When ERP data was exported |
| `imported_at` | DATETIME | Auto |
| `row_count` / `valid_row_count` / `invalid_row_count` | INT UNSIGNED | |
| `checksum` | VARCHAR(128) | File SHA-256 |
| `status` | VARCHAR(20) | `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED` |
| `error_summary` | TEXT | |
| `imported_by_id` | FK -> accounts_user | SET NULL |
| `metadata_json` | JSON | |
| timestamps + audit FKs | | |

---

### ERP Reference Tables

All share the same pattern: FK to `erp_import_batch`, natural key field, `is_active`, `raw_json`.

| Table | Natural Key | Key Fields | Rows |
|---|---|---|---|
| `posting_core_erp_vendor_ref` | `vendor_code` | vendor_name, normalized_vendor_name, vendor_group, country_code, currency, payment_terms | 1 |
| `posting_core_erp_item_ref` | `item_code` | item_name, normalized_item_name, item_type, category, uom, tax_code | 1 |
| `posting_core_erp_tax_ref` | `tax_code` | tax_label, country_code, rate | 3 |
| `posting_core_erp_cost_center_ref` | `cost_center_code` | cost_center_name, department, business_unit | 1 |
| `posting_core_erp_po_ref` | `(po_number, po_line_number)` | vendor_code, item_code, description, quantity, unit_price, line_amount, currency, status, is_open | 1 |

> Duplicate handling: intra-file duplicates (same natural key appearing twice in one file) are rejected with an error message. Same-batch re-runs skip rows already present. Cross-batch: Vendor records are upserted in `vendors_vendor` so there is always exactly one `Vendor` per `vendor_code`.

---

### `posting_core_item_alias` / `posting_core_posting_rule`

| Table | Purpose |
|---|---|
| `posting_core_item_alias` | Item description variants -> ERPItemReference |
| `posting_core_posting_rule` | Configurable posting transformation rules |

---

### `erp_integration_connection`

ERP connector configuration. Current rows: **1**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `name` | VARCHAR(200) | UNIQUE |
| `connector_type` | VARCHAR(20) | `CUSTOM`, `DYNAMICS`, `ZOHO`, `SALESFORCE`, `SAP`, indexed |
| `base_url` | VARCHAR(500) | |
| `auth_config_json` | JSON | Stores env var names, not raw secrets |
| `status` | VARCHAR(20) | `ACTIVE`, `INACTIVE`, `ERROR` |
| `timeout_seconds` | INT UNSIGNED | Default: 30 |
| `is_default` | BOOL | Only one can be default |
| `metadata_json` | JSON | |
| timestamps + audit FKs | | |

---

### `erp_integration_cache`

TTL cache for ERP lookups. Current rows: **0**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `cache_key` | VARCHAR(255) | UNIQUE, indexed |
| `resolution_type` | VARCHAR(30) | `VENDOR`, `ITEM`, `TAX`, `COST_CENTER`, `PO`, `GRN`, `DUPLICATE` |
| `connector_name` | VARCHAR(200) | |
| `value_json` | JSON | Cached result |
| `expires_at` | DATETIME | indexed — TTL controlled by `ERP_CACHE_TTL_SECONDS` (default 3600s) |
| `source_type` | VARCHAR(20) | `API`, `DB_FALLBACK`, `CACHE` |
| `created_at` / `updated_at` | DATETIME | |

---

### `erp_integration_resolution_log` / `erp_integration_submission_log`

| Table | Purpose | Rows |
|---|---|---|
| `erp_integration_resolution_log` | Audit log for every ERP lookup attempt | 50 |
| `erp_integration_submission_log` | Audit log for every ERP submission attempt | 0 |

Both record: `resolution_type`/`submission_type`, `source_type`, `resolved`/`status`, `confidence`, `duration_ms`, related FKs to Invoice/ReconciliationResult/PostingRun.

---

## 15. Procurement

Quotation management and supplier analysis.

| Table | Purpose |
|---|---|
| `procurement_request` | Procurement request (RFQ) |
| `procurement_request_attribute` | Key-value attributes per request |
| `procurement_supplier_quotation` | Supplier quotation linked to request |
| `procurement_quotation_line_item` | Line items in a quotation |
| `procurement_analysis_run` | LLM-driven analysis execution |
| `procurement_recommendation_result` | Recommended supplier per analysis |
| `procurement_benchmark_result` / `_line` | Price benchmarking per quotation |
| `procurement_compliance_result` | Compliance checks against rules |
| `procurement_validation_rule_set` | Sets of validation rules |
| `procurement_validation_rule` | Individual validation rule |
| `procurement_validation_result` / `_item` | Validation run results |

---

## 16. Copilot

AI assistant sessions.

| Table | Purpose | Rows |
|---|---|---|
| `copilot_session` | Chat session (UUID PK) | By user, linked to case/invoice |
| `copilot_message` | Messages in a session (`USER`/`ASSISTANT`/`SYSTEM`) | |
| `copilot_session_artifact` | Structured artifacts attached to session | |

---

## 17. Audit & Observability

### `auditlog_audit_event`

Compliance-grade business event log. Current rows: **484**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `entity_type` | VARCHAR(100) | indexed — e.g. `Invoice`, `APCase`, `AgentRun` |
| `entity_id` | BIGINT | indexed |
| `action` | VARCHAR(50) | indexed — e.g. `APPROVED`, `REJECTED`, `STATUS_CHANGED` |
| `event_type` | VARCHAR(60) | indexed — 38+ event type codes |
| `event_description` | TEXT | |
| `old_values` / `new_values` | JSON | Before/after snapshot |
| `performed_by_id` | FK -> accounts_user | SET NULL |
| `performed_by_agent` | VARCHAR(100) | Agent type if system action |
| `ip_address` | GenericIPAddressField | |
| `user_agent` | VARCHAR(500) | |
| `metadata_json` | JSON | |
| `trace_id` / `span_id` / `parent_span_id` | VARCHAR | Distributed tracing |
| `invoice_id` / `case_id` / `reconciliation_result_id` / `review_assignment_id` / `agent_run_id` | BIGINT | Cross-reference IDs, indexed |
| `actor_email` | VARCHAR | RBAC snapshot |
| `actor_primary_role` | VARCHAR(50) | |
| `actor_roles_snapshot_json` | JSON | |
| `permission_checked` | VARCHAR(100) | |
| `permission_source` | VARCHAR(50) | `role_grant`, `admin_bypass`, `override` |
| `access_granted` | BOOL | |
| `status_before` / `status_after` | VARCHAR(50) | |
| `reason_code` | VARCHAR(50) | |
| `input_snapshot_json` / `output_snapshot_json` | JSON | |
| `duration_ms` | INT UNSIGNED | |
| `error_code` | VARCHAR(50) | |
| `is_redacted` | BOOL | PII-redacted entries |
| `created_at` / `updated_at` | DATETIME | |

---

### `auditlog_processing_log`

Operational observability log. Current rows: **469**

Similar structure to `auditlog_audit_event` but for operational (non-compliance) events: service calls, Celery tasks, API requests. Includes `task_name`, `task_id`, `service_name`, `endpoint_name`, `retry_count`, `exception_class`.

---

### `auditlog_file_status`

File processing stage tracker. Links `DocumentUpload` to stage+status pairs with timestamps.

---

## 18. Reports & Integrations

### `reports_generated`

Generated report records.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `report_type` | VARCHAR(50) | |
| `title` | VARCHAR(200) | |
| `generated_by_id` | FK -> accounts_user | SET NULL |
| `file_path` | VARCHAR(500) | |
| `parameters_json` | JSON | Report parameters |
| `row_count` | INT UNSIGNED | |
| timestamps + audit FKs | | |

---

### `integrations_config` / `integrations_log`

External integration configuration and execution log (Google Drive, OneDrive, email ingestion).

---

## 19. Core Shared

### `core_prompt_template`

LLM prompt templates managed via Admin. Current rows: **12**

| Column | Type | Notes |
|---|---|---|
| `id` | BIGINT PK | |
| `slug` | SlugField(120) | UNIQUE, indexed — used to fetch in code |
| `name` | VARCHAR(200) | |
| `category` | VARCHAR(50) | indexed — `extraction`, `reconciliation`, `agent`, `copilot` |
| `content` | TEXT | Template string with `{variable}` placeholders |
| `description` | TEXT | |
| `is_active` | BOOL | indexed |
| `version` | INT UNSIGNED | |
| `created_at` / `updated_at` | DATETIME | |

**Usage**: `PromptTemplate.objects.get(slug="invoice_extraction").render(ocr_text=..., vendor_name=...)`

---

## 20. Status Enumerations

### Invoice Status

| Value | Description |
|---|---|
| `UPLOADED` | File received, extraction not started |
| `EXTRACTION_IN_PROGRESS` | LLM extraction running |
| `EXTRACTED` | Extraction completed |
| `VALIDATED` | Field validation passed |
| `INVALID` | Validation failed |
| `PENDING_APPROVAL` | Awaiting human approval gate |
| `READY_FOR_RECON` | Approved, queued for reconciliation |
| `RECONCILED` | Reconciliation completed |
| `FAILED` | Processing error |

### Reconciliation Match Status

| Value | Description |
|---|---|
| `MATCHED` | All lines within strict tolerance |
| `PARTIAL_MATCH` | Some lines within tolerance, some outside |
| `UNMATCHED` | PO not found or no lines match |
| `REQUIRES_REVIEW` | Exceptions require human decision |
| `ERROR` | Processing error |

### Posting Status

| Value | Description |
|---|---|
| `NOT_READY` | Invoice not yet approved/reconciled |
| `READY_FOR_POSTING` | Eligible, awaiting pipeline trigger |
| `MAPPING_IN_PROGRESS` | Stage 3: mapping running |
| `MAPPING_REVIEW_REQUIRED` | Low-confidence mappings need review |
| `READY_TO_SUBMIT` | Payload built, approved |
| `SUBMISSION_IN_PROGRESS` | ERP call in progress |
| `POSTED` | Successfully submitted to ERP |
| `POST_FAILED` | ERP submission failed |
| `RETRY_PENDING` | Queued for retry |
| `REJECTED` | Manually rejected |
| `SKIPPED` | Not applicable (e.g. duplicate) |

### Agent Run Status

| Value | Description |
|---|---|
| `PENDING` | Queued |
| `RUNNING` | Executing ReAct loop |
| `COMPLETED` | Successfully finished |
| `FAILED` | Error during execution |
| `SKIPPED` | Skipped by policy engine |

---

## 21. Index Reference

Key indexes beyond PKs and FKs:

| Table | Index Name | Columns | Purpose |
|---|---|---|---|
| `accounts_user` | `idx_user_role` | `(role)` | Filter by legacy role |
| `accounts_permission` | `idx_perm_module_action` | `(module, action)` | Permission lookup |
| `documents_invoice` | — | `(invoice_number)` | Dedup check |
| `documents_invoice` | — | `(po_number)` | PO matching |
| `documents_invoice` | — | `(status)` | Queue queries |
| `documents_invoice` | — | `(is_duplicate)` | Duplicate filter |
| `documents_purchase_order` | — | `(po_number)` UNIQUE | Lookup by PO number |
| `documents_grn` | `idx_grn_number` | `(grn_number)` UNIQUE | GRN lookup |
| `documents_grn` | `idx_grn_po` | `(purchase_order_id)` | GRNs per PO |
| `documents_upload` | — | `(file_hash)` | Duplicate file detection |
| `vendors_vendor` | `idx_vendor_code` | `(code)` | ERP code lookup |
| `vendors_vendor` | `idx_vendor_norm_name` | `(normalized_name)` | Name matching |
| `posting_core_vendor_alias` | — | `(normalized_alias)` | Alias resolution |
| `posting_core_erp_vendor_ref` | `idx_vref_code` | `(vendor_code)` | ERP ref lookup |
| `reconciliation_result` | — | `(match_status)` | Queue by status |
| `agents_run` | — | `(agent_type)` | Filter by type |
| `agents_orchestration_run` | — | `(trace_id)` | Trace lookup |
| `auditlog_audit_event` | — | `(entity_type, entity_id)` | Entity history |
| `auditlog_audit_event` | — | `(event_type)` | Event type filter |
| `auditlog_audit_event` | — | `(invoice_id)`, `(case_id)`, `(reconciliation_result_id)` | Cross-ref lookups |
| `erp_integration_cache` | — | `(cache_key)` UNIQUE | Cache hit |
| `erp_integration_cache` | — | `(expires_at)` | TTL expiry scan |
| `cases_apcase` | — | `(status, processing_path)` | Queue by path+status |
| `cases_apcase` | — | `(assigned_to_id, status)` | User workqueue |
