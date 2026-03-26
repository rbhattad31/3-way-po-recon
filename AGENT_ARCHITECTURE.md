# Agent Architecture — Developer Guide

> Covers the complete agentic layer of the 3-Way PO Reconciliation Platform:
> how agents are structured, how the pipeline executes, how the deterministic
> and LLM layers interact, a concrete upgrade path to a full reasoning
> engine without breaking the existing flow, best-practice upgrades for each
> existing agent, and open source tools for agent observability.

---

## Table of Contents

1. [Big Picture](#1-big-picture)
2. [Core Concepts and Data Structures](#2-core-concepts-and-data-structures)
3. [Component Inventory](#3-component-inventory)
4. [Agent Definitions (Database-Backed Config)](#4-agent-definitions-database-backed-config)
5. [The Tool System](#5-the-tool-system)
6. [The LLM Client](#6-the-llm-client)
7. [BaseAgent and the ReAct Loop](#7-baseagent-and-the-react-loop)
8. [Concrete Agent Implementations](#8-concrete-agent-implementations)
9. [The PolicyEngine](#9-the-policyengine)
10. [The DeterministicResolver](#10-the-deterministicresolver)
11. [The AgentOrchestrator](#11-the-agentorchestrator)
12. [RBAC Guardrails](#12-rbac-guardrails)
13. [Agent Feedback Loop](#13-agent-feedback-loop)
14. [Observability and Governance](#14-observability-and-governance)
15. [How the Pipeline Is Triggered](#15-how-the-pipeline-is-triggered)
16. [Upgrade Path: Reasoning Engine](#16-upgrade-path-reasoning-engine)
17. [Upgrading Existing Agents to Best Agentic Practices](#17-upgrading-existing-agents-to-best-agentic-practices)
18. [Open Source Tools for Agent Performance and Inter-Agent Tracing](#18-open-source-tools-for-agent-performance-and-inter-agent-tracing)
    - 18.1 Open Source vs. SaaS Clarification
    - 18.2 Recommended Combination for This Stack
    - 18.3 Coverage Matrix
    - 18.4 Langfuse
    - 18.5 Phoenix / Arize
    - 18.6 OpenLLMetry / openinference
    - 18.7 Weave / W&B (Not Recommended)
    - 18.8 What the Internal Platform Already Covers
    - 18.9 Windows Compatibility for All Tools
    - 18.10 Recommended Integration Sequence

---

## 1. Big Picture

The agentic layer sits **after** the deterministic reconciliation engine. It
is invoked only when the matching result is not a clean MATCHED (or an
auto-closeable PARTIAL_MATCH). Its job is to:

1. Understand *why* a match failed.
2. Attempt to recover missing data (PO, GRN).
3. Classify exceptions and decide where to route the case.
4. Produce a human-readable summary for the reviewer.

```
ReconciliationResult (PARTIAL_MATCH / UNMATCHED / REQUIRES_REVIEW)
        |
        v
  PolicyEngine.plan()               <-- deterministic: no LLM
        |
        +--> skip_agents=True       --> auto-close by tolerance band (no AI)
        |
        +--> agents=[...] list
                |
                v
  AgentOrchestrator.execute()
        |
        +--> llm_agents (PO_RETRIEVAL, GRN_RETRIEVAL, INVOICE_UNDERSTANDING,
        |                RECONCILIATION_ASSIST)
        |        |
        |        v
        |    BaseAgent.run()         <-- ReAct loop (LLM + tools)
        |        |
        |        +--> feedback loop if PO found --> re-reconcile
        |
        +--> deterministic_tail (EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
                 |
                 v
             DeterministicResolver.resolve()  <-- rule-based, no LLM
                 |
                 v
             synthetic AgentRun records (same schema as LLM runs)
        |
        v
  _apply_post_policies()
        +--> should_auto_close()    --> MATCHED (no human needed)
        +--> should_escalate()      --> AgentEscalation record
```

**Key design choice:** the pipeline runs the LLM only for agents that
genuinely need reasoning (retrieval, understanding, partial-match assist).
The final classification, routing, and summary steps are handled by a
rule-based `DeterministicResolver` that writes identical `AgentRun` records
so governance/audit is indistinguishable from the LLM path.

---

## 2. Core Concepts and Data Structures

### AgentContext

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

The `memory` field carries an `AgentMemory` instance created by the orchestrator
at the start of each pipeline run. It accumulates findings (resolved PO, GRN
numbers, agent reasoning summaries, running recommendation) so that later agents
have structured access to what earlier agents discovered -- replacing the
previous plain-string `ctx.extra["prior_reasoning"]` approach.
Both mechanisms are kept in parallel for backward compatibility.

### AgentOutput

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

### AgentPlan

What `PolicyEngine.plan()` returns.

```python
@dataclass
class AgentPlan:
    agents: List[str]          # ordered AgentType values to run
    reason: str                # human-readable explanation
    skip_agents: bool          # True -> skip all agents
    auto_close: bool           # True -> auto-close the result
    reconciliation_mode: str   # propagated to context
    plan_source: str = "deterministic"  # "deterministic" or "llm"
    plan_confidence: float = 0.0        # planner self-reported confidence (0-1)
```

### OrchestrationResult

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
    plan_source: str = ""    # which planner produced the plan ("deterministic" or "llm")
    plan_confidence: float = 0.0  # planner self-reported confidence; 0.0 for deterministic
```

---

## 3. Component Inventory

| Component | File | Role |
|---|---|---|
| `AgentContext` / `AgentOutput` | `base_agent.py` | Data contracts |
| `AgentMemory` | `agent_memory.py` | Structured cross-agent findings store |
| `BaseAgent` | `base_agent.py` | ReAct loop, message persistence, `_sanitise_text()`, `_call_llm_with_retry()` |
| `LLMClient` | `llm_client.py` | Azure OpenAI / OpenAI wrapper |
| `PolicyEngine` | `policy_engine.py` | Decide which agents to run (no LLM) |
| `ReasoningPlanner` | `reasoning_planner.py` | LLM-backed planner; wraps PolicyEngine as fallback |
| `DeterministicResolver` | `deterministic_resolver.py` | Rule-based exception routing |
| `AgentOrchestrator` | `orchestrator.py` | Sequence execution, feedback, post-policy |
| `AgentGuardrailsService` | `guardrails_service.py` | RBAC enforcement |
| `AgentTraceService` | `agent_trace_service.py` | Unified governance writes |
| `DecisionLogService` | `decision_log_service.py` | Recommendation lifecycle |
| `BaseTool` / `ToolRegistry` | `tools/registry/base.py` | Tool system |
| Concrete tools (6) | `tools/registry/tools.py` | PO, GRN, vendor, invoice lookups |
| Concrete agents (8) | `agent_classes.py` | Specialised implementations |
| `AGENT_CLASS_REGISTRY` | `agent_classes.py` | AgentType -> class map |
| `_AgentRunOutputProxy` | `orchestrator.py` | Adapts `AgentRun` DB record to `AgentMemory` interface |

---

## 4. Agent Definitions (Database-Backed Config)

Every agent type has an `AgentDefinition` record in the database (seeded by
`seed_config`). This is the source of truth for whether an agent is enabled
and what tools it may use.

```python
class AgentDefinition(BaseModel):
    agent_type    # AgentType enum value
    name
    description
    enabled       # False -> orchestrator skips
    config_json   # {"allowed_tools": ["po_lookup", ...], "max_tokens": 4096}
```

**To disable an agent** without code changes: set `enabled=False` via admin.
The orchestrator calls `AgentDefinition.objects.filter(agent_type=..., enabled=True).first()`
at the start of each `BaseAgent.run()`. If None, a run record is still created
but with no `agent_definition` FK.

---

## 5. The Tool System

### Structure

```
BaseTool (abstract)
  +-- name: str
  +-- description: str
  +-- parameters_schema: dict     # JSON Schema passed to LLM
  +-- required_permission: str    # RBAC permission code
  +-- run(**kwargs) -> ToolResult  # implement this
  +-- execute(**kwargs) -> ToolResult  # wraps run() with timing + error handling
  +-- get_spec() -> ToolSpec      # returns LLM-facing spec

ToolRegistry (singleton)
  +-- register(tool)
  +-- get(name) -> BaseTool
  +-- get_specs(names) -> List[ToolSpec]   # passed to LLM as tools=
```

### Registered Tools

| Tool Name | Permission | Purpose |
|---|---|---|
| `po_lookup` | `purchase_orders.view` | Lookup PO by number or vendor; tries ERP resolver first |
| `grn_lookup` | `grns.view` | Lookup GRN by PO number; tries ERP resolver first |
| `vendor_search` | `vendors.view` | Search vendors by name |
| `invoice_details` | `invoices.view` | Full invoice data (header + lines) |
| `exception_list` | `reconciliation.view` | Active exceptions for a result |
| `reconciliation_summary` | `reconciliation.view` | Match status + key metrics |

### Adding a Tool

```python
@register_tool
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something useful."
    required_permission = "module.action"
    parameters_schema = {
        "type": "object",
        "properties": {
            "param_one": {"type": "string", "description": "..."},
        },
        "required": ["param_one"],
    }

    def run(self, param_one: str = "", **kwargs) -> ToolResult:
        # business logic here
        return ToolResult(success=True, data={"result": param_one})
```

Register: `ToolDefinition` record pointing to the new tool name; reference it
in the relevant agent's `config_json["allowed_tools"]`.

---

## 6. The LLM Client

`LLMClient` is a thin wrapper around `openai.AzureOpenAI` (or `OpenAI`).
Configured entirely via settings/env vars.

```python
client = LLMClient(
    model=None,          # defaults to AZURE_OPENAI_DEPLOYMENT or LLM_MODEL_NAME
    temperature=None,    # defaults to LLM_TEMPERATURE (0.1)
    max_tokens=None,     # defaults to LLM_MAX_TOKENS (4096)
)

response: LLMResponse = client.chat(
    messages=[LLMMessage(role="system", content="..."), ...],
    tools=[ToolSpec(name="...", description="...", parameters={...})],
    tool_choice="auto",          # or "none" / specific function
    response_format=None,        # {"type": "json_object"} for structured output
)
```

`LLMResponse` carries:
- `content`: text content (None when finish_reason is `tool_calls`)
- `tool_calls`: list of `LLMToolCall(id, name, arguments)`
- `prompt_tokens`, `completion_tokens`, `total_tokens`

The client resolves provider from `LLM_PROVIDER` setting:
- `"azure_openai"` (default) -> `AzureOpenAI` with deployment-as-model
- any other value -> plain `OpenAI`

---

## 7. BaseAgent and the ReAct Loop

Every agent follows the **Reason + Act** pattern:

```
INIT: [system_msg, user_msg]
LOOP (max 6 rounds):
  1. LLM call with current messages + tool specs
  2. If finish_reason != tool_calls -> interpret_response() -> done
  3. Append assistant msg (with tool_calls array)
  4. For each tool call:
       a. RBAC check (authorize_tool)
       b. tool.execute(**arguments)
       c. Append tool response msg (with tool_call_id)
       d. Persist AgentStep
  5. Back to 1
FINALIZE: persist AgentRun, DecisionLog entries
```

Message format follows the OpenAI tool-calling convention exactly:
- Assistant messages include a `tool_calls` array.
- Tool response messages include `tool_call_id` and `name`.

### Implementing a New Agent

```python
class MyNewAgent(BaseAgent):
    agent_type = AgentType.MY_NEW_TYPE   # add to core/enums.py first

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.my_new_type")   # seed via seed_prompts

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Invoice: {ctx.invoice_id}\n"
            "Do something useful."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["invoice_details", "po_lookup"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)
```

### `_sanitise_text()` -- ASCII Safety on All Stored Output

`BaseAgent` exposes a static method that must be applied to any LLM-generated
text before it is persisted:

```python
@staticmethod
def _sanitise_text(text: str) -> str:
    replacements = {
        "\u2018": "'", "\u2019": "'",       # curly single quotes
        "\u201c": '"', "\u201d": '"',       # curly double quotes
        "\u2014": "--", "\u2013": "-",      # em/en dash
        "\u2026": "...",                    # ellipsis
        "\u2192": "->", "\u2190": "<-", "\u21d2": "=>",  # arrows
        "\u2022": "-",                      # bullet
    }
    for char, ascii_eq in replacements.items():
        text = text.replace(char, ascii_eq)
    return re.sub(r"[^\x00-\x7F]", "", text)
```

`_finalise_run()` calls it before writing `agent_run.summarized_reasoning`:

```python
agent_run.summarized_reasoning = self._sanitise_text(output.reasoning)[:2000]
```

This satisfies the project-wide rule that `AgentRun.summarized_reasoning`,
`ReconciliationResult.summary`, `ReviewAssignment.reviewer_summary`, and
`DecisionLog.rationale` must contain only ASCII characters.

Then:
1. Add `AgentType.MY_NEW_TYPE` to `apps/core/enums.py`.
2. Register in `AGENT_CLASS_REGISTRY` in `agent_classes.py`.
3. Add `agents.run_my_new_type` permission to `seed_rbac.py`.
4. Map permission to roles and to `SYSTEM_AGENT` in the role matrix.
5. Add entry to `AGENT_PERMISSIONS` in `guardrails_service.py`.
6. Add to `PolicyEngine` decision logic if needed.
7. Create `AgentDefinition` record (via seed or admin).

---

## 8. Concrete Agent Implementations

| Agent | Type Enum | LLM Used | Tools | Notes |
|---|---|---|---|---|
| `ExceptionAnalysisAgent` | `EXCEPTION_ANALYSIS` | Yes | po_lookup, grn_lookup, invoice_details, exception_list, recon_summary | Also emits `<reviewer_summary>` block for ReviewAssignment; validates recommendation_type against enum; clamps confidence to [0.0, 1.0] |
| `InvoiceExtractionAgent` | `INVOICE_EXTRACTION` | Yes (temp=0, json_object) | None | Single-shot extraction; runs during upload, not reconciliation pipeline |
| `InvoiceUnderstandingAgent` | `INVOICE_UNDERSTANDING` | Yes | invoice_details, po_lookup, vendor_search | Runs when extraction confidence < threshold |
| `PORetrievalAgent` | `PO_RETRIEVAL` | Yes | po_lookup, vendor_search, invoice_details | Triggers feedback loop if PO found; `interpret_response` normalises evidence to `found_po` from fallback keys |
| `GRNRetrievalAgent` | `GRN_RETRIEVAL` | Yes | grn_lookup, po_lookup, invoice_details | 3-way mode only; `build_user_message` returns a no-op JSON payload immediately when `ctx.reconciliation_mode == "TWO_WAY"` |
| `ReviewRoutingAgent` | `REVIEW_ROUTING` | **No (deterministic)** | reconciliation_summary, exception_list | Replaced by DeterministicResolver |
| `CaseSummaryAgent` | `CASE_SUMMARY` | **No (deterministic)** | invoice_details, po_lookup, grn_lookup, etc. | Replaced by DeterministicResolver |
| `ReconciliationAssistAgent` | `RECONCILIATION_ASSIST` | Yes | invoice_details, po_lookup, grn_lookup, etc. | Handles PARTIAL_MATCH analysis |

`EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, and `CASE_SUMMARY` are listed in
`DeterministicResolver.REPLACED_AGENTS`. The orchestrator partitions the
plan so these always run through the rule-based path. Their `AgentRun`
records use `llm_model_used="deterministic"` and zero token counts.

---

## 9. The PolicyEngine

`PolicyEngine.plan(result)` maps reconciliation state to an ordered agent list.
It is purely deterministic (no LLM, no I/O aside from DB reads).

> **Note:** `AgentOrchestrator` now uses `ReasoningPlanner` instead of
> `PolicyEngine` directly. `ReasoningPlanner` wraps `PolicyEngine` as its
> deterministic fallback and always calls the LLM to produce the final plan.
> On any LLM error the deterministic `PolicyEngine` result is used unchanged.

### Decision Matrix

| Condition | Agents Planned |
|---|---|
| MATCHED, confidence >= threshold | `skip_agents=True` (no pipeline) |
| PARTIAL_MATCH, all lines within auto-close band, no HIGH exceptions | `skip_agents=True, auto_close=True` |
| PO_NOT_FOUND exception | + PO_RETRIEVAL |
| GRN_NOT_FOUND exception (3-way only) | + GRN_RETRIEVAL |
| extraction confidence < threshold | + INVOICE_UNDERSTANDING |
| PARTIAL_MATCH (outside auto-close band) | + RECONCILIATION_ASSIST |
| any exceptions exist | + EXCEPTION_ANALYSIS |
| any agents planned | + REVIEW_ROUTING + CASE_SUMMARY (always appended) |
| REQUIRES_REVIEW / UNMATCHED / ERROR with no specific agents | EXCEPTION_ANALYSIS + REVIEW_ROUTING + CASE_SUMMARY |

### Auto-Close Tolerance Band

`_within_auto_close_band()` checks every line result against the wider
auto-close thresholds (default: qty 5%, price 3%, amount 3%). If all lines
pass AND there are no HIGH-severity exceptions, the result is auto-closed
without any AI agents.

---

## 10. The DeterministicResolver

`DeterministicResolver.resolve()` applies a priority-ordered rule matrix to
exception types and severities. It replaces three LLM agents
(`EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, `CASE_SUMMARY`) with deterministic
logic while producing identical output structures.

### Rule Priority (highest first)

| Priority | Condition | Recommendation |
|---|---|---|
| 0 | Prior agent recommended AUTO_CLOSE with confidence >= 0.80 | `AUTO_CLOSE` |
| 1 | `EXTRACTION_LOW_CONFIDENCE` exception | `REPROCESS_EXTRACTION` |
| 2 | `VENDOR_MISMATCH` exception | `SEND_TO_VENDOR_CLARIFICATION` |
| 3 | GRN/receipt exception types | `SEND_TO_PROCUREMENT` |
| 4 | 3+ independent issue categories AND HIGH severity | `ESCALATE_TO_MANAGER` |
| 5 | Default | `SEND_TO_AP_REVIEW` |

Numeric mismatches (`QTY_MISMATCH`, `PRICE_MISMATCH`, `AMOUNT_MISMATCH`,
`TAX_MISMATCH`) are collapsed to a single category for complexity assessment
to avoid false escalation from natural cascading.

The resolver also builds a structured `case_summary` string and persists it
on `ReconciliationResult.summary`.

---

## 11. The AgentOrchestrator

`AgentOrchestrator.execute(result, request_user)` is the single public entry
point for the entire agentic pipeline.

### Execution Sequence

```
1. resolve_actor(request_user)              # user or SYSTEM_AGENT
2. authorize_orchestration(actor)           # agents.orchestrate permission
3. build_trace_context_for_agent(actor)     # set TraceContext thread-local
3a. idempotency guard: query AgentRun.objects for RUNNING runs on this result;
    if found -> set orch_result.skipped=True / skip_reason and return early
4. policy.plan(result)                      # ReasoningPlanner -> PolicyEngine fallback
4a. orch_result.plan_source = plan.plan_source
    orch_result.plan_confidence = plan.plan_confidence
5. if plan.skip_agents -> auto-close or skip, return
6. partition plan.agents:
     llm_agents       = plan.agents - REPLACED_AGENTS
     deterministic_tail = plan.agents & REPLACED_AGENTS
7. build AgentContext (exceptions, extra, RBAC fields, trace IDs)
8. create AgentMemory() and assign ctx.memory = memory
9. for agent_type in llm_agents:
     a. authorize_agent(actor, agent_type)
     b. agent_cls().run(ctx)
     c. memory.record_agent_output(agent_type, _AgentRunOutputProxy(agent_run))
     d. if EXCEPTION_ANALYSIS: write reviewer summary to ReviewAssignment
     e. if agent in _RECOMMENDING_AGENTS: log_recommendation()
     f. if agent in _FEEDBACK_AGENTS: _apply_agent_findings() -> re-reconcile;
           refresh ctx.po_number, ctx.exceptions, ctx.memory.resolved_po_number,
           ctx.memory.facts["grn_available"], ctx.memory.facts["grn_fully_received"]
     g. _reflect(agent_type, agent_run, result, remaining, ctx)
           -> may insert new agents into llm_agents immediately after current position
     h. on first iteration only: stamp plan metadata onto agent_run.input_payload
           (plan_source, plan_confidence, planned_agents) and save(update_fields=...)
10. if deterministic_tail: _apply_deterministic_resolution()
11. _resolve_final_recommendation()         # highest-confidence AgentRecommendation
12. _apply_post_policies():
     - should_auto_close() -> result.match_status = MATCHED
     - should_escalate()   -> create AgentEscalation
```

### Context Forwarding Between Agents

After each LLM agent completes, the orchestrator updates structured memory:

```python
memory.record_agent_output(agent_type, _AgentRunOutputProxy(agent_run))
```

`_AgentRunOutputProxy` is an internal adapter that reads `.summarized_reasoning`,
`.output_payload`, and `.confidence` from an `AgentRun` DB record and presents
them as `.reasoning`, `.recommendation_type`, `.confidence`, and `.evidence` --
the interface expected by `AgentMemory.record_agent_output()`.

After the feedback loop (PO_RETRIEVAL only), context is also refreshed on memory:

```python
ctx.memory.resolved_po_number = ctx.po_number
# Always written -- bool() ensures False is correctly propagated (not just skipped).
ctx.memory.facts["grn_available"] = bool(result.grn_available)
ctx.memory.facts["grn_fully_received"] = bool(result.grn_fully_received)
```

Agents read from `ctx.memory` directly. The table below documents what each
agent reads in its `build_user_message()` method:

| Agent | Fields read from `ctx.memory` |
|---|---|
| `ReviewRoutingAgent` | `agent_summaries` (all prior summaries), `current_recommendation`, `current_confidence` |
| `CaseSummaryAgent` | `agent_summaries` (pipe-joined, 100 chars each), `current_recommendation`, `current_confidence` |
| `ReconciliationAssistAgent` | `resolved_po_number` (appended to prompt when not None) |

All other agents do not currently read from `ctx.memory` in `build_user_message()`;
they receive context exclusively through `AgentContext` fields (`po_number`,
`exceptions`, etc.).

---

## 12. RBAC Guardrails

`AgentGuardrailsService` is the sole gatekeeper for every agent action.

### Permission Map

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
| Call `po_lookup` tool | `purchase_orders.view` | `authorize_tool()` |
| Call `grn_lookup` tool | `grns.view` | `authorize_tool()` |
| Auto-close a result | `recommendations.auto_close` | `authorize_action()` |
| Escalate a case | `cases.escalate` | `authorize_action()` |

### System Agent Identity

When no human user is available (Celery async trigger, scheduled jobs),
`resolve_actor(None)` returns the `system-agent@internal` user with the
`SYSTEM_AGENT` role (rank 100, `is_system_role=True`). This identity has
exactly the permissions seeded in `seed_rbac` for that role -- it is never
an admin bypass.

### Fail-Closed Behaviour

- Unknown tool names: denied by `authorize_tool()` (returns False).
- Unknown agent types: denied (`AGENT_PERMISSIONS.get()` returns None -> False).
- Missing `SYSTEM_AGENT` role (seed not run): `resolve_actor` still returns a
  user object but it will fail permission checks.

---

## 13. Agent Feedback Loop

When the `PORetrievalAgent` (the only current `_FEEDBACK_AGENTS` member)
completes, the orchestrator checks its output for a PO number.
`PORetrievalAgent.interpret_response()` normalises the evidence dict so the
feedback loop can always find the PO regardless of which key the LLM used.
The normalisation checks keys in this priority order:
`"found_po"` (canonical) → `"po_number"` → `"matched_po"` → `"result"` → `"found"` → `"po"`.
The first non-empty string is copied to `evidence["found_po"]`.

If a PO number is found:

```
1. Lookup PurchaseOrder by po_number (or normalized variant)
2. AgentFeedbackService.apply_found_po(result, po, agent_run_id)
   |
   +--> Link invoice to PO (re-link)
   +--> Re-run deterministic matching (ThreeWayMatchService or TwoWayMatchService)
   +--> Update ReconciliationResult status, exceptions, line results
3. Refresh AgentContext:
   - ctx.po_number = new po.po_number
   - ctx.exceptions = refreshed from DB
   - ctx.extra["grn_available"], ["grn_fully_received"] updated
```

Subsequent agents in the pipeline then see the updated state. The resolved PO
number is also stored in `ctx.memory.resolved_po_number` when `AgentMemory` is
available.

---

## 14. Observability and Governance

### Persisted Records Per Run

| Record | Written By | Content |
|---|---|---|
| `AgentRun` | `BaseAgent.run()` start | Agent type, status, tokens, RBAC fields, trace ID |
| `AgentMessage` | `_save_message()` | Every system/user/assistant/tool message |
| `AgentStep` | `_execute_tool()` | Every tool call: input, output, duration, success |
| `DecisionLog` | `_finalise_run()` | Every `decisions` entry from AgentOutput |
| `AgentRecommendation` | `DecisionLogService.log_recommendation()` | REVIEW_ROUTING + CASE_SUMMARY only |
| `AgentEscalation` | `_apply_post_policies()` | When escalation triggered |
| `AuditEvent` (guardrail) | `log_guardrail_decision()` | Every RBAC allow/deny |
| `AuditEvent` (recommendation) | orchestrator post-run | AGENT_RECOMMENDATION_CREATED |

### AgentRun RBAC Fields

Every `AgentRun` record carries:
- `actor_user_id`, `actor_primary_role`, `actor_roles_snapshot_json`
- `permission_checked`, `permission_source`, `access_granted`
- `trace_id`, `span_id`

Deterministic runs use `llm_model_used="deterministic"`, token counts = 0.

### Governance API

`/api/v1/governance/` exposes 9 endpoints. The `agent-trace` and
`agent-performance` endpoints read directly from `AgentRun`, `AgentStep`,
`AgentMessage`, and `DecisionLog`.

### Plan Comparison Dashboard Card

The `agent_performance` view (`apps/dashboard/views.py`) also queries
`AgentRun.input_payload__plan_source` over the past 7 days and passes a
`plan_comparison` context variable to `templates/dashboard/agent_performance.html`.
This renders a Bootstrap 5 card with a table:

| Column | Source |
|---|---|
| Planner | `plan_source` value ("deterministic" or "llm") |
| Runs | count of AgentRun records for that planner |
| Avg Agent Confidence | average `final_confidence` for those runs |

The card shows "No plan data yet." when no plan metadata has been stored
(e.g., before any LLM planner runs or before any `AgentRun.input_payload`
records are populated).

---

## 15. How the Pipeline Is Triggered

### Synchronous (view-triggered)

```python
# apps/reconciliation/template_views.py -> start_reconciliation view
from apps.agents.services.orchestrator import AgentOrchestrator
orchestrator = AgentOrchestrator()
orchestrator.execute(result, request_user=request.user)
```

### Asynchronous (Celery)

```python
# apps/agents/tasks.py
@shared_task(bind=True, max_retries=2, acks_late=True)
def run_agent_pipeline_task(self, reconciliation_result_id: int) -> dict:
    result = ReconciliationResult.objects.get(pk=reconciliation_result_id)
    orchestrator = AgentOrchestrator()
    orch_result = orchestrator.execute(result, request_user=None)
    # request_user=None -> SYSTEM_AGENT identity used
    return {"status": orch_result.final_recommendation, ...}
```

### On Windows Dev (no Redis)

`CELERY_TASK_ALWAYS_EAGER=True` (default) runs tasks synchronously in-process.
The async and sync paths produce identical results.

---

## 16. Upgrade Path: Reasoning Engine

The current architecture is a **fixed-pipeline** system: the `PolicyEngine`
decides a static ordered list of agents, each runs once, and the
`DeterministicResolver` handles the terminal steps. This section describes
how to upgrade it to a **dynamic reasoning engine** where a meta-agent plans,
reflects, and re-plans on the fly -- while keeping the existing deterministic
flow as the safe fallback.

### Current Architecture Constraints

| Constraint | Impact |
|---|---|
| Agent order is fixed (list from PolicyEngine) | Cannot react to mid-pipeline discoveries |
| Each agent runs exactly once | Cannot retry or branch based on findings |
| DeterministicResolver always handles tail agents | LLM cannot override routing in edge cases |
| No planning step -- just a rule lookup | Cannot handle novel exception combinations |

> **Implementation status:** Phases 1, 2, and 3 are **fully implemented** and
> merged. `AgentMemory` is live in `agent_memory.py`. `ReasoningPlanner` is
> live in `reasoning_planner.py` and is what `AgentOrchestrator.__init__()`
> instantiates -- the LLM planning path is always active with `PolicyEngine`
> as the internal fallback on error. The reflection step (`_reflect()`) runs
> after every agent in the loop. Phases 4 and 5 remain optional/future.

### Target Architecture: Planner + Executor

```
ReconciliationResult
        |
        v
  ReasoningPlanner (new)           <-- LLM call: produces a structured plan
        |                              given: match_status, exceptions, mode
        +--> JSON plan: [
        |      {"agent": "PO_RETRIEVAL", "rationale": "PO_NOT_FOUND exception"},
        |      {"agent": "EXCEPTION_ANALYSIS", "rationale": "analyse remaining exceptions"},
        |      {"agent": "REVIEW_ROUTING", "rationale": "route based on analysis"}
        |    ]
        |
        v
  ReasoningExecutor (new)          <-- iterates the plan, runs each agent,
        |                              updates shared memory, can re-plan
        |
        +--> SharedMemory (new)    <-- structured dict of findings from all agents
        |
        +--> Reflection step       <-- after each agent: should we re-plan?
        |
        v
  DeterministicResolver            <-- UNCHANGED: still the safe fallback for
                                       terminal classification when planner
                                       confidence is below threshold
```

### What to Build

#### Step 1 -- SharedMemory (no breaking changes)

Replace `ctx.extra` string-passing with a typed memory object. This is
additive and does not change any existing code paths.

```python
# apps/agents/services/agent_memory.py  (new file)

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class AgentMemory:
    """Accumulated findings shared across all agents in a single orchestration run."""

    # Findings from retrieval agents
    resolved_po_number: Optional[str] = None
    resolved_grn_numbers: List[str] = field(default_factory=list)
    extraction_issues: List[str] = field(default_factory=list)

    # Reasoning chain
    agent_summaries: Dict[str, str] = field(default_factory=dict)
    # {"PO_RETRIEVAL": "Found PO-4821 matching vendor X"}

    # Running recommendation
    current_recommendation: Optional[str] = None
    current_confidence: float = 0.0

    # Arbitrary facts for novel agent types
    facts: Dict[str, Any] = field(default_factory=dict)

    def record_agent_output(self, agent_type: str, output) -> None:
        """Called by executor after each agent run."""
        self.agent_summaries[agent_type] = output.reasoning[:500]
        if output.recommendation_type:
            if output.confidence > self.current_confidence:
                self.current_recommendation = output.recommendation_type
                self.current_confidence = output.confidence
        evidence = output.evidence or {}
        if evidence.get("found_po"):
            self.resolved_po_number = evidence["found_po"]
```

Add `memory: Optional[AgentMemory] = None` to `AgentContext`. Existing code
ignores it; new planner/executor code uses it.

#### Step 2 -- ReasoningPlanner (always active)

The planner makes a single LLM call to produce a structured execution plan.
It always runs; `PolicyEngine` is the internal fallback on any LLM error.

```python
# apps/agents/services/reasoning_planner.py

class ReasoningPlanner:
    """Wraps PolicyEngine and enhances the plan using an LLM.

    The LLM plan is always attempted. On any LLM error the deterministic
    PolicyEngine result is used as a safe fallback.
    """

    def __init__(self) -> None:
        self._fallback = PolicyEngine()
        self._llm = LLMClient(temperature=0.0, max_tokens=1024)

    def plan(self, result) -> AgentPlan:
        quick_plan = self._fallback.plan(result)

        # Skip agent execution for clean matches or auto-close cases.
        if quick_plan.skip_agents:
            return quick_plan

        # Attempt LLM-driven plan; fall back to deterministic on any error.
        try:
            return self._llm_plan(result)
        except Exception as exc:
            logger.warning(
                "ReasoningPlanner LLM plan failed for result %s (%s); "
                "falling back to deterministic plan.",
                getattr(result, "pk", "?"),
                exc,
            )
            return quick_plan
```

`ReasoningPlanner` also delegates the two post-policy checks so the orchestrator
can call them on `self.policy` regardless of which planner is in use:

```python
def should_auto_close(self, recommendation_type, confidence) -> bool:
    return self._fallback.should_auto_close(recommendation_type, confidence)

def should_escalate(self, recommendation_type, confidence) -> bool:
    return self._fallback.should_escalate(recommendation_type, confidence)
```

`_llm_plan()` applies three validation guards before returning the plan, and
stamps `plan_source` / `plan_confidence` on the returned `AgentPlan`:

```python
# Guard 1 -- empty plan
if not valid_steps:
    raise ValueError("LLM planner returned no valid agent steps.")

# Guard 2 -- CASE_SUMMARY must be last if present
agent_names = [s["agent_type"] for s in valid_steps]
if "CASE_SUMMARY" in agent_names and agent_names[-1] != "CASE_SUMMARY":
    raise ValueError("CASE_SUMMARY must be the last agent in the plan.")

# Guard 3 -- GRN_RETRIEVAL is invalid for TWO_WAY reconciliation
if recon_mode == "TWO_WAY" and "GRN_RETRIEVAL" in agent_names:
    raise ValueError("GRN_RETRIEVAL is not valid for TWO_WAY reconciliation.")

plan_confidence = float(payload.get("confidence", 0.0))
return AgentPlan(..., plan_source="llm", plan_confidence=plan_confidence)
```

If any guard raises, the exception propagates to `plan()` which catches it and
falls back to the deterministic `quick_plan` (so the pipeline degrades
gracefully rather than crashing).

#### Step 3 -- Reflection Step (implemented)

After each LLM agent completes, `_reflect()` inspects the findings and may
insert additional agents immediately after the current position. This allows
the pipeline to react to mid-run discoveries without re-planning from scratch.

```python
# In AgentOrchestrator.execute(), after the feedback loop block:

if last_output:
    extra_agents = self._reflect(
        agent_type,
        last_output,
        result,
        llm_agents[llm_agents.index(agent_type) + 1:],
        ctx,
    )
    if extra_agents:
        insert_pos = llm_agents.index(agent_type) + 1
        for i, new_agent in enumerate(extra_agents):
            llm_agents.insert(insert_pos + i, new_agent)
        logger.info(
            "Reflection inserted agents %s after %s for result %s",
            extra_agents, agent_type, result.pk,
        )


def _reflect(self, completed_agent_type, agent_run, result, remaining_agents, ctx):
    """Inspect the just-completed agent run and return agent types to insert.

    Returns a list of agent_type strings (possibly empty). Never raises.
    """
    try:
        if ctx.memory is None:
            return []

        # Rule 1: PO was just found in a 3-way case -- check for GRN next.
        if (
            completed_agent_type == AgentType.PO_RETRIEVAL
            and ctx.memory.resolved_po_number is not None
            and getattr(result, "reconciliation_mode", "") != "TWO_WAY"
            and AgentType.GRN_RETRIEVAL not in remaining_agents
        ):
            return [AgentType.GRN_RETRIEVAL]

        # Rule 2: Very low confidence extraction -- investigate discrepancies too.
        if (
            completed_agent_type == AgentType.INVOICE_UNDERSTANDING
            and agent_run.confidence is not None
            and agent_run.confidence < 0.5
            and AgentType.RECONCILIATION_ASSIST not in remaining_agents
        ):
            return [AgentType.RECONCILIATION_ASSIST]

        return []
    except Exception:
        logger.exception(
            "_reflect() raised unexpectedly for agent %s result %s",
            completed_agent_type,
            getattr(result, "pk", "?"),
        )
        return []
```

Reflection rules:

| Trigger | Condition | Agent Inserted |
|---|---|---|
| `PO_RETRIEVAL` succeeds | `ctx.memory.resolved_po_number is not None` and mode != TWO_WAY and GRN_RETRIEVAL not already queued | `GRN_RETRIEVAL` |
| `INVOICE_UNDERSTANDING` finishes | `agent_run.confidence < 0.5` and RECONCILIATION_ASSIST not already queued | `RECONCILIATION_ASSIST` |

#### Step 3b -- BaseAgent LLM Retry Wrapper

All LLM calls inside the ReAct tool loop go through `_call_llm_with_retry()`,
a static method on `BaseAgent`. It retries up to 3 times on transient OpenAI
errors with exponential backoff (2s, 4s, 8s), then re-raises on exhaustion:

```python
@staticmethod
def _call_llm_with_retry(llm, messages, tools, max_retries=3, base_delay=2):
    import time as _time
    import openai
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return llm.chat(messages=messages, tools=tools if tools else None)
        except (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "LLM transient error (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, max_retries, delay, exc,
                )
                _time.sleep(delay)
    raise last_exc
```

Errors not in the retry list (`AuthenticationError`, `BadRequestError`, etc.)
propagate immediately without retrying.

#### Step 4 -- LLM-Backed Terminal Agents (optional)

Currently `EXCEPTION_ANALYSIS`, `REVIEW_ROUTING`, and `CASE_SUMMARY` always
go to `DeterministicResolver`. To allow the LLM to handle genuinely novel
cases, add a complexity check:

```python
# In _apply_deterministic_resolution(), before calling resolver:

if (
    getattr(settings, "AGENT_REASONING_ENGINE_ENABLED", False)
    and self._is_complex_case(result, fresh_exceptions)
):
    # Run LLM agents for the tail instead of deterministic resolver
    for det_agent_type in deterministic_agents:
        agent_cls = AGENT_CLASS_REGISTRY.get(det_agent_type)
        if agent_cls:
            agent_run = agent_cls().run(ctx)
            orch.agents_executed.append(det_agent_type)
            orch.agent_runs.append(agent_run)
    return   # skip deterministic path

# Otherwise fall through to existing DeterministicResolver call (unchanged)

def _is_complex_case(self, result, exceptions) -> bool:
    """True for cases the rule matrix cannot handle reliably."""
    types = {e["exception_type"] for e in exceptions}
    severities = {e.get("severity") for e in exceptions}
    # Novel combination: multiple HIGH + cross-team exceptions
    return (
        len(types) > 4
        or (len(types) > 2 and "HIGH" in severities and "VENDOR_MISMATCH" in types)
    )
```

### Migration Strategy

| Phase | What Changes | Status | Existing Flow |
|---|---|---|---|
| Phase 1: SharedMemory | Add `AgentMemory` dataclass to `AgentContext` | **DONE** | Unchanged (field ignored by legacy callers) |
| Phase 2: ReasoningPlanner | `AgentOrchestrator` uses `ReasoningPlanner` (flag off) | **DONE** | Identical -- `PolicyEngine` runs as fallback |
| Phase 3: Enable planner | Set `AGENT_REASONING_ENGINE_ENABLED=True` | Ready to enable | LLM plans the pipeline; deterministic still handles tail |
| Phase 4: Reflection | Add reflection hook after each agent | Not started | Optional inserts only; DeterministicResolver unchanged |
| Phase 5: LLM tail | Complex cases use LLM terminal agents | Not started | DeterministicResolver still handles standard cases |

### What Must Never Change

- **`PolicyEngine`** must remain intact as the fallback. Never delete it.
- **`DeterministicResolver`** handles all standard cases regardless of
  whether the reasoning engine is enabled. It is the correctness guarantee.
- **`AgentGuardrailsService`** -- all permission checks remain mandatory for
  both LLM-planned and deterministic paths.
- **`AgentRun` schema** -- deterministic and LLM runs use the same model.
  Governance and audit code must not distinguish between them.
- **`SYSTEM_AGENT` identity** -- autonomous runs (Celery) always use the
  system-agent user. The reasoning planner follows the same rule.

### Feature Flag Summary

| Setting | Default | Effect |
|---|---|---|
| `AGENT_REASONING_ENGINE_ENABLED` | `False` | When True, enables LLM planner + optional reflection |
| `AGENT_REASONING_REFLECTION_ENABLED` | `False` | When True, enables per-agent reflection step |
| `AGENT_REASONING_LLM_TAIL_ENABLED` | `False` | When True, complex cases use LLM terminal agents |

All three flags are independent. They can be enabled incrementally. When all
are False the system behaves exactly as it does today.

---

## 17. Upgrading Existing Agents to Best Agentic Practices

This section audits each existing agent against current best-practice standards
and provides a concrete upgrade checklist. The goal is to close the gap between
the current working implementation and the ideal agentic design -- without
breaking anything that already works.

### 17.1 Best Practice Checklist (applies to all agents)

| Practice | Standard | Current State | Action Required |
|---|---|---|---|
| Structured output enforcement | Use `response_format={"type":"json_object"}` or explicit JSON-only instruction | Only `InvoiceExtractionAgent` uses `json_object`; others rely on prompt instructions | Upgrade ReAct agents to use json_object mode or schema validation |
| Idempotent runs | Re-running the same agent twice on the same input should produce safe, identical-or-compatible results | Agents create new `AgentRun` records each time but do not deduplicate | Add idempotency key check at orchestrator level |
| Tool error resilience | Agents must handle tool failures gracefully and not hallucinate when a tool returns an error | `_execute_tool` returns a `ToolResult(success=False)` but the LLM still sees the error string -- it may try to guess the answer | Add explicit "if tool fails, escalate rather than guess" instruction to system prompts |
| Prompt versioning | Every agent run should record which prompt version was used | `AgentRun.prompt_version` field exists but is never written | Populate from `PromptTemplate.version` during `_init_messages()` |
| Token budget awareness | Agent should not silently truncate context | No context-length guard; very large exception lists could overflow | Add a pre-flight token estimator and truncate `ctx.exceptions` to the N highest-severity ones when over budget |
| Confidence calibration | Confidence scores must be grounded in evidence, not just stated | Agents produce confidence from LLM output; no post-hoc calibration | Add a cross-check: if confidence > 0.9 but tool calls failed, cap at 0.75 |
| Retry with backoff | Transient LLM failures should retry before failing the run | `AgentDefinition.max_retries=2` field exists but `BaseAgent.run()` does not read it | Wire `max_retries` into the try/except block in `BaseAgent.run()` |
| Minimal tool surface | Each agent should only see the tools it actually needs | `allowed_tools` is correctly scoped per agent | No change needed -- already correct |
| Human-readable reasoning | `summarized_reasoning` must be meaningful to a non-technical reviewer | Content is taken directly from LLM output; quality varies | Add a reasoning quality check: minimum 50 characters, must mention at least one invoice or PO reference |
| No special characters in output | Agent-generated strings that are stored to DB or shown in UI must use ASCII only | **DONE** -- `BaseAgent._sanitise_text()` is implemented and called in `_finalise_run()` | No further action required |

---

### 17.2 Per-Agent Upgrade Guide

#### ExceptionAnalysisAgent

Current gaps:
- Produces two output formats in one response (standard JSON + `<reviewer_summary>` block).
  This makes parsing fragile and increases the chance of parse failure.
- `_parse_reviewer_summary()` silently returns None on parse failure with only a warning log.

Recommended upgrades:

1. **Split into two dedicated prompts.** Keep the main analysis JSON as-is.
   Have the reviewer summary as a second, simpler structured call after the
   ReAct loop completes (not embedded in the same response).

2. **Add fallback reviewer summary.** When `_parse_reviewer_summary()` returns
   None, generate a minimal summary from the main JSON output rather than
   leaving `reviewer_summary` empty on the `ReviewAssignment`.

3. **Validate recommendation_type against enum.** **DONE** -- implemented in
   `interpret_response()`. Invalid values are replaced with `SEND_TO_AP_REVIEW`
   and confidence is capped at 0.6. Confidence is always clamped to [0.0, 1.0].

---

#### InvoiceExtractionAgent

Current gaps:
- Uses `response_format={"type":"json_object"}` (correct) but does not
  validate the returned schema before storing.
- `confidence` is taken directly from the LLM output without a bounds check.

Recommended upgrades:

1. **Schema validation after extraction.** Use a lightweight schema check
   (required keys, numeric confidence, non-empty line_items) before calling
   `_finalise_run()`. If validation fails, mark the run FAILED so the
   extraction pipeline can retry.

2. **Confidence bounds.** Clamp to [0.0, 1.0] and check that it is not
   suspiciously high (> 0.95) when critical fields like `invoice_number`
   are empty.

```python
def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
    data = _parse_agent_json(content)
    # Bounds check
    conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    # Penalise for empty critical fields
    if not data.get("invoice_number") or not data.get("vendor_name"):
        conf = min(conf, 0.5)
    data["confidence"] = conf
    return AgentOutput(
        reasoning=f"Extracted {len(data.get('line_items', []))} line items with confidence {conf}",
        confidence=conf,
        evidence=data,
        raw_content=content,
    )
```

---

#### PORetrievalAgent

Current gaps:
- The feedback loop in the orchestrator checks evidence keys `found_po`,
  `po_number`, `matched_po` -- but the agent prompt does not explicitly
  instruct the LLM to use these key names.
- If the LLM puts the PO number in `evidence["result"]` or similar, the
  feedback loop silently does nothing.

**DONE** -- `interpret_response()` now normalises the evidence dict.
Fallback keys checked in priority order: `po_number` -> `matched_po` ->
`result` -> `found` -> `po`. The first non-empty string value is copied to
`evidence["found_po"]`. The system prompt upgrade (instructing the LLM to use
`found_po` explicitly) is the remaining recommended action.

---

#### GRNRetrievalAgent

Current gaps:
- No mode guard inside the agent itself; it relied entirely on the
  `PolicyEngine` never scheduling it in TWO_WAY mode.

**DONE** -- `build_user_message()` now returns a machine-parseable JSON
no-op string immediately when `ctx.reconciliation_mode == "TWO_WAY"`.
`interpret_response` / `_to_agent_output` handle this gracefully (zero
confidence, empty decisions, empty evidence).

---

#### ReviewRoutingAgent and CaseSummaryAgent

These are already replaced by `DeterministicResolver` in the current pipeline.
Their class definitions exist for the case where LLM tail is enabled (Section 16
Phase 5). When that path is active:

- Both agents should use `response_format={"type":"json_object"}`.
- `CaseSummaryAgent` should not use tools at that point -- the DeterministicResolver
  already built the summary template; the LLM's job is enrichment only.
- `ReviewRoutingAgent` should receive the full agent memory (Section 16.1) as
  context so it does not re-derive what prior agents already established.

---

#### ReconciliationAssistAgent

Current gaps:
- The most open-ended agent -- broad tool access and a general-purpose prompt.
  This is the most likely to hallucinate when tools return partial data.

Recommended upgrades:

1. **Ground every claim in a tool result.** Add to the system prompt:
   "You MUST call at least one tool before forming your recommendation.
   Never recommend AUTO_CLOSE without first verifying amounts via
   reconciliation_summary or invoice_details."

2. **Add a minimum tool-call enforcement check.** In `BaseAgent.run()`,
   after the ReAct loop, check that `step_counter > 2` (at least one tool
   call happened). If not, downgrade confidence by 20% before finalising.

---

### 17.3 Shared Improvements for All Agents

These can be implemented once in `BaseAgent` and all agents inherit them.

#### Output Sanitiser -- DONE

`BaseAgent._sanitise_text()` is implemented and called in `_finalise_run()`.
See Section 7 for the full method. No further action required for existing
agents. New agents inherit this automatically.

#### Prompt Version Capture

```python
# In BaseAgent._init_messages(), after building sys_msg:
try:
    from apps.core.models import PromptTemplate
    pt = PromptTemplate.objects.filter(
        slug=f"agent.{self.agent_type.lower()}", is_active=True
    ).only("version").first()
    if pt:
        agent_run.prompt_version = pt.version
        agent_run.save(update_fields=["prompt_version"])
except Exception:
    pass
```

#### Retry on Transient LLM Failure

```python
# In BaseAgent.run(), wrap the LLM call with retry:
import time as _time

MAX_LLM_RETRIES = 2
RETRY_DELAY_SECONDS = 2

for attempt in range(MAX_LLM_RETRIES + 1):
    try:
        llm_resp = self.llm.chat(messages=[...], tools=tool_specs)
        break
    except Exception as exc:
        if attempt < MAX_LLM_RETRIES:
            _time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
            continue
        raise
```

#### Token Budget Pre-flight

```python
# In AgentOrchestrator.execute(), before building AgentContext:
MAX_EXCEPTION_CONTEXT = 20  # keep only the 20 highest-severity exceptions

if len(exceptions) > MAX_EXCEPTION_CONTEXT:
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    exceptions = sorted(
        exceptions,
        key=lambda e: severity_order.get(e.get("severity", "LOW"), 3)
    )[:MAX_EXCEPTION_CONTEXT]
```

---

## 18. Open Source Tools for Agent Performance and Inter-Agent Tracing

The platform already has a strong internal governance layer
(`AgentTraceService`, `DecisionLog`, `ToolCall`, governance API). The tools
below extend this with visual dashboards, cross-session analytics, and
evaluation frameworks that would otherwise need to be built from scratch.

### 18.1 Open Source vs. SaaS Clarification

A common misconception: **LangSmith is not open source**. The LangChain agent
SDK (`langchain`) is MIT-licensed, but the LangSmith observability server has
never been released as open source. The only backend is `smith.langchain.com`.
Do not plan around a self-hosted LangSmith.

| Tool | Truly Open Source | Self-hostable | License |
|---|---|---|---|
| **Langfuse** | Yes | Yes (Docker + Postgres) | MIT |
| **Phoenix (Arize)** | Yes | Yes (Docker + SQLite or Postgres) | Apache 2.0 |
| **OpenLLMetry / openinference** | Yes (SDK) | Yes (any OTEL backend) | Apache 2.0 |
| **Helicone** | Yes (backend) | Yes (Docker + Clickhouse) | Apache 2.0 |
| **Weave (W&B)** | SDK only | No -- W&B cloud required | -- |
| **LangSmith** | No | No -- LangChain cloud only | -- |
| **PromptLayer** | No | No -- proprietary SaaS | -- |

### 18.2 Recommended Combination for This Stack

**Tier 1 (recommended):** Langfuse + openinference instrumentation

- Langfuse is self-hostable on the existing Postgres + Redis stack (same infrastructure as Django/Celery).
- Its trace/span/generation hierarchy maps directly to `AgentRun > AgentStep > AgentMessage`.
- First-class Azure OpenAI cost tracking with deployment-name pricing overrides.
- `openinference-instrumentation-openai` auto-instruments every `AzureOpenAI` SDK call with zero per-call changes.

**Tier 1b (pure OTEL alternative):** Phoenix + openinference

- Better fit if the org already runs an OpenTelemetry stack for non-LLM services.
- Phoenix stores to SQLite (default) or Postgres, runs as a single container.
- Strongest eval templates (hallucination, Q&A, relevance via LLM-as-judge).

**Not recommended:**

- **Weave/W&B:** The backend is W&B cloud only -- PO/invoice financial data must not leave your infrastructure on a mandatory external service.
- **LangSmith:** Closed backend, not self-hostable.
- **PromptLayer:** Not an agent observability tool; predates multi-step agent tracing.

---

### 18.3 Coverage Matrix

| Capability | Langfuse | Phoenix | openinference (OTel) |
|---|---|---|---|
| Latency, token usage, cost | Native (model-level pricing) | Via OTEL spans | Captures, exports to backend |
| Tool call frequency/latency | Custom spans on AgentStep | OTEL span attributes | Auto-captured from OpenAI tool calls |
| Inter-agent message tracing | Parent/child spans | Parent/child OTEL spans | Trace context propagation |
| Hallucination / reasoning eval | Built-in eval runner + LLM-as-judge | Best-in-class eval templates | N/A (instrumentation only) |
| Execution flow visualisation | Waterfall trace UI | Interactive trace tree | N/A |
| Azure OpenAI | Yes, deployment-name cost mapping | Yes, via openinference | Yes, transparent proxy |
| Self-hostable | Yes | Yes | SDK only, needs OTEL backend |

---

### 18.4 Langfuse (Recommended First Choice)

**GitHub:** https://github.com/langfuse/langfuse
**Pip:** `langfuse`
**Self-hostable:** Yes -- Docker Compose or Kubernetes. Postgres + Redis only (same stack as this project).
**License:** MIT (core), commercial for cloud

Langfuse is the closest match to what this project already tracks internally.
It provides:
- Trace/span hierarchy (maps directly to `AgentRun` -> `AgentStep` -> `ToolCall`)
- Prompt version management (mirrors `PromptRegistry`)
- Token cost tracking per model per run
- Score/feedback collection (human feedback on recommendations)
- Cross-run analytics: average latency, token usage, tool call frequency per agent type
- Evaluation datasets: capture bad agent outputs and replay them to test prompt changes

**Integration approach -- three options (all additive, none replace existing models):**

**Option A -- Low-level SDK** (maps 1:1 to `AgentRun/AgentStep` schema):

```python
from langfuse import Langfuse
lf = Langfuse()

trace = lf.trace(id=str(agent_run.pk), name="agent_run", user_id=str(actor_user_id))
span  = trace.span(name="agent_step", input=step.input_data, metadata={"tool": step.action})
gen   = span.generation(name="llm_call", model=llm_model, usage={"input": pt, "output": ct})
gen.end(output=response_content)
span.end(output=step.output_data)
trace.update(output=final_output)
```

Emit from Django post-save signals on `AgentStep` saves to keep Langfuse and Postgres in sync.

**Option B -- Decorator approach** (wraps ReAct loop functions):

```python
from langfuse.decorators import observe, langfuse_context

@observe(name="agent_run")
def run_agent(query: str):
    langfuse_context.update_current_trace(session_id=session_id)
    ...
```

**Option C -- OpenAI SDK patch** (zero-code LLM call capture):

```python
from langfuse.openai import openai   # drop-in replacement for: import openai
```

**LangfuseTracer wrapper** (additive, does not replace existing AgentRun writes):

```python
# apps/agents/services/langfuse_tracer.py  (new file)

from __future__ import annotations
from typing import Any, Dict, List, Optional
from django.conf import settings

try:
    from langfuse import Langfuse
    from langfuse.decorators import langfuse_context, observe
    _LANGFUSE_ENABLED = getattr(settings, "LANGFUSE_ENABLED", False)
except ImportError:
    _LANGFUSE_ENABLED = False


class LangfuseTracer:
    """Thin wrapper that sends LLM calls to Langfuse when enabled.

    All existing AgentRun/AgentStep records continue to be written as before.
    Langfuse receives a copy for the visual dashboard only.
    """

    _client = None

    @classmethod
    def get_client(cls):
        if not _LANGFUSE_ENABLED:
            return None
        if cls._client is None:
            cls._client = Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=getattr(settings, "LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
        return cls._client

    @classmethod
    def trace_llm_call(
        cls,
        agent_type: str,
        agent_run_id: int,
        messages: List[Dict],
        response_content: str,
        tool_calls: List,
        tokens: Dict[str, int],
        model: str,
    ) -> None:
        client = cls.get_client()
        if not client:
            return
        try:
            trace = client.trace(
                name=f"agent.{agent_type}",
                id=str(agent_run_id),
                metadata={"agent_type": agent_type},
            )
            trace.generation(
                name="llm_call",
                model=model,
                input=messages,
                output=response_content,
                usage={
                    "input": tokens.get("prompt_tokens", 0),
                    "output": tokens.get("completion_tokens", 0),
                    "total": tokens.get("total_tokens", 0),
                },
            )
        except Exception:
            pass  # Never let tracing break the agent run
```

Wire into `BaseAgent.run()` after each LLM call:

```python
from apps.agents.services.langfuse_tracer import LangfuseTracer

# After llm_resp is received (in the ReAct loop):
LangfuseTracer.trace_llm_call(
    agent_type=self.agent_type,
    agent_run_id=agent_run.pk,
    messages=[m for m in messages if m["role"] != "tool"],
    response_content=llm_resp.content or "",
    tool_calls=llm_resp.tool_calls,
    tokens={
        "prompt_tokens": llm_resp.prompt_tokens,
        "completion_tokens": llm_resp.completion_tokens,
        "total_tokens": llm_resp.total_tokens,
    },
    model=llm_resp.model,
)
```

**Settings to add:**

```python
# config/settings.py
LANGFUSE_ENABLED = env.bool("LANGFUSE_ENABLED", default=False)
LANGFUSE_PUBLIC_KEY = env.str("LANGFUSE_PUBLIC_KEY", default="")
LANGFUSE_SECRET_KEY = env.str("LANGFUSE_SECRET_KEY", default="")
LANGFUSE_HOST = env.str("LANGFUSE_HOST", default="http://localhost:3000")  # self-hosted
```

---

### 18.5 Phoenix / Arize (Best for Local Dev Inspection and Evals)

**GitHub:** https://github.com/Arize-ai/phoenix
**Pip:** `arize-phoenix` (server), `arize-phoenix-otel` (instrumentation), `openinference-instrumentation-openai` (auto-instrument)
**Self-hostable:** Yes -- runs as a local process, no external dependencies.
Stores traces in SQLite by default (zero config) or Postgres for production.
**License:** Apache 2.0

Phoenix provides a local browser-based trace inspector. The `openinference`
library auto-instruments the OpenAI SDK so every `client.chat.completions.create()`
call is captured without any code changes.

**Integration (zero-code for LLM calls):**

```python
# In config/settings.py or apps/__init__.py (only in dev/staging):

import os
if os.getenv("PHOENIX_ENABLED", "false").lower() == "true":
    import phoenix as px
    from openinference.instrumentation.openai import OpenAIInstrumentor

    px.launch_app()               # starts the local UI on http://localhost:6006
    OpenAIInstrumentor().instrument()  # patches openai SDK automatically
```

Because `LLMClient` uses `openai.AzureOpenAI`, the `OpenAIInstrumentor` patches
it automatically. Every LLM call in every agent will appear in the Phoenix UI
with full message/token/latency detail.

**What it shows:**
- Full conversation thread per `AgentRun`
- Token usage per round, cumulative per session
- Tool call latency waterfall
- Side-by-side comparison of two runs on the same input
- Hallucination scores (via `phoenix.evals`)

**Limitation:** Phoenix does not know about the inter-agent orchestration
(which agent ran after which). To show the pipeline graph, emit an
OpenTelemetry span per agent using the approach in Section 18.4.

---

### 18.6 OpenLLMetry / openinference (Best for Inter-Agent Tracing)

**GitHub:** https://github.com/traceloop/openllmetry
**Pip:** `opentelemetry-sdk`, `openinference-instrumentation-openai` (for auto-instrumentation);
optionally `traceloop-sdk` as a convenience wrapper
**Self-hostable:** The SDK is fully open source. Route telemetry to Phoenix,
Langfuse, Jaeger, Grafana Tempo, or any OTEL-compatible backend -- no vendor lock-in.
**License:** Apache 2.0

Think of OpenLLMetry/openinference as the **instrumentation layer** and Phoenix
or Langfuse as the **server**. It uses OpenTelemetry to trace agent pipelines
as distributed spans and is the right tool for answering: "which agent was the
bottleneck and which tool call was slowest?"

**Integration (using Traceloop's convenience wrapper):**

```python
# In Django AppConfig.ready() or wsgi.py -- one-liner setup:
from traceloop.sdk import Traceloop
Traceloop.init(
    app_name="po-recon-agent",
    api_endpoint="http://localhost:6006/v1/traces",   # point at Phoenix or Langfuse
    disable_batch=False,
)
```

**Manual OTEL setup (no Traceloop dependency):**

```python
# apps/agents/services/otel_tracer.py  (new file)

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from openinference.instrumentation.openai import OpenAIInstrumentor
from django.conf import settings

_provider = None


def init_otel():
    """Call once from Django AppConfig.ready() when OTEL_ENABLED=True."""
    global _provider
    if _provider or not getattr(settings, "OTEL_ENABLED", False):
        return
    exporter = OTLPSpanExporter(
        endpoint=getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:6006/v1/traces")
    )
    _provider = TracerProvider()
    _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)
    OpenAIInstrumentor().instrument(tracer_provider=_provider)  # auto-patches AzureOpenAI


def get_tracer():
    return trace.get_tracer("po-recon.agents")
```

Wrap each agent execution in `AgentOrchestrator`:

```python
from apps.agents.services.otel_tracer import get_tracer
from opentelemetry import trace as otel_trace

tracer = get_tracer()

# In execute(), around the agent run loop:
with tracer.start_as_current_span(
    f"agent.{agent_type}",
    attributes={
        "agent.type": agent_type,
        "reconciliation.result_id": str(result.pk),
        "agent_run.id": str(agent_run.pk) if agent_run else "",
    },
) as span:
    agent_run = agent.run(ctx)
    span.set_attribute("agent.confidence", agent_run.confidence or 0.0)
    span.set_attribute("agent.tokens_total", agent_run.total_tokens or 0)
    span.set_attribute("agent.status", agent_run.status)
```

The existing `trace_id` and `span_id` on `AgentRun` can be populated from the
OTel span context:

```python
ctx_span = otel_trace.get_current_span()
if ctx_span and ctx_span.is_recording():
    ctx.trace_id = format(ctx_span.get_span_context().trace_id, "032x")
    ctx.span_id = format(ctx_span.get_span_context().span_id, "016x")
```

This makes `AgentRun.trace_id` and the OTel trace ID identical, so you can
jump from the governance DB to the Jaeger/Tempo trace in one click.

---

### 18.7 Weave / W&B (Not Recommended for This Stack)

**GitHub:** https://github.com/wandb/weave
**Pip:** `weave`
**Self-hostable:** No. The SDK is Apache 2.0, but all data is sent to `wandb.ai`.
There is no supported path to run the W&B backend on-premise.
**License:** Apache 2.0 (SDK only)

**Hard blocker for this project:** PO/invoice financial data must not be sent
to an external mandatory cloud service. Weave has no self-hosted backend option.

If evaluation dataset tracking is needed, use Phoenix's eval framework instead
(Section 18.5) -- it provides comparable LLM-as-judge evaluation templates
and runs entirely on your infrastructure.

For reference, Weave tracks agent inputs/outputs as versioned datasets and
lets you run evaluations. The `@weave.op()` decorator approach looks like this:

**Integration pattern:**

```python
# After orchestration completes, log the result to Weave:

import weave
from django.conf import settings

if getattr(settings, "WEAVE_ENABLED", False):
    weave.init(project_name="po-recon-agents")

    @weave.op()
    def log_orchestration_result(result_id, agents_executed, final_recommendation, confidence):
        return {
            "result_id": result_id,
            "agents_executed": agents_executed,
            "final_recommendation": final_recommendation,
            "confidence": confidence,
        }

    log_orchestration_result(
        result_id=orch_result.reconciliation_result_id,
        agents_executed=orch_result.agents_executed,
        final_recommendation=orch_result.final_recommendation,
        confidence=orch_result.final_confidence,
    )
```

Use case: capture every case where a human reviewer overrides the agent
recommendation. That `accepted=False` signal on `AgentRecommendation` is the
ground truth for your evaluation dataset.

---

### 18.8 What the Internal Platform Already Covers (No External Tool Needed)

Before adding any external tool, note what the built-in governance layer
already provides:

| Capability | Built-in Location |
|---|---|
| Full message-level audit trail | `AgentMessage` model + governance API |
| Tool call timing and success rate | `AgentStep.duration_ms`, `ToolCall.status` |
| Token usage per agent per run | `AgentRun.prompt_tokens`, `completion_tokens`, `total_tokens` |
| Confidence over time | `AgentRun.confidence` queryable via governance API |
| Decision audit with evidence | `DecisionLog` + `agent-performance` endpoint |
| Per-invoice full trace | `AgentTraceService.get_trace_for_invoice()` |
| RBAC / guardrail decisions | `AuditEvent` with `GUARDRAIL_GRANTED/DENIED` types |
| Inter-agent context forwarding | `ctx.extra["prior_reasoning"]` logged in each agent's `AgentMessage` |

External tools add: visual timeline UI, cross-session aggregated analytics,
evaluation scoring, and alerts. They do not replace what is already here.

---

### 18.9 Windows Compatibility for All Tools

All development on this project runs on **Windows 11 Pro**. This section
gives an exact compatibility verdict for each tool and the precise steps
needed on Windows.

#### Python SDKs -- all work natively on Windows

Every pip package listed below is pure Python and installs without issue
on Windows. No native extensions, no platform-specific compiled code.

```
pip install langfuse
pip install arize-phoenix arize-phoenix-otel
pip install openinference-instrumentation-openai
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
pip install traceloop-sdk
```

Run these inside the existing virtual environment used by the Django app.

---

#### Phoenix server -- runs natively on Windows, no Docker needed

Phoenix is the easiest option on Windows because it runs as a plain Python
process with no external dependencies.

```bash
pip install arize-phoenix

# Start the UI server (runs in a background thread inside the same Python process)
python -m phoenix.server.main serve

# Or launch programmatically from Django app startup (dev only):
# import phoenix as px
# px.launch_app()
```

The UI opens at `http://localhost:6006`. Traces are stored in SQLite under
`%USERPROFILE%\.phoenix\` by default -- no config needed.

**Windows-specific note:** `px.launch_app()` launches a background thread.
On Windows with Django's `runserver --reload`, the thread is re-created on
each code reload. Use `PHOENIX_ENABLED=true` with a conditional check and
a `threading.Event` guard to avoid duplicate launches:

```python
# In apps/agents/apps.py AgentConfig.ready():
import os
import threading

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
        px.launch_app()
        OpenAIInstrumentor().instrument()
    except Exception:
        pass
```

---

#### Langfuse Python SDK -- works natively on Windows

The `langfuse` pip package has no OS dependency. Install and use immediately:

```python
from langfuse import Langfuse
lf = Langfuse(
    public_key="...",
    secret_key="...",
    host="http://localhost:3000",   # or cloud.langfuse.com
)
```

**Langfuse self-hosted server on Windows:** The server is a Docker Compose
stack (Next.js + Postgres + Redis + worker). On Windows 11 this requires
**Docker Desktop with the WSL2 backend** (the default for Windows 11):

1. Install Docker Desktop from `https://docker.com/products/docker-desktop`.
   Enable the WSL2 integration during setup. WSL2 is included in Windows 11.

2. Clone and start:
   ```bash
   git clone https://github.com/langfuse/langfuse.git
   cd langfuse
   docker compose up -d
   ```

3. The UI is available at `http://localhost:3000`.

4. To reuse the existing project Postgres (avoid a second Postgres container),
   edit `docker-compose.yml` and replace the `db` service with the connection
   string for your local Postgres instance. The Langfuse server accepts a
   standard `DATABASE_URL` env var.

**Performance note:** Docker Desktop on Windows uses a WSL2 VM. File I/O
inside the container is fast, but mounting Windows-native paths (e.g.
`C:\...`) into the container is slow. The Langfuse server does not mount
host paths so this is not an issue.

---

#### openinference / OpenTelemetry SDK -- works natively on Windows

All `opentelemetry-*` and `openinference-*` packages are pure Python. The
`OTLPSpanExporter` sends spans over HTTP to Phoenix (`http://localhost:6006`)
or Langfuse -- no OS-level socket differences between Windows and Linux.

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http openinference-instrumentation-openai
```

No changes to the `otel_tracer.py` code shown in Section 18.6 are needed
for Windows.

---

#### Helicone self-hosted -- requires Docker Desktop (Clickhouse dependency)

Helicone's self-hosted backend requires **Clickhouse** as its analytics store.
Clickhouse has no native Windows binary; it must run in Docker. This makes
Helicone the most operationally complex option on Windows:

- Docker Desktop with WSL2 required
- Clickhouse container is memory-heavy (1 GB+ RAM)
- The proxy service and Clickhouse both run as containers

Given the alternatives (Langfuse and Phoenix) cover the same capabilities
with simpler Windows setup, Helicone is not recommended for this project.

---

#### Weave / W&B -- SDK installs on Windows but not recommended

The `weave` pip package installs on Windows. However, as noted in Section
18.7, all data is sent to `wandb.ai` (no self-hosted option), which is a
hard blocker for PO/invoice financial data regardless of OS.

---

#### Quick-start summary for Windows 11

| Tool | Windows setup | Effort |
|---|---|---|
| Phoenix server | `pip install arize-phoenix` then `python -m phoenix.server.main serve` | 2 minutes |
| openinference SDK | `pip install openinference-instrumentation-openai` | 1 minute |
| Langfuse SDK | `pip install langfuse` | 1 minute |
| Langfuse server | Docker Desktop (WSL2) + `docker compose up` | 15-20 minutes first time |
| OTel SDK | `pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http` | 1 minute |
| Helicone | Docker Desktop + Clickhouse -- not recommended | Complex |

**Fastest path on Windows:** install `arize-phoenix` +
`openinference-instrumentation-openai`, call `OpenAIInstrumentor().instrument()`
once at app startup, and open `http://localhost:6006`. Every Azure OpenAI
call across all agents is visible immediately with zero infrastructure setup.

---

### 18.10 Recommended Integration Sequence

1. **Start with Phoenix + openinference** (zero infrastructure, local dev only).
   Install `arize-phoenix` + `openinference-instrumentation-openai`. Call
   `OpenAIInstrumentor().instrument()` once at startup (see Windows guard
   pattern in Section 18.9). Set `PHOENIX_ENABLED=true`. Every Azure OpenAI
   call immediately appears in the browser at `http://localhost:6006` -- no
   DB changes, no settings changes for other team members.

2. **Add Langfuse** (staging and production). Self-host with Docker Compose
   on Windows using Docker Desktop (WSL2 backend) -- or deploy the container
   to the staging server directly. Wire `LangfuseTracer` into `BaseAgent`
   using Option A (low-level SDK, Section 18.4) so Langfuse traces map 1:1
   to `AgentRun` PKs. This gives the team a shared production dashboard for
   latency, token cost, and confidence trends.

3. **Add OTel spans around the orchestration loop** (when inter-agent
   bottleneck analysis is needed). Use `init_otel()` from Section 18.6.
   Point to Phoenix or Langfuse as the OTEL backend -- no separate Jaeger
   instance needed. Populate `AgentRun.trace_id` from the OTel span context
   so governance records and OTel traces share the same ID.

4. **Add Phoenix evals as a batch job** (when building an evaluation dataset).
   Read `AgentRecommendation.accepted=False` records from Postgres as the
   ground-truth dataset. Run `HallucinationEvaluator` and `QAEvaluator`
   against the corresponding `AgentMessage` records. Run before any
   system-prompt change to measure regression. This replaces Weave without
   requiring any external cloud dependency.
