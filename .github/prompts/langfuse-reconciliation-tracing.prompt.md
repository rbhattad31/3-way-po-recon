---
mode: edit
description: Add Langfuse tracing to the reconciliation Celery task and runner service
---

# Langfuse Tracing — Reconciliation Pipeline

Add Langfuse observability to the reconciliation task and runner service so every reconciliation run appears as a linked trace with a `reconciliation_match` quality score.

## Target files

- `apps/reconciliation/tasks.py` — `run_reconciliation_task`
- `apps/reconciliation/services/runner_service.py` — `ReconciliationRunnerService.run()`

## What to implement

### 1. Root trace in `run_reconciliation_task`

Resolve the trace ID from the `ReconciliationRun` record's `trace_id` field (or fall back to `str(run.pk)`). Open the trace before calling the service and close it after.

```python
from apps.core.langfuse_client import start_trace, end_span

_trace_id = getattr(run, "trace_id", None) or str(run.pk)
_lf_trace = None
try:
    _lf_trace = start_trace(
        _trace_id,
        "reconciliation_task",
        metadata={
            "task_id": self.request.id,
            "run_pk": run.pk,
            "invoice_id": run.invoice_id,
        },
    )
except Exception:
    pass

try:
    ReconciliationRunnerService.run(run, lf_trace=_lf_trace)
finally:
    try:
        if _lf_trace:
            end_span(_lf_trace, output={"run_status": run.status})
    except Exception:
        pass
```

Pass `lf_trace` as a kwarg into `ReconciliationRunnerService.run()` so child spans can attach to it.

### 2. Child spans per stage in `ReconciliationRunnerService`

Add `lf_trace=None` to the `run()` signature. Wrap each major stage:

| Stage | Span name |
|---|---|
| Mode resolution | `"recon_mode_resolution"` |
| 2-way / 3-way matching | `"recon_matching"` |
| Exception building | `"recon_exception_build"` |
| Result persistence | `"recon_result_persist"` |

Pattern for each stage:

```python
from apps.core.langfuse_client import start_span, end_span

_lf_span = None
try:
    if lf_trace:
        _lf_span = start_span(lf_trace, name="recon_matching", metadata={"mode": recon_mode})
except Exception:
    pass

try:
    # ... existing matching logic ...
finally:
    try:
        if _lf_span:
            end_span(_lf_span, output={"match_status": result.match_status})
    except Exception:
        pass
```

### 3. `reconciliation_match` score after classification

Immediately after `result.match_status` is determined (before returning from `run()`), emit the score:

```python
from apps.core.langfuse_client import score_trace

_MATCH_SCORES = {
    "MATCHED": 1.0,
    "PARTIAL_MATCH": 0.5,
    "REQUIRES_REVIEW": 0.3,
    "UNMATCHED": 0.0,
}

try:
    score_trace(
        _trace_id,
        "reconciliation_match",
        _MATCH_SCORES.get(result.match_status, 0.0),
        comment=f"mode={recon_mode} match_status={result.match_status}",
    )
except Exception:
    pass
```

## Rules

- Add `lf_trace=None` to service method signatures — this is an optional kwarg, existing callers are unaffected.
- Every Langfuse call must be inside its own `try/except Exception: pass` — tracing must never break reconciliation.
- Do NOT import `langfuse` directly; use only `apps.core.langfuse_client`.
- Trace ID convention: `run.trace_id` if set, else `str(run.pk)`. Do not generate a new UUID.
- Do not alter the reconciliation matching logic, tolerance engine, or exception building logic.
