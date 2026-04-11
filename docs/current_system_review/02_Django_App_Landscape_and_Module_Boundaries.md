# 02 — Django App Landscape and Module Boundaries

**Generated**: 2026-04-09 | **Method**: Code-first inspection of all app directories  
**Confidence**: High for structure; Medium for internal service boundaries of unread apps

---

## 1. App Inventory

| # | App | Classification | Responsibility |
|---|-----|---------------|---------------|
| 1 | `core` | Platform/Shared | Enums, BaseModel, TimestampMixin, PromptTemplate, middleware, prompt_registry, metrics, utils, logging, health checks, decorators |
| 2 | `accounts` | Auth/RBAC | Custom User, CompanyProfile (tenant), RBAC models (Role, Permission, UserRole, etc.) |
| 3 | `vendors` | Domain | Vendor master data, normalized names, aliases |
| 4 | `documents` | Domain | DocumentUpload, Invoice, InvoiceLineItem, PurchaseOrder, POLineItem, GoodsReceiptNote, GRNLineItem |
| 5 | `extraction` | Domain/Pipeline | 11-stage extraction pipeline, approval gate, credit system, bulk extraction, decision codes |
| 6 | `extraction_core` | Platform | ExtractionRun model, control center views, extraction configuration API |
| 7 | `extraction_configs` | Config | Extraction configuration models and admin |
| 8 | `reconciliation` | Domain/Engine | 14-service matching engine, ReconciliationConfig, ReconciliationPolicy, ReconciliationResult, ReconciliationException, ReconciliationRun |
| 9 | `agents` | Agentic | AgentDefinition, AgentRun, AgentOrchestrationRun, AgentStep, AgentMessage, DecisionLog, AgentRecommendation, AgentEscalation, LLMCostRate + all agent services + SupervisorAgent (skills/, plugins/) |
| 10 | `tools` | Agentic | BaseTool, ToolRegistry, 6 base + 24 supervisor tools, tool call logger |
| 11 | `cases` | Domain | APCase (central object), APCaseStage, APCaseDecision, ReviewAssignment, state machine, CaseOrchestrator, StageExecutor |
| 12 | `copilot` | UI/AI | Conversational copilot service, API and template views |
| 13 | `dashboard` | UI/Analytics | Dashboard views, agent governance service, analytics APIs |
| 14 | `reports` | UI | Report URL stubs (no working export service) |
| 15 | `auditlog` | Governance | AuditEvent (compliance), ProcessingLog (operational), timeline service, governance API |
| 16 | `integrations` | Integration | Registered but purpose unclear; likely general integration utilities |
| 17 | `erp_integration` | Integration | 6 ERP connectors, ERPResolutionService, CacheService, secrets resolver, audit service |
| 18 | `posting` | Domain | Invoice posting workflow, posting views, posting tasks |
| 19 | `posting_core` | Platform | VendorAliasMapping, reference import tables (vendor/item/tax/cost-center) |
| 20 | `procurement` | Domain | Should-cost benchmarking, compliance validation, quotation management |
| 21 | `core_eval` | Platform/AI | Generic evaluation framework: EvalRun, EvalMetric, LearningSignal, LearningAction, learning engine |
| 22 | `reviews` | Stub | INSTALLED_APPS note: "migrations-only stub; models moved to apps.cases" |

**Total registered in INSTALLED_APPS**: 21 (plus `reviews` stub = 22)  
**Note**: `extraction_documents` referenced in migration history (migration 0006 dropped FK constraint before dropping table) but not in INSTALLED_APPS — indicates a superseded app.

---

## 2. App Classifications

### Domain Apps (own business entities and rules)
- `vendors`, `documents`, `extraction`, `reconciliation`, `cases`, `procurement`, `posting`

### Platform/Shared Apps (infrastructure, no unique business domain)
- `core`, `accounts`, `extraction_core`, `extraction_configs`, `posting_core`, `core_eval`

### Agentic Apps (LLM agent framework)
- `agents`, `tools`

### Integration Apps (external system connectors)
- `erp_integration`, `integrations`

