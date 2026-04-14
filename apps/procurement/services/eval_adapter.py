"""ProcurementEvalAdapter -- bridges procurement analysis into core_eval.

This adapter is fail-silent by design and can be called after every
AnalysisRun completion/failure.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

APP_MODULE = "procurement"
ENTITY_TYPE = "AnalysisRun"
SIG_RUN_FAILED = "run_failed"
SIG_VALIDATION_FAILURE = "validation_failure"


class ProcurementEvalAdapter:
    """Write procurement run outcomes into EvalRun, EvalMetric, LearningSignal."""

    @classmethod
    def sync_for_analysis_run(cls, run, *, trace_id: str = "") -> None:
        try:
            cls._sync_for_analysis_run_inner(run, trace_id=trace_id)
        except Exception:
            logger.exception(
                "ProcurementEvalAdapter.sync_for_analysis_run failed for run=%s (non-fatal)",
                getattr(run, "pk", "?"),
            )

    @classmethod
    def _sync_for_analysis_run_inner(cls, run, *, trace_id: str = "") -> None:
        from apps.core_eval.models import EvalRun
        from apps.core_eval.services.learning_engine import LearningEngine
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        _tenant = getattr(run, "tenant", None) or getattr(getattr(run, "request", None), "tenant", None)
        _trace_id = trace_id or getattr(run, "trace_id", "") or ""
        _run_key = f"procurement::{getattr(run, 'run_type', '')}::{getattr(run, 'pk', '')}"

        eval_status = EvalRun.Status.RUNNING
        run_status = str(getattr(run, "status", "") or "")
        if run_status in {"COMPLETED", "DONE"}:
            eval_status = EvalRun.Status.COMPLETED
        elif run_status in {"FAILED", "ERROR"}:
            eval_status = EvalRun.Status.FAILED

        eval_run, _ = EvalRunService.create_or_update(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE,
            entity_id=str(getattr(run, "pk", "")),
            run_key=_run_key,
            status=eval_status,
            trace_id=_trace_id,
            input_snapshot_json={
                "request_id": str(getattr(getattr(run, "request", None), "request_id", "")),
                "run_type": str(getattr(run, "run_type", "") or ""),
            },
            tenant=_tenant,
        )

        started_at = getattr(run, "started_at", None)
        completed_at = getattr(run, "completed_at", None)
        if started_at and not eval_run.started_at:
            eval_run.started_at = started_at
        if completed_at:
            eval_run.completed_at = completed_at
        elif eval_status in {EvalRun.Status.COMPLETED, EvalRun.Status.FAILED}:
            eval_run.completed_at = timezone.now()

        if eval_run.started_at and eval_run.completed_at:
            eval_run.duration_ms = max(
                0,
                int((eval_run.completed_at - eval_run.started_at).total_seconds() * 1000),
            )
        eval_run.save(update_fields=["started_at", "completed_at", "duration_ms", "updated_at"])

        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name="run_type",
            value_type="string",
            value=str(getattr(run, "run_type", "") or ""),
            tenant=_tenant,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name="run_completed",
            value_type="float",
            value=1.0 if eval_status == EvalRun.Status.COMPLETED else 0.0,
            unit="ratio",
            tenant=_tenant,
        )

        confidence = getattr(run, "confidence_score", None)
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0.0
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="run_confidence",
                value_type="float",
                value=max(0.0, min(1.0, confidence)),
                unit="ratio",
                tenant=_tenant,
            )

        if eval_status == EvalRun.Status.FAILED:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_RUN_FAILED,
                entity_type=ENTITY_TYPE,
                entity_id=str(getattr(run, "pk", "")),
                aggregation_key=f"proc-run-failed-{getattr(run, 'pk', '')}",
                confidence=1.0,
                actor=getattr(run, "triggered_by", None),
                payload_json={
                    "run_type": str(getattr(run, "run_type", "") or ""),
                    "error_message": str(getattr(run, "error_message", "") or "")[:1000],
                },
                eval_run=eval_run,
                tenant=_tenant,
            )
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_VALIDATION_FAILURE,
                entity_type=ENTITY_TYPE,
                entity_id=str(getattr(run, "pk", "")),
                aggregation_key=f"proc-run-error::{getattr(run, 'run_type', '')}",
                confidence=1.0,
                actor=getattr(run, "triggered_by", None),
                payload_json={
                    "error": str(getattr(run, "error_message", "") or "run_failed")[:300],
                    "run_type": str(getattr(run, "run_type", "") or ""),
                },
                eval_run=eval_run,
                tenant=_tenant,
            )

        if str(getattr(run, "run_type", "") or "") == "VALIDATION":
            cls._record_validation_signals(
                run=run,
                eval_run=eval_run,
                tenant=_tenant,
            )

        if eval_status in {EvalRun.Status.COMPLETED, EvalRun.Status.FAILED}:
            try:
                LearningEngine(tenant=_tenant).run(module=APP_MODULE)
            except Exception:
                logger.debug(
                    "ProcurementEvalAdapter: LearningEngine run failed (non-fatal) for run=%s",
                    getattr(run, "pk", "?"),
                    exc_info=True,
                )

    @classmethod
    def _record_validation_signals(cls, *, run, eval_run, tenant) -> None:
        from apps.core.enums import ValidationItemStatus
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        validation_result = getattr(run, "validation_result", None)
        if validation_result is None:
            return

        failed_items = validation_result.items.filter(
            status__in=[
                ValidationItemStatus.MISSING,
                ValidationItemStatus.AMBIGUOUS,
            ],
        )
        for item in failed_items:
            error_text = str(item.remarks or item.item_label or item.item_code or "validation_failure")
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_VALIDATION_FAILURE,
                entity_type=ENTITY_TYPE,
                entity_id=str(getattr(run, "pk", "")),
                aggregation_key=f"validation-item::{item.item_code}",
                confidence=1.0 if item.status == ValidationItemStatus.MISSING else 0.8,
                actor=getattr(run, "triggered_by", None),
                field_name=str(item.item_code or ""),
                payload_json={
                    "error": error_text[:300],
                    "item_code": str(item.item_code or ""),
                    "status": str(item.status or ""),
                    "severity": str(item.severity or ""),
                    "run_type": str(getattr(run, "run_type", "") or ""),
                },
                eval_run=eval_run,
                tenant=tenant,
            )
