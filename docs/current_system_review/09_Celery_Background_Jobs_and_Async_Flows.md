# 09 — Celery, Background Jobs, and Async Flows

**Generated**: 2026-04-09 | **Method**: Code-first inspection  
**Evidence files**: `config/celery.py`, `extraction/tasks.py`, `reconciliation/tasks.py`, `agents/tasks.py`, `cases/tasks.py`, `core_eval/tasks.py` (inferred from beat schedule)

---

## 1. Celery Configuration

```python
# config/celery.py + config/settings.py
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = "django-db"   # django-celery-results
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Asia/Kolkata"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ALWAYS_EAGER = False  # True in dev/test via env var
CELERY_TASK_EAGER_PROPAGATES = CELERY_TASK_ALWAYS_EAGER

# Beat schedule (config/celery.py)
beat_schedule = {
    "process-approved-learning-actions": {
        "task": "core_eval.process_approved_learning_actions",
        "schedule": crontab(minute="*/30"),   # every 30 minutes
    }
}
```

**Result backend**: `django-db` — all task results stored in `django_celery_results_taskresult` table.  
**Queues**: Single `default` queue — no queue specialization for different task types.  
**Dev mode**: `CELERY_TASK_ALWAYS_EAGER=True` runs tasks synchronously (no Redis needed).

---

## 2. Task Inventory

### `extraction.tasks.run_extraction_task`

```
Trigger:  DocumentUpload created (web upload or bulk upload API)
Retries:  Not explicitly documented — uses Django task default
Acks:     Default (ack on receipt)

Flow:
  1. Create ExtractionRun record (extraction_core)
  2. Reserve credit for user
  3. Run 11-stage extraction pipeline (ExtractionAdapter)
  4. On OCR success: consume credit; on failure: refund credit
  5. Langfuse trace: TRACE_EXTRACTION_PIPELINE
  6. Score: EXTRACTION_CONFIDENCE, EXTRACTION_IS_VALID, EXTRACTION_SUCCESS, etc.
  7. On completion (if auto-approved): trigger process_case_task
```

### `reconciliation.tasks.run_reconciliation_task`

```
Trigger:  User clicks "Reconcile" in UI, or triggered programmatically
Retries:  max_retries=5, default_retry_delay=60s
Acks:     Default

Signature: (tenant_id, invoice_ids, config_id, triggered_by_id)
  - invoice_ids=None → process ALL READY_FOR_RECON invoices

Flow:
  1. Resolve config, user, invoices
  2. Open Langfuse root trace (TRACE_RECONCILIATION_PIPELINE)
  3. ReconciliationRunnerService.run() — per-invoice matching
  4. Persist Langfuse trace_id on ReconciliationRun
  5. Link ReconciliationResult records back to APCase records
  6. Dispatch run_agent_pipeline_task for each non-MATCHED result
  7. Score: RECON_FINAL_SUCCESS, RECON_ROUTED_TO_AGENTS
```

### `reconciliation.tasks.reconcile_single_invoice_task`

```
Trigger:  Convenience wrapper — wraps run_reconciliation_task with single invoice
Retries:  Inherits from run_reconciliation_task
```

### `agents.tasks.run_agent_pipeline_task`

```
Trigger:  Auto-dispatched from run_reconciliation_task for non-MATCHED results
          OR manual trigger via API
Retries:  max_retries=1, default_retry_delay=30s

Signature: (tenant_id, reconciliation_result_id, actor_user_id)

Flow:
  1. Load ReconciliationResult
  2. Resolve actor (user or system-agent fallback)
  3. Open Langfuse task-level trace (TRACE_AGENT_PIPELINE)
  4. AgentOrchestrator.execute(result, request_user, tenant)
  5. Close Langfuse trace with outcome metadata
  6. Return: agents_executed, final_recommendation, skipped, error
```

### `cases.tasks.process_case_task`

```
Trigger:  After extraction approval (auto or human) → case created → this task dispatched
Retries:  max_retries=3, default_retry_delay=30s, acks_late=True

Signature: (tenant_id, case_id)

Flow:
  1. Load APCase (with tenant filter)
  2. Run SystemCaseIntakeAgent (governance record)
  3. Open Langfuse trace (TRACE_CASE_PIPELINE)
  4. CaseOrchestrator.run(tenant, lf_trace, lf_trace_id)
  5. On completion: score CASE_PROCESSING_SUCCESS=1.0
  6. On failure: score CASE_PROCESSING_SUCCESS=0.0 → retry
```

### `cases.tasks.reprocess_case_from_stage_task`

```
Trigger:  Manual reprocess action from UI (e.g. rerun from RECONCILIATION stage)
Retries:  max_retries=2, default_retry_delay=10s, acks_late=True

Signature: (tenant_id, case_id, stage)

Flow:
  1. Load APCase
  2. CaseOrchestrator.run_from(stage, ...)
  3. Score CASE_PROCESSING_SUCCESS + CASE_REPROCESSED
```

### `core_eval.tasks.process_approved_learning_actions` (Beat)

```
Trigger:  Celery Beat, every 30 minutes
Purpose:  Process approved LearningAction records from core_eval framework
          → apply learned improvements to configuration or prompts
```

### `procurement.tasks.run_analysis_task`