### Governance Apps (audit, compliance)
- `auditlog`

### UI Apps (views and templates only)
- `dashboard`, `reports`, `copilot`

### Stub/Legacy
- `reviews` (models moved to `cases`)

---

## 3. Key Cross-App Dependencies

```
core (BaseModel, enums, utils, prompt_registry)
  └── consumed by: ALL apps

accounts (User, CompanyProfile)
  └── consumed by: ALL apps (tenant FK, AUTH_USER_MODEL)

documents (DocumentUpload, Invoice, PO, GRN)
  └── consumed by: extraction, reconciliation, agents, cases, tools, posting

vendors (Vendor)
  └── consumed by: documents, extraction, tools, posting_core

extraction (ExtractionApproval, credit pipeline)
  └── consumed by: cases (triggers case creation on approval)

reconciliation (ReconciliationResult, ReconciliationException)
  └── consumed by: agents (orchestration input), cases, tools

agents (AgentRun, AgentRecommendation, AgentOrchestrationRun)
  └── consumed by: cases (review assignment), dashboard, auditlog

tools (tool registry, BaseTool)
  └── consumed by: agents (base_agent.py imports tool registry)

cases (APCase, ReviewAssignment, APCaseDecision)
  └── consumed by: agents (recommendation overrides reference APCaseDecision), dashboard

auditlog (AuditEvent, ProcessingLog)
  └── consumed by: all apps (log writers)

erp_integration (ERPResolutionService, connector factory)
  └── consumed by: tools (po_lookup, grn_lookup via ERP resolution)

posting_core (VendorAliasMapping)
  └── consumed by: tools (vendor_search uses VendorAliasMapping)

core_eval (EvalRun, EvalMetric, LearningAction)
  └── consumed by: extraction, reconciliation, agents (eval_adapter modules)
```

---

## 4. Module Boundary Notes

### `core` — Shared Foundation
Critical app. Contains:
- `enums.py`: All platform-wide enumerations (AgentType, AgentRunStatus, MatchStatus, CaseStatus, RecommendationType, UserRole, ReconciliationMode, ExceptionType, etc.)
- `models.py`: `BaseModel` (pk, created_at, updated_at, is_active), `TimestampMixin`, `PromptTemplate`
- `middleware.py`: `TenantMiddleware`, `LoginRequiredMiddleware`, `RBACMiddleware`, `RequestTraceMiddleware`
- `prompt_registry.py`: Central prompt loading (Langfuse → DB → hardcoded fallback)
- `decorators.py`: `@observed_service`, `@observed_task` for service tracing
- `evaluation_constants.py`: All Langfuse score keys (e.g. EXTRACTION_CONFIDENCE, CASE_PROCESSING_SUCCESS)

### `documents` — Document Domain
Central data model source. All invoice/PO/GRN FKs across the platform point here.
- Invoice has both raw (extracted as-is) and normalized fields
- `extraction_raw_json` stores the full LLM extraction output for reference
- `DocumentUpload.blob_*` fields manage Azure Blob lifecycle

### `extraction` — Pipeline Orchestration
Contains 20 service files. Key services:
- `extraction_adapter.py`: Main pipeline coordinator
- `invoice_prompt_composer.py`: Modular prompt composition
- `response_repair_service.py`: 5 deterministic pre-parser rules
- `parser_service.py`: JSON → domain objects
- `normalization_service.py`: Dates, amounts, PO numbers
- `validation_service.py`: Field validation + decision codes
- `duplicate_detection_service.py`: Invoice number deduplication
- `confidence_scorer.py`: Field-level confidence
- `approval_service.py`: Extraction approval gate

### `reconciliation` — Matching Engine
Contains 14 service files in `services/`. Key services:
- `runner_service.py`: Batch entry point for reconciliation runs
- `mode_resolver.py`: 3-tier cascade (policy → heuristic → config)
- `po_lookup_service.py`: PO resolution (ERP → mirror DB)
- `header_match_service.py`: Vendor, currency, total amount matching
- `line_match_service.py`: Line-level quantity/price/amount matching
- `grn_match_service.py`: GRN quantity matching (3-way only)
- `tolerance_engine.py`: Strict band + auto-close band classification
- `classification_service.py`: MATCHED / PARTIAL / UNMATCHED
- `exception_builder_service.py`: Structured exception creation
- `agent_feedback_service.py`: Atomic re-reconciliation after agent PO recovery

