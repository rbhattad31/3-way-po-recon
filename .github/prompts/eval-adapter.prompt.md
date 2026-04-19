---
mode: agent
description: "Add an evaluation adapter to bridge a production pipeline to the core_eval framework"
---

# Add an Eval Adapter

## Step 0 -- Read Existing Architecture First

### Documentation
- `docs/EVAL_LEARNING.md` -- full eval/learning architecture, data model, adapter pattern, learning engine rules, safety controls
- `docs/current_system_review/02_Django_App_Landscape.md` -- core_eval app boundaries
- `docs/current_system_review/08_Audit_and_Traceability.md` -- how eval events fit into the audit trail

### Source Files
- `apps/extraction/services/eval_adapter.py` -- `ExtractionEvalAdapter` (canonical example: study `record_extraction_eval()` and `record_approval_outcome()`)
- `apps/reconciliation/services/eval_adapter.py` -- `ReconciliationEvalAdapter` (second example: study `record_recon_eval()` and `record_review_outcome()`)
- `apps/core_eval/models.py` -- `EvalRun`, `EvalMetric`, `EvalFieldOutcome`, `LearningSignal`, `LearningAction` (study field purposes)
- `apps/core_eval/services/eval_run_service.py` -- `EvalRunService.create_or_update()` (upsert pattern)
- `apps/core_eval/services/eval_metric_service.py` -- `EvalMetricService.upsert()` (metric recording)
- `apps/core_eval/services/eval_field_outcome_service.py` -- `EvalFieldOutcomeService.record()` (predicted vs ground truth)
- `apps/core_eval/services/learning_signal_service.py` -- `LearningSignalService.record()` (observable events)
- `apps/core_eval/services/learning_engine.py` -- `LearningEngine` (5 threshold rules, aggregation, action proposal)

### Comprehension Check
1. The eval layer is **one-way**: production -> eval. Eval never writes back to production models.
2. `EvalRun` is upserted per pipeline execution (keyed on `app_module` + `entity_type` + `entity_id`)
3. `EvalFieldOutcome.predicted_value` = the pipeline's output (LLM/model value). `ground_truth_value` starts empty, populated only on human approval/correction.
4. `LearningSignal` records are atomic observations: one signal per event (e.g. `"field_corrected"`, `"confidence_low"`)
5. All eval code runs inside `try/except` -- errors never propagate to the calling pipeline
6. Models use `TimestampMixin` (not `BaseModel`) with `tenant_id` for multi-tenant isolation

---

## Inputs

- **Pipeline name**: which pipeline this adapter bridges (e.g. `posting`, `agent`)
- **App path**: which `apps/<app>/services/` directory to place the adapter
- **Entity type**: what entity the eval tracks (e.g. `"posting_run"`, `"agent_run"`)
- **Key metrics**: list of numeric metrics to capture per run
- **Key signals**: list of learning signal types to emit

---

## Steps

### 1. Define Signal Type Constants

At the top of the adapter file:

```python
# Signal type constants
SIG_PIPELINE_SUCCESS = "pipeline_success"
SIG_PIPELINE_FAILURE = "pipeline_failure"
SIG_CONFIDENCE_LOW = "confidence_low"
SIG_FIELD_CORRECTED = "field_corrected"
# ... domain-specific signals
```

### 2. Create the Adapter Class

In `apps/<app>/services/eval_adapter.py`:

