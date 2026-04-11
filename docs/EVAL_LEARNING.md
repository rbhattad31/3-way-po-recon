# Evaluation & Learning Architecture

> Full reference: `apps/core_eval/` (models, services, management commands, tests)

## 1. Overview

The Evaluation & Learning layer is a **domain-agnostic framework** that sits alongside the production pipelines (extraction, reconciliation, posting, agents) without modifying them. Its purpose:

1. **Track pipeline quality** -- capture metrics, field-level outcomes, and confidence scores for every pipeline run.
2. **Collect learning signals** -- record human corrections, approval outcomes, validation failures, and auto-approve events as structured signals.
3. **Detect patterns** -- a deterministic rule engine scans accumulated signals and proposes corrective actions.
4. **Surface actions for human review** -- proposed actions (prompt edits, threshold adjustments, normalization rules) are never auto-applied; they require explicit human approval.

The layer is fail-silent: errors in eval/learning code never propagate to the calling pipeline.

---

## 2. Architecture Diagram

```
Production Pipelines          Eval / Learning Layer
=====================         ===============================

 Extraction Task               EvalRun
   |                             +-- EvalMetric (N)
   |-- ExtractionEvalAdapter --> +-- EvalFieldOutcome (N)
   |                             +-- LearningSignal (N)
   v
 Approval Service                    |
   |                                 v
   |-- ExtractionEvalAdapter --> LearningSignal (additional)
   |
   v
 Reconciliation Runner               |
   |                                 v
   |-- ReconEvalAdapter -------> EvalRun + Metrics + Signals
   |
   v
 Review Service                      |
   |                                 v
   |-- ReconEvalAdapter -------> FieldOutcomes + Signals
   |                                 |
   v                                 v
 [Future: Posting / Agent       LearningEngine (deterministic)
   adapters]                         |
                                     v
                                LearningAction (PROPOSED)
                                     |
                                     v
                                Human Review (approve/reject)
                                     |
                                     v
                                LearningAction (APPLIED / REJECTED)
```

Key design constraint: **the arrow from Production to Eval is one-way**. The eval layer reads production data and writes to its own tables. It never writes back to production models or alters pipeline behavior.

---

## 3. Data Model

All models inherit `TimestampMixin` (not `BaseModel`) and include a `tenant_id` field for multi-tenant isolation.

### 3.1 EvalRun

One evaluation pass against any entity (e.g. one `ExtractionResult`, one `ReconciliationResult`).

| Field | Type | Purpose |
|---|---|---|
| `app_module` | CharField(120) | Originating module (e.g. `"extraction"`, `"reconciliation"`) |
| `entity_type` | CharField(120) | Model name (e.g. `"ExtractionResult"`) |
| `entity_id` | CharField(255) | PK of evaluated entity |
| `run_key` | CharField(255) | Distinguishes retries / versions (e.g. `"extraction-42"`) |
| `prompt_hash` | CharField(64) | SHA-256 of prompt template used |
| `prompt_slug` | CharField(200) | PromptTemplate slug for lineage |
| `status` | CharField(20) | `CREATED` / `PENDING` / `RUNNING` / `COMPLETED` / `FAILED` |
| `trace_id` | CharField(255) | Distributed trace correlation (Langfuse / OTel) |
| `triggered_by` | FK(User) | Who triggered the pipeline run |
| `config_json` | JSONField | Run-level configuration snapshot |
| `input_snapshot_json` | JSONField | Inputs evaluated (invoice_id, confidence, etc.) |
| `result_json` | JSONField | Aggregated results |
| `error_json` | JSONField | Error details on failure |
| `started_at` / `completed_at` | DateTimeField | Timing |
| `duration_ms` | PositiveIntegerField | Duration in milliseconds |

**Indexes**: `app_module`, `(entity_type, entity_id)`, `prompt_hash`, `created_at`, `(app_module, entity_type, entity_id, run_key)`, `tenant_id`.

### 3.2 EvalMetric

Named numeric, text, or JSON metric attached to an `EvalRun`.