### `agents` — Agentic Layer
Contains 16 service files. Clear separation:
- `agent_classes.py`: 8 concrete LLM agent classes + `AGENT_CLASS_REGISTRY`
- `system_agent_classes.py`: 5 deterministic system agents
- `base_agent.py`: `BaseAgent`, `AgentContext`, `AgentOutput`, ReAct loop
- `orchestrator.py`: `AgentOrchestrator.execute()`, sequence control
- `policy_engine.py`: Deterministic agent selection rules
- `reasoning_planner.py`: Optional LLM-backed planning
- `guardrails_service.py`: RBAC enforcement for all agent actions
- `llm_client.py`: LLM abstraction (Azure OpenAI + Langfuse metadata)
- `agent_memory.py`: In-memory `AgentMemory` object passed between agents in sequence
- `decision_log_service.py`: `DecisionLogService` for persisting decisions
- `agent_trace_service.py`: Observability helpers for agent execution

### `cases` — Case Lifecycle
Central business orchestration app. Contains:
- `models.py`: APCase, APCaseStage, APCaseDecision, APCaseArtifact, ReviewAssignment, ReviewAction
- `state_machine/case_state_machine.py`: CASE_TRANSITIONS list (from, to, allowed_trigger_types)
- `orchestrators/case_orchestrator.py`: `CaseOrchestrator.run()` drives the state machine
- `orchestrators/stage_executor.py`: Executes individual stages
- `services/`: Activity, assignment, creation, routing, summary, review workflow services

### `erp_integration` — ERP Connectivity
- `connector_factory.py`: `ConnectorFactory.get_connector()` returns the right connector
- `cache_service.py`: L1/L2/L3 caching with freshness TTLs
- `resolution_service.py`: `ERPResolutionService` — unified resolution API
- `services/resolution/`: Per-entity resolvers (PO, GRN, vendor, item, tax, cost_center)
- `services/connectors/`: 6 connector implementations

### `core_eval` — Evaluation Framework
Domain-agnostic. Consumed by other apps via `eval_adapter.py` modules:
- `extraction/services/eval_adapter.py` → wires extraction to core_eval
- `reconciliation/services/eval_adapter.py` → wires reconciliation to core_eval
- `agents/services/eval_adapter.py` → wires agents to core_eval
- Beat task: `process_approved_learning_actions` (every 30 min)

---

## 5. Overlap / Dead Code / Deprecation Notes

| Issue | Location | Severity |
|-------|----------|---------|
| `reviews` app is a stub | `apps/reviews/`, INSTALLED_APPS comment | Low (migration compatibility only) |
| `extraction_documents` app dropped | Migration 0006 in extraction_core | Low (historical migration) |
| Duplicate agent concept: LLM vs system agents | `agent_classes.py` + `system_agent_classes.py` | Medium (naming ambiguity for new developers) |
| `integrations` app: unclear purpose | `apps/integrations/` | Unknown — needs inspection |
| Line match LLM fallback | `reconciliation/services/line_match_llm_fallback.py` | Needs verification if used in production |

---

## 6. Module-Boundary Risks

| Risk | Description | Recommendation |
|------|-------------|---------------|
| Tools coupling | `tools/registry/tools.py` directly imports from `documents`, `vendors`, `reconciliation`, `posting_core`, `erp_integration` | Acceptable — tools are data-access adapters by design |
| Reconciliation ↔ Agents | `reconciliation.tasks` directly dispatches `run_agent_pipeline_task` | Tight coupling; acceptable for now |
| Cases ↔ Agents | `AgentRecommendation.overridden_by_decision` FK to `cases.APCaseDecision` | Bidirectional coupling; manageable |
| Core_eval universality | Each consuming app has its own `eval_adapter.py` — consistent but verbose | Consider a universal adapter factory |
