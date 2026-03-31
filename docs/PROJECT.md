# 3-Way PO Reconciliation Platform — Comprehensive Project Documentation

> **Version**: 2.0 · **Last Updated**: March 2026  
> **Stack**: Django 4.2 · MySQL · Celery + Redis · Azure OpenAI · Azure Document Intelligence · Bootstrap 5

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Technology Stack](#3-technology-stack)
4. [Application Structure](#4-application-structure)
5. [Data Models](#5-data-models)
6. [Business Enumerations](#6-business-enumerations)
7. [Document Processing Pipeline](#7-document-processing-pipeline)
8. [Reconciliation Engine](#8-reconciliation-engine)
9. [AI Agent System](#9-ai-agent-system)
10. [Case Management Platform](#10-case-management-platform)
11. [Review Workflow](#11-review-workflow)
12. [Dashboard & Analytics](#12-dashboard--analytics)
13. [Governance & Audit Trail](#13-governance--audit-trail)
14. [API Reference](#14-api-reference)
15. [Template Views & UI](#15-template-views--ui)
16. [Celery Tasks](#16-celery-tasks)
17. [Seed Data & Management Commands](#17-seed-data--management-commands)
18. [Prompt Registry](#18-prompt-registry)
19. [Configuration Reference](#19-configuration-reference)
20. [Security & Permissions](#20-security--permissions)
21. [Development Guide](#21-development-guide)
22. [Status & Roadmap](#22-status--roadmap)
23. [Invoice Posting Agent](#23-invoice-posting-agent)
24. [ERP Integration Layer](#24-erp-integration-layer)
25. [Procurement Intelligence Platform](#25-procurement-intelligence-platform)

---

## 1. Executive Summary

The **3-Way PO Reconciliation Platform** is an enterprise Django application that automates the matching of supplier Invoices against Purchase Orders (POs) and Goods Receipt Notes (GRNs). It combines deterministic rule-based matching with an AI agent pipeline powered by Azure OpenAI to resolve discrepancies, recommend actions, and route exceptions for human review.

### Key Capabilities

| Capability | Description |
|---|---|
| **Document Extraction** | Azure Document Intelligence OCR + GPT-4o structured extraction from PDF/image invoices |
| **2-Way Matching** | Invoice ↔ PO comparison (header + line-level) with configurable tolerances |
| **3-Way Matching** | Invoice ↔ PO ↔ GRN comparison including receipt verification |
| **Mode Resolution** | Policy-based → heuristic → default cascade to determine 2-way vs 3-way per invoice |
| **AI Agent Pipeline** | 8 specialized agents (ReAct loop with tool-calling) for exception analysis and resolution |
| **Auto-Close Logic** | Tiered tolerance bands (strict: 2%/1%/1%, auto-close: 5%/3%/3%) for automatic disposition |
| **Case Management** | Full AP case lifecycle with state machine, stage-based processing, and copilot chat |
| **Review Workflow** | Role-based assignment, review decision tracking, and field corrections |
| **Governance** | Complete audit trail with 38+ event types, agent trace visibility, and unified case timeline |
| **Non-PO Processing** | Validation pipeline for invoices without PO references (9 checks including spend category and policy) |
| **Invoice Posting Agent** | 9-stage pipeline mapping approved invoices to ERP-ready proposals (vendor, item, tax, cost-center resolution) |
| **ERP Integration Layer** | Connector framework (Dynamics, Zoho, Salesforce, Custom) with cache, resolver, and fallback chain |
| **Procurement Intelligence** | Product recommendation, should-cost benchmarking, and 6-dimension validation for procurement requests |
| **Multi-Country Extraction** | Jurisdiction-aware extraction platform with credit system, OCR cost tracking, and country pack governance |

### Business Flow Summary

```
Invoice Upload → OCR Extraction → Validation → Mode Resolution (2-Way/3-Way/Non-PO)
    → Deterministic Matching → Classification → Exception Building
    → AI Agent Pipeline (if non-MATCHED) → Recommendation
    → Review Workflow (if needed) → Approval/Rejection → Close
```

---

## 2. Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Bootstrap 5 UI Layer                        │
│  Dashboard │ Cases │ Documents │ Reconciliation │ Reviews │ Gov.   │
├─────────────────────────────────────────────────────────────────────┤
│                     Django Template Views                           │
│              (template_views.py per app)                            │
├─────────────────────────────────────────────────────────────────────┤
│                     REST API Layer (DRF)                            │
│  /api/v1/documents/ │ /reconciliation/ │ /agents/ │ /cases/ │ ...  │
├──────────────┬──────────────┬───────────────────┬───────────────────┤
│   Services   │  Agents      │   Orchestrators   │   State Machine  │
│  (stateless) │  (ReAct LLM) │  (case pipeline)  │  (transitions)   │
├──────────────┴──────────────┴───────────────────┴───────────────────┤
│                        ORM / Model Layer                            │
│  accounts │ documents │ reconciliation │ agents │ cases │ reviews   │
├─────────────────────────────────────────────────────────────────────┤
│              MySQL (utf8mb4) │ Redis │ Azure Blob │ Azure OpenAI    │
└─────────────────────────────────────────────────────────────────────┘
```

### Service Interconnection Map

```
ReconciliationRunnerService
├── POLookupService (via ERPResolutionService)
├── ReconciliationModeResolver
│   └── ReconciliationPolicy (DB)
├── ReconciliationExecutionRouter
│   ├── TwoWayMatchService
│   │   ├── HeaderMatchService → ToleranceEngine
│   │   └── LineMatchService → ToleranceEngine
│   └── ThreeWayMatchService
│       ├── HeaderMatchService → ToleranceEngine
│       ├── LineMatchService → ToleranceEngine
│       ├── GRNLookupService (via ERPResolutionService)
│       └── GRNMatchService
├── ClassificationService
├── ExceptionBuilderService
├── ReconciliationResultService
├── ReviewWorkflowService
└── AuditService

ERPResolutionService (shared gateway — used by reconciliation + posting + tools)
├── POResolver  → PODBFallback (MIRROR_DB: documents.PurchaseOrder)
│                             (DB_FALLBACK: posting_core.ERPPOReference)
├── GRNResolver → GRNDBFallback (MIRROR_DB: documents.GoodsReceiptNote)
├── VendorResolver  → VendorDBFallback (DB_FALLBACK: posting_core.ERPVendorReference)
├── ItemResolver    → ItemDBFallback
├── TaxResolver     → TaxDBFallback
├── CostCenterResolver → CostCenterDBFallback
└── DuplicateInvoiceResolver

AgentOrchestrator
├── PolicyEngine (deterministic agent plan)
├── AGENT_CLASS_REGISTRY (7 agent classes)
│   └── BaseAgent (ReAct loop)
│       ├── LLMClient (Azure OpenAI / OpenAI)
│       └── ToolRegistry (6 tools)
├── DeterministicResolver (cost-saving replacement for some agents)
├── AgentFeedbackService → ReconciliationRunnerService (re-reconcile)
├── DecisionLogService
├── RecommendationService
└── AgentTraceService

CaseOrchestrator
├── CaseStateMachine (30+ transitions)
├── StageExecutor (11 stage handlers)
│   ├── ReconciliationRunnerService
│   ├── NonPOValidationService
│   ├── AgentOrchestrator
│   ├── CaseAssignmentService
│   └── CaseSummaryService
├── CaseRoutingService
└── CaseCreationService

Extraction Pipeline
├── InvoiceUploadService
├── InvoiceExtractionAdapter (Azure DI + GPT-4o)
├── ExtractionParserService
├── NormalizationService
├── ValidationService
├── DuplicateDetectionService
└── InvoicePersistenceService
```

---

## 3. Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Framework** | Django 4.2+ | Web framework, ORM, admin |
| **API** | Django REST Framework | RESTful API endpoints |
| **Database** | MySQL (utf8mb4) | Primary data store |
| **Task Queue** | Celery + Redis | Async task processing |
| **OCR** | Azure Document Intelligence | PDF/image text extraction |
| **LLM** | Azure OpenAI (GPT-4o) | Invoice extraction + agent reasoning |
| **Storage** | Azure Blob Storage | Document file storage |
| **Frontend** | Bootstrap 5 + Django Templates | Server-rendered UI |
| **Auth** | Django auth (email-based) | Authentication & role-based access |
| **Fuzzy Matching** | thefuzz (fuzzywuzzy) | Description similarity scoring |
| **Data Processing** | pandas | Report generation, data analysis |
| **Testing** | pytest + factory-boy | Test framework (configured, not yet written) |

### Full Dependencies (requirements.txt)

```
Django>=4.2                 celery>=5.3            redis>=5.0
djangorestframework>=3.14   django-filter>=23.5    django-cors-headers>=4.3
mysqlclient>=2.2            openai>=1.12           azure-ai-documentintelligence>=1.0
azure-storage-blob>=12.19   python-dotenv>=1.0     thefuzz>=0.22
python-Levenshtein>=0.25    pandas>=2.2            openpyxl>=3.1
Pillow>=10.2                pytest>=8.0            pytest-django>=4.8
factory-boy>=3.3            gunicorn>=21.2         whitenoise>=6.6
```

---

## 4. Application Structure

The project contains **20 Django apps** under `apps/`:

| App | Purpose | Key Files |
|---|---|---|
| **accounts** | Custom User model (email login), enterprise RBAC (roles, permissions, overrides) | `models.py`, `rbac_models.py`, `rbac_services.py`, `managers.py`, `forms.py`, `template_views.py` |
| **agents** | AI agent orchestration, ReAct loop, 8 agent types | `models.py`, `services/` (10+ files) |
| **auditlog** | Audit events, processing logs, governance views, observability API | `models.py`, `services.py`, `timeline_service.py`, `serializers.py`, `views.py` |
| **cases** | AP Case lifecycle, state machine, stage orchestration | `models.py`, `orchestrators/`, `services/`, `state_machine/` |
| **copilot** | AP Copilot conversational assistant (read-only Q&A) | `models.py`, `services/copilot_service.py`, `template_views.py` |
| **core** | Base models, enums, constants, permissions, utilities, observability | `models.py`, `enums.py`, `constants.py`, `permissions.py`, `utils.py`, `trace.py`, `logging_utils.py`, `metrics.py`, `decorators.py` |
| **dashboard** | Analytics, KPIs, summary endpoints | `services.py`, `api_views.py` |
| **documents** | Invoice, PO, GRN data models & upload | `models.py`, `blob_service.py` |
| **erp_integration** | ERP connector framework + resolution chain (cache → API → DB fallback) | `models.py`, `services/connectors/`, `services/resolution/`, `services/db_fallback/`, `services/submission/` |
| **extraction** | OCR + LLM extraction pipeline (8 services), approval gate, bulk extraction | `services/`, `tasks.py`, `template_views.py` |
| **extraction_configs** | Extraction configuration metadata and runtime settings | `models.py`, `services/` |
| **extraction_core** | Multi-country extraction platform: 13 models, 30 service classes, 60+ API endpoints, jurisdiction resolution, schema-driven extraction, evidence capture, credit/OCR cost tracking | `models.py`, `services/`, `views.py` |
| **extraction_documents** | Extraction document management | `models.py`, `views.py` |
| **integrations** | External system connectors (PO/GRN API, RPA) | `models.py`, `contracts.py` |
| **posting** | Invoice posting business layer: lifecycle state, review queues, user actions, templates | `models.py`, `services/`, `tasks.py`, `template_views.py` |
| **posting_core** | Invoice posting platform layer: 9-stage pipeline, mapping engine, ERP reference import, governance trail | `models.py`, `services/`, `views.py` |
| **procurement** | Procurement Intelligence Platform: recommendation, benchmarking, validation, quotation extraction | `models.py`, `services/`, `agents/`, `tasks.py`, `template_views.py` |
| **reconciliation** | Matching engine (14 services), tolerance, classification | `services/` (14 files), `tasks.py` |
| **reports** | Report generation tracking | `models.py` |
| **reviews** | Review assignment, decisions, comments | `models.py`, `services.py` |
| **tools** | Agent tool registry (6 tools) | `registry/base.py`, `registry/tools.py` |
| **vendors** | Vendor master data, aliases, vendor list/detail UI | `models.py`, `template_views.py` |

### File Organization Convention

| What | Where |
|---|---|
| Models | `apps/<app>/models.py` |
| DRF Serializers | `apps/<app>/serializers.py` |
| API Views (DRF) | `apps/<app>/views.py` |
| Template Views | `apps/<app>/template_views.py` |
| API URL routes | `apps/<app>/api_urls.py` → `/api/v1/<app>/` |
| Template URL routes | `apps/<app>/urls.py` → top-level |
| Celery Tasks | `apps/<app>/tasks.py` |
| Business Logic | `apps/<app>/services/` or `apps/<app>/services.py` |
| Agent Guardrails | `apps/agents/services/guardrails_service.py` |
| Enums | `apps/core/enums.py` |
| Permissions | `apps/core/permissions.py` |
| Utilities | `apps/core/utils.py` |
| ERP Connectors | `apps/erp_integration/services/connectors/` |
| ERP Resolvers | `apps/erp_integration/services/resolution/` |
| ERP DB Fallbacks | `apps/erp_integration/services/db_fallback/` |
| Posting Business Logic | `apps/posting/services/` |
| Posting Core Pipeline | `apps/posting_core/services/` |
| Posting ERP Reference Models | `apps/posting_core/models.py` |
| Posting Import Pipeline | `apps/posting_core/services/import_pipeline/` |
| Admin | `apps/<app>/admin.py` |
| Templates | `templates/<app>/` |
| Static files | `static/css/`, `static/js/` |
| Config | `config/settings.py`, `config/urls.py`, `config/celery.py` |

---

## 5. Data Models

### 5.1 Core Base Models (`apps/core/models.py`)

All business entities inherit from `BaseModel`:

```
BaseModel
├── TimestampMixin (created_at, updated_at — auto-managed)
└── AuditMixin (created_by, updated_by — FK to User)
```

Additional mixins:
- **SoftDeleteMixin** — `is_active` flag; never hard-delete business entities
- **NotesMixin** — `notes` TextField for free-form annotation

**PromptTemplate** — Versioned LLM prompt templates with `{variable}` placeholders, organized by category (extraction, agent, case).

### 5.2 Accounts (`apps/accounts/models.py`, `rbac_models.py`)

| Model | Fields | Notes |
|---|---|---|
| **User** | email (login), first_name, last_name, role (legacy), is_active, is_staff, department | Custom model; RBAC helpers: `get_primary_role()`, `get_all_roles()`, `has_permission()`, `get_effective_permissions()`, `clear_permission_cache()`, `sync_legacy_role_field()` |
| **Role** | code, name, description, is_system_role, is_active, rank | 6 system roles seeded; supports custom roles |
| **Permission** | code (e.g. `invoices.view`), name, module, action, is_active | 40 permissions across 14 modules |
| **RolePermission** | role FK, permission FK, is_allowed | Many-to-many with explicit allow flag; unique_together |
| **UserRole** | user FK, role FK, is_primary, assigned_by, expires_at, is_active | Multi-role with expiry; `is_expired`/`is_effective` properties |
| **UserPermissionOverride** | user FK, permission FK, override_type (ALLOW/DENY), reason, assigned_by, expires_at | Per-user overrides with audit trail |
| **MenuConfig** | label, icon_class, url_name, required_permission, parent FK, order, is_separator | Dynamic menu items (future use) |

**Permission Resolution Order**: Admin bypass → User DENY overrides → User ALLOW overrides → Role permissions

Legacy roles: `ADMIN`, `AP_PROCESSOR`, `REVIEWER`, `FINANCE_MANAGER`, `AUDITOR` — synced to new RBAC UserRole table

### 5.3 Documents (`apps/documents/models.py`)

| Model | Key Fields | Relationships |
|---|---|---|
| **DocumentUpload** | file, original_filename, file_size, file_hash (SHA-256), content_type, document_type, processing_state, blob_name/container/url | — |
| **Invoice** | raw_* fields (vendor_name, vendor_tax_id, buyer_name, invoice_number, invoice_date, due_date, po_number, currency, subtotal, tax_amount, total_amount), normalized fields (invoice_number, invoice_date, due_date, po_number, currency, subtotal, tax_amount, tax_percentage, tax_breakdown `{cgst,sgst,igst,vat}`, total_amount, vendor_tax_id, buyer_name), status, extraction_confidence, is_duplicate, duplicate_of | FK: document_upload, vendor, created_by; migration 0009 added due_date, vendor_tax_id, buyer_name, tax_percentage, tax_breakdown |
| **InvoiceLineItem** | raw & normalized qty/unit_price/tax/line_amount, description, item_code, extraction_confidence, item_category, is_service_item, is_stock_item | FK: invoice |
| **PurchaseOrder** | po_number, po_date, currency, total_amount, tax_amount, status, buyer_name, department | FK: vendor |
| **PurchaseOrderLineItem** | line_number, item_code, description, quantity, unit_price, tax_amount, line_amount, unit_of_measure, item_category, is_service_item, is_stock_item | FK: purchase_order |
| **GoodsReceiptNote** | grn_number, receipt_date, status, warehouse, receiver_name | FK: purchase_order, vendor |
| **GRNLineItem** | line_number, item_code, description, quantity_received/accepted/rejected, unit_of_measure | FK: goods_receipt_note, po_line |

#### Invoice Status Flow
```
UPLOADED -> EXTRACTION_IN_PROGRESS -> EXTRACTED -> VALIDATED -> PENDING_APPROVAL -> READY_FOR_RECON -> RECONCILED
                                   -> INVALID                -> (auto-approve)                       -> FAILED
                                                             -> INVALID (rejected)
```

- **PENDING_APPROVAL**: Human-in-the-loop gate after successful extraction. All valid extractions require approval (human or auto) before reconciliation.
- Auto-approval triggers when `EXTRACTION_AUTO_APPROVE_ENABLED=true` and confidence >= `EXTRACTION_AUTO_APPROVE_THRESHOLD`.

### 5.4 Extraction (`apps/extraction/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **ExtractionResult** | engine_name, engine_version, raw_response (JSON), confidence, duration_ms, success, error_message | FK: document_upload, invoice |
| **ExtractionApproval** | status (ExtractionApprovalStatus), reviewed_by, reviewed_at, rejection_reason, confidence_at_review, original_values_snapshot (JSON), fields_corrected_count, is_touchless | OneToOne: invoice; FK: extraction_result — human-in-the-loop gate |
| **ExtractionFieldCorrection** | entity_type (header/line_item), entity_id, field_name, original_value, corrected_value, corrected_by | FK: approval — granular correction tracking for analytics |

### 5.5 Vendors (`apps/vendors/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **Vendor** | code (unique), name, normalized_name, tax_id, address, country, currency, payment_terms, contact_email | SoftDeleteMixin |
| **VendorAlias** | alias_name, normalized_alias, source (manual/extraction/erp) | FK: vendor; unique_together: vendor + normalized_alias |

### 5.6 Reconciliation (`apps/reconciliation/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **ReconciliationConfig** | qty/price/amount tolerance %, auto-close thresholds, auto_close_on_match, enable_agents, extraction_confidence_threshold, default_reconciliation_mode, enable_mode_resolver, enable_grn_for_stock_items, enable_two_way_for_services | Singleton-style config |
| **ReconciliationPolicy** | policy_code, reconciliation_mode, vendor, invoice_type, item_category, business_unit, location_code, is_service/stock_invoice, priority, effective_from/to | Mode resolution rules; lower priority = higher precedence |
| **ReconciliationRun** | status, started_at, completed_at, total_invoices, matched/partial/unmatched/error/review counts, triggered_by, reconciliation_mode | FK: config |
| **ReconciliationResult** | match_status, requires_review, vendor_match, currency_match, po_total_match, total_amount_difference, grn_available, grn_fully_received, extraction/deterministic confidence, reconciliation_mode, mode_resolution_reason, summary | FK: run, invoice, purchase_order |
| **ReconciliationResultLine** | qty_invoice/po/received, qty_difference/within_tolerance, price_invoice/po, price_difference/within_tolerance, amount_invoice/po, amount_difference/within_tolerance, tax_invoice/po/difference, description_similarity | FK: result, invoice_line, po_line |
| **ReconciliationException** | exception_type, severity, message, details (JSON), applies_to_mode, resolved, resolved_by, resolved_at | FK: result |

#### Match Status Values
```
MATCHED | PARTIAL_MATCH | UNMATCHED | REQUIRES_REVIEW | ERROR
```

### 5.7 Agents (`apps/agents/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **AgentDefinition** | agent_type, name, description, purpose, entry_conditions, success_criteria, prohibited_actions, allowed_recommendation_types, default_fallback_recommendation, requires_tool_grounding, min_tool_calls, tool_failure_confidence_cap, output_schema_name, lifecycle_status, owner_team, capability_tags, domain_tags, config_json, allowed_tools, system_prompt, is_active, max_iterations | Registry of agent types; all fields are first-class DB columns (not in config_json) |
| **AgentOrchestrationRun** | reconciliation_result FK, status (PLANNED/RUNNING/COMPLETED/PARTIAL/FAILED), plan_source, plan_confidence, planned_agents (JSON), executed_agents (JSON), final_recommendation, final_confidence, skip_reason, error_message, actor_user_id, trace_id, started_at, completed_at, duration_ms | Top-level pipeline invocation record; duplicate-run guard (RUNNING blocks re-entry for same result) |
| **AgentRun** | agent_type, status, input/output_payload (JSON), confidence, summarized_reasoning, prompt_tokens/completion_tokens/total_tokens, duration_ms, trace_id, span_id, actor_primary_role, actor_roles_snapshot_json, permission_source, access_granted, invocation_reason, prompt_version, cost_estimate | FK: definition, reconciliation_result |
| **AgentStep** | step_number, action, input/output_data (JSON), duration_ms | FK: agent_run |
| **AgentMessage** | role (system/user/assistant/tool), content, tool_calls (JSON), tool_call_id, token_count | FK: agent_run |
| **DecisionLog** | decision_type, rationale, confidence, evidence_refs (JSON), recommendation_type, rule_name, rule_version, policy_code, trace_id, actor_primary_role | FK: agent_run, reconciliation_result |
| **AgentRecommendation** | recommendation_type, confidence, reasoning, evidence (JSON), accepted, accepted_by, accepted_at | FK: agent_run, reconciliation_result; UniqueConstraint on (result, type, run) |
| **AgentEscalation** | severity, reason, suggested_assignee_role, resolved, resolved_by | FK: agent_run, reconciliation_result |

#### Agent Run Status
```
PENDING -> RUNNING -> COMPLETED | FAILED | SKIPPED
```

### 5.8 Cases (`apps/cases/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **APCase** | case_number (AP-YYMMDD-NNNN), status, processing_path, priority, risk_score, extraction_confidence, requires_human_review, assigned_to, source_channel | FK: invoice, vendor, purchase_order, reconciliation_result, review_assignment |
| **APCaseStage** | stage_name, status, performed_by_type, performed_by_agent, retry_count, input/output_payload, started/completed_at | FK: case |
| **APCaseArtifact** | artifact_type, linked_object_type/id, payload (JSON), version | FK: case, stage |
| **APCaseDecision** | decision_type, decision_source, confidence, rationale, evidence (JSON) | FK: case, stage |
| **APCaseAssignment** | assignment_type, assigned_user, assigned_role, queue_name, status | FK: case |
| **APCaseSummary** | latest_summary, reviewer_summary, finance_summary, recommendation | FK: case (OneToOne) |
| **APCaseComment** | body, is_internal | FK: case, author |
| **APCaseActivity** | activity_type, actor, metadata (JSON) | FK: case |

#### Case Status Flow
```
NEW → INTAKE_IN_PROGRESS → EXTRACTION_IN_PROGRESS → PATH_RESOLUTION_IN_PROGRESS
    → PO_RETRIEVAL_IN_PROGRESS (or NON_PO_VALIDATION)
    → TWO_WAY_MATCHING / THREE_WAY_MATCHING
    → EXCEPTION_ANALYSIS_IN_PROGRESS → REVIEW_ROUTING
    → IN_REVIEW → CLOSED | REJECTED | ESCALATED | FAILED
```

### 5.9 Reviews (`apps/reviews/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **ReviewAssignment** | status, priority, due_date, notes | FK: reconciliation_result, assigned_to |
| **ReviewComment** | body, is_internal | FK: assignment, author |
| **ManualReviewAction** | action_type, field_name, old_value, new_value, reason | FK: assignment, performed_by |
| **ReviewDecision** | decision, reason, decided_at | OneToOne: assignment; FK: decided_by |

#### Review Status Flow
```
PENDING → ASSIGNED → IN_REVIEW → APPROVED | REJECTED | REPROCESSED
```

### 5.10 Supporting Models

| Model | App | Purpose |
|---|---|---|
| **ToolDefinition** | tools | Tool registry (name, schema, module_path) |
| **ToolCall** | tools | Tool invocation audit (input/output, duration, status) |
| **IntegrationConfig** | integrations | External endpoint config (PO_API, GRN_API, RPA) |
| **IntegrationLog** | integrations | Integration call audit log |
| **ProcessingLog** | auditlog | Operational logging |
| **AuditEvent** | auditlog | State change/governance events (38+ types) |
| **FileProcessingStatus** | auditlog | File upload lifecycle tracking |
| **GeneratedReport** | reports | Report generation tracking |

### 5.11 ERP Integration (`apps/erp_integration/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **ERPConnection** | connector_type, name, base_url, credentials_json, is_default, status, is_active | Active connector config; `ConnectorFactory.get_default_connector()` returns the active default |
| **ERPReferenceCacheRecord** | resolution_type, cache_key, result_json, expires_at | TTL-based lookup cache; expiry controlled by `ERP_CACHE_TTL_SECONDS` (default 3600s) |
| **ERPResolutionLog** | resolution_type, input_params, result_json, source (API/CACHE/DB_FALLBACK), duration_ms | Per-lookup audit trail |
| **ERPSubmissionLog** | submission_type, payload_json, response_json, status, erp_document_number, duration_ms | Per-submission audit trail |

ERP connector enums live in `apps/erp_integration/enums.py`: `ERPConnectorType`, `ERPConnectionStatus`, `ERPSourceType`, `ERPResolutionType`, `ERPSubmissionType`, `ERPSubmissionStatus`.

### 5.12 Invoice Posting (`apps/posting/models.py`, `apps/posting_core/models.py`)

**Business layer (`apps/posting/`)**:

| Model | Key Fields | Notes |
|---|---|---|
| **InvoicePosting** | invoice (1:1), extraction_result, status (11 states), stage, posting_confidence, review_queue, is_touchless, mapping_summary_json, payload_snapshot_json, erp_document_number, retry_count | Primary lifecycle tracker; status: NOT_READY -> READY_FOR_POSTING -> MAPPING_IN_PROGRESS -> MAPPING_REVIEW_REQUIRED / READY_TO_SUBMIT -> SUBMISSION_IN_PROGRESS -> POSTED / POST_FAILED -> RETRY_PENDING / REJECTED / SKIPPED |
| **InvoicePostingFieldCorrection** | field_name, old_value, new_value, corrected_by, reason | Field correction audit trail during review |

**Platform layer (`apps/posting_core/`)**:

| Model | Key Fields | Notes |
|---|---|---|
| **PostingRun** | invoice, status (PENDING/RUNNING/COMPLETED/FAILED/CANCELLED), stage_reached, error_code, error_message, erp_source_metadata_json | Authoritative execution record per pipeline invocation |
| **PostingFieldValue** | field_name, resolved_value, source, confidence | Resolved header fields |
| **PostingLineItem** | line_number, item_code, vendor_code, tax_code, cost_center_code, quantity, amount | Resolved line items |
| **PostingIssue** | check_type, severity (INFO/WARNING/ERROR), message | Validation issues |
| **PostingEvidence** | field_name, evidence_type, evidence_value, source_ref | Source provenance for resolved values |
| **PostingApprovalRecord** | approved_by, approved_at, status, notes | Governance mirror (1:1 with PostingRun); sole writer: `PostingGovernanceTrailService` |
| **ERPVendorReference** | vendor_code, vendor_name, normalized_name | ERP vendor master (imported from Excel) |
| **ERPItemReference** | item_code, item_name, uom, tax_code, normalized_name | ERP item/material master |
| **ERPTaxCodeReference** | tax_code, rate, country_code, description | ERP tax code catalog |
| **ERPCostCenterReference** | cost_center_code, cost_center_name, department | ERP org structure |
| **ERPPOReference** | po_number, po_line, vendor_code, item_code, open_qty, open_amount | Open PO lines for matching |
| **ERPReferenceImportBatch** | batch_type, status, row_count, valid_row_count, invalid_row_count, checksum | Import batch metadata |
| **VendorAliasMapping** | alias_name, normalized_alias, erp_vendor_code | Vendor name variant -> ERP code |
| **ItemAliasMapping** | alias_description, normalized_alias, erp_item_code | Item description variant -> ERP code |
| **PostingRule** | rule_type (TAX_CODE/COST_CENTER/LINE_TYPE), condition, action_value | Configurable posting rules |

### 5.13 Procurement (`apps/procurement/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **ProcurementRequest** | request_id (UUID), title, description, domain_code, schema_code, request_type (RECOMMENDATION/BENCHMARK/BOTH), status (DRAFT -> READY -> PROCESSING -> COMPLETED/REVIEW_REQUIRED/FAILED), priority, currency, assigned_to, trace_id | Top-level business entity |
| **ProcurementRequestAttribute** | request FK, attribute_code, attribute_label, data_type, value_text/number/json, is_required, normalized_value | Dynamic key-value requirements; unique on (request, attribute_code) |
| **SupplierQuotation** | request FK, vendor_name, quotation_number, quotation_date, total_amount, currency, uploaded_document FK, extraction_status, extraction_confidence | Supplier quote linked to document upload |
| **QuotationLineItem** | quotation FK, line_number, description, normalized_description, category_code, quantity, unit, unit_rate, total_amount, brand, model, extraction_confidence | Per-line priced item; unique on (quotation, line_number) |
| **AnalysisRun** | run_id (UUID), request FK, run_type, status, started/completed_at, triggered_by, input_snapshot_json, output_summary, confidence_score, trace_id, error_message | Execution instance; one request can have many runs |
| **RecommendationResult** | run (1:1), recommended_option, reasoning_summary, reasoning_details_json, confidence_score, constraints_json, compliance_status, output_payload_json | Recommendation output |
| **BenchmarkResult** | run FK, quotation FK, total_quoted/benchmark_amount, variance_pct, risk_level, summary_json | Per-quotation benchmark header |
| **BenchmarkResultLine** | benchmark_result FK, quotation_line FK, benchmark_min/avg/max, quoted_value, variance_pct, variance_status, remarks | Per-line comparison |
| **ComplianceResult** | run (1:1), compliance_status, rules_checked_json, violations_json, recommendations_json | Compliance check output |
| **ValidationRuleSet** | domain_code, schema_code, rule_set_code, rule_set_name, validation_type, is_active, priority, config_json | Reusable validation rules per domain/schema |
| **ValidationResult** | run (1:1), overall_status (PASS/FAIL/NEEDS_REVIEW), score, dimensions_json, issues_json | Validation run output |
| **ValidationResultItem** | validation_result FK, dimension, status, message, detail_json | Per-dimension finding |

### Entity Relationship Summary

```
User (accounts)
  ├── has role: ADMIN | AP_PROCESSOR | REVIEWER | FINANCE_MANAGER | AUDITOR
  └── referenced by: Invoice.created_by, ReviewAssignment.assigned_to, APCase.assigned_to

Vendor ──< VendorAlias

DocumentUpload ── Invoice ──< InvoiceLineItem
                     ├── references: PurchaseOrder.po_number
                     └── status: InvoiceStatus enum

PurchaseOrder ──< PurchaseOrderLineItem
     └──< GoodsReceiptNote ──< GRNLineItem

ExtractionResult ── DocumentUpload + Invoice
ExtractionApproval ── Invoice (1:1) + ExtractionResult
     └──< ExtractionFieldCorrection (per-field correction audit)

ReconciliationConfig (tolerances & settings)
ReconciliationPolicy (mode rules per vendor/category)
ReconciliationRun ──< ReconciliationResult ──< ReconciliationResultLine
                                           ──< ReconciliationException

APCase ──< APCaseStage ──< APCaseArtifact
       ──< APCaseDecision
       ──< APCaseAssignment
       ── APCaseSummary (1:1)
       ──< APCaseComment
       ──< APCaseActivity

AgentDefinition
AgentRun ──< AgentStep, AgentMessage, DecisionLog
         ──< AgentRecommendation, AgentEscalation
         ── ReconciliationResult

ReviewAssignment ──< ReviewComment, ManualReviewAction
                 ── ReviewDecision (1:1)

ToolDefinition ──< ToolCall ── AgentRun
```

---

## 6. Business Enumerations

Core app enums live in `apps/core/enums.py` (25 classes). ERP-specific enums live in `apps/erp_integration/enums.py` (6 classes). Posting enums are inline in `apps/posting/models.py` and `apps/posting_core/models.py`. Key enums:

### Invoice & Documents
| Enum | Values |
|---|---|
| `InvoiceStatus` | UPLOADED, EXTRACTION_IN_PROGRESS, EXTRACTED, VALIDATED, INVALID, **PENDING_APPROVAL**, READY_FOR_RECON, RECONCILED, FAILED |
| `ExtractionApprovalStatus` | PENDING, APPROVED, REJECTED, AUTO_APPROVED |
| `InvoiceType` | PO_BACKED, NON_PO, UNKNOWN |
| `DocumentType` | INVOICE, PO, GRN |
| `FileProcessingState` | QUEUED, PROCESSING, COMPLETED, FAILED |

### Reconciliation
| Enum | Values |
|---|---|
| `ReconciliationMode` | TWO_WAY, THREE_WAY |
| `ReconciliationModeApplicability` | TWO_WAY, THREE_WAY, BOTH |
| `ReconciliationRunStatus` | PENDING, RUNNING, COMPLETED, FAILED, PARTIAL |
| `MatchStatus` | MATCHED, PARTIAL_MATCH, UNMATCHED, ERROR, REQUIRES_REVIEW |
| `ExceptionType` | PO_NOT_FOUND, VENDOR_MISMATCH, ITEM_MISMATCH, QTY_MISMATCH, PRICE_MISMATCH, TAX_MISMATCH, AMOUNT_MISMATCH, DUPLICATE_INVOICE, EXTRACTION_LOW_CONFIDENCE, CURRENCY_MISMATCH, LOCATION_MISMATCH, GRN_NOT_FOUND, RECEIPT_SHORTAGE, INVOICE_QTY_EXCEEDS_RECEIVED, OVER_RECEIPT, MULTI_GRN_PARTIAL_RECEIPT, RECEIPT_LOCATION_MISMATCH, DELAYED_RECEIPT |
| `ExceptionSeverity` | LOW, MEDIUM, HIGH, CRITICAL |

### Agents
| Enum | Values |
|---|---|
| `AgentType` | INVOICE_EXTRACTION, INVOICE_UNDERSTANDING, PO_RETRIEVAL, GRN_RETRIEVAL, RECONCILIATION_ASSIST, EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY |
| `AgentRunStatus` | PENDING, RUNNING, COMPLETED, FAILED, SKIPPED |
| `RecommendationType` | AUTO_CLOSE, SEND_TO_AP_REVIEW, SEND_TO_PROCUREMENT, SEND_TO_VENDOR_CLARIFICATION, REPROCESS_EXTRACTION, ESCALATE_TO_MANAGER |
| `ToolCallStatus` | REQUESTED, SUCCESS, FAILED |

### Reviews
| Enum | Values |
|---|---|
| `ReviewStatus` | PENDING, ASSIGNED, IN_REVIEW, APPROVED, REJECTED, REPROCESSED |
| `ReviewActionType` | APPROVE, REJECT, REQUEST_INFO, REPROCESS, ESCALATE, CORRECT_FIELD, ADD_COMMENT |

### Users & Roles
| Enum | Values |
|---|---|
| `UserRole` | ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR |

### Case Management
| Enum | Values |
|---|---|
| `ProcessingPath` | TWO_WAY, THREE_WAY, NON_PO, UNRESOLVED |
| `CaseStatus` | NEW, INTAKE_IN_PROGRESS, EXTRACTION_IN_PROGRESS, PATH_RESOLUTION_IN_PROGRESS, _(20 total statuses)_ |
| `CaseStageType` | INTAKE, EXTRACTION, PATH_RESOLUTION, PO_RETRIEVAL, TWO_WAY_MATCHING, THREE_WAY_MATCHING, GRN_ANALYSIS, NON_PO_VALIDATION, EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY, REVIEWER_COPILOT, APPROVAL, GL_CODING, POSTING |
| `CasePriority` | LOW, MEDIUM, HIGH, CRITICAL |
| `PerformedByType` | SYSTEM, DETERMINISTIC, AGENT, HUMAN |
| `DecisionType` | PATH_SELECTED, PO_LINKED, GRN_LINKED, MATCH_DETERMINED, EXCEPTION_CLASSIFIED, AUTO_CLOSED, SENT_TO_REVIEW, REVIEW_COMPLETED, ESCALATED, APPROVED, REJECTED, GL_CODE_PROPOSED |
| `ArtifactType` | EXTRACTION_RESULT, PO_LINK, GRN_LINK, RECONCILIATION_RESULT, VALIDATION_RESULT, AGENT_OUTPUT, REVIEW_DECISION, SUPPORTING_DOCUMENT, APPROVAL_PACKET, GL_CODING_PROPOSAL |

### Audit
| Enum | Values |
|---|---|
| `AuditEventType` | INVOICE_UPLOADED, EXTRACTION_COMPLETED, EXTRACTION_FAILED, VALIDATION_FAILED, RECONCILIATION_STARTED, RECONCILIATION_COMPLETED, AGENT_RECOMMENDATION_CREATED, REVIEW_ASSIGNED, REVIEW_APPROVED, REVIEW_REJECTED, FIELD_CORRECTED, RECONCILIATION_RERUN, AGENT_RUN_STARTED, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED, RECONCILIATION_MODE_RESOLVED, POLICY_APPLIED, MANUAL_MODE_OVERRIDE, GUARDRAIL_GRANTED, GUARDRAIL_DENIED, TOOL_CALL_AUTHORIZED, TOOL_CALL_DENIED, RECOMMENDATION_ACCEPTED, RECOMMENDATION_DENIED, AUTO_CLOSE_AUTHORIZED, AUTO_CLOSE_DENIED, SYSTEM_AGENT_USED, CASE_CLOSED, CASE_REJECTED, CASE_REPROCESSED, CASE_ESCALATED, CASE_FAILED, CASE_STATUS_CHANGED, POSTING_STARTED, POSTING_MAPPING_COMPLETED, POSTING_SUBMITTED, POSTING_SUCCEEDED, ERP_REFERENCE_IMPORT_COMPLETED, _(and more)_ |

### Invoice Posting
| Enum | Values |
|---|---|
| `InvoicePostingStatus` | NOT_READY, READY_FOR_POSTING, MAPPING_IN_PROGRESS, MAPPING_REVIEW_REQUIRED, READY_TO_SUBMIT, SUBMISSION_IN_PROGRESS, POSTED, POST_FAILED, REJECTED, RETRY_PENDING, SKIPPED |
| `PostingRunStatus` | PENDING, RUNNING, COMPLETED, FAILED, CANCELLED |
| `PostingStage` | ELIGIBILITY_CHECK, SNAPSHOT_BUILD, MAPPING, VALIDATION, CONFIDENCE, REVIEW_ROUTING, PAYLOAD_BUILD, SUBMISSION, FINALIZATION |
| `PostingReviewQueue` | ITEM_MAPPING_REVIEW, VENDOR_MAPPING_REVIEW, TAX_REVIEW, COST_CENTER_REVIEW, PO_REVIEW, POSTING_OPS |

### ERP Integration (`apps/erp_integration/enums.py`)
| Enum | Values |
|---|---|
| `ERPConnectorType` | CUSTOM, DYNAMICS, ZOHO, SALESFORCE |
| `ERPConnectionStatus` | ACTIVE, INACTIVE, ERROR |
| `ERPSourceType` | API, CACHE, DB_FALLBACK |
| `ERPResolutionType` | VENDOR, ITEM, TAX, COST_CENTER, PO, GRN, DUPLICATE_INVOICE |
| `ERPSubmissionType` | CREATE_INVOICE, PARK_INVOICE |
| `ERPSubmissionStatus` | PENDING, SUBMITTED, SUCCEEDED, FAILED |

---

## 7. Document Processing Pipeline

### 7.1 Upload Flow

```
User uploads PDF/image
    ↓
InvoiceUploadService
├── Validate file extension (.pdf, .png, .jpg, .jpeg, .tiff, .tif)
├── Validate file size (≤ 25 MB)
├── Compute SHA-256 hash
├── Persist DocumentUpload record
└── Upload to Azure Blob Storage (input/ folder)
    ↓
process_invoice_upload_task (Celery)
```

### 7.2 Extraction Pipeline

The extraction runs as a Celery task (`process_invoice_upload_task`). Orchestrated by `InvoiceExtractionAdapter` with the following stages:

**Stage 1 — OCR (`InvoiceExtractionAdapter`)**
- Azure Document Intelligence (primary) — PDF/image → raw text
- PyPDF2 native fallback when `EXTRACTION_OCR_ENABLED=false` or `ExtractionRuntimeSettings.ocr_enabled=false`
- Output: ocr_text, page count, duration_ms

**Stage 2 — Invoice Category Classification (`InvoiceCategoryClassifier`)** *(Phase 2)*
- Rule-based classifier: `goods` / `service` / `travel`
- Title-zone scoring (first 3 000 chars, full weight) + body scoring (0.4× weight)
- Defaults to `service` when confidence < 0.20 or ambiguous
- Output: `InvoiceCategoryResult` (category, confidence, signals, is_ambiguous)

**Stage 3 — Modular Prompt Composition (`InvoicePromptComposer`)** *(Phase 2)*
- Fetches `extraction.invoice_base` + category overlay + country/tax overlay from `PromptRegistry`
- `PromptRegistry` resolution: Langfuse (60s TTL) → DB (`PromptTemplate`) → hardcoded defaults
- Computes prompt_hash (sha256 first 16 chars) for Langfuse traceability
- 18 registered prompts (base + 3 category + 2 country + 12 agent/other)
- Output: `PromptComposition` (final_prompt, components dict, prompt_hash)

**Stage 4 — Invoice Extraction Agent (`InvoiceExtractionAgent`)**
- Single-shot LLM agent (no ReAct loop), `temperature=0`, `response_format=json_object`
- Receives composed prompt via `ctx.extra["composed_prompt"]`; falls back to `extraction.invoice_system` if absent
- Extracts: vendor_name, vendor_tax_id, buyer_name, invoice_number, invoice_date, due_date, po_number, currency, subtotal, tax_percentage, tax_amount, tax_breakdown (cgst/sgst/igst/vat), total_amount, document_type, line_items
- Full agent traceability: `AgentRun` + `AgentMessage` records

**Stage 5 — Response Repair (`ResponseRepairService`)** *(Phase 2)*
- Deterministic pre-parser correction layer; operates on a copy (never mutates input)
- 5 rules: (a) invoice_number exclusion (IRN, CART Ref, Hotel Booking ID, etc.) with OCR recovery; (b) tax_percentage recomputation from tax_amount/subtotal; (c) subtotal alignment with pre-tax line sums; (d) line-level tax allocation for service/travel; (e) travel line consolidation (Basic Fare + Hotel Taxes → Total Fare)
- Repair metadata embedded in `raw_json["_repair"]` for audit

**Step 6 — Parsing (`ExtractionParserService`)**
- Parses repaired JSON → `ParsedInvoice` dataclass
- Preserves raw values for auditability

**Step 7 — Normalization (`NormalizationService`)**
- Vendor name: lowercase, strip, collapse whitespace
- Invoice number: uppercase, strip spaces
- PO number: uppercase, strip leading zeros/prefixes, remove non-alphanumeric
- Dates: YYYY-MM-DD parse from multiple formats
- Amounts: safe Decimal conversion; `tax_breakdown` dict normalized
- Currency: normalize to 3-char ISO code

**Step 8 — Validation (`ValidationService`)**
- **Mandatory**: invoice_number, vendor_name, total_amount
- **Recommended**: po_number, invoice_date, subtotal, confidence ≥ 0.75
- Output: `ValidationResult` (is_valid, issues, errors/warnings)

**Step 9 — Duplicate Detection (`DuplicateDetectionService`)**
- Check 1: Same invoice_number + vendor → DUPLICATE
- Check 2: Same invoice_number + amount within 90 days → DUPLICATE
- Check 3: Same vendor + amount + date → WARNING

**Step 10 — Persistence (`InvoicePersistenceService`)**
- Resolve vendor FK (by normalized name or alias lookup)
- Set invoice status: INVALID (if validation failed), EXTRACTED/VALIDATED (otherwise)
- Save Invoice + InvoiceLineItem records (including vendor_tax_id, buyer_name, due_date, tax_breakdown)
- Save ExtractionResult metadata (including prompt_hash, invoice_category, repair actions)

**Step 11 — Extraction Approval Gate (`ExtractionApprovalService`)**
- For valid, non-duplicate invoices: check auto-approval eligibility
- **Auto-approval**: When `EXTRACTION_AUTO_APPROVE_ENABLED=true` and confidence ≥ `EXTRACTION_AUTO_APPROVE_THRESHOLD`, auto-approve → READY_FOR_RECON
- **Human approval**: Otherwise set invoice to PENDING_APPROVAL, create ExtractionApproval record with original values snapshot
- Human reviews extracted data, optionally corrects fields → each correction tracked as ExtractionFieldCorrection
- On approve: invoice transitions to READY_FOR_RECON, AP Case auto-created
- On reject: invoice marked INVALID for re-extraction
- Analytics: touchless rate, most-corrected fields, approval breakdown via `get_approval_analytics()`

### 7.3 Azure Blob Storage (`documents/blob_service.py`)

| Function | Purpose |
|---|---|
| `upload_to_blob()` | Save file to Azure Blob |
| `download_blob_to_tempfile()` | Retrieve to temporary file |
| `generate_blob_sas_url()` | Time-limited read URL (30 min default) |
| `move_blob()` | Copy + delete (e.g., input/ → processed/) |
| `delete_blob()` | Remove blob |

Folder structure: `input/`, `processed/`, `exception/`

---

## 8. Reconciliation Engine

### 8.1 Overview

The reconciliation engine performs deterministic matching of invoices against POs and optionally GRNs. It is orchestrated by `ReconciliationRunnerService` and comprises **14 specialized services**.

### 8.2 Reconciliation Pipeline

```
ReconciliationRunnerService.run(invoice_ids)
    │
    ├─ For each invoice:
    ├─ 1. PO Lookup (`POLookupService` via `ERPResolutionService`)
    │   │      Chain: cache -> MIRROR_DB (documents.PurchaseOrder)
    │   │               -> live API -> DB_FALLBACK (ERPPOReference)
    │   │      Result carries: erp_source_type, erp_provenance, is_stale
    │   │
    │   ├─ 2. Mode Resolution (ReconciliationModeResolver)
    │   │      Cascade: policy → heuristic → config default
    │   │
    │   ├─ 3. Execution Router (ReconciliationExecutionRouter)
    │   │      ├── TWO_WAY → TwoWayMatchService
    │   │      └── THREE_WAY → ThreeWayMatchService
    │   │
    │   ├─ 4. Classification (ClassificationService)
    │   │      → MATCHED / PARTIAL_MATCH / UNMATCHED / REQUIRES_REVIEW
    │   │
    │   ├─ 5. Exception Building (ExceptionBuilderService)
    │   │      → Create structured exception records
    │   │
    │   ├─ 6. Result Persistence (ReconciliationResultService)
    │   │      -> Save result + line results + exceptions
    │   │      -> Persist ERP provenance: po_erp_source_type, grn_erp_source_type,
    │   │         data_is_stale, erp_source_metadata_json
    │   │
    │   ├─ 7. Auto-Create ReviewAssignment (if REQUIRES_REVIEW)
    │   │
    │   └─ 8. Update Invoice Status → RECONCILED or FAILED
    │
    └─ Update ReconciliationRun totals
```

### 8.3 Mode Resolution

The system determines whether each invoice needs 2-way or 3-way matching via a 3-tier cascade:

**Tier 1 — Policy-Based** (`ReconciliationPolicy` model)
- Priority-ordered rules matching: vendor, invoice_type, item_category, business_unit, location_code, service/stock flags
- Date-validity checked (effective_from/to)
- Most specific policy wins (lowest priority number)

**Tier 2 — Heuristic-Based**
- Service keywords in line descriptions → TWO_WAY (if `enable_two_way_for_services`)
- Stock keywords in line descriptions → THREE_WAY (if `enable_grn_for_stock_items`)
- Keywords: "consulting", "maintenance", "service" → service; "stock", "inventory", "goods" → stock

**Tier 3 — Default**
- Falls back to `ReconciliationConfig.default_reconciliation_mode` (typically THREE_WAY)

### 8.4 Two-Way Match Service

Compares Invoice ↔ PO:

1. **Header Match** (`HeaderMatchService`)
   - Vendor: FK comparison or normalized name match
   - Currency: exact match
   - Total amount: within tolerance (default 1%)

2. **Line Match** (`LineMatchService`)
   - Composite scoring algorithm per line pair:
     - Line number bonus: 0.20 (if same position)
     - Description similarity: 0–0.30 (fuzzy match threshold: 70%)
     - Quantity comparison: 0–0.20
     - Price comparison: 0–0.20
     - Amount comparison: 0–0.20
   - Minimum score to match: 0.30
   - Best-match assignment (greedy)

### 8.5 Three-Way Match Service

Extends 2-way with GRN verification:

1. Header match (same as 2-way)
2. Line match (same as 2-way)
3. **GRN Lookup** (`GRNLookupService` via `ERPResolutionService`) -- Resolves via
   shared ERP layer (MIRROR_DB: documents.GoodsReceiptNote), hydrates ORM objects
   from `grn_ids` in the resolution result for line-level matching.
   Result carries: erp_source_type, erp_provenance, is_stale
4. **GRN Match** (`GRNMatchService`) — Compare Invoice/PO quantities against received quantities:
   - Over-receipt: received > ordered
   - Under-receipt: received < ordered
   - Invoice exceeds received

### 8.6 Tolerance Engine

Two tiers of tolerance for numeric comparisons:

| Metric | Strict Tolerance | Auto-Close Tolerance |
|---|---|---|
| Quantity | 2% | 5% |
| Price | 1% | 3% |
| Amount | 1% | 3% |

Formula: `|actual - expected| / expected × 100`

The auto-close band is used by the PolicyEngine to automatically close PARTIAL_MATCH results that fall within the wider tolerance.

### 8.7 Classification Logic

`ClassificationService` applies a deterministic decision tree:

| Condition | Result |
|---|---|
| PO not found | UNMATCHED |
| Low extraction confidence (< threshold) | REQUIRES_REVIEW |
| All headers OK + all lines matched + within tolerance + no GRN issues | MATCHED |
| Headers OK + some tolerance breaches | PARTIAL_MATCH |
| Header issues + lines matched | PARTIAL_MATCH |
| GRN receipt issues (3-way only) | REQUIRES_REVIEW |
| Unmatched lines exist | REQUIRES_REVIEW |
| Fallback | REQUIRES_REVIEW |

### 8.8 Exception Types

**Common (2-Way + 3-Way):**
- PO_NOT_FOUND, VENDOR_MISMATCH, CURRENCY_MISMATCH, AMOUNT_MISMATCH
- QTY_MISMATCH, PRICE_MISMATCH, TAX_MISMATCH, ITEM_MISMATCH
- DUPLICATE_INVOICE, EXTRACTION_LOW_CONFIDENCE, LOCATION_MISMATCH

**3-Way Only (GRN-related):**
- GRN_NOT_FOUND, RECEIPT_SHORTAGE, INVOICE_QTY_EXCEEDS_RECEIVED
- OVER_RECEIPT, MULTI_GRN_PARTIAL_RECEIPT, RECEIPT_LOCATION_MISMATCH, DELAYED_RECEIPT

Each exception is tagged with severity (LOW/MEDIUM/HIGH/CRITICAL) and `applies_to_mode` (TWO_WAY/THREE_WAY/BOTH).

### 8.9 Result Persistence

`ReconciliationResultService` saves:
- **ReconciliationResult**: header evidence, mode metadata, confidence scores
- **ReconciliationResultLine**: per-line comparison data (qty, price, amount, tax, description similarity)
- **ReconciliationException**: structured exception records

**Deterministic Confidence** — Weighted composite:
- Header match weight: 40–45%
- Line match weight: 45–55%
- GRN match weight: 15% (3-way only)

### 8.10 Agent Feedback Loop

`AgentFeedbackService` handles re-reconciliation when an agent recovers a missing PO:

1. Link recovered PO to ReconciliationResult and Invoice
2. Build synthetic POLookupResult
3. Re-run header, line, GRN matching (respecting original mode)
4. Re-classify with new evidence
5. Delete old exceptions/line results, rebuild fresh
6. Auto-create review if needed
7. Full audit trail logged
8. **Atomic** — entire operation is transaction-safe

---

## 9. AI Agent System

### 9.1 Architecture

The agent system uses a **ReAct (Reasoning + Acting)** pattern where LLM agents iteratively reason about problems and call tools to gather information.

```
AgentOrchestrator.execute(result, request_user=...)
    ↓
AgentGuardrailsService.resolve_actor(request_user)       ← Actor resolution (user or SYSTEM_AGENT)
    ↓
AgentGuardrailsService.authorize_orchestration(actor)     ← "agents.orchestrate" permission check
    ↓
Build RBAC snapshot (actor_primary_role, roles, permission_source)
    ↓
PolicyEngine.plan()  →  AgentPlan (which agents, in what order)
    ↓
For each planned agent:
    AgentGuardrailsService.authorize_agent(actor, agent_type)  ← Per-agent permission check
    ↓
    BaseAgent.run(ctx)                                         ← ctx includes full RBAC context
    ├── Build system prompt + user message (with mode context)
    ├── ReAct Loop (max 6 iterations):
    │   ├── LLM chat (with tool definitions)
    │   ├── If tool_calls:
    │   │   ├── AgentGuardrailsService.authorize_tool(actor, tool) ← Per-tool permission check
    │   │   ├── tool.execute(**arguments)
    │   │   └── ToolCallLogger.log(...) → loop
    │   └── If no tool_calls → interpret response → return
    ├── Log: AgentMessages, AgentSteps, DecisionLog
    └── Return: AgentRun (status, confidence, reasoning, RBAC fields)
    ↓
DeterministicResolver (replaces EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
    ↓
AgentFeedbackService (re-reconcile if PO found by agent)
    ↓
authorize_action("auto_close_result") / authorize_action("escalate_case")
    ↓
Final Recommendation + Auto-Close / Escalation
```

### 9.2 Eight Agent Types

| Agent | Type | Purpose | Tools |
|---|---|---|---|
| **InvoiceExtractionAgent** | INVOICE_EXTRACTION | Extracts structured invoice data from OCR text (always runs, single-shot json_object mode) | None |
| **InvoiceUnderstandingAgent** | INVOICE_UNDERSTANDING | Validates low-confidence extractions within case orchestrator (conditional: confidence < 75%) | invoice_details, vendor_search |
| **PORetrievalAgent** | PO_RETRIEVAL | Finds correct PO when deterministic lookup failed | po_lookup, vendor_search, invoice_details |
| **GRNRetrievalAgent** | GRN_RETRIEVAL | Investigates GRN issues (3-way only) | grn_lookup, po_lookup, invoice_details |
| **ReconciliationAssistAgent** | RECONCILIATION_ASSIST | General-purpose for partial match investigation | All 6 tools |
| **ExceptionAnalysisAgent** | EXCEPTION_ANALYSIS | Root cause analysis of exceptions | exception_list, reconciliation_summary, invoice_details |
| **ReviewRoutingAgent** | REVIEW_ROUTING | Determines appropriate review queue/team | exception_list, reconciliation_summary |
| **CaseSummaryAgent** | CASE_SUMMARY | Produces human-readable case summary | reconciliation_summary, exception_list |

### 9.3 Tool Registry (6 Tools)

All tools extend `BaseTool` and are registered via the `@register_tool` decorator. Each tool declares a `required_permission` enforced by `AgentGuardrailsService.authorize_tool()` before execution:

| Tool | Input | Output | Required Permission |
|---|---|---|---|
| **po_lookup** | po_number | PO header + line items | `purchase_orders.view` |
| **grn_lookup** | po_number | GRN list, receipt quantities per line | `grns.view` |
| **vendor_search** | query (name/code/alias) | Matching vendors (direct + alias matches) | `vendors.view` |
| **invoice_details** | invoice_id | Full invoice details + line items | `invoices.view` |
| **exception_list** | reconciliation_result_id | All exceptions with metadata | `reconciliation.view` |
| **reconciliation_summary** | reconciliation_result_id | Match status, confidence, header evidence | `reconciliation.view` |

Tool calls are logged via `ToolCallLogger` with status (REQUESTED/SUCCESS/FAILED), duration, and input/output. Authorization denials are logged as `TOOL_CALL_DENIED` audit events.

### 9.4 ReasoningPlanner

`ReasoningPlanner` is the entry point for planning agent execution. It always makes a single LLM call to produce a structured `AgentPlan`. If the LLM fails, `PolicyEngine` is the internal deterministic fallback. There is no feature flag — the LLM planner is always active.

```python
@dataclass
class AgentPlan:
    agents: List[str]          # ordered AgentType values to run
    reason: str
    skip_agents: bool          # True -> skip all agents (auto-close or high confidence)
    auto_close: bool           # True -> mark result MATCHED without agents
    reconciliation_mode: str
    plan_source: str           # "deterministic" or "llm"
    plan_confidence: float     # planner self-reported confidence
```

**LLM plan validation guards** (any violation falls back to deterministic):
1. Empty agent list
2. `CASE_SUMMARY` not last if present
3. `GRN_RETRIEVAL` in a TWO_WAY plan

### 9.5 AgentMemory

`AgentMemory` is created by the orchestrator at pipeline start and passed through all agents via `AgentContext`. It accumulates findings so later agents have structured access to what earlier agents discovered.

| Field | Purpose |
|---|---|
| `resolved_po_number` | PO recovered by PO_RETRIEVAL agent |
| `resolved_grn_numbers` | GRNs recovered by GRN_RETRIEVAL agent |
| `extraction_issues` | Issues flagged by INVOICE_UNDERSTANDING agent |
| `agent_summaries` | Per-agent reasoning snippets (first 500 chars) |
| `current_recommendation` | Highest-confidence recommendation seen so far |
| `current_confidence` | Confidence associated with current recommendation |
| `facts` | Pre-seeded: grn_available, grn_fully_received, is_two_way, vendor_name, match_status |

### 9.6 Policy Engine

`PolicyEngine` determines which agents to run based on deterministic rules (no LLM cost):

| Condition | Action |
|---|---|
| MATCHED + high confidence (≥ 0.8) | Skip agents entirely |
| PARTIAL_MATCH within auto-close band | Auto-close, skip agents |
| PO_NOT_FOUND exception | Queue PO_RETRIEVAL |
| GRN_NOT_FOUND (3-way only) | Queue GRN_RETRIEVAL |
| Low extraction confidence | Queue INVOICE_UNDERSTANDING |
| PARTIAL_MATCH (outside band) | Queue RECONCILIATION_ASSIST |
| Any exceptions | Queue EXCEPTION_ANALYSIS |
| If any agents queued | Append REVIEW_ROUTING + CASE_SUMMARY |

**Auto-Close Band Logic:**
- All line discrepancies within wider tolerance (5% qty, 3% price, 3% amount)
- No HIGH-severity exceptions
- All matched lines check passes
- Mode-aware: skips GRN_RETRIEVAL in TWO_WAY mode

### 9.7 Deterministic Resolver

`DeterministicResolver` replaces costly LLM calls for EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY with rule-based logic:

| Priority | Condition | Recommendation |
|---|---|---|
| 1 | Prior AUTO_CLOSE (confidence ≥ 0.80) | AUTO_CLOSE |
| 2 | EXTRACTION_LOW_CONFIDENCE | REPROCESS_EXTRACTION |
| 3 | VENDOR_MISMATCH | SEND_TO_VENDOR_CLARIFICATION |
| 4 | GRN/receipt issues | SEND_TO_PROCUREMENT |
| 5 | Complex case (3+ categories + HIGH severity) | ESCALATE_TO_MANAGER |
| 6 | Default | SEND_TO_AP_REVIEW |

Creates synthetic AgentRun records for auditability.

### 9.8 LLM Client

`LLMClient` wraps both Azure OpenAI and plain OpenAI APIs:

- Configurable via environment variables (`AZURE_OPENAI_*` or `OPENAI_API_KEY`)
- Supports tool-calling in OpenAI-compliant format
- Tool calls: `tool_calls` array on assistant messages, `tool_call_id` + `name` on responses
- `response_format` parameter: supports `{"type": "json_object"}` for deterministic JSON output (used by InvoiceExtractionAgent)
- Returns: `LLMResponse` (content, tool_calls, finish_reason, token counts)

### 9.9 Orchestration Flow

`AgentOrchestrator.execute(result, request_user=None)`:

1. **Resolve actor** — `AgentGuardrailsService.resolve_actor(request_user)` returns the triggering user or the `SYSTEM_AGENT` service account
2. **Authorize orchestration** — `authorize_orchestration(actor)` validates `agents.orchestrate` permission
3. **Build RBAC snapshot** — captures `actor_primary_role`, `actor_roles_snapshot`, `permission_source` at execution time
4. Load ReconciliationResult + exceptions
5. Ask PolicyEngine for agent plan
6. Partition agents: LLM-required vs deterministic-replaceable
7. Build `AgentContext` with reconciliation mode awareness **+ full RBAC context** (actor_user_id, actor_primary_role, actor_roles_snapshot, permission_checked, permission_source, access_granted, trace_id, span_id)
8. **Per-agent authorization** — `authorize_agent(actor, agent_type)` before each `agent.run(ctx)`; unauthorized agents are skipped with a logged denial
9. Execute LLM agents sequentially (with per-tool authorization inside BaseAgent)
10. Execute deterministic agents (cheaper alternatives) — RBAC fields populated on synthetic AgentRun records
11. Apply feedback loop for PO_RETRIEVAL agents
12. Resolve final recommendation
13. **Authorize post-policies** — `authorize_action(actor, "auto_close_result")` for auto-close; `authorize_action(actor, "escalate_case")` for escalation

Output: `OrchestrationResult` (agents_executed, agent_runs, final_recommendation, confidence)

### 9.10 RBAC Guardrails

**AgentGuardrailsService** (`apps/agents/services/guardrails_service.py`) is the central RBAC enforcement point for the entire agent subsystem. All agent operations flow through this service before execution.

#### Authorization Checks

| Method | Permission | Purpose |
|---|---|---|
| `authorize_orchestration()` | `agents.orchestrate` | Gate entry to pipeline |
| `authorize_agent()` | `agents.run_*` (8 per-type) | Gate each agent type |
| `authorize_tool()` | Tool's `required_permission` | Gate each tool call |
| `authorize_recommendation()` | `recommendations.*` (6 types) | Gate recommendation acceptance |
| `authorize_action()` | `recommendations.auto_close`, `cases.escalate`, etc. | Gate post-pipeline actions |

#### Actor Resolution

When `request_user` is provided (sync UI path), that user's permissions govern the pipeline. When no user is available (Celery async, system-triggered), `resolve_actor()` returns the **SYSTEM_AGENT** service account — a dedicated `User` record (`system-agent@internal`) with the `SYSTEM_AGENT` role and a scoped permission set.

#### RBAC Snapshot

`build_rbac_snapshot(actor)` captures the actor's RBAC state at execution time and stores it on every `AgentRun`:

| Field | Source |
|---|---|
| `actor_primary_role` | `user.get_primary_role().code` |
| `actor_roles_snapshot_json` | `list(user.get_role_codes())` |
| `permission_source` | `"SYSTEM_AGENT"` or `"USER"` |
| `access_granted` | Boolean result of permission check |

#### Guardrail Audit Events

All authorization decisions are logged as `AuditEvent` records:

| Event Type | When |
|---|---|
| `GUARDRAIL_GRANTED` | Permission check passed |
| `GUARDRAIL_DENIED` | Permission check failed (agent/action skipped) |
| `TOOL_CALL_AUTHORIZED` | Tool execution permitted |
| `TOOL_CALL_DENIED` | Tool execution blocked |
| `RECOMMENDATION_ACCEPTED` | Recommendation acceptance authorized |
| `RECOMMENDATION_DENIED` | Recommendation acceptance blocked |
| `AUTO_CLOSE_AUTHORIZED` | Auto-close action permitted |
| `AUTO_CLOSE_DENIED` | Auto-close action blocked |
| `SYSTEM_AGENT_USED` | SYSTEM_AGENT identity resolved for autonomous run |

### 9.11 Tracing & Governance

**AgentTraceService** provides unified tracing:
- `start_agent_run()` / `finish_agent_run()`
- `log_agent_step()`, `log_tool_call()`, `log_agent_decision()`
- `get_trace_for_result()` / `get_trace_for_invoice()` — full trace aggregation

**DecisionLogService** records all agent decisions with rationale and evidence.

**RecommendationService** manages agent recommendations with acceptance tracking.

---

## 10. Case Management Platform

### 10.1 Overview

The case management system (`apps/cases/`) provides a structured AP case lifecycle with state machine-driven stage processing.

### 10.2 Case Creation

`CaseCreationService`:
- Creates APCase from invoice upload
- Generates case number: `AP-YYMMDD-NNNN`
- Infers invoice type (PO_BACKED or UNKNOWN based on po_number)
- Assesses priority: HIGH (≥$50K), MEDIUM (≥$10K), LOW
- Creates initial INTAKE stage

### 10.3 Processing Paths

| Path | When | Stages |
|---|---|---|
| **TWO_WAY** | Service invoices, policy-matched | INTAKE → EXTRACTION → PATH_RESOLUTION → PO_RETRIEVAL → TWO_WAY_MATCHING → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY |
| **THREE_WAY** | Stock/goods invoices | Same as TWO_WAY + GRN_ANALYSIS (conditional) |
| **NON_PO** | No PO reference | INTAKE → EXTRACTION → PATH_RESOLUTION → NON_PO_VALIDATION → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY |

### 10.4 State Machine

`CaseStateMachine` enforces **30+ valid transitions** with trigger validation:
- Trigger types: SYSTEM, DETERMINISTIC, AGENT, HUMAN
- Terminal states: CLOSED, REJECTED, ESCALATED, FAILED
- Methods: `can_transition()`, `get_allowed_transitions()`, `is_terminal()`, `transition()`
- **Audit logging**: Terminal state transitions automatically create AuditEvents (CASE_CLOSED, CASE_REJECTED, CASE_ESCALATED, CASE_FAILED) with invoice_id, case_id, status_before/status_after, and trigger_type metadata

### 10.5 Stage Executor

`StageExecutor` dispatches individual stages:

| Stage | Handler |
|---|---|
| INTAKE | Validate upload, classify document |
| EXTRACTION | Monitor completion, validate quality; invoke Invoice Understanding Agent if confidence < 75% |
| PATH_RESOLUTION | CaseRoutingService → determine path |
| PO_RETRIEVAL | Deterministic lookup + agent fallback |
| TWO_WAY_MATCHING | ReconciliationRunnerService |
| THREE_WAY_MATCHING | ReconciliationRunnerService (mode handles) |
| GRN_ANALYSIS | GRN Retrieval Agent |
| NON_PO_VALIDATION | NonPOValidationService (9 checks) |
| EXCEPTION_ANALYSIS | AgentOrchestrator |
| REVIEW_ROUTING | Create APCaseAssignment |
| CASE_SUMMARY | Deterministic summary ± agent |

### 10.6 Non-PO Validation

`NonPOValidationService` runs 9 deterministic checks for invoices without PO references:

| Check | Description |
|---|---|
| vendor | Active vendor, linked to case |
| duplicates | By number+vendor, by amount+date |
| mandatory_fields | Number, date, vendor, amount, currency |
| supporting_documents | Threshold-based: $5K, $25K, $50K |
| spend_category | TRAVEL, UTILITIES, MAINTENANCE, CONSULTING, SUPPLIES |
| policy | Amount thresholds: $100K, $250K |
| cost_center | Cost center validation (stub) |
| tax | Reasonability check against default VAT rate |
| budget | Budget availability check (stub) |

Output: `NonPOValidationResult` (checks, overall_status: PASS/FAIL/NEEDS_REVIEW, risk_score)

### 10.7 Case Routing

`CaseRoutingService`:
- No PO → NON_PO path
- PO found → ReconciliationModeResolver → TWO_WAY or THREE_WAY path
- PO reference but not found → check confidence; if < 0.5 → NON_PO; else UNRESOLVED
- Supports `reroute_path()` for mid-processing path changes

### 10.8 Case Summary

`CaseSummaryService` builds deterministic template summaries from case data, aggregating invoice facts, processing path, reconciliation result, validation results, and current status.

### 10.9 Orchestrator Terminal-State Behaviour

`CaseOrchestrator._run_common_tail()` always executes `CASE_SUMMARY` — even when `CaseStateMachine.is_terminal()` returns `True` after `EXCEPTION_ANALYSIS` (e.g. auto-close sets status → CLOSED). This ensures that:

- The case summary is **regenerated on every reprocess**, reflecting the latest reconciliation outcome.
- `REVIEW_ROUTING` is still **skipped** for terminal cases (no new assignment needed).

```
EXCEPTION_ANALYSIS
    ↓
is_terminal()?
├── Yes → CASE_SUMMARY (always) → return   # auto-closed, rejected, etc.
└── No  → REVIEW_ROUTING → CASE_SUMMARY
```

---

## 10.10 AP Copilot

The `apps/copilot/` app provides a **read-only conversational assistant** that lets AP users investigate cases, invoices, reconciliation results, exceptions, and governance metadata through natural-language questions. It never modifies records.

### Models

| Model | Purpose |
|---|---|
| `CopilotSession` | A conversation session linked (optionally) to an `APCase` and/or `Invoice`. Stores pin/archive status, RBAC snapshot at creation, `last_message_at`, and a `trace_id`. Uses UUID PK. |
| `CopilotMessage` | A single turn (USER / ASSISTANT / SYSTEM) within a session. Assistant messages carry `structured_payload_json` (summary, evidence, recommendation, follow-up prompts), `consulted_agents_json`, `evidence_payload_json`, `governance_payload_json`, and `token_count`. |
| `CopilotSessionArtifact` | References to business objects surfaced during a session (invoice, case, PO, GRN, etc.) — typed by `CopilotArtifactType`. |

### `APCopilotService`

Stateless service class in `apps/copilot/services/copilot_service.py`:

| Method | Purpose |
|---|---|
| `start_session(user, case_id=None)` | Create (or resume active) session; links case/invoice when `case_id` is provided. |
| `list_sessions(user, include_archived)` | Return user's sessions ordered by `last_message_at`. |
| `archive_session(user, session_id)` | Soft-archive a session. |
| `toggle_pin(user, session_id)` | Flip `is_pinned` flag. |
| `link_case_to_session(user, session_id, case_id)` | Attach a case to an existing session. |
| `save_user_message(session, text)` | Persist user turn. |
| `answer_question(user, message, session)` | **Core chat handler** — detects small-talk, assembles case context + evidence, looks up agent runs, returns structured `{summary, evidence, consulted_agents, recommendation, governance, follow_up_prompts}` dict. Currently deterministic; designed for future LLM routing. |
| `save_assistant_message(session, payload)` | Persist structured assistant response. |
| `build_case_context(case_id, user)` | Aggregates invoice, PO, GRN, recon result, exceptions, agents, recommendation into one dict. |
| `build_case_evidence(case_id, user)` | Evidence cards for invoice, PO lines, GRN lines, exceptions. |
| `build_case_governance(case_id, user)` | Audit trail, RBAC events, agent trace (privileged roles only). |
| `build_case_timeline(case_id, user)` | Delegates to `CaseTimelineService`. |
| `get_suggestions(user)` | Returns 4 role-aware suggested prompts from `ROLE_PROMPTS`. |
| `get_session_detail(user, session_id)` | Fetch single session (ownership-checked). |
| `load_session_messages(user, session_id)` | Fetch ordered messages for a session. |

### Small-Talk Handling

`_detect_small_talk(message)` short-circuits business processing for greetings, thanks, identity questions, and acknowledgements. Business keywords (case, invoice, PO, vendor, etc.) override detection regardless of message length.

### Role-Aware Visibility

| Visibility Level | Roles | Extra data |
|---|---|---|
| Governance | ADMIN, AUDITOR | Full audit trail, RBAC events, permission-denial trace in responses |
| Extended | FINANCE_MANAGER, REVIEWER | Recommendations, review history |
| Operational | AP_PROCESSOR | Case status, exceptions, extraction confidence |

### API Endpoints (`/api/v1/copilot/`)

| Endpoint | Method | Description | Permission |
|---|---|---|---|
| `session/start/` | POST | Start or resume a session | `agents.use_copilot` |
| `sessions/` | GET | List user's sessions | `agents.use_copilot` |
| `session/<session_id>/` | GET, PATCH | Session detail; PATCH actions: `archive`, `pin`, `link_case`, `unlink_case` | `agents.use_copilot` |
| `session/<session_id>/messages/` | GET | Paginated message history | `agents.use_copilot` |
| `chat/` | POST | Send message → structured response | `agents.use_copilot` |
| `case/<case_id>/context/` | GET | Full case context bundle | `cases.view` |
| `case/<case_id>/timeline/` | GET | Case timeline (delegates to `CaseTimelineService`) | `cases.view` |
| `case/<case_id>/evidence/` | GET | Evidence cards | `cases.view` |
| `case/<case_id>/governance/` | GET | Governance data (audit, RBAC, agent trace) | `cases.view` |
| `suggestions/` | GET | Role-aware suggested prompts | `agents.use_copilot` |
| `cases/search/` | GET | Case search by keyword/status | `cases.view` |

### Template Views (`/copilot/`)

| URL | View | Description |
|---|---|---|
| `/copilot/` | `copilot_workspace` | Main copilot workspace with session list and suggestions |
| `/copilot/case/<case_id>/` | `copilot_case` | Case-linked workspace — auto-starts/resumes session for the case |
| `/copilot/session/<session_id>/` | `copilot_session` | Resume a specific session |

Template: `templates/copilot/ap_copilot_workspace.html`. JS panel: `static/js/copilot-panel.js`.

---

## 11. Review Workflow

### 11.1 Lifecycle

```
ReconciliationResult (REQUIRES_REVIEW)
    ↓
Auto-create ReviewAssignment (PENDING)
    ↓
Assign Reviewer → ASSIGNED
    ↓
Start Review → IN_REVIEW
    ↓
Record Actions (field corrections, comments, info requests)
    ↓
Final Decision:
├── APPROVED → Close case
├── REJECTED → Close with reason
└── REPROCESSED → Re-run reconciliation
```

### 11.2 Service Methods

`ReviewWorkflowService`:

| Method | Purpose |
|---|---|
| `create_assignment()` | Create ReviewAssignment |
| `assign_reviewer()` | Assign user to assignment |
| `start_review()` | Transition to IN_REVIEW |
| `record_action()` | Log ManualReviewAction (field corrections, etc.) |
| `add_comment()` | Add ReviewComment |
| `approve()` | Approve with reason → update ReconciliationResult |
| `reject()` | Reject with reason → update ReconciliationResult |
| `request_reprocess()` | Re-queue for reconciliation |

### 11.3 Audit Integration

All review decisions log to `AuditService`:
- REVIEW_ASSIGNED event
- REVIEW_APPROVED / REVIEW_REJECTED events
- FIELD_CORRECTED events for manual corrections

---

## 12. Dashboard & Analytics

### 12.1 Service Endpoints

`DashboardService` provides read-only aggregations:

| Method | Returns |
|---|---|
| `get_summary()` | Total invoices, vendors, pending reviews, total exceptions, match rate % |
| `get_match_status_breakdown()` | Count by MatchStatus (MATCHED, PARTIAL_MATCH, etc.) |
| `get_exception_breakdown()` | Count by ExceptionType |
| `get_mode_breakdown()` | TWO_WAY vs THREE_WAY counts with match rates |
| `get_agent_performance()` | Per-agent: run count, success %, avg confidence, token usage |
| `get_daily_volume()` | Time series of processing volume (30 days default) |
| `get_recent_activity()` | Recent processing events across entities |

### 12.2 UI Views

- **Main Dashboard** (`/dashboard/`) — Summary cards + recent activity
- **Agent Monitor** (`/dashboard/agents/`) — KPIs + agent activity + case operations metrics

---

## 13. Governance & Audit Trail

### 13.1 Observability Infrastructure

Three core modules provide enterprise-grade observability:

| Module | File | Purpose |
|---|---|---|
| **TraceContext** | `apps/core/trace.py` | Distributed tracing — `trace_id`, `span_id`, `parent_span_id`, RBAC snapshot. Thread-local propagation via `get_current()`/`set_current()`. Celery header serialization. |
| **Structured Logging** | `apps/core/logging_utils.py` | `JSONLogFormatter` (production) and `DevLogFormatter` (development). `TraceLogger` auto-injects trace context. `redact_dict()` scrubs PII/financial data. `DurationTimer` for latency tracking. |
| **Metrics** | `apps/core/metrics.py` | Thread-safe in-process counters via `MetricsService`. Tracks RBAC checks, extractions, reconciliations, reviews, agent runs, case transitions, task executions. |

`RequestTraceMiddleware` (in `apps/core/middleware.py`) creates a root `TraceContext` per HTTP request, enriches it with the authenticated user's RBAC snapshot, and sets `X-Trace-ID` / `X-Request-ID` response headers.

### 13.2 Observability Decorators

Three decorators in `apps/core/decorators.py` instrument service methods, view functions, and Celery tasks:

| Decorator | Target | Behaviour |
|---|---|---|
| `@observed_service` | Service class methods | Creates child span, measures duration, writes `ProcessingLog`, optionally writes `AuditEvent`. |
| `@observed_action` | Django view functions (FBV) | Resolves RBAC permission source, checks permission, writes both `AuditEvent` and `ProcessingLog`. |
| `@observed_task` | Celery tasks | Reconstructs `TraceContext` from Celery headers, wraps execution with duration/error tracking. |

Instrumented services: extraction task, reconciliation runner, agent feedback, agent orchestrator, review approve/reject/reprocess, case orchestrator, case creation, case routing, invoice upload view, start reconciliation view.

### 13.3 RBAC-Aware Audit Events

`AuditEvent` model carries 20+ fields for full RBAC and traceability context:

| Field Group | Fields |
|---|---|
| **Trace** | `trace_id`, `span_id`, `parent_span_id` |
| **Actor RBAC** | `actor_primary_role`, `actor_email`, `actor_roles_snapshot` (JSON) |
| **Permission** | `permission_checked`, `permission_source`, `access_granted` |
| **Cross-references** | `invoice_id`, `case_id`, `reconciliation_result_id` |
| **Status Change** | `status_before`, `status_after` |
| **Timing** | `duration_ms` |
| **Error** | `error_code`, `is_redacted` |

`AuditService.log_event()` accepts an optional `TraceContext`, auto-populates RBAC fields from the actor, and redacts sensitive payload data.

Query helpers: `fetch_case_history()`, `fetch_access_history()`, `fetch_permission_denials()`, `fetch_rbac_activity()`.

`AuditService` logs 17+ event types:

| Category | Events |
|---|---|
| **Document** | INVOICE_UPLOADED |
| **Extraction** | EXTRACTION_COMPLETED, EXTRACTION_FAILED, VALIDATION_FAILED |
| **Reconciliation** | RECONCILIATION_STARTED, RECONCILIATION_COMPLETED, RECONCILIATION_RERUN |
| **Mode** | RECONCILIATION_MODE_RESOLVED, POLICY_APPLIED, MANUAL_MODE_OVERRIDE |
| **Agent** | AGENT_RUN_STARTED, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED, AGENT_RECOMMENDATION_CREATED |
| **Review** | REVIEW_ASSIGNED, REVIEW_APPROVED, REVIEW_REJECTED, FIELD_CORRECTED, REVIEWER_ASSIGNED, REVIEW_STARTED |
| **Case Management** | CASE_ASSIGNED, CASE_CLOSED, CASE_REJECTED, CASE_REPROCESSED, CASE_ESCALATED, CASE_FAILED, CASE_STATUS_CHANGED, COMMENT_ADDED |

### 13.4 Enhanced Case Timeline

`CaseTimelineService` builds a unified chronological timeline per invoice, merging 8 event categories:

| Category | Source | Enrichment |
|---|---|---|
| `audit` | AuditEvent | RBAC badge (role + permission + granted/denied), status change, field corrections |
| `mode_resolution` | AuditEvent (MODE events) | Mode-specific context |
| `agent_run` | AgentRun | Duration, agent type, invocation reason |
| `tool_call` | ToolCall | Input/output summary, duration |
| `decision` | DecisionLog | Decision type, rule/policy traceability, RBAC context |
| `recommendation` | AgentRecommendation | Acceptance status |
| `review` / `review_action` / `review_decision` | ReviewAssignment + children | Review lifecycle |
| `case` / `stage` | APCase / APCaseStage | Stage durations, trace IDs |

Each timeline entry includes an `rbac_badge` dict (`{role, permission, granted}`) when RBAC context is available, plus `status_change`, `field_changes`, and `duration_ms` when applicable.

**Ordering**: `get_case_timeline()` returns entries sorted **latest-first** (`reverse=True` on the `timestamp` key). The case agent view (`/cases/<pk>/agent/`) likewise queries related querysets with `-created_at` (stages, decisions, comments) so the most recent activity appears at the top.

`get_stage_timeline(case_id)` returns a stage-centric timeline for case governance views.

### 13.5 Agent Run Traceability

`AgentRun` model carries trace and RBAC fields:

| Field | Purpose |
|---|---|
| `trace_id` / `span_id` | Links agent execution to the request trace |
| `invocation_reason` | Why the agent was triggered (e.g., "PARTIAL_MATCH exception") |
| `prompt_version` | Version of the prompt template used |
| `actor_user_id` / `permission_checked` | Who initiated and what permission was checked |
| `actor_primary_role` | Primary role of the actor at execution time (e.g., `ADMIN`, `SYSTEM_AGENT`) |
| `actor_roles_snapshot_json` | JSON snapshot of all active roles at execution time |
| `permission_source` | How permission was resolved: `USER` or `SYSTEM_AGENT` |
| `access_granted` | Boolean result of the authorization check |
| `cost_estimate` | Estimated LLM cost for the run |

All RBAC fields are populated by `AgentGuardrailsService.build_rbac_snapshot()` at orchestration time and propagated via `AgentContext`.

`DecisionLog` entries now include `decision_type`, rule/policy references, and RBAC context fields.

### 13.6 Governance UI

| View | URL | Access |
|---|---|---|
| **Audit Event List** | `/governance/` | Filterable log (role, trace_id, denied-only filter, 50/page), RBAC columns |
| **Invoice Governance** | `/governance/invoice/<id>/` | Full dashboard: audit trail + agent trace + timeline + access history |

Audit Event List enhancements:
- **RBAC columns**: Actor Role, Permission (with granted/denied icon)
- **New filters**: Role dropdown, Trace ID text input, "Denied Only" checkbox
- **Visual**: `table-danger` row highlighting for access-denied events

Invoice Governance enhancements:
- **Access History tab**: Shows all access events for the invoice with actor, role, permission, granted/denied status
- **RBAC badges** in timeline entries: role, permission, granted/denied icons
- **Status change** display in timeline events
- **Field correction** display in timeline events
- **Trace ID** display per timeline entry

Role-based visibility: ADMIN and AUDITOR see full agent trace data and access history.

---

## 14. API Reference

All APIs are under `/api/v1/` using Django REST Framework.

### 14.1 Documents API (`/api/v1/documents/`)

| Endpoint | Method | Description |
|---|---|---|
| `uploads/` | GET, POST | List/create document uploads (multipart file) |
| `uploads/{id}/` | GET, PUT, DELETE | Document upload detail |
| `invoices/` | GET | List invoices (filterable, searchable) |
| `invoices/{id}/` | GET | Invoice detail with line items |
| `purchase-orders/` | GET | List POs |
| `purchase-orders/{id}/` | GET | PO detail with line items |
| `grns/` | GET | List GRNs |
| `grns/{id}/` | GET | GRN detail with line items |

### 14.2 Reconciliation API (`/api/v1/reconciliation/`)

| Endpoint | Method | Description |
|---|---|---|
| `configs/` | GET, POST, PUT | Reconciliation config (admin-only) |
| `policies/` | GET, POST, PUT, DELETE | Reconciliation policies (admin-only, filterable by mode/vendor) |
| `runs/` | GET | List reconciliation runs |
| `runs/{id}/` | GET | Run detail with counts |
| `results/` | GET | List results (filterable by match_status, mode) |
| `results/{id}/` | GET | Result detail with line results + exceptions |

### 14.3 Agents API (`/api/v1/agents/`)

| Endpoint | Method | Description |
|---|---|---|
| `definitions/` | GET, POST, PUT | Agent definitions (CRUD) |
| `runs/` | GET | List agent runs |
| `runs/{id}/` | GET | Run detail with steps, messages, decisions |

### 14.4 Reviews API (`/api/v1/reviews/`)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | List review assignments |
| `{id}/` | GET | Assignment detail |
| `{id}/assign/` | POST | Assign reviewer |
| `{id}/start/` | POST | Start review |
| `{id}/decide/` | POST | Submit decision (approve/reject/reprocess) |
| `{id}/comment/` | POST | Add comment |

### 14.5 Governance API (`/api/v1/governance/`)

| Endpoint | Method | Description |
|---|---|---|
| `invoices/{id}/audit-history/` | GET | Full audit trail for invoice |
| `invoices/{id}/agent-trace/` | GET | Agent runs with steps, tools, decisions |
| `invoices/{id}/recommendations/` | GET | Agent recommendations |
| `invoices/{id}/timeline/` | GET | Unified case timeline |
| `invoices/{id}/access-history/` | GET | RBAC access events for invoice (who accessed, permission, granted/denied) |
| `cases/{id}/stage-timeline/` | GET | Stage-centric timeline for a case |
| `permission-denials/` | GET | Recent permission denial events (filterable by user/date) |
| `rbac-activity/` | GET | RBAC-related audit events (role changes, permission checks) |
| `agent-performance/` | GET | Agent run performance summary (counts, durations, success rates) |

### 14.6 Dashboard API (`/api/v1/dashboard/`)

| Endpoint | Method | Description |
|---|---|---|
| `summary/` | GET | KPI summary |
| `match-status/` | GET | Match status breakdown |
| `exceptions/` | GET | Exception type distribution |
| `mode-breakdown/` | GET | 2-Way vs 3-Way split |
| `agent-performance/` | GET | Per-agent metrics |
| `daily-volume/` | GET | Daily processing volume |
| `recent-activity/` | GET | Recent events |

### 14.7 Accounts / RBAC API (`/api/v1/accounts/`)

| Endpoint | Method | Description | Permission |
|---|---|---|---|
| `users/` | GET | List users (search, filter by role/dept/status) | `users.manage` |
| `users/{id}/` | GET, PUT | User detail / update | `users.manage` |
| `users/{id}/roles/` | GET | User's role assignments | `users.manage` |
| `users/{id}/assign-role/` | POST | Assign role to user | `users.manage` |
| `users/{id}/remove-role/` | POST | Remove role from user | `users.manage` |
| `users/{id}/overrides/` | GET | User's permission overrides | `users.manage` |
| `users/{id}/create-override/` | POST | Add permission override | `users.manage` |
| `users/{id}/remove-override/` | POST | Remove permission override | `users.manage` |
| `roles/` | GET, POST | List/create roles | `roles.manage` |
| `roles/{id}/` | GET, PUT, DELETE | Role CRUD (soft-delete for system roles) | `roles.manage` |
| `roles/{id}/clone/` | POST | Clone role with permissions | `roles.manage` |
| `permissions/` | GET | Permission catalog (read-only) | `roles.manage` |
| `role-matrix/` | GET, PUT | Full role-permission matrix (bulk update) | `roles.manage` |

### 14.7 Cases API (`/api/v1/cases/`)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | List cases (filterable by path, status, priority, vendor) |
| `{id}/` | GET | Case detail |
| `{id}/timeline/` | GET | Case timeline events |
| `{id}/artifacts/` | GET | Case artifacts |
| `{id}/decisions/` | GET | Case decisions |
| `{id}/stages/` | GET | Case stages |
| `{id}/summary/` | GET | Case summary |
| `{id}/comments/` | GET, POST | Case comments |
| `{id}/assign/` | POST | Assign case to reviewer |
| `{id}/run-stage/` | POST | Re-run specific stage |
| `{id}/reroute-path/` | POST | Change processing path |
| `{id}/copilot-chat/` | POST | Q&A assistant for case |
| `stats/` | GET | Aggregate statistics |

### 14.8 Vendors API (`/api/v1/vendors/`)

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET, POST | List/create vendors (filterable by country/currency) |
| `{id}/` | GET, PUT, DELETE | Vendor detail with aliases |

### 14.9 Copilot API (`/api/v1/copilot/`)

| Endpoint | Method | Description | Permission |
|---|---|---|---|
| `session/start/` | POST | Start or resume a session | `agents.use_copilot` |
| `sessions/` | GET | List user's sessions | `agents.use_copilot` |
| `session/<id>/` | GET, PATCH | Session detail; PATCH actions: archive, pin, link_case | `agents.use_copilot` |
| `session/<id>/messages/` | GET | Paginated message history | `agents.use_copilot` |
| `chat/` | POST | Send message and receive structured response | `agents.use_copilot` |
| `case/<id>/context/` | GET | Full case context bundle | `cases.view` |
| `case/<id>/timeline/` | GET | Case timeline | `cases.view` |
| `case/<id>/evidence/` | GET | Evidence cards | `cases.view` |
| `case/<id>/governance/` | GET | Governance data (privileged roles only) | `cases.view` |
| `suggestions/` | GET | Role-aware suggested prompts | `agents.use_copilot` |
| `cases/search/` | GET | Case search by keyword/status | `cases.view` |

### 14.10 ERP Integration API (`/api/v1/erp/`)

| Endpoint | Method | Description |
|---|---|---|
| `resolve/<resolution_type>/` | GET, POST | On-demand ERP reference resolution (vendor/item/tax/cost_center/po/grn/duplicate_invoice) |

### 14.11 Posting API (`/api/v1/posting/`)

| Endpoint | Method | Description |
|---|---|---|
| `postings/` | GET | List invoice postings (filter: status, review_queue) |
| `postings/{id}/` | GET | Posting detail with corrections |
| `postings/{id}/approve/` | POST | Approve posting (optional field corrections) |
| `postings/{id}/reject/` | POST | Reject posting with reason |
| `postings/{id}/submit/` | POST | Submit to ERP (Phase 1 mock) |
| `postings/{id}/retry/` | POST | Retry failed posting |
| `prepare/` | POST | Trigger posting pipeline for an invoice (async, returns 202) |

### 14.12 Posting Core API (`/api/v1/posting-core/`)

| Endpoint | Method | Description |
|---|---|---|
| `runs/` | GET | List posting runs (filter: invoice, status) |
| `runs/{id}/` | GET | Run detail (field values, lines, issues, evidence) |
| `upload/` | POST | Upload ERP reference Excel/CSV (multipart) |
| `import-batches/` | GET | List import batches |
| `vendors/` | CRUD | ERP vendor references |
| `items/` | CRUD | ERP item references |
| `tax-codes/` | CRUD | ERP tax code references |
| `cost-centers/` | CRUD | ERP cost center references |
| `po-refs/` | CRUD | ERP PO references |
| `vendor-aliases/` | CRUD | Vendor alias mappings |
| `item-aliases/` | CRUD | Item alias mappings |
| `rules/` | CRUD | Posting rules |

### 14.13 Procurement API (`/api/v1/procurement/`)

| Endpoint | Method | Description |
|---|---|---|
| `requests/` | GET, POST | List/create procurement requests |
| `requests/{id}/` | GET, PUT, DELETE | Request detail |
| `requests/{id}/run-analysis/` | POST | Trigger analysis run (RECOMMENDATION/BENCHMARK/BOTH) |
| `requests/{id}/run-validation/` | POST | Trigger validation run |
| `requests/{id}/quotations/` | GET, POST | Quotations for a request |
| `quotations/{id}/line-items/` | GET, POST | Line items for a quotation |
| `validation-rulesets/` | GET | Available validation rule sets (read-only) |
| `runs/{id}/` | GET | Analysis run detail with results |

### 14.14 API Standards

- **Authentication**: SessionAuthentication (Django sessions)
- **Pagination**: 25 per page (PageNumberPagination)
- **Filtering**: `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`
- **Permissions**: Role-based (see Section 20)

---

## 15. Template Views & UI

### 15.1 Template File Structure

```
templates/
├── base.html                          # Base layout (Bootstrap 5, navbar, sidebar)
├── accounts/
│   ├── login.html                     # Login page
│   ├── user_list.html                 # User management (search, filter, paginated)
│   ├── user_create.html               # Add new user with role assignment
│   ├── user_detail.html               # Tabbed: profile, roles, permissions, overrides
│   ├── role_list.html                 # Role management (search, user counts)
│   ├── role_create.html               # Create new custom role
│   ├── role_detail.html               # Role editor with permission checkboxes
│   ├── permission_list.html           # Permission catalog by module
│   └── role_matrix.html               # Full role×permission matrix grid
├── agents/
│   └── reference.html                 # Agent/stage reference guide
├── cases/
│   ├── case_inbox.html                # Case listing with filters
│   ├── case_console.html              # Deep-dive case investigation
│   └── partials/
│       ├── _case_sidebar.html
│       ├── _stage_timeline.html
│       ├── _copilot_panel.html
│       └── _exceptions_panel.html
├── dashboard/
│   ├── index.html                     # Main dashboard
│   └── agent_monitor.html             # Agent operations monitor
├── documents/
│   ├── invoice_list.html
│   ├── invoice_detail.html
│   ├── po_list.html
│   ├── po_detail.html
│   ├── grn_list.html
│   └── grn_detail.html
├── vendors/
│   ├── vendor_list.html             # Vendor directory with KPIs, filters
│   └── vendor_detail.html           # Vendor detail + related POs/invoices/GRNs
├── governance/
│   ├── audit_event_list.html          # Filterable audit log
│   └── invoice_governance.html        # Full governance dashboard
├── partials/
│   ├── _navbar.html
│   ├── _sidebar.html
│   ├── _pagination.html
│   └── _upload_modal.html
├── reconciliation/
│   ├── result_list.html               # Results with "Start Reconciliation" panel
│   ├── result_detail.html             # Result detail + agent trace
│   ├── case_console.html              # Legacy investigation view
│   └── settings.html                  # Tolerance config viewer
└── reviews/
    ├── assignment_list.html           # Review queue + bulk assign
    └── assignment_detail.html         # Review with comments & decision
```

### 15.2 Key Template Views

| URL | View | Description |
|---|---|---|
| `/` | redirect | Redirects to `/dashboard/` |
| `/dashboard/` | `dashboard_view` | Summary cards + recent activity |
| `/dashboard/agents/` | `agent_monitor_view` | KPIs + agent activity |
| `/cases/` | `case_inbox` | Filterable case listing |
| `/cases/<id>/` | `case_console` | Case investigation with stages, decisions, timeline |
| `/cases/<id>/reprocess/` | `reprocess_case` | Re-run from specific stage |
| `/invoices/` | `invoice_list` | Invoice listing |
| `/invoices/<id>/` | `invoice_detail` | Invoice detail with extraction data |
| `/purchase-orders/` | `po_list` | PO listing |
| `/grns/` | `grn_list` | GRN listing |
| `/reconciliation/` | `result_list` | Reconciliation results |
| `/reconciliation/<id>/` | `result_detail` | Result detail with agent trace |
| `/reconciliation/settings/` | `reconciliation_settings` | Tolerance config viewer |
| `/reviews/` | `assignment_list` | Review queue |
| `/reviews/<id>/` | `assignment_detail` | Review detail with decision |
| `/governance/` | `audit_event_list` | Audit log |
| `/governance/invoice/<id>/` | `invoice_governance` | Full governance dashboard |
| `/vendors/` | `vendor_list` | Vendor directory with filters |
| `/vendors/<id>/` | `vendor_detail` | Vendor detail + related documents |
| `/agents/reference/` | `agent_reference` | Agent/stage reference page |
| `/accounts/login/` | Django LoginView | Authentication |
| `/accounts/admin-console/users/` | `UserListView` | User management list |
| `/accounts/admin-console/users/new/` | `UserCreateView` | Create new user |
| `/accounts/admin-console/users/<id>/` | `UserDetailView` | User detail/edit (tabs) |
| `/accounts/admin-console/roles/` | `RoleListView` | Role management list |
| `/accounts/admin-console/roles/new/` | `RoleCreateView` | Create new role |
| `/accounts/admin-console/roles/<id>/` | `RoleDetailView` | Role detail/permission editor |
| `/accounts/admin-console/permissions/` | `PermissionListView` | Permission catalog |
| `/accounts/admin-console/role-matrix/` | `RolePermissionMatrixView` | Role-permission matrix |
| `/copilot/` | `copilot_workspace` | AP Copilot main workspace with session list and suggestions |
| `/copilot/case/<id>/` | `copilot_case` | Case-linked copilot workspace (auto-starts/resumes session) |
| `/copilot/session/<id>/` | `copilot_session` | Resume a specific copilot session |
| `/posting/` | `posting_workbench` | Invoice posting list with KPIs, filters, pagination |
| `/posting/<id>/` | `posting_detail` | Posting detail with proposal, issues, and actions |
| `/posting/<id>/approve/` | `posting_approve` | Approve posting |
| `/posting/<id>/reject/` | `posting_reject` | Reject posting |
| `/posting/<id>/submit/` | `posting_submit` | Submit to ERP |
| `/posting/<id>/retry/` | `posting_retry` | Retry failed posting |
| `/posting/imports/` | `reference_import_list` | ERP reference import batch history |
| `/procurement/` | `request_list` | Procurement request listing |
| `/procurement/new/` | `request_create` | Create procurement request |
| `/procurement/<id>/` | `request_workspace` | Request detail with analysis runs |
| `/procurement/runs/<id>/` | `run_detail` | Analysis run detail with results |

### 15.3 Static Assets

| File | Purpose |
|---|---|
| `static/css/design-tokens.css` | Design system variables |
| `static/css/app.css` | Global styles |
| `static/css/dashboard.css` | Dashboard-specific |
| `static/css/reviews.css` | Review pages |
| `static/css/case-console.css` | Case console |
| `static/css/agent-monitor.css` | Agent monitor |
| `static/css/case-inbox.css` | Case inbox |
| `static/js/app.js` | Global JavaScript |
| `static/js/case-console.js` | Case console interactions |
| `static/js/case-console-v2.js` | Enhanced case console |
| `static/js/copilot-panel.js` | Copilot chat panel |

---

## 16. Celery Tasks

| Task | App | Purpose | Settings |
|---|---|---|---|
| `process_invoice_upload_task` | extraction | Full extraction pipeline (OCR -> parse -> validate -> persist) | bind=True, max_retries=3, acks_late=True |
| `run_reconciliation_task` | reconciliation | Batch reconciliation run (2-way/3-way matching) | bind=True, max_retries=2 |
| `reconcile_single_invoice_task` | reconciliation | Single invoice convenience wrapper | bind=True |
| `run_agent_pipeline_task` | agents | Execute agent pipeline for non-MATCHED results; accepts optional `actor_user_id` for RBAC propagation | bind=True, max_retries=2 |
| `process_case_task` | cases | Run CaseOrchestrator for APCase lifecycle | bind=True, max_retries=3, acks_late=True |
| `reprocess_case_from_stage_task` | cases | Reprocess case from specific stage | bind=True |
| `prepare_posting_task` | posting | Run PostingPipeline for an invoice; triggered automatically on extraction approval | bind=True, max_retries=2 |
| `import_reference_excel_task` | posting | Import ERP reference data (vendor/item/tax/cost_center/po) from uploaded Excel/CSV | bind=True |
| `run_analysis_task` | procurement | Execute recommendation or benchmarking analysis run | bind=True, max_retries=2 |
| `run_validation_task` | procurement | Execute procurement validation run (6 dimensions) | bind=True |

**Windows Development**: `CELERY_TASK_ALWAYS_EAGER=True` (default) runs tasks synchronously without Redis.

---

## 17. Seed Data & Management Commands

### 17.1 Commands

| Command | Flags | Purpose |
|---|---|---|
| `python manage.py seed_config` | `--flush` | Foundation data: 6 users, 7 agent definitions, 6 tool definitions, reconciliation config, 7 policies |
| `python manage.py seed_rbac` | `--sync-users` | 6 RBAC roles (incl. SYSTEM_AGENT), 40 permissions, role-permission matrix; `--sync-users` maps existing users to RBAC roles |
| `python manage.py seed_prompts` | `--force` | 12 PromptTemplate records from registry defaults; `--force` overwrites existing |
| `python manage.py seed_ap_data` | `--reset --mode demo\|qa\|large --summary --seed N` | Realistic Saudi McDonald's AP case data (30 demo / +50 qa / +200 large scenarios) |
| `python manage.py create_cases_for_existing_invoices` | `--process` | Backfill APCase records for existing invoices; `--process` auto-runs pipeline |

**Recommended seed order:**
```bash
python manage.py seed_config --flush     # 1. Users, agents, tools, recon config
python manage.py seed_rbac --sync-users   # 2. RBAC roles & permissions
python manage.py seed_prompts --force     # 3. Prompt templates
python manage.py seed_ap_data --reset --summary  # 4. Full AP case data + observability
```

### 17.2 `seed_config` Details

Creates platform foundation data:
- **6 users**: admin, ap_processor, reviewer, finance_mgr, auditor, demo_user
- **7 agent definitions** with `config_json` & `allowed_tools` per agent type (INVOICE_UNDERSTANDING, PO_RETRIEVAL, GRN_RETRIEVAL, RECONCILIATION_ASSIST, EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
- **6 tool definitions**: po_lookup, grn_lookup, vendor_search, invoice_details, exception_list, reconciliation_summary
- **1 ReconciliationConfig**: default tolerances (strict: 2%/1%/1%, auto-close: 5%/3%/3%)
- **7 ReconciliationPolicies**: vendor/category/location-based mode mappings

`--flush` deletes policies, config, tool defs, agent defs before recreating (users are preserved).

### 17.3 `seed_ap_data` Details

Creates realistic McDonald's Saudi Arabia AP data in a **6-stage pipeline**:

| Stage | What's Created | Key Counts (demo mode) |
|---|---|---|
| 1. Users | 10 users (admin, processors, reviewers, finance, auditor) | 10 |
| 2. Vendors | Vendors + Arabic aliases across 5 KSA regions | 30 vendors, 81 aliases |
| 3. Transactional data | POs, GRNs, Invoices with line items per scenario | 20 POs, 13 GRNs, 30 invoices |
| 4. Cases & recon | APCase, ReconciliationRun/Result/Exception, stages, decisions, artifacts | 30 cases, 21 results, 22 exceptions |
| 5. Agent & review | AgentRun, Recommendations, ReviewAssignment, Comments, Summaries, AuditEvents | 120 runs, 15 reviews, 125 audit events |
| 6. Observability | AgentStep, AgentMessage, ToolCall, DecisionLog, AgentEscalation, ProcessingLog, ManualReviewAction; enriches AgentRun (trace_id, tokens, cost) & AuditEvent (RBAC, cross-refs) | 280 steps, 568 messages, 137 tool calls, 78 decisions, 193 proc logs |

**30 deterministic scenarios** covering:
- **TWO_WAY (1–8)**: Perfect match, amount/price/qty/tax mismatch, PO not found, duplicate, auto-close band, escalation
- **THREE_WAY (9–16)**: Perfect 3-way, receipt shortage, over-delivery, missing GRN, multi-GRN, low-confidence extraction
- **NON_PO (17–24)**: Government fees, pest control, marketing, staffing, training — no PO reference
- **Cross-cutting (25–30)**: Escalated multi-exception, early pipeline stages, rejected, in-review

**Modes**: `--mode demo` (30 scenarios), `--mode qa` (+50 random), `--mode large` (+200 random).

`--reset` performs a full flush of all AP-related data (cases, recon, agents, reviews, audit, documents, vendors) before seeding.

### 17.4 Seed Helpers Architecture

Seed data helpers live in `apps/cases/management/commands/seed_helpers/`:

| File | Purpose |
|---|---|
| `constants.py` | 30 vendors, 10 users, 5 regions, 12 branches, 9 line-item categories, 30 scenario definitions |
| `master_data.py` | `seed_users()`, `seed_vendors()`, `seed_vendor_aliases()` |
| `transactional_data.py` | `create_transactional_data()` — POs, GRNs, Invoices with line items per scenario |
| `case_builder.py` | `create_cases_and_recon()` — APCase, ReconciliationRun/Result/Exception, stages, decisions, artifacts |
| `agent_review_data.py` | `seed_agent_review_data()` — AgentRun, Recommendations, ReviewAssignment, Comments, Summaries, AuditEvents |
| `observability_data.py` | `seed_observability_data()` — AgentStep, AgentMessage, ToolCall, DecisionLog, Escalation, ProcessingLog, ManualReviewAction; trace/RBAC enrichment |
| `bulk_generator.py` | `generate_bulk_scenarios()` — random scenario generation for qa/large modes |

### 17.5 Observability Data (Stage 6)

The observability seeder (`observability_data.py`) creates full traceability records:

| Model | Per-Case Data | Total (demo) |
|---|---|---|
| **AgentStep** | 2–3 ReAct loop steps per agent run (action, input/output, duration) | ~280 |
| **AgentMessage** | 3–5 LLM conversation messages (system, user, assistant, tool) per run | ~568 |
| **ToolCall** | 1–2 tool invocations per relevant agent run with realistic I/O payloads | ~137 |
| **DecisionLog** | 2–3 decisions: MODE_RESOLUTION, MATCH_DETERMINATION, ROUTING_DECISION/AUTO_CLOSE | ~78 |
| **AgentEscalation** | For ESCALATED cases — severity, reason, suggested assignee role | ~2 |
| **ProcessingLog** | 5–8 structured log entries tracing the pipeline lifecycle | ~193 |
| **ManualReviewAction** | CORRECT_FIELD, ESCALATE, REJECT, ADD_COMMENT for reviewed cases | ~9 |

**Enrichment applied to existing records:**
- **AgentRun**: `trace_id`, `span_id`, `llm_model_used` (gpt-4o), `prompt_tokens`, `completion_tokens`, `cost_estimate`, `invocation_reason`, `permission_checked`
- **AuditEvent**: `trace_id`, `span_id`, `actor_email`, `actor_primary_role`, `actor_roles_snapshot_json`, `permission_checked`, `permission_source`, `status_before`/`status_after`, `duration_ms`, cross-refs (`invoice_id`, `case_id`, `reconciliation_result_id`, `review_assignment_id`)
- **DecisionLog**: `trace_id`, `rule_name`, `rule_version`, `config_snapshot_json` (tolerance bands), `policy_code`, `actor_primary_role`

All trace IDs are consistent per case lifecycle — a single `trace_id` links all records for one case across AgentRun → AuditEvent → DecisionLog → ProcessingLog.

### 17.6 Production Seed Scripts

One-off scripts created to seed PO and GRN data for specific production cases (execute via `python manage.py shell < scripts/<name>.py`):

| Script | Purpose |
|---|---|
| `scripts/query_case.py` | Query any case by case_number — prints invoice, PO, vendor, line items |
| `scripts/query_po.py` | Query invoice/case by PO number on any environment |
| `scripts/seed_case_0012.py` | AP-260316-0012 — creates PO 2601017 (ID=5) + GRN GRN-2601017-001 (ID=6); Al-Safi Danone, 4 lines, SAR 212,400 |
| `scripts/fix_po_amounts.py` | Corrects PO 2601017 line amounts to include tax: 85500→98325, 48000→55200, 19500→22425, 33000→37950 |
| `scripts/fix_po_total.py` | Corrects PO 2601017 header total: 241800→212400 |
| `scripts/seed_case_0013.py` | AP-260316-0013 — creates PO 2601015 (ID=6) + GRN GRN-2601015-001 (ID=7); NADEC, 2 lines, SAR 238,700 |
| `scripts/seed_case_0016.py` | AP-260316-0016 — creates PO 2601006 (ID=7) + GRN GRN-2601006-001 (ID=8); 5 lines (cleaning/hygiene), SAR 146,512.50 |
| `scripts/seed_case_0014.py` | AP-260316-0014 — creates PO 2601005 (ID=8) + GRN GRN-2601005-001 (ID=9); 5 lines (MCD packaging), SAR 197,355 |

**Pattern for running on production:**
```bash
scp scripts/<name>.py finance-agents:/opt/finance-agents/scripts/<name>.py
ssh finance-agents "cd /opt/finance-agents && source venv/bin/activate && python manage.py shell < scripts/<name>.py"
```

---

## 18. Prompt Registry

### 18.1 Architecture

`PromptRegistry` (`apps/core/prompt_registry.py`) provides centralized prompt management with a 3-tier lookup:
1. **Cache** (in-memory) — fastest
2. **Database** (PromptTemplate model) — configurable via admin
3. **Defaults** (hardcoded) — fallback guarantee

### 18.2 Registered Prompts (18 defaults)

| Slug | Category | Purpose |
|---|---|---|
| `extraction.invoice_system` | extraction | System prompt for invoice data extraction (legacy single-prompt path) |
| `extraction.invoice_base` | extraction | Base extraction instructions (Phase 2 composition base) |
| `extraction.invoice_category_goods` | extraction | Goods invoice category overlay |
| `extraction.invoice_category_service` | extraction | Service invoice category overlay |
| `extraction.invoice_category_travel` | extraction | Travel invoice category overlay |
| `extraction.country_india_gst` | extraction | India GST-specific extraction rules |
| `extraction.country_generic_vat` | extraction | Generic VAT country overlay |
| `agent.invoice_understanding` | agent | Invoice understanding agent system prompt |
| `agent.po_retrieval` | agent | PO retrieval agent system prompt |
| `agent.grn_retrieval` | agent | GRN retrieval agent system prompt |
| `agent.reconciliation_assist` | agent | Reconciliation assist agent system prompt |
| `agent.exception_analysis` | agent | Exception analysis agent system prompt |
| `agent.review_routing` | agent | Review routing agent system prompt |
| `agent.case_summary` | agent | Case summary agent system prompt |
| `case.path_resolution` | case | Path resolution guidance |
| `case.exception_analysis` | case | Case exception analysis |
| `case.review_routing` | case | Review routing guidance |
| `case.copilot_system` | case | Copilot chat system prompt |
| `case.copilot_user` | case | Copilot chat user template |

All prompts are pushed to Langfuse via `python manage.py push_prompts_to_langfuse`. Dots in slug names are replaced with dashes for Langfuse naming (`extraction.invoice_base` -> `extraction-invoice_base`).

**Phase 2 prompt composition** (`InvoicePromptComposer`):
```
extraction.invoice_base
  + extraction.invoice_category_{goods|service|travel}
  + extraction.country_{jurisdiction}_{regime}   (e.g. country_india_gst)
  = final_prompt -> InvoiceExtractionAgent
```

`PromptRegistry` resolution per component: Langfuse (60s TTL) -> DB (PromptTemplate) -> hardcoded defaults.

### 18.3 Standard Agent Output Format

All agents produce JSON following this schema:
```json
{
  "reasoning": "Step-by-step analysis...",
  "recommendation_type": "AUTO_CLOSE | SEND_TO_AP_REVIEW | ...",
  "confidence": 0.85,
  "decisions": [
    {"type": "...", "rationale": "...", "evidence": "..."}
  ],
  "evidence": {
    "key": "value"
  }
}
```

---

## 19. Configuration Reference

### 19.1 Core Settings (`config/settings.py`)

| Setting | Default | Description |
|---|---|---|
| **Database** | MySQL utf8mb4 | Primary data store |
| **AUTH_USER_MODEL** | `accounts.User` | Custom email-based user |
| **LOGIN_URL** | `/accounts/login/` | Login redirect |
| **REST_FRAMEWORK.PAGE_SIZE** | 25 | API pagination |
| **CELERY_BROKER_URL** | `redis://localhost:6379/0` | Redis broker |
| **CELERY_TASK_ALWAYS_EAGER** | `True` | Sync mode for Windows dev |

### 19.2 Tolerance Constants (`apps/core/constants.py`)

| Constant | Value | Purpose |
|---|---|---|
| `DEFAULT_QTY_TOLERANCE_PCT` | 2.0% | Strict quantity tolerance |
| `DEFAULT_PRICE_TOLERANCE_PCT` | 1.0% | Strict price tolerance |
| `DEFAULT_AMOUNT_TOLERANCE_PCT` | 1.0% | Strict amount tolerance |
| `AUTO_CLOSE_QTY_TOLERANCE_PCT` | 5.0% | Auto-close quantity band |
| `AUTO_CLOSE_PRICE_TOLERANCE_PCT` | 3.0% | Auto-close price band |
| `AUTO_CLOSE_AMOUNT_TOLERANCE_PCT` | 3.0% | Auto-close amount band |
| `EXTRACTION_CONFIDENCE_THRESHOLD` | 0.75 | Minimum extraction confidence |
| `MAX_UPLOAD_SIZE_MB` | 25 | Upload size limit |
| `FUZZY_MATCH_THRESHOLD` | 80 | Description similarity threshold |
| `AGENT_MAX_RETRIES` | 2 | Agent retry limit |
| `AGENT_TIMEOUT_SECONDS` | 120 | Agent timeout |
| `AGENT_CONFIDENCE_THRESHOLD` | 0.70 | Minimum agent confidence for action |
| `REVIEW_AUTO_CLOSE_THRESHOLD` | 0.95 | Auto-close review confidence |
| `DEFAULT_PAGE_SIZE` | 25 | API pagination |
| `DEFAULT_RECONCILIATION_MODE` | THREE_WAY | Default matching mode |
| `DEFAULT_CURRENCY` | USD | Default currency |
| `POSTING_REFERENCE_FRESHNESS_HOURS` | 168 | Hours before ERP reference data is considered stale |
| `ERP_CACHE_TTL_SECONDS` | 3600 | Seconds for ERP lookup cache TTL |
| `EXTRACTION_AUTO_APPROVE_ENABLED` | False | Enable auto-approval of extractions at threshold |
| `EXTRACTION_AUTO_APPROVE_THRESHOLD` | 0.90 | Confidence floor for auto-approval |

### 19.3 Allowed Upload Extensions

`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`

### 19.4 Environment Variables

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI authentication |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name |
| `AZURE_DI_ENDPOINT` | Azure Document Intelligence endpoint |
| `AZURE_DI_KEY` | Azure Document Intelligence key |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Blob Storage connection |
| `AZURE_STORAGE_CONTAINER_NAME` | Blob container name |
| `OPENAI_API_KEY` | OpenAI API key (non-Azure fallback) |
| `SECRET_KEY` | Django secret key |
| `DATABASE_URL` | Database connection string |
| `REDIS_URL` | Redis connection URL |
| `CELERY_TASK_ALWAYS_EAGER` | Run tasks synchronously (True/False) |
| `EXTRACTION_AUTO_APPROVE_ENABLED` | Enable auto-approval of high-confidence extractions |
| `EXTRACTION_AUTO_APPROVE_THRESHOLD` | Confidence threshold for auto-approval (default 0.90) |
| `POSTING_REFERENCE_FRESHNESS_HOURS` | Max ERP reference age before staleness warnings (default 168h) |
| `ERP_CACHE_TTL_SECONDS` | TTL for ERP resolution cache records (default 3600) |
| `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` | Confidence threshold for ERP duplicate invoice fallback (default 0.8) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (enables LLM observability tracing) |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key |
| `LANGFUSE_HOST` | Langfuse server URL (e.g. `https://us.cloud.langfuse.com`) |

---

## 20. Security & Permissions

### 20.1 Authentication

- Custom User model with **email-based login** (no username field)
- Django session authentication for web UI
- DRF SessionAuthentication for API
- `LoginRequiredMiddleware` redirects anonymous users to `/accounts/login/`
- `RBACMiddleware` pre-loads role codes and effective permissions per request (warm cache)
- Exempt paths: `/admin/`, `/accounts/`, `/api/`

### 20.2 Enterprise RBAC System

The platform implements a full enterprise RBAC (Role-Based Access Control) system layered on top of the original single-role model.

#### Architecture

| Component | File | Purpose |
|---|---|---|
| **RBAC Models** | `apps/accounts/rbac_models.py` | Role, Permission, RolePermission, UserRole, UserPermissionOverride, MenuConfig |
| **Permission Engine** | `apps/core/permissions.py` | DRF classes, CBV mixins, FBV decorators — all RBAC-backed |
| **Middleware** | `apps/core/middleware.py` | `RBACMiddleware` pre-loads permissions into `request.user` cache |
| **Template Tags** | `apps/core/templatetags/rbac_tags.py` | `has_permission`, `has_role`, `has_any_permission`, `if_can` block tag |
| **Context Processor** | `apps/core/context_processors.py` | `rbac_context` injects `user_permissions`, `user_role_codes`, `is_admin` |
| **Audit Service** | `apps/accounts/rbac_services.py` | `RBACEventService` logs all RBAC changes to `AuditEvent` |
| **Seed Command** | `apps/accounts/management/commands/seed_rbac.py` | Seeds roles, permissions, matrix; syncs legacy users |

#### Permission Resolution Order

```
1. Admin bypass → all permissions granted
2. User DENY overrides → explicitly blocked
3. User ALLOW overrides → explicitly granted
4. Role permissions → union of all active role permissions
5. Legacy fallback → uses User.role field if no UserRole entries exist
```

#### Seeded Roles & Permission Matrix

| Role | Rank | Key Permissions |
|---|---|---|
| **ADMIN** | 10 | All 40 permissions |
| **FINANCE_MANAGER** | 20 | invoices.view, reconciliation.view/override, cases.view/assign/escalate/add_comment, reviews.view/assign/decide, governance.view, agents.view/orchestrate, users.manage, roles.manage, purchase_orders.view, grns.view, vendors.view, recommendations.auto_close/route_review/escalate/reprocess/route_procurement/vendor_clarification |
| **AUDITOR** | 30 | *.view (read-only across all modules), governance.view, vendors.view, purchase_orders.view, grns.view |
| **REVIEWER** | 40 | invoices.view, reconciliation.view, cases.view/add_comment, reviews.view/decide, agents.view/use_copilot, governance.view, purchase_orders.view, grns.view, vendors.view, recommendations.route_review |
| **AP_PROCESSOR** | 50 | invoices.view/create/edit/trigger_reconciliation, reconciliation.view/run, reviews.view, cases.view/edit/add_comment, agents.view/use_copilot, purchase_orders.view*, grns.view*, vendors.view* |
| **SYSTEM_AGENT** | 100 | agents.orchestrate + all agents.run_* + purchase_orders.view, grns.view, vendors.view, invoices.view, reconciliation.view + recommendations.auto_close/route_review/escalate/reprocess + cases.escalate |

*\* AP_PROCESSOR: POs, GRNs, and Vendors are **scoped** to data linked to their own uploaded invoices (unless `ap_processor_sees_all_cases` is enabled in ReconciliationConfig).*

*\*\* SYSTEM_AGENT: Dedicated service account (`system-agent@internal`) for autonomous agent operations. `is_system_role=True`, rank 100. Used by `AgentGuardrailsService.resolve_actor()` when no human user context is available.*

#### Permission Codes (40 total, 14 modules)

| Module | Permissions |
|---|---|
| invoices | `view`, `create`, `edit`, `delete`, `trigger_reconciliation` |
| reconciliation | `view`, `run`, `override` |
| cases | `view`, `edit`, `add_comment`, `assign`, `escalate` |
| reviews | `view`, `assign`, `decide` |
| governance | `view` |
| agents | `view`, `use_copilot`, `orchestrate`, `run_extraction`, `run_po_retrieval`, `run_grn_retrieval`, `run_exception_analysis`, `run_reconciliation_assist`, `run_review_routing`, `run_case_summary` |
| config | `manage` |
| users | `manage` |
| roles | `manage` |
| vendors | `view` |
| purchase_orders | `view` |
| grns | `view` |
| recommendations | `auto_close`, `route_review`, `escalate`, `reprocess`, `route_procurement`, `vendor_clarification` |
| extraction | `reprocess` |

#### Data Scoping (AP_PROCESSOR)

When `ReconciliationConfig.ap_processor_sees_all_cases` is **off** (default), AP_PROCESSOR users see only data related to their own uploaded invoices:

| Entity | Scoping Logic |
|---|---|
| **Invoices** | `document_upload__uploaded_by=user` |
| **Purchase Orders** | POs matching `po_number` values from user's invoices |
| **GRNs** | GRNs linked to POs from user's invoices |
| **Vendors** | Vendors linked to user's invoices (`vendor_id`) |
| **Dashboard KPIs** | All aggregations scoped through the above filters |

All other roles (ADMIN, FINANCE_MANAGER, AUDITOR, REVIEWER) see the full unscoped data.

### 20.3 DRF Permission Classes (Backward-Compatible)

| Permission Class | RBAC Backing | Notes |
|---|---|---|
| `IsAdmin` | `user.role == ADMIN` or RBAC admin | Preserved original |
| `IsAPProcessor` | `has_role(AP_PROCESSOR)` | Preserved original |
| `IsReviewer` | `has_role(REVIEWER)` | Preserved original |
| `IsFinanceManager` | `has_role(FINANCE_MANAGER)` | Preserved original |
| `IsAuditor` | `has_role(AUDITOR)` | Preserved original |
| `IsAdminOrReadOnly` | Read: any, Write: admin | Preserved original |
| `IsReviewAssignee` | Assignment check + admin/FM | Preserved original |
| `HasAnyRole` | Configurable `allowed_roles` | Preserved original |
| **`HasPermissionCode`** | `user.has_permission(code)` | **New** — code-level check |
| **`HasAnyPermission`** | Any of listed codes | **New** |
| **`HasRole`** | RBAC role check | **New** |

### 20.4 Django View Mixins & Decorators

| Helper | Type | Usage |
|---|---|---|
| `PermissionRequiredMixin` | CBV mixin | `required_permission = "invoices.view"` |
| `AnyPermissionRequiredMixin` | CBV mixin | `required_permissions = ["invoices.view", "cases.view"]` |
| `RoleRequiredMixin` | CBV mixin | `required_role = "ADMIN"` |
| `@permission_required_code("code")` | FBV decorator | Function view permission check |
| `@role_required("ADMIN")` | FBV decorator | Function view role check |

### 20.5 Template Tags

```django
{% load rbac_tags %}

{% has_permission "invoices.view" as can_view %}
{% if can_view %}<a href="...">Invoices</a>{% endif %}

{% has_role "ADMIN" as is_admin %}
{% has_any_permission "invoices.view,cases.view" as can_see %}

{% if_can "reconciliation.run" %}
  <button>Run Reconciliation</button>
{% end_if_can %}
```

### 20.6 Case Permissions

| Permission | Description |
|---|---|
| `CanViewCase` | ADMIN, AUDITOR, FINANCE_MANAGER, AP_PROCESSOR, REVIEWER |
| `CanEditCase` | ADMIN, AP_PROCESSOR |
| `CanAssignCase` | ADMIN, FINANCE_MANAGER |
| `CanUseCopilot` | ADMIN, AP_PROCESSOR, REVIEWER |

### 20.7 RBAC Audit Events

| Event Type | Trigger |
|---|---|
| `ROLE_ASSIGNED` | Role assigned to user |
| `ROLE_REMOVED` | Role removed from user |
| `ROLE_PERMISSION_CHANGED` | Permissions added/removed from role |
| `USER_PERMISSION_OVERRIDE` | User-level ALLOW/DENY override added |
| `USER_ACTIVATED` | User activated |
| `USER_DEACTIVATED` | User deactivated |
| `ROLE_CREATED` | New role created |
| `ROLE_UPDATED` | Role metadata updated |
| `PRIMARY_ROLE_CHANGED` | User's primary role changed |

#### Agent Guardrail Audit Events

| Event Type | Trigger |
|---|---|
| `GUARDRAIL_GRANTED` | Agent operation authorized by guardrails service |
| `GUARDRAIL_DENIED` | Agent operation denied by guardrails service |
| `TOOL_CALL_AUTHORIZED` | Tool execution authorized for actor |
| `TOOL_CALL_DENIED` | Tool execution denied for actor |
| `RECOMMENDATION_ACCEPTED` | Recommendation acceptance authorized |
| `RECOMMENDATION_DENIED` | Recommendation acceptance denied |
| `AUTO_CLOSE_AUTHORIZED` | Auto-close action authorized |
| `AUTO_CLOSE_DENIED` | Auto-close action denied |
| `SYSTEM_AGENT_USED` | SYSTEM_AGENT identity resolved for autonomous run |

### 20.8 Admin Console UI

Full Bootstrap 5 management screens at `/accounts/admin-console/`:

| Screen | URL | Features |
|---|---|---|
| User List | `/users/` | Search, filter by role/dept/status, pagination, Add User button |
| User Create | `/users/new/` | Email, name, password, department, initial role |
| User Detail | `/users/<id>/` | Profile edit, role assign/remove/set-primary, overrides, activate/deactivate |
| Role List | `/roles/` | Search, user counts, system/custom badges |
| Role Create | `/roles/new/` | Code, name, description, rank |
| Role Detail | `/roles/<id>/` | Edit metadata, permission checkboxes by module (Select All / Clear All) |
| Permission Catalog | `/permissions/` | Grouped by module, shows granted-to roles |
| Role Matrix | `/role-matrix/` | Full role×permission grid with bulk save |

Sidebar navigation shows Admin Console links only to users with `users.manage` or `roles.manage` permissions.

### 20.9 Soft Delete

Business entities use `SoftDeleteMixin` (is_active flag) — never hard-delete. This ensures auditability and data integrity.

---

## 21. Development Guide

### 21.1 Quick Start

```bash
# 1. Clone repository
git clone <repo-url>
cd 3-way-po-recon

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env  # Edit with your credentials

# 5. Run migrations
python manage.py migrate

# 6. Seed data
python manage.py seed_config
python manage.py seed_prompts
python manage.py seed_ap_data --mode=demo

# 7. Run server
python manage.py runserver
```

### 21.2 Access Points

| URL | Purpose |
|---|---|
| `http://localhost:8000/` | Dashboard (redirects from /) |
| `http://localhost:8000/admin/` | Django admin |
| `http://localhost:8000/cases/` | Case inbox |
| `http://localhost:8000/reconciliation/` | Reconciliation results |
| `http://localhost:8000/reviews/` | Review queue |
| `http://localhost:8000/governance/` | Audit log |
| `http://localhost:8000/api/v1/` | API root |

### 21.3 Windows Development Mode

Default: `CELERY_TASK_ALWAYS_EAGER=True` — tasks execute synchronously without Redis.

For async mode:
```bash
# Start Redis (via Docker or Windows installer)
# Set CELERY_TASK_ALWAYS_EAGER=False in .env
celery -A config worker -l info
```

### 21.4 Adding New Features

**New Model:**
1. Define in `apps/<app>/models.py`, inherit `BaseModel`
2. Add enums to `apps/core/enums.py`
3. Run `python manage.py makemigrations <app> && python manage.py migrate`
4. Register in `apps/<app>/admin.py`
5. Add serializer in `apps/<app>/serializers.py`
6. Add ViewSet in `apps/<app>/views.py`
7. Register routes in `apps/<app>/api_urls.py`

**New Service:**
1. Create in `apps/<app>/services/`
2. Call from task or view (never from serializer)
3. Keep stateless; accept model instances or IDs

**New Agent Type:**
1. Add enum to `AgentType` in `apps/core/enums.py`
2. Create agent class in `apps/agents/services/`, extend `BaseAgent`
3. Register in `AGENT_CLASS_REGISTRY`
4. Add to `PolicyEngine` decision logic
5. Create `AgentDefinition` record
6. Add `agents.run_<type>` permission to `seed_rbac.py` PERMISSIONS list
7. Map permission to appropriate roles in `ROLE_MATRIX` and to `SYSTEM_AGENT`
8. Add entry to `AGENT_PERMISSIONS` dict in `guardrails_service.py`

**New Tool:**
1. Create tool class in `apps/tools/registry/tools.py`, extend `BaseTool`
2. Decorate with `@register_tool`
3. Set `required_permission` (e.g., `"purchase_orders.view"`) — enforced by `AgentGuardrailsService.authorize_tool()`
4. Implement `execute()` method
5. Add `ToolDefinition` record
6. Reference in agent's `allowed_tools`

**New Template View:**
1. Create view in `apps/<app>/template_views.py`
2. Add URL in `apps/<app>/urls.py`
3. Create template in `templates/<app>/`
4. Extend `base.html`

### 21.5 Debugging Tips

| Symptom | Cause / Fix |
|---|---|
| **Celery tasks not running** | Check `CELERY_TASK_ALWAYS_EAGER=True` (Windows dev mode) |
| **LLM calls failing** | Check `AZURE_OPENAI_*` env vars |
| **Agent 400 errors from OpenAI** | Ensure tool-calling message format (tool_calls array + tool_call_id) |
| **Extraction failing** | Check `AZURE_DI_*` env vars |
| **Login redirect loop** | `LoginRequiredMiddleware` — exempt: /admin/, /accounts/, /api/ |
| **Migration issues** | MySQL requires utf8mb4 charset |
| **Template not found** | Templates in `templates/<app>/`, check TEMPLATES setting |
| **Confidence showing 1%** | `extraction_confidence` stored as 0.0–1.0; use `widthratio` in templates |

---

## 22. Status & Roadmap

### Implemented

- All data models, migrations, enums (25 core enums + 6 ERP enums), permissions, middleware
- Two-agent extraction architecture: InvoiceExtractionAgent (always, single-shot, json_object) + InvoiceUnderstandingAgent (conditional: confidence < 75%)
- **Phase 2 extraction upgrade**: `InvoiceCategoryClassifier` (goods/service/travel), `InvoicePromptComposer` (base + category + country overlays, prompt_hash), `ResponseRepairService` (5 deterministic repair rules pre-parser); wired into InvoiceExtractionAdapter
- **Invoice model extended**: vendor_tax_id, buyer_name, due_date, tax_percentage, tax_breakdown (cgst/sgst/igst/vat)
- **Multi-country extraction platform** (`apps/extraction_core/`): 13 models, 30 service classes, 60+ API endpoints, jurisdiction resolution, schema-driven extraction, evidence capture, credit system, OCR cost tracking, country pack governance, Phase 2 hardening (decision codes, recovery lane, evidence-aware confidence, prompt-source audit trail), Indian e-invoice QR code decoding (NIC JWT + plain-JSON, Azure DI barcodes, OCR IRN fallback)
- Extraction pipeline (Azure DI OCR + GPT-4o, 11 stages) with human-in-the-loop approval gate
- **Extraction Approval**: `ExtractionApproval` + `ExtractionFieldCorrection` models; approve/reject/auto-approve with configurable threshold; touchless-rate analytics
- Reconciliation engine (14 services; 2-way/3-way matching with mode resolver)
- ReconciliationPolicy model with priority-ordered mode rules; tiered tolerance (strict + auto-close bands)
- AI agent orchestration (8 agents, `ReasoningPlanner` LLM-based planner with `PolicyEngine` fallback, tool registry, LLM client with response_format support)
- `AgentOrchestrationRun` model: top-level pipeline invocation record with duplicate-run guard (RUNNING blocks re-entry), status machine (PLANNED/RUNNING/COMPLETED/PARTIAL/FAILED)
- `AgentMemory`: cross-agent structured memory propagated via `AgentContext`; pre-seeded facts and per-agent summary accumulation
- `AgentDefinition` catalog: all fields are first-class DB columns (purpose, entry_conditions, success_criteria, prohibited_actions, tool_grounding contract, lifecycle_status, etc.)
- Agent RBAC guardrails: `AgentGuardrailsService` -- central RBAC enforcement for all agent operations (orchestration, per-agent, per-tool, recommendation, post-policy, data-scope authorization)
- `UserRole.scope_json`: per-assignment scope restrictions (allowed_business_units, allowed_vendor_ids)
- SYSTEM_AGENT role (rank 100, `is_system_role=True`) with `system-agent@internal` service account for autonomous operations
- Agent RBAC audit: 9 `AuditEventType` values (GUARDRAIL_GRANTED/DENIED, TOOL_CALL_AUTHORIZED/DENIED, RECOMMENDATION_ACCEPTED/DENIED, AUTO_CLOSE_AUTHORIZED/DENIED, SYSTEM_AGENT_USED)
- `AgentRun` RBAC fields: `actor_primary_role`, `actor_roles_snapshot_json`, `permission_source`, `access_granted` -- populated on every agent run
- Agent feedback loop (PO re-reconciliation); deterministic resolver; agent tracing and governance
- Case management platform (state machine, 11 stages, 3 processing paths); Non-PO validation (9 checks)
- AP Copilot (`apps/copilot/`): read-only conversational assistant with session management, structured responses, role-aware visibility, and governance integration
- Review workflow with decision tracking; Dashboard analytics (7 API endpoints)
- **Invoice Posting Agent** (`apps/posting/` + `apps/posting_core/`): 9-stage pipeline (eligibility, snapshot, mapping, validation, confidence, review routing, payload build, finalization, status); 11 posting statuses; 6 review queues; Excel/CSV ERP reference import; governance trail; 17 posting audit event types; posting workbench + detail templates; full DRF API
- **ERP Integration Layer** (`apps/erp_integration/`): `ERPConnection` model + `ConnectorFactory`; 4 connector implementations (Custom, Dynamics, Zoho, Salesforce); 7 resolver types with DB fallback + TTL cache; resolution + submission audit logs; wired into `PostingMappingEngine` and `POLookupTool`/`GRNLookupTool`
- **Procurement Intelligence Platform** (`apps/procurement/`): product/solution recommendation, should-cost benchmarking, 6-dimension validation; `QuotationExtractionAgent` for LLM-based quotation data extraction; `AttributeMappingService` for field synonym mapping; DRF API + Bootstrap 5 templates
- Audit logging (38+ event types including case lifecycle, RBAC guardrail, posting events); CaseTimelineService (8 event categories); governance views
- Observability: TraceContext, structured JSON logging, MetricsService, RequestTraceMiddleware, @observed_service/@observed_action/@observed_task decorators; Langfuse integration (fail-silent tracing, scores, prompt management)
- DRF APIs with filtering, search, pagination; vendor UI (list + detail)
- RBAC data scoping: AP_PROCESSOR sees only POs/GRNs/Vendors linked to their own invoices
- Enterprise RBAC: Role, Permission, RolePermission, UserRole (with scope_json), UserPermissionOverride; RBAC engine, middleware, template tags, DRF classes, CBV mixins, admin console (8 screens), API, seed (6 roles incl. SYSTEM_AGENT, 40 permissions)
- Prompt registry with 18 defaults; pushed to Langfuse via `push_prompts_to_langfuse`
- Seed data (4 commands): config, rbac, prompts, ap_data (30+ scenarios, 6-stage pipeline)
- **Tests**: Reconciliation engine: 73. Extraction (base + Phase 2): 232+. Extraction core: 50+. Total: 355+ passing.
- Azure Blob Storage integration; Windows synchronous dev mode; Admin panel registration

### Not Yet Implemented

| Area | Description |
|---|---|
| **Real ERP submission** | `PostingActionService.submit_posting()` is Phase 1 mock -- replace with live ERP connector call (SAP BAPI, Oracle REST, etc.) |
| **Auto-submit (touchless posting)** | Auto-advance `is_touchless=True` postings to SUBMISSION_IN_PROGRESS without human approval |
| **Feedback learning** | Train `VendorAliasMapping`/`ItemAliasMapping` from accepted field corrections |
| **Scheduled ERP re-import** | Celery Beat periodic task to pull fresh master data from shared drive/ERP |
| **LLM-assisted item mapping** | Use GPT for fuzzy item description matching in `PostingMappingEngine._resolve_item()` |
| **Extraction Refinement** | Multi-page invoice support, edge-case layout handling |
| **Report Exports** | Full CSV/Excel export (CSV exists for case console only) |
| **Celery Beat** | No periodic task schedules configured |
| **Email Notifications** | No notification system for review assignments |
| **Docker/Deployment** | No Dockerfile or docker-compose |
| **CI/CD** | No GitHub Actions or pipeline |
| **Frontend Interactivity** | AJAX enhancements for server-rendered templates |
| **Additional tests** | Factory-boy factories and integration tests for DRF endpoints, posting pipeline, ERP connectors, procurement |

---

*This documentation was auto-generated from codebase analysis. Refer to source files for the most current implementation details.*

---

## 23. Invoice Posting Agent

> Full reference: [POSTING_AGENT.md](POSTING_AGENT.md)

The Invoice Posting Agent bridges approved invoices (past the reconciliation gate) into ERP-ready posting proposals. It spans two Django apps:

- **`apps/posting/`** — business/UI layer: eligibility gate, orchestration, action service, workbench templates
- **`apps/posting_core/`** — platform/core layer: 9-stage pipeline, mapping engine, validation, confidence scoring, review routing, governance trail

### 23.1 Pipeline Stages

| Stage | Name | Description |
|---|---|---|
| 1 | ELIGIBILITY_CHECK | Verify invoice status, PO link, prior run guard |
| 2 | SNAPSHOT_BUILD | Capture immutable invoice + line snapshot |
| 3 | MAPPING | Resolve vendor, item, tax code, cost center via ERP reference tables or live ERP API |
| 4 | VALIDATION | Field-level completeness + business rule checks |
| 5 | CONFIDENCE | 5-dimension weighted score (header 15%, vendor 25%, line 30%, tax 15%, freshness 15%) |
| 6 | REVIEW_ROUTING | Determine queues needing attention; set `is_touchless` |
| 7 | PAYLOAD_BUILD | Assemble ERP posting JSON payload |
| 8 | FINALIZATION | Write `InvoicePosting.status`, log governance record |
| 9 | STATUS | Emit audit event, update `PostingRun.status` |

Stage 9b also runs a duplicate invoice check via the ERP integration layer.

### 23.2 Mapping Engine Resolution Chain

Each field follows: exact code match -> alias lookup -> name fuzzy match -> LLM fallback (item only, Phase 2).

Resolution provenance per field is stored in `PostingRun.erp_source_metadata_json`.

### 23.3 Posting Status Lifecycle

```
NOT_READY -> READY_FOR_POSTING -> MAPPING_IN_PROGRESS
    -> MAPPING_REVIEW_REQUIRED | READY_TO_SUBMIT
    -> SUBMISSION_IN_PROGRESS -> POSTED | POST_FAILED
    -> RETRY_PENDING | REJECTED | SKIPPED
```

### 23.4 Review Queues

`VENDOR_MAPPING_REVIEW`, `ITEM_MAPPING_REVIEW`, `TAX_REVIEW`, `COST_CENTER_REVIEW`, `PO_REVIEW`, `POSTING_OPS`

### 23.5 ERP Reference Import

`ExcelImportOrchestrator` ingests vendor/item/tax/cost-center/open-PO master data from Excel or CSV into the reference tables (`ERPVendorReference`, `ERPItemReference`, `ERPTaxCodeReference`, `ERPCostCenterReference`, `ERPPOReference`).

---

## 24. ERP Integration Layer

> Full reference: [POSTING_AGENT.md](POSTING_AGENT.md) -- ERP Integration section

`apps/erp_integration/` is a shared connectivity layer used by both the posting pipeline and agent tools.

### 24.1 Architecture

```
Request -> ERPCacheService (TTL lookup)
        -> BaseERPConnector.lookup() (live API)
        -> DB Fallback Adapter (see tiers below)
        -> ERPAuditService (log resolution + submission)
```

**DB Fallback tiers by resolution type:**

| Resolution Type | Tier 1 (confidence 1.0) | Tier 2 (confidence 0.75) |
|---|---|---|
| PURCHASE_ORDER | `documents.PurchaseOrder` (full transactional record) | `posting_core.ERPPOReference` (ERP snapshot; adds `_source_tier` + `_warning`) |
| GRN | `documents.GoodsReceiptNote` | -- (single tier) |
| VENDOR | `posting_core.ERPVendorReference` | -- (single tier) |
| ITEM | `posting_core.ERPItemReference` | -- (single tier) |
| TAX_CODE | `posting_core.ERPTaxCodeReference` | -- (single tier) |
| COST_CENTER | `posting_core.ERPCostCenterReference` | -- (single tier) |
| DUPLICATE_INVOICE | `posting_core` duplicate check | -- (single tier) |

The two-tier PO chain means the reconciliation agent and posting agent see the same PO universe: transactional POs created in the recon system (Tier 1) AND POs imported from ERP master data exports (Tier 2).

### 24.2 Connectors

| Connector | Class | Notes |
|---|---|---|
| Custom ERP | `CustomERPConnector` | Generic REST adapter |
| Microsoft Dynamics 365 | `DynamicsConnector` | OAuth2 + OData |
| Zoho Books | `ZohoConnector` | Zoho OAuth |
| Salesforce | `SalesforceConnector` | SOQL queries |

Add new connectors by extending `BaseERPConnector`, implementing capability flags, and registering in `ConnectorFactory._CONNECTOR_MAP`.

### 24.3 Resolution Types

`VENDOR`, `ITEM`, `TAX_CODE`, `COST_CENTER`, `PURCHASE_ORDER`, `GRN`, `DUPLICATE_INVOICE`

### 24.4 Key Settings

| Setting | Default | Description |
|---|---|---|
| `ERP_CACHE_TTL_SECONDS` | 3600 | Cache TTL for ERP reference lookups |
| `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` | 0.8 | Min confidence for DB-fallback duplicate check |

### 24.5 API Endpoint

`POST /api/v1/erp/resolve/<resolution_type>/` -- on-demand ERP reference resolution

### 24.6 Reference Data UI

`/erp-connections/reference-data/` (`erp_integration:erp_reference_data`) -- browse all 5 imported reference tables (Vendors, Items, Tax Codes, Cost Centers, Open POs) in a tabbed interface with:
- KPI cards showing record counts per table
- Per-tab search and pagination
- Import batch provenance column (batch ID, imported date)
- Empty-state prompt linking to Import Reference Data
- Help callout explaining the two-tier PO resolution chain

Accessed from the **ERP Integration** sidebar section (separate from Posting Agent).

---

## 25. Procurement Intelligence Platform

> Full reference: [PROCUREMENT.md](PROCUREMENT.md)

`apps/procurement/` provides three intelligence flows before a purchase request reaches the PO stage.

### 25.1 Intelligence Flows

| Flow | Entry | Output |
|---|---|---|
| **Recommendation** | Product specification + budget | Ranked vendor shortlist with compliance score |
| **Benchmark** | Line item + quantity | Should-cost estimate vs market data |
| **Validation** | Draft PO or quotation | 6-dimension compliance score (policy, budget, vendor, quality, ESG, risk) |

### 25.2 Agents

| Agent | Class | Purpose |
|---|---|---|
| RecommendationAgent | `apps/procurement/agents/` | Vendor/product recommendation via LLM + catalog search |
| BenchmarkAgent | `apps/procurement/agents/` | Should-cost analysis with market reference data |
| ComplianceAgent | `apps/procurement/agents/` | Multi-dimension PO validation |
| QuotationExtractionAgent | `apps/procurement/agents/quotation_extraction_agent.py` | LLM extraction of structured data from quotation PDFs (60K char OCR limit, `max_tokens=8192`) |

### 25.3 Quotation Extraction Pipeline

1. OCR via Azure Document Intelligence (60K char truncation)
2. `QuotationDocumentPrefillService` calls `QuotationExtractionAgent.extract()`
3. `AttributeMappingService` normalizes field synonyms (`_QUOTATION_FIELD_SYNONYMS`)
4. Extracted data stored as JSON in `prefill_payload_json` -- NOT persisted to DB
5. User review + confirmation via `PrefillReviewService.confirm_quotation_prefill()` triggers DB write

### 25.4 Key Models

`ProcurementRequest`, `VendorQuotation`, `QuotationLineItem`, `RecommendationRun`, `BenchmarkRun`, `ValidationRun`, `ComplianceScore`, `ProcurementPolicy`, `MarketReferenceData`, `ProcurementApproval`, `SupplierPerformance`, `ProcurementAuditEvent`, `CategoryBudget`

### 25.5 API Base Path

`/api/v1/procurement/` -- request CRUD, quotation management, recommendation/benchmark/validation runs, category budgets, supplier performance, market reference data
