---
description: "Add Langfuse tracing (spans, scores, generations) to a service, task, or pipeline stage. Enforces the platform's fail-silent guard pattern, score naming conventions, span=argument requirement, and trace_id conventions."
agent: agent
argument-hint: "What to instrument (e.g. 'add Langfuse spans to the NonPOInvoiceValidationService')"
tools: [read, edit, search]
---

Add Langfuse LLM observability tracing to the specified component.

**Step 1 — Read the Target**
- Read the target service/task file completely
- Identify: entry points (methods that start a pipeline), LLM calls, key decision points

**Step 2 — Determine Trace ID**
- Read `docs/LANGFUSE_OBSERVABILITY.md` section on trace ID conventions
- Select the appropriate `trace_id` pattern for this pipeline context

**Step 3 — Add Root Trace (for task-level entry)**
```python
from apps.core.langfuse_client import start_trace, end_span, score_trace
_lf_trace = start_trace(
    _trace_id, "pipeline_name",
    metadata={"entity_id": obj.pk, "tenant_id": tenant_id}
)
try:
    # ... pipeline work
finally:
    try:
        end_span(_lf_trace, output={"status": "done"})
    except Exception:
        pass
```

**Step 4 — Add Stage Spans**
- Wrap each pipeline stage in `start_span` / `end_span` with `level="ERROR"` on failure
- Always call `end_span()` in a `finally` block — never leave spans open

**Step 5 — Add Scores**
- Emit scores using `score_trace(_trace_id, "score_name", float_value, comment=..., span=_lf_span)`
- ALWAYS pass `span=` — without it scores are orphaned in Langfuse SDK v4
- Use only score names from the conventions table in `copilot-instructions.md`
- New score names must be added to the conventions table

**Step 6 — Guard ALL Langfuse Calls**
- Wrap every Langfuse call in `try/except Exception: pass`
- Langfuse errors MUST NEVER propagate to the caller

**Target**: $input
