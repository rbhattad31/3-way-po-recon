# Agent Architecture — Developer Guide

> Covers the complete agentic layer of the 3-Way PO Reconciliation Platform:
> how agents are structured, how the pipeline executes, how the deterministic
> and LLM layers interact, and a concrete upgrade path to a full reasoning
> engine without breaking the existing flow.

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
```

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
```

---

## 3. Component Inventory

| Component | File | Role |
|---|---|---|
| `AgentContext` / `AgentOutput` | `base_agent.py` | Data contracts |
| `BaseAgent` | `base_agent.py` | ReAct loop, message persistence |
| `LLMClient` | `llm_client.py` | Azure OpenAI / OpenAI wrapper |
| `PolicyEngine` | `policy_engine.py` | Decide which agents to run (no LLM) |
| `DeterministicResolver` | `deterministic_resolver.py` | Rule-based exception routing |
| `AgentOrchestrator` | `orchestrator.py` | Sequence execution, feedback, post-policy |
| `AgentGuardrailsService` | `guardrails_service.py` | RBAC enforcement |
| `AgentTraceService` | `agent_trace_service.py` | Unified governance writes |
| `DecisionLogService` | `decision_log_service.py` | Recommendation lifecycle |
| `BaseTool` / `ToolRegistry` | `tools/registry/base.py` | Tool system |
| Concrete tools (6) | `tools/registry/tools.py` | PO, GRN, vendor, invoice lookups |
| Concrete agents (8) | `agent_classes.py` | Specialised implementations |
| `AGENT_CLASS_REGISTRY` | `agent_classes.py` | AgentType -> class map |

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
| `ExceptionAnalysisAgent` | `EXCEPTION_ANALYSIS` | Yes | po_lookup, grn_lookup, invoice_details, exception_list, recon_summary | Also emits `<reviewer_summary>` block for ReviewAssignment |
| `InvoiceExtractionAgent` | `INVOICE_EXTRACTION` | Yes (temp=0, json_object) | None | Single-shot extraction; runs during upload, not reconciliation pipeline |
| `InvoiceUnderstandingAgent` | `INVOICE_UNDERSTANDING` | Yes | invoice_details, po_lookup, vendor_search | Runs when extraction confidence < threshold |
| `PORetrievalAgent` | `PO_RETRIEVAL` | Yes | po_lookup, vendor_search, invoice_details | Triggers feedback loop if PO found |
| `GRNRetrievalAgent` | `GRN_RETRIEVAL` | Yes | grn_lookup, po_lookup, invoice_details | 3-way mode only; suppressed in TWO_WAY |
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
4. policy.plan(result)                      # get AgentPlan
5. if plan.skip_agents -> auto-close or skip, return
6. partition plan.agents:
     llm_agents       = plan.agents - REPLACED_AGENTS
     deterministic_tail = plan.agents & REPLACED_AGENTS
7. build AgentContext (exceptions, extra, RBAC fields, trace IDs)
8. for agent_type in llm_agents:
     a. authorize_agent(actor, agent_type)
     b. agent_cls().run(ctx)
     c. pass forward: ctx.extra["prior_reasoning"], ctx.extra["recommendation_type"]
     d. if EXCEPTION_ANALYSIS: write reviewer summary to ReviewAssignment
     e. if agent in _RECOMMENDING_AGENTS: log_recommendation()
     f. if agent in _FEEDBACK_AGENTS: _apply_agent_findings() -> re-reconcile
9. if deterministic_tail: _apply_deterministic_resolution()
10. _resolve_final_recommendation()         # highest-confidence AgentRecommendation
11. _apply_post_policies():
     - should_auto_close() -> result.match_status = MATCHED
     - should_escalate()   -> create AgentEscalation
```

### Context Forwarding Between Agents

After each LLM agent completes, the orchestrator injects its output into
`ctx.extra` before the next agent runs:

```python
ctx.extra["prior_reasoning"] = last_output.summarized_reasoning or ""
ctx.extra["recommendation_type"] = (last_output.output_payload or {}).get("recommendation_type", "")
```

This gives each successive agent awareness of prior analysis without
re-running them.

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
completes, the orchestrator checks its output for evidence keys `found_po`,
`po_number`, or `matched_po`. If a PO number is found:

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

Subsequent agents in the pipeline then see the updated state.

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
| No cross-agent memory beyond `ctx.extra["prior_reasoning"]` | Later agents lack structured access to earlier findings |
| No planning step -- just a rule lookup | Cannot handle novel exception combinations |

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

#### Step 2 -- ReasoningPlanner (additive, feature-flagged)

The planner is a single LLM call that produces a structured execution plan.
It runs instead of `PolicyEngine.plan()` when `AGENT_REASONING_ENGINE_ENABLED=True`.
When the flag is off, the existing `PolicyEngine` runs unchanged.

```python
# apps/agents/services/reasoning_planner.py  (new file)

import json
from typing import List, Optional
from dataclasses import dataclass, field
from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.agents.services.policy_engine import AgentPlan, PolicyEngine
from apps.core.enums import AgentType
from django.conf import settings


@dataclass
class PlannedStep:
    agent_type: str
    rationale: str
    priority: int = 0


@dataclass
class ReasoningPlan:
    steps: List[PlannedStep] = field(default_factory=list)
    overall_reasoning: str = ""
    confidence: float = 0.0
    fallback_to_deterministic: bool = False