| Field | Type | Purpose |
|---|---|---|
| `eval_run` | FK(EvalRun) | Parent run (nullable for standalone metrics) |
| `metric_name` | CharField(200) | e.g. `"extraction_confidence"`, `"decision_code_count"` |
| `metric_value` | FloatField | Numeric value (nullable) |
| `string_value` | TextField | Text value |
| `json_value` | JSONField | Structured value (e.g. list of decision codes) |
| `unit` | CharField(50) | e.g. `"ratio"`, `"count"`, `"seconds"` |
| `dimension_json` | JSONField | Arbitrary slicing dimensions |
| `metadata_json` | JSONField | Extra context |

**Validation**: At most one of `metric_value`, `string_value`, `json_value` is populated per record.

### 3.3 EvalFieldOutcome

Per-field predicted-vs-ground-truth outcome for an `EvalRun`.

| Field | Type | Purpose |
|---|---|---|
| `eval_run` | FK(EvalRun) | |
| `field_name` | CharField(200) | e.g. `"invoice_number"`, `"total_amount"` |
| `status` | CharField(20) | `CORRECT` / `INCORRECT` / `MISSING` / `EXTRA` / `SKIPPED` |
| `predicted_value` | TextField | Final LLM-extracted value (pipeline output persisted to Invoice) |
| `ground_truth_value` | TextField | Empty at extraction; populated on human approval (corrected value or confirmed prediction) |
| `confidence` | FloatField | Per-field confidence 0.0--1.0 (LLM confidence when source is LLM) |
| `detail_json` | JSONField | Source provenance: `source` (llm/deterministic), `deterministic_value`, `deterministic_confidence`, `category` |

**Lifecycle**:

1. **At extraction time**: `predicted_value` = LLM value from `raw_response` (deterministic fallback only when LLM has no value). `ground_truth_value` = empty. `status` = CORRECT (has predicted) or MISSING (no predicted).
2. **On human correction**: `ground_truth_value` = corrected value, `status` = INCORRECT (via `_update_field_outcomes_from_corrections`).
3. **On approval confirmation**: Non-corrected fields get `ground_truth_value` = `predicted_value`, `status` = CORRECT (via `_confirm_ground_truth_on_approval`).

### 3.4 LearningSignal

An atomic observation from production that may feed pattern detection later.

| Field | Type | Purpose |
|---|---|---|
| `app_module` | CharField(120) | Originating module |
| `signal_type` | CharField(120) | Category (see Signal Types below) |
| `entity_type` | CharField(120) | e.g. `"Invoice"` |
| `entity_id` | CharField(255) | PK of related entity |
| `aggregation_key` | CharField(255) | Grouping key for pattern detection |
| `confidence` | FloatField | Signal strength 0.0--1.0 |
| `actor` | FK(User) | Who produced the signal |
| `field_name` | CharField(200) | For field-level signals |
| `old_value` / `new_value` | TextField | Before / after (for corrections) |
| `payload_json` | JSONField | Full signal payload |
| `eval_run` | FK(EvalRun) | Optional link to parent eval run |

**Signal types emitted today**:

| Signal Type | Source | When |
|---|---|---|
| `field_correction` | `ExtractionEvalAdapter.sync_for_approval()` | Human corrects a field during approval |
| `approval_outcome` | `ExtractionEvalAdapter.sync_for_approval()` | Human approves or rejects an extraction |
| `auto_approve_outcome` | `ExtractionEvalAdapter.sync_for_approval()` | System auto-approves an extraction |
| `validation_failure` | `ExtractionEvalAdapter.sync_for_extraction_result()` | Extraction validation fails |
| `review_override` | `ExtractionEvalAdapter.sync_for_approval()` | Human approves with corrections (non-touchless) |
| `prompt_review_candidate` | `ExtractionEvalAdapter.sync_for_extraction_result()` | Decision codes suggest prompt issues |

### 3.5 LearningAction

A proposed corrective action generated by the `LearningEngine`.

| Field | Type | Purpose |
|---|---|---|
| `action_type` | CharField(120) | Category (see Action Types below) |
| `status` | CharField(20) | `PROPOSED` / `APPROVED` / `APPLIED` / `REJECTED` / `FAILED` |
| `app_module` | CharField(120) | Target module |
| `target_description` | TextField | Human-readable target + dedup tag |
| `rationale` | TextField | Why the engine proposed this |
| `input_signals_json` | JSONField | Summary of triggering signals |
| `action_payload_json` | JSONField | The action details (field, examples, fix) |
| `result_json` | JSONField | Outcome after application |
| `proposed_by` / `approved_by` | FK(User) | Provenance |
| `applied_at` | DateTimeField | When applied |

