# Copilot Instructions -- 3-Way PO Reconciliation Platform

## Project Identity

Django 5.0 enterprise AP finance application for 3-way Purchase Order reconciliation
(Invoice vs PO vs GRN). MySQL (utf8mb4), Celery 5.6+Redis, Azure OpenAI (GPT-4o),
Azure Document Intelligence, Bootstrap 5. 23 Django apps under `apps/`.
`apps/reviews` is a migrations-only stub (merged into `apps/cases`).

**Key docs**:
- `docs/PROJECT.md` -- full architecture, models, data flow
- `docs/current_system_review/` -- code-first system analysis (19 documents, April 2026)
- `docs/AGENT_ARCHITECTURE_COMBINED.md` -- agentic layer, policy engine, guardrails
- `docs/MULTI_TENANT.md` -- tenant isolation patterns
- `docs/LANGFUSE_OBSERVABILITY.md` -- observability patterns, trace/score conventions

**Copilot customization files** (read these when working on specific areas):
- `.github/instructions/django-conventions.instructions.md` -- Python/model rules
- `.github/instructions/agent-system.instructions.md` -- agent architecture rules
- `.github/instructions/erp-integration.instructions.md` -- ERP connector rules
- `.github/instructions/rbac.instructions.md` -- RBAC enforcement rules
- `.github/instructions/langfuse-tracing.instructions.md` -- Langfuse observability rules
- `.github/instructions/multi-tenant.instructions.md` -- tenant isolation rules
- `.github/instructions/posting-pipeline.instructions.md` -- posting pipeline rules

**Available slash-command prompts** (type `/` in chat):
- `/scaffold-model` -- scaffold a new Django model end-to-end
- `/add-agent` -- add a new LLM or deterministic agent
- `/add-erp-connector` -- add a new ERP connector
- `/write-tests` -- write tests for a service, view, or agent
- `/add-langfuse-tracing` -- instrument a pipeline with Langfuse
- `/add-permission` -- add a new RBAC permission
- `/debug-pipeline` -- diagnose a failing pipeline

---

## Non-Negotiable Engineering Rules

### ASCII Only

Do not use Unicode arrows, fancy quotes, em/en dashes, ellipsis, or any non-ASCII character
in Python source, string literals, comments, docstrings, or LLM-generated text persisted to DB.
Use `->`, `--`, `...`, straight quotes. Apply `_sanitise_text()` before `.save()` on any
agent-generated content written to `AgentRun.summarized_reasoning`,
`ReconciliationResult.summary`, `ReviewAssignment.reviewer_summary`, `DecisionLog.rationale`.

### Soft Delete

Never hard-delete business entities. Use `SoftDeleteMixin` (`is_active` flag).

### Enum Placement

All enums go in `apps/core/enums.py`. Never inline string choices on model fields.
Exception: ERP connector enums live in `apps/erp_integration/enums.py`.

### Constants and Utilities

Constants in `apps/core/constants.py`. Shared utilities in `apps/core/utils.py`.

---

## Architecture Rules

### Service-Layer Pattern

- Business logic goes in **service classes** under `apps/<app>/services/`, never in views or serializers.
- Services are stateless. Accept model instances or IDs as arguments.
- Views/tasks call services; services call the ORM.
- Keep views thin: request parsing, permission checking, response formatting only.

### Model Inheritance

All models inherit from `apps.core.models.BaseModel` (includes `TimestampMixin` + `AuditMixin`),
unless they are lightweight join/log tables that use `TimestampMixin` only.

### API Design

- All APIs under `/api/v1/` using Django REST Framework.
- `ModelViewSet` or `ReadOnlyModelViewSet` with `permission_classes`.
- Default pagination: 25 per page (`PageNumberPagination`).
- Filtering: `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`.
- Serializers in `serializers.py` per app. Separate List/Detail serializers when needed.
- API routes in `api_urls.py`; template routes in `urls.py`.

### Celery Tasks

- Tasks in `tasks.py` per app. Use `@shared_task(bind=True)` with `max_retries` and `default_retry_delay`.
- Tasks call service classes. Never put business logic in task functions.
- Use `acks_late=True` for important tasks. JSON serialization.
- Windows dev mode: `CELERY_TASK_ALWAYS_EAGER=True` (default) for synchronous execution without Redis.

---

## Multi-Tenant Isolation

Shared-database row-level isolation via `CompanyProfile` as tenant entity.
Every business model has a `tenant` FK to `CompanyProfile`.

- `TenantMiddleware` sets `request.tenant` from `user.company`.
- `TenantQuerysetMixin` on all ViewSets/CBVs.
- `require_tenant()` decorator for FBVs.
- `scoped_queryset()` for service-layer queries.
- `BaseTool._scoped()` for agent tools.
- Platform admins (`is_platform_admin=True`) bypass tenant scoping.
- Celery tasks accept `tenant_id` argument.

Reference: `apps/core/tenant_utils.py`, `docs/MULTI_TENANT.md`.

---

## RBAC

Custom User model uses email login. `AUTH_USER_MODEL = "accounts.User"`.
`User.company` FK to `CompanyProfile`. `User.is_platform_admin` for cross-tenant access.

- RBAC models: `apps/accounts/rbac_models.py` (Role, Permission, RolePermission, UserRole, UserPermissionOverride).
- Permission classes: `apps/core/permissions.py` (`HasPermissionCode`, `HasAnyPermission`, `HasRole` for DRF; `PermissionRequiredMixin` for CBV; `@permission_required_code` for FBV).
- Template tags: `apps/core/templatetags/rbac_tags.py` (`{% has_permission %}`, `{% has_role %}`, `{% if_can %}`).
- Permission convention: `{module}.{action}` (e.g. `invoices.view`, `agents.run_reconciliation`).
- 10 system roles (incl. SUPER_ADMIN rank 1, SYSTEM_AGENT rank 100). 65+ permissions across 18 modules.
- `UserRole.scope_json`: per-assignment scope restrictions (`allowed_business_units`, `allowed_vendor_ids`). Null means unrestricted. ADMIN and SYSTEM_AGENT bypass.
- Permission precedence: ADMIN bypass -> user DENY override -> user ALLOW override -> role permissions.

---

## Agent System Summary

- 14 agents: 9 LLM (extend `BaseAgent`, ReAct loop, max 6 iterations; SupervisorAgent uses 15 rounds) + 5 deterministic (extend `DeterministicSystemAgent`).
- `AgentOrchestrator` -> `ReasoningPlanner` (LLM) -> fallback `PolicyEngine` (deterministic).
- `SupervisorAgent`: full AP lifecycle orchestrator with 5-phase skill-based composition, 30+ tools, smart query routing (CASE_ANALYSIS / AP_INSIGHTS / HYBRID).
- `AgentGuardrailsService`: central RBAC enforcement (orchestration, per-agent, per-tool, recommendation, data-scope authorization).
- `SYSTEM_AGENT` identity (`system-agent@internal`) for Celery/system-triggered runs.
- Tools registered in `apps/tools/registry/` via `@register_tool`. Each declares `required_permission`.
- OpenAI-compliant tool-calling format.
- `AgentOutputSchema` (Pydantic v2) validates all agent JSON output.
- Every run, step, tool call, decision persisted via `AgentTraceService`.
- Prompt resolution chain: in-process cache -> Langfuse (label="production") -> DB (PromptTemplate) -> hardcoded fallback.

---

## Observability and Audit

### Internal Tracing

- `TraceContext` (`apps/core/trace.py`): distributed tracing with `trace_id`, `span_id`, RBAC snapshot.
- Decorators: `@observed_service`, `@observed_action`, `@observed_task` on all entry-point methods.
- `RequestTraceMiddleware`: root `TraceContext` per request.

### Langfuse

- All calls fail-silent. Import from `apps.core.langfuse_client`.
- Always pass `span=` to `score_trace()` / `score_trace_safe()` (OTel trace_id extraction).
- Guard every Langfuse call in `try/except Exception: pass`. Never let tracing errors propagate.
- Score keys centralized in `apps/core/evaluation_constants.py`.
- See `docs/LANGFUSE_INTEGRATION.md` for trace ID conventions, score conventions, and code patterns.

### Audit

- `AuditEvent` model: 20+ fields, RBAC snapshot, trace IDs, cross-references. 38+ event types.
- `ProcessingLog`: operational observability (durations, retries, failures). Not for compliance.
- `DecisionLog`: every key decision (agent, deterministic, policy, human) with full rationale.
- `CaseTimelineService`: unified chronological timeline per invoice (8 event categories).

---

## File Placement

| What | Where |
|---|---|
| Models | `apps/<app>/models.py` |
| Serializers | `apps/<app>/serializers.py` |
| API Views | `apps/<app>/views.py` |
| Template Views | `apps/<app>/template_views.py` |
| API URLs | `apps/<app>/api_urls.py` (under `/api/v1/<app>/`) |
| Template URLs | `apps/<app>/urls.py` (top level) |
| Tasks | `apps/<app>/tasks.py` |
| Services | `apps/<app>/services/` directory |
| Enums | `apps/core/enums.py` |
| Templates | `templates/<app>/` |
| Static files | `static/css/`, `static/js/` |
| Config | `config/settings.py`, `config/urls.py`, `config/celery.py` |

Specialized locations:

