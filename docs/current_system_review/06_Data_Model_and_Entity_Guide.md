# 06 — Data Model and Entity Guide

**Generated**: 2026-04-09 | **Method**: Code-first inspection of all major models.py files  
**Confidence**: High for inspected models including procurement core entities; Medium for copilot and benchmarking-only internals not fully traversed

---

## 1. Core Base Classes

### `BaseModel` (`apps/core/models.py`)
All domain entities inherit from this:
```
pk (BigAutoField)
created_at (DateTimeField, auto_now_add)
updated_at (DateTimeField, auto_now)
is_active (BooleanField, default=True, db_index=True)
```

### `TimestampMixin`
Lightweight version without `is_active`:
```
created_at (DateTimeField, auto_now_add)
updated_at (DateTimeField, auto_now)
```

### Multi-tenancy Pattern
Every model with tenant scope has:
```python
tenant = ForeignKey("accounts.CompanyProfile", on_delete=CASCADE, null=True, blank=True, db_index=True)
```

---

## 2. Entity Reference

### `accounts.CompanyProfile` — Tenant
| Field | Type | Notes |
|-------|------|-------|
| name | CharField | Organization name |
| slug | SlugField | URL-safe identifier |
| (additional fields inferred) | — | Billing, address, config |

**Cross-cutting**: Every major table has a `tenant` FK to this model. `TenantMiddleware` sets `request.tenant` per request.

---

### `accounts.User` — Custom User
Extends `AbstractBaseUser`:
| Field | Type | Notes |
|-------|------|-------|
| email | EmailField (unique) | Login identifier |
| role | CharField | Legacy single-role field (synced from primary RBAC role) |
| company | FK to CompanyProfile | User's tenant |
| (standard auth fields) | — | is_active, is_staff, etc. |

**RBAC extension**: `UserRole`, `UserPermissionOverride` in `rbac_models.py` provide the full RBAC layer.

---

### `vendors.Vendor` — Vendor Master
| Field | Type | Notes |
|-------|------|-------|
| code | CharField | Short vendor code |
| name | CharField | Full name |
| normalized_name | CharField | Lowercased, stripped for matching |
| is_active | BooleanField | |
| tax_id | CharField | GST / VAT registration |
| (address, payment terms, etc.) | — | Inferred from domain |

**Alias matching**: `posting_core.VendorAliasMapping` stores alternate names → used by `VendorSearchTool`.

---

### `documents.DocumentUpload` — File Upload Record
| Field | Type | Notes |
|-------|------|-------|
| file | FileField | Local disk (dev only) |
| original_filename | CharField(500) | |
| file_size | PositiveIntegerField | Bytes |
| file_hash | CharField(64) | SHA-256 for deduplication |
| document_type | CharField | INVOICE / CREDIT_NOTE / etc. |
| processing_state | CharField | QUEUED / PROCESSING / COMPLETED / FAILED |
| blob_path / blob_url | CharField | Azure Blob Storage coordinates |
| uploaded_by | FK to User | |
| processing_message | TextField | Error or status message |

---

### `documents.Invoice` — Invoice Header
| Field | Type | Notes |
|-------|------|-------|
| document_upload | FK to DocumentUpload | |
| vendor | FK to Vendor (nullable) | Resolved from extraction + vendor search |
| raw_* fields (11) | CharField | Exact LLM extraction output |
| invoice_number | CharField | Normalized |
| normalized_invoice_number | CharField | For deduplication |
| invoice_date, due_date | DateField | Normalized |
| po_number / normalized_po_number | CharField | For PO matching |
| currency | CharField | 3-char ISO |
| subtotal, tax_amount, total_amount | DecimalField(18,2) | Normalized amounts |
| tax_percentage | DecimalField(7,4) | e.g. 18.0 for 18% |
| tax_breakdown | JSONField | {cgst, sgst, igst, vat} |
| vendor_tax_id | CharField | GSTIN / VAT number |
| buyer_name | CharField | Billed-to entity |
| extraction_confidence | FloatField | Overall extraction quality |
| extraction_raw_json | JSONField | Full LLM extraction output |
| status | CharField | PENDING / PROCESSING / READY_FOR_RECON / RECONCILED / etc. |
| is_duplicate | BooleanField | |

**Indexes**: invoice_number, normalized_invoice_number, po_number, normalized_po_number

---

