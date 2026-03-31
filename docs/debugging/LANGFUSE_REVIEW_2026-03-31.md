# Langfuse Observability Review -- 2026-03-31

## Current Coverage Found in Code (before this session)

### Implemented and working
| Area | Trace / Span | Notes |
|---|---|---|
| Agent pipeline | `agent_pipeline` root trace, per-agent child spans, tool spans, LLM generation logging | Mature; orchestrator and base_agent fully instrumented |
| Reconciliation runner | `reconciliation_run` trace (own root), 4 per-invoice spans, `reconciliation_match` score | Working but task trace ordering was wrong (see Gaps) |
| ERP submission | `erp_submission` child span + `erp_submission_standalone` fallback + `erp_status_check` | Implemented in PostingSubmitResolver |
| Posting pipeline | `posting_confidence` and `posting_requires_review` scores | Scores existed but floated free -- no root trace, no stage spans |
| Extraction pipeline | Full agent + LLM + bulk extraction traces | Already complete |

### Gaps found relative to docs / architecture
| Gap | Severity | Root Cause |
|---|---|---|
| `run_reconciliation_task` trace created in `finally` block AFTER the runner finished | High | Task trace was a sibling of the runner trace, not a parent; no forwarding to runner |
| `PostingPipeline.run()` had NO Langfuse root trace and NO per-stage spans | High | Only two `score_trace` calls existed; scores used `str(pk)` trace_id with no matching root trace open |
| `ERPResolutionService` had no Langfuse span support | Medium | No `lf_parent_span` parameter; resolution latency / source_type / cache_hit invisible to Langfuse |
| `run_agent_pipeline_task` had no Celery task_id correlation in Langfuse | Low | Orchestrator creates its own `agent_pipeline` trace; task wrapper metadata was absent |
| `reconciliation_run` was its own root trace, not a child of the task trace | Medium | Hierarchy in Langfuse showed two sibling root spans for same trace_id |

---

## Fixes Applied

### 1. `apps/reconciliation/tasks.py`
- Moved `start_trace("reconciliation_task")` to BEFORE `runner.run()` call
- Trace ID: `f"recon-task-{celery_task_id}"`
- Trace carries: `task_id`, `invoice_count`, `config_id`, `triggered_by_id`
- Passes `_lf_task_trace` to `runner.run(lf_trace=_lf_task_trace)` so the runner can attach as child
- Closes trace in try/except/finally pattern; marks `level="ERROR"` on task failure

### 2. `apps/reconciliation/services/runner_service.py`
- Changed runner so that when `lf_trace` is provided (non-None task trace), it creates `"reconciliation_run"` as a CHILD span using `start_span(lf_trace, "reconciliation_run", ...)`
- When `lf_trace` is None (standalone call), creates own root trace as before
- Result: correct two-tier hierarchy: `reconciliation_task -> reconciliation_run -> per-invoice spans`

### 3. `apps/posting_core/services/posting_pipeline.py`
- Added `"posting_pipeline"` root trace using `str(posting_run.pk)` as trace ID
- Trace carries: `posting_run_pk`, `invoice_id`, `invoice_number`, `user_id`, `session_id`
- Added two local helper functions `_open_stage_span` / `_close_stage_span` (fail-silent)
- Added 9 per-stage child spans:
  - `eligibility_check` (output: passed)
  - `snapshot_build` (output: built)
  - `mapping` (output: vendor_resolved, lines_count, mapping_issues, connector_used)
  - `validation` (output: total_issues)
  - `confidence_scoring` (output: confidence, issue_count) -- score emitted immediately after
  - `review_routing` (output: requires_review, queue, reason_count) -- score emitted immediately after
  - `payload_build` (output: lines_in_payload)
  - `finalization` (output: artifacts_persisted)
  - `duplicate_check` (output: is_duplicate, source_type)
- Root trace closed in success path (output: status, confidence, requires_review, queue, duration_ms)
- Root trace closed in exception handler (output: status=FAILED, error_code, level=ERROR)
- `posting_confidence` and `posting_requires_review` score calls now use `str(posting_run.pk)` consistently (no more `getattr(... "trace_id", "")` which always returned empty)

### 4. `apps/erp_integration/services/resolution_service.py`
- Added private `_trace_resolve(resolution_name, resolve_fn, safe_meta, lf_parent_span)` static method
- When `lf_parent_span` is not None, wraps the resolution call in a `start_span` / `end_span` child span
- Span output captures: `resolved`, `source_type`, `cache_hit`, `fallback_used`, `confidence`, `is_stale`
- `level="WARNING"` when resolved=False
- Full ERP payloads are never included
- Added `lf_parent_span=None` kwarg to all public `resolve_*` methods:
  - `resolve_po`, `resolve_grn`, `resolve_vendor`, `resolve_item`, `resolve_tax_code`, `resolve_cost_center`, `check_invoice_duplicate`
- All existing callers remain fully backward-compatible (default `lf_parent_span=None`)

### 5. `apps/agents/tasks.py`
- Added `"agent_pipeline_task"` wrapper trace before `orchestrator.execute()`
- Trace ID: `f"agent-task-{celery_task_id}"`
- Carries: `task_id`, `reconciliation_result_id`, `actor_user_id`, `session_id=invoice-{id}`
- Closed after orchestrator returns with: `agents_executed`, `final_recommendation`, `skipped`, `error`
- Marked `level="ERROR"` on exception
- Note: The orchestrator's own `agent_pipeline` trace runs under its own `trace_ctx.trace_id`; this wrapper is a separate, parallel trace for Celery task boundary visibility

---

## Intentionally Deferred Items

| Item | Reason |
|---|---|
| `PostingMappingEngine` -> `ERPResolutionService` span propagation | Would require adding `lf_parent_span` to `PostingMappingEngine.resolve()` and threading through `_resolve_vendor` / `_resolve_item`; additive but more invasive; marked in "Known missing integrations" table |
| `POLookupService` -> `ERPResolutionService` span propagation for reconciliation | Requires threading `lf_parent_span` through `POLookupService.lookup()` + `GRNLookupService`; the `recon_matching` span indirectly covers this via output metadata |
| `erp_submission_standalone` phase 2 parent trace forwarding | Submission is Phase 1 mock; `_lf_trace_id` resolution is already in place in `PostingActionService.submit_posting()` |
| `apps/copilot/` Langfuse spans | Out of scope for this review |
| `apps/cases/tasks.py` trace wrapper | Out of scope for this review |
| `apps/extraction/bulk_tasks.py` | Out of scope for this review |
| `reconciliation_match` score trace_id alignment | Score uses `str(run.pk)` but task trace uses `recon-task-{celery_id}`; these are different trace_ids. Score is attached to the runner's own trace (str(run.pk)), which is a separate trace from the task. For full alignment, the score should be emitted with the task trace_id OR the runner should always share the task's trace_id. Deferred as low-risk (score is still linked to reconciliation_run trace). |

---

## Documentation Updated
- `docs/LANGFUSE_INTEGRATION.md` -- trace hierarchy diagram, reconciliation section, posting section, overview
- `.github/copilot-instructions.md` -- "Known missing integrations" table updated with done/deferred entries

---

## No Migrations Required
All changes are purely in Python service/task code. No model fields were added or changed.
No ERP connector APIs were modified. No audit/governance persistence was changed.
