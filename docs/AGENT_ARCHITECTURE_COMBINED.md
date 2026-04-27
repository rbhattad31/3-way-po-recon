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

See [LANGFUSE_OBSERVABILITY.md §7.7](LANGFUSE_OBSERVABILITY.md) for the
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

**Full integration reference**: [LANGFUSE_OBSERVABILITY.md](LANGFUSE_OBSERVABILITY.md)

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

---

## Appendix: Reconciliation + Agent Pipeline

> Comprehensive reference for the deterministic reconciliation engine and LLM agent pipeline — apps/reconciliation/ + apps/agents/ + apps/tools/.

# Reconciliation + Agent Pipeline — Comprehensive Reference

**App paths:** `apps/reconciliation/` + `apps/agents/` + `apps/tools/`
**Dependencies:** `apps/documents/`, `apps/erp_integration/`, `apps/cases/`, `apps/auditlog/`, `apps/core/`
**Status:** Production-ready -- deterministic engine (2-way + 3-way), LLM agent pipeline (8 LLM + 5 deterministic system agents = 13 total), ERP-backed source resolution, full RBAC enforcement, Langfuse tracing.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Two-Layer Architecture](#2-two-layer-architecture)
3. [End-to-End Data Flow](#3-end-to-end-data-flow)
4. [Reconciliation Runner Service](#4-reconciliation-runner-service)
5. [Mode Resolution (3-Tier Cascade)](#5-mode-resolution-3-tier-cascade)
6. [PO Lookup Service (ERP-Backed)](#6-po-lookup-service-erp-backed)
7. [Execution Router](#7-execution-router)
8. [Two-Way Match Service](#8-two-way-match-service)
9. [Three-Way Match Service](#9-three-way-match-service)
10. [Tolerance Engine](#10-tolerance-engine)
11. [Classification Service](#11-classification-service)
12. [Exception Builder Service](#12-exception-builder-service)
13. [Result Service and ERP Provenance](#13-result-service-and-erp-provenance)
14. [Agent Orchestrator](#14-agent-orchestrator)
15. [Planning Layer: ReasoningPlanner and PolicyEngine](#15-planning-layer-reasoningplanner-and-policyengine)
16. [Deterministic Resolver](#16-deterministic-resolver)
17. [BaseAgent and the ReAct Loop](#17-baseagent-and-the-react-loop)
18. [Agent Registry: All 8 Agent Types](#18-agent-registry-all-8-agent-types)
19. [Agent Memory](#19-agent-memory)
20. [RBAC and Guardrails Service](#20-rbac-and-guardrails-service)
21. [Tool Registry: All 6 Tools](#21-tool-registry-all-6-tools)
22. [Agent Feedback Loop](#22-agent-feedback-loop)
23. [Data Models](#23-data-models)
24. [Prompt Registry Integration](#24-prompt-registry-integration)
25. [LLM Client Configuration](#25-llm-client-configuration)
26. [Langfuse Observability](#26-langfuse-observability)
27. [Audit Trail and Governance](#27-audit-trail-and-governance)
28. [Configuration Reference](#28-configuration-reference)
29. [File Reference](#29-file-reference)

---

## 1. Overview

The reconciliation and agent system handles the full lifecycle from a raw approved invoice to a final resolution recommendation ready for AP review or auto-closure.

The system is split into two cooperating layers:

- **Deterministic reconciliation engine** (`apps/reconciliation/`): Pure logic, no LLM. Looks up the PO, resolves the reconciliation mode (TWO_WAY vs THREE_WAY), runs line and header matching against configurable tolerance thresholds, classifies the result, and writes structured exceptions to the database.

- **AI agent pipeline** (`apps/agents/`): Eight LLM-backed agents with tool-calling capabilities. The agents investigate exceptions, attempt to recover missing documents, analyse quality issues, determine review routing, and produce case summaries. The pipeline is triggered automatically for every non-MATCHED result.

Key design principles:

- **Mode awareness**: Both layers track whether a reconciliation is `TWO_WAY` or `THREE_WAY`. GRN checks are suppressed in TWO_WAY mode at every level -- exception builder, classification, agent planner, tool calls.
- **ERP-backed source resolution**: PO and GRN data flow through `ERPResolutionService`, which implements a cache -> ERP API -> DB resolution chain. Every resolution carries provenance metadata (source type, confidence, freshness).
- **Fail-closed RBAC**: All agent operations go through `AgentGuardrailsService`. Unknown actors resolve to the SYSTEM_AGENT identity. Deny decisions are always logged as `AuditEvent` records.
- **Full auditability**: Every LLM call, tool invocation, decision, and recommendation is persisted to the database and optionally to Langfuse.

---

## 2. Two-Layer Architecture

```
+---------------------------------------------------------------------------+
|  LAYER 1: DETERMINISTIC RECONCILIATION ENGINE (apps/reconciliation/)      |
|                                                                           |
|  ReconciliationRunnerService                                              |
|    |                                                                      |
|    +--> POLookupService (ERP-backed) ---> PurchaseOrder (ORM)            |
|    |                                                                      |
|    +--> ReconciliationModeResolver                                        |
|    |     policy table -> heuristic keywords -> config default             |
|    |                                                                      |
|    +--> ReconciliationExecutionRouter                                     |
|    |     TwoWayMatchService  |  ThreeWayMatchService                      |
|    |       HeaderMatchService  +  LineMatchService                        |
|    |       (+ GRNLookupService + GRNMatchService in 3-way)               |
|    |                                                                      |
|    +--> ClassificationService --> MatchStatus                             |
|    |                                                                      |
|    +--> ExceptionBuilderService --> ReconciliationException (bulk_create) |
|    |                                                                      |
|    +--> ReconciliationResultService --> ReconciliationResult (DB)         |
|           (+ ReviewAssignment auto-creation for REQUIRES_REVIEW)         |
|                                                                           |
+---------------------------------------------------------------------------+
                                  |
                                  | (for non-MATCHED results)
                                  v
+---------------------------------------------------------------------------+
|  LAYER 2: LLM AGENT PIPELINE (apps/agents/)                              |
|                                                                           |
|  AgentOrchestrator                                                        |
|    |                                                                      |
|    +--> AgentGuardrailsService (RBAC: orchestrate + data-scope)          |
|    |                                                                      |
|    +--> ReasoningPlanner                                                  |
|    |     LLM plan (always) -> PolicyEngine fallback on error              |
|    |                                                                      |
|    +--> Partition: LLM agents | DeterministicResolver tail                |
|    |                                                                      |
|    +--> AgentContext + AgentMemory (shared state)                        |
|    |                                                                      |
|    +--> Execute agents in order                                           |
|    |     BaseAgent.run() -- ReAct loop (max 6 rounds)                    |
|    |       tool call -> ToolRegistry.execute() -> ToolResult             |
|    |                                                                      |
|    +--> AgentFeedbackService (if PO/GRN recovered -> re-reconcile)       |
|    |                                                                      |
|    +--> RecommendationService.create() / AgentEscalation                 |
|                                                                           |
+---------------------------------------------------------------------------+
```

---

## 3. End-to-End Data Flow

```
Invoice (status=READY_FOR_RECON)
  |
  v
ReconciliationRunnerService.run()
  |
  +--> ReconciliationRun created (status=RUNNING)
  |
  +--> For each invoice:
  |     _reconcile_single(run, invoice, lf_trace)
  |
  |     Step 1: POLookupService.lookup(invoice)
  |             ERPResolutionService.resolve_po()
  |             -> POLookupResult (found, po_id, po, erp_source_type, is_stale)
  |
  |     Step 1b: vendor+amount discovery fallback (if no po_number on invoice)
  |              -> backfills invoice.po_number, logs AuditEvent
  |
  |     Step 2: ReconciliationModeResolver.resolve(invoice, po)
  |             -> ModeResolutionResult (mode=TWO_WAY|THREE_WAY, policy_code, reason)
  |             Langfuse span: "mode_resolution"
  |
  |     Step 3: ReconciliationExecutionRouter.execute(invoice, po_result, mode)
  |             -> RoutedMatchOutput (header_result, line_result, grn_result)
  |             Langfuse span: "match_execution"
  |             For THREE_WAY: GRNLookupService.lookup(po)
  |                            GRNMatchService.match(invoice, po, grn_summaries)
  |
  |     Step 4: ClassificationService.classify(...) -> MatchStatus
  |             Decision tree (7 gates, deterministic)
  |
  |     Step 5: ExceptionBuilderService.build(...) -> [ReconciliationException]
  |             Applies mode tag (BOTH | THREE_WAY) to each exception
  |
  |     Step 6: ReconciliationResultService.save(...)
  |             -> ReconciliationResult (DB + ERP provenance fields)
  |             -> ReconciliationResultLine per matched pair
  |             -> bulk_create exceptions
  |             Langfuse span: "result_save"
  |
  |     Step 7: Invoice status -> RECONCILED
  |             ReviewAssignment auto-create (REQUIRES_REVIEW results)
  |             AuditEvent: RECONCILIATION_COMPLETED_SINGLE
  |
  v
ReconciliationRun finalised (COMPLETED, counts)
AuditEvent: RECONCILIATION_COMPLETED
Langfuse trace root closed with match counts

  |
  | (non-MATCHED results -- synchronous or async via Celery)
  v
AgentOrchestrator.execute(result, request_user)
  |
  +--> Guardrails: resolve_actor, authorize_orchestration, authorize_data_scope
  +--> ReasoningPlanner.plan(result) -> AgentPlan
  +--> Duplicate-run guard (reject if RUNNING AgentOrchestrationRun exists)
  +--> AgentOrchestrationRun created (RUNNING)
  +--> AgentContext + AgentMemory built
  |
  +--> For each LLM agent in plan (in priority order):
  |     BaseAgent.run(ctx) -> AgentRun (DB)
  |       GuardrailsService.authorize_agent()
  |       GuardrailsService.authorize_tool() per tool call
  |       ReAct loop: LLM -> tool_calls -> execute -> loop (max 6 rounds)
  |       AgentMemory.record_agent_output()
  |       AgentFeedbackService check (PO_RETRIEVAL, GRN_RETRIEVAL)
  |
  +--> DeterministicResolver tail (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
  |     -- or LLM agents for these, depending on plan
  |
  +--> RecommendationService.create() (dedup guard)
  +--> AgentOrchestrationRun finalised (COMPLETED | PARTIAL | FAILED)
```

---

## 4. Reconciliation Runner Service

**File:** `apps/reconciliation/services/runner_service.py`
**Class:** `ReconciliationRunnerService`

The runner is the entry point for the deterministic reconciliation layer. It is called directly from the `start_reconciliation` template view (synchronously) or from `run_reconciliation_task` Celery task (asynchronously).

### Constructor

```python
ReconciliationRunnerService()
```

On init, all sub-services are instantiated:

| Attribute | Type | Purpose |
|---|---|---|
| `self.config` | `ReconciliationConfig` | Active config (via `get_or_create()`) |
| `self.tolerance` | `ToleranceEngine` | Numeric threshold comparisons |
| `self.po_lookup` | `POLookupService` | PO resolution via ERP layer |
| `self.mode_resolver` | `ReconciliationModeResolver` | TWO_WAY vs THREE_WAY resolution |
| `self.router` | `ReconciliationExecutionRouter` | Dispatches to 2-way or 3-way |
| `self.classifier` | `ClassificationService` | Final match status decision |
| `self.exception_builder` | `ExceptionBuilderService` | Structured exception creation |
| `self.result_service` | `ReconciliationResultService` | DB persistence |

### `run(invoices, triggered_by, lf_trace)` 

```
invoices: Optional[QuerySet]  -- None means "fetch all READY_FOR_RECON"
triggered_by: Optional[User]  -- actor for audit trail
lf_trace: Optional[object]    -- parent Langfuse trace
```

**Sequence:**

1. Fetch `READY_FOR_RECON` invoices (if `invoices=None`).
2. Create `ReconciliationRun(status=RUNNING)`.
3. Open Langfuse root trace (`"reconciliation_run"`).
4. Log `RECONCILIATION_STARTED` `AuditEvent` per invoice.
5. Call `_reconcile_single()` for each invoice; count `matched / partial / unmatched / review / errors`.
6. Finalise `ReconciliationRun(status=COMPLETED, counts, completed_at)`.
7. Close Langfuse trace with counts.
8. Log `RECONCILIATION_COMPLETED` `AuditEvent`.
9. Emit `score_trace("reconciliation_match", ...)` per result.

### `_reconcile_single(run, invoice, lf_trace)`

Runs the 7-step per-invoice pipeline (see Section 3). Each step is wrapped in a `try/except` — a failure in any step records an `ERROR` result and does not abort processing for remaining invoices.

The Langfuse spans opened within `_reconcile_single` are children of the runner's root trace:

| Span name | Step |
|---|---|
| `mode_resolution` | Step 2 |
| `match_execution` | Step 3 |
| `result_save` | Step 6 |

---

## 5. Mode Resolution (3-Tier Cascade)

**File:** `apps/reconciliation/services/mode_resolver.py`
**Class:** `ReconciliationModeResolver`

Determines whether an invoice should be reconciled as `TWO_WAY` (Invoice vs PO) or `THREE_WAY` (Invoice vs PO vs GRN).

### Resolution cascade

```
Tier 1: ReconciliationPolicy lookup (explicit policy rules)
  |
  +--> Policies ordered by (priority ASC, policy_code ASC)
  +--> First matching policy wins
  +--> Matching criteria (all must be met if set):
       vendor_id, item_category, location_code, business_unit,
       is_service_invoice (bool), is_stock_invoice (bool)
       effective_from / effective_to (date-range)
  |
  v
Tier 2: Config-driven heuristics (if no policy matched)
  |
  +--> Check PO line items: is_service_item -> TWO_WAY
  +--> Check PO line items: is_stock_item -> THREE_WAY
  +--> Check item descriptions against _SERVICE_KEYWORDS -> TWO_WAY
  +--> Check item descriptions against _STOCK_KEYWORDS -> THREE_WAY
  |
  v
Tier 3: Config default mode
  +--> ReconciliationConfig.default_reconciliation_mode
       (typically THREE_WAY unless overridden)
```

### `ModeResolutionResult` dataclass

| Field | Type | Description |
|---|---|---|
| `mode` | str | `"TWO_WAY"` or `"THREE_WAY"` |
| `policy_code` | str | Matched policy code (blank if heuristic/default) |
| `policy_name` | str | Human-readable policy name |
| `reason` | str | Explanation text for audit trail |
| `grn_required` | bool | Whether GRN check is required |
| `resolution_method` | str | `"policy"` / `"heuristic"` / `"default"` |

### Service keywords (heuristic)

The heuristic compares line item descriptions against keyword sets:

| Keyword set | Signal | Mode result |
|---|---|---|
| `_SERVICE_KEYWORDS` (30 words) | services, maintenance, utilities, etc. | `TWO_WAY` |
| `_STOCK_KEYWORDS` (30 words) | frozen, fresh, packaging, inventory, etc. | `THREE_WAY` |

---

## 6. PO Lookup Service (ERP-Backed)

**File:** `apps/reconciliation/services/po_lookup_service.py`
**Class:** `POLookupService`

Resolves a Purchase Order for a given invoice using the ERP-backed resolution chain.

### `lookup(invoice) -> POLookupResult`

**Primary path (invoice has `po_number`):**

1. Call `ERPResolutionService.resolve_po(po_number=invoice.po_number)`.
2. If resolved: extract `po_id` from result value, fetch `PurchaseOrder` ORM object via `po_id` (or `po_number` normalised fallback).
3. Populate `POLookupResult` with ERP provenance fields.

**Discovery path (invoice has no `po_number`):**

```
ERPResolutionService.resolve_vendor(vendor_name=invoice.raw_vendor_name)
  -> vendor_id
    -> PurchaseOrder.objects.filter(vendor_id=vendor_id, status="OPEN")
       .filter(total_amount near invoice.total_amount within 5%)
```

If one matching PO is found, `invoice.po_number` is backfilled and saved. An `AuditEvent(RECONCILIATION_PO_DISCOVERED)` is logged.

### `POLookupResult` dataclass

| Field | Type | Description |
|---|---|---|
| `found` | bool | Whether a PO was resolved |
| `po` | Optional[PurchaseOrder] | ORM instance |
| `po_number` | str | Resolved PO number |
| `erp_source_type` | str | `ERPSourceType` value |
| `erp_confidence` | float | Resolution confidence (0.0-1.0) |
| `is_stale` | bool | Data freshness flag |
| `warnings` | List[str] | Freshness/provenance warnings |
| `erp_provenance` | dict | Full `to_provenance_dict()` output |

---

## 7. Execution Router

**File:** `apps/reconciliation/services/execution_router.py`
**Class:** `ReconciliationExecutionRouter`

Dispatches a reconciliation to the correct match pipeline based on the resolved mode.

```python
router = ReconciliationExecutionRouter(tolerance_engine)
output = router.execute(invoice, po_result, mode_resolution)
```

- If `po_result.found is False`: returns an early `RoutedMatchOutput` without running any match service.
- If `mode_resolution.mode == TWO_WAY`: delegates to `TwoWayMatchService`.
- If `mode_resolution.mode == THREE_WAY`: delegates to `ThreeWayMatchService`.

### `RoutedMatchOutput` dataclass

| Field | Type | Description |
|---|---|---|
| `mode` | str | The mode actually executed |
| `po_result` | POLookupResult | PO resolution output |
| `header_result` | Optional[HeaderMatchResult] | Header match (vendor, currency, total, tax) |
| `line_result` | Optional[LineMatchResult] | Line-level match summary |
| `grn_result` | Optional[GRNMatchResult] | GRN match (3-way only; None in 2-way) |
| `grn_required` | bool | From mode resolution |
| `grn_checked` | bool | Whether GRN lookup was attempted |
| `mode_resolution` | ModeResolutionResult | Full mode resolution metadata |

---

## 8. Two-Way Match Service

**File:** `apps/reconciliation/services/two_way_match_service.py`
**Class:** `TwoWayMatchService`

Performs Invoice vs PO matching without GRN verification.

### Sub-services

| Service | File | Responsibility |
|---|---|---|
| `HeaderMatchService` | `header_match_service.py` | Vendor, currency, totals, tax comparisons |
| `LineMatchService` | `line_match_service.py` | Deterministic multi-signal line scorer (11 weighted signals, optional LLM fallback) |

### `HeaderMatchResult` fields

| Field | Type | Meaning |
|---|---|---|
| `all_ok` | bool | True if all header checks passed |
| `vendor_match` | Optional[bool] | Vendor identity check |
| `currency_match` | Optional[bool] | Currency code check |
| `po_total_match` | Optional[bool] | Total amount within tolerance |
| `tax_match` | Optional[bool] | Tax amount within tolerance |
| `total_comparison` | Optional[FieldComparison] | Numeric diff for total |
| `tax_comparison` | Optional[FieldComparison] | Numeric diff for tax |

### `LineMatchResult` fields

| Field | Type | Meaning |
|---|---|---|
| `all_lines_matched` | bool | Every invoice line found a PO line match |
| `all_within_tolerance` | bool | All matched lines pass tolerance checks |
| `pairs` | List[LineMatchPair] | One entry per matched or unmatched invoice line |
| `unmatched_invoice_lines` | List | Invoice lines with no PO match |
| `unmatched_po_lines` | List | PO lines not referenced by any invoice line |
| `decisions` | List[LineMatchDecision] | v2: rich per-invoice-line decision with full signal breakdown |

### `LineMatchPair` fields

| Field | Type | Meaning |
|---|---|---|
| `invoice_line` | InvoiceLineItem | Invoice side |
| `po_line` | Optional[PurchaseOrderLineItem] | PO side (None if unmatched) |
| `matched` | bool | Whether a pairing was found |
| `qty_comparison` | Optional[FieldComparison] | Quantity diff + tolerance flag |
| `price_comparison` | Optional[FieldComparison] | Unit price diff + tolerance flag |
| `amount_comparison` | Optional[FieldComparison] | Line amount diff + tolerance flag |
| `decision` | Optional[LineMatchDecision] | v2: rich scoring decision with signal breakdown |

### `LineMatchDecision` fields (v2)

| Field | Type | Meaning |
|---|---|---|
| `invoice_line` | InvoiceLineItem | The invoice line being matched |
| `selected_po_line` | Optional[PurchaseOrderLineItem] | Best PO line (None if AMBIGUOUS/UNRESOLVED) |
| `status` | str | MATCHED / AMBIGUOUS / UNRESOLVED |
| `match_method` | str | EXACT / DETERMINISTIC / LLM_FALLBACK / NONE |
| `total_score` | float | Weighted composite score (0.0-1.0) |
| `confidence_band_val` | str | HIGH / GOOD / MODERATE / LOW / NONE |
| `candidate_count` | int | Number of PO line candidates scored |
| `best_score` | float | Top candidate score |
| `second_best_score` | float | Runner-up score |
| `top_gap` | float | best - second_best |
| `is_ambiguous` | bool | Ambiguity flag |
| `matched_signals` | List[str] | Signals that contributed to match |
| `rejected_signals` | List[str] | Disqualifiers / contradictions |
| `explanation` | str | Human-readable scoring explanation |
| `candidate_scores` | List[LineCandidateScore] | Full per-candidate signal breakdown |

### `LineCandidateScore` fields (v2)

| Signal | Weight | Scoring |
|---|---|---|
| `item_code_score` | 0.30 | Exact item_code match (when both sides have it) |
| `description_exact_score` | 0.20 | Normalised text equality |
| `description_token_score` | 0.15 | Jaccard token overlap (tiered: >=0.85/0.70/0.55/0.40) |
| `description_fuzzy_score` | 0.10 | RapidFuzz token_sort_ratio (tiered: >=90/80/70/60) |
| `quantity_score` | 0.10 | Quantity proximity (tiered: exact/<=2%/<=5%/<=10%) |
| `unit_price_score` | 0.07 | Unit price proximity (tiered: <=1%/<=3%/<=5%) |
| `amount_score` | 0.03 | Line amount proximity (tiered: <=1%/<=3%/<=5%) |
| `uom_score` | 0.02 | UOM equivalence map (~20 groups) |
| `category_score` | 0.01 | Category compatibility |
| `service_stock_score` | 0.01 | Service/stock flag compatibility |
| `line_number_score` | 0.01 | Same line_number alignment |

Additional per-candidate fields: `penalties`, `disqualifiers`, `matched_signals`, `decision_notes`, `matched_tokens`, raw similarity values (`token_similarity_raw`, `fuzzy_similarity_raw`, `qty_variance_pct`, `price_variance_pct`, `amount_variance_pct`).

---

## 9. Three-Way Match Service

**File:** `apps/reconciliation/services/three_way_match_service.py`
**Class:** `ThreeWayMatchService`

Extends two-way matching with GRN verification. After running the two-way match, calls `GRNLookupService` and `GRNMatchService`.

### GRN resolution

```python
GRNLookupService.lookup(purchase_order) -> List[GRNSummary]
```

`GRNSummary` fields:

| Field | Type | Description |
|---|---|---|
| `grn` | GoodsReceiptNote | ORM instance |
| `grn_number` | str | GRN identifier |
| `receipt_date` | date | Date goods received |
| `status` | str | GRN status |
| `line_items` | List[GRNLineItem] | Received quantities per line |
| `erp_source_type` | str | ERP source used (MIRROR_DB, DB_FALLBACK, etc.) |
| `erp_provenance` | dict | Full ERP provenance dict |
| `is_stale` | bool | Freshness flag |
| `warnings` | List[str] | Provenance warnings |

### GRN matching

```python
GRNMatchService.match(invoice, purchase_order, grn_summaries) -> GRNMatchResult
```

`GRNMatchResult` fields:

| Field | Type | Description |
|---|---|---|
| `grn_available` | bool | At least one GRN exists for this PO |
| `grn_fully_received` | bool | All PO lines have matching GRN receipts |
| `has_receipt_issues` | bool | Shortage, over-receipt, or partial receipt |
| `grn_summaries` | List[GRNSummary] | All matched GRNs |
| `erp_source_type` | str | Copied from the primary GRN summary |
| `erp_provenance` | dict | Copied from the primary GRN summary |
| `is_stale` | bool | Propagated from GRN summary |

### ERP provenance propagation

After `grn_match.match()`, the three-way service copies GRN provenance fields from `grn_summaries[0]` into the `GRNMatchResult`. These are then persisted to `ReconciliationResult.grn_erp_source_type` and `ReconciliationResult.erp_source_metadata_json`.

---

## 10. Tolerance Engine

**File:** `apps/reconciliation/services/tolerance_engine.py`
**Class:** `ToleranceEngine`

Encapsulates configurable percentage-based tolerance comparisons for quantity, unit price, and line amount fields.

### Strict thresholds (default)

| Field | Default tolerance |
|---|---|
| Quantity | 2.0% |
| Price (unit price) | 1.0% |
| Amount (line total) | 1.0% |

These values come from `ReconciliationConfig.quantity_tolerance_pct`, `price_tolerance_pct`, `amount_tolerance_pct`.

### Auto-close thresholds

The `PolicyEngine._within_auto_close_band()` method uses a second set of wider thresholds for PARTIAL_MATCH auto-close decisions. These come from `ReconciliationConfig`:

| Field | Default |
|---|---|
| `review_auto_close_qty_tolerance` | 5.0% |
| `review_auto_close_price_tolerance` | 3.0% |
| `review_auto_close_amount_tolerance` | 3.0% |

### `FieldComparison` dataclass

| Field | Type | Description |
|---|---|---|
| `invoice_value` | Optional[Decimal] | Value from invoice |
| `po_value` | Optional[Decimal] | Value from PO |
| `difference` | Optional[Decimal] | `invoice_value - po_value` |
| `difference_pct` | Optional[Decimal] | Percentage difference |
| `within_tolerance` | Optional[bool] | Whether diff is within threshold; `None` if either value is missing |

---

## 11. Classification Service

**File:** `apps/reconciliation/services/classification_service.py`
**Class:** `ClassificationService`

Pure deterministic decision tree. Receives all match results and outputs a `MatchStatus` value.

### Decision tree (7 gates, evaluated in order)

| Gate | Condition | Result |
|---|---|---|
| 1 | `po_result.found is False` | `UNMATCHED` |
| 2 | `invoice.is_duplicate` | `REQUIRES_REVIEW` |
| 3 | `extraction_confidence < confidence_threshold` (default 0.75) | `REQUIRES_REVIEW` |
| 4 | `header.all_ok and line.all_lines_matched and line.all_within_tolerance and grn_ok` | `MATCHED` |
| 4a | *(2-way mode)*: GRN check is skipped; only header + line conditions apply | `MATCHED` |
| 5 | `header.all_ok and line.all_lines_matched and not line.all_within_tolerance` | `PARTIAL_MATCH` |
| 6 | `header.all_ok and line.all_lines_matched` (header issue) | `PARTIAL_MATCH` |
| 7 | GRN receipt issues (3-way only) | `REQUIRES_REVIEW` |
| Default | Unmatched invoice/PO lines, or fallback | `REQUIRES_REVIEW` |

Note: `ERROR` status is set by the runner service if `_reconcile_single()` raises an unhandled exception.

---

## 12. Exception Builder Service

**File:** `apps/reconciliation/services/exception_builder_service.py`
**Class:** `ExceptionBuilderService`

Converts comparison evidence into structured `ReconciliationException` instances. Returns unsaved instances for bulk creation within a transaction.

### Exception types generated

| Category | Exception Type | Severity | Mode |
|---|---|---|---|
| PO | `PO_NOT_FOUND` | HIGH | BOTH |
| Invoice | `DUPLICATE_INVOICE` | HIGH | BOTH |
| Extraction | `EXTRACTION_LOW_CONFIDENCE` | MEDIUM | BOTH |
| Header | `VENDOR_MISMATCH` | HIGH | BOTH |
| Header | `CURRENCY_MISMATCH` | MEDIUM | BOTH |
| Header | `AMOUNT_MISMATCH` | HIGH | BOTH |
| Header | `TAX_MISMATCH` | MEDIUM | BOTH |
| Line | `QTY_MISMATCH` | MEDIUM | BOTH |
| Line | `PRICE_MISMATCH` | MEDIUM | BOTH |
| Line | `AMOUNT_MISMATCH` | HIGH | BOTH |
| Line | `UNMATCHED_INVOICE_LINE` | HIGH | BOTH |
| Line | `UNMATCHED_PO_LINE` | MEDIUM | BOTH |
| GRN | `GRN_NOT_FOUND` | HIGH | THREE_WAY |
| GRN | `RECEIPT_SHORTAGE` | HIGH | THREE_WAY |
| GRN | `INVOICE_QTY_EXCEEDS_RECEIVED` | HIGH | THREE_WAY |
| GRN | `OVER_RECEIPT` | MEDIUM | THREE_WAY |
| GRN | `MULTI_GRN_PARTIAL_RECEIPT` | MEDIUM | THREE_WAY |

All exceptions are tagged with `applies_to_mode` (`BOTH` or `THREE_WAY`) using constant lookup in `apps/core/constants.py:THREE_WAY_ONLY_EXCEPTION_TYPES`.

---

## 13. Result Service and ERP Provenance

**File:** `apps/reconciliation/services/result_service.py`
**Class:** `ReconciliationResultService`

Persists all output of the deterministic pipeline to the database within a single atomic transaction.

### Operations in `save()`

1. Create or update `ReconciliationResult` with:
   - `match_status`, `reconciliation_mode`, `mode_resolved_by`, `mode_policy_code`
   - `deterministic_confidence` (calculated from match evidence)
   - ERP provenance fields (from `po_result` and `grn_result`)
2. Create `ReconciliationResultLine` per `LineMatchPair`.
3. Bulk-create `ReconciliationException` instances returned by the builder.
4. Langfuse span: `"result_save"`.

### ERP provenance fields on `ReconciliationResult`

| Field | Source |
|---|---|
| `po_erp_source_type` | `po_result.erp_source_type` |
| `grn_erp_source_type` | `grn_result.erp_source_type` (None in 2-way) |
| `data_is_stale` | `po_result.is_stale or grn_result.is_stale` |
| `erp_source_metadata_json` | Merged dict: `{"po": po_result.erp_provenance, "grn": grn_result.erp_provenance}` |

---

## 14. Agent Orchestrator

**File:** `apps/agents/services/orchestrator.py`
**Class:** `AgentOrchestrator`

The single entry point for the AI agent layer. Called synchronously from the `start_reconciliation` view (after the deterministic run) or asynchronously from `run_agent_pipeline_task`.

### Constructor

```python
AgentOrchestrator()
  self.policy = ReasoningPlanner()
  self.decision_service = DecisionLogService()
  self.resolver = DeterministicResolver()
```

### `execute(result, request_user) -> OrchestrationResult`

**Step-by-step:**

1. **Resolve actor**: `AgentGuardrailsService.resolve_actor(request_user)` -- returns authenticated user or SYSTEM_AGENT.
2. **Authorise**: `authorize_orchestration(actor)` -- checks `agents.orchestrate` permission. Raises `PermissionDenied` on failure.
3. **Data-scope check**: `authorize_data_scope(actor, result)` -- checks `UserRole.scope_json` for `allowed_vendor_ids` and `allowed_business_units`. ADMIN and SYSTEM_AGENT bypass.
4. **TraceContext**: Build child trace context enriched with RBAC snapshot.
5. **Langfuse trace**: Open `"agent_pipeline"` trace.
6. **Plan**: `self.policy.plan(result)` -> `AgentPlan`.
7. **Skip check**: If `plan.skip_agents`: create COMPLETED orchestration run, optionally auto-close, return.
8. **Duplicate-run guard**: Query for any `RUNNING` `AgentOrchestrationRun` for this result. If found, return `OrchestrationResult(skipped=True)`.
9. **Create `AgentOrchestrationRun`** (status=RUNNING).
10. **Partition plan**: Split `plan.agents` into `llm_agents` (non-deterministic) and `deterministic_tail` (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY -- replaced by `DeterministicResolver` / `DeterministicSystemAgent` subclasses).
11. **Build `AgentContext`**: Attach `result`, `invoice_id`, `po_number`, `exceptions`, `reconciliation_mode`, RBAC fields.
12. **Build `AgentMemory`**: Pre-seed facts: `grn_available`, `grn_fully_received`, `is_two_way`, `vendor_name`, `match_status`, `extraction_confidence`.
13. **Execute LLM agents**: For each `agent_type` in `llm_agents`:
    - Instantiate from `AGENT_CLASS_REGISTRY`.
    - Check `AgentDefinition.enabled`.
    - Call `agent.run(ctx)` -> `AgentRun`.
    - Update `AgentMemory.record_agent_output()`.
    - Check feedback: `AgentFeedbackService.maybe_re_reconcile(agent_type, agent_run, ctx)`.
    - Emit `score_trace("agent_confidence", ...)`.
14. **Execute deterministic tail**: For REVIEW_ROUTING and CASE_SUMMARY, instantiate their system agent replacements (`SystemReviewRoutingAgent`, `SystemCaseSummaryAgent`) from `_SYSTEM_AGENT_REPLACEMENTS` and call `agent.run(ctx)`. For EXCEPTION_ANALYSIS, call `DeterministicResolver.resolve()` directly with synthetic `AgentRun` records. All produce `DeterministicResolution` with full auditability.
15. **Recommendations**: If plan includes `_RECOMMENDING_AGENTS`, call `RecommendationService.create()` with dedup guard.
16. **Finalize**: Update `AgentOrchestrationRun` to COMPLETED (or PARTIAL if any LLM agent failed).

### `_RECOMMENDING_AGENTS` and `_FEEDBACK_AGENTS`

```python
_RECOMMENDING_AGENTS = {"REVIEW_ROUTING", "CASE_SUMMARY"}
_FEEDBACK_AGENTS = {"PO_RETRIEVAL"}
```

Only agents in `_RECOMMENDING_AGENTS` emit formal `AgentRecommendation` DB records. The `PO_RETRIEVAL` agent triggers a feedback re-reconciliation if `found_po` appears in its output evidence.

---

## 15. Planning Layer: ReasoningPlanner and PolicyEngine

### ReasoningPlanner (primary)

**File:** `apps/agents/services/reasoning_planner.py`
**Class:** `ReasoningPlanner`

Always active -- no feature flag. Uses an LLM call to plan the agent sequence, falling back deterministically on any error.

**Contract:**
- LLM plan is always attempted for non-clean results.
- `PolicyEngine` runs first as a baseline (fast, no LLM).
- If `quick_plan.skip_agents`, the planner returns early without LLM.
- If the LLM call or JSON parsing fails, `quick_plan` (PolicyEngine result) is returned with `plan_source="deterministic"`.

**LLM planner system prompt** summarises 7 available agents and 5 hard constraints (no GRN_RETRIEVAL in TWO_WAY, CASE_SUMMARY must be last, etc.).

**LLM response schema:**

```json
{
  "overall_reasoning": "...",
  "confidence": 0.9,
  "steps": [
    {"agent_type": "EXCEPTION_ANALYSIS", "rationale": "...", "priority": 1}
  ]
}
```

Validation: unknown `agent_type` values are dropped; `GRN_RETRIEVAL` in a TWO_WAY plan raises `ValueError` (triggers fallback); `CASE_SUMMARY` out of last position raises `ValueError`.

**LLM config:** `temperature=0.0`, `max_tokens=1024`, `response_format=json_object`.

### PolicyEngine (deterministic fallback)

**File:** `apps/agents/services/policy_engine.py`
**Class:** `PolicyEngine`

Rule-based agent selector. Also used for `should_auto_close()` and `should_escalate()` checks (delegated from `ReasoningPlanner`).

### `AgentPlan` dataclass

| Field | Type | Description |
|---|---|---|
| `agents` | List[str] | Ordered list of agent type strings |
| `reason` | str | Explanation of why this plan was chosen |
| `skip_agents` | bool | True if no agents should run at all |
| `auto_close` | bool | True if result can be auto-closed |
| `reconciliation_mode` | str | Mode from result |
| `plan_source` | str | `"llm"` or `"deterministic"` |
| `plan_confidence` | float | LLM plan confidence (0.0 if deterministic) |

### PolicyEngine rules (7 rules, evaluated in order)

| Rule | Condition | Action |
|---|---|---|
| R1 | `MATCHED` and `confidence >= REVIEW_AUTO_CLOSE_THRESHOLD` | `skip_agents=True` |
| R1b | `PARTIAL_MATCH` and `_within_auto_close_band()` | `auto_close=True`, `skip_agents=True` |
| R2 | `PO_NOT_FOUND` exception exists | Add `PO_RETRIEVAL` to plan |
| R3 | `GRN_NOT_FOUND` or receipt issues (THREE_WAY only) | Add `GRN_RETRIEVAL` to plan |
| R4 | `extraction_confidence < AGENT_CONFIDENCE_THRESHOLD` | Add `INVOICE_UNDERSTANDING` to plan |
| R5 | `PARTIAL_MATCH` | Add `RECONCILIATION_ASSIST` to plan |
| Always | Any non-empty plan | Append `EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY` |
| Fallback | `REQUIRES_REVIEW` / `UNMATCHED` / `ERROR` with no prior agents | Full tail: `EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY` |

### `_within_auto_close_band(result)` (auto-close for PARTIAL_MATCH)

Returns `True` only if **all** of the following hold:

1. No `HIGH` severity exceptions.
2. All `ReconciliationResultLine` records have quantity diff within `review_auto_close_qty_tolerance` (5%).
3. All `ReconciliationResultLine` records have price diff within `review_auto_close_price_tolerance` (3%).
4. All `ReconciliationResultLine` records have amount diff within `review_auto_close_amount_tolerance` (3%).

---

## 16. Deterministic Resolver

**File:** `apps/agents/services/deterministic_resolver.py`
**Class:** `DeterministicResolver`

Replaces the `EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, and `CASE_SUMMARY` agents with deterministic rule-based logic when the exception set is unambiguous. Saves cost and latency while maintaining full auditability.

```python
DeterministicResolver.REPLACED_AGENTS = {"EXCEPTION_ANALYSIS", "REVIEW_ROUTING", "CASE_SUMMARY"}
```

REVIEW_ROUTING and CASE_SUMMARY are now executed as `DeterministicSystemAgent` subclasses
(`SystemReviewRoutingAgent`, `SystemCaseSummaryAgent`) that wrap `DeterministicResolver.resolve()`
internally while producing standard `AgentRun`, `DecisionLog`, Langfuse spans, and audit events.
EXCEPTION_ANALYSIS still uses `DeterministicResolver` directly with synthetic `AgentRun` records.

### Resolution rules (priority order)

| Priority | Condition | Recommendation |
|---|---|---|
| 0 (highest) | Prior agent recommended `AUTO_CLOSE` with confidence >= 0.80 | `AUTO_CLOSE` |
| 1 | No active exceptions | `SEND_TO_AP_REVIEW` (confidence 0.95) |
| 2 | `EXTRACTION_LOW_CONFIDENCE` exception present | `REPROCESS_EXTRACTION` |
| 3 | `VENDOR_MISMATCH` exception present | `SEND_TO_VENDOR_CLARIFICATION` |
| 4 | Any of `GRN_NOT_FOUND`, `RECEIPT_SHORTAGE`, `INVOICE_QTY_EXCEEDS_RECEIVED`, `OVER_RECEIPT`, `MULTI_GRN_PARTIAL_RECEIPT` | `SEND_TO_PROCUREMENT` |
| 5 | 3+ distinct exception categories AND at least one HIGH severity | `ESCALATE_TO_MANAGER` |
| Default | Everything else | `SEND_TO_AP_REVIEW` |

Note: Numeric mismatch types (`QTY_MISMATCH`, `PRICE_MISMATCH`, `AMOUNT_MISMATCH`, `TAX_MISMATCH`) are counted as **one** category for complexity assessment (they typically cascade from the same root cause).

### `DeterministicResolution` dataclass

| Field | Type | Description |
|---|---|---|
| `recommendation_type` | str | `RecommendationType` value |
| `confidence` | float | Deterministic confidence score |
| `reasoning` | str | Human-readable explanation |
| `evidence` | dict | Structured evidence dict |
| `case_summary` | str | Template-rendered reviewer summary |

---

## 17. BaseAgent and the ReAct Loop

**File:** `apps/agents/services/base_agent.py`
**Class:** `BaseAgent`

Abstract base class for all agents. Implements the ReAct (Reason + Act) loop with tool calling, timeout enforcement, retry logic, composite confidence scoring, and Langfuse integration.

### `AgentContext` dataclass

| Field | Type | Description |
|---|---|---|
| `reconciliation_result` | ReconciliationResult | Current result being investigated |
| `invoice_id` | int | Invoice PK |
| `po_number` | str | PO number from invoice (may be missing) |
| `exceptions` | List[dict] | Serialised exception list |
| `extra` | dict | Agent-specific extra data |
| `reconciliation_mode` | str | `"TWO_WAY"` or `"THREE_WAY"` |
| `actor_user_id` | Optional[int] | Actor for RBAC audit trail |
| `actor_primary_role` | str | Actor's primary role code |
| `actor_roles_snapshot` | List[str] | All active role codes at execution time |
| `permission_checked` | str | Permission code that was verified |
| `permission_source` | str | `"ROLE"` / `"SYSTEM_AGENT"` / `"USER"` |
| `access_granted` | bool | Whether guardrail granted access |
| `trace_id` | str | Distributed trace ID |
| `span_id` | str | Parent span ID |
| `memory` | AgentMemory | Shared in-process memory across agents |
| `_langfuse_trace` | Optional[object] | Langfuse trace object (internal) |

### `AgentOutput` dataclass

| Field | Type | Description |
|---|---|---|
| `reasoning` | str | Agent's reasoning text |
| `recommendation_type` | Optional[str] | `RecommendationType` value |
| `confidence` | float | Confidence score (0.0-1.0) |
| `evidence` | dict | Structured evidence from tools |
| `decisions` | List[dict] | Decision log entries |
| `tools_used` | List[str] | Tool names actually called |
| `raw_content` | str | Raw LLM response string |

### `run(ctx) -> AgentRun` sequence

```
1. Authorise: AgentGuardrailsService.authorize_agent(actor, agent_type)
2. Create AgentRun(status=RUNNING, RBAC fields, trace_id, span_id)
3. Stamp prompt_version from PromptRegistry.version_for(agent_type)
4. Open Langfuse span (child of ctx._langfuse_trace)
5. Load AgentDefinition: timeout_seconds, max_retries, requires_tool_grounding,
   min_tool_calls, tool_failure_confidence_cap
6. Build initial messages: [system_prompt, user_message]
7. Load tool specs from ToolRegistry for allowed_tools
8. ReAct loop (max MAX_TOOL_ROUNDS=6 rounds):
   a. Deadline check (TimeoutError if elapsed > timeout_s)
   b. LLM call with retry (_call_llm_with_retry, max_retries from AgentDefinition)
   c. Record token usage on AgentRun
   d. Save assistant message to AgentMessage
   e. If no tool_calls: finish (steps below)
   f. If tool_calls: add assistant message with tool_calls array
      For each tool call:
        - _execute_tool(name, arguments, agent_run, step)
        - Open Langfuse span per tool call
        - count failed_tool_count / total_tool_calls
        - Append tool response message (with tool_call_id + name)
        - Save to AgentMessage
9. Finish: interpret_response(content, ctx) -> AgentOutput
10. Override tools_used from runtime tracking (authoritative)
11. Compute composite confidence:
    llm_confidence - (failed_tool_penalties) - (evidence quality adjustment)
12. Apply catalog guards:
    - requires_tool_grounding: cap at 0.4 if no tools called
    - min_tool_calls: cap at 0.5 if below minimum
    - tool_failure_confidence_cap: apply catalog cap if any tool failed
13. Evidence check: if empty evidence, cap at 0.5
14. _finalise_run(agent_run, output, start)
15. Close Langfuse span with output dict
16. score_trace("agent_confidence", output.confidence)
```

### Timeout and retry configuration

| Setting | Source | Default |
|---|---|---|
| `timeout_seconds` | `AgentDefinition.timeout_seconds` | 120s (env: `AGENT_TIMEOUT_SECONDS`) |
| `max_retries` | `AgentDefinition.max_retries` | 2 (env: `AGENT_MAX_RETRIES`) |

### Composite confidence formula

```
composite = llm_confidence
  - (failed_tool_count / total_tool_calls * 0.2)  # tool failure penalty
  - (0.1 if evidence is empty or missing)           # evidence penalty
  (clamped to [0.0, 1.0])
```

Catalog-level caps are then applied on top (requires_tool_grounding, min_tool_calls, tool_failure_confidence_cap).

---

## 18. Agent Registry: All 8 Agent Types

### Agent type overview

| Agent Type | Class | ReAct? | Tools | Key Output |
|---|---|---|---|---|
| `EXCEPTION_ANALYSIS` | `ExceptionAnalysisAgent` | Yes (+ 2nd LLM call) | po_lookup, grn_lookup, invoice_details, exception_list, reconciliation_summary | Recommendation + reviewer summary |
| `INVOICE_EXTRACTION` | `InvoiceExtractionAgent` | No (single-shot) | None | Extracted invoice fields |
| `INVOICE_UNDERSTANDING` | `InvoiceUnderstandingAgent` | Yes | invoice_details, po_lookup, vendor_search | Quality assessment |
| `PO_RETRIEVAL` | `PORetrievalAgent` | Yes | po_lookup, vendor_search, invoice_details | `found_po` in evidence |
| `GRN_RETRIEVAL` | `GRNRetrievalAgent` | Yes | grn_lookup, po_lookup, invoice_details | GRN availability confirmation |
| `REVIEW_ROUTING` | `ReviewRoutingAgent` | Yes | reconciliation_summary, exception_list | Routing recommendation |
| `CASE_SUMMARY` | `CaseSummaryAgent` | Yes | invoice_details, po_lookup, grn_lookup, reconciliation_summary, exception_list | Human-readable summary |
| `RECONCILIATION_ASSIST` | `ReconciliationAssistAgent` | Yes | invoice_details, po_lookup, grn_lookup, reconciliation_summary, exception_list | Discrepancy analysis |

---

### `ExceptionAnalysisAgent`

**Agent type:** `EXCEPTION_ANALYSIS`
**System prompt key:** `agent.exception_analysis`

**Special behaviour:** After the ReAct loop completes, makes a second LLM call (`_generate_reviewer_summary()`) to produce a structured reviewer-facing summary.

Reviewer summary fields persisted on `ReviewAssignment`:

| Field | Description |
|---|---|
| `reviewer_summary` | Narrative case summary |
| `reviewer_risk_level` | Risk classification |
| `reviewer_confidence` | Summary confidence |
| `reviewer_recommendation` | Recommended action |
| `reviewer_suggested_actions` | Numbered action list |
| `reviewer_summary_generated_at` | Timestamp |

Also logged via `AgentTraceService.log_agent_decision(decision_type="REVIEWER_SUMMARY")`.

**User message fields:** `mode_context`, `result.pk`, `invoice_id`, `po_number`, `match_status`, `extraction_confidence`, `exceptions` (JSON serialised).

---

### `InvoiceExtractionAgent`

**Agent type:** `INVOICE_EXTRACTION`
**System prompt key:** `extraction.invoice_system` (or composed from `ctx.extra["composed_prompt"]`)
**Mode:** Single-shot (no tools), `temperature=0.0`, `response_format=json_object`

Used during the extraction pipeline (not typically included in the reconciliation agent plan). When invoked standalone, opens its own Langfuse trace.

Captures prompt composition metadata (`prompt_source_type`, `prompt_hash`, `base_prompt_key`, `category_prompt_key`, `country_prompt_key`) and persists it to `AgentRun.input_payload["_prompt_meta"]`.

---

### `InvoiceUnderstandingAgent`

**Agent type:** `INVOICE_UNDERSTANDING`
**System prompt key:** `agent.invoice_understanding`
**Tools:** `invoice_details`, `po_lookup`, `vendor_search`

Invoked when `extraction_confidence < AGENT_CONFIDENCE_THRESHOLD`. Analyses extraction quality and flags issues via `AgentMemory.extraction_issues`.

User message includes: `invoice_id`, `po_number`, `extraction_confidence`, `match_status`, `validation_warnings` (from memory or extra).

---

### `PORetrievalAgent`

**Agent type:** `PO_RETRIEVAL`
**System prompt key:** `agent.po_retrieval`
**Tools:** `po_lookup`, `vendor_search`, `invoice_details`

Invoked when the deterministic PO lookup failed (exception type `PO_NOT_FOUND`). Normalises evidence to always place the recovered PO number under `evidence["found_po"]` so the orchestrator feedback loop can read it.

Evidence fallback keys checked (in order): `found_po`, `po_number`, `matched_po`, `result`, `found`, `po`.

Triggers `AgentFeedbackService.maybe_re_reconcile()` if `found_po` is non-empty.

---

### `GRNRetrievalAgent`

**Agent type:** `GRN_RETRIEVAL`
**System prompt key:** `agent.grn_retrieval`
**Tools:** `grn_lookup`, `po_lookup`, `invoice_details`

**Only invoked in THREE_WAY mode.** The PolicyEngine suppresses this agent for TWO_WAY reconciliations, and the LLM planner has a hard rule that rejects any plan including GRN_RETRIEVAL for a TWO_WAY result.

User message pre-checks `ctx.reconciliation_mode == "TWO_WAY"` and returns an early no-op JSON if somehow invoked in the wrong mode.

Pre-seeds memory context: `grn_available`, `grn_fully_received`.

---

### `ReviewRoutingAgent`

**Agent type:** `REVIEW_ROUTING`
**System prompt key:** `agent.review_routing`
**Tools:** `reconciliation_summary`, `exception_list`

In `_RECOMMENDING_AGENTS` -- produces a formal `AgentRecommendation` record. Receives full prior agent findings from `AgentMemory.agent_summaries` and `AgentMemory.current_recommendation`.

User message: `reconciliation_result.pk`, `match_status`, `exceptions` JSON, prior agent findings block.

---

### `CaseSummaryAgent`

**Agent type:** `CASE_SUMMARY`
**System prompt key:** `agent.case_summary`
**Tools:** `invoice_details`, `po_lookup`, `grn_lookup`, `reconciliation_summary`, `exception_list`

Always the **last** agent in any plan. In `_RECOMMENDING_AGENTS`. Produces a human-readable case summary using all prior agent memory and the full tool set.

User message: `result.pk`, `invoice_id`, `po_number`, `match_status`, condensed prior analysis (first 100 chars per agent), current recommendation + confidence.

---

### `ReconciliationAssistAgent`

**Agent type:** `RECONCILIATION_ASSIST`
**System prompt key:** `agent.reconciliation_assist`
**Tools:** `invoice_details`, `po_lookup`, `grn_lookup`, `reconciliation_summary`, `exception_list`

Invoked for `PARTIAL_MATCH` results to investigate the source of discrepancies. Aware of any PO number recovered by a prior `PORetrievalAgent` via `AgentMemory.resolved_po_number`.

---

## 19. Agent Memory

**File:** `apps/agents/services/agent_memory.py`
**Class:** `AgentMemory`

Plain Python dataclass (no DB). Created once per orchestration run, passed through `AgentContext.memory`. Every agent can read and write to it.

### Fields

| Field | Type | Description |
|---|---|---|
| `resolved_po_number` | Optional[str] | PO number recovered by `PORetrievalAgent` |
| `resolved_grn_numbers` | List[str] | GRN numbers confirmed by `GRNRetrievalAgent` |
| `extraction_issues` | List[str] | Quality issues from `InvoiceUnderstandingAgent` |
| `agent_summaries` | Dict[str, str] | Reasoning summaries keyed by `agent_type` (max 500 chars each) |
| `current_recommendation` | Optional[str] | Highest-confidence recommendation seen so far |
| `current_confidence` | float | Confidence of `current_recommendation` |
| `facts` | Dict[str, Any] | Free-form key-value store |

### Pre-seeded facts (by orchestrator)

| Key | Source |
|---|---|
| `grn_available` | `result.grn_available` |
| `grn_fully_received` | `result.grn_fully_received` |
| `is_two_way` | `result.reconciliation_mode == "TWO_WAY"` |
| `vendor_name` | `invoice.vendor.name` or `invoice.raw_vendor_name` |
| `match_status` | `result.match_status` |
| `extraction_confidence` | `result.extraction_confidence` |

### `record_agent_output(agent_type, output)` mutation

After each agent run, the orchestrator calls `memory.record_agent_output()`:

1. Saves `output.reasoning[:500]` to `agent_summaries[agent_type]`.
2. If `output.confidence > current_confidence`: promotes recommendation.
3. If `output.evidence["found_po"]` exists and is a non-empty string: sets `resolved_po_number`.

---

## 20. RBAC and Guardrails Service

**File:** `apps/agents/services/guardrails_service.py`
**Class:** `AgentGuardrailsService`

Single responsibility: RBAC checks and audit. No business logic. Fail-closed design -- unknown actors resolve to SYSTEM_AGENT (not admin bypass).

### Permission maps

**Orchestration:**

| Permission | Required for |
|---|---|
| `agents.orchestrate` | Any `AgentOrchestrator.execute()` call |

**Per-agent permissions:**

| Agent Type | Permission |
|---|---|
| `INVOICE_EXTRACTION` | `agents.run_extraction` |
| `INVOICE_UNDERSTANDING` | `agents.run_extraction` |
| `PO_RETRIEVAL` | `agents.run_po_retrieval` |
| `GRN_RETRIEVAL` | `agents.run_grn_retrieval` |
| `RECONCILIATION_ASSIST` | `agents.run_reconciliation_assist` |
| `EXCEPTION_ANALYSIS` | `agents.run_exception_analysis` |
| `REVIEW_ROUTING` | `agents.run_review_routing` |
| `CASE_SUMMARY` | `agents.run_case_summary` |

**Per-tool permissions:**

| Tool | Permission |
|---|---|
| `po_lookup` | `purchase_orders.view` |
| `grn_lookup` | `grns.view` |
| `vendor_search` | `vendors.view` |
| `invoice_details` | `invoices.view` |
| `exception_list` | `reconciliation.view` |
| `reconciliation_summary` | `reconciliation.view` |

**Recommendation permissions:**

| Recommendation Type | Permission |
|---|---|
| `AUTO_CLOSE` | `recommendations.auto_close` |
| `SEND_TO_AP_REVIEW` | `recommendations.route_review` |
| `ESCALATE_TO_MANAGER` | `recommendations.escalate` |
| `REPROCESS_EXTRACTION` | `recommendations.reprocess` |
| `SEND_TO_PROCUREMENT` | `recommendations.route_procurement` |
| `SEND_TO_VENDOR_CLARIFICATION` | `recommendations.vendor_clarification` |

**Action permissions:**

| Action | Permission |
|---|---|
| `auto_close_result` | `recommendations.auto_close` |
| `assign_review` | `reviews.assign` |
| `escalate_case` | `cases.escalate` |
| `reprocess_extraction` | `extraction.reprocess` |
| `rerun_reconciliation` | `reconciliation.run` |

### SYSTEM_AGENT identity

When `request_user` is unauthenticated or absent (Celery task): `resolve_actor()` returns (or creates) `system-agent@internal`. This user is assigned the `SYSTEM_AGENT` role via `_assign_system_agent_role()`. `SYSTEM_AGENT` and `ADMIN` always bypass data-scope checks.

### Data-scope enforcement

`authorize_data_scope(actor, result)` reads `UserRole.scope_json` for all active role assignments. Scope keys:

- `allowed_business_units: List[str]` -- result's `invoice.business_unit` must be in list (if set).
- `allowed_vendor_ids: List[int]` -- result's `invoice.vendor_id` must be in list (if set).

Multiple roles grant **additive** scope (union).

### Guardrail audit logging

Every guardrail decision (grant or deny) is logged as:
- `AuditEvent` with type `GUARDRAIL_GRANTED` or `GUARDRAIL_DENIED`.
- `score_trace("rbac_guardrail", 1.0 | 0.0)` to Langfuse.

---

## 21. Tool Registry: All 6 Tools

**File:** `apps/tools/registry/tools.py` (concrete tools)
**File:** `apps/tools/registry/base.py` (BaseTool, ToolRegistry, `@register_tool`)

All tools extend `BaseTool` and are registered via the `@register_tool` decorator. The registry is a module-level singleton; tools are loaded at import time.

**Multi-Tenant Scoping:** All tools use `self._scoped(queryset)` on every DB query. The tenant is injected from `AgentContext.tenant` via `BaseAgent._execute_tool()` -> `BaseTool.execute()`. When a tenant is set, `_scoped()` applies `.filter(tenant=self._tenant)` to ensure tools only access data within the correct tenant boundary. See [MULTI_TENANT.md](MULTI_TENANT.md).

### `BaseTool` interface

Each tool declares:

| Attribute | Description |
|---|---|
| `name` | Short unique identifier (the function name in tool specs) |
| `description` | What the tool does (shown to LLM in tool spec) |
| `when_to_use` | Guidance on appropriate use cases |
| `when_not_to_use` | Anti-patterns to avoid |
| `no_result_meaning` | What an empty result means |
| `failure_handling_instruction` | How to handle tool failures |
| `authoritative_fields` | Fields the LLM can trust as ground truth |
| `evidence_keys_produced` | Keys that appear in the returned data dict |
| `parameters_schema` | JSON Schema for tool arguments |
| `required_permission` | RBAC permission code checked before execution |

### Tool: `po_lookup`

**Class:** `POLookupTool`
**Permission:** `purchase_orders.view`

**Parameters:**

| Param | Type | Optional | Description |
|---|---|---|---|
| `po_number` | string | Yes | PO number (supports partial/contains match) |
| `vendor_id` | integer | Yes | Vendor PK -- list open POs for this vendor |

**Resolution order:**
1. ERP resolution via `ERPResolutionService.with_default_connector().resolve_po()`.
2. If import fails: direct `PurchaseOrder` ORM lookup (exact, normalized, contains).

**ERP metadata in response:**

| Key | Description |
|---|---|
| `_erp_source` | `ERPSourceType` value |
| `_erp_confidence` | Resolution confidence |
| `_erp_fallback_used` | Whether DB fallback was used |
| `_erp_is_stale` | Data freshness flag |

**Evidence keys produced:** `found`, `po_number`, `vendor`, `vendor_id`, `total_amount`, `currency`, `status`, `line_items`.

---

### Tool: `grn_lookup`

**Class:** `GRNLookupTool`
**Permission:** `grns.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `po_number` | string | Yes | Find GRNs for this PO |

**Resolution order:**
1. ERP resolution via `ERPResolutionService.with_default_connector().resolve_grn()`.
2. If import fails: direct `GoodsReceiptNote` ORM query.

**Evidence keys produced:** `found`, `po_number`, `grn_count`, `grns` (list).

Each GRN entry: `grn_number`, `receipt_date`, `status`, `warehouse`, `line_items` (with `quantity_received`, `quantity_accepted`, `quantity_rejected`).

---

### Tool: `vendor_search`

**Class:** `VendorSearchTool`
**Permission:** `vendors.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Vendor name, code, or alias |

**Lookup strategy:**
1. Name/code: `Q(code__iexact=raw) | Q(normalized_name=normalized) | Q(name__icontains=raw)` -- active vendors only.
2. Alias: `VendorAlias.objects.filter(normalized_alias=normalized)`.

**Evidence keys produced:** `query`, `count`, `vendors`.

Each vendor entry: `vendor_id`, `code`, `name`, `match_type` (`"direct"` or `"alias"`), optionally `alias`.

---

### Tool: `invoice_details`

**Class:** `InvoiceDetailsTool`
**Permission:** `invoices.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `invoice_id` | integer | Yes | Invoice PK |

Returns the full extracted invoice including normalised line items. Tax percentage is inferred from raw JSON or calculated from `tax_amount / subtotal` if not directly available.

**Evidence keys produced:** `invoice_id`, `invoice_number`, `vendor`, `vendor_id`, `po_number`, `invoice_date`, `currency`, `subtotal`, `tax_percentage`, `tax_amount`, `total_amount`, `status`, `extraction_confidence`, `is_duplicate`, `line_items`.

---

### Tool: `exception_list`

**Class:** `ExceptionListTool`
**Permission:** `reconciliation.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `reconciliation_result_id` | integer | Yes | ReconciliationResult PK |

Returns all exceptions for the result from `ReconciliationException`.

**Evidence keys produced:** `reconciliation_result_id`, `exceptions` (list with `id`, `exception_type`, `severity`, `message`, `resolved`).

---

### Tool: `reconciliation_summary`

**Class:** `ReconciliationSummaryTool`
**Permission:** `reconciliation.view`

**Parameters:**

| Param | Type | Required | Description |
|---|---|---|---|
| `reconciliation_result_id` | integer | Yes | ReconciliationResult PK |

Returns the reconciliation summary: match status, mode, confidence, exception counts, key line-level discrepancies.

**Evidence keys produced:** `reconciliation_result_id`, `match_status`, `reconciliation_mode`, `deterministic_confidence`, `exception_count`, `exception_types`, `line_count`, `matched_lines`, `within_tolerance_lines`.

---

## 22. Agent Feedback Loop

**File:** `apps/reconciliation/services/agent_feedback_service.py`
**Class:** `AgentFeedbackService`

When `PORetrievalAgent` (or `GRNRetrievalAgent`) recovers a missing document, the orchestrator calls `AgentFeedbackService.maybe_re_reconcile()`. This atomically:

1. Re-links the invoice to the recovered PO number (`invoice.po_number = found_po`).
2. Triggers `ReconciliationRunnerService._reconcile_single()` again for the same invoice.
3. Updates `AgentMemory.resolved_po_number`.
4. Only runs if `last_output.status == COMPLETED`.

This allows a successfully recovered PO to produce a `MATCHED` result in the same orchestration run without requiring a full re-reconciliation job.

---

## 23. Data Models

### `ReconciliationRun`

**Table:** `reconciliation_run`

| Field | Type | Description |
|---|---|---|
| `status` | CharField | `PENDING / RUNNING / COMPLETED / FAILED` |
| `triggered_by` | FK(User) | Actor who started the run |
| `started_at` | DateTimeField | |
| `completed_at` | DateTimeField | |
| `total_invoices` | PositiveIntegerField | Total processed |
| `matched_count` | PositiveIntegerField | |
| `partial_match_count` | PositiveIntegerField | |
| `unmatched_count` | PositiveIntegerField | |
| `requires_review_count` | PositiveIntegerField | |
| `error_count` | PositiveIntegerField | |
| `trace_id` | CharField | Distributed trace ID |

### `ReconciliationResult`

**Table:** `reconciliation_result`

| Field | Type | Description |
|---|---|---|
| `invoice` | FK(Invoice) | |
| `purchase_order` | FK(PurchaseOrder) | |
| `reconciliation_run` | FK(ReconciliationRun) | |
| `match_status` | CharField | `MATCHED / PARTIAL_MATCH / UNMATCHED / REQUIRES_REVIEW / ERROR` |
| `reconciliation_mode` | CharField | `TWO_WAY / THREE_WAY` |
| `mode_resolved_by` | CharField | `"policy"` / `"heuristic"` / `"default"` |
| `mode_policy_code` | CharField | Policy code if mode was policy-resolved |
| `deterministic_confidence` | FloatField | Confidence from deterministic engine |
| `extraction_confidence` | FloatField | Copied from invoice |
| `grn_available` | BooleanField | At least one GRN exists |
| `grn_fully_received` | BooleanField | All PO lines received |
| `po_erp_source_type` | CharField | ERP source used for PO data |
| `grn_erp_source_type` | CharField | ERP source used for GRN data |
| `data_is_stale` | BooleanField | Whether any source data is stale |
| `erp_source_metadata_json` | JSONField | Full ERP provenance dict (`{"po": {...}, "grn": {...}}`) |
| `summary` | TextField | Agent-generated summary (ASCII-safe) |
| `trace_id` | CharField | Distributed trace ID |
| `agent_trace_id` | CharField | Agent pipeline trace ID |

### `ReconciliationResultLine`

**Table:** `reconciliation_result_line`

| Field | Type | Description |
|---|---|---|
| `result` | FK(ReconciliationResult) | Parent result |
| `invoice_line` | FK(InvoiceLineItem) | |
| `po_line` | FK(PurchaseOrderLineItem) | |
| `qty_difference_pct` | DecimalField | Quantity percentage difference |
| `price_difference_pct` | DecimalField | Unit price percentage difference |
| `amount_difference_pct` | DecimalField | Line amount percentage difference |
| `qty_within_tolerance` | BooleanField | |
| `price_within_tolerance` | BooleanField | |
| `amount_within_tolerance` | BooleanField | |
| `match_method` | CharField(20) | EXACT / DETERMINISTIC / LLM_FALLBACK / NONE |
| `match_confidence` | DecimalField(5,4) | Composite score 0.0000-1.0000 |
| `confidence_band` | CharField(20) | HIGH / GOOD / MODERATE / LOW / NONE |
| `description_match_score` | DecimalField(5,4) | Exact description signal score |
| `token_similarity_score` | DecimalField(5,4) | Jaccard token overlap score |
| `fuzzy_similarity_score` | DecimalField(5,4) | RapidFuzz fuzzy score |
| `quantity_match_score` | DecimalField(5,4) | Quantity proximity score |
| `price_match_score` | DecimalField(5,4) | Price proximity score |
| `amount_match_score` | DecimalField(5,4) | Amount proximity score |
| `candidate_count` | PositiveIntegerField | Number of PO lines scored |
| `is_ambiguous` | BooleanField | Whether ambiguity was detected |
| `matched_signals` | JSONField | List of signals that contributed |
| `rejected_signals` | JSONField | List of disqualifiers/contradictions |
| `line_match_meta` | JSONField | Full decision metadata (top_gap, second_best, matched_tokens, decision_notes) |

### `ReconciliationException`

**Table:** `reconciliation_exception`

| Field | Type | Description |
|---|---|---|
| `result` | FK(ReconciliationResult) | |
| `result_line` | FK(ReconciliationResultLine) | Nullable |
| `exception_type` | CharField | `ExceptionType` enum value |
| `severity` | CharField | `HIGH / MEDIUM / LOW` |
| `message` | TextField | Human-readable description |
| `details` | JSONField | Structured data (amounts, pct diff, etc.) |
| `resolved` | BooleanField | Whether exception has been manually resolved |
| `applies_to_mode` | CharField | `BOTH / THREE_WAY` |

### `AgentDefinition`

**Table:** `agents_definition`

| Field | Type | Description |
|---|---|---|
| `agent_type` | CharField | Unique `AgentType` value |
| `enabled` | BooleanField | Whether this agent can be invoked |
| `max_retries` | PositiveIntegerField | LLM retry count |
| `timeout_seconds` | PositiveIntegerField | Per-run timeout |
| `config_json` | JSONField | `{"allowed_tools": [...]}` per agent |
| `purpose` | TextField | Contract field |
| `entry_conditions` | TextField | Contract field |
| `success_criteria` | TextField | Contract field |
| `prohibited_actions` | JSONField | List of forbidden recommendation types |
| `requires_tool_grounding` | BooleanField | Must call at least one tool |
| `min_tool_calls` | PositiveIntegerField | Minimum successful tool calls |
| `tool_failure_confidence_cap` | FloatField | Max confidence when a tool fails |
| `allowed_recommendation_types` | JSONField | Null = all allowed |
| `default_fallback_recommendation` | CharField | Used when output is invalid |
| `output_schema_name` | CharField | e.g., `"AgentOutputSchema"` |
| `output_schema_version` | CharField | e.g., `"v1"` |
| `lifecycle_status` | CharField | `draft / active / deprecated` |
| `owner_team` | CharField | |
| `capability_tags` | JSONField | e.g., `["retrieval", "routing"]` |
| `domain_tags` | JSONField | e.g., `["po", "grn", "vendor"]` |

### `AgentRun`

**Table:** `agents_run`

| Field | Type | Description |
|---|---|---|
| `agent_type` | CharField | `AgentType` value |
| `agent_definition` | FK(AgentDefinition) | |
| `reconciliation_result` | FK(ReconciliationResult) | |
| `document_upload` | FK(DocumentUpload) | For extraction runs |
| `status` | CharField | `PENDING / RUNNING / COMPLETED / FAILED / SKIPPED` |
| `input_payload` | JSONField | Initial context |
| `output_payload` | JSONField | `AgentOutput` serialised |
| `summarized_reasoning` | TextField | ASCII-safe reasoning summary |
| `confidence` | FloatField | Final composite confidence |
| `started_at` / `completed_at` | DateTimeField | |
| `duration_ms` | PositiveIntegerField | |
| `error_message` | TextField | |
| `trace_id` / `span_id` | CharField | Distributed trace IDs |
| `prompt_version` | CharField | Prompt hash (max 50 chars) |
| `invocation_reason` | CharField | e.g., `"auto:PARTIAL_MATCH"` |
| `actor_user_id` | PositiveIntegerField | |
| `actor_primary_role` | CharField | e.g., `"SYSTEM_AGENT"` |
| `actor_roles_snapshot_json` | JSONField | Role codes at execution time |
| `permission_source` | CharField | `ROLE / SYSTEM_AGENT / USER` |
| `access_granted` | BooleanField | Guardrail decision |
| `llm_model_used` | CharField | |
| `prompt_tokens` | PositiveIntegerField | Cumulative across all rounds |
| `completion_tokens` | PositiveIntegerField | |
| `total_tokens` | PositiveIntegerField | |
| `cost_estimate` | DecimalField | |

### `AgentOrchestrationRun`

**Table:** `agents_orchestration_run`

| Field | Type | Description |
|---|---|---|
| `reconciliation_result` | FK(ReconciliationResult) | |
| `status` | CharField | `PLANNED / RUNNING / COMPLETED / PARTIAL / FAILED` |
| `plan_source` | CharField | `"llm"` or `"deterministic"` |
| `plan_agents` | JSONField | List of agent types in the plan |
| `agents_completed` | JSONField | List of completed agent types |
| `agents_failed` | JSONField | List of failed agent types |
| `started_at` / `completed_at` | DateTimeField | |
| `actor_user_id` | PositiveIntegerField | |

Acts as the **duplicate-run guard**: the orchestrator rejects entry if a `RUNNING` record exists for the same `reconciliation_result`.

### `AgentRecommendation`

**Table:** `agents_recommendation`

| Field | Type | Description |
|---|---|---|
| `agent_run` | FK(AgentRun) | |
| `reconciliation_result` | FK(ReconciliationResult) | |
| `recommendation_type` | CharField | `RecommendationType` value |
| `confidence` | FloatField | |
| `reasoning` | TextField | |
| `accepted` | BooleanField | Whether AP team accepted |
| `accepted_by` | FK(User) | |
| `accepted_at` | DateTimeField | |

**Uniqueness constraint:** `(reconciliation_result, recommendation_type, agent_run)` -- prevents duplicate recommendations. `RecommendationService.log_recommendation()` also checks for any PENDING recommendation of the same `(result, type)` before creating.

---

## 24. Prompt Registry Integration

**Module:** `apps/core/prompt_registry.py`

Agents retrieve their system prompts by key at runtime. The registry supports prompt versioning and caching.

### Prompt keys used by reconciliation agents

| Agent | Prompt Key |
|---|---|
| `ExceptionAnalysisAgent` | `agent.exception_analysis` |
| `InvoiceExtractionAgent` | `extraction.invoice_system` (or composed) |
| `InvoiceUnderstandingAgent` | `agent.invoice_understanding` |
| `PORetrievalAgent` | `agent.po_retrieval` |
| `GRNRetrievalAgent` | `agent.grn_retrieval` |
| `ReviewRoutingAgent` | `agent.review_routing` |
| `CaseSummaryAgent` | `agent.case_summary` |
| `ReconciliationAssistAgent` | `agent.reconciliation_assist` |

Prompt versions are stamped on `AgentRun.prompt_version` (prompt hash, max 50 chars) for auditability.

Seed prompts via: `python manage.py seed_prompts`

---

## 25. LLM Client Configuration

**File:** `apps/agents/services/llm_client.py`
**Class:** `LLMClient`

Supports both Azure OpenAI and standard OpenAI. Configured via environment variables:

| Setting | Env Var | Default |
|---|---|---|
| API type | `AZURE_OPENAI_API_KEY` (presence selects Azure) | OpenAI |
| Azure endpoint | `AZURE_OPENAI_ENDPOINT` | |
| Azure deployment | `AZURE_OPENAI_DEPLOYMENT` | |
| OpenAI API key | `OPENAI_API_KEY` | |
| Default model | `OPENAI_MODEL` | `gpt-4o` |

### Per-agent temperature settings

| Agent | Temperature | Notes |
|---|---|---|
| `InvoiceExtractionAgent` | 0.0 | Deterministic extraction |
| `ReasoningPlanner` | 0.0 | Deterministic planning |
| All ReAct agents | 0.2 (default) | Allows some creativity for investigation |

### Token limits

| Context | `max_tokens` |
|---|---|
| ReAct loop LLM calls | 4096 (default) |
| Reviewer summary call (ExceptionAnalysis) | 2048 |
| Planner call | 1024 |
| Extraction agent | 8192 |

---

## 26. Langfuse Observability

The reconciliation and agent pipeline emits rich Langfuse traces. All Langfuse calls are fail-silent (errors are caught and ignored).

### Trace structure

```
Root trace: "reconciliation_run" (trace_id = run.trace_id or str(run.pk))
  |
  +--> child span: "po_lookup"      (per invoice, step 1 -- PO lookup with ERP provenance)
  |     |
  |     +--> child span: "erp_resolution"  (when ERP connector available via lf_parent_span)
  |           +--> "erp_cache_lookup"      (BaseResolver cache check)
  |           +--> "erp_live_lookup"       (BaseResolver live API call)
  |           +--> "erp_db_fallback"       (BaseResolver DB fallback)
  |
  +--> child span: "mode_resolution"  (per invoice, step 2)
  +--> child span: "grn_lookup"       (per invoice, THREE_WAY only, step 3)
  +--> child span: "match_execution"  (per invoice, step 4)
  +--> child span: "classification"   (per invoice, step 5)
  +--> child span: "result_persist"   (per invoice, step 6)
  +--> child span: "exception_build"  (per invoice, step 7)
  +--> child span: "review_workflow_trigger" (per invoice, step 8)
  |
  +--> score: "reconciliation_match" (MATCHED=1.0 | PARTIAL=0.5 | REQUIRES_REVIEW=0.3 | UNMATCHED=0.0)

Root trace: "agent_pipeline" (trace_id = reconciliation_result.agent_trace_id)
  |
  +--> child span per agent: "agent_{AGENT_TYPE}"
  |     |
  |     +--> child span per tool call: "tool_{tool_name}"
  |           |
  |           +--> child span: "erp_resolution"  (for po_lookup / grn_lookup tools)
  |                 +--> "erp_cache_lookup" / "erp_live_lookup" / "erp_db_fallback"
  |
  +--> score: "agent_confidence" per agent run
  +--> score: "rbac_guardrail" per guardrail decision (1.0=granted | 0.0=denied)
  +--> score: "rbac_data_scope" (0.0 on deny path only)
```

### ERP resolution span threading

`POLookupService.lookup()` and `GRNLookupService.lookup()` accept `lf_parent_span=`
and pass it to `ERPResolutionService.resolve_po/grn()`. In the reconciliation runner,
`_lf_po` (the po_lookup span) is passed as the parent so ERP spans nest properly.

For agent tools, `BaseAgent._execute_tool()` injects `lf_parent_span=_tool_span`
into tool kwargs. `POLookupTool` and `GRNLookupTool` forward it to the ERP resolver.

ERP resolution spans emit 6 observation scores (success, latency_ok, result_present,
fresh, authoritative, used_fallback). Per-stage spans (cache, live, fallback) emit
additional scores. All metadata is sanitised via `langfuse_helpers.sanitize_erp_metadata()`.

**Full ERP tracing reference**: [LANGFUSE_OBSERVABILITY.md §7.7](LANGFUSE_OBSERVABILITY.md)

### Score value conventions

| Score Name | Values | Emitted by |
|---|---|---|
| `reconciliation_match` | MATCHED=1.0, PARTIAL=0.5, REQUIRES_REVIEW=0.3, UNMATCHED=0.0 | `ReconciliationRunnerService.run()` |
| `agent_confidence` | 0.0-1.0 composite | `BaseAgent.run()` |
| `rbac_guardrail` | 1.0=granted, 0.0=denied | `AgentGuardrailsService.authorize_*()` |
| `rbac_data_scope` | 0.0 (deny path only) | `authorize_data_scope()` |

---

## 27. Audit Trail and Governance

### AuditEvent types emitted by the reconciliation layer

| Event Type | Trigger |
|---|---|
| `RECONCILIATION_STARTED` | Per invoice at start of `run()` |
| `RECONCILIATION_COMPLETED_SINGLE` | After each invoice in `_reconcile_single()` |
| `RECONCILIATION_COMPLETED` | After all invoices in `run()` |
| `RECONCILIATION_PO_DISCOVERED` | Vendor+amount discovery backfills `invoice.po_number` |
| `RECONCILIATION_ERROR` | Unhandled exception in `_reconcile_single()` |

### AuditEvent types emitted by the agent layer

| Event Type | Trigger |
|---|---|
| `GUARDRAIL_GRANTED` | Successful RBAC check in `AgentGuardrailsService` |
| `GUARDRAIL_DENIED` | Failed RBAC check |
| `TOOL_CALL_AUTHORIZED` | Tool permission check granted |
| `TOOL_CALL_DENIED` | Tool permission denied |
| `RECOMMENDATION_ACCEPTED` | Recommendation accepted by AP team |
| `RECOMMENDATION_DENIED` | Recommendation rejected |
| `AUTO_CLOSE_AUTHORIZED` | Auto-close authorized |
| `AUTO_CLOSE_DENIED` | Auto-close denied |
| `SYSTEM_AGENT_USED` | SYSTEM_AGENT identity was resolved |

### `AgentTraceService` (persistence)

**File:** `apps/agents/services/agent_trace_service.py`

Single entry point for recording all agent activity:

- `log_agent_run(agent_run)` -- persists step-level detail
- `log_tool_call(agent_run, tool_name, args, result, duration_ms)` -- `ToolCall` model
- `log_agent_decision(agent_run, decision_type, content)` -- `DecisionLog` model

### `DecisionLog` model

| Field | Type | Description |
|---|---|---|
| `agent_run` | FK(AgentRun) | |
| `decision_type` | CharField | e.g., `"RECOMMENDATION"`, `"REVIEWER_SUMMARY"` |
| `rationale` | TextField | ASCII-safe decision text |
| `recommendation_type` | CharField | If a recommendation decision |
| `confidence` | FloatField | |
| `created_at` | DateTimeField | |

---

## 28. Configuration Reference

### `ReconciliationConfig` fields

| Field | Default | Description |
|---|---|---|
| `quantity_tolerance_pct` | 2.0 | Strict qty tolerance (%) |
| `price_tolerance_pct` | 1.0 | Strict price tolerance (%) |
| `amount_tolerance_pct` | 1.0 | Strict amount tolerance (%) |
| `review_auto_close_qty_tolerance` | 5.0 | Auto-close band qty (%) |
| `review_auto_close_price_tolerance` | 3.0 | Auto-close band price (%) |
| `review_auto_close_amount_tolerance` | 3.0 | Auto-close band amount (%) |
| `review_auto_close_confidence_threshold` | 0.85 | Min deterministic confidence for auto-close |
| `extraction_confidence_threshold` | 0.75 | Below this -> REQUIRES_REVIEW |
| `enable_mode_resolver` | True | Enable 2-way/3-way mode resolution |
| `default_reconciliation_mode` | THREE_WAY | Fallback mode |

### Environment variables (agent pipeline)

| Variable | Default | Description |
|---|---|---|
| `AGENT_TIMEOUT_SECONDS` | 120 | Per-agent run timeout (overridden by `AgentDefinition.timeout_seconds`) |
| `AGENT_MAX_RETRIES` | 2 | LLM retry count (overridden by `AgentDefinition.max_retries`) |
| `AGENT_CONFIDENCE_THRESHOLD` | 0.75 | Below this -> invoke `INVOICE_UNDERSTANDING` agent |
| `REVIEW_AUTO_CLOSE_THRESHOLD` | 0.85 | MATCHED + confidence >= this -> skip agents |
| `CELERY_TASK_ALWAYS_EAGER` | True (dev) | Run tasks synchronously (Windows dev) |

### Environment variables (ERP source resolution)

| Variable | Default | Description |
|---|---|---|
| `ERP_CACHE_TTL_SECONDS` | 3600 | TTL for `ERPReferenceCacheRecord` |
| `ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD` | 0.8 | Min confidence for duplicate invoice match |
| `ERP_LIVE_REFRESH_ON_STALE` | False | Trigger live ERP API call if data is stale |
| `ERP_MIRROR_AS_PRIMARY` | False | Treat mirror DB as the primary source |

---

## 29. File Reference

### Reconciliation app (`apps/reconciliation/`)

| File | Responsibility |
|---|---|
| `services/runner_service.py` | Top-level orchestrator for deterministic pipeline |
| `services/mode_resolver.py` | 3-tier mode resolution (policy -> heuristic -> default) |
| `services/po_lookup_service.py` | ERP-backed PO resolution with discovery fallback |
| `services/grn_lookup_service.py` | ERP-backed GRN lookup and hydration |
| `services/execution_router.py` | Dispatches to 2-way or 3-way match service |
| `services/two_way_match_service.py` | Invoice vs PO matching (2-way) |
| `services/three_way_match_service.py` | Invoice vs PO vs GRN matching with ERP provenance propagation |
| `services/header_match_service.py` | Vendor, currency, total, tax comparisons |
| `services/line_match_service.py` | Line-by-line quantity, price, amount matching |
| `services/grn_match_service.py` | GRN receipt verification (`GRNMatchResult`) |
| `services/tolerance_engine.py` | Configurable percentage tolerance comparisons |
| `services/classification_service.py` | 7-gate deterministic decision tree |
| `services/exception_builder_service.py` | Structured exception creation (17 types) |
| `services/result_service.py` | DB persistence + ERP provenance fields |
| `services/agent_feedback_service.py` | Re-reconciliation on PO/GRN recovery |
| `models.py` | ReconciliationRun, ReconciliationResult, ReconciliationResultLine, ReconciliationException, ReconciliationConfig, ReconciliationPolicy |
| `tasks.py` | `run_reconciliation_task` (Celery) |
| `template_views.py` | `start_reconciliation` + case console views |
| `views.py` | DRF ViewSets |
| `api_urls.py` | API routes under `/api/v1/reconciliation/` |

### Agents app (`apps/agents/`)

| File | Responsibility |
|---|---|
| `services/orchestrator.py` | Agent pipeline entry point; RBAC + plan + execution |
| `services/reasoning_planner.py` | LLM-backed planner (always active); PolicyEngine fallback |
| `services/policy_engine.py` | Deterministic 7-rule agent selector; auto-close checks |
| `services/base_agent.py` | BaseAgent with ReAct loop; AgentContext; AgentOutput |
| `services/agent_classes.py` | All 8 LLM agent implementations + 5 system agents + AGENT_CLASS_REGISTRY |
| `services/agent_memory.py` | AgentMemory shared in-process state |
| `services/guardrails_service.py` | RBAC checks; SYSTEM_AGENT identity; guardrail audit logging |
| `services/deterministic_resolver.py` | Rule-based replacement for 3 LLM agents |
| `services/deterministic_system_agent.py` | DeterministicSystemAgent base class (skip ReAct loop) |
| `services/system_agent_classes.py` | 5 concrete system agents (review routing, case summary, bulk intake, case intake, posting prep) |
| `services/agent_trace_service.py` | Unified governance tracing |
| `services/recommendation_service.py` | AgentRecommendation create/accept with dedup |
| `services/decision_log_service.py` | DecisionLog persistence |
| `services/llm_client.py` | LLM client (Azure OpenAI + OpenAI) |
| `models.py` | AgentDefinition, AgentRun, AgentOrchestrationRun, AgentStep, AgentMessage, AgentRecommendation, AgentEscalation |
| `tasks.py` | `run_agent_pipeline_task` (Celery) |

### Tools app (`apps/tools/`)

| File | Responsibility |
|---|---|
| `registry/base.py` | BaseTool, ToolResult, ToolRegistry, @register_tool |
| `registry/tools.py` | All 6 tool implementations |
| `models.py` | ToolDefinition, ToolCall |

### ERP integration (`apps/erp_integration/`)

| File | Responsibility |
|---|---|
| `services/resolution_service.py` | `ERPResolutionService` -- central facade for all resolution types |
| `services/db_fallback/po_fallback.py` | Two-tier PO DB fallback (MIRROR_DB -> DB_FALLBACK) |
| `services/db_fallback/grn_fallback.py` | GRN DB fallback (MIRROR_DB) |
| `services/connectors/base.py` | `ERPResolutionResult` dataclass with provenance fields |
| `enums.py` | `ERPSourceType`, `ERPDataDomain`, `ERPResolutionType` |

### Key config and enum files

| File | What to find |
|---|---|
| `apps/core/enums.py` | `MatchStatus`, `ExceptionType`, `ExceptionSeverity`, `AgentType`, `RecommendationType`, `AgentRunStatus`, `ReconciliationMode`, `ReconciliationModeApplicability` |
| `apps/core/constants.py` | `THREE_WAY_ONLY_EXCEPTION_TYPES` |
| `config/settings.py` | `AGENT_TIMEOUT_SECONDS`, `AGENT_MAX_RETRIES`, `AGENT_CONFIDENCE_THRESHOLD`, `REVIEW_AUTO_CLOSE_THRESHOLD`, all ERP freshness settings |

---

## Appendix: Invoice Extraction Agent

> Feature documentation for the invoice extraction pipeline — apps/extraction/ + apps/extraction_core/.

# Invoice Extraction Agent — Feature Documentation

> **Modules**: `apps/extraction/` (Application Layer — UI, Task, Core Models) + `apps/extraction_core/` (Platform Layer — Configuration, Execution, Governance)
> **Dependencies**: Azure Document Intelligence (OCR), Azure OpenAI GPT-4o (LLM), Agent Framework (`apps/agents/`)
> **Status**: Human-in-the-loop approval gate + multi-country extraction platform + credit-based usage control + OCR cost tracking + Phase 2 modular prompt composition + deterministic response repair + field-level confidence scoring + critical field validation + hard reconciliation math checks + **Phase 2 hardening: decision codes, recovery lane, evidence-aware field confidence, prompt-source audit trail** + **Indian e-invoice QR code decoding (NIC JWT + plain-JSON formats, Azure DI barcodes add-on, OCR plain-text IRN fallback)**. 355 passing, 2 pre-existing failures, 1 skipped (total collected ~358) — see `apps/extraction/tests/`. ERP connectors and Celery Beat schedules are pending.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Extraction Pipeline](#3-extraction-pipeline)
4. [Data Models](#4-data-models)
5. [Services](#5-services)
6. [Extraction Core — Multi-Country Extraction Platform](#6-extraction-core--multi-country-extraction-platform)
7. [Master Data Enrichment](#7-master-data-enrichment)
8. [Approval Gate](#8-approval-gate)
9. [Agent Framework Integration](#9-agent-framework-integration)
10. [LLM Prompt](#10-llm-prompt)
11. [Template Views & URLs](#11-template-views--urls)
12. [Templates (UI)](#12-templates-ui)
13. [Extraction Review Console](#13-extraction-review-console)
14. [Enums & Status Flows](#14-enums--status-flows)
15. [Configuration](#15-configuration)
16. [Permissions & RBAC](#16-permissions--rbac)
17. [Credit System](#17-credit-system)
18. [OCR Cost Tracking](#18-ocr-cost-tracking)
19. [Django Admin](#19-django-admin)
20. [File Reference](#20-file-reference)
21. [Bulk Extraction Intake (Phase 1)](#21-bulk-extraction-intake-phase-1)
22. [Phase 2 Hardening](#22-phase-2-hardening)
23. [Indian e-Invoice QR Code Support](#23-indian-e-invoice-qr-code-support)

---

## 1. Overview

The Invoice Extraction Agent converts uploaded invoice documents (PDF, PNG, JPG, TIFF) into structured, normalized data. The system spans two Django apps:

- **`apps/extraction/`** — Application layer: template views (workbench, console, approval queue, country packs), Celery task, core models (`ExtractionResult`, `ExtractionApproval`, `ExtractionFieldCorrection`), 8 pipeline services, and the human approval gate.
- **`apps/extraction_core/`** — Platform layer: 13 data models, 30 service classes, 60+ API endpoints, multi-country jurisdiction resolution, schema-driven extraction, evidence capture, confidence scoring, review routing, analytics/learning, and country pack governance.

### Base Extraction Pipeline (apps/extraction)

Uses a two-stage pipeline:

1. **Azure Document Intelligence** — OCR to extract raw text from the document.
2. **Azure OpenAI GPT-4o** — LLM-based structured extraction from OCR text into a typed JSON schema.

After extraction, the data passes through parsing, normalization, validation, and duplicate detection before being persisted. A **human approval gate** ensures every extraction is reviewed (or auto-approved at high confidence) before the invoice enters reconciliation.

### Extended Platform Pipeline (apps/extraction_core)

Adds an 11-stage governed pipeline with:

1. **4-tier jurisdiction resolution** — Document declared → entity profile → runtime settings → auto-detect
2. **Schema-driven extraction** — Versioned schemas per jurisdiction + document type
3. **Document intelligence** — Document classification, party extraction, relationship extraction
4. **Multi-page support** — Page segmentation, header/footer dedup, cross-page table stitching
5. **Country-specific normalization & validation** — Jurisdiction-aware rules (IN-GST, AE-VAT, SA-ZATCA)
6. **Evidence capture** — Field provenance with OCR snippets, page numbers, bounding boxes
7. **Confidence scoring** — Multi-dimensional (header, tax, line items, jurisdiction)
8. **Review routing** — Queue-based routing (EXCEPTION_OPS, TAX_REVIEW, VENDOR_OPS)
9. **Master data enrichment** — Vendor matching, PO lookup, confidence adjustments
10. **Analytics/learning** — Correction feedback → ExtractionAnalyticsSnapshot
11. **Country pack governance** — DRAFT → ACTIVE → DEPRECATED lifecycle per jurisdiction

### Cross-Module Integration

Template views in `apps/extraction/` enrich their context with `apps/extraction_core/` models via `ExecutionContext`:
- Workbench uses `get_execution_context()` to load review_queue and source indicator for each result
- Console uses `get_execution_context()` to populate extraction_ctx (review queue, schema, method, source badges) + `ExtractionCorrection` audit trail
- Country packs page queries `CountryPack` with jurisdiction profiles
- Source badge in console header shows **Governed** (green) or **Legacy** (warning) based on `ExecutionContext.source`

### Execution Ownership

**ExtractionRun** (`apps/extraction_core/models.py`) is the **authoritative execution record** — the runtime source of truth. **ExtractionResult** (`apps/extraction/models.py`) is the **UI-facing summary** with an `extraction_run` FK linking back to the governing run.

Views resolve execution data via `ExecutionContext` (`apps/extraction/services/execution_context.py`):
1. Check `extraction_result.extraction_run` FK (direct link)
2. Fall back to `ExtractionRun.objects.filter(document__document_upload_id=...)` (lookup by upload)
3. Return legacy context (all None) if no governed run exists

**Phase 2 hardening fields** (populated on all paths via `_enrich_hardening_fields()` from `raw_response` keys):

| Field | Type | Source |
|-------|------|--------|
| `decision_codes` | `List[str]` | `raw_response["_decision_codes"]` |
| `prompt_source` | `str \| None` | `raw_response["_prompt_meta"]["prompt_source_type"]` |
| `prompt_hash` | `str \| None` | `raw_response["_prompt_meta"]["prompt_hash"]` |
| `recovery_lane_invoked` | `bool` | `raw_response["_recovery"]["invoked"]` |
| `recovery_lane_succeeded` | `bool \| None` | `raw_response["_recovery"]["succeeded"]` (only set when invoked) |

### GovernanceTrailService

`GovernanceTrailService` (`apps/extraction_core/services/governance_trail.py`) is the **sole writer** of `ExtractionApprovalRecord`. Called by:
- `ExtractionApprovalService.approve()` / `.reject()` (legacy flow)
- `ExtractionRunViewSet.approve()` / `.reject()` (governed API)

### Permission Split

- `invoices.create` — upload only (file selection and dispatch to extraction task)
- `extraction.correct` — edit/correct extracted field values (workbench, console, API)
- `extraction.approve` / `extraction.reject` — finalize extraction decisions

---

## 2. Architecture

### Two-App Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  apps/extraction/  (Application Layer)                       │
│                                                              │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ Template Views│  │ Celery Task   │  │ Core Models      │  │
│  │ (15 views)   │  │ (pipeline)    │  │ ExtractionResult │  │
│  │ workbench    │  │               │  │ ExtractionApproval│ │
│  │ console      │  │ 8 services    │  │ FieldCorrection  │  │
│  │ approvals    │  │               │  │                  │  │
│  │ country packs│  │               │  │                  │  │
│  └──────┬───────┘  └───────────────┘  └──────────────────┘  │
│         │ cross-module queries (ExtractionRun, CountryPack)  │
├─────────┼───────────────────────────────────────────────────┤
│  apps/extraction_core/  (Platform Layer)                     │
│                                                              │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │ Configuration│  │ Pipeline (30  │  │ Governance       │  │
│  │ Jurisdiction │  │ services)     │  │ CountryPack      │  │
│  │ Schema       │  │ 11-stage      │  │ Analytics        │  │
│  │ Runtime      │  │ orchestrator  │  │ Learning         │  │
│  │ Entity       │  │               │  │ Audit            │  │
│  └──────────────┘  └───────────────┘  └──────────────────┘  │
│                                                              │
│  ┌──────────────┐  ┌───────────────┐                         │
│  │ 60+ API      │  │ 13 Models     │                         │
│  │ endpoints    │  │ ExtractionRun │                         │
│  │ Config +     │  │ FieldValue    │                         │
│  │ Execution    │  │ Evidence ...  │                         │
│  └──────────────┘  └───────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow Diagram (Base Pipeline — Updated)

> **Current flow** includes category classification, modular prompt composition, and deterministic response repair (added in Phase 2 upgrade).

```
User uploads PDF/Image
         │
         ▼
  DocumentUpload record created
         │
         ▼
  process_invoice_upload_task (Celery)
         │
         ▼
  ┌──────────────────────────────────┐
  │  Stage 1: OCR                    │
  │  Azure Document Intelligence     │
  │  features=[BARCODES]             │
  │  → raw OCR text + qr_texts[]     │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 1c: QR Decode             │   ← NEW (Indian e-invoice)
  │  QRCodeDecoderService            │
  │  Strategy 1: Azure DI barcodes   │
  │  Strategy 2: OCR text IRN regex  │
  │  Strategy 3: pyzbar (optional)   │
  │  → QRInvoiceData (or None)       │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 2: Category Classification│   ← NEW
  │  InvoiceCategoryClassifier       │
  │  goods | service | travel        │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 3: Prompt Composition     │   ← NEW
  │  InvoicePromptComposer           │
  │  base + category + country/tax   │
  │  overlays → final system prompt  │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 4: InvoiceExtractionAgent │
  │  GPT-4o → structured JSON        │
  │  (temp=0, json_object mode)      │
  │  Uses composed prompt if provided│
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Stage 5: Response Repair        │   ← NEW
  │  ResponseRepairService           │
  │  invoice# exclusion, tax recomp, │
  │  subtotal align, line tax alloc, │
  │  travel consolidation            │
  └──────────────┬───────────────────┘
                 │
                 ▼
  ExtractionParserService  (JSON → ParsedInvoice)
         │
         ▼
  NormalizationService  (clean, type-cast, standardize)
         │
         ├──► FieldConfidenceService  (per-field scores + evidence_flags)
         ▼
  ValidationService  (mandatory fields, confidence check)
         │
         ├──► ReconciliationValidatorService  (6 math checks)
         ├──► derive_codes()  (machine-readable decision codes)
         ├──► RecoveryLaneService.evaluate()  (policy: named failure modes only)
         │         │ if triggered
         │         └──► InvoiceUnderstandingAgent  (bounded recovery)
         ▼
  DuplicateDetectionService  (vendor + invoice# match)
         │
         ▼
  InvoicePersistenceService  (save Invoice + LineItems)
  ExtractionResultPersistenceService  (save engine metadata)
  → ExtractionResult.raw_response includes:
      _repair, _field_confidence, _validation,
      _prompt_meta, _decision_codes, _recovery, _qr
         │
         ▼
  ┌─────────────────────────────────────────┐
  │             Approval Gate               │
  │                                         │
  │  Auto-approve enabled AND               │
  │  confidence ≥ threshold?                │
  │    YES → AUTO_APPROVED → READY_FOR_RECON│
  │    NO  → PENDING_APPROVAL               │
  │         → Human review in Approval Queue│
  │                                         │
  │  Human approves → READY_FOR_RECON       │
  │  Human rejects  → INVALID (re-extract)  │
  └─────────────────────────────────────────┘
         │
         ▼
  AP Case created at upload time (before extraction)
  Invoice linked to case after extraction persistence
  (pipeline pauses at EXTRACTION_APPROVAL if
   human approval needed; resumes on approve)
         │
         ▼
  Reconciliation pipeline
```

### Service Architecture

```
InvoiceExtractionAdapter (orchestrates stages 1 + 2)
  ├── Azure Document Intelligence Client (OCR)
  └── InvoiceExtractionAgent (LLM extraction via agent framework)
        ├── LLMClient (Azure OpenAI, temp=0, max_tokens=4096)
        ├── PromptRegistry ("extraction.invoice_system")
        └── AgentRun / AgentMessage (traceability)

ExtractionParserService → NormalizationService → ValidationService
  → DuplicateDetectionService → InvoicePersistenceService
    → ExtractionResultPersistenceService → ExtractionApprovalService
```

---

## 3. Extraction Pipeline

**Task**: `process_invoice_upload_task` in `apps/extraction/tasks.py`  \n**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`\n\n> **Tenant propagation**: The task accepts `tenant_id` and resolves it to a `CompanyProfile` instance. All records created during extraction (Invoice, InvoiceLineItem, ExtractionResult, ExtractionApproval, APCase) inherit the tenant from the upload or from the resolved tenant. See [MULTI_TENANT.md](MULTI_TENANT.md).", "oldString": "**Task**: `process_invoice_upload_task` in `apps/extraction/tasks.py`  \n**Decorator**: `@shared_task(bind=True, max_retries=2, default_retry_delay=30)`

> **Execution path**: `ExtractionPipeline` (governed, 11-stage, in `apps/extraction_core`) is the preferred execution path. `ExtractionService` (legacy) remains active for backward compatibility. Step 6 also writes `extraction_run` to `ExtractionResult.extraction_run` FK, linking the UI summary to the authoritative execution record.

### Pipeline Steps

| Step | Service | Description |
|------|---------|-------------|
| 0 | `CreditService.reserve()` | Reserve 1 credit (`ref_type="document_upload"`, `ref_id=upload.pk`). Hard-stop if insufficient. |
| 1 | `InvoiceExtractionAdapter` | OCR (with `features=[AnalysisFeature.BARCODES]`) + LLM extraction → `ExtractionResponse` (includes `_repair`, `_qr` metadata in `raw_json`) |
| 1a | `DocumentTypeClassifier` | Classify OCR text → reject non-invoices (GRN, PO, DELIVERY_NOTE, STATEMENT) with credit refund. Rejection requires `confidence ≥ 0.60` **and** `not is_ambiguous`. |
| 1b | `_run_governed_pipeline()` | Wire governed extraction pipeline (`ExtractionPipeline.run()`) as an enrichment step. Creates `ExtractionDocument` linked to the upload, passes OCR text + invoice reference. Wrapped in try/except for graceful degradation — if the governed pipeline fails, the legacy pipeline continues and the result shows "Legacy" source. |
| 1c | `QRCodeDecoderService` | Decode Indian e-invoice QR (IRN, GSTIN, total, doc type). Three strategies: Azure DI barcodes → OCR text IRN regex → pyzbar pixel decode. Sets `ExtractionResponse.qr_data`; embeds `_qr` in `raw_json`. Fail-silent — `None` when no QR found. |
| 2 | `ExtractionParserService` | Parse raw JSON → `ParsedInvoice` dataclass |
| 3 | `NormalizationService` | Normalize fields (dates, amounts, PO numbers) → `NormalizedInvoice` |
| 3a | `FieldConfidenceService` | Deterministic per-field confidence scoring (0.0–1.0) based on presence, parse success, repair actions. Attaches `field_confidence` dict to `NormalizedInvoice`. Embeds `_field_confidence` in `raw_json` for persistence. |
| 4 | `ValidationService` + `ExtractionConfidenceScorer` | Check mandatory fields, compute deterministic overall confidence. Reads `NormalizedInvoice.field_confidence` to detect low-confidence critical fields → sets `requires_review_override`. |
| 4a | `ReconciliationValidatorService` | 6 deterministic math checks: total consistency, line sum, line math, tax breakdown, tax %, line tax sum. Issues serialised to `raw_json["_validation"]`. Math ERRORs surfaced as validation warnings. |
| 4b | `derive_codes()` | Maps ValidationResult + ReconciliationValidationResult + FieldConfidenceResult + prompt_source_type → list of machine-readable decision codes. Embedded into `raw_json["_decision_codes"]`. |
| 4c | `RecoveryLaneService` | Deterministic policy evaluation against named failure modes. When triggered, invokes `InvoiceUnderstandingAgent` with bounded recovery context. Output embedded into `raw_json["_recovery"]`. Fail-silent. |
| 5 | `DuplicateDetectionService` | Detect re-submitted invoices |
| 6 | `InvoicePersistenceService` + `ExtractionResultPersistenceService` | Persist to database (sets `extraction_run` FK). `ExtractionResult.raw_response` contains `_repair`, `_field_confidence`, `_validation`, `_prompt_meta`, `_decision_codes`, `_recovery`, and `_qr` metadata. |
| 6a | `CreditService.consume()` / `.refund()` | On success → consume; on OCR failure → refund (see §17 decision table) |
| 7 | Approval Gate | Auto-approve or queue for human review. `requires_review_override=True` skips auto-approval entirely (critical field confidence failure). |

### Audit Events

- `EXTRACTION_STARTED` — logged when the task begins
- `EXTRACTION_COMPLETED` — logged on successful extraction + persistence
- `EXTRACTION_FAILED` — logged on any pipeline failure

### Azure Blob Integration

- **Input path**: `input/{year}/{month}/filename`
- **On success**: File moved to `processed/`
- **On failure**: File moved to `exception/`

---

## 4. Data Models

### 4.1 ExtractionResult

**Table**: `extraction_result` | **File**: `apps/extraction/models.py` | **Inherits**: `BaseModel`

UI-facing summary record — **not** the execution source of truth. The authoritative execution record is `ExtractionRun` (apps/extraction_core). This model links to it via `extraction_run` FK.

| Field | Type | Description |
|-------|------|-------------|
| `document_upload` | FK → DocumentUpload | Source file |
| `invoice` | FK → Invoice (nullable) | Linked invoice after persistence |
| `extraction_run` | FK → ExtractionRun (nullable) | Link to authoritative execution record |
| `engine_name` | CharField | Engine identifier (default: `"default"`) |
| `engine_version` | CharField | Engine version string |
| `raw_response` | JSONField (nullable) | Full JSON response from LLM |
| `confidence` | FloatField (nullable) | 0.0–1.0 extraction confidence |
| `duration_ms` | PositiveIntegerField (nullable) | Extraction duration in milliseconds |
| `success` | BooleanField | Whether extraction succeeded |
| `error_message` | TextField | Error details if failed |
| `ocr_page_count` | PositiveIntegerField | Number of pages processed by OCR (default: 0) |
| `ocr_duration_ms` | PositiveIntegerField (nullable) | OCR processing duration in milliseconds |
| `ocr_char_count` | PositiveIntegerField | Number of characters extracted by OCR (default: 0) |

### 4.2 ExtractionApproval

**Table**: `extraction_approval` | **File**: `apps/extraction/models.py` | **Inherits**: `BaseModel`

Tracks human approval/rejection of extraction results and field corrections.

| Field | Type | Description |
|-------|------|-------------|
| `invoice` | OneToOneField → Invoice | Linked invoice |
| `extraction_result` | FK → ExtractionResult (nullable) | Source extraction |
| `status` | CharField | `ExtractionApprovalStatus` enum |
| `reviewed_by` | FK → User (nullable) | Reviewer |
| `reviewed_at` | DateTimeField (nullable) | Review timestamp |
| `rejection_reason` | TextField | Reason for rejection |
| `confidence_at_review` | FloatField (nullable) | Confidence snapshot at approval time |
| `original_values_snapshot` | JSONField | Extracted header + line values pre-correction |
| `fields_corrected_count` | PositiveIntegerField | Number of field corrections made |
| `is_touchless` | BooleanField (indexed) | True if approved without any corrections |

**Indexes**: `status`, `is_touchless`

### 4.3 ExtractionFieldCorrection

**Table**: `extraction_field_correction` | **File**: `apps/extraction/models.py` | **Inherits**: `TimestampMixin`

Records individual field corrections for granular analytics.

| Field | Type | Description |
|-------|------|-------------|
| `approval` | FK → ExtractionApproval | Parent approval |
| `entity_type` | CharField | `'header'` or `'line_item'` |
| `entity_id` | PositiveIntegerField (nullable) | PK of InvoiceLineItem (for line corrections) |
| `field_name` | CharField | Name of the corrected field |
| `original_value` | TextField | Value before correction |
| `corrected_value` | TextField | Value after correction |
| `corrected_by` | FK → User (nullable) | User who made the correction |

### 4.4 Related Document Models

**Invoice** (`documents_invoice`) — stores raw + normalized invoice header fields:

- **Raw fields**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`, `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date`
- **Normalized fields**: `invoice_number`, `normalized_invoice_number`, `invoice_date`, `po_number`, `normalized_po_number`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `due_date` (DateField), `vendor_tax_id` (CharField 100), `buyer_name` (CharField 255), `tax_percentage` (Decimal 7,4), `tax_breakdown` (JSONField `{cgst, sgst, igst, vat}`)
- **Extraction metadata**: `extraction_confidence` (float 0.0–1.0), `extraction_remarks`, `extraction_raw_json`
- **Status**: `status` (InvoiceStatus enum)

> Migration `0009_add_tax_breakdown_vendor_tax_id_buyer_due_date` added the `due_date`, `vendor_tax_id`, `buyer_name`, `tax_percentage`, `tax_breakdown`, `raw_vendor_tax_id`, `raw_buyer_name`, and `raw_due_date` fields.

**InvoiceLineItem** (`documents_invoice_line`) — line items:

- **Raw fields**: `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`
- **Normalized fields**: `description`, `normalized_description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`, `tax_percentage` (Decimal 7,4, nullable)
- **Classification**: `item_category`, `is_service_item`, `is_stock_item`

**DocumentUpload** (`documents_upload`) — file metadata:

- `original_filename`, `file_size`, `file_hash` (SHA-256), `content_type`
- `processing_state` (FileProcessingState enum), `processing_message`
- Azure Blob fields: `blob_path`, `blob_container`, `blob_name`

---

## 5. Services

### 5.0 Observability

All extraction services are decorated with `@observed_service` from `apps/core/decorators.py`. This creates a child trace span, measures duration, writes a `ProcessingLog` entry, and optionally emits an `AuditEvent` for each service method invocation.

#### Langfuse integration

In addition to the Django-native `@observed_service` instrumentation, the
extraction pipeline emits Langfuse traces, generations, and scores at three
specific points. All calls are fail-silent (`try/except`) and never block
extraction.

| Call site | Location | What is emitted |
|---|---|---|
| Agent extraction trace | `InvoiceExtractionAgent.run()` | Root trace `"invoice_extraction"` with `user_id` + `session_id=f"case-{case_number}"` (falls back to `"extraction-upload-{upload_id}"`) |
| LLM fallback trace | `InvoiceExtractionAdapter._llm_extract()` | Root trace `"llm_extract_fallback"` + `log_generation` with token counts |
| Extraction approval scores | `ExtractionApprovalService` | `score_trace` calls on auto-approve, human approve, and reject (see below) |

**Approval lifecycle scores** (trace ID: `f"approval-{approval.pk}"`):

| Score name | Value | When |
|---|---|---|
| `extraction_auto_approve_confidence` | 0.0--1.0 | `try_auto_approve()` fires |
| `extraction_approval_decision` | `1.0` (approve) / `0.0` (reject) | Human approve or reject |
| `extraction_approval_confidence` | 0.0--1.0 | Human approve (confidence snapshot) |
| `extraction_corrections_count` | 0.0+ (raw count) | Human approve with corrections |

**Extraction pipeline scores** (`extraction_pipeline.py` Step 9, trace ID: `str(run.pk)`):

| Score name | Value | Meaning |
|---|---|---|
| `extraction_confidence` | 0.0--1.0 | `output.overall_confidence` (guarded with `or 0.0`) |
| `extraction_requires_review` | 0.0 or 1.0 | `routing.needs_review` |

**Bulk extraction user attribution**: `InvoiceExtractionAdapter.extract()` accepts
`actor_user_id` kwarg forwarded from `process_invoice_upload_task`. This ensures
bulk jobs appear under the correct user in the Langfuse Users tab:

```python
adapter.extract(file_path, actor_user_id=upload.uploaded_by_id)
```

Full Langfuse reference: [LANGFUSE_OBSERVABILITY.md](LANGFUSE_OBSERVABILITY.md)

### 5.1 InvoiceExtractionAdapter

**File**: `apps/extraction/services/extraction_adapter.py`  
**Decorator**: `@observed_service("extraction.extract", entity_type="DocumentUpload", audit_event="EXTRACTION_STARTED")`

Orchestrates the two-stage extraction pipeline:

**Stage 1 — Text Extraction** (OCR or native, controlled by `ocr_enabled` flag):
```python
ocr_enabled = self._is_ocr_enabled()  # Check ExtractionRuntimeSettings → settings.EXTRACTION_OCR_ENABLED
if ocr_enabled:
    ocr_text, ocr_page_count, ocr_duration_ms, qr_texts = self._ocr_document(file_path)
else:
    ocr_text, ocr_page_count, ocr_duration_ms = self._extract_text_native(file_path)
    qr_texts = []

# Stage 1c — QR decode (after OCR, before category classification)
qr_data = self._decode_qr(file_path, ocr_text, qr_texts)
```

**`_ocr_document(file_path)`** — Azure Document Intelligence:
- Uses `DocumentAnalysisClient` from `azure.ai.formrecognizer` with `prebuilt-read` model and **`features=[AnalysisFeature.BARCODES]`**
- The `features` kwarg is **required** for barcode extraction — without it, `page.barcodes` is always empty even when the document contains QR codes
- Concatenates all pages' text lines; collects `kind="QRCode"` barcode values into `qr_texts`
- Returns `(text, page_count, duration_ms, qr_texts)` **4-tuple** (changed from 3-tuple)
- Credentials: `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY`
- Cost: $1.50 per 1,000 pages (barcode add-on is free)

> **Azure DI barcode API notes:**
> - Available from `azure-ai-formrecognizer >= 3.3.0` (API version `2023-07-31`)
> - Barcode `kind` is `"QRCode"` (PascalCase) — the code calls `.upper()` before comparing, making it case-insensitive
> - If `page.barcodes` attribute is absent (older SDK), `getattr(page, "barcodes", [])` returns `[]` safely
> - When Azure DI does not decode the QR (e.g. very small/distorted QR), the pipeline falls through to OCR-text regex and pyzbar strategies automatically
> - The QR value returned by Azure DI is typically a **NIC-signed JWT** (RS256, `iss="NIC"`), not plain JSON. `QRCodeDecoderService.decode_from_texts()` calls `_unwrap_jwt()` before attempting JSON parsing.

**`_extract_text_native(file_path)`** — PyPDF2 fallback (no OCR cost):
- Uses `PyPDF2.PdfReader` to extract embedded text layer from native PDFs
- Returns `(text, page_count, duration_ms)` — same tuple shape
- No Azure DI call — zero OCR cost, near-instant
- Useful for accuracy comparison testing

**`_is_ocr_enabled()`** — Two-tier flag check:
1. `ExtractionRuntimeSettings.get_active().ocr_enabled` (DB, toggleable from Extraction Control Center UI)
2. Fallback: `settings.EXTRACTION_OCR_ENABLED` (env var, default: `True`)

**Stage 2 — LLM Extraction**:
```python
raw_json, agent_run_id = _agent_extract(ocr_text, document_upload_id=document_upload_id)
```
- Instantiates `InvoiceExtractionAgent()`
- Returns JSON + `AgentRun.pk` for traceability
- After the `AgentRun` record is created, the method immediately stamps `AgentRun.document_upload_id` via a targeted `UPDATE` (`AgentRun.objects.filter(pk=...).update(document_upload_id=...)`) so that all runs for a given upload are queryable via `AgentRun.objects.filter(document_upload_id=...)`

**`extract()` signature** (`InvoiceExtractionAdapter.extract`):
```python
def extract(self, file_path: str, document_upload_id: Optional[int] = None) -> ExtractionResponse:
```
The `document_upload_id` parameter is supplied by the Celery task (`process_invoice_upload_task`) and passed through to `_agent_extract()`. It is optional — if `None`, the AgentRun FK is simply not set (backward-compatible).

**Engine name tracking**: `engine_name` is set to `"azure_di_gpt4o_agent"` when OCR is used, or `"native_pdf_gpt4o_agent"` when native extraction is used. This allows filtering and comparing accuracy by extraction method.

**Returns**: `ExtractionResponse` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `success` | bool | Whether extraction succeeded |
| `raw_json` | dict | Extracted JSON data (contains `_repair`, `_qr`, `_prompt_meta` etc.) |
| `confidence` | float | 0.0–1.0 confidence |
| `engine_name` | str | `"azure_di_gpt4o_agent"` (OCR) or `"native_pdf_gpt4o_agent"` (no OCR) |
| `engine_version` | str | `"2.0"` |
| `duration_ms` | int | Extraction duration |
| `error_message` | str | Error details if failed |
| `ocr_text` | str | Raw OCR text |
| `ocr_page_count` | int | Number of pages processed (default: 0) |
| `ocr_duration_ms` | int | OCR processing duration in ms (default: 0) |
| `ocr_char_count` | int | Characters extracted (default: 0) |
| `invoice_category` | str | `"goods"` / `"service"` / `"travel"` from category classifier |
| `category_confidence` | float | Category classification confidence |
| `prompt_components` | dict | Modular prompt component keys used |
| `prompt_hash` | str | SHA-256 of the final composed prompt |
| `was_repaired` | bool | Whether `ResponseRepairService` made any change |
| `repair_actions` | list | List of repair action strings applied |
| `qr_data` | `QRInvoiceData \| None` | Decoded e-invoice QR payload (see §23); `None` when no QR found |

**Fallback**: Direct LLM extraction without agent framework via `_llm_extract(ocr_text)` — uses `response_format={"type": "json_object"}`, temperature=0.0, max_tokens=4096.

### 5.2 ExtractionParserService

**File**: `apps/extraction/services/parser_service.py`  
**Decorator**: `@observed_service("extraction.parse", entity_type="ExtractionResult")`

Parses raw JSON → structured dataclasses:

- **ParsedInvoice**: `raw_vendor_name`, `raw_invoice_number`, `raw_invoice_date`, `raw_po_number`, `raw_currency`, `raw_subtotal`, `raw_tax_amount`, `raw_total_amount`, `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date`, `raw_tax_percentage`, `raw_tax_breakdown` (dict), `confidence`, `line_items`
- **ParsedLineItem**: `line_number`, `raw_description`, `raw_quantity`, `raw_unit_price`, `raw_tax_amount`, `raw_line_amount`, `raw_tax_percentage`

Flexible field mapping (e.g., accepts both `item_description` and `description`). Validates that `tax_breakdown` is a dict (defaults to `{}` if the LLM returns a non-dict value).

### 5.3 NormalizationService

**File**: `apps/extraction/services/normalization_service.py`  
**Decorator**: `@observed_service("extraction.normalize", entity_type="Invoice")`

Normalizes parsed values to proper types:

| Operation | Detail |
|-----------|--------|
| Vendor name | `normalize_string()` — lowercase, strip, remove diacritics |
| Invoice number | `normalize_invoice_number()` — strip spaces/dashes/special chars |
| PO number | `normalize_po_number()` — same normalization |
| Date | `parse_date()` — flexible parsing (DD/MM/YYYY, YYYY-MM-DD, etc.) — used for both `invoice_date` and `due_date` |
| Currency | `parse_currency()` — fallback to `"USD"` |
| Amounts | `to_decimal()` — parse currency strings to `Decimal` — used for `subtotal`, `tax_amount`, `total_amount`, `tax_percentage`, and line amounts |
| Line items | Same normalization per line (includes `tax_percentage`) |
| Tax breakdown | `_normalize_tax_breakdown(raw)` — coerces `cgst`, `sgst`, `igst`, `vat` keys to `float`; defaults missing keys to `0.0` |

**New fields added to `NormalizedInvoice`**:
- `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date`, `raw_tax_percentage` — raw string carry-throughs
- `raw_tax_breakdown` — raw dict carry-through
- `vendor_tax_id` (str) — passthrough of the GSTIN/VAT identifier
- `buyer_name` (str) — billed-to entity name
- `due_date` (Optional[date]) — parsed payment due date
- `tax_percentage` (Optional[Decimal]) — headline tax rate percentage
- `tax_breakdown` (dict) — cleaned `{cgst, sgst, igst, vat}` dict (all floats, defaults 0.0)

**New fields added to `NormalizedLineItem`**:
- `raw_tax_percentage` (str) — raw string from LLM
- `tax_percentage` (Optional[Decimal]) — parsed line-level tax rate

Utility functions live in `apps/core/utils.py`.

### 5.4 ValidationService

**File**: `apps/extraction/services/validation_service.py`  
**Decorator**: `@observed_service("extraction.validate", entity_type="Invoice")`

Returns `ValidationResult` with `is_valid`, `errors`, and `warnings`.

**Errors** (blocking — marks invoice as INVALID):
- `normalized_invoice_number` missing
- `vendor_name_normalized` missing
- `total_amount` missing or non-numeric
- `tax_percentage` is not a valid Indian GST slab when `tax_breakdown` contains `cgst`/`sgst`/`igst` keys (see GST rate validation below)

**Warnings** (non-blocking):
- `normalized_po_number` missing (will require agent lookup)
- `invoice_date` unparseable
- `subtotal` missing
- No line items extracted
- Low extraction confidence (< `EXTRACTION_CONFIDENCE_THRESHOLD` = 0.75)
- Line item missing quantity / unit_price / description

#### GST Rate Validation

When a GST invoice is detected (any of `cgst`, `sgst`, `igst` keys present in `tax_breakdown`), `tax_percentage` must be one of the recognised Indian GST slabs. Any value outside the valid set causes a blocking **error** (invoice status → INVALID); the user must correct the field manually.

**Standard slabs**: `{0, 3, 5, 12, 18, 28}` percent.

**Special case — 0.25% for precious/semi-precious stones**: The 0.25% slab (GST Schedule I, Chapter 71, HSN headings 7102–7104) is permitted **only** when `_is_precious_stone_invoice()` returns `True`. This helper scans all line item description fields (`description`, `normalized_description`, `raw_description`) and the vendor name for keywords:

> `diamond`, `diamonds`, `gemstone`, `gem stone`, `gems`, `precious stone`, `semi-precious`, `ruby`, `rubies`, `emerald`, `sapphire`, `pearl`, `pearls`, `topaz`, `opal`, `amethyst`, `tanzanite`, `alexandrite`, `spinel`, `tourmaline`, `rough stone`, `rough gem`, and Chapter 71 HSN code substrings `7102`–`7104`.

If 0.25% is present for an invoice that does not match any precious stone keyword, it is still rejected as invalid.

| Invoice type | Valid `tax_percentage` values |
|---|---|
| Standard GST invoice | `{0, 3, 5, 12, 18, 28}` |
| GST invoice with precious stone line items | `{0, 0.25, 3, 5, 12, 18, 28}` |

**Repair service alignment**: `ResponseRepairService._GST_STANDARD_RATES` is `(0, 0.25, 3, 5, 12, 18, 28)` so that `_extract_gst_rate_from_ocr()` and `_repair_tax_percentage()` accept 0.25% as a valid OCR-scanned rate without triggering an additional repair action.

### 5.4a ExtractionConfidenceScorer

**File**: `apps/extraction/services/confidence_scorer.py`  
**Called by**: Pipeline step 4 (after `ValidationService`)

Replaces the LLM's self-reported confidence with a deterministic, auditable score computed from what was actually extracted. Returns a `ConfidenceBreakdown` dataclass with `overall` (0.0–1.0), dimension scores, and a list of penalty reasons.

**Three dimensions (weighted sum)**:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Field coverage | 50% | Were critical/important/optional header fields extracted? |
| Line-item quality | 30% | How complete are the extracted line items? |
| Cross-field consistency | 20% | Do the numbers add up? |

**Field coverage — header field weights** (normalised internally):

| Field | Weight | Notes |
|-------|--------|-------|
| `total_amount` | 5.0 | Critical |
| `invoice_number` | 5.0 | Critical |
| `vendor_name` | 4.0 | Critical |
| `invoice_date` | 3.0 | Important |
| `currency` | 2.0 | Important (USD default gets 50% partial credit) |
| `po_number` | 2.0 | Useful |
| `subtotal` | 1.5 | Useful |
| `tax_amount` | 1.5 | Useful |

Missing fields generate `missing:<field>` penalties.

**Line-item quality — per-line field weights** (normalised internally):

| Field | Weight |
|-------|--------|
| `description` | 3.0 |
| `quantity` | 3.0 |
| `unit_price` | 3.0 |
| `line_amount` | 2.0 |
| `tax_amount` | 1.0 |

Returns average completeness across all lines. Zero line items → `no_line_items` penalty → 0.0 score.

**Cross-field consistency checks**:

| Check | Tolerance | Penalty format |
|-------|-----------|----------------|
| `subtotal + tax_amount ≈ total_amount` | 2% | `total_mismatch:<expected>!=<actual>` |
| `sum(line_amounts) ≈ subtotal` (or total) | 5% | `line_sum_mismatch:<sum>!=<reference>` |
| `qty × unit_price ≈ line_amount` (per line) | 2% | (no per-line penalty to avoid noise) |

If no consistency checks are possible (all values missing), returns 0.5 (neutral).

**Output**: `ConfidenceBreakdown` with `overall`, `field_coverage`, `line_item_quality`, `consistency`, `penalties` list, `llm_original` (preserved for audit comparison). The `overall` score is clamped to [0.0, 1.0] and written to `Invoice.extraction_confidence`.

### 5.4b FieldConfidenceService

**File**: `apps/extraction/services/field_confidence_service.py`
**Called by**: Pipeline step 3a (after `NormalizationService`, before `ValidationService`)

Produces a **per-field confidence map** (0.0–1.0) for every extracted header field and per-line sub-field. Unlike `ExtractionConfidenceScorer` (which produces a single scalar), this service identifies *which* fields are unreliable.

**Scoring bands**:

| Band | Score | Meaning |
|------|-------|---------|
| Explicit + clean | 0.95–1.00 | Field present in LLM output, parsed OK, no repair touching this field |
| Minor repair elsewhere | 0.80–0.94 | Field parsed OK; a repair action ran but did not affect this field |
| Direct repair | 0.60–0.79 | Repair action directly modified this field (e.g., `tax_percentage.recomputed`) |
| Recovered | 0.65 | `invoice_number` recovered from OCR by repair (`invoice_number.recovered_from_ocr`) |
| Suspicious | 0.30–0.59 | Value present but anomalous (zero total, non-3-char currency defaulted) |
| Missing / failed | 0.00–0.29 | Field absent from LLM output or normalization returned None/empty |

**Critical fields** (`CRITICAL_FIELDS`): `invoice_number`, `vendor_name`, `invoice_date`, `currency`, `total_amount`

**Output**: `FieldConfidenceResult` with:
- `header: Dict[str, float]` — per-field score for all header fields
- `lines: List[Dict[str, float]]` — per-line scores (description, line_amount, quantity, unit_price, tax_percentage, tax_amount, line_math)
- `weakest_critical_field: str` — name of the lowest-scoring critical field
- `weakest_critical_score: float` — its score
- `low_confidence_fields: List[str]` — all header fields with score < 0.6
- `evidence_flags: Dict[str, str]` — per-field notes when score was adjusted by evidence (see §5.4e)

**Evidence-aware scoring** (optional params added in Phase 2 hardening):

```python
FieldConfidenceService.score(
    normalized, raw_json, repair_actions,
    ocr_text="...",           # raw OCR text for substring confirmation
    evidence_context={        # extraction evidence hints
        "extraction_method": "repaired",   # explicit|repaired|recovered|derived
        "snippets": {"invoice_number": "INV-001 ..."},
    }
)
```

| Signal | Effect |
|--------|--------|
| `extraction_method=repaired` | Caps critical field scores at 0.78 |
| `extraction_method=recovered` | Caps critical field scores at 0.65 |
| `extraction_method=derived` | Caps critical field scores at 0.55 |
| `extraction_method=explicit` | No cap — baseline scoring applies |
| OCR substring match (≥ 3 chars) | Boosts score by +0.10, capped at 0.95 |
| Evidence snippet present (≥ 2 chars) | Boosts score by +0.05, capped at 0.90 |
| `qr_verified[field]` matches extracted value | Sets score to **0.99**; flag `"qr_confirmed"` |
| `qr_verified[field]` mismatches extracted value | Caps score at **0.40**; flag `"qr_mismatch:extracted=...\|qr=..."` |

**QR verification** (`evidence_context["qr_verified"]` dict, populated from `QRInvoiceData.to_evidence_context()`):
- Applied as step 4 of evidence-aware scoring (after method caps, OCR boost, and snippet boost)
- Comparison is separator-normalised: strips `/`, `-`, and spaces; uppercases both sides before comparing
- Fields verified: `invoice_number`, `invoice_date`, `vendor_tax_id`, `total_amount`
- If extracted value is empty (field absent), QR comparison is **skipped** — the 0.0 score stands
- `QR_MISMATCH` decision code is emitted when any field has `"qr_mismatch"` in its flag (see §5.4e)

**Persistence**: `FieldConfidenceService.to_serializable(result)` is embedded into `raw_response["_field_confidence"]` by the pipeline task before `ExtractionResult` is saved.

**Fail-silent**: Any exception returns an empty `FieldConfidenceResult` and logs a warning. The pipeline continues unchanged.

**Integration with ValidationService**: The result dict is attached to `NormalizedInvoice.field_confidence`. `ValidationService` reads it to detect low-confidence critical fields (see §5.4c).

### 5.4c Critical Field Validation

**File**: `apps/extraction/services/validation_service.py` (extended `ValidationResult`)

`ValidationResult` now carries three additional attributes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `critical_failures` | `List[str]` | Names of critical fields with `field_confidence < 0.60` |
| `field_review_flags` | `Dict[str, str]` | field → reason string for each failed field |
| `requires_review_override` | `bool` | `True` if any critical field triggered a failure |

When `requires_review_override=True`, the pipeline **skips auto-approval entirely** and routes directly to human review, regardless of the overall `ExtractionConfidenceScorer` score.

**Critical confidence threshold**: 0.60 (hardcoded; critical fields must clear this to avoid forced review).

### 5.4d ReconciliationValidatorService

**File**: `apps/extraction/services/reconciliation_validator.py`
**Called by**: Pipeline step 4a (after `ValidationService`)

Runs 6 deterministic math checks on the normalized invoice. Produces **structured issues** (not just a penalty string) so the UI and audit log can display exactly which math check failed.

| Check | Issue Code | Severity | Tolerance | Condition |
|-------|-----------|---------|-----------|-----------|
| `TOTAL_CHECK` | `TOTAL_MISMATCH` | **ERROR** | 2% | `subtotal + tax_amount ≠ total_amount` |
| `LINE_SUM_CHECK` | `LINE_SUM_MISMATCH` | WARNING | 5% | `Σ line_amounts ≠ subtotal` |
| `LINE_MATH_CHECK` | `LINE_MATH_MISMATCH` | WARNING | 2% per line | `qty × unit_price ≠ line_amount` |
| `TAX_BREAKDOWN_CHECK` | `TAX_BREAKDOWN_MISMATCH` | WARNING | abs 0.50 | `sum(cgst+sgst+igst+vat) ≠ tax_amount` |
| `TAX_PCT_CHECK` | `TAX_PCT_INCONSISTENT` | INFO | 1pp | `(tax_amount/subtotal×100) ≠ tax_percentage` |
| `LINE_TAX_SUM_CHECK` | `LINE_TAX_SUM_MISMATCH` | INFO | 5% | `Σ line.tax_amounts ≠ tax_amount` |

**`is_clean`**: `True` only when no ERROR-severity issues exist (warnings/info are non-blocking).

**Relationship to `ExtractionConfidenceScorer`**: The scorer already performs binary pass/fail consistency checks that feed into the overall confidence score. `ReconciliationValidatorService` is **additive** — it produces granular structured issues without modifying the scorer.

**Persistence**: Serialized via `ReconciliationValidatorService.to_serializable(result)` and embedded into `raw_response["_validation"]`.

**Fail-silent**: Any exception returns an empty `ReconciliationValidationResult(is_clean=True)` and logs a warning.

### 5.4e Decision Codes (`decision_codes.py`)

**File**: `apps/extraction/decision_codes.py`
**Called by**: Pipeline step 4b (after step 4a)

Centralised machine-readable constants + `derive_codes()` helper. Maps pipeline outputs → a list of string codes the routing engine, recovery lane, and audit log can consume without parsing human-readable messages.

**Constants**:

| Code | Trigger |
|------|---------|
| `INV_NUM_UNRECOVERABLE` | `invoice_number` in `critical_failures` |
| `TOTAL_MISMATCH_HARD` | `TOTAL_MISMATCH` in reconciliation issues |
| `LINE_SUM_MISMATCH` | `LINE_SUM_MISMATCH` in reconciliation issues |
| `LINE_TABLE_INCOMPLETE` | > 50% of lines have `line_amount` score < 0.5 |
| `TAX_ALLOC_AMBIGUOUS` | `TAX_BREAKDOWN_MISMATCH` in reconciliation issues |
| `TAX_BREAKDOWN_MISMATCH` | `TAX_BREAKDOWN_MISMATCH` in reconciliation issues |
| `VENDOR_MATCH_LOW` | `vendor_name` in `critical_failures` OR `vendor_name` score < 0.40 |
| `LOW_CONFIDENCE_CRITICAL_FIELD` | Any field in `critical_failures` |
| `PROMPT_COMPOSITION_FALLBACK_USED` | `prompt_source_type` = `"monolithic_fallback"` or `"agent_default"` |
| `RECOVERY_LANE_INVOKED` | Added by task when recovery lane runs |
| `RECOVERY_LANE_SUCCEEDED` | Added by task when recovery lane produces output |
| `RECOVERY_LANE_FAILED` | Added by task when recovery lane errors |
| `QR_IRN_PRESENT` | `qr_data.irn` is a non-empty 64-char string — IRN available for dedup/audit |
| `QR_DATA_VERIFIED` | QR decoded and ≥ 1 field confirmed (no mismatch detected) |
| `QR_MISMATCH` | `"qr_mismatch"` flag in any `evidence_flags` entry — hard review required |
| `IRN_DUPLICATE` | Same IRN seen on a previously processed invoice — hard duplicate |

**`derive_codes(validation_result, recon_val_result, field_conf_result, prompt_source_type, qr_data=None)`**:
- Accepts all five inputs (all optional)
- Returns a deduplicated list of applicable codes in a stable order
- `qr_data` (`QRInvoiceData | None`): emits `QR_IRN_PRESENT` when IRN present; reads `evidence_flags` to choose `QR_DATA_VERIFIED` vs `QR_MISMATCH`
- Fail-silent: returns `[]` on any exception

**`ROUTING_MAP`**: Maps each code → canonical review queue string.

| Code | Queue |
|------|-------|
| `INV_NUM_UNRECOVERABLE`, `TOTAL_MISMATCH_HARD`, `LINE_TABLE_INCOMPLETE`, `IRN_DUPLICATE` | `EXCEPTION_OPS` |
| `TAX_ALLOC_AMBIGUOUS`, `TAX_BREAKDOWN_MISMATCH` | `TAX_REVIEW` |
| `VENDOR_MATCH_LOW` | `MASTER_DATA_REVIEW` |
| `QR_MISMATCH`, `LOW_CONFIDENCE_CRITICAL_FIELD`, `LINE_SUM_MISMATCH`, `PROMPT_COMPOSITION_FALLBACK_USED` | `AP_REVIEW` |

**`HARD_REVIEW_CODES`**: `{INV_NUM_UNRECOVERABLE, TOTAL_MISMATCH_HARD, LINE_TABLE_INCOMPLETE, IRN_DUPLICATE, QR_MISMATCH}` — always require human review regardless of confidence score.

**Persistence**: Embedded into `raw_response["_decision_codes"]` and included in `AuditService` metadata.

### 5.4f RecoveryLaneService

**File**: `apps/extraction/services/recovery_lane_service.py`
**Called by**: Pipeline step 4c (after `derive_codes()`)

Bounded post-extraction anomaly correction via `InvoiceUnderstandingAgent`. Never replaces the original extraction — output is **additive only**.

**Trigger codes** (named failure modes only — generic low confidence does NOT trigger):

```
INV_NUM_UNRECOVERABLE    TOTAL_MISMATCH_HARD    TAX_ALLOC_AMBIGUOUS
VENDOR_MATCH_LOW         LINE_TABLE_INCOMPLETE  PROMPT_COMPOSITION_FALLBACK_USED
```

**API**:

```python
# Step 1 — deterministic policy (no I/O)
decision: RecoveryDecision = RecoveryLaneService.evaluate(decision_codes)
# decision.should_invoke, decision.trigger_codes, decision.recovery_actions

# Step 2 — agent invocation (fail-silent)
result: RecoveryResult = RecoveryLaneService.invoke(
    decision, invoice_id,
    validation_result=..., field_conf_result=..., actor_user_id=...
)
```

**`RecoveryDecision`** (policy output):

| Field | Type | Description |
|-------|------|-------------|
| `should_invoke` | bool | `True` only when a named trigger code is present |
| `trigger_codes` | List[str] | Which codes triggered recovery |
| `recovery_actions` | List[str] | Bounded actions for the agent (e.g., `verify_invoice_number`) |
| `reason` | str | Human-readable explanation |

**`RecoveryResult`** (agent output):

| Field | Type | Description |
|-------|------|-------------|
| `invoked` | bool | Whether the agent was called |
| `succeeded` | bool | Whether agent produced reasoning or evidence |
| `agent_reasoning` | str | Agent's analysis text (truncated to 500 chars in serialization) |
| `agent_confidence` | float | Agent-reported confidence |
| `agent_recommendation` | str | Agent recommendation type |
| `agent_evidence` | dict | Key evidence dict from agent |
| `agent_run_id` | int | FK to `AgentRun` record |
| `error` | str | Empty string if no error; exception message otherwise |

**Recovery action mapping** (per trigger code):

| Code | Actions |
|------|---------|
| `INV_NUM_UNRECOVERABLE` | `verify_invoice_number`, `cross_check_ocr` |
| `TOTAL_MISMATCH_HARD` | `verify_totals`, `recheck_line_sums`, `check_tax` |
| `TAX_ALLOC_AMBIGUOUS` | `verify_tax_breakdown`, `check_tax_type` |
| `VENDOR_MATCH_LOW` | `verify_vendor_name`, `vendor_lookup` |
| `LINE_TABLE_INCOMPLETE` | `verify_line_items`, `recount_lines` |
| `PROMPT_COMPOSITION_FALLBACK_USED` | `full_invoice_review` |

**Persistence**: `RecoveryResult.to_serializable()` embedded into `raw_response["_recovery"]`. `AgentRun.input_payload["_recovery_meta"]` stamped with trigger codes and actions.

**Fail-silent**: Any exception in `invoke()` returns `RecoveryResult(invoked=True, succeeded=False, error=...)` — the pipeline never raises.

### 5.5 DuplicateDetectionService

**File**: `apps/extraction/services/duplicate_detection_service.py`
**Decorator**: `@observed_service("extraction.duplicate_check", entity_type="Invoice")`

Returns `DuplicateCheckResult` with `is_duplicate`, `duplicate_invoice_id`, `reason`.

**Detection checks** (in order):
1. **Exact match**: `normalized_invoice_number` + vendor's `normalized_name`
2. **Amount match**: `normalized_invoice_number` + `total_amount`
3. Excludes invoices already marked as duplicates

### 5.6 InvoicePersistenceService

**File**: `apps/extraction/services/persistence_service.py`  
**Decorator**: `@observed_service("extraction.persist_invoice", entity_type="Invoice", audit_event="INVOICE_PERSISTED")`

Saves normalized invoice + line items to the database.

**Status determination**:
- Invalid validation → `INVALID`
- Valid validation → `VALIDATED`
- No validation → `EXTRACTED`

**Additional logic**:
- Sets `is_duplicate` flag and `duplicate_of_id` if duplicate detected
- **Total reconciliation** (`_reconcile_totals`): Compares line-item sum against extracted header subtotal. Only overrides when line items sum to **more** than the header (indicating the header was misread/truncated). When line items sum to **less**, keeps the original header total (the LLM likely missed some line items). Recomputes `total_amount = new_subtotal + tax_amount`.
- Resolves vendor via `Vendor.normalized_name` or `VendorAlias.normalized_alias`

**New fields persisted** (added in migration `0009_add_tax_breakdown_vendor_tax_id_buyer_due_date`):

*Invoice header fields*:
- `raw_vendor_tax_id`, `raw_buyer_name`, `raw_due_date` — raw string values from LLM
- `vendor_tax_id` (CharField 100) — GSTIN/VAT/tax registration number
- `buyer_name` (CharField 255) — billed-to entity name
- `due_date` (DateField, nullable) — payment due date parsed from the invoice
- `tax_percentage` (DecimalField 7,4, nullable) — headline tax rate (e.g. 18.0 for 18%)
- `tax_breakdown` (JSONField, default `{}`) — component tax amounts `{cgst, sgst, igst, vat}` as floats

*Line item fields*:
- `tax_percentage` (DecimalField 7,4, nullable) — per-line tax rate percentage

### 5.7 ExtractionResultPersistenceService

**Decorator**: `@observed_service("extraction.persist_result", entity_type="ExtractionResult", audit_event="EXTRACTION_RESULT_PERSISTED")`

Persists `ExtractionResult` record with engine metadata (separate from Invoice data).

**Confidence source**: Prefers `invoice.extraction_confidence` (deterministic score from `ExtractionConfidenceScorer`) over the LLM self-reported `extraction_response.confidence`. Falls back to LLM value only when the deterministic score is unavailable.

**Additional audit events emitted inline**:
- `DUPLICATE_DETECTED` — when `DuplicateCheckResult.is_duplicate` is True
- `VENDOR_RESOLVED` — when vendor is resolved via `Vendor.normalized_name` or `VendorAlias.normalized_alias`

### 5.8 ExtractionApprovalService

**File**: `apps/extraction/services/approval_service.py`  
**Decorators**:
- `create_pending_approval()`: `@observed_service("extraction.create_approval", entity_type="ExtractionApproval", audit_event="EXTRACTION_APPROVAL_PENDING")`
- `try_auto_approve()`: `@observed_service("extraction.try_auto_approve", entity_type="ExtractionApproval")`
- `approve()`: `@observed_service("extraction.approve", entity_type="ExtractionApproval", audit_event="EXTRACTION_APPROVED")`
- `reject()`: `@observed_service("extraction.reject", entity_type="ExtractionApproval", audit_event="EXTRACTION_REJECTED")`

> **Rerun idempotency**: `create_pending_approval()` and `try_auto_approve()` both use `update_or_create(invoice=invoice, defaults={...})` instead of `objects.create()`. This prevents `IntegrityError` on the `OneToOneField` when an invoice is re-extracted — the existing `ExtractionApproval` record is reset to `PENDING` (or `AUTO_APPROVED`) with a fresh data snapshot rather than creating a duplicate row.

See [Section 8: Approval Gate](#8-approval-gate).

### 5.9 UploadService

**File**: `apps/extraction/services/upload_service.py`  
**Decorator**: `@observed_service("extraction.upload", entity_type="DocumentUpload", audit_event="INVOICE_UPLOADED")`

Handles file upload, SHA-256 hash computation, and `DocumentUpload` record creation.

---

## 6. Extraction Core — Multi-Country Extraction Platform

The `apps/extraction_core/` app is a fully governed, multi-country, schema-driven extraction platform. It provides 13 data models, 30 service classes, 60+ API endpoints, and full Django admin coverage. It extends the base extraction pipeline (`apps/extraction/`) with document intelligence, multi-page support, jurisdiction-aware schema-driven extraction, confidence scoring, master data enrichment, review routing, evidence capture, analytics/learning, and country pack governance.

### Architecture

```
                            ┌─────────────────────────────────────┐
                            │    Extraction Core Platform          │
                            │                                      │
  ┌───────────────┐         │  Configuration Layer                 │
  │ TaxJurisdiction│◄────────┤  ├─ TaxJurisdictionProfile          │
  │   Profile      │         │  ├─ ExtractionSchemaDefinition      │
  └───────────────┘         │  ├─ ExtractionRuntimeSettings        │
                            │  └─ EntityExtractionProfile          │
                            │                                      │
                            │  Execution Layer                     │
  ┌───────────────┐         │  ├─ ExtractionRun (tracks pipeline)  │
  │ ExtractionRun  │◄────────┤  ├─ ExtractionFieldValue            │
  │   + children   │         │  ├─ ExtractionLineItem              │
  └───────────────┘         │  ├─ ExtractionEvidence               │
                            │  ├─ ExtractionIssue                  │
                            │  ├─ ExtractionApprovalRecord         │
                            │  └─ ExtractionCorrection             │
                            │                                      │
                            │  Governance Layer                    │
  ┌───────────────┐         │  ├─ CountryPack                      │
  │  CountryPack   │◄────────┤  └─ ExtractionAnalyticsSnapshot     │
  └───────────────┘         └─────────────────────────────────────┘
```

### 4-Tier Jurisdiction Resolution

Resolution follows a strict precedence cascade:

| Tier | Source | Service | When Used |
|------|--------|---------|-----------|
| 1 | Document-level declared | `JurisdictionResolutionService` | Caller provides explicit country/regime |
| 2 | Entity profile | `EntityExtractionProfile` | Vendor has configured extraction preferences |
| 3 | System-level settings | `ExtractionRuntimeSettings` | Global defaults (AUTO/FIXED/HYBRID mode) |
| 4 | Auto-detection fallback | `JurisdictionResolverService` | Multi-signal scoring (GSTIN→IN, TRN→AE, VAT→SA) |

**Modes**: AUTO (always detect), FIXED (use configured), HYBRID (detect + validate + mismatch warnings)

### ExtractionPipeline (11-Stage Governed Pipeline)

**File**: `apps/extraction_core/services/extraction_pipeline.py`  
**Class**: `ExtractionPipeline`

| Stage | Service | Description |
|-------|---------|-------------|
| 1 | `JurisdictionResolutionService` | 4-tier jurisdiction resolution |
| 2 | `SchemaRegistryService` | Jurisdiction-aware schema selection |
| 3 | `PromptBuilderService` | Dynamic prompt from schema + jurisdiction |
| 4 | `PageParser` | Multi-page OCR segmentation, header/footer dedup |
| 5 | Deterministic extraction | Rule-based field extraction from OCR text |
| 5a | `TableStitcher` + `LineItemExtractor` | Cross-page table reconstruction + line item extraction |
| 5b | `LLMExtractionAdapter` | LLM-based extraction for remaining/low-confidence fields |
| 6 | `EnhancedNormalizationService` | Country-specific field normalization (dates, amounts, tax IDs) |
| 7 | `EnhancedValidationService` | Country-aware validation with ExtractionIssue persistence |
| 7b | `MasterDataEnrichmentService` | Post-extraction vendor matching, PO lookup, confidence adjustments |
| 8 | `ConfidenceScorer` | Multi-dimensional confidence scoring (header/tax/line/jurisdiction) |
| 8b | `EvidenceCaptureService` | Capture field provenance (snippets, pages, bounding boxes) |
| 9 | `ReviewRoutingEngine` | Queue-based review routing with priority tiers |
| 10 | Persist | Save `ExtractionRun` + field values + line items + evidence + issues |
| 11 | `ExtractionAuditService` | Emit audit events for each pipeline stage |

Each stage emits a governance audit event (e.g., `JURISDICTION_RESOLVED`, `SCHEMA_SELECTED`, `EVIDENCE_CAPTURED`, `REVIEW_ROUTE_ASSIGNED`).

> **Dataclass naming**: The runtime dataclass is `ExtractionExecutionResult` (in `extraction_service.py`) to avoid collision with the Django model `ExtractionResult` (in `apps/extraction/models.py`). A backward-compatible alias `ExtractionResult = ExtractionExecutionResult` is provided.

### ExtractionService (Legacy Pipeline Orchestrator)

**File**: `apps/extraction_core/services/extraction_service.py`  
**Class**: `ExtractionService`

The original pipeline orchestrator. Coordinates jurisdiction → schema → deterministic extraction → LLM fallback → normalization → validation → enrichment → confidence → routing → persistence.

> The `ExtractionExecutionResult` dataclass returned by this service was previously named `ExtractionResult`. The rename avoids collision with the Django model of the same name in `apps/extraction/models.py`.

### Data Models (13 models)

#### Configuration Models

**TaxJurisdictionProfile** — Tax jurisdiction master data:
- `country_code`, `country_name`, `tax_regime`, `regime_full_name`, `default_currency`
- `tax_id_label`, `tax_id_regex`, `date_formats` (JSON), `locale_code`, `fiscal_year_start_month`
- Unique: (`country_code`, `tax_regime`)

**ExtractionSchemaDefinition** — Versioned extraction schema per jurisdiction:
- `jurisdiction` (FK), `document_type`, `schema_version`, `name`, `description`
- `header_fields_json`, `line_item_fields_json`, `tax_fields_json`, `config_json`
- Unique: (`jurisdiction`, `document_type`, `schema_version`)
- Method: `get_all_field_keys()` returns combined field list

**ExtractionRuntimeSettings** — Singleton system-level configuration:
- `jurisdiction_mode` (AUTO|FIXED|HYBRID), `default_country_code`, `default_regime_code`
- `enable_jurisdiction_detection`, `allow_manual_override`, `confidence_threshold_for_detection`
- `fallback_to_detection_on_schema_miss`
- Classmethod: `get_active()` returns current active record

**EntityExtractionProfile** — Per-vendor extraction preferences:
- `entity` (OneToOne Vendor), `default_country_code`, `default_regime_code`
- `jurisdiction_mode`, `schema_override_code`, `validation_profile_override_code`, `normalization_profile_override_code`

#### Execution/Tracking Models

**ExtractionRun** — Primary execution record (~25 fields):
- Status: PENDING|RUNNING|COMPLETED|FAILED|CANCELLED
- Jurisdiction: `country_code`, `regime_code`, `jurisdiction_source` (FIXED|ENTITY|AUTO_DETECTED), FK to TaxJurisdictionProfile
- Schema: `schema_code`, `schema_version`, FK to ExtractionSchemaDefinition
- Confidence: `overall_confidence`, `header_confidence`, `tax_confidence`, `line_item_confidence`, `jurisdiction_confidence`
- Output: `extracted_data_json`, `extraction_method`, `error_message`
- Review: `review_queue`, `requires_review`, `review_reasons_json`
- Timing: `started_at`, `completed_at`, `duration_ms`
- Metrics: `field_count`, `mandatory_coverage_pct`, `field_coverage_pct`
- Indexes: (`country_code`, `regime_code`), (`status`, `created_at`)

**ExtractionFieldValue** — Per-field result with confidence & correction tracking:
- `extraction_run` (FK), `field_code`, `value`, `normalized_value`, `confidence`
- `extraction_method`, `is_corrected`, `corrected_value`, `category` (HEADER|LINE_ITEM|TAX|PARTY)
- `line_item_index`, `is_valid`, `validation_message`
- Index: (`extraction_run`, `field_code`)

**ExtractionLineItem** — Structured line item record:
- `extraction_run` (FK), `line_index`, `data_json`, `confidence`, `page_number`, `is_valid`
- Unique: (`extraction_run`, `line_index`)

**ExtractionEvidence** — Provenance tracking per field:
- `extraction_run` (FK), `field_code`, `page_number`, `snippet` (OCR text)
- `bounding_box` (JSON coords), `extraction_method`, `confidence`, `line_item_index`

**ExtractionIssue** — Validation/extraction issues:
- `extraction_run` (FK), `severity` (ERROR|WARNING|INFO), `field_code`, `check_type`, `message`, `details_json`

**ExtractionApprovalRecord** — Approval gate for run:
- `extraction_run` (OneToOne), `action` (APPROVE|REJECT|ESCALATE|SEND_BACK)
- `approved_by` (FK User), `comments`, `decided_at`

**ExtractionCorrection** — Field correction audit trail:
- `extraction_run` (FK), `field_code`, `original_value`, `corrected_value`
- `correction_reason`, `corrected_by` (FK User)

#### Governance/Analytics Models

**ExtractionAnalyticsSnapshot** — Learning/analytics data:
- `snapshot_type`, `country_code`, `regime_code`, `period_start`, `period_end`
- `data_json`, `run_count`, `correction_count`, `average_confidence`

**CountryPack** — Country governance record:
- `jurisdiction` (OneToOne TaxJurisdictionProfile), `pack_status` (DRAFT|ACTIVE|DEPRECATED)
- `schema_version`, `validation_profile_version`, `normalization_profile_version`
- `activated_at`, `deactivated_at`, `config_json`, `notes`

### Key Dataclasses

**ExtractionOutputContract** (`output_contract.py`):
- `meta` — `MetaBlock` (extraction_id, document_type, jurisdiction, schema, prompt, method, timestamps, duration)
- `fields` — dict of `FieldValue` (value, normalized, confidence, method, evidence list)
- `parties` — `PartiesBlock` (supplier, buyer, ship_to, bill_to)
- `tax` — `TaxBlock` (tax_id, rates, breakdown, totals)
- `line_items` — list of `LineItemRow`
- `references` — `ReferencesBlock` (po_numbers, grn_refs, contracts, shipments)
- `warnings` — list of `WarningItem`

**ExtractionExecutionResult** (dataclass in `extraction_service.py`, aliased as `ExtractionResult` for backward compatibility):
- `fields`, `line_items`, `jurisdiction` (JurisdictionMeta), `document_intelligence` (DocumentIntelligenceResult)
- `enrichment` (EnrichmentResult), `page_info` (ParsedDocument), `confidence_breakdown` (ConfidenceBreakdown)
- `review_decision` (ReviewRoutingDecision), `validation_issues`, `warnings`, `overall_confidence`, `duration_ms`

> **Naming**: The runtime dataclass is `ExtractionExecutionResult` to distinguish it from the Django model `ExtractionResult` (UI-facing summary). The alias `ExtractionResult = ExtractionExecutionResult` remains for backward compatibility.

### Service Directory (30 services)

#### Core Pipeline & Orchestration

| Service | File | Purpose |
|---------|------|---------|
| `ExtractionPipeline` | `extraction_pipeline.py` | 11-stage governed pipeline orchestrator with audit events |
| `ExtractionService` | `extraction_service.py` | Original pipeline orchestrator |
| `BaseExtractionService` | `base_extraction_service.py` | Schema-driven extraction base class |

#### Jurisdiction Resolution

| Service | File | Purpose |
|---------|------|---------|
| `JurisdictionResolverService` | `jurisdiction_resolver.py` | Multi-signal jurisdiction detection (GSTIN, TRN, VAT) |
| `JurisdictionResolutionService` | `resolution_service.py` | 4-tier precedence cascade (document → entity → system → auto-detect) |

#### Schema & Registry

| Service | File | Purpose |
|---------|------|---------|
| `SchemaRegistryService` | `schema_registry.py` | Cached schema lookup (5-min TTL), version-aware |

#### Document Intelligence (Pre-Extraction)

| Service | File | Purpose |
|---------|------|---------|
| `DocumentTypeClassifier` | `document_classifier.py` | Multilingual keyword classification (EN/AR/HI/FR/DE/ES); types: INVOICE, CREDIT_NOTE, DEBIT_NOTE, GRN, PURCHASE_ORDER, DELIVERY_NOTE, STATEMENT. Includes **negative signals** (−2.0 to −3.0) for report-adjacent terms ("reconciliation", "summary report", "3-way", "matching report", "variance report", "audit report") on GRN/PO/DELIVERY_NOTE to prevent false classification of reconciliation/summary reports. |
| `PartyExtractor` | `party_extractor.py` | Supplier/buyer/ship-to/bill-to extraction |
| `RelationshipExtractor` | `relationship_extractor.py` | PO/GRN/contract/shipment cross-reference extraction |
| `DocumentIntelligenceService` | `document_intelligence.py` | Pre-extraction analysis orchestrator |

#### Field Extraction & Parsing

| Service | File | Purpose |
|---------|------|---------|
| `LineItemExtractor` | `line_item_extractor.py` | Schema-driven line item extraction with column mapping |
| `PageParser` | `page_parser.py` | Multi-page segmentation, header/footer dedup |
| `TableStitcher` | `table_stitcher.py` | Cross-page table continuation detection |

#### Normalization & Validation

| Service | File | Purpose |
|---------|------|---------|
| `NormalizationService` | `normalization_service.py` | Jurisdiction-driven field normalization |
| `EnhancedNormalizationService` | `enhanced_normalization.py` | Country-specific normalization (IN/AE/SA/DE/FR currency/date localization) |
| `ValidationService` | `validation_service.py` | Jurisdiction-driven field validation |
| `EnhancedValidationService` | `enhanced_validation.py` | Country-aware validation with ExtractionIssue persistence |

#### Evidence, Audit & Tracing

| Service | File | Purpose |
|---------|------|---------|
| `EvidenceCaptureService` | `evidence_service.py` | Capture field provenance (snippets, pages, bounding boxes) → ExtractionEvidence records |
| `ExtractionAuditService` | `extraction_audit.py` | Extraction-specific audit logging (8 event types per pipeline stage) |

#### Confidence & Review Routing

| Service | File | Purpose |
|---------|------|---------|
| `ConfidenceScorer` | `confidence_scorer.py` | Multi-dimensional scoring for governed pipeline (header=0.3, tax=0.3, line_item=0.2, jurisdiction=0.2) |
| `ReviewRoutingService` | `review_routing.py` | Confidence-driven review routing with priority tiers |
| `ReviewRoutingEngine` | `review_routing_engine.py` | Queue-based routing (EXCEPTION_OPS, TAX_REVIEW, VENDOR_OPS); thresholds: CRITICAL=0.4, LOW=0.65, TAX=0.6. Extended with optional `decision_codes` param — code-based routing runs first (Rule 0) and can short-circuit confidence rules for `HARD_REVIEW_CODES`. |

#### LLM & Prompts

| Service | File | Purpose |
|---------|------|---------|
| `PromptBuilderService` | `prompt_builder.py` | Dynamic LLM prompt from schema + jurisdiction |
| `PromptBuilderService` | `prompt_builder_service.py` | Enhanced prompt builder (global/country/regime/document/schema/tax/evidence sections) |
| `LLMExtractionAdapter` | `llm_extraction_adapter.py` | LLM client wrapper; retry on parse failures |

#### Master Data & Learning

| Service | File | Purpose |
|---------|------|---------|
| `MasterDataEnrichmentService` | `master_data_enrichment.py` | Post-extraction vendor/PO/customer matching + confidence adjustments |
| `LearningFeedbackService` | `learning_service.py` | Analytics from corrections & failures → ExtractionAnalyticsSnapshot |

#### Country Governance

| Service | File | Purpose |
|---------|------|---------|
| `CountryPackService` | `country_pack_service.py` | Multi-country support lifecycle: DRAFT → ACTIVE → DEPRECATED |

#### Output Contract

| Service | File | Purpose |
|---------|------|---------|
| ExtractionOutputContract | `output_contract.py` | Canonical output shape (MetaBlock, FieldValue, PartiesBlock, TaxBlock, LineItemRow, ReferencesBlock) |

### API Endpoints

**Configuration API** (`/api/v1/extraction-core/`):

| Method | Path | View | Description |
|--------|------|------|-------------|
| GET/POST | `/jurisdictions/` | `TaxJurisdictionProfileViewSet` | List/create tax jurisdictions |
| GET/PUT/DELETE | `/jurisdictions/<id>/` | | Retrieve/update/delete |
| GET/POST | `/schemas/` | `ExtractionSchemaDefinitionViewSet` | List/create schemas |
| GET/PUT/DELETE | `/schemas/<id>/` | | Retrieve/update/delete |
| GET | `/schemas/<id>/field-definitions/` | | Get fields for schema |
| GET | `/schemas/<id>/versions/` | | List schema versions |
| GET/POST | `/runtime-settings/` | `ExtractionRuntimeSettingsViewSet` | List/create settings |
| GET/PUT/DELETE | `/runtime-settings/<id>/` | | Retrieve/update/delete |
| GET | `/runtime-settings/active/` | | Get active runtime settings |
| GET/POST | `/entity-profiles/` | `EntityExtractionProfileViewSet` | List/create vendor profiles |
| GET/PUT/DELETE | `/entity-profiles/<id>/` | | Retrieve/update/delete |
| POST | `/resolve-jurisdiction/` | `JurisdictionResolveView` | Simple jurisdiction resolution |
| POST | `/resolve-jurisdiction-full/` | `JurisdictionResolutionView` | Full 4-tier resolution (jurisdiction + schema + config) |
| POST | `/lookup-schema/` | `SchemaLookupView` | Schema lookup by jurisdiction + doc type |
| POST | `/extract/` | `ExtractionView` | Trigger extraction |

**Execution API** (`/api/v1/extraction-pipeline/`):

| Method | Path | View | Description |
|--------|------|------|-------------|
| POST | `/run/` | `RunPipelineView` | Trigger governed extraction pipeline |
| GET | `/runs/` | `ExtractionRunViewSet` | List runs (filter: country, status, queue, requires_review, document) |
| GET | `/runs/<id>/` | | Run detail |
| GET | `/runs/<id>/summary/` | | Lightweight summary |
| GET | `/runs/<id>/fields/` | | List field values |
| GET | `/runs/<id>/line-items/` | | List line items |
| GET | `/runs/<id>/validation/` | | List issues |
| GET | `/runs/<id>/evidence/` | | List evidence records |
| GET | `/runs/<id>/corrections/` | | List corrections |
| POST | `/runs/<id>/correct-field/` | | Correct a field value |
| POST | `/runs/<id>/approve/` | | Approve extraction |
| POST | `/runs/<id>/reject/` | | Reject extraction |
| POST | `/runs/<id>/reprocess/` | | Reprocess extraction |
| POST | `/runs/<id>/escalate/` | | Escalate to review queue |
| GET | `/analytics/` | `ExtractionAnalyticsViewSet` | List analytics snapshots |
| GET/POST | `/country-packs/` | `CountryPackViewSet` | List/create country packs |

### Serializers (~25 classes)

**Configuration serializers** (`serializers.py`): `TaxJurisdictionProfileSerializer`, `TaxJurisdictionProfileListSerializer`, `ExtractionSchemaDefinitionSerializer`, `ExtractionSchemaDefinitionListSerializer`, `ExtractionRuntimeSettingsSerializer`, `EntityExtractionProfileSerializer`, `EntityExtractionProfileListSerializer`

**Request serializers**: `JurisdictionResolveRequestSerializer`, `JurisdictionResolutionRequestSerializer`, `SchemaLookupRequestSerializer`, `ExtractionRequestSerializer`

**Execution serializers** (`extraction_serializers.py`): `ExtractionRunListSerializer`, `ExtractionRunDetailSerializer`, `ExtractionRunSummarySerializer`, `ExtractionFieldValueSerializer`, `ExtractionLineItemSerializer`, `ExtractionEvidenceSerializer`, `ExtractionIssueSerializer`, `ExtractionApprovalRecordSerializer`, `ExtractionCorrectionSerializer`, `ExtractionAnalyticsSnapshotSerializer`, `CountryPackSerializer`, `ApproveRejectRequestSerializer`, `CorrectFieldRequestSerializer`, `EscalateRequestSerializer`, `RunPipelineRequestSerializer`

### Django Admin (13 models registered)

All models registered in `apps/extraction_core/admin.py` with full admin features:

| Admin Class | List Display Highlights |
|-------------|------------------------|
| `TaxJurisdictionProfileAdmin` | country_code, tax_regime, default_currency, is_active |
| `ExtractionSchemaDefinitionAdmin` | name, jurisdiction, document_type, schema_version, is_active |
| `ExtractionRuntimeSettingsAdmin` | name, jurisdiction_mode, defaults, detection settings |
| `EntityExtractionProfileAdmin` | entity, country_code, regime_code, jurisdiction_mode |
| `ExtractionRunAdmin` | id, document, status, country_code, overall_confidence, review_queue, duration_ms |
| `ExtractionFieldValueAdmin` | extraction_run, field_code, value, confidence, category, is_corrected |
| `ExtractionLineItemAdmin` | extraction_run, line_index, confidence, is_valid |
| `ExtractionEvidenceAdmin` | extraction_run, field_code, page_number, extraction_method |
| `ExtractionIssueAdmin` | extraction_run, severity, field_code, check_type, message |
| `ExtractionApprovalRecordAdmin` | extraction_run, action, approved_by, decided_at |
| `ExtractionCorrectionAdmin` | extraction_run, field_code, original/corrected values, corrected_by |
| `ExtractionAnalyticsSnapshotAdmin` | snapshot_type, country_code, regime_code, run_count, average_confidence |
| `CountryPackAdmin` | jurisdiction, pack_status, schema/validation/normalization versions |

### Migrations

| File | Description |
|------|-------------|
| `0001_initial.py` | Creates initial models |
| `0002_entityextractionprofile_extractionruntimesettings.py` | Adds EntityExtractionProfile + ExtractionRuntimeSettings |
| `0003_add_extraction_run_pipeline_models.py` | Adds ExtractionRun + all pipeline tracking models |

---

## 7. Master Data Enrichment

**File**: `apps/extraction_core/services/master_data_enrichment.py`  
**Pipeline position**: Step 7b (after validation, before confidence scoring)

The Master Data Enrichment Service matches extracted entities against the system's master data (Vendors, VendorAliases, PurchaseOrders) and adjusts extraction confidence based on match quality.

### Matching Tiers

**Vendor Matching** (`_match_vendor()`) — 3-tier cascade:

| Tier | Match Type | Confidence | Description |
|------|-----------|------------|-------------|
| 1 | `EXACT_TAX_ID` | 0.98 | Exact tax ID match against `Vendor.tax_id` |
| 2 | `ALIAS` | 0.95 | Normalized alias match against `VendorAlias.normalized_alias` |
| 3 | `FUZZY` | 0.70–0.95 | SequenceMatcher fuzzy name match (threshold: 0.70, high: 0.85) |

- Scopes vendor candidates by country (if `country_code` provided)
- Limits to 500 candidates for fuzzy matching
- Uses `_normalize_name()` — lowercase, strip company suffixes (Pvt Ltd, GmbH, LLC, etc.), collapse whitespace, remove punctuation

**Customer Matching** (`_match_customer()`):
- Checks `VendorAlias` table first (buyer may appear as alias)
- Falls back to fuzzy match against `PurchaseOrder.buyer_name` values

**PO Lookup** (`_lookup_po()`):
- Exact match on `PurchaseOrder.po_number`
- Falls back to normalized match on `PurchaseOrder.normalized_po_number`
- Uses `_normalize_po_number()` — uppercase, remove separators

### Confidence Adjustments

| Adjustment | Value | Condition |
|-----------|-------|----------|
| `VENDOR_MATCH_BOOST` | +0.05 | Vendor matched (any tier) |
| `VENDOR_MISMATCH_PENALTY` | −0.08 | Tax ID present but no vendor found |
| `PO_MATCH_BOOST` | +0.05 | PO number found in system |
| `PO_VENDOR_MATCH_BOOST` | +0.03 | Cross-validated: PO vendor = matched vendor |

- Warns on PO vendor mismatch (PO belongs to different vendor than matched)
- All adjustments are clamped to 0.0–1.0 range

### Dataclasses

- `MasterDataMatch` — match_type, entity_id, entity_code, entity_name, matched_value, similarity, confidence
- `POLookupResult` — found, po_id, po_number, vendor_id, vendor_name, po_status, total_amount, currency, confidence
- `EnrichmentResult` — vendor_match, customer_match, po_lookup, confidence_adjustments, warnings, duration_ms; properties: `vendor_id`, `customer_id`, `match_confidence`

### Integration

The enrichment result is:
- Stored in `ExtractionResult.enrichment` dataclass field
- Serialized in `to_dict()` for JSON persistence
- Persisted to `extracted_data_json` on `ExtractionDocument`
- Displayed in the Extraction Review Console (Master Data Matches card)

---

## 8. Approval Gate

### Overview

Every extracted invoice must pass through a human approval step before entering reconciliation. This ensures extraction quality while building analytics for future automation.

### Dual-Model Pattern

Approval state is tracked in **two models** serving different purposes:

| Model | App | Owner | Purpose |
|-------|-----|-------|---------|
| `ExtractionApproval` | `apps/extraction` | `ExtractionApprovalService` | **Business state machine** — drives Invoice status transitions, tracks field corrections, computes touchless rate. OneToOne with Invoice. |
| `ExtractionApprovalRecord` | `apps/extraction_core` | `GovernanceTrailService` | **Governance mirror** — immutable audit record per ExtractionRun. Written exclusively by `GovernanceTrailService`. OneToOne with ExtractionRun. |

Both records are created on every approval/rejection:
1. `ExtractionApprovalService.approve()` / `.reject()` updates `ExtractionApproval` (business state) then calls `GovernanceTrailService.record_approval_decision()` to write `ExtractionApprovalRecord` (governance trail).
2. `ExtractionRunViewSet.approve()` / `.reject()` (governed API) delegates entirely to `GovernanceTrailService.record_approval_decision()` — no direct `ExtractionApprovalRecord` writes in the viewset.

**GovernanceTrailService uses `update_or_create(extraction_run=run, defaults={...})`** inside `transaction.atomic()`, so re-decisions (e.g., second approval after reprocess) safely update the existing record rather than violating the OneToOne constraint.

### Approval Flow

```
Extraction Complete
       │
       ▼
  Auto-approve enabled AND confidence ≥ threshold?
       │
  ┌────┴────┐
  YES       NO
  │         │
  ▼         ▼
AUTO_APPROVED  PENDING_APPROVAL
(is_touchless=True)  │
  │         │
  ▼         ▼
READY_FOR_RECON  Approval Queue UI
  │         │
  ▼    ┌────┴─────────┐
AP Case   APPROVE  REJECT  REPROCESS
(already    │       │       │
 exists)    ▼       ▼       ▼
   READY_FOR_RECON  INVALID  New ExtractionRun created
   (case resumes)   (re-extract)  ExtractionApproval reset to PENDING
                                  ExtractionApprovalRecord history retained

   Note: AP Case is created at upload time (before extraction begins).
   The invoice is linked to the case after extraction persistence via
   CaseCreationService.link_invoice_to_case().  The case pipeline
   pauses at EXTRACTION_APPROVAL stage if the invoice needs human
   approval.  On approve, the existing case resumes from
   PATH_RESOLUTION onward.  On reject, the case remains paused.

   ─────── Both records written on every decision ───────
   ExtractionApproval (business)  ←  ExtractionApprovalService
   ExtractionApprovalRecord (governance)  ←  GovernanceTrailService
```

### Reprocess Behavior

When an extraction is reprocessed:
- A **new** `ExtractionRun` is created (the old run remains for audit history)
- `ExtractionApproval.status` resets to `PENDING` (same record, updated in place)
- The previous `ExtractionApprovalRecord` is retained (immutable history per run)
- A new `ExtractionApprovalRecord` is created for the new run upon the next approval/rejection
- **Credit reserve**: 1 credit is reserved (`reference_type="reprocess"`, `reference_id=upload.pk`) before re-extraction starts
- **Finalization guard**: Reprocess is blocked if the current `ExtractionApprovalRecord` has status `APPROVED` or `AUTO_APPROVED`. Both `extraction_rerun` (template view) and `ExtractionRunViewSet.reprocess()` (API) enforce this — API returns HTTP 409 CONFLICT

### Concurrency & Locking

Approval and rejection operations use row-level locking to prevent race conditions:

- **`ExtractionApprovalService.approve()`** / **`.reject()`**: Re-fetch the `ExtractionApproval` row with `select_for_update()` inside `@transaction.atomic` before checking the `PENDING` precondition. This serializes concurrent approve/reject attempts on the same invoice.
- **CreditService**: All balance-mutating methods (`reserve`, `consume`, `refund`, `allocate`, `adjust`) use `select_for_update()` on `UserCreditAccount` inside `transaction.atomic()`.
- **GovernanceTrailService**: Uses `update_or_create()` inside `transaction.atomic()` — safe against parallel writes to the same ExtractionRun's approval record.

Valid state transitions for `ExtractionApproval.status`:
```
PENDING → APPROVED   (approve)
PENDING → REJECTED   (reject)
PENDING → PENDING    (reprocess resets, then re-enters queue)
AUTO_APPROVED → ×    (terminal state, no further transitions)
```

### Service Methods

**`create_pending_approval(invoice, extraction_result)`**
- Uses `update_or_create(invoice=invoice, defaults={...})` to create or reset the `ExtractionApproval` record
- On first run: creates with `status=PENDING`; on rerun: resets `status=PENDING`, clears `reviewed_by`, `reviewed_at`, `is_touchless=False`
- Snapshots current header + line values as `original_values_snapshot`
- Logs "Created" vs "Reset existing" for observability
- Called when auto-approval is not triggered

**`try_auto_approve(invoice, extraction_result)`**
- Checks `EXTRACTION_AUTO_APPROVE_ENABLED` setting (default: `false`)
- If enabled and confidence >= `EXTRACTION_AUTO_APPROVE_THRESHOLD` (default: `1.1` — effectively disabled):
  - Uses `update_or_create(invoice=invoice, defaults={...})` to create or reset the `ExtractionApproval` record with `status=AUTO_APPROVED, is_touchless=True, reviewed_at=timezone.now()`
  - Sets `invoice.status = READY_FOR_RECON`
  - Returns the approval object
- Otherwise returns `None`

**`approve(approval, user, corrections=None)`**
- Locks the `ExtractionApproval` row with `select_for_update()` and verifies `status == PENDING`
- Applies field corrections to Invoice + LineItems
- Creates `ExtractionFieldCorrection` records for each changed field
- Sets `is_touchless = (len(corrections) == 0)`
- Transitions invoice to `READY_FOR_RECON`
- Logs `EXTRACTION_APPROVED` audit event
- Calls `GovernanceTrailService.record_approval_decision()` to write governance mirror

**`reject(approval, user, reason)`**
- Locks the `ExtractionApproval` row with `select_for_update()` and verifies `status == PENDING`
- Sets `status = REJECTED` with `rejection_reason`
- Transitions invoice to `INVALID`
- Logs `EXTRACTION_REJECTED` audit event
- Calls `GovernanceTrailService.record_approval_decision()` to write governance mirror

**`get_approval_analytics()`**
- Returns analytics dict: `total`, `pending`, `approved`, `auto_approved`, `rejected`, `touchless_count`, `human_corrected_count`, `touchless_rate`, `avg_corrections_per_review`, `most_corrected_fields` (top 10)

### Correctable Fields

| Type | Fields |
|------|--------|
| Header | `invoice_number`, `po_number`, `invoice_date`, `due_date`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `raw_vendor_name`, `vendor_tax_id`, `buyer_name`, `tax_percentage` |
| Line Item | `description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`, `tax_percentage` |

### Auto-Approval Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `EXTRACTION_AUTO_APPROVE_ENABLED` | `false` | Master toggle for auto-approval |
| `EXTRACTION_AUTO_APPROVE_THRESHOLD` | `1.1` | Confidence threshold (1.1 = unreachable = all human) |

**Design rationale**: Auto-approval is deliberately disabled by default so all extractions require human review initially. As the system builds confidence and correction analytics accumulate, the threshold can be lowered (e.g., 0.95) to enable gradual automation.

---

## 9. Agent Framework Integration

### InvoiceExtractionAgent

**File**: `apps/agents/services/agent_classes.py`  
**Type**: `AgentType.INVOICE_EXTRACTION`

A single-shot LLM agent (no tool calls, no ReAct loop) optimized for deterministic JSON extraction.

| Property | Value |
|----------|-------|
| Temperature | 0.0 |
| Max tokens | 4096 |
| Response format | `{"type": "json_object"}` |
| Tools | None (empty list) |
| System prompt | `PromptRegistry.get("extraction.invoice_system")` |

**Execution flow**:
1. Creates `AgentRun` record
2. `_init_messages()` — selects system prompt and **records prompt source**:
   - `ctx.extra["composed_prompt"]` present → uses it, sets `self._prompt_source_type = "composed"`
   - Absent → falls back to `self.system_prompt` (PromptRegistry), sets `self._prompt_source_type = "monolithic_fallback"`
3. Calls LLM with `response_format=json_object`
4. Saves assistant message to `AgentMessage`
5. Parses JSON → `AgentOutput` (with confidence, evidence, reasoning)
6. Finalizes `AgentRun` with output payload + token usage
7. **Persists prompt metadata** to `AgentRun.input_payload["_prompt_meta"]` (fail-silent):

```python
{
    "prompt_source_type": "composed" | "monolithic_fallback",
    "prompt_hash": "abc123...",        # 16-char sha256 from PromptComposition
    "base_prompt_key": "extraction.invoice_base",
    "base_prompt_version": "v3",
    "category_prompt_key": "extraction.goods_overlay",
    "category_prompt_version": "v1",
    "country_prompt_key": "",
    "country_prompt_version": "",
    "invoice_category": "goods",
    "components": {"base": "v3", "goods": "v1"},
}
```

`AgentRun.prompt_version` is set to `prompt_hash[:50]` (or `source_type[:50]` if no hash).
`AgentRun.invocation_reason` is set to `"extraction:<source_type>"`.

**Prompt source precedence**:
1. `ctx.extra["composed_prompt"]` — modular composed prompt from `InvoicePromptComposer`
2. `self.system_prompt` → `PromptRegistry.get("extraction.invoice_system")` — monolithic fallback
3. If PromptRegistry also fails, the agent errors (not a silent fallback)

When path 2 is taken, `PROMPT_COMPOSITION_FALLBACK_USED` decision code is emitted in step 4b.

**Traceability**:
- `AgentRun` — execution metadata, LLM model, token usage, duration, `prompt_version`, `invocation_reason`
- `AgentRun.input_payload["_prompt_meta"]` — full prompt source audit trail
- `AgentMessage` — system, user, and assistant messages
- No `AgentStep` or `ToolCall` records (single-shot, no tool loop)

### InvoiceUnderstandingAgent

**File**: `apps/agents/services/agent_classes.py`  
**Type**: `AgentType.INVOICE_UNDERSTANDING`

A deeper analysis agent that runs after extraction for low-confidence or ambiguous results. Uses the full ReAct loop with tools.

| Property | Value |
|----------|-------|
| System prompt | `PromptRegistry.get("agent.invoice_understanding")` |
| Tools | `invoice_details`, `po_lookup`, `vendor_search` |
| Max iterations | 6 (inherited from `BaseAgent`) |

**When invoked**: Two invocation paths:

1. **Case orchestrator path** (original) — runs for low-confidence extractions or when validation warnings are present during case processing.
2. **Recovery lane path** (Phase 2 hardening) — invoked by `RecoveryLaneService` during the extraction pipeline (step 4c) when named failure modes are detected. In this path, `ctx.reconciliation_result=None` and `ctx.extra` carries `recovery_trigger_codes`, `recovery_actions`, `validation_warnings`, and `low_confidence_fields`. The agent's `AgentRun.input_payload["_recovery_meta"]` is stamped with the trigger context, and `invocation_reason` is set to `"RECOVERY_LANE"`.

---

## 10. LLM Prompts

The prompt layer has three builder strategies. The **modular composition** path is the outer primary pipeline; the **schema-driven v2.0** path runs *inside* it at step 1b as a governed enrichment layer (fail-silent, wrapped in `try/except`). Agent and case prompts are resolved via the monolithic `PromptRegistry` path.

> **Key relationship**: Callers always invoke `InvoiceExtractionAgent` (modular composition). `InvoiceExtractionAgent` internally calls `ExtractionPipeline.run()` at step 1b via `_run_governed_pipeline()`. The schema-driven path is *not* a parallel entry point that callers choose -- it runs automatically inside Path A and produces governance records only. If it fails, Path A continues and labels the result "Legacy source".

| Path | Role | Primary class | File | Used by |
|---|---|---|---|---|
| **Modular composition** | Primary outer pipeline | `InvoicePromptComposer` | `apps/extraction/services/invoice_prompt_composer.py` | `InvoiceExtractionAgent` -- all Celery tasks and direct callers |
| **Schema-driven v2.0** | Step 1b enrichment (fail-silent, inside Path A) | `PromptBuilderService` | `apps/extraction_core/services/prompt_builder_service.py` | `ExtractionPipeline`, `LLMExtractionAdapter` -- called from inside `InvoiceExtractionAgent` |
| **Monolithic** | Agent + case resolution | `PromptRegistry` | `apps/core/prompt_registry.py` | All 8 ReAct agents, case-level calls, `InvoiceUnderstandingAgent` |

---

### 10.1 Monolithic path -- `PromptRegistry`

**File**: `apps/core/prompt_registry.py`

Used by the agent-based extraction path and all 8 ReAct agents. Resolution order (highest to lowest priority):

1. **Langfuse** (name: `extraction-invoice_system`, label `production`, 60s in-process TTL cache)
2. **Database** -- `PromptTemplate` model (slug `extraction.invoice_system`, `is_active=True`)
3. **Hardcoded default** -- `_DEFAULTS["extraction.invoice_system"]` in `prompt_registry.py`

```python
from apps.core.prompt_registry import PromptRegistry

prompt = PromptRegistry.get("extraction.invoice_system")
prompt = PromptRegistry.get("agent.exception_analysis", mode_context="3-WAY ...")
```

**18 managed prompts** total -- 2 extraction + 8 agent + 8 overlay/country prompts. Sync to Langfuse:

```bash
python manage.py push_prompts_to_langfuse            # push all to Langfuse (production label)
python manage.py push_prompts_to_langfuse --slug extraction.invoice_system
python manage.py push_prompts_to_langfuse --label staging   # staging label for testing
python manage.py push_prompts_to_langfuse --purge    # delete all then re-seed (fixes misnamed prompts)

python manage.py seed_prompts          # create DB PromptTemplate records for missing slugs
python manage.py seed_prompts --force  # overwrite existing with hardcoded defaults
```

The Langfuse version is **source of truth in production**. If no Langfuse key is set, falls through to DB then hardcoded default automatically.

---

### 10.2 Schema-driven path -- `PromptBuilderService` v2.0

**File**: `apps/extraction_core/services/prompt_builder_service.py`
**Used by**: `ExtractionPipeline._build_prompt()` (step 3), `LLMExtractionAdapter`

Generates fully dynamic prompts from `ExtractionSchemaDefinition` + `TaxJurisdictionProfile`. Zero hardcoded country-specific text -- everything is derived from schema field definitions and jurisdiction config.

**Version constants**: `PROMPT_VERSION = "2.0"`, `PROMPT_CODE = "extraction_core_v2"`

#### Public API

```python
from apps.extraction_core.services.prompt_builder_service import PromptBuilderService

payload = PromptBuilderService.build(
    country_code="IN",
    regime_code="GST",
    document_type="invoice",
    schema=schema_definition,              # ExtractionSchemaDefinition instance
    jurisdiction_profile=jurisdiction,     # TaxJurisdictionProfile -- optional
    field_definitions=field_defs,          # list[ExtractionFieldDefinition] -- optional
    unresolved_field_keys={"invoice_number", "vendor_name"},  # hybrid mode -- optional
)
# Returns dict:
# {
#     "prompt_code": "extraction_core_v2",
#     "prompt_version": "2.0",
#     "system_message": "<7-section assembled prompt>",
#     "user_message_template": "<template with OCR placeholder>",
#     "expected_schema": {...},   # dynamic JSON schema from schema definition
#     "field_count": 14,          # number of fields requested (reduced in hybrid mode)
# }

user_message = PromptBuilderService.build_user_message(ocr_text)
# Wraps ocr_text[:60000] in a standard extraction request envelope
```

#### 7-section prompt assembly

The system message is assembled from these sections in order; empty sections are silently dropped:

| # | Section method | What it contains |
|---|---|---|
| 1 | `_global_instructions()` | Extraction rules: JSON-only output, null policy, value vs confidence vs evidence envelope, monetary/date formatting, no markdown |
| 2 | `_country_regime_instructions()` | Country code, tax regime label, tax ID label, expected currency, date formats, `extraction_notes` from `jurisdiction_profile.config_json` |
| 3 | `_document_type_instructions()` | Document type label and per-document guidance |
| 4 | `_schema_fields_section()` | Header fields, tax fields, line-item fields from `ExtractionSchemaDefinition`; each annotated with display name, data type, and `[REQUIRED]` flag |
| 5 | `_tax_instructions()` | Regime-specific notes, `tax_id_regex` from jurisdiction profile, list of tax-flagged field keys with display names |
| 6 | `_evidence_confidence_rules()` | Confidence band definitions: 1.0 (unambiguous) / 0.7-0.9 (inference) / 0.3-0.6 (ambiguous) / 0.0 + null (not found); verbatim snippet requirement |
| 7 | `_output_format_section()` | Expected JSON schema rendered inline so the LLM knows the exact output shape |

#### Hybrid mode (`unresolved_field_keys`)

When `unresolved_field_keys` is provided, sections 4 and 7 include only those fields. Used when deterministic extraction already resolved some fields -- the LLM call is scoped to the remainder, reducing token usage and improving focus:

```python
payload = PromptBuilderService.build(
    ...,
    unresolved_field_keys={"invoice_number", "po_number"},
)
# payload["field_count"] == 2
# payload["expected_schema"] contains only those 2 fields
```

#### Schema-driven expected output

`build_expected_schema()` reads `ExtractionSchemaDefinition.header_fields_json`, `tax_fields_json`, and `line_item_fields_json` to produce a dynamic JSON schema. This schema is:
- Embedded in section 7 of the system prompt so the LLM sees the exact required shape
- Used downstream by `LLMExtractionAdapter` to validate and parse the LLM response
- Stored on `ExtractionRun` via `prompt_code` / `prompt_version` for audit traceability

Each extracted field uses this per-field envelope (schema-driven path):

```json
{
  "header_fields": {
    "invoice_number": { "value": "INV-2024-001", "confidence": 0.97, "evidence": "Invoice No. INV-2024-001" },
    "vendor_name":    { "value": "Acme Pvt Ltd",  "confidence": 1.0,  "evidence": "ACME PRIVATE LIMITED" }
  },
  "tax_fields": {
    "tax_amount": { "value": "1800.00", "confidence": 0.95, "evidence": "GST 18% = 1800.00" },
    "cgst":       { "value": "900.00",  "confidence": 0.95, "evidence": "CGST @ 9% = 900.00" }
  },
  "line_items": [
    {
      "item_description": { "value": "Cloud Hosting", "confidence": 1.0,  "evidence": "Cloud Hosting Services" },
      "quantity":         { "value": "1",             "confidence": 0.90, "evidence": "Qty: 1" },
      "unit_price":       { "value": "10000.00",      "confidence": 0.90, "evidence": "Rate: 10000" }
    }
  ]
}
```

Compare with the **monolithic path** flat output (used by `InvoiceExtractionAgent`):

```json
{
  "confidence": 0.94,
  "vendor_name": "Acme Pvt Ltd",
  "invoice_number": "INV-2024-001",
  "total_amount": 11800,
  "line_items": [{ "item_description": "Cloud Hosting", "quantity": 1 }]
}
```

---

### 10.3 `LLMExtractionAdapter`

**File**: `apps/extraction_core/services/llm_extraction_adapter.py`

Wraps `LLMClient` to drive a schema-driven extraction call. Uses `prompt_builder.py` (the earlier `PromptBuilderService` variant that works with `ExtractionTemplate` / `FieldSpec` objects) to build messages, then invokes the LLM with retry on JSON parse failures.

```python
adapter = LLMExtractionAdapter()
results, audit = adapter.extract_fields(
    template=template,           # ExtractionTemplate (header/tax/line-item FieldSpec lists)
    ocr_text=ocr_text,
    jurisdiction_profile=jurisdiction,
    unresolved_field_keys=None,  # None = extract all; set[str] = hybrid mode
)
# results : list[FieldResult]       (value, confidence, evidence, method per field)
# audit   : LLMExtractionAudit      (see fields below)
```

**`LLMExtractionAudit` fields:**

| Field | Type | Description |
|---|---|---|
| `model` | `str` | Deployment name used for the call |
| `prompt_tokens` | `int` | Tokens in the request |
| `completion_tokens` | `int` | Tokens in the response |
| `total_tokens` | `int` | Combined token count |
| `duration_ms` | `int` | Wall-clock latency |
| `attempts` | `int` | LLM calls made (1 + retries on parse failure) |
| `success` | `bool` | True if at least one attempt returned parseable JSON |
| `error_message` | `str` | Last error string if all attempts failed |
| `fields_extracted` | `int` | Number of non-null field results returned |

---

### 10.4 `PromptRegistryService` -- `ExtractionPromptTemplate` lifecycle

**File**: `apps/extraction_core/services/prompt_registry_service.py`
**Model**: `ExtractionPromptTemplate` (in `extraction_core` app, separate from `core.PromptTemplate`)

Manages versioned prompt templates scoped to the extraction schema system.

```python
from apps.extraction_core.services.prompt_registry_service import PromptRegistryService

# List with filters
qs = PromptRegistryService.list_prompts({
    "prompt_code": "extraction_core_v2",
    "country_code": "IN",
    "regime_code": "GST",
    "status": "ACTIVE",
})

# Create
prompt = PromptRegistryService.create_prompt({
    "prompt_code": "extraction_core_v2",
    "prompt_category": "extraction",
    "country_code": "IN",
    "regime_code": "GST",
    "document_type": "invoice",
    "schema_code": "invoice_v1",
    "prompt_text": "...",
    "variables_json": [],
    "effective_from": date.today(),
}, user=request.user)

# Update (diff stored for audit)
PromptRegistryService.update_prompt(prompt.pk, {"prompt_text": "..."}, user=request.user)
```

Filterable fields: `prompt_code`, `prompt_category`, `country_code`, `regime_code`, `document_type`, `schema_code`, `status`, `search`. `update_prompt()` computes and stores a diff of `prompt_text` changes for auditability.

---

### 10.5 How `ExtractionPipeline` wires the prompt layer

`ExtractionPipeline` is the new primary entry point for governed extraction. Step 3 of 12 calls `PromptBuilderService.build()` and persists the prompt identity back to `ExtractionRun`:

```python
# ExtractionPipeline._build_prompt()  (step 3)
prompt_payload = PromptBuilderService.build(
    country_code=resolution.country_code,
    regime_code=resolution.regime_code or "",
    document_type=document_type,
    schema=schema,                           # selected by SchemaRegistryService (step 2)
    jurisdiction_profile=resolution.jurisdiction,
)
run.prompt_code = prompt_payload["prompt_code"]       # "extraction_core_v2"
run.prompt_version = prompt_payload["prompt_version"] # "2.0"
run.save(update_fields=["prompt_code", "prompt_version", "updated_at"])
ExtractionAuditService.log_prompt_selected(...)       # audit event emitted
```

The assembled `system_message` is passed to `LLMClient.chat()` as the system turn. `build_user_message(ocr_text)` provides the user turn (OCR text wrapped in a standard envelope, truncated at 60 000 characters).

`InvoiceExtractionAgent` is the outer orchestrator used by all Celery tasks. It uses `InvoicePromptComposer` overlays for its own primary LLM call, then invokes `ExtractionPipeline.run()` at step 1b as a fail-silent enrichment step that produces the governance records described above. The agent is the outer pipeline; `ExtractionPipeline` is called from inside it, not the other way around.

---

### 10.6 Key extraction rules (monolithic path)

Embedded in `extraction.invoice_system` and applied by `InvoiceExtractionAgent`:

| Rule | Description |
|------|-------------|
| **Pre-extraction analysis** | Mandatory step: identify document type, table vs pricing breakdown structure, tax regime, quantity logic |
| **Label binding** | Values bound to nearest explicit label; no identifier guessing by format alone |
| **Header block recovery** | When label and value are on separate OCR lines, search the nearby header section only |
| **invoice_number sources** | Only from: Invoice Number, Invoice No, Tax Invoice No, Bill No |
| **Reference exclusions** | CART Ref. No., Client Code, IRN, Document No., Booking Confirmation No., Hotel Booking ID, Requisition Number, Passenger Name, Employee Code, Cost Center Code -- **never** used as invoice_number |
| **po_number** | Only when explicitly labeled (PO Number / P.O. No / Purchase Order), else `""` |
| **vendor_name** | English characters only; transliterate/translate if OCR contains Arabic/Urdu/non-English |
| **vendor_tax_id** | GSTIN or VAT registration number of the vendor (not buyer) |
| **buyer_name** | Entity under "Bill To" |
| **due_date** | Extract if present, else `""` |
| **tax_breakdown** | Map CGST->cgst, SGST->sgst, IGST->igst, VAT->vat; default 0 if missing |
| **document_type** | Always `"invoice"` |
| **item_category** | One of: Food, Logistics, Packaging, Maintenance, Utilities, Equipment, Services, Materials, Other |
| **subtotal** | Sum of ALL pre-tax components (base fare, service charges, fees); exclude GST/VAT, roundoff, total |
| **tax_percentage** | Computed: `(tax_amount / subtotal) x 100`; NOT copied from component-level rate |
| **Travel invoice** | Convert pricing breakdown (Base Fare, Service Charges) into line items; consolidate Basic Fare + Hotel Taxes -> Total Fare |
| **Consistency** | `subtotal + tax_amount ~= total_amount` (+-2%); `sum(line_amounts) ~= subtotal` (+-5%); prefer computed if mismatch |
| **Defaults** | Missing text -> `""`; missing numbers -> `0` |

---

## 10a. Invoice Category Classifier

**File**: `apps/extraction_core/services/invoice_category_classifier.py`

Classifies invoice OCR text into one of three categories **before** LLM extraction so the prompt can be tailored:

| Category | Key signals |
|---|---|
| `travel` | hotel, itinerary, passenger name, CART Ref, PNR, booking ID, room rate, fare |
| `goods`  | HSN code, qty/pcs/unit, rate per unit, SKU, batch no, e-way bill |
| `service` | professional fees, SAC, consulting, subscription, maintenance contract, management fee |

**Result dataclass**: `InvoiceCategoryResult`

| Field | Type | Description |
|---|---|---|
| `category` | `str` | `goods` / `service` / `travel` |
| `confidence` | `float` | 0.0–1.0 |
| `signals` | `list[str]` | Matched keyword evidence (max 10) |
| `is_ambiguous` | `bool` | True when top-2 score gap < 0.20 |

**Fallback**: Defaults to `service` when input is empty or confidence < 0.20.

---

## 10b. Modular Prompt Composition

**File**: `apps/extraction/services/invoice_prompt_composer.py`
**Registry**: `apps/core/prompt_registry.py`

### Why prompt overlays instead of multiple agents

A single `InvoiceExtractionAgent` is retained because:
- The extraction schema (JSON output shape) is identical for all invoice types
- Category-specific guidance is additive — overlays append targeted rules to the base
- Fewer agents = simpler failure modes, unified tracing, one Langfuse config

### Registry keys

| Key | Purpose |
|---|---|
| `extraction.invoice_base` | Base extraction prompt (versioned independently of monolithic fallback) |
| `extraction.invoice_system` | Monolithic fallback (unchanged — backward compatible) |
| `extraction.invoice_category_goods` | Goods-specific extraction rules overlay |
| `extraction.invoice_category_service` | Service-specific extraction rules overlay |
| `extraction.invoice_category_travel` | Travel-specific rules (invoice# exclusions, subtotal, line structure) |
| `extraction.country_india_gst` | India GST rules (GSTIN, IRN, CGST/SGST/IGST) |
| `extraction.country_generic_vat` | Generic VAT rules |

All keys are Langfuse-overridable via the normal PromptRegistry resolution chain (Langfuse → DB → hardcoded default).

### Composition result: `PromptComposition`

| Field | Type | Description |
|---|---|---|
| `final_prompt` | `str` | Assembled system prompt sent to the LLM |
| `components` | `dict[str, str]` | `{slug: version}` for each part used |
| `prompt_hash` | `str` | sha256 hex (16 chars) of `final_prompt` — deterministic across runs |

### Backward compatibility / fallback

1. If `extraction.invoice_base` is absent → uses `extraction.invoice_system`
2. If category overlay is absent or empty → skipped (base prompt only)
3. If country overlay is absent → skipped
4. If `InvoicePromptComposer` raises → `InvoiceExtractionAgent` uses its own `system_prompt` property (existing behaviour)

### Langfuse metadata logged per extraction

```
invoice_category, invoice_category_confidence,
base_prompt_key, base_prompt_version,
category_prompt_key, category_prompt_version,
country_prompt_key, country_prompt_version,
prompt_hash, schema_code
```

---

## 10c. Response Repair / Validator

**File**: `apps/extraction/services/response_repair_service.py`

### Why deterministic repair before parsing

The parser (`ExtractionParserService`) is a pure JSON→dataclass mapper. Placing repair upstream means:
- The parser, normalizer, validator, and confidence scorer all receive cleaner data
- Every repair is explicitly recorded in `repair_actions` — auditable
- No silent value invention — repairs only fire when OCR evidence exists

### Phase 1 rules

| Rule | Trigger | Action |
|---|---|---|
| **a. Invoice number exclusion** | `invoice_number` matches CART Ref, Client Code, IRN, Booking ID, Document No., etc. | Attempt OCR recovery for a real invoice-labelled number; clear to `""` if not found |
| **b. Tax % recomputation** | LLM tax_percentage differs >0.5pp from `tax_amount/subtotal×100` | Recompute from amounts |
| **c. Subtotal alignment** | `subtotal` differs >1 unit from sum of pre-tax line amounts (GST/VAT lines excluded) | Align subtotal to line sum |
| **d. Line tax allocation** | Travel/service invoice; single service-charge line; all tax on base/hotel line | Move tax to service-charge line, zero base line tax |
| **e. Travel consolidation** | Basic Fare + Hotel Taxes + Total Fare lines exist; Total Fare ≈ Basic + Taxes | Remove sub-lines, keep Total Fare line |

### Result dataclass: `RepairResult`

| Field | Description |
|---|---|
| `repaired_json` | Modified (or original) JSON dict |
| `repair_actions` | List of human-readable action strings |
| `warnings` | Non-fatal issues (e.g., could not recover invoice number) |
| `was_repaired` | `True` if any action was applied |

### Persistence

Repair metadata is embedded in `ExtractionResult.raw_response` under the `_repair` key (no migration needed):

```json
{
  "vendor_name": "...",
  "invoice_number": "...",
  "_repair": {
    "was_repaired": true,
    "repair_actions": ["invoice_number: replaced CART-9876 with INV-001"],
    "warnings": []
  }
}
```

The parser ignores `_repair` naturally (it only reads known field names).

---

## 11. Template Views & URLs

### URL Routing

**File**: `apps/extraction/urls.py` — all routes are under `/extraction/`

| URL Pattern | View | Method | Permission | Description |
|-------------|------|--------|------------|-------------|
| `/extraction/` | `extraction_workbench` | GET | `invoices.view` | Main workbench with KPIs + approval tab |
| `/extraction/upload/` | `extraction_upload` | POST | `invoices.create` | Upload + extract |
| `/extraction/filter/` | `extraction_ajax_filter` | GET | `invoices.view` | AJAX filter results |
| `/extraction/export/` | `extraction_export_csv` | GET | `invoices.view` | CSV export |
| `/extraction/result/<id>/` | `extraction_result_detail` | GET | `invoices.view` | Result detail view |
| `/extraction/result/<id>/json/` | `extraction_result_json` | GET | `invoices.view` | Download raw JSON |
| `/extraction/result/<id>/rerun/` | `extraction_rerun` | POST | `extraction.reprocess` | Re-run extraction |
| `/extraction/result/<id>/edit/` | `extraction_edit_values` | POST | `extraction.correct` | Edit extracted values |
| `/extraction/approvals/` | `extraction_approval_queue` | GET | `invoices.view` | Redirects to workbench?tab=approvals |
| `/extraction/approvals/<id>/` | `extraction_approval_detail` | GET | `invoices.view` | Approval detail/review |
| `/extraction/approvals/<id>/approve/` | `extraction_approve` | POST | `extraction.approve` | Approve extraction |
| `/extraction/approvals/<id>/reject/` | `extraction_reject` | POST | `extraction.reject` | Reject extraction |
| `/extraction/console/<id>/` | `extraction_console` | GET | `invoices.view` | Agentic review console |
| `/extraction/approvals/analytics/` | `extraction_approval_analytics` | GET | `invoices.view` | Analytics JSON endpoint |
| `/extraction/country-packs/` | `country_pack_list` | GET | `extraction.view` | Country pack governance |

**API URLs**: `apps/extraction/api_urls.py` — empty (no REST API endpoints; all APIs live in `extraction_core`).

### Observability

All 15 template views are decorated with:
- `@login_required` — enforced by `LoginRequiredMiddleware`
- `@permission_required_code("<permission>")` — RBAC permission check
- `@observed_action("<action_name>")` — creates trace span, captures actor identity, role snapshot, permission checked; writes `AuditEvent`

### Data Scoping (AP_PROCESSOR)

AP_PROCESSOR users see only extractions linked to their own uploaded invoices. The `_scope_extractions_for_user(queryset, user)` helper filters by `document_upload__uploaded_by=user` when the user's primary role is `AP_PROCESSOR`. This scoping is applied to:
- Workbench queryset (extraction results list)
- KPI statistics (counts and averages)
- AJAX filter endpoint (filtered results)

### Cross-Module Enrichment (extraction_core integration)

Several template views enrich their context with data from `extraction_core` models:

- **`extraction_workbench`**: Pre-loads `ExtractionRun.review_queue` for each result (bulk query via `document__document_upload_id` mapping). Displays review queue as badge in results table.
- **`extraction_console`**: Loads `ExtractionRun` by `document_upload_id` to enrich context with `review_queue`, `schema_code`, `schema_version`, `extraction_method`, `requires_review`. Loads `ExtractionCorrection` records for corrections tab.
- **`country_pack_list`**: Queries `CountryPack.objects.select_related("jurisdiction")` to display governance table.

All cross-module lookups are wrapped in `try/except` for graceful degradation if extraction_core data isn't populated.

### View Details

**`extraction_workbench`** — Main extraction agent page with three tabs:
- **Agent Runs tab**: KPI stats (total, success, failed, avg confidence, avg duration); advanced filters (search, status, confidence range, date range, review queue); paginated results table (20 per page) with review queue column; "Run Agent" file upload modal (PDF, PNG, JPG, TIFF — max 20 MB)
- **Approvals tab**: Approval queue with filter/search + analytics strip
- **Rejected tab**: Failed/rejected uploads (`DocumentUpload.processing_state=FAILED`). Table with columns: ID, Filename, Rejection Reason, Detected Doc Type, Uploaded timestamp, Uploaded By. Paginated with count badge. Visible when document type classification rejects non-invoice uploads (GRN, PO, etc.).

**`extraction_upload`** — File upload handler:
- Validates file type and size (20 MB max)
- Computes SHA-256 hash
- Creates `DocumentUpload` record
- Runs extraction pipeline (standalone mode — no case creation)
- Optional Azure Blob Storage upload

**`extraction_result_detail`** — Detailed extraction result:
- Engine metadata (name, version, duration, confidence)
- Raw vs normalized invoice data side-by-side
- Validation issues (errors + warnings)
- Line items table with service/stock item badges
- Action buttons: Edit Values, Download JSON, Re-extract, View Full Invoice

**`extraction_edit_values`** — Inline value editing:
- Accepts JSON payload with `header` and `lines` corrections
- Header fields: `invoice_number`, `po_number`, `invoice_date`, `due_date`, `currency`, `subtotal`, `tax_amount`, `total_amount`, `raw_vendor_name`, `vendor_tax_id`, `buyer_name`, `tax_percentage`
- Line fields: `description`, `quantity`, `unit_price`, `tax_amount`, `line_amount`, `tax_percentage`
- Returns changed fields list and count
- Audits changes as `EXTRACTION_COMPLETED` event

**`extraction_approval_queue`** — Backward-compatible redirect to `workbench?tab=approvals`. Forwards query params.

**`extraction_approval_detail`** — Review and approve/reject:
- Confidence and metadata cards
- Validation issues alert
- Editable header fields and line items (read-only if already reviewed)
- Previous corrections history table
- Approve/Reject buttons with AJAX handlers

**`extraction_export_csv`** — CSV export with columns: ID, Filename, Invoice #, Vendor, Currency, Subtotal, Tax, Total, PO, Confidence %, Status, Duration, Engine, Extracted At.

**`extraction_console`** — Agentic deep-dive review console:
- Full context build: header fields, tax fields, parties, enrichment, line items, validation re-run
- Pipeline stages with state tracking (10 stages)
- Approval record lookup
- ExtractionRun enrichment (review queue, schema, method badges in header bar)
- Corrections tab with ExtractionCorrection audit trail
- Permission context (can_approve, can_reprocess, can_escalate)
- Assignable users for escalation
- See [Section 13: Extraction Review Console](#13-extraction-review-console) for full template/layout details.

**`country_pack_list`** — Country pack governance page:
- KPI strip: total, active, draft, deprecated counts
- Governance table: country, regime, status (color-coded badges), schema/validation/normalization versions, activated date, notes
- Gated by `extraction.view` permission

---

## 12. Templates (UI)

All templates are in `templates/extraction/` and extend `base.html` (Bootstrap 5). Total: 19 template files.

### Top-Level Templates

| File | Purpose |
|------|---------|
| `workbench.html` | Main workbench with **3 tabs**: Agent Runs (KPIs, filters, results with review queue column) + Approvals + **Rejected** (failed uploads with rejection reason, doc type, timestamp) |
| `result_detail.html` | Single extraction result detail |
| `approval_detail.html` | Approval review page (approve/reject modals) |
| `approval_queue.html` | Deprecated — redirects to workbench |
| `country_packs.html` | Country pack governance (KPI strip + governance table with status badges) |

### workbench.html
- Three-tab layout: **Agent Runs**, **Approvals**, and **Rejected**
- Agent Runs: KPI stat cards (total, success, failed, avg confidence, avg duration); advanced filter panel (search, status, confidence presets/slider, date range, review queue dropdown); results table with review queue column; "Run Agent" modal for file upload (drag-and-drop, file validation)
- Approvals: Approval queue with filter/search + analytics strip
- Rejected: Failed uploads table (ID, Filename, Reason, Doc Type, Uploaded, By) with pagination + count badge. Shows uploads rejected by document type classification gate.

### result_detail.html
- Engine metadata panel (name, version, duration, file info)
- Error message display (if extraction failed)
- Raw vs Normalized comparison table
- Invoice header + line items display
- Validation issues list
- Action buttons: Edit Values, Download JSON, Re-extract, View Full Invoice

### approval_detail.html
- Confidence card with percentage + status badge
- Invoice metadata card (vendor, amount, PO, date)
- Validation issues alert banner
- Editable header fields form (text inputs for each correctable field)
- Editable line items table (inline editing)
- Previous corrections table (showing original → corrected values)
- Reject modal with reason textarea
- JavaScript handlers for Approve (AJAX POST) and Reject (modal + AJAX POST)

### country_packs.html
- Breadcrumb navigation
- KPI strip: Total Packs, Active, Draft, Deprecated (with color-coded badges)
- Governance table: Country, Regime, Status (ACTIVE=green, DRAFT=amber, DEPRECATED=red), Schema Version, Validation Version, Normalization Version, Activated date, Notes
- Empty state message when no packs exist

---

## 13. Extraction Review Console

### Overview

The Extraction Review Console is an enterprise-grade, agentic deep-dive UI for reviewing individual extraction results. It provides document viewing, 5-tab intelligence panels, approval workflow modals, and a pipeline timeline — all in a single-page Bootstrap 5 layout.

**Route**: `/extraction/console/<id>/` → `extraction_console` view  
**Template**: `templates/extraction/console/console.html`  
**Static**: `static/css/extraction_console.css`, `static/js/extraction_console.js`

### Layout Structure

```
┌──────────────────────────────────────────────────────────────┐
│  HEADER BAR — ID, file, status, confidence, jurisdiction,    │
│               review queue badge, schema badge,              │
│               extraction method badge,                       │
│               action buttons (Approve, Edit, Reprocess,      │
│               Escalate, Comment)                             │
├──────────────────────────────────────────────────────────────┤
│  INTELLIGENCE PANEL (6 tabs, full-width col-12)              │
│                                                              │
│  Tab 1: Extracted Data                                       │
│    - Header Fields table (vendor_name, invoice_number,       │
│      invoice_date, due_date, po_number, vendor_tax_id,       │
│      buyer_name, currency, subtotal, tax_amount,             │
│      tax_percentage, total_amount)                           │
│    - Parties card (exc-supplementary-card)                   │
│    - Tax & Jurisdiction card                                 │
│    - Tax Breakdown card (CGST/SGST/IGST/VAT components;      │
│      only rendered when invoice_tax_breakdown is non-empty)  │
│    - Master Data Matches card (exc-supplementary-card)       │
│    - Line Items table (expandable; Tax % column shown when   │
│      has_line_tax_pct is True)                               │
│                                                              │
│  Tab 2: Validation                                           │
│    - Errors / Warnings / Passed                              │
│    - Go-to-field navigation                                  │
│                                                              │
│  Tab 3: Evidence                                             │
│    - Field evidence cards                                    │
│    - Source snippets, page refs                              │
│                                                              │
│  Tab 4: Agent Reasoning                                      │
│    - Step-by-step reasoning timeline                         │
│    - Decisions, collapsible details                          │
│                                                              │
│  Tab 5: Audit Trail                                          │
│    - Chronological event timeline                            │
│    - Actor/role badges                                       │
│    - Before/after change tracking                            │
│                                                              │
│  Tab 6: Corrections                                          │
│    - Field correction audit trail table                      │
│    - Original → Corrected values with reasons                │
│    - Corrected-by user + timestamp                           │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  PIPELINE TIMELINE — Upload → OCR → Jurisdiction → Schema →  │
│  Extraction → Normalize → Validate → Enrich → Confidence →   │
│  Review (state-aware pills)                                  │
└──────────────────────────────────────────────────────────────┘
```

> **Note**: The document viewer column was removed. The console uses a single-column, full-width layout for the intelligence panel. The `_document_viewer.html` template is no longer included.

### Template Files (16 files in `templates/extraction/console/`)

| File | Purpose |
|------|---------|
| `console.html` | Main layout — extends `base.html`, includes all partials/modals, loads CSS/JS. 6 tab pills. |
| `_header_bar.html` | Command bar — extraction ID, status/confidence badges (uses `{% widthratio %}` for 0–1 → percentage conversion), jurisdiction badges, review queue badge (bg-info-subtle), schema badge (bg-dark-subtle), extraction method badge (conditional: HYBRID=purple, LLM=primary, else=secondary), action buttons |
| `_document_viewer.html` | **Deprecated** — no longer included in layout. File exists but is unused. |
| `_extracted_data.html` | Tab 1 — Header Fields, Parties, Tax/Jurisdiction, Tax Breakdown (CGST/SGST/IGST/VAT; shown only when non-zero), Master Data Matches, Line Items with **summary footer** (summed Qty, Tax Amount, Total across all line items) and optional Tax % column |
| `_confidence_badge.html` | Reusable confidence % indicator (green ≥85%, amber ≥50%, red <50%). Uses `{% widthratio confidence 1 100 %}` to convert 0–1 float to percentage. |
| `_validation_panel.html` | Tab 2 — Errors/Warnings/Passed grouped by severity, "Go to field" navigation |
| `_evidence_panel.html` | Tab 3 — Evidence cards with source snippets and page references |
| `_reasoning_panel.html` | Tab 4 — Agent reasoning timeline with step indicators, decisions, collapsible details |
| `_audit_trail.html` | Tab 5 — Chronological event timeline with actor/role badges, before/after tracking |
| `_corrections_panel.html` | Tab 6 — Field correction audit trail table (columns: Field Code, Original Value (strikethrough), Corrected Value (green), Reason, Corrected By, Date). Empty state with guidance text. |
| `_cost_tokens_panel.html` | Cost & Tokens — 5 KPI cards (Total/LLM/OCR cost, tokens, OCR pages), cost breakdown (LLM vs OCR), token breakdown bar, execution details table |
| `_bottom_timeline.html` | Pipeline stage progress bar with state indicators (completed/active/error/skipped/pending) |
| `_approve_modal.html` | Approval modal — warnings summary, notes, review confirmation checkbox |
| `_reprocess_modal.html` | Reprocess modal — reason select, override options (force LLM, override jurisdiction) |
| `_escalate_modal.html` | Escalation modal — severity, assignee select, flagged fields list |
| `_comment_modal.html` | Comment modal — text, related fields, internal toggle |

### Key Features

**Field Filtering**: Toggle buttons for All Fields / Flagged Only / Low Confidence to focus review on problem areas. "Flagged Only" shows rows with the `exc-flagged` class (fields with validation issues). "Low Confidence" shows rows with `exc-low-confidence` or `exc-med-confidence` classes. Supplementary cards (Parties, Master Data Matches) are hidden when a filter other than "All" is active. An empty state message is displayed when no rows match the selected filter.

**Edit Mode**: Toggle switch enables inline editing on all header and tax fields. Modified fields get visual highlighting (`exc-modified` class). Original values preserved in `data-original` for comparison.

**Go-to-Field Navigation**: Validation issues and evidence cards have clickable field links that switch to the Extracted Data tab and scroll/highlight the target field row.

**Line Item Expand/Flag**: Each line item row has expand (shows all field details) and flag (marks for review) actions.

**Modal Workflows**: All approval actions go through Bootstrap modals with CSRF-protected AJAX POST requests. Toast notifications for success/error feedback.

**Permission-Aware Actions**: Action buttons (Approve, Reprocess, Escalate) are conditionally rendered based on the user's RBAC permissions: `extraction.approve` for approval, `extraction.reprocess` for re-extraction, `cases.escalate` for escalation. Checked via `user.has_permission()` (custom RBAC, not Django's `has_perm()`).

### Static Assets

**`static/css/extraction_console.css`** (~200 lines):
- `.exc-conf-high/med/low` confidence badge colors
- `.exc-field-table` compact field table styling
- `.exc-field-row.exc-low-confidence` / `.exc-med-confidence` left-border indicators
- `.exc-field-row.exc-flagged` left-border indicator for validation-issue fields
- `.exc-field-row.exc-editing` edit mode show/hide
- `.exc-source-snippet` evidence source styling
- `.exc-reasoning-step-number` numbered step circles with connectors
- `.exc-audit-dot-*` timeline dot colors per event type
- `.exc-stage-*` pipeline pill state colors
- `.exc-pipeline-timeline` horizontal scrollable timeline
- `.exc-filter-empty` empty state styling for filter results
- `.exc-supplementary-card` styling for Parties / Enrichment cards
- Responsive breakpoints (≤991px: reduced heights)

**`static/js/extraction_console.js`** (~200 lines):
- Tab persistence (sessionStorage)
- Field filter toggles (all/flagged/low-confidence) with supplementary card visibility
- Filter empty state toggle
- Edit mode toggle with modification tracking
- Go-to-field navigation (cross-tab + scroll + highlight animation)
- Evidence field filter dropdown
- Line item expand/collapse and flag toggle
- AJAX modal submission (approve/reprocess/escalate/comment) with CSRF
- Toast notification system

### View Context

The `extraction_console` view builds the following context for the template:

| Context Variable | Source | Description |
|-----------------|--------|-------------|
| `extraction` | Computed dict | ID, file_name, status, confidence, created_at, resolved_jurisdiction, jurisdiction_source, jurisdiction_confidence, jurisdiction_warning, review_queue, schema_code, schema_version, extraction_method, requires_review |
| `ext` | `ExtractionResult` | Original extraction result record |
| `header_fields` | Invoice model | Dict of field dicts (display_name, value, raw_value, confidence, method, is_mandatory, evidence). Includes: `vendor_name`, `invoice_number`, `invoice_date`, `due_date`, `po_number`, `vendor_tax_id`, `buyer_name`, `currency`, `subtotal`, `tax_amount`, `tax_percentage`, `total_amount` |
| `tax_fields` | Invoice model | Tax-specific field dicts: `tax_amount`, `tax_percentage`, and individual tax breakdown rows (`cgst`, `sgst`, `igst`, `vat` — only non-zero components added) |
| `invoice_tax_breakdown` | `invoice.tax_breakdown` | Raw breakdown dict `{cgst, sgst, igst, vat}` used by the Tax Breakdown card |
| `has_line_tax_pct` | Computed bool | `True` when at least one line item has a non-null `tax_percentage` — controls Tax % column visibility in line items table |
| `parties` | `raw_response.document_intelligence.parties` | Supplier/buyer/ship-to/bill-to from document intelligence; falls back to `invoice.vendor_name` + `invoice.vendor_tax_id` for supplier, and `invoice.buyer_name` for buyer |
| `enrichment` | `raw_response.enrichment` | Vendor/customer/PO matches from master data enrichment |
| `line_items` | `InvoiceLineItem` queryset | List of dicts with description, qty, price, `tax_percentage`, tax, total, confidence, fields |
| `line_items_totals` | Computed | Dict with summed `quantity`, `tax_amount`, `total` across all line items — displayed in table footer |
| `errors` / `warnings` | Re-run `ValidationService` | Grouped validation issues |
| `validation_field_issues` | Computed | Map of field names with validation issues |
| `pipeline_stages` | Computed | 10-stage pipeline with state indicators |
| `approval` | `ExtractionApproval` | Current approval record (if exists) |
| `corrections` | `ExtractionCorrection` queryset | Field correction audit trail from `ExtractionRun` (select_related corrected_by) |
| `correction_count` | int | Count of corrections for badge display |
| `permissions` | Request user | `can_approve` (`extraction.approve`), `can_reprocess` (`extraction.reprocess`), `can_escalate` (`cases.escalate`) — checked via `user.has_permission()` |
| `assignable_users` | `User.objects` | Top 50 active users for escalation |

**ExtractionRun enrichment**: The view calls `get_execution_context(ext)` to populate governed execution metadata. The enriched `ExecutionContext` provides `review_queue`, `schema_code`, `schema_version`, `extraction_method`, `requires_review`, `extraction_run_id`, `country_code`, `regime_code`, `jurisdiction_source`, `overall_confidence`, `review_reasons`, `approval_action`, `approval_decided_at`, and `duration_ms`. These appear as badges and metadata in the header bar and pipeline timeline.

**Query optimization**: The workbench, AJAX filter, and CSV export querysets include `select_related("extraction_run")` to avoid N+1 queries when `get_execution_context()` accesses the FK.

---

## 14. Enums & Status Flows

### InvoiceStatus

```
UPLOADED → EXTRACTION_IN_PROGRESS → EXTRACTED → VALIDATED → PENDING_APPROVAL → READY_FOR_RECON → RECONCILED
                                  ↘ INVALID                ↗ (auto-approve)                    ↘ FAILED
                                                           ↘ INVALID (rejected)
```

| Value | Description |
|-------|-------------|
| `UPLOADED` | File uploaded, awaiting extraction |
| `EXTRACTION_IN_PROGRESS` | Extraction pipeline running |
| `EXTRACTED` | Raw extraction complete (no validation) |
| `VALIDATED` | Extraction passed validation |
| `INVALID` | Validation failed or extraction rejected |
| `PENDING_APPROVAL` | Awaiting human review in approval queue |
| `READY_FOR_RECON` | Approved — ready for reconciliation |
| `RECONCILED` | Reconciliation complete |
| `FAILED` | Pipeline failure |

### ExtractionApprovalStatus

| Value | Description |
|-------|-------------|
| `PENDING` | Awaiting human review |
| `APPROVED` | Human approved (with or without corrections) |
| `REJECTED` | Human rejected (invoice → INVALID) |
| `AUTO_APPROVED` | System auto-approved (high confidence, touchless) |

### FileProcessingState

| Value | Description |
|-------|-------------|
| `QUEUED` | Upload queued for processing |
| `PROCESSING` | Extraction in progress |
| `COMPLETED` | Extraction finished successfully |
| `FAILED` | Extraction failed |

### Extraction Audit Event Types

| Event Type | When Logged |
|------------|-------------|
| `EXTRACTION_STARTED` | Extraction adapter begins OCR + LLM pipeline |
| `EXTRACTION_COMPLETED` | Pipeline completes successfully |
| `EXTRACTION_FAILED` | Pipeline fails |
| `CREDIT_CHECKED` | Pre-flight credit balance/limit check |
| `CREDIT_RESERVED` | Credits reserved for in-progress extraction |
| `CREDIT_CONSUMED` | Credits consumed after successful extraction |
| `CREDIT_REFUNDED` | Credits refunded after extraction failure |
| `CREDIT_ALLOCATION_UPDATED` | Admin allocates or adjusts credits |
| `CREDIT_LIMIT_EXCEEDED` | Credit reservation rejected (insufficient balance or monthly limit) |
| `CREDIT_MONTHLY_RESET` | Monthly usage counter reset |
| `INVOICE_PERSISTED` | Invoice + line items saved to database |
| `EXTRACTION_RESULT_PERSISTED` | ExtractionResult record saved |
| `DUPLICATE_DETECTED` | Duplicate invoice detected during persistence |
| `VENDOR_RESOLVED` | Vendor matched via normalized name or alias during persistence |
| `EXTRACTION_APPROVAL_PENDING` | Approval record created (PENDING) |
| `EXTRACTION_APPROVED` | Human approves extraction |
| `EXTRACTION_AUTO_APPROVED` | System auto-approves extraction |
| `EXTRACTION_REJECTED` | Human rejects extraction |
| `EXTRACTION_FIELD_CORRECTED` | Field correction applied during approval |

### Extraction Platform Governance Event Types

| Event Type | When Logged | Category |
|------------|-------------|----------|
| `JURISDICTION_RESOLVED` | Jurisdiction resolved (tier + country + regime) | governance |
| `SCHEMA_SELECTED` | Schema selected for extraction | governance |
| `PROMPT_SELECTED` | Prompt template selected | governance |
| `NORMALIZATION_COMPLETED` | Country-specific normalization complete | telemetry |
| `VALIDATION_COMPLETED` | Country-specific validation complete | telemetry |
| `EVIDENCE_CAPTURED` | Field evidence captured | telemetry |
| `REVIEW_ROUTE_ASSIGNED` | Review queue assigned | governance |
| `EXTRACTION_REPROCESSED` | Extraction re-run triggered | business |
| `EXTRACTION_ESCALATED` | Extraction escalated to review queue | business |
| `EXTRACTION_COMMENT_ADDED` | Comment added to extraction | business |
| `SETTINGS_UPDATED` | Runtime settings or schema updated | governance |
| `SCHEMA_UPDATED` | Schema definition modified | governance |
| `PROMPT_UPDATED` | Prompt template modified | governance |
| `ROUTING_RULE_UPDATED` | Routing rule modified | governance |
| `ANALYTICS_SNAPSHOT_CREATED` | Analytics snapshot generated | telemetry |

### Event Category Taxonomy

All extraction audit events carry an `event_category` field in metadata (added by `ExtractionAuditService._base_metadata()`):

| Category | Purpose | UI Behavior |
|----------|---------|-------------|
| `business` | User-visible state changes (approve, reject, correct, reprocess, escalate, comment) | Always show in timelines |
| `governance` | Governed pipeline decisions (jurisdiction, schema, review routing, started/completed/failed) | Show in timelines |
| `telemetry` | Low-level pipeline steps (normalization, validation, evidence capture) | Collapse/filter in UI |

---

## 15. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_API_KEY` | `""` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | `""` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_VERSION` | `"2024-02-01"` | OpenAI API version |
| `AZURE_OPENAI_DEPLOYMENT` | `""` | Deployment name |
| `LLM_MODEL_NAME` | `"gpt-4o"` | Model name |
| `AZURE_DI_ENDPOINT` | `""` | Azure Document Intelligence endpoint |
| `AZURE_DI_KEY` | `""` | Azure Document Intelligence key |
| `AZURE_BLOB_CONNECTION_STRING` | `""` | Blob storage connection string |
| `AZURE_BLOB_CONTAINER_NAME` | `"finance-agents"` | Blob container name |
| `EXTRACTION_CONFIDENCE_THRESHOLD` | `0.75` | Confidence below this triggers validation warning |
| `EXTRACTION_AUTO_APPROVE_THRESHOLD` | `1.1` | Confidence threshold for auto-approval (1.1 = disabled) |
| `EXTRACTION_AUTO_APPROVE_ENABLED` | `"false"` | Master toggle for auto-approval |
| `EXTRACTION_OCR_ENABLED` | `"true"` | OCR toggle — `true` uses Azure DI, `false` uses native PDF text extraction (PyPDF2). Runtime override via `ExtractionRuntimeSettings.ocr_enabled`. |

### Settings File

All settings are in `config/settings.py`. Values are loaded from environment variables or `.env` file.

### OCR Mode Configuration

The OCR mode can be controlled at two levels:

1. **Runtime setting** (takes precedence): `ExtractionRuntimeSettings.ocr_enabled` — toggleable from the Extraction Control Center UI without app restart.
2. **Environment variable** (fallback): `EXTRACTION_OCR_ENABLED` — default `true`.

When OCR is disabled, the system uses PyPDF2 to extract the native text layer from PDFs. This is useful for:
- **Accuracy comparison**: Run the same invoice with OCR on vs off to measure LLM extraction quality difference.
- **Cost reduction testing**: Native extraction has zero Azure DI cost ($1.50/1,000 pages saved).
- **Speed testing**: Native extraction is near-instant vs Azure DI latency.

---

## 16. Permissions & RBAC

### Permission Codes

| Permission | Description |
|------------|-------------|
| `invoices.view` | View extraction results, approval queue, analytics |
| `invoices.create` | Upload files (upload only — edit uses `extraction.correct`) |
| `extraction.view` | View extraction platform data (country packs, schemas, settings) |
| `extraction.correct` | Correct/edit extracted field values (workbench + console UI + API) |
| `extraction.approve` | Approve extracted invoice data before reconciliation |
| `extraction.reject` | Reject extracted data and request re-extraction |
| `extraction.reprocess` | Re-run extraction on existing uploads |
| `extraction.escalate` | Escalate extraction to review queue (API) |
| `cases.escalate` | Escalate extraction for case-level review (console UI) |
| `credits.view` | View credit accounts and balances |
| `credits.manage` | Allocate, adjust, and manage user credit accounts |

### Role Access

| Role | Permissions |
|------|-------------|
| ADMIN | All extraction + credit permissions |
| AP_PROCESSOR | `invoices.view`, `invoices.create`, `extraction.correct`, `extraction.approve`, `extraction.reject`, `extraction.reprocess` (scoped to own uploads) |
| REVIEWER | `invoices.view` |
| FINANCE_MANAGER | `invoices.view`, `invoices.create`, `extraction.correct`, `extraction.approve`, `extraction.reject`, `extraction.reprocess`, `credits.view`, `credits.manage` |
| AUDITOR | `invoices.view` |
| SYSTEM_AGENT | `extraction.approve`, `extraction.reject` |

### Data Scoping

AP_PROCESSOR users are scoped to see only extractions linked to their own uploaded invoices. The `_scope_extractions_for_user()` helper in `template_views.py` filters by `document_upload__uploaded_by=user` when the user's primary role (via `UserRole` enum) is `AP_PROCESSOR`. This is applied to:
- Workbench queryset (paginated extraction results)
- KPI statistics (total, success, failed counts and averages)
- AJAX filter endpoint (filtered extraction results)

All other roles see all extractions.

### Permission Enforcement

- **View decorators**: `@permission_required_code("<permission>")` — checks against RBAC Permission model
- **Template checks**: `{% has_permission "extraction.approve" as can_approve %}` — uses RBAC template tags
- **Console permissions**: Checked via `user.has_permission("<code>")` (custom RBAC engine, **not** Django's `has_perm()`)
- **Separation of duties**: Approve and reject use dedicated `extraction.approve` / `extraction.reject` permissions, separate from `invoices.create` (upload/edit)

### Sidebar Navigation

The extraction section in the sidebar (`templates/partials/sidebar.html`) includes:
- **Invoice Extraction Agent** — links to the workbench (`/extraction/`), gated by `{% has_permission "invoices.view" %}`
- **Extraction Control Center** — links to the extraction core overview (`/extraction-control-center/`), gated by `{% has_permission "extraction.view" %}`
- **Credits** — links to credit account management (`/extraction/credits/`), gated by `{% has_permission "credits.manage" %}`, uses `bi-coin` icon. Located in the Admin Console sidebar section. Visible to ADMIN and FINANCE_MANAGER roles.

---

## 17. Credit System

### Overview

A per-user credit-based usage control system for invoice extraction. Every extraction consumes 1 credit. Credits are managed by ADMIN and FINANCE_MANAGER roles.

### Data Models

**UserCreditAccount** (`extraction_usercreditaccount`) — OneToOne per User:

| Field | Type | Description |
|-------|------|-------------|
| `user` | OneToOneField → User | Account owner |
| `balance_credits` | PositiveIntegerField | Available credit balance |
| `reserved_credits` | PositiveIntegerField | Credits reserved for in-progress extractions |
| `monthly_limit` | PositiveIntegerField | Monthly usage cap (0 = unlimited) |
| `monthly_used` | PositiveIntegerField | Credits used this month |
| `is_active` | BooleanField | Whether the account is active |
| `last_reset_at` | DateTimeField | Last monthly reset timestamp |

**Properties**: `available_credits` (balance − reserved), `has_available_credits()`, `can_consume_monthly()`

**CreditTransaction** (`extraction_credittransaction`) — Immutable ledger:

| Field | Type | Description |
|-------|------|-------------|
| `account` | FK → UserCreditAccount | Parent account |
| `transaction_type` | CharField | RESERVE, CONSUME, REFUND, ALLOCATE, ADJUST, MONTHLY_RESET |
| `credits` | IntegerField | Signed credit amount |
| `balance_after` | IntegerField | Snapshot of balance after transaction |
| `reserved_after` | IntegerField | Snapshot of reserved after transaction |
| `monthly_used_after` | IntegerField | Snapshot of monthly_used after transaction |
| `reference_type` | CharField | document_upload, admin, system |
| `reference_id` | CharField | Optional external reference |
| `remarks` | TextField | Mandatory for admin adjustments |
| `created_by` | FK → User | Who performed the action |

### Service: CreditService

**File**: `apps/extraction/services/credit_service.py`

| Method | Purpose | Creates Transaction | Audit Event |
|--------|---------|-------------------|-------------|
| `check_can_reserve(user)` | Pre-flight balance/limit check | No | `CREDIT_CHECKED` |
| `reserve(user, amount)` | Lock credits for upload | RESERVE | `CREDIT_RESERVED` |
| `consume(user, amount)` | Deduct after successful extraction | CONSUME | `CREDIT_CONSUMED` |
| `refund(user, amount)` | Return credits on failure | REFUND | `CREDIT_REFUNDED` |
| `allocate(user, amount)` | Admin add credits (amount > 0) | ALLOCATE | `CREDIT_ALLOCATION_UPDATED` |
| `adjust(user, amount)` | Admin correct (±amount) | ADJUST | `CREDIT_ALLOCATION_UPDATED` |
| `reset_monthly_if_due(account)` | Monthly usage reset | MONTHLY_RESET | `CREDIT_MONTHLY_RESET` |

### Upload Integration

The upload flow checks credits before allowing extraction:
```
User clicks Upload → check_can_reserve() → reserve(1, ref_type="document_upload", ref_id=upload.pk) → run extraction
  → Success: consume(1) — charged for successful extraction
  → OCR Failure: refund(1) — no charge for failed extraction
```

**Reprocess flow**: `extraction_rerun` also reserves 1 credit before re-extraction (`ref_type="reprocess"`, `ref_id=f"reprocess-{upload.pk}-{timestamp}"`). A unique timestamp-based `reference_id` is generated on every reprocess attempt so that the idempotency guard does not block subsequent reprocesses of the same upload. Blocked if the current approval is already finalized (`APPROVED`/`AUTO_APPROVED`).

The task receives `credit_ref_type` and `credit_ref_id` as explicit kwargs and threads them through all four consume/refund call sites (OCR failure refund, pipeline failure consume, success consume, persist failure consume). This ensures the correct unique reference is used regardless of which pipeline branch completes.

### Credit Decision Table — ChargePolicy

All charge/refund decisions are centralized in `ChargePolicy` (`apps/extraction/services/credit_service.py`). Each scenario maps to exactly one of **CONSUME**, **REFUND**, or **NOOP**.

| Scenario | ChargePolicy method | Outcome | reference_type | reference_id |
|----------|-------------------|---------|---------------|-------------|
| Successful extraction (invoice) | `for_extraction_success()` | CONSUME | `document_upload` | `upload.pk` |
| Non-invoice document (classified away) | `for_non_invoice_document()` | REFUND | `document_upload` | `upload.pk` |
| OCR failure (adapter returned error) | `for_ocr_failure()` | REFUND | `document_upload` | `upload.pk` |
| Parse / normalize / validate failure | `for_pipeline_failure()` | REFUND | `document_upload` | `upload.pk` |
| Duplicate invoice detected | `for_duplicate_invoice()` | CONSUME | `document_upload` | `upload.pk` |
| Unsupported jurisdiction / schema | `for_unsupported_jurisdiction()` | REFUND | `document_upload` | `upload.pk` |
| Manual reprocess (re-extraction) | `for_reprocess()` | CONSUME | `reprocess` | `f"reprocess-{upload.pk}-{timestamp}"` (unique per attempt) |
| Rejection after human review | `for_rejection_after_review()` | NOOP | — | — |

### Credit Pipeline Integration

The Celery task (`process_invoice_upload_task`) determines credit outcome by pipeline stage:

| Stage | Outcome | Credit Action | Rationale |
|-------|---------|---------------|-----------|
| Step 0 (reserve) | Insufficient balance | Block upload (no task dispatched) | User sees error, no credit spent |
| Step 1 (OCR) | OCR failure | **Refund** | No meaningful extraction occurred |
| Step 1 (OCR) | OCR success → pipeline continues | — (pending) | Wait for final outcome |
| Step 2–5 (parse/normalize/validate/dedup) | Pipeline error | **Consume** | OCR resources were used |
| Step 6 (persist) | Persistence failure | **Consume** | OCR + LLM resources were used |
| Step 6a | Extraction succeeded | **Consume** | Full pipeline completed |
| Retry | Celery retry triggered | **No-op** | Idempotency prevents duplicate transactions; same `reference_id` |
| Max retries exhausted | Final failure | Last stage outcome applies | If OCR never succeeded → refund; if OCR succeeded → consume |

> **Sync fallback path** (no blob storage): OCR success → consume, extraction failure → refund.

**Idempotency**: `reserve()`, `consume()`, and `refund()` check for existing transactions with the same `reference_type + reference_id` before creating duplicates. This ensures safe retries and Celery retry safety. For initial uploads the `reference_id` is `str(upload.pk)`; for reprocesses it is `f"reprocess-{upload.pk}-{timestamp}"` (unique per attempt) so each reprocess attempt is independently idempotent without blocking subsequent ones.

**Invariant enforcement**: All credit mutations validate `balance_credits >= 0`, `reserved_credits >= 0`, `monthly_used >= 0`, and `balance_credits >= reserved_credits`. Violations raise `CreditAccountingError`.

**Reason codes**: `INSUFFICIENT_BALANCE`, `INACTIVE_ACCOUNT`, `MONTHLY_LIMIT_EXCEEDED`, `OK` — defined as constants in `credit_service.py`.

The workbench UI shows a credit strip with current balance and blocks uploads when credits are insufficient.

### Views

| URL | View | Permission | Description |
|-----|------|------------|-------------|
| `/extraction/credits/` | `credit_account_list` | `credits.view` | All accounts with search/pagination |
| `/extraction/credits/<user_id>/` | `credit_account_detail` | `credits.view` | Account detail + transaction ledger (50 most recent) + adjustment form |
| `/extraction/credits/<user_id>/adjust/` | `credit_account_adjust` | `credits.manage` | POST: add, subtract, set_limit, toggle_active |

### Audit Trail

Every credit operation is recorded in two layers:
- **CreditTransaction** — immutable ledger with balance snapshots, searchable in Django admin and the credit detail page
- **AuditEvent** — 7 event types: `CREDIT_CHECKED`, `CREDIT_RESERVED`, `CREDIT_CONSUMED`, `CREDIT_REFUNDED`, `CREDIT_ALLOCATION_UPDATED`, `CREDIT_LIMIT_EXCEEDED`, `CREDIT_MONTHLY_RESET`

### Management Command

```bash
python manage.py bootstrap_credit_accounts --initial-credits 100 --monthly-limit 50 --force
```
- Creates `UserCreditAccount` for all active users
- `--force` updates existing accounts
- `--initial-credits` sets starting balance (default: 0)
- `--monthly-limit` sets monthly cap (default: 0 = unlimited)

### Sidebar Navigation

**Credits** link in the Admin Console sidebar section, gated by `credits.manage` permission. Uses `bi-coin` icon. Visible to ADMIN and FINANCE_MANAGER roles.

---

## 18. OCR Cost Tracking

### Overview

The extraction console's Cost & Tokens panel tracks both LLM and OCR costs per extraction.

### Cost Calculation

| Component | Pricing | Tracked Fields |
|-----------|---------|----------------|
| **LLM** (GPT-4o) | $5.00/1M input tokens, $15.00/1M output tokens | `prompt_tokens`, `completion_tokens`, `total_tokens` |
| **OCR** (Azure Document Intelligence) | $1.50/1,000 pages | `ocr_page_count` |

**Total cost** = LLM cost + OCR cost

### Data Flow

1. `_ocr_document()` returns `(text, page_count, duration_ms)` — page count and duration captured at OCR time
2. `ExtractionResponse` carries `ocr_page_count`, `ocr_duration_ms`, `ocr_char_count`
3. `ExtractionResultPersistenceService` saves OCR fields to `ExtractionResult` model
4. Console view queries **all** `AgentRun` rows linked to the upload via `AgentRun.objects.filter(document_upload_id=..., agent_type=INVOICE_EXTRACTION)` and aggregates token fields using `SUM()` across every run (initial upload + all reprocesses)
5. Console calculates: `ocr_cost = ocr_pages x $1.50 / 1,000 x run_count` (OCR is re-run on every reprocess) and `llm_cost` from the aggregated token totals

### Multi-Run Aggregation

Each reprocess re-runs the full pipeline (OCR + LLM). `AgentRun.document_upload` (FK, indexed) links every run back to the originating `DocumentUpload`. The console therefore shows **cumulative** token usage and cost across all runs, not just the most recent.

| Data | Source | Behavior |
|------|--------|----------|
| `prompt_tokens`, `completion_tokens`, `total_tokens` | `SUM()` across all `AgentRun` rows for the upload | Cumulative across all runs |
| OCR cost | `ocr_page_count x $1.50/1000 x run_count` | Multiplied by number of extraction runs |
| `run_count` | `AgentRun.objects.filter(document_upload_id=...).count()` | Shown in "Extraction Runs" KPI card |

### Console Cost Panel

**Template**: `templates/extraction/console/_cost_tokens_panel.html`

**KPI Cards** (5):
- Total Cost (LLM + OCR, all runs) — warning color
- LLM Cost — primary color
- OCR Cost — info color
- Total Tokens (all runs) — success color
- Extraction Runs (`run_count`) — secondary color

**Cost Breakdown**: Side-by-side LLM vs OCR bars with dollar amounts and detail (token counts / page+char counts)

**Token Breakdown**: Stacked progress bar (prompt vs completion tokens, summed across all runs)

**Execution Details**: LLM Model, OCR Engine, Agent Type, Status, OCR Duration, LLM Duration, Timestamps, Pricing rates, Agent Run ID

---

## 19. Django Admin

### apps/extraction Admin

**File**: `apps/extraction/admin.py`

#### ExtractionResultAdmin

| Feature | Detail |
|---------|--------|
| List display | ID, upload, invoice, engine, confidence (color-coded), success badge, duration, created_at |
| Filters | success, engine_name, engine_version |
| Search | filename, error_message |
| Fieldsets | Links, Engine, Result, Raw Data (collapsed), Audit (collapsed) |

#### ExtractionApprovalAdmin

| Feature | Detail |
|---------|--------|
| List display | ID, invoice, status (color-coded), confidence (color-coded), fields_corrected_count, is_touchless, reviewed_by, reviewed_at |
| Filters | status, is_touchless |
| Search | invoice number, vendor name |
| Inlines | `ExtractionFieldCorrectionInline` (tabular, read-only) |
| Fieldsets | Links, Decision, Metrics, Snapshot (collapsed), Audit (collapsed) |

### apps/extraction_core Admin

**File**: `apps/extraction_core/admin.py` — 13 models registered

| Admin Class | List Display Highlights |
|-------------|------------------------|
| `TaxJurisdictionProfileAdmin` | country_code, country_name, tax_regime, default_currency, tax_id_label, is_active |
| `ExtractionSchemaDefinitionAdmin` | name, jurisdiction, document_type, schema_version, is_active |
| `ExtractionRuntimeSettingsAdmin` | name, jurisdiction_mode, default_country_code, default_regime_code, is_active |
| `EntityExtractionProfileAdmin` | entity, country_code, regime_code, jurisdiction_mode, is_active |
| `ExtractionRunAdmin` | id, document, status, country_code, overall_confidence, review_queue, requires_review, duration_ms |
| `ExtractionFieldValueAdmin` | extraction_run, field_code, value, confidence, category, is_corrected |
| `ExtractionLineItemAdmin` | extraction_run, line_index, confidence, is_valid |
| `ExtractionEvidenceAdmin` | extraction_run, field_code, page_number, extraction_method, confidence |
| `ExtractionIssueAdmin` | extraction_run, severity, field_code, check_type, message |
| `ExtractionApprovalRecordAdmin` | extraction_run, action, approved_by, decided_at |
| `ExtractionCorrectionAdmin` | extraction_run, field_code, original/corrected values, corrected_by |
| `ExtractionAnalyticsSnapshotAdmin` | snapshot_type, country_code, period, run_count, average_confidence |
| `CountryPackAdmin` | jurisdiction, pack_status, schema/validation/normalization versions, activated_at |

#### UserCreditAccountAdmin

| Feature | Detail |
|---------|--------|
| List display | User email, balance, reserved, available (color-coded), monthly_limit, monthly_used, is_active |
| Filters | is_active |
| Search | user email |
| Inlines | `CreditTransactionInline` (last 50 transactions, read-only) |
| Validation | Manual adjustments require `remarks` field; validates `balance >= reserved` to prevent invariant violation |

#### CreditTransactionAdmin

| Feature | Detail |
|---------|--------|
| List display | Account (email), transaction_type, credits, balance_after, reference_type, created_at |
| Filters | transaction_type, reference_type |
| Search | account email, reference_id, remarks |
| Read-only | All fields (immutable ledger — no add/edit/delete) |

---

## 20. File Reference

### apps/extraction (Application Layer — UI, Task, Core Models)

| File | Purpose |
|------|---------|
| `apps/extraction/models.py` | ExtractionResult, ExtractionApproval, ExtractionFieldCorrection models |
| `apps/extraction/tasks.py` | Main extraction pipeline Celery task |
| `apps/extraction/admin.py` | Django admin registrations (3 models) |
| `apps/extraction/template_views.py` | All 15 template views (workbench, upload, approval queue, console, country packs) |
| `apps/extraction/urls.py` | URL routing (15 routes) |
| `apps/extraction/api_urls.py` | API URL routing (empty) |
| `apps/extraction/services/extraction_adapter.py` | Azure DI OCR (with `features=[BARCODES]`) + QR decode + LLM extraction orchestration |
| `apps/extraction/services/qr_decoder_service.py` | `QRCodeDecoderService` — decode Indian e-invoice QR (4 strategies: `azure_barcode`, `ocr_text`, `ocr_irn_text`, `pyzbar`); `_unwrap_jwt()` for NIC-signed JWT detection; `_PLAIN_IRN_RE` for plain-text `IRN :` label fallback; `QRInvoiceData` dataclass; serialized to `raw_response["_qr"]` |
| `apps/extraction/services/parser_service.py` | JSON → ParsedInvoice dataclass parsing |
| `apps/extraction/services/normalization_service.py` | Field normalization (dates, amounts, strings) |
| `apps/extraction/services/field_confidence_service.py` | Per-field confidence scoring (0.0–1.0) + evidence-aware adjustments (`ocr_text`, `evidence_context`, `qr_verified`); QR match → 0.99, QR mismatch → cap 0.40; serialized to `raw_response["_field_confidence"]` |
| `apps/extraction/services/reconciliation_validator.py` | 6 deterministic math checks; structured issues with severity (ERROR/WARNING/INFO); serialized to `raw_response["_validation"]` |
| `apps/extraction/services/validation_service.py` | Mandatory field validation + deterministic confidence scoring + critical field check (reads `field_confidence`, sets `requires_review_override`) |
| `apps/extraction/decision_codes.py` | Centralized machine-readable decision code constants + `derive_codes()` (accepts `qr_data`) + `ROUTING_MAP` + `HARD_REVIEW_CODES`; includes `QR_DATA_VERIFIED`, `QR_MISMATCH`, `QR_IRN_PRESENT`, `IRN_DUPLICATE`; serialized to `raw_response["_decision_codes"]` |
| `apps/extraction/services/recovery_lane_service.py` | `RecoveryLaneService` — deterministic policy evaluation + fail-silent `InvoiceUnderstandingAgent` invocation; serialized to `raw_response["_recovery"]` |
| `apps/extraction/services/duplicate_detection_service.py` | Duplicate invoice detection |
| `apps/extraction/services/persistence_service.py` | Invoice + LineItem + ExtractionResult persistence |
| `apps/extraction/services/approval_service.py` | Approval lifecycle (approve/reject/auto-approve + analytics) |
| `apps/extraction/services/upload_service.py` | File upload, hash computation, DocumentUpload creation |
| `apps/extraction/services/credit_service.py` | Credit reserve/consume/refund/allocate/adjust service + `ChargePolicy` (centralized charge/refund decisions) + audit events, idempotency, invariant enforcement |
| `apps/extraction/services/confidence_scorer.py` | Deterministic confidence scoring for legacy pipeline (field coverage 50%, line quality 30%, consistency 20%) |
| `apps/extraction/services/execution_context.py` | ExecutionContext dataclass + get_execution_context() — centralized governed/legacy data resolution |
| `apps/extraction/credit_models.py` | UserCreditAccount + CreditTransaction models |
| `apps/extraction/credit_views.py` | Credit account list/detail/adjust views |
| `apps/extraction/forms.py` | CreditAdjustmentForm (add/subtract/set_limit/toggle_active) |
| `apps/extraction/management/commands/bootstrap_credit_accounts.py` | Bootstrap credit accounts for all users |

### apps/extraction_core (Platform Layer — Configuration, Execution, Governance)

| File | Purpose |
|------|---------|
| `apps/extraction_core/models.py` | 13 models (jurisdiction, schema, runtime, entity, run, field, line item, evidence, issue, approval, correction, analytics, country pack) |
| `apps/extraction_core/admin.py` | Django admin registrations (13 models) |
| `apps/extraction_core/views.py` | Configuration API ViewSets (jurisdictions, schemas, settings, entity profiles, resolve/lookup) |
| `apps/extraction_core/extraction_views.py` | Execution API ViewSets (runs, country packs, analytics, pipeline trigger) |
| `apps/extraction_core/serializers.py` | Configuration API serializers |
| `apps/extraction_core/extraction_serializers.py` | Execution API serializers |
| `apps/extraction_core/api_urls.py` | Configuration API URL routing (`/api/v1/extraction-core/`) |
| `apps/extraction_core/extraction_api_urls.py` | Execution API URL routing (`/api/v1/extraction-pipeline/`) |
| **Core Pipeline & Orchestration** | |
| `apps/extraction_core/services/extraction_pipeline.py` | 11-stage governed pipeline orchestrator |
| `apps/extraction_core/services/extraction_service.py` | Original pipeline orchestrator (`ExtractionExecutionResult` dataclass — renamed from `ExtractionResult`; legacy alias emits `DeprecationWarning` via module `__getattr__`, target removal 2026-Q3) |
| `apps/extraction_core/services/base_extraction_service.py` | Schema-driven extraction base class |
| **Jurisdiction Resolution** | |
| `apps/extraction_core/services/jurisdiction_resolver.py` | Multi-signal jurisdiction detection |
| `apps/extraction_core/services/resolution_service.py` | 4-tier resolution cascade |
| **Schema & Registry** | |
| `apps/extraction_core/services/schema_registry.py` | Cached, version-aware schema lookup |
| **Document Intelligence** | |
| `apps/extraction_core/services/document_classifier.py` | Multilingual document type classification |
| `apps/extraction_core/services/relationship_extractor.py` | PO/GRN/contract cross-reference extraction |
| `apps/extraction_core/services/party_extractor.py` | Supplier/buyer/ship-to/bill-to extraction |
| `apps/extraction_core/services/document_intelligence.py` | Pre-extraction analysis orchestrator |
| **Field Extraction & Parsing** | |
| `apps/extraction_core/services/line_item_extractor.py` | Schema-driven line item extraction |
| `apps/extraction_core/services/page_parser.py` | Multi-page OCR text segmentation |
| `apps/extraction_core/services/table_stitcher.py` | Cross-page table continuation |
| **Normalization & Validation** | |
| `apps/extraction_core/services/normalization_service.py` | Jurisdiction-driven field normalization |
| `apps/extraction_core/services/enhanced_normalization.py` | Country-specific normalization (IN/AE/SA/DE/FR) |
| `apps/extraction_core/services/validation_service.py` | Jurisdiction-driven field validation |
| `apps/extraction_core/services/enhanced_validation.py` | Country-aware validation with ExtractionIssue persistence |
| **Evidence, Audit & Tracing** | |
| `apps/extraction_core/services/evidence_service.py` | Field provenance capture → ExtractionEvidence records |
| `apps/extraction_core/services/extraction_audit.py` | Extraction-specific audit logging with `event_category` taxonomy (business/governance/telemetry). NOTE: log_extraction_approved/rejected are deprecated no-ops — use GovernanceTrailService |
| `apps/extraction_core/services/governance_trail.py` | GovernanceTrailService — sole writer of ExtractionApprovalRecord (uses `update_or_create` inside `transaction.atomic`) |
| **Confidence & Review Routing** | |
| `apps/extraction_core/services/confidence_scorer.py` | Multi-dimensional confidence scoring |
| `apps/extraction_core/services/review_routing.py` | Confidence-driven review routing |
| `apps/extraction_core/services/review_routing_engine.py` | Queue-based routing; extended with `decision_codes` param for code-first routing (Rule 0 — highest precedence) |
| **LLM & Prompts** | |
| `apps/extraction_core/services/prompt_builder.py` | Dynamic LLM prompt construction |
| `apps/extraction_core/services/prompt_builder_service.py` | Enhanced data-driven prompt builder |
| `apps/extraction_core/services/llm_extraction_adapter.py` | LLM client wrapper for schema extraction |
| **Master Data & Learning** | |
| `apps/extraction_core/services/master_data_enrichment.py` | Post-extraction vendor/PO/customer matching |
| `apps/extraction_core/services/learning_service.py` | Analytics from corrections/failures → ExtractionAnalyticsSnapshot |
| **Country Governance** | |
| `apps/extraction_core/services/country_pack_service.py` | Country pack lifecycle management |
| **Output Contract** | |
| `apps/extraction_core/services/output_contract.py` | Canonical extraction output contract (MetaBlock, FieldValue, PartiesBlock, TaxBlock, LineItemRow) |

### Agent Framework

| File | Purpose |
|------|---------|
| `apps/agents/services/agent_classes.py` | InvoiceExtractionAgent + InvoiceUnderstandingAgent |
| `apps/agents/services/base_agent.py` | BaseAgent ReAct framework |
| `apps/core/prompt_registry.py` | LLM prompt templates (extraction.invoice_system) |

### Shared Infrastructure

| File | Purpose |
|------|---------|
| `apps/core/enums.py` | InvoiceStatus, ExtractionApprovalStatus, AuditEventType (incl. 15 extraction governance events), DocumentType |
| `apps/core/utils.py` | Normalization utilities (strings, dates, amounts, PO numbers) |
| `apps/documents/models.py` | DocumentUpload, Invoice, InvoiceLineItem models |
| `config/settings.py` | Azure credentials, thresholds, auto-approve config, OCR toggle |

### Templates

| File | Purpose |
|------|---------|
| `templates/extraction/workbench.html` | Extraction workbench UI (Agent Runs + Approvals tabs) |
| `templates/extraction/result_detail.html` | Extraction result detail UI |
| `templates/extraction/approval_detail.html` | Approval review UI |
| `templates/extraction/approval_queue.html` | Deprecated — redirects to workbench |
| `templates/extraction/country_packs.html` | Country pack governance (KPI strip + table) |
| `templates/extraction/credit_account_list.html` | Credit account list with search/pagination |
| `templates/extraction/credit_account_detail.html` | Credit account detail + transaction ledger + adjustment form |
| `templates/extraction/console/console.html` | Main review console layout (6 tabs) |
| `templates/extraction/console/_header_bar.html` | Command bar (status, confidence, review queue, schema, method badges) |
| `templates/extraction/console/_document_viewer.html` | **Deprecated** — no longer included in layout |
| `templates/extraction/console/_extracted_data.html` | Tab 1: Header, Parties, Tax, Enrichment, Line Items |
| `templates/extraction/console/_confidence_badge.html` | Reusable confidence % badge (green/amber/red) |
| `templates/extraction/console/_validation_panel.html` | Tab 2: Errors/Warnings/Passed with go-to-field |
| `templates/extraction/console/_evidence_panel.html` | Tab 3: Evidence cards with source snippets |
| `templates/extraction/console/_reasoning_panel.html` | Tab 4: Agent reasoning timeline |
| `templates/extraction/console/_audit_trail.html` | Tab 5: Chronological audit event timeline |
| `templates/extraction/console/_corrections_panel.html` | Tab 6: Field correction audit trail (original → corrected) |
| `templates/extraction/console/_bottom_timeline.html` | Pipeline stage progress bar |
| `templates/extraction/console/_approve_modal.html` | Approval confirmation modal |
| `templates/extraction/console/_reprocess_modal.html` | Reprocess extraction modal |
| `templates/extraction/console/_escalate_modal.html` | Escalation modal |
| `templates/extraction/console/_cost_tokens_panel.html` | Cost & Tokens panel (LLM + OCR cost breakdown, token usage, execution details) |
| `templates/extraction/console/_comment_modal.html` | Add comment modal |

### Static Assets

| File | Purpose |
|------|---------|
| `static/css/extraction_console.css` | Review console custom styles (~200 lines) |
| `static/js/extraction_console.js` | Review console JavaScript (~200 lines) |
| `templates/partials/sidebar.html` | Navigation sidebar (extraction + country packs + credits links) |

---

## Debugging Tips

- **LLM calls failing?** Check `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` env vars.
- **OCR failing?** Check `AZURE_DI_ENDPOINT` and `AZURE_DI_KEY` env vars.
- **OCR disabled?** Check `ExtractionRuntimeSettings.ocr_enabled` in the Extraction Control Center, or `EXTRACTION_OCR_ENABLED` env var. When disabled, native PDF extraction via PyPDF2 is used (no Azure DI cost).
- **Credits showing 0?** If `bootstrap_credit_accounts` was run before the user existed, use `--force` flag to update existing accounts. Or adjust via `/extraction/credits/<user_id>/`.
- **Upload blocked (insufficient credits)?** Check the user's `UserCreditAccount.balance_credits` and `monthly_used` vs `monthly_limit`. Adjust via credit management UI or Django admin.
- **Extraction task not running?** On Windows without Redis, ensure `CELERY_TASK_ALWAYS_EAGER=True` (tasks run synchronously).
- **Confidence showing 1%?** `extraction_confidence` is stored as 0.0–1.0 float; templates use `{% widthratio %}` to display as percentage.
- **Auto-approve not working?** Check both `EXTRACTION_AUTO_APPROVE_ENABLED=true` AND `EXTRACTION_AUTO_APPROVE_THRESHOLD` < 1.0 (e.g., 0.95).
- **Agent 400 errors from OpenAI?** Ensure tool-calling messages follow OpenAI format: assistant messages include `tool_calls` array, tool responses include `tool_call_id`.
- **Approval queue empty?** Invoices only appear when `status=PENDING_APPROVAL` — check that the extraction pipeline completed successfully and auto-approve didn't trigger.
- **Recovery lane not triggering?** Check `raw_response["_decision_codes"]` — recovery only fires for named codes (`INV_NUM_UNRECOVERABLE`, `TOTAL_MISMATCH_HARD`, etc.), not for generic low confidence.
- **prompt_source shows None in console?** The extraction may predate Phase 2 hardening — `_prompt_meta` is absent from older `raw_response` records. `_enrich_hardening_fields()` handles this gracefully (returns `None`).
- **derive_codes returns empty?** Check that `FieldConfidenceService` and `ReconciliationValidatorService` ran successfully (steps 3a and 4a). If they failed silently, their results are `None` and `derive_codes()` receives no inputs.

---

## 21. Bulk Extraction Intake (Phase 1)

Bulk Extraction Intake allows operators to point the system at a folder or cloud drive, discover all invoice documents, and process them through the existing extraction pipeline in a single job. This is Phase 1 — manual-start, batch-oriented intake.

### 21.1 Overview

- **Manual start only** — no watched folders, no continuous sync.
- **Reuses existing pipeline** — each discovered file goes through the same `DocumentUpload` → `process_invoice_upload_task` → extraction → approval flow.
- **Per-item credit reservation** — one credit reserved and consumed per file; credit-blocked items are skipped without stopping the job.
- **Duplicate protection** — by `source_file_id` within the job and by SHA-256 `file_hash` against existing `DocumentUpload` records.

### 21.2 Supported Sources

| Source Type | Adapter | Auth | Config Keys |
|---|---|---|---|
| `LOCAL_FOLDER` | `LocalFolderBulkSourceAdapter` | Filesystem access | `folder_path` |
| `GOOGLE_DRIVE` | `GoogleDriveBulkSourceAdapter` | Service-account JSON | `service_account_json`, `folder_id` |
| `ONEDRIVE` | `OneDriveBulkSourceAdapter` | Client credentials OAuth2 | `tenant_id`, `client_id`, `client_secret`, `drive_id`, `folder_path` |

Supported file types: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`.

### 21.3 Data Models

All models in `apps/extraction/bulk_models.py`.

| Model | Inherits | Purpose |
|---|---|---|
| `BulkSourceConnection` | `BaseModel` | Reusable source configuration (name, type, `config_json`) |
| `BulkExtractionJob` | `BaseModel` | One batch run — tracks status, counters, timestamps |
| `BulkExtractionItem` | `TimestampMixin` | One file within a job — tracks status, links to `DocumentUpload` and `ExtractionRun` |

**Job status flow:**
```
QUEUED → SCANNING → PROCESSING → COMPLETED | PARTIAL_FAILED | FAILED
```

**Item status flow:**
```
DISCOVERED → REGISTERED → PROCESSING → PROCESSED
           → SKIPPED (unsupported type)
           → DUPLICATE (file hash or source_file_id collision)
           → CREDIT_BLOCKED (insufficient credits)
           → FAILED (download/upload/extraction error)
           → UNSUPPORTED (non-supported extension)
```

### 21.4 Processing Flow

1. User selects a `BulkSourceConnection` and clicks "Start Job" in the UI.
2. `BulkExtractionService.create_job()` creates a `QUEUED` job and logs `BULK_JOB_CREATED` audit event.
3. `run_bulk_job_task` (Celery) calls `BulkExtractionService.run_job()`:
   - **Validate** source connection config.
   - **Scan** — adapter's `list_files()` discovers documents; items are created as `DISCOVERED`.
   - **Process** each item sequentially:
     - Duplicate check (source_file_id within prior items + SHA-256 hash against `DocumentUpload`).
     - Credit reservation via `CreditService.reserve()`.
     - Download via adapter's `download_file()`.
     - Compute SHA-256 hash, re-check for hash duplicates.
     - Create `DocumentUpload` record.
     - Upload to Azure Blob Storage.
     - Run extraction synchronously via `process_invoice_upload_task.run()`.
     - Credit consumption via `CreditService.consume()`.
     - Link `ExtractionRun` to the item.
   - **Finalize** — compute counters, set terminal status.
4. Extracted invoices enter the normal approval queue.

### 21.5 Credit Handling

- Uses the existing `CreditService` from `apps/extraction/services/credit_service.py`.
- **Per-item** reserve → consume lifecycle. If reservation fails, the item is marked `CREDIT_BLOCKED` and the job continues with remaining items.
- On item failure after reservation, credits are refunded via `CreditService.refund()`.
- Reference type: `"bulk_item"`, reference ID: `BulkExtractionItem.id`.

### 21.6 Duplicate Protection

Two layers:
1. **Source-level** — `source_file_id` uniqueness within prior `BulkExtractionItem` records for the same source connection.
2. **Content-level** — SHA-256 file hash checked against `DocumentUpload.file_hash` across the entire system.

Duplicates are marked with `DUPLICATE` status and a descriptive `skip_reason`.

### 21.7 UI & Routes

| URL | View | Permission | Method |
|---|---|---|---|
| `/extraction/bulk/` | `bulk_job_list` | `extraction.bulk_view` | GET |
| `/extraction/bulk/start/` | `bulk_job_start` | `extraction.bulk_create` | POST |
| `/extraction/bulk/<id>/` | `bulk_job_detail` | `extraction.bulk_view` | GET |

Templates: `templates/extraction/bulk_job_list.html`, `templates/extraction/bulk_job_detail.html`.

Sidebar entry: "Bulk Extraction" under AI Agents section, gated by `extraction.bulk_view`.

### 21.8 Permissions

| Code | Roles Granted |
|---|---|
| `extraction.bulk_view` | ADMIN, AP_PROCESSOR, FINANCE_MANAGER, AUDITOR, SYSTEM_AGENT |
| `extraction.bulk_create` | ADMIN, AP_PROCESSOR, FINANCE_MANAGER, SYSTEM_AGENT |

### 21.9 Audit Events

| Event Type | When |
|---|---|
| `BULK_JOB_CREATED` | Job record created |
| `BULK_JOB_STARTED` | Job begins processing |
| `BULK_ITEM_REGISTERED` | Item enters extraction pipeline |
| `BULK_ITEM_SKIPPED` | Item skipped (unsupported/duplicate) |
| `BULK_ITEM_CREDIT_BLOCKED` | Insufficient credits for item |
| `BULK_JOB_COMPLETED` | Job finished (success or partial) |
| `BULK_JOB_FAILED` | Job failed with unrecoverable error |

### 21.10 Phase 1 Limitations

- **Manual start only** — no watched folders, no scheduled polling.
- **Sequential item processing** — items are processed one at a time within a job (no parallel extraction).
- **No re-import** — failed items cannot be retried individually; start a new job.
- **No continuous sync** — no change detection or incremental scanning.
- **Google Drive / OneDrive adapters** require external libraries (`google-api-python-client`, `msal`, `requests`) — not yet in `requirements.txt`.

### 21.11 Files

| File | Purpose |
|---|---|
| `apps/extraction/bulk_models.py` | BulkSourceConnection, BulkExtractionJob, BulkExtractionItem |
| `apps/extraction/services/bulk_source_adapters.py` | Source adapters (Local, Google Drive, OneDrive) + factory |
| `apps/extraction/services/bulk_service.py` | BulkExtractionService orchestrator |
| `apps/extraction/bulk_tasks.py` | Celery task `run_bulk_job_task` |
| `apps/extraction/bulk_views.py` | Template views (list, start, detail) |
| `templates/extraction/bulk_job_list.html` | Job list + start modal |
| `templates/extraction/bulk_job_detail.html` | Job detail + items table |

---

## 22. Langfuse Observability

Full reference: [LANGFUSE_OBSERVABILITY.md](LANGFUSE_OBSERVABILITY.md)

### 22.1 Active trace call sites

| # | Name | Location | Trace ID |
|---|---|---|---|
| 1 | `invoice_extraction` | `InvoiceExtractionAgent.run()` — standalone (no pipeline trace) | Django `trace_id` |
| 2 | `llm_extract_fallback` | `InvoiceExtractionAdapter._llm_extract()` — direct Azure OpenAI fallback | `f"inv-{invoice_id}"` or uuid |
| 3 | Extraction pipeline scores | `ExtractionPipeline.run()` Step 9 | `str(run.pk)` |
| 4 | Approval scores | `ExtractionApprovalService` (auto-approve, approve, reject) | `f"approval-{approval.pk}"` |

### 22.2 LLM fallback trace structure

When `InvoiceExtractionAgent` is unavailable and `_llm_extract()` is called
directly, a standalone root trace records the Azure OpenAI call:

```
llm_extract_fallback   (start_trace)
  -- LLM_EXTRACT_FALLBACK   (start_span)
     -- llm_extract_fallback_chat   (log_generation, with token counts)
```

The system prompt is fetched once via `_get_extraction_prompt()` and reused
in both the `client.chat.completions.create()` call and `log_generation`.

### 22.3 Approval score lifecycle

All scores use `f"approval-{approval.pk}"` as trace ID, linking priority,
confidence, and decision scores for the same approval record in Langfuse.

```
approval-42
  score: extraction_auto_approve_confidence = 0.94   (try_auto_approve)
    -- OR --
  score: extraction_approval_decision        = 1.0    (approve)
  score: extraction_approval_confidence      = 0.87   (approve)
  score: extraction_corrections_count        = 3.0    (if corrections made)
    -- OR --
  score: extraction_approval_decision        = 0.0    (reject)
```

### 22.4 Session attribution

Every extraction trace uses `session_id=f"case-{case_number}"` when a case
exists (created at upload time), falling back to `"extraction-upload-{upload_id}"`.
This groups all pipeline stages for the same document -- extraction,
reconciliation, case processing, and agents -- into one Langfuse session.
The `user_id` is set to `actor_user_id` (the Django `User.pk`) so you can
filter traces per reviewer in the Users tab.

### 22.5 Known SDK quirk (v4)

Langfuse SDK v4 removed `user_id`/`session_id` from `start_observation()`.
Both are set post-creation as OTel span attributes. Do **not** pass them
directly to `start_observation()` -- this causes a silent `TypeError`
that returns `None` and breaks all traces. See [LANGFUSE_OBSERVABILITY.md §11 Issue 1](LANGFUSE_OBSERVABILITY.md) for the fix pattern.

---

## 22. Phase 2 Hardening

This section documents the five hardening changes added after Phase 2 (modular prompt composition + response repair + field confidence). All changes are additive and fail-silent — no breaking changes to existing approval, governance, or Langfuse flows.

### 22.1 Decision Codes

**File**: `apps/extraction/decision_codes.py`

Machine-readable string constants for every named failure mode in the pipeline. Replaces ad-hoc string matching in routing and recovery logic.

**`derive_codes(validation_result, recon_val_result, field_conf_result, prompt_source_type, qr_data=None)`**:
- Called at pipeline step 4b (after all validation and math checks)
- Returns a deduplicated, stable-order list of applicable codes
- Embedded into `raw_response["_decision_codes"]` and audit metadata
- `qr_data` (optional `QRInvoiceData`) adds QR-specific codes: `QR_IRN_PRESENT`, `QR_DATA_VERIFIED`, `QR_MISMATCH`
- Fail-silent: returns `[]` on any exception

**Usage in downstream components**:

| Consumer | How it uses codes |
|----------|------------------|
| `RecoveryLaneService.evaluate()` | Checks membership in `RECOVERY_TRIGGER_CODES` |
| `ReviewRoutingEngine.evaluate()` | Maps codes via `ROUTING_MAP` for queue assignment (Rule 0, highest precedence) |
| `ExecutionContext` | Populated from `raw_response["_decision_codes"]` for UI display |
| Audit log | Included in `AuditService` metadata for every `EXTRACTION_COMPLETED` event |

### 22.2 Recovery Lane

**File**: `apps/extraction/services/recovery_lane_service.py`

Post-extraction bounded correction. Triggered **only** by named failure modes — **not** by generic low-confidence scores.

**Design rules**:
- `evaluate()` is a pure deterministic function (no I/O, no DB calls)
- `invoke()` is the only function that touches the database (creates `AgentRun`)
- Output is strictly additive — original extraction is never modified
- Always fail-silent — pipeline never raises due to recovery lane failure
- Agent is `InvoiceUnderstandingAgent` with `reconciliation_result=None` and bounded `ctx.extra`

**Recovery trigger flow**:
```
derive_codes()
    └─ any code in RECOVERY_TRIGGER_CODES?
           │ YES
           ▼
    RecoveryLaneService.evaluate(codes)  →  RecoveryDecision
           │ should_invoke=True
           ▼
    RecoveryLaneService.invoke(decision, invoice_id, ...)
           │
           ▼
    InvoiceUnderstandingAgent.run(ctx)
           │
           ▼
    RecoveryResult  →  raw_response["_recovery"]
                   →  AgentRun.input_payload["_recovery_meta"]
```

**Not triggered by**:
- `LOW_CONFIDENCE_CRITICAL_FIELD` alone
- `LINE_SUM_MISMATCH` alone
- Any confidence score below threshold (only named codes trigger)

### 22.3 Evidence-Aware Field Confidence

**File**: `apps/extraction/services/field_confidence_service.py` (extended)

`FieldConfidenceService.score()` accepts two new optional parameters:

- **`ocr_text: str`** — Raw OCR text. When a critical field's extracted value appears verbatim in the OCR text (≥ 3 chars), its score is boosted by +0.10 (capped at 0.95). Confirmed in `evidence_flags[field] = "... ocr_confirmed"`.

- **`evidence_context: dict`** — Extraction evidence hints:
  - `"extraction_method"`: `"explicit"` | `"repaired"` | `"recovered"` | `"derived"` — caps critical field scores when the overall extraction was not explicit.
  - `"snippets"`: dict mapping field name → raw text snippet from the document. Each present snippet boosts the field score by +0.05 (capped at 0.90).
  - `"qr_verified"`: dict mapping field name → QR ground-truth value (populated by `QRInvoiceData.to_evidence_context()`). QR match → score **0.99**; QR mismatch → score capped at **0.40**. See §23 for full QR verification flow.

**`evidence_flags`** (new field on `FieldConfidenceResult`): records why each adjusted field was modified. Included in `raw_response["_field_confidence"]["evidence_flags"]`. QR-specific flags: `"qr_confirmed"` and `"qr_mismatch:extracted=...|qr=..."`.

**Backward compatible**: Both params are optional; existing call sites without them produce identical results (no `evidence_flags` populated).

### 22.4 Prompt Source Audit Trail

**File**: `apps/agents/services/agent_classes.py` — `InvoiceExtractionAgent`

Previously, the agent silently fell back from composed prompt to monolithic fallback without recording which path was used. Now:

- `_init_messages()` explicitly records `self._prompt_source_type = "composed"` or `"monolithic_fallback"`
- After `_finalise_run()`, the agent persists full prompt metadata to `AgentRun.input_payload["_prompt_meta"]`
- `AgentRun.prompt_version` = `prompt_hash[:50]` (or fallback source string)
- `AgentRun.invocation_reason` = `"extraction:<source_type>"`

**Prompt source precedence** (in order):
1. `ctx.extra["composed_prompt"]` — modular composed prompt from `InvoicePromptComposer` → `"composed"`
2. `PromptRegistry.get("extraction.invoice_system")` — monolithic fallback → `"monolithic_fallback"`

If path 2 is taken, `PROMPT_COMPOSITION_FALLBACK_USED` is emitted in step 4b decision codes.

### 22.5 ExecutionContext Extensions

**File**: `apps/extraction/services/execution_context.py`

Five new fields on `ExecutionContext` (Phase 2 hardening), populated on all resolution paths (governed, legacy lookup, and pure legacy) via `_enrich_hardening_fields()`:

```python
decision_codes: list           # from raw_response["_decision_codes"]
prompt_source: str | None      # from raw_response["_prompt_meta"]["prompt_source_type"]
prompt_hash: str | None        # from raw_response["_prompt_meta"]["prompt_hash"]
recovery_lane_invoked: bool    # from raw_response["_recovery"]["invoked"]
recovery_lane_succeeded: bool | None  # set only when recovery_lane_invoked=True
```

### 22.6 ReviewRoutingEngine — Decision Code Routing (Rule 0)

**File**: `apps/extraction_core/services/review_routing_engine.py`

`ReviewRoutingEngine.evaluate()` extended with optional `decision_codes: List[str]` parameter.

When provided, **Rule 0** runs first via `_apply_decision_codes()`:
- Maps each code to a queue via `ROUTING_MAP`
- Uses a priority ladder: `EXCEPTION_OPS > TAX_REVIEW > MASTER_DATA_REVIEW > AP_REVIEW`
- Sets priority `"CRITICAL"` if any `HARD_REVIEW_CODES` member is present, else `"HIGH"`
- If `EXCEPTION_OPS` with `CRITICAL` priority is set, returns immediately (skips all other rules)
- Falls through to confidence-based rules 1–6 for any remaining routing logic

Fully backward-compatible: `decision_codes=None` (default) skips Rule 0 entirely.

### 22.7 raw_response Key Summary

All Phase 2 hardening outputs are embedded as private keys in `ExtractionResult.raw_response`:

| Key | Set by | Content |
|-----|--------|---------|
| `_repair` | `ResponseRepairService` | Repair actions applied, fields modified |
| `_field_confidence` | `FieldConfidenceService` | Per-field scores + `evidence_flags` (incl. QR match/mismatch flags) |
| `_validation` | `ReconciliationValidatorService` | 6 math check results + severity |
| `_prompt_meta` | `InvoiceExtractionAgent.run()` | Prompt source type, hash, component versions |
| `_decision_codes` | `derive_codes()` | List of machine-readable code strings (incl. QR codes) |
| `_recovery` | `RecoveryLaneService.invoke()` | Agent output, trigger codes, succeeded flag |
| `_qr` | `QRCodeDecoderService` (via adapter) | Decoded e-invoice QR payload: `irn`, `irn_date`, `seller_gstin`, `buyer_gstin`, `doc_number`, `doc_date`, `total_value`, `item_count`, `main_hsn`, `doc_type`, `decode_strategy`, `signature_verified` |

---

## 23. Indian e-Invoice QR Code Support

> **Added**: 2026-03-28

### Background

All B2B invoices from Indian businesses with turnover > ₹5 Cr are mandated under the GST e-invoice scheme (GSTN notification). Before being shared with the buyer, every invoice is registered on the **Invoice Registration Portal (IRP / NIC)**, which:
1. Validates the invoice
2. Assigns an **IRN** (Invoice Reference Number) — a 64-character SHA-256 hash
3. Stamps a **digitally-signed QR code** containing key invoice fields

This QR code is printed on every compliant Indian B2B invoice and is the **highest-confidence source of ground truth** available for extraction — more reliable than OCR text because it is:
- Machine-generated (no OCR errors)
- Cryptographically tied to the invoice via IRP's digital signature
- Canonical (the same values the government's portal accepted)

### QR Payload (GSTN e-Invoice Spec v1.1)

IRP QR payload format — two variants:

**a. Plain JSON** (spec v1.0 / some vendors):
```json
{
  "Version":    "1.1",
  "Irn":        "<64-char sha256 hex>",
  "IrnDt":      "2024-01-15 10:30:00",
  "SellerGstin":"29AAAAA0000A1ZA",
  "BuyerGstin": "07BBBBB0000B1ZD",
  "DocNo":      "INV/2024/001",
  "DocDt":      "15/01/2024",
  "TotInvVal":  11800.00,
  "ItemCnt":    3,
  "MainHsnCode":"8471",
  "DocTyp":     "INV"
}
```

**b. NIC-signed JWT** (spec v1.1 — production standard):
```
<base64url_header>.<base64url_payload>.<signature>
JWT header:   {"alg": "RS256", "typ": "JWT"}
JWT payload:  {"iss": "NIC", "data": "<stringified e-invoice JSON>"}
```
The invoice fields live inside `payload["data"]` as a JSON string. `_unwrap_jwt()` handles detection and unwrapping before `_parse_einvoice_json` is called.

`DocTyp` values: `"INV"` (invoice), `"CRN"` (credit note), `"DBN"` (debit note).

### QRCodeDecoderService

**File**: `apps/extraction/services/qr_decoder_service.py`

Stateless, fail-silent. All methods are `@staticmethod`. Returns `Optional[QRInvoiceData]` — never raises.

**Four decode strategies** (attempted in order, first success wins):

| # | Strategy | Source | Requires |
|---|----------|--------|---------|
| 1 | **Azure DI barcodes** | `qr_texts` list from `_ocr_document()` — `decode_from_texts` calls `_unwrap_jwt` then `_parse_einvoice_json` | `features=[AnalysisFeature.BARCODES]` in API call |
| 2 | **OCR text — JSON inline** | `ocr_text` from Azure DI or native PDF | Nothing extra — `_decode_from_ocr_text` Path A searches for 64-char IRN JSON pattern |
| 3 | **OCR text — plain-text IRN label** | `ocr_text` | Nothing extra — `_decode_from_ocr_text` Path B matches `IRN :` label on invoice face |
| 4 | **pyzbar pixel decode** | Raw image bytes from file | `pip install pyzbar Pillow` (optional) + PyMuPDF or pdf2image for PDFs |

**`_unwrap_jwt(text: str) -> Optional[str]`** — called by `decode_from_texts` before JSON parsing:
- Detects JWT format: text with 3 `.`-separated parts
- Base64url-decodes the middle part (JWT payload)
- Extracts `payload["data"]` (the stringified e-invoice JSON) and returns it for `_parse_einvoice_json`
- If `payload` itself contains `"Irn"` key (no nested `"data"`), serialises payload as JSON
- Returns `None` if text is not a JWT — falls through to direct JSON parse

**`_decode_from_ocr_text` — two paths:**

**Path A** (existing): QR JSON payload appears inline in OCR text. Azure DI sometimes includes decoded barcode text in the OCR output. Searches for `"Irn"\s*:\s*"<64hex>"` pattern.

**Path B** (new): plain-text IRN label detection — fallback when no QR JSON is found in OCR text:
- Pre-processing: joins PDF hyphenated line-breaks (`-\n` followed by hex char → remove hyphen+newline)
- Regex: `_PLAIN_IRN_RE = re.compile(r'\bIRN\b\s*[:\-]?\s*([a-fA-F0-9]{64})', re.IGNORECASE)`
- Builds minimal `QRInvoiceData(irn=..., seller_gstin=..., buyer_gstin=..., decode_strategy="ocr_irn_text")`
- GSTINs harvested from full OCR text via `_GSTIN_RE`
- `doc_number`, `doc_date`, `total_value` are **empty** — only IRN + GSTINs are recoverable this way
- Useful for confirming e-invoice registration when the QR cannot be decoded

**`decode_strategy` field values:**

| Strategy | Description | Fields available |
|----------|-------------|-----------------|
| `azure_barcode` | Azure DI barcodes add-on decoded the QR (JWT or plain JSON) | All fields |
| `ocr_text` | QR JSON found inline in OCR text | All fields |
| `ocr_irn_text` | IRN extracted from plain-text `IRN :` label on invoice face | IRN + GSTINs only; `doc_number`, `doc_date`, `total_value` are empty |
| `pyzbar` | pyzbar pixel-level image decode | All fields |

**Strategy 1 is the primary path** because Azure DI pre-decodes the QR to a text string before passing it to our service. Strategies 2–4 are fallbacks for cases where:
- The Azure DI barcodes API did not decode the QR (very small/distorted QR, or older SDK without features support)
- Native PDF extraction was used instead of Azure DI (OCR disabled)
- `pyzbar` is available for high-accuracy pixel-level decode as a last resort

**What happens when OCR doesn't return the barcode value?**

Azure DI returns barcodes **only when `features=[AnalysisFeature.BARCODES]` is explicitly passed** to `begin_analyze_document()`. Without this flag, `page.barcodes` is always empty. The pipeline handles this gracefully:

```
Azure DI call WITHOUT features=BARCODES
    → page.barcodes = []
    → qr_texts = []
    → Strategy 1: no-op (empty list)
    → Strategy 2: OCR text Path A — IRN JSON regex scan
        → If the QR was decoded into text by Azure DI's text layer
          (unusual but possible for large/clear QR codes): finds JSON
        → Otherwise: no match
    → Strategy 3: OCR text Path B — plain-text IRN label scan
        → Matches "IRN : <64hex>" on invoice face
        → Returns partial QRInvoiceData (IRN + GSTINs only)
    → Strategy 4: pyzbar pixel decode (if installed)
        → Decodes QR from raw image pixels — works regardless of
          what Azure DI returned in text
    → If all strategies fail: qr_data = None
        → pipeline continues without QR data (no degradation)
```

The `features=[AnalysisFeature.BARCODES]` flag is now set in `_ocr_document()`. If the flag is removed or the SDK is downgraded, strategies 2–4 remain as fallbacks.

### QRInvoiceData Dataclass

```python
@dataclass
class QRInvoiceData:
    irn: str              # 64-char IRN (sha256 hex)
    irn_date: str         # "YYYY-MM-DD HH:MM:SS"
    seller_gstin: str     # Supplier's 15-char GSTIN (uppercased)
    buyer_gstin: str      # Buyer's 15-char GSTIN (empty for B2C)
    doc_number: str       # Invoice number as registered on IRP
    doc_date: str         # "DD/MM/YYYY" or "YYYY-MM-DD"
    total_value: Decimal | None
    item_count: int
    main_hsn: str         # HSN/SAC of primary line
    doc_type: str         # "INV" | "CRN" | "DBN"
    decode_strategy: str  # "azure_barcode" | "ocr_text" | "ocr_irn_text" | "pyzbar"
    signature_verified: bool  # Always False (NIC cert verification not implemented)
```

**`to_evidence_context()`** — builds the `evidence_context` dict for `FieldConfidenceService.score()`:
```python
{
    "qr_verified": {
        "invoice_number": qr.doc_number,
        "invoice_date":   qr.doc_date,
        "vendor_tax_id":  qr.seller_gstin,
        "total_amount":   str(qr.total_value),
    },
    "qr_irn":        qr.irn,
    "qr_doc_type":   qr.doc_type,
    "qr_item_count": qr.item_count,
    "qr_buyer_gstin": qr.buyer_gstin,
}
```

### How QR Data Flows Through the Pipeline

```
_ocr_document()
    ├─ features=[AnalysisFeature.BARCODES]
    └─ Returns (ocr_text, page_count, duration_ms, qr_texts)
                                               │
                                               ▼
                                    _decode_qr(file_path, ocr_text, qr_texts)
                                               │
                                               ▼ (fail-silent)
                                    QRInvoiceData or None
                                               │
                  ┌────────────────────────────┤
                  │                            │
                  ▼                            ▼
    raw_json["_qr"] =              ExtractionResponse.qr_data
    qr_data.to_serializable()
                                               │
                                ┌──────────────┤
                                │              │
                                ▼              ▼
            evidence_context =       derive_codes(
            qr_data.to_evidence_context()  qr_data=qr_data)
                                │                   │
                                ▼                   ▼
            FieldConfidenceService     QR_IRN_PRESENT
            .score(... evidence_context)  QR_DATA_VERIFIED
                                │       or QR_MISMATCH
                                ▼
              evidence_flags["invoice_number"] = "qr_confirmed"
              → score 0.99
              OR
              evidence_flags["invoice_number"] = "qr_mismatch:..."
              → score capped at 0.40
```

### Impact on Confidence and Routing

| Scenario | Field Score | Decision Code | Route |
|----------|-------------|---------------|-------|
| QR present, all checked fields match | 0.99 per field | `QR_DATA_VERIFIED`, `QR_IRN_PRESENT` | Normal approval flow |
| QR present, any field mismatches | ≤ 0.40 per mismatched field | `QR_MISMATCH`, `QR_IRN_PRESENT` | Hard review — `AP_REVIEW` queue |
| IRN seen before on another invoice | — | `IRN_DUPLICATE` | `EXCEPTION_OPS` — rejection required |
| No QR found | Unchanged | (no QR codes emitted) | Normal scoring |

`QR_MISMATCH` and `IRN_DUPLICATE` are both in `HARD_REVIEW_CODES` — they bypass auto-approval unconditionally.

### Audit Trail

The QR decode result is included in:
- `ExtractionResult.raw_response["_qr"]` — full serialised `QRInvoiceData`
- `AuditService` metadata on `EXTRACTION_COMPLETED` event: `qr_irn`, `qr_doc_type`, `qr_decode_strategy`
- `raw_response["_decision_codes"]` — QR-specific codes
- `raw_response["_field_confidence"]["evidence_flags"]` — per-field QR match/mismatch detail

### SDK Requirement

| Requirement | Detail |
|-------------|--------|
| SDK package | `azure-ai-formrecognizer >= 3.3.0` (current: 3.3.2) |
| API version | `2023-07-31` or later (barcode add-on feature added) |
| Feature flag | `features=[AnalysisFeature.BARCODES]` in `begin_analyze_document()` |
| Barcode `kind` | `"QRCode"` (PascalCase) — code uses `.upper()` for case-insensitive match |
| Optional deps | `pyzbar`, `Pillow`, PyMuPDF / pdf2image (strategy 3 only) |

### FieldConfidenceService — QR Ground-Truth Comparison Normalisation

When `FieldConfidenceService.score()` compares an extracted field value against the QR ground-truth value, it applies field-type-aware normalisation before comparing (simple separator-stripping is insufficient for production data):

| Field type | Normalisation | Example |
|-----------|---------------|---------|
| Date fields (`"date" in fname`) | `_norm_date()` — tries 6 format patterns, returns `YYYYMMDD` | `"20/09/2025"` and `"2025-09-20"` → both `"20250920"` |
| Amount fields (`"amount" or "total" in fname`) | `_norm_amount()` — strips commas, round-trips through `float(v)` | `"41958"` and `"41958.0"` → both `"41958.0"` |
| All other fields | `_sep_re.sub("", v).upper().strip()` — strip `[\s\-/]`, uppercase | `"VNR/1639/25-26"` → `"VNR163925-26"` |

Supported date formats in `_DATE_FMTS`: `%d/%m/%Y`, `%Y-%m-%d`, `%d-%m-%Y`, `%m/%d/%Y`, `%d/%m/%y`, `%Y/%m/%d`

### Limitations

- **JWT signature not verified** — The NIC digital signature (RS256) is decoded but not cryptographically verified. Fields are used as high-confidence hints, not as a security control. The NIC public certificate is at `https://einvoice1.gst.gov.in/Others/PublicKey`; verification would require the `cryptography` package.
- **`ocr_irn_text` strategy is partial** — Only IRN + GSTINs are available; `doc_number`, `doc_date`, `total_value` cannot be recovered from plain text alone. The QR panel shows a warning and prompts reprocessing with the BARCODES feature.
- **pyzbar not available in this deployment** — Strategy 4 is always skipped. Install `pip install pyzbar Pillow` to enable.
- **B2C invoices** — `BuyerGstin` is empty for B2C (end-consumer) invoices; `buyer_gstin` will be `""`.
- **Credit notes / debit notes** — `DocTyp = "CRN"` / `"DBN"` are handled; `QR_DATA_VERIFIED` is still emitted. The consuming reconciliation flow should check `qr_doc_type` for credit/debit note handling.
- **Older invoices** — Pre-e-invoice mandate invoices (before 2020-10-01 for the first tranche) will not have QR codes; `qr_data = None` is the normal outcome.

---

## Appendix: Invoice Posting Agent

> Architecture and developer guide for the posting agent that produces ERP-ready posting proposals — apps/posting/ + apps/posting_core/.

# Invoice Posting Agent — Architecture & Developer Guide

## Overview

The Invoice Posting Agent is a **Phase 1** implementation that transforms approved invoice extractions into ERP-ready posting proposals. It resolves vendor, item, tax, cost-center, and PO references from **Excel-imported ERP master data**, validates the proposal, scores confidence, routes to review queues when needed, and (mock) submits to ERP.

The system follows the same **two-layer architecture** as the extraction system:\n\n> **Multi-Tenant**: All posting models (`InvoicePosting`, `PostingRun`, etc.) carry a `tenant` FK to `CompanyProfile`. The posting pipeline inherits the tenant from the source Invoice. ERP reference data (vendor, item, tax, cost-center tables) is also tenant-scoped. See [MULTI_TENANT.md](MULTI_TENANT.md).", "oldString": "The system follows the same **two-layer architecture** as the extraction system:

| Layer | Django App | Purpose |
|---|---|---|
| **Business / UI** | `apps/posting/` | Workflow state, user-facing actions, templates, API |
| **Platform / Core** | `apps/posting_core/` | Execution records, mapping engine, validation, ERP reference data |

---

## File Layout

```
apps/posting/                              # Business layer
├── models.py                              # InvoicePosting, InvoicePostingFieldCorrection
├── services/
│   ├── eligibility_service.py             # 7-check eligibility gate
│   ├── posting_orchestrator.py            # Orchestrates prepare_posting lifecycle
│   └── posting_action_service.py          # approve / reject / submit / retry
├── tasks.py                               # Celery: prepare_posting_task, import_reference_excel_task
├── views.py                               # DRF ViewSets + PostingPrepareView
├── serializers.py                         # DRF serializers
├── api_urls.py                            # /api/v1/posting/
├── template_views.py                      # Workbench, detail, import list
├── urls.py                                # /posting/
└── admin.py

apps/posting_core/                         # Platform layer
├── models.py                              # PostingRun, ERP references (15 models)
├── services/
│   ├── import_pipeline/                   # ERP reference import from Excel/CSV
│   │   ├── import_parsers.py              # parse_excel_file, normalize_header
│   │   ├── import_validators.py           # validate_columns, validate_row
│   │   ├── vendor_importer.py             # VendorImporter
│   │   ├── item_importer.py               # ItemImporter
│   │   ├── tax_importer.py                # TaxImporter
│   │   ├── cost_center_importer.py        # CostCenterImporter
│   │   ├── po_importer.py                 # POImporter
│   │   └── excel_import_orchestrator.py   # ExcelImportOrchestrator.run_import()
│   ├── posting_mapping_engine.py          # Core value: resolve ERP mappings
│   ├── posting_pipeline.py                # 9-stage pipeline orchestration
│   ├── posting_snapshot_builder.py        # Capture invoice snapshot as JSON
│   ├── posting_validation.py              # Validate proposal completeness
│   ├── posting_confidence.py              # Weighted confidence scoring
│   ├── posting_review_routing.py          # Review queue assignment
│   ├── posting_governance_trail.py        # Governance mirror writes
│   ├── posting_audit.py                   # Centralized audit logging
│   └── payload_builder.py                 # Build canonical ERP payload
├── views.py                               # DRF: PostingRunViewSet, ERP ref ViewSets
├── serializers.py                         # DRF serializers for all models
├── api_urls.py                            # /api/v1/posting-core/
├── urls.py                                # (empty — all UIs in apps.posting)
└── admin.py
```

---

## Data Model

### Business Layer (`apps/posting/`)

**InvoicePosting** — One-to-one with Invoice. Tracks posting lifecycle.

| Field | Type | Description |
|---|---|---|
| `invoice` | OneToOneField → Invoice | The invoice being posted |
| `extraction_result` | FK → ExtractionResult | Source extraction |
| `extraction_run` | FK → ExtractionRun | Source extraction run |
| `status` | InvoicePostingStatus (11 states) | Current lifecycle state |
| `stage` | PostingStage | Last completed pipeline stage |
| `posting_confidence` | Float | 0.0–1.0 overall confidence |
| `review_queue` | PostingReviewQueue | Assigned review queue |
| `is_touchless` | Boolean | True if no human review needed |
| `mapping_summary_json` | JSON | Summary of mapping results |
| `payload_snapshot_json` | JSON | ERP-ready posting payload |
| `erp_document_number` | CharField | ERP document ID after posting |
| `retry_count` | PositiveInt | Number of retry attempts |

**InvoicePostingFieldCorrection** — Tracks field corrections during review.

### Platform Layer (`apps/posting_core/`)

**PostingRun** — Authoritative execution record per pipeline invocation (analogous to ExtractionRun).

| Key Children | Description |
|---|---|
| `PostingFieldValue` | Resolved field values with source/confidence |
| `PostingLineItem` | Resolved line items with ERP codes |
| `PostingIssue` | Validation issues (severity, check_type) |
| `PostingEvidence` | Source evidence for resolved values |
| `PostingApprovalRecord` | Governance mirror (1:1) |

**ERP Reference Models** (imported from Excel):

| Model | Key Fields | Purpose |
|---|---|---|
| `ERPReferenceImportBatch` | batch_type, status, row_count | Batch metadata |
| `ERPVendorReference` | vendor_code, vendor_name, normalized | Vendor master |
| `ERPItemReference` | item_code, item_name, uom, tax_code | Item/material master |
| `ERPTaxCodeReference` | tax_code, rate, country_code | Tax code master |
| `ERPCostCenterReference` | cost_center_code, department | Cost center master |
| `ERPPOReference` | po_number, po_line, vendor_code, item_code | Open PO lines |

**Alias & Rules:**

| Model | Purpose |
|---|---|
| `VendorAliasMapping` | Map vendor name variants → ERP vendor code |
| `ItemAliasMapping` | Map item description variants → ERP item code |
| `PostingRule` | Configurable tax/cost-center/line-type rules |

---

## Status Lifecycle

```
                          ┌──────────────────────────────────────────┐
                          │                                          │
NOT_READY ──► READY_FOR_POSTING ──► MAPPING_IN_PROGRESS ─┬──► MAPPING_REVIEW_REQUIRED ──► READY_TO_SUBMIT
                                                          │                                    │
                                                          └──► READY_TO_SUBMIT ◄───────────────┘
                                                                     │
                                                                     ▼
                                                          SUBMISSION_IN_PROGRESS ──► POSTED
                                                                     │
                                                                     ▼
                                                                POST_FAILED ──► RETRY_PENDING ──► (re-enter pipeline)
                                                                     │
                                                                     ▼
                                                                  REJECTED

                                                          SKIPPED (manual skip)
```

**Approval Actions:**

| Action | Allowed From | Transitions To |
|---|---|---|
| `approve` | MAPPING_REVIEW_REQUIRED, READY_TO_SUBMIT | READY_TO_SUBMIT |
| `reject` | MAPPING_REVIEW_REQUIRED, READY_TO_SUBMIT, POST_FAILED | REJECTED |
| `submit` | READY_TO_SUBMIT | POSTED (Phase 1 mock) |
| `retry` | POST_FAILED, RETRY_PENDING, MAPPING_REVIEW_REQUIRED | Re-enters pipeline |

---

## Pipeline Architecture

The posting pipeline executes a 9-stage sequence inside `PostingPipeline.run()`:

```
Invoice (READY_FOR_RECON)
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  PostingPipeline.run(invoice)                                   │
│                                                                 │
│  1. ELIGIBILITY_CHECK    → PostingEligibilityService.check()    │
│  2. SNAPSHOT_BUILD       → PostingSnapshotBuilder.build()       │
│  3. MAPPING              → PostingMappingEngine.resolve()       │
│  4. VALIDATION           → PostingValidationService.validate()  │
│  5. CONFIDENCE           → PostingConfidenceService.calculate() │
│  6. REVIEW_ROUTING       → PostingReviewRoutingService.route()  │
│  7. PAYLOAD_BUILD        → PayloadBuilder.build()               │
│  8. FINALIZATION         → Persist field values, line items,    │
│                            issues, evidence (bulk_create)       │
│  9. STATUS               → Set final PostingRun status          │
│                                                                 │
└────────────────────────────────────────────┬────────────────────┘
                                             │
                                             ▼
                                        PostingRun
                                    (COMPLETED / FAILED)
```

---

## Mapping Engine — The Core Value

`PostingMappingEngine` resolves extracted invoice data to ERP-native codes using **imported reference tables** (never live Excel reads). Each resolution follows a **chain of strategies** that stops at first match:

### Vendor Resolution Chain
```
1. Exact vendor_code match in ERPVendorReference
2. Alias match in VendorAliasMapping (normalized)
3. Exact name match in ERPVendorReference
4. Partial/fuzzy name match (normalized contains)
5. → UNRESOLVED (routes to VENDOR_MAPPING_REVIEW queue)
```

### Item Resolution Chain (per line)
```
1. PO reference lookup → ERPPOReference (if PO number available)
2. Exact item_code match in ERPItemReference
3. Alias match in ItemAliasMapping
4. Name/description match in ERPItemReference
5. PostingRule-based mapping (rule_type=TAX_CODE/COST_CENTER)
6. → UNRESOLVED (routes to ITEM_MAPPING_REVIEW queue)
```

### Tax Code Resolution Chain
```
1. Explicit from extraction (if tax_code field populated)
2. Item default (ERPItemReference.tax_code)
3. Rate match (ERPTaxCodeReference.rate nearest)
4. PostingRule fallback (rule_type=TAX_CODE)
5. → UNRESOLVED (routes to TAX_REVIEW queue)
```

### Cost Center Resolution Chain
```
1. PostingRule match (rule_type=COST_CENTER, condition matches)
2. Exact ERPCostCenterReference lookup
3. → UNRESOLVED (routes to COST_CENTER_REVIEW queue)
```

### Reference Freshness
The engine tracks which `ERPReferenceImportBatch` was used for each resolution. Stale references (older than `POSTING_REFERENCE_FRESHNESS_HOURS`, default 168h / 7 days) generate WARNING issues and reduce confidence.

---

## Confidence Scoring

`PostingConfidenceService` calculates a weighted 0.0–1.0 score across 5 dimensions:

| Dimension | Weight | Calculation |
|---|---|---|
| Header Completeness | 15% | Proportion of required header fields present |
| Vendor Mapping | 25% | Direct vendor confidence from resolution chain |
| Line Mapping | 30% | Average confidence across all resolved lines |
| Tax Completeness | 15% | Proportion of lines with tax_code assigned |
| Reference Freshness | 15% | Inverse of staleness issue count |

---

## Review Queue Routing

`PostingReviewRoutingService.route()` determines whether human review is needed:

| Condition | Queue Assignment | Reason |
|---|---|---|
| No vendor_code resolved | `VENDOR_MAPPING_REVIEW` | "Vendor code not resolved" |
| Line item_code missing + low confidence | `ITEM_MAPPING_REVIEW` | "Item mapping unresolved for line N" |
| Any line missing tax_code | `TAX_REVIEW` | "Tax code not assigned" |
| Any line missing cost_center | `COST_CENTER_REVIEW` | "Cost center not resolved" |
| ERROR-severity issues exist | `POSTING_OPS` | "N blocking issue(s) found" |
| Confidence < 0.7 | `POSTING_OPS` | "Low overall confidence" |

If no conditions trigger → `requires_review=False`, `is_touchless=True` → auto-advances to READY_TO_SUBMIT.

---

## ERP Reference Import Pipeline

### Supported Reference Types

| Batch Type | Model | Required Columns | Purpose |
|---|---|---|---|
| `VENDOR` | ERPVendorReference | vendor_code, vendor_name | Vendor master |
| `ITEM` | ERPItemReference | item_code, item_name | Material/item master |
| `TAX` | ERPTaxCodeReference | tax_code | Tax code catalog |
| `COST_CENTER` | ERPCostCenterReference | cost_center_code, cost_center_name | Org structure |
| `OPEN_PO` | ERPPOReference | po_number | Open PO lines for matching |

### Import Flow

```
Excel/CSV Upload
       │
       ▼
  parse_excel_file()           # openpyxl for .xlsx, csv for .csv
       │                       # Normalizes headers, computes checksum
       ▼
  validate_columns()           # Checks required columns present
       │
       ▼
  TypeImporter.import_rows()   # Type-specific bulk_create + normalization
       │
       ▼
  ERPReferenceImportBatch      # Status: COMPLETED / PARTIAL / FAILED
       │                       # Tracks row_count, valid_row_count, invalid_row_count
       ▼
  Audit Event                  # ERP_REFERENCE_IMPORT_COMPLETED
```

### Upload API
```
POST /api/v1/posting-core/upload/
Content-Type: multipart/form-data

Fields:
  file:         Excel (.xlsx) or CSV file
  batch_type:   VENDOR | ITEM | TAX | COST_CENTER | OPEN_PO
  source_as_of: (optional) YYYY-MM-DD date of ERP export
```

---

## Integration Points

### Trigger: Extraction Approval → Posting

When extraction is approved (human or auto), the `ExtractionApprovalService` enqueues the posting pipeline:

```python
# In ExtractionApprovalService.approve() and try_auto_approve():
cls._enqueue_posting(invoice, user)  # best-effort, never blocks approval

# _enqueue_posting():
prepare_posting_task.delay(
    invoice_id=invoice.pk,
    user_id=user.pk if user else None,
    trigger="approval" | "auto_approval",
)
```

This is **best-effort** — posting failures never block the extraction approval path.

### Manual Trigger

```
POST /api/v1/posting/prepare/
{
    "invoice_id": 123,
    "trigger": "manual"
}
```

Returns `202 Accepted` — posting preparation runs async via Celery.

---

## API Endpoints

### Posting Business API (`/api/v1/posting/`)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/posting/postings/` | List invoice postings (filter: status, review_queue) |
| GET | `/api/v1/posting/postings/{id}/` | Posting detail with corrections |
| POST | `/api/v1/posting/postings/{id}/approve/` | Approve posting (optional corrections) |
| POST | `/api/v1/posting/postings/{id}/reject/` | Reject posting (reason) |
| POST | `/api/v1/posting/postings/{id}/submit/` | Submit to ERP (Phase 1 mock) |
| POST | `/api/v1/posting/postings/{id}/retry/` | Retry failed posting |
| POST | `/api/v1/posting/prepare/` | Trigger posting for an invoice |

### Posting Core API (`/api/v1/posting-core/`)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/posting-core/runs/` | List posting runs (filter: invoice, status) |
| GET | `/api/v1/posting-core/runs/{id}/` | Run detail (field values, lines, issues) |
| POST | `/api/v1/posting-core/upload/` | Upload ERP reference Excel/CSV |
| GET | `/api/v1/posting-core/import-batches/` | List import batches |
| CRUD | `/api/v1/posting-core/vendors/` | ERP vendor references |
| CRUD | `/api/v1/posting-core/items/` | ERP item references |
| CRUD | `/api/v1/posting-core/tax-codes/` | ERP tax codes |
| CRUD | `/api/v1/posting-core/cost-centers/` | ERP cost centers |
| CRUD | `/api/v1/posting-core/po-refs/` | ERP PO references |
| CRUD | `/api/v1/posting-core/vendor-aliases/` | Vendor alias mappings |
| CRUD | `/api/v1/posting-core/item-aliases/` | Item alias mappings |
| CRUD | `/api/v1/posting-core/rules/` | Posting rules |

### Template Views (`/posting/`)

| URL | View | Description |
|---|---|---|
| `/posting/` | `posting_workbench` | List with KPIs, filters, pagination |
| `/posting/{id}/` | `posting_detail` | Detail with proposal, issues, actions |
| `/posting/{id}/approve/` | `posting_approve` | POST: approve |
| `/posting/{id}/reject/` | `posting_reject` | POST: reject |
| `/posting/{id}/submit/` | `posting_submit` | POST: submit to ERP |
| `/posting/{id}/retry/` | `posting_retry` | POST: retry pipeline |
| `/posting/imports/` | `reference_import_list` | ERP import batch history |

---

## Enum Reference

### InvoicePostingStatus (11 states)
`NOT_READY` · `READY_FOR_POSTING` · `MAPPING_IN_PROGRESS` · `MAPPING_REVIEW_REQUIRED` · `READY_TO_SUBMIT` · `SUBMISSION_IN_PROGRESS` · `POSTED` · `POST_FAILED` · `REJECTED` · `RETRY_PENDING` · `SKIPPED`

### PostingRunStatus (5 states)
`PENDING` · `RUNNING` · `COMPLETED` · `FAILED` · `CANCELLED`

### PostingStage (9 stages)
`ELIGIBILITY_CHECK` · `SNAPSHOT_BUILD` · `MAPPING` · `VALIDATION` · `CONFIDENCE` · `REVIEW_ROUTING` · `PAYLOAD_BUILD` · `SUBMISSION` · `FINALIZATION`

### PostingReviewQueue (6 queues)
`ITEM_MAPPING_REVIEW` · `VENDOR_MAPPING_REVIEW` · `TAX_REVIEW` · `COST_CENTER_REVIEW` · `PO_REVIEW` · `POSTING_OPS`

### ERPReferenceBatchType (5 types)
`VENDOR` · `ITEM` · `TAX` · `COST_CENTER` · `OPEN_PO`

### Audit Events (17 posting-related)
`POSTING_STARTED` · `POSTING_ELIGIBILITY_PASSED` · `POSTING_ELIGIBILITY_FAILED` · `POSTING_MAPPING_COMPLETED` · `POSTING_MAPPING_REVIEW_REQUIRED` · `POSTING_VALIDATION_COMPLETED` · `POSTING_READY_TO_SUBMIT` · `POSTING_SUBMITTED` · `POSTING_SUCCEEDED` · `POSTING_FAILED` · `POSTING_APPROVED` · `POSTING_REJECTED` · `POSTING_FIELD_CORRECTED` · `ERP_REFERENCE_IMPORT_STARTED` · `ERP_REFERENCE_IMPORT_COMPLETED` · `ERP_REFERENCE_IMPORT_FAILED`

---

## Governance & Audit Trail

Every posting operation is fully auditable:

- **PostingRun** preserves complete execution history (snapshots, proposals, payloads)
- **PostingApprovalRecord** mirrors every approve/reject decision (written only by `PostingGovernanceTrailService`)
- **PostingIssue / PostingEvidence** explain validation results and source provenance
- **InvoicePostingFieldCorrection** tracks every manual correction during review
- **AuditEvent** entries logged for all 17 posting event types via `PostingAuditService`
- **ERPReferenceImportBatch** tracks every import (checksums, row counts, errors)
- All service entry points decorated with `@observed_service` for tracing

---

## Configuration

| Setting | Default | Description |
|---|---|---|
| `POSTING_REFERENCE_FRESHNESS_HOURS` | 168 (7 days) | Max age of ERP reference data before staleness warnings |
| `CELERY_TASK_ALWAYS_EAGER` | True (Windows dev) | When True, tasks run synchronously (no Redis required) |

---

## Langfuse Observability

The posting pipeline emits rich Langfuse traces with 9 per-stage spans plus
ERP resolution child spans nested under the `mapping` stage.

### Trace hierarchy

```
posting_pipeline (root trace -- one per PostingRun)
  -- eligibility_check    (stage 1)
  -- snapshot_build       (stage 2)
  -- mapping              (stage 3)
     -- erp_resolution    (per resolve_vendor / resolve_item / resolve_tax / etc.)
        -- erp_cache_lookup
        -- erp_live_lookup
        -- erp_db_fallback
  -- validation           (stage 4)
  -- confidence_scoring   (stage 5, emits posting_confidence score)
  -- review_routing       (stage 6, emits posting_requires_review score)
  -- payload_build        (stage 7)
  -- finalization         (stage 8)
  -- duplicate_check      (stage 9b)
     -- erp_resolution    (duplicate invoice check)
```

ERP resolution spans are created by `ERPResolutionService._trace_resolve()` via
`apps/erp_integration/services/langfuse_helpers.py`. Metadata is automatically
sanitised (no API keys, tokens, or passwords) and values >2000 chars are truncated.

`PostingMappingEngine` passes `lf_parent_span=self._lf_mapping_span` to all
`resolve_*()` calls so ERP spans nest under the `mapping` stage.

**Full reference**: [LANGFUSE_OBSERVABILITY.md §§7.4–7.7](LANGFUSE_OBSERVABILITY.md)

---

## Phase 2+ Extension Points

The system is designed for incremental enhancement:

| Extension | Where | Notes |
|---|---|---|
| **Real ERP submission** | `PostingActionService.submit_posting()` | Replace mock with ERP API connector or RPA bridge |
| **SAP / Oracle connectors** | New `apps/posting_core/connectors/` | Implement per-ERP protocol (BAPI, REST, IDoc) |
| **Auto-submit** | `PostingOrchestrator` | Auto-submit when `is_touchless=True` and confidence ≥ threshold |
| **Feedback learning** | `PostingMappingEngine` | Train alias mappings from accepted corrections |
| **Bulk posting** | `PostingOrchestrator` | Batch multiple invoices into single ERP journal |
| **Scheduled re-import** | Celery Beat | Periodic `import_reference_excel_task` from shared drive |
| **LLM-assisted mapping** | `PostingMappingEngine._resolve_item()` | Use GPT for fuzzy item description matching |
| **Rejection → re-extraction** | `PostingActionService.reject_posting()` | Trigger re-extraction with feedback |

---

## Appendix: ReasoningPlanner — Architecture & Upgrade Path

> Architecture reference and LLM-only upgrade path for the agent planning layer.

# ReasoningPlanner -- Architecture, Current State, and LLM-Only Upgrade Path

**Created**: 2026-04-10 | **Status**: Reference document  
**Audience**: Developers, architects, product owners

---

## Table of Contents

1. [Overview](#1-overview)
2. [How the Current Agent Planning Pipeline Works](#2-how-the-current-agent-planning-pipeline-works)
3. [PolicyEngine (Deterministic Planner)](#3-policyengine-deterministic-planner)
4. [ReasoningPlanner (LLM-Enhanced Planner)](#4-reasoningplanner-llm-enhanced-planner)
5. [Orchestrator Integration](#5-orchestrator-integration)
6. [Post-Plan Pipeline: Reflection + Deterministic Resolution](#6-post-plan-pipeline-reflection--deterministic-resolution)
7. [Eval Tracking: LLM vs Deterministic Plan Comparison](#7-eval-tracking-llm-vs-deterministic-plan-comparison)
8. [Approach: Fully LLM-Dependent Planning (No Deterministic Fallback)](#8-approach-fully-llm-dependent-planning-no-deterministic-fallback)
9. [Implementation Checklist for Full LLM Mode](#9-implementation-checklist-for-full-llm-mode)
10. [Risk Matrix and Mitigations](#10-risk-matrix-and-mitigations)
11. [Key Files](#11-key-files)

---

## 1. Overview

The agent pipeline decides **which AI agents** to run for a given reconciliation result and **in what order**. This decision is called a **plan**. Today, plan generation has two modes:

| Mode | Flag | Planner Class | LLM Calls | Production Default |
|------|------|--------------|-----------|-------------------|
| **Deterministic** | `AGENT_REASONING_ENGINE_ENABLED=false` | `PolicyEngine` | 0 | Yes (current default) |
| **LLM-Enhanced** | `AGENT_REASONING_ENGINE_ENABLED=true` | `ReasoningPlanner` | 1 (planning call) | No |

Both modes feed into the same orchestrator, which sequences agent execution, applies reflection rules, and runs deterministic resolution for tail agents.

---

## 2. How the Current Agent Planning Pipeline Works

```
ReconciliationResult (non-MATCHED)
        |
        v
[AgentOrchestrator.__init__()]
        |
        |-- AGENT_REASONING_ENGINE_ENABLED=false --> PolicyEngine.plan()
        |-- AGENT_REASONING_ENGINE_ENABLED=true  --> ReasoningPlanner.plan()
        |                                               |
        |                                               |-- PolicyEngine.plan() runs FIRST (baseline)
        |                                               |-- If skip_agents=True, return immediately
        |                                               |-- Else: call LLM for agent selection
        |                                               |-- On LLM error: fall back to PolicyEngine result
        v
   AgentPlan {agents: [...], plan_source: "deterministic"|"llm", plan_confidence: float}
        |
        v
[Orchestrator.execute()]
        |
        |-- 1. RBAC checks (actor, orchestration permission, data scope)
        |-- 2. Duplicate-run guard (reject if RUNNING orchestration exists)
        |-- 3. If plan.skip_agents: auto-close or skip, return early
        |-- 4. Partition agents: LLM-agents vs deterministic-tail
        |-- 5. Execute LLM agents in sequence (with reflection after each)
        |-- 6. Execute deterministic tail (DeterministicResolver)
        |-- 7. Resolve final recommendation (highest confidence)
        |-- 8. Apply post-policies (auto-close / escalate)
        v
   OrchestrationResult
```

---

## 3. PolicyEngine (Deterministic Planner)

**File**: `apps/agents/services/policy_engine.py`

The PolicyEngine uses hardcoded rules to select agents based on:

- `match_status` (MATCHED, PARTIAL_MATCH, UNMATCHED, REQUIRES_REVIEW, ERROR)
- `deterministic_confidence` (float 0.0-1.0)
- `extraction_confidence` (float 0.0-1.0)
- `reconciliation_mode` (TWO_WAY, THREE_WAY, NON_PO)
- Exception types present on the result

### Decision Rules

| # | Condition | Agent Sequence | Result |
|---|-----------|---------------|--------|
| 1 | MATCHED + high confidence | (none) | `skip_agents=True` |
| 1b | PARTIAL_MATCH within auto-close tolerance band | (none) | `skip_agents=True, auto_close=True` |
| 2 | PO_NOT_FOUND exception | PO_RETRIEVAL -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 3 | GRN_NOT_FOUND (3-way only) | GRN_RETRIEVAL -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 4 | PARTIAL_MATCH (outside auto-close) | RECONCILIATION_ASSIST -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 5 | Low extraction confidence | INVOICE_UNDERSTANDING -> EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| 6 | REQUIRES_REVIEW / UNMATCHED / ERROR (fallback) | EXCEPTION_ANALYSIS -> REVIEW_ROUTING -> CASE_SUMMARY | |
| NON_PO | Non-PO mode | [INVOICE_UNDERSTANDING if low conf] -> [EXCEPTION_ANALYSIS if exceptions] -> REVIEW_ROUTING -> CASE_SUMMARY | |

### Mode Awareness

- **TWO_WAY**: GRN_RETRIEVAL is never included; GRN_NOT_FOUND exceptions are ignored
- **NON_PO**: No PO/GRN retrieval or reconciliation assist
- **THREE_WAY**: Full agent set available

### Auto-Close Band Check (`_within_auto_close_band`)

Before queuing agents, the PolicyEngine checks if a PARTIAL_MATCH result has all line discrepancies within the wider auto-close tolerance (qty: 5%, price: 3%, amount: 3%). If yes, it skips agents entirely and auto-closes the result (upgrades PARTIAL_MATCH to MATCHED). Exceptions:
- GRN_NOT_FOUND in 3-way blocks auto-close
- First-partial invoices block auto-close
- HIGH severity exceptions block auto-close

### Post-Run Policy Checks

After all agents execute, PolicyEngine provides:
- `should_auto_close(recommendation_type, confidence)` -- AUTO_CLOSE recommendation + confidence >= threshold
- `should_escalate(recommendation_type, confidence)` -- ESCALATE_TO_MANAGER recommendation + confidence >= threshold

These are always deterministic regardless of the planner used.

---

## 4. ReasoningPlanner (LLM-Enhanced Planner)

**File**: `apps/agents/services/reasoning_planner.py`

### Architecture

```
ReasoningPlanner
    |
    +-- PolicyEngine (internal, used for baseline + fallback + post-run checks)
    +-- LLMClient (temperature=0.0, max_tokens=1024)
```

The ReasoningPlanner wraps PolicyEngine. It does NOT replace it. The relationship is:

1. **PolicyEngine runs first** as a baseline (`quick_plan`)
2. If `quick_plan.skip_agents=True` -- return immediately (no LLM call)
3. Otherwise, attempt LLM planning via `_llm_plan()`
4. On any LLM failure -- fall back to `quick_plan` (deterministic)
5. `should_auto_close()` and `should_escalate()` always delegate to PolicyEngine

### LLM Planning Call

The planner sends a single chat completion request with:

**System Prompt** -- describes all 7 available agents, their purposes, and rules:
- GRN_RETRIEVAL must not appear in TWO_WAY mode
- CASE_SUMMARY should be last
- Use minimum set of agents needed
- Assign integer priorities (lower = earlier)
- Respond with valid JSON only

**User Message** -- includes the reconciliation result context:
```
match_status: PARTIAL_MATCH
reconciliation_mode: THREE_WAY
deterministic_confidence: 0.4500
extraction_confidence: 0.8200
exception_types: ["QTY_MISMATCH", "PRICE_MISMATCH"]
```

**Expected Response Schema**:
```json
{
    "overall_reasoning": "Invoice has quantity and price mismatches...",
    "confidence": 0.85,
    "steps": [
        {"agent_type": "RECONCILIATION_ASSIST", "rationale": "...", "priority": 1},
        {"agent_type": "EXCEPTION_ANALYSIS", "rationale": "...", "priority": 2},
        {"agent_type": "REVIEW_ROUTING", "rationale": "...", "priority": 3},
        {"agent_type": "CASE_SUMMARY", "rationale": "...", "priority": 4}
    ]
}
```

### Validation Rules (Post-LLM)

After parsing the LLM response, the planner validates:

1. **Agent type validation**: Only `AgentType` enum values are accepted; unknown values are silently dropped
2. **Priority sorting**: Steps sorted by priority ascending
3. **Non-empty check**: At least one valid step must remain after filtering (raises ValueError otherwise)
4. **CASE_SUMMARY position**: If present, must be last (raises ValueError otherwise)
5. **GRN_RETRIEVAL in TWO_WAY**: Rejected outright (raises ValueError)

Any ValueError from validation triggers the fallback to the deterministic plan.

### AgentPlan Output

```python
AgentPlan(
    agents=["RECONCILIATION_ASSIST", "EXCEPTION_ANALYSIS", "REVIEW_ROUTING", "CASE_SUMMARY"],
    reason="Invoice has quantity and price mismatches...",
    skip_agents=False,
    auto_close=False,
    reconciliation_mode="THREE_WAY",
    plan_source="llm",          # vs "deterministic"
    plan_confidence=0.85,       # LLM's self-assessed confidence
)
```

---

## 5. Orchestrator Integration

**File**: `apps/agents/services/orchestrator.py`

### Planner Selection (Constructor)

```python
class AgentOrchestrator:
    def __init__(self):
        if getattr(settings, "AGENT_REASONING_ENGINE_ENABLED", False):
            self.policy = ReasoningPlanner()
        else:
            self.policy = PolicyEngine()
```

The orchestrator treats both planners identically after `plan()` returns. The `plan_source` and `plan_confidence` fields on `AgentPlan` are propagated to:

- `OrchestrationResult.plan_source` / `plan_confidence`
- `AgentOrchestrationRun.plan_source` / `plan_confidence` (DB record)
- First `AgentRun.input_payload` (includes `plan_source`, `plan_confidence`, `planned_agents`)
- Langfuse trace output metadata

### Agent Partitioning

The orchestrator splits the plan into two groups:

1. **LLM agents** -- executed in sequence via the ReAct loop (PO_RETRIEVAL, GRN_RETRIEVAL, INVOICE_UNDERSTANDING, RECONCILIATION_ASSIST)
2. **Deterministic tail** -- replaced by `DeterministicResolver` (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY become SYSTEM_REVIEW_ROUTING, SYSTEM_CASE_SUMMARY)

This means even when the LLM planner includes EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY in its plan, those are still executed deterministically via `DeterministicResolver` -- not as LLM agents. The planner controls which investigation agents run; the routing/summary step is always rule-based.

---

## 6. Post-Plan Pipeline: Reflection + Deterministic Resolution

### Reflection (Always Active)

After each LLM agent completes, the orchestrator's `_reflect()` method may insert additional agents:

- **After PO_RETRIEVAL**: If a PO was found in a 3-way case and GRN_RETRIEVAL is not already planned/executed, insert GRN_RETRIEVAL
- **After INVOICE_UNDERSTANDING**: If confidence < 0.5 and RECONCILIATION_ASSIST is not already planned/executed, insert RECONCILIATION_ASSIST

Reflection is independent of the planner -- it runs the same whether PolicyEngine or ReasoningPlanner generated the plan.

### DeterministicResolver (Always Active for Tail Agents)

The `DeterministicResolver` handles EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY with rule-based logic that maps exception types to recommendation types:

| Exception Pattern | Recommendation |
|------------------|---------------|
| EXTRACTION_LOW_CONFIDENCE | REPROCESS_EXTRACTION |
| VENDOR_MISMATCH | SEND_TO_VENDOR_CLARIFICATION |
| VENDOR_NOT_VERIFIED (Non-PO) | SEND_TO_AP_REVIEW |
| GRN / receipt issues | SEND_TO_PROCUREMENT |
| Complex (3+ types + HIGH severity) | ESCALATE_TO_MANAGER |
| All others | SEND_TO_AP_REVIEW |

---

## 7. Eval Tracking: LLM vs Deterministic Plan Comparison

**File**: `apps/agents/services/eval_adapter.py`

When the ReasoningPlanner is active, the eval adapter records comparison metrics on the pipeline-level EvalRun:

| Metric | Type | Description |
|--------|------|-------------|
| `plan_source` | string | `"llm"` or `"deterministic"` |
| `plan_source_is_llm` | float | `1.0` if LLM, `0.0` if deterministic |
| `plan_confidence` | float | LLM's self-reported confidence (0.0 for deterministic) |
| `planned_agents_count` | float | Number of agents in the original plan |
| `plan_adherence` | float | Fraction of planned agents that were actually executed |

These metrics enable A/B comparison in the eval UI at `/eval/` to determine whether LLM plans produce better outcomes.

---

## 8. Approach: Fully LLM-Dependent Planning (No Deterministic Fallback)

This section outlines how to make the entire agent pipeline fully LLM-driven, removing the deterministic safety net. This is a **significant architectural change** with real operational risk.

### 8.1 What "Fully LLM-Dependent" Means

| Layer | Current State | Fully LLM Target |
|-------|--------------|-------------------|
| **Plan generation** | PolicyEngine (deterministic) or ReasoningPlanner (LLM with deterministic fallback) | LLM planner only -- no PolicyEngine fallback |
| **Auto-close / skip decision** | PolicyEngine rules (tolerance band, confidence threshold) | LLM decides whether to skip or auto-close |
| **Agent selection** | Pre-defined rule table or LLM-selected (from fixed list) | LLM selects from agent catalog, potentially with dynamic agent composition |
| **Tail agents (routing/summary)** | DeterministicResolver (rule-based) | LLM agents (full ReAct loop for EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY) |
| **Post-run policy** | PolicyEngine.should_auto_close/should_escalate | LLM recommends action; optional deterministic guardrails |
| **Reflection** | Hardcoded rules (2 reflection rules) | LLM-based meta-reasoning after each agent |

### 8.2 Phased Approach

#### Phase 1: LLM Planning with Soft Fallback (Current + Improvements)

**Goal**: Keep deterministic safety net but make the LLM plan authoritative when it succeeds.

Changes needed:
1. Enable `AGENT_REASONING_ENGINE_ENABLED=true` in production
2. Enrich the LLM planner's system prompt with:
   - Historical plan outcomes (from eval data)
   - Auto-close eligibility signals (pass tolerance band results to the LLM)
   - Exception severity information
3. Log plan divergence -- when LLM plan differs from PolicyEngine plan, record both in eval metrics
4. Monitor plan quality via Langfuse scores and eval dashboard

#### Phase 2: LLM Planning without Fallback

**Goal**: Remove the PolicyEngine fallback from the planning step. The LLM planner must succeed or the pipeline errors out with a retry.

Changes to `ReasoningPlanner.plan()`:

```python
def plan(self, result) -> AgentPlan:
    # No quick_plan baseline -- LLM decides everything
    try:
        return self._llm_plan(result)
    except Exception as exc:
        # Instead of falling back to PolicyEngine, retry or error
        logger.error(
            "LLM planner failed for result %s (%s); retrying...",
            getattr(result, "pk", "?"), exc,
        )
        # Retry once with increased temperature
        try:
            return self._llm_plan(result, temperature=0.2)
        except Exception:
            raise PlanningError(
                f"LLM planner failed after retry for result {getattr(result, 'pk', '?')}"
            ) from exc
```

Requirements before this phase:
- [ ] LLM planner success rate > 99% (measured via eval metrics)
- [ ] Plan quality score (adherence + outcome) >= deterministic baseline
- [ ] Retry/circuit-breaker mechanism in LLMClient
- [ ] Alert on planning failures (ops threshold)

#### Phase 3: LLM Auto-Close Decision

**Goal**: Let the LLM decide whether a result should be auto-closed, replacing the tolerance band math.

Add to the LLM planner's prompt and response schema:

```json
{
    "overall_reasoning": "...",
    "confidence": 0.95,
    "auto_close": true,
    "auto_close_rationale": "All line discrepancies are within 2% and there are no high-severity exceptions",
    "steps": []
}
```

The LLM would receive:
- All line-level discrepancy data (qty, price, amount deviations)
- Exception list with severity
- Historical auto-close rate for this vendor/category
- Current tolerance thresholds (as reference context, not enforcement)

Guardrail: Even in full LLM mode, keep a **hard ceiling** -- never auto-close if any exception has severity=HIGH or total amount deviation > 10%. This is a safety guardrail, not a deterministic planner.

#### Phase 4: LLM Tail Agents (Replace DeterministicResolver)

**Goal**: Run EXCEPTION_ANALYSIS, REVIEW_ROUTING, and CASE_SUMMARY as full LLM agents instead of the rule-based DeterministicResolver.

Changes:
1. Remove `_SYSTEM_AGENT_REPLACEMENTS` mapping in orchestrator
2. Remove `DeterministicResolver` partitioning -- all agents run through the ReAct loop
3. These agents would use tools to:
   - Query exception details (existing `exception_list` tool)
   - Look up routing rules (new tool needed)
   - Read previous agent summaries from AgentMemory
   - Generate structured case summaries

Trade-offs:
- **Cost**: +2-3 LLM calls per pipeline run (exception analysis + routing + summary)
- **Latency**: +15-45 seconds per pipeline run
- **Quality**: Potentially better for complex multi-exception cases; worse for simple cases where rules are already perfect
- **Auditability**: LLM reasoning is captured in AgentRun.summarized_reasoning, but less predictable than rule tables

#### Phase 5: LLM-Based Reflection

**Goal**: Replace the two hardcoded reflection rules with LLM meta-reasoning.

After each agent completes, call the LLM with:
- The agent's output (reasoning, confidence, recommendation)
- Remaining planned agents
- Current AgentMemory state
- All exceptions (resolved and unresolved)

Ask the LLM: "Should any additional agents be inserted? Should any planned agents be removed? Should the pipeline stop early?"

This requires a lightweight "meta-agent" call with a focused system prompt and the current pipeline state.

### 8.3 Enhanced LLM Planner Prompt (for Phase 2+)

The current prompt only lists agent descriptions and basic rules. A fully LLM-dependent planner needs richer context:

```
You are an expert AP reconciliation pipeline planner. Decide which AI agents
should investigate this reconciliation result and in what order.

AVAILABLE AGENTS:
  PO_RETRIEVAL          - Searches for the correct Purchase Order [...]
  GRN_RETRIEVAL         - Investigates Goods Receipt Notes [...]
  INVOICE_UNDERSTANDING - Re-analyses extracted invoice fields [...]
  RECONCILIATION_ASSIST - Investigates partial-match discrepancies [...]
  EXCEPTION_ANALYSIS    - Performs root-cause analysis [...]
  REVIEW_ROUTING        - Determines the correct review queue [...]
  CASE_SUMMARY          - Produces a concise case summary [...]

RECONCILIATION RESULT:
  match_status: {match_status}
  reconciliation_mode: {recon_mode}
  deterministic_confidence: {det_confidence}
  extraction_confidence: {extraction_confidence}
  exception_types: {exc_types}
  exception_severities: {exc_severities}

LINE-LEVEL DISCREPANCIES:
  {line_discrepancy_summary}

TOLERANCE THRESHOLDS (reference):
  strict: qty={strict_qty}%, price={strict_price}%, amount={strict_amount}%
  auto_close: qty={ac_qty}%, price={ac_price}%, amount={ac_amount}%
  all_within_auto_close_band: {within_band}

HISTORICAL CONTEXT:
  vendor_auto_close_rate: {vendor_ac_rate}
  similar_case_resolution_pattern: {similar_pattern}

DECISION OPTIONS:
  1. skip_agents=true, auto_close=true  -- Auto-close (no agents needed)
  2. skip_agents=true, auto_close=false -- Skip agents (clean match)
  3. steps=[...]                        -- Run these agents in order

RULES:
  1. GRN_RETRIEVAL must never appear when reconciliation_mode is TWO_WAY
  2. CASE_SUMMARY should be last
  3. Use the minimum set of agents needed
  4. Be conservative with auto_close -- only when you are highly confident
  5. Assign each step a unique integer priority starting from 1

Respond ONLY with valid JSON:
{
    "overall_reasoning": "...",
    "confidence": 0.9,
    "auto_close": false,
    "skip_agents": false,
    "steps": [{"agent_type": "...", "rationale": "...", "priority": 1}]
}
```

### 8.4 Circuit Breaker for LLM Planning

In a fully LLM-dependent mode, you need protection against sustained LLM outages:

```python
class LLMPlannerCircuitBreaker:
    """Circuit breaker pattern for the LLM planner.
    
    States:
      CLOSED   -- Normal operation, LLM planner is called
      OPEN     -- LLM planner is bypassed (too many recent failures)
      HALF_OPEN -- Allow one probe request to test recovery
    
    Thresholds:
      failure_threshold: 5 failures in rolling window -> OPEN
      recovery_timeout: 60 seconds -> transition to HALF_OPEN
    """
```

When the circuit breaker is OPEN, the planner could:
- Fall back to PolicyEngine (safest, contradicts "fully LLM" goal)
- Queue the result for retry via Celery delayed task
- Return a minimal plan (EXCEPTION_ANALYSIS + REVIEW_ROUTING + CASE_SUMMARY) as a conservative default

---

## 9. Implementation Checklist for Full LLM Mode

### Pre-requisites (before enabling any phase)

- [ ] **Eval baseline established**: Run 100+ reconciliation results with `AGENT_REASONING_ENGINE_ENABLED=false` and record plan outcomes
- [ ] **LLM plan quality measured**: Run same results with `AGENT_REASONING_ENGINE_ENABLED=true` and compare:
  - Plan divergence rate (how often LLM disagrees with PolicyEngine)
  - Final recommendation accuracy (compared to human review decisions)
  - Pipeline completion rate (success vs error)
- [ ] **Langfuse dashboards built**: Filterable by `plan_source` to compare LLM vs deterministic
- [ ] **Cost model validated**: Average LLM token usage per planning call * expected volume

### Phase 1 (LLM with fallback -- low risk)

- [ ] Set `AGENT_REASONING_ENGINE_ENABLED=true` in production `.env`
- [ ] Monitor `plan_source` distribution in Langfuse (expect most plans to be "llm")
- [ ] Track fallback rate (plan_source="deterministic" when ReasoningPlanner is active means LLM failed)
- [ ] Run for 2+ weeks before proceeding

### Phase 2 (LLM without fallback -- medium risk)

- [ ] Implement retry logic in `_llm_plan()`
- [ ] Add circuit breaker with monitoring
- [ ] Create `PlanningError` exception class
- [ ] Update orchestrator to handle `PlanningError` (retry task, don't silently skip)
- [ ] Add ops alert for planning failure rate > 1%

### Phase 3 (LLM auto-close -- high risk)

- [ ] Expand LLM prompt with line-level discrepancy data
- [ ] Add `auto_close` and `skip_agents` to LLM response schema
- [ ] Implement hard-ceiling guardrails (HIGH severity, amount deviation)
- [ ] Shadow-mode first: run LLM auto-close decision alongside deterministic, compare results, do NOT act on LLM's decision
- [ ] Gradual rollout: start with specific vendors/categories where auto-close accuracy is already high

### Phase 4 (LLM tail agents -- medium risk, high cost)

- [ ] Create full LLM-based ExceptionAnalysisAgent (already exists: `AgentType.EXCEPTION_ANALYSIS`)
- [ ] Create full LLM-based ReviewRoutingAgent (already exists: `AgentType.REVIEW_ROUTING`)
- [ ] Keep CaseSummaryAgent as LLM (already exists: `AgentType.CASE_SUMMARY`)
- [ ] Remove `DeterministicResolver` from orchestrator
- [ ] Remove `_SYSTEM_AGENT_REPLACEMENTS` mapping
- [ ] Monitor cost increase and latency impact

### Phase 5 (LLM reflection -- medium risk)

- [ ] Create `ReflectionAgent` or lightweight meta-call
- [ ] Replace hardcoded `_reflect()` rules with LLM call
- [ ] Maintain the same reflection insertion mechanism (returns list of agent_type strings)

---

## 10. Risk Matrix and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| LLM outage stops all reconciliation | HIGH | LOW | Circuit breaker + Celery retry + optional PolicyEngine emergency fallback |
| LLM plans wrong agents (wastes time/cost) | MEDIUM | MEDIUM | Validation rules (current: GRN in TWO_WAY, CASE_SUMMARY position) + eval monitoring |
| LLM auto-closes a result that should go to review | HIGH | LOW | Hard-ceiling guardrails (HIGH severity, amount threshold) + shadow mode rollout |
| LLM cost increase (3-5x more tokens per pipeline) | MEDIUM | HIGH | Phase gradually; measure cost-per-invoice; set budget caps |
| LLM latency increase (+15-45s per pipeline) | LOW | HIGH | Acceptable for async Celery pipeline; consider parallel agent execution for independent agents |
| LLM hallucinates non-existent agent types | LOW | LOW | Already handled: validation drops unknown agent types |
| LLM ignores mode constraints (GRN in TWO_WAY) | MEDIUM | LOW | Already handled: post-parse validation raises ValueError |

---

## 11. Key Files

| File | Purpose |
|------|---------|
| `apps/agents/services/reasoning_planner.py` | LLM-enhanced planner (ReasoningPlanner class) |
| `apps/agents/services/policy_engine.py` | Deterministic planner (PolicyEngine class) + AgentPlan dataclass |
| `apps/agents/services/orchestrator.py` | Agent pipeline orchestration (planner selection, execution, reflection) |
| `apps/agents/services/llm_client.py` | LLM client (Azure OpenAI / OpenAI chat completion) |
| `apps/agents/services/deterministic_resolver.py` | Rule-based tail agent replacement |
| `apps/agents/services/agent_memory.py` | Cross-agent structured memory |
| `apps/agents/services/eval_adapter.py` | Eval tracking for plan source comparison |
| `apps/agents/tests/test_reasoning_planner.py` | 17 tests — see [TEST_DOCUMENTATION.md §4.4.7](TEST_DOCUMENTATION.md) |
| `config/settings.py` | `AGENT_REASONING_ENGINE_ENABLED` setting (line 274) |
| `apps/core/enums.py` | `AgentType` enum (valid agent type values) |

---

## Appendix: Evaluation & Learning Architecture

> Reference for the eval/learning framework — apps/core_eval/.

# Evaluation & Learning Architecture

> Full reference: `apps/core_eval/` (models, services, management commands, tests)

## 1. Overview

The Evaluation & Learning layer is a **domain-agnostic framework** that sits alongside the production pipelines (extraction, reconciliation, posting, agents) without modifying them. Its purpose:

1. **Track pipeline quality** -- capture metrics, field-level outcomes, and confidence scores for every pipeline run.
2. **Collect learning signals** -- record human corrections, approval outcomes, validation failures, and auto-approve events as structured signals.
3. **Detect patterns** -- a deterministic rule engine scans accumulated signals and proposes corrective actions.
4. **Surface actions for human review** -- proposed actions (prompt edits, threshold adjustments, normalization rules) are never auto-applied; they require explicit human approval.

The layer is fail-silent: errors in eval/learning code never propagate to the calling pipeline.

---

## 2. Architecture Diagram

```
Production Pipelines          Eval / Learning Layer
=====================         ===============================

 Extraction Task               EvalRun
   |                             +-- EvalMetric (N)
   |-- ExtractionEvalAdapter --> +-- EvalFieldOutcome (N)
   |                             +-- LearningSignal (N)
   v
 Approval Service                    |
   |                                 v
   |-- ExtractionEvalAdapter --> LearningSignal (additional)
   |
   v
 Reconciliation Runner               |
   |                                 v
   |-- ReconEvalAdapter -------> EvalRun + Metrics + Signals
   |
   v
 Review Service                      |
   |                                 v
   |-- ReconEvalAdapter -------> FieldOutcomes + Signals
   |                                 |
   v                                 v
 [Future: Posting / Agent       LearningEngine (deterministic)
   adapters]                         |
                                     v
                                LearningAction (PROPOSED)
                                     |
                                     v
                                Human Review (approve/reject)
                                     |
                                     v
                                LearningAction (APPLIED / REJECTED)
```

Key design constraint: **the arrow from Production to Eval is one-way**. The eval layer reads production data and writes to its own tables. It never writes back to production models or alters pipeline behavior.

---

## 3. Data Model

All models inherit `TimestampMixin` (not `BaseModel`) and include a `tenant_id` field for multi-tenant isolation.

### 3.1 EvalRun

One evaluation pass against any entity (e.g. one `ExtractionResult`, one `ReconciliationResult`).

| Field | Type | Purpose |
|---|---|---|
| `app_module` | CharField(120) | Originating module (e.g. `"extraction"`, `"reconciliation"`) |
| `entity_type` | CharField(120) | Model name (e.g. `"ExtractionResult"`) |
| `entity_id` | CharField(255) | PK of evaluated entity |
| `run_key` | CharField(255) | Distinguishes retries / versions (e.g. `"extraction-42"`) |
| `prompt_hash` | CharField(64) | SHA-256 of prompt template used |
| `prompt_slug` | CharField(200) | PromptTemplate slug for lineage |
| `status` | CharField(20) | `CREATED` / `PENDING` / `RUNNING` / `COMPLETED` / `FAILED` |
| `trace_id` | CharField(255) | Distributed trace correlation (Langfuse / OTel) |
| `triggered_by` | FK(User) | Who triggered the pipeline run |
| `config_json` | JSONField | Run-level configuration snapshot |
| `input_snapshot_json` | JSONField | Inputs evaluated (invoice_id, confidence, etc.) |
| `result_json` | JSONField | Aggregated results |
| `error_json` | JSONField | Error details on failure |
| `started_at` / `completed_at` | DateTimeField | Timing |
| `duration_ms` | PositiveIntegerField | Duration in milliseconds |

**Indexes**: `app_module`, `(entity_type, entity_id)`, `prompt_hash`, `created_at`, `(app_module, entity_type, entity_id, run_key)`, `tenant_id`.

### 3.2 EvalMetric

Named numeric, text, or JSON metric attached to an `EvalRun`.

| Field | Type | Purpose |
|---|---|---|
| `eval_run` | FK(EvalRun) | Parent run (nullable for standalone metrics) |
| `metric_name` | CharField(200) | e.g. `"extraction_confidence"`, `"decision_code_count"` |
| `metric_value` | FloatField | Numeric value (nullable) |
| `string_value` | TextField | Text value |
| `json_value` | JSONField | Structured value (e.g. list of decision codes) |
| `unit` | CharField(50) | e.g. `"ratio"`, `"count"`, `"seconds"` |
| `dimension_json` | JSONField | Arbitrary slicing dimensions |
| `metadata_json` | JSONField | Extra context |

**Validation**: At most one of `metric_value`, `string_value`, `json_value` is populated per record.

### 3.3 EvalFieldOutcome

Per-field predicted-vs-ground-truth outcome for an `EvalRun`.

| Field | Type | Purpose |
|---|---|---|
| `eval_run` | FK(EvalRun) | |
| `field_name` | CharField(200) | e.g. `"invoice_number"`, `"total_amount"` |
| `status` | CharField(20) | `CORRECT` / `INCORRECT` / `MISSING` / `EXTRA` / `SKIPPED` |
| `predicted_value` | TextField | Final LLM-extracted value (pipeline output persisted to Invoice) |
| `ground_truth_value` | TextField | Empty at extraction; populated on human approval (corrected value or confirmed prediction) |
| `confidence` | FloatField | Per-field confidence 0.0--1.0 (LLM confidence when source is LLM) |
| `detail_json` | JSONField | Source provenance: `source` (llm/deterministic), `deterministic_value`, `deterministic_confidence`, `category` |

**Lifecycle**:

1. **At extraction time**: `predicted_value` = LLM value from `raw_response` (deterministic fallback only when LLM has no value). `ground_truth_value` = empty. `status` = CORRECT (has predicted) or MISSING (no predicted).
2. **On human correction**: `ground_truth_value` = corrected value, `status` = INCORRECT (via `_update_field_outcomes_from_corrections`).
3. **On approval confirmation**: Non-corrected fields get `ground_truth_value` = `predicted_value`, `status` = CORRECT (via `_confirm_ground_truth_on_approval`).

### 3.4 LearningSignal

An atomic observation from production that may feed pattern detection later.

| Field | Type | Purpose |
|---|---|---|
| `app_module` | CharField(120) | Originating module |
| `signal_type` | CharField(120) | Category (see Signal Types below) |
| `entity_type` | CharField(120) | e.g. `"Invoice"` |
| `entity_id` | CharField(255) | PK of related entity |
| `aggregation_key` | CharField(255) | Grouping key for pattern detection |
| `confidence` | FloatField | Signal strength 0.0--1.0 |
| `actor` | FK(User) | Who produced the signal |
| `field_name` | CharField(200) | For field-level signals |
| `old_value` / `new_value` | TextField | Before / after (for corrections) |
| `payload_json` | JSONField | Full signal payload |
| `eval_run` | FK(EvalRun) | Optional link to parent eval run |

**Signal types emitted today**:

| Signal Type | Source | When |
|---|---|---|
| `field_correction` | `ExtractionEvalAdapter.sync_for_approval()` | Human corrects a field during approval |
| `approval_outcome` | `ExtractionEvalAdapter.sync_for_approval()` | Human approves or rejects an extraction |
| `auto_approve_outcome` | `ExtractionEvalAdapter.sync_for_approval()` | System auto-approves an extraction |
| `validation_failure` | `ExtractionEvalAdapter.sync_for_extraction_result()` | Extraction validation fails |
| `review_override` | `ExtractionEvalAdapter.sync_for_approval()` | Human approves with corrections (non-touchless) |
| `prompt_review_candidate` | `ExtractionEvalAdapter.sync_for_extraction_result()` | Decision codes suggest prompt issues |

### 3.5 LearningAction

A proposed corrective action generated by the `LearningEngine`.

| Field | Type | Purpose |
|---|---|---|
| `action_type` | CharField(120) | Category (see Action Types below) |
| `status` | CharField(20) | `PROPOSED` / `APPROVED` / `APPLIED` / `REJECTED` / `FAILED` |
| `app_module` | CharField(120) | Target module |
| `target_description` | TextField | Human-readable target + dedup tag |
| `rationale` | TextField | Why the engine proposed this |
| `input_signals_json` | JSONField | Summary of triggering signals |
| `action_payload_json` | JSONField | The action details (field, examples, fix) |
| `result_json` | JSONField | Outcome after application |
| `proposed_by` / `approved_by` | FK(User) | Provenance |
| `applied_at` | DateTimeField | When applied |

**Action types proposed today**:

| Action Type | Trigger Rule | Meaning |
|---|---|---|
| `field_normalization_candidate` | Field Correction Hotspot | A field is corrected >= 20 times; consider adding a normalization rule |
| `prompt_review` | Prompt Weakness | A prompt has >= 30% correction rate across runs; review the prompt |
| `threshold_tune` | Auto-Approve Risk | Auto-approved items are frequently corrected; raise threshold |
| `validation_rule_candidate` | Validation Failure Cluster | Same validation error repeats >= 10 times; add a rule |
| `vendor_rule_candidate` | Vendor-Specific Issue | Corrections cluster around a vendor; add vendor-specific mapping |

---

## 4. Service Layer

### 4.1 Low-level CRUD Services

These services provide the persistence API. All methods are stateless.

| Service | Location | Public Methods |
|---|---|---|
| `EvalRunService` | `apps/core_eval/services/eval_run_service.py` | `create()`, `create_or_update()`, `mark_running()`, `mark_completed()`, `mark_failed()`, `get_by_entity()`, `get_latest()` |
| `EvalMetricService` | `apps/core_eval/services/eval_metric_service.py` | `record()`, `upsert()`, `list_for_run()`, `list_by_name()` |
| `EvalFieldOutcomeService` | `apps/core_eval/services/eval_field_outcome_service.py` | `record()`, `bulk_record()`, `replace_for_run()`, `list_for_run()`, `summary_for_run()` |
| `LearningSignalService` | `apps/core_eval/services/learning_signal_service.py` | `record()`, `list_by_entity()`, `list_by_module()`, `count_by_field()` |
| `LearningActionService` | `apps/core_eval/services/learning_action_service.py` | `propose()`, `approve()`, `mark_applied()`, `mark_rejected()`, `mark_failed()`, `list_by_status()`, `list_by_type()` |

### 4.2 LearningEngine

**Location**: `apps/core_eval/services/learning_engine.py`

The `LearningEngine` is a deterministic, rule-based engine that scans `LearningSignal` records within a configurable time window and proposes `LearningAction` records.

```python
engine = LearningEngine(days=7, min_confidence=0.0, cooldown_days=3)
summary = engine.run()                    # all modules
summary = engine.run(module="extraction") # single module
summary = engine.run(dry_run=True)        # preview only, no DB writes
```

#### Configuration

| Parameter | Default | Purpose |
|---|---|---|
| `days` | 7 | Time window for signal scanning |
| `min_confidence` | 0.0 | Minimum signal confidence to include |
| `cooldown_days` | 3 | Skip proposing if identical action exists within this period |

#### Rule Thresholds

| Constant | Default | Rule |
|---|---|---|
| `FIELD_CORRECTION_MIN_COUNT` | 20 | Min corrections to trigger field hotspot |
| `PROMPT_WEAKNESS_MIN_CORRECTIONS` | 10 | Min corrections to evaluate a prompt |
| `PROMPT_WEAKNESS_CORRECTION_RATE` | 0.30 | Correction rate threshold (30%) |
| `AUTO_APPROVE_RISK_MIN_COUNT` | 5 | Min risky auto-approvals to trigger |
| `VALIDATION_CLUSTER_MIN_COUNT` | 10 | Min identical validation errors to trigger |
| `VENDOR_ISSUE_MIN_COUNT` | 10 | Min vendor-specific corrections to trigger |

#### Safety Controls

1. **Dedup** -- A `[dedup_key:...]` tag in `target_description` prevents duplicate open (PROPOSED/APPROVED) actions for the same pattern.
2. **Cooldown** -- If an action with the same dedup_key was created within `cooldown_days`, it is skipped.
3. **Dry-run** -- `run(dry_run=True)` detects patterns and populates the summary but writes nothing.
4. **Idempotency** -- Running the engine twice on the same data produces no duplicates.

#### Aggregation Helpers

Public methods for ad-hoc signal analysis:

| Method | Returns |
|---|---|
| `aggregate_signals_by_key(aggregation_key)` | Count, unique entities, avg confidence, samples |
| `aggregate_signals_by_field(field_code)` | Count, avg confidence, top corrected values |
| `aggregate_signals_by_module(module_name)` | Total count, breakdown by signal_type |
| `aggregate_signals_by_prompt(prompt_hash)` | Count, avg confidence, breakdown by signal_type |

---

## 5. Adapters (Pipeline Integration)

Adapters are the bridge between production pipelines and the eval layer. Each adapter is a fail-silent class with `@classmethod` methods. Errors are logged but never propagated.

### 5.1 ExtractionEvalAdapter

**Location**: `apps/extraction/services/eval_adapter.py`

Wired into two places:

1. **`apps/extraction/tasks.py`** (after extraction persistence) calls `sync_for_extraction_result()` which creates:
   - `EvalRun` (one per extraction, upserted by `run_key`)
   - `EvalMetric` records (extraction_success, extraction_confidence, is_valid, is_duplicate, weakest_critical_field_score, decision_code_count, etc.)
   - `EvalFieldOutcome` records: predicted = LLM value from `raw_response` (deterministic fallback only when LLM has no value); ground truth = empty (populated later during approval)
   - `LearningSignal` records for validation failures and prompt review candidates

2. **`apps/extraction/services/approval_service.py`** (after approve / reject / auto-approve) calls `sync_for_approval()` which creates:
   - `LearningSignal` for `approval_outcome` or `auto_approve_outcome`
   - `LearningSignal` for each `field_correction` (from `ExtractionFieldCorrection` records)
   - `LearningSignal` for `review_override` (non-touchless approval with corrections)
   - `EvalMetric` updates on the parent extraction `EvalRun` (corrections_count, approval_decision, approval_confidence)
   - Ground truth confirmation: for non-corrected fields, `ground_truth_value` = `predicted_value` and `status` = CORRECT (via `_confirm_ground_truth_on_approval`)
   - For corrected fields, `ground_truth_value` = corrected value and `status` = INCORRECT (via `_update_field_outcomes_from_corrections`)

### 5.2 ReconciliationEvalAdapter (Implemented)

**File**: `apps/reconciliation/services/eval_adapter.py`

Bridges the reconciliation engine and review workflow into the eval layer. Wired at three lifecycle points:

1. **`ReconciliationRunnerService._reconcile_single()`** (after result persistence) -- calls `sync_for_result()`:
   - Creates/updates `EvalRun` (entity_type `reconciliation`, entity_id = result PK)
   - Stores predicted metrics: match_status, requires_review, auto_close_eligible, po_found, grn_found
   - Stores runtime metrics: exception_count, line_count, confidence, duration_ms, reconciliation_mode
   - Emits `LearningSignal` for match_outcome, mode_resolution, exception_pattern, auto_close_decision
   - Creates `EvalFieldOutcome` records for match_status, review_routing, auto_close, po_found, grn_found (predicted only; ground truth populated on review)

2. **`ReviewService.create_assignment()`** (after assignment creation) -- calls `sync_for_review_assignment()`:
   - Emits `review_created` learning signal with reviewer, priority, queue metadata

3. **`ReviewService._finalise()`** (after review decision) -- calls `sync_for_review_outcome()`:
   - Stores actual metrics: actual_match_status, review_decision, corrections_count
   - Emits learning signals: review_outcome, match_override (when reviewer changed match status), correction (per field)
   - Updates `EvalFieldOutcome` ground truth values from review decision

### 5.3 Future Adapters (Not Yet Implemented)

| Adapter | Pipeline | Signals to Capture |
|---|---|---|
| `PostingEvalAdapter` | Posting pipeline | Mapping accuracy, review queue frequency, confidence calibration |
| `AgentEvalAdapter` | Agent orchestrator | Recommendation acceptance rate, tool call success rate, confidence calibration |

---

## 6. Management Command

```bash
# Run with defaults (7-day window, all modules)
python manage.py run_learning_engine

# Restrict to extraction module, 14-day window
python manage.py run_learning_engine --module extraction --days 14

# Preview only (no DB writes)
python manage.py run_learning_engine --dry-run

# With minimum confidence filter and custom cooldown
python manage.py run_learning_engine --min-confidence 0.5 --cooldown-days 7
```

Output:
```
LearningEngine run complete:
  signals scanned   = 142
  rules evaluated   = 5
  actions proposed  = 3
  skipped (dedup)   = 1
  skipped (cooldown)= 0
  -> PROPOSED: field_normalization_candidate / ... -> LearningAction#12
  -> PROPOSED: prompt_review / ... -> LearningAction#13
  -> PROPOSED: threshold_tune / ... -> LearningAction#14
  -> DEDUP: vendor_rule_candidate / ...
```

---

## 7. Testing

> Full eval & learning test documentation (6 test files, ~120 tests) has been consolidated into [TEST_DOCUMENTATION.md §4.11](TEST_DOCUMENTATION.md).

---

## 8. Data Flow Example

Complete flow for one invoice extraction with a field correction:

```
1. Upload + OCR + LLM extraction
   -> ExtractionResult saved (raw_response contains LLM-extracted values)
   -> Governed pipeline runs deterministic extraction (ExtractionFieldValue records)

2. tasks.py calls ExtractionEvalAdapter.sync_for_extraction_result()
   -> EvalRun created (app_module="extraction", entity_type="ExtractionResult")
   -> EvalMetric records: extraction_confidence=0.88, is_valid=1.0, ...
   -> EvalFieldOutcome records:
        invoice_number: predicted="INV-001" (from LLM), ground_truth="" (empty), status=CORRECT, conf=0.95
        total_amount:   predicted="1,000.00" (from LLM), ground_truth="" (empty), status=CORRECT, conf=0.80
        buyer_name:     predicted="Acme Corp" (from LLM), ground_truth="" (empty), status=CORRECT, conf=1.0
      detail_json per field: {source: "llm", deterministic_value: "...", deterministic_confidence: 0.0}

3. Human reviews extraction, corrects total_amount: "1,000.00" -> "1000.00"
   -> ExtractionApproval.status = APPROVED
   -> ExtractionFieldCorrection saved

4. approval_service.py calls ExtractionEvalAdapter.sync_for_approval()
   -> LearningSignal (signal_type="approval_outcome", entity_id=invoice.pk)
   -> LearningSignal (signal_type="field_correction", field_name="total_amount",
                       old_value="1,000.00", new_value="1000.00")
   -> LearningSignal (signal_type="review_override", fields_corrected=1)
   -> EvalFieldOutcome for total_amount updated: status=INCORRECT, ground_truth="1000.00"
   -> Ground truth confirmation for non-corrected fields:
        invoice_number: ground_truth="INV-001" (= predicted), status=CORRECT
        buyer_name:     ground_truth="Acme Corp" (= predicted), status=CORRECT
   -> EvalMetric: extraction_corrections_count=1

5. After N similar corrections accumulate, operator runs:
   $ python manage.py run_learning_engine --module extraction

6. LearningEngine scans LearningSignal records in the last 7 days
   -> Rule 1 (field_correction_hotspot): total_amount corrected 25 times
   -> LearningAction proposed:
        action_type = "field_normalization_candidate"
        target_description = "Field 'total_amount' corrected 25 times ..."
        action_payload_json = {
            "field_code": "total_amount",
            "issue": "frequent formatting corrections",
            "top_corrected_values": [{"value": "1000.00", "count": 18}],
            "suggested_fix": "normalize format before validation"
        }

7. Human reviews LearningAction in admin, approves, then applies fix
   -> LearningAction.status transitions: PROPOSED -> APPROVED -> APPLIED
```

---

## 9. Design Decisions

| Decision | Rationale |
|---|---|
| Domain-agnostic models | `core_eval` has no FK to Invoice, PO, or ExtractionResult. Adapters bridge the gap. Any module can emit signals. |
| Fail-silent adapters | Eval data is secondary. A bug in the adapter must never break extraction/approval. |
| `TimestampMixin` only (not `BaseModel`) | Eval tables are lightweight analytics data, not business entities. No soft-delete needed. |
| No auto-apply | All `LearningAction` proposals require human approval. The engine never modifies prompts, thresholds, or normalization rules on its own. |
| Dedup via `target_description` tag | Uses `[dedup_key:...]` text tag instead of JSONField `__contains` for SQLite compatibility in tests. |
| Cooldown period | Prevents re-proposing the same action immediately after rejection. Default 3 days. |
| Tenant isolation | `tenant_id` FK on all models provides row-level multi-tenant isolation via `CompanyProfile`. See [MULTI_TENANT.md](MULTI_TENANT.md). |

---

## 10. Adding a New Adapter

To integrate a new pipeline (e.g. reconciliation) with the eval layer:

1. Create `apps/<module>/services/eval_adapter.py` with a class following the `ExtractionEvalAdapter` pattern.
2. Define signal type constants (e.g. `SIG_MATCH_OUTCOME = "match_outcome"`).
3. Call `EvalRunService.create_or_update()` to upsert an `EvalRun` per pipeline execution.
4. Call `EvalMetricService.upsert()` for each numeric metric.
5. Call `LearningSignalService.record()` for each observable event.
6. Wire the adapter call into the pipeline task/service (in a `try/except` block).
7. Optionally add new rules to `LearningEngine` if new signal types warrant pattern detection.
8. Add tests: adapter unit tests + end-to-end tests with the engine.

---

## 11. File Inventory

| File | Purpose |
|---|---|
| `apps/core_eval/models.py` | 5 models: EvalRun, EvalMetric, EvalFieldOutcome, LearningSignal, LearningAction |
| `apps/core_eval/apps.py` | Django AppConfig |
| `apps/core_eval/admin.py` | Admin registration |
| `apps/core_eval/services/eval_run_service.py` | EvalRun CRUD |
| `apps/core_eval/services/eval_metric_service.py` | EvalMetric CRUD + upsert |
| `apps/core_eval/services/eval_field_outcome_service.py` | EvalFieldOutcome CRUD + replace_for_run |
| `apps/core_eval/services/learning_signal_service.py` | LearningSignal CRUD |
| `apps/core_eval/services/learning_action_service.py` | LearningAction lifecycle (propose/approve/apply/reject) |
| `apps/core_eval/services/learning_engine.py` | Deterministic rule engine (5 rules, aggregation helpers) |
| `apps/core_eval/management/commands/run_learning_engine.py` | Management command |
| `apps/core_eval/tests/test_learning_engine.py` | 22 unit tests — see [TEST_DOCUMENTATION.md §4.11.1](TEST_DOCUMENTATION.md) |
| `apps/core_eval/tests/test_end_to_end.py` | 13 end-to-end tests — see [TEST_DOCUMENTATION.md §4.11.4](TEST_DOCUMENTATION.md) |
| `apps/core_eval/tests/test_views.py` | 29 RBAC view tests — see [TEST_DOCUMENTATION.md §4.11.5](TEST_DOCUMENTATION.md) |
| `apps/core_eval/template_views.py` | 5 FBV views: eval_run_list, eval_run_detail, learning_signal_list, learning_action_list, learning_action_detail |
| `apps/core_eval/urls.py` | URL routes (app_name="core_eval"), mounted at `/eval/` |
| `templates/core_eval/eval_run_list.html` | Eval runs list with KPI cards, filters, pagination |
| `templates/core_eval/eval_run_detail.html` | Eval run detail with metrics, field outcomes, signals |
| `templates/core_eval/learning_signal_list.html` | Learning signals list with filters |
| `templates/core_eval/learning_action_list.html` | Learning actions list with KPI cards, filters |
| `templates/core_eval/learning_action_detail.html` | Learning action detail with JSON payloads |
| `apps/extraction/services/eval_adapter.py` | ExtractionEvalAdapter (extraction <-> core_eval bridge) |
| `apps/extraction/tests/test_eval_adapter.py` | 10 adapter unit tests — see [TEST_DOCUMENTATION.md §4.11.2](TEST_DOCUMENTATION.md) |
| `apps/extraction/tests/test_approval_integration.py` | 25 eval integration tests — see [TEST_DOCUMENTATION.md §4.11.3](TEST_DOCUMENTATION.md) |
| `apps/reconciliation/services/eval_adapter.py` | ReconciliationEvalAdapter (reconciliation <-> core_eval bridge) |
| `apps/reconciliation/tests/test_recon_eval_adapter.py` | 21 adapter tests — see [TEST_DOCUMENTATION.md §4.11.6](TEST_DOCUMENTATION.md) |

---

## 12. RBAC & Permissions

### Permissions

| Code | Module | Action | Description |
|---|---|---|---|
| `eval.view` | eval | view | View eval runs, learning signals, and learning actions |
| `eval.manage` | eval | manage | Approve, reject, or apply learning actions |

### Role Grants

| Role | `eval.view` | `eval.manage` |
|---|---|---|
| ADMIN | Yes | Yes |
| FINANCE_MANAGER | Yes | Yes |
| REVIEWER | Yes | -- |
| AUDITOR | Yes | -- |
| AP_PROCESSOR | -- | -- |
| SYSTEM_AGENT | -- | -- |

Permissions are seeded via `python manage.py seed_rbac`. All 5 template views require `eval.view`. The sidebar "Eval & Learning" section is gated by `{% has_permission "eval.view" %}`.

---

## 13. Audit Events

Six `AuditEventType` values track eval & learning lifecycle events:

| Event Type | Fired By | When |
|---|---|---|
| `LEARNING_ENGINE_RUN` | `LearningEngine.run()` | After each engine execution (includes signals_scanned, rules_evaluated, actions_proposed counts) |
| `LEARNING_ACTION_PROPOSED` | `LearningEngine._propose_action()` | When a new LearningAction is created from a detected pattern |
| `LEARNING_ACTION_APPROVED` | `LearningActionService.approve()` | When a human approves a proposed action |
| `LEARNING_ACTION_REJECTED` | `LearningActionService.mark_rejected()` | When a human rejects a proposed action |
| `LEARNING_ACTION_APPLIED` | `LearningActionService.mark_applied()` | When an approved action is applied to the system |
| `LEARNING_ACTION_FAILED` | `LearningActionService.mark_failed()` | When an approved action fails during application |

All audit calls are fail-silent (wrapped in `try/except`) -- audit logging errors never block the calling operation. Each event includes `status_before`/`status_after` metadata for action lifecycle transitions.

---

## 14. UI Views

Five template views provide a browsable interface for eval & learning data, mounted at `/eval/`.

| URL | View | Template | Description |
|---|---|---|---|
| `/eval/` | `eval_run_list` | `core_eval/eval_run_list.html` | List with KPI cards (total/completed/failed), filters (module, status, entity_type), pagination |
| `/eval/runs/<pk>/` | `eval_run_detail` | `core_eval/eval_run_detail.html` | Detail with metrics, field outcomes, linked signals |
| `/eval/signals/` | `learning_signal_list` | `core_eval/learning_signal_list.html` | List with filters (module, signal_type, field_name) |
| `/eval/actions/` | `learning_action_list` | `core_eval/learning_action_list.html` | List with KPI cards (proposed/approved/applied/rejected), filters |
| `/eval/actions/<pk>/` | `learning_action_detail` | `core_eval/learning_action_detail.html` | Full detail with target, rationale, JSON payloads |

All views use `@login_required` + `@permission_required_code("eval.view")`. Sidebar navigation is under "Eval & Learning" with 3 items: Eval Runs, Learning Signals, Learning Actions.

---

## Appendix: AP Finance Agents — Functional Test Document

> This appendix has been moved to the consolidated test documentation.
> Full content: [TEST_DOCUMENTATION.md §10](TEST_DOCUMENTATION.md) — AP Finance Agents Manual Functional Test Guide (TC-AP-001 through TC-AP-015, sample test data, tester cheat sheet, execution order, evidence checklist, exit criteria).

