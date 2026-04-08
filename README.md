# 3-Way PO Reconciliation Platform

An enterprise Django application that automates **configurable 2-way and 3-way Purchase Order (PO) reconciliation** -- matching Invoices against Purchase Orders (POs) and, when applicable, Goods Receipt Notes (GRNs). The system uses **shared-database multi-tenancy** (row-level isolation via `CompanyProfile`) to support multiple organizations. It extracts invoice data from uploaded PDFs using a modular AI pipeline, resolves the reconciliation mode, performs deterministic matching with tolerance-based comparison, routes complex cases to LLM-powered agents, and sends unresolvable items to human reviewers.

## Tech Stack

- **Backend:** Django 4.2+, Django REST Framework, Celery + Redis
- **Database:** MySQL (utf8mb4)
- **AI/ML:** Azure OpenAI (GPT-4o), Azure Document Intelligence, LangChain, Langfuse (LLM observability)
- **Matching:** thefuzz, RapidFuzz, python-Levenshtein
- **Frontend:** Django Templates, Bootstrap 5, Chart.js
- **Testing:** pytest, pytest-django, factory-boy (124+ passing tests)

## Architecture

```
Upload PDF
  → OCR (Azure DI)
  → Category Classify (goods / service / travel)
  → Compose Prompt (base + category overlay + country overlay)
  → LLM Extract (Azure OpenAI GPT-4o)
  → Response Repair (5 deterministic rules)
  → Parse → Normalize → Validate
  → Resolve Mode (2-way / 3-way)
  → Match → Classify Exceptions
  → [Agent Analysis] → [Human Review] → Approve / Reject
```

**26 Django apps** under `apps/`:
`core` · `accounts` · `vendors` · `documents` · `extraction` · `extraction_core` · `extraction_configs` · `extraction_documents` · `reconciliation` · `agents` · `tools` · `reviews` · `cases` · `copilot` · `dashboard` · `reports` · `auditlog` · `posting` · `posting_core` · `procurement` · `erp_integration` · `integrations` · `vendors`

## Key Features

### Invoice Extraction (Phase 2 Pipeline)
- **11-stage extraction pipeline** — Azure DI OCR + invoice category classification (goods/service/travel) + modular prompt composition (base + category + country overlays) + GPT-4o structured extraction + deterministic response repair + parse + normalize + validate + duplicate detection + persist + approval gate
- **Modular prompt system** — 18 prompts in Langfuse: `extraction.invoice_base`, 3 category overlays, 2 country overlays (India GST, generic VAT), 12 agent prompts; prompt_hash logged per extraction for traceability
- **Deterministic response repair** — 5 rules pre-parser: invoice number exclusion (IRN/CART Ref/Hotel Booking ID), tax percentage recomputation, subtotal/line reconciliation, line-level tax allocation, travel line consolidation
- **Rich extracted fields** — vendor_name, vendor_tax_id, buyer_name, invoice_number, invoice_date, due_date, po_number, currency, subtotal, tax_percentage, tax_amount, tax_breakdown (cgst/sgst/igst/vat), total_amount, document_type, line_items
- **Human approval gate** — ExtractionApproval with field correction tracking, touchless rate analytics; configurable auto-approval threshold
- **Credit system** — per-user credit accounts (reserve → consume → refund lifecycle)
- **Langfuse observability** — prompt version, invoice category, repair actions, token counts, confidence scores all traced

### Reconciliation Engine
- **3-way matching engine** — 14 services: PO lookup, mode resolution, header/line/GRN matching, classification, exception building, tiered tolerance engine, agent feedback loop
- **Configurable 2-way / 3-way mode** — ReconciliationPolicy rules (vendor, category, location, business-unit), ModeResolver 3-tier cascade (policy → heuristic → config default)
- **Tiered tolerance system** — Strict band (2%/1%/1%) for initial classification + auto-close band (5%/3%/3%) for PARTIAL_MATCH auto-close without AI

### AI Agent System
- **8 LLM-powered agents** — InvoiceExtraction, InvoiceUnderstanding, PO Retrieval, GRN Retrieval, Exception Analysis, Review Routing, Case Summary, Reconciliation Assist
- **6 agent tools** — po_lookup, grn_lookup, vendor_search, invoice_details, exception_list, reconciliation_summary (OpenAI tool-calling format)
- **Agent feedback loop** — When PO/GRN agent recovers a missing document, atomic re-reconciliation: re-link → re-match → re-classify → rebuild exceptions
- **RBAC guardrails** — `AgentGuardrailsService` enforces per-agent, per-tool, and per-recommendation permissions; SYSTEM_AGENT service account