| What | Where |
|---|---|
| RBAC models | `apps/accounts/rbac_models.py` |
| Permissions | `apps/core/permissions.py` |
| Agent guardrails | `apps/agents/services/guardrails_service.py` |
| LLM agent classes | `apps/agents/services/agent_classes.py` |
| System agent classes | `apps/agents/services/system_agent_classes.py` |
| Supervisor agent | `apps/agents/services/supervisor_agent.py` |
| Supervisor skills | `apps/agents/skills/` |
| Tool classes (base) | `apps/tools/registry/tools.py` |
| Supervisor tools | `apps/tools/registry/supervisor_tools.py` |
| AP insights tools | `apps/tools/registry/ap_insights_tools.py` |
| ERP connectors | `apps/erp_integration/services/connectors/` |
| ERP resolvers | `apps/erp_integration/services/resolution/` |
| ERP DB fallbacks | `apps/erp_integration/services/db_fallback/` |
| Posting pipeline | `apps/posting_core/services/` |
| Posting business | `apps/posting/services/` |
| Eval/Learning | `apps/core_eval/services/` |
| Observability | `apps/core/trace.py`, `logging_utils.py`, `metrics.py`, `decorators.py` |
| Prompt registry | `apps/core/prompt_registry.py` |
| Evaluation constants | `apps/core/evaluation_constants.py` |

---

## Code Generation Expectations

- Python 3.8+. Type hints on public functions.
- New services: decorate entry points with `@observed_service`.
- New views: decorate with `@observed_action` (FBV) or ensure `@observed_service` on called service.
- New tasks: decorate with `@observed_task`. Add Langfuse root trace and scores.
- New models: inherit `BaseModel`, add `tenant` FK, add admin registration.
- New API endpoints: enforce RBAC via `permission_classes`, apply `TenantQuerysetMixin`.
- New templates: extend `base.html`, gate with `{% has_permission %}`.
- Tests: tenant isolation, RBAC denial, status transitions.

---

## Key References

| Document | Content |
|---|---|
| `PROJECT.md` | Full architecture, models, data flow |
| `docs/AGENT_ARCHITECTURE.md` | Agent layer, policy engine, guardrails, tool registry |
| `docs/current_system_review/` | Latest code-first system analysis (19 documents) |
| `docs/MULTI_TENANT.md` | Tenant isolation patterns |
| `docs/LANGFUSE_INTEGRATION.md` | Observability patterns, trace/score conventions |
| `docs/POSTING_AGENT.md` | Posting pipeline details |
| `docs/ERP_INTEGRATION.md` | ERP connector and resolution architecture |
| `docs/EVAL_LEARNING.md` | Evaluation and learning framework |
| `docs/RECON_AGENT.md` | Reconciliation + agent pipeline reference |
| `docs/EXTRACTION_AGENT.md` | Extraction pipeline reference |
| `docs/REASONING_PLANNER.md` | LLM planner architecture |
| `docs/PROCUREMENT.md` | Procurement module reference |
# Copilot Instructions — 3-Way PO Reconciliation Platform

## Project Context

This is a Django 5.0 enterprise application for **3-way Purchase Order reconciliation** (Invoice vs PO vs GRN). It uses MySQL (utf8mb4), Celery 5.6+Redis, Azure OpenAI (GPT-4o), Azure Document Intelligence, and Bootstrap 5 templates. The codebase lives under `apps/` with **23 Django apps** (including: `posting`, `posting_core`, `erp_integration`, `extraction_core`, `procurement`, `core_eval`, `benchmarking`, `extraction_configs`). Note: `apps/reviews` was merged into `apps/cases` -- the `reviews` entry in INSTALLED_APPS is a migrations-only stub.

The platform uses **shared-database multi-tenancy** with row-level isolation via `CompanyProfile` as the tenant entity. Every business model has a `tenant` FK to `CompanyProfile`. See [MULTI_TENANT.md](../docs/MULTI_TENANT.md) for full details.

**Read [PROJECT.md](../PROJECT.md) for full architecture, models, services, and data flow.**

---

## Code Conventions

### Django & Python
- **No special characters in generated code** — do not use Unicode arrows (→ ► ↘), fancy quotes (" " ' '), em/en dashes (— –), ellipsis (…), or any non-ASCII characters in Python source files, string literals, comments, or docstrings unless they are explicitly required by a data value (e.g. a test fixture). Use plain ASCII equivalents: `->` for arrows, `--` for dashes, `...` for ellipsis, straight quotes for strings.
- **Python 3.8+**, type hints encouraged on public functions.
- **All models** inherit from `apps.core.models.BaseModel` (which includes `TimestampMixin` + `AuditMixin`), unless they are lightweight join/log tables that use `TimestampMixin` only.
- **Soft delete** via `SoftDeleteMixin` (is_active flag) — never hard-delete business entities.
- **Enums** live in `apps/core/enums.py` — always add new enums there, never inline string choices.
- **Constants** live in `apps/core/constants.py`.
- **Utility functions** (normalization, parsing, tolerance checks) live in `apps/core/utils.py`.
- **Permissions** are RBAC-backed classes in `apps/core/permissions.py`; RBAC models in `apps/accounts/rbac_models.py`; template tags in `apps/core/templatetags/rbac_tags.py`.
- Custom **User model** uses email login (not username): `AUTH_USER_MODEL = "accounts.User"`. `User.company` FK to `CompanyProfile` (tenant). `User.is_platform_admin` flag for cross-tenant platform admin access.
- **Multi-Tenant Isolation**: `TenantMiddleware` sets `request.tenant` from `user.company`; `TenantQuerysetMixin` on all ViewSets/CBVs; `require_tenant()` for FBVs; `scoped_queryset()` for services; `BaseTool._scoped()` for agent tools. Platform admins (`is_platform_admin=True`) bypass tenant scoping. See `apps/core/tenant_utils.py`.
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

### ERP Integration Layer
- **`apps/erp_integration/`** is the shared ERP connectivity layer used by both the posting pipeline and agent tools.
- **Resolution chain**: cache → ERP API connector → DB fallback. All lookups go through `BaseResolver` subclasses in `apps/erp_integration/services/resolution/`.
- **Connectors** (`apps/erp_integration/services/connectors/`): `BaseERPConnector` → `CustomERPConnector`, `DynamicsConnector`, `ZohoConnector`, `SalesforceConnector`. New connectors must implement capability flags (`supports_vendor_lookup()` etc.) and the relevant lookup/submission methods.
- **`ConnectorFactory`**: `get_default_connector()` returns the active default `ERPConnection` record as a connector instance; `get_connector_by_name(name)` retrieves by name.
- **DB fallback adapters** (`apps/erp_integration/services/db_fallback/`): one per resolution type — vendor, item, tax, cost center, PO, GRN, duplicate invoice. Vendor/item/tax/cost-center adapters fall back to local `posting_core` reference tables. **PO fallback is two-tier**: Tier 1 queries `documents.PurchaseOrder` (confidence 1.0, full transactional record); Tier 2 queries `posting_core.ERPPOReference` (confidence 0.75, adds `_source_tier: "erp_reference_snapshot"` and `_warning` to the result). GRN fallback uses `documents.GoodsReceiptNote` directly.
- **Submission** (`apps/erp_integration/services/submission/posting_submit_resolver.py`): wraps ERP create/park invoice calls.
- **Cache** (`ERPCacheService`): TTL-based DB cache (`ERPReferenceCacheRecord`), controlled by `ERP_CACHE_TTL_SECONDS` env var (default 3600s).
- **Audit** (`ERPAuditService`): logs every resolution + submission to `ERPResolutionLog` / `ERPSubmissionLog` and `AuditEvent`.
- **`PostingMappingEngine`** now accepts `connector=` kwarg; when provided, vendor/item resolution goes through the ERP resolver chain first, then falls back to direct DB. Source metadata per field is stored in `PostingRun.erp_source_metadata_json`.
- **`POLookupTool` / `GRNLookupTool`** now attempt ERP resolution first (`_resolve_via_erp()`); fall through to direct DB only if the resolver import fails. Agent tool spans are threaded via `lf_parent_span` kwarg from `BaseAgent._execute_tool()`.
- **Settings**: `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` (default 0.8), `ERP_CACHE_TTL_SECONDS` (default 3600).
- **API**: `GET/POST /api/v1/erp/resolve/<resolution_type>/` — on-demand ERP reference resolution.
- **Reference Data UI**: `/erp-connections/reference-data/` (`erp_integration:erp_reference_data`) — browse all 5 imported reference tables (Vendors, Items, Tax Codes, Cost Centers, Open POs) with search, pagination, KPI cards, and import-batch provenance. Sidebar: ERP Integration section (Reference Data, Import Reference Data, ERP Connections).
- **ERP connector enums** live in `apps/erp_integration/enums.py` (not `apps/core/enums.py`): `ERPConnectorType`, `ERPConnectionStatus`, `ERPSourceType`, `ERPResolutionType`, `ERPSubmissionType`, `ERPSubmissionStatus`.
- **Langfuse tracing** (`apps/erp_integration/services/langfuse_helpers.py`): ERP-specific helpers for fail-silent tracing. `sanitize_erp_metadata()` redacts API keys/tokens/passwords and truncates large values. `sanitize_erp_error()` maps raw errors to safe categories. `start_erp_span()` / `end_erp_span()` auto-sanitize metadata. Per-stage traced wrappers (`trace_erp_cache_lookup`, `trace_erp_live_lookup`, `trace_erp_db_fallback`, `trace_erp_submission`) create child spans with observation scores. Source provenance helpers: `build_source_chain()`, `freshness_status_label()`, `is_authoritative_source()`. All callers thread `lf_parent_span` through `ERPResolutionService._trace_resolve()` -> `BaseResolver.resolve()` -> per-stage wrappers.