_PLANNER_SYSTEM_PROMPT = """
You are a reconciliation orchestration planner for an accounts-payable system.
Given a reconciliation result (match status, exception types, extraction confidence,
reconciliation mode), produce a minimal ordered execution plan of AI agents to
investigate and resolve the case.

Available agents:
- PO_RETRIEVAL: find a missing or mismatched Purchase Order
- GRN_RETRIEVAL: find missing Goods Receipt Note (3-way mode only)
- INVOICE_UNDERSTANDING: re-analyse invoice when extraction confidence is low
- RECONCILIATION_ASSIST: investigate a partial match discrepancy
- EXCEPTION_ANALYSIS: classify and explain reconciliation exceptions
- REVIEW_ROUTING: determine which team should review the case
- CASE_SUMMARY: produce a human-readable summary for the reviewer

Rules:
- Only include agents that are genuinely needed.
- GRN_RETRIEVAL must not appear in TWO_WAY mode.
- EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY should appear if exceptions exist.
- Return JSON only.
"""

_PLANNER_OUTPUT_SCHEMA = (
    '{"overall_reasoning": "...", "confidence": 0.9, '
    '"steps": [{"agent_type": "PO_RETRIEVAL", "rationale": "...", "priority": 1}, ...]}'
)


class ReasoningPlanner:
    """LLM-based dynamic planner. Falls back to PolicyEngine on failure."""

    def __init__(self):
        self._llm = LLMClient(temperature=0.0, max_tokens=1024)
        self._fallback = PolicyEngine()

    def plan(self, result) -> AgentPlan:
        """Produce an agent plan using the LLM, falling back to PolicyEngine."""
        if not getattr(settings, "AGENT_REASONING_ENGINE_ENABLED", False):
            return self._fallback.plan(result)

        try:
            return self._llm_plan(result)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "ReasoningPlanner LLM call failed for result %s -- falling back", result.pk
            )
            return self._fallback.plan(result)

    def _llm_plan(self, result) -> AgentPlan:
        exc_types = list(
            result.exceptions.values_list("exception_type", flat=True)
        )
        user_message = (
            f"Match status: {result.match_status}\n"
            f"Reconciliation mode: {getattr(result, 'reconciliation_mode', 'THREE_WAY')}\n"
            f"Deterministic confidence: {result.deterministic_confidence or 0.0:.2f}\n"
            f"Extraction confidence: {result.extraction_confidence or 0.0:.2f}\n"
            f"Exception types: {exc_types}\n\n"
            f"Produce a minimal agent execution plan. Respond ONLY with JSON in this schema:\n"
            f"{_PLANNER_OUTPUT_SCHEMA}"
        )
        resp = self._llm.chat(
            messages=[
                LLMMessage(role="system", content=_PLANNER_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_message),
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.content or "{}")
        steps = data.get("steps", [])
        agents = [s["agent_type"] for s in sorted(steps, key=lambda x: x.get("priority", 0))]

        # Safety: validate all agent types
        valid = {v for v in AgentType}
        agents = [a for a in agents if a in valid]

        recon_mode = getattr(result, "reconciliation_mode", "") or ""
        return AgentPlan(
            agents=agents,
            reason=data.get("overall_reasoning", "LLM-planned"),
            reconciliation_mode=recon_mode,
        )
```

Wire it into `AgentOrchestrator.__init__()`:

```python
# In orchestrator.py __init__:
from apps.agents.services.reasoning_planner import ReasoningPlanner

self.policy = ReasoningPlanner()  # replaces PolicyEngine(); falls back internally
```

No other changes needed -- `ReasoningPlanner.plan()` returns the same
`AgentPlan` dataclass that the orchestrator already consumes.

#### Step 3 -- Reflection Step (optional, additive)

Add a lightweight reflection check after each LLM agent. If the agent
found something unexpected (e.g. PO retrieved changes the exception set),
the executor can insert additional agents.

```python
# In AgentOrchestrator.execute(), after each agent run:

if getattr(settings, "AGENT_REASONING_ENGINE_ENABLED", False):
    extra_agents = self._reflect(agent_type, last_output, result, remaining_agents)
    if extra_agents:
        llm_agents = extra_agents + llm_agents   # prepend urgent new steps

def _reflect(self, completed_agent, agent_run, result, remaining):
    """Decide if new agents should be inserted based on latest findings."""
    output = agent_run.output_payload or {}
    evidence = output.get("evidence", {})

    # Example: PO was just found -- if GRN_RETRIEVAL is not already planned
    # and this is 3-way mode, add it now.
    recon_mode = getattr(result, "reconciliation_mode", "")
    if (
        completed_agent == AgentType.PO_RETRIEVAL
        and evidence.get("found_po")
        and recon_mode != "TWO_WAY"
        and AgentType.GRN_RETRIEVAL not in remaining
    ):
        return [AgentType.GRN_RETRIEVAL]

    return []
```

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

| Phase | What Changes | Existing Flow |
|---|---|---|
| Phase 1: SharedMemory | Add `AgentMemory` dataclass to `AgentContext` | Unchanged (field ignored) |
| Phase 2: ReasoningPlanner | `AgentOrchestrator` uses `ReasoningPlanner` (flag off) | Identical -- `PolicyEngine` runs as fallback |
| Phase 3: Enable planner | Set `AGENT_REASONING_ENGINE_ENABLED=True` | LLM plans the pipeline; deterministic still handles tail |
| Phase 4: Reflection | Add reflection hook after each agent | Optional inserts only; DeterministicResolver unchanged |
| Phase 5: LLM tail | Complex cases use LLM terminal agents | DeterministicResolver still handles standard cases |

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
