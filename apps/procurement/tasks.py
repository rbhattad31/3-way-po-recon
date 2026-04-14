"""Celery tasks for the Procurement Intelligence platform."""
from __future__ import annotations

import logging

from celery import shared_task

from apps.core.decorators import observed_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.run_analysis", audit_event="ANALYSIS_RUN_STARTED", entity_type="AnalysisRun")
def run_analysis_task(self, tenant_id: int = None, run_id: int = 0) -> dict:
    """Execute an analysis run (recommendation or cost analysis).

    Dispatches to the appropriate service based on run_type.
    """
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from apps.core.enums import AnalysisRunType, ProcurementRequestStatus
    from apps.procurement.models import AnalysisRun
    from apps.procurement.services.eval_adapter import ProcurementEvalAdapter
    from apps.procurement.services.analysis_run_service import AnalysisRunService
    from apps.benchmarking.services.procurement_cost_service import ProcurementCostService
    from apps.procurement.services.recommendation_service import RecommendationService
    from apps.procurement.services.request_service import ProcurementRequestService

    # Tenant-scoped query -- filter via request__tenant so legacy AnalysisRun records
    # that have tenant=NULL (created before the auto-derive fix) are still found.
    qs = AnalysisRun.objects.select_related("request", "triggered_by")
    if tenant:
        qs = qs.filter(request__tenant=tenant)
    run = qs.get(pk=run_id)
    request = run.request
    # Use the user who triggered the run so RBAC checks reflect the real actor.
    # Falls back to SYSTEM_AGENT (None) when triggered_by is null.
    request_user = run.triggered_by if run.triggered_by_id else None

    # Phase 6: Langfuse root trace for task-level observability
    _lf_trace = None
    _lf_trace_id = (getattr(run, "trace_id", "") or "").replace("-", "") or str(run.run_id).replace("-", "")
    try:
        from apps.core.langfuse_client import start_trace_safe
        _lf_trace = start_trace_safe(
            _lf_trace_id,
            "procurement_analysis_task",
            metadata={
                "run_id": str(run.run_id),
                "run_type": str(run.run_type),
                "task_id": str(self.request.id or ""),
                "tenant_id": tenant_id,
            },
        )
    except Exception:
        pass

    # Mark request as PROCESSING
    ProcurementRequestService.update_status(request, ProcurementRequestStatus.PROCESSING)

    _task_result: dict = {"status": "failed"}
    try:
        if run.run_type == AnalysisRunType.RECOMMENDATION:
            result = RecommendationService.run_recommendation(request, run, request_user=request_user)
            
            # Auto-generate external suggestions for HVAC requests
            try:
                if request.domain_code == "HVAC":
                    from apps.procurement.services.market_intelligence_service import MarketIntelligenceService
                    MarketIntelligenceService.generate_auto(
                        request,
                        generated_by=None,
                        run=run,
                        request_user=request_user,
                    )
            except Exception as _mi_exc:
                logger.warning("Auto-generation of market intelligence failed for request %s: %s", request.pk, _mi_exc)
            
            _task_result = {
                "status": "completed",
                "run_id": str(run.run_id),
                "run_type": "RECOMMENDATION",
                "recommended_option": result.recommended_option,
                "confidence": result.confidence_score,
            }
            ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))
            return _task_result

        elif run.run_type == AnalysisRunType.BENCHMARK:
            # Find the quotation for cost analysis
            quotation = request.quotations.first()
            if not quotation:
                AnalysisRunService.fail_run(run, "No quotation found for cost analysis")
                ProcurementRequestService.update_status(
                    request, ProcurementRequestStatus.FAILED,
                )
                return {"status": "failed", "error": "No quotation available"}

            result = ProcurementCostService.run_cost_analysis(request, run, quotation)
            ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))
            _task_result = {
                "status": "completed",
                "run_id": str(run.run_id),
                "run_type": "COST_ANALYSIS",
                "risk_level": result.risk_level,
                "variance_pct": str(result.variance_pct),
            }
            return _task_result

        elif run.run_type == AnalysisRunType.VALIDATION:
            from apps.procurement.services.validation.orchestrator_service import (
                ValidationOrchestratorService,
            )

            result = ValidationOrchestratorService.run_validation(request, run)
            ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))
            _task_result = {
                "status": "completed",
                "run_id": str(run.run_id),
                "run_type": "VALIDATION",
                "overall_status": result.overall_status,
                "completeness_score": result.completeness_score,
            }
            return _task_result

        else:
            AnalysisRunService.fail_run(run, f"Unknown run_type: {run.run_type}")
            ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))
            _task_result = {"status": "failed", "error": f"Unknown run_type: {run.run_type}"}
            return _task_result

    except Exception as exc:
        logger.exception("Analysis run %s failed: %s", run_id, exc)
        try:
            run.refresh_from_db()
            ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))
        except Exception:
            pass
        _task_result = {"status": "failed", "error": str(exc)}
        return _task_result
    finally:
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace, output=_task_result, is_root=True)
            score_trace_safe(
                _lf_trace_id,
                "procurement_analysis_success",
                1.0 if _task_result.get("status") == "completed" else 0.0,
                span=_lf_trace,
            )
        except Exception:
            pass


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.run_validation", audit_event="VALIDATION_RUN_STARTED", entity_type="AnalysisRun")
def run_validation_task(self, tenant_id: int = None, run_id: int = 0, *, agent_enabled: bool = False) -> dict:
    """Execute a validation run for a procurement request.

    Invokes the ValidationOrchestratorService to run all applicable
    deterministic validators and optionally the ValidationAgent.
    """
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from apps.core.enums import ProcurementRequestStatus, ValidationOverallStatus
    from apps.procurement.models import AnalysisRun
    from apps.procurement.services.request_service import ProcurementRequestService
    from apps.procurement.services.eval_adapter import ProcurementEvalAdapter
    from apps.procurement.services.validation.orchestrator_service import (
        ValidationOrchestratorService,
    )

    # Tenant-scoped query -- filter via request__tenant so legacy records are still found.
    qs = AnalysisRun.objects.select_related("request")
    if tenant:
        qs = qs.filter(request__tenant=tenant)
    run = qs.get(pk=run_id)
    request = run.request

    # Phase 6: Langfuse root trace
    _lf_trace_v = None
    _lf_trace_id_v = (getattr(run, "trace_id", "") or "").replace("-", "") or str(run.run_id).replace("-", "")
    try:
        from apps.core.langfuse_client import start_trace_safe
        _lf_trace_v = start_trace_safe(
            _lf_trace_id_v,
            "procurement_validation_task",
            metadata={"run_id": str(run.run_id), "task_id": str(self.request.id or ""), "tenant_id": tenant_id},
        )
    except Exception:
        pass

    _val_result: dict = {"status": "failed"}
    try:
        result = ValidationOrchestratorService.run_validation(
            request, run, agent_enabled=agent_enabled,
        )
        ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))

        # Update request status based on validation outcome
        status_map = {
            ValidationOverallStatus.PASS: ProcurementRequestStatus.READY,
            ValidationOverallStatus.PASS_WITH_WARNINGS: ProcurementRequestStatus.READY,
            ValidationOverallStatus.REVIEW_REQUIRED: ProcurementRequestStatus.REVIEW_REQUIRED,
            ValidationOverallStatus.FAIL: ProcurementRequestStatus.FAILED,
        }
        new_status = status_map.get(result.overall_status, ProcurementRequestStatus.REVIEW_REQUIRED)
        ProcurementRequestService.update_status(request, new_status)

        _val_result = {
            "status": "completed",
            "run_id": str(run.run_id),
            "run_type": "VALIDATION",
            "overall_status": result.overall_status,
            "completeness_score": result.completeness_score,
        }
        return _val_result

    except Exception as exc:
        logger.exception("Validation run %s failed: %s", run_id, exc)
        try:
            run.refresh_from_db()
            ProcurementEvalAdapter.sync_for_analysis_run(run, trace_id=getattr(run, "trace_id", ""))
        except Exception:
            pass
        _val_result = {"status": "failed", "error": str(exc)}
        return _val_result
    finally:
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace_v, output=_val_result, is_root=True)
            score_trace_safe(
                _lf_trace_id_v,
                "procurement_validation_success",
                1.0 if _val_result.get("status") == "completed" else 0.0,
                span=_lf_trace_v,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Prefill Tasks
# ---------------------------------------------------------------------------
@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("procurement.request_prefill", audit_event="PREFILL_STARTED", entity_type="ProcurementRequest")
def run_request_prefill_task(self, tenant_id: int = None, request_id: int = 0) -> dict:
    """Run OCR + LLM extraction to prefill a ProcurementRequest from an uploaded document."""
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from apps.procurement.models import ProcurementRequest
    from apps.procurement.services.prefill.request_prefill_service import RequestDocumentPrefillService

    # Tenant-scoped query to avoid multi-tenant isolation issues
    qs = ProcurementRequest.objects.select_related("uploaded_document")
    if tenant:
        qs = qs.filter(tenant=tenant)
    proc_request = qs.get(pk=request_id)

    try:
        payload = RequestDocumentPrefillService.run_prefill(proc_request, tenant=tenant)
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
def run_quotation_prefill_task(self, tenant_id: int = None, quotation_id: int = 0) -> dict:
    """Run OCR + LLM extraction to prefill a SupplierQuotation from an uploaded document."""
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from apps.procurement.models import SupplierQuotation
    from apps.procurement.services.prefill.quotation_prefill_service import QuotationDocumentPrefillService

    # Tenant-scoped query to avoid multi-tenant isolation issues
    qs = SupplierQuotation.objects.select_related("uploaded_document", "request")
    if tenant:
        qs = qs.filter(tenant=tenant)
    quotation = qs.get(pk=quotation_id)

    try:
        payload = QuotationDocumentPrefillService.run_prefill(quotation, tenant=tenant)
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
def generate_market_intelligence_task(self, tenant_id: int = None, request_id: int = 0) -> dict:
    """Generate and persist AI market intelligence for a ProcurementRequest.

    Called automatically (with ~20s countdown) when a new HVAC request is created,
    so the market intelligence page shows pre-populated results on the first visit.

    Can also be queued manually via the seed_market_intelligence management command.
    """
    from apps.accounts.models import CompanyProfile
    from apps.procurement.models import ProcurementRequest
    from apps.procurement.services.market_intelligence_service import MarketIntelligenceService

    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None

    try:
        qs = ProcurementRequest.objects.all()
        if tenant:
            qs = qs.filter(tenant=tenant)
        proc_request = qs.get(pk=request_id)
    except ProcurementRequest.DoesNotExist:
        logger.warning("generate_market_intelligence_task: request pk=%s not found", request_id)
        return {"status": "skipped", "reason": "Request not found"}

    # Phase 6: Langfuse root trace
    _lf_trace_mi = None
    _lf_trace_id_mi = str(request_id)
    try:
        from apps.core.langfuse_client import start_trace_safe
        _lf_trace_mi = start_trace_safe(
            _lf_trace_id_mi,
            "procurement_market_intelligence_task",
            metadata={"request_id": request_id, "task_id": str(self.request.id or ""), "tenant_id": tenant_id},
        )
    except Exception:
        pass

    _mi_result: dict = {"status": "failed"}
    try:
        result = MarketIntelligenceService.generate_auto(proc_request, generated_by=None)
        logger.info(
            "generate_market_intelligence_task: completed for pk=%s, %d suggestions",
            request_id, len(result.get("suggestions", [])),
        )
        _mi_result = {
            "status": "completed",
            "request_id": request_id,
            "suggestion_count": len(result.get("suggestions", [])),
        }
        return _mi_result
    except Exception as exc:
        logger.warning(
            "generate_market_intelligence_task: failed for pk=%s: %s", request_id, exc,
        )
        try:
            self.retry(exc=exc)
        except Exception:
            # Catches both MaxRetriesExceededError and celery.exceptions.Retry
            # (the latter propagates in CELERY_TASK_ALWAYS_EAGER dev mode).
            pass
        _mi_result = {"status": "failed", "error": str(exc)}
        return _mi_result
    finally:
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace_mi, output=_mi_result, is_root=True)
            score_trace_safe(
                _lf_trace_id_mi,
                "procurement_market_intel_success",
                1.0 if _mi_result.get("status") == "completed" else 0.0,
                span=_lf_trace_mi,
            )
        except Exception:
            pass
