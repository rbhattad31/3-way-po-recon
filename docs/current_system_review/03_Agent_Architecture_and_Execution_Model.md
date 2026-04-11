# 03 — Agent Architecture and Execution Model

**Generated**: 2026-04-09 | **Method**: Code-first inspection of `apps/agents/` and related tasks  
**Evidence files**: `agent_classes.py`, `base_agent.py`, `orchestrator.py`, `policy_engine.py`, `guardrails_service.py`, `system_agent_classes.py`, `agents/tasks.py`

---

## 1. Agent Inventory

### LLM Agents (9 — in `AGENT_CLASS_REGISTRY`, `apps/agents/services/agent_classes.py`)

| Agent | `AgentType` | System Prompt Key | Allowed Tools | Primary Purpose |
|-------|------------|-------------------|--------------|----------------|
| InvoiceExtractionAgent | `INVOICE_EXTRACTION` | `extraction.invoice_system` | None | Single-shot GPT-4o extraction, temperature=0 |
| InvoiceUnderstandingAgent | `INVOICE_UNDERSTANDING` | `agent.invoice_understanding` | invoice_details, po_lookup, vendor_search | Resolve extraction ambiguity / quality issues |
| PORetrievalAgent | `PO_RETRIEVAL` | `agent.po_retrieval` | po_lookup, vendor_search, invoice_details | Find PO when deterministic lookup failed |
| GRNRetrievalAgent | `GRN_RETRIEVAL` | `agent.grn_retrieval` | grn_lookup, po_lookup, invoice_details | Investigate missing/partial GRN data (3-way only) |
| ExceptionAnalysisAgent | `EXCEPTION_ANALYSIS` | `agent.exception_analysis` | po_lookup, grn_lookup, invoice_details, exception_list, reconciliation_summary | Root cause exceptions + reviewer summary (2nd LLM call) |
| ReviewRoutingAgent | `REVIEW_ROUTING` | `agent.review_routing` | reconciliation_summary, exception_list | Select review queue, priority, assignee role |
| CaseSummaryAgent | `CASE_SUMMARY` | `agent.case_summary` | All 5 business tools | Generate reviewer-facing case summary |
| ReconciliationAssistAgent | `RECONCILIATION_ASSIST` | `agent.reconciliation_assist` | All 5 business tools | General-purpose reconciliation advisor |
| **SupervisorAgent** | `SUPERVISOR` | `agent.supervisor_ap_lifecycle` | 30 tools (24 supervisor + 6 base) | Full AP lifecycle orchestrator; 5-phase non-linear processing with skill-based composition. See [17_Supervisor_Agent_Architecture.md](17_Supervisor_Agent_Architecture.md) |

### System Agents (5 — deterministic, `apps/agents/services/system_agent_classes.py`)

| Agent | `AgentType` | Purpose |
|-------|------------|---------|
| SystemCaseIntakeAgent | `SYSTEM_CASE_INTAKE` | Governance-visible case intake record; runs at start of every case processing |
| SystemReviewRoutingAgent | `SYSTEM_REVIEW_ROUTING` | Deterministic review assignment (replaces LLM ReviewRoutingAgent in tail) |
| SystemCaseSummaryAgent | `SYSTEM_CASE_SUMMARY` | Deterministic case summary (replaces LLM CaseSummaryAgent in tail) |
| SystemBulkExtractionIntakeAgent | `SYSTEM_BULK_EXTRACTION_INTAKE` | Tracks bulk upload lifecycle |
| SystemPostingPreparationAgent | `SYSTEM_POSTING_PREPARATION` | Records posting workflow initiation |

---

## 2. Orchestrators / Runners / Registries

### AgentOrchestrator (`apps/agents/services/orchestrator.py`)

The primary orchestration entry point. Called from `run_agent_pipeline_task` Celery task.

