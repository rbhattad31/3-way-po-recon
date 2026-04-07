# Observability Upgrade Summary

**Date**: 2026-04-01
**Scope**: Standardized, evaluation-ready, cross-flow Langfuse/tracing architecture

---

## 1. What Changed

### New Files Created

| File | Purpose |
|---|---|
| `apps/core/evaluation_constants.py` | Centralized score name taxonomy -- ~145 constants covering all domains |
| `apps/core/observability_helpers.py` | Cross-flow correlation helpers: `derive_session_id()`, `build_observability_context()`, `latency_ok()`, per-flow eval metadata builders, sanitization |

### Files Modified (Score String Standardization)

All raw string score names replaced with constants from `evaluation_constants.py`:

| File | Changes |
|---|---|
| `apps/erp_integration/services/langfuse_helpers.py` | 24 score calls -> constants; latency threshold -> `LATENCY_THRESHOLD_ERP_MS` |
| `apps/reconciliation/tasks.py` | Trace name -> `TRACE_RECONCILIATION_PIPELINE`; session_id -> `derive_session_id()`; 3 score calls -> constants; persist `langfuse_trace_id` on `ReconciliationRun` |
| `apps/reconciliation/services/runner_service.py` | 25 score calls -> constants (incl. PO/GRN freshness, classification, exception counts) |
| `apps/agents/services/orchestrator.py` | Trace name -> `TRACE_AGENT_PIPELINE`; session_id -> `derive_session_id()`; 5 pipeline scores -> constants |
| `apps/agents/services/base_agent.py` | 6 score calls -> constants (`AGENT_CONFIDENCE`, `AGENT_RECOMMENDATION_PRESENT`, `AGENT_TOOL_SUCCESS_RATE`, `TOOL_CALL_SUCCESS`) |
| `apps/agents/tasks.py` | Trace name -> `TRACE_AGENT_PIPELINE + "_task"`; session_id -> `derive_session_id()` |
| `apps/agents/services/guardrails_service.py` | 4 score calls -> `RBAC_GUARDRAIL`, `RBAC_DATA_SCOPE` |
| `apps/cases/tasks.py` | 2 tasks: trace names -> `TRACE_CASE_PIPELINE`; session_id -> `derive_session_id()`; scores -> `CASE_PROCESSING_SUCCESS`, `CASE_REPROCESSED` |
| `apps/cases/orchestrators/case_orchestrator.py` | 14 score calls -> constants (all stage scores + trace-level scores) |
| `apps/extraction/tasks.py` | Trace name -> `TRACE_EXTRACTION_PIPELINE`; 20 score calls -> constants; persist `langfuse_trace_id` on `ExtractionResult` |
| `apps/extraction/bulk_tasks.py` | 1 score call -> `EXTRACTION_BULK_JOB_SUCCESS_RATE` |
| `apps/extraction/services/approval_service.py` | 5 score calls -> constants |
| `apps/posting_core/services/posting_pipeline.py` | 2 score calls -> `POSTING_FINAL_CONFIDENCE`, `POSTING_FINAL_REQUIRES_REVIEW`; persist `langfuse_trace_id` on `PostingRun` |
| `apps/reviews/services.py` | 9 score calls -> constants |
| `apps/copilot/services/copilot_service.py` | 1 score call -> `COPILOT_SESSION_LENGTH` |

### DB Model Changes

| Model | Field Added | Migration |
|---|---|---|
| `ReconciliationRun` | `langfuse_trace_id` (CharField, max_length=64, db_index=True) | `0006_add_langfuse_trace_id` |
| `PostingRun` | `langfuse_trace_id` (CharField, max_length=64, db_index=True) | `0004_add_langfuse_trace_id` |
| `ExtractionResult` | `langfuse_trace_id` (CharField, max_length=64, db_index=True) | `0011_add_langfuse_trace_id` |

### Documentation Updated

| File | What was added |
|---|---|
| `docs/LANGFUSE_INTEGRATION.md` | New sections: Standardized Score Taxonomy, Cross-Flow Correlation, DB Model Trace ID Fields |

---

## 2. Score Taxonomy

### Constants by Domain

