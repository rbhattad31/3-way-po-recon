# Langfuse Integration Guide

## Overview

Langfuse is used for LLM observability: tracing every agent run, extraction call,
posting pipeline run, and LLM generation. The integration is fail-silent -- if
Langfuse is unreachable or misconfigured, the application continues to work
without any impact.

- **SDK version**: `langfuse==4.0.1`
- **Prompt management**: prompts are stored in Langfuse and fetched at runtime
  (60-second cache TTL). The `PromptRegistry` falls back to hardcoded defaults
  if Langfuse is unavailable.
- **Tracing**: every agent pipeline, extraction run, reconciliation run, posting
  pipeline run, bulk extraction job, case processing task, and copilot answer
  call is recorded as a Langfuse trace with child spans and LLM generation logs.
- **Scores**: numeric quality scores are attached to traces after reconciliation
  match classification, posting confidence calculation, extraction pipeline
  routing, review assignment / decision events, and copilot session archive.
- **ERP spans**: ERP submission and status-check calls are wrapped in traces;
  ERP resolution calls can be wrapped as child spans when a parent span is
  provided by the caller (posting pipeline, reconciliation).

---

## Configuration

Add the following to your `.env` file:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://us.cloud.langfuse.com   # or https://cloud.langfuse.com
```

If `LANGFUSE_PUBLIC_KEY` is not set, the Langfuse client is disabled and all
tracing calls become no-ops.

---

## Architecture

### Client singleton (`apps/core/langfuse_client.py`)

| Function | Purpose |
|---|---|
| `get_client()` | Returns the `Langfuse` singleton (thread-safe, lazy init). Returns `None` if not configured. |
| `start_trace(trace_id, name, *, ...)` | Opens a root trace span tied to the Django `trace_id`. Sets `user.id` and `session.id` OTel attributes for the Users/Sessions tabs. |
| `start_span(parent, name, *, ...)` | Opens a child span under a parent span. Inherits user/session from root. |
| `log_generation(span, name, *, ...)` | Records an LLM call (tokens, model, messages, completion) as a child generation. |
| `end_span(span, *, ...)` | Closes a span, optionally setting output and level. |
| `score_trace(trace_id, name, value, *, comment)` | Attaches a numeric score to a trace by `trace_id`. `comment` is optional human-readable context. |
| `push_prompt(slug, content, *, labels)` | Pushes a prompt to Langfuse prompt management. |
| `get_prompt(slug, *, label, fallback)` | Fetches a prompt from Langfuse (with fallback to local default). |
| `slug_to_langfuse_name(slug)` | Converts `extraction.invoice_system` -> `extraction-invoice_system`. |

### Prompt naming convention

Local slug (in `PromptRegistry`) | Langfuse name
----|----
`extraction.invoice_system` | `extraction-invoice_system`
`extraction.invoice_base` | `extraction-invoice_base`
`extraction.invoice_category_goods` | `extraction-invoice_category_goods`
`extraction.invoice_category_service` | `extraction-invoice_category_service`
`extraction.invoice_category_travel` | `extraction-invoice_category_travel`
`extraction.country_india_gst` | `extraction-country_india_gst`
`extraction.country_generic_vat` | `extraction-country_generic_vat`
`agent.exception_analysis` | `agent-exception_analysis`
`agent.invoice_understanding` | `agent-invoice_understanding`
_(+ 9 more agent prompts)_ | —

All dots are replaced with dashes (`slug.replace(".", "-")`). Use `slug_to_langfuse_name()` for the conversion. **18 prompts total** are registered in `_DEFAULTS` and pushed by `push_prompts_to_langfuse`.

#### Prompt composition flow (Phase 2)

`InvoicePromptComposer` assembles the final system prompt at extraction time:

```
extraction.invoice_base          (base extraction instructions)
  + extraction.invoice_category_{category}   (goods / service / travel overlay)
  + extraction.country_{country}_{regime}    (e.g. country_india_gst)
  ─────────────────────────────────────────
  = final_prompt  →  InvoiceExtractionAgent
