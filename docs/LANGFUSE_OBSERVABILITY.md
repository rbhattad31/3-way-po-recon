# Langfuse & Observability Guide

**Version**: 2.0 · April 2026
**Scope**: Langfuse integration, score taxonomy, cross-flow correlation, prompt management
**Source files replaced**: `LANGFUSE_INTEGRATION.md`, `OBSERVABILITY_UPGRADE_SUMMARY.md`

---

## Table of Contents

1. [Overview](#1-overview)
2. [Configuration](#2-configuration)
3. [Architecture](#3-architecture)
   - 3.1 [Client Singleton (`langfuse_client.py`)](#31-client-singleton-langfuse_clientpy)
   - 3.2 [Observability Helpers (`observability_helpers.py`)](#32-observability-helpers-observability_helperspy)
   - 3.3 [Prompt Naming and Composition](#33-prompt-naming-and-composition)
   - 3.4 [DB Model Trace ID Fields](#34-db-model-trace-id-fields)
4. [Trace Hierarchy](#4-trace-hierarchy)
5. [User and Session Attribution](#5-user-and-session-attribution)
6. [Tool Call Spans](#6-tool-call-spans)
7. [Trace Call Sites by Pipeline](#7-trace-call-sites-by-pipeline)
   - 7.1 [Extraction Pipeline](#71-extraction-pipeline)
   - 7.2 [Reconciliation Pipeline](#72-reconciliation-pipeline)
   - 7.3 [Agent Pipeline](#73-agent-pipeline)
   - 7.4 [Posting Pipeline](#74-posting-pipeline)
   - 7.5 [Case Pipeline](#75-case-pipeline)
   - 7.6 [Review Workflow](#76-review-workflow)
   - 7.7 [ERP Integration](#77-erp-integration)
   - 7.8 [Bulk Extraction](#78-bulk-extraction)
   - 7.9 [Copilot](#79-copilot)
8. [Score Taxonomy](#8-score-taxonomy)
   - 8.1 [Domain Groups](#81-domain-groups)
   - 8.2 [Root Trace Name Constants](#82-root-trace-name-constants)
   - 8.3 [Latency Thresholds](#83-latency-thresholds)
   - 8.4 [Full Score Reference Table](#84-full-score-reference-table)
   - 8.5 [Adding a New Score](#85-adding-a-new-score)
9. [Cross-Flow Correlation](#9-cross-flow-correlation)
10. [Prompt Management](#10-prompt-management)
11. [Known Issues and Fixes](#11-known-issues-and-fixes)
12. [Debugging Guide](#12-debugging-guide)
13. [Deferred Gaps](#13-deferred-gaps)
14. [Upgrade History](#14-upgrade-history)

---

## 1. Overview

Langfuse is the LLM observability layer for this platform. It records every agent run, extraction call, posting pipeline stage, reconciliation pass, case orchestration, and ERP resolution as a structured trace with child spans, LLM generation logs, and numeric quality scores.

**Key design principles**:

- **Fail-silent**: If Langfuse is unreachable or misconfigured, all tracing calls become no-ops. The application continues to work without any impact.
- **SDK version**: `langfuse==4.0.1`
- **Prompt management**: Prompts are stored in Langfuse and fetched at runtime with a 60-second cache TTL. Falls back to hardcoded defaults if Langfuse is unavailable.
- **Score taxonomy**: All score name strings are centralized as constants in `apps/core/evaluation_constants.py`. Raw string literals are not used at call sites.
- **Cross-flow correlation**: `apps/core/observability_helpers.py` provides shared session ID derivation, metadata builders, and sanitization used by all pipelines.

**What is traced**:

| Pipeline | Root Trace Name | Trace Count |
|---|---|---|
| Invoice extraction (upload) | `extraction_pipeline` | 1 per upload |
| Reconciliation task | `reconciliation_task` | 1 per Celery run |
| Agent pipeline | `agent_pipeline` | 1 per agent run |
| Case processing | `case_pipeline` | 1 per case task |
| Posting pipeline | `posting_pipeline` | 1 per PostingRun |
| Review workflow | `review_assignment` | 1 per assignment |
| Bulk extraction job | `bulk_extraction_job` | 1 per BulkExtractionJob |
| Copilot answer | `copilot_answer` | 1 per question |
| LLM extraction fallback | `llm_extract_fallback` | 1 per fallback call |
| ERP submission (standalone) | `erp_submission_standalone` | when no parent trace |
| System agents | `system_agent` | when SYSTEM_* agents run |
| Supervisor pipeline | `supervisor_pipeline` | when supervisor agent runs |

---

## 2. Configuration

Add the following to your `.env` file:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://us.cloud.langfuse.com   # or https://cloud.langfuse.com
LANGFUSE_ENVIRONMENT=production               # default: development
```

If `LANGFUSE_PUBLIC_KEY` is not set, the Langfuse client is disabled and all tracing calls become no-ops. No error is raised.

**No Django `settings.py` entries** — all configuration is environment-variable only.

---

## 3. Architecture

### 3.1 Client Singleton (`langfuse_client.py`)

**Location**: `apps/core/langfuse_client.py`

Uses double-checked locking (`_client_lock`) for thread-safe lazy initialization. Global state: `_client`, `_client_initialised`. Returns `None` on any failure.

#### Core Functions

| Function | Purpose |
|---|---|
| `get_client()` | Returns the `Langfuse` singleton; `None` if not configured |
| `start_trace(trace_id, name, *, invoice_id, result_id, user_id, session_id, metadata)` | Opens root trace; sets `user.id` and `session.id` as OTel span attributes |
| `start_span(parent, name, *, metadata)` | Opens child span under parent |
| `log_generation(span, name, *, model, prompt_messages, completion, prompt_tokens, completion_tokens, total_tokens, metadata, prompt)` | Logs an LLM call with token usage |
| `end_span(span, *, output, level, is_root)` | Closes span; propagates to trace-level OTel attributes when `is_root=True` |
| `score_trace(trace_id, name, value, *, comment, span)` | Attaches numeric score to trace; when `span=` provided, extracts real OTel trace_id (see Issue 4) |
| `score_observation(observation, name, value, *, comment)` | Scores a specific span/observation |
| `update_trace(span, *, output, metadata, is_root)` | Updates existing span with additional output/metadata |
| `get_prompt(slug, label)` | Fetches managed prompt from Langfuse (60s TTL cache) |
| `prompt_text(slug, label)` | Returns prompt text string (first system message or fallback) |
| `push_prompt(slug, content, *, labels)` | Creates/updates chat prompt in Langfuse |
| `flush()` | Flushes pending events before process exit |
| `set_current_span(span)` | Stores span in thread-local storage for downstream services |
| `get_current_span()` | Retrieves span from thread-local storage |

#### Safe Variants (guaranteed to never raise)

| Function | Wraps |
|---|---|
| `start_trace_safe(...)` | `start_trace(...)` |
| `start_span_safe(...)` | `start_span(...)` |
| `end_span_safe(...)` | `end_span(...)` |
| `score_trace_safe(...)` | `score_trace(...)` |
| `score_observation_safe(...)` | `score_observation(...)` |
| `update_trace_safe(...)` | `update_trace(...)` |

Use the `_safe` variants in hot paths (reconciliation per-invoice loop, case per-stage loop, agent per-tool loop) where a Langfuse failure must never propagate.

#### Internal Helpers

| Function | Purpose |
|---|---|
| `_extract_otel_trace_id(span)` | Extracts real 128-bit OTel trace_id hex from span's `_otel_span` (fixes SDK v4 trace_id mismatch — see Issue 4) |
| `_extract_session_id(span)` | Extracts `session.id` from OTel span attributes with recursive fallback to thread-local root |
| `slug_to_langfuse_name(slug)` | Converts `extraction.invoice_system` → `extraction-invoice_system` (dots → dashes) |
| `langfuse_name_to_slug(name)` | Reverse conversion: dashes back to dots |

---

### 3.2 Observability Helpers (`observability_helpers.py`)

**Location**: `apps/core/observability_helpers.py`

All functions are fail-silent. Functions never raise — errors return safe defaults.

#### Session and Metadata

| Function | Purpose |
|---|---|
| `derive_session_id(*, case_number, invoice_id, document_upload_id, case_id)` | Returns stable session ID for Langfuse session grouping (priority order below) |
| `build_observability_context(*, tenant_id, invoice_id, document_upload_id, extraction_result_id, extraction_run_id, reconciliation_result_id, reconciliation_run_id, case_id, case_number, posting_run_id, actor_user_id, trigger, po_number, vendor_code, vendor_name, reconciliation_mode, match_status, case_stage, posting_stage, source)` | Builds 19-field cross-linking metadata dict |
| `merge_trace_metadata(base, *extras)` | Merges multiple metadata dicts; later keys win; strips `None` values |

#### Per-Flow Metadata Builders

| Function | Populated Fields |
|---|---|
| `build_extraction_eval_metadata(...)` | `prompt_source`, `prompt_hash`, `decision_codes`, `recovery_lane_invoked`, `recovery_lane_succeeded`, `extraction_success`, `final_confidence`, `requires_review_override`, `duplicate_detected`, `approval_status`, `final_outcome` |
| `build_recon_eval_metadata(...)` | `po_found`, `grn_found`, `reconciliation_mode`, `final_match_status`, `exception_count`, `requires_review`, `auto_close_eligible`, `routed_to_agents`, `routed_to_review` |
| `build_agent_eval_metadata(...)` | `planner_source`, `planned_agents`, `executed_agents`, `prior_match_status`, `final_recommendation`, `final_confidence`, `escalation_triggered`, `feedback_rerun_triggered` |
| `build_case_eval_metadata(...)` | `case_id`, `case_number`, `current_stage`, `final_stage`, `current_status`, `final_status`, `resolved_path`, `review_required`, `assigned_queue`, `assigned_reviewer_id`, `reprocess_requested` |
| `build_posting_eval_metadata(...)` | `posting_stage`, `final_status`, `review_queue`, `is_touchless`, `issue_count`, `blocking_issue_count`, `ready_to_submit`, `erp_document_number_present` |
| `build_erp_span_metadata(...)` | `source_used`, `freshness_status`, `connector_name`, `connector_type`, `operation_type`, `result_present`, `retryable_failure`, `sanitized_error_type` |

#### Sanitization

| Function | Purpose |
|---|---|
| `sanitize_langfuse_metadata(meta)` | Strips sensitive keys (`api_key`, `password`, `token`, `secret`, etc.); truncates values >2000 chars; sanitizes nested dicts recursively |
| `sanitize_summary_text(text, max_length)` | Strips non-ASCII characters; truncates to `max_length` |

#### Latency Scoring

| Function | Purpose |
|---|---|
| `latency_ok(latency_ms, threshold_ms)` | Returns `1.0` if within threshold, else `0.0` |
| `score_latency(observation, latency_ms, threshold_ms, *, score_name, comment)` | Emits a latency_ok observation score via `score_observation_safe` |

#### Constants (from `observability_helpers.py`)

**ERP error category labels** (11):
`ERP_ERROR_TIMEOUT`, `ERP_ERROR_UNAUTHORIZED`, `ERP_ERROR_RATE_LIMITED`, `ERP_ERROR_VALIDATION`, `ERP_ERROR_EMPTY_RESULT`, `ERP_ERROR_CONNECTOR_UNAVAILABLE`, `ERP_ERROR_NORMALIZATION_FAILED`, `ERP_ERROR_SUBMISSION_FAILED`, `ERP_ERROR_RETRYABLE`, `ERP_ERROR_NON_RETRYABLE`, `ERP_ERROR_UNKNOWN`

**Resolution source labels** (6):
`SOURCE_CACHE`, `SOURCE_LIVE_API`, `SOURCE_MIRROR_DB`, `SOURCE_DB_FALLBACK`, `SOURCE_MANUAL_OVERRIDE`, `SOURCE_NONE`

**Freshness labels** (3):
`FRESHNESS_FRESH`, `FRESHNESS_STALE`, `FRESHNESS_UNKNOWN`

---

### 3.3 Prompt Naming and Composition

**Slug → Langfuse name**: All dots replaced with dashes via `slug_to_langfuse_name()`.

| Local slug (`PromptRegistry`) | Langfuse name |
|---|---|
| `extraction.invoice_system` | `extraction-invoice_system` |
| `extraction.invoice_base` | `extraction-invoice_base` |
| `extraction.invoice_category_goods` | `extraction-invoice_category_goods` |
| `extraction.invoice_category_service` | `extraction-invoice_category_service` |
| `extraction.invoice_category_travel` | `extraction-invoice_category_travel` |
| `extraction.country_india_gst` | `extraction-country_india_gst` |
| `extraction.country_generic_vat` | `extraction-country_generic_vat` |
| `agent.exception_analysis` | `agent-exception_analysis` |
| `agent.invoice_understanding` | `agent-invoice_understanding` |
| _(+ 9 more agent prompts)_ | — |

**18 prompts total** are registered in `_DEFAULTS` and pushed by `push_prompts_to_langfuse`.

#### Prompt Composition Flow (Phase 2)

`InvoicePromptComposer` assembles the final system prompt at extraction time:

```
extraction.invoice_base          (base extraction instructions)
  + extraction.invoice_category_{category}   (goods / service / travel overlay)
  + extraction.country_{country}_{regime}    (e.g. country_india_gst)
  ─────────────────────────────────────────
  = final_prompt  →  InvoiceExtractionAgent
```

Resolution order for each component:
1. Langfuse (production label, 60s TTL cache)
2. DB (`PromptTemplate` model)
3. Hardcoded `_DEFAULTS` in `prompt_registry.py`

`InvoicePromptComposer.compose()` returns a `PromptComposition` with `final_prompt`, `components` dict, and `prompt_hash` (sha256 first 16 chars). The hash is logged as `prompt_hash` metadata on every `invoice_extraction` trace.

---

### 3.4 DB Model Trace ID Fields

The following models carry trace ID fields for cross-referencing Langfuse traces with DB records:

| Model | Field | Field Type | Persisted from |
|---|---|---|---|
| `ExtractionResult` | `langfuse_trace_id` | `CharField(max_length=255, blank=True)` | `process_invoice_upload_task` |
| `ReconciliationRun` | `langfuse_trace_id` | `CharField(max_length=64, db_index=True)` | `run_reconciliation_task` |
| `PostingRun` | `langfuse_trace_id` | `CharField(max_length=64, db_index=True)` | `PostingPipeline.run()` |
| `AgentRun` | `trace_id` | `CharField(max_length=64)` | Agent orchestrator |
| `APCaseStage` | `trace_id` | `CharField(max_length=64)` | Case orchestrator |
| `CopilotSession` | `trace_id` | `CharField(max_length=64)` | Copilot service |
| `ProcessingLog` | `trace_id` | `CharField(max_length=64)` | Middleware/decorators |
| `AuditEvent` | `trace_id` | `CharField(max_length=64)` | Middleware/decorators |

**Migration note** (`langfuse_trace_id` fields): `blank=True, default=""` — no data backfill needed.

```bash
python manage.py migrate reconciliation  # 0006_add_langfuse_trace_id
python manage.py migrate extraction      # 0011_add_langfuse_trace_id
python manage.py migrate posting_core    # 0004_add_langfuse_trace_id
```

---

## 4. Trace Hierarchy

Full nesting diagram for all active pipelines:

```
root trace  (start_trace)
  ── extraction_pipeline     (process_invoice_upload_task — root trace per upload)
     ── ocr_extraction       (OCR + LLM via adapter.extract())
        ── INVOICE_EXTRACTION (InvoiceExtractionAgent)
           ── llm_chat       (log_generation, one per LLM call)
     ── document_type_classification
     ── governed_pipeline    (ExtractionPipeline.run())
     ── parsing
     ── normalization
     ── field_confidence     (scores: weakest_critical_score)
     ── validation           (scores: validation_is_valid)
     ── decision_code_derivation (scores: decision_code_count)
     ── recovery_lane        (scores: recovery_invoked)
     ── duplicate_detection  (scores: is_duplicate)
     ── persistence
     ── approval_gate        (scores: requires_human_review)
     Trace-level scores: extraction_confidence, extraction_success,
       extraction_is_valid, extraction_is_duplicate, extraction_requires_review,
       weakest_critical_field_score, decision_code_count, response_was_repaired, qr_detected

  ── agent_pipeline          (AgentOrchestrator.execute() — root trace)
     ── EXCEPTION_ANALYSIS / INVOICE_UNDERSTANDING / ...  (per-agent spans)
        ── llm_chat          (log_generation per LLM round)
        ── tool_po_lookup    (per tool call in ReAct loop)
           ── erp_resolution    (when ERP connector available)
              ── erp_cache_lookup
              ── erp_live_lookup
              ── erp_db_fallback
        ── tool_grn_lookup
           ── erp_resolution    (same pattern)
        ── tool_invoice_details
     ── reviewer_summary
        ── llm_chat
     Pipeline-level scores: agent_pipeline_final_confidence,
       agent_pipeline_recommendation_present, agent_pipeline_escalation_triggered,
       agent_pipeline_auto_close_candidate, agent_pipeline_agents_executed_count
     Per-agent scores: agent_confidence, agent_recommendation_present, agent_tool_success_rate

  ── agent_pipeline_task     (run_agent_pipeline_task Celery task wrapper — root trace)
     (orchestrator's agent_pipeline trace runs under its own trace_id)

  ── invoice_extraction      (InvoiceExtractionAgent standalone — when no ctx._langfuse_trace)

  ── llm_extract_fallback    (InvoiceExtractionAdapter._llm_extract() — direct Azure OAI fallback)
     ── LLM_EXTRACT_FALLBACK
        ── llm_extract_fallback_chat  (log_generation)

  ── reconciliation_task     (run_reconciliation_task Celery task wrapper — root trace)
     ── reconciliation_run   (ReconciliationRunnerService.run() — child span)
        ── po_lookup              (per invoice: erp_source_type, is_stale metadata)
           ── erp_resolution       (when ERP connector available)
              ── erp_cache_lookup
              ── erp_live_lookup
              ── erp_db_fallback
        ── mode_resolution
        ── grn_lookup             (THREE_WAY only)
        ── match_execution
        ── classification
        ── result_persist
        ── exception_build
        ── review_workflow_trigger
     Trace-level scores: reconciliation_match, recon_final_status_matched,
       recon_final_status_partial_match, recon_final_status_requires_review,
       recon_final_status_unmatched, recon_po_found, recon_grn_found,
       recon_auto_close_eligible, recon_routed_to_review, recon_exception_count_final,
       recon_final_success, recon_routed_to_agents

  ── posting_pipeline        (PostingPipeline.run() — one per PostingRun)
     ── eligibility_check    (stage 1)
     ── snapshot_build       (stage 2)
     ── mapping              (stage 3)
        ── erp_resolve_vendor
           ── erp_resolution → erp_cache_lookup / erp_live_lookup / erp_db_fallback
        ── erp_resolve_item
        ── erp_resolve_tax
        ── erp_resolve_cost_center
        ── erp_resolve_po
     ── validation           (stage 4)
     ── confidence_scoring   (stage 5, emits posting_confidence score)
     ── review_routing       (stage 6, emits posting_requires_review score)
     ── payload_build        (stage 7)
     ── finalization         (stage 8)
     ── duplicate_check      (stage 9b)
        ── erp_resolution (duplicate invoice check)

  ── erp_submission_standalone  (when no parent trace ID available)
     ── erp_submission
  ── erp_status_check           (PostingSubmitResolver.get_posting_status() — always isolated)

  ── case_pipeline           (process_case_task / reprocess_case_from_stage_task)
     ── case_stage_INTAKE
     ── case_stage_EXTRACTION
     ── case_stage_EXTRACTION_APPROVAL
     ── case_stage_PATH_RESOLUTION
     ── case_stage_PO_RETRIEVAL
     ── case_stage_TWO_WAY_MATCHING
     ── case_stage_THREE_WAY_MATCHING
     ── case_stage_EXCEPTION_ANALYSIS
     ── case_stage_REVIEW_ROUTING
     ── case_stage_NON_PO_VALIDATION
     ── case_stage_CASE_SUMMARY
     Trace-level scores: case_processing_success, case_stages_executed,
       case_closed, case_terminal, case_reprocessed,
       case_path_resolved, case_match_status, case_auto_closed,
       case_routed_to_review

  ── review_assignment       (ReviewWorkflowService.create_assignment() — root trace)
     ── review_assign_reviewer
     ── review_start
     ── review_record_action (per reviewer action — field corrections, etc.)
     ── review_add_comment
     ── review_finalise      (approve/reject/reprocess decision)
     Trace-level scores: review_priority, review_assignment_created,
       review_decision, review_approved, review_rejected,
       review_reprocess_requested, review_had_corrections, review_fields_corrected_count

  ── bulk_extraction_job     (run_bulk_job_task — one per BulkExtractionJob)
     ── bulk_item_extraction (one per eligible item)
     ── gdrive_test_connection / gdrive_list_files / gdrive_download_file
     ── onedrive_test_connection / onedrive_list_files / onedrive_download_file

  ── copilot_answer          (APCopilotService.answer_question())
```

---

## 5. User and Session Attribution

Every root trace carries:

- **`user.id`** — Django `User.pk` as string. Populates the **Users** tab in Langfuse.
- **`session.id`** — Derived via `derive_session_id()` with priority:

| Priority | Pattern | Source |
|---|---|---|
| 1 | `case-{case_number}` | Case-anchored flows (AP Case created at upload time) |
| 2 | `invoice-{invoice_id}` | Invoice-centric flows |
| 3 | `upload-{upload_id}` | Pre-invoice extraction (rare fallback) |
| 4 | `case-{case_id}` | Case-only flows (numeric fallback) |
| 5 | `None` | No context available |

`case_number` is the preferred anchor because AP Cases are created at upload time — every downstream pipeline stage can use the same `session_id`, enabling unified trace linking in the Langfuse Sessions view.

These are set as OpenTelemetry span attributes (`user.id`, `session.id`) on the root `LangfuseSpan._otel_span` immediately after `start_observation()` returns (SDK v4 requirement — see Issue 1).

---

## 6. Tool Call Spans

Every tool execution inside `BaseAgent.run()` (the ReAct loop) is wrapped in a Langfuse child span under the current agent span.

**Location**: `apps/agents/services/base_agent.py` — `for tc in llm_resp.tool_calls:` loop.

```python
_tool_span = start_span(
    _lf_span,
    name=f"tool_{tc.name}",
    metadata={"tool_name": tc.name, "tool_call_id": tc.id, "arguments": tc.arguments},
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

- Only runs when `_lf_span is not None`.
- Applies to all 8 agent types that use `BaseAgent.run()`. `InvoiceExtractionAgent` has its own `run()` override and uses no tools.
- `BaseAgent._execute_tool()` injects `lf_parent_span=_tool_span` into tool kwargs before calling `tool.execute(**arguments)`. The span is removed from `arguments` after execution to avoid serialisation errors.

---

## 7. Trace Call Sites by Pipeline

### 7.1 Extraction Pipeline

**Files**: `apps/extraction/tasks.py`, `apps/extraction/services/approval_service.py`, `apps/extraction_core/services/extraction_pipeline.py`

**Root trace** (`process_invoice_upload_task`): `start_trace(trace_id, "extraction_pipeline", ...)`. Trace ID: upload `trace_id`. Persisted to `ExtractionResult.langfuse_trace_id`.

**Approval scores** (`apps/extraction/services/approval_service.py`): Uses `f"approval-{approval.pk}"` as trace ID across all three decision points:

- `try_auto_approve()` → `extraction_auto_approve_confidence`
- `approve()` → `extraction_approval_decision=1.0`, `extraction_approval_confidence`, `extraction_corrections_count` (only if corrections > 0)
- `reject()` → `extraction_approval_decision=0.0`

**Extraction pipeline scores** (`extraction_pipeline.py` Step 9): `extraction_confidence` (0.0–1.0) and `extraction_requires_review` (0.0/1.0) using `str(run.pk)` as trace ID.

---

### 7.2 Reconciliation Pipeline

**Files**: `apps/reconciliation/tasks.py`, `apps/reconciliation/services/runner_service.py`

**Task-level root trace** (`run_reconciliation_task`): Name `"reconciliation_task"`, trace ID `f"recon-task-{celery_task_id}"`. Persisted to `ReconciliationRun.langfuse_trace_id`.

**Service child span** (`ReconciliationRunnerService.run()`): `start_span(lf_task_trace, "reconciliation_run")`. Closed after `recon_run.save()`.

**Per-invoice child spans** (in `_reconcile_single`):

| Span | Wraps | Output |
|---|---|---|
| `po_lookup` | `POLookupService.lookup()` | `erp_source_type`, `is_stale` |
| `mode_resolution` | `ModeResolutionResolver.resolve()` | `mode` |
| `grn_lookup` | `GRNLookupService.lookup()` (3-way only) | `grn_count`, `is_stale` |
| `match_execution` | `ReconciliationExecutionRouter.execute()` + `ClassificationService.classify()` | `match_status`, header/line/grn ratios |
| `classification` | Match classification | `auto_close_candidate` flag |
| `result_persist` | `ReconciliationResultService.save()` | `result_id` |
| `exception_build` | `ExceptionBuilderService.build()` | `exception_count` |
| `review_workflow_trigger` | `ReviewWorkflowService` | creates `ReviewAssignment` when needed |

**`reconciliation_match` score** (emitted per invoice):

| `MatchStatus` | Score |
|---|---|
| `MATCHED` | `1.0` |
| `PARTIAL_MATCH` | `0.5` |
| `REQUIRES_REVIEW` | `0.3` |
| `UNMATCHED` | `0.0` |

Fallback: when called without a Celery task ID, runner creates its own standalone `"reconciliation_run"` root trace.

---

### 7.3 Agent Pipeline

**Files**: `apps/agents/services/orchestrator.py`, `apps/agents/services/agent_classes.py`, `apps/agents/services/guardrails_service.py`, `apps/agents/tasks.py`

**Task-level root trace** (`run_agent_pipeline_task`): Name `"agent_pipeline_task"`, metadata includes `prior_match_status`, `reconciliation_mode`, `trigger`.

**Orchestrator trace** (`AgentOrchestrator.execute()`): Name `"agent_pipeline"`. Passed via `ctx._langfuse_trace` so every child agent creates a span under it.

**Standalone extraction** (`InvoiceExtractionAgent.run()` — when no `ctx._langfuse_trace`): Creates own root trace named `"invoice_extraction"`.

**RBAC guardrail scores** (`apps/agents/services/guardrails_service.py`):

| Method | Grant scored | Deny scored | Score name |
|---|---|---|---|
| `authorize_orchestration` | via `log_guardrail_decision` | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_agent` | direct (TraceContext) | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_tool` | direct (TraceContext) | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_recommendation` | via `log_guardrail_decision` | via `log_guardrail_decision` | `rbac_guardrail` |
| `authorize_data_scope` | not scored (noise) | direct (TraceContext) | `rbac_data_scope` |

Score values: `rbac_guardrail=1.0` (GRANTED), `rbac_guardrail=0.0` (DENIED). Filter `score:rbac_guardrail=0` in Langfuse to find all authorization failures.

---

### 7.4 Posting Pipeline

**File**: `apps/posting_core/services/posting_pipeline.py`

**Root trace**: `str(posting_run.pk)` as trace ID, name `"posting_pipeline"`. Persisted to `PostingRun.langfuse_trace_id`.

**Per-stage child spans** (created by `_open_stage_span` / `_close_stage_span` helpers):

| Stage | Span | Key Output | Score Emitted |
|---|---|---|---|
| 1 | `eligibility_check` | `passed: True` | — |
| 2 | `snapshot_build` | `built: True` | — |
| 3 | `mapping` | `vendor_resolved`, `lines_count`, `mapping_issues`, `connector_used` | — |
| 4 | `validation` | `total_issues` | — |
| 5 | `confidence_scoring` | `confidence`, `issue_count` | `posting_confidence` (0.0–1.0) |
| 6 | `review_routing` | `requires_review`, `queue`, `reason_count` | `posting_requires_review` (0.0/1.0) |
| 7 | `payload_build` | `lines_in_payload` | — |
| 8 | `finalization` | `artifacts_persisted: True` | — |
| 9 | `duplicate_check` | `is_duplicate`, `source_type` | — |

**ERP submission**: When `lf_parent_span` is forwarded from `PostingActionService`, the `erp_submission` span nests under the posting pipeline. Fallback standalone root trace ID: `f"erp-sub-{posting_run_id}"` → `f"erp-inv-{invoice_id}"` → `uuid4().hex`.

---

### 7.5 Case Pipeline

**Files**: `apps/cases/tasks.py`, `apps/cases/orchestrators/case_orchestrator.py`

Both `process_case_task` and `reprocess_case_from_stage_task` open a `"case_task"` root trace before the orchestrator runs. Trace ID: `f"case-{case_id}"` (stable across retries — all attempts for same case are grouped).

Orchestrator creates per-stage spans named `case_stage_{STAGE_NAME}` with `stage_index` and `case_status_before` metadata. Stage-level scores use `_safe` variants with `span=self._lf_trace`.

---

### 7.6 Review Workflow

**File**: `apps/cases/services/review_workflow_service.py`

Trace ID convention: `f"review-{assignment.pk}"` — consistent across creation, priority scoring, and final decision scoring.

**`review_decision` score mapping**:

| `ReviewStatus` | Score |
|---|---|
| `APPROVED` | `1.0` |
| `REPROCESSED` | `0.5` |
| `REJECTED` | `0.0` |

**`review_priority` score**: `float(priority) / 10.0` — priority 1–10 normalised to 0.0–1.0.

---

### 7.7 ERP Integration

**File**: `apps/erp_integration/services/langfuse_helpers.py` (dedicated helpers module)

All tracing is fail-silent. The module provides:

| Function | Purpose |
|---|---|
| `sanitize_erp_metadata(meta)` | Strips sensitive keys; truncates values >2000 chars |
| `sanitize_erp_error(error)` | Maps raw errors to safe categories |
| `start_erp_span(parent, name, metadata)` | Opens child span with auto-sanitised metadata |
| `end_erp_span(span, output, level)` | Closes span with sanitised output |
| `score_erp_observation(span, name, value)` | Attaches observation-level score |
| `score_erp_trace(trace_id, name, value)` | Attaches trace-level score |
| `build_source_chain(...)` | Returns compact list of sources attempted e.g. `["cache:miss", "live_api:ok"]` |
| `freshness_status_label(is_stale, source_type)` | Returns `"fresh"` or `"stale"` |
| `is_authoritative_source(source_type)` | Returns `True` for `API` or `CACHE` sources |

**3-stage span hierarchy** (when `lf_parent_span` provided to `ERPResolutionService`):

```
erp_resolution      (created by _trace_resolve)
  ── erp_cache_lookup   (score: erp_cache_hit)
  ── erp_live_lookup    (scores: erp_live_lookup_success, erp_live_lookup_latency_ok,
                                 erp_live_lookup_rate_limited, erp_live_lookup_timeout)
  ── erp_db_fallback    (scores: erp_db_fallback_used, erp_db_fallback_success)
```

**`lf_parent_span` threading pattern**:

| Caller | Parent span source |
|---|---|
| `PostingMappingEngine._try_vendor_via_resolver()` | `self._lf_mapping_span` (posting stage 3) |
| `PostingMappingEngine._try_item_via_resolver()` | `self._lf_mapping_span` |
| `PostingMappingEngine._try_tax_via_resolver()` | `self._lf_mapping_span` |
| `PostingMappingEngine._try_cost_center_via_resolver()` | `self._lf_mapping_span` |
| `PostingMappingEngine._load_po_refs()` | `self._lf_mapping_span` |
| `PostingPipeline._check_duplicate()` | `_lf_s9` (duplicate_check stage span) |
| `POLookupService.lookup()` | `lf_parent_span` from `runner_service._lf_po` |
| `GRNLookupService.lookup()` | `lf_parent_span` (not yet threaded from `ThreeWayMatchService`) |
| `POLookupTool._resolve_via_erp()` | `kwargs["lf_parent_span"]` (injected by `BaseAgent._execute_tool`) |
| `GRNLookupTool._resolve_via_erp()` | `kwargs["lf_parent_span"]` (same) |

**Metadata sanitisation**: Keys always stripped: `api_key`, `api_secret`, `token`, `access_token`, `refresh_token`, `bearer`, `password`, `secret`, `secret_key`, `authorization`, `auth_header`, `auth_token`, `credentials`, `client_secret`, `private_key`, `cookie`, `session_token`, `x-api-key`.

---

### 7.8 Bulk Extraction

**Files**: `apps/extraction/bulk_tasks.py`, `apps/extraction/services/bulk_service.py`, `apps/extraction/services/bulk_source_adapters.py`

**Root trace** (`run_bulk_job_task`): Trace ID `job.trace_id or str(job.pk)`. After completion, emits `bulk_job_success_rate = job.total_success / (job.total_found or 1)`.

**Per-item spans**: `"bulk_item_extraction"` child span per eligible item. Items with `status == FAILED` are closed with `level="ERROR"`.

**Connector spans** (Google Drive and OneDrive only — `LocalFolderBulkSourceAdapter` is unwrapped):

| Adapter | Span | Key Output |
|---|---|---|
| Google Drive | `gdrive_test_connection` | — |
| Google Drive | `gdrive_list_files` | `file_count` |
| Google Drive | `gdrive_download_file` | — |
| OneDrive | `onedrive_test_connection` | — |
| OneDrive | `onedrive_list_files` | `file_count` |
| OneDrive | `onedrive_download_file` | — |

Adapters receive the trace via `adapter.lf_trace = lf_trace` set on the instance before any method call.

---

### 7.9 Copilot

**File**: `apps/copilot/services/copilot_service.py`

**`answer_question()`**: Trace ID `session.trace_id or f"copilot-{session.pk}"`, session_id `f"copilot-{session.pk}"`. Closed before each return path with `{"topic": _topic, "case_id": ...}`.

**`archive_session()`**: Emits `copilot_session_length` (raw message count float) using trace ID `f"copilot-{session_id}"`.

---

## 8. Score Taxonomy

All score names are constants in `apps/core/evaluation_constants.py`. **Do not use raw string literals at call sites.**

Total: **165 constants** (117 score names + 13 trace names + 7 latency thresholds + 28 additional cross-cutting/system/supervisor constants).

### 8.1 Domain Groups

| Domain Prefix | Count | Examples |
|---|---|---|
| `EXTRACTION_` | 21 | `EXTRACTION_SUCCESS`, `EXTRACTION_CONFIDENCE`, `EXTRACTION_OCR_CHAR_COUNT`, `EXTRACTION_APPROVAL_DECISION` |
| `RECON_` | 54 | `RECON_FINAL_SUCCESS`, `RECON_PO_FOUND`, `RECON_HEADER_MATCH_RATIO`, `RECON_BLOCKING_EXCEPTION_COUNT`, `RECON_PREDICTED_MATCH_STATUS`, `RECON_SIG_*` (learning signals) |
| `AGENT_` / `AGENT_PIPELINE_` | 10 | `AGENT_CONFIDENCE`, `AGENT_PIPELINE_FINAL_CONFIDENCE`, `AGENT_FEEDBACK_IMPROVED_OUTCOME` |
| `CASE_` | 19 | `CASE_CLOSED`, `CASE_MATCH_STATUS`, `CASE_NON_PO_RISK_SCORE`, `CASE_NON_PO_APPROVAL_READY` |
| `REVIEW_` | 9 | `REVIEW_APPROVED`, `REVIEW_DECISION`, `REVIEW_FIELDS_CORRECTED_COUNT`, `REVIEW_REQUIRED_CORRECTLY_TRIGGERED` |
| `POSTING_` | 16 | `POSTING_FINAL_CONFIDENCE`, `POSTING_VENDOR_MAPPING_SUCCESS`, `POSTING_REFERENCE_FRESHNESS_SCORE` |
| `ERP_` | 24 | `ERP_RESOLUTION_SUCCESS`, `ERP_CACHE_HIT`, `ERP_SUBMISSION_SUCCESS`, `ERP_DUPLICATE_FOUND` |
| `SYSTEM_` | 7 | `SYSTEM_AGENT_SUCCESS`, `SYSTEM_REVIEW_ROUTING_SUCCESS`, `SYSTEM_BULK_INTAKE_SUCCESS`, `SYSTEM_POSTING_PREPARATION_SUCCESS` |
| `SUPERVISOR_` | 5 | `SUPERVISOR_CONFIDENCE`, `SUPERVISOR_RECOMMENDATION_PRESENT`, `SUPERVISOR_RECOVERY_USED`, `SUPERVISOR_AUTO_CLOSE_CANDIDATE` |
| `RBAC_` | 2 | `RBAC_GUARDRAIL`, `RBAC_DATA_SCOPE` |
| `COPILOT_` | 1 | `COPILOT_SESSION_LENGTH` |
| `TOOL_` | 1 | `TOOL_CALL_SUCCESS` |
| Cross-cutting | ~8 | `LATENCY_OK`, `FALLBACK_USED`, `DECISION_CONFIDENCE_ALIGNMENT`, `STALE_DATA_ACCEPTED`, `TOUCHLESS_CANDIDATE_SELECTED`, `ROUTING_DECISION_EXECUTED`, `FALLBACK_USED_BUT_SUCCESSFUL` |

#### Full RECON_ Domain (54 constants)

`RECON_ACTUAL_AUTO_CLOSE`, `RECON_ACTUAL_FINAL_ROUTE`, `RECON_ACTUAL_GRN_FOUND`, `RECON_ACTUAL_MATCH_STATUS`, `RECON_ACTUAL_REVIEW_CREATED`, `RECON_AUTO_CLOSE_CORRECT`, `RECON_AUTO_CLOSE_ELIGIBLE`, `RECON_BLOCKING_EXCEPTION_COUNT`, `RECON_CLASSIFIED_AUTO_CLOSE`, `RECON_CLASSIFIED_REQUIRES_REVIEW`, `RECON_CORRECTED_BY_REVIEWER`, `RECON_EXCEPTION_COUNT_FINAL`, `RECON_FINAL_STATUS_MATCHED`, `RECON_FINAL_STATUS_PARTIAL_MATCH`, `RECON_FINAL_STATUS_REQUIRES_REVIEW`, `RECON_FINAL_STATUS_UNMATCHED`, `RECON_FINAL_SUCCESS`, `RECON_GRN_FOUND`, `RECON_GRN_FOUND_CORRECT`, `RECON_GRN_LOOKUP_AUTHORITATIVE`, `RECON_GRN_LOOKUP_FRESH`, `RECON_GRN_LOOKUP_SUCCESS`, `RECON_GRN_MATCH_RATIO`, `RECON_HEADER_MATCH_RATIO`, `RECON_INVOICE_ERROR`, `RECON_LINE_MATCH_RATIO`, `RECON_MATCH_STATUS_CORRECT`, `RECON_PO_FOUND`, `RECON_PO_FOUND_CORRECT`, `RECON_PO_LOOKUP_AUTHORITATIVE`, `RECON_PO_LOOKUP_FRESH`, `RECON_PO_LOOKUP_SUCCESS`, `RECON_PREDICTED_AUTO_CLOSE`, `RECON_PREDICTED_GRN_FOUND`, `RECON_PREDICTED_MATCH_STATUS`, `RECON_PREDICTED_PO_FOUND`, `RECON_PREDICTED_REQUIRES_REVIEW`, `RECON_RECONCILIATION_MATCH`, `RECON_REPROCESSED`, `RECON_REVIEW_OUTCOME`, `RECON_REVIEW_ROUTE_CORRECT`, `RECON_ROUTED_TO_AGENTS`, `RECON_ROUTED_TO_REVIEW`, `RECON_SIG_MISSING_GRN`, `RECON_SIG_MISSING_PO`, `RECON_SIG_REPROCESS`, `RECON_SIG_REVIEW_OVERRIDE`, `RECON_SIG_TOLERANCE_REVIEW`, `RECON_SIG_WRONG_AUTO_CLOSE`, `RECON_SIG_WRONG_MATCH_STATUS`, `RECON_SIG_WRONG_REVIEW_ROUTE`, `RECON_TOLERANCE_PASSED`, `RECON_WARNING_EXCEPTION_COUNT`

Note: `RECON_SIG_*` constants (7 signal types) and `RECON_PREDICTED_*` / `RECON_ACTUAL_*` constants reflect the evaluation framework's pattern-matching and learning signal infrastructure.

---

### 8.2 Root Trace Name Constants

13 constants in `evaluation_constants.py`:

| Constant | Value |
|---|---|
| `TRACE_EXTRACTION_PIPELINE` | `"extraction_pipeline"` |
| `TRACE_RECONCILIATION_PIPELINE` | `"reconciliation_pipeline"` |
| `TRACE_AGENT_PIPELINE` | `"agent_pipeline"` |
| `TRACE_CASE_PIPELINE` | `"case_pipeline"` |
| `TRACE_POSTING_PIPELINE` | `"posting_pipeline"` |
| `TRACE_ERP_SUBMISSION_PIPELINE` | `"erp_submission_pipeline"` |
| `TRACE_REVIEW_WORKFLOW` | `"review_workflow"` |
| `TRACE_COPILOT_SESSION` | `"copilot_session"` |
| `TRACE_SYSTEM_AGENT` | `"system_agent"` |
| `TRACE_SYSTEM_BULK_INTAKE` | `"system_bulk_intake"` |
| `TRACE_SYSTEM_CASE_INTAKE` | `"system_case_intake"` |
| `TRACE_SYSTEM_POSTING_PREPARATION` | `"system_posting_preparation"` |
| `TRACE_SUPERVISOR_PIPELINE` | `"supervisor_pipeline"` |

---

### 8.3 Latency Thresholds

7 constants in `evaluation_constants.py`:

| Constant | Value (ms) | Use Case |
|---|---|---|
| `LATENCY_THRESHOLD_OCR_MS` | 30000 | Document Intelligence OCR |
| `LATENCY_THRESHOLD_LLM_MS` | 20000 | LLM generation call |
| `LATENCY_THRESHOLD_ERP_MS` | 5000 | ERP API call / resolution stage |
| `LATENCY_THRESHOLD_DB_MS` | 2000 | Database query / fallback |
| `LATENCY_THRESHOLD_RECON_STAGE_MS` | 5000 | Reconciliation sub-stage |
| `LATENCY_THRESHOLD_POSTING_STAGE_MS` | 5000 | Posting pipeline stage |
| `LATENCY_THRESHOLD_TOOL_CALL_MS` | 10000 | Agent tool call |

---

### 8.4 Full Score Reference Table

| Score name | Value range | Emitted from | Trace ID convention |
|---|---|---|---|
| `reconciliation_match` | 0.0 / 0.3 / 0.5 / 1.0 | `runner_service.py` `_reconcile_single()` | `run.trace_id` or `str(run.pk)` |
| `posting_confidence` | 0.0–1.0 | `posting_pipeline.py` Stage 5 | `str(posting_run.pk)` |
| `posting_requires_review` | 0.0 or 1.0 | `posting_pipeline.py` Stage 6 | `str(posting_run.pk)` |
| `extraction_confidence` | 0.0–1.0 | `extraction_pipeline.py` Step 9 | `str(run.pk)` |
| `extraction_requires_review` | 0.0 or 1.0 | `extraction_pipeline.py` Step 9 | `str(run.pk)` |
| `review_priority` | 0.0–1.0 (priority/10) | `review_workflow_service.py` `create_assignment()` | `f"review-{assignment.pk}"` |
| `review_decision` | 0.0 / 0.5 / 1.0 | `review_workflow_service.py` `_finalise()` | `f"review-{assignment.pk}"` |
| `extraction_auto_approve_confidence` | 0.0–1.0 | `approval_service.py` `try_auto_approve()` | `f"approval-{approval.pk}"` |
| `extraction_approval_decision` | 0.0 or 1.0 | `approval_service.py` `approve()` / `reject()` | `f"approval-{approval.pk}"` |
| `extraction_approval_confidence` | 0.0–1.0 | `approval_service.py` `approve()` | `f"approval-{approval.pk}"` |
| `extraction_corrections_count` | 0.0+ (raw count) | `approval_service.py` `approve()` (only if corrections > 0) | `f"approval-{approval.pk}"` |
| `rbac_guardrail` | 0.0 or 1.0 | `guardrails_service.py` all methods | active pipeline `trace_id` via `TraceContext.get_current()` |
| `rbac_data_scope` | 0.0 (deny only) | `guardrails_service.py` `authorize_data_scope()` | `TraceContext.get_current().trace_id` |
| `bulk_job_success_rate` | 0.0–1.0 | `bulk_tasks.py` `run_bulk_job_task` | `job.trace_id` or `str(job.pk)` |
| `copilot_session_length` | 0.0+ (raw message count) | `copilot_service.py` `archive_session()` | `f"copilot-{session_id}"` |
| `erp_resolution_success` | 0.0 or 1.0 | `resolution_service.py` `_trace_resolve()` | parent pipeline trace |
| `erp_resolution_latency_ok` | 0.0 or 1.0 | `resolution_service.py` `_trace_resolve()` | parent pipeline trace |
| `erp_resolution_result_present` | 0.0 or 1.0 | `resolution_service.py` `_trace_resolve()` | parent pipeline trace |
| `erp_resolution_fresh` | 0.0 or 1.0 | `resolution_service.py` `_trace_resolve()` | parent pipeline trace |
| `erp_resolution_authoritative` | 0.0 or 1.0 | `resolution_service.py` `_trace_resolve()` | parent pipeline trace |
| `erp_resolution_used_fallback` | 0.0 or 1.0 | `resolution_service.py` `_trace_resolve()` | parent pipeline trace |
| `erp_cache_hit` | 0.0 or 1.0 | `base.py` `_cache_check_traced()` | parent pipeline trace |
| `erp_live_lookup_success` | 0.0 or 1.0 | `base.py` `_api_lookup_traced()` | parent pipeline trace |
| `erp_live_lookup_latency_ok` | 0.0 or 1.0 | `base.py` `_api_lookup_traced()` | parent pipeline trace |
| `erp_live_lookup_rate_limited` | 0.0 or 1.0 | `base.py` `_api_lookup_traced()` | parent pipeline trace |
| `erp_live_lookup_timeout` | 0.0 or 1.0 | `base.py` `_api_lookup_traced()` | parent pipeline trace |
| `erp_db_fallback_used` | 1.0 (always) | `base.py` `_db_fallback_traced()` | parent pipeline trace |
| `erp_db_fallback_success` | 0.0 or 1.0 | `base.py` `_db_fallback_traced()` | parent pipeline trace |
| `erp_submission_attempted` | 1.0 (always) | `posting_submit_resolver.py` via `trace_erp_submission` | `f"erp-sub-{posting_run_id}"` or parent |
| `erp_submission_success` | 0.0 or 1.0 | `posting_submit_resolver.py` | same |
| `erp_submission_latency_ok` | 0.0 or 1.0 | `posting_submit_resolver.py` | same |
| `erp_submission_retryable_failure` | 0.0 or 1.0 | `posting_submit_resolver.py` | same |
| `erp_submission_document_number_present` | 0.0 or 1.0 | `posting_submit_resolver.py` | same |
| `erp_duplicate_found` | 0.0 or 1.0 | `posting_pipeline.py` via `trace_erp_duplicate_check` | parent pipeline trace |

---

### 8.5 Adding a New Score

1. Add the constant in the correct domain group in `apps/core/evaluation_constants.py`.
2. Add a brief inline comment describing the score semantics and value range.
3. Import and use the constant at every call site — no raw string literals.
4. Add the score to the reference table above.
5. If emitting from a hot path, use `score_trace_safe()` or `score_observation_safe()`.
6. Pass `span=` to `score_trace_safe()` so the real OTel trace_id is used (see Issue 4).

---

## 9. Cross-Flow Correlation

### Session ID Convention

All pipelines derive their session ID via `derive_session_id()` from `apps/core/observability_helpers.py`:

```python
from apps.core.observability_helpers import derive_session_id

session_id = derive_session_id(case_number="AP-260407-0001")  # "case-AP-260407-0001"
session_id = derive_session_id(invoice_id=42)                 # "invoice-42"
session_id = derive_session_id(document_upload_id=7)          # "upload-7"
session_id = derive_session_id(case_id=3)                     # "case-3"
session_id = derive_session_id()                              # None
```

`case_number` is the preferred anchor because AP Cases are created at upload time — every downstream pipeline stage can use the same `session_id`.

### Metadata Builder

`build_observability_context()` produces a 19-field cross-linking metadata dict:

```python
from apps.core.observability_helpers import build_observability_context

meta = build_observability_context(
    invoice_id=42,
    case_id=3,
    actor_user_id=1,
    trigger="manual",
    source="deterministic",
)
# {"invoice_id": 42, "case_id": 3, "actor_user_id": 1, "trigger": "manual", "source": "deterministic"}
```

`None` and empty-string values are excluded from the output dict.

### Latency Scoring

```python
from apps.core.observability_helpers import latency_ok, score_latency
from apps.core.evaluation_constants import LATENCY_THRESHOLD_ERP_MS

passed = latency_ok(duration_ms=3200, threshold_ms=LATENCY_THRESHOLD_ERP_MS)  # True
score_latency(span, duration_ms=3200, threshold_ms=LATENCY_THRESHOLD_ERP_MS,
              score_name="erp_resolution_latency_ok")                          # scores 1.0
```

---

## 10. Prompt Management

### Push All Prompts to Langfuse

```bash
python manage.py push_prompts_to_langfuse
```

### Delete All Prompts and Reseed (fixes name mismatches)

```bash
python manage.py push_prompts_to_langfuse --purge
```

The `--purge` flag:
1. Calls `GET /api/public/v2/prompts` to list all existing prompts.
2. Calls `DELETE /api/public/v2/prompts/{name}` for each one.
3. Proceeds with the normal push of all `PromptRegistry` defaults.

Authentication for the REST calls uses HTTP Basic Auth with `LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY`.

### Other Options

```bash
# Push only one prompt
python manage.py push_prompts_to_langfuse --slug agent.exception_analysis

# Push with a specific label
python manage.py push_prompts_to_langfuse --label staging

# Preview without sending
python manage.py push_prompts_to_langfuse --dry-run
```

After pushing, open **Langfuse → Prompts**, edit the content, and set its label to `production`. Django picks up the new version within 60 seconds (cache TTL).

---

## 11. Known Issues and Fixes

### Issue 1 — `start_observation()` does not accept `user_id`/`session_id` (SDK v4)

**Symptom**: All Langfuse traces disappeared after adding user/session attribution. No errors logged; `start_trace` returned `None` silently.

**Root cause**: Langfuse SDK v3 accepted `user_id` and `session_id` directly in `start_observation()`. SDK v4 (installed: `4.0.1`) removed them. Passing unknown kwargs caused a silent `TypeError` returning `None`.

**Fix**: Remove `user_id`/`session_id` from `start_observation()` and set them as OTel span attributes after the span is created:

```python
from langfuse._client.attributes import TRACE_USER_ID, TRACE_SESSION_ID

otel_span = getattr(span, "_otel_span", None)
if otel_span is not None:
    if user_id:
        otel_span.set_attribute(TRACE_USER_ID, str(user_id))   # "user.id"
    if session_id:
        otel_span.set_attribute(TRACE_SESSION_ID, session_id)  # "session.id"
```

---

### Issue 2 — Bulk extraction job had no Langfuse traces

**Symptom**: Individual invoice uploads created traces; bulk extraction jobs produced no traces.

**Root cause**: `run_bulk_job_task` called `BulkExtractionService.run_job()` with no Langfuse context.

**Fix**: Added root trace in `run_bulk_job_task`; propagated trace to `adapter.lf_trace` and as `lf_parent` to `_process_item()`; added `self.lf_trace = None` to `BaseBulkSourceAdapter.__init__`; wrapped all network-bound methods in Google Drive and OneDrive adapters.

---

### Issue 3 — Prompt names mismatched in Langfuse

**Symptom**: Warning `Prompt 'extraction-invoice_system-label:production' not found`. Prompts existed but with different names.

**Root cause**: `slug_to_langfuse_name()` replaces all dots with dashes. Prompts had previously been pushed with a different naming scheme.

**Fix**: Run `python manage.py push_prompts_to_langfuse --purge` to delete all existing prompts and reseed with correct names.

---

### Issue 4 — Scores orphaned / blank session_id and user_id (SDK v4 OTel trace_id mismatch)

**Symptom**: Scores appeared in Langfuse but showed blank `session_id`, blank `user_id`, and were not linked to their parent trace. The "observation" column was empty for case_pipeline and reconciliation_run scores.

**Root cause**: In SDK v4, `start_observation()` creates an OTel span with an auto-generated 128-bit trace_id. Our application-level trace_id strings (`"case-42"`, `"recon-task-abc"`) are set as `TraceContext` correlation IDs but are **not** the same as the OTel trace_id Langfuse uses internally. `score_trace(trace_id, ...)` with our application string could not match it to any existing trace — scores floated free.

**Fix**:

1. Added `_extract_otel_trace_id(span)` helper that reads the real OTel trace_id from the span object:

```python
def _extract_otel_trace_id(span) -> Optional[str]:
    otel_span = getattr(span, "_otel_span", None)
    if otel_span is not None:
        sc = otel_span.get_span_context()
        if sc is not None:
            tid = getattr(sc, "trace_id", 0)
            if tid:
                return format(tid, "032x")  # 128-bit int → 32-char hex
    return None
```

2. Updated `score_trace()` and `score_trace_safe()` to accept an optional `span=` parameter:

```python
def score_trace(trace_id, name, value, *, comment="", span=None):
    real_tid = _extract_otel_trace_id(span) if span else None
    lf.create_score(trace_id=real_tid or trace_id, name=name, value=value, ...)
```

3. Updated 35 `score_trace_safe()` call sites across 5 files to pass `span=`:

| File | Calls updated | `span=` value |
|---|---|---|
| `apps/cases/tasks.py` | 6 | `_lf_trace` |
| `apps/cases/orchestrators/case_orchestrator.py` | 8 | `self._lf_trace` |
| `apps/reconciliation/tasks.py` | 3 | `_lf_task_trace` |
| `apps/reconciliation/services/runner_service.py` | 10 | `lf_trace` |
| `apps/cases/services/review_workflow_service.py` | 8 | `_lf_trace` or `_lf_span` |

**Pattern for new code**: Always pass `span=` when calling `score_trace_safe()`:

```python
score_trace_safe(
    _trace_id,              # application-level ID (used as fallback only)
    RECON_RECONCILIATION_MATCH,
    1.0,
    comment="context",
    span=_lf_trace,         # the Langfuse span from start_trace/start_span
)
```

---

## 12. Debugging Guide

| Symptom | Check |
|---|---|
| No traces appearing | Verify `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` are set. Check Django logs for `Langfuse disabled` or `start_trace failed`. |
| Traces appear but Users/Sessions tabs are empty | Confirm SDK version is 4.x. The `_otel_span.set_attribute()` approach requires SDK v4. |
| Prompt 404 warning in logs | Run `push_prompts_to_langfuse`. If names are wrong, run with `--purge`. |
| Old prompt content being served | Langfuse client caches prompts for 60 seconds. Wait or restart to force refresh. |
| `start_trace` returns None | Set `LANGFUSE_LOG_LEVEL=debug` and check for exceptions in client init. Ensure host URL has no trailing slash. |
| Scores appear but not linked to a trace | Confirm the pipeline that emits the score also calls `start_trace` before scoring. Pass `span=` to `score_trace_safe()` (see Issue 4). |
| Scores show blank session_id / user_id | The `score_trace()` call is using the application-level trace_id string instead of the real OTel trace_id. Pass `span=` to `score_trace_safe()`. See Issue 4. |
| ERP resolution spans missing | Verify `lf_parent_span` is passed from the pipeline stage to the resolver. See §7.7 threading table. |

---

## 13. Deferred Gaps

| Gap | Status | Notes |
|---|---|---|
| `APCase.langfuse_trace_id` field | Deferred | Child `APCaseStage` already has `trace_id`; parent-level linkage is lower priority |
| `ExtractionRun.langfuse_trace_id` field | Deferred | `ExtractionResult.langfuse_trace_id` covers the primary use case |
| `ReviewAssignment.langfuse_trace_id` field | Deferred | Reviews use `f"review-{assignment.pk}"` convention which is deterministic |
| Test file score strings | Intentional | Test files use raw strings for readability |
| `score_latency()` broad adoption | Deferred | Helper exists and used in ERP; remaining pipeline stages use per-domain latency scoring |
| Decision-quality signals at scale | Deferred | Constants defined (`DECISION_CONFIDENCE_ALIGNMENT`, `STALE_DATA_ACCEPTED`, etc.) but emission requires outcome comparison logic not yet built |
| `GRNLookupService` ERP parent span | Deferred | `lf_parent_span` not yet threaded from `ThreeWayMatchService` into `GRNLookupService.lookup()` |
| SUPERVISOR_ pipeline instrumentation | Pending | `TRACE_SUPERVISOR_PIPELINE` constant and 5 score constants defined; tracing call sites not yet implemented |
| SYSTEM_ agent tracing | Pending | `TRACE_SYSTEM_*` constants defined; tracing call sites for system agents not yet fully implemented |

---

## 14. Upgrade History

### April 2026 — Standardized Score Taxonomy and Cross-Flow Correlation

**New files**:

| File | Purpose |
|---|---|
| `apps/core/evaluation_constants.py` | Centralized score name taxonomy — 165 constants |
| `apps/core/observability_helpers.py` | Cross-flow helpers: `derive_session_id()`, `build_observability_context()`, per-flow metadata builders, sanitization, latency scoring |

**Files modified** (raw string score names → constants, standardized session IDs):

| File | Changes |
|---|---|
| `apps/erp_integration/services/langfuse_helpers.py` | 24 score calls → constants; latency threshold → `LATENCY_THRESHOLD_ERP_MS` |
| `apps/reconciliation/tasks.py` | Trace name → `TRACE_RECONCILIATION_PIPELINE`; session_id → `derive_session_id()`; persist `langfuse_trace_id` on `ReconciliationRun` |
| `apps/reconciliation/services/runner_service.py` | 25 score calls → constants |
| `apps/agents/services/orchestrator.py` | Trace name → `TRACE_AGENT_PIPELINE`; 5 pipeline scores → constants |
| `apps/agents/services/base_agent.py` | 6 score calls → constants |
| `apps/agents/tasks.py` | Trace name → `TRACE_AGENT_PIPELINE + "_task"` |
| `apps/agents/services/guardrails_service.py` | 4 score calls → `RBAC_GUARDRAIL`, `RBAC_DATA_SCOPE` |
| `apps/cases/tasks.py` | 2 tasks: trace names → `TRACE_CASE_PIPELINE`; scores → `CASE_PROCESSING_SUCCESS`, `CASE_REPROCESSED` |
| `apps/cases/orchestrators/case_orchestrator.py` | 14 score calls → constants |
| `apps/extraction/tasks.py` | Trace name → `TRACE_EXTRACTION_PIPELINE`; 20 score calls → constants; persist `langfuse_trace_id` on `ExtractionResult` |
| `apps/extraction/bulk_tasks.py` | 1 score call → `EXTRACTION_BULK_JOB_SUCCESS_RATE` |
| `apps/extraction/services/approval_service.py` | 5 score calls → constants |
| `apps/posting_core/services/posting_pipeline.py` | 2 score calls → constants; persist `langfuse_trace_id` on `PostingRun` |
| `apps/cases/services/review_workflow_service.py` | 9 score calls → constants |
| `apps/copilot/services/copilot_service.py` | 1 score call → `COPILOT_SESSION_LENGTH` |

**DB model changes**:

| Model | Field | Migration |
|---|---|---|
| `ReconciliationRun` | `langfuse_trace_id` (CharField, max_length=64, db_index=True) | `reconciliation/0006_add_langfuse_trace_id` |
| `PostingRun` | `langfuse_trace_id` (CharField, max_length=64, db_index=True) | `posting_core/0004_add_langfuse_trace_id` |
| `ExtractionResult` | `langfuse_trace_id` (CharField, max_length=255, blank=True) | `extraction/0011_add_langfuse_trace_id` |

**ERP observability additions** (as part of same upgrade):
- All 7 resolver types emit standardized ERP scores.
- 3-stage span hierarchy: `erp_cache_lookup` → `erp_live_lookup` → `erp_db_fallback`.
- Decision-quality signals: `STALE_DATA_ACCEPTED`, `FALLBACK_USED_BUT_SUCCESSFUL`.

**OTel trace_id fix** (Issue 4 resolution):
- Added `_extract_otel_trace_id(span)` to `langfuse_client.py`.
- Updated `score_trace()` / `score_trace_safe()` to accept `span=` parameter.
- Updated 35 call sites across 5 files.