| Domain | Count | Examples |
|---|---|---|
| Extraction | 20 | `EXTRACTION_SUCCESS`, `EXTRACTION_CONFIDENCE`, `EXTRACTION_OCR_CHAR_COUNT`, `EXTRACTION_APPROVAL_DECISION` |
| Reconciliation | 23 | `RECON_FINAL_SUCCESS`, `RECON_PO_FOUND`, `RECON_HEADER_MATCH_RATIO`, `RECON_BLOCKING_EXCEPTION_COUNT` |
| Agents | 11 | `AGENT_CONFIDENCE`, `AGENT_PIPELINE_FINAL_CONFIDENCE`, `TOOL_CALL_SUCCESS` |
| Case | 15 | `CASE_CLOSED`, `CASE_MATCH_STATUS`, `CASE_NON_PO_RISK_SCORE` |
| Review | 8 | `REVIEW_APPROVED`, `REVIEW_DECISION`, `REVIEW_FIELDS_CORRECTED_COUNT` |
| Posting | 16 | `POSTING_FINAL_CONFIDENCE`, `POSTING_VENDOR_MAPPING_SUCCESS` |
| ERP | 24 | `ERP_RESOLUTION_SUCCESS`, `ERP_CACHE_HIT`, `ERP_SUBMISSION_SUCCESS` |
| Cross-cutting | 8 | `RBAC_GUARDRAIL`, `COPILOT_SESSION_LENGTH`, `LATENCY_OK` |
| Decision-quality | 6 | `DECISION_CONFIDENCE_ALIGNMENT`, `STALE_DATA_ACCEPTED` |

### Root Trace Names

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

### Latency Thresholds

| Constant | Value | Use Case |
|---|---|---|
| `LATENCY_THRESHOLD_OCR_MS` | 30000 | Document Intelligence OCR |
| `LATENCY_THRESHOLD_LLM_MS` | 20000 | LLM generation call |
| `LATENCY_THRESHOLD_ERP_MS` | 5000 | ERP API call |
| `LATENCY_THRESHOLD_DB_MS` | 2000 | Database query / fallback |
| `LATENCY_THRESHOLD_RECON_STAGE_MS` | 5000 | Reconciliation sub-stage |
| `LATENCY_THRESHOLD_POSTING_STAGE_MS` | 5000 | Posting pipeline stage |
| `LATENCY_THRESHOLD_TOOL_CALL_MS` | 10000 | Agent tool call |

---

## 3. Cross-Flow Correlation Model

### Session ID Convention

All pipelines now use `derive_session_id()` from `observability_helpers.py`:

| Priority | Pattern | Source |
|---|---|---|
| 1 | `case-{case_number}` | Case-anchored flows (AP Case created at upload time) |
| 2 | `invoice-{invoice_id}` | Invoice-centric flows (fallback when no case_number) |
| 3 | `upload-{upload_id}` | Pre-invoice extraction (rare fallback) |
| 4 | `case-{case_id}` | Case-only flows (numeric fallback) |
| 5 | `None` | No context available |

### Metadata Builder

`build_observability_context()` produces a standardized metadata dict with 18
cross-linking fields: `invoice_id`, `case_id`, `reconciliation_result_id`,
`posting_run_id`, `extraction_run_id`, `upload_id`, `actor_user_id`, `trigger`,
`source`, `vendor_id`, `po_number`, `business_unit`, and arbitrary extras.

---

## 4. ERP Observability Coverage

- All 7 resolver types (vendor, item, tax, cost_center, po, grn, duplicate_invoice) emit standardized ERP scores.
- 3-stage span hierarchy: `erp_cache_lookup` -> `erp_live_lookup` -> `erp_db_fallback`.
- Decision-quality signals added: `STALE_DATA_ACCEPTED`, `FALLBACK_USED_BUT_SUCCESSFUL`.
- All score names use constants from `evaluation_constants.py`.
- Latency threshold uses centralized `LATENCY_THRESHOLD_ERP_MS`.

---

## 5. Gaps Identified (Deferred)

| Gap | Status | Notes |
|---|---|---|
| `APCase.langfuse_trace_id` field | Deferred | Child `APCaseStage` already has `trace_id`; parent linkage is lower priority |
| `ExtractionRun.langfuse_trace_id` field | Deferred | `ExtractionResult.langfuse_trace_id` covers the primary use case |
| `ReviewAssignment.langfuse_trace_id` field | Deferred | Reviews use `f"review-{assignment.pk}"` convention which is deterministic |
| Test file score strings | Deferred | Test files intentionally use raw strings for readability |
| Posting observation-level scores | Deferred | Constants exist (`POSTING_VENDOR_MAPPING_SUCCESS`, etc.) but the pipeline stages don't emit them yet -- requires mapping engine instrumentation |
| `score_latency()` adoption | Deferred | Helper exists but not yet called from pipeline stages; current stages use per-domain latency scoring |
| Decision-quality signals at scale | Deferred | Constants defined (`DECISION_CONFIDENCE_ALIGNMENT`, etc.) but emission requires outcome comparison logic not yet built |

---

## 6. Migration Checklist

```bash
# Apply new DB migrations
python manage.py migrate reconciliation
python manage.py migrate extraction
python manage.py migrate posting_core
```

No breaking changes. All modifications are additive:
- New model fields have `blank=True, default=""` -- no data backfill needed.
- Score constants produce the same string values as before -- Langfuse evaluations continue to work.
- `observability_helpers.py` is a new import; existing code is unaffected if not imported.