```
AgentOrchestrator.execute(reconciliation_result, request_user, tenant)
  ├── AgentGuardrailsService.check_orchestrate_permission()       # RBAC gate
  ├── AgentOrchestrationRun.objects.create()                       # persistence record
  ├── PolicyEngine.get_agent_plan(result, exceptions)              # determine agents to run
  │    └── OR ReasoningPlanner.plan() if AGENT_REASONING_ENGINE_ENABLED=true
  ├── For each agent in plan:
  │    ├── AgentGuardrailsService.check_agent_permission()         # per-agent RBAC
  │    ├── AgentClass = AGENT_CLASS_REGISTRY[agent_type]
  │    ├── agent.run(ctx)                                           # ReAct loop
  │    ├── Record AgentRun, AgentStep, AgentMessage, DecisionLog
  │    ├── AgentMemory.record_agent_output()                        # pass findings forward
  │    ├── _DeterministicResolver.check_for_feedback_loop()        # PO feedback?
  │    └── If PO_RETRIEVAL found PO → re-reconcile atomically
  ├── Persist AgentRecommendation (for recommending agents)
  └── Update AgentOrchestrationRun (final_recommendation, status)
```

**Recommending agents** (only these emit formal `AgentRecommendation`):  
`REVIEW_ROUTING`, `CASE_SUMMARY`, `SYSTEM_REVIEW_ROUTING`, `SYSTEM_CASE_SUMMARY`

**System agent replacements**: In tail position, LLM agents are replaced by deterministic system agents:
- `REVIEW_ROUTING` → `SYSTEM_REVIEW_ROUTING`
- `CASE_SUMMARY` → `SYSTEM_CASE_SUMMARY`

### AGENT_CLASS_REGISTRY

```python
# agent_classes.py
AGENT_CLASS_REGISTRY: Dict[str, type] = {
    AgentType.INVOICE_EXTRACTION:    InvoiceExtractionAgent,
    AgentType.EXCEPTION_ANALYSIS:    ExceptionAnalysisAgent,
    AgentType.INVOICE_UNDERSTANDING: InvoiceUnderstandingAgent,
    AgentType.PO_RETRIEVAL:          PORetrievalAgent,
    AgentType.GRN_RETRIEVAL:         GRNRetrievalAgent,
    AgentType.REVIEW_ROUTING:        ReviewRoutingAgent,
    AgentType.CASE_SUMMARY:          CaseSummaryAgent,
    AgentType.RECONCILIATION_ASSIST: ReconciliationAssistAgent,
    AgentType.SUPERVISOR:            SupervisorAgent,
    # + 5 system agents merged in at import time
}
```

### PolicyEngine (`apps/agents/services/policy_engine.py`)

Deterministic rule-based plan selector. Inputs: `ReconciliationResult`, exceptions list, reconciliation mode. Outputs: ordered list of `AgentType` values.

**Key rules** (inferred from agent design):
- `PO_RETRIEVAL` → if PO lookup failed
- `GRN_RETRIEVAL` → if 3-way mode and GRN missing/incomplete
- `EXCEPTION_ANALYSIS` → if exceptions present
- `INVOICE_UNDERSTANDING` → if extraction confidence low
- `REVIEW_ROUTING` → always if case needs review
- `CASE_SUMMARY` → always as final agent

### ReasoningPlanner (`apps/agents/services/reasoning_planner.py`)

Optional LLM-backed planner. Activated by `AGENT_REASONING_ENGINE_ENABLED=true`.  
Returns the same ordered agent list as `PolicyEngine` but using an LLM to reason about the best sequence.  
**Status**: Available, not enabled by default. Not mentioned in README as active.

---

## 3. Invocation Paths