### `documents.PurchaseOrder` — PO Header
| Field | Type | Notes |
|-------|------|-------|
| po_number | CharField | Original PO number |
| normalized_po_number | CharField | Normalized for matching |
| vendor | FK to Vendor | |
| po_date | DateField | |
| currency | CharField | |
| total_amount, tax_amount | DecimalField | |
| status | CharField | OPEN / PARTIALLY_INVOICED / CLOSED |
| (business_unit, location, category) | CharField | For mode resolution policy matching |

**Line items**: `POLineItem` (FK to PurchaseOrder): line_number, item_code, description, quantity, unit_price, tax_amount, line_amount, unit_of_measure

---

### `documents.GoodsReceiptNote` — GRN Header
| Field | Type | Notes |
|-------|------|-------|
| purchase_order | FK to PurchaseOrder | |
| grn_number | CharField | |
| receipt_date | DateField | |
| warehouse | CharField | |
| status | CharField | RECEIVED / PARTIAL / REJECTED |

**Line items**: `GRNLineItem`: line_number, item_code, description, quantity_received, quantity_accepted, quantity_rejected

---

### `reconciliation.ReconciliationResult` — Match Outcome
| Field | Type | Notes |
|-------|------|-------|
| invoice | FK to Invoice | |
| purchase_order | FK to PurchaseOrder (nullable) | |
| run | FK to ReconciliationRun | |
| match_status | CharField | MATCHED / PARTIAL_MATCH / UNMATCHED / ERROR |
| reconciliation_mode | CharField | TWO_WAY / THREE_WAY / NON_PO |
| vendor_match / currency_match / po_total_match | BooleanField | Header match flags |
| total_amount_difference | DecimalField | |
| grn_available / grn_fully_received | BooleanField | 3-way flags |
| extraction_confidence | FloatField | From extraction stage |
| deterministic_confidence | FloatField | From matching engine |
| requires_review | BooleanField | Triggers review workflow |
| summary | TextField | Human-readable summary |
| line_match_data / header_match_data | JSONField | Detailed matching evidence |

---

### `reconciliation.ReconciliationException` — Single Exception
| Field | Type | Notes |
|-------|------|-------|
| result | FK to ReconciliationResult | |
| exception_type | CharField | e.g. AMOUNT_MISMATCH, QTY_MISMATCH |
| severity | CharField | LOW / MEDIUM / HIGH / CRITICAL |
| message | TextField | Human-readable exception description |
| field_name | CharField | Which field caused the exception |
| invoice_value / po_value / grn_value | DecimalField | Comparison values |
| tolerance_pct | FloatField | Applied tolerance |
| resolved | BooleanField | Whether exception was resolved |

---

### `cases.APCase` — Central Business Object
| Field | Type | Notes |
|-------|------|-------|
| case_number | CharField (unique) | AP-NNNNNN format |
| document_upload | FK to DocumentUpload | |
| invoice | OneToOneField to Invoice | Set after extraction |
| vendor | FK to Vendor | |
| purchase_order | FK to PurchaseOrder | |
| reconciliation_result | FK to ReconciliationResult | |
| review_assignment | FK to ReviewAssignment | |
| source_channel | CharField | WEB_UPLOAD / API / BULK |
| invoice_type | CharField | GOODS / SERVICE / TRAVEL / UNKNOWN |
| processing_path | CharField | TWO_WAY / THREE_WAY / NON_PO / UNRESOLVED |
| status | CharField | Full state machine status |
| current_stage | CharField | Current stage type |
| priority | IntegerField | |
| is_active | BooleanField | Soft delete via SoftDeleteMixin |
| stages | related manager → APCaseStage | |

---

### `procurement.ProcurementRequest` — Procurement Workspace Root
| Field | Type | Notes |
|-------|------|-------|
| tenant | FK to CompanyProfile | Explicit multi-tenant scope |
| request_id | UUIDField | Public identifier |
| title / description | CharField / TextField | Request summary |
| domain_code / schema_code | CharField | Domain-specific routing and form schema |
| request_type | CharField | RECOMMENDATION / BENCHMARK / BOTH |
| status | CharField | `PENDING_RFQ` / `READY_RFQ` / `COMPLETED` / `FAILED` |
| geography_country / geography_city | CharField | Regional context for recommendation and market intelligence |
| currency | CharField(3) | |
| assigned_to | FK to User | Optional procurement owner |
| trace_id | CharField | Trace correlation |
| uploaded_document | FK to DocumentUpload | Source RFQ / requirement document for prefill |
| source_document_type | CharField | RFQ / BOQ / PROPOSAL / etc. |
| prefill_status / prefill_confidence | CharField / FloatField | PDF-led intake state |
| prefill_payload_json | JSONField | Extracted request payload awaiting confirmation |
| duplicate_of / is_duplicate | FK / BooleanField | Duplicate request detection |

