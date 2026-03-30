---
mode: edit
description: Add Langfuse tracing to the bulk extraction job pipeline
---

# Langfuse Tracing — Bulk Extraction Job

Add Langfuse observability to `apps/extraction/bulk_tasks.py` and `apps/extraction/services/bulk_service.py` following the project-wide tracing conventions in `apps/core/langfuse_client.py`.

## Target files

- `apps/extraction/bulk_tasks.py` — Celery task `run_bulk_job_task`
- `apps/extraction/services/bulk_service.py` — `BulkExtractionService._process_item()`
- `apps/extraction/services/bulk_source_adapters.py` — `GoogleDriveBulkSourceAdapter`, `OneDriveBulkSourceAdapter`

## What to implement

### 1. Root trace in `run_bulk_job_task`

At the start of the task, open a root trace using the job's existing `trace_id` field (or fall back to `str(job.pk)`). Close it in a `finally` block.

```python
from apps.core.langfuse_client import start_trace, end_span, score_trace

_trace_id = getattr(job, "trace_id", None) or str(job.pk)

_lf_trace = None
try:
    _lf_trace = start_trace(
        _trace_id,
        "bulk_extraction_job",
        metadata={
            "task_id": self.request.id,
            "job_pk": job.pk,
            "source_type": job.source_type,
            "total_files": job.total_files,
        },
    )
except Exception:
    pass

try:
    result = BulkExtractionService.run(job)
finally:
    try:
        if _lf_trace:
            processed = getattr(result, "processed_count", 0)
            total = getattr(result, "total_files", 1) or 1
            score_trace(
                _trace_id,
                "bulk_job_success_rate",
                processed / total,
                comment=f"processed={processed} total={total}",
            )
            end_span(_lf_trace, output={"status": job.status, "processed": processed})
    except Exception:
        pass
```

### 2. Per-item child span in `BulkExtractionService._process_item()`

Accept an optional `lf_parent` kwarg and open a child span for every file processed. Mark failed items with `level="ERROR"`.

```python
from apps.core.langfuse_client import start_span, end_span

_lf_item_span = None
try:
    if lf_parent:
        _lf_item_span = start_span(
            lf_parent,
            name="bulk_item_extraction",
            metadata={"file_name": item.file_name, "document_upload_id": item.pk},
        )
except Exception:
    pass

success = False
try:
    # ... existing extraction logic ...
    success = True
finally:
    try:
        if _lf_item_span:
            end_span(
                _lf_item_span,
                output={"success": success, "invoice_id": getattr(invoice, "pk", None)},
                level="DEFAULT" if success else "ERROR",
            )
    except Exception:
        pass
```

### 3. Connector spans in source adapters

For each network-bound method (`test_connection`, `list_files`, `download_file`), wrap the body with a short span so latency and auth failures surface in Langfuse.

```python
from apps.core.langfuse_client import start_span, end_span

_lf_span = None
try:
    if lf_trace:
        _lf_span = start_span(lf_trace, name="gdrive_list_files", metadata={"folder_id": self.folder_id})
except Exception:
    pass

try:
    # ... existing list_files logic ...
finally:
    try:
        if _lf_span:
            end_span(_lf_span, output={"file_count": len(files)})
    except Exception:
        pass
```

## Rules

- Every Langfuse call must be inside its own `try/except Exception: pass` block — never let tracing errors propagate.
- Do NOT import `langfuse` directly; use only functions from `apps.core.langfuse_client`.
- Trace ID convention: `job.trace_id` if set, else `str(job.pk)`. Do not generate a new UUID.
- Score name is exactly `bulk_job_success_rate` (float 0.0–1.0).
- Do not alter existing business logic, exception handling, or Celery retry behaviour.
