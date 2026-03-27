# Langfuse Integration Guide

## Overview

Langfuse is used for LLM observability: tracing every agent run, extraction call,
and LLM generation. The integration is fail-silent — if Langfuse is unreachable
or misconfigured, the application continues to work without any impact.

- **SDK version**: `langfuse==4.0.1`
- **Prompt management**: prompts are stored in Langfuse and fetched at runtime
  (60-second cache TTL). The `PromptRegistry` falls back to hardcoded defaults
  if Langfuse is unavailable.
- **Tracing**: every agent pipeline, extraction run, and reviewer summary call
  is recorded as a Langfuse trace with child spans and LLM generation logs.
- **Scores**: numeric quality scores are attached to traces after reconciliation
  match classification, posting confidence calculation, extraction pipeline
  routing, and review assignment / decision events.
- **ERP spans**: synchronous ERP submission and status-check calls are wrapped
  in single-operation traces so latency and failure rate are visible in Langfuse.

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
`agent.exception_analysis` | `agent-exception_analysis`
`agent.invoice_understanding` | `agent-invoice_understanding`

All dots are replaced with dashes. Use `slug_to_langfuse_name()` for the conversion.

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

  -- erp_submission          (PostingSubmitResolver.submit_invoice())
     (closed immediately with success/error output)

  -- erp_status_check        (PostingSubmitResolver.get_posting_status())
     (closed immediately with status/error output)

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

### 5. Reconciliation match score (`apps/reconciliation/services/runner_service.py`)

After `_reconcile_single()` classifies the match result and transitions the
invoice status, a `score_trace` is emitted before returning:

```python
score_trace(
    _trace_id,               # run.trace_id or str(run.pk)
    "reconciliation_match",
    _score_value,            # MATCHED=1.0, PARTIAL_MATCH=0.5, REQUIRES_REVIEW=0.3, UNMATCHED=0.0
    comment=f"invoice={invoice.pk} status={match_status}",
)
```

Score mapping:

| `MatchStatus` | Score |
|---|---|
| `MATCHED` | `1.0` |
| `PARTIAL_MATCH` | `0.5` |
| `REQUIRES_REVIEW` | `0.3` |
| `UNMATCHED` | `0.0` |

`trace_id` is taken from `run.trace_id` if present, otherwise falls back to
`str(run.pk)`. If the reconciliation run has no linked Langfuse root trace the
score is recorded as an unlinked score — this is acceptable for now.

### 6. Posting confidence score (`apps/posting_core/services/posting_pipeline.py`)

Stage 6 emits a confidence score immediately after calculation:

```python
score_trace(
    _trace_id,
    "posting_confidence",
    float(confidence),       # 0.0 -- 1.0 composite score
    comment=f"invoice={invoice.pk} requires_review=pending issues={len(all_issues)}",
)
```

Stage 7 emits a binary review-routing score after `PostingReviewRoutingService.route()`:

```python
score_trace(
    _trace_id,
    "posting_requires_review",
    1.0 if requires_review else 0.0,
    comment=f"queue={primary_queue} reasons={len(review_reasons)}",
)
```

Both calls use `posting_run.trace_id` or `str(posting_run.pk)` as the trace ID.

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

Both `submit_invoice()` and `get_posting_status()` open a root trace immediately
after measuring `duration_ms` and close it before calling `_log_submission()`.
The trace ID is derived from the first available identifier:

```
f"erp-{posting_run_id}"  ->  f"erp-inv-{invoice_id}"  ->  uuid4().hex
```

**`erp_submission`** (from `submit_invoice()`):

```python
_lf_trace = start_trace(
    _trace_id, "erp_submission",
    invoice_id=invoice_id,
    metadata={"submission_type": submission_type, "connector_name": ..., "posting_run_id": ...},
)
end_span(_lf_trace, output={
    "success": result.success,
    "status": str(result.status),
    "erp_document_number": result.erp_document_number or "",
    "duration_ms": result.duration_ms,
    "error_message": result.error_message or "",
}, level="ERROR" if not result.success else "DEFAULT")
```

