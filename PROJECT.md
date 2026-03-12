# 3-Way PO Reconciliation Platform — Comprehensive Project Documentation

> **Version**: 1.0 · **Last Updated**: March 2026  
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
| **AI Agent Pipeline** | 7 specialized agents (ReAct loop with tool-calling) for exception analysis and resolution |
| **Auto-Close Logic** | Tiered tolerance bands (strict: 2%/1%/1%, auto-close: 5%/3%/3%) for automatic disposition |
| **Case Management** | Full AP case lifecycle with state machine, stage-based processing, and copilot chat |
| **Review Workflow** | Role-based assignment, review decision tracking, and field corrections |
| **Governance** | Complete audit trail with 17 event types, agent trace visibility, and unified case timeline |
| **Non-PO Processing** | Validation pipeline for invoices without PO references (9 checks including spend category and policy) |

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
├── POLookupService
├── ReconciliationModeResolver
│   └── ReconciliationPolicy (DB)
├── ReconciliationExecutionRouter
│   ├── TwoWayMatchService
│   │   ├── HeaderMatchService → ToleranceEngine
│   │   └── LineMatchService → ToleranceEngine
│   └── ThreeWayMatchService
│       ├── HeaderMatchService → ToleranceEngine
│       ├── LineMatchService → ToleranceEngine
│       ├── GRNLookupService
│       └── GRNMatchService
├── ClassificationService
├── ExceptionBuilderService
├── ReconciliationResultService
├── ReviewWorkflowService
└── AuditService

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

The project contains **13 Django apps** under `apps/`:

| App | Purpose | Key Files |
|---|---|---|
| **accounts** | Custom User model (email login), roles | `models.py`, `managers.py` |
| **agents** | AI agent orchestration, ReAct loop, 7 agent types | `models.py`, `services/` (10 files) |
| **auditlog** | Audit events, processing logs, governance views | `models.py`, `services.py`, `timeline_service.py` |
| **cases** | AP Case lifecycle, state machine, stage orchestration | `models.py`, `orchestrators/`, `services/`, `state_machine/` |
| **core** | Base models, enums, constants, permissions, utilities | `models.py`, `enums.py`, `constants.py`, `permissions.py`, `utils.py` |
| **dashboard** | Analytics, KPIs, summary endpoints | `services.py`, `api_views.py` |
| **documents** | Invoice, PO, GRN data models & upload | `models.py`, `blob_service.py` |
| **extraction** | OCR + LLM extraction pipeline (7 services) | `services/`, `tasks.py` |
| **integrations** | External system connectors (PO/GRN API, RPA) | `models.py`, `contracts.py` |
| **reconciliation** | Matching engine (14 services), tolerance, classification | `services/` (14 files), `tasks.py` |
| **reports** | Report generation tracking | `models.py` |
| **reviews** | Review assignment, decisions, comments | `models.py`, `services.py` |
| **tools** | Agent tool registry (6 tools) | `registry/base.py`, `registry/tools.py` |
| **vendors** | Vendor master data, aliases | `models.py` |

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
| Enums | `apps/core/enums.py` |
| Permissions | `apps/core/permissions.py` |
| Utilities | `apps/core/utils.py` |
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

### 5.2 Accounts (`apps/accounts/models.py`)

| Model | Fields | Notes |
|---|---|---|
| **User** | email (login), first_name, last_name, role, is_active, is_staff, department | Custom model; `AUTH_USER_MODEL = "accounts.User"` |

Roles: `ADMIN`, `AP_PROCESSOR`, `REVIEWER`, `FINANCE_MANAGER`, `AUDITOR`

### 5.3 Documents (`apps/documents/models.py`)

| Model | Key Fields | Relationships |
|---|---|---|
| **DocumentUpload** | file, original_filename, file_size, file_hash (SHA-256), content_type, document_type, processing_state, blob_name/container/url | — |
| **Invoice** | raw_* fields (vendor_name, invoice_number, po_number, currency, subtotal, tax, total), normalized fields, invoice_date, status, extraction_confidence, is_duplicate, duplicate_of | FK: document_upload, vendor, created_by |
| **InvoiceLineItem** | raw & normalized qty/unit_price/tax/line_amount, description, item_code, extraction_confidence, item_category, is_service_item, is_stock_item | FK: invoice |
| **PurchaseOrder** | po_number, po_date, currency, total_amount, tax_amount, status, buyer_name, department | FK: vendor |
| **PurchaseOrderLineItem** | line_number, item_code, description, quantity, unit_price, tax_amount, line_amount, unit_of_measure, item_category, is_service_item, is_stock_item | FK: purchase_order |
| **GoodsReceiptNote** | grn_number, receipt_date, status, warehouse, receiver_name | FK: purchase_order, vendor |
| **GRNLineItem** | line_number, item_code, description, quantity_received/accepted/rejected, unit_of_measure | FK: goods_receipt_note, po_line |

#### Invoice Status Flow
```
UPLOADED → EXTRACTION_IN_PROGRESS → EXTRACTED → VALIDATED → READY_FOR_RECON → RECONCILED
                                  ↘ INVALID                                  ↘ FAILED
```

### 5.4 Extraction (`apps/extraction/models.py`)

| Model | Key Fields | Notes |
|---|---|---|
| **ExtractionResult** | engine_name, engine_version, raw_response (JSON), confidence, duration_ms, success, error_message | FK: document_upload, invoice |

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
| **AgentDefinition** | agent_type, name, description, config_json, allowed_tools, system_prompt, is_active, max_iterations | Registry of agent types |
| **AgentRun** | agent_type, status, input/output_payload (JSON), confidence, summarized_reasoning, prompt_tokens/completion_tokens/total_tokens, duration_ms | FK: definition, reconciliation_result |
| **AgentStep** | step_number, action, input/output_data (JSON), duration_ms | FK: agent_run |
| **AgentMessage** | role (system/user/assistant/tool), content, tool_calls (JSON), tool_call_id, token_count | FK: agent_run |
| **DecisionLog** | decision_type, rationale, confidence, evidence_refs (JSON), recommendation_type | FK: agent_run, reconciliation_result |
| **AgentRecommendation** | recommendation_type, confidence, reasoning, evidence (JSON), accepted, accepted_by, accepted_at | FK: agent_run, reconciliation_result |
| **AgentEscalation** | severity, reason, suggested_assignee_role, resolved, resolved_by | FK: agent_run, reconciliation_result |

#### Agent Run Status
```
PENDING → RUNNING → COMPLETED | FAILED | SKIPPED
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
| **AuditEvent** | auditlog | State change/governance events (17 types) |
| **FileProcessingStatus** | auditlog | File upload lifecycle tracking |
| **GeneratedReport** | reports | Report generation tracking |

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

All enums live in `apps/core/enums.py` (24 classes). Key enums:

### Invoice & Documents
| Enum | Values |
|---|---|
| `InvoiceStatus` | UPLOADED, EXTRACTION_IN_PROGRESS, EXTRACTED, VALIDATED, INVALID, READY_FOR_RECON, RECONCILED, FAILED |
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
| `AgentType` | INVOICE_UNDERSTANDING, PO_RETRIEVAL, GRN_RETRIEVAL, RECONCILIATION_ASSIST, EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY |
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
| `AuditEventType` | INVOICE_UPLOADED, EXTRACTION_COMPLETED, EXTRACTION_FAILED, VALIDATION_FAILED, RECONCILIATION_STARTED, RECONCILIATION_COMPLETED, AGENT_RECOMMENDATION_CREATED, REVIEW_ASSIGNED, REVIEW_APPROVED, REVIEW_REJECTED, FIELD_CORRECTED, RECONCILIATION_RERUN, AGENT_RUN_STARTED, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED, RECONCILIATION_MODE_RESOLVED, POLICY_APPLIED, MANUAL_MODE_OVERRIDE |

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

### 7.2 Extraction Pipeline (7 Services)

The extraction runs as a Celery task (`process_invoice_upload_task`) and executes sequentially:

**Step 1 — OCR (`InvoiceExtractionAdapter`)**
- Sends document to Azure Document Intelligence
- Returns raw text content from PDF/image

**Step 2 — LLM Extraction (`InvoiceExtractionAdapter`)**
- Sends OCR text to Azure OpenAI GPT-4o with structured extraction prompt
- Returns JSON with invoice header fields + line items
- Output: `ExtractionResponse` (raw_json, confidence, duration_ms)

**Step 3 — Parsing (`ExtractionParserService`)**
- Parses raw JSON into `ParsedInvoice` dataclass
- Preserves raw values for auditability

**Step 4 — Normalization (`NormalizationService`)**
- Vendor name: lowercase, strip, collapse whitespace
- Invoice number: uppercase, strip spaces
- PO number: uppercase, strip leading zeros/prefixes, remove non-alphanumeric
- Dates: best-effort parse from multiple formats
- Amounts: safe Decimal conversion
- Currency: normalize to 3-char ISO code

**Step 5 — Validation (`ValidationService`)**
- **Mandatory**: invoice_number, vendor_name, total_amount
- **Recommended**: po_number, invoice_date, subtotal, confidence ≥ 0.75
- Output: `ValidationResult` (is_valid, issues, errors/warnings)

**Step 6 — Duplicate Detection (`DuplicateDetectionService`)**
- Check 1: Same invoice_number + vendor → DUPLICATE
- Check 2: Same invoice_number + amount within 90 days → DUPLICATE
- Check 3: Same vendor + amount + date → WARNING

**Step 7 — Persistence (`InvoicePersistenceService`)**
- Resolve vendor FK (by normalized name or alias lookup)
- Set invoice status: INVALID (if validation failed), EXTRACTED/VALIDATED (otherwise)
- Save Invoice + InvoiceLineItem records
- Save ExtractionResult metadata

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
    │   ├─ 1. PO Lookup (POLookupService)
    │   │      Strategy: exact → normalized → vendor+amount discovery
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
    │   │      → Save result + line results + exceptions
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
3. **GRN Lookup** (`GRNLookupService`) — Aggregate all GRNs for the PO
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
AgentOrchestrator
    ↓
PolicyEngine.plan()  →  AgentPlan (which agents, in what order)
    ↓
For each planned agent:
    BaseAgent.run()
    ├── Build system prompt + user message (with mode context)
    ├── ReAct Loop (max 6 iterations):
    │   ├── LLM chat (with tool definitions)
    │   ├── If tool_calls → execute tools → add results → loop
    │   └── If no tool_calls → interpret response → return
    ├── Log: AgentMessages, AgentSteps, DecisionLog
    └── Return: AgentRun (status, confidence, reasoning, output)
    ↓
DeterministicResolver (replaces EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
    ↓
AgentFeedbackService (re-reconcile if PO found by agent)
    ↓
Final Recommendation + Auto-Close / Escalation
```

### 9.2 Seven Agent Types

| Agent | Type | Purpose | Tools |
|---|---|---|---|
| **InvoiceUnderstandingAgent** | INVOICE_UNDERSTANDING | Deep-dives into low-confidence extractions | invoice_details |
| **PORetrievalAgent** | PO_RETRIEVAL | Finds correct PO when deterministic lookup failed | po_lookup, vendor_search, invoice_details |
| **GRNRetrievalAgent** | GRN_RETRIEVAL | Investigates GRN issues (3-way only) | grn_lookup, po_lookup, invoice_details |
| **ReconciliationAssistAgent** | RECONCILIATION_ASSIST | General-purpose for partial match investigation | All 6 tools |
| **ExceptionAnalysisAgent** | EXCEPTION_ANALYSIS | Root cause analysis of exceptions | exception_list, reconciliation_summary, invoice_details |
| **ReviewRoutingAgent** | REVIEW_ROUTING | Determines appropriate review queue/team | exception_list, reconciliation_summary |
| **CaseSummaryAgent** | CASE_SUMMARY | Produces human-readable case summary | reconciliation_summary, exception_list |

### 9.3 Tool Registry (6 Tools)

All tools extend `BaseTool` and are registered via the `@register_tool` decorator:

| Tool | Input | Output |
|---|---|---|
| **po_lookup** | po_number | PO header + line items |
| **grn_lookup** | po_number | GRN list, receipt quantities per line |
| **vendor_search** | query (name/code/alias) | Matching vendors (direct + alias matches) |
| **invoice_details** | invoice_id | Full invoice details + line items |
| **exception_list** | reconciliation_result_id | All exceptions with metadata |
| **reconciliation_summary** | reconciliation_result_id | Match status, confidence, header evidence |

Tool calls are logged via `ToolCallLogger` with status (REQUESTED/SUCCESS/FAILED), duration, and input/output.

### 9.4 Policy Engine

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

### 9.5 Deterministic Resolver

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

### 9.6 LLM Client

`LLMClient` wraps both Azure OpenAI and plain OpenAI APIs:

- Configurable via environment variables (`AZURE_OPENAI_*` or `OPENAI_API_KEY`)
- Supports tool-calling in OpenAI-compliant format
- Tool calls: `tool_calls` array on assistant messages, `tool_call_id` + `name` on responses
- Returns: `LLMResponse` (content, tool_calls, finish_reason, token counts)

### 9.7 Orchestration Flow

`AgentOrchestrator.run()`:

1. Load ReconciliationResult + exceptions
2. Ask PolicyEngine for agent plan
3. Partition agents: LLM-required vs deterministic-replaceable
4. Build `AgentContext` with reconciliation mode awareness
5. Execute LLM agents sequentially
6. Execute deterministic agents (cheaper alternatives)
7. Apply feedback loop for PO_RETRIEVAL agents
8. Resolve final recommendation
9. Apply post-policies (auto-close, escalation)

