---
description: "Use when working on the agent system, agent tools, policy engine, guardrails service, supervisor agent, or AgentDefinition records. Covers ReAct loop, tool-calling format, RBAC guardrails, AgentOutputSchema, and the SYSTEM_AGENT identity pattern."
applyTo: "apps/agents/**/*.py,apps/tools/**/*.py"
---
# Agent System Conventions

## Agent Architecture
- LLM agents: extend `BaseAgent` — ReAct loop, max 6 iterations, OpenAI-compliant tool-calling
- Deterministic agents: extend `DeterministicSystemAgent` — skip ReAct, implement `execute_deterministic(ctx)`
- Supervisor agent: extends `BaseAgent` with 5-phase skill composition, 15 rounds, 30+ tools
- `AgentOrchestrator` -> `ReasoningPlanner` (LLM, always active) -> fallback `PolicyEngine` (deterministic)

## Tool-Calling Format (OpenAI-compliant)
- Assistant messages: include `tool_calls` array
- Tool response messages: include `tool_call_id` AND `name` fields
- This format is REQUIRED — deviation causes 400 errors from OpenAI API

## RBAC Guardrails (Non-Negotiable)
- `AgentGuardrailsService` enforces all RBAC — NEVER bypass
- Sequence: orchestration permission -> per-agent permission -> data-scope authorization -> per-tool permission
- Tool `required_permission` must match a seeded Permission code
- All guardrail decisions (grant/deny) are logged as `AuditEvent` records

## AgentOutputSchema
- All LLM agent JSON output is validated via `AgentOutputSchema` (Pydantic v2)
- `recommendation_type`: invalid values coerced to `SEND_TO_AP_REVIEW`
- `confidence`: clamped to [0.0, 1.0]
- Applied via `enforce_json_response=True` on `BaseAgent`

## SYSTEM_AGENT Identity
- When no human user context is available (Celery, system-triggered): `AgentGuardrailsService.resolve_actor()` returns `system-agent@internal` with `SYSTEM_AGENT` role
- SYSTEM_AGENT (rank 100, `is_system_role=True`) bypasses scope checks

## Idempotent Recommendations
- Two-layer dedup: `DecisionLogService.log_recommendation()` + model `UniqueConstraint` on `(reconciliation_result, recommendation_type, agent_run)` + `IntegrityError` guard

## AgentDefinition Catalog Fields (all are DB columns, not in config_json)
`purpose`, `entry_conditions`, `success_criteria`, `prohibited_actions`, `allowed_recommendation_types`, `default_fallback_recommendation`, `requires_tool_grounding`, `min_tool_calls`, `tool_failure_confidence_cap`, `output_schema_name`, `output_schema_version`, `lifecycle_status`, `owner_team`, `capability_tags`, `domain_tags`, `human_review_required_conditions`

## Adding a New Agent — Checklist
1. Add `AgentType` enum value in `apps/core/enums.py`
2. Create agent class in `apps/agents/services/agent_classes.py` or `system_agent_classes.py`
3. Add to `AGENT_CLASS_REGISTRY`
4. Add to `PolicyEngine` decision logic
5. Add `agents.run_<type>` permission to `seed_rbac.py` PERMISSIONS + ROLE_MATRIX
6. Add to `AGENT_PERMISSIONS` in `guardrails_service.py`
7. Create `AgentDefinition` DB record via admin or seed command
