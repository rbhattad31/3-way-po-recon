---
mode: edit
description: Link ERP submission Langfuse spans to the parent posting pipeline trace
---

# Langfuse Tracing — ERP Submission Layer

The ERP submission resolver (`apps/erp_integration/services/submission/posting_submit_resolver.py`) currently creates isolated traces that are not linked to the parent posting pipeline trace. Fix this by accepting the posting pipeline's `trace_id` and attaching submission spans as children.

## Target files

- `apps/erp_integration/services/submission/posting_submit_resolver.py` — `PostingSubmitResolver.submit()`
- `apps/posting/services/posting_action_service.py` — `PostingActionService.submit_posting()`

## What to implement

### 1. Accept parent trace ID in `PostingSubmitResolver.submit()`

Add `lf_trace_id: str | None = None` to the `submit()` signature. Use it to attach the submission span to the parent trace instead of creating a new root trace.

```python
from apps.core.langfuse_client import start_span, end_span, get_client

def submit(self, posting_run, connector, lf_trace_id=None):
    _lf_span = None
    try:
        if lf_trace_id:
            # attach as child of the existing posting pipeline trace
            lf_client = get_client()
            if lf_client:
                _lf_span = lf_client.span(
                    trace_id=lf_trace_id,
                    name="erp_submission",
                    metadata={
                        "posting_run_pk": posting_run.pk,
                        "connector_type": getattr(connector, "connector_type", None),
                    },
                )
    except Exception:
        pass

    try:
        # ... existing submission logic (unchanged) ...
        result = connector.submit_invoice(payload)
        return result
    finally:
        try:
            if _lf_span:
                _lf_span.end(
                    output={
                        "success": getattr(result, "success", False),
                        "erp_reference": getattr(result, "erp_reference_id", None),
                    },
                    level="DEFAULT" if getattr(result, "success", False) else "ERROR",
                )
        except Exception:
            pass
```

### 2. Forward the trace ID from `PostingActionService.submit_posting()`

Resolve the posting pipeline's trace ID and pass it down to the resolver:

```python
_lf_trace_id = None
try:
    posting_run = PostingRun.objects.filter(
        invoice_posting=posting
    ).order_by("-created_at").first()
    _lf_trace_id = getattr(posting_run, "trace_id", None) or (
        str(posting_run.pk) if posting_run else None
    )
except Exception:
    pass

resolver = PostingSubmitResolver()
result = resolver.submit(posting_run, connector, lf_trace_id=_lf_trace_id)
```

### 3. Fallback trace for standalone submissions

If `lf_trace_id` is `None` (e.g. called directly from a test or admin action), create a short-lived root trace scoped only to the submission:

```python
from apps.core.langfuse_client import start_trace, end_span

if not lf_trace_id:
    _fallback_id = f"erp-sub-{posting_run.pk}"
    _lf_root = None
    try:
        _lf_root = start_trace(_fallback_id, "erp_submission_standalone",
                               metadata={"posting_run_pk": posting_run.pk})
    except Exception:
        pass
    # ... run submission ...
    try:
        if _lf_root:
            end_span(_lf_root, output={"success": result.success})
    except Exception:
        pass
```

## Rules

- Every Langfuse call must be wrapped in `try/except Exception: pass`.
- Do NOT import `langfuse` directly; use only `apps.core.langfuse_client`.
- The goal is to link submission spans to the parent posting pipeline trace — do not create a new root trace when `lf_trace_id` is provided.
- Do not alter the ERP connector call, retry logic, or `ERPSubmissionLog` writing.
- The `lf_trace_id` kwarg is optional with default `None` — no existing callers need to be updated in phase 1.
