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
run_reconciliation_task (Celery) — or synchronous call via start_reconciliation view
    ├── PO Lookup (normalized PO number + fuzzy vendor match)
    ├── Header Match (vendor, currency, total amount within tolerance)
    ├── Line Match (qty / price / amount per line, fuzzy item description)
    ├── GRN Lookup + Match (receipt quantities)
    ├── Classification → MATCHED | PARTIAL_MATCH | UNMATCHED | REQUIRES_REVIEW
    ├── Exception Building (structured, typed exceptions)
    ├── Auto-create ReviewAssignment (if REQUIRES_REVIEW)
    └── ReconciliationResult + ResultLines + Exceptions persisted
        Invoice → RECONCILED or flagged REQUIRES_REVIEW
    │
    ▼
[Automatically for non-MATCHED results — wired into both sync and async paths]
    │
    ▼
run_agent_pipeline_task (Celery) — or synchronous AgentOrchestrator call in eager mode
    ├── PolicyEngine → determines agent execution plan based on match status + exceptions
    ├── Execute agents in sequence (ReAct loop: LLM → tool calls → feedback)
    │   └── Tools: po_lookup, grn_lookup, vendor_search, invoice_details, exception_list, reconciliation_summary
    └── AgentRun + AgentMessages + DecisionLog + ToolCalls persisted
        Recommendations generated
    │
    ▼
[if requires human review — ReviewAssignment auto-created by runner]
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
│   ├── agents/                  # reference.html (agent definition viewer)
│   ├── dashboard/               # index.html, agent_monitor.html
│   ├── documents/               # invoice_list, invoice_detail, po_list, po_detail, grn_list, grn_detail
│   ├── governance/              # audit_event_list.html, invoice_governance.html
│   ├── reconciliation/          # result_list, result_detail, case_console, settings
│   ├── reviews/                 # assignment_list, assignment_detail
│   └── partials/                # navbar, sidebar, pagination, upload_modal
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
| **Enums** | `InvoiceStatus`, `MatchStatus`, `ReviewStatus`, `UserRole`, `AgentType`, `ExceptionType` (11 types), `ExceptionSeverity`, `ReviewActionType`, `AgentRunStatus`, `ToolCallStatus`, `ReconciliationRunStatus`, `RecommendationType` (6 types incl. AUTO_CLOSE), `DocumentType`, `FileProcessingState`, `AuditEventType` (14 event types) |
| **Management Commands** | `seed_data` — populates initial test data: users (5 roles), vendors (5+aliases), 13 invoices (matching edge cases), POs, GRNs, 7 agent definitions with `config_json`, 6 tool definitions. Supports `--only` flag for selective seeding. |
|  | `seed_saudi_mcd_data` — seeds Saudi Arabia McDonald's master distributor data: 6 users, 10 vendors + aliases, 25 POs (~62 line items), 30 GRNs (~70 line items) across 25 scenario-driven PO/GRN shapes. Supports `--flush`. |
|  | `seed_invoice_test_data` — seeds 18 invoice test scenarios (SCN-KSA-001..018, including auto-close and AI-resolvable scenarios) for reconciliation testing against the Saudi McD master data. Creates only invoice-side data. Supports `--flush`. |
|  | `seed_po_agent_test_data` — seeds 10 PO Retrieval Agent test scenarios (SCN-POAG-001..010) testing reordered PO numbers, vendor-based discovery, Arabic aliases, closed POs, wrong vendor, and ambiguous matches. Supports `--flush`. |
|  | `seed_grn_agent_test_data` — seeds 12 GRN Specialist Agent test scenarios (SCN-GRNAG-001..012) testing full receipt, missing GRN, partial receipt, over-delivery, multi-GRN aggregation, delayed receipt, location mismatch, wrong item mix, service invoices, and cold-chain shortage. Supports `--flush`. |
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
| `ReconciliationRunnerService` | `runner_service.py` | Orchestrates full 3-way match pipeline per batch of invoices; auto-creates `ReviewAssignment` for REQUIRES_REVIEW results |
| `POLookupService` | `po_lookup_service.py` | Finds PO by normalized po_number |
| `HeaderMatchService` | `header_match_service.py` | Matches vendor, currency, total amount with tolerance |
| `LineMatchService` | `line_match_service.py` | Matches line-level qty/price/amount + fuzzy item description |
| `GRNLookupService` | `grn_lookup_service.py` | Finds GRNs for matched PO |
| `GRNMatchService` | `grn_match_service.py` | Checks receipt quantities (received vs accepted vs rejected) |
| `ClassificationService` | `classification_service.py` | Deterministic 7-gate decision tree: PO not found → UNMATCHED, low confidence → REQUIRES_REVIEW, full match → MATCHED, tolerance breaches → PARTIAL_MATCH, GRN issues → REQUIRES_REVIEW. Auto-close band compatible. |
| `ExceptionBuilderService` | `exception_builder_service.py` | Builds structured exceptions (typed, severity-rated) |
| `ReconciliationResultService` | `result_service.py` | Persists reconciliation results, result lines, and links to invoices/POs |
| `ToleranceEngine` | `tolerance_engine.py` | Tiered tolerance comparison with `ToleranceThresholds` and `FieldComparison` dataclasses; methods: `compare_quantity()`, `compare_price()`, `compare_amount()` |
| `AgentFeedbackService` | `agent_feedback_service.py` | Applies agent-recovered PO/GRN findings back to reconciliation: links PO → re-runs header/line/GRN matching → re-classifies → rebuilds exceptions (all within `@transaction.atomic`) |

