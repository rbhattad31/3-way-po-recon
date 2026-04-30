"""Celery tasks for the posting pipeline."""
from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from apps.core.decorators import observed_task

logger = logging.getLogger(__name__)


def _mark_direct_import_batch_failed(batch_id: int | None, error_message: str) -> None:
    """Best-effort status update to avoid stale PENDING import batches."""
    if not batch_id:
        return

    from apps.core.enums import ERPReferenceBatchStatus
    from apps.posting_core.models import ERPReferenceImportBatch

    batch = ERPReferenceImportBatch.objects.filter(pk=batch_id).first()
    if not batch:
        return

    batch.status = ERPReferenceBatchStatus.FAILED
    batch.error_summary = (error_message or "Import failed")[:500]
    batch.save(update_fields=["status", "error_summary", "updated_at"])


@shared_task(bind=True, max_retries=2, default_retry_delay=60, acks_late=True)
@observed_task("posting.prepare_posting", audit_event="POSTING_STARTED", entity_type="Invoice")
def prepare_posting_task(self, tenant_id: int | None = None, invoice_id: int = 0, user_id: int | None = None, trigger: str = "system") -> dict:
    """Prepare a posting proposal for a single invoice.

    Called automatically after extraction approval or manually from the UI.
    """
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from django.contrib.auth import get_user_model
    from apps.posting.services.posting_orchestrator import PostingOrchestrator

    User = get_user_model()
    user = None
    if user_id:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            pass

    try:
        posting = PostingOrchestrator.prepare_posting(
            tenant=tenant,
            invoice_id=invoice_id,
            user=user,
            trigger=trigger,
        )
        return {
            "status": "ok",
            "posting_id": posting.pk,
            "posting_status": posting.status,
        }
    except Exception as exc:
        logger.exception("prepare_posting_task failed for invoice %s", invoice_id)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=1, default_retry_delay=30, acks_late=True)
@observed_task("posting.import_reference_excel", audit_event="ERP_REFERENCE_IMPORT_STARTED", entity_type="ERPReferenceImportBatch")
def import_reference_excel_task(
    self,
    tenant_id: int | None = None,
    file_path: str = "",
    batch_type: str = "",
    user_id: int | None = None,
    source_as_of: str | None = None,
    column_map: dict | None = None,
) -> dict:
    """Import ERP reference data from an Excel/CSV file.

    Args:
        file_path: Absolute path to the uploaded file.
        batch_type: One of VENDOR, ITEM, TAX_CODE, COST_CENTER, PO.
        user_id: Uploader user PK.
        source_as_of: ISO date string for the data as-of date.
        column_map: Optional header overrides.
    """
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from datetime import date
    from django.contrib.auth import get_user_model
    from apps.posting_core.services.import_pipeline.excel_import_orchestrator import (
        ExcelImportOrchestrator,
    )

    User = get_user_model()
    user = None
    if user_id:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            pass

    parsed_as_of = None
    if source_as_of:
        try:
            parsed_as_of = date.fromisoformat(source_as_of)
        except ValueError:
            pass

    try:
        batch = ExcelImportOrchestrator.run_import(
            tenant=tenant,
            file_path=file_path,
            batch_type=batch_type,
            user=user,
            source_as_of=parsed_as_of,
            column_map=column_map,
        )
        return {
            "status": "ok",
            "batch_id": batch.pk,
            "valid_row_count": batch.valid_row_count,
            "invalid_row_count": batch.invalid_row_count,
        }
    except Exception as exc:
        logger.exception("import_reference_excel_task failed for %s (%s)", file_path, batch_type)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=1, default_retry_delay=30, acks_late=True)
@observed_task("posting.import_reference_direct", audit_event="ERP_REFERENCE_IMPORT_STARTED", entity_type="ERPReferenceImportBatch")
def import_reference_direct_task(
    self,
    tenant_id: int | None = None,
    batch_type: str = "",
    connector_name: str = "",
    user_id: int | None = None,
    source_as_of: str | None = None,
    batch_id: int | None = None,
) -> dict:
    """Import ERP reference data directly from a live ERP connector.

    Args:
        batch_type: One of VENDOR, ITEM, TAX_CODE, COST_CENTER, OPEN_PO.
        connector_name: Name of the ERPConnection to query.
        user_id: Importing user PK.
        source_as_of: ISO date string for the data as-of date.
    """
    from apps.accounts.models import CompanyProfile
    tenant = CompanyProfile.objects.filter(pk=tenant_id).first() if tenant_id else None
    from datetime import date
    from django.contrib.auth import get_user_model
    from apps.posting_core.services.direct_erp_importer import DirectERPImportOrchestrator

    User = get_user_model()
    user = None
    if user_id:
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            pass

    parsed_as_of = None
    if source_as_of:
        try:
            parsed_as_of = date.fromisoformat(source_as_of)
        except ValueError:
            pass

    try:
        batch = DirectERPImportOrchestrator.run_import(
            batch_type=batch_type,
            connector_name=connector_name,
            tenant=tenant,
            user=user,
            source_as_of=parsed_as_of,
            existing_batch_id=batch_id,
        )
        return {
            "status": "ok",
            "batch_id": batch.pk,
            "valid_row_count": batch.valid_row_count,
            "invalid_row_count": batch.invalid_row_count,
        }
    except ValueError as exc:
        # Known failure (connectivity, bad config) — log as WARNING, no retry.
        _mark_direct_import_batch_failed(batch_id, str(exc))
        logger.warning("import_reference_direct_task failed for %s (%s): %s", connector_name, batch_type, exc)
        raise
    except Exception as exc:
        logger.exception("import_reference_direct_task failed unexpectedly for %s (%s)", connector_name, batch_type)
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            _mark_direct_import_batch_failed(batch_id, str(exc))
            raise