```
Trigger:  POST /api/v1/procurement/requests/{id}/runs/
Retries:  max_retries=2, default_retry_delay=30s

Signature: (tenant_id, run_id)

Flow:
  1. Tenant-scoped AnalysisRun lookup via request__tenant
  2. Request status moved to PENDING_RFQ while analysis executes
  3. Dispatch by run_type:
       - RECOMMENDATION → RecommendationService.run_recommendation(...)
       - BENCHMARK      → apps.benchmarking.services.procurement_cost_service.ProcurementCostService.run_cost_analysis(...)
       - VALIDATION     → ValidationOrchestratorService.run_validation(...)
  4. Langfuse root trace: procurement_analysis_task
  5. Eval sync via ProcurementEvalAdapter
```

Important runtime note: BENCHMARK currently resolves to a compatibility bridge service in the `benchmarking` app rather than a full corridor-analysis engine.

### `procurement.tasks.run_validation_task`

```
Trigger:  POST /api/v1/procurement/requests/{id}/validate/
Retries:  max_retries=2, default_retry_delay=30s

Flow:
  1. Tenant-scoped AnalysisRun lookup
  2. ValidationOrchestratorService.run_validation(..., agent_enabled=...)
  3. Request status updated from ValidationOverallStatus:
       PASS / PASS_WITH_WARNINGS / REVIEW_REQUIRED -> PENDING_RFQ
       FAIL -> FAILED
  4. Langfuse root trace: procurement_validation_task
```

### Procurement Prefill and Market-Intelligence Tasks

```
run_request_prefill_task(tenant_id, request_id)
  -> RequestDocumentPrefillService.run_prefill()

run_quotation_prefill_task(tenant_id, quotation_id)
  -> QuotationDocumentPrefillService.run_prefill()

generate_market_intelligence_task(tenant_id, request_id)
  -> MarketIntelligenceService.generate_auto()
  -> Langfuse root trace: procurement_market_intelligence_task
```

---

## 3. Task Chain / Workflow

```
[User uploads invoice]
     ↓
run_extraction_task (Celery)
     ↓ (on extraction approval)
process_case_task (Celery, acks_late=True)
     ↓ (CaseOrchestrator → triggers recon)
run_reconciliation_task (Celery, retries=5)
     ↓ (for each non-MATCHED result)
run_agent_pipeline_task (Celery, retries=1)
     [end of automatic chain]

[User creates procurement request / quotation]
  ↓
run_request_prefill_task OR run_quotation_prefill_task (optional)
  ↓
run_analysis_task (recommendation / benchmark) OR run_validation_task
  ↓
generate_market_intelligence_task (manual or auto for HVAC requests)
```

**Note**: The chain is triggered via `dispatch_task()` helper (in `core/utils.py`) which wraps `.delay()` with tenant propagation.

---

## 4. Retry and Failure Handling

| Task | max_retries | delay | acks_late | Failure behavior |
|------|------------|-------|----------|----------------|
| run_extraction_task | Default | Default | No | Credit refunded; ExtractionRun marked FAILED |
| run_reconciliation_task | 5 | 60s | No | `safe_retry(self, exc)` — exponential backoff |
| run_agent_pipeline_task | 1 | 30s | No | `safe_retry(self, exc)`; marks orchestration run FAILED |
| process_case_task | 3 | 30s | Yes | `safe_retry(self, exc)`; Langfuse scores 0.0 |
| reprocess_case_from_stage_task | 2 | 10s | Yes | `safe_retry(self, exc)` |

**`safe_retry(self, exc)`**: Utility in `core/utils.py` that calls `self.retry(exc=exc)` with exponential backoff. Prevents runaway retries.

**`acks_late=True`** on case tasks: task is only acknowledged after completion, preventing loss of case processing work on worker crash.

---

## 5. Actor Context Propagation

All tasks accept `actor_user_id` (or `triggered_by_id`) in their signatures:
- Resolved to a `User` object at task start
- Falls back to `SYSTEM_AGENT` user if `actor_user_id` is None
- Propagated into `AgentContext.actor_user_id`
- Captured in `AgentRun.actor_user_id` and `AuditEvent.performed_by`
- Langfuse trace metadata includes `user_id=actor_user_id`

---

## 6. Idempotency and Duplicate Risk

| Risk | Mitigation | Gap |
|------|-----------|-----|
| Duplicate extraction for same file | `file_hash` deduplication on `DocumentUpload` | Gap: race condition between upload and hash check |
| Duplicate reconciliation run | `ReconciliationRun` record created per task; results linked per invoice | Gap: concurrent reconciliation of same invoice not prevented |
| Duplicate agent pipeline for same result | Not explicitly prevented | Gap: two concurrent `run_agent_pipeline_task` for same `reconciliation_result_id` could create duplicate `AgentOrchestrationRun` |
| `acks_late` prevents loss | Task re-executed on worker crash if not yet acked | Risk: idempotency of `process_case_task` depends on `CaseOrchestrator` being idempotent |

---

## 7. Operational Risks

| Risk | Severity | Notes |
|------|---------|-------|
| No Celery Beat for scheduled reconciliation | Medium | Reconciliation only on-demand; no SLA-driven auto-processing |
| Single `default` queue for all tasks | Medium | Heavy agent tasks could starve extraction tasks; no queue priority |
| Redis unauthenticated in dev config | HIGH in prod | Must override `CELERY_BROKER_URL` in all non-dev environments |
| No Celery worker health monitoring | Medium | No Flower or monitoring tool confirmed |
| No email notification on task failure | Medium | Admin must check Celery logs or result backend table |
| LLM timeout 120s per call; multi-tool agents | Medium | 5-tool ReAct loop could take 10+ minutes without an overall agent timeout |
| No scheduled ERP data sync | Medium | ERP mirror tables only refreshed on-demand or via API connector on cache miss |
| Beat worker not documented as required | Low | Celery Beat must run separately; README doesn't mention it explicitly |