### Enterprise Platform
- **Case management** — 11-stage state machine (INTAKE → EXTRACTION → PATH_RESOLUTION → … → CLOSED), 3 processing paths (TWO_WAY / THREE_WAY / NON_PO)
- **Human review workflow** — auto-assignment for REQUIRES_REVIEW, review actions/comments/decisions with full audit trail
- **ERP integration** — 6 connector types: Custom API, SQL Server, MySQL, Dynamics 365, Zoho Books, Salesforce; `ConnectorFactory` + `CacheService` (L1/L2/L3)
- **Invoice posting** — ERP posting workflow (PROPOSED → REVIEW_REQUIRED → READY_TO_SUBMIT → SUBMITTED), mapping engine, confidence scoring, governance trail
- **Procurement intelligence** — should-cost benchmarking, compliance validation, quotation management
- **RBAC** — 6 roles (ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR, SYSTEM_AGENT), 40 permissions, per-user overrides
- **Governance & audit** — 38+ audit event types, unified case timeline, agent RBAC compliance metrics, 9 governance API endpoints
- **Dashboard analytics** — 7 API endpoints for summary stats, match breakdowns, agent performance, mode breakdown
- **34+ Bootstrap 5 templates** — workbench, extraction console (6-tab), approval queue, reconciliation, case console, review queue, governance, RBAC admin
- **Full DRF API** — REST endpoints under `/api/v1/` with filtering, search, pagination

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment variables (.env file)
# DATABASE_URL, SECRET_KEY
# AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
# AZURE_DI_ENDPOINT, AZURE_DI_KEY
# AZURE_BLOB_CONNECTION_STRING
# LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY   (optional — disables LLM tracing if unset)

# Run migrations
python manage.py migrate

# Seed data (recommended order)
python manage.py seed_config --flush
python manage.py seed_rbac --sync-users
python manage.py seed_prompts --force
python manage.py push_prompts_to_langfuse     # sync 18 prompts to Langfuse
python manage.py seed_ap_data --reset --summary

# Option A: Windows dev mode (synchronous — no Redis needed)
# CELERY_TASK_ALWAYS_EAGER=True is default in settings.py
python manage.py runserver

# Option B: Full async mode
# Set CELERY_TASK_ALWAYS_EAGER=False in .env
redis-server
celery -A config worker -l info
python manage.py runserver
```

**Dashboard:** http://localhost:8000/dashboard/
**Admin:** http://localhost:8000/admin/
**Extraction:** http://localhost:8000/extraction/
**Governance:** http://localhost:8000/governance/

## Documentation

| Document | Purpose |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | Full architecture, model reference, service docs, API endpoints, status flows |
| [docs/EXTRACTION_AGENT.md](docs/EXTRACTION_AGENT.md) | Invoice extraction pipeline, Phase 2 prompt composition, response repair, credit system |
| [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) | Agent framework, ReAct loop, tool system, RBAC guardrails, observability |
| [docs/LANGFUSE_INTEGRATION.md](docs/LANGFUSE_INTEGRATION.md) | Langfuse tracing, prompt management, scoring, known SDK quirks |
| [docs/POSTING_AGENT.md](docs/POSTING_AGENT.md) | Invoice posting pipeline, ERP mapping engine, confidence scoring |
| [docs/PROCUREMENT.md](docs/PROCUREMENT.md) | Procurement intelligence, benchmarking, compliance |
| [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md) | Production deployment (Azure Ubuntu, Nginx, Gunicorn, Systemd) |
| [deploy/MONITORING_OPS.md](deploy/MONITORING_OPS.md) | Monitoring, Celery/Redis observability, alerting, troubleshooting |
| [docs/debugging/extraction_ocr_debugging.md](docs/debugging/extraction_ocr_debugging.md) | OCR + LLM debugging guide |

## Implementation Status

| Area | Status |
|---|---|
| Models, migrations, enums, permissions | ✅ Complete |
| Phase 2 extraction pipeline (11 stages: OCR + classify + compose + LLM + repair + parse + normalize + validate + duplicate + persist + approve) | ✅ Complete |
| Invoice model extended (vendor_tax_id, buyer_name, due_date, tax_percentage, tax_breakdown) | ✅ Complete |
| Modular prompt system (18 prompts, Langfuse-synced) | ✅ Complete |
| Deterministic response repair (5 rules, 25 tests) | ✅ Complete |
| Reconciliation engine (14 services + Celery tasks) | ✅ Complete |
| Agent orchestration (8 agents, policy engine, 6 tools) | ✅ Complete |
| Agent pipeline wiring (auto-runs after reconciliation) | ✅ Complete |
| Reconciliation UI (start recon with checkbox selection) | ✅ Complete |
| Review workflow + auto-assignment + bulk assignment UI | ✅ Complete |
| DRF APIs, templates, admin, seed data | ✅ Complete |
| Saudi McD seed data (25 POs, 30 GRNs, 40 test scenarios) | ✅ Complete |
| Configurable 2-way/3-way reconciliation mode | ✅ Complete |
| Tiered tolerance (strict + auto-close bands) | ✅ Complete |
| Agent feedback loop (PO/GRN re-reconciliation) | ✅ Complete |
| RBAC guardrails (per-agent, per-tool, per-recommendation) | ✅ Complete |
| Governance views (audit log + invoice governance) | ✅ Complete |
| Case console + CSV export | ✅ Complete |
| ERP integration framework (6 connector types) | ✅ Complete |
| Invoice posting workflow | ✅ Complete |
| Langfuse observability integration | ✅ Complete |
| Tests: Reconciliation (73) + Extraction Phase 2 (51) | ✅ 124+ passing |
| Extraction refinement (edge-case layouts, multi-page) | ⬜ Not started |
| Report exports (Excel/PDF) | ⬜ Stub |
| Celery Beat (scheduled tasks) | ⬜ Not started |
| Email notifications | ⬜ Not started |
| Docker / CI-CD | ⬜ Not started |