**Action types proposed today**:

| Action Type | Trigger Rule | Meaning |
|---|---|---|
| `field_normalization_candidate` | Field Correction Hotspot | A field is corrected >= 20 times; consider adding a normalization rule |
| `prompt_review` | Prompt Weakness | A prompt has >= 30% correction rate across runs; review the prompt |
| `threshold_tune` | Auto-Approve Risk | Auto-approved items are frequently corrected; raise threshold |
| `validation_rule_candidate` | Validation Failure Cluster | Same validation error repeats >= 10 times; add a rule |
| `vendor_rule_candidate` | Vendor-Specific Issue | Corrections cluster around a vendor; add vendor-specific mapping |

---

## 4. Service Layer

### 4.1 Low-level CRUD Services

These services provide the persistence API. All methods are stateless.

| Service | Location | Public Methods |
|---|---|---|
| `EvalRunService` | `apps/core_eval/services/eval_run_service.py` | `create()`, `create_or_update()`, `mark_running()`, `mark_completed()`, `mark_failed()`, `get_by_entity()`, `get_latest()` |
| `EvalMetricService` | `apps/core_eval/services/eval_metric_service.py` | `record()`, `upsert()`, `list_for_run()`, `list_by_name()` |
| `EvalFieldOutcomeService` | `apps/core_eval/services/eval_field_outcome_service.py` | `record()`, `bulk_record()`, `replace_for_run()`, `list_for_run()`, `summary_for_run()` |
| `LearningSignalService` | `apps/core_eval/services/learning_signal_service.py` | `record()`, `list_by_entity()`, `list_by_module()`, `count_by_field()` |
| `LearningActionService` | `apps/core_eval/services/learning_action_service.py` | `propose()`, `approve()`, `mark_applied()`, `mark_rejected()`, `mark_failed()`, `list_by_status()`, `list_by_type()` |

### 4.2 LearningEngine

**Location**: `apps/core_eval/services/learning_engine.py`

The `LearningEngine` is a deterministic, rule-based engine that scans `LearningSignal` records within a configurable time window and proposes `LearningAction` records.

```python
engine = LearningEngine(days=7, min_confidence=0.0, cooldown_days=3)
summary = engine.run()                    # all modules
summary = engine.run(module="extraction") # single module
summary = engine.run(dry_run=True)        # preview only, no DB writes
```

#### Configuration

| Parameter | Default | Purpose |
|---|---|---|
| `days` | 7 | Time window for signal scanning |
| `min_confidence` | 0.0 | Minimum signal confidence to include |
| `cooldown_days` | 3 | Skip proposing if identical action exists within this period |

#### Rule Thresholds

| Constant | Default | Rule |
|---|---|---|
| `FIELD_CORRECTION_MIN_COUNT` | 20 | Min corrections to trigger field hotspot |
| `PROMPT_WEAKNESS_MIN_CORRECTIONS` | 10 | Min corrections to evaluate a prompt |
| `PROMPT_WEAKNESS_CORRECTION_RATE` | 0.30 | Correction rate threshold (30%) |
| `AUTO_APPROVE_RISK_MIN_COUNT` | 5 | Min risky auto-approvals to trigger |
| `VALIDATION_CLUSTER_MIN_COUNT` | 10 | Min identical validation errors to trigger |
| `VENDOR_ISSUE_MIN_COUNT` | 10 | Min vendor-specific corrections to trigger |

#### Safety Controls

1. **Dedup** -- A `[dedup_key:...]` tag in `target_description` prevents duplicate open (PROPOSED/APPROVED) actions for the same pattern.
2. **Cooldown** -- If an action with the same dedup_key was created within `cooldown_days`, it is skipped.
3. **Dry-run** -- `run(dry_run=True)` detects patterns and populates the summary but writes nothing.
4. **Idempotency** -- Running the engine twice on the same data produces no duplicates.

#### Aggregation Helpers

Public methods for ad-hoc signal analysis:

| Method | Returns |
|---|---|
| `aggregate_signals_by_key(aggregation_key)` | Count, unique entities, avg confidence, samples |
| `aggregate_signals_by_field(field_code)` | Count, avg confidence, top corrected values |
| `aggregate_signals_by_module(module_name)` | Total count, breakdown by signal_type |
| `aggregate_signals_by_prompt(prompt_hash)` | Count, avg confidence, breakdown by signal_type |

---

## 5. Adapters (Pipeline Integration)

Adapters are the bridge between production pipelines and the eval layer. Each adapter is a fail-silent class with `@classmethod` methods. Errors are logged but never propagated.

### 5.1 ExtractionEvalAdapter

**Location**: `apps/extraction/services/eval_adapter.py`

Wired into two places:

1. **`apps/extraction/tasks.py`** (after extraction persistence) calls `sync_for_extraction_result()` which creates:
   - `EvalRun` (one per extraction, upserted by `run_key`)
   - `EvalMetric` records (extraction_success, extraction_confidence, is_valid, is_duplicate, weakest_critical_field_score, decision_code_count, etc.)
   - `EvalFieldOutcome` records: predicted = LLM value from `raw_response` (deterministic fallback only when LLM has no value); ground truth = empty (populated later during approval)
   - `LearningSignal` records for validation failures and prompt review candidates

2. **`apps/extraction/services/approval_service.py`** (after approve / reject / auto-approve) calls `sync_for_approval()` which creates:
   - `LearningSignal` for `approval_outcome` or `auto_approve_outcome`
   - `LearningSignal` for each `field_correction` (from `ExtractionFieldCorrection` records)
   - `LearningSignal` for `review_override` (non-touchless approval with corrections)
   - `EvalMetric` updates on the parent extraction `EvalRun` (corrections_count, approval_decision, approval_confidence)
   - Ground truth confirmation: for non-corrected fields, `ground_truth_value` = `predicted_value` and `status` = CORRECT (via `_confirm_ground_truth_on_approval`)
   - For corrected fields, `ground_truth_value` = corrected value and `status` = INCORRECT (via `_update_field_outcomes_from_corrections`)

### 5.2 ReconciliationEvalAdapter (Implemented)

**File**: `apps/reconciliation/services/eval_adapter.py`

Bridges the reconciliation engine and review workflow into the eval layer. Wired at three lifecycle points:

1. **`ReconciliationRunnerService._reconcile_single()`** (after result persistence) -- calls `sync_for_result()`:
   - Creates/updates `EvalRun` (entity_type `reconciliation`, entity_id = result PK)
   - Stores predicted metrics: match_status, requires_review, auto_close_eligible, po_found, grn_found
   - Stores runtime metrics: exception_count, line_count, confidence, duration_ms, reconciliation_mode
   - Emits `LearningSignal` for match_outcome, mode_resolution, exception_pattern, auto_close_decision
   - Creates `EvalFieldOutcome` records for match_status, review_routing, auto_close, po_found, grn_found (predicted only; ground truth populated on review)

2. **`ReviewService.create_assignment()`** (after assignment creation) -- calls `sync_for_review_assignment()`:
   - Emits `review_created` learning signal with reviewer, priority, queue metadata

3. **`ReviewService._finalise()`** (after review decision) -- calls `sync_for_review_outcome()`:
   - Stores actual metrics: actual_match_status, review_decision, corrections_count
   - Emits learning signals: review_outcome, match_override (when reviewer changed match status), correction (per field)
   - Updates `EvalFieldOutcome` ground truth values from review decision

### 5.3 Future Adapters (Not Yet Implemented)

| Adapter | Pipeline | Signals to Capture |
|---|---|---|
| `PostingEvalAdapter` | Posting pipeline | Mapping accuracy, review queue frequency, confidence calibration |
| `AgentEvalAdapter` | Agent orchestrator | Recommendation acceptance rate, tool call success rate, confidence calibration |

---

## 6. Management Command

```bash
# Run with defaults (7-day window, all modules)
python manage.py run_learning_engine

# Restrict to extraction module, 14-day window
python manage.py run_learning_engine --module extraction --days 14

# Preview only (no DB writes)
python manage.py run_learning_engine --dry-run

# With minimum confidence filter and custom cooldown
python manage.py run_learning_engine --min-confidence 0.5 --cooldown-days 7
```

