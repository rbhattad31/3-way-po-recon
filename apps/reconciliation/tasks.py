"""Celery tasks for the reconciliation engine."""
from __future__ import annotations

import logging
from typing import List, Optional

from celery import shared_task
from django.utils import timezone

from apps.core.enums import InvoiceStatus, ReconciliationRunStatus
from apps.documents.models import Invoice
from apps.reconciliation.models import ReconciliationConfig, ReconciliationRun

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def run_reconciliation_task(
    self,
    invoice_ids: Optional[List[int]] = None,
    config_id: Optional[int] = None,
    triggered_by_id: Optional[int] = None,
) -> dict:
    """Execute a full reconciliation run as a Celery task.

    Args:
        invoice_ids: Specific invoice PKs to reconcile.
                     If None, all READY_FOR_RECON invoices are processed.
        config_id: ReconciliationConfig PK.  Falls back to the default config.
        triggered_by_id: User PK of the person who triggered the run.
    """
    from apps.reconciliation.services.runner_service import ReconciliationRunnerService

    # Resolve config
    config = None
    if config_id:
        config = ReconciliationConfig.objects.filter(pk=config_id).first()

    # Resolve user
    triggered_by = None
    if triggered_by_id:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        triggered_by = User.objects.filter(pk=triggered_by_id).first()

    # Resolve invoices
    invoices = None
    if invoice_ids:
        invoices = list(
            Invoice.objects.filter(pk__in=invoice_ids)
            .select_related("vendor", "document_upload")
        )
        if not invoices:
            return {"status": "error", "message": "No matching invoices found"}

    runner = ReconciliationRunnerService(config=config)

    # Open a task-level Langfuse root trace BEFORE the runner executes so that
    # the runner can create its "reconciliation_run" span as a child of this
    # trace, giving the correct hierarchy in Langfuse:
    #   reconciliation_task (task root)
    #     -- reconciliation_run (service span)
    #          -- recon_mode_resolution (per invoice)
    #          -- recon_matching        (per invoice)
    #          -- recon_result_persist  (per invoice)
    #          -- recon_exception_build (per invoice)
    _lf_task_trace = None
    _lf_task_trace_id = f"recon-task-{self.request.id}" if self.request.id else None
    try:
        from apps.core.langfuse_client import start_trace
        if _lf_task_trace_id:
            _lf_task_trace = start_trace(
                _lf_task_trace_id,
                "reconciliation_task",
                user_id=triggered_by.pk if triggered_by else None,
                metadata={
                    "task_id": self.request.id,
                    "invoice_count": len(invoices) if invoices else "all",
                    "config_id": config_id,
                    "triggered_by_id": triggered_by_id,
                },
            )
    except Exception:
        pass

    run = None
    try:
        run = runner.run(invoices=invoices, triggered_by=triggered_by, lf_trace=_lf_task_trace)
    except Exception as exc:
        logger.exception("Reconciliation task failed")
        try:
            from apps.core.langfuse_client import end_span
            end_span(_lf_task_trace, output={"error": str(exc)[:200]}, level="ERROR")
            _lf_task_trace = None
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)
    finally:
        try:
            if _lf_task_trace is not None:
                from apps.core.langfuse_client import end_span
                end_span(
                    _lf_task_trace,
                    output={
                        "run_pk": run.pk if run else None,
                        "run_status": run.status if run else "error",
                        "total_invoices": run.total_invoices if run else 0,
                    },
                )
        except Exception:
            pass

    # Chain agent pipeline for non-matched results
    from apps.agents.tasks import run_agent_pipeline_task
    from apps.reconciliation.models import ReconciliationResult

    agent_result_ids = list(
        ReconciliationResult.objects.filter(run=run)
        .exclude(match_status="MATCHED")
        .values_list("pk", flat=True)
    )
    from apps.core.utils import dispatch_task
    actor_id = triggered_by.pk if triggered_by else None
    for result_id in agent_result_ids:
        dispatch_task(run_agent_pipeline_task, result_id, actor_id)

    return {
        "status": "ok",
        "run_id": run.pk,
        "total_invoices": run.total_invoices,
        "matched": run.matched_count,
        "partial": run.partial_count,
        "unmatched": run.unmatched_count,
        "errors": run.error_count,
        "review": run.review_count,
        "agent_tasks_dispatched": len(agent_result_ids),
    }


@shared_task
def reconcile_single_invoice_task(invoice_id: int, config_id: Optional[int] = None) -> dict:
    """Reconcile a single invoice (convenience wrapper)."""
    return run_reconciliation_task.apply(
        args=([invoice_id], config_id, None)
    ).get()
