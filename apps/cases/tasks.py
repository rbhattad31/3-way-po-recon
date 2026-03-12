"""Celery tasks for asynchronous case processing."""

from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, acks_late=True)
def process_case_task(self, case_id: int):
    """
    Run the CaseOrchestrator for an APCase asynchronously.

    Called after invoice upload + extraction, or when reprocessing.
    """
    from apps.cases.models import APCase
    from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

    try:
        case = APCase.objects.get(id=case_id)
        orchestrator = CaseOrchestrator(case)
        orchestrator.run()
        logger.info("Case %s processing completed (status=%s)", case.case_number, case.status)
    except APCase.DoesNotExist:
        logger.error("Case %d not found", case_id)
    except Exception as exc:
        logger.exception("Case %d processing failed", case_id)
        from apps.core.utils import safe_retry
        safe_retry(self, exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=10, acks_late=True)
def reprocess_case_from_stage_task(self, case_id: int, stage: str):
    """Reprocess a case from a specific stage."""
    from apps.cases.models import APCase
    from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

    try:
        case = APCase.objects.get(id=case_id)
        orchestrator = CaseOrchestrator(case)
        orchestrator.run_from(stage)
        logger.info("Case %s reprocessed from %s (status=%s)", case.case_number, stage, case.status)
    except APCase.DoesNotExist:
        logger.error("Case %d not found", case_id)
    except Exception as exc:
        logger.exception("Case %d reprocessing failed from %s", case_id, stage)
        from apps.core.utils import safe_retry
        safe_retry(self, exc)
