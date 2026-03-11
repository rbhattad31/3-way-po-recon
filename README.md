# 3-Way PO Reconciliation Platform

An enterprise Django application that automates **configurable 2-way and 3-way Purchase Order (PO) reconciliation** — matching Invoices against Purchase Orders (POs) and, when applicable, Goods Receipt Notes (GRNs). The system extracts invoice data from uploaded PDFs, resolves the reconciliation mode (2-way for services, 3-way for stock), performs deterministic matching with tolerance-based comparison, routes complex cases to LLM-powered agents, and sends unresolvable items to human reviewers.

## Tech Stack

- **Backend:** Django 4.2+, Django REST Framework, Celery + Redis
- **Database:** MySQL (utf8mb4)
- **AI/ML:** Azure OpenAI (GPT-4o), Azure Document Intelligence, LangChain
- **Matching:** thefuzz, RapidFuzz, python-Levenshtein
- **Frontend:** Django Templates, Bootstrap 5, Chart.js
- **Testing:** pytest, pytest-django, factory-boy

## Architecture

```
Upload PDF → OCR (Azure DI) → LLM Extract (Azure OpenAI GPT-4o) → Normalize → Validate → Resolve Mode (2-way/3-way) → Match → Classify
    → [Agent Analysis] → [Human Review] → Approve/Reject
```

**13 Django apps** under `apps/`:
`core` · `accounts` · `vendors` · `documents` · `extraction` · `reconciliation` · `agents` · `tools` · `reviews` · `dashboard` · `reports` · `auditlog` · `integrations`

## Key Features

- **Invoice extraction pipeline** — 8 service classes: upload, Azure Document Intelligence OCR + Azure OpenAI GPT-4o structured extraction, parsing, normalization, validation, duplicate detection, persistence
- **3-way matching engine** — 14 services: PO lookup, mode resolution, header/line/GRN matching, 2-way match service, 3-way match service, execution router, classification, exception building, tiered tolerance engine, agent feedback loop (PO/GRN re-reconciliation)
- **Configurable 2-way / 3-way mode** — ReconciliationPolicy rules (vendor, category, location, business-unit), ModeResolver with 3-tier cascade (policy → heuristic → config default), TwoWayMatchService (Invoice vs PO only), ThreeWayMatchService (Invoice vs PO vs GRN), mode-aware agents/classification/exceptions
- **Tiered tolerance system** — Strict band (2%/1%/1%) for initial classification + auto-close band (5%/3%/3%) for PARTIAL_MATCH auto-close without AI
- **7 LLM-powered agents** — Exception Analysis, Invoice Understanding, PO Retrieval, GRN Retrieval, Review Routing, Case Summary, Reconciliation Assist — wired to run automatically after reconciliation for non-MATCHED results
- **6 agent tools** — po_lookup, grn_lookup, vendor_search, invoice_details, exception_list, reconciliation_summary (OpenAI-compliant tool-calling format)
- **Agent feedback loop** — When PO/GRN agent recovers a missing document, atomic re-reconciliation: re-link PO → re-match → re-classify → rebuild exceptions
- **Recommendation & escalation tracking** — AgentRecommendation (with acceptance tracking) + AgentEscalation (severity-based, suggested assignee role)
- **Human review workflow** — auto-creation of ReviewAssignment for REQUIRES_REVIEW results, manual bulk assignment UI, review actions, comments, decisions with full audit trail
- **Governance & auditability** — Unified case timeline (CaseTimelineService), agent trace service, audit event log (14 event types), role-based governance dashboard (ADMIN/AUDITOR full trace)
- **Reconciliation UI** — "Start Reconciliation" panel with checkbox selection, case console (deep-dive), tolerance settings viewer, CSV export
- **Dashboard analytics** — 7 API endpoints for summary stats, match breakdowns, agent performance, mode breakdown
- **23 Bootstrap 5 templates** — invoices, POs, GRNs, reconciliation results, case console, settings, reviews, agent monitor/reference, governance (audit log + invoice governance), upload modal
- **Full DRF API** — REST endpoints under `/api/v1/` with filtering, search, and pagination; governance API under `/api/v1/governance/`
- **Seed data** — `python manage.py seed_data` — 5 users, 5 vendors, 13 invoices (with edge-case scenarios), POs, GRNs, 7 agent definitions, 6 tool definitions
- **Saudi McD test data** — `seed_saudi_mcd_data` + `seed_invoice_test_data` + `seed_po_agent_test_data` + `seed_grn_agent_test_data` — 10 vendors, 25 POs, 30 GRNs, 40 test scenarios (18 invoice + 10 PO Agent + 12 GRN Agent) for Saudi Arabia McDonald’s distributor reconciliation testing- **Mixed-mode test data** — `seed_mixed_mode_data` — 12 scenarios (SCN-MODE-001..012), 7 reconciliation policies, service vendor, covering all mode resolution paths (policy, heuristic, default fallback)
## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment variables (.env file)
# DATABASE, SECRET_KEY
# AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
# AZURE_DI_ENDPOINT, AZURE_DI_KEY

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Seed sample data (optional)
python manage.py seed_data

# Or: Seed Saudi McD master data + 52 test scenarios
python manage.py seed_saudi_mcd_data
python manage.py seed_invoice_test_data
python manage.py seed_po_agent_test_data
python manage.py seed_grn_agent_test_data
python manage.py seed_mixed_mode_data

# Option A: Windows dev mode (no Redis needed — runs synchronously)
# CELERY_TASK_ALWAYS_EAGER=True is the default in settings.py
python manage.py runserver

# Option B: Full async mode (requires Redis)
# Set CELERY_TASK_ALWAYS_EAGER=False in settings or env
redis-server
celery -A config worker -l info
python manage.py runserver
```

**Dashboard:** http://localhost:8000/dashboard/
**Admin:** http://localhost:8000/admin/
**Governance:** http://localhost:8000/governance/

## Documentation

See [PROJECT.md](PROJECT.md) for full architecture details, model reference, service documentation, API endpoints, and status flows.

## Implementation Status

| Area | Status |
|---|---|
| Models, migrations, enums, permissions | ✅ Complete |
| Extraction pipeline (Azure DI + Azure OpenAI, 8 services) | ✅ Complete |
| Reconciliation engine (14 services + Celery tasks) | ✅ Complete |
| Agent orchestration (7 agents, policy engine, 6 tools) | ✅ Complete |
| Agent pipeline wiring (auto-runs after reconciliation) | ✅ Complete |
| Reconciliation UI (start recon with checkbox selection) | ✅ Complete |
| Review workflow + auto-assignment + bulk assignment UI | ✅ Complete |
| DRF APIs, templates, admin, seed data (13 invoices) | ✅ Complete |
| Saudi McD seed data (25 POs, 30 GRNs, 40 test scenarios) | ✅ Complete |
| Configurable 2-way/3-way reconciliation mode | ✅ Complete |
| Mixed-mode test data (12 scenarios, 7 policies) | ✅ Complete |
| Tiered tolerance (strict + auto-close bands) | ✅ Complete |
| Agent feedback loop (PO/GRN re-reconciliation) | ✅ Complete |
| Recommendation & escalation tracking | ✅ Complete |
| Governance views (audit log + invoice governance) | ✅ Complete |
| Case console + CSV export | ✅ Complete |
| Tests (pytest + factory-boy) | ⬜ Not started |
| Extraction refinement (edge-case layouts, multi-page) | ⬜ Not started |
| ERP integrations, report exports | ⬜ Stub |
| Docker / CI-CD / deployment | ⬜ Not started |