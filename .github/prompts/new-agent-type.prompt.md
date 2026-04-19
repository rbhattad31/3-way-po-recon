---
mode: agent
description: "Use when adding a new agent type to the reconciliation/posting/case pipeline"
---

# New Agent Type

You are implementing a new agent type in the 3-Way PO Reconciliation platform.
Follow every step below. Do not skip steps. List all files you will create or modify before writing code.

## Step 0 -- Read Existing Architecture First

Before writing ANY code, you MUST read and understand the existing agent infrastructure.
Do not rely on summaries. Read these files in order:

### Architecture Documentation (read first)
1. `docs/AGENT_ARCHITECTURE.md` -- Full agent layer reference. Sections to focus on:
   - Section 2 (Core Runtime Model) -- `AgentContext`, `AgentMemory`, `AgentOutput` dataclasses
   - Section 3 (Orchestration Flow) -- how `ReasoningPlanner` -> `AgentOrchestrator` -> agents
   - Section 4 (Agent Catalog and Contract Model) -- `AgentDefinition` catalog fields
   - Section 5 (Tool System) -- tool specs, `@register_tool`, execution protocol
   - Section 6 (Prompting and Output Contracts) -- `AgentOutputSchema`, JSON response enforcement
   - Section 7 (Concrete Agents) -- existing 13 agent implementations and their patterns
   - Section 8 (Governance and RBAC) -- guardrails integration
   - Section 10 (Extension Guide) -- the official extension checklist

2. `docs/PROJECT.md` -- Full platform architecture. Understand where the agent layer fits in the overall data flow.

### Source Files (read to understand the runtime contract)
3. `apps/agents/services/base_agent.py` -- The LLM agent base class. Study:
   - `AgentContext` dataclass (all fields, especially RBAC fields and `_langfuse_trace`)
   - `AgentOutput` dataclass (the return type every agent must produce)
   - `BaseAgent.run()` -- the ReAct loop implementation, tool execution, confidence computation
   - `_init_messages()`, `_execute_tool()`, `_compute_composite_confidence()`
   - How `AgentRun` is created with RBAC metadata, prompt version, Langfuse spans
   - Abstract interface: `system_prompt`, `build_user_message`, `allowed_tools`, `interpret_response`

4. `apps/agents/services/deterministic_system_agent.py` -- The deterministic base class. Study:
   - How it overrides `run()` to skip the ReAct loop entirely
   - How it stubs `system_prompt`, `build_user_message`, `allowed_tools`, `interpret_response`
   - The `execute_deterministic(ctx) -> AgentOutput` abstract method
   - How it creates `AgentRun` with `llm_model_used="deterministic"` and zero tokens

5. `apps/agents/services/agent_classes.py` -- All 8 existing LLM agent implementations. Study the patterns:
   - How `system_prompt` is constructed with mode-awareness
   - How `_mode_context()` provides mode-specific instructions
   - How `interpret_response()` parses JSON and handles malformed output
   - How `AGENT_CLASS_REGISTRY` maps `AgentType` -> class

6. `apps/agents/services/system_agent_classes.py` -- All 5 deterministic agents. Study:
   - How `execute_deterministic()` produces structured `AgentOutput`
   - How `DecisionLog` entries are created
   - How audit events are emitted

7. `apps/agents/services/orchestrator.py` -- How agents are sequenced and how context flows between them.

8. `apps/agents/services/policy_engine.py` -- How the deterministic plan decides which agents to run.

9. `apps/agents/services/guardrails_service.py` -- How RBAC is enforced per-agent, per-tool, and per-recommendation.

10. `apps/agents/services/agent_output_schema.py` -- Pydantic v2 schema that validates agent JSON output.

11. `apps/agents/models.py` -- `AgentRun`, `AgentOrchestrationRun`, `AgentRecommendation`, `AgentDefinition` models.

After reading these files, confirm you understand:
- The full ReAct loop lifecycle (LLM call -> tool parse -> tool execute -> loop)
- How `AgentContext` RBAC fields flow from orchestrator to agent run
- How `AgentOutputSchema` validates and coerces output
- How `_compute_composite_confidence()` adjusts confidence based on tool success
- How `AgentMemory.record_agent_output()` shares state between agents in a pipeline
- The `AgentDefinition` catalog contract (especially `requires_tool_grounding`, `min_tool_calls`, `tool_failure_confidence_cap`)

## Pre-Implementation Checklist

After reading the architecture, confirm:
1. The agent's purpose does not overlap with any of the 13 existing agents (8 LLM + 5 deterministic).
2. You know whether this is an LLM agent (extends `BaseAgent`) or a deterministic system agent (extends `DeterministicSystemAgent`).
3. You have identified which tools the agent needs access to (from the 6 registered tools, or a new tool).
4. You have identified the recommendation types the agent may produce.
5. You have identified where in the orchestration sequence this agent should run (before/after which existing agents).
6. You understand the `AgentContext` fields your agent will consume and the `AgentOutput` fields it will produce.

## Step 1 -- Enum Registration

File: `apps/core/enums.py`

