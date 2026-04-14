"""AnalysisRunService -- lightweight run lifecycle helpers.

Agent-first compatible: keeps AnalysisRun state transitions centralized while
actual business decisions are produced by agents.
"""
from __future__ import annotations

from django.utils import timezone

from apps.core.enums import AnalysisRunStatus
from apps.procurement.models import AnalysisRun


class AnalysisRunService:
    """Create and manage AnalysisRun lifecycle records."""

    @staticmethod
    def create_run(*, request, run_type: str, triggered_by=None, tenant=None) -> AnalysisRun:
        return AnalysisRun.objects.create(
            tenant=tenant or getattr(request, "tenant", None),
            request=request,
            run_type=run_type,
            status=AnalysisRunStatus.QUEUED,
            triggered_by=triggered_by,
            started_at=None,
            completed_at=None,
            input_snapshot_json={},
            output_summary="",
            error_message="",
            trace_id=getattr(request, "trace_id", "") or "",
        )

    @staticmethod
    def start_run(run: AnalysisRun) -> AnalysisRun:
        run.status = AnalysisRunStatus.RUNNING
        if not run.started_at:
            run.started_at = timezone.now()
        run.error_message = ""
        run.save(update_fields=["status", "started_at", "error_message", "updated_at"])
        return run

    @staticmethod
    def complete_run(run: AnalysisRun, *, output_summary: str = "", confidence_score=None) -> AnalysisRun:
        run.status = AnalysisRunStatus.COMPLETED
        if not run.started_at:
            run.started_at = timezone.now()
        run.completed_at = timezone.now()
        if output_summary:
            run.output_summary = output_summary
        if confidence_score is not None:
            run.confidence_score = confidence_score
        run.save(
            update_fields=[
                "status",
                "started_at",
                "completed_at",
                "output_summary",
                "confidence_score",
                "updated_at",
            ],
        )
        return run

    @staticmethod
    def fail_run(run: AnalysisRun, error_message: str) -> AnalysisRun:
        run.status = AnalysisRunStatus.FAILED
        if not run.started_at:
            run.started_at = timezone.now()
        run.completed_at = timezone.now()
        run.error_message = str(error_message or "")[:4000]
        run.save(update_fields=["status", "started_at", "completed_at", "error_message", "updated_at"])
        return run