```

The `PromptRegistry` resolution order for each component:
1. Langfuse (production label, 60s TTL cache)
2. DB (`PromptTemplate` model)
3. Hardcoded `_DEFAULTS` in `prompt_registry.py`

`InvoicePromptComposer.compose()` returns a `PromptComposition` with `final_prompt`, `components` (dict of key→content used), and `prompt_hash` (sha256 first 16 chars). The hash is logged to Langfuse as `prompt_hash` metadata on every `invoice_extraction` trace so prompt changes are traceable in the UI.

### Trace hierarchy

```
root trace  (start_trace)
  -- agent_pipeline / invoice_extraction
     -- EXCEPTION_ANALYSIS / INVOICE_EXTRACTION  (start_span per agent)
        -- llm_chat          (log_generation, one per LLM call)
        -- tool_po_lookup    (start_span, one per tool call in ReAct loop)
        -- tool_grn_lookup
        -- tool_invoice_details
        -- llm_chat          (next ReAct round)
        -- tool_...
     -- reviewer_summary
        -- llm_chat

  -- llm_extract_fallback    (only when _llm_extract() fallback is used)
     -- LLM_EXTRACT_FALLBACK
        -- llm_extract_fallback_chat  (log_generation)

  -- posting_pipeline        (PostingPipeline.run() -- one per PostingRun)
     -- eligibility_check    (stage 1)
     -- snapshot_build       (stage 2)
     -- mapping              (stage 3, emits vendor_resolved / mapping_issues)
        -- erp_resolve_vendor   (ERPResolutionService child span, when connector present)
        -- erp_resolve_item     (one per line item, when connector present)
     -- validation           (stage 4, emits total_issues)
     -- confidence_scoring   (stage 5, emits posting_confidence score)
     -- review_routing       (stage 6, emits posting_requires_review score)
     -- payload_build        (stage 7)
     -- finalization         (stage 8, persist artifacts)
     -- duplicate_check      (stage 9b, emits is_duplicate / source_type)
     -- erp_submission       (child span when lf_trace_id forwarded from PostingActionService)

  -- erp_submission_standalone  (isolated fallback trace when no parent trace ID is available)
  -- erp_status_check           (PostingSubmitResolver.get_posting_status() -- always isolated)
     (closed immediately with status/error output)

  -- reconciliation_task     (run_reconciliation_task Celery task wrapper -- root trace)
     -- reconciliation_run   (ReconciliationRunnerService.run() -- child span)
        -- recon_mode_resolution  (per invoice)
        -- recon_matching         (per invoice -- router + classifier)
        -- recon_result_persist   (per invoice -- result_service.save)
        -- recon_exception_build  (per invoice -- exception_builder.build)
        -- recon_mode_resolution  (next invoice...)
        -- ...

  -- agent_pipeline_task     (run_agent_pipeline_task Celery task wrapper)
     (standalone root trace carrying Celery task_id; the orchestrator's
      agent_pipeline trace runs concurrently under its own trace_id)

  -- case_task               (process_case_task / reprocess_case_from_stage_task)
     (root trace per Celery task invocation, trace_id=case-{case_id},
      metadata includes task_id, case_id, and stage)

  -- copilot_answer          (APCopilotService.answer_question())
     (root trace per answer call; trace_id from session.trace_id or copilot-{session.pk};
      session_id=copilot-{session.pk}; closed before each return path with topic output)

  -- bulk_extraction_job     (run_bulk_job_task -- one per BulkExtractionJob)
     -- bulk_item_extraction (one per eligible item in BulkExtractionJob)
     -- gdrive_test_connection / gdrive_list_files / gdrive_download_file
        (GoogleDriveBulkSourceAdapter -- only when lf_trace is set)
     -- onedrive_test_connection / onedrive_list_files / onedrive_download_file
        (OneDriveBulkSourceAdapter -- only when lf_trace is set)

```

Each `tool_*` span captures input (tool name, call ID, arguments) and output
(success flag, duration_ms, data keys, error message). Failed tool calls are
marked with `level="ERROR"` so they appear highlighted in the Langfuse UI.

### User and Session attribution

Every root trace carries:

- **`user.id`** (`user_id` arg to `start_trace`) — the Django `User.pk` as a string.
  Populates the **Users** tab in Langfuse so you can see all traces for a given user.
- **`session.id`** (`session_id` arg to `start_trace`) — always `"invoice-{invoice_id}"`.
  Populates the **Sessions** tab and groups all LLM calls for the same invoice
  (across agent pipeline runs and standalone extraction) into one session.

These are set as OpenTelemetry span attributes (`user.id`, `session.id`) on the
root `LangfuseSpan._otel_span` immediately after `start_observation()` returns.

---

## Tool call spans

Every tool execution inside `BaseAgent.run()` (the ReAct loop) is wrapped in a
Langfuse child span under the current agent span.

**Location**: `apps/agents/services/base_agent.py` — `for tc in llm_resp.tool_calls:` loop.

```python
_tool_span = start_span(
    _lf_span,
    name=f"tool_{tc.name}",
    metadata={
        "tool_name": tc.name,
        "tool_call_id": tc.id,
        "arguments": tc.arguments,
    },
)
# ... execute tool ...
end_span(
    _tool_span,
    output={
        "success": tool_result.success,
        "duration_ms": tool_result.duration_ms,
        "data_keys": list(tool_result.data.keys()) if isinstance(tool_result.data, dict) else None,
        "error": tool_result.error or None,
    },
    level="ERROR" if not tool_result.success else "DEFAULT",
)
```

- Only runs when `_lf_span is not None` (i.e. Langfuse is enabled and the agent
  span was created successfully).
- Both imports (`start_span`, `end_span`) are done lazily inside `try/except`
  blocks — a Langfuse failure never affects tool execution.
- Applies to all 8 agent types that use `BaseAgent.run()`. `InvoiceExtractionAgent`
  has its own `run()` override and uses no tools, so it is unaffected.

---

## Trace call sites

### 1. Agent pipeline (`apps/agents/services/orchestrator.py`)

```python
_lf_trace = start_trace(
    trace_id=trace_ctx.trace_id,
    name="agent_pipeline",
    invoice_id=result.invoice_id,
    result_id=result.pk,
    user_id=actor.pk if actor else None,
    session_id=f"invoice-{result.invoice_id}" if result.invoice_id else None,
    metadata={...},
)
```

The trace is opened before the first agent runs and is passed via
`ctx._langfuse_trace` so every child agent creates a child span under it.

### 2. Standalone extraction (`apps/agents/services/agent_classes.py` — `InvoiceExtractionAgent.run()`)

When extraction runs outside the agent pipeline (no `ctx._langfuse_trace`),
the agent creates its own root trace:

```python
_lf_trace = start_trace(
    _trace_id,
    "invoice_extraction",
    invoice_id=ctx.invoice_id or None,
    user_id=ctx.actor_user_id or None,
    session_id=f"invoice-{ctx.invoice_id}" if ctx.invoice_id else None,
    metadata={"agent_run_id": agent_run.pk},
)
```

### 3. Reviewer summary (`ExceptionAnalysisAgent._generate_reviewer_summary()`)

The second LLM call (after the main ReAct loop) passes metadata directly on the
LLM client so it is captured by `log_generation`:

```python
self.llm._langfuse_metadata = {
    ...
    "user_id": ctx.actor_user_id or "",
    "session_id": f"invoice-{ctx.invoice_id}" if ctx.invoice_id else "",
}
```

### 4. Bulk extraction user attribution

`InvoiceExtractionAdapter.extract()` accepts an optional `actor_user_id` kwarg
which is forwarded into `AgentContext`. The Celery task passes it from the upload:

```python
extraction_resp = adapter.extract(file_path, actor_user_id=upload.uploaded_by_id)
```

### 5. Reconciliation pipeline (`apps/reconciliation/tasks.py` + `apps/reconciliation/services/runner_service.py`)

The reconciliation pipeline is fully instrumented with a root task trace, a
service-level child span, four child spans per invoice, and a `reconciliation_match`
quality score.

#### Root traces and hierarchy

**Task-level root trace** (`run_reconciliation_task`): opened at the **start** of
the task, before the runner executes. Name: `"reconciliation_task"`.
Trace ID: `f"recon-task-{celery_task_id}"`. The task span is closed in a
`finally`-style block after the runner returns.

**Service-level child span** (`ReconciliationRunnerService.run()`): opened as a
child of the task-level trace using `start_span(lf_task_trace, "reconciliation_run")`.
Name: `"reconciliation_run"`. Closed after `recon_run.save()` with match-count
summary `{matched, partial, unmatched, errors, review}`.

This gives a clean two-tier hierarchy in Langfuse:

```
reconciliation_task  (task root, trace_id=recon-task-<celery_id>)
  -- reconciliation_run  (service child span)
       -- recon_mode_resolution   (per invoice)
       -- recon_matching          (per invoice)
       -- recon_result_persist    (per invoice)
       -- recon_exception_build   (per invoice)
