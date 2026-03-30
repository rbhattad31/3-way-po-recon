"""Bulk Extraction Processing Service.

Orchestrates the bulk extraction flow:
1. Validate source connection
2. Scan files via adapter
3. Create BulkExtractionItem rows
4. For each eligible file: credit check → register → extract
5. Update job summary counters
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    BulkItemStatus,
    BulkJobStatus,
    DocumentType,
    FileProcessingState,
)
from apps.documents.models import DocumentUpload
from apps.extraction.bulk_models import (
    BulkExtractionItem,
    BulkExtractionJob,
    BulkSourceConnection,
)
from apps.extraction.services.bulk_source_adapters import (
    DiscoveredFile,
    DownloadedFile,
    get_adapter,
)

logger = logging.getLogger(__name__)


class BulkExtractionService:
    """Stateless service orchestrating a single bulk extraction job."""

    # ── Job lifecycle ───────────────────────────────────────

    @classmethod
    def create_job(
        cls,
        source_connection: BulkSourceConnection,
        started_by,
    ) -> BulkExtractionJob:
        """Create a new QUEUED bulk extraction job."""
        job = BulkExtractionJob.objects.create(
            source_connection=source_connection,
            started_by=started_by,
            status=BulkJobStatus.QUEUED,
        )
        cls._audit_job(job, AuditEventType.BULK_JOB_CREATED)
        return job

    @classmethod
    def run_job(cls, job: BulkExtractionJob, lf_trace=None) -> BulkExtractionJob:
        """Execute the full bulk extraction job synchronously.

        1. Scan source
        2. Create item rows
        3. Process each eligible item
        4. Update job summary
        """
        job.status = BulkJobStatus.SCANNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at", "updated_at"])
        cls._audit_job(job, AuditEventType.BULK_JOB_STARTED)

        try:
            # Phase 1: Scan
            adapter = get_adapter(
                job.source_connection.source_type,
                job.source_connection.config_json,
            )
            adapter.lf_trace = lf_trace
            validation = adapter.validate_config()
            if not validation.valid:
                return cls._fail_job(job, f"Source validation failed: {validation.message}")

            discovered_files = adapter.list_files()
            job.total_found = len(discovered_files)
            job.save(update_fields=["total_found", "updated_at"])

            # Phase 2: Create item rows
            items = cls._create_item_rows(job, discovered_files)

            # Phase 3: Process eligible items
            job.status = BulkJobStatus.PROCESSING
            job.save(update_fields=["status", "updated_at"])

            for item in items:
                if item.status != BulkItemStatus.DISCOVERED:
                    continue  # Already marked UNSUPPORTED
                try:
                    cls._process_item(job, item, adapter, lf_parent=lf_trace)
                except Exception as e:
                    logger.exception(
                        "Bulk item %s failed: %s", item.pk, e
                    )
                    item.status = BulkItemStatus.FAILED
                    item.error_message = str(e)[:2000]
                    item.save(update_fields=["status", "error_message", "updated_at"])

            # Phase 4: Finalize
            return cls._finalize_job(job)

        except Exception as e:
            logger.exception("Bulk job %s failed: %s", job.job_id, e)
            return cls._fail_job(job, str(e)[:2000])

    # ── Item creation ───────────────────────────────────────

    @classmethod
    def _create_item_rows(
        cls,
        job: BulkExtractionJob,
        files: list[DiscoveredFile],
    ) -> list[BulkExtractionItem]:
        """Create BulkExtractionItem rows for each discovered file."""
        items = []
        for f in files:
            status = BulkItemStatus.DISCOVERED
            skip_reason = ""
            if not f.is_supported:
                status = BulkItemStatus.UNSUPPORTED
                skip_reason = f"Unsupported file type: {os.path.splitext(f.name)[1]}"

            item = BulkExtractionItem.objects.create(
                job=job,
                source_file_id=f.file_id,
                source_name=f.name,
                source_path=f.path,
                mime_type=f.mime_type,
                file_size=f.size,
                status=status,
                skip_reason=skip_reason,
            )
            items.append(item)
        return items

    # ── Per-item processing ─────────────────────────────────

    @classmethod
    def _process_item(
        cls,
        job: BulkExtractionJob,
        item: BulkExtractionItem,
        adapter,
        lf_parent=None,
    ) -> None:
        """Process a single bulk item: duplicate check -> credit -> upload -> extract."""
        _lf_item_span = None
        try:
            from apps.core.langfuse_client import start_span
            if lf_parent:
                _lf_item_span = start_span(
                    lf_parent,
                    name="bulk_item_extraction",
                    metadata={"file_name": item.source_name, "item_pk": item.pk},
                )
        except Exception:
            pass

        user = job.started_by

        # 1. Duplicate check (same source_file_id in prior items)
        if cls._is_duplicate(item):
            item.status = BulkItemStatus.DUPLICATE
            item.skip_reason = "File already processed in a prior bulk job"
            item.save(update_fields=["status", "skip_reason", "updated_at"])
            cls._audit_item(job, item, AuditEventType.BULK_ITEM_SKIPPED)
            try:
                from apps.core.langfuse_client import end_span
                if _lf_item_span:
                    end_span(_lf_item_span, output={"item_status": item.status}, level="DEFAULT")
            except Exception:
                pass
            return

        # 2. Credit check
        from apps.extraction.services.credit_service import CreditService

        credit_check = CreditService.check_can_reserve(user, credits=1)
        if not credit_check.allowed:
            item.status = BulkItemStatus.CREDIT_BLOCKED
            item.skip_reason = credit_check.message
            item.save(update_fields=["status", "skip_reason", "updated_at"])
            cls._audit_item(job, item, AuditEventType.BULK_ITEM_CREDIT_BLOCKED)
            try:
                from apps.core.langfuse_client import end_span
                if _lf_item_span:
                    end_span(_lf_item_span, output={"item_status": item.status}, level="DEFAULT")
            except Exception:
                pass
            return

        # 3. Download to temp file
        downloaded: Optional[DownloadedFile] = None
        try:
            downloaded = adapter.download_file(
                DiscoveredFile(
                    file_id=item.source_file_id,
                    name=item.source_name,
                    path=item.source_path,
                    mime_type=item.mime_type,
                    size=item.file_size,
                )
            )
        except Exception as e:
            item.status = BulkItemStatus.FAILED
            item.error_message = f"Download failed: {e}"
            item.save(update_fields=["status", "error_message", "updated_at"])
            try:
                from apps.core.langfuse_client import end_span
                if _lf_item_span:
                    end_span(_lf_item_span, output={"item_status": item.status}, level="ERROR")
            except Exception:
                pass
            return

        try:
            # 4. Compute hash + check file-hash duplicate
            file_hash = cls._compute_file_hash(downloaded.local_path)
            existing_upload = DocumentUpload.objects.filter(
                file_hash=file_hash,
            ).first()
            if existing_upload:
                item.status = BulkItemStatus.DUPLICATE
                item.skip_reason = f"File hash matches existing upload #{existing_upload.pk}"
                item.document_upload = existing_upload
                item.save(update_fields=["status", "skip_reason", "document_upload", "updated_at"])
                cls._audit_item(job, item, AuditEventType.BULK_ITEM_SKIPPED)
                return

            # 5. Reserve credit
            reserve_result = CreditService.reserve(
                user,
                credits=1,
                reference_type="bulk_extraction",
                reference_id=f"bulk_{job.pk}_{item.pk}",
                remarks=f"Reserved for bulk item: {item.source_name}",
            )
            if not reserve_result.allowed:
                item.status = BulkItemStatus.CREDIT_BLOCKED
                item.skip_reason = reserve_result.message
                item.save(update_fields=["status", "skip_reason", "updated_at"])
                cls._audit_item(job, item, AuditEventType.BULK_ITEM_CREDIT_BLOCKED)
                return

            # 6. Create DocumentUpload record
            mime_type = downloaded.mime_type or item.mime_type or "application/pdf"
            file_size = downloaded.size or os.path.getsize(downloaded.local_path)

            doc_upload = DocumentUpload.objects.create(
                original_filename=item.source_name,
                file_size=file_size,
                file_hash=file_hash,
                content_type=mime_type,
                document_type=DocumentType.INVOICE,
                processing_state=FileProcessingState.QUEUED,
                uploaded_by=user,
            )
            item.document_upload = doc_upload
            item.status = BulkItemStatus.REGISTERED
            item.save(update_fields=["document_upload", "status", "updated_at"])
            cls._audit_item(job, item, AuditEventType.BULK_ITEM_REGISTERED)

            # 7. Try blob upload
            cls._try_blob_upload(doc_upload, downloaded.local_path, item.source_name, mime_type)

            # 8. Trigger extraction via existing task
            item.status = BulkItemStatus.PROCESSING
            item.save(update_fields=["status", "updated_at"])

            cls._run_extraction(doc_upload)

            # 9. Refresh upload state and update item
            doc_upload.refresh_from_db()
            if doc_upload.processing_state == FileProcessingState.COMPLETED:
                item.status = BulkItemStatus.PROCESSED
            elif doc_upload.processing_state == FileProcessingState.FAILED:
                item.status = BulkItemStatus.FAILED
                item.error_message = doc_upload.processing_message[:2000]
            item.save(update_fields=["status", "error_message", "updated_at"])

            # Link extraction run if available
            cls._link_extraction_run(item, doc_upload)

        finally:
            # Clean up temp file
            if downloaded and downloaded.local_path:
                try:
                    os.unlink(downloaded.local_path)
                except OSError:
                    pass
            try:
                from apps.core.langfuse_client import end_span
                if _lf_item_span:
                    end_span(
                        _lf_item_span,
                        output={
                            "success": item.status == BulkItemStatus.PROCESSED,
                            "item_status": item.status,
                        },
                        level="DEFAULT" if item.status != BulkItemStatus.FAILED else "ERROR",
                    )
            except Exception:
                pass

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _compute_file_hash(file_path: str) -> str:
        """Compute SHA-256 hash of a local file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def _is_duplicate(item: BulkExtractionItem) -> bool:
        """Check if this source_file_id was already processed in a prior bulk item."""
        if not item.source_file_id:
            return False
        return BulkExtractionItem.objects.filter(
            source_file_id=item.source_file_id,
            status__in=[
                BulkItemStatus.REGISTERED,
                BulkItemStatus.PROCESSING,
                BulkItemStatus.PROCESSED,
            ],
        ).exclude(pk=item.pk).exists()

    @staticmethod
    def _try_blob_upload(
        doc_upload: DocumentUpload,
        local_path: str,
        original_name: str,
        content_type: str,
    ) -> None:
        """Attempt Azure Blob upload — non-fatal if not configured."""
        try:
            from apps.documents.blob_service import is_blob_storage_enabled, upload_to_blob, build_blob_path

            if not is_blob_storage_enabled():
                return

            from django.conf import settings as django_settings

            container_name = getattr(django_settings, "AZURE_BLOB_CONTAINER_NAME", "")
            blob_path = build_blob_path("input", original_name, doc_upload.pk)

            with open(local_path, "rb") as fh:
                upload_to_blob(fh, blob_path, content_type=content_type)

            doc_upload.blob_path = blob_path
            doc_upload.blob_container = container_name
            doc_upload.blob_name = blob_path
            doc_upload.blob_uploaded_at = timezone.now()
            doc_upload.save(update_fields=[
                "blob_path", "blob_container", "blob_name",
                "blob_uploaded_at", "updated_at",
            ])
        except Exception as exc:
            logger.warning("Blob upload skipped for bulk item (non-fatal): %s", exc)

    @staticmethod
    def _run_extraction(doc_upload: DocumentUpload) -> None:
        """Trigger the existing extraction pipeline for a DocumentUpload.

        Uses synchronous execution to keep per-item status tracking simple.
        """
        from apps.extraction.tasks import process_invoice_upload_task

        # Run synchronously within the bulk job task to track item-level status
        try:
            process_invoice_upload_task.run(upload_id=doc_upload.pk)
        except Exception as e:
            logger.warning(
                "Extraction failed for upload %s: %s", doc_upload.pk, e,
            )

    @staticmethod
    def _link_extraction_run(item: BulkExtractionItem, doc_upload: DocumentUpload) -> None:
        """Link the extraction run to the bulk item if one was created."""
        try:
            from apps.extraction.models import ExtractionResult

            ext_result = ExtractionResult.objects.filter(
                document_upload=doc_upload,
            ).order_by("-created_at").first()
            if ext_result and ext_result.extraction_run_id:
                item.extraction_run_id = ext_result.extraction_run_id
                item.save(update_fields=["extraction_run_id", "updated_at"])
        except Exception:
            pass

    @classmethod
    def _finalize_job(cls, job: BulkExtractionJob) -> BulkExtractionJob:
        """Compute summary counters and finalize job status."""
        items = job.items.all()
        job.total_registered = items.filter(status__in=[
            BulkItemStatus.REGISTERED, BulkItemStatus.PROCESSING,
            BulkItemStatus.PROCESSED, BulkItemStatus.FAILED,
        ]).count()
        job.total_success = items.filter(status=BulkItemStatus.PROCESSED).count()
        job.total_failed = items.filter(status=BulkItemStatus.FAILED).count()
        job.total_skipped = items.filter(status__in=[
            BulkItemStatus.SKIPPED, BulkItemStatus.UNSUPPORTED,
            BulkItemStatus.DUPLICATE,
        ]).count()
        job.total_credit_blocked = items.filter(
            status=BulkItemStatus.CREDIT_BLOCKED,
        ).count()
        job.completed_at = timezone.now()

        if job.total_failed == 0 and job.total_credit_blocked == 0:
            job.status = BulkJobStatus.COMPLETED
        elif job.total_success > 0:
            job.status = BulkJobStatus.PARTIAL_FAILED
        else:
            job.status = BulkJobStatus.FAILED

        job.save(update_fields=[
            "status", "completed_at",
            "total_registered", "total_success", "total_failed",
            "total_skipped", "total_credit_blocked", "updated_at",
        ])
        event_type = (
            AuditEventType.BULK_JOB_COMPLETED
            if job.status != BulkJobStatus.FAILED
            else AuditEventType.BULK_JOB_FAILED
        )
        cls._audit_job(job, event_type)
        return job

    @classmethod
    def _fail_job(cls, job: BulkExtractionJob, message: str) -> BulkExtractionJob:
        """Mark job as FAILED with error message."""
        job.status = BulkJobStatus.FAILED
        job.error_message = message[:2000]
        job.completed_at = timezone.now()
        job.save(update_fields=[
            "status", "error_message", "completed_at", "updated_at",
        ])
        cls._audit_job(job, AuditEventType.BULK_JOB_FAILED)
        return job

    # ── Audit helpers ───────────────────────────────────────

    @staticmethod
    def _audit_job(job: BulkExtractionJob, event_type) -> None:
        try:
            from apps.auditlog.services import AuditService

            AuditService.log_event(
                entity_type="BulkExtractionJob",
                entity_id=job.pk,
                event_type=event_type,
                description=f"Bulk job {job.job_id}: {event_type}",
                metadata={
                    "job_id": str(job.job_id),
                    "source": job.source_connection.name,
                    "status": job.status,
                    "total_found": job.total_found,
                    "started_by": getattr(job.started_by, "email", ""),
                },
                user=job.started_by,
            )
        except Exception:
            logger.warning("Audit log failed for bulk job %s", job.job_id)

    @staticmethod
    def _audit_item(job: BulkExtractionJob, item: BulkExtractionItem, event_type) -> None:
        try:
            from apps.auditlog.services import AuditService

            AuditService.log_event(
                entity_type="BulkExtractionItem",
                entity_id=item.pk,
                event_type=event_type,
                description=f"Bulk item {item.source_name}: {event_type}",
                metadata={
                    "job_id": str(job.job_id),
                    "item_id": item.pk,
                    "source_name": item.source_name,
                    "status": item.status,
                    "skip_reason": item.skip_reason,
                },
                user=job.started_by,
            )
        except Exception:
            logger.warning("Audit log failed for bulk item %s", item.pk)
