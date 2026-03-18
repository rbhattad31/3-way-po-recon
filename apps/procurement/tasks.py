"""Celery tasks for the Procurement Intelligence platform."""
from __future__ import annotations

import logging

from celery import shared_task

from apps.core.decorators import observed_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.run_analysis", audit_event="ANALYSIS_RUN_STARTED", entity_type="AnalysisRun")
def run_analysis_task(self, run_id: int) -> dict:
    """Execute an analysis run (recommendation or benchmark).

    Dispatches to the appropriate service based on run_type.
    """
    from apps.core.enums import AnalysisRunType, ProcurementRequestStatus
    from apps.procurement.models import AnalysisRun
    from apps.procurement.services.analysis_run_service import AnalysisRunService
    from apps.procurement.services.benchmark_service import BenchmarkService
    from apps.procurement.services.recommendation_service import RecommendationService
    from apps.procurement.services.request_service import ProcurementRequestService

    run = AnalysisRun.objects.select_related("request").get(pk=run_id)
    request = run.request

    # Mark request as PROCESSING
    ProcurementRequestService.update_status(request, ProcurementRequestStatus.PROCESSING)

    try:
        if run.run_type == AnalysisRunType.RECOMMENDATION:
            result = RecommendationService.run_recommendation(request, run)
            return {
                "status": "completed",
                "run_id": str(run.run_id),
                "run_type": "RECOMMENDATION",
                "recommended_option": result.recommended_option,
                "confidence": result.confidence_score,
            }

        elif run.run_type == AnalysisRunType.BENCHMARK:
            # Find the quotation to benchmark
            quotation = request.quotations.first()
            if not quotation:
                AnalysisRunService.fail_run(run, "No quotation found for benchmarking")
                ProcurementRequestService.update_status(
                    request, ProcurementRequestStatus.FAILED,
                )
                return {"status": "failed", "error": "No quotation available"}

            result = BenchmarkService.run_benchmark(request, run, quotation)
            return {
                "status": "completed",
                "run_id": str(run.run_id),
                "run_type": "BENCHMARK",
                "risk_level": result.risk_level,
                "variance_pct": str(result.variance_pct),
            }

        else:
            AnalysisRunService.fail_run(run, f"Unknown run_type: {run.run_type}")
            return {"status": "failed", "error": f"Unknown run_type: {run.run_type}"}

    except Exception as exc:
        logger.exception("Analysis run %s failed: %s", run_id, exc)
        return {"status": "failed", "error": str(exc)}