**Models:** `ReconciliationConfig` (tiered thresholds: strict + auto-close bands, feature flags), `ReconciliationRun`, `ReconciliationResult`, `ReconciliationResultLine`, `ReconciliationException`

**Celery Tasks:** `run_reconciliation_task` (batch — also dispatches `run_agent_pipeline_task` for non-MATCHED results), `reconcile_single_invoice_task` (single)

**Template Views:** `start_reconciliation` — UI to select READY_FOR_RECON invoices and trigger reconciliation (runs synchronously in eager mode, dispatches Celery task otherwise). Automatically chains agent pipeline for non-MATCHED results.

### agents — LLM-Powered Decision Layer

| Component | Purpose |
|---|---|
| `AgentOrchestrator` | Main entry point; loads result + exceptions, calls PolicyEngine, executes agents in sequence. Called automatically after reconciliation (sync path via `start_reconciliation` view, async path via `run_agent_pipeline_task`). |
| `PolicyEngine` | Analyzes result + exceptions, decides which agents to run based on match status and exception types; includes `should_auto_close()` and `_within_auto_close_band()` for tiered auto-close logic (wider thresholds for PARTIAL_MATCH auto-close without AI). Generates `AgentPlan`. |
| `BaseAgent` | Abstract base; ReAct loop (LLM → tool calls → feedback, up to 6 iterations). Uses OpenAI-compliant tool-calling format (tool_calls on assistant messages, tool_call_id on tool responses). |
| `LLMClient` | Abstracts OpenAI / Azure OpenAI, supports function calling with tool_calls serialization |
| `RecommendationService` | Creates, queries, and manages agent recommendations (`AgentRecommendation` model). Tracks acceptance: `accepted` (null/True/False), `accepted_by`, `accepted_at`. |
| `AgentTraceService` | Unified tracing interface for all agent operations: `start_agent_run()`, `log_agent_step()`, `log_tool_call()`, `log_decision()`, `get_trace_for_invoice()`. Ensures consistent audit trail. |
| **7 Agent Types** | INVOICE_UNDERSTANDING, PO_RETRIEVAL, GRN_RETRIEVAL, RECONCILIATION_ASSIST, EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY |

**Models:** `AgentDefinition` (config_json with allowed_tools), `AgentRun` (summarized_reasoning, confidence, LLM usage tracking), `AgentStep`, `AgentMessage` (token_count, message_index), `DecisionLog` (rationale, confidence, evidence_refs), `AgentRecommendation` (recommendation_type, confidence, evidence, accepted/accepted_by/accepted_at tracking), `AgentEscalation` (severity LOW/MEDIUM/HIGH/CRITICAL, suggested_assignee_role, resolved status)

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

