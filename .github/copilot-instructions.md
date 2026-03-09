# Copilot Instructions — 3-Way PO Reconciliation Platform

## Project Context

This is a Django 4.2+ enterprise application for **3-way Purchase Order reconciliation** (Invoice vs PO vs GRN). It uses MySQL, Celery+Redis, OpenAI/Azure OpenAI, and Bootstrap 5 templates. The codebase lives under `apps/` with 13 Django apps.

**Read [PROJECT.md](../PROJECT.md) for full architecture, models, services, and data flow.**

---

## Code Conventions

### Django & Python
- **Python 3.8+**, type hints encouraged on public functions.
- **All models** inherit from `apps.core.models.BaseModel` (which includes `TimestampMixin` + `AuditMixin`), unless they are lightweight join/log tables that use `TimestampMixin` only.
- **Soft delete** via `SoftDeleteMixin` (is_active flag) — never hard-delete business entities.
- **Enums** live in `apps/core/enums.py` — always add new enums there, never inline string choices.
- **Constants** live in `apps/core/constants.py`.
- **Utility functions** (normalization, parsing, tolerance checks) live in `apps/core/utils.py`.
- **Permissions** are role-based classes in `apps/core/permissions.py`.
- Custom **User model** uses email login (not username): `AUTH_USER_MODEL = "accounts.User"`.
- **Settings** are in `config/settings.py`; environment-specific values come from env vars or `.env`.

### Services Pattern
- Business logic goes in **service classes** (e.g., `apps/reconciliation/services/runner_service.py`), not in views or serializers.
- Services are stateless classes with class methods or instance methods.
- Views/tasks call services; services call the ORM.
- Keep views thin — only request parsing, permission checking, and response formatting.

### API Design
- All APIs are under `/api/v1/` using **Django REST Framework**.
- Use `ModelViewSet` or `ReadOnlyModelViewSet` with proper `permission_classes`.
- Default pagination: 25 per page (`PageNumberPagination`).
- Filtering via `django-filter` (`DjangoFilterBackend`), searching via `SearchFilter`, ordering via `OrderingFilter`.
- Serializers go in `serializers.py` per app. Use separate List/Detail serializers when needed.
- API URLs go in `api_urls.py` per app; template URLs go in `urls.py`.

### Celery Tasks
- Tasks go in `tasks.py` per app.
- Use `@shared_task(bind=True)` with explicit `max_retries` and `default_retry_delay`.
- Tasks should call service classes — never put business logic directly in task functions.
- Use `acks_late=True` for important tasks.
- Serialization format: JSON.

### Agent System
- All agents extend `BaseAgent` (in `apps/agents/services/`).
- Agents use **ReAct loop**: LLM → parse tool calls → execute tools → loop (max 6 iterations).
- Tools are registered in `apps/tools/registry/` via decorator pattern.
- `AgentOrchestrator` is the entry point; `PolicyEngine` decides which agents to run.
- Every agent run, message, tool call, and decision is persisted for auditability.
- LLM client supports both OpenAI and Azure OpenAI (configurable via env vars).

### Templates
- Templates use **Bootstrap 5** with Django template inheritance from `base.html`.
- Template views go in `template_views.py` per app (separate from API views in `views.py`).
- Partial templates go in `templates/partials/`.
- Use Django template tags and context processors (e.g., `pending_reviews` in `apps/core/context_processors.py`).

---

## File Organization

| What | Where |
|---|---|
| Models | `apps/<app>/models.py` |
| DRF Serializers | `apps/<app>/serializers.py` |
| API Views (DRF) | `apps/<app>/views.py` |
| Template Views | `apps/<app>/template_views.py` |
| API URL routes | `apps/<app>/api_urls.py` → included under `/api/v1/<app>/` |
| Template URL routes | `apps/<app>/urls.py` → included at top level |
| Celery Tasks | `apps/<app>/tasks.py` |
| Business Logic | `apps/<app>/services/` (directory) or `apps/<app>/services.py` |
| Enums | `apps/core/enums.py` |
| Permissions | `apps/core/permissions.py` |
| Utilities | `apps/core/utils.py` |
| Admin | `apps/<app>/admin.py` |
| Templates | `templates/<app>/` |
| Static files | `static/css/`, `static/js/` |
| Config | `config/settings.py`, `config/urls.py`, `config/celery.py` |

---

## Key Models & Relationships

```
User (accounts)
  ├── has role: ADMIN | AP_PROCESSOR | REVIEWER | FINANCE_MANAGER | AUDITOR
  └── referenced by: Invoice.created_by, ReviewAssignment.assigned_to, etc.

Vendor (vendors) ──< VendorAlias

DocumentUpload (documents)
  └── Invoice (documents) ──< InvoiceLineItem
       ├── references: PurchaseOrder.po_number
       └── has: extraction_confidence, status (InvoiceStatus)

PurchaseOrder (documents) ──< PurchaseOrderLineItem
  └── GoodsReceiptNote (documents) ──< GRNLineItem

ExtractionResult (extraction) ── linked to DocumentUpload + Invoice

ReconciliationConfig (reconciliation)
ReconciliationRun ──< ReconciliationResult ──< ReconciliationResultLine
                                            ──< ReconciliationException
ReconciliationResult ── linked to Invoice + PurchaseOrder

AgentDefinition (agents)
AgentRun ──< AgentStep, AgentMessage, DecisionLog
AgentRun ── linked to ReconciliationResult
ToolCall (tools) ── linked to AgentRun + ToolDefinition

ReviewAssignment (reviews) ──< ReviewComment, ManualReviewAction
ReviewAssignment ── ReviewDecision (OneToOne)
ReviewAssignment ── linked to ReconciliationResult

ProcessingLog, AuditEvent, FileProcessingStatus (auditlog)
IntegrationConfig ──< IntegrationLog (integrations)
GeneratedReport (reports)
```

