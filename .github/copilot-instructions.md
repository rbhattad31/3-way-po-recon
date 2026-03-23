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
- **Permissions** are RBAC-backed classes in `apps/core/permissions.py`; RBAC models in `apps/accounts/rbac_models.py`; template tags in `apps/core/templatetags/rbac_tags.py`.
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
- Tool-calling uses **OpenAI-compliant format**: `tool_calls` array on assistant messages, `tool_call_id` + `name` on tool response messages.
- Tools are registered in `apps/tools/registry/` via decorator pattern: `po_lookup`, `grn_lookup`, `vendor_search`, `invoice_details`, `exception_list`, `reconciliation_summary`. Each tool declares `required_permission` (e.g., `"purchase_orders.view"`).
- `AgentOrchestrator` is the entry point; `PolicyEngine` decides which agents to run based on match status + exception types.
- `PolicyEngine` also handles **auto-close logic**: `should_auto_close()` and `_within_auto_close_band()` check if PARTIAL_MATCH falls within wider auto-close thresholds (qty: 5%, price: 3%, amount: 3%).
- Agent pipeline is **wired to run automatically** after reconciliation for non-MATCHED results (sync via `start_reconciliation` view, async via `run_agent_pipeline_task`).
- **AgentGuardrailsService** (`apps/agents/services/guardrails_service.py`): Central RBAC enforcement for all agent operations — orchestration permission (`agents.orchestrate`), per-agent authorization (`agents.run_*` × 8), per-tool authorization (tool's `required_permission`), recommendation authorization (`recommendations.*` × 6), and post-policy authorization (auto-close, escalation).
- **SYSTEM_AGENT** identity: When no human user context is available (Celery async, system-triggered), `AgentGuardrailsService.resolve_actor()` returns a dedicated service account (`system-agent@internal`) with the `SYSTEM_AGENT` role (rank 100, `is_system_role=True`).
- Every agent run, message, tool call, and decision is persisted for auditability via `AgentTraceService`.
- `AgentRun` carries RBAC fields: `actor_primary_role`, `actor_roles_snapshot_json`, `permission_source`, `access_granted` — populated on every run.
- All guardrail decisions (grant/deny) are logged as `AuditEvent` records (9 event types: `GUARDRAIL_GRANTED/DENIED`, `TOOL_CALL_AUTHORIZED/DENIED`, `RECOMMENDATION_ACCEPTED/DENIED`, `AUTO_CLOSE_AUTHORIZED/DENIED`, `SYSTEM_AGENT_USED`).
- `RecommendationService` manages agent recommendations (`AgentRecommendation` model) with acceptance tracking; `mark_recommendation_accepted()` checks `authorize_recommendation()` before allowing accept/reject.
- `AgentFeedbackService` handles PO/GRN re-reconciliation when an agent recovers a missing document (atomic re-linking + re-matching).
- LLM client supports both OpenAI and Azure OpenAI (configurable via env vars).
- Agent definitions include `config_json` with `allowed_tools` per agent type.

### Observability & Tracing
- **TraceContext** (`apps/core/trace.py`): Distributed tracing with `trace_id`, `span_id`, `parent_span_id`, RBAC snapshot. Thread-local propagation. Celery header (de)serialization.
- **Structured Logging** (`apps/core/logging_utils.py`): `JSONLogFormatter` (production), `DevLogFormatter` (dev). `TraceLogger` auto-injects trace context. `redact_dict()` scrubs PII/financial data.
- **Metrics** (`apps/core/metrics.py`): Thread-safe in-process counters via `MetricsService`. Tracks RBAC, extraction, reconciliation, review, agent, case, task metrics.
- **Decorators** (`apps/core/decorators.py`): `@observed_service` (service methods), `@observed_action` (FBV views), `@observed_task` (Celery tasks). All create child spans, measure duration, write `ProcessingLog`/`AuditEvent`.
- **RequestTraceMiddleware** (`apps/core/middleware.py`): Creates root `TraceContext` per request, enriches with RBAC, sets `X-Trace-ID`/`X-Request-ID` headers.
- When adding new services or views, decorate entry-point methods with the appropriate `@observed_*` decorator.

### Governance & Audit
- `AuditEvent` model has 20+ fields: trace IDs, RBAC snapshot (actor_primary_role, actor_email, actor_roles_snapshot), permission tracking (permission_checked, permission_source, access_granted), cross-references (invoice_id, case_id, reconciliation_result_id), status_before/after, duration_ms, error_code.
- `AuditService` (`apps/auditlog/services.py`) has query helpers: `fetch_case_history()`, `fetch_access_history()`, `fetch_permission_denials()`, `fetch_rbac_activity()`.
- `CaseTimelineService` (`apps/auditlog/timeline_service.py`) builds a unified chronological timeline per invoice with 8 event categories: audit, mode_resolution, agent_run, tool_call, decision, recommendation, review/review_action/review_decision, case/stage. Entries include RBAC badges, status changes, field corrections, duration tracking.
- `AgentTraceService` in `apps/agents/services/agent_trace_service.py` is the single entry point for recording all agent activity (runs, steps, tool calls, decisions).
- Governance API: 9 endpoints at `/api/v1/governance/` (audit-history, agent-trace, recommendations, timeline, access-history, stage-timeline, permission-denials, rbac-activity, agent-performance).
- Governance views (`apps/auditlog/template_views.py`): `audit_event_list` (filterable log with RBAC columns, role/trace_id/denied-only filters) and `invoice_governance` (full dashboard with access history tab, RBAC badges in timeline; ADMIN/AUDITOR see full trace).
- Templates are in `templates/governance/`.

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
| RBAC Models | `apps/accounts/rbac_models.py` |
| RBAC Services | `apps/accounts/rbac_services.py` |
| RBAC Template Tags | `apps/core/templatetags/rbac_tags.py` |
| Permissions | `apps/core/permissions.py` |
| Agent Guardrails | `apps/agents/services/guardrails_service.py` |
| Observability | `apps/core/trace.py`, `apps/core/logging_utils.py`, `apps/core/metrics.py`, `apps/core/decorators.py` |
| Utilities | `apps/core/utils.py` |
| Seed Commands | `apps/core/management/commands/seed_config.py`, `seed_prompts.py`; `apps/cases/management/commands/seed_ap_data.py` |
| Seed Helpers | `apps/cases/management/commands/seed_helpers/` (constants, master_data, transactional_data, case_builder, agent_review_data, observability_data, bulk_generator) |
| Admin | `apps/<app>/admin.py` |
| Templates | `templates/<app>/` (also `templates/governance/` for audit/governance views, `templates/vendors/` for vendor UI) |
| Static files | `static/css/`, `static/js/` |
| Config | `config/settings.py`, `config/urls.py`, `config/celery.py` |

---

## Key Models & Relationships

```
User (accounts)
  ├── has legacy role field: ADMIN | AP_PROCESSOR | REVIEWER | FINANCE_MANAGER | AUDITOR
  ├── ──< UserRole ──> Role (RBAC multi-role with expiry)
  ├── ──< UserPermissionOverride ──> Permission (ALLOW/DENY per-user)
  └── referenced by: Invoice.created_by, ReviewAssignment.assigned_to, etc.

Role (accounts) ──< RolePermission ──> Permission (accounts)
  └── has: code, name, rank, is_system_role, is_active

Permission (accounts)
  └── has: code (e.g. invoices.view), module, action, is_active

Vendor (vendors) ──< VendorAlias

DocumentUpload (documents)
  └── Invoice (documents) ──< InvoiceLineItem
       ├── references: PurchaseOrder.po_number
       └── has: extraction_confidence, status (InvoiceStatus)

PurchaseOrder (documents) ──< PurchaseOrderLineItem (item_category, is_service_item, is_stock_item)
  └── GoodsReceiptNote (documents) ──< GRNLineItem

ExtractionResult (extraction) ── linked to DocumentUpload + Invoice\nExtractionApproval (extraction) ── OneToOne Invoice, FK ExtractionResult\n  └──< ExtractionFieldCorrection (per-field correction audit trail)

ReconciliationConfig (reconciliation) — tiered tolerance: strict + auto-close bands; mode resolver settings
ReconciliationPolicy (reconciliation) — vendor/category/location/business-unit → mode mapping
ReconciliationRun ──< ReconciliationResult ──< ReconciliationResultLine
                                            ──< ReconciliationException
ReconciliationResult ── linked to Invoice + PurchaseOrder (reconciliation_mode, mode_resolved_by)

AgentDefinition (agents)
AgentRun ──< AgentStep, AgentMessage, DecisionLog
AgentRun ──< AgentRecommendation (with acceptance tracking)
AgentRun ──< AgentEscalation (severity-based, suggested assignee)
AgentRun ── linked to ReconciliationResult
AgentRun ── RBAC fields: actor_primary_role, actor_roles_snapshot_json, permission_source, access_granted
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
UPLOADED → EXTRACTION_IN_PROGRESS → EXTRACTED → VALIDATED → PENDING_APPROVAL → READY_FOR_RECON → RECONCILED
                                  ↘ INVALID                ↗ (auto-approve)                    ↘ FAILED
                                                           ↘ INVALID (rejected)
```
- **PENDING_APPROVAL**: Human-in-the-loop gate. All valid extractions require human approval before reconciliation.
- Auto-approval: When `EXTRACTION_AUTO_APPROVE_ENABLED=true` and confidence ≥ `EXTRACTION_AUTO_APPROVE_THRESHOLD`, the system auto-approves and skips human review.
- Models: `ExtractionApproval` (one-to-one with Invoice), `ExtractionFieldCorrection` (tracks every field correction for analytics).
- Service: `ExtractionApprovalService` in `apps/extraction/services/approval_service.py`.
- Analytics: `get_approval_analytics()` returns touchless rate, most-corrected fields, approval breakdown.

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
PENDING → RUNNING → COMPLETED | FAILED | SKIPPED
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
6. Add `agents.run_<type>` permission to `seed_rbac.py` PERMISSIONS list.
7. Map permission to appropriate roles in `ROLE_MATRIX` and to `SYSTEM_AGENT`.
8. Add entry to `AGENT_PERMISSIONS` dict in `apps/agents/services/guardrails_service.py`.

### When modifying the quotation extraction pipeline
1. OCR text limit is 60K chars (in `QuotationDocumentPrefillService._extract_quotation_data()` and `QuotationExtractionAgent.extract()`).
2. LLM extraction uses `max_tokens=8192` for quotation extraction responses.
3. Field synonym mapping is in `AttributeMappingService` (`_QUOTATION_FIELD_SYNONYMS`).
4. Line items are NOT persisted to DB during extraction — only stored as JSON in `prefill_payload_json`. Persistence happens during user confirmation via `PrefillReviewService.confirm_quotation_prefill()`.
5. Key files: `apps/procurement/services/prefill/quotation_prefill_service.py`, `apps/procurement/agents/quotation_extraction_agent.py`, `apps/procurement/services/prefill/attribute_mapping_service.py`.

### When adding a new tool
1. Create tool class in `apps/tools/registry/tools.py` extending `BaseTool`.
2. Decorate with `@register_tool`.
3. Set `required_permission` (e.g., `"purchase_orders.view"`) — enforced by `AgentGuardrailsService.authorize_tool()`.
4. Implement `execute()` method.
5. Add `ToolDefinition` record.
6. Reference in relevant agent's `allowed_tools`.

### When adding a new permission
1. Add `Permission` record via `seed_rbac` command or Django admin.
2. Use convention: `{module}.{action}` (e.g. `reports.export`).
3. Assign to roles via `RolePermission` or the role-permission matrix UI.
4. Use in views: `HasPermissionCode("reports.export")` (DRF) or `required_permission = "reports.export"` (CBV mixin).
5. Use in templates: `{% has_permission "reports.export" as can_export %}`.

### When adding a new template view
1. Create view in `apps/<app>/template_views.py`.
2. Add URL in `apps/<app>/urls.py`.
3. Create template in `templates/<app>/`.
4. Extend `base.html` with `{% extends "base.html" %}`.
5. Add permission: use `PermissionRequiredMixin` with `required_permission`.

---

## What's Implemented vs. What's Next

### ✅ Fully implemented
- All models, migrations, enums (25 enum classes incl. `ReconciliationMode`, `ReconciliationModeApplicability`), permissions, middleware
- **Enterprise RBAC**: Role, Permission, RolePermission, UserRole, UserPermissionOverride models (`apps/accounts/rbac_models.py`)
- **RBAC Permission Engine**: `HasPermissionCode`, `HasAnyPermission`, `HasRole` (DRF); `PermissionRequiredMixin`, `AnyPermissionRequiredMixin`, `RoleRequiredMixin` (CBV); `@permission_required_code`, `@role_required` (FBV)
- **RBAC Middleware**: `RBACMiddleware` pre-loads permission cache per request; `rbac_context` processor injects `user_permissions`, `user_role_codes`, `is_admin`
- **RBAC Template Tags**: `{% has_permission %}`, `{% has_role %}`, `{% has_any_permission %}`, `{% if_can %}` block tag
- **RBAC Audit**: `RBACEventService` logs 9 event types (ROLE_ASSIGNED, ROLE_REMOVED, ROLE_PERMISSION_CHANGED, USER_PERMISSION_OVERRIDE, USER_ACTIVATED, USER_DEACTIVATED, ROLE_CREATED, ROLE_UPDATED, PRIMARY_ROLE_CHANGED)
- **RBAC Admin Console**: 8 Bootstrap 5 UI screens — User list/create/detail, Role list/create/detail, Permission catalog, Role-Permission matrix
- **RBAC API**: `/api/v1/accounts/` — UserViewSet (CRUD + roles/overrides), RoleViewSet (CRUD + clone), PermissionViewSet, RolePermissionMatrixView
- **RBAC Seed**: `python manage.py seed_rbac --sync-users` — 6 roles (incl. SYSTEM_AGENT), 40 permissions, full matrix, legacy user sync
- Extraction pipeline (two-agent architecture: InvoiceExtractionAgent always + InvoiceUnderstandingAgent for low confidence; 8 service classes in 7 files + Celery task; Azure Document Intelligence OCR + Azure OpenAI GPT-4o)
- Extraction approval gate: `ExtractionApproval` + `ExtractionFieldCorrection` models; `ExtractionApprovalService` (approve/reject/auto-approve); touchless-rate analytics; approval queue UI; configurable auto-approval (`EXTRACTION_AUTO_APPROVE_ENABLED`, `EXTRACTION_AUTO_APPROVE_THRESHOLD`)
- Reconciliation engine (14 services + Celery tasks); configurable 2-way/3-way matching with mode resolver (policy → heuristic → default); tiered tolerance (strict: 2%/1%/1%, auto-close: 5%/3%/3%)
- `ReconciliationModeResolver` — 3-tier mode cascade: (1) ReconciliationPolicy lookup, (2) heuristic (item flags + service keywords), (3) config default
- `TwoWayMatchService` (Invoice vs PO only), `ThreeWayMatchService` (Invoice vs PO vs GRN), `ReconciliationExecutionRouter`
- `ReconciliationPolicy` model: vendor, item_category, location_code, business_unit, is_service_invoice, is_stock_invoice, priority-ordered matching
- Mode-aware classification, exception building (applies_to_mode tagging), result persistence (mode metadata + confidence weights)
- Agent orchestration (8 agents, policy engine with auto-close logic + mode-aware GRN suppression, tool registry, LLM client, decision log service)
- Agent RBAC guardrails: `AgentGuardrailsService` — central RBAC enforcement (orchestration, per-agent, per-tool, recommendation, post-policy authorization); SYSTEM_AGENT identity for autonomous runs; 9 guardrail audit event types; AgentRun RBAC fields populated on every run
- Mode-aware agents: `AgentContext.reconciliation_mode`, `_mode_context()` helper on all agent types, PolicyEngine suppresses GRN_RETRIEVAL in 2-way
- Agent feedback loop: `AgentFeedbackService` re-reconciles when PO/GRN agent recovers missing document (atomic)
- Agent recommendation service: `AgentRecommendation` model with acceptance tracking + `AgentEscalation` model
- Agent trace service: unified governance tracing (`AgentTraceService`)
- Agent pipeline wired to run automatically after reconciliation for non-MATCHED results (sync + async paths)
- 6 class-based tools (po_lookup, grn_lookup, vendor_search, invoice_details, exception_list, reconciliation_summary)
- OpenAI-compliant tool-calling format (tool_calls on assistant messages, tool_call_id on tool responses)
- Tool call logging: `ToolCallLogger` persists every invocation with status, duration, input/output
- Review workflow (service + API + templates) with auto-creation of ReviewAssignment for REQUIRES_REVIEW results
- Review UI: "Awaiting Assignment" panel + bulk assignment creation
- Reconciliation UI: "Start Reconciliation" panel with checkbox invoice selection (triggers matching + agent pipeline)
- Case console: deep-dive investigation view per reconciliation result + CSV export
- Reconciliation settings viewer (tolerance configuration)
- Dashboard analytics (service + 7 API endpoints incl. mode-breakdown)
- DRF APIs (all ViewSets, serializers, routing) + Governance API (`/api/v1/governance/`) + Reconciliation Policies API (`/api/v1/reconciliation/policies/`)
- Bootstrap 5 templates (34 templates incl. partials, governance views, RBAC admin console, vendor pages)
- Vendor UI: list page (KPIs, country/currency/search filters, PO/invoice/alias counts) + detail page (aliases, recent POs/invoices/GRNs)
- RBAC permissions for document pages: `vendors.view`, `purchase_orders.view`, `grns.view` — all roles granted, AP_PROCESSOR scoped to own invoices
- RBAC data scoping: AP_PROCESSOR sees only POs/GRNs/Vendors linked to their own uploaded invoices (via `_scope_pos_for_user`, `_scope_grns_for_user`, `_scope_vendors_for_user`)
- Sidebar navigation gated by RBAC `{% has_permission %}` tags for POs, GRNs, Vendors, Governance, Admin Console
- Admin panel registration
- Audit logging & governance: ProcessingLog, AuditEvent (~38 event types, 20+ RBAC/trace fields), FileProcessingStatus, CaseTimelineService (8 event categories with RBAC badges, status changes, field corrections, duration tracking), governance views (audit event list with RBAC columns + invoice governance dashboard with access history tab)
- Observability infrastructure: TraceContext (distributed tracing), structured JSON logging with PII redaction, in-process MetricsService, RequestTraceMiddleware
- Observability decorators: `@observed_service`, `@observed_action`, `@observed_task` — 10 instrumented service/view/task entry points
- Enhanced governance API: 9 endpoints (audit-history, agent-trace, recommendations, timeline, access-history, stage-timeline, permission-denials, rbac-activity, agent-performance)
- Seed data: `seed_config` (6 users, 7 agent defs, 6 tool defs, recon config, 7 policies), `seed_rbac` (6 roles incl. SYSTEM_AGENT, 40 permissions, matrix, user sync), `seed_prompts` (12 prompt templates), `seed_ap_data` (30 deterministic scenarios: TWO_WAY/THREE_WAY/NON_PO + cross-cutting, with 6-stage pipeline: users → vendors → transactional → cases/recon → agent/review → observability)
- Seed observability data (stage 6 of `seed_ap_data`): AgentStep (~280), AgentMessage (~568), ToolCall (~137), DecisionLog (~78), AgentEscalation (~2), ProcessingLog (~193), ManualReviewAction (~9); enriches AgentRun with trace_id/tokens/cost and AuditEvent with RBAC/cross-refs
- Seed helpers architecture in `apps/cases/management/commands/seed_helpers/`: constants.py, master_data.py, transactional_data.py, case_builder.py, agent_review_data.py, observability_data.py, bulk_generator.py
- Windows dev mode: `CELERY_TASK_ALWAYS_EAGER=True` (default) for synchronous execution without Redis
- Root URL (`/`) redirects to `/dashboard/`; `LOGIN_URL = /accounts/login/`

### ⬜ Not yet implemented (next steps)
- **Tests**: pytest + factory-boy configured but no tests written. Need unit tests for services, integration tests for API endpoints, and factory classes for all models.
- **Extraction refinement**: Tune LLM extraction prompts, add support for multi-page invoices, handle edge-case layouts.
- **ERP integrations**: Build actual connectors for PO/GRN ingestion (PO_API, GRN_API).
- **Report export services**: GeneratedReport model exists but full CSV/Excel export logic not built (CSV export exists for case console only).
- **Celery Beat schedules**: No periodic tasks configured yet.
- **Email notifications**: No notification system for review assignments.
- **Docker / deployment**: No Dockerfile or docker-compose.
- **CI/CD pipeline**: No GitHub Actions or similar.
- **Frontend JS interactivity**: Templates are server-rendered; AJAX calls to API endpoints could enhance UX.

---

## Debugging Tips

- **Celery tasks not running?** If on Windows without Redis, ensure `CELERY_TASK_ALWAYS_EAGER=True` (default in settings.py) — tasks run synchronously. For async mode, set `CELERY_TASK_ALWAYS_EAGER=False`, start Redis, and run: `celery -A config worker -l info`
- **LLM calls failing?** Check `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` env vars in settings.
- **Agent 400 errors from OpenAI?** Ensure tool-calling messages follow the format: assistant messages include `tool_calls` array, tool response messages include `tool_call_id` and `name`.
- **Extraction failing?** Check `AZURE_DI_ENDPOINT` and `AZURE_DI_KEY` env vars for Azure Document Intelligence.
- **Login redirect loop?** `LoginRequiredMiddleware` redirects all anonymous requests except /admin/, /accounts/, /api/. `LOGIN_URL` is `/accounts/login/`.
- **Migration issues?** MySQL requires utf8mb4; check `DATABASES` charset setting.
- **Template not found?** Templates are in `templates/<app>/`; check `TEMPLATES` setting in settings.py.
- **Confidence showing 1%?** `extraction_confidence` is stored as 0.0–1.0 float; templates use `{% widthratio %}` to display as percentage.

---

## Important Files to Read First

| File | Why |
|---|---|
| `config/settings.py` | All configuration (DB, Celery, LLM, REST, Auth, tolerances) |
| `apps/core/enums.py` | All business enumerations |
| `apps/core/utils.py` | Normalization, parsing, tolerance utilities |
| `apps/core/permissions.py` | RBAC-backed permission classes, CBV mixins, FBV decorators |
| `apps/documents/models.py` | Invoice, PO, GRN data models |
| `apps/reconciliation/template_views.py` | Start reconciliation view + agent pipeline wiring |
| `apps/reconciliation/services/runner_service.py` | Core 3-way matching orchestration + auto-ReviewAssignment |
| `apps/reconciliation/services/tolerance_engine.py` | Tiered tolerance comparison (strict + auto-close bands) |
| `apps/reconciliation/services/agent_feedback_service.py` | Agent PO/GRN re-reconciliation loop |
| `apps/agents/services/orchestrator.py` | Agent pipeline orchestration |
| `apps/agents/services/base_agent.py` | Base agent with ReAct loop |
| `apps/agents/services/agent_classes.py` | All 8 agent implementations |
| `apps/tools/registry/tools.py` | All 6 tool classes |
| `apps/tools/registry/base.py` | BaseTool, ToolRegistry, @register_tool |
| `apps/core/trace.py` | TraceContext for distributed tracing |
| `apps/core/logging_utils.py` | Structured JSON logging, PII redaction |
| `apps/core/metrics.py` | In-process metrics collection |
| `apps/core/decorators.py` | `@observed_service`, `@observed_action`, `@observed_task` decorators |
| `apps/extraction/tasks.py` | Extraction pipeline task |
| `apps/extraction/services/approval_service.py` | Extraction approval gate (approve/reject/auto-approve, field correction tracking, touchless analytics) |
| `apps/reviews/services.py` | Review workflow lifecycle |
| `apps/agents/services/recommendation_service.py` | Agent recommendation lifecycle (create, query, accept) |
| `apps/agents/services/agent_trace_service.py` | Unified agent governance tracing |
| `apps/agents/services/policy_engine.py` | Agent plan + auto-close band logic |
| `apps/agents/services/guardrails_service.py` | Central RBAC enforcement for all agent operations |
| `apps/auditlog/timeline_service.py` | Unified case timeline service |
| `apps/auditlog/template_views.py` | Governance views (audit log + invoice governance) |
| `apps/accounts/rbac_models.py` | RBAC data models (Role, Permission, UserRole, etc.) |
| `apps/accounts/rbac_services.py` | RBAC audit service |
| `apps/accounts/template_views.py` | Admin console UI views (user/role/perm management) |
| `apps/vendors/template_views.py` | Vendor list/detail views with RBAC + AP_PROCESSOR scoping |
| `apps/procurement/services/prefill/quotation_prefill_service.py` | Quotation OCR → LLM extraction pipeline (60K char limit) |
| `apps/procurement/agents/quotation_extraction_agent.py` | LLM-based quotation data extraction agent |
| `apps/procurement/services/prefill/attribute_mapping_service.py` | Field synonym mapping for extracted quotation/request fields |
| `apps/core/templatetags/rbac_tags.py` | RBAC template tags for permission-aware rendering |
