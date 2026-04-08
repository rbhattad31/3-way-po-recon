# Agent Architecture -- Developer Guide

> 3-Way PO Reconciliation Platform -- Agentic Layer Reference

---

## Table of Contents

1. [Overview](#1-overview)
2. [Core Runtime Model](#2-core-runtime-model)
3. [Orchestration Flow](#3-orchestration-flow)
4. [Agent Catalog and Contract Model](#4-agent-catalog-and-contract-model)
5. [Tool System and Authoring Standard](#5-tool-system-and-authoring-standard)
6. [Prompting and Output Contracts](#6-prompting-and-output-contracts)
7. [Concrete Agents](#7-concrete-agents)
8. [Governance and RBAC](#8-governance-and-rbac)
9. [Observability and Audit](#9-observability-and-audit)
10. [Extension Guide](#10-extension-guide)
11. [Remaining Gaps and Roadmap](#11-remaining-gaps-and-roadmap)

---

## 1. Overview

### 1.1 Purpose

The agentic layer sits **after** the deterministic reconciliation engine. It
is invoked only when the matching result is not a clean MATCHED (or an
auto-closeable PARTIAL_MATCH). Its job is to:

1. Understand why a match failed.
2. Attempt to recover missing documents (PO, GRN).
3. Classify exceptions and decide where to route the case.
4. Produce a human-readable summary for the reviewer.

### 1.2 High-Level Flow

```
ReconciliationResult (PARTIAL_MATCH | UNMATCHED | REQUIRES_REVIEW)
        |
        v
  ReasoningPlanner.plan()           -- LLM plan with PolicyEngine fallback
        |
        +--> skip_agents=True  -->  auto-close by tolerance band (no AI)
        |
        +--> agents=[...] list
                |
                v
  AgentOrchestrator.execute()
        |
        +--> llm_agents (PO_RETRIEVAL, GRN_RETRIEVAL, INVOICE_UNDERSTANDING,
        |                EXCEPTION_ANALYSIS, RECONCILIATION_ASSIST)
        |        |
        |        v
        |    BaseAgent.run()         -- ReAct loop (LLM + tools)
        |        |
        |        +--> feedback loop if PO found --> re-reconcile
        |        |
        |        +--> _reflect()     -- may insert additional agents
        |
        +--> deterministic_tail (REVIEW_ROUTING, CASE_SUMMARY)
                 |
                 v
             DeterministicResolver.resolve()  -- rule-based, no LLM
        |
        v
  _apply_post_policies()
        +--> should_auto_close()   --> mark MATCHED (no human needed)
        +--> should_escalate()     --> create AgentEscalation record
```

### 1.3 Key Design Principles

**Deterministic-first, agentic where needed.** The reconciliation engine and
`DeterministicResolver` handle all standard cases. LLM agents run only for
genuine uncertainty. A rule-based `PolicyEngine` always exists as a fallback
if the LLM planner fails.

**System agent pattern.** Five `DeterministicSystemAgent` subclasses wrap
platform-level deterministic capabilities (review routing, case summary, bulk
intake, case intake, posting preparation) in the standard agent framework --
producing `AgentRun`, `DecisionLog`, and Langfuse traces without LLM calls,
tool-calling loops, or artificial chat messages. They override `run()` and
implement `execute_deterministic(ctx)` instead of the ReAct loop.

**Governance-first.** Every agent run, tool call, and decision is persisted
in `AgentRun`, `AgentStep`, `DecisionLog`, and `AuditEvent` before any
downstream action. The audit trail is identical for LLM and deterministic
runs -- `AgentRun` records from deterministic agents use
`llm_model_used="deterministic"` and zero token counts.

**Fail-closed RBAC.** No agent action (plan, run, tool call, recommendation,
auto-close, escalation) executes without a passing RBAC check via
`AgentGuardrailsService`. Unknown actions are denied, not defaulted.

**ASCII-only stored output.** All LLM-generated text written to the database
(`AgentRun.summarized_reasoning`, `ReconciliationResult.summary`,
`ReviewAssignment.reviewer_summary`, `DecisionLog.rationale`) must pass
through `BaseAgent._sanitise_text()` before saving.

---

## 2. Core Runtime Model

### 2.1 AgentContext

Immutable bag passed into every agent run. Populated by the orchestrator.

```python
@dataclass
class AgentContext:
    reconciliation_result: Optional[ReconciliationResult]
    invoice_id: int
    po_number: Optional[str]
    exceptions: List[Dict]           # from result.exceptions.values()
    extra: Dict[str, Any]            # vendor_name, total_amount, grn_available, etc.
    reconciliation_mode: str         # "TWO_WAY" | "THREE_WAY"
    # RBAC fields (set by guardrails service)
    actor_user_id: Optional[int]
    actor_primary_role: str
    actor_roles_snapshot: List[str]
    permission_checked: str
    permission_source: str
    access_granted: bool
    trace_id: str
    span_id: str
    # Structured cross-agent memory (None for legacy callers)
    memory: Optional[AgentMemory] = None
```

### 2.2 AgentMemory

Created by the orchestrator at pipeline start. Accumulates findings so later
agents have structured access to what earlier agents discovered.

```python
@dataclass
class AgentMemory:
    resolved_po_number: Optional[str] = None
    resolved_grn_numbers: List[str] = field(default_factory=list)
    extraction_issues: List[str] = field(default_factory=list)
    agent_summaries: Dict[str, str] = field(default_factory=dict)
    current_recommendation: Optional[str] = None
    current_confidence: float = 0.0
    facts: Dict[str, Any] = field(default_factory=dict)

    def record_agent_output(self, agent_type: str, output) -> None:
        self.agent_summaries[agent_type] = output.reasoning[:500]
        if output.recommendation_type:
            if output.confidence > self.current_confidence:
                self.current_recommendation = output.recommendation_type
                self.current_confidence = output.confidence
        evidence = output.evidence or {}
        if evidence.get("found_po"):
            self.resolved_po_number = evidence["found_po"]
```

**Facts pre-seeded by the orchestrator before any agent runs:**

| Key | Value | Consumer |
|---|---|---|
| `facts["grn_available"]` | `bool(result.grn_available)` | `GRNRetrievalAgent`, post-feedback refresh |
| `facts["grn_fully_received"]` | `bool(result.grn_fully_received)` | `GRNRetrievalAgent`, post-feedback refresh |
| `facts["is_two_way"]` | `ctx.reconciliation_mode == "TWO_WAY"` | Mode-aware agents |
| `facts["vendor_name"]` | `result.vendor_name or ""` | Prompt context enrichment |
| `facts["match_status"]` | `str(result.match_status or "")` | `InvoiceUnderstandingAgent`, `ReconciliationAssistAgent` |

**What each agent reads from `ctx.memory`:**

| Agent | Fields read |
|---|---|
| `ReviewRoutingAgent` | `agent_summaries`, `current_recommendation`, `current_confidence` |
| `CaseSummaryAgent` | `agent_summaries` (pipe-joined, 100 chars each), `current_recommendation`, `current_confidence` |
| `ReconciliationAssistAgent` | `resolved_po_number` (appended to prompt when set) |
| `GRNRetrievalAgent` | `facts["grn_available"]`, `facts["grn_fully_received"]` |
| `InvoiceUnderstandingAgent` | `facts["extraction_confidence"]`, `facts["match_status"]`, `facts["validation_warnings"]` |

### 2.3 AgentOutput

What `interpret_response()` returns after the ReAct loop finishes.

```python
@dataclass
class AgentOutput:
    reasoning: str
    recommendation_type: Optional[str]   # RecommendationType enum value or None
    confidence: float                    # 0.0-1.0
    evidence: Dict[str, Any]
    decisions: List[Dict]                # [{decision, rationale, confidence}]
    raw_content: str
```

### 2.4 AgentPlan

What `ReasoningPlanner.plan()` returns.

```python
@dataclass
class AgentPlan:
    agents: List[str]            # ordered AgentType values to run
    reason: str
    skip_agents: bool            # True -> skip all agents
    auto_close: bool             # True -> auto-close the result
    reconciliation_mode: str
    plan_source: str = "deterministic"   # "deterministic" or "llm"
    plan_confidence: float = 0.0         # planner self-reported confidence
```

### 2.5 OrchestrationResult

Aggregated outcome returned by `AgentOrchestrator.execute()`.

```python
@dataclass
class OrchestrationResult:
    reconciliation_result_id: int
    agents_executed: List[str]
    agent_runs: List[AgentRun]
    final_recommendation: Optional[str]
    final_confidence: float
    final_reasoning: str
    skipped: bool
    skip_reason: str
    error: str
    plan_source: str = ""
    plan_confidence: float = 0.0
```

### 2.6 AgentOrchestrationRun (DB Model)

Persisted state record for one full invocation of `AgentOrchestrator.execute()`.
Created before any agent runs; updated incrementally as agents complete.
Serves three purposes: duplicate-run protection, visibility into incomplete
pipelines, and foundation for future partial resume.

```python
class AgentOrchestrationRun(BaseModel):
    class Status(models.TextChoices):
        PLANNED   = "PLANNED"    # not yet started
        RUNNING   = "RUNNING"    # pipeline is executing
        COMPLETED = "COMPLETED"  # all agents finished
        PARTIAL   = "PARTIAL"    # some agents failed, pipeline reached the end
        FAILED    = "FAILED"     # pipeline crashed before completing

    reconciliation_result        # FK -> ReconciliationResult
    status                       # see Status above
    plan_source                  # "deterministic" or "llm"
    plan_confidence              # float 0-1 from planner
    planned_agents               # JSON list of agent types from planner
    executed_agents              # JSON list updated after each agent finishes
    final_recommendation
    final_confidence
    skip_reason
    error_message
    actor_user_id
    trace_id
    started_at / completed_at / duration_ms
```

**State machine:**

```
PLANNED --[execute() called]--> RUNNING
                                   |
                                   +-- all agents done, no error -------> COMPLETED (terminal)
                                   +-- all agents done, some errors ----> PARTIAL  (terminal)
                                   +-- unhandled exception -------------> FAILED   (terminal)
                                   +-- skip_agents=True ----------------> COMPLETED (no agents run)

Duplicate guard:
  RUNNING already exists for same reconciliation_result
    -> new execute() call returns early (orch_result.skipped=True)
    -> no new record created, no agents run
```

**Stale RUNNING records:** A worker crash leaves a `RUNNING` record blocking
all retries. Set `status=FAILED` via Django admin or the governance API.
Monitor for records where `started_at < now() - 10 min AND status=RUNNING`.

---

## 3. Orchestration Flow

### 3.1 The AgentOrchestrator

`AgentOrchestrator.execute(result, request_user)` is the single public entry
point for the entire agentic pipeline.

**Execution sequence:**

```
1.  resolve_actor(request_user)             -- user or SYSTEM_AGENT
2.  authorize_orchestration(actor)          -- agents.orchestrate permission
3.  authorize_data_scope(actor, result)     -- business-unit + vendor scope check
4.  plan = ReasoningPlanner.plan(result)    -- LLM plan, PolicyEngine fallback
4a. if plan.skip_agents:
        create AgentOrchestrationRun(COMPLETED, skip_reason=plan.reason)
        if plan.auto_close: mark result MATCHED
        return
4b. duplicate-run guard: if RUNNING record exists for result -> return early
5.  create AgentOrchestrationRun(RUNNING, planned_agents=plan.agents)
6.  partition plan.agents:
        llm_agents   = plan.agents - REPLACED_AGENTS
        det_tail     = plan.agents & REPLACED_AGENTS
7.  build AgentContext (exceptions, extra, RBAC fields, trace IDs)
        ctx.exceptions = _truncate_exceptions(exceptions, max=20)
8.  ctx.memory = AgentMemory()
    ctx.memory.facts pre-seeded: grn_available, grn_fully_received, is_two_way, vendor_name, match_status
9.  for agent_type in llm_agents:
        a. authorize_agent(actor, agent_type)
        b. agent_cls().run(ctx)
        c. save executed_agents to AgentOrchestrationRun
        d. memory.record_agent_output(agent_type, ...)
        e. if EXCEPTION_ANALYSIS: write reviewer_summary to ReviewAssignment
        f. if agent in _RECOMMENDING_AGENTS: log_recommendation() with IntegrityError guard
        g. if agent in _FEEDBACK_AGENTS: _apply_agent_findings() -> re-reconcile
           refresh ctx.po_number, ctx.exceptions, ctx.memory fields
        h. _reflect(agent_type, ...) -> may insert additional agents
10. _apply_deterministic_resolution()       -- REVIEW_ROUTING, CASE_SUMMARY
11. _resolve_final_recommendation()         -- highest-confidence AgentRecommendation
12. _apply_post_policies():
        should_auto_close() -> result.match_status = MATCHED
        should_escalate()   -> create AgentEscalation
13. finalize AgentOrchestrationRun:
        status = PARTIAL if orch_result.error else COMPLETED
        final_recommendation, final_confidence, completed_at -> save()
```

### 3.2 ReasoningPlanner (Always Active)

`ReasoningPlanner` makes a single LLM call to produce a structured execution
plan. If the LLM call fails for any reason, `PolicyEngine` is the internal
deterministic fallback.

```python
class ReasoningPlanner:
    def plan(self, result) -> AgentPlan:
        quick_plan = self._fallback.plan(result)    # PolicyEngine
        if quick_plan.skip_agents:
            return quick_plan
        try:
            return self._llm_plan(result)   # stamps plan_source="llm", plan_confidence=float
        except Exception:
            logger.warning("LLM plan failed; falling back to deterministic plan.")
            return quick_plan               # plan_source="deterministic"
```

**LLM plan validation guards:**
1. Empty plan -> raise (fallback to deterministic).
2. `CASE_SUMMARY` not last if present -> raise.
3. `GRN_RETRIEVAL` in a TWO_WAY plan -> raise.

**Post-policy delegation:** `ReasoningPlanner` exposes `should_auto_close()`
and `should_escalate()` by delegating to `PolicyEngine`, so the orchestrator
always calls these through the same interface regardless of which planner ran.

### 3.3 PolicyEngine Decision Matrix

`PolicyEngine.plan()` is the deterministic plan that `ReasoningPlanner` falls
back to. It is also the authoritative source for the auto-close logic.

| Condition | Agents Planned |
|---|---|
| MATCHED, confidence >= threshold | `skip_agents=True` |
| PARTIAL_MATCH, all lines within auto-close band, no HIGH exceptions | `skip_agents=True, auto_close=True` |
| PO_NOT_FOUND exception | + PO_RETRIEVAL |
| GRN_NOT_FOUND exception (3-way only) | + GRN_RETRIEVAL |
| extraction confidence < threshold | + INVOICE_UNDERSTANDING |
| PARTIAL_MATCH outside auto-close band | + RECONCILIATION_ASSIST |
| any exceptions exist | + EXCEPTION_ANALYSIS |
| any agents planned | + REVIEW_ROUTING + CASE_SUMMARY (always appended) |
| REQUIRES_REVIEW / UNMATCHED / ERROR, no specific trigger | EXCEPTION_ANALYSIS + REVIEW_ROUTING + CASE_SUMMARY |

**Auto-close tolerance band:** `_within_auto_close_band()` checks every line
result against wider thresholds (default: qty 5%, price 3%, amount 3%).
If all lines pass and no HIGH-severity exceptions exist, the result is
auto-closed without any LLM agents.

### 3.4 DeterministicResolver

Replaces three LLM agents (`EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`,
`CASE_SUMMARY`) with rule-based logic, producing identical `AgentRun` output
structures. These agents are listed in `DeterministicResolver.REPLACED_AGENTS`.

**Rule priority (highest first):**

| Priority | Condition | Recommendation |
|---|---|---|
| 0 | Prior agent recommended AUTO_CLOSE with confidence >= 0.80 | `AUTO_CLOSE` |
| 1 | `EXTRACTION_LOW_CONFIDENCE` exception | `REPROCESS_EXTRACTION` |
| 2 | `VENDOR_MISMATCH` exception | `SEND_TO_VENDOR_CLARIFICATION` |
| 3 | GRN/receipt exception types | `SEND_TO_PROCUREMENT` |
| 4 | 3+ independent issue categories AND HIGH severity | `ESCALATE_TO_MANAGER` |
| 5 | Default | `SEND_TO_AP_REVIEW` |

Numeric mismatches (`QTY_MISMATCH`, `PRICE_MISMATCH`, `AMOUNT_MISMATCH`,
`TAX_MISMATCH`) are collapsed to one category for complexity assessment to
avoid false escalation from natural cascading.

Deterministic runs use `llm_model_used="deterministic"` and zero token counts.

### 3.5 DeterministicSystemAgent (Base Class)

**File:** `apps/agents/services/deterministic_system_agent.py`

Abstract base class for system agents that skip the ReAct loop entirely.
Subclasses implement `execute_deterministic(ctx) -> AgentOutput` which runs
pure deterministic logic. The overridden `run()` method:

1. Creates an `AgentRun` with `llm_model_used="deterministic"`, zero token counts, and full RBAC metadata.
2. Calls `execute_deterministic(ctx)` -- the subclass hook.
3. Persists `DecisionLog` records from `output.decisions`.
4. Emits Langfuse spans with `SYSTEM_AGENT_SUCCESS` and `SYSTEM_AGENT_DECISION_COUNT` scores.
5. Logs `SYSTEM_AGENT_RUN_COMPLETED` or `SYSTEM_AGENT_RUN_FAILED` audit events.

Key differences from `BaseAgent`:
- `__init__` skips `LLMClient` creation (no API key env vars required).
- `system_prompt`, `build_user_message`, `allowed_tools`, `interpret_response` are stub no-ops.
- No ReAct loop, no tool calling, no message history.

### 3.6 Concrete System Agents

**File:** `apps/agents/services/system_agent_classes.py`

Five concrete implementations:

| Class | `agent_type` | Purpose | Wraps |
|---|---|---|---|
| `SystemReviewRoutingAgent` | `SYSTEM_REVIEW_ROUTING` | Route cases to correct review queue | `DeterministicResolver.resolve()` |
| `SystemCaseSummaryAgent` | `SYSTEM_CASE_SUMMARY` | Produce human-readable case summary | `DeterministicResolver.resolve()` |
| `SystemBulkExtractionIntakeAgent` | `SYSTEM_BULK_EXTRACTION_INTAKE` | Record bulk extraction job stats | `ctx.extra` data |
| `SystemCaseIntakeAgent` | `SYSTEM_CASE_INTAKE` | Record case creation/stage init | `ctx.extra` data |
| `SystemPostingPreparationAgent` | `SYSTEM_POSTING_PREPARATION` | Record posting pipeline outcomes | `ctx.extra` data |

All are registered in `AGENT_CLASS_REGISTRY` via `_get_system_agent_classes()` in `agent_classes.py`.

**Orchestrator integration:** The orchestrator maps legacy agent types to system agent types via `_SYSTEM_AGENT_REPLACEMENTS`:
```python
_SYSTEM_AGENT_REPLACEMENTS = {
    AgentType.REVIEW_ROUTING: AgentType.SYSTEM_REVIEW_ROUTING,
    AgentType.CASE_SUMMARY: AgentType.SYSTEM_CASE_SUMMARY,
}
```
In `_apply_deterministic_resolution()`, REVIEW_ROUTING and CASE_SUMMARY are instantiated as their system agent replacements and executed via `agent.run(ctx)` with `prior_recommendation`/`prior_confidence` threaded through `ctx.extra`.

### 3.5 Reflection Step

After each LLM agent, `_reflect()` inspects findings and may insert additional
agents immediately after the current position:

| Trigger | Condition | Agent Inserted |
|---|---|---|
| PO_RETRIEVAL completes | `ctx.memory.resolved_po_number` is set AND mode != TWO_WAY AND GRN_RETRIEVAL not already run or in remaining | `GRN_RETRIEVAL` |
| INVOICE_UNDERSTANDING completes | `agent_run.confidence < 0.5` AND RECONCILIATION_ASSIST not already run or in remaining | `RECONCILIATION_ASSIST` |

`_reflect()` only runs when `last_output.status == AgentRunStatus.COMPLETED` -- FAILED or SKIPPED
runs do not trigger reflection. `_reflect()` never raises; all exceptions are caught and logged.
The `already_executed` set prevents re-inserting an agent that has already run in this pipeline
invocation (prevents duplicate GRN_RETRIEVAL or RECONCILIATION_ASSIST insertions on retry).

### 3.6 Agent Feedback Loop

When `PORetrievalAgent` (the only `_FEEDBACK_AGENTS` member) finds a PO:

```
1. Lookup PurchaseOrder by po_number (normalised from evidence["found_po"])
2. AgentFeedbackService.apply_found_po(result, po, agent_run_id)
     -> re-link invoice to PO
     -> re-run deterministic matching (ThreeWayMatchService or TwoWayMatchService)
     -> update ReconciliationResult status, exceptions, line results
3. Refresh AgentContext:
     ctx.po_number = new po.po_number
     ctx.exceptions = refreshed from DB (_truncate_exceptions applied)
     ctx.memory.resolved_po_number, facts["grn_available"], facts["grn_fully_received"] updated
```

**Status guard:** The feedback loop (`_apply_agent_findings`) only executes when
`last_output.status == AgentRunStatus.COMPLETED`. A FAILED agent run does not
trigger re-reconciliation, preventing partial/corrupt PO links.

**Evidence normalisation:** `PORetrievalAgent.interpret_response()` always
copies the PO number to `evidence["found_po"]`. Fallback key priority:
`found_po` -> `po_number` -> `matched_po` -> `result` -> `found` -> `po`.

### 3.7 LLM Client

`LLMClient` is a thin wrapper around `openai.AzureOpenAI` (or `OpenAI`).

```python
client = LLMClient(
    model=None,          # defaults to AZURE_OPENAI_DEPLOYMENT or LLM_MODEL_NAME
    temperature=None,    # defaults to LLM_TEMPERATURE (0.1)
    max_tokens=None,     # defaults to LLM_MAX_TOKENS (4096)
)
response = client.chat(
    messages=[...],
    tools=[ToolSpec(...)],
    tool_choice="auto",
    response_format=None,
)
```

`LLMResponse` carries `content`, `tool_calls` (list of `LLMToolCall(id, name, arguments)`),
and token counts. Provider resolved from `LLM_PROVIDER` setting:
`"azure_openai"` (default) -> `AzureOpenAI`; anything else -> plain `OpenAI`.

**Retry wrapper:** All LLM calls use `BaseAgent._call_llm_with_retry()`,
which retries up to 3 times on `RateLimitError`, `APIConnectionError`, and
`InternalServerError` with exponential backoff (2s, 4s, 8s).

---

## 4. Agent Catalog and Contract Model

### 4.1 AgentDefinition Model

Every agent type has an `AgentDefinition` DB record (seeded by `seed_config`
and enriched by `seed_agent_contracts`). This is the source of truth for
whether an agent is enabled and what contract it must honour.

```python
class AgentDefinition(BaseModel):
    # Core identity
    agent_type         # AgentType enum value
    name
    description
    enabled            # False -> orchestrator skips this agent
    llm_model          # Override model (blank = platform default)
    config_json        # Legacy config (allowed_tools list lives here)

    # Narrative / catalog
    purpose                    # what this agent does and why
    entry_conditions           # when it should be invoked
    success_criteria           # what a successful run looks like

    # Tool grounding contract
    requires_tool_grounding    # True -> cap confidence at 0.4 if no tools called
    min_tool_calls             # advisory minimum (applies 0.4 cap if not met)
    tool_failure_confidence_cap  # override when any tool fails (default platform: 0.5)

    # Recommendation contract
    prohibited_actions              # JSON list of RecommendationType values; never emit these
    allowed_recommendation_types    # JSON list; null = all allowed
    default_fallback_recommendation # used when output is suppressed or invalid

    # Output schema
    output_schema_name         # e.g. "AgentOutputSchema"
    output_schema_version      # e.g. "v1"

    # Lifecycle / governance
    lifecycle_status           # draft | active | deprecated
    owner_team
    capability_tags            # JSON list e.g. ["retrieval", "routing"]
    domain_tags                # JSON list e.g. ["po", "grn"]
    human_review_required_conditions
```

**Contract fields are first-class DB columns** (not stored in `config_json`).
Seed or update via the `seed_agent_contracts` management command
(idempotent) or via Django admin (AgentDefinition -> Contract fieldsets).

### 4.2 Capability Tags

`extraction`, `understanding`, `retrieval`, `assist`, `routing`, `summary`,
`validation`, `enrichment`. An agent may carry multiple tags.

### 4.3 Lifecycle Statuses

| Status | Meaning |
|---|---|
| `draft` | In development; not run in production pipelines |
| `active` | Production use; `PolicyEngine` may invoke this agent |
| `deprecated` | No longer invoked; record kept for audit history |

### 4.4 Runtime Enforcement

`BaseAgent._finalise_run()` enforces five checks using contract fields.
All checks are **fail-open**: if `agent_def` is None or the field is null,
the check is silently skipped.

| Check | Contract Field | What Happens |
|---|---|---|
| 1. Default fallback | `default_fallback_recommendation` | Applied when output `recommendation_type` is None |
| 2. Allowed rec types | `allowed_recommendation_types` | Falls back to default, caps confidence at 0.6 |
| 3. Prohibited actions | `prohibited_actions` | Overrides prohibited rec type with fallback, caps confidence at 0.5 |
| 4. Tool grounding | `requires_tool_grounding` | Caps confidence at 0.4 if no tools were called |
| 5. Tool failure cap | `tool_failure_confidence_cap` | Stricter cap applied when `failed_tool_count > 0` |

Checks run in order 1 -> 5. After check 5, `_enforce_evidence_keys()` runs
to inject the three required evidence keys (see Section 6.5).

### 4.5 Agent Contract Template

When adding a new agent, fill in this template and run `seed_agent_contracts`
after the `AgentDefinition` record is created.

```
# -----------------------------------------------------------------------
# AGENT CONTRACT
# -----------------------------------------------------------------------
Agent name:
Agent type (AgentType value):
Primary capability:      [extraction|understanding|retrieval|assist|routing|summary|validation|enrichment]
Secondary capabilities:  [comma-separated or none]

# -- Purpose and scope --
Purpose:
Entry conditions:
Success criteria:

# -- Recommendation contract --
Prohibited actions:              [comma-separated RecommendationType values or none]
Allowed recommendation types:   [comma-separated or "all"]
Default fallback recommendation: [RecommendationType value]

# -- Tool grounding --
Requires tool grounding: [true|false]
Minimum tool calls:      [integer, 0 if not enforced]
Tool failure confidence cap: [0.0 - 1.0 or null]

# -- Human review --
Human review conditions:

# -- Output schema --
Output schema:         AgentOutputSchema
Output schema version: v1

# -- Lifecycle --
Lifecycle status: [draft|active|deprecated]
Owner team:

# -- System prompt sections (fill in each) --
System prompt sections:
  1. Role / purpose:
  2. Task objective:
  3. Tool usage policy (_TOOL_POLICY_<TYPE>):
       Preferred tool order:
       Mandatory tool conditions:
       Fallback if mandatory tool fails:
  4. DO NOT INFER rules:     [shared -- _DO_NOT_INFER_RULES]
  5. Tool failure rules:     [shared -- _TOOL_FAILURE_RULES]
  6. Evidence citation rules:[shared -- _EVIDENCE_CITATION_RULES]
  7. Reasoning quality rules:[shared -- _REASONING_QUALITY_RULES]
  8. Confidence rules:       [shared -- _CONFIDENCE_RULES]
  9. Output schema:          [shared -- _AGENT_JSON_INSTRUCTION]
# -----------------------------------------------------------------------
```

---

## 5. Tool System and Authoring Standard

### 5.1 Structure

```
BaseTool (abstract)
  +-- name: str
  +-- description: str
  +-- parameters_schema: dict        # JSON Schema passed to LLM
  +-- required_permission: str       # RBAC permission code
  +-- when_to_use: str
  +-- when_not_to_use: str
  +-- no_result_meaning: str
  +-- failure_handling_instruction: str
  +-- evidence_keys_produced: list
  +-- authoritative_fields: list
  +-- _tenant: CompanyProfile | None  # set by execute() from kwargs
  +-- _scoped(qs) -> QuerySet         # applies tenant filter if _tenant is set
  +-- run(**kwargs) -> ToolResult    # implement this
  +-- execute(**kwargs) -> ToolResult  # wraps run() with timing + error handling + tenant extraction
  +-- get_spec() -> ToolSpec         # builds LLM-facing spec (see 5.3)

ToolRegistry (singleton)
  +-- register(tool)
  +-- get(name) -> BaseTool
  +-- get_specs(names) -> List[ToolSpec]    # passed to LLM as tools=
```

### 5.2 Registered Tools

| Tool Name | Permission | Purpose |
|---|---|---|
| `po_lookup` | `purchase_orders.view` | Lookup PO by number or vendor; tries ERP resolver first |
| `grn_lookup` | `grns.view` | Lookup GRN by PO number; tries ERP resolver first |
| `vendor_search` | `vendors.view` | Search vendors by name |
| `invoice_details` | `invoices.view` | Full invoice data (header + lines) |
| `exception_list` | `reconciliation.view` | Active exceptions for a reconciliation result |
| `reconciliation_summary` | `reconciliation.view` | Match status + key metrics |

> **Multi-Tenant Note**: All tools use `self._scoped(queryset)` on every DB query.
> `BaseAgent._execute_tool()` injects `tenant=self._agent_context.tenant` into
> tool kwargs. `BaseTool.execute()` extracts `tenant` and stores it as `self._tenant`.
> When `_tenant` is set, `_scoped()` applies `.filter(tenant=self._tenant)`.

### 5.3 How `get_spec()` Composes the LLM Description

```
<base description>
Use when: <when_to_use>
Do not use when: <when_not_to_use>
No result means: <no_result_meaning>
On failure: <failure_handling_instruction>
```

Each line is only appended when the attribute is non-empty.
`evidence_keys_produced` is NOT sent to the LLM -- it is documentation only.

### 5.4 Tool Attribute Rules

| Attribute | Rule |
|---|---|
| `description` | One sentence; describes what the tool returns |
| `when_to_use` | One sentence, active voice; "Call this tool when..." |
| `when_not_to_use` | One sentence; "Do not use when..." |
| `no_result_meaning` | What an empty/null result means; prevents incorrect inference |
| `failure_handling_instruction` | What the LLM should do on tool error |
| `evidence_keys_produced` | Keys from output that agents should store as evidence |
| `authoritative_fields` | Fields this tool is the single source of truth for |

Keep each attribute under 120 characters. Use plain ASCII only.

### 5.5 Adding a Tool

Declare all ten attributes; `get_spec()` is inherited.

```python
@register_tool
class MyTool(BaseTool):
    # -- Core identity --
    name = "my_tool"
    description = "One-sentence what it does and when an agent should reach for it."
    required_permission = "module.action"
    parameters_schema = {
        "type": "object",
        "properties": {
            "param_one": {"type": "string", "description": "..."},
        },
        "required": ["param_one"],
    }

    # -- Behavioural guidance (surfaced to LLM via get_spec) --
    when_to_use = "Call this tool when you need X and you do not yet have it from a prior tool call."
    when_not_to_use = "Do not call this when Y is already present in evidence from a previous tool."
    no_result_meaning = "The record does not exist in the system; do not retry with guessed parameters."
    failure_handling_instruction = "If this tool fails, note the failure in _uncertainties and reduce confidence."

    # -- Evidence contract --
    evidence_keys_produced = ["key_one", "key_two"]
    authoritative_fields = ["key_one"]

    def run(self, param_one: str = "", **kwargs) -> ToolResult:
        return ToolResult(success=True, data={"key_one": param_one, "key_two": "..."})
```

**Registration checklist:**
1. Create a `ToolDefinition` DB record with `name` matching the class attribute.
2. Add the tool name to the relevant agent's `config_json["allowed_tools"]`.
3. Seed the permission (`module.action`) in `seed_rbac` if it is new.
4. Reference the tool in Section 7 under the relevant agent's "Tools used".

### 5.6 Tool Langfuse Trace Threading

`BaseAgent._execute_tool()` injects `lf_parent_span=_tool_span` into the tool's
kwargs before calling `tool.execute(**arguments)`. This allows ERP-backed tools
(e.g. `POLookupTool`, `GRNLookupTool`) to forward the span to
`ERPResolutionService.resolve_*()` so ERP resolution spans nest under the agent's
tool call span in Langfuse.

The span object is removed from `arguments` after execution (before audit
persistence via `ToolCallLogger` and `AgentStep`) to avoid serialisation errors.

Tools that use ERP resolution should extract the span from kwargs:

```python
def _resolve_via_erp(self, po_number, **kwargs):
    svc = ERPResolutionService.with_default_connector()
    result = svc.resolve_po(
        po_number=po_number,
        lf_parent_span=kwargs.get("lf_parent_span"),
    )
```

See [LANGFUSE_INTEGRATION.md](LANGFUSE_INTEGRATION.md) Section 11.4 for the
full caller threading table.

---

## 6. Prompting and Output Contracts

### 6.1 ReAct Loop

Every LLM agent follows the Reason + Act pattern:

```
INIT: [system_msg, user_msg]
LOOP (max 6 rounds):
  1. LLM call with current messages + tool specs
  2. If finish_reason != tool_calls -> interpret_response() -> done
  3. Append assistant msg (with tool_calls array)
  4. For each tool call:
       a. RBAC check (authorize_tool)
       b. tool.execute(**arguments)
       c. Append tool response msg (with tool_call_id + name)
       d. Persist AgentStep
  5. Back to 1
FINALIZE: _apply_tool_failure_guards() -> _enforce_evidence_keys()
          -> _guard_reasoning_quality() -> persist AgentRun + DecisionLog
```

Messages follow the OpenAI tool-calling convention:
- Assistant messages include a `tool_calls` array.
- Tool response messages include `tool_call_id` and `name`.

### 6.2 Mandatory Shared Prompt Blocks

All agent prompts in `apps/core/prompt_registry.py` follow this block order.
Deviating from this order violates the shared rules assumption.

**Tool-using agents (all blocks):**

1. `_TOOL_POLICY_<AGENT_TYPE>` -- per-agent tool usage policy
2. `_DO_NOT_INFER_RULES` -- seven rules prohibiting fabrication
3. `_TOOL_FAILURE_RULES` -- three rules for handling tool errors
4. `_EVIDENCE_CITATION_RULES` -- five rules for grounding and citation
5. `_REASONING_QUALITY_RULES` -- heuristic weak-reasoning detection rules
6. `_CONFIDENCE_RULES` -- five confidence calibration rules
7. `_AGENT_JSON_INSTRUCTION` -- output schema and example

**Agents without tools (REVIEW_ROUTING, CASE_SUMMARY):** omit
`_TOOL_FAILURE_RULES`; include all other blocks.

### 6.3 Per-Agent Tool Usage Policy

Each tool-using agent declares a `_TOOL_POLICY_<TYPE>` constant specifying:
- Which tools to call first (preferred order).
- Conditions under which a tool call is **mandatory** before any recommendation.
- What to do if the mandatory tool fails.

Insert this block between the role/task section and `_DO_NOT_INFER_RULES`.

### 6.4 Output Schema Validation

`AgentOutputSchema` (Pydantic v2, `agent_output_schema.py`) validates all
standard agent JSON output:
- `recommendation_type`: invalid values coerced to `SEND_TO_AP_REVIEW`.
- `confidence`: clamped to [0.0, 1.0].
- `decisions` and `evidence` presence checked.

`enforce_json_response=True` (default on `BaseAgent`) passes
`response_format={"type":"json_object"}` to every ReAct loop LLM call.
`InvoiceExtractionAgent` sets this to `False` and handles its own format.

### 6.5 Required Evidence Keys

The `evidence` block in every agent JSON output must include:

| Key | Value |
|---|---|
| `_tools_used` | List of tool names that returned data used in the decision |
| `_grounding` | `"full"` (all from tools), `"partial"` (some context-only), or `"none"` (no tool data) |
| `_uncertainties` | List of unresolved questions, or empty list |

Additional keys must match the `authoritative_fields` of the tools called.

**Runtime enforcement:** `BaseAgent._enforce_evidence_keys()` is called from
`_finalise_run()` after all catalog checks:
- Preserves any LLM-supplied values (non-destructive).
- Auto-adds missing keys from runtime-tracked data, sets `_evidence_keys_auto_added=True`.
- Caps confidence at 0.5 when `_grounding == "none"`.

### 6.6 Confidence Calibration

| Range | Meaning |
|---|---|
| 0.9 - 1.0 | All evidence from successful tool calls, no ambiguity |
| 0.7 - 0.89 | Strong evidence; at most one source is context-only |
| 0.5 - 0.69 | Partial evidence or one minor tool uncertainty |
| below 0.5 | Tool failures, incomplete evidence, or conflicting signals |

**Composite confidence scoring:** `BaseAgent._compute_composite_confidence()`
blends LLM confidence (60%), tool success rate (25%), and evidence quality (15%):

```python
tool_score = 1.0 if total_tool_calls == 0 else (total_tool_calls - failed) / total_tool_calls
evidence_score = 0.5 if not evidence or list(evidence.keys()) == ["_provenance"] else 1.0
composite = llm_confidence * 0.6 + tool_score * 0.25 + evidence_score * 0.15
result = max(0.0, min(1.0, composite))
```

Do not assign 0.9+ if any tool call failed or any field was context-only.

### 6.7 Tool Failure Runtime Guards

`BaseAgent._apply_tool_failure_guards(output, failed_tool_count, total_tool_calls)`
runs in both exit paths of the ReAct loop:

**Rule 1 -- any tool failed:**
- Caps confidence at 0.5.
- Downgrades `AUTO_CLOSE` to `SEND_TO_AP_REVIEW` (stricter recommendations preserved).
- Sets `evidence._provenance = "tool_failures"`.

**Rule 2 -- tool-grounded agent called no tools:**
Applies to: `PO_RETRIEVAL`, `GRN_RETRIEVAL`, `RECONCILIATION_ASSIST`,
`INVOICE_UNDERSTANDING`, `EXCEPTION_ANALYSIS`
- Caps confidence at 0.6.
- Sets `evidence._provenance = "no_tools_called"`.

### 6.8 Reasoning Quality Guard

`BaseAgent._guard_reasoning_quality(output, agent_type)` runs in `_finalise_run()`
before persisting `summarized_reasoning`. Weak-reasoning heuristics:

- Reasoning shorter than 40 characters -> weak.
- Vague opener ("Based on analysis", "Upon review", etc.) with no domain marker
  word ("invoice", "po", "amount", "vendor", "match", "quantity", ...) -> weak.

When weak, a safe fallback is derived from structured output:
`[auto-summary agent=TYPE] Recommendation: X. Confidence: Y%. Evidence: key=val...`
(capped at 500 chars, ASCII-safe). The original reasoning is never hard-failed.

### 6.9 ASCII-Safety Rule

`BaseAgent._sanitise_text()` must be applied before persisting any LLM-generated
text to the database:

```python
@staticmethod
def _sanitise_text(text: str) -> str:
    replacements = {
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2014": "--", "\u2013": "-",
        "\u2026": "...",
        "\u2192": "->", "\u2190": "<-", "\u21d2": "=>",
        "\u2022": "-",
    }
    for char, ascii_eq in replacements.items():
        text = text.replace(char, ascii_eq)
    return re.sub(r"[^\x00-\x7F]", "", text)
```

Fields that must always be sanitised: `AgentRun.summarized_reasoning`,
`ReconciliationResult.summary`, `ReviewAssignment.reviewer_summary`,
`DecisionLog.rationale`.

---

## 7. Concrete Agents

### 7.1 Agent Summary

| Agent | Type Enum | LLM | Tools | Replaced By |
|---|---|---|---|---|
| ExceptionAnalysisAgent | `EXCEPTION_ANALYSIS` | Yes | po_lookup, grn_lookup, invoice_details, exception_list, reconciliation_summary | DeterministicResolver (standard cases) |
| InvoiceExtractionAgent | `INVOICE_EXTRACTION` | Yes | None | -- (runs during upload, not pipeline) |
| InvoiceUnderstandingAgent | `INVOICE_UNDERSTANDING` | Yes | invoice_details, po_lookup, vendor_search | -- |
| PORetrievalAgent | `PO_RETRIEVAL` | Yes | po_lookup, vendor_search, invoice_details | -- |
| GRNRetrievalAgent | `GRN_RETRIEVAL` | Yes | grn_lookup, po_lookup, invoice_details | -- |
| ReviewRoutingAgent | `REVIEW_ROUTING` | No | reconciliation_summary, exception_list | SystemReviewRoutingAgent |
| CaseSummaryAgent | `CASE_SUMMARY` | No | invoice_details, po_lookup, grn_lookup | SystemCaseSummaryAgent |
| ReconciliationAssistAgent | `RECONCILIATION_ASSIST` | Yes | invoice_details, po_lookup, grn_lookup, reconciliation_summary, exception_list | -- |
| **SystemReviewRoutingAgent** | `SYSTEM_REVIEW_ROUTING` | No | None | -- (deterministic) |
| **SystemCaseSummaryAgent** | `SYSTEM_CASE_SUMMARY` | No | None | -- (deterministic) |
| **SystemBulkExtractionIntakeAgent** | `SYSTEM_BULK_EXTRACTION_INTAKE` | No | None | -- (deterministic) |
| **SystemCaseIntakeAgent** | `SYSTEM_CASE_INTAKE` | No | None | -- (deterministic) |
| **SystemPostingPreparationAgent** | `SYSTEM_POSTING_PREPARATION` | No | None | -- (deterministic) |

`EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, and `CASE_SUMMARY` are listed in
`DeterministicResolver.REPLACED_AGENTS`. The orchestrator routes REVIEW_ROUTING
and CASE_SUMMARY through `SystemReviewRoutingAgent` / `SystemCaseSummaryAgent`
(which wrap `DeterministicResolver` internally). EXCEPTION_ANALYSIS uses
`DeterministicResolver` directly with synthetic `AgentRun` records. All carry
`llm_model_used="deterministic"` and zero token counts.

### 7.2 ExceptionAnalysisAgent [IMPLEMENTED]

**Purpose:** Analyse active reconciliation exceptions, determine the root cause,
and produce a structured summary for the reviewer.

**Tools used:** `exception_list` (mandatory first call), `po_lookup`,
`grn_lookup`, `invoice_details`, `reconciliation_summary`.

**Implemented behaviour:**
- `_build_reviewer_summary()` issues a second dedicated LLM call after the
  ReAct loop; the main analysis JSON no longer embeds a `<reviewer_summary>` block.
- If the second call fails, a minimal summary is constructed from the main
  output (recommendation type + confidence + first evidence value) -- the
  `reviewer_summary` field on `ReviewAssignment` is never left empty.
- `interpret_response()` replaces unknown `recommendation_type` values with
  `SEND_TO_AP_REVIEW` and clamps confidence to [0.0, 1.0].

### 7.3 InvoiceExtractionAgent [IMPLEMENTED — Phase 2]

**Purpose:** Single-shot extraction of invoice header and line-item data from
OCR text. Runs during upload, not during the reconciliation pipeline.

**Tools used:** None (no ReAct loop; single LLM call with `response_format=json_object`, `temperature=0`).

**Phase 2 enhancements:**

- **Composed prompt via `ctx.extra`**: `InvoiceExtractionAdapter` passes `composed_prompt` (base + category overlay + country overlay from `InvoicePromptComposer`) and `prompt_metadata` (invoice_category, category_confidence, prompt_hash, component versions) via `ctx.extra`. The `_init_messages()` override in `InvoiceExtractionAgent` uses `ctx.extra.get("composed_prompt")` as the system message if present; falls back to `PromptRegistry.get("extraction.invoice_system")` otherwise.

- **Extended Langfuse metadata**: `self.llm._langfuse_metadata` extended with 10 fields from `ctx.extra.get("prompt_metadata", {})` — invoice_category, invoice_category_confidence, base_prompt_key, base_prompt_version, category_prompt_key, category_prompt_version, country_prompt_key, country_prompt_version, prompt_hash, schema_code.

- **Extracted fields** (from the updated prompt): vendor_name, vendor_tax_id, buyer_name, invoice_number, invoice_date, due_date, po_number, currency, subtotal, tax_percentage, tax_amount, tax_breakdown (cgst/sgst/igst/vat), total_amount, document_type, line_items.

**Remaining gap:** No lightweight schema check (required keys, numeric
confidence, non-empty line_items) before calling `_finalise_run()`. If the
LLM returns a partial response the run succeeds with incomplete data.

### 7.4 InvoiceUnderstandingAgent [IMPLEMENTED]

**Purpose:** Deeper invoice analysis when extraction confidence is below
threshold. Resolves ambiguous vendor references, validates line items against
PO context.

**Tools used:** `invoice_details`, `po_lookup`, `vendor_search`.

### 7.5 PORetrievalAgent [IMPLEMENTED]

**Purpose:** Locate the correct PO when the invoice arrives without a valid PO
reference. Triggers the feedback loop if a match is found.

**Tools used:** `po_lookup` (mandatory first call), `vendor_search`,
`invoice_details`.

**Evidence normalisation:** `interpret_response()` always copies the found PO
number to `evidence["found_po"]` from whichever key the LLM used
(`po_number`, `matched_po`, `result`, `found`, or `po`).

**Prompt contract (implemented):** `_TOOL_POLICY_PO_RETRIEVAL` explicitly
instructs the LLM to return `found_po` as the canonical key.

### 7.6 GRNRetrievalAgent [IMPLEMENTED]

**Purpose:** Locate the GRN for a 3-way match when the GRN is missing or
ambiguous.

**Tools used:** `grn_lookup`, `po_lookup`, `invoice_details`.

**Mode guard (implemented):** `build_user_message()` returns a machine-
parseable JSON no-op immediately when `ctx.reconciliation_mode == "TWO_WAY"`.

### 7.7 ReviewRoutingAgent [IMPLEMENTED -- DETERMINISTIC]

**Purpose:** Route the reviewed case to the correct team queue.

**Runtime:** Replaced by `SystemReviewRoutingAgent` which wraps
`DeterministicResolver.resolve()` in the standard `DeterministicSystemAgent`
framework. Produces a full `AgentRun` with `DecisionLog` records, Langfuse
spans, and audit events -- all without LLM calls.

### 7.8 CaseSummaryAgent [IMPLEMENTED -- DETERMINISTIC]

**Purpose:** Produce a human-readable case summary after all analysis agents
have run.

**Runtime:** Replaced by `SystemCaseSummaryAgent` which wraps
`DeterministicResolver.resolve()`. Builds `case_summary` and persists it on
`ReconciliationResult.summary`.

### 7.9-7.13 System Agents [IMPLEMENTED -- DETERMINISTIC]

Five `DeterministicSystemAgent` subclasses provide observability and
auditability for platform-level operations without LLM overhead:

- **7.9 SystemReviewRoutingAgent** (`SYSTEM_REVIEW_ROUTING`): Wraps `DeterministicResolver` for routing.
- **7.10 SystemCaseSummaryAgent** (`SYSTEM_CASE_SUMMARY`): Wraps `DeterministicResolver` for summaries.
- **7.11 SystemBulkExtractionIntakeAgent** (`SYSTEM_BULK_EXTRACTION_INTAKE`): Records bulk extraction job stats from `ctx.extra`.
- **7.12 SystemCaseIntakeAgent** (`SYSTEM_CASE_INTAKE`): Records case creation and stage initialization from `ctx.extra`.
- **7.13 SystemPostingPreparationAgent** (`SYSTEM_POSTING_PREPARATION`): Records posting pipeline outcomes from `ctx.extra`.

All use `llm_model_used="deterministic"`, zero tokens, and emit
`SYSTEM_AGENT_RUN_COMPLETED` / `SYSTEM_AGENT_RUN_FAILED` audit events.
Seeded via `seed_agent_contracts` with `requires_tool_grounding=False`
and `capability_tags=["deterministic"]`.

### 7.9 ReconciliationAssistAgent [IMPLEMENTED]

**Purpose:** Assist with PARTIAL_MATCH analysis when exceptions fall outside
the auto-close band.

**Tools used:** `invoice_details`, `po_lookup`, `grn_lookup`,
`reconciliation_summary`, `exception_list`.

**Implemented behaviour:**
- `_TOOL_POLICY_RECONCILIATION_ASSIST` in `prompt_registry.py` includes:
  "You MUST call at least one tool before forming your recommendation. Never
  recommend AUTO_CLOSE without first verifying amounts via
  `reconciliation_summary` or `invoice_details`."
- `BaseAgent._enforce_evidence_keys()` caps confidence at 0.5 when
  `_grounding == "none"` (no tool data in evidence).

---

## 8. Governance and RBAC

### 8.1 AgentGuardrailsService

`AgentGuardrailsService` is the sole gatekeeper for every agent action.
No agent operation executes without a passing check.

**Permission map:**

| Check | Permission Code | Evaluated By |
|---|---|---|
| Trigger the pipeline | `agents.orchestrate` | `authorize_orchestration()` |
| Run INVOICE_EXTRACTION / UNDERSTANDING | `agents.run_extraction` | `authorize_agent()` |
| Run PO_RETRIEVAL | `agents.run_po_retrieval` | `authorize_agent()` |
| Run GRN_RETRIEVAL | `agents.run_grn_retrieval` | `authorize_agent()` |
| Run RECONCILIATION_ASSIST | `agents.run_reconciliation_assist` | `authorize_agent()` |
| Run EXCEPTION_ANALYSIS | `agents.run_exception_analysis` | `authorize_agent()` |
| Run REVIEW_ROUTING | `agents.run_review_routing` | `authorize_agent()` |
| Run CASE_SUMMARY | `agents.run_case_summary` | `authorize_agent()` |
| Call `po_lookup` | `purchase_orders.view` | `authorize_tool()` |
| Call `grn_lookup` | `grns.view` | `authorize_tool()` |
| Auto-close a result | `recommendations.auto_close` | `authorize_action()` |
| Escalate a case | `cases.escalate` | `authorize_action()` |

### 8.2 System Agent Identity

When no human user is available (Celery async, scheduled jobs),
`resolve_actor(None)` returns the `system-agent@internal` user with the
`SYSTEM_AGENT` role (rank 100, `is_system_role=True`). This identity has
exactly the permissions seeded in `seed_rbac` -- it is never an admin bypass.

`system-agent@internal` has `company=NULL` (no tenant). When running on behalf
of a specific entity (e.g., `ReconciliationResult`), the tenant is resolved from
the entity itself (`entity.tenant`) and threaded through the pipeline via
`AgentContext.tenant`. `StageExecutor` passes `tenant=case.tenant` to the
orchestrator. Celery tasks (`run_agent_pipeline_task`, `process_case_task`) also
pass `tenant_id` and guard entity fetches with `filter(tenant=tenant)`.

### 8.3 Data-Scope Authorization [IMPLEMENTED]

`AgentGuardrailsService.authorize_data_scope(actor, result)` is called in
`execute()` immediately after `authorize_orchestration()`. A denial causes
orchestration to return early (fail-closed).

| Method | Purpose |
|---|---|
| `get_actor_scope(actor)` | Returns `{allowed_business_units, allowed_vendor_ids}` union across active `UserRole.scope_json` entries; ADMIN/SYSTEM_AGENT always unrestricted |
| `get_result_scope(result)` | Returns `{business_unit, vendor_id}` from current schema |
| `authorize_data_scope(actor, result)` | Orchestrates the above; logs AuditEvent for every allow/deny |

Scope stored in `UserRole.scope_json` (nullable). Null means unrestricted.

**Currently enforced dimensions:**

| Dimension | Source on Actor | Source on Result | Status |
|---|---|---|---|
| Business unit | `UserRole.scope_json["allowed_business_units"]` | `ReconciliationPolicy.business_unit` | ENFORCED |
| Vendor | `UserRole.scope_json["allowed_vendor_ids"]` | `result.invoice.vendor_id` | ENFORCED |
| **Tenant** | `user.company` (CompanyProfile FK) | `result.tenant` | **ENFORCED** (middleware + tool scoping) |
| Country / legal entity | -- | No `country_code` field on Invoice/PO | PENDING |
| Cost centre | -- | No `cost_centre` field on Invoice/PO | PENDING |

### 8.4 Fail-Closed Behaviour

- Unknown tool names: denied by `authorize_tool()`.
- Unknown agent types: denied (`AGENT_PERMISSIONS.get()` returns None -> False).
- Missing `SYSTEM_AGENT` role (seed not run): user is returned but fails permission checks.

### 8.5 Decision Provenance

Every `DecisionLog` and `AgentRecommendation` must cite at least one provenance
source. Naming convention:

```
<tool_name>.<output_key>           e.g.  po_lookup.matched_line_count
<exception_type_code>              e.g.  PRICE_VARIANCE_EXCEEDED
<deterministic_rule>.<rule_id>     e.g.  DeterministicResolver.PRICE_WITHIN_AUTO_CLOSE
```

Required enforcement in `DecisionLogService` (see Section 11 for open status):
- `log_decision()` must reject entries where both `rule_name` is blank and
  `evidence_refs` is null or empty.
- `log_recommendation()` must reject entries where `reasoning` < 20 characters.

### 8.6 Duplicate Recommendation Prevention [IMPLEMENTED]

Two-layer idempotency:

**Layer 1 (service layer):** `DecisionLogService.log_recommendation()` filters
for any PENDING (`accepted=None`) recommendation of the same
`(reconciliation_result, recommendation_type)` before creating. Returns the
existing record without a DB write.

**Layer 2 (model constraint):** `AgentRecommendation` carries a
`UniqueConstraint` on `(reconciliation_result, recommendation_type, agent_run)`.
Both orchestrator call sites are wrapped with an `IntegrityError` guard.

---

## 9. Observability and Audit

### 9.1 Persisted Records Per Run

| Record | Written By | Content |
|---|---|---|
| `AgentOrchestrationRun` | `AgentOrchestrator.execute()` | Pipeline-level state machine |
| `AgentRun` | `BaseAgent.run()` | Agent type, status, tokens, RBAC fields, trace ID |
| `AgentMessage` | `_save_message()` | Every system/user/assistant/tool message |
| `AgentStep` | `_execute_tool()` | Every tool call: input, output, duration, success |
| `DecisionLog` | `_finalise_run()` | Every `decisions` entry; fields: `decision`, `rationale`, `confidence`, `evidence_refs`, `trace_id`, `span_id`, `invoice_id`, `recommendation_type`, `prompt_version` |
| `AgentRecommendation` | `DecisionLogService.log_recommendation()` | Per-recommending-agent recommendation |
| `AgentEscalation` | `_apply_post_policies()` | When escalation is triggered |
| `AuditEvent` (guardrail) | `log_guardrail_decision()` | Every RBAC allow/deny |
| `AuditEvent` (recommendation) | orchestrator post-run | `AGENT_RECOMMENDATION_CREATED` |

`AgentRun` RBAC fields: `actor_user_id`, `actor_primary_role`,
`actor_roles_snapshot_json`, `permission_checked`, `permission_source`,
`access_granted`, `trace_id`, `span_id`.

### 9.2 Pipeline Trigger

**Synchronous (view-triggered):**

```python
# apps/reconciliation/template_views.py
from apps.agents.services.orchestrator import AgentOrchestrator
orchestrator = AgentOrchestrator()
orchestrator.execute(result, request_user=request.user)
```

**Asynchronous (Celery):**

```python
# apps/agents/tasks.py
@shared_task(bind=True, max_retries=2, acks_late=True)
def run_agent_pipeline_task(self, reconciliation_result_id: int) -> dict:
    result = ReconciliationResult.objects.get(pk=reconciliation_result_id)
    orch_result = AgentOrchestrator().execute(result, request_user=None)
    return {"status": orch_result.final_recommendation, ...}
```

`request_user=None` triggers SYSTEM_AGENT identity.
`CELERY_TASK_ALWAYS_EAGER=True` (default on Windows dev) runs tasks
synchronously in-process; sync and async paths produce identical results.

### 9.3 Governance API

`/api/v1/governance/` exposes 9 endpoints:

| Endpoint | Data Source |
|---|---|
| `audit-history` | `AuditEvent` |
| `agent-trace` | `AgentRun`, `AgentStep`, `AgentMessage`, `DecisionLog` |
| `recommendations` | `AgentRecommendation` |
| `timeline` | `CaseTimelineService` |
| `access-history` | `AuditEvent` (RBAC types) |
| `stage-timeline` | `AuditEvent` (stage transition types) |
| `permission-denials` | `AuditEvent` (GUARDRAIL_DENIED) |
| `rbac-activity` | `AuditEvent` (RBAC assignment types) |
| `agent-performance` | `AgentRun` + `AgentStep` aggregates |

The `agent-performance` endpoint also provides a `plan_comparison` table
breaking down run count and average confidence by `plan_source`
("deterministic" vs "llm") over the past 7 days.

### 9.4 Built-In Observability Coverage

Before adding any external observability tool, note what is already provided:

| Capability | Built-In Location |
|---|---|
| Full message-level audit trail | `AgentMessage` + governance API |
| Tool call timing and success rate | `AgentStep.duration_ms`, `ToolCall.status` |
| Token usage per agent per run | `AgentRun.prompt_tokens/completion_tokens/total_tokens` |
| Confidence over time | `AgentRun.confidence` via governance API |
| Decision audit with evidence | `DecisionLog` + `agent-performance` endpoint |
| Per-invoice full trace | `AgentTraceService.get_trace_for_invoice()` |
| RBAC / guardrail decisions | `AuditEvent` with GUARDRAIL_GRANTED/DENIED types |
| Inter-agent context forwarding | `AgentMemory` + `ctx.extra["prior_reasoning"]` |

### 9.5 Langfuse Integration (Implemented)

Langfuse is the active LLM observability backend. The integration uses
`apps/core/langfuse_client.py` — a fail-silent singleton. All calls are
wrapped in `try/except`; a Langfuse outage never affects agent execution.

**SDK**: `langfuse==4.0.1` (self-hosted or cloud). Configure via `.env`:
```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://us.cloud.langfuse.com
```

**Full integration reference**: `docs/LANGFUSE_INTEGRATION.md`

#### Agent pipeline tracing

`AgentOrchestrator.execute()` opens a root trace before the first agent runs
and passes it via `ctx._langfuse_trace`. Every child agent creates a child
span underneath it:

```python
_lf_trace = start_trace(
    trace_id=trace_ctx.trace_id,
    name="agent_pipeline",
    invoice_id=result.invoice_id,
    result_id=result.pk,
    user_id=actor.pk if actor else None,
    session_id=f"invoice-{result.invoice_id}" if result.invoice_id else None,
    metadata={...},
)
```

Session ID convention `"invoice-{invoice_id}"` groups all LLM calls across
pipeline re-runs for the same invoice in the Langfuse Sessions tab.

#### Tool call spans

Every tool execution in `BaseAgent.run()` (the ReAct loop) is wrapped in a
child span:

```python
_tool_span = start_span(_lf_span, name=f"tool_{tc.name}", metadata={...})
tool_result = self._execute_tool(...)
end_span(_tool_span, output={"success": ..., "duration_ms": ..., "error": ...},
         level="ERROR" if not tool_result.success else "DEFAULT")
```

Failed tool calls appear highlighted in red in the Langfuse UI.

`_execute_tool()` injects `lf_parent_span=_tool_span` into tool kwargs so
ERP-backed tools (`POLookupTool`, `GRNLookupTool`) create ERP resolution child
spans under the tool span. See Section 5.6 for details.

#### RBAC guardrail scores

`log_guardrail_decision()` emits a `score_trace` for every guardrail decision
when inside an active pipeline trace:

| Score name | Value | Meaning |
|---|---|---|
| `rbac_guardrail` | `1.0` | Permission GRANTED |
| `rbac_guardrail` | `0.0` | Permission DENIED |
| `rbac_data_scope` | `0.0` | Data scope violation (deny path only) |

Filter `score:rbac_guardrail = 0` in Langfuse to surface all authorization
failures across any pipeline run.

#### Prompt management

Agent prompts are version-controlled in Langfuse. `PromptRegistry` fetches
them at runtime (60-second TTL) and falls back to local defaults if Langfuse
is unreachable. Push or reseed prompts:

```powershell
python manage.py push_prompts_to_langfuse            # push all
python manage.py push_prompts_to_langfuse --purge    # delete + reseed
```

#### SDK v4 compatibility note

Langfuse SDK v4 removed `user_id`/`session_id` from `start_observation()`.
They are set post-creation via OTel span attributes:

```python
from langfuse._client.attributes import TRACE_USER_ID, TRACE_SESSION_ID
otel_span = getattr(span, "_otel_span", None)
if otel_span:
    otel_span.set_attribute(TRACE_USER_ID, str(user_id))
    otel_span.set_attribute(TRACE_SESSION_ID, session_id)
```

Do **not** pass `user_id`/`session_id` to `start_observation()` --
it causes a silent `TypeError` that returns `None` and breaks all tracing.

#### Phoenix (local dev alternative)

Pure Python, no Docker needed. Stores traces in SQLite locally.

```python
# In apps/agents/apps.py AgentConfig.ready() -- dev only:
import os, threading
_phoenix_started = threading.Event()

def start_phoenix_once():
    if os.getenv("PHOENIX_ENABLED", "false").lower() != "true":
        return
    if _phoenix_started.is_set():
        return
    _phoenix_started.set()
    try:
        import phoenix as px
        from openinference.instrumentation.openai import OpenAIInstrumentor
        px.launch_app()               # http://localhost:6006
        OpenAIInstrumentor().instrument()
    except Exception:
        pass
```

`OpenAIInstrumentor` auto-patches `AzureOpenAI` -- every LLM call appears
in the Phoenix UI without per-call code changes.

**Option B -- Langfuse (staging/production):**
Self-hosted on Docker Compose (Postgres + Redis -- same stack as this project).

```python
# apps/agents/services/langfuse_tracer.py  (additive -- does not replace existing writes)
from langfuse import Langfuse

class LangfuseTracer:
    @classmethod
    def trace_llm_call(cls, agent_type, agent_run_id, messages,
                       response_content, tokens, model):
        client = cls.get_client()
        if not client:
            return
        try:
            trace = client.trace(name=f"agent.{agent_type}", id=str(agent_run_id))
            trace.generation(name="llm_call", model=model, input=messages,
                             output=response_content, usage=tokens)
        except Exception:
            pass   # never let tracing break an agent run
```

Settings: `LANGFUSE_ENABLED`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_HOST` (default `http://localhost:3000`).

**OTel spans (inter-agent bottleneck analysis):**

```python
from opentelemetry import trace as otel_trace
tracer = otel_trace.get_tracer("po-recon.agents")

with tracer.start_as_current_span(f"agent.{agent_type}", attributes={...}) as span:
    agent_run = agent_cls().run(ctx)
    span.set_attribute("agent.confidence", agent_run.confidence or 0.0)
```

Point the `OTLPSpanExporter` at Phoenix (`http://localhost:6006/v1/traces`)
or Langfuse -- no separate Jaeger instance needed.

**Not recommended:**
- **Weave/W&B:** no self-hosted backend; financial data must not go to W&B cloud.
- **LangSmith:** closed SaaS backend only; not self-hostable despite common misconception.

---

## 10. Extension Guide

### 10.1 How to Add a New Agent

1. Add `AgentType.MY_NEW_TYPE` to `apps/core/enums.py`.
2. Create the agent class in `apps/agents/services/agent_classes.py` extending `BaseAgent`:

```python
class MyNewAgent(BaseAgent):
    agent_type = AgentType.MY_NEW_TYPE

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.my_new_type")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Invoice: {ctx.invoice_id}\n"
            + "Your task: ..."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["invoice_details", "po_lookup"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)
```

3. Register in `AGENT_CLASS_REGISTRY` in `agent_classes.py`.
4. Add `agents.run_my_new_type` permission to `seed_rbac.py` PERMISSIONS list.
5. Map the permission to appropriate roles and to `SYSTEM_AGENT` in the role matrix.
6. Add the entry to `AGENT_PERMISSIONS` in `guardrails_service.py`.
7. Add to `PolicyEngine` decision logic if the new agent has specific trigger conditions.
8. Create an `AgentDefinition` record (via `seed_config` or admin), filling the
   full contract (Section 4.5 template).
9. Add system prompt constant to `apps/core/prompt_registry.py` following the
   block order in Section 6.2.
10. Run `python manage.py seed_agent_contracts --agent-type MY_NEW_TYPE`.

### 10.2 How to Add a New Tool

Follow the checklist in Section 5.5. Key points:
- Declare all ten attributes.
- Create a `ToolDefinition` DB record.
- Add to the relevant agent's `config_json["allowed_tools"]`.
- Seed any new permission in `seed_rbac`.
- **Tenant scoping**: Use `self._scoped(queryset)` on every database query in `run()`. This is inherited from `BaseTool` and applies the tenant filter automatically when a tenant is present in the agent context.

### 10.3 When to Add a New Agent vs Extend an Existing One

**Add a NEW agent when:**
- The capability does not exist in any current agent.
- The scope requires different `prohibited_actions` or `allowed_recommendation_types`.
- Entry conditions differ substantially from all existing agents.
- The existing agent prompt would exceed 1500 tokens after the addition.
- A different output schema or schema version is needed.

**Extend an EXISTING agent when:**
- The new behaviour fits the existing agent's purpose and entry conditions.
- Only the prompt or tool list needs updating.
- Output schema and `allowed_recommendation_types` stay the same.
- The change does not violate `prohibited_actions`.
- The prompt stays under 1500 tokens.

**Decision checklist (answer yes -> add new agent):**
```
[ ] New behaviour requires different tools than the existing agent allows?
[ ] New behaviour conflicts with any prohibited_actions?
[ ] New behaviour targets a recommendation type not in allowed_recommendation_types?
[ ] Would existing agent prompt exceed 1500 tokens after addition?
[ ] Are entry conditions substantially different?
```

**When updating an existing agent:**
1. Update the system prompt constant in `apps/core/prompt_registry.py`.
2. Update `allowed_tools` on the agent class if a new tool is needed.
3. Update contract fields via `seed_agent_contracts` or Django admin.
4. Run `python manage.py seed_agent_contracts --dry-run` to verify.
5. Set `lifecycle_status=draft` during development; back to `active` before deploy.

### 10.4 Component Inventory

| Component | File | Role |
|---|---|---|
| `AgentContext` / `AgentOutput` | `base_agent.py` | Data contracts |
| `AgentMemory` | `agent_memory.py` | Structured cross-agent findings store |
| `BaseAgent` | `base_agent.py` | ReAct loop, sanitise, truncate, composite confidence, retry |
| `AgentOutputSchema` | `agent_output_schema.py` | Pydantic v2 output validation |
| `LLMClient` | `llm_client.py` | Azure OpenAI / OpenAI wrapper |
| `PolicyEngine` | `policy_engine.py` | Deterministic agent plan (no LLM) |
| `ReasoningPlanner` | `reasoning_planner.py` | LLM-backed planner; PolicyEngine fallback |
| `DeterministicResolver` | `deterministic_resolver.py` | Rule-based exception routing |
| `DeterministicSystemAgent` | `deterministic_system_agent.py` | Base class for deterministic system agents (skip ReAct) |
| System agents (5) | `system_agent_classes.py` | SystemReviewRouting, SystemCaseSummary, SystemBulkExtractionIntake, SystemCaseIntake, SystemPostingPreparation |
| `AgentOrchestrator` | `orchestrator.py` | Sequence execution, feedback, post-policy |
| `AgentOrchestrationRun` | `agents/models.py` | DB state machine for one pipeline invocation |
| `AgentGuardrailsService` | `guardrails_service.py` | RBAC enforcement |
| `AgentTraceService` | `agent_trace_service.py` | Unified governance writes |
| `DecisionLogService` | `decision_log_service.py` | Recommendation lifecycle |
| `BaseTool` / `ToolRegistry` | `tools/registry/base.py` | Tool system |
| Concrete tools (6) | `tools/registry/tools.py` | PO, GRN, vendor, invoice lookups |
| Concrete LLM agents (8) | `agent_classes.py` | Specialised LLM implementations |
| Concrete system agents (5) | `system_agent_classes.py` | Deterministic implementations |
| `AGENT_CLASS_REGISTRY` | `agent_classes.py` | `AgentType` -> class map (13 entries: 8 LLM + 5 system) |
| `_AgentRunOutputProxy` | `orchestrator.py` | Adapts `AgentRun` to `AgentMemory` interface |

---

## 11. Remaining Gaps and Roadmap

### [DONE] -- Fully Implemented

- **Orchestration state machine** -- `AgentOrchestrationRun` with duplicate-run
  guard, terminal states, and stale-run detection.
- **Agent contract fields** -- all 8 AgentDefinition records have full contract
  columns; `seed_agent_contracts` is idempotent.
- **Tool metadata standard** -- all 6 `BaseTool` subclasses have all 10
  attributes; `get_spec()` composes them into LLM descriptions.
- **Prompt block ordering** -- all 7 tool-using agent prompts follow the
  mandatory 7-block order; `_REASONING_QUALITY_RULES` is block 5.
- **Required evidence keys** -- `_enforce_evidence_keys()` runs in
  `_finalise_run()` after all catalog checks; keys are always present in
  persisted evidence.
- **Composite confidence scoring** -- `_compute_composite_confidence()` blends
  LLM score (60%), tool success (25%), evidence quality (15%).
- **Tool failure guards** -- `_apply_tool_failure_guards()` in both ReAct exit
  paths; AUTO_CLOSE downgraded on tool failure; no-tool cap at 0.6.
- **Reasoning quality guard** -- `_guard_reasoning_quality()` detects weak
  reasoning and produces an auto-summary fallback.
- **Structured output validation** -- `AgentOutputSchema` (Pydantic v2)
  validates all standard agent output; applied via `enforce_json_response=True`.
- **Idempotent recommendations** -- two-layer dedup (service filter + model
  unique constraint + IntegrityError guard).
- **Data-scope authorization** -- business unit + vendor scope enforced in
  `authorize_data_scope()`; logged as AuditEvent.
- **LLM retry with backoff** -- `_call_llm_with_retry()` retries on transient
  OpenAI errors (max 3, exponential backoff 2/4/8s).
- **Exception truncation** -- `_truncate_exceptions()` caps at 20, sorted by
  severity; called at 3 orchestrator sites.
- **Prompt version capture** -- `BaseAgent.run()` always writes
  `agent_run.prompt_version` before the ReAct loop.
- **ASCII sanitisation** -- `_sanitise_text()` applied in `_finalise_run()`.
- **ReasoningPlanner** -- LLM planner always active; PolicyEngine is the
  internal fallback on LLM error.
- **Reflection step** -- `_reflect()` runs after every LLM agent; deduped
  via `already_executed` set.
- **AgentMemory** -- structured cross-agent findings; agents read from
  `ctx.memory` in `build_user_message()`.
- **ExceptionAnalysisAgent reviewer summary** -- separate LLM call;
  no-fail fallback summary.
- **PORetrievalAgent found_po normalisation** -- interpret_response normalises
  to canonical key; prompt explicitly instructs `found_po`.
- **GRNRetrievalAgent TWO_WAY guard** -- immediate no-op JSON when mode is TWO_WAY.
- **ReconciliationAssistAgent tool grounding** -- prompt MANDATORY rule;
  runtime `_enforce_evidence_keys()` caps confidence at 0.5 if `_grounding == "none"`.

---

### [PARTIAL] -- Partially Implemented

**Decision Provenance Enforcement**
- Convention is defined (Section 8.5).
- `_finalise_run()` enforces non-empty evidence on `DecisionLog` entries
  (caps confidence at 0.5, sets `_provenance` marker).
- `DecisionLogService.log_decision()` does NOT yet reject entries where both
  `rule_name` and `evidence_refs` are empty.
- `DecisionLogService.log_recommendation()` does NOT yet reject `reasoning`
  shorter than 20 characters.
- Target: add the two validation raises to `DecisionLogService`.

**Post-Policy Idempotency**
- `ReconciliationResult.summary` overwrite: safe (no guard needed).
- Auto-close: check `result.match_status != MATCHED` before writing (present
  but no explicit commit guard).
- `AgentEscalation`: no duplicate guard yet. Required:
  ```python
  already_escalated = AgentEscalation.objects.filter(
      reconciliation_result=result, resolved=False).exists()
  if not already_escalated:
      AgentEscalation.objects.create(...)
  ```

**LLM Tail for Complex Cases**
- `AGENT_REASONING_LLM_TAIL_ENABLED` setting exists (default `False`).
- `_is_complex_case()` detection logic is scaffolded.
- LLM-based `ReviewRoutingAgent` and `CaseSummaryAgent` class bodies exist
  but are not wired into the deterministic tail path when the flag is enabled.
- Completion requires: complexity check in `_apply_deterministic_resolution()`,
  wiring agent classes for the LLM tail path, and ensuring these agents use
  `response_format={"type":"json_object"}`.

**InvoiceExtractionAgent Schema Validation**
- No lightweight schema check (required keys, numeric confidence, non-empty
  line_items) before `_finalise_run()`. A partial LLM response succeeds silently.
- Target: add schema validation in `interpret_response()`; mark run FAILED
  if validation fails so the extraction pipeline can retry. Also add a
  confidence penalty when `invoice_number` or `vendor_name` is empty.

---

### [FUTURE] -- Not Yet Started

**Country / Cost-Centre Scope Enforcement**
- `Invoice` and `PurchaseOrder` do not yet have `country_code` or `cost_centre`
  fields. Add schema columns, then wire into `authorize_data_scope()`.

**Feedback Learning from Field Corrections**
- `VendorAliasMapping` and `ItemAliasMapping` exist in `posting_core` but are
  not updated when a human reviewer accepts an ExtractionFieldCorrection.

**Scheduled ERP Reference Re-import**
- No Celery Beat task to pull fresh vendor/item master data from shared drive
  or ERP on a schedule.

**LLM-Assisted Item Mapping**
- `PostingMappingEngine._resolve_item()` uses exact/alias/fuzzy matching only.
  GPT-based semantic description matching would improve coverage on novel items.

**External Observability Wiring**
- Phoenix / Langfuse / OTel integration code examples are in Section 9.5.
  None are wired into the app by default -- they are opt-in via env flags.
  `PHOENIX_ENABLED`, `LANGFUSE_ENABLED`, `OTEL_ENABLED` exist in settings
  but the corresponding tracer / instrumentor startup code is not yet present
  in `AgentConfig.ready()`.

**Email Notifications**
- No notification system for new review assignments or agent escalations.

**Auto-Submit for Touchless Postings**
- `PostingActionService.submit_posting()` is a Phase 1 mock. Real ERP
  connector call (SAP BAPI, Oracle REST, etc.) not yet implemented.
  Auto-advance of touchless postings (`is_touchless=True`, confidence above
  threshold) to `SUBMISSION_IN_PROGRESS` is also not yet implemented.