---

## Status Transitions

### Invoice Status Flow
```
UPLOADED → EXTRACTION_IN_PROGRESS → EXTRACTED → VALIDATED → READY_FOR_RECON → RECONCILED
                                  ↘ INVALID                                 ↘ FAILED
```

### Reconciliation Match Status
```
MATCHED | PARTIAL_MATCH | UNMATCHED | REQUIRES_REVIEW | ERROR
```

### Review Status Flow
```
PENDING → ASSIGNED → IN_REVIEW → APPROVED | REJECTED | REPROCESSED
```

### Agent Run Status
```
PENDING → RUNNING → COMPLETED | FAILED | TIMED_OUT
```

---

## Common Patterns for Prompts

### When adding a new model
1. Define in `apps/<app>/models.py`, inherit from `BaseModel`.
2. Add any new enums to `apps/core/enums.py`.
3. Create and run migration: `python manage.py makemigrations <app> && python manage.py migrate`.
4. Register in `apps/<app>/admin.py`.
5. Add serializer in `apps/<app>/serializers.py`.
6. Add ViewSet in `apps/<app>/views.py`.
7. Register routes in `apps/<app>/api_urls.py`.

### When adding a new service
1. Create in `apps/<app>/services/` directory.
2. Import and call from task or view — never directly from serializer.
3. Keep service stateless; accept model instances or IDs as arguments.

### When adding a new agent type
1. Add enum value to `AgentType` in `apps/core/enums.py`.
2. Create agent class in `apps/agents/services/`, extend `BaseAgent`.
3. Register in `AGENT_CLASS_REGISTRY`.
4. Add to `PolicyEngine` decision logic.
5. Create `AgentDefinition` record (via admin or migration).

### When adding a new tool
1. Create tool function in `apps/tools/registry/tools.py`.
2. Decorate with `@register_tool(name, description, input_schema, output_schema)`.
3. Add `ToolDefinition` record.
4. Reference in relevant agent's `allowed_tools`.

### When adding a new template view
1. Create view in `apps/<app>/template_views.py`.
2. Add URL in `apps/<app>/urls.py`.
3. Create template in `templates/<app>/`.
4. Extend `base.html` with `{% extends "base.html" %}`.

---

## What's Implemented vs. What's Next

### ✅ Fully implemented
- All models, migrations, enums, permissions, middleware
- Extraction pipeline (6 services + Celery task; adapter is a test stub)
- Reconciliation engine (11 services + Celery tasks)
- Agent orchestration (7 agents, policy engine, tool registry, LLM client)
- Review workflow (service + API + templates)
- Dashboard analytics (service + 6 API endpoints)
- DRF APIs (all ViewSets, serializers, routing)
- Bootstrap 5 templates (15 templates)
- Admin panel registration
- Audit logging models

### ⬜ Not yet implemented (next steps)
- **Tests**: pytest + factory-boy configured but no tests written. Need unit tests for services, integration tests for API endpoints, and factory classes for all models.
- **Real extraction adapter**: Replace stub with Azure Form Recognizer client.
- **ERP integrations**: Build actual connectors for PO/GRN ingestion (PO_API, GRN_API).
- **Report export services**: GeneratedReport model exists but CSV/Excel export logic not built.
- **Celery Beat schedules**: No periodic tasks configured yet.
- **Email notifications**: No notification system for review assignments.
- **Seed data / fixtures**: No sample POs, GRNs, or invoices for testing.
- **Docker / deployment**: No Dockerfile or docker-compose.
- **CI/CD pipeline**: No GitHub Actions or similar.
- **Frontend JS interactivity**: Templates are server-rendered; AJAX calls to API endpoints could enhance UX.

---

## Debugging Tips

- **Celery tasks not running?** Ensure Redis is running and Celery worker is started: `celery -A config worker -l info`
- **LLM calls failing?** Check `OPENAI_API_KEY` or `AZURE_OPENAI_*` env vars in settings.
- **Login redirect loop?** `LoginRequiredMiddleware` redirects all anonymous requests except /admin/, /accounts/, /api/.
- **Migration issues?** MySQL requires utf8mb4; check `DATABASES` charset setting.
- **Template not found?** Templates are in `templates/<app>/`; check `TEMPLATES` setting in settings.py.

---

## Important Files to Read First

| File | Why |
|---|---|
| `config/settings.py` | All configuration (DB, Celery, LLM, REST, Auth, tolerances) |
| `apps/core/enums.py` | All business enumerations |
| `apps/core/utils.py` | Normalization, parsing, tolerance utilities |
| `apps/core/permissions.py` | Role-based permission classes |
| `apps/documents/models.py` | Invoice, PO, GRN data models |
| `apps/reconciliation/services/runner_service.py` | Core 3-way matching orchestration |
| `apps/agents/services/orchestrator.py` | Agent pipeline orchestration |
| `apps/agents/services/base_agent.py` | Base agent with ReAct loop |
| `apps/extraction/tasks.py` | Extraction pipeline task |
| `apps/reviews/services.py` | Review workflow lifecycle |
