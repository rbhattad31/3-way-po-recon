"""BenchmarkingEvalAdapter -- bridges benchmarking runs into core_eval.

Fail-silent adapter invoked by BenchmarkEngine.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

APP_MODULE = "benchmarking"
ENTITY_TYPE = "BenchmarkRequest"
SIG_BENCHMARK_FAILED = "benchmark_run_failed"
SIG_VALIDATION_FAILURE = "validation_failure"


class BenchmarkingEvalAdapter:
    """Persist benchmark request outcomes into EvalRun, metrics, and signals."""

    @classmethod
    def sync_for_request(cls, bench_request, *, error_message: str = "", trace_id: str = "") -> None:
        try:
            cls._sync_for_request_inner(bench_request, error_message=error_message, trace_id=trace_id)
        except Exception:
            logger.exception(
                "BenchmarkingEvalAdapter.sync_for_request failed for request=%s (non-fatal)",
                getattr(bench_request, "pk", "?"),
            )

    @classmethod
    def _sync_for_request_inner(cls, bench_request, *, error_message: str, trace_id: str) -> None:
        from apps.core_eval.models import EvalRun
        from apps.core_eval.services.learning_engine import LearningEngine
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        _tenant = getattr(bench_request, "tenant", None)
        _trace_id = trace_id or ""
        _run_key = f"benchmark-request::{getattr(bench_request, 'pk', '')}"
        status_str = str(getattr(bench_request, "status", "") or "")

        eval_status = EvalRun.Status.RUNNING
        if status_str == "COMPLETED":
            eval_status = EvalRun.Status.COMPLETED
        elif status_str == "FAILED":
            eval_status = EvalRun.Status.FAILED

        eval_run, _ = EvalRunService.create_or_update(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE,
            entity_id=str(getattr(bench_request, "pk", "")),
            run_key=_run_key,
            status=eval_status,
            trace_id=_trace_id,
            input_snapshot_json={
                "geography": str(getattr(bench_request, "geography", "") or ""),
                "scope_type": str(getattr(bench_request, "scope_type", "") or ""),
            },
            tenant=_tenant,
        )

        if eval_status in {EvalRun.Status.COMPLETED, EvalRun.Status.FAILED} and not eval_run.completed_at:
            eval_run.completed_at = timezone.now()
            eval_run.save(update_fields=["completed_at", "updated_at"])

        quotation_count = 0
        line_count = 0
        try:
            from apps.benchmarking.models import BenchmarkLineItem

            quotation_count = int(bench_request.quotations.filter(is_active=True).count())
            line_count = int(
                BenchmarkLineItem.objects.filter(
                    quotation__request=bench_request,
                    quotation__is_active=True,
                    is_active=True,
                ).count()
            )
        except Exception:
            pass

        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name="request_completed",
            value_type="float",
            value=1.0 if eval_status == EvalRun.Status.COMPLETED else 0.0,
            unit="ratio",
            tenant=_tenant,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name="quotation_count",
            value_type="float",
            value=float(quotation_count),
            unit="count",
            tenant=_tenant,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name="line_item_count",
            value_type="float",
            value=float(line_count),
            unit="count",
            tenant=_tenant,
        )

        result = getattr(bench_request, "result", None)
        if result is not None:
            deviation = getattr(result, "overall_deviation_pct", None)
            if deviation is not None:
                try:
                    deviation = float(deviation)
                    EvalMetricService.upsert(
                        eval_run=eval_run,
                        metric_name="overall_deviation_pct",
                        value_type="float",
                        value=deviation,
                        unit="percentage",
                        tenant=_tenant,
                    )
                except (TypeError, ValueError):
                    pass

            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="overall_status",
                value_type="string",
                value=str(getattr(result, "overall_status", "") or ""),
                tenant=_tenant,
            )

        if eval_status == EvalRun.Status.FAILED:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_BENCHMARK_FAILED,
                entity_type=ENTITY_TYPE,
                entity_id=str(getattr(bench_request, "pk", "")),
                aggregation_key=f"benchmark-failed-{getattr(bench_request, 'pk', '')}",
                confidence=1.0,
                actor=getattr(bench_request, "submitted_by", None),
                payload_json={
                    "error": (error_message or str(getattr(bench_request, "error_message", "") or ""))[:1000],
                },
                eval_run=eval_run,
                tenant=_tenant,
            )
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_VALIDATION_FAILURE,
                entity_type=ENTITY_TYPE,
                entity_id=str(getattr(bench_request, "pk", "")),
                aggregation_key="benchmark-request-failed",
                confidence=1.0,
                actor=getattr(bench_request, "submitted_by", None),
                payload_json={
                    "error": (error_message or str(getattr(bench_request, "error_message", "") or "run_failed"))[:300],
                    "status": status_str,
                },
                eval_run=eval_run,
                tenant=_tenant,
            )

        cls._record_variance_signals(
            bench_request=bench_request,
            eval_run=eval_run,
            tenant=_tenant,
        )

        if eval_status in {EvalRun.Status.COMPLETED, EvalRun.Status.FAILED}:
            try:
                LearningEngine(tenant=_tenant).run(module=APP_MODULE)
            except Exception:
                logger.debug(
                    "BenchmarkingEvalAdapter: LearningEngine run failed (non-fatal) for request=%s",
                    getattr(bench_request, "pk", "?"),
                    exc_info=True,
                )

    @classmethod
    def _record_variance_signals(cls, *, bench_request, eval_run, tenant) -> None:
        from apps.benchmarking.models import BenchmarkLineItem, VarianceStatus
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        risky_items = BenchmarkLineItem.objects.filter(
            quotation__request=bench_request,
            quotation__is_active=True,
            is_active=True,
            variance_status__in=[VarianceStatus.HIGH, VarianceStatus.NEEDS_REVIEW],
        ).select_related("quotation")[:250]

        for item in risky_items:
            category = str(item.category or "UNCATEGORIZED")
            variance_pct = item.variance_pct
            variance_text = "n/a" if variance_pct is None else f"{float(variance_pct):.2f}%"
            status = str(item.variance_status or "")
            error_text = f"line_variance_{status.lower()}::{category}::{variance_text}"

            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_VALIDATION_FAILURE,
                entity_type=ENTITY_TYPE,
                entity_id=str(getattr(bench_request, "pk", "")),
                aggregation_key=f"variance::{category}",
                confidence=1.0 if status == VarianceStatus.HIGH else 0.75,
                actor=getattr(bench_request, "submitted_by", None),
                field_name="variance_pct",
                payload_json={
                    "error": error_text[:300],
                    "line_item_id": item.pk,
                    "category": category,
                    "variance_status": status,
                    "variance_pct": variance_pct,
                    "quotation_id": getattr(item.quotation, "pk", None),
                },
                eval_run=eval_run,
                tenant=tenant,
            )
