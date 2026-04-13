"""Celery tasks -- agentic pipeline execution."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.core.enums import AgentRunStatus
from apps.core.evaluation_constants import (
    TRACE_AGENT_PIPELINE,
    TRACE_SUPERVISOR_PIPELINE,
    SUPERVISOR_CONFIDENCE,
    SUPERVISOR_RECOMMENDATION_PRESENT,
    SUPERVISOR_TOOLS_USED_COUNT,
    SUPERVISOR_RECOVERY_USED,
    SUPERVISOR_AUTO_CLOSE_CANDIDATE,
)
from apps.core.observability_helpers import build_observability_context, derive_session_id

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def run_agent_pipeline_task(self, tenant_id: int | None = None, reconciliation_result_id: int = 0, actor_user_id: int | None = None) -> dict:
    """Execute the full agentic pipeline for a single ReconciliationResult.

    Args:
        tenant_id: PK of the CompanyProfile (tenant) for this run.
        reconciliation_result_id: PK of the ReconciliationResult to process.
        actor_user_id: PK of the user who triggered the pipeline.
            When ``None`` the system-agent identity is used.
    """
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from apps.agents.services.orchestrator import AgentOrchestrator
    from apps.reconciliation.models import ReconciliationResult

    try:
        qs = ReconciliationResult.objects.select_related(
            "invoice", "invoice__vendor", "purchase_order",
        )
        if tenant:
            qs = qs.filter(tenant=tenant)
        result = qs.get(pk=reconciliation_result_id)
    except ReconciliationResult.DoesNotExist:
        logger.error("ReconciliationResult %s not found", reconciliation_result_id)
        return {"error": f"ReconciliationResult {reconciliation_result_id} not found"}

    # Resolve requesting user (or fall back to system-agent)
    request_user = None
    if actor_user_id:
        from apps.accounts.models import User
        request_user = User.objects.filter(pk=actor_user_id).first()

    orchestrator = AgentOrchestrator()

    # Before executing, attach the Celery task_id to the result's invoice session
    # in Langfuse so the async task boundary is visible alongside the agent_pipeline
    # trace that the orchestrator creates.  This is best-effort and fail-silent.
    try:
        from apps.core.langfuse_client import start_trace, end_span
        _celery_task_id = self.request.id

        # Resolve case_number for session_id linkage
        _case_number = None
        try:
            from apps.cases.models import APCase
            _agent_case = APCase.objects.filter(
                invoice_id=result.invoice_id, is_active=True,
            ).values_list("case_number", flat=True).first()
            _case_number = _agent_case
        except Exception:
            pass

        if _celery_task_id:
            # Strip UUID hyphens to get a valid 32-char hex Langfuse trace ID.
            _lf_wrapper = start_trace(
                _celery_task_id.replace("-", ""),

                TRACE_AGENT_PIPELINE + "_task",
                invoice_id=result.invoice_id,
                user_id=actor_user_id,
                session_id=derive_session_id(
                    case_number=_case_number,
                    invoice_id=result.invoice_id,
                ),
                metadata=build_observability_context(
                    tenant_id=tenant_id,
                    invoice_id=result.invoice_id,
                    reconciliation_result_id=reconciliation_result_id,
                    actor_user_id=actor_user_id,
                    match_status=str(getattr(result, "match_status", "")),
                    reconciliation_mode=getattr(result, "reconciliation_mode", ""),
                    trigger="auto",
                    source="agentic",
                    **{"task_id": _celery_task_id},
                ),
            )
        else:
            _lf_wrapper = None
    except Exception:
        _lf_wrapper = None
    try:
        from apps.core.langfuse_client import set_current_span
        set_current_span(_lf_wrapper)
    except Exception:
        pass

    try:
        outcome = orchestrator.execute(result, request_user=request_user, tenant=tenant)
    except Exception as exc:
        logger.exception("Agent pipeline failed for result %s", reconciliation_result_id)
        try:
            if _lf_wrapper is not None:
                end_span(_lf_wrapper, output={"error": str(exc)[:200]}, level="ERROR")
                _lf_wrapper = None
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)

    try:
        if _lf_wrapper is not None:
            end_span(
                _lf_wrapper,
                output={
                    "agents_executed": outcome.agents_executed,
                    "final_recommendation": outcome.final_recommendation,
                    "skipped": outcome.skipped,
                    "error": outcome.error or None,
                },
            )
    except Exception:
        pass

    return {
        "reconciliation_result_id": reconciliation_result_id,
        "agents_executed": outcome.agents_executed,
        "final_recommendation": outcome.final_recommendation,
        "final_confidence": outcome.final_confidence,
        "skipped": outcome.skipped,
        "skip_reason": outcome.skip_reason,
        "error": outcome.error,
    }


# ---------------------------------------------------------------------------
# Supervisor learning signal helpers
# ---------------------------------------------------------------------------

# Signal type constants (follow extraction/reconciliation adapter naming)
SIG_SUPERVISOR_LOW_CONFIDENCE = "supervisor_low_confidence"
SIG_SUPERVISOR_TOOL_FAILURE = "supervisor_tool_failure"
SIG_SUPERVISOR_RECOVERY_USED = "supervisor_recovery_used"
SIG_SUPERVISOR_FALLBACK_RECOMMENDATION = "supervisor_fallback_recommendation"


def _record_supervisor_signals(agent_run, invoice_id, tenant):
    """Record learning signals from a completed supervisor agent run.

    Signals capture observable patterns that the LearningEngine can use
    to propose corrective actions (prompt tuning, threshold adjustment,
    skill configuration changes).
    """
    from apps.core_eval.services.learning_signal_service import LearningSignalService
    from apps.core_eval.services.eval_run_service import EvalRunService
    from apps.core_eval.models import EvalRun

    output = agent_run.output_payload or {}
    evidence = output.get("evidence") or {}
    status = agent_run.status or ""
    confidence = float(agent_run.confidence or 0)
    tools_used = output.get("tools_used") or []
    rec_type = output.get("recommendation_type") or ""

    # Resolve the EvalRun (created by sync_for_agent_run above)
    eval_run = None
    try:
        eval_run = EvalRun.objects.filter(
            app_module="agents",
            entity_type="AgentRun",
            entity_id=str(agent_run.pk),
        ).first()
    except Exception:
        pass

    # -- Signal 1: Low confidence outcome --
    # Supervisor completed but with low confidence, indicating uncertainty
    if status == "COMPLETED" and confidence < 0.5:
        LearningSignalService.record(
            app_module="agents",
            signal_type=SIG_SUPERVISOR_LOW_CONFIDENCE,
            aggregation_key=f"supervisor::invoice::{invoice_id}",
            confidence=confidence,
            payload_json={
                "agent_run_id": agent_run.pk,
                "invoice_id": invoice_id,
                "recommendation_type": rec_type,
                "tools_used_count": len(tools_used),
            },
            eval_run=eval_run,
            tenant=tenant,
        )

    # -- Signal 2: Tool failures during execution --
    # Check if any tool calls failed (from AgentStep records)
    try:
        from apps.agents.models import AgentStep
        failed_steps = AgentStep.objects.filter(
            agent_run=agent_run,
            success=False,
            action__startswith="tool_call:",
        ).count()
        if failed_steps > 0:
            LearningSignalService.record(
                app_module="agents",
                signal_type=SIG_SUPERVISOR_TOOL_FAILURE,
                aggregation_key=f"supervisor::tool_failures::invoice::{invoice_id}",
                confidence=1.0,
                payload_json={
                    "agent_run_id": agent_run.pk,
                    "invoice_id": invoice_id,
                    "failed_tool_count": failed_steps,
                    "tools_used": tools_used,
                },
                eval_run=eval_run,
                tenant=tenant,
            )
    except Exception:
        logger.debug("Failed to check tool failure steps (non-fatal)", exc_info=True)

    # -- Signal 3: Recovery actions used --
    # Supervisor had to do re-extraction or retry, indicating quality issues
    recovery_actions = evidence.get("recovery_actions") or []
    if recovery_actions:
        LearningSignalService.record(
            app_module="agents",
            signal_type=SIG_SUPERVISOR_RECOVERY_USED,
            aggregation_key=f"supervisor::recovery::invoice::{invoice_id}",
            confidence=confidence,
            payload_json={
                "agent_run_id": agent_run.pk,
                "invoice_id": invoice_id,
                "recovery_actions": recovery_actions,
                "recommendation_type": rec_type,
            },
            eval_run=eval_run,
            tenant=tenant,
        )

    # -- Signal 4: Fallback recommendation used --
    # Supervisor defaulted to SEND_TO_AP_REVIEW because it couldn't decide
    if evidence.get("_recommendation_submitted") is False or (
        rec_type == "SEND_TO_AP_REVIEW" and confidence < 0.4
    ):
        LearningSignalService.record(
            app_module="agents",
            signal_type=SIG_SUPERVISOR_FALLBACK_RECOMMENDATION,
            aggregation_key=f"supervisor::fallback::invoice::{invoice_id}",
            confidence=confidence,
            payload_json={
                "agent_run_id": agent_run.pk,
                "invoice_id": invoice_id,
                "recommendation_type": rec_type,
                "warning": evidence.get("_warning", ""),
            },
            eval_run=eval_run,
            tenant=tenant,
        )


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def run_supervisor_pipeline_task(
    self,
    tenant_id: int | None = None,
    invoice_id: int = 0,
    document_upload_id: int | None = None,
    reconciliation_result_id: int | None = None,
    actor_user_id: int | None = None,
    reconciliation_mode: str = "",
    shadow_mode: bool = True,
) -> dict:
    """Execute the SupervisorAgent for a single invoice.

    This runs the full AP lifecycle (OCR -> extraction -> validation ->
    matching -> investigation -> decision) via a single LLM agent that
    calls deterministic services as tools.

    Args:
        tenant_id: PK of CompanyProfile (tenant).
        invoice_id: PK of the Invoice to process.
        document_upload_id: PK of the DocumentUpload (optional).
        reconciliation_result_id: PK of ReconciliationResult (optional).
        actor_user_id: PK of the triggering user (None = system agent).
        reconciliation_mode: TWO_WAY / THREE_WAY / NON_PO.
        shadow_mode: If True, supervisor observes but does not mutate state.
    """
    from apps.accounts.models import CompanyProfile
    from apps.agents.services.supervisor_agent import SupervisorAgent
    from apps.agents.services.supervisor_context_builder import build_supervisor_context
    from apps.core.enums import AgentRunStatus
    from django.core.cache import cache

    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None

    # Resolve reconciliation result if provided
    recon_result = None
    if reconciliation_result_id:
        from apps.reconciliation.models import ReconciliationResult
        try:
            qs = ReconciliationResult.objects.select_related("invoice", "purchase_order")
            if tenant:
                qs = qs.filter(tenant=tenant)
            recon_result = qs.get(pk=reconciliation_result_id)
            if not invoice_id:
                invoice_id = recon_result.invoice_id
            if not reconciliation_mode:
                reconciliation_mode = getattr(recon_result, "reconciliation_mode", "")
        except ReconciliationResult.DoesNotExist:
            logger.warning("ReconciliationResult %s not found", reconciliation_result_id)

    if not invoice_id:
        logger.error("run_supervisor_pipeline_task: invoice_id is required")
        return {"error": "invoice_id is required"}

    lock_key = f"supervisor:run:{tenant_id or 'global'}:{invoice_id}"
    lock_acquired = cache.add(lock_key, "1", timeout=600)
    if not lock_acquired:
        logger.info(
            "Skipping supervisor run due to active lock tenant=%s invoice=%s",
            tenant_id,
            invoice_id,
        )
        return {
            "invoice_id": invoice_id,
            "status": "SKIPPED",
            "reason": "supervisor_run_in_progress",
            "shadow_mode": shadow_mode,
        }

    # Resolve requesting user
    request_user = None
    if actor_user_id:
        from apps.accounts.models import User
        request_user = User.objects.filter(pk=actor_user_id).first()

    # ── Langfuse: resolve case_number for session linkage ─────────────
    _case_number = None
    _case_id = None
    try:
        from apps.cases.models import APCase
        _case_qs = APCase.objects.filter(
            invoice_id=invoice_id, is_active=True,
        ).values_list("case_number", "pk").first()
        if _case_qs:
            _case_number, _case_id = _case_qs
    except Exception:
        pass

    # Resolve vendor info for metadata
    _vendor_name = None
    _po_number = None
    if recon_result:
        _po_number = (
            recon_result.purchase_order.po_number
            if recon_result.purchase_order else None
        )
        try:
            _vendor_name = str(
                getattr(recon_result.invoice, "vendor_name", "")
                or getattr(recon_result.invoice, "supplier_name", "")
            )[:100]
        except Exception:
            pass

    # ── Langfuse: root trace with session + cross-linking metadata ────
    _lf_trace = None
    _trace_id = None
    try:
        from apps.core.langfuse_client import (
            start_trace, end_span, set_current_span,
            score_trace_safe, end_span_safe,
        )
        _celery_task_id = self.request.id
        if _celery_task_id:
            _trace_id = _celery_task_id.replace("-", "")
            _lf_trace = start_trace(
                _trace_id,
                TRACE_SUPERVISOR_PIPELINE,
                invoice_id=invoice_id,
                user_id=actor_user_id,
                session_id=derive_session_id(
                    case_number=_case_number,
                    invoice_id=invoice_id,
                    document_upload_id=document_upload_id,
                    case_id=_case_id,
                ),
                metadata=build_observability_context(
                    tenant_id=tenant_id,
                    invoice_id=invoice_id,
                    document_upload_id=document_upload_id,
                    reconciliation_result_id=reconciliation_result_id,
                    case_id=_case_id,
                    case_number=_case_number,
                    actor_user_id=actor_user_id,
                    reconciliation_mode=reconciliation_mode,
                    po_number=_po_number,
                    vendor_name=_vendor_name,
                    trigger="supervisor",
                    source="supervisor_agent",
                    match_status=(
                        str(getattr(recon_result, "match_status", ""))
                        if recon_result else None
                    ),
                ),
            )
            set_current_span(_lf_trace)
    except Exception:
        _lf_trace = None

    # Build context
    ctx = build_supervisor_context(
        invoice_id=invoice_id,
        document_upload_id=document_upload_id,
        reconciliation_result=recon_result,
        po_number=_po_number,
        reconciliation_mode=reconciliation_mode,
        actor_user_id=actor_user_id if request_user else None,
        actor_primary_role=(
            getattr(request_user, "role", "") if request_user else "SYSTEM_AGENT"
        ),
        trace_id=self.request.id or "",
        tenant=tenant,
        langfuse_trace=_lf_trace,
    )

    # Execute supervisor
    agent = SupervisorAgent()
    try:
        agent_run = agent.run(ctx)
    except Exception as exc:
        logger.exception(
            "Supervisor pipeline failed for invoice %s", invoice_id,
        )
        try:
            if _lf_trace is not None:
                end_span_safe(
                    _lf_trace,
                    output={"error": str(exc)[:200]},
                    level="ERROR",
                    is_root=True,
                )
                if _trace_id:
                    score_trace_safe(
                        _trace_id, SUPERVISOR_CONFIDENCE, 0.0,
                        comment="pipeline_error", span=_lf_trace,
                    )
                _lf_trace = None
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)
        cache.delete(lock_key)
        return {"error": str(exc)[:200], "invoice_id": invoice_id}

    # -- Eval & Learning integration --
    try:
        from apps.agents.services.eval_adapter import AgentEvalAdapter
        AgentEvalAdapter.sync_for_agent_run(agent_run)
    except Exception:
        logger.debug(
            "AgentEvalAdapter sync failed for supervisor agent_run=%s (non-fatal)",
            getattr(agent_run, "pk", "?"),
            exc_info=True,
        )

    # Record supervisor-specific learning signals
    try:
        _record_supervisor_signals(agent_run, invoice_id, tenant)
    except Exception:
        logger.debug(
            "Supervisor learning signals failed for agent_run=%s (non-fatal)",
            getattr(agent_run, "pk", "?"),
            exc_info=True,
        )

    # Close Langfuse trace -- emit root scores, then close with is_root=True
    _out_payload = agent_run.output_payload or {}
    _final_confidence = float(_out_payload.get("confidence", 0))
    _final_recommendation = _out_payload.get("recommendation_type", "")
    _final_status = str(agent_run.status)
    try:
        if _lf_trace is not None and _trace_id:
            # -- supervisor-specific root scores --
            score_trace_safe(
                _trace_id, SUPERVISOR_CONFIDENCE, _final_confidence,
                comment=f"status={_final_status}",
                span=_lf_trace,
            )
            score_trace_safe(
                _trace_id, SUPERVISOR_RECOMMENDATION_PRESENT,
                1.0 if _final_recommendation else 0.0,
                comment=_final_recommendation or "none",
                span=_lf_trace,
            )
            # tools used count (from AgentStep records or output)
            _tools_count = float(_out_payload.get("tools_used_count", 0))
            if not _tools_count:
                try:
                    from apps.agents.models import AgentStep
                    _tools_count = float(
                        AgentStep.objects.filter(
                            agent_run=agent_run, step_type="TOOL_CALL",
                        ).count()
                    )
                except Exception:
                    _tools_count = 0.0
            score_trace_safe(
                _trace_id, SUPERVISOR_TOOLS_USED_COUNT, _tools_count,
                comment=f"tools_used={int(_tools_count)}",
                span=_lf_trace,
            )
            score_trace_safe(
                _trace_id, SUPERVISOR_RECOVERY_USED,
                1.0 if _out_payload.get("recovery_actions") else 0.0,
                span=_lf_trace,
            )
            score_trace_safe(
                _trace_id, SUPERVISOR_AUTO_CLOSE_CANDIDATE,
                1.0 if _final_recommendation == "AUTO_CLOSE" else 0.0,
                span=_lf_trace,
            )
            # -- close root trace --
            end_span_safe(
                _lf_trace,
                output={
                    "agent_run_id": agent_run.pk,
                    "status": _final_status,
                    "recommendation": _final_recommendation,
                    "confidence": _final_confidence,
                },
                is_root=True,
            )
    except Exception:
        pass

    cache.delete(lock_key)
    return {
        "invoice_id": invoice_id,
        "agent_run_id": agent_run.pk,
        "status": str(agent_run.status),
        "recommendation": (
            agent_run.output_payload.get("recommendation_type", "")
            if agent_run.output_payload else ""
        ),
        "confidence": (
            agent_run.output_payload.get("confidence", 0)
            if agent_run.output_payload else 0
        ),
        "shadow_mode": shadow_mode,
    }