```

When `run_reconciliation_task` runs without a Celery task ID (e.g. called from
a management command or directly), the task trace is skipped and the runner
falls back to creating its own standalone `"reconciliation_run"` root trace.

#### Per-invoice child spans (`_reconcile_single`)

Four spans are opened and closed around each deterministic pipeline stage:

| Span name | Wraps | Output keys |
|---|---|---|
| `recon_mode_resolution` | `ModeResolutionResolver.resolve()` | `mode` |
| `recon_matching` | `ReconciliationExecutionRouter.execute()` + `ClassificationService.classify()` | `match_status` |
| `recon_result_persist` | `ReconciliationResultService.save()` + `result_line_map` build | `result_id` |
| `recon_exception_build` | `ExceptionBuilderService.build()` + `bulk_create` | `exception_count` |

Each span is opened in its own `try/except`, and `end_span` is always called
in a `finally` block. A failure in span creation never affects the matching logic.

#### `reconciliation_match` score

Emitted at the end of `_reconcile_single()`, before returning:

```python
score_trace(
    _trace_id,               # run.trace_id or str(run.pk)
    "reconciliation_match",
    _score_value,            # MATCHED=1.0, PARTIAL_MATCH=0.5, REQUIRES_REVIEW=0.3, UNMATCHED=0.0
    comment=f"mode={reconciliation_mode} match_status={match_status} invoice={invoice.pk}",
)
```

Score mapping:

| `MatchStatus` | Score |
|---|---|
| `MATCHED` | `1.0` |
| `PARTIAL_MATCH` | `0.5` |
| `REQUIRES_REVIEW` | `0.3` |
| `UNMATCHED` | `0.0` |

### 6. Posting pipeline (`apps/posting_core/services/posting_pipeline.py`)

The 9-stage posting pipeline is fully traced with a root trace, one child span per
stage, and two quality scores emitted after stages 5 and 6.

#### Root trace (`PostingPipeline.run()`)

Opened after `PostingRun.objects.create()` using `str(posting_run.pk)` as the
trace ID. Name: `"posting_pipeline"`. Session ID: `"invoice-{invoice.pk}"`.
Closed in both the success path and exception handler, with appropriate output
and `level="ERROR"` on failure:

```python
_trace_id = str(posting_run.pk)
_lf_trace = start_trace(
    _trace_id,
    "posting_pipeline",
    invoice_id=invoice.pk,
    user_id=user.pk if user else None,
    session_id=f"invoice-{invoice.pk}",
    metadata={
        "posting_run_pk": posting_run.pk,
        "invoice_id": invoice.pk,
        "invoice_number": invoice.invoice_number or "",
    },
)
```

#### Per-stage child spans (actual code order)

Spans are created by the `_open_stage_span` / `_close_stage_span` local helpers
inside `run()`. Each helper is fail-silent.

| Stage | Span name | Key output fields |
|---|---|---|
| 1 | `eligibility_check` | `passed: True` |
| 2 | `snapshot_build` | `built: True` |
| 3 | `mapping` | `vendor_resolved`, `lines_count`, `mapping_issues`, `connector_used` |
| 4 | `validation` | `total_issues` |
| 5 | `confidence_scoring` | `confidence`, `issue_count` -- also emits `posting_confidence` score |
| 6 | `review_routing` | `requires_review`, `queue`, `reason_count` -- also emits `posting_requires_review` score |
| 7 | `payload_build` | `lines_in_payload` |
| 8 | `finalization` | `artifacts_persisted: True` |
| 9 | `duplicate_check` | `is_duplicate`, `source_type` |

#### Quality scores

**After stage 5** (`confidence_scoring`) emits `posting_confidence`:

```python
score_trace(
    str(posting_run.pk),     # trace_id
    "posting_confidence",
    float(confidence),       # 0.0 -- 1.0 composite score
    comment=f"invoice={invoice.pk} requires_review='pending' issues={len(all_issues)}",
)
```

**After stage 6** (`review_routing`) emits `posting_requires_review`:

```python
score_trace(
    str(posting_run.pk),
    "posting_requires_review",
    1.0 if requires_review else 0.0,
    comment=f"queue={primary_queue} reasons={len(review_reasons)}",
)
```

Both calls use `str(posting_run.pk)` as the trace ID, which matches the root
trace opened at the start of `run()`. Scores are therefore always linked to
a real trace in Langfuse.

#### ERP resolution child spans (when connector is present)

`ERPResolutionService` methods (`resolve_vendor`, `resolve_item`, etc.) accept
an optional `lf_parent_span` kwarg. When a non-None parent span is provided,
the service wraps the entire resolution (cache + API + DB fallback) as a child
span with the following output fields:

| Field | Type | Notes |
|---|---|---|
| `resolved` | bool | Whether the lookup succeeded |
| `source_type` | str | CACHE / API / MIRROR_DB / DB_FALLBACK / NONE |
| `cache_hit` | bool | True when source_type is CACHE |
| `fallback_used` | bool | True when DB fallback path was taken |
| `confidence` | float | 0.0 -- 1.0 |
| `is_stale` | bool | True when data exceeds freshness threshold |

Span names follow the pattern `erp_{resolution_name}` (e.g. `erp_resolve_vendor`,
`erp_resolve_po`). Full ERP payloads are **never** logged."

To see these spans, callers must pass the current Langfuse span as `lf_parent_span`:

```python
result = erp_service.resolve_vendor(
    vendor_code=vendor_code,
    lf_parent_span=_lf_mapping_span,   # the "mapping" stage span
)
```

`PostingOrchestrator.prepare_posting()` extracts the same trace ID before calling
`PostingPipeline.run()` so it can be forwarded to the ERP submission layer in Phase 2:

```python
# PostingActionService.submit_posting() already resolves _lf_trace_id from
# the latest PostingRun for forwarding to PostingSubmitResolver.submit_invoice()
```

### 7. Direct LLM fallback (`InvoiceExtractionAdapter._llm_extract()`)

When the agent framework is unavailable, extraction falls back to a direct
Azure OpenAI call. This call is now logged as its own root trace:

```python
_lf_trace = start_trace(
    _trace_id,
    "llm_extract_fallback",
    metadata={"ocr_char_count": len(ocr_text)},
)
_lf_span = start_span(_lf_trace, "LLM_EXTRACT_FALLBACK")
# ... client.chat.completions.create() ...
log_generation(
    span=_lf_span,
    name="llm_extract_fallback_chat",
    model=deployment,
    prompt_messages=[...],
    completion=content,
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
    total_tokens=prompt_tokens + completion_tokens,
)
end_span(_lf_span, output={"completion_length": len(content or "")})
end_span(_lf_trace)
```

The system prompt is extracted once via `_get_extraction_prompt()` and reused
in both the OpenAI call and the `log_generation` call.

### 8. Extraction pipeline scores (`apps/extraction_core/services/extraction_pipeline.py`)

After Step 9 (review routing) sets `output.requires_review` and `output.review_reasons`,
two scores are emitted using `str(run.pk)` as the trace ID:

```python
score_trace(
    _trace_id,
    "extraction_confidence",
    float(output.overall_confidence or 0.0),
    comment=f"document={extraction_document_id} country={run.country_code} requires_review={routing.needs_review}",
)
score_trace(
    _trace_id,
    "extraction_requires_review",
    1.0 if routing.needs_review else 0.0,
    comment=f"reasons={routing.reasons[:3]}",
)
```

`output.overall_confidence` is guarded with `or 0.0` since it may be `None`
before confidence calculation completes.

### 9. Review assignment priority (`apps/reviews/services.py` — `create_assignment()`)

After the `ReviewAssignment` is created and the audit event is logged, a priority
score is emitted using `f"review-{assignment.pk}"` as the trace ID:

```python
score_trace(
    f"review-{assignment.pk}",
    "review_priority",
    float(priority) / 10.0,   # priority 1-10 normalised to 0.0-1.0
    comment=f"assignment={assignment.pk} invoice={result.invoice_id} result={result.pk}",
)
```

Using `f"review-{assignment.pk}"` consistently links the priority score at
creation time with the final decision score (call site 10).

### 10. Review decision (`apps/reviews/services.py` — `_finalise()`)

After `ReviewDecision.objects.update_or_create(...)` and the audit log, a
decision score is emitted using the same `f"review-{assignment.pk}"` trace ID:

```python
score_trace(
    f"review-{assignment.pk}",
    "review_decision",
    _decision_score,   # APPROVED=1.0, REPROCESSED=0.5, REJECTED=0.0
    comment=f"decision={decision_status} invoice={_invoice_id} reviewer={user.pk}",
)
```

Score mapping:

| `ReviewStatus` | Score |
|---|---|
| `APPROVED` | `1.0` |
| `REPROCESSED` | `0.5` |
| `REJECTED` | `0.0` |

### 11. ERP submission spans (`apps/erp_integration/services/submission/posting_submit_resolver.py`)

`submit_invoice()` now accepts an optional `lf_trace_id` kwarg. When provided,
the submission span is attached as a child of the caller's trace (e.g. the
posting pipeline trace). When omitted, a standalone fallback trace is created
so the call is always visible in Langfuse regardless of context.

#### Linked path — child span under parent trace

When `lf_trace_id` is supplied (e.g. forwarded from `PostingActionService`):

```python
lf_client = get_client()
_lf_span = lf_client.span(
    trace_id=lf_trace_id,
    name="erp_submission",
    metadata={"submission_type": submission_type, "connector_name": ..., "posting_run_id": ...},
)
_lf_span.end(output={
    "success": result.success,
    "status": str(result.status),
    "erp_document_number": result.erp_document_number or "",
    "duration_ms": result.duration_ms,
    "error_message": result.error_message or "",
}, level="ERROR" if not result.success else "DEFAULT")
```

This makes the ERP submission appear as a child span inside the `posting_pipeline`
trace rather than as a separate isolated trace.

#### Standalone fallback — root trace when no parent is available

When `lf_trace_id` is `None` (direct calls from tests, admin actions, or callers
that have not yet been wired up):

```python
_fallback_id = (
    f"erp-sub-{posting_run_id}" if posting_run_id
    else f"erp-inv-{invoice_id}" if invoice_id
    else uuid4().hex
)
_lf_trace = start_trace(_fallback_id, "erp_submission_standalone", ...)
end_span(_lf_trace, output={...}, level=...)
```

The fallback ID chain is: `f"erp-sub-{posting_run_id}"` -> `f"erp-inv-{invoice_id}"` -> `uuid4().hex`.

#### Trace ID forwarding from `PostingActionService.submit_posting()`

Before the Phase 1 mock block, `submit_posting()` resolves the trace ID from the
latest `PostingRun` so it is available to pass to the real ERP connector call in
Phase 2:

```python
_lf_trace_id = None
try:
    _latest_run = PostingRun.objects.filter(
        invoice_id=posting.invoice_id,
    ).order_by("-created_at").first()
    _lf_trace_id = getattr(_latest_run, "trace_id", None) or (
        str(_latest_run.pk) if _latest_run else None
    )