Output:
```
LearningEngine run complete:
  signals scanned   = 142
  rules evaluated   = 5
  actions proposed  = 3
  skipped (dedup)   = 1
  skipped (cooldown)= 0
  -> PROPOSED: field_normalization_candidate / ... -> LearningAction#12
  -> PROPOSED: prompt_review / ... -> LearningAction#13
  -> PROPOSED: threshold_tune / ... -> LearningAction#14
  -> DEDUP: vendor_rule_candidate / ...
```

---

## 7. Testing

### Test Files

| File | Tests | Scope |
|---|---|---|
| `apps/core_eval/tests/test_learning_engine.py` | 22 | Unit tests for LearningEngine: aggregation, all 5 rules, safety controls, management command |
| `apps/extraction/tests/test_eval_adapter.py` | 10 | Unit tests for ExtractionEvalAdapter: sync_for_extraction_result (EvalRun, metrics, field outcomes, idempotency, fail-silent) |
| `apps/extraction/tests/test_approval_integration.py` | 25 | Integration tests for approval -> LearningSignal creation (approve, reject, auto-approve, field corrections, review overrides) |
| `apps/core_eval/tests/test_end_to_end.py` | 13 | End-to-end: ExtractionEvalAdapter creates signals -> LearningEngine detects patterns -> LearningActions proposed |
| `apps/core_eval/tests/test_views.py` | 29 | RBAC view tests: anonymous redirect, permission denied, authorized access, filters, 404 handling |
| `apps/reconciliation/tests/test_recon_eval_adapter.py` | 21 | ReconciliationEvalAdapter: sync_for_result, sync_for_review_outcome, idempotency, fail-safety, review assignment |
| `apps/core/tests/test_evaluation_constants.py` | -- | Evaluation constant validation |

### Running Tests

```bash
# All eval/learning tests (120 total)
python -m pytest apps/core_eval/ apps/extraction/tests/test_eval_adapter.py apps/extraction/tests/test_approval_integration.py apps/reconciliation/tests/test_recon_eval_adapter.py -v

# Just the engine
python -m pytest apps/core_eval/tests/test_learning_engine.py -v

# Just end-to-end
python -m pytest apps/core_eval/tests/test_end_to_end.py -v

# Just reconciliation eval adapter
python -m pytest apps/reconciliation/tests/test_recon_eval_adapter.py -v
```

---

## 8. Data Flow Example

Complete flow for one invoice extraction with a field correction:

```
1. Upload + OCR + LLM extraction
   -> ExtractionResult saved (raw_response contains LLM-extracted values)
   -> Governed pipeline runs deterministic extraction (ExtractionFieldValue records)

2. tasks.py calls ExtractionEvalAdapter.sync_for_extraction_result()
   -> EvalRun created (app_module="extraction", entity_type="ExtractionResult")
   -> EvalMetric records: extraction_confidence=0.88, is_valid=1.0, ...
   -> EvalFieldOutcome records:
        invoice_number: predicted="INV-001" (from LLM), ground_truth="" (empty), status=CORRECT, conf=0.95
        total_amount:   predicted="1,000.00" (from LLM), ground_truth="" (empty), status=CORRECT, conf=0.80
        buyer_name:     predicted="Acme Corp" (from LLM), ground_truth="" (empty), status=CORRECT, conf=1.0
      detail_json per field: {source: "llm", deterministic_value: "...", deterministic_confidence: 0.0}

3. Human reviews extraction, corrects total_amount: "1,000.00" -> "1000.00"
   -> ExtractionApproval.status = APPROVED
   -> ExtractionFieldCorrection saved

4. approval_service.py calls ExtractionEvalAdapter.sync_for_approval()
   -> LearningSignal (signal_type="approval_outcome", entity_id=invoice.pk)
   -> LearningSignal (signal_type="field_correction", field_name="total_amount",
                       old_value="1,000.00", new_value="1000.00")
   -> LearningSignal (signal_type="review_override", fields_corrected=1)
   -> EvalFieldOutcome for total_amount updated: status=INCORRECT, ground_truth="1000.00"
   -> Ground truth confirmation for non-corrected fields:
        invoice_number: ground_truth="INV-001" (= predicted), status=CORRECT
        buyer_name:     ground_truth="Acme Corp" (= predicted), status=CORRECT
   -> EvalMetric: extraction_corrections_count=1

5. After N similar corrections accumulate, operator runs:
   $ python manage.py run_learning_engine --module extraction

6. LearningEngine scans LearningSignal records in the last 7 days
   -> Rule 1 (field_correction_hotspot): total_amount corrected 25 times
   -> LearningAction proposed:
        action_type = "field_normalization_candidate"
        target_description = "Field 'total_amount' corrected 25 times ..."
        action_payload_json = {
            "field_code": "total_amount",
            "issue": "frequent formatting corrections",
            "top_corrected_values": [{"value": "1000.00", "count": 18}],
            "suggested_fix": "normalize format before validation"
        }

7. Human reviews LearningAction in admin, approves, then applies fix
   -> LearningAction.status transitions: PROPOSED -> APPROVED -> APPLIED
```