Related entities:
- `ProcurementRequestAttribute` — dynamic typed attributes with normalized value, extraction source, confidence
- `SupplierQuotation` — vendor quotation linked to the request
- `AnalysisRun` — recommendation / benchmark / validation execution history

### `procurement.SupplierQuotation` — Procurement Quotation Header
| Field | Type | Notes |
|-------|------|-------|
| tenant | FK to CompanyProfile | |
| request | FK to ProcurementRequest | Parent request |
| vendor_name / quotation_number | CharField | Supplier identifier |
| quotation_date | DateField | |
| total_amount / currency | Decimal / CharField | |
| uploaded_document | FK to DocumentUpload | Uploaded quotation source |
| extraction_status / extraction_confidence | CharField / FloatField | OCR/LLM extraction state |
| prefill_status | CharField | NOT_STARTED / IN_PROGRESS / COMPLETED / FAILED / REVIEW_PENDING |
| prefill_payload_json | JSONField | Extracted payload before user confirmation |

### `procurement.QuotationLineItem` — Quotation Detail Row
| Field | Type | Notes |
|-------|------|-------|
| quotation | FK to SupplierQuotation | Parent quotation |
| line_number | PositiveIntegerField | Unique per quotation |
| description / normalized_description | TextField | Raw and normalized text |
| category_code | CharField | Category for validation / benchmark grouping |
| quantity / unit / unit_rate / total_amount | Decimal / CharField | Commercial fields |
| brand / model | CharField | Product detail |
| extraction_confidence / extraction_source | Float / CharField | Prefill provenance |

### `procurement.AnalysisRun` — Procurement Execution Record
| Field | Type | Notes |
|-------|------|-------|
| run_id | UUIDField | Public identifier |
| request | FK to ProcurementRequest | Parent request |
| run_type | CharField | RECOMMENDATION / BENCHMARK / VALIDATION |
| status | CharField | QUEUED / RUNNING / COMPLETED / FAILED |
| started_at / completed_at | DateTimeField | Lifecycle timing |
| triggered_by | FK to User | Human actor if present |
| input_snapshot_json | JSONField | Frozen run input |
| output_summary | TextField | Human-readable result summary |
| confidence_score | FloatField | |
| trace_id / error_message | CharField / TextField | Trace and failure metadata |
| thought_process_log | JSONField | Validation flow persists staged reasoning here |

### `procurement.RecommendationResult` / `ComplianceResult`
- `RecommendationResult` stores recommended option, reasoning summary/details, constraints, compliance status, and full output payload
- `ComplianceResult` stores rules checked, violations, and remediation recommendations for a run

### `procurement.BenchmarkResult`
Current production-facing benchmark persistence is header-level:
- `run`, `quotation`
- `total_quoted_amount`, `total_benchmark_amount`
- `variance_pct`, `risk_level`
- `summary_json`

Live BENCHMARK task execution currently writes this model via `apps.benchmarking.services.procurement_cost_service`, which acts as a compatibility bridge and does not yet populate a full corridor-analysis result set.

### `procurement.ValidationRuleSet` / `ValidationRule` / `ValidationResult`
- `ValidationRuleSet` and `ValidationRule` define domain/schema-scoped deterministic checks
- `ValidationResult` stores overall status, completeness score, readiness flags, next action, and structured missing/warning/ambiguous lists
- `ValidationResultItem` stores each individual finding with category, severity, source type, and structured details

### `procurement.ProcurementAgentExecutionRecord`
Procurement-specific execution log for the Phase 1 compatibility bridge:
- `run`, `agent_type`, `status`
- `confidence_score`, `reasoning_summary`
- `input_snapshot`, `output_snapshot`, `error_message`
- `trace_id`, `span_id`
- `actor_user_id`, `actor_primary_role`

### `procurement.MarketIntelligenceSuggestion` / `ExternalSourceRegistry`
- `MarketIntelligenceSuggestion` stores provider/model metadata, structured suggestion payload, citations, AI summary, and trace linkage
- `ExternalSourceRegistry` is the allow-list and priority registry used to constrain discovery sources for procurement market-intelligence flows

---