except Exception:
    pass
# Phase 2: pass lf_trace_id=_lf_trace_id to the resolver
```

**`erp_status_check`** (from `get_posting_status()`) — unchanged; still creates
an isolated root trace using the same fallback ID chain, as status checks are
triggered independently of the pipeline.

Failed ERP calls (`result.success == False`) are marked `level="ERROR"` so they
appear highlighted in red in the Langfuse UI.

### 12. Extraction approval scores (`apps/extraction/services/approval_service.py`)

Three decision points emit scores using `f"approval-{approval.pk}"` as trace ID,
linking all scores for the same approval lifecycle.

**`try_auto_approve()`** — emits confidence when auto-approval fires:

```python
score_trace(
    f"approval-{approval.pk}",
    "extraction_auto_approve_confidence",
    float(confidence),
    comment=f"invoice={invoice.pk} threshold={threshold:.2f} touchless=True",
)
```

**`approve()`** — emits up to three scores on human approval:

```python
# Decision: always 1.0 for approve
score_trace(_trace_id, "extraction_approval_decision", 1.0,
    comment=f"invoice={invoice.pk} reviewer={user.pk} corrections={len(correction_records)} touchless={approval.is_touchless}")

# Confidence at time of review
score_trace(_trace_id, "extraction_approval_confidence",
    float(approval.confidence_at_review or 0.0), comment=f"invoice={invoice.pk}")