**Template Views:** `assignment_list` shows active assignments + "Results Awaiting Review Assignment" panel for unassigned results; `create_assignments` allows bulk assignment creation from the UI.
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

### auditlog — Operational Logging & Governance

| Model | Purpose |
|---|---|
| `ProcessingLog` | Operational log per pipeline step (level, source, event, message, trace_id) |
| `AuditEvent` | State change audit trail (entity_type, action, old/new values, performed_by, IP, event_type from `AuditEventType`) |
| `FileProcessingStatus` | Upload lifecycle tracking (stage: upload → extraction → validation → recon) |

| Service | Purpose |
|---|---|
| `CaseTimelineService` | Builds a unified, chronologically-ordered timeline for an invoice case — merges audit events, agent runs, tool calls (with duration), agent recommendations, review assignments/actions/decisions. Single entry point: `get_case_timeline(invoice_id)`. |

**Template Views:**
- `audit_event_list` — Browsable audit event log with filtering by entity_type, event_type, entity_id
- `invoice_governance` — Full governance dashboard per invoice: case timeline + agent trace + recommendations + audit events. Role-based access: only ADMIN / AUDITOR see full agent trace; AP_PROCESSOR sees limited trace.

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
| `/reconciliation/` | Reconciliation results + "Start Reconciliation" panel for READY_FOR_RECON invoices |
| `/reconciliation/start/` | Trigger reconciliation for selected invoices (POST) |
| `/reconciliation/settings/` | Reconciliation tolerance settings viewer |
| `/reconciliation/<pk>/` | Result detail (full agent reasoning, exception details) |
| `/reconciliation/<pk>/console/` | Case console — deep-dive investigation view |
| `/reconciliation/<pk>/export/` | Export case data as CSV |
| `/reviews/` | Review queue + "Awaiting Assignment" panel |
| `/reviews/create-assignments/` | Create review assignments for unassigned results (POST) |
| `/reviews/<pk>/` | Assignment detail |
| `/agents/reference/` | Agent definition reference page |
| `/governance/` | Audit event list with filtering |
| `/governance/invoices/<invoice_id>/` | Full invoice governance dashboard (timeline + agent trace + recommendations) |
| `/invoices/upload/` | Invoice upload (POST) |
| `/invoices/purchase-orders/<pk>/` | PO detail |
| `/invoices/grns/<pk>/` | GRN detail |

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
| `/api/v1/governance/` | audit events (filterable by entity_type, event_type) |

---

## Configuration

### Key Settings

