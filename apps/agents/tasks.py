"""Celery tasks — agentic pipeline execution."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.core.enums import AgentRunStatus

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def run_agent_pipeline_task(self, reconciliation_result_id: int) -> dict:
    """Execute the full agentic pipeline for a single ReconciliationResult.

    Meant to be chained after ``run_reconciliation_task``::

        chain(
            run_reconciliation_task.s(invoice_id),
            run_agent_pipeline_task.s(),
        ).apply_async()
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

    orchestrator = AgentOrchestrator()

    try:
        outcome = orchestrator.execute(result)
    except Exception as exc:
        logger.exception("Agent pipeline failed for result %s", reconciliation_result_id)
        from apps.core.utils import safe_retry
        safe_retry(self, exc)

    return {
        "reconciliation_result_id": reconciliation_result_id,
        "agents_executed": outcome.agents_executed,
        "final_recommendation": outcome.final_recommendation,
        "final_confidence": outcome.final_confidence,
        "skipped": outcome.skipped,
        "skip_reason": outcome.skip_reason,
        "error": outcome.error,
    }
