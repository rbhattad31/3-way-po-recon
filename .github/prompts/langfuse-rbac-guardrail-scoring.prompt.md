---
mode: edit
description: Emit Langfuse scores for every RBAC guardrail grant/deny decision
---

# Langfuse Scoring — RBAC Guardrails

Add `score_trace` calls to `apps/agents/services/guardrails_service.py` so every guardrail decision (grant or deny) is visible in Langfuse with a named score. This makes RBAC enforcement auditable in the Langfuse dashboard without duplicating the `AuditEvent` log.

## Target file

`apps/agents/services/guardrails_service.py` — `AgentGuardrailsService`

## What to implement

### Score every guardrail decision

After every `AuditService.log_event()` call that records a guardrail outcome, emit a matching Langfuse score. The score is tied to the current agent run's trace ID.

Helper to resolve the trace ID:

```python
def _lf_trace_id_for_run(agent_run) -> str | None:
    """Return the Langfuse trace ID for agent_run, or None if unavailable."""
    try:
        return getattr(agent_run, "trace_id", None) or str(agent_run.pk)
    except Exception:
        return None
```

#### `authorize_orchestration()` — grant path

```python
from apps.core.langfuse_client import score_trace

try:
    score_trace(
        _lf_trace_id_for_run(agent_run),
        "rbac_guardrail",
        1.0,
        comment=f"authorize_orchestration GRANTED user={user.pk}",
    )
except Exception:
    pass
```

#### `authorize_orchestration()` — deny path

```python
try:
    score_trace(
        _lf_trace_id_for_run(agent_run),
        "rbac_guardrail",
        0.0,
        comment=f"authorize_orchestration DENIED user={user.pk} reason={reason}",
    )
except Exception:
    pass
```

Apply the same pattern to:

| Method | Score name | Grant value | Deny value |
|---|---|---|---|
| `authorize_orchestration` | `rbac_guardrail` | 1.0 | 0.0 |
| `authorize_agent` | `rbac_guardrail` | 1.0 | 0.0 |
| `authorize_tool` | `rbac_guardrail` | 1.0 | 0.0 |
| `authorize_recommendation` | `rbac_guardrail` | 1.0 | 0.0 |
| `authorize_data_scope` | `rbac_data_scope` | _(skip grant)_ | 0.0 |

For `authorize_data_scope`, emit a score **only on deny** (grant is the common path and would add noise).

### Score comment conventions

Include enough context to identify the actor and resource in Langfuse without PII:

```
"rbac_guardrail DENIED method=authorize_tool tool=po_lookup user_role=AP_PROCESSOR"
```

## Rules

- Every `score_trace` call must be wrapped in `try/except Exception: pass`.
- Do NOT import `langfuse` directly; use only `apps.core.langfuse_client`.
- Add scores **after** the existing `AuditService.log_event()` call — never before, never instead of.
- Do not alter guardrail logic, permission checking, exception raising, or audit logging.
- If `agent_run` is `None` (system-triggered context), skip the score silently.
- Score name for data scope is `rbac_data_scope` to distinguish from per-operation guardrails.