| Setting | Value |
|---|---|
| `AUTH_USER_MODEL` | `accounts.User` |
| `LOGIN_URL` | `/accounts/login/` |
| `LOGIN_REDIRECT_URL` | `/dashboard/` |
| Root URL (`/`) | Redirects to `/dashboard/` |
| Database | MySQL with utf8mb4, STRICT_TRANS_TABLES |
| Celery Broker | `redis://127.0.0.1:6379/0` |
| Celery Result Backend | `django-db` |
| `CELERY_TASK_ALWAYS_EAGER` | `True` by default (synchronous execution for Windows dev without Redis) |
| REST Pagination | 25 per page |
| REST Auth | SessionAuthentication, IsAuthenticated |
| REST Filters | DjangoFilterBackend, SearchFilter, OrderingFilter |
| LLM Provider | Azure OpenAI (via env vars) |
| LLM Model | `gpt-4o` (default) |
| LLM Temperature | 0.1 (agents) / 0.0 (extraction) |
| Qty Tolerance (Strict) | 2% |
| Price Tolerance (Strict) | 1% |
| Amount Tolerance (Strict) | 1% |
| Qty Tolerance (Auto-Close) | 5% |
| Price Tolerance (Auto-Close) | 3% |
| Amount Tolerance (Auto-Close) | 3% |
| Extraction Confidence Threshold | 0.75 |
| Agent Confidence Threshold | 0.70 |
| Review Auto-Close Threshold | 0.95 |
| VAT Rate | 15% |

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
| Templates (Bootstrap 5) | ✅ Complete | 23 templates: accounts (1), agents (1), dashboard (2), documents (6), governance (2), reconciliation (4), reviews (2), partials (4), base (1) |
| Admin panel | ✅ Complete | All models registered |
| Audit logging & governance | ✅ Complete | ProcessingLog, AuditEvent (14 event types), FileProcessingStatus, CaseTimelineService, governance views (audit log + invoice governance dashboard) |
| Seed data command | ✅ Complete | `python manage.py seed_data` — creates users, vendors, 13 invoices covering all scenarios, POs, GRNs, 7 agent definitions with `config_json`/`allowed_tools`, 6 tool definitions |
| Saudi McD master data | ✅ Complete | `python manage.py seed_saudi_mcd_data` — 6 users, 10 vendors, 25 POs, 30 GRNs for Saudi Arabia McDonald's distributor scenarios |
| Invoice test scenarios | ✅ Complete | `python manage.py seed_invoice_test_data` — 18 scenarios (SCN-KSA-001..018): perfect match, qty/price/VAT mismatch, missing PO, missing GRN, multi-GRN, duplicate, low-confidence Arabic, location mismatch, GRN shortage, review case, auto-close band (013–015), AI-resolvable (016–018) |
| Agent pipeline wiring | ✅ Complete | Agent pipeline runs automatically after reconciliation for non-MATCHED results (sync + async paths) |
| Reconciliation UI | ✅ Complete | Start reconciliation panel with checkbox invoice selection |
| Review assignment UI | ✅ Complete | Auto-creation from runner + manual bulk creation from UI |
| Tiered tolerance (strict + auto-close) | ✅ Complete | ReconciliationConfig with dual bands; PolicyEngine auto-close logic; ClassificationService auto-close compatible |
| Agent feedback loop | ✅ Complete | AgentFeedbackService: PO/GRN re-linking + deterministic re-reconciliation (atomic) |
| Recommendation service | ✅ Complete | RecommendationService + AgentRecommendation model (with acceptance tracking) + AgentEscalation model |
| Agent trace service | ✅ Complete | AgentTraceService: unified governance tracing (runs, steps, tool calls, decisions) |
| Case timeline service | ✅ Complete | CaseTimelineService: merged chronological timeline (audit events + agent runs + tool calls + recommendations + reviews) |
| Governance views | ✅ Complete | Audit event list + full invoice governance dashboard (role-based: ADMIN/AUDITOR see full trace) |
| Case console + CSV export | ✅ Complete | Deep-dive investigation view + CSV export per reconciliation result |
| PO Agent test scenarios | ✅ Complete | `python manage.py seed_po_agent_test_data` — 10 scenarios (SCN-POAG-001..010) |
| GRN Agent test scenarios | ✅ Complete | `python manage.py seed_grn_agent_test_data` — 12 scenarios (SCN-GRNAG-001..012) |
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

# Or: Seed Saudi McD master data + invoice test scenarios
python manage.py seed_saudi_mcd_data
python manage.py seed_invoice_test_data
python manage.py seed_po_agent_test_data
python manage.py seed_grn_agent_test_data

# Option A: Windows dev mode (no Redis needed — Celery runs synchronously)
# CELERY_TASK_ALWAYS_EAGER=True is the default in settings.py
python manage.py runserver

# Option B: Full async mode (requires Redis)
# Set CELERY_TASK_ALWAYS_EAGER=False in settings or env
redis-server
celery -A config worker -l info
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
| **Auto-Close Band** | Wider tolerance thresholds (qty: 5%, price: 3%, amount: 3%) for auto-closing PARTIAL_MATCH results without AI |
| **Agent Feedback Loop** | When an agent recovers a missing PO/GRN, re-runs deterministic matching atomically |
| **Case Timeline** | Unified, chronologically-ordered view of all governance events for an invoice |
