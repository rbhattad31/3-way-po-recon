"""Celery tasks for bulk extraction intake."""
from __future__ import annotations

import logging

from celery import shared_task

from apps.core.decorators import observed_task

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

    job = BulkExtractionService.run_job(job)

    return {
        "status": job.status,
        "job_id": str(job.job_id),
        "total_found": job.total_found,
        "total_success": job.total_success,
        "total_failed": job.total_failed,
        "total_skipped": job.total_skipped,
        "total_credit_blocked": job.total_credit_blocked,
    }
