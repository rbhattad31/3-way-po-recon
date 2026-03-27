---
mode: edit
description: Add Langfuse tracing to the 9-stage posting pipeline and confidence scoring
---

# Langfuse Tracing — Invoice Posting Pipeline

Add Langfuse observability to `apps/posting_core/services/posting_pipeline.py` so every posting run appears as a root trace containing one child span per pipeline stage, with `posting_confidence` and `posting_requires_review` quality scores.

## Target files

- `apps/posting_core/services/posting_pipeline.py` — `PostingPipeline.run()`
- `apps/posting/services/posting_orchestrator.py` — `PostingOrchestrator.prepare_posting()`

## What to implement

### 1. Root trace in `PostingPipeline.run()`

Resolve the trace ID from `posting_run.trace_id` (or fall back to `str(posting_run.pk)`). Open it before stage 1 and close it after stage 9.

```python
from apps.core.langfuse_client import start_trace, end_span, score_trace

_trace_id = getattr(posting_run, "trace_id", None) or str(posting_run.pk)
_lf_trace = None
try:
    _lf_trace = start_trace(
        _trace_id,
        "posting_pipeline",
        metadata={
            "posting_run_pk": posting_run.pk,
            "invoice_id": posting_run.invoice_posting.invoice_id,
            "connector_used": bool(self._connector),
        },
    )
except Exception:
    pass
```

Close in the existing `finally` block (or add one):

```python
finally:
    try:
        if _lf_trace:
            end_span(_lf_trace, output={"final_status": posting_run.status})
    except Exception:
        pass
```

### 2. Child span per pipeline stage

The pipeline has 9 stages. Wrap each `_run_stage_N()` call with a span. Use `level="ERROR"` when a stage fails.

```python
from apps.core.langfuse_client import start_span, end_span

_STAGE_NAMES = {
    1: "eligibility_check",
    2: "snapshot_build",
    3: "mapping",
    4: "validation",
    5: "duplicate_check",
    6: "confidence_scoring",
    7: "review_routing",
    8: "payload_build",
    9: "finalization",
}

for stage_num, stage_fn in enumerate(self._stages, start=1):
    _lf_span = None
    try:
        if _lf_trace:
            _lf_span = start_span(
                _lf_trace,
                name=_STAGE_NAMES.get(stage_num, f"stage_{stage_num}"),
                metadata={"stage": stage_num},
            )
    except Exception:
        pass

    stage_ok = False
    try:
        stage_fn(posting_run)
        stage_ok = True
    finally:
        try:
            if _lf_span:
                end_span(
                    _lf_span,
                    output={"passed": stage_ok, "status": posting_run.status},
                    level="DEFAULT" if stage_ok else "ERROR",
                )
        except Exception:
            pass
```

### 3. Quality scores after key stages

After stage 6 (confidence scoring), emit `posting_confidence`:

```python
try:
    score_trace(
        _trace_id,
        "posting_confidence",
        float(posting_run.composite_confidence or 0.0),
        comment=f"is_touchless={posting_run.is_touchless}",
    )
except Exception:
    pass
```

After stage 7 (review routing), emit `posting_requires_review`:

```python
try:
    score_trace(
        _trace_id,
        "posting_requires_review",
        0.0 if posting_run.is_touchless else 1.0,
        comment=f"review_queue={posting_run.review_queue}",
    )
except Exception:
    pass
```

### 4. Pass `_trace_id` to `PostingOrchestrator`

In `PostingOrchestrator.prepare_posting()`, extract the trace ID before calling `PostingPipeline.run()` so it can be forwarded to the ERP submission layer later:

```python
_trace_id = getattr(posting_run, "trace_id", None) or str(posting_run.pk)
PostingPipeline(connector=connector).run(posting_run)
```

## Rules

- Every Langfuse call must be wrapped in `try/except Exception: pass`.
- Do NOT import `langfuse` directly; use only `apps.core.langfuse_client`.
- Trace ID convention: `posting_run.trace_id` if set, else `str(posting_run.pk)`.
- Score names are exactly `posting_confidence` (float 0.0–1.0) and `posting_requires_review` (0.0 or 1.0).
- Do not alter pipeline stage logic, status transitions, or error handling.
- `is_touchless` and `composite_confidence` are existing fields on `PostingRun` — do not rename or recalculate them.