Output: `OrchestrationResult` (agents_executed, agent_runs, final_recommendation, confidence)

### 9.8 Tracing & Governance

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
- Terminal states: CLOSED, REJECTED
- Methods: `can_transition()`, `get_allowed_transitions()`, `is_terminal()`, `transition()`

### 10.5 Stage Executor

`StageExecutor` dispatches individual stages:

| Stage | Handler |
|---|---|
| INTAKE | Validate upload, classify document |
| EXTRACTION | Monitor completion, validate quality |
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

### 13.1 Audit Events

`AuditService` logs 17+ event types:

| Category | Events |
|---|---|
| **Document** | INVOICE_UPLOADED |
| **Extraction** | EXTRACTION_COMPLETED, EXTRACTION_FAILED, VALIDATION_FAILED |
| **Reconciliation** | RECONCILIATION_STARTED, RECONCILIATION_COMPLETED, RECONCILIATION_RERUN |
| **Mode** | RECONCILIATION_MODE_RESOLVED, POLICY_APPLIED, MANUAL_MODE_OVERRIDE |
| **Agent** | AGENT_RUN_STARTED, AGENT_RUN_COMPLETED, AGENT_RUN_FAILED, AGENT_RECOMMENDATION_CREATED |
| **Review** | REVIEW_ASSIGNED, REVIEW_APPROVED, REVIEW_REJECTED, FIELD_CORRECTED |

### 13.2 Case Timeline

`CaseTimelineService` builds a unified chronological timeline per invoice, merging:
- AuditEvent records (Invoice + ReconciliationResult entities)
- AgentRun records (with agent definition names)
- ToolCall records (per run)
- AgentRecommendation records
- ReviewAssignment, ManualReviewAction, ReviewDecision records

Each entry is categorized: `audit`, `agent_run`, `tool_call`, `recommendation`, `review`, `review_action`, `review_decision`

### 13.3 Governance UI

| View | URL | Access |
|---|---|---|
| **Audit Event List** | `/governance/` | Filterable log, 50 per page |
| **Invoice Governance** | `/governance/invoice/<id>/` | Full dashboard: audit trail + agent trace + timeline |

Role-based visibility: ADMIN and AUDITOR see full agent trace data.

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

### 14.9 API Standards

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
│   └── login.html                     # Login page
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
| `/agents/reference/` | `agent_reference` | Agent/stage reference page |
| `/accounts/login/` | Django LoginView | Authentication |

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
| `process_invoice_upload_task` | extraction | Full extraction pipeline (OCR → parse → validate → persist) | bind=True, max_retries=3, acks_late=True |
| `run_reconciliation_task` | reconciliation | Batch reconciliation run (2-way/3-way matching) | bind=True, max_retries=2 |
| `reconcile_single_invoice_task` | reconciliation | Single invoice convenience wrapper | bind=True |
| `run_agent_pipeline_task` | agents | Execute agent pipeline for non-MATCHED results | bind=True, max_retries=2 |
| `process_case_task` | cases | Run CaseOrchestrator for APCase lifecycle | bind=True, max_retries=3, acks_late=True |
| `reprocess_case_from_stage_task` | cases | Reprocess case from specific stage | bind=True |

**Windows Development**: `CELERY_TASK_ALWAYS_EAGER=True` (default) runs tasks synchronously without Redis.

---

## 17. Seed Data & Management Commands

### 17.1 Commands

| Command | Purpose |
|---|---|
| `python manage.py seed_config` | Foundation data: 6 users, 7 agent definitions, 6 tool definitions, reconciliation config, policies |
| `python manage.py seed_prompts` | Populate PromptTemplate records from registry defaults (--force to overwrite) |
| `python manage.py seed_ap_data` | Realistic Saudi McDonald's test data (modes: demo, qa, large) |
| `python manage.py create_cases_for_existing_invoices` | Backfill APCase records for existing invoices (--process to auto-run) |

### 17.2 Seed Data Details

**`seed_config`** creates:
- 6 users (admin, ap_processor, reviewer, finance_mgr, auditor, demo_user)
- 7 agent definitions with config_json & allowed_tools
- 6 tool definitions (po_lookup, grn_lookup, vendor_search, invoice_details, exception_list, reconciliation_summary)
- Reconciliation config with default tolerances
- Reconciliation policies

