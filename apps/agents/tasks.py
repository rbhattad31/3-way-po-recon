"""Celery tasks -- agentic pipeline execution."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.core.enums import AgentRunStatus
from apps.core.evaluation_constants import TRACE_AGENT_PIPELINE
from apps.core.observability_helpers import build_observability_context, derive_session_id

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def run_agent_pipeline_task(self, reconciliation_result_id: int, actor_user_id: int | None = None) -> dict:
    """Execute the full agentic pipeline for a single ReconciliationResult.

    Args:
        reconciliation_result_id: PK of the ReconciliationResult to process.
        actor_user_id: PK of the user who triggered the pipeline.
            When ``None`` the system-agent identity is used.
    """
    from apps.agents.services.orchestrator import AgentOrchestrator
    from apps.reconciliation.models import ReconciliationResult

    try:
        result = ReconciliationResult.objects.select_related(
            "invoice", "invoice__vendor", "purchase_order",
        ).get(pk=reconciliation_result_id)
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
        outcome = orchestrator.execute(result, request_user=request_user)
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