### Invoice Posting Agent (`apps/posting/` + `apps/posting_core/`)
- **Two-layer architecture**: `apps/posting/` (business/UI layer) + `apps/posting_core/` (platform/core layer), mirroring the extraction system.
- **`PostingPipeline`** runs a 9-stage sequence: ELIGIBILITY_CHECK → SNAPSHOT_BUILD → MAPPING → VALIDATION → CONFIDENCE → REVIEW_ROUTING → PAYLOAD_BUILD → FINALIZATION → STATUS. Stage 9b also runs a duplicate invoice check via the ERP integration layer.
- **`PostingMappingEngine`** resolves vendor, item, tax, cost-center, and PO references from imported ERP reference tables (or live ERP API when `connector` is provided). Each resolution follows a strategy chain (exact code → alias → name → fuzzy).
- **Posting status lifecycle**: `NOT_READY` → `READY_FOR_POSTING` → `MAPPING_IN_PROGRESS` → `MAPPING_REVIEW_REQUIRED` | `READY_TO_SUBMIT` → `SUBMISSION_IN_PROGRESS` → `POSTED` | `POST_FAILED` → `RETRY_PENDING` | `REJECTED` | `SKIPPED`.
- **ERP reference import**: `ExcelImportOrchestrator` ingests vendor/item/tax/cost-center/open-PO master data from Excel/CSV into `ERPVendorReference`, `ERPItemReference`, `ERPTaxCodeReference`, `ERPCostCenterReference`, `ERPPOReference` tables.
- **Trigger**: `ExtractionApprovalService.approve()` / `try_auto_approve()` enqueues `prepare_posting_task` automatically (best-effort; never blocks approval).
- **Review queues**: `VENDOR_MAPPING_REVIEW`, `ITEM_MAPPING_REVIEW`, `TAX_REVIEW`, `COST_CENTER_REVIEW`, `PO_REVIEW`, `POSTING_OPS`.
- **Confidence scoring**: 5-dimensional weighted score (header completeness 15%, vendor mapping 25%, line mapping 30%, tax completeness 15%, reference freshness 15%). `is_touchless=True` when no review needed.
- **`PostingRun.erp_source_metadata_json`**: captures per-field ERP resolution source (connector, fallback used, confidence) for every pipeline run.
- **Governance**: 17 posting-specific `AuditEventType` values; `PostingGovernanceTrailService` is the sole writer of `PostingApprovalRecord`.
- **Phase 1 mock submit**: `PostingActionService.submit_posting()` is a mock; replace with real ERP connector call for Phase 2.
- **Setting**: `POSTING_REFERENCE_FRESHNESS_HOURS` (default 168h / 7 days).

### Agent System
- **Full architecture reference:** See [AGENT_ARCHITECTURE.md](../AGENT_ARCHITECTURE.md) for the complete agentic layer documentation, including all agent implementations, the PolicyEngine decision matrix, the DeterministicResolver rule table, RBAC guardrails, the reasoning engine upgrade path, best-practice upgrade guide per agent, and open source observability tool recommendations.
- **No special characters in agent output stored to DB** -- this rule extends beyond source code. LLM-generated text written to `AgentRun.summarized_reasoning`, `ReconciliationResult.summary`, `ReviewAssignment.reviewer_summary`, and `DecisionLog.rationale` must use ASCII only. Apply the `_sanitise_text()` helper (defined in `AGENT_ARCHITECTURE.md` Section 17.3) before any `.save()` call on agent-generated content.
- All LLM agents extend `BaseAgent` (in `apps/agents/services/`).
- LLM agents use **ReAct loop**: LLM -> parse tool calls -> execute tools -> loop (max 6 iterations).
- **Deterministic system agents** extend `DeterministicSystemAgent` (`apps/agents/services/deterministic_system_agent.py`) which skips the ReAct loop entirely. Subclasses implement `execute_deterministic(ctx) -> AgentOutput`. Five concrete system agents in `apps/agents/services/system_agent_classes.py`: `SystemReviewRoutingAgent` (`SYSTEM_REVIEW_ROUTING`), `SystemCaseSummaryAgent` (`SYSTEM_CASE_SUMMARY`), `SystemBulkExtractionIntakeAgent` (`SYSTEM_BULK_EXTRACTION_INTAKE`), `SystemCaseIntakeAgent` (`SYSTEM_CASE_INTAKE`), `SystemPostingPreparationAgent` (`SYSTEM_POSTING_PREPARATION`). All produce standard `AgentRun`, `DecisionLog`, Langfuse spans, and audit events without LLM calls.
- Tool-calling uses **OpenAI-compliant format**: `tool_calls` array on assistant messages, `tool_call_id` + `name` on tool response messages.
- Tools are registered in `apps/tools/registry/` via decorator pattern: `po_lookup`, `grn_lookup`, `vendor_search`, `invoice_details`, `exception_list`, `reconciliation_summary`. Each tool declares `required_permission` (e.g., `"purchase_orders.view"`).
- **`ReasoningPlanner`** is the entry point for planning: always makes a single LLM call to decide which agents to run and in what order; falls back to `PolicyEngine` (deterministic) on any LLM error. There is no feature flag -- the LLM planner is always active.
- `PolicyEngine` handles **auto-close logic**: `should_auto_close()` and `_within_auto_close_band()` check if PARTIAL_MATCH falls within wider auto-close thresholds (qty: 5%, price: 3%, amount: 3%).
- **`AgentOrchestrationRun`** (`apps/agents/models.py`): Top-level DB record for one `AgentOrchestrator.execute()` invocation. Status machine: PLANNED -> RUNNING -> COMPLETED | PARTIAL | FAILED. Acts as duplicate-run guard: a RUNNING record blocks re-entry for the same `ReconciliationResult`.
- Agent pipeline is **wired to run automatically** after reconciliation for non-MATCHED results (sync via `start_reconciliation` view, async via `run_agent_pipeline_task`).
- **AgentGuardrailsService** (`apps/agents/services/guardrails_service.py`): Central RBAC enforcement for all agent operations -- orchestration permission (`agents.orchestrate`), per-agent authorization (`agents.run_*` x 13, including 5 `agents.run_system_*`), per-tool authorization (tool's `required_permission`), recommendation authorization (`recommendations.*` x 6), post-policy authorization (auto-close, escalation), and **data-scope authorization** (`authorize_data_scope()` checks business-unit and vendor-id scope from `UserRole.scope_json`; called immediately after `authorize_orchestration()`).
- **`UserRole.scope_json`** (nullable JSON on `rbac_models.py`): Per-assignment scope restrictions. Supported keys: `allowed_business_units` (list[str]), `allowed_vendor_ids` (list[int]). Null means unrestricted. ADMIN and SYSTEM_AGENT always bypass scope checks.
- **SYSTEM_AGENT** identity: When no human user context is available (Celery async, system-triggered), `AgentGuardrailsService.resolve_actor()` returns a dedicated service account (`system-agent@internal`) with the `SYSTEM_AGENT` role (rank 100, `is_system_role=True`).
- Every agent run, message, tool call, and decision is persisted for auditability via `AgentTraceService`.
- `AgentRun` carries RBAC fields: `actor_primary_role`, `actor_roles_snapshot_json`, `permission_source`, `access_granted` — populated on every run.
- All guardrail decisions (grant/deny) are logged as `AuditEvent` records (9 event types: `GUARDRAIL_GRANTED/DENIED`, `TOOL_CALL_AUTHORIZED/DENIED`, `RECOMMENDATION_ACCEPTED/DENIED`, `AUTO_CLOSE_AUTHORIZED/DENIED`, `SYSTEM_AGENT_USED`).
- `RecommendationService` manages agent recommendations (`AgentRecommendation` model) with acceptance tracking; `mark_recommendation_accepted()` checks `authorize_recommendation()` before allowing accept/reject.
- **Idempotent recommendations**: two-layer dedup -- `DecisionLogService.log_recommendation()` filters for any PENDING rec of the same `(reconciliation_result, recommendation_type)` before creating; model `UniqueConstraint` on `(reconciliation_result, recommendation_type, agent_run)` + `IntegrityError` guard at both orchestrator call sites.
- `AgentFeedbackService` handles PO/GRN re-reconciliation when an agent recovers a missing document (atomic re-linking + re-matching). Only runs when `last_output.status == COMPLETED`.
- **`AgentOutputSchema`** (`apps/agents/services/agent_output_schema.py`): Pydantic v2 schema for all standard agent JSON output. Validates `recommendation_type` (coerces invalid values to `SEND_TO_AP_REVIEW`), clamps `confidence` to [0.0, 1.0]. Applied via `enforce_json_response=True` on `BaseAgent`.
- **`AgentDefinition` catalog fields**: `purpose`, `entry_conditions`, `success_criteria`, `prohibited_actions`, `allowed_recommendation_types`, `default_fallback_recommendation`, `requires_tool_grounding`, `min_tool_calls`, `tool_failure_confidence_cap`, `output_schema_name`, `output_schema_version`, `lifecycle_status`, `owner_team`, `capability_tags`, `domain_tags`, `human_review_required_conditions`. All are first-class DB columns (not in `config_json`). Seed/update via `seed_agent_contracts` command.
- LLM client supports both OpenAI and Azure OpenAI (configurable via env vars).
- Agent definitions include `config_json` with `allowed_tools` per agent type.

### Observability & Tracing
- **TraceContext** (`apps/core/trace.py`): Distributed tracing with `trace_id`, `span_id`, `parent_span_id`, RBAC snapshot. Thread-local propagation. Celery header (de)serialization.
- **Structured Logging** (`apps/core/logging_utils.py`): `JSONLogFormatter` (production), `DevLogFormatter` (dev). `TraceLogger` auto-injects trace context. `redact_dict()` scrubs PII/financial data.
- **Metrics** (`apps/core/metrics.py`): Thread-safe in-process counters via `MetricsService`. Tracks RBAC, extraction, reconciliation, review, agent, case, task metrics.
- **Decorators** (`apps/core/decorators.py`): `@observed_service` (service methods), `@observed_action` (FBV views), `@observed_task` (Celery tasks). All create child spans, measure duration, write `ProcessingLog`/`AuditEvent`.
- **RequestTraceMiddleware** (`apps/core/middleware.py`): Creates root `TraceContext` per request, enriches with RBAC, sets `X-Trace-ID`/`X-Request-ID` headers.
- When adding new services or views, decorate entry-point methods with the appropriate `@observed_*` decorator.
- **External agent observability tools** (Langfuse, Phoenix, openinference, OpenLLMetry): See `AGENT_ARCHITECTURE.md` Section 18 for the full comparison, integration code, and Windows-specific setup. Key points for this Windows 11 dev environment:
  - **Phoenix** (`arize-phoenix` + `openinference-instrumentation-openai`): pure Python, no Docker needed. Start with `python -m phoenix.server.main serve` on port 6006. Use the `threading.Event` guard in `AgentConfig.ready()` to prevent duplicate launches on Django `runserver --reload`.
  - **Langfuse SDK** (`langfuse`): pure Python, installs directly via pip. Self-hosted Langfuse server needs Docker Desktop with the WSL2 backend (Windows 11 default). Set `LANGFUSE_ENABLED`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` in `.env`.
  - **openinference / OTel SDK** (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`): pure Python, works on Windows. Point `OTLPSpanExporter` at `http://localhost:6006` (Phoenix) -- no separate collector needed.
  - **Weave/W&B and LangSmith are not self-hostable** -- do not use for financial/PO data. LangSmith is not open source despite common misconceptions; the server is closed SaaS only.
  - All agent-observable content stored to DB (`AgentRun.summarized_reasoning`, `ReconciliationResult.summary`, `ReviewAssignment.reviewer_summary`, `DecisionLog.rationale`) must be passed through `_sanitise_text()` (defined in `AGENT_ARCHITECTURE.md` Section 17.3) before saving to strip non-ASCII characters that LLMs may generate.

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
| Seed Commands | `apps/core/management/commands/seed_all.py` (unified), `seed_config.py`, `seed_prompts.py`; `apps/accounts/.../seed_rbac.py`; `apps/agents/.../seed_agent_contracts.py`; `apps/extraction_core/.../seed_extraction_config.py`, `seed_control_center.py`; `apps/extraction/.../seed_credits.py` |
| Flush Invoice Data | `apps/core/management/commands/flush_invoices.py` (deletes invoices + cases/recon/agents/reviews, **preserves POs/GRNs/vendors**/config/users/RBAC) |
| Flush ALL Test Data | `apps/core/management/commands/flush_test_data.py` (deletes **everything** incl. POs/GRNs/vendors, preserves config/users/RBAC) |
| Admin | `apps/<app>/admin.py` |
| Templates | `templates/<app>/` (also `templates/governance/` for audit/governance views, `templates/vendors/` for vendor UI) |
| ERP Connectors | `apps/erp_integration/services/connectors/` |
| ERP Resolvers | `apps/erp_integration/services/resolution/` |
| ERP DB Fallbacks | `apps/erp_integration/services/db_fallback/` |
| ERP Submission | `apps/erp_integration/services/submission/` |
| ERP Langfuse Helpers | `apps/erp_integration/services/langfuse_helpers.py` (sanitization, span/score wrappers, source provenance) |
| ERP Connection Config | `apps/erp_integration/models.py` (`ERPConnection`, `ERPReferenceCacheRecord`, `ERPResolutionLog`, `ERPSubmissionLog`) |
| Posting Business Logic | `apps/posting/services/` (eligibility, orchestrator, action service) |
| Posting Core Pipeline | `apps/posting_core/services/` (mapping engine, pipeline, validation, confidence, review routing, governance trail) |
| Posting ERP Reference Models | `apps/posting_core/models.py` (`ERPVendorReference`, `ERPItemReference`, `ERPTaxCodeReference`, `ERPCostCenterReference`, `ERPPOReference`, alias/rule models) |
| Posting Import Pipeline | `apps/posting_core/services/import_pipeline/` (parsers, validators, type importers, orchestrator) |
| Eval & Learning Models | `apps/core_eval/models.py` (EvalRun, EvalMetric, EvalFieldOutcome, LearningSignal, LearningAction) |
| Eval & Learning Services | `apps/core_eval/services/` (eval_run_service, eval_metric_service, eval_field_outcome_service, learning_signal_service, learning_action_service, learning_engine) |
| Eval Adapters | `apps/extraction/services/eval_adapter.py` (ExtractionEvalAdapter -- extraction <-> core_eval bridge), `apps/reconciliation/services/eval_adapter.py` (ReconciliationEvalAdapter -- reconciliation <-> core_eval bridge) |
| Learning Engine Command | `apps/core_eval/management/commands/run_learning_engine.py` |
| Eval & Learning Views | `apps/core_eval/template_views.py` (5 FBV views: eval_run_list, eval_run_detail, learning_signal_list, learning_action_list, learning_action_detail) |
| Eval & Learning URLs | `apps/core_eval/urls.py` (mounted at `/eval/`) |
| Static files | `static/css/`, `static/js/` |
| Config | `config/settings.py`, `config/urls.py`, `config/celery.py` |

---

## Key Models & Relationships

```
User (accounts)
  ├── has legacy role field: ADMIN | AP_PROCESSOR | REVIEWER | FINANCE_MANAGER | AUDITOR | SUPER_ADMIN
  ├── company FK -> CompanyProfile (tenant)
  ├── is_platform_admin BooleanField (cross-tenant platform admin)
  ├── ──< UserRole ──> Role (RBAC multi-role with expiry)
  ├── ──< UserPermissionOverride ──> Permission (ALLOW/DENY per-user)
  └── referenced by: Invoice.created_by, ReviewAssignment.assigned_to, etc.

CompanyProfile (accounts) -- TENANT ENTITY
  ├── name, legal_name, country, currency, industry, website
  ├── ──< CompanyAlias, CompanyTaxID
  └── referenced by: all business models via `tenant` FK

Role (accounts) ──< RolePermission ──> Permission (accounts)
  └── has: code, name, rank, is_system_role, is_active; 10 system roles (incl. SUPER_ADMIN rank 1)

Permission (accounts)
  └── has: code (e.g. invoices.view), module, action, is_active; 65 permissions across 18 modules (incl. tenants.*, platform.settings, eval.*, procurement.*)
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

AgentDefinition (agents) — catalog fields: purpose, entry_conditions, prohibited_actions, tool_grounding contract, lifecycle_status
AgentOrchestrationRun (agents) — top-level pipeline invocation; status PLANNED/RUNNING/COMPLETED/PARTIAL/FAILED; duplicate-run guard
AgentRun ──< AgentStep, AgentMessage, DecisionLog
AgentRun ──< AgentRecommendation (with acceptance tracking + UniqueConstraint on result+type+run)
AgentRun ──< AgentEscalation (severity-based, suggested assignee)
AgentRun ── linked to ReconciliationResult
AgentRun ── RBAC fields: actor_primary_role, actor_roles_snapshot_json, permission_source, access_granted
ToolCall (tools) ── linked to AgentRun + ToolDefinition

ReviewAssignment (cases) ──< ReviewComment, ManualReviewAction
ReviewAssignment ── ReviewDecision (OneToOne)
ReviewAssignment ── linked to ReconciliationResult

ProcessingLog, AuditEvent (auditlog)
IntegrationConfig ──< IntegrationLog (integrations)

ERPConnection (erp_integration)
ERPReferenceCacheRecord (erp_integration) — TTL cache for ERP lookups
ERPResolutionLog (erp_integration) — audit log per lookup attempt
ERPSubmissionLog (erp_integration) — audit log per ERP submission

EvalRun (core_eval) — one evaluation pass per entity
  +--< EvalMetric (N) — named metrics (numeric, text, JSON)
  +--< EvalFieldOutcome (N) -- per-field predicted (LLM) vs ground truth (empty until approval)
  +--< LearningSignal (N) — atomic observations from production

LearningAction (core_eval) — proposed corrective action
  status: PROPOSED -> APPROVED -> APPLIED | REJECTED | FAILED

InvoicePosting (posting) — 1:1 Invoice; lifecycle state + review queue + payload snapshot
InvoicePostingFieldCorrection (posting) — per-field correction audit trail

PostingRun (posting_core) ──< PostingFieldValue, PostingLineItem, PostingIssue, PostingEvidence
PostingRun ──< PostingApprovalRecord (governance mirror)
PostingRun.erp_source_metadata_json — ERP resolution provenance per field
ERPVendorReference / ERPItemReference / ERPTaxCodeReference / ERPCostCenterReference / ERPPOReference (posting_core)
ERPReferenceImportBatch (posting_core) — import metadata (checksum, row counts)
VendorAliasMapping / ItemAliasMapping / PostingRule (posting_core)
```

---

## Status Transitions

### Invoice Status Flow
```
UPLOADED -> EXTRACTION_IN_PROGRESS -> EXTRACTED -> VALIDATED -> PENDING_APPROVAL -> READY_FOR_RECON -> RECONCILED
                                   \-> INVALID                 /-> (auto-approve)                    \-> FAILED
                                                               \-> INVALID (rejected)
```
- **AP Case created immediately after upload** (before extraction begins), giving a stable `case_number` for Langfuse session_id tracing across all downstream pipelines. Invoice is linked to the case after extraction persistence via `CaseCreationService.link_invoice_to_case()`. Case pipeline pauses at `PENDING_EXTRACTION_APPROVAL` if invoice needs human approval.
- **PENDING_APPROVAL**: Human-in-the-loop gate. All valid extractions require human approval before reconciliation.
- Auto-approval: When `EXTRACTION_AUTO_APPROVE_ENABLED=true` and confidence >= `EXTRACTION_AUTO_APPROVE_THRESHOLD`, the system auto-approves and skips human review. Case pipeline continues without pausing.
- On manual approval: `ExtractionApprovalService` resumes the existing case from PATH_RESOLUTION onward (does not create a new case).
- Models: `ExtractionApproval` (one-to-one with Invoice), `ExtractionFieldCorrection` (tracks every field correction for analytics).
- Service: `ExtractionApprovalService` in `apps/extraction/services/approval_service.py`.
- Analytics: `get_approval_analytics()` returns touchless rate, most-corrected fields, approval breakdown.