```
Path A — Reconciliation auto-trigger:
  run_reconciliation_task (Celery)
    → per non-MATCHED ReconciliationResult
    → dispatch_task(run_agent_pipeline_task, tenant_id, result_id, actor_id)

Path B — Manual trigger (API/UI):
  POST /api/v1/cases/<id>/run-agents/  (inferred from agents/views.py)
    → run_agent_pipeline_task.delay(...)

Path C — Extraction (InvoiceExtractionAgent only):
  run_extraction_task (Celery)
    → InvoiceExtractionAgent().run(ctx)  # called directly, not via orchestrator

Path D — Case pipeline (system agents):
  process_case_task (Celery)
    → SystemCaseIntakeAgent().run(ctx)  # called directly before CaseOrchestrator
    → CaseOrchestrator.run()
        → StageExecutor → may call SystemReviewRoutingAgent, SystemCaseSummaryAgent

Path E — Supervisor (single-agent lifecycle):
  SupervisorAgent(skill_names=DEFAULT_SKILLS).run(ctx)
    → build_supervisor_context(invoice_id, mode, tenant, ...)
    → ReAct loop with 30 tools, max 15 rounds
    → 5-phase non-linear: UNDERSTAND → VALIDATE → MATCH → INVESTIGATE → DECIDE
    → submit_recommendation → AgentOutput
```

---

## 4. Execution Lifecycle (Single Agent Run)

```
BaseAgent.run(ctx: AgentContext)
  1. Load AgentDefinition from DB (enabled=True, agent_type match)
  2. Create AgentRun record (status=RUNNING, RBAC metadata, trace_id)
  3. _init_messages(ctx, agent_run)
     └── Resolve system prompt (composed_prompt from ctx.extra OR PromptRegistry)
     └── Build user message via build_user_message(ctx)
     └── Persist AgentMessage records (system + user)
  4. Open Langfuse trace/span (fail-silent)
  5. ReAct loop (max iterations from AgentDefinition.max_retries or default):
     a. LLMClient.chat(messages, tools=available_tools_schema)
     b. If tool_calls in response:
        i.  AgentGuardrailsService.check_tool_permission(tool_name, actor)
        ii. tool.run(**args) → ToolResult
        iii. AgentStep persisted (action, input, output, duration_ms)
        iv. Append tool result to messages
     c. If text response (no more tool_calls):
        → interpret_response(content, ctx) → AgentOutput
        → break loop
  6. _finalise_run(agent_run, output, start):
     - AgentRun.status = COMPLETED / FAILED
     - AgentRun.output_payload = {recommendation_type, confidence, evidence, decisions}
     - AgentRun.summarized_reasoning = output.reasoning[:500]
     - AgentRun.confidence = output.confidence
     - AgentRun.duration_ms, completed_at
     - AgentRun.prompt_tokens, completion_tokens, actual_cost_usd
  7. Close Langfuse span
  8. Return AgentRun
```

### ExceptionAnalysisAgent special behavior
After the standard ReAct loop, makes a **second LLM call** to generate a structured `reviewer_summary` JSON, which is persisted on the `ReviewAssignment` record so human reviewers see it immediately.

---

## 5. AgentContext

```python
@dataclass
class AgentContext:
    reconciliation_result: Optional[ReconciliationResult]
    invoice_id: int = 0
    po_number: str = ""
    exceptions: List[dict] = []
    reconciliation_mode: str = ""
    document_upload_id: int = 0
    extra: dict = {}
    memory: Optional[AgentMemory] = None
    # RBAC
    actor_user_id: Optional[int] = None
    actor_primary_role: str = ""
    actor_roles_snapshot: List[str] = []
    permission_checked: str = ""
    permission_source: str = ""
    access_granted: bool = False
    # Observability
    trace_id: str = ""
    span_id: str = ""
    tenant: Optional[CompanyProfile] = None
    _langfuse_trace: Any = None
```

`AgentMemory` is passed between agents in the orchestration sequence to share findings:
- `agent_summaries`: dict of agent_type → reasoning summary
- `current_recommendation`, `current_confidence`
- `resolved_po_number`: set by PORetrievalAgent if PO found
- `facts`: arbitrary key-value store for inter-agent communication

