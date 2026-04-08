# Celery in the 3-Way PO Reconciliation Platform

## Overview

Celery is the distributed task queue used to run all long-running, asynchronous, and pipeline-driven work in the platform. It decouples HTTP request handling from heavy computation (LLM extraction, 3-way matching, agent execution, ERP import) and prevents blocking the web process.

**Technology stack:**
- Broker: Redis (`redis://127.0.0.1:6379/0` by default)
- Result backend: `django-db` (task results stored in the database via `django-celery-results`)
- Worker: Celery 5.x running as a `systemd` service in production
- Scheduler: Celery Beat (configured but no periodic tasks registered yet)
- Monitor: Flower web UI exposed at `/flower/`

---

## Table of Contents

1. [Configuration](#1-configuration)
2. [Task Inventory](#2-task-inventory)
3. [Task Patterns and Conventions](#3-task-patterns-and-conventions)
4. [Task Chaining](#4-task-chaining)
5. [Queues](#5-queues)
6. [Windows / Dev Mode](#6-windows--dev-mode)
7. [Utility Helpers](#7-utility-helpers)
8. [Observability](#8-observability)
9. [Deployment](#9-deployment)
10. [Monitoring with Flower](#10-monitoring-with-flower)
11. [Adding a New Task](#11-adding-a-new-task)

---

## 1. Configuration

### Celery Application (`config/celery.py`)

```python
app = Celery("po_recon")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.task_default_queue = "default"
app.autodiscover_tasks()
```

`autodiscover_tasks()` scans every installed Django app for a `tasks.py` module and registers all `@shared_task` decorated functions automatically.

### Django Settings (`config/settings.py`)

| Setting | Default | Purpose |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/0` | Redis message broker |
| `CELERY_RESULT_BACKEND` | `django-db` | Persist task results in MySQL via django-celery-results |
| `CELERY_ACCEPT_CONTENT` | `["json"]` | Only accept JSON-serialized messages |
| `CELERY_TASK_SERIALIZER` | `json` | Serialize task arguments as JSON |
| `CELERY_RESULT_SERIALIZER` | `json` | Serialize task results as JSON |
| `CELERY_TIMEZONE` | Same as `TIME_ZONE` | Task timezone (Asia/Kolkata) |
| `CELERY_TASK_TRACK_STARTED` | `True` | Update task state to STARTED when worker picks it up |
| `CELERY_TASK_DEFAULT_QUEUE` | `default` | Queue used when no explicit queue is given |
| `CELERY_TASK_ALWAYS_EAGER` | `True` (dev) | Run tasks synchronously without a worker (Windows dev) |
| `CELERY_TASK_EAGER_PROPAGATES` | Same as `ALWAYS_EAGER` | Re-raise exceptions from eager tasks |

Override broker URL in production:
```bash
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_TASK_ALWAYS_EAGER=False
```

---

## 2. Task Inventory

### 2.1 Extraction — `apps/extraction/tasks.py`

#### `process_invoice_upload_task(upload_id, credit_ref_type, credit_ref_id)`

End-to-end single-invoice extraction pipeline.

| Parameter | Type | Description |
|---|---|---|
| `upload_id` | `int` | `DocumentUpload` PK |
| `credit_ref_type` | `str` | Credit reservation reference type (default `"document_upload"`) |
| `credit_ref_id` | `str` | Credit reservation ID |

**Retry policy:** `max_retries=2`, `default_retry_delay=30s`

**Steps executed:**
1. Download file from Azure Blob Storage
2. Run OCR via Azure Document Intelligence
3. Parse raw extraction into structured dataclass
4. Normalize fields (dates, amounts, tax numbers)
5. Validate mandatory fields and confidence thresholds
6. Duplicate invoice detection
7. Persist `Invoice` + `InvoiceLineItem` + `ExtractionResult`
8. Transition upload and invoice statuses

**Triggered by:** `DocumentUpload` view (`apps/documents/template_views.py`) immediately after file upload.

---

### 2.2 Bulk Extraction — `apps/extraction/bulk_tasks.py`

#### `run_bulk_job_task(job_id)`

Execute a `BulkExtractionJob`: discovers files from a configured source (Google Drive, OneDrive, S3) and processes each through `process_invoice_upload_task`.

| Parameter | Type | Description |
|---|---|---|
| `job_id` | `int` | `BulkExtractionJob` PK |

**Retry policy:** `max_retries=1`, `default_retry_delay=60s`

**Triggered by:** Admin or API when a bulk job is created.

**Langfuse:** Emits root trace `"bulk_extraction_job"` and scores `bulk_job_success_rate` at completion.

---

### 2.3 Reconciliation — `apps/reconciliation/tasks.py`

#### `run_reconciliation_task(invoice_ids, config_id, triggered_by_id)`

Execute a full reconciliation run for one or more invoices.

| Parameter | Type | Description |
|---|---|---|
| `invoice_ids` | `list[int] \| None` | Specific invoices to reconcile; `None` = all `READY_FOR_RECON` |
| `config_id` | `int \| None` | `ReconciliationConfig` PK; falls back to the default config |
| `triggered_by_id` | `int \| None` | User PK who triggered the run |

**Retry policy:** `max_retries=1`, `default_retry_delay=60s`

**Post-run chaining:** After the run completes, the task automatically dispatches `run_agent_pipeline_task` for every `ReconciliationResult` that is not `MATCHED`.

**Returns:**
```python
{
    "status": "ok",
    "run_id": int,
    "total_invoices": int,
    "matched": int,
    "partial": int,
    "unmatched": int,
    "errors": int,
    "review": int,
    "agent_tasks_dispatched": int,
}
```

**Triggered by:** "Start Reconciliation" view (`apps/reconciliation/template_views.py`).

#### `reconcile_single_invoice_task(invoice_id, config_id)`

Convenience wrapper that runs `run_reconciliation_task` for a single invoice inline (no extra worker hop).

---

### 2.4 Agent Pipeline — `apps/agents/tasks.py`

#### `run_agent_pipeline_task(reconciliation_result_id, actor_user_id)`

Execute the full 8-agent ReAct pipeline for one `ReconciliationResult`.

| Parameter | Type | Description |
|---|---|---|
| `reconciliation_result_id` | `int` | `ReconciliationResult` PK |
| `actor_user_id` | `int \| None` | User PK; `None` activates the SYSTEM_AGENT identity |

**Retry policy:** `max_retries=1`, `default_retry_delay=30s`

**RBAC:** Resolved via `AgentGuardrailsService.resolve_actor()`. When `actor_user_id` is `None`, the SYSTEM_AGENT service account (`system-agent@internal`, role rank 100) is used.

**Langfuse:** Opens a wrapper trace `"agent_pipeline_task"` tied to the Celery `task_id`, nesting over the orchestrator's internal `"agent_pipeline"` trace.

**Returns:**
```python
{
    "reconciliation_result_id": int,
    "agents_executed": list[str],
    "final_recommendation": str,
    "final_confidence": float,
    "skipped": bool,
    "skip_reason": str | None,
    "error": str | None,
}
```

**Triggered by:** `run_reconciliation_task` (automatic), or directly from `start_reconciliation` view for synchronous mode.

---

### 2.5 Cases — `apps/cases/tasks.py`

#### `process_case_task(case_id)`

Run the full `CaseOrchestrator` pipeline for an `APCase`.

| Parameter | Type | Description |
|---|---|---|
| `case_id` | `int` | `APCase` PK |

**Retry policy:** `max_retries=3`, `default_retry_delay=30s`, `acks_late=True`

**Triggered by:** Post-extraction and post-approval hooks, or manual reprocessing.

#### `reprocess_case_from_stage_task(case_id, stage)`

Re-run a case from a specific pipeline stage (e.g. after a field correction).

| Parameter | Type | Description |
|---|---|---|
| `case_id` | `int` | `APCase` PK |
| `stage` | `str` | Stage name to resume from |

**Retry policy:** `max_retries=2`, `default_retry_delay=10s`, `acks_late=True`

---

### 2.6 Posting — `apps/posting/tasks.py`

#### `prepare_posting_task(invoice_id, user_id, trigger)`

Prepare an ERP posting proposal for an invoice by running the 9-stage posting pipeline.

| Parameter | Type | Description |
|---|---|---|
| `invoice_id` | `int` | `Invoice` PK |
| `user_id` | `int \| None` | User who triggered it (`None` = system) |
| `trigger` | `str` | `"approval"`, `"auto_approval"`, `"manual"`, or `"system"` |

**Retry policy:** `max_retries=2`, `default_retry_delay=60s`, `acks_late=True`

**Decorated with:** `@observed_task("posting.prepare_posting", audit_event="POSTING_STARTED")`

**Triggered by:**
- Automatically: `ExtractionApprovalService.approve()` and `try_auto_approve()` after extraction approval.
- Manually: `POST /api/v1/posting/prepare/` API endpoint.

#### `import_reference_excel_task(file_path, batch_type, user_id, source_as_of, column_map)`

Import ERP reference master data (vendors, items, tax codes, cost centers, open POs) from an Excel/CSV file.

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `str` | Absolute path to the uploaded temp file |
| `batch_type` | `str` | `VENDOR`, `ITEM`, `TAX_CODE`, `COST_CENTER`, or `PO` |
| `user_id` | `int \| None` | Uploader user PK |
| `source_as_of` | `str \| None` | ISO date of the data snapshot |
| `column_map` | `dict \| None` | Optional header overrides |

**Retry policy:** `max_retries=1`, `default_retry_delay=30s`, `acks_late=True`

**Triggered by:** ERP reference import view at `/posting/imports/` (`apps/posting_core/views.py`).

---

### 2.7 Procurement — `apps/procurement/tasks.py`

#### `run_analysis_task(run_id)`

Execute an `AnalysisRun` — dispatches to the correct service based on `run_type` (RECOMMENDATION, BENCHMARK, or VALIDATION).

| Parameter | Type | Description |
|---|---|---|
| `run_id` | `int` | `AnalysisRun` PK |

**Retry policy:** `max_retries=2`, `default_retry_delay=30s`

**Triggered by:** Procurement request workspace (template view and API).

#### `run_validation_task(run_id, *, agent_enabled)`

Run deterministic validators plus optional `ValidationAgent` for a procurement request.

| Parameter | Type | Description |
|---|---|---|
| `run_id` | `int` | `AnalysisRun` PK |
| `agent_enabled` | `bool` | Enable the LLM validation agent |

**Retry policy:** `max_retries=2`, `default_retry_delay=30s`

#### `run_request_prefill_task(request_id)`

OCR + LLM extraction to auto-populate a `ProcurementRequest` from an attached document.

| Parameter | Type | Description |
|---|---|---|
| `request_id` | `int` | `ProcurementRequest` PK |

**Retry policy:** `max_retries=2`, `default_retry_delay=30s`

**Triggered by:** Request creation view when a source document is attached.

#### `run_quotation_prefill_task(quotation_id)`

OCR + LLM extraction to auto-populate a `SupplierQuotation` from an attached document.

| Parameter | Type | Description |
|---|---|---|
| `quotation_id` | `int` | `SupplierQuotation` PK |

**Retry policy:** `max_retries=2`, `default_retry_delay=30s`

**Triggered by:** Quotation creation view when a document is attached.

---

## 3. Task Patterns and Conventions

### `@shared_task(bind=True)` with retry parameters

Every production task uses `bind=True` to access `self` (the task instance) for retries, `max_retries`, a `default_retry_delay`, and typically `acks_late=True` for at-least-once delivery on failure.

```python
@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
def my_task(self, entity_id: int) -> dict:
    ...
```

### Lazy imports inside task bodies

All Django model and service imports are deferred inside the function body. This avoids circular import issues at module load time, since `tasks.py` files are imported early by Celery's autodiscovery.

```python
@shared_task(bind=True, max_retries=2)
def process_invoice_upload_task(self, upload_id: int) -> dict:
    from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter
    from apps.documents.models import DocumentUpload
    ...
```

### Retry via `safe_retry()`

Never call `raise self.retry(exc=exc)` directly. Always use the `safe_retry()` helper from `apps.core.utils` so the task degrades gracefully when Redis is unavailable (Windows dev / CI):

```python
from apps.core.utils import safe_retry

except Exception as exc:
    safe_retry(self, exc)
```

### `acks_late=True`

High-value tasks (`prepare_posting_task`, `import_reference_excel_task`, `process_case_task`, `reprocess_case_from_stage_task`) use `acks_late=True`. The message is not acknowledged until the task completes, so a worker crash causes the broker to re-deliver the message rather than lose it.

### Structured return dicts

Tasks return plain JSON-serializable dicts so results can be inspected via `django-celery-results` or polled via the Celery API:

```python
return {
    "status": "ok",
    "run_id": run.pk,
    "total_invoices": run.total_invoices,
}
```

### Tenant propagation

All tenant-aware tasks accept `tenant_id` as a parameter. The tenant is resolved
from `CompanyProfile` at the start of the task and used for:
1. Guarding entity fetches: `qs.filter(tenant=tenant)` when tenant is set.
2. Propagating to child records and downstream services.
3. Passing to sub-tasks (e.g., `run_agent_pipeline_task.delay(tenant_id=..., ...)`).

```python
@shared_task(bind=True, max_retries=2, acks_late=True)
def process_case_task(self, tenant_id=None, case_id=0):
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    qs = APCase.objects.all()
    if tenant:
        qs = qs.filter(tenant=tenant)
    case = qs.get(id=case_id)
    ...
```

See [MULTI_TENANT.md](MULTI_TENANT.md) for the full multi-tenant architecture.

---

## 4. Task Chaining

Celery tasks are chained by dispatching child tasks from within a parent task body using `dispatch_task()` rather than Celery Canvas primitives. This keeps the chain simple and observable.

```
DocumentUpload (web request)
    |
    v
process_invoice_upload_task          [extraction app]
    |  (on approval)
    v
prepare_posting_task                 [posting app]

run_reconciliation_task              [reconciliation app]
    |  (for each non-MATCHED result)
    v
run_agent_pipeline_task              [agents app]
```

**Reconciliation -> Agent chain (in `apps/reconciliation/tasks.py`):**

```python
agent_result_ids = list(
    ReconciliationResult.objects.filter(run=run)
    .exclude(match_status="MATCHED")
    .values_list("pk", flat=True)
)
for result_id in agent_result_ids:
    dispatch_task(run_agent_pipeline_task, result_id, actor_id)
```

**Extraction Approval -> Posting chain (in `apps/extraction/services/approval_service.py`):**

```python
prepare_posting_task.delay(
    invoice_id=invoice.pk,
    user_id=user_id,
    trigger="approval",
)
```

This is best-effort: if the dispatch fails it is logged and ignored — approval is never blocked.

---

## 5. Queues

The production worker consumes 5 named queues:

| Queue | Purpose |
|---|---|
| `default` | General-purpose fallback for all tasks not assigned a specific queue |
| `agents` | Agent pipeline tasks (`run_agent_pipeline_task`) |
| `reconciliation` | Reconciliation runs (`run_reconciliation_task`) |
| `extraction` | Invoice extraction (`process_invoice_upload_task`, `run_bulk_job_task`) |
| `scheduled` | Reserved for future Celery Beat periodic tasks |

All current tasks use the `default` queue because no explicit `queue=` argument is set in the task decorators. Named queues are declared at worker startup (see `--queues` flag in the systemd unit) and are ready for future routing via Celery's `task_routes` setting.

---

## 6. Windows / Dev Mode

On Windows without Redis, Celery cannot connect to a broker. Two env-var flags make the entire stack work synchronously:

```bash
CELERY_TASK_ALWAYS_EAGER=True     # default in settings.py
CELERY_TASK_EAGER_PROPAGATES=True # propagates exceptions from eager tasks
```

When `ALWAYS_EAGER=True`, calling `.delay()` or `.apply_async()` on any task runs it **synchronously in the same process** — no worker or Redis is needed.

### `dispatch_task()` helper

Views and services that call `.delay()` directly may silently fail if the broker drops. Use `dispatch_task()` from `apps.core.utils` for an extra safety layer:

```python
from apps.core.utils import dispatch_task

dispatch_task(run_agent_pipeline_task, result_id, actor_id)
```

Internally it tries `.delay()` first, then falls back to `.run()` (synchronous) if the broker raises any exception:

```python
def dispatch_task(task, *args, **kwargs):
    try:
        return task.delay(*args, **kwargs)
    except Exception:
        return task.run(*args, **kwargs)
```

### `safe_retry()` helper

When `ALWAYS_EAGER=True` and `self.retry()` is called, Celery raises a `TypeError` because there is no worker context. `safe_retry()` catches this and re-raises the original exception instead of crashing:

```python
def safe_retry(task_self, exc):
    try:
        raise task_self.retry(exc=exc)
    except (AttributeError, TypeError):
        raise exc  # no worker context — propagate directly
    except Exception as retry_exc:
        if "Connection" in type(retry_exc).__name__:
            raise exc  # broker unavailable
        raise
```

---

## 7. Utility Helpers

All Celery helpers live in `apps/core/utils.py`.

| Helper | Purpose |
|---|---|
| `dispatch_task(task, *args, **kwargs)` | Safe `.delay()` with synchronous fallback |
| `safe_retry(task_self, exc)` | Broker-safe `self.retry()` wrapper |

Import both with:
```python
from apps.core.utils import dispatch_task, safe_retry
```

---

## 8. Observability

### `@observed_task` decorator

Located in `apps/core/decorators.py`. When applied alongside `@shared_task`, it:

1. Deserializes a `TraceContext` from Celery task kwargs (`trace_context_*` headers).
2. Creates a child span in the trace system.
3. Logs task lifecycle events (started / completed / failed) to `ProcessingLog` and `AuditEvent`.

```python
@shared_task(bind=True, max_retries=2)
@observed_task("posting.prepare_posting", audit_event="POSTING_STARTED", entity_type="Invoice")
def prepare_posting_task(self, invoice_id: int, **kwargs) -> dict:
    ...
```

Tasks must accept `**kwargs` to receive the trace header fields.

### Langfuse tracing

Tasks that front a long pipeline open a root Langfuse trace to give the entire execution a single traceable entry point:

| Task | Langfuse trace name | Score emitted |
|---|---|---|
| `run_reconciliation_task` | `reconciliation_task` | `reconciliation_match` (per result) |
| `run_agent_pipeline_task` | `agent_pipeline_task` | -- (orchestrator emits inside) |
| `process_case_task` | `case_task` | -- |
| `reprocess_case_from_stage_task` | `case_task` | -- |
| `run_bulk_job_task` | `bulk_extraction_job` | `bulk_job_success_rate` |

All Langfuse calls are fail-silent (wrapped in `try/except`). Missing `LANGFUSE_PUBLIC_KEY` disables tracing transparently.

---

## 9. Deployment

### Worker (`deploy/finance-agents-celery.service`)

```bash
celery -A config worker \
    --loglevel=info \
    --logfile=/opt/finance-agents/logs/celery-worker.log \
    --queues=default,agents,reconciliation,extraction,scheduled \
    --concurrency=4 \
    --max-tasks-per-child=200 \
    --time-limit=600 \
    --soft-time-limit=540 \
    -Ofair
```

| Flag | Value | Meaning |
|---|---|---|
| `-A config` | `config` | Celery app defined in `config/celery.py` |
| `--concurrency` | `4` | 4 parallel worker processes (adjust for CPU count) |
| `--max-tasks-per-child` | `200` | Recycle worker process every 200 tasks (prevents memory leaks) |
| `--time-limit` | `600` | Hard kill a task after 600 s |
| `--soft-time-limit` | `540` | Raise `SoftTimeLimitExceeded` at 540 s (task can clean up) |
| `-Ofair` | -- | Fair scheduling: one task per prefetch slot |

### Beat scheduler (`deploy/finance-agents-celerybeat.service`)

```bash
celery -A config beat \
    --loglevel=info \
    --pidfile=/opt/finance-agents/run/celerybeat.pid \
    --schedule=/opt/finance-agents/run/celerybeat-schedule
```

The Beat process is deployed and managed as a separate systemd unit. No periodic tasks are registered yet (`CELERY_BEAT_SCHEDULE` is absent from `settings.py`). The `scheduled` worker queue and Beat service are ready for future periodic jobs such as:
- Nightly ERP reference re-import
- Stale posting cleanup
- Cache TTL purge

### Starting the worker locally (Linux / WSL)

```bash
# Start Redis
redis-server --daemonize yes

# Disable eager mode, then start the worker
export CELERY_TASK_ALWAYS_EAGER=False
celery -A config worker --loglevel=info --queues=default,agents,reconciliation,extraction,scheduled -Ofair

# Optional: start Beat in a second terminal
celery -A config beat --loglevel=info
```

### Production systemd management

```bash
# Start all Celery services
sudo systemctl start finance-agents-celery finance-agents-celerybeat finance-agents-flower

# Follow live logs
sudo journalctl -u finance-agents-celery -f --no-pager
sudo journalctl -u finance-agents-celerybeat -f --no-pager

# Restart after code deploy
sudo systemctl restart finance-agents-celery finance-agents-celerybeat
```

---

## 10. Monitoring with Flower

Flower is the real-time Celery monitoring dashboard deployed as a systemd service.

| Setting | Value |
|---|---|
| URL (behind Nginx) | `http://<server-ip>/flower/` |
| Internal port | `5555` (bound to `127.0.0.1`) |
| Auth | HTTP Basic auth (`admin` / configured password) |
| Persistence | SQLite DB at `/opt/finance-agents/run/flower.db` |
| Task history | Last 10,000 tasks |
| Offline worker purge | Workers not seen for 60 s auto-removed |

### What Flower shows

- Active, reserved, and scheduled tasks per worker
- Task result, arguments, and execution time
- Worker status (online / offline) and process details
- Real-time task event stream
- Rate and retry statistics

---

## 11. Adding a New Task

Follow these steps to add a task correctly:

1. **Create the task** in `apps/<app>/tasks.py`:

```python
from celery import shared_task
from apps.core.decorators import observed_task

@shared_task(bind=True, max_retries=2, default_retry_delay=30, acks_late=True)
@observed_task("myapp.my_task", audit_event="MY_EVENT_STARTED", entity_type="MyModel")
def my_new_task(self, entity_id: int, **kwargs) -> dict:
    from apps.myapp.models import MyModel
    from apps.myapp.services.my_service import MyService

    try:
        entity = MyModel.objects.get(pk=entity_id)
        result = MyService.run(entity)
        return {"status": "ok", "result_id": result.pk}
    except Exception as exc:
        logger.exception("my_new_task failed for entity %s", entity_id)
        from apps.core.utils import safe_retry
        safe_retry(self, exc)
```

2. **Dispatch the task** from a view or service using `dispatch_task()`:

```python
from apps.core.utils import dispatch_task
from apps.myapp.tasks import my_new_task

dispatch_task(my_new_task, entity.pk)
```

3. **Add Langfuse tracing** (optional but recommended for pipeline tasks):

```python
from apps.core.langfuse_client import start_trace, end_span

_trace_id = f"my-task-{entity_id}"
_lf_trace = None
try:
    _lf_trace = start_trace(_trace_id, "my_task_name", metadata={"entity_id": entity_id})
except Exception:
    pass

try:
    ...
finally:
    try:
        if _lf_trace:
            end_span(_lf_trace, output={"status": "ok"})
    except Exception:
        pass
```

4. **Do not forget:**
   - Use lazy imports inside the function body to avoid circular imports.
   - Never put business logic in the task function -- call a service class.
   - Accept `**kwargs` when using `@observed_task` to receive trace headers.
   - Use `safe_retry()` instead of `raise self.retry(exc=exc)` directly.
   - Return a JSON-serializable `dict` so results can be inspected.
   - Autodiscovery picks up the task automatically -- no registration needed.