**`erp_status_check`** (from `get_posting_status()`) — identical pattern with
`"erp_status_check"` as the name and `"document_number"` in metadata instead
of `"submission_type"`.

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

**`log_guardrail_decision()`** — called for every guardrail decision across the
entire agent system. Emits a score only when `trace_ctx.trace_id` is non-empty
(i.e. inside an active agent pipeline trace):

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

**`authorize_data_scope()`** — emits `rbac_data_scope = 0.0` **only** on the
deny path (successful scope checks are not scored to reduce noise).
Uses `TraceContext.get_current()` since no `trace_ctx` object is threaded
through `authorize_data_scope()`:

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

---

## Scores reference

| Score name | Value range | Source | Trace ID convention |
|---|---|---|---|
| `reconciliation_match` | 0.0 / 0.3 / 0.5 / 1.0 | `runner_service.py` | `run.trace_id` or `str(run.pk)` |
| `posting_confidence` | 0.0 – 1.0 | `posting_pipeline.py` Stage 6 | `posting_run.trace_id` or `str(posting_run.pk)` |
| `posting_requires_review` | 0.0 or 1.0 | `posting_pipeline.py` Stage 7 | same as above |
| `extraction_confidence` | 0.0 – 1.0 | `extraction_pipeline.py` Step 9 | `str(run.pk)` |
| `extraction_requires_review` | 0.0 or 1.0 | `extraction_pipeline.py` Step 9 | same as above |
| `review_priority` | 0.0 – 1.0 (priority/10) | `reviews/services.py` `create_assignment()` | `f"review-{assignment.pk}"` |
| `review_decision` | 0.0 / 0.5 / 1.0 | `reviews/services.py` `_finalise()` | `f"review-{assignment.pk}"` |
| `extraction_auto_approve_confidence` | 0.0 – 1.0 | `approval_service.py` `try_auto_approve()` | `f"approval-{approval.pk}"` |
| `extraction_approval_decision` | 0.0 or 1.0 | `approval_service.py` `approve()` / `reject()` | `f"approval-{approval.pk}"` |
| `extraction_approval_confidence` | 0.0 – 1.0 | `approval_service.py` `approve()` | `f"approval-{approval.pk}"` |
| `extraction_corrections_count` | 0.0+ (raw count) | `approval_service.py` `approve()` | `f"approval-{approval.pk}"` |
| `rbac_guardrail` | 0.0 or 1.0 | `guardrails_service.py` `log_guardrail_decision()` | active agent pipeline `trace_id` |
| `rbac_data_scope` | 0.0 (deny only) | `guardrails_service.py` `authorize_data_scope()` | `TraceContext.get_current().trace_id` |

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

### Issue 2 — Bulk extraction had no Langfuse traces

**Symptom**: Individual invoice uploads created traces; bulk extraction jobs did not.

**Root cause**: `InvoiceExtractionAdapter._agent_extract()` built an `AgentContext`
with `actor_user_id=None` and `invoice_id=0`. Because `session_id` is computed as
`f"invoice-{ctx.invoice_id}"`, a zero `invoice_id` produced an ugly `"invoice-0"`
session or was suppressed. More importantly, no `actor_user_id` meant the traces
were not attributed to any user.

The deeper problem was Issue 1 above — `start_trace` was returning `None`, so
traces were not created at all regardless.

**Fix**:

- `InvoiceExtractionAdapter.extract(file_path, *, actor_user_id=None)` — new kwarg.
- `InvoiceExtractionAdapter._agent_extract(ocr_text, *, actor_user_id=None)` — forwarded into `AgentContext(actor_user_id=actor_user_id)`.
- `process_invoice_upload_task` — passes `actor_user_id=upload.uploaded_by_id`.
- `InvoiceExtractionAgent.run()` — already passes `ctx.actor_user_id` and `ctx.invoice_id` to `start_trace` after the previous session's changes.

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
| Scores appear in Langfuse but not linked to a trace | The pipeline (reconciliation/posting) does not yet create a root trace — score is recorded as unlinked. This is expected until root traces are added to those pipelines. |