**`seed_ap_data`** creates realistic McDonald's Saudi Arabia data:
- **demo mode**: Small dataset for quick testing
- **qa mode**: Medium dataset for QA validation
- **large mode**: Full dataset for performance testing
- Includes: users, vendors (with Arabic aliases), POs, GRNs, invoices, cases, reconciliation data, agent/review data
- 5-stage pipeline: users → vendors → transactional data → cases/recon → agent/review data
- Covers: Riyadh/Jeddah/Dammam warehouses, multiple item categories

---

## 18. Prompt Registry

### 18.1 Architecture

`PromptRegistry` (`apps/core/prompt_registry.py`) provides centralized prompt management with a 3-tier lookup:
1. **Cache** (in-memory) — fastest
2. **Database** (PromptTemplate model) — configurable via admin
3. **Defaults** (hardcoded) — fallback guarantee

### 18.2 Registered Prompts (13 defaults)

| Slug | Category | Purpose |
|---|---|---|
| `extraction.invoice_system` | extraction | System prompt for invoice data extraction |
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

---

## 20. Security & Permissions

### 20.1 Authentication

- Custom User model with **email-based login** (no username field)
- Django session authentication for web UI
- DRF SessionAuthentication for API
- `LoginRequiredMiddleware` redirects anonymous users to `/accounts/login/`
- Exempt paths: `/admin/`, `/accounts/`, `/api/`

### 20.2 Role-Based Permissions

| Permission Class | Allowed Roles |
|---|---|
| `IsAdmin` | ADMIN |
| `IsAPProcessor` | AP_PROCESSOR, ADMIN |
| `IsReviewer` | REVIEWER, FINANCE_MANAGER, ADMIN |
| `IsFinanceManager` | FINANCE_MANAGER, ADMIN |
| `IsAuditor` | AUDITOR, ADMIN |
| `IsAdminOrReadOnly` | Any authenticated (read), ADMIN (write) |
| `IsReviewAssignee` | Assigned reviewer, ADMIN, FINANCE_MANAGER |
| `HasAnyRole` | Configurable via view's `allowed_roles` |

### 20.3 Case Permissions

| Permission | Description |
|---|---|
| `CanViewCase` | ADMIN, AUDITOR, FINANCE_MANAGER, AP_PROCESSOR, REVIEWER |
| `CanEditCase` | ADMIN, AP_PROCESSOR |
| `CanAssignCase` | ADMIN, FINANCE_MANAGER |
| `CanUseCopilot` | ADMIN, AP_PROCESSOR, REVIEWER |

### 20.4 Soft Delete

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

**New Tool:**
1. Create tool class in `apps/tools/registry/tools.py`, extend `BaseTool`
2. Decorate with `@register_tool`
3. Implement `execute()` method
4. Add `ToolDefinition` record
5. Reference in agent's `allowed_tools`

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

- All data models, migrations, enums (24 enum classes), permissions, middleware
- Extraction pipeline (Azure DI OCR + GPT-4o, 7 services)
- Reconciliation engine (14 services; 2-way/3-way matching with mode resolver)
- ReconciliationPolicy model with priority-ordered mode rules
- Tiered tolerance (strict + auto-close bands)
- AI agent orchestration (7 agents, policy engine, tool registry, LLM client)
- Agent feedback loop (PO re-reconciliation)
- Deterministic resolver (cost-saving LLM replacement)
- Agent tracing and governance
- Case management platform (state machine, 11 stages, 3 processing paths)
- Non-PO validation (9 checks)
- Review workflow with decision tracking
- Dashboard analytics (7 API endpoints)
- Audit logging (17 event types)
- Unified case timeline
- DRF APIs with filtering, search, pagination
- Bootstrap 5 templates (23 templates)
- Prompt registry with 13 defaults
- Seed data commands (4 commands including Saudi McD scenarios)
- Azure Blob Storage integration
- Admin panel registration
- Windows synchronous dev mode

### Not Yet Implemented

| Area | Description |
|---|---|
| **Tests** | pytest + factory-boy configured; no tests written yet |
| **Extraction Refinement** | Multi-page invoice support, edge-case layout handling |
| **ERP Integrations** | Actual PO/GRN API connectors (models exist, not wired) |
| **Report Exports** | Full CSV/Excel export (CSV exists for case console only) |
| **Celery Beat** | No periodic task schedules configured |
| **Email Notifications** | No notification system for review assignments |
| **Docker/Deployment** | No Dockerfile or docker-compose |
| **CI/CD** | No GitHub Actions or pipeline |
| **Frontend Interactivity** | AJAX enhancements for server-rendered templates |

---

*This documentation was auto-generated from codebase analysis. Refer to source files for the most current implementation details.*
