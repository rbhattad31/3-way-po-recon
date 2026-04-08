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

        elif run.run_type == AnalysisRunType.VALIDATION:
            from apps.procurement.services.validation.orchestrator_service import (
                ValidationOrchestratorService,
            )

            result = ValidationOrchestratorService.run_validation(request, run)
            return {
                "status": "completed",
                "run_id": str(run.run_id),
                "run_type": "VALIDATION",
                "overall_status": result.overall_status,
                "completeness_score": result.completeness_score,
            }

        else:
            AnalysisRunService.fail_run(run, f"Unknown run_type: {run.run_type}")
            return {"status": "failed", "error": f"Unknown run_type: {run.run_type}"}

    except Exception as exc:
        logger.exception("Analysis run %s failed: %s", run_id, exc)
        return {"status": "failed", "error": str(exc)}


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.run_validation", audit_event="VALIDATION_RUN_STARTED", entity_type="AnalysisRun")
def run_validation_task(self, run_id: int, *, agent_enabled: bool = False) -> dict:
    """Execute a validation run for a procurement request.

    Invokes the ValidationOrchestratorService to run all applicable
    deterministic validators and optionally the ValidationAgent.
    """
    from apps.core.enums import ProcurementRequestStatus, ValidationOverallStatus
    from apps.procurement.models import AnalysisRun
    from apps.procurement.services.request_service import ProcurementRequestService
    from apps.procurement.services.validation.orchestrator_service import (
        ValidationOrchestratorService,
    )

    run = AnalysisRun.objects.select_related("request").get(pk=run_id)
    request = run.request

    try:
        result = ValidationOrchestratorService.run_validation(
            request, run, agent_enabled=agent_enabled,
        )

        # Update request status based on validation outcome
        status_map = {
            ValidationOverallStatus.PASS: ProcurementRequestStatus.READY,
            ValidationOverallStatus.PASS_WITH_WARNINGS: ProcurementRequestStatus.READY,
            ValidationOverallStatus.REVIEW_REQUIRED: ProcurementRequestStatus.REVIEW_REQUIRED,
            ValidationOverallStatus.FAIL: ProcurementRequestStatus.FAILED,
        }
        new_status = status_map.get(result.overall_status, ProcurementRequestStatus.REVIEW_REQUIRED)
        ProcurementRequestService.update_status(request, new_status)

        return {
            "status": "completed",
            "run_id": str(run.run_id),
            "run_type": "VALIDATION",
            "overall_status": result.overall_status,
            "completeness_score": result.completeness_score,
        }

    except Exception as exc:
        logger.exception("Validation run %s failed: %s", run_id, exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Prefill Tasks
# ---------------------------------------------------------------------------
@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.request_prefill", audit_event="PREFILL_STARTED", entity_type="ProcurementRequest")
def run_request_prefill_task(self, request_id: int) -> dict:
    """Run OCR + LLM extraction to prefill a ProcurementRequest from an uploaded document."""
    from apps.procurement.models import ProcurementRequest
    from apps.procurement.services.prefill.request_prefill_service import RequestDocumentPrefillService

    proc_request = ProcurementRequest.objects.select_related("uploaded_document").get(pk=request_id)

    try:
        payload = RequestDocumentPrefillService.run_prefill(proc_request)
        return {
            "status": "completed",
            "request_id": request_id,
            "prefill_status": proc_request.prefill_status,
            "field_count": len(payload.get("core_fields", {})) + len(payload.get("attributes", [])),
        }
    except Exception as exc:
        logger.exception("Request prefill %s failed: %s", request_id, exc)
        from apps.procurement.services.prefill.prefill_status_service import PrefillStatusService
        PrefillStatusService.mark_request_failed(proc_request, str(exc))
        return {"status": "failed", "error": str(exc)}


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.quotation_prefill", audit_event="PREFILL_STARTED", entity_type="SupplierQuotation")
def run_quotation_prefill_task(self, quotation_id: int) -> dict:
    """Run OCR + LLM extraction to prefill a SupplierQuotation from an uploaded document."""
    from apps.procurement.models import SupplierQuotation
    from apps.procurement.services.prefill.quotation_prefill_service import QuotationDocumentPrefillService

    quotation = SupplierQuotation.objects.select_related("uploaded_document", "request").get(pk=quotation_id)

    try:
        payload = QuotationDocumentPrefillService.run_prefill(quotation)
        return {
            "status": "completed",
            "quotation_id": quotation_id,
            "prefill_status": quotation.prefill_status,
            "line_item_count": len(payload.get("line_items", [])),
        }
    except Exception as exc:
        logger.exception("Quotation prefill %s failed: %s", quotation_id, exc)
        from apps.procurement.services.prefill.prefill_status_service import PrefillStatusService
        PrefillStatusService.mark_quotation_failed(quotation, str(exc))
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Market Intelligence Tasks
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
@observed_task(
    "procurement.market_intelligence",
    audit_event="MARKET_INTELLIGENCE_STARTED",
    entity_type="ProcurementRequest",
)
def generate_market_intelligence_task(self, request_id: int) -> dict:
    """Generate and persist AI market intelligence for a ProcurementRequest.

    Called automatically (with ~20s countdown) when a new HVAC request is created,
    so the market intelligence page shows pre-populated results on the first visit.

    Can also be queued manually via the seed_market_intelligence management command.
    """
    from apps.procurement.models import ProcurementRequest
    from apps.procurement.services.market_intelligence_service import MarketIntelligenceService

    try:
        proc_request = ProcurementRequest.objects.get(pk=request_id)
    except ProcurementRequest.DoesNotExist:
        logger.warning("generate_market_intelligence_task: request pk=%s not found", request_id)
        return {"status": "skipped", "reason": "Request not found"}

    try:
        result = MarketIntelligenceService.generate(proc_request, generated_by=None)
        logger.info(
            "generate_market_intelligence_task: completed for pk=%s, %d suggestions",
            request_id, len(result.get("suggestions", [])),
        )
        return {
            "status": "completed",
            "request_id": request_id,
            "suggestion_count": len(result.get("suggestions", [])),
        }
    except Exception as exc:
        logger.warning(
            "generate_market_intelligence_task: failed for pk=%s: %s", request_id, exc,
        )
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass
        return {"status": "failed", "error": str(exc)}