### `agents.AgentRun` — Agent Execution Record
Key fields (full model in 03_Agent_Architecture doc):
- `agent_type`, `agent_definition`, `reconciliation_result`, `document_upload`
- `status` (PENDING/RUNNING/COMPLETED/FAILED)
- `input_payload`, `output_payload` (JSONField)
- `confidence`, `summarized_reasoning`
- `trace_id`, `span_id`, `prompt_version`
- RBAC snapshot: `actor_primary_role`, `actor_roles_snapshot_json`, `permission_source`, `access_granted`
- Token usage: `prompt_tokens`, `completion_tokens`, `total_tokens`
- Cost: `actual_cost_usd`, `cost_currency`

---

### `agents.DecisionLog` — Decision Audit Trail
| Field | Type | Notes |
|-------|------|-------|
| agent_run | FK to AgentRun (nullable) | Null for human decisions |
| decision_type | CharField | e.g. path_selected, mode_resolved, match_determined |
| decision | CharField(500) | The decision made |
| rationale | TextField | Why this decision was made |
| confidence | FloatField | |
| deterministic_flag | BooleanField | True = rule-based, False = LLM |
| rule_name / rule_version | CharField | Traceability for deterministic decisions |
| policy_code / policy_version | CharField | Policy traceability |
| prompt_template_id / prompt_version | Mixed | LLM prompt traceability |
| config_snapshot_json | JSONField | Config values at decision time |
| actor_user_id / actor_primary_role | Mixed | Human actor if applicable |
| trace_id / span_id | CharField | Distributed trace linkage |
| invoice_id / case_id / reconciliation_result_id | BigIntegerField | Cross-references |

---

### `auditlog.AuditEvent` — Compliance Audit Log
| Field | Type | Notes |
|-------|------|-------|
| entity_type / entity_id | CharField / BigInt | What was changed |
| action | CharField | created / updated / status_change / etc. |
| old_values / new_values | JSONField | Before/after snapshot |
| performed_by | FK to User | |
| performed_by_agent | CharField | Agent name if system action |
| event_type | CharField | Typed event code (38+ types) |
| event_description | TextField | |
| trace_id / span_id / parent_span_id | CharField | Distributed trace |
| actor_primary_role / actor_roles_snapshot_json | Mixed | RBAC at action time |
| permission_checked / permission_source / access_granted | Mixed | Auth decision |
| status_before / status_after | CharField | State transition |
| input_snapshot_json / output_snapshot_json | JSONField | Redacted payloads |
| invoice_id / case_id / reconciliation_result_id / review_assignment_id / agent_run_id | BigInt | Cross-references |

---

## 3. Entity Lifecycle / Status Fields

| Entity | Status Field | Values |
|--------|-------------|--------|
| DocumentUpload | processing_state | QUEUED → PROCESSING → COMPLETED / FAILED |
| Invoice | status | PENDING → PROCESSING → READY_FOR_RECON → RECONCILED → APPROVED / REJECTED |
| ReconciliationResult | match_status | MATCHED / PARTIAL_MATCH / UNMATCHED / ERROR |
| AgentRun | status | PENDING → RUNNING → COMPLETED / FAILED |
| AgentOrchestrationRun | status | PLANNED → RUNNING → COMPLETED / PARTIAL / FAILED |
| APCase | status | (full state machine — see 05_Features_and_Workflows.md) |
| ExtractionApproval | (approval status) | PENDING / APPROVED / REJECTED |
| ERP Posting | (posting status) | PROPOSED → REVIEW_REQUIRED → READY_TO_SUBMIT → SUBMITTED |
| ProcurementRequest | status | PENDING_RFQ → READY_RFQ → COMPLETED / FAILED |
| Procurement AnalysisRun | status | QUEUED → RUNNING → COMPLETED / FAILED |
| SupplierQuotation | prefill_status | NOT_STARTED → IN_PROGRESS → REVIEW_PENDING / COMPLETED / FAILED |

---

## 4. Cross-App Entity Flow

```
DocumentUpload
  → Invoice (1:1 after extraction)
  → APCase (1:1 via Invoice)
  → ReconciliationResult (1:1 per Invoice per Run)
       → ReconciliationException (1:N)
       → AgentRun (1:N via reconciliation_result FK)
            → AgentRecommendation (1:N)
            → DecisionLog (1:N)
            → AgentStep (1:N)
       → AgentOrchestrationRun (1:N)
  → ReviewAssignment (0:1 per APCase)
  → APCaseDecision (1:N per APCase)
  → AuditEvent (N per entity_id/entity_type)
```