# Correction count (only emitted if corrections > 0)
score_trace(_trace_id, "extraction_corrections_count",
    float(len(correction_records)), comment=f"invoice={invoice.pk}")
```

**`reject()`** — emits decision = 0.0 on human rejection:

```python
score_trace(
    f"approval-{approval.pk}",
    "extraction_approval_decision",
    0.0,
    comment=f"invoice={invoice.pk} reviewer={user.pk} reason={reason[:100]}",
)
```

### 13. RBAC guardrail scores (`apps/agents/services/guardrails_service.py`)

Every guardrail grant or deny decision emits a Langfuse score so RBAC enforcement
is auditable in the Langfuse dashboard alongside the `AuditEvent` log.

#### Trace ID helper

`AgentGuardrailsService._lf_trace_id_for_run(agent_run)` is a static helper that
returns the best available trace ID for an agent run without raising:

```python
@staticmethod
def _lf_trace_id_for_run(agent_run) -> Optional[str]:
    try:
        return getattr(agent_run, "trace_id", None) or str(agent_run.pk)
    except Exception:
        return None
```

Use this when a caller already holds an `AgentRun` instance (e.g. the deny path
in `base_agent._execute_tool()` and `recommendation_service.mark_recommendation_accepted()`).

#### `log_guardrail_decision()` — grant and deny (all methods)

This is the single choke point that the orchestrator, recommendation service, and
tool executor all call on deny. It builds `trace_ctx` internally and emits a score
only when `trace_ctx.trace_id` is non-empty (i.e. inside an active agent pipeline
trace):

```python
score_trace(
    _trace_id,
    "rbac_guardrail",
    1.0 if granted else 0.0,   # 1.0 = GRANTED, 0.0 = DENIED
    comment=f"action={action} permission={permission_code} role={actor_primary_role} granted={granted}",
)
```

Scoring 0.0 for denied decisions makes it straightforward to filter
`score:rbac_guardrail = 0` in Langfuse to find all authorization failures.

Methods that route through `log_guardrail_decision()`:

| Method | When called |
|---|---|
| `authorize_orchestration` | deny path (orchestrator) + both paths for recommendation / tool deny |
| `authorize_agent` | deny path (orchestrator loop) |
| `authorize_tool` | deny path (`_execute_tool` in `base_agent.py`) |
| `authorize_recommendation` | deny path (`recommendation_service.py`) |
| `authorize_data_scope` | both grant and deny (data scope always audited) |

#### `authorize_agent()` — grant path (direct score)

The deny path is already covered by the orchestrator calling `log_guardrail_decision`.
The grant path emits a score directly using `TraceContext.get_current()` so every
successful agent authorization is surfaced alongside deny events:

```python
granted = user.has_permission(perm)
if granted:
    try:
        from apps.core.langfuse_client import score_trace
        from apps.core.trace import TraceContext
        _ctx = TraceContext.get_current()
        _tid = getattr(_ctx, "trace_id", "") or ""
        if _tid:
            score_trace(
                _tid,
                "rbac_guardrail",
                1.0,
                comment=f"rbac_guardrail GRANTED method=authorize_agent agent_type={agent_type} user_role={_role}",
            )
    except Exception:
        pass
