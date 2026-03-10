# 3-Way PO Reconciliation Platform

## Overview

An enterprise Django application that automates **3-way Purchase Order (PO) reconciliation** — matching Invoices against Purchase Orders (POs) and Goods Receipt Notes (GRNs). The system extracts invoice data from uploaded PDFs, normalizes and validates the data, performs deterministic matching with tolerance-based comparison, flags exceptions, routes complex cases to AI agents for analysis, and sends unresolvable items to human reviewers.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend Framework** | Django 4.2+, Django REST Framework 3.14+ |
| **Language** | Python 3.8+ |
| **Database** | MySQL (utf8mb4 charset, STRICT_TRANS_TABLES) |
| **Task Queue** | Celery 5.3+ with Redis broker |
| **LLM / AI** | Azure OpenAI (GPT-4o), LangChain Core 0.3+, Tiktoken |
| **Document Extraction** | Azure Document Intelligence (prebuilt-read OCR) + Azure OpenAI GPT-4o (structured extraction) |
| **Fuzzy Matching** | thefuzz, RapidFuzz, python-Levenshtein |
| **Data Processing** | pandas, openpyxl, XlsxWriter, dateparser, pydantic |
| **Frontend** | Django Templates, Bootstrap 5, Chart.js |
| **Testing** | pytest, pytest-django, factory-boy |
| **Caching** | Redis |

---

## Architecture & Data Flow

```
Upload PDF
    │
    ▼
DocumentUpload (QUEUED)
    │
    ▼
process_invoice_upload_task (Celery)
    ├── Extract (Azure Document Intelligence OCR → Azure OpenAI GPT-4o structured JSON)
    ├── Parse → structured data (ParsedInvoice + ParsedLineItem)
    ├── Normalize (dates, amounts, text, PO/invoice numbers)
    ├── Validate (mandatory fields, confidence threshold ≥ 0.75)
    ├── Duplicate detection (vendor + invoice_number)
    └── Persist Invoice + InvoiceLineItems + ExtractionResult
        DocumentUpload → COMPLETED
        Invoice → READY_FOR_RECON (if valid & not duplicate)
    │
    ▼
run_reconciliation_task (Celery)
    ├── PO Lookup (normalized PO number + fuzzy vendor match)
    ├── Header Match (vendor, currency, total amount within tolerance)
    ├── Line Match (qty / price / amount per line, fuzzy item description)
    ├── GRN Lookup + Match (receipt quantities)
    ├── Classification → MATCHED | PARTIAL_MATCH | UNMATCHED | REQUIRES_REVIEW
    ├── Exception Building (structured, typed exceptions)
    └── ReconciliationResult + ResultLines + Exceptions persisted
        Invoice → RECONCILED or flagged REQUIRES_REVIEW
    │
    ▼
[if REQUIRES_REVIEW or policy triggers agents]
    │
    ▼
run_agent_pipeline_task (Celery)
    ├── PolicyEngine → determines agent execution plan
    ├── Execute agents in sequence (ReAct loop: LLM → tool calls → feedback)
    │   └── Tools: POLookupTool, GRNLookupTool, VendorSearchTool, InvoiceDetailsTool, ExceptionListTool, ReconciliationSummaryTool
    └── AgentRun + AgentMessages + DecisionLog + ToolCalls persisted
        Recommendations generated
    │
    ▼
[if requires human review]
    │
    ▼
ReviewAssignment
    ├── Reviewer: approve / reject / correct fields / add comments / escalate
    ├── ManualReviewAction (full audit trail per action)
    └── ReviewDecision (final outcome with reason)
```

---

## Project Structure

