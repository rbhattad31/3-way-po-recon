# 3-Way PO Reconciliation Platform

An enterprise Django application that automates **3-way Purchase Order (PO) reconciliation** — matching Invoices against Purchase Orders (POs) and Goods Receipt Notes (GRNs). The system extracts invoice data from uploaded PDFs, performs deterministic matching with tolerance-based comparison, routes complex cases to LLM-powered agents, and sends unresolvable items to human reviewers.

## Tech Stack

- **Backend:** Django 4.2+, Django REST Framework, Celery + Redis
- **Database:** MySQL (utf8mb4)
- **AI/ML:** Azure OpenAI (GPT-4o), Azure Document Intelligence, LangChain
- **Matching:** thefuzz, RapidFuzz, python-Levenshtein
- **Frontend:** Django Templates, Bootstrap 5, Chart.js
- **Testing:** pytest, pytest-django, factory-boy

## Architecture

```
Upload PDF → OCR (Azure DI) → LLM Extract (Azure OpenAI GPT-4o) → Normalize → Validate → Match (3-way) → Classify
    → [Agent Analysis] → [Human Review] → Approve/Reject
```

**13 Django apps** under `apps/`:
`core` · `accounts` · `vendors` · `documents` · `extraction` · `reconciliation` · `agents` · `tools` · `reviews` · `dashboard` · `reports` · `auditlog` · `integrations`

## Key Features

- **Invoice extraction pipeline** — 8 service classes: upload, Azure Document Intelligence OCR + Azure OpenAI GPT-4o structured extraction, parsing, normalization, validation, duplicate detection, persistence
- **3-way matching engine** — 10 services: PO lookup, header/line/GRN matching, classification, exception building, tolerance engine
- **7 LLM-powered agents** — Exception Analysis, Invoice Understanding, PO Retrieval, GRN Retrieval, Review Routing, Case Summary, Reconciliation Assist
- **6 agent tools** — POLookupTool, GRNLookupTool, VendorSearchTool, InvoiceDetailsTool, ExceptionListTool, ReconciliationSummaryTool
- **Human review workflow** — assignment, review actions, comments, decisions with full audit trail
- **Dashboard analytics** — 6 API endpoints for summary stats, match breakdowns, agent performance
- **16 Bootstrap 5 templates** — invoices, POs, GRNs, reconciliation results, reviews, agent monitor
- **Full DRF API** — REST endpoints under `/api/v1/` with filtering, search, and pagination
- **Seed data** — `python manage.py seed_data` to populate sample users, vendors, POs, and invoices

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment variables (.env file)
# DATABASE, REDIS, SECRET_KEY
# AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT
# AZURE_DI_ENDPOINT, AZURE_DI_KEY

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Seed sample data (optional)
python manage.py seed_data

# Start Redis
redis-server

# Start Celery worker
celery -A config worker -l info

# Start development server
python manage.py runserver
```

**Dashboard:** http://localhost:8000/dashboard/
**Admin:** http://localhost:8000/admin/

## Documentation

See [PROJECT.md](PROJECT.md) for full architecture details, model reference, service documentation, API endpoints, and status flows.

## Implementation Status

| Area | Status |
|---|---|
| Models, migrations, enums, permissions | ✅ Complete |
| Extraction pipeline (Azure DI + Azure OpenAI, 8 services) | ✅ Complete |
| Reconciliation engine (10 services + Celery tasks) | ✅ Complete |
| Agent orchestration (7 agents, policy engine, 6 tools) | ✅ Complete |
| Review workflow, DRF APIs, templates, admin, seed data | ✅ Complete |
| Tests (pytest + factory-boy) | ⬜ Not started |
| Extraction refinement (edge-case layouts, multi-page) | ⬜ Not started |
| ERP integrations, report exports | ⬜ Stub |
| Docker / CI-CD / deployment | ⬜ Not started |