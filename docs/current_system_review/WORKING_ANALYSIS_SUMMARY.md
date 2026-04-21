# Working Analysis Summary

**Generated**: 2026-04-09  
**Method**: Code-first iterative inspection — Phase 1–6 complete  
**Confidence**: High (core models, services, tasks, RBAC, agents all verified from code)

---

## A. Repository Structure

```
/
├── apps/                   # 21 installed Django apps
├── config/                 # Django project config (settings, urls, celery, wsgi, asgi)
├── docs/                   # Existing docs + this review
├── deploy/                 # Deployment guides (Nginx, Gunicorn, Systemd)
├── scripts/                # Debugging/seed scripts (not production tasks)
├── templates/              # Global Bootstrap 5 templates
├── static/                 # JS/CSS assets
├── logs/                   # Rotating JSON logs (po_recon.log)
├── requirements.txt        # Pinned production deps (Django 5.0.14)
├── conftest.py             # pytest fixtures
└── manage.py
```

**Stack confirmed from requirements.txt**:
- Django 5.0.14, DRF 3.16, django-filter 25.1, django-celery-results 2.6
- MySQL (utf8mb4, STRICT_TRANS_TABLES)
- Celery 5.6.2 + Redis 6.4 (broker), `django-db` (result backend)
- OpenAI 2.30 (Azure), LangChain-OpenAI, Langfuse 4.0.1
- Azure Document Intelligence (azure-ai-formrecognizer 3.3.2), Azure Blob
- OpenTelemetry (API + SDK + OTLP exporter), openinference (OpenAI instrumentation)
- thefuzz, RapidFuzz, python-Levenshtein (string matching)
- pydantic 2.12, openpyxl 3.1, Pillow 12.1, dateparser 1.3

---

## B. Django Project Structure

- **Root module**: `config/` (settings.py, urls.py, celery.py)
- **Custom user model**: `accounts.User` (`AUTH_USER_MODEL`)
- **Multi-tenancy**: Row-level via `CompanyProfile` FK (`tenant` field) on nearly all models
- **Middleware stack** (ordered): Security → Session → Common → CSRF → Auth → `TenantMiddleware` → Messages → XFrame → `LoginRequiredMiddleware` → `RBACMiddleware` → `RequestTraceMiddleware`
- **Celery beat**: `process_approved_learning_actions` every 30 min (only beat schedule defined)
- **Settings**: `CELERY_TASK_ALWAYS_EAGER` off by default, env-overrideable for dev/test

---

## C. App Inventory

| App | Type | Status | Notes |
|-----|------|--------|-------|
| `core` | Platform/shared | Active | Enums, BaseModel, PromptTemplate, middleware, prompt_registry, metrics, utils |
| `accounts` | Auth/RBAC | Active | Custom User, CompanyProfile (tenant), RBAC models |
| `vendors` | Domain | Active | Vendor model, normalized name, aliases |
| `documents` | Domain | Active | DocumentUpload, Invoice, PurchaseOrder, GoodsReceiptNote + line items |
| `extraction` | Domain/Pipeline | Active | 11-stage extraction pipeline, 20 service modules |
| `extraction_core` | Platform | Active | ExtractionRun model, control center, extraction_documents (legacy table migration) |
| `extraction_configs` | Config | Active | Extraction configuration models |
| `reconciliation` | Domain/Engine | Active | 14-service matching engine, ReconciliationResult, ReconciliationException |
| `agents` | Agentic | Active | 9 LLM agents (8 standard + 1 supervisor) + 5 system agents, orchestrator, policy engine, skills, plugins |
| `tools` | Agentic | Active | 6 tools with BaseTool pattern + permission registry |
| `cases` | Domain | Active | APCase central object, 11+ stage state machine, orchestrators |
| `copilot` | UI/AI | Active | Copilot conversational service + views |
| `dashboard` | UI/Analytics | Active | 7 API endpoints, analytics views |
| `reports` | UI | Stub | URLs registered, exports not implemented |
| `auditlog` | Governance | Active | AuditEvent (compliance), ProcessingLog (operational) |
| `integrations` | Integration | Partial | App registered; specific connectors in erp_integration |
| `erp_integration` | Integration | Active | 6 connector types, resolution service, cache service |
| `posting` | Domain | Active | Invoice posting workflow (PROPOSED → SUBMITTED) |
| `posting_core` | Platform | Active | VendorAliasMapping, reference import tables |
| `benchmarking` | Domain | Active | Dedicated benchmark request/quotation domain plus procurement compatibility benchmark services |
| `procurement` | Domain | Active | Procurement request intake, quotation prefill, validation, HVAC recommendation, market intelligence, result persistence |
| `core_eval` | Platform/AI | Active | Generic eval framework: EvalRun, EvalMetric, LearningSignal, LearningAction |
| `reviews` | Stub | Legacy | Comment in settings: "migrations-only stub; models moved to apps.cases" |

---

## D. Core Business Domain

**Accounts Payable Invoice Processing Automation** for multi-tenant enterprise organizations:
1. Receive invoice PDFs (web upload or bulk)
2. OCR + AI extraction of structured invoice data
3. Match invoices against Purchase Orders (2-way) and Goods Receipt Notes (3-way)
4. Route exceptions to LLM agents for analysis and recommendation
5. Human review for unresolvable exceptions
6. ERP posting of approved invoices

Seed data references "Saudi McD" (McDonald's Saudi Arabia), suggesting hospitality/F&B use case, though the platform is designed generically.

---

## E. Agentic Components

**LLM Agents** (8 in `AGENT_CLASS_REGISTRY`):
1. `InvoiceExtractionAgent` — single-shot GPT-4o extraction, temperature=0
2. `InvoiceUnderstandingAgent` — ReAct loop, tools: invoice_details, po_lookup, vendor_search
3. `PORetrievalAgent` — ReAct loop, tools: po_lookup, vendor_search, invoice_details
4. `GRNRetrievalAgent` — ReAct loop, tools: grn_lookup, po_lookup, invoice_details (3-way only)
5. `ExceptionAnalysisAgent` — ReAct loop + second LLM call for reviewer summary
6. `ReviewRoutingAgent` — ReAct loop, tools: reconciliation_summary, exception_list
7. `CaseSummaryAgent` — ReAct loop, all 5 business tools
8. `ReconciliationAssistAgent` — ReAct loop, all 5 business tools

**System Agents** (5 deterministic, in `system_agent_classes.py`):
- `SystemCaseIntakeAgent`, `SystemReviewRoutingAgent`, `SystemCaseSummaryAgent`,
  `SystemBulkExtractionIntakeAgent`, `SystemPostingPreparationAgent`

**Orchestration**: `AgentOrchestrator.execute()` called from `run_agent_pipeline_task`  
- Plan source: `PolicyEngine` (deterministic, default) or `ReasoningPlanner` (LLM, `AGENT_REASONING_ENGINE_ENABLED=true`)
- Feedback loop: `PO_RETRIEVAL` findings → re-reconcile atomically

**Tools** (6 registered via `@register_tool`):
- `po_lookup`, `grn_lookup`, `vendor_search`, `invoice_details`, `exception_list`, `reconciliation_summary`
- Each has `required_permission`, semantic metadata (when_to_use, no_result_meaning, failure_handling_instruction)
- ERP integration layer tried first; DB fallback if unavailable

---

## F. Prompt/Tool/Model Governance

**PromptRegistry** resolution order:
1. In-process cache
2. Langfuse (production label)
3. Database `PromptTemplate` model
4. Hardcoded fallback (for critical prompts)

**Prompt keys confirmed**:
- `extraction.invoice_system` (monolithic fallback)
- `agent.exception_analysis`, `agent.invoice_understanding`, `agent.po_retrieval`
- `agent.grn_retrieval`, `agent.review_routing`, `agent.case_summary`, `agent.reconciliation_assist`
- Modular composition: `extraction.invoice_base` + category/country overlays via `InvoicePromptComposer`
- Prompt hash logged to `AgentRun.prompt_version` and `AgentRun.input_payload._prompt_meta`

**LLM Config**: `LLM_PROVIDER=azure_openai`, `AZURE_OPENAI_DEPLOYMENT`, `LLM_TEMPERATURE=0.1`, `LLM_MAX_TOKENS=4096`, `LLM_REQUEST_TIMEOUT=120s`

**Cost tracking**: `LLMCostRate` model (per-model pricing), `actual_cost_usd` on `AgentRun`

---

## G. RBAC/Governance Findings

**Models**: `Role`, `Permission`, `RolePermission`, `UserRole`, `UserPermissionOverride`, `MenuConfig`

**Roles (6 system roles)**: ADMIN, AP_PROCESSOR, REVIEWER, FINANCE_MANAGER, AUDITOR, SYSTEM_AGENT

**Permission precedence** (from rbac_models.py docstring):
1. ADMIN → always granted
2. User-level DENY → blocks even if role grants
3. User-level ALLOW → grants even without role
4. Role-level → union of active role permissions

**Guardrails**: `AgentGuardrailsService` enforces:
- `ORCHESTRATE_PERMISSION = "agents.orchestrate"`
- Per-agent permissions (e.g. `agents.run_extraction`, `agents.run_po_retrieval`)
- Per-tool permissions (e.g. `purchase_orders.view`, `grns.view`)
- Per-recommendation permissions (e.g. `recommendations.auto_close`, `recommendations.escalate`)
- Per-action permissions (e.g. `reviews.assign`, `reconciliation.run`)

**System identity**: `system-agent@internal` user, SYSTEM_AGENT role — not admin bypass, least-privilege

**RBAC snapshots**: `actor_primary_role`, `actor_roles_snapshot_json`, `permission_source`, `access_granted` stored on `AgentRun`, `AuditEvent`, `DecisionLog`

---

## H. Audit/Traceability Findings

**AuditEvent** (compliance-grade, `auditlog_audit_event`):
- Entity-based (type + ID), action, old/new values, performed_by (user or agent)
- `event_type` (38+ event types), `performed_by_agent`, trace_id/span_id
- RBAC snapshot at action time (actor_email, actor_primary_role, permission_source, access_granted)

**ProcessingLog** (operational, `auditlog_processing_log`):
- Duration, retry_count, exception_class, success flag
- Task-linked (task_name, task_id)

**DecisionLog** (agent decisions, `agents_decision_log`):
- Per-decision: decision_type, decision, rationale, confidence, deterministic_flag
- Rule/policy/prompt traceability (rule_name, rule_version, policy_code, prompt_version)
- RBAC context snapshot

**Langfuse trace hierarchy**:
- Root trace (Celery task ID → 32-char hex)
- Span: extraction / reconciliation / agent pipeline
- Child spans: per-agent, per-tool calls
- Scores: EXTRACTION_CONFIDENCE, CASE_PROCESSING_SUCCESS, RECON_FINAL_SUCCESS, etc.

**AgentRun** stores: prompt_tokens, completion_tokens, total_tokens, actual_cost_usd, llm_model_used

---

## I. Celery/Background Findings

**Tasks inventory** (confirmed from tasks.py files):
- `extraction.tasks.run_extraction_task` — 11-stage pipeline execution
- `reconciliation.tasks.run_reconciliation_task` — max_retries=5, delay=60s
- `reconciliation.tasks.reconcile_single_invoice_task` — convenience wrapper
- `agents.tasks.run_agent_pipeline_task` — max_retries=1, delay=30s
- `cases.tasks.process_case_task` — max_retries=3, delay=30s, acks_late=True
- `cases.tasks.reprocess_case_from_stage_task` — max_retries=2, delay=10s, acks_late=True
- `core_eval.tasks.process_approved_learning_actions` — beat schedule every 30 min

**Chain**: reconciliation_task → (auto-chain) → run_agent_pipeline_task for non-MATCHED results

**Actor propagation**: `actor_user_id` passed through all Celery task signatures

**No Celery Beat for**: reconciliation runs, extraction runs, ERP sync — all triggered on-demand

**Operational gaps**: No email notifications, no scheduled ERP data sync, no Celery Beat health checks

---

## J. Integrations Identified

| Integration | Type | Module | Status |
|------------|------|--------|--------|
| Azure OpenAI (GPT-4o) | LLM | `agents.services.llm_client` | Active |
| Azure Document Intelligence | OCR | `extraction.services.extraction_adapter` | Active |
| Azure Blob Storage | Document storage | `documents.models` blob fields | Active |
| Langfuse | LLM observability | `core.langfuse_client` | Active |
| Redis | Celery broker | `config.settings` | Active |
| MySQL | Primary DB | `config.settings` | Active |
| ERP: Custom API | ERP connector | `erp_integration.services.connectors.custom_erp` | Active |
| ERP: SQL Server | ERP connector | `erp_integration.services.connectors.sqlserver` | Active |
| ERP: MySQL | ERP connector | `erp_integration.services.connectors.mysql` | Active |
| ERP: Dynamics 365 | ERP connector | `erp_integration.services.connectors.dynamics` | Active |
| ERP: Zoho Books | ERP connector | `erp_integration.services.connectors.zoho` | Active |
| ERP: Salesforce | ERP connector | `erp_integration.services.connectors.salesforce` | Active |
| OpenTelemetry | Observability | `core.observability_helpers` | Active |
| Loki | Log aggregation | `core.logging_utils.SilentLokiHandler` | Optional (LOKI_ENABLED) |
| Email | Notifications | — | Not implemented |

---

## K. Existing Docs Reviewed

| File | Assessment |
|------|-----------|
| `README.md` | Accurate high-level overview; implementation status table is current |
| `docs/PROJECT.md` | Comprehensive; needs verification against current code |
| `docs/AGENT_ARCHITECTURE.md` | Documents agent framework; may predate system agents |
| `docs/EXTRACTION_AGENT.md` | Phase 2 pipeline; aligns with code |
| `docs/LANGFUSE_INTEGRATION.md` | Integration details; should verify Langfuse 4.x SDK quirks |
| `docs/CELERY.md` | Task docs; beat schedule is minimal (only learning actions) |
| `docs/POSTING_AGENT.md` | Posting workflow; needs code verification |
| `docs/PROCUREMENT.md` | Procurement intelligence; mostly aligned for request/recommendation/validation/prefill flows, but benchmark runtime differs from full should-cost design |
| `docs/ERP_INTEGRATION.md` | ERP connectors; aligns with code structure |
| `docs/DATABASE.md` | DB model reference; likely has some drift |
| `docs/MULTI_TENANT.md` | Tenancy model; aligns with CompanyProfile FK pattern |
| `docs/EVAL_LEARNING.md` | core_eval framework; active |
| `docs/RECON_AGENT.md` | Reconciliation agent; needs verification |
| `docs/OBSERVABILITY_UPGRADE_SUMMARY.md` | Recent observability work; likely current |

---

## L. Code-vs-Doc Mismatches

1. **README app count**: README says "26 Django apps" but `INSTALLED_APPS` has 21 apps (extraction_documents not in installed apps; reviews is stub)
2. **reviews app**: README doesn't flag it as a stub; code comment in settings.py explicitly notes "migrations-only stub; models moved to apps.cases"
3. **Beat schedule**: README says "Celery Beat (scheduled tasks) — Not started" but celery.py has `process_approved_learning_actions` on 30-min schedule — this is implemented
4. **ReasoningPlanner**: Available in code (`AGENT_REASONING_ENGINE_ENABLED`) but not prominently documented
5. **System agents**: 5 system/deterministic agents exist alongside the 9 LLM agents (8 standard + 1 supervisor); README only mentions 8
6. **Supervisor agent**: Full AP lifecycle orchestrator with 5 skills, 24 dedicated tools, PluginToolRouter, non-linear 5-phase processing — see `17_Supervisor_Agent_Architecture.md`

---

## M. Proposed Documentation File Set

All files in `docs/current_system_review/`:
- `WORKING_ANALYSIS_SUMMARY.md` (this file)
- `00_System_Overview.md`
- `01_Inferred_Business_Requirements.md`
- `02_Django_App_Landscape_and_Module_Boundaries.md`
- `03_Agent_Architecture_and_Execution_Model.md`
- `04_Prompt_Tool_and_Model_Governance.md`
- `05_Features_and_Workflows.md`
- `06_Data_Model_and_Entity_Guide.md`
- `07_RBAC_Security_and_Governance.md`
- `08_Audit_Traceability_and_Observability.md`
- `09_Celery_Background_Jobs_and_Async_Flows.md`
- `10_Integrations_and_External_Dependencies.md`
- `11_Documentation_Gap_Assessment.md`
- `12_Open_Questions_and_Validation_Points.md`

---

## N. Risks, Ambiguities, and Confidence

| Area | Confidence | Notes |
|------|-----------|-------|
| Core models and relationships | High | Read from source |
| Agent classes and orchestration | High | Full code read |
| RBAC model | High | rbac_models.py verified |
| Audit model | High | auditlog/models.py verified |
| Celery tasks | High | All tasks.py files read |
| ERP connectors | Medium | Structure confirmed; connector internals not read |
| Posting workflow states | Medium | Inferred from README + model glimpse |
| Procurement module | Medium | Request, prefill, recommendation, validation, and task flows verified; benchmark path currently confirmed as a compatibility bridge |
| Core_eval learning engine | Medium | Model and services confirmed; business rules not read |
| Test coverage claims (124+) | Unverified | Stated in README; not counted from code |