### Case Status Flow
```
NEW -> INTAKE_IN_PROGRESS -> EXTRACTION_IN_PROGRESS -> EXTRACTION_COMPLETED
  -> PENDING_EXTRACTION_APPROVAL (pauses if invoice needs human approval)
  -> PATH_RESOLUTION_IN_PROGRESS -> {TWO_WAY | THREE_WAY | NON_PO}_IN_PROGRESS
  -> EXCEPTION_ANALYSIS_IN_PROGRESS -> READY_FOR_REVIEW -> IN_REVIEW -> CLOSED | REJECTED | ESCALATED
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

### When adding a new ERP connector
1. Create class in `apps/erp_integration/services/connectors/` extending `BaseERPConnector`.
2. Override the relevant `supports_*()` capability flags and lookup/submission methods.
3. Add the new `ERPConnectorType` value to `apps/erp_integration/enums.py`.
4. Register in `_CONNECTOR_MAP` in `apps/erp_integration/services/connector_factory.py`.
5. Create an `ERPConnection` record (via admin or seed) with `connector_type` set to the new value.

### When adding a new eval adapter for a pipeline
1. Create `apps/<module>/services/eval_adapter.py` following the `ExtractionEvalAdapter` pattern.
2. Define signal type constants (e.g., `SIG_MATCH_OUTCOME = "match_outcome"`).
3. Call `EvalRunService.create_or_update()` to upsert an `EvalRun` per pipeline execution.
4. Call `EvalMetricService.upsert()` for each numeric metric.
5. For `EvalFieldOutcome`: `predicted_value` = final pipeline output (LLM/model value), `ground_truth_value` = empty at pipeline time (populated only on human approval/correction). Store pipeline-internal details (deterministic values, source provenance) in `detail_json`.
6. Call `LearningSignalService.record()` for each observable event.
7. Wire the adapter call into the pipeline task/service inside a `try/except` block (fail-silent).
8. Optionally add new rules to `LearningEngine` if new signal types warrant pattern detection.
9. Add tests: adapter unit tests + end-to-end tests with the engine.

### When adding a new ERP resolver
1. Create class in `apps/erp_integration/services/resolution/` extending `BaseResolver`.
2. Set `resolution_type` to the appropriate `ERPResolutionType` value.
3. Implement `_check_capability(connector)`, `_api_lookup(connector, **params)`, and `_db_fallback(**params)`.
4. Create a matching DB fallback adapter in `apps/erp_integration/services/db_fallback/`.
5. Add the corresponding `ERPResolutionType` enum value in `apps/erp_integration/enums.py` if it doesn't exist.

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
- **RBAC Seed**: `python manage.py seed_rbac --sync-users` -- 10 roles (incl. SUPER_ADMIN, SYSTEM_AGENT, PROCUREMENT), 65 permissions, full matrix, legacy user sync
- **Multi-Tenant Architecture**: Shared-database row-level isolation via `CompanyProfile` tenant FK on 28+ models; `TenantMiddleware` sets `request.tenant`; `TenantQuerysetMixin` on all ViewSets/CBVs; `require_tenant()` for FBVs; `scoped_queryset()` for services; `BaseTool._scoped()` for agent tools; Celery tasks accept `tenant_id`; platform admin (`is_platform_admin` + SUPER_ADMIN role rank 1) bypasses tenant scoping. See [MULTI_TENANT.md](../docs/MULTI_TENANT.md).
- Extraction pipeline (two-agent architecture: InvoiceExtractionAgent always + InvoiceUnderstandingAgent for low confidence; 8 service classes in 7 files + Celery task; Azure Document Intelligence OCR + Azure OpenAI GPT-4o)
- Extraction approval gate: `ExtractionApproval` + `ExtractionFieldCorrection` models; `ExtractionApprovalService` (approve/reject/auto-approve); touchless-rate analytics; approval queue UI; configurable auto-approval (`EXTRACTION_AUTO_APPROVE_ENABLED`, `EXTRACTION_AUTO_APPROVE_THRESHOLD`). AP Case created at upload time (before extraction); invoice linked to case after extraction persistence; case pipeline pauses at `PENDING_EXTRACTION_APPROVAL` if human approval needed; approval resumes existing case.
- Reconciliation engine (14 services + Celery tasks); configurable 2-way/3-way matching with mode resolver (policy -> heuristic -> default); tiered tolerance (strict: 2%/1%/1%, auto-close: 5%/3%/3%)
- **LineMatchService v2** (deterministic multi-signal scorer): 11 weighted signals (item_code 0.30, desc_exact 0.20, token_sim 0.15, fuzzy 0.10, qty 0.10, price 0.07, amount 0.03, uom 0.02, category 0.01, service_stock 0.01, line_number 0.01); 4 penalty types; 5 confidence bands (HIGH/GOOD/MODERATE/LOW/NONE); classification into MATCHED/AMBIGUOUS/UNRESOLVED; optional LLM fallback for unresolved/ambiguous lines only; rich `LineMatchDecision` + `LineCandidateScore` per line; backward compatible `LineMatchPair`/`LineMatchResult` output. Helper modules: `line_match_helpers.py` (text normalization, similarity, numeric proximity, UOM equivalence), `line_match_types.py` (dataclasses + constants), `line_match_llm_fallback.py` (extension point). 14 new fields on `ReconciliationResultLine` (match_method, match_confidence, confidence_band, per-signal scores, candidate_count, is_ambiguous, matched/rejected_signals JSON, line_match_meta JSON). 4 new `ExceptionType` values: NO_CONFIDENT_PO_LINE_MATCH, MULTIPLE_PO_LINE_CANDIDATES, LINE_DESCRIPTION_AMBIGUOUS, LINE_MATCH_LOW_CONFIDENCE.
- `ReconciliationModeResolver` — 3-tier mode cascade: (1) ReconciliationPolicy lookup, (2) heuristic (item flags + service keywords), (3) config default
- `TwoWayMatchService` (Invoice vs PO only), `ThreeWayMatchService` (Invoice vs PO vs GRN), `ReconciliationExecutionRouter`
- `ReconciliationPolicy` model: vendor, item_category, location_code, business_unit, is_service_invoice, is_stock_invoice, priority-ordered matching
- Mode-aware classification, exception building (applies_to_mode tagging), result persistence (mode metadata + confidence weights)
- Agent orchestration (13 agents: 8 LLM + 5 deterministic system agents, policy engine with auto-close logic + mode-aware GRN suppression, tool registry, LLM client, decision log service)
- Deterministic system agents: `DeterministicSystemAgent` base class + 5 concrete agents (`SystemReviewRoutingAgent`, `SystemCaseSummaryAgent`, `SystemBulkExtractionIntakeAgent`, `SystemCaseIntakeAgent`, `SystemPostingPreparationAgent`) -- produce `AgentRun`, `DecisionLog`, Langfuse spans, and `SYSTEM_AGENT_RUN_COMPLETED`/`SYSTEM_AGENT_RUN_FAILED` audit events without LLM calls
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
- Audit logging & governance: ProcessingLog, AuditEvent (~38 event types, 20+ RBAC/trace fields), CaseTimelineService (8 event categories with RBAC badges, status changes, field corrections, duration tracking), governance views (audit event list with RBAC columns + invoice governance dashboard with access history tab)
- Observability infrastructure: TraceContext (distributed tracing), structured JSON logging with PII redaction, in-process MetricsService, RequestTraceMiddleware
- Observability decorators: `@observed_service`, `@observed_action`, `@observed_task` — 10 instrumented service/view/task entry points
- Enhanced governance API: 9 endpoints (audit-history, agent-trace, recommendations, timeline, access-history, stage-timeline, permission-denials, rbac-activity, agent-performance)
- **Unified seed**: `python manage.py seed_all [--flush] [--skip STEP]` runs 7 steps in order: seed_config -> seed_rbac -> seed_prompts -> seed_agent_contracts -> seed_extraction_config -> seed_control_center -> seed_credits
- **Flush invoice data** (default): `python manage.py flush_invoices [--confirm]` deletes invoices, cases, reconciliation runs/results/exceptions, extraction results, agent runs, reviews, audit events, copilot sessions, posting data, eval/learning data, credit transactions. **Preserves POs, GRNs, vendors**, users, RBAC, config, agent/tool definitions. Use this when the user says "flush invoice data".
- **Flush ALL test data** (nuclear): `python manage.py flush_test_data [--confirm]` deletes **everything** including POs, GRNs, vendors, in addition to all the above. Only use when the user explicitly asks to flush "all data" or "test data".
- Windows dev mode: `CELERY_TASK_ALWAYS_EAGER=True` (default) for synchronous execution without Redis
- Root URL (`/`) redirects to `/dashboard/`; `LOGIN_URL = /accounts/login/`

- **Invoice Posting Agent** (`apps/posting/` + `apps/posting_core/`): 9-stage pipeline (eligibility, snapshot, mapping, validation, confidence, review routing, payload build, finalization, status); 11 posting statuses; 6 review queues; Excel/CSV ERP reference import; governance trail; 17 audit event types; posting workbench + detail templates; full DRF API (`/api/v1/posting/` + `/api/v1/posting-core/`).
- **ERP Integration Layer** (`apps/erp_integration/`): `ERPConnection` model + `ConnectorFactory`; 4 connector implementations (Custom, Dynamics, Zoho, Salesforce); 7 resolver types with DB fallback (PO fallback is two-tier: `documents.PurchaseOrder` -> `posting_core.ERPPOReference`); TTL cache; resolution + submission audit logs; `POST /api/v1/erp/resolve/<type>/`; reference data browse UI at `/erp-connections/reference-data/`; wired into `PostingMappingEngine` (connector kwarg) and `POLookupTool`/`GRNLookupTool` (ERP-first with legacy DB fallback).
- `PostingRun.erp_source_metadata_json` field — captures ERP resolution provenance per pipeline run.

- **Evaluation & Learning Framework** (`apps/core_eval/`): 5 domain-agnostic models (EvalRun, EvalMetric, EvalFieldOutcome, LearningSignal, LearningAction); 6 service classes; deterministic `LearningEngine` with 5 threshold rules; `ExtractionEvalAdapter` wired into extraction task + approval service (predicted = LLM value, ground truth = empty until human approval confirms/corrects); `ReconciliationEvalAdapter` wired into reconciliation runner + review service (predicted = match result, ground truth = review decision); `run_learning_engine` management command; RBAC permissions (`eval.view`, `eval.manage`); 6 audit event types (`LEARNING_ENGINE_RUN`, `LEARNING_ACTION_PROPOSED/APPROVED/REJECTED/APPLIED/FAILED`); 5 browsable UI views at `/eval/` with sidebar navigation; 120 tests (22 unit + 13 e2e + 29 RBAC view + 35 extraction adapter/integration + 21 recon adapter). See [EVAL_LEARNING.md](../docs/EVAL_LEARNING.md).

### ⬜ Not yet implemented (next steps)
- **Tests**: Need additional unit tests for services, integration tests for API endpoints, and factory classes for all models. Existing: reconciliation (73 + 88 line-match v2), extraction (282+), extraction_core (50+), eval & learning (120).
- **Extraction refinement**: Tune LLM extraction prompts, add support for multi-page invoices, handle edge-case layouts.
- **Real ERP submission**: `PostingActionService.submit_posting()` is Phase 1 mock — replace with live ERP connector call (SAP BAPI, Oracle REST, etc.).
- **Auto-submit**: Auto-advance touchless postings (`is_touchless=True`, confidence ≥ threshold) directly to `SUBMISSION_IN_PROGRESS` without human approval.
- **Feedback learning**: Train `VendorAliasMapping` / `ItemAliasMapping` from accepted field corrections. `LearningEngine` proposes actions; auto-apply not yet implemented.
- **Scheduled ERP reference re-import**: Celery Beat task to pull fresh master data from shared drive/ERP.
- **LLM-assisted item mapping**: Use GPT for fuzzy item description matching in `PostingMappingEngine._resolve_item()`.
- **Report export services**: Full CSV/Excel export logic not yet built (CSV export exists for case console only).
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
- **ERP connector not resolving?** Check that an `ERPConnection` record exists with `is_default=True`, `status=ACTIVE`, `is_active=True`. If none, `ConnectorFactory.get_default_connector()` returns `None` and posting falls back to direct DB lookups.
- **Posting stuck in MAPPING_IN_PROGRESS?** Check `PostingRun` for `error_code`; inspect `PostingIssue` records with `severity=ERROR`. Also verify ERP reference tables are populated via `/posting/imports/`.
- **ERP cache stale?** `ERPReferenceCacheRecord` entries expire per `ERP_CACHE_TTL_SECONDS` (default 3600s). Delete cache records or reduce TTL to force re-resolution.
- **`PostingMappingEngine` not using ERP?** Confirm `PostingPipeline._get_erp_connector()` returns a non-None connector — requires at least one active default `ERPConnection` record.
- **Need to re-test invoices?** `python manage.py flush_invoices --confirm` deletes invoice-related data while preserving POs, GRNs, vendors, config, users, and RBAC.
- **Need a full clean slate?** `python manage.py flush_test_data --confirm` deletes ALL transactional data (including POs, GRNs, vendors). Then re-seed vendor/PO data with `python manage.py seed_vendor_case_0001` or similar.

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
| `apps/reconciliation/services/line_match_service.py` | Deterministic multi-signal line scorer (v2: 11 signals, ambiguity detection, LLM fallback hook) |
| `apps/reconciliation/services/line_match_types.py` | Scorer dataclasses (`LineCandidateScore`, `LineMatchDecision`, `LLMFallbackResult`) + threshold constants |
| `apps/reconciliation/services/line_match_helpers.py` | Text normalization, token/fuzzy similarity, numeric proximity, UOM equivalence helpers |
| `apps/reconciliation/services/line_match_llm_fallback.py` | LLM fallback extension point (no-op base; subclass to wire actual LLM) |
| `apps/reconciliation/services/agent_feedback_service.py` | Agent PO/GRN re-reconciliation loop |
| `apps/agents/services/orchestrator.py` | Agent pipeline orchestration |
| `apps/agents/services/base_agent.py` | Base agent with ReAct loop |
| `apps/agents/services/agent_classes.py` | All 8 LLM agent implementations |
| `apps/agents/services/deterministic_system_agent.py` | DeterministicSystemAgent base class (skip ReAct) |
| `apps/agents/services/system_agent_classes.py` | 5 concrete system agents |
| `apps/tools/registry/tools.py` | All 6 tool classes |
| `apps/tools/registry/base.py` | BaseTool, ToolRegistry, @register_tool |
| `apps/core/trace.py` | TraceContext for distributed tracing |
| `apps/core/logging_utils.py` | Structured JSON logging, PII redaction |
| `apps/core/metrics.py` | In-process metrics collection |
| `apps/core/decorators.py` | `@observed_service`, `@observed_action`, `@observed_task` decorators |
| `apps/extraction/tasks.py` | Extraction pipeline task |
| `apps/extraction/services/approval_service.py` | Extraction approval gate (approve/reject/auto-approve, field correction tracking, touchless analytics) |
| `apps/cases/services/review_workflow_service.py` | Review workflow lifecycle |
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
| `apps/erp_integration/services/connectors/base.py` | `BaseERPConnector` + `ERPResolutionResult` / `ERPSubmissionResult` data classes |
| `apps/erp_integration/services/resolution/base.py` | `BaseResolver` — cache → API → DB fallback pattern |
| `apps/erp_integration/services/connector_factory.py` | `ConnectorFactory` — instantiates connectors from `ERPConnection` records |
| `apps/erp_integration/models.py` | `ERPConnection`, `ERPReferenceCacheRecord`, `ERPResolutionLog`, `ERPSubmissionLog` |
| `apps/erp_integration/services/langfuse_helpers.py` | ERP-specific Langfuse tracing helpers -- sanitization, span/score wrappers, source provenance utilities |
| `apps/posting_core/services/posting_mapping_engine.py` | Core posting value resolution + ERP connector integration |
| `apps/posting_core/services/posting_pipeline.py` | 9-stage posting pipeline orchestration (incl. duplicate check) |
| `apps/posting/services/posting_orchestrator.py` | Orchestrates `prepare_posting` lifecycle |
| `apps/posting/services/posting_action_service.py` | Approve / reject / submit / retry actions |
| `apps/core_eval/models.py` | EvalRun, EvalMetric, EvalFieldOutcome, LearningSignal, LearningAction |
| `apps/core_eval/services/learning_engine.py` | Deterministic learning engine (5 rules, aggregation, safety controls) |
| `apps/extraction/services/eval_adapter.py` | ExtractionEvalAdapter (extraction <-> core_eval bridge; predicted=LLM, ground_truth=empty until approval) |
| `apps/reconciliation/services/eval_adapter.py` | ReconciliationEvalAdapter (reconciliation <-> core_eval bridge; predicted=match result, ground_truth=review decision) |

---

## Langfuse Observability Patterns

**Reference**: `apps/core/langfuse_client.py` + `docs/LANGFUSE_INTEGRATION.md`

All Langfuse calls are fail-silent -- if `LANGFUSE_PUBLIC_KEY` is not set, every function returns `None` and callers must guard accordingly.

### Available helpers (import from `apps.core.langfuse_client`)

```python
from apps.core.langfuse_client import (
    start_trace,   # open a root trace span
    start_span,    # open a child span under a trace or span
    end_span,      # close any span, optionally set output + level
    log_generation,# record one LLM call (model, messages, tokens, completion)
    score_trace,   # attach a numeric score to a trace by trace_id
)
```

### Trace ID conventions

| Context | Trace ID pattern |
|---|---|
| Extraction pipeline (task) | `uuid4().hex` (generated per task run; session_id=`"case-{case_number}"` when case exists, fallback `"extraction-upload-{upload_id}"`) |
| Agent pipeline / extraction | `trace_ctx.trace_id` (from `TraceContext`) |
| Reconciliation run | `run.trace_id` if set, else `str(run.pk)` |
| Posting run | `posting_run.trace_id` if set, else `str(posting_run.pk)` |
| ERP submission | `f"erp-{posting_run_id}"` -> `f"erp-inv-{invoice_id}"` -> `uuid4().hex` |
| Extraction approval | `f"approval-{approval.pk}"` |
| Review assignment | `f"review-{assignment.pk}"` |
| Bulk job | `f"bulk-{job.pk}"` |
| Copilot session | `f"copilot-{session.pk}"` |

### Score value conventions

| Score name | Values | When to emit |
|---|---|---|
| `reconciliation_match` | MATCHED=1.0, PARTIAL=0.5, REQUIRES_REVIEW=0.3, UNMATCHED=0.0 | After match classification |
| `posting_confidence` | 0.0-1.0 composite | After stage 6 of posting pipeline |
| `posting_requires_review` | 1.0 or 0.0 | After review routing (stage 7) |
| `extraction_confidence` | 0.0-1.0 | After extraction pipeline persistence |
| `extraction_success` | 1.0 or 0.0 | After extraction completes or fails |
| `extraction_is_valid` | 1.0 or 0.0 | After validation stage |
| `extraction_is_duplicate` | 1.0 or 0.0 | After duplicate detection |
| `extraction_requires_review` | 1.0 or 0.0 | After routing in extraction pipeline |
| `weakest_critical_field_score` | 0.0-1.0 | After field confidence scoring |
| `decision_code_count` | integer as float | After decision code derivation |
| `response_was_repaired` | 1.0 or 0.0 | When LLM response repair was applied |
| `qr_detected` | 1.0 or 0.0 | When e-invoice QR code data found |
| `review_priority` | priority / 10.0 (normalised to 0.0-1.0) | On `create_assignment()` |
| `review_decision` | APPROVED=1.0, REPROCESSED=0.5, REJECTED=0.0 | On `_finalise()` |
| `rbac_guardrail` | 1.0=GRANTED, 0.0=DENIED | Every guardrail decision |
| `rbac_data_scope` | 0.0 only (deny path) | `authorize_data_scope()` deny |
| `bulk_job_success_rate` | 0.0-1.0 (processed / total) | After bulk job completes |
| `copilot_session_length` | message count (raw int as float) | On session archive |
| `recon_final_success` | 1.0 or 0.0 | After reconciliation task completes or fails |
| `recon_routed_to_agents` | float (count of non-MATCHED results) | After reconciliation run |
| `recon_routed_to_review` | float (count of REQUIRES_REVIEW results) | After reconciliation run |
| `recon_final_status_matched` | 1.0 or 0.0 per invoice | After match classification |
| `recon_final_status_partial_match` | 1.0 or 0.0 per invoice | After match classification |
| `recon_final_status_requires_review` | 1.0 or 0.0 per invoice | After match classification |
| `recon_final_status_unmatched` | 1.0 or 0.0 per invoice | After match classification |
| `recon_po_found` | 1.0 or 0.0 per invoice | After PO lookup |
| `recon_grn_found` | 1.0 or 0.0 per invoice (THREE_WAY) | After GRN lookup |
| `recon_auto_close_eligible` | 1.0 or 0.0 per invoice | After classification |
| `recon_exception_count_final` | integer as float per invoice | After exception build |
| `agent_pipeline_final_confidence` | 0.0-1.0 | After agent orchestrator execute() |
| `agent_pipeline_recommendation_present` | 1.0 or 0.0 | After agent pipeline |
| `agent_pipeline_escalation_triggered` | 1.0 or 0.0 | After agent pipeline |
| `agent_pipeline_auto_close_candidate` | 1.0 or 0.0 | After agent pipeline |
| `agent_pipeline_agents_executed_count` | integer as float | After agent pipeline |
| `agent_confidence` | 0.0-1.0 per agent | After each agent run |
| `agent_recommendation_present` | 1.0 or 0.0 per agent | After each agent run |
| `agent_tool_success_rate` | 0.0-1.0 per agent | After each agent run |
| `case_processing_success` | 1.0 or 0.0 | After case task completes or fails |
| `case_stages_executed` | integer as float | After case orchestrator run |
| `case_closed` | 1.0 or 0.0 | After case orchestrator run |
| `case_terminal` | 1.0 or 0.0 | After case orchestrator run |
| `case_path_resolved` | 1.0 or 0.0 | After PATH_RESOLUTION stage |
| `case_match_status` | MATCHED=1.0, PARTIAL=0.5, REVIEW=0.3, UNMATCHED=0.0 | After matching stage |
| `case_auto_closed` | 1.0 or 0.0 | After EXCEPTION_ANALYSIS stage |
| `case_routed_to_review` | 1.0 or 0.0 | After REVIEW_ROUTING stage |
| `case_reprocessed` | 1.0 or 0.0 | After reprocess task completes |
| `review_assignment_created` | 1.0 | On create_assignment() |
| `review_approved` | 1.0 or 0.0 | On _finalise() |
| `review_rejected` | 1.0 or 0.0 | On _finalise() |
| `review_reprocess_requested` | 1.0 or 0.0 | On _finalise() |
| `review_had_corrections` | 1.0 or 0.0 | On _finalise() |
| `review_fields_corrected_count` | integer as float | On record_action() for CORRECT_FIELD |
| `erp_resolution_success` | 1.0 or 0.0 | After ERP resolution (per resolve call) |
| `erp_resolution_latency_ok` | 1.0 if <=5s, 0.0 if >5s | After ERP resolution (per resolve call) |
| `erp_resolution_result_present` | 1.0 or 0.0 | After ERP resolution (per resolve call) |
| `erp_resolution_fresh` | 1.0 or 0.0 (stale check) | After ERP resolution (per resolve call) |
| `erp_resolution_authoritative` | 1.0 if API/CACHE, 0.0 if fallback | After ERP resolution (per resolve call) |
| `erp_resolution_used_fallback` | 1.0 or 0.0 | After ERP resolution (per resolve call) |
| `erp_cache_hit` | 1.0 or 0.0 | After cache check in BaseResolver |
| `erp_live_lookup_success` | 1.0 or 0.0 | After live API call in BaseResolver |
| `erp_live_lookup_latency_ok` | 1.0 if <=5s, 0.0 if >5s | After live API call in BaseResolver |
| `erp_live_lookup_rate_limited` | 1.0 or 0.0 | After live API call in BaseResolver |
| `erp_live_lookup_timeout` | 1.0 or 0.0 | After live API call in BaseResolver |
| `erp_db_fallback_used` | 1.0 (always, only emitted when fallback runs) | After DB fallback in BaseResolver |
| `erp_db_fallback_success` | 1.0 or 0.0 | After DB fallback in BaseResolver |
| `erp_submission_attempted` | 1.0 (always) | On ERP submission call |
| `erp_submission_success` | 1.0 or 0.0 | After ERP submission completes |
| `erp_submission_latency_ok` | 1.0 if <=5s, 0.0 if >5s | After ERP submission completes |
| `erp_submission_retryable_failure` | 1.0 or 0.0 | After ERP submission failure |
| `erp_submission_document_number_present` | 1.0 or 0.0 | After successful ERP submission |

### When adding a new Celery task that triggers a pipeline

1. Resolve a `trace_id` at the top of the task:
   ```python
   import uuid
   _trace_id = getattr(obj, "trace_id", None) or str(obj.pk)
   ```
2. Call `start_trace()` before the service call, `end_span()` after:
   ```python
   from apps.core.langfuse_client import start_trace, end_span
   _lf_trace = start_trace(
       _trace_id,
       "task_name",          # snake_case name shown in Langfuse UI
       metadata={"task_id": self.request.id, "obj_pk": obj.pk},
   )
   try:
       result = SomeService.run(obj)
   finally:
       end_span(_lf_trace, output={"status": result.status if result else "error"})
   ```
3. Wrap the whole block in `try/except Exception` -- never let Langfuse errors propagate.
4. Pass `_trace_id` into the service so child spans can be attached to it.

### When adding a new pipeline stage (span)

```python
_lf_span = start_span(
    _lf_trace,                # parent trace or span
    name="stage_name",        # e.g. "eligibility_check"
    metadata={"stage": 1, "invoice_id": invoice.pk},
)
# ... do work ...
end_span(
    _lf_span,
    output={"passed": True, "issues": []},
    level="ERROR" if failed else "DEFAULT",
)
```

Always call `end_span()` in a `finally` block so spans are never left open.

### When adding an LLM call

```python
log_generation(
    span=_lf_span,
    name="descriptive_call_name",       # e.g. "invoice_extraction_chat"
    model=deployment_name,              # e.g. "gpt-4o"
    prompt_messages=[                   # list of {"role": ..., "content": ...}
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ],
    completion=response_text,
    prompt_tokens=usage.prompt_tokens,
    completion_tokens=usage.completion_tokens,
    total_tokens=usage.total_tokens,
)
```

### When emitting a quality score

```python
from apps.core.langfuse_client import score_trace
score_trace(
    _trace_id,
    "score_name",    # from the conventions table above
    float_value,     # always a float in range 0.0-1.0 (or raw count)
    comment=f"context={...}",    # human-readable, optional but encouraged
    span=_lf_trace,  # REQUIRED: pass the Langfuse span so the real OTel trace_id is extracted
)
```

**Important**: Always pass `span=` to `score_trace()` / `score_trace_safe()`. In Langfuse SDK v4, the real OTel trace_id differs from our application-level string trace_id. Without `span=`, scores are orphaned and show blank session_id/user_id. The `_extract_otel_trace_id(span)` helper extracts the 128-bit OTel trace_id from the span's `_otel_span` context.
```