---

## 9. Design Decisions

| Decision | Rationale |
|---|---|
| Domain-agnostic models | `core_eval` has no FK to Invoice, PO, or ExtractionResult. Adapters bridge the gap. Any module can emit signals. |
| Fail-silent adapters | Eval data is secondary. A bug in the adapter must never break extraction/approval. |
| `TimestampMixin` only (not `BaseModel`) | Eval tables are lightweight analytics data, not business entities. No soft-delete needed. |
| No auto-apply | All `LearningAction` proposals require human approval. The engine never modifies prompts, thresholds, or normalization rules on its own. |
| Dedup via `target_description` tag | Uses `[dedup_key:...]` text tag instead of JSONField `__contains` for SQLite compatibility in tests. |
| Cooldown period | Prevents re-proposing the same action immediately after rejection. Default 3 days. |
| Tenant isolation | `tenant_id` FK on all models provides row-level multi-tenant isolation via `CompanyProfile`. See [MULTI_TENANT.md](MULTI_TENANT.md). |

---

## 10. Adding a New Adapter

To integrate a new pipeline (e.g. reconciliation) with the eval layer:

1. Create `apps/<module>/services/eval_adapter.py` with a class following the `ExtractionEvalAdapter` pattern.
2. Define signal type constants (e.g. `SIG_MATCH_OUTCOME = "match_outcome"`).
3. Call `EvalRunService.create_or_update()` to upsert an `EvalRun` per pipeline execution.
4. Call `EvalMetricService.upsert()` for each numeric metric.
5. Call `LearningSignalService.record()` for each observable event.
6. Wire the adapter call into the pipeline task/service (in a `try/except` block).
7. Optionally add new rules to `LearningEngine` if new signal types warrant pattern detection.
8. Add tests: adapter unit tests + end-to-end tests with the engine.

---

## 11. File Inventory

| File | Purpose |
|---|---|
| `apps/core_eval/models.py` | 5 models: EvalRun, EvalMetric, EvalFieldOutcome, LearningSignal, LearningAction |
| `apps/core_eval/apps.py` | Django AppConfig |
| `apps/core_eval/admin.py` | Admin registration |
| `apps/core_eval/services/eval_run_service.py` | EvalRun CRUD |
| `apps/core_eval/services/eval_metric_service.py` | EvalMetric CRUD + upsert |
| `apps/core_eval/services/eval_field_outcome_service.py` | EvalFieldOutcome CRUD + replace_for_run |
| `apps/core_eval/services/learning_signal_service.py` | LearningSignal CRUD |
| `apps/core_eval/services/learning_action_service.py` | LearningAction lifecycle (propose/approve/apply/reject) |
| `apps/core_eval/services/learning_engine.py` | Deterministic rule engine (5 rules, aggregation helpers) |
| `apps/core_eval/management/commands/run_learning_engine.py` | Management command |
| `apps/core_eval/tests/test_learning_engine.py` | 22 unit tests |
| `apps/core_eval/tests/test_end_to_end.py` | 13 end-to-end tests |
| `apps/core_eval/tests/test_views.py` | 29 RBAC view tests (anonymous, permission denied, authorized, filters) |
| `apps/core_eval/template_views.py` | 5 FBV views: eval_run_list, eval_run_detail, learning_signal_list, learning_action_list, learning_action_detail |
| `apps/core_eval/urls.py` | URL routes (app_name="core_eval"), mounted at `/eval/` |
| `templates/core_eval/eval_run_list.html` | Eval runs list with KPI cards, filters, pagination |
| `templates/core_eval/eval_run_detail.html` | Eval run detail with metrics, field outcomes, signals |
| `templates/core_eval/learning_signal_list.html` | Learning signals list with filters |
| `templates/core_eval/learning_action_list.html` | Learning actions list with KPI cards, filters |
| `templates/core_eval/learning_action_detail.html` | Learning action detail with JSON payloads |
| `apps/extraction/services/eval_adapter.py` | ExtractionEvalAdapter (extraction <-> core_eval bridge) |
| `apps/extraction/tests/test_eval_adapter.py` | 10 adapter unit tests |
| `apps/extraction/tests/test_approval_integration.py` | 25 eval integration tests |
| `apps/reconciliation/services/eval_adapter.py` | ReconciliationEvalAdapter (reconciliation <-> core_eval bridge) |
| `apps/reconciliation/tests/test_recon_eval_adapter.py` | 21 adapter tests (sync_for_result, review outcome, idempotency, fail-safety) |

