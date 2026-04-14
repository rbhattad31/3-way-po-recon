"""Invoice upload service — handles file reception, hashing, and metadata persistence."""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from apps.core.constants import ALLOWED_UPLOAD_EXTENSIONS, MAX_UPLOAD_SIZE_MB
from apps.core.enums import DocumentType, FileProcessingState
from apps.documents.blob_service import build_blob_path, build_blob_url, is_blob_storage_enabled, upload_to_blob
from apps.documents.models import DocumentUpload

from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)


class InvoiceUploadService:
    """Persist an uploaded invoice file and return a DocumentUpload record."""

    @staticmethod
    @observed_service("extraction.upload", entity_type="DocumentUpload", audit_event="INVOICE_UPLOADED")
    def upload(file: UploadedFile, uploaded_by=None, *, tenant=None, document_type: str = DocumentType.INVOICE) -> DocumentUpload:
        """Validate, hash, and save uploaded file.

        Returns the created DocumentUpload instance.
        Raises ValueError on validation failure.
        """
        InvoiceUploadService._validate_file(file)
        if not is_blob_storage_enabled():
            raise ValueError("Azure Blob Storage is not configured. Uploads require blob storage.")
        file_hash = InvoiceUploadService._compute_hash(file)

        doc = DocumentUpload(
            original_filename=file.name,
            file_size=file.size,
            file_hash=file_hash,
            content_type=getattr(file, "content_type", ""),
            document_type=document_type,
            processing_state=FileProcessingState.QUEUED,
            uploaded_by=uploaded_by,
            tenant=tenant,
        )
        doc.save()

        try:
            blob_path = build_blob_path("input", file.name, doc.pk)
            upload_to_blob(file, blob_path, content_type=getattr(file, "content_type", ""))
            doc.blob_path = blob_path
            doc.blob_container = getattr(settings, "AZURE_BLOB_CONTAINER_NAME", "")
            doc.blob_name = blob_path
            doc.blob_url = build_blob_url(blob_path)
            doc.blob_uploaded_at = timezone.now()
            doc.save(update_fields=[
                "blob_path", "blob_container", "blob_name", "blob_url", "blob_uploaded_at", "updated_at",
            ])
        except Exception:
            logger.exception("Invoice upload blob persistence failed for upload_id=%s", doc.pk)
            doc.delete()
            raise

        logger.info("Invoice uploaded: upload_id=%s filename=%s hash=%s", doc.pk, file.name, file_hash)
        return doc

    # ------------------------------------------------------------------
    @staticmethod
    def _validate_file(file: UploadedFile) -> None:
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ValueError(f"Unsupported file type '{ext}'. Allowed: {ALLOWED_UPLOAD_EXTENSIONS}")
        max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if file.size > max_bytes:
            raise ValueError(f"File exceeds maximum size of {MAX_UPLOAD_SIZE_MB} MB")

    @staticmethod
    def _compute_hash(file: UploadedFile) -> str:
        sha = hashlib.sha256()
        for chunk in file.chunks():
            sha.update(chunk)
        file.seek(0)
        return sha.hexdigest()