### Guard pattern (all Langfuse calls must be guarded)

```python
try:
    from apps.core.langfuse_client import start_span, end_span
    _lf_span = start_span(_lf_trace, name="my_span") if _lf_trace else None
except Exception:
    _lf_span = None

# ... do work ...

try:
    if _lf_span:
        end_span(_lf_span, output={...})
except Exception:
    pass
```

### Known missing integrations (implement these next)

| File | What to add |
|---|---|
| ~~`apps/extraction/bulk_tasks.py` — `run_bulk_job_task`~~ | ~~Root trace `"bulk_job"` wrapping the entire job; score `bulk_job_success_rate` at the end~~ **Done (already implemented)** |
| ~~`apps/extraction/services/bulk_service.py` — `_process_item()`~~ | ~~Child span per item; mark `level="ERROR"` on failure~~ **Done (already implemented)** |
| ~~`apps/extraction/services/bulk_source_adapters.py` — `GoogleDriveBulkSourceAdapter`, `OneDriveBulkSourceAdapter`~~ | ~~Span per `test_connection()`, `list_files()`, `download_file()` to surface latency and auth failures~~ **Done (already implemented)** |
| ~~`apps/reconciliation/tasks.py` -- `run_reconciliation_task`~~ | ~~Root trace `"reconciliation_task"` forwarded into `ReconciliationRunnerService`~~ **Done (2026-03-31)** -- **Enriched**: session_id, trigger, invoices_preview; 3 task-level scores (recon_final_success, recon_routed_to_agents, recon_routed_to_review) |
| ~~`apps/posting_core/services/posting_pipeline.py`~~ | ~~Root trace `"posting_pipeline"` opened at stage 1 so `posting_confidence` and `posting_requires_review` scores are linked~~ **Done (2026-03-31): 9 per-stage spans + root trace added** |
| ~~`apps/reconciliation/services/runner_service.py`~~ | ~~Root trace `"reconciliation_run"` so `reconciliation_match` scores are linked~~ **Done** -- **Enriched**: 8 per-invoice spans (po_lookup, mode_resolution, grn_lookup, match_execution, classification, result_persist, exception_build, review_workflow_trigger) + 15+ observation scores + 11 trace-level scores + eval-ready root trace metadata |
| ~~`apps/erp_integration/services/resolution_service.py`~~ | ~~ERP resolution Langfuse spans~~ **Done (2026-03-31): `_trace_resolve` helper + `lf_parent_span` kwarg on all resolve_* methods** |
| ~~`apps/agents/tasks.py` -- `run_agent_pipeline_task`~~ | ~~Root trace at task level~~ **Done** -- **Enriched**: prior_match_status, reconciliation_mode, trigger=auto metadata |
| ~~`apps/agents/services/orchestrator.py`~~ | ~~Agent pipeline trace~~ **Done** -- **Enriched**: case_id, case_number, prior_match_status, exception_count, vendor info; 5 pipeline-level scores (agent_pipeline_final_confidence, recommendation_present, escalation_triggered, auto_close_candidate, agents_executed_count) |
| ~~`apps/agents/services/base_agent.py`~~ | ~~Per-agent and per-tool spans~~ **Done** -- **Enriched**: agent_type, reconciliation_mode, po_number metadata; 3 per-agent scores (agent_confidence, agent_recommendation_present, agent_tool_success_rate); tool spans include source_used + tool_call_success score |
| ~~`apps/erp_integration/services/submission/posting_submit_resolver.py`~~ | ~~Inherit parent trace ID from posting pipeline instead of creating isolated traces~~ **Done**: Full tracing via `langfuse_helpers.py` -- `erp_submission` span with 5 scores (attempted, success, latency_ok, retryable_failure, document_number_present); metadata sanitized via `sanitize_erp_error()`; standalone root trace when no parent |
| ~~`apps/copilot/services/copilot_service.py` -- `answer_question()`~~ | ~~Span `"copilot_answer"` with `metadata={"topic": topic, "session_id": ...}`; score `copilot_session_length` on session archive~~ **Done (2026-03-31)** |
| ~~`apps/cases/tasks.py` -- `process_case_task`, `reprocess_case_from_stage_task`~~ | ~~Root trace `"case_task"` per task invocation~~ **Done** -- **Enriched**: session_id, invoice_id, case_number, vendor metadata; lf_trace passed to CaseOrchestrator; case_processing_success + case_reprocessed scores |
| ~~`apps/cases/orchestrators/case_orchestrator.py`~~ | ~~Per-stage spans in case pipeline~~ **Done** -- **Enriched**: per-stage Langfuse spans with stage_index, processing_path, case_status_before; stage-specific observation scores; 4 trace-level scores (case_stages_executed, case_closed, case_terminal, case_path_resolved, case_match_status, case_auto_closed, case_routed_to_review) |
| ~~`apps/cases/services/review_workflow_service.py`~~ | ~~Review workflow spans and scores~~ **Done** -- **Enriched**: create_assignment trace with match_status, exception_count, session_id; record_action spans + review_fields_corrected_count score; add_comment spans; _finalise enriched with 5 decision scores (review_approved, review_rejected, review_reprocess_requested, review_had_corrections, review_fields_corrected_count). Moved from `apps/reviews/services.py` during reviews-to-cases merge. |
| ~~`apps/posting_core/services/posting_mapping_engine.py`~~ | ~~Pass the `mapping` stage span via `lf_parent_span` to `ERPResolutionService.resolve_vendor/resolve_item` calls so ERP spans appear nested under the `mapping` span in Langfuse~~ **Done (2026-03-31)** |
| ~~`apps/extraction/tasks.py` -- `process_invoice_upload_task`~~ | ~~Root trace `"extraction_pipeline"` with 12 per-stage spans + 10 trace-level scores + per-observation scores.~~ **Done** |

### Debugging Langfuse

- **No traces appearing**: Verify `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` are set. Check logs for `Langfuse disabled` or `start_trace failed`.
- **Scores unlinked**: The pipeline is not creating a root trace -- `score_trace` scores are recorded but float free. Add a `start_trace` at pipeline entry and pass the same `trace_id` to `score_trace`. Also ensure `span=` is passed to `score_trace`/`score_trace_safe` (see below).
- **Scores show blank session_id/user_id**: In Langfuse SDK v4, the real OTel trace_id differs from our application-level string trace_id. Pass `span=` to `score_trace_safe()` so `_extract_otel_trace_id()` can extract the real 128-bit OTel trace_id. Without it, scores cannot be linked to the parent trace (which carries user/session attributes).
- **Users/Sessions tab empty**: Confirm SDK is v4.x. User/session attribution uses `_otel_span.set_attribute()` -- not constructor kwargs.
- **Prompt 404 in logs**: Run `python manage.py push_prompts_to_langfuse`. If names are wrong, run with `--purge`.
- **`start_trace` returns None**: Set `LANGFUSE_LOG_LEVEL=debug`; look for `TypeError` from unknown kwargs or bad host URL (must not have trailing slash).