Add a new value to the `AgentType` enum class. Use UPPER_SNAKE_CASE. The string value
must match the pattern used by existing agents (e.g. `"reconciliation"`, `"po_retrieval"`).

```python
# Example
NEW_AGENT = "new_agent"
```

## Step 2 -- Agent Class

### LLM Agent

File: `apps/agents/services/agent_classes.py`

Create a new class extending `BaseAgent`. Follow the patterns in existing agents
(e.g. `ReconciliationAssistAgent`, `ExceptionAnalysisAgent`) found in the same file.

Required abstract interface (see `base_agent.py` for the exact signatures):
- `agent_type = AgentType.NEW_AGENT` -- must match the enum value from Step 1.
- `enforce_json_response = True` -- enables `AgentOutputSchema` Pydantic validation.
- `system_prompt` property -- return the system message string. Study how existing agents use `_mode_context()` to inject mode-specific instructions.
- `build_user_message(ctx: AgentContext) -> str` -- format the user message from context. Include `ctx.exceptions`, `ctx.po_number`, `ctx.reconciliation_mode`, and relevant `ctx.extra` fields. Study existing agents for the expected format.
- `allowed_tools` property -- return list of tool name strings (must match names in `apps/tools/registry/tools.py`). Cross-reference with what you set in `AgentDefinition.config_json["allowed_tools"]` in Step 5.
- `interpret_response(content: str, ctx: AgentContext) -> AgentOutput` -- parse the LLM's JSON response into an `AgentOutput`. Handle malformed JSON gracefully (log warning, return low-confidence fallback). Study `ReconciliationAssistAgent.interpret_response()` for the canonical pattern.

Runtime behavior (handled by `BaseAgent.run()` -- do not reimplement):
- ReAct loop runs up to `MAX_TOOL_ROUNDS = 6` iterations.
- `_compute_composite_confidence()` adjusts confidence based on tool success rates.
- `requires_tool_grounding` / `min_tool_calls` / `tool_failure_confidence_cap` from `AgentDefinition` are enforced automatically.
- `AgentRun` is created with RBAC metadata from `AgentContext`.
- Langfuse spans are created automatically if `ctx._langfuse_trace` is set.
- Prompt version is stamped via `PromptRegistry.version_for()`.

Additional requirements:
- Implement `_mode_context()` if the agent behaves differently in TWO_WAY vs THREE_WAY mode.
- ASCII only in all prompt text. No Unicode arrows, fancy quotes, or em dashes.
- Apply `_sanitise_text()` to any LLM output before persisting to `AgentRun.summarized_reasoning` or `DecisionLog.rationale`.

### Deterministic System Agent

File: `apps/agents/services/system_agent_classes.py`

Create a new class extending `DeterministicSystemAgent`. Follow the patterns in existing
system agents (e.g. `SystemReviewRoutingAgent`, `SystemCaseIntakeAgent`) found in the same file.

Required:
- `agent_type = AgentType.NEW_AGENT` -- must match the enum value from Step 1.
- Implement `execute_deterministic(self, ctx: AgentContext) -> AgentOutput`.
  - This replaces the entire ReAct loop. No LLM calls, no tool calls.
  - Return `AgentOutput` with `reasoning` (human-readable), `confidence` (typically 0.90-1.0 for deterministic logic), and `evidence` dict.
  - `_sanitise_text()` any text before setting it on `AgentOutput.reasoning`.

Runtime behavior (handled by `DeterministicSystemAgent.run()` -- do not reimplement):
- `AgentRun` created with `llm_model_used="deterministic"`, zero token counts.
- `DecisionLog` entries created automatically.
- Langfuse spans emitted if `ctx._langfuse_trace` is set.
- Audit events (`SYSTEM_AGENT_RUN_COMPLETED` / `SYSTEM_AGENT_RUN_FAILED`) emitted automatically.
- The abstract stubs for `system_prompt`, `build_user_message`, `allowed_tools`, `interpret_response` are already provided as no-ops -- do not override them.

Note: `DeterministicSystemAgent.__init__()` skips `BaseAgent.__init__()` and sets `self.llm = None`
because deterministic agents never require LLM API keys.

## Step 3 -- Registry

File: `apps/agents/services/agent_classes.py` (or `system_agent_classes.py`)

Add the new class to `AGENT_CLASS_REGISTRY`:

```python
AGENT_CLASS_REGISTRY[AgentType.NEW_AGENT] = NewAgentClass
```

## Step 4 -- PolicyEngine Integration

File: `apps/agents/services/policy_engine.py`

Read `PolicyEngine.plan()` to understand the existing decision matrix before modifying it.
Also read `AGENT_ARCHITECTURE.md` Section 3 for the orchestration flow.

Update `PolicyEngine.plan()` to include the new agent in the execution plan when appropriate.
Decide:
- Under which match statuses should this agent run? (PARTIAL_MATCH, UNMATCHED, REQUIRES_REVIEW, etc.)
- Should it run before or after existing agents in the sequence? (Study the current ordering in `plan()`)
- Should `PolicyEngine` suppress it in certain reconciliation modes (e.g. GRN agents are suppressed in TWO_WAY)?
- Should it be in the `llm_agents` list or the `deterministic_tail` list?

