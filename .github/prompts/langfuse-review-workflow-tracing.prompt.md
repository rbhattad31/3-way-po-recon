---
mode: edit
description: Add Langfuse tracing and scores to the review assignment workflow
---

# Langfuse Tracing — Review Workflow

Add Langfuse observability to `apps/reviews/services.py` so review assignments appear as traces with `review_priority` and `review_decision` quality scores.

## Target file

`apps/reviews/services.py` — `ReviewService`

## What to implement

### 1. Trace ID per review assignment

Derive the trace ID from the assignment: `f"review-{assignment.pk}"`. This keeps review traces independent from the reconciliation trace (which is already scored separately).

```python
_lf_trace_id = f"review-{assignment.pk}"
```

### 2. Root trace on `create_assignment()`

After the `ReviewAssignment` record is saved, open a root trace and immediately emit the `review_priority` score.

```python
from apps.core.langfuse_client import start_trace, score_trace

_lf_trace_id = f"review-{assignment.pk}"
try:
    start_trace(
        _lf_trace_id,
        "review_assignment",
        metadata={
            "assignment_pk": assignment.pk,
            "reconciliation_result_id": assignment.reconciliation_result_id,
            "assigned_to": getattr(assignment.assigned_to, "pk", None),
            "review_type": assignment.review_type,
        },
    )
except Exception:
    pass

try:
    score_trace(
        _lf_trace_id,
        "review_priority",
        float(assignment.priority) / 10.0,   # normalise to 0.0-1.0
        comment=f"priority={assignment.priority} review_type={assignment.review_type}",
    )
except Exception:
    pass
```

### 3. Lifecycle spans

Wrap each state transition with a child span using `get_client().span(trace_id=..., name=...)`.

| Method | Span name |
|---|---|
| `assign_reviewer()` | `"review_assign_reviewer"` |
| `start_review()` | `"review_start"` |
| `_finalise()` | `"review_finalise"` |

Pattern:

```python
from apps.core.langfuse_client import get_client

_lf_trace_id = f"review-{assignment.pk}"
_lf_span = None
try:
    lf = get_client()
    if lf:
        _lf_span = lf.span(
            trace_id=_lf_trace_id,
            name="review_assign_reviewer",
            metadata={"reviewer_id": reviewer.pk},
        )
except Exception:
    pass

try:
    # ... existing assignment logic unchanged ...
finally:
    try:
        if _lf_span:
            _lf_span.end(output={"status": assignment.status})
    except Exception:
        pass
```

### 4. `review_decision` score on `_finalise()`

After the decision is recorded, emit the outcome score:

```python
from apps.core.langfuse_client import score_trace

_DECISION_SCORES = {
    "APPROVED": 1.0,
    "REPROCESSED": 0.5,
    "REJECTED": 0.0,
}

try:
    score_trace(
        f"review-{assignment.pk}",
        "review_decision",
        _DECISION_SCORES.get(decision.decision_type, 0.5),
        comment=f"decision={decision.decision_type} decided_by={decided_by.pk}",
    )
except Exception:
    pass
```

## Rules

- Every Langfuse call must be wrapped in `try/except Exception: pass`.
- Do NOT import `langfuse` directly; use only `apps.core.langfuse_client`.
- Trace ID is always `f"review-{assignment.pk}"` — consistent across all methods.
- `review_priority` is `float(assignment.priority) / 10.0` (normalised). Clamp to 0.0–1.0 if the priority field can exceed 10.
- `review_decision` scores: APPROVED=1.0, REPROCESSED=0.5, REJECTED=0.0. Default 0.5 for unexpected values.
- Do not alter review status transitions, `ReviewDecision` creation, or `ManualReviewAction` logging.
