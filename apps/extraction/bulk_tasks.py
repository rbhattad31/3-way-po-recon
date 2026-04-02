"""Celery tasks for bulk extraction intake."""
from __future__ import annotations

import logging

from celery import shared_task

from apps.core.decorators import observed_task
from apps.core.evaluation_constants import EXTRACTION_BULK_JOB_SUCCESS_RATE

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
@observed_task(
    "extraction.run_bulk_job",
    audit_event="BULK_JOB_STARTED",
    entity_type="BulkExtractionJob",
)
def run_bulk_job_task(self, job_id: int) -> dict:
    """Execute a bulk extraction job.

    Scans the configured source, discovers files, and processes
    each eligible file through the existing extraction pipeline.
    """
    from apps.extraction.bulk_models import BulkExtractionJob
    from apps.extraction.services.bulk_service import BulkExtractionService

    try:
        job = BulkExtractionJob.objects.select_related(
            "source_connection", "started_by",
        ).get(pk=job_id)
    except BulkExtractionJob.DoesNotExist:
        logger.error("BulkExtractionJob %s not found", job_id)
        return {"status": "error", "message": f"Job {job_id} not found"}

    import hashlib as _hl
    _trace_id = getattr(job, "trace_id", None) or _hl.md5(f"bulk-job-{job.pk}".encode()).hexdigest()
    _lf_trace = None
    try:
        from apps.core.langfuse_client import start_trace
        _lf_trace = start_trace(
            _trace_id,
            "bulk_extraction_job",
            metadata={
                "task_id": self.request.id,
                "job_pk": job.pk,
                "source_type": getattr(job.source_connection, "source_type", None),
                "total_found": job.total_found,
            },
        )
    except Exception:
        pass

    try:
        job = BulkExtractionService.run_job(job, lf_trace=_lf_trace)
    finally:
        try:
            from apps.core.langfuse_client import end_span, score_trace
            if _lf_trace:
                processed = getattr(job, "total_success", 0)
                total = getattr(job, "total_found", 1) or 1
                score_trace(
                    _trace_id,
                    EXTRACTION_BULK_JOB_SUCCESS_RATE,
                    processed / total,
                    comment=f"processed={processed} total={total}",
                )
                end_span(_lf_trace, output={"status": job.status, "processed": processed})
        except Exception:
            pass

    return {
        "status": job.status,
        "job_id": str(job.job_id),
        "total_found": job.total_found,
        "total_success": job.total_success,
        "total_failed": job.total_failed,
        "total_skipped": job.total_skipped,
        "total_credit_blocked": job.total_credit_blocked,
    }