If the agent is a system agent, also update the `ReasoningPlanner` prompt (in `apps/agents/services/reasoning_planner.py`)
so the LLM planner knows this agent exists and when to include it.

## Step 5 -- AgentDefinition Seed Record

File: `apps/agents/management/commands/seed_agent_contracts.py`

Add a new `AgentDefinition` record with all catalog fields:

| Field | Required |
|---|---|
| `agent_type` | `AgentType.NEW_AGENT` |
| `purpose` | One-sentence description |
| `entry_conditions` | When should this agent be invoked |
| `success_criteria` | What constitutes a successful run |
| `prohibited_actions` | What the agent must never do |
| `allowed_recommendation_types` | JSON list of `RecommendationType` values |
| `default_fallback_recommendation` | `RecommendationType` value |
| `requires_tool_grounding` | `True` if agent must call tools before recommending |
| `min_tool_calls` | Minimum tool calls required (0 for system agents) |
| `tool_failure_confidence_cap` | Max confidence if tools fail (e.g. 0.3) |
| `output_schema_name` | `"AgentOutputSchema"` |
| `output_schema_version` | `"1.0"` |
| `lifecycle_status` | `"active"` |
| `config_json` | `{"allowed_tools": [...]}` |

## Step 6 -- RBAC Permission

File: `apps/accounts/management/commands/seed_rbac.py`

1. Add `agents.run_<agent_type>` to the `PERMISSIONS` list.
2. Map the permission to appropriate roles in `ROLE_MATRIX`. At minimum: ADMIN, FINANCE_MANAGER, SYSTEM_AGENT.
3. If this is a system agent, ensure `SYSTEM_AGENT` role has the permission.

## Step 7 -- Guardrails Registration

File: `apps/agents/services/guardrails_service.py`

Add entry to `AGENT_PERMISSIONS` dict:

```python
AgentType.NEW_AGENT: "agents.run_new_agent",
```

This enables `AgentGuardrailsService.authorize_agent()` to enforce RBAC for the new agent.

## Step 8 -- Langfuse Observability

The base classes (`BaseAgent` / `DeterministicSystemAgent`) already emit:
- Per-agent spans with `agent_type` metadata.
- `agent_confidence`, `agent_recommendation_present`, `agent_tool_success_rate` scores.

If the new agent has custom stages, add child spans:

```python
from apps.core.langfuse_client import start_span, end_span

_lf_span = start_span(self._lf_parent_span, name="custom_stage", metadata={...})
try:
    # ... stage logic ...
finally:
    end_span(_lf_span, output={...})
```

## Step 9 -- Tests

Create test file: `All_Testing/test_<agent_type>_agent.py` (or appropriate location).

Required test cases:
1. **Agent executes successfully** with valid context and produces expected output schema.
2. **RBAC denial** -- agent blocked when user lacks `agents.run_<type>` permission.
3. **Tool authorization** -- each allowed tool call passes guardrails; disallowed tools are denied.
4. **Tenant isolation** -- agent only accesses data within the tenant boundary.
5. **Registry lookup** -- `AGENT_CLASS_REGISTRY[AgentType.NEW_AGENT]` returns the correct class.
6. **PolicyEngine inclusion** -- agent appears in plan for expected match statuses.
7. **AgentOutputSchema validation** -- output conforms to Pydantic schema.
8. **ASCII output** -- `summarized_reasoning` and `rationale` contain no non-ASCII characters.
9. **System agent identity** -- if run in Celery context, uses SYSTEM_AGENT role.
10. **Idempotent recommendations** -- duplicate recommendations are deduplicated.

## Step 10 -- Migration

Run:
```
python manage.py makemigrations agents
python manage.py migrate
python manage.py seed_agent_contracts
python manage.py seed_rbac --sync-users
```

## Files Modified Summary

List all files before starting:

| File | Change |
|---|---|
| `apps/core/enums.py` | Add `AgentType` value |
| `apps/agents/services/agent_classes.py` or `system_agent_classes.py` | Agent class + registry entry |
| `apps/agents/services/policy_engine.py` | Plan inclusion logic |
| `apps/agents/management/commands/seed_agent_contracts.py` | AgentDefinition record |
| `apps/accounts/management/commands/seed_rbac.py` | Permission + role mapping |
| `apps/agents/services/guardrails_service.py` | AGENT_PERMISSIONS entry |
| Test file(s) | 10+ test cases |

## Constraints

- Tenant isolation: the agent must never access data outside the current tenant boundary. Use `BaseTool._scoped()` in tools, `scoped_queryset()` in services.
- RBAC: every agent operation is gated by `AgentGuardrailsService`. Do not bypass.
- Audit: `AgentTraceService` records every run, step, tool call, and decision. The base class handles this -- do not disable it.
- Recommendations must be idempotent (two-layer dedup via `DecisionLogService` + model `UniqueConstraint`).
- If this agent calls tools, set `requires_tool_grounding = True` and enforce `min_tool_calls` > 0.
- If tools fail, cap confidence at `tool_failure_confidence_cap` (e.g. 0.3).
- Output must pass `AgentOutputSchema` validation (`enforce_json_response = True`).
