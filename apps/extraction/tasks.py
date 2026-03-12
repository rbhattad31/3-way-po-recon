"""Celery tasks for the extraction pipeline."""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.core.enums import FileProcessingState, InvoiceStatus
from apps.documents.models import DocumentUpload

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_invoice_upload_task(self, upload_id: int) -> dict:
    """End-to-end extraction pipeline for a single DocumentUpload.

    Steps executed sequentially:
      1. Extract raw data (adapter)
      2. Parse into structured dataclass
      3. Normalise fields
      4. Validate mandatory fields & thresholds
      5. Duplicate detection
      6. Persist invoice + line items + extraction result
      7. Transition upload & invoice status
    """
    from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter, ExtractionResponse
    from apps.extraction.services.parser_service import ExtractionParserService
    from apps.extraction.services.normalization_service import NormalizationService
    from apps.extraction.services.validation_service import ValidationService
    from apps.extraction.services.duplicate_detection_service import DuplicateDetectionService
    from apps.extraction.services.persistence_service import (
        InvoicePersistenceService,
        ExtractionResultPersistenceService,
    )

    try:
        upload = DocumentUpload.objects.get(pk=upload_id)
    except DocumentUpload.DoesNotExist:
        logger.error("DocumentUpload %s not found", upload_id)
        return {"status": "error", "message": f"Upload {upload_id} not found"}

    upload.processing_state = FileProcessingState.PROCESSING
    upload.save(update_fields=["processing_state", "updated_at"])

    try:
        # 1. Extract
        adapter = InvoiceExtractionAdapter()
        # Download from Azure Blob Storage
        if not upload.blob_path:
            _fail_upload(upload, "No blob_path set — document not in Azure Blob Storage")
            return {"status": "error", "message": "No blob_path set on upload"}

        from apps.documents.blob_service import download_blob_to_tempfile
        file_path = download_blob_to_tempfile(upload.blob_path)

        try:
            extraction_resp: ExtractionResponse = adapter.extract(file_path)
        finally:
            import os
            try:
                os.unlink(file_path)
            except OSError:
                pass

        if not extraction_resp.success:
            _fail_upload(upload, extraction_resp.error_message)
            ExtractionResultPersistenceService.save(upload, None, extraction_resp)
            return {"status": "error", "message": extraction_resp.error_message}

        # 2. Parse
        parser = ExtractionParserService()
        parsed = parser.parse(extraction_resp.raw_json)

        # 3. Normalise
        normalizer = NormalizationService()
        normalized = normalizer.normalize(parsed)

        # 4. Validate
        validator = ValidationService()
        validation_result = validator.validate(normalized)

        # 5. Duplicate check
        dup_service = DuplicateDetectionService()
        dup_result = dup_service.check(normalized)

        # 6. Persist
        persistence = InvoicePersistenceService()
        invoice = persistence.save(
            normalized=normalized,
            upload=upload,
            extraction_raw_json=extraction_resp.raw_json,
            validation_result=validation_result,
            duplicate_result=dup_result,
        )
        ExtractionResultPersistenceService.save(upload, invoice, extraction_resp)

        # 7. Finalise upload state
        upload.processing_state = FileProcessingState.COMPLETED
        upload.save(update_fields=["processing_state", "updated_at"])

        # Move blob from input/ to processed/
        if upload.blob_path and upload.blob_path.startswith("input/"):
            try:
                from apps.documents.blob_service import move_blob
                new_path = upload.blob_path.replace("input/", "processed/", 1)
                move_blob(upload.blob_path, new_path)
                upload.blob_path = new_path
                upload.save(update_fields=["blob_path", "updated_at"])
            except Exception as mv_err:
                logger.warning("Blob move to processed/ failed: %s", mv_err)

        # If valid and not duplicate, mark ready for reconciliation
        if validation_result.is_valid and not dup_result.is_duplicate:
            invoice.status = InvoiceStatus.READY_FOR_RECON
            invoice.save(update_fields=["status", "updated_at"])

        # Audit: extraction completed
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=invoice.pk,
            event_type=AuditEventType.EXTRACTION_COMPLETED,
            description=f"Extraction completed for invoice {invoice.invoice_number} (confidence: {invoice.extraction_confidence})",
            metadata={"upload_id": upload_id, "is_duplicate": dup_result.is_duplicate, "is_valid": validation_result.is_valid},
        )

        # --- Auto-create AP Case and trigger case processing ---
        case_id = None
        if validation_result.is_valid and not dup_result.is_duplicate:
            try:
                from apps.cases.services.case_creation_service import CaseCreationService
                case = CaseCreationService.create_from_upload(
                    invoice=invoice,
                    uploaded_by=upload.uploaded_by,
                )
                case_id = case.pk
                logger.info("Created AP Case %s for invoice %s", case.case_number, invoice.invoice_number)

                # Trigger case orchestration
                from apps.cases.tasks import process_case_task
                from apps.core.utils import dispatch_task
                dispatch_task(process_case_task, case_id=case.pk)
            except Exception as case_exc:
                logger.exception(
                    "AP Case creation/processing failed for invoice %s: %s",
                    invoice.pk, case_exc,
                )

        logger.info(
            "Extraction pipeline completed for upload %s -> invoice %s (status=%s)",
            upload_id, invoice.pk, invoice.status,
        )
        return {
            "status": "ok",
            "upload_id": upload_id,
            "invoice_id": invoice.pk,
            "invoice_status": invoice.status,
            "is_duplicate": dup_result.is_duplicate,
            "is_valid": validation_result.is_valid,
            "case_id": case_id,
        }

    except Exception as exc:
        logger.exception("Extraction pipeline failed for upload %s", upload_id)
        _fail_upload(upload, str(exc))
        # Audit: extraction failed
        try:
            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType
            AuditService.log_event(
                entity_type="DocumentUpload",
                entity_id=upload_id,
                event_type=AuditEventType.EXTRACTION_FAILED,
                description=f"Extraction failed: {str(exc)[:200]}",
                metadata={"error": str(exc)[:500]},
            )
        except Exception:
            pass
        try:
            raise self.retry(exc=exc)
        except (AttributeError, TypeError):
            # Running outside Celery context (sync fallback) — re-raise directly
            raise exc


def _fail_upload(upload: DocumentUpload, message: str) -> None:
    upload.processing_state = FileProcessingState.FAILED
    upload.processing_message = message[:2000]
    upload.save(update_fields=["processing_state", "processing_message", "updated_at"])

    # Move blob from input/ to exception/
    if upload.blob_path and upload.blob_path.startswith("input/"):
        try:
            from apps.documents.blob_service import move_blob
            new_path = upload.blob_path.replace("input/", "exception/", 1)
            move_blob(upload.blob_path, new_path)
            upload.blob_path = new_path
            upload.save(update_fields=["blob_path", "updated_at"])
        except Exception as mv_err:
            logger.warning("Blob move to exception/ failed: %s", mv_err)