```python
import logging
from apps.core_eval.services.eval_run_service import EvalRunService
from apps.core_eval.services.eval_metric_service import EvalMetricService
from apps.core_eval.services.eval_field_outcome_service import EvalFieldOutcomeService
from apps.core_eval.services.learning_signal_service import LearningSignalService

logger = logging.getLogger(__name__)


class MyPipelineEvalAdapter:
    """Bridges the <pipeline> pipeline to the core_eval framework."""

    @classmethod
    def record_pipeline_eval(cls, run_instance, tenant_id=None):
        """Called at the end of the pipeline run. Fail-silent."""
        try:
            eval_run = EvalRunService.create_or_update(
                app_module="my_pipeline",
                entity_type="my_entity",
                entity_id=str(run_instance.pk),
                tenant_id=tenant_id,
                metadata={
                    "status": run_instance.status,
                    "confidence": run_instance.confidence,
                },
            )

            # Record metrics
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="pipeline_confidence",
                numeric_value=float(run_instance.confidence or 0),
            )

            # Record field outcomes (predicted = pipeline output, ground_truth = empty)
            for field_name, field_value in cls._extract_fields(run_instance):
                EvalFieldOutcomeService.record(
                    eval_run=eval_run,
                    field_name=field_name,
                    predicted_value=str(field_value) if field_value else "",
                    ground_truth_value="",  # Populated on human approval
                )

            # Emit learning signals
            if run_instance.confidence and run_instance.confidence < 0.5:
                LearningSignalService.record(
                    eval_run=eval_run,
                    signal_type=SIG_CONFIDENCE_LOW,
                    detail_json={"confidence": float(run_instance.confidence)},
                )

        except Exception:
            logger.debug("Eval adapter failed for %s pk=%s", "my_entity", run_instance.pk, exc_info=True)

    @classmethod
    def record_human_outcome(cls, run_instance, corrections=None, tenant_id=None):
        """Called on human approval/rejection. Updates ground truth."""
        try:
            eval_run = EvalRunService.create_or_update(
                app_module="my_pipeline",
                entity_type="my_entity",
                entity_id=str(run_instance.pk),
                tenant_id=tenant_id,
            )

            if corrections:
                for field_name, old_val, new_val in corrections:
                    EvalFieldOutcomeService.record(
                        eval_run=eval_run,
                        field_name=field_name,
                        predicted_value=str(old_val) if old_val else "",
                        ground_truth_value=str(new_val) if new_val else "",
                    )
                    LearningSignalService.record(
                        eval_run=eval_run,
                        signal_type=SIG_FIELD_CORRECTED,
                        detail_json={"field": field_name, "old": str(old_val), "new": str(new_val)},
                    )

        except Exception:
            logger.debug("Eval adapter human outcome failed", exc_info=True)
```

### 3. Wire into the Pipeline

Call the adapter at the end of the pipeline task/service, inside a `try/except`:

```python
# In the pipeline task or service, after the main work:
try:
    MyPipelineEvalAdapter.record_pipeline_eval(run_instance, tenant_id=tenant_id)
except Exception:
    pass  # Eval is fail-silent
```

### 4. Wire into the Approval/Review Service

Call `record_human_outcome()` when a human approves, rejects, or corrects:

```python
try:
    MyPipelineEvalAdapter.record_human_outcome(run_instance, corrections=corrections_list)
except Exception:
    pass
```

### 5. (Optional) Add Learning Engine Rules

If the new signal types warrant pattern detection, add rules to `LearningEngine`:

```python
# In apps/core_eval/services/learning_engine.py
# Add a new rule method and register it in _RULES list
```

### 6. Add RBAC Permissions

If not already present, ensure `eval.view` and `eval.manage` permissions exist in `seed_rbac.py`.

### 7. Write Tests

- Adapter unit test: `record_pipeline_eval()` creates EvalRun + EvalMetric + EvalFieldOutcome
- Adapter unit test: `record_human_outcome()` updates ground truth
- Fail-silent test: adapter does not raise when DB is unavailable
- Tenant isolation: eval records scoped to correct tenant
- Integration test: pipeline run -> adapter -> learning engine detects pattern

---

## Constraints

- Eval layer is **read-only from production's perspective** -- never write back to pipeline models
- All adapter methods must be wrapped in `try/except` -- never propagate errors
- `EvalFieldOutcome.ground_truth_value` is empty at pipeline time -- only populated on human review
- Use `TimestampMixin` conventions (not `BaseModel`) for any new eval models
- ASCII only in all signal types, metric names, and stored values