---

## 12. RBAC & Permissions

### Permissions

| Code | Module | Action | Description |
|---|---|---|---|
| `eval.view` | eval | view | View eval runs, learning signals, and learning actions |
| `eval.manage` | eval | manage | Approve, reject, or apply learning actions |

### Role Grants

| Role | `eval.view` | `eval.manage` |
|---|---|---|
| ADMIN | Yes | Yes |
| FINANCE_MANAGER | Yes | Yes |
| REVIEWER | Yes | -- |
| AUDITOR | Yes | -- |
| AP_PROCESSOR | -- | -- |
| SYSTEM_AGENT | -- | -- |

Permissions are seeded via `python manage.py seed_rbac`. All 5 template views require `eval.view`. The sidebar "Eval & Learning" section is gated by `{% has_permission "eval.view" %}`.

---

## 13. Audit Events

Six `AuditEventType` values track eval & learning lifecycle events:

| Event Type | Fired By | When |
|---|---|---|
| `LEARNING_ENGINE_RUN` | `LearningEngine.run()` | After each engine execution (includes signals_scanned, rules_evaluated, actions_proposed counts) |
| `LEARNING_ACTION_PROPOSED` | `LearningEngine._propose_action()` | When a new LearningAction is created from a detected pattern |
| `LEARNING_ACTION_APPROVED` | `LearningActionService.approve()` | When a human approves a proposed action |
| `LEARNING_ACTION_REJECTED` | `LearningActionService.mark_rejected()` | When a human rejects a proposed action |
| `LEARNING_ACTION_APPLIED` | `LearningActionService.mark_applied()` | When an approved action is applied to the system |
| `LEARNING_ACTION_FAILED` | `LearningActionService.mark_failed()` | When an approved action fails during application |

All audit calls are fail-silent (wrapped in `try/except`) -- audit logging errors never block the calling operation. Each event includes `status_before`/`status_after` metadata for action lifecycle transitions.

---

## 14. UI Views

Five template views provide a browsable interface for eval & learning data, mounted at `/eval/`.

| URL | View | Template | Description |
|---|---|---|---|
| `/eval/` | `eval_run_list` | `core_eval/eval_run_list.html` | List with KPI cards (total/completed/failed), filters (module, status, entity_type), pagination |
| `/eval/runs/<pk>/` | `eval_run_detail` | `core_eval/eval_run_detail.html` | Detail with metrics, field outcomes, linked signals |
| `/eval/signals/` | `learning_signal_list` | `core_eval/learning_signal_list.html` | List with filters (module, signal_type, field_name) |
| `/eval/actions/` | `learning_action_list` | `core_eval/learning_action_list.html` | List with KPI cards (proposed/approved/applied/rejected), filters |
| `/eval/actions/<pk>/` | `learning_action_detail` | `core_eval/learning_action_detail.html` | Full detail with target, rationale, JSON payloads |

All views use `@login_required` + `@permission_required_code("eval.view")`. Sidebar navigation is under "Eval & Learning" with 3 items: Eval Runs, Learning Signals, Learning Actions.