return granted
```

#### `authorize_tool()` — grant path (direct score)

Same pattern as `authorize_agent`. The deny path is scored by `log_guardrail_decision`
inside `base_agent._execute_tool()`; the grant path is scored here:

```python
if granted:
    try:
        score_trace(
            _tid,
            "rbac_guardrail",
            1.0,
            comment=f"rbac_guardrail GRANTED method=authorize_tool tool={tool_name} user_role={_role}",
        )
    except Exception:
        pass
return granted
```

#### `authorize_data_scope()` — deny-only score

Emits `rbac_data_scope = 0.0` **only** on the deny path (successful scope checks
are not scored to reduce noise). Uses `TraceContext.get_current()` since no
`trace_ctx` object is threaded through `authorize_data_scope()`:

```python
if not granted:
    # ... logger.warning ...
    score_trace(
        _trace_id,          # from TraceContext.get_current().trace_id
        "rbac_data_scope",
        0.0,
        comment=f"actor={actor.pk} result={result.pk}",
    )
```

#### Score coverage summary

| Method | Grant scored | Deny scored | Score name |
|---|---|---|---|
| `authorize_orchestration` | via `log_guardrail_decision` | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_agent` | direct (TraceContext) | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_tool` | direct (TraceContext) | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_recommendation` | via `log_guardrail_decision` | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_data_scope` | not scored (noise) | direct (TraceContext) | `rbac_data_scope` |

### 15. Case processing tasks (`apps/cases/tasks.py`)

Both `process_case_task` and `reprocess_case_from_stage_task` now open a
`"case_task"` root trace at entry, before the orchestrator runs.

**Trace ID**: `f"case-{case_id}"`. This is stable across retries so all
attempts for the same case are grouped in Langfuse.

**For `process_case_task`**:

```python
_lf_trace = start_trace(
    f"case-{case_id}",
    "case_task",
    metadata={"task_id": self.request.id, "case_id": case_id, "stage": "full"},
)
```

The trace is closed after the orchestrator returns with `{"status": case.status,
"case_number": case.case_number}`. On `APCase.DoesNotExist`, the trace closes
with `level="ERROR"` and `{"error": "not_found"}`. On any other exception, the
trace closes with `level="ERROR"` before `safe_retry()` is called.

**For `reprocess_case_from_stage_task`**, the same pattern applies with
`{"stage": stage}` in the metadata and `{"status": case.status, "stage": stage}`
in the success output.

### 16. Copilot service (`apps/copilot/services/copilot_service.py`)

#### `answer_question()` span

Every call to `answer_question()` opens a `"copilot_answer"` trace at entry:

```python
_lf_span = start_trace(
    _session_trace_id,         # session.trace_id or f"copilot-{session.pk}"
    "copilot_answer",
    session_id=f"copilot-{session.pk}",
    metadata={"session_id": str(session.pk), "case_id": session.linked_case_id},
)
```

The trace is closed before each of the two return paths with `topic` and
`case_id` in the output:

```python
end_span(_lf_span, output={"topic": _topic, "case_id": session.linked_case_id})
```

The `_topic` variable is set to `"small_talk"` on the short-circuit path and to
the classified topic string (`"overview"`, `"invoice"`, `"reconciliation"`, etc.)
on the main path.

#### `archive_session()` score

After a successful archive, `archive_session()` counts the session's messages
and emits `copilot_session_length` as a raw message-count float:

```python
msg_count = CopilotMessage.objects.filter(
    session__pk=session_id, session__user=user,
).count()
score_trace(
    f"copilot-{session_id}",
    "copilot_session_length",
    float(msg_count),
    comment=f"session={session_id} messages={msg_count}",
)
```

Trace ID `f"copilot-{session_id}"` matches the `session_id` attribute set on
`answer_question()` traces, so the score is grouped with all answer spans in the
Langfuse Sessions tab.

### 14. Bulk extraction job (`apps/extraction/bulk_tasks.py` + `apps/extraction/services/bulk_service.py` + `apps/extraction/services/bulk_source_adapters.py`)

The bulk extraction pipeline is fully traced with a root trace, per-item child
spans, and connector-level spans for Google Drive and OneDrive.

#### Root trace (`run_bulk_job_task`)

Opened before `BulkExtractionService.run_job()` and closed in a `finally` block:

```python
_trace_id = getattr(job, "trace_id", None) or str(job.pk)
_lf_trace = start_trace(
    _trace_id,
    "bulk_extraction_job",
    metadata={
        "task_id": self.request.id,
        "job_pk": job.pk,
        "source_type": job.source_connection.source_type,
        "total_found": job.total_found,
    },
)
```

After the job completes, a `bulk_job_success_rate` score is emitted and the
trace is closed:

```python
score_trace(
    _trace_id,
    "bulk_job_success_rate",
    job.total_success / (job.total_found or 1),
    comment=f"processed={job.total_success} total={job.total_found}",
)
end_span(_lf_trace, output={"status": job.status, "processed": job.total_success})
```

#### Per-item spans (`BulkExtractionService._process_item`)

The `lf_trace` handle is forwarded from `run_job()` into `_process_item(lf_parent=)`.
Each eligible item opens a `"bulk_item_extraction"` child span:

```python
_lf_item_span = start_span(
    lf_parent,
    name="bulk_item_extraction",
    metadata={"file_name": item.source_name, "item_pk": item.pk},
)
```

The span is closed before each early-return path (DUPLICATE, CREDIT_BLOCKED,
download FAILED) and in the `finally` cleanup block. Items that finish with
`status == FAILED` are closed with `level="ERROR"`.

#### Connector spans (source adapters)

`run_job()` sets `adapter.lf_trace = lf_trace` on every adapter instance before
calling any adapter method. The adapter base class initialises `self.lf_trace = None`
so existing callers (management commands, tests) are unaffected.

Both `GoogleDriveBulkSourceAdapter` and `OneDriveBulkSourceAdapter` wrap all
network-bound methods:

| Adapter | Span name | Key output |
|---|---|---|
| Google Drive | `gdrive_test_connection` | -- |
| Google Drive | `gdrive_list_files` | `file_count` |
| Google Drive | `gdrive_download_file` | -- |
| OneDrive | `onedrive_test_connection` | -- |
| OneDrive | `onedrive_list_files` | `file_count` |
| OneDrive | `onedrive_download_file` | -- |

`LocalFolderBulkSourceAdapter` is not wrapped (local I/O latency is negligible
and there are no auth failures to surface).

---

## Scores reference

| Score name | Value range | Source | Trace ID convention |
|---|---|---|---|
| `reconciliation_match` | 0.0 / 0.3 / 0.5 / 1.0 | `runner_service.py` | `run.trace_id` or `str(run.pk)` |
| `posting_confidence` | 0.0 – 1.0 | `posting_pipeline.py` Stage 6 (`confidence_scoring` span) | `posting_run.trace_id` or `str(posting_run.pk)` — always linked to root `posting_pipeline` trace |
| `posting_requires_review` | 0.0 or 1.0 | `posting_pipeline.py` Stage 7 (`review_routing` span) | same as above |
| `extraction_confidence` | 0.0 – 1.0 | `extraction_pipeline.py` Step 9 | `str(run.pk)` |
| `extraction_requires_review` | 0.0 or 1.0 | `extraction_pipeline.py` Step 9 | same as above |
| `review_priority` | 0.0 – 1.0 (priority/10) | `reviews/services.py` `create_assignment()` | `f"review-{assignment.pk}"` |
| `review_decision` | 0.0 / 0.5 / 1.0 | `reviews/services.py` `_finalise()` | `f"review-{assignment.pk}"` |
| `extraction_auto_approve_confidence` | 0.0 – 1.0 | `approval_service.py` `try_auto_approve()` | `f"approval-{approval.pk}"` |
| `extraction_approval_decision` | 0.0 or 1.0 | `approval_service.py` `approve()` / `reject()` | `f"approval-{approval.pk}"` |
| `extraction_approval_confidence` | 0.0 – 1.0 | `approval_service.py` `approve()` | `f"approval-{approval.pk}"` |
| `extraction_corrections_count` | 0.0+ (raw count) | `approval_service.py` `approve()` | `f"approval-{approval.pk}"` |
| `rbac_guardrail` | 0.0 or 1.0 | `guardrails_service.py` — `log_guardrail_decision()` (all methods) + direct grant path in `authorize_agent()` / `authorize_tool()` | active agent pipeline `trace_id` via `TraceContext.get_current()` |
| `rbac_data_scope` | 0.0 (deny only) | `guardrails_service.py` `authorize_data_scope()` | `TraceContext.get_current().trace_id` |
| `bulk_job_success_rate` | 0.0 -- 1.0 | `bulk_tasks.py` `run_bulk_job_task` | `job.trace_id` or `str(job.pk)` |
| `copilot_session_length` | 0.0+ (raw message count) | `copilot_service.py` `archive_session()` | `f"copilot-{session_id}"` |

---

## Prompt management

### Push all prompts to Langfuse

```powershell
python manage.py push_prompts_to_langfuse
```

### Delete all prompts and reseed (fixes name mismatches)

```powershell
python manage.py push_prompts_to_langfuse --purge
```

The `--purge` flag:
1. Calls `GET /api/public/v2/prompts` to list all existing prompts.
2. Calls `DELETE /api/public/v2/prompts/{name}` for each one.
3. Proceeds with the normal push of all `PromptRegistry` defaults.

Authentication for the REST calls uses HTTP Basic Auth with
`LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY`.

### Other options

```powershell
# Push only one prompt
python manage.py push_prompts_to_langfuse --slug agent.exception_analysis

