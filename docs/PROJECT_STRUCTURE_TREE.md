# Project Structure Tree and File Usage

This document gives a quick view of the repository layout and what each major file/folder is used for.

## 1) High-Level Project Tree

```text
3-way-po-recon/
├── .github/                      # Repo automation/workflows + Copilot instructions
├── .vscode/                      # Editor workspace settings
├── .venv/                        # Local Python virtual environment (local/dev)
├── apps/                         # Django app modules (business domains)
│   ├── accounts/                 # Users, roles, permissions, RBAC
│   ├── agents/                   # Agent orchestration, runs, decisions
│   ├── auditlog/                 # Audit events, governance timeline, trace history
│   ├── benchmarking/             # Benchmarking features and artifacts
│   ├── cases/                    # Case lifecycle and review workflow integration
│   ├── copilot/                  # Copilot session and assistant features
│   ├── core/                     # Shared enums, utilities, base classes, middleware
│   ├── core_eval/                # Evaluation + learning signal framework
│   ├── dashboard/                # Dashboard services/views
│   ├── documents/                # Invoice, PO, GRN models and data flows
│   ├── erp_integration/          # ERP connectors, resolvers, submission, cache
│   ├── extraction/               # OCR/LLM extraction pipeline + approval flow
│   ├── extraction_configs/       # Extraction configuration models/views
│   ├── extraction_core/          # Shared extraction internals
│   ├── extraction_documents/     # Extraction-related document handling
│   ├── integrations/             # Integration configs/logging
│   ├── posting/                  # Posting orchestration and workbench layer
│   ├── posting_core/             # Posting pipeline core + mapping/validation
│   ├── procurement/              # Procurement workflows, prefill, services
│   ├── reconciliation/           # 2-way/3-way matching and exceptions
│   ├── reports/                  # Report views/services
│   ├── reviews/                  # Legacy/migration-only review app shell
│   ├── tools/                    # Agent tools registry and tool classes
│   └── vendors/                  # Vendor master data + vendor UI/API
├── config/                       # Django settings, URL routing, ASGI/WSGI, Celery
├── deploy/                       # Deployment scripts, systemd, nginx, monitoring
├── docs/                         # Architecture and feature documentation
├── logs/                         # Runtime logs
├── media/                        # Uploaded files (invoices, forms, etc.)
├── notebooks/                    # Jupyter notebooks for experiments
├── requirement_documents/        # Business requirement/source docs
├── scripts/                      # Utility scripts and one-off maintenance tools
├── static/                       # Source static assets (css/js/images)
├── staticfiles/                  # Collected static output for deployment
├── templates/                    # Django templates by module
├── manage.py                     # Django management entrypoint
├── requirements.txt              # Python dependency list
├── README.md                     # Project setup + overview
├── conftest.py                   # Pytest shared fixtures/config
└── docker-compose.loki.yml       # Loki/observability local stack config
```

## 2) Core Files and Their Uses

| File | Use |
|---|---|
| `manage.py` | Runs Django commands (`runserver`, `migrate`, `test`, custom commands). |
| `requirements.txt` | Pins/installable Python dependencies for this project. |
| `config/settings.py` | Main Django settings (DB, apps, middleware, auth, Celery, API). |
| `config/test_settings.py` | Test-specific settings used by pytest/test runs. |
| `config/urls.py` | Root URL router that includes app URLs and API routes. |
| `config/celery.py` | Celery app initialization and task discovery config. |
| `conftest.py` | Shared pytest fixtures and test bootstrapping. |
| `README.md` | Quick-start, setup, and platform overview for contributors. |
| `Agent_Functionalities.txt` | Functional notes/summary for agent capabilities. |
| `task.txt` | Task scratchpad or project-specific run notes. |
| `LANDING_PAGE_SETUP.md` | Setup details for landing page behavior/content. |

## 3) Standard Django App File Pattern (inside each app)

Most app modules under `apps/<app_name>/` follow this structure:

- `models.py` -> database models
- `serializers.py` -> DRF serializers
- `views.py` -> API/DRF views
- `template_views.py` -> HTML/template views
- `api_urls.py` -> API route registration
- `urls.py` -> template/UI route registration
- `services/` or `services.py` -> business logic layer
- `tasks.py` -> Celery task entry points
- `admin.py` -> Django admin registration
- `tests/` -> app test coverage

## 4) Important Docs to Start With

- `docs/PROJECT.md` -> full architecture and data flow
- `docs/MULTI_TENANT.md` -> tenant model and isolation rules
- `docs/AGENT_ARCHITECTURE.md` -> agent orchestration and contracts
- `docs/ERP_INTEGRATION.md` -> ERP integration design and APIs
- `docs/POSTING_AGENT.md` -> posting pipeline stages and statuses
- `docs/EVAL_LEARNING.md` -> evaluation + learning framework
- `docs/LANGFUSE_INTEGRATION.md` -> tracing and score integration details

## 5) Quick Navigation Tips

- Business domain code: `apps/`
- Environment/config wiring: `config/`
- User-facing HTML: `templates/`
- API + backend logic: app `views.py` + `services/`
- Operations and deployment: `deploy/`
- Architecture references: `docs/`