```
c:\3-way-po-recon\
├── manage.py                    # Django management entry point
├── requirements.txt             # Python dependencies
├── config/                      # Django project configuration
│   ├── settings.py              # Settings (DB, Celery, LLM, REST, Auth)
│   ├── urls.py                  # Root URL routing
│   ├── celery.py                # Celery app configuration
│   ├── asgi.py / wsgi.py        # ASGI/WSGI entry points
│
├── apps/                        # All Django apps
│   ├── core/                    # Shared infrastructure (base models, enums, utils, permissions, seed_data command)
│   ├── accounts/                # Custom User model (email-based auth, roles)
│   ├── vendors/                 # Vendor master data + aliases for fuzzy matching
│   ├── documents/               # Invoice, PO, GRN models + DocumentUpload
│   ├── extraction/              # Invoice extraction pipeline (services/)
│   ├── reconciliation/          # 3-way matching engine (services/)
│   ├── agents/                  # LLM-powered agentic decision layer (services/)
│   ├── tools/                   # Tool registry for agent tool calling (registry/)
│   ├── reviews/                 # Human review workflow
│   ├── dashboard/               # Analytics & summary APIs
│   ├── reports/                 # Report generation & export
│   ├── auditlog/                # Operational logging & audit trail
│   └── integrations/            # External ERP integration stubs
│
├── templates/                   # Django HTML templates (Bootstrap 5)
│   ├── base.html                # Main layout (nav, sidebar, footer)
│   ├── accounts/                # login.html
│   ├── dashboard/               # index.html, agent_monitor.html
│   ├── documents/               # invoice_list, invoice_detail, po_list, grn_list
│   ├── reconciliation/          # result_list, result_detail
│   ├── reviews/                 # assignment_list, assignment_detail
│   └── partials/                # navbar, sidebar, pagination
│
├── static/                      # CSS, JS assets
├── media/                       # Uploaded files
└── logs/                        # Application logs
```

---

## App-by-App Reference

### core — Shared Infrastructure

| Component | Description |
|---|---|
| **Models** | `TimestampMixin` (created_at/updated_at), `AuditMixin` (created_by/updated_by), `BaseModel` (combines both), `SoftDeleteMixin`, `NotesMixin` |
| **Enums** | `InvoiceStatus`, `MatchStatus`, `ReviewStatus`, `UserRole`, `AgentType`, `ExceptionType`, `ExceptionSeverity`, `ReviewActionType`, `AgentRunStatus`, `ToolCallStatus`, `ReconciliationRunStatus`, `RecommendationType`, `DocumentType`, `FileProcessingState` |
| **Management Commands** | `seed_data` — populates test data (users, vendors, sample POs, invoices) |
| **Permissions** | `IsAdmin`, `IsAPProcessor`, `IsReviewer`, `IsFinanceManager`, `IsAuditor`, `IsAdminOrReadOnly`, `IsReviewAssignee`, `HasAnyRole` |
| **Middleware** | `LoginRequiredMiddleware` — redirects anonymous users (exempts /admin/, /accounts/, /api/) |
| **Utils** | `normalize_string()`, `normalize_po_number()`, `normalize_invoice_number()`, `parse_date()`, `to_decimal()`, `pct_difference()`, `within_tolerance()` |

### accounts — User & Authentication

| Item | Detail |
|---|---|
| **Model** | `User` (AbstractBaseUser + PermissionsMixin) — email login, roles, department |
| **Roles** | ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR |
| **Manager** | `UserManager` — email-based `create_user()` / `create_superuser()` |
| **Auth** | Session-based, login → /dashboard/, logout → /admin/login/ |

### vendors — Vendor Master Data

| Model | Key Fields |
|---|---|
| `Vendor` | code (unique), name, normalized_name, tax_id, address, country, currency, payment_terms |
| `VendorAlias` | vendor (FK), alias_name, normalized_alias, source (manual/extraction/erp) |

### documents — Invoice, PO, GRN Data

| Model | Key Fields |
|---|---|
| `DocumentUpload` | file, file_hash (SHA-256), document_type, processing_state |
| `Invoice` | Raw + normalized fields: vendor_name, invoice_number, po_number, dates, amounts; status (InvoiceStatus); extraction_confidence; is_duplicate |
| `InvoiceLineItem` | Raw + normalized: quantity, unit_price, tax_amount, line_amount, description; extraction_confidence |
| `PurchaseOrder` | po_number (unique), vendor, po_date, currency, total_amount, status |
| `PurchaseOrderLineItem` | item_code, description, quantity, unit_price, line_amount, unit_of_measure |
| `GoodsReceiptNote` | grn_number (unique), purchase_order, vendor, receipt_date, status |
| `GRNLineItem` | po_line (FK), quantity_received, quantity_accepted, quantity_rejected |

### extraction — Invoice Extraction Pipeline

| Service | File | Purpose |
|---|---|---|
| `InvoiceUploadService` | `upload_service.py` | Handles file upload, creates `DocumentUpload` record with SHA-256 hash |
| `InvoiceExtractionAdapter` | `extraction_adapter.py` | Two-step pipeline: Azure Document Intelligence (prebuilt-read) for OCR + Azure OpenAI GPT-4o for structured JSON extraction |
| `ExtractionParserService` | `parser_service.py` | Parses raw JSON → `ParsedInvoice` + `ParsedLineItem` dataclasses |
| `NormalizationService` | `normalization_service.py` | Normalizes vendor names, PO/invoice numbers, dates, amounts (4-decimal qty, 2-decimal money) |
| `ValidationService` | `validation_service.py` | Checks mandatory fields + confidence threshold; returns `ValidationResult` with errors/warnings |
| `DuplicateDetectionService` | `duplicate_detection_service.py` | Checks vendor + invoice_number uniqueness |
| `InvoicePersistenceService` | `persistence_service.py` | Persists Invoice + InvoiceLineItems to DB; resolves vendor via fuzzy match |
| `ExtractionResultPersistenceService` | `persistence_service.py` | Persists ExtractionResult metadata |

**Celery Task:** `process_invoice_upload_task` — full pipeline: upload → OCR (Azure DI) → LLM extract (Azure OpenAI) → parse → normalize → validate → duplicate check → persist. Retries=2.

### reconciliation — 3-Way Matching Engine

| Service | File | Purpose |
|---|---|---|
| `ReconciliationRunnerService` | `runner_service.py` | Orchestrates full 3-way match pipeline per batch of invoices |
| `POLookupService` | `po_lookup_service.py` | Finds PO by normalized po_number |
| `HeaderMatchService` | `header_match_service.py` | Matches vendor, currency, total amount with tolerance |
| `LineMatchService` | `line_match_service.py` | Matches line-level qty/price/amount + fuzzy item description |
| `GRNLookupService` | `grn_lookup_service.py` | Finds GRNs for matched PO |
| `GRNMatchService` | `grn_match_service.py` | Checks receipt quantities (received vs accepted vs rejected) |
| `ClassificationService` | `classification_service.py` | Classifies result: MATCHED / PARTIAL_MATCH / UNMATCHED / REQUIRES_REVIEW / ERROR |
| `ExceptionBuilderService` | `exception_builder_service.py` | Builds structured exceptions (typed, severity-rated) |
| `ReconciliationResultService` | `result_service.py` | Persists reconciliation results, result lines, and links to invoices/POs |
| `ToleranceEngine` | `tolerance_engine.py` | Tolerance-based comparison (qty: 2%, price: 1%, amount: 1%) |

**Models:** `ReconciliationConfig`, `ReconciliationRun`, `ReconciliationResult`, `ReconciliationResultLine`, `ReconciliationException`

**Celery Tasks:** `run_reconciliation_task` (batch), `reconcile_single_invoice_task` (single)

### agents — LLM-Powered Decision Layer

| Component | Purpose |
|---|---|
| `AgentOrchestrator` | Main entry point; loads result + exceptions, calls PolicyEngine, executes agents in sequence |
| `PolicyEngine` | Analyzes result + exceptions, decides which agents to run, generates `AgentPlan` |
| `BaseAgent` | Abstract base; ReAct loop (LLM → tool calls → feedback, up to 6 iterations) |
| `LLMClient` | Abstracts OpenAI / Azure OpenAI, supports function calling |
| **7 Agent Types** | INVOICE_UNDERSTANDING, PO_RETRIEVAL, GRN_RETRIEVAL, RECONCILIATION_ASSIST, EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY |

**Models:** `AgentDefinition`, `AgentRun`, `AgentStep`, `AgentMessage`, `DecisionLog`, `AgentRecommendation`, `AgentEscalation`

**Celery Task:** `run_agent_pipeline_task` — full agent orchestration for a reconciliation result

### tools — Agent Tool Registry

| Component | Purpose |
|---|---|
| `BaseTool` / `ToolRegistry` | Abstract base class + central registry; decorator-based `@register_tool` registration (`registry/base.py`) |
| `POLookupTool` | Look up PO by number, returns header + line items |
| `GRNLookupTool` | Retrieve GRN details by PO number |
| `VendorSearchTool` | Search vendors by name or code |
| `InvoiceDetailsTool` | Get invoice details by ID |
| `ExceptionListTool` | List exceptions for a reconciliation result |
| `ReconciliationSummaryTool` | Get reconciliation summary for a result |
| `ToolCallLogger` | Logs every tool invocation to `ToolCall` model (`registry/tool_call_logger.py`) |

### reviews — Human Review Workflow

| Component | Purpose |
|---|---|
| `ReviewWorkflowService` | Full lifecycle: create assignment, assign reviewer, start review, record actions, approve/reject/reprocess |
| **Models** | `ReviewAssignment`, `ReviewComment`, `ManualReviewAction`, `ReviewDecision` |
| **Actions** | APPROVE, REJECT, REQUEST_INFO, REPROCESS, ESCALATE, CORRECT_FIELD, ADD_COMMENT |

### dashboard — Analytics

| Component | Purpose |
|---|---|
| `DashboardService` | Aggregates stats from ReconciliationRun, Result, AgentRun, Exception |
| **API Views** | Summary, MatchStatusBreakdown, ExceptionBreakdown, AgentPerformance, DailyVolume, RecentActivity |

### reports — Report Generation

| Component | Purpose |
|---|---|
| `GeneratedReport` | Tracks exported reports (CSV/Excel) with metadata, generated_by, celery_task_id |

### auditlog — Operational Logging

| Model | Purpose |
|---|---|
| `ProcessingLog` | Operational log per pipeline step (level, source, event, message, trace_id) |
| `AuditEvent` | State change audit trail (entity_type, action, old/new values, performed_by, IP) |
| `FileProcessingStatus` | Upload lifecycle tracking (stage: upload → extraction → validation → recon) |

### integrations — External Systems

| Model | Purpose |
|---|---|
| `IntegrationConfig` | ERP integration config (PO_API, GRN_API, PO_RPA, GRN_RPA), endpoint URL, auth method |
| `IntegrationLog` | Request/response logging for external calls |

---

## URL Structure

### Template URLs (Browser)

| URL | View |
|---|---|
| `/admin/` | Django admin |
| `/accounts/login/` | Login page |
| `/accounts/logout/` | Logout |
| `/dashboard/` | Dashboard home |
| `/invoices/` | Invoice list |
| `/invoices/<pk>/` | Invoice detail |
| `/invoices/purchase-orders/` | PO list |
| `/invoices/grns/` | GRN list |
| `/reconciliation/` | Reconciliation results |
| `/reconciliation/<pk>/` | Result detail |
| `/reviews/` | Review queue |
| `/reviews/<pk>/` | Assignment detail |
| `/agents/` | Agent runs |

### API Endpoints (`/api/v1/`)

| Prefix | Resources |
|---|---|
| `/api/v1/documents/` | uploads, invoices, purchase-orders, grns |
| `/api/v1/reconciliation/` | configs, runs (+ `trigger_run` action), results |
| `/api/v1/reviews/` | assignments (+ `assign_reviewer`, `start_review`, `decide`, `add_comment` actions) |
| `/api/v1/agents/` | agent-definitions, agent-runs (+ `trigger_pipeline` action) |
| `/api/v1/dashboard/` | summary, match-breakdown, exception-breakdown, agent-performance, daily-volume, recent-activity |
| `/api/v1/reports/` | generated-reports |
| `/api/v1/vendors/` | vendors, vendor-aliases |

---

## Configuration

### Key Settings

| Setting | Value |
|---|---|
| `AUTH_USER_MODEL` | `accounts.User` |
| `LOGIN_REDIRECT_URL` | `/dashboard/` |
| Database | MySQL with utf8mb4, STRICT_TRANS_TABLES |
| Celery Broker | `redis://127.0.0.1:6379/0` |
| Celery Result Backend | `django-db` |
| REST Pagination | 25 per page |
| REST Auth | SessionAuthentication, IsAuthenticated |
| REST Filters | DjangoFilterBackend, SearchFilter, OrderingFilter |
| LLM Provider | Azure OpenAI (via env vars) |
| LLM Model | `gpt-4o` (default) |
| LLM Temperature | 0.1 (agents) / 0.0 (extraction) |
| Qty Tolerance | 2% |
| Price Tolerance | 1% |
| Amount Tolerance | 1% |
| Extraction Confidence Threshold | 0.75 |