# Push with a specific label
python manage.py push_prompts_to_langfuse --label staging

# Preview without sending
python manage.py push_prompts_to_langfuse --dry-run
```

After pushing, open **Langfuse -> Prompts**, edit the content, and set its label
to `production`. Django picks up the new version within 60 seconds (cache TTL).

---

## Known issues and fixes

### Issue 1 — `start_observation()` does not accept `user_id`/`session_id` (Langfuse SDK v4)

**Symptom**: All Langfuse traces disappeared after adding user/session attribution.
No errors were logged; `start_trace` returned `None` silently.

**Root cause**: Langfuse SDK v3 accepted `user_id` and `session_id` directly in
`start_observation()`. Langfuse SDK v4 (installed: `4.0.1`) removed them from
that function signature. Passing unknown kwargs caused a `TypeError` which was
silently caught, returning `None` and breaking all downstream tracing.

**Fix** (`apps/core/langfuse_client.py`):

Remove `user_id` / `session_id` from the `start_observation()` call and instead
set them as OpenTelemetry span attributes **after** the span is created:

```python
from langfuse._client.attributes import TRACE_USER_ID, TRACE_SESSION_ID

otel_span = getattr(span, "_otel_span", None)
if otel_span is not None:
    if user_id:
        otel_span.set_attribute(TRACE_USER_ID, str(user_id))   # "user.id"
    if session_id:
        otel_span.set_attribute(TRACE_SESSION_ID, session_id)  # "session.id"
