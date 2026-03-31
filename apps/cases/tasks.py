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

    _trace_id = f"case-{case_id}"
    _lf_trace = None
    try:
        from apps.core.langfuse_client import start_trace
        _lf_trace = start_trace(
            _trace_id,
            "case_task",
            metadata={"task_id": self.request.id, "case_id": case_id, "stage": "full"},
        )
    except Exception:
        pass

    try:
        case = APCase.objects.get(id=case_id)
        orchestrator = CaseOrchestrator(case)
        orchestrator.run()
        logger.info("Case %s processing completed (status=%s)", case.case_number, case.status)
        try:
            from apps.core.langfuse_client import end_span
            if _lf_trace:
                end_span(_lf_trace, output={"status": case.status, "case_number": case.case_number})
        except Exception:
            pass
    except APCase.DoesNotExist:
        logger.error("Case %d not found", case_id)
        try:
            from apps.core.langfuse_client import end_span
            if _lf_trace:
                end_span(_lf_trace, output={"error": "not_found"}, level="ERROR")
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Case %d processing failed", case_id)
        try:
            from apps.core.langfuse_client import end_span
            if _lf_trace:
                end_span(_lf_trace, output={"error": str(exc)[:200]}, level="ERROR")
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=10, acks_late=True)
def reprocess_case_from_stage_task(self, case_id: int, stage: str):
    """Reprocess a case from a specific stage."""
    from apps.cases.models import APCase
    from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

    _trace_id = f"case-{case_id}"
    _lf_trace = None
    try:
        from apps.core.langfuse_client import start_trace
        _lf_trace = start_trace(
            _trace_id,
            "case_task",
            metadata={"task_id": self.request.id, "case_id": case_id, "stage": stage},
        )
    except Exception:
        pass

    try:
        case = APCase.objects.get(id=case_id)
        orchestrator = CaseOrchestrator(case)
        orchestrator.run_from(stage)
        logger.info("Case %s reprocessed from %s (status=%s)", case.case_number, stage, case.status)
        try:
            from apps.core.langfuse_client import end_span
            if _lf_trace:
                end_span(_lf_trace, output={"status": case.status, "stage": stage})
        except Exception:
            pass
    except APCase.DoesNotExist:
        logger.error("Case %d not found", case_id)
        try:
            from apps.core.langfuse_client import end_span
            if _lf_trace:
                end_span(_lf_trace, output={"error": "not_found"}, level="ERROR")
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Case %d reprocessing failed from %s", case_id, stage)
        try:
            from apps.core.langfuse_client import end_span
            if _lf_trace:
                end_span(_lf_trace, output={"error": str(exc)[:200]}, level="ERROR")
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)