---

## 6. Deterministic vs LLM Boundary

| Component | Type | Notes |
|-----------|------|-------|
| Extraction response repair (5 rules) | Deterministic | Pre-parser, always runs |
| PO lookup (po_lookup_service) | Deterministic | DB/ERP lookup with normalization |
| Mode resolution (ModeResolver) | Deterministic | 3-tier cascade |
| Header matching | Deterministic | Fuzzy string + numeric tolerance |
| Line matching | Deterministic | thefuzz/RapidFuzz, tolerance bands |
| GRN matching | Deterministic | Quantity comparison |
| Exception building | Deterministic | Rule-based exception type assignment |
| Auto-close decision | Deterministic | Tolerance band check |
| PolicyEngine plan | Deterministic | Rules-based agent sequence selection |
| InvoiceExtractionAgent | LLM (GPT-4o) | Single-shot, temperature=0 |
| All other LLM agents | LLM (GPT-4o) | ReAct loop, temperature=0.1 |
| ReasoningPlanner | LLM (GPT-4o) | Optional; off by default |
| SupervisorAgent | LLM (GPT-4o) | Full lifecycle; 15 tool rounds, skill-composed prompt |
| SystemReviewRoutingAgent | Deterministic | Rule-based, no LLM |
| SystemCaseSummaryAgent | Deterministic | Rule-based, no LLM |

**Design principle**: Deterministic engine is the primary path; LLM agents only invoked for non-trivially-resolved exceptions.

---

## 7. Outputs / Recommendations Persistence

| Model | Purpose | Key Fields |
|-------|---------|-----------|
| `AgentRun` | One execution of one agent | status, output_payload, confidence, summarized_reasoning, RBAC snapshot, token counts, cost |
| `AgentOrchestrationRun` | Top-level pipeline invocation | planned_agents, executed_agents, final_recommendation, plan_source |
| `AgentStep` | Sub-step within a run (tool call or reasoning step) | action, input_data, output_data, success, duration_ms |
| `AgentMessage` | Chat-style message log | role (system/user/assistant/tool), content, token_count |
| `AgentRecommendation` | Formal recommendation from recommending agents | recommendation_type, confidence, reasoning, evidence, accepted, overridden_by_decision |
| `AgentEscalation` | Escalation when confidence < threshold | severity, reason, suggested_assignee_role, resolved |
| `DecisionLog` | Key decisions for audit | decision_type, decision, rationale, confidence, deterministic_flag, rule/policy/prompt trace |

---

## 8. Evidence / Confidence / Status Handling

- `AgentRun.confidence`: float [0.0, 1.0] — clamped in `interpret_response()`
- Invalid `recommendation_type` → fallback to `SEND_TO_AP_REVIEW` with confidence capped at 0.6
- Tool failure → confidence cap applied (configurable per `AgentDefinition.tool_failure_confidence_cap`)
- `requires_tool_grounding=True` on AgentDefinition → recommendation suppressed if no tool succeeded

---

## 9. Incomplete / Risky Areas

| Area | Risk | Notes |
|------|------|-------|
| ReasoningPlanner | Untested in production | Hidden behind env flag; no tests visible in README stats |
| `line_match_llm_fallback.py` | Unknown activation | Service file exists; whether called in production path unclear |
| `agent_memory.py` | In-memory only | AgentMemory not persisted separately — loss on task restart |
| GRNRetrievalAgent in 2-way mode | Guard: returns early JSON | Mode check in `build_user_message`; orchestrator should also suppress via PolicyEngine |
| ExceptionAnalysisAgent second LLM call | Non-fatal failure | If reviewer summary fails, main AgentRun is unaffected but reviewer sees no summary |
| `AgentDefinition.prohibited_actions` | Not enforced in code | JSON field exists on model; enforcement code not verified |