```

The constants `TRACE_USER_ID = "user.id"` and `TRACE_SESSION_ID = "session.id"`
are defined in `langfuse._client.attributes`.

### Issue 2 — Bulk extraction job pipeline had no Langfuse traces

**Symptom**: Individual invoice uploads created traces via the agent pipeline;
bulk extraction jobs (`BulkExtractionJob`) produced no traces in Langfuse even
when Langfuse was fully configured.

**Root cause**: `run_bulk_job_task` called `BulkExtractionService.run_job()` with
no Langfuse context. The service and adapters had no tracing hook points.

**Fix**:

- `run_bulk_job_task` — opens a `"bulk_extraction_job"` root trace using
  `job.trace_id or str(job.pk)`, passes the handle to `run_job(lf_trace=)`,
  and closes it in a `finally` block with a `bulk_job_success_rate` score.
- `BulkExtractionService.run_job(lf_trace=None)` — propagates trace to
  `adapter.lf_trace` and forwards it as `lf_parent` to `_process_item()`.
- `BulkExtractionService._process_item(lf_parent=None)` — opens a
  `"bulk_item_extraction"` child span per item; closes with `level="ERROR"`
  on `FAILED` status.
- `BaseBulkSourceAdapter.__init__` — adds `self.lf_trace = None`.
- `GoogleDriveBulkSourceAdapter` and `OneDriveBulkSourceAdapter` — all three
  network-bound methods (`test_connection`, `list_files`, `download_file`) are
  wrapped with start/end span pairs. `LocalFolderBulkSourceAdapter` is unchanged.

### Issue 3 — Prompt names mismatched in Langfuse

**Symptom**: Warning logged — `Prompt 'extraction-invoice_system-label:production' not found`.
The prompts existed in Langfuse but with different names.

**Root cause**: Prompts had been pushed previously with a different naming scheme.
`slug_to_langfuse_name()` uses `slug.replace(".", "-")` which replaces ALL dots,
so `extraction.invoice_system` becomes `extraction-invoice_system`.

**Fix**: Run `python manage.py push_prompts_to_langfuse --purge` to delete all
existing prompts and reseed with the correct names.

---

## Debugging

| Symptom | Check |
|---|---|
| No traces appearing | Verify `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` are set. Check Django logs for `Langfuse disabled` or `start_trace failed`. |
| Traces appear but Users/Sessions tabs are empty | Confirm SDK version is 4.x. The `_otel_span.set_attribute()` approach requires v4. |
| Prompt 404 warning in logs | Run `push_prompts_to_langfuse`. If names are wrong, run with `--purge`. |
| Old prompt content being served | Langfuse client caches prompts for 60 seconds. Wait or restart to force refresh. |
| `start_trace` returns None | Set `LANGFUSE_LOG_LEVEL=debug` and check for exceptions in the client init. Ensure host URL has no trailing slash. |
| Scores appear in Langfuse but not linked to a trace | Confirm the pipeline that emits the score also calls `start_trace` before the score. The posting, reconciliation, and bulk extraction pipelines all create root traces — scores from these pipelines are always linked. |