### Environment Variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DATABASE_URL` or `DB_*` | MySQL connection |
| `REDIS_URL` | Redis for Celery + cache |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI API version (default: `2024-02-01`) |
| `AZURE_OPENAI_DEPLOYMENT` | Azure OpenAI deployment name |
| `LLM_MODEL_NAME` | Model name override (default: `gpt-4o`) |
| `AZURE_DI_ENDPOINT` | Azure Document Intelligence endpoint URL |
| `AZURE_DI_KEY` | Azure Document Intelligence API key |

---

## Implementation Status

| Component | Status | Notes |
|---|---|---|
| Project structure & config | ✅ Complete | Django project, settings, URLs, Celery |
| Models & migrations | ✅ Complete | All 13 apps, all models defined |
| Core utilities & enums | ✅ Complete | Normalization, permissions, middleware, 14 enum classes |
| Extraction services | ✅ Complete | 8 service classes in 7 files; Azure Document Intelligence OCR + Azure OpenAI GPT-4o extraction |
| Extraction Celery task | ✅ Complete | Full pipeline task with retries |
| Reconciliation services | ✅ Complete | Full 3-way matching pipeline (10 services) |
| Reconciliation Celery tasks | ✅ Complete | Batch + single invoice tasks |
| Agent orchestration | ✅ Complete | Orchestrator, PolicyEngine, BaseAgent, LLMClient, DecisionLogService |
| Agent classes (7 types) | ✅ Complete | All 7 agent types implemented in `agent_classes.py` |
| Tool registry | ✅ Complete | BaseTool, 6 tool classes, ToolCallLogger |
| Review workflow | ✅ Complete | Full lifecycle service |
| DRF APIs | ✅ Complete | All ViewSets, serializers, URL routing |
| Dashboard analytics | ✅ Complete | Service + 6 API views |
| Templates (Bootstrap 5) | ✅ Complete | 16 templates with full layout (incl. partials) |
| Admin panel | ✅ Complete | All models registered |
| Audit logging models | ✅ Complete | ProcessingLog, AuditEvent, FileProcessingStatus |
| Seed data command | ✅ Complete | `python manage.py seed_data` — creates users, vendors, sample POs, invoices |
| Tests | ⬜ Not started | pytest + factory-boy configured but no tests written |
| ERP integrations | ⬜ Stub | IntegrationConfig models exist, no connectors |
| Report export logic | ⬜ Partial | Model exists, export services not implemented |

---

## How to Run

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (create .env file)
# DATABASE, REDIS, SECRET_KEY
# AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
# AZURE_DI_ENDPOINT, AZURE_DI_KEY

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Seed sample data (optional — creates users, vendors, POs, invoices)
python manage.py seed_data

# Start Redis (required for Celery)
redis-server

# Start Celery worker
celery -A config worker -l info

# Start development server
python manage.py runserver
```

**Admin login:** `http://localhost:8000/admin/`
**Dashboard:** `http://localhost:8000/dashboard/`

---

## Glossary

| Term | Definition |
|---|---|
| **3-Way Match** | Comparing Invoice vs Purchase Order vs Goods Receipt Note |
| **PO** | Purchase Order — authorization to buy goods/services |
| **GRN** | Goods Receipt Note — confirmation of goods received |
| **AP** | Accounts Payable — department responsible for paying invoices |
| **Tolerance** | Acceptable percentage difference between matched values |
| **Exception** | A discrepancy found during reconciliation (typed, severity-rated) |
| **ReAct Loop** | LLM reasoning pattern: Reason → Act (tool call) → Observe → Repeat |
| **Deterministic Match** | Rule-based matching (no AI), using fuzzy string comparison + tolerance |
| **Agentic Layer** | LLM-powered agents that analyze exceptions and recommend actions |
