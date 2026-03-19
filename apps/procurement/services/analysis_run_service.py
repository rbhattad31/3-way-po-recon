"""AnalysisRunService — create and manage analysis run lifecycle."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.utils import timezone

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import AnalysisRunStatus, ProcurementRequestStatus
from apps.core.trace import TraceContext
from apps.procurement.models import AnalysisRun, ProcurementRequest
from apps.procurement.services.request_service import AttributeService

logger = logging.getLogger(__name__)


class AnalysisRunService:
    """Manage the lifecycle of analysis runs."""

    @staticmethod
    @observed_service("procurement.analysis_run.create", audit_event="ANALYSIS_RUN_CREATED")
    def create_run(
        *,
        request: ProcurementRequest,
        run_type: str,
        triggered_by=None,
    ) -> AnalysisRun:
        ctx = TraceContext.get_current()

        # Build input snapshot
        input_snapshot = {
            "request_id": str(request.request_id),
            "request_type": request.request_type,
            "domain_code": request.domain_code,
            "attributes": AttributeService.get_attributes_dict(request),
        }

        run = AnalysisRun.objects.create(
            request=request,
            run_type=run_type,
            status=AnalysisRunStatus.QUEUED,
            triggered_by=triggered_by,
            input_snapshot_json=input_snapshot,
            trace_id=ctx.trace_id if ctx else "",
        )

        AuditService.log_event(
            entity_type="AnalysisRun",
            entity_id=run.pk,
            event_type="ANALYSIS_RUN_CREATED",
            description=f"{run_type} run created for request {request.request_id}",
            user=triggered_by,
            trace_ctx=ctx,
            status_after=AnalysisRunStatus.QUEUED,
        )
        return run

    @staticmethod
    def start_run(run: AnalysisRun) -> AnalysisRun:
        run.status = AnalysisRunStatus.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at", "updated_at"])

        AuditService.log_event(
            entity_type="AnalysisRun",
            entity_id=run.pk,
            event_type="ANALYSIS_RUN_STARTED",
            description=f"Run {run.run_id} started",
            trace_ctx=TraceContext.get_current(),
            status_before=AnalysisRunStatus.QUEUED,
            status_after=AnalysisRunStatus.RUNNING,
        )
        return run

    @staticmethod
    def complete_run(
        run: AnalysisRun,
        *,
        output_summary: str = "",
        confidence_score: float | None = None,
    ) -> AnalysisRun:
        run.status = AnalysisRunStatus.COMPLETED
        run.completed_at = timezone.now()
        run.output_summary = output_summary
        run.confidence_score = confidence_score
        run.save(update_fields=[
            "status", "completed_at", "output_summary", "confidence_score", "updated_at",
        ])

        AuditService.log_event(
            entity_type="AnalysisRun",
            entity_id=run.pk,
            event_type="ANALYSIS_RUN_COMPLETED",
            description=f"Run {run.run_id} completed",
            trace_ctx=TraceContext.get_current(),
            status_before=AnalysisRunStatus.RUNNING,
            status_after=AnalysisRunStatus.COMPLETED,
            output_snapshot={"confidence": confidence_score, "summary": output_summary[:500]},
        )
        return run

    @staticmethod
    def fail_run(run: AnalysisRun, error_message: str = "") -> AnalysisRun:
        run.status = AnalysisRunStatus.FAILED
        run.completed_at = timezone.now()
        run.error_message = error_message
        run.save(update_fields=["status", "completed_at", "error_message", "updated_at"])

        AuditService.log_event(
            entity_type="AnalysisRun",
            entity_id=run.pk,
            event_type="ANALYSIS_RUN_FAILED",
            description=f"Run {run.run_id} failed: {error_message[:200]}",
            trace_ctx=TraceContext.get_current(),
            status_before=AnalysisRunStatus.RUNNING,
            status_after=AnalysisRunStatus.FAILED,
            error_code="ANALYSIS_RUN_FAILURE",
        )
        return run
