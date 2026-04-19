"""Attachment storage and document-pipeline linking service."""
from __future__ import annotations

import hashlib

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.enums import DocumentType
from apps.email_integration.enums import EmailAttachmentProcessingStatus
from apps.email_integration.models import EmailAttachment
from apps.extraction.services.upload_service import InvoiceUploadService


class AttachmentService:
    """Stores normalized attachment metadata and optionally creates DocumentUpload records."""

    @staticmethod
    def _sha256(content_bytes: bytes) -> str:
        return hashlib.sha256(content_bytes or b"").hexdigest()

    @classmethod
    def store_attachments(cls, email_message, attachments, *, tenant=None, uploaded_by=None, trigger_extraction=True):
        saved = []
        for attachment in attachments or []:
            content_bytes = attachment.get("content_bytes") or b""
            file_name = attachment.get("filename") or "attachment.bin"
            content_type = attachment.get("content_type") or "application/octet-stream"
            sha256_hash = cls._sha256(content_bytes) if content_bytes else ""

            email_attachment = EmailAttachment.objects.create(
                tenant=tenant,
                email_message=email_message,
                provider_attachment_id=(attachment.get("provider_attachment_id") or "").strip(),
                filename=file_name,
                content_type=content_type,
                size_bytes=attachment.get("size_bytes") or len(content_bytes),
                sha256_hash=sha256_hash,
                safe_to_process=True,
            )

            if content_bytes:
                linked_upload = cls._create_document_upload(
                    file_name,
                    content_type,
                    content_bytes,
                    tenant=tenant,
                    uploaded_by=uploaded_by,
                )
                if linked_upload is not None:
                    email_attachment.linked_document_upload = linked_upload
                    email_attachment.processing_status = EmailAttachmentProcessingStatus.LINKED
                    email_attachment.save(update_fields=["linked_document_upload", "processing_status", "updated_at"])
                    if trigger_extraction:
                        cls._trigger_extraction(linked_upload, tenant=tenant)
            saved.append(email_attachment)
        return saved

    @staticmethod
    def _create_document_upload(filename: str, content_type: str, content_bytes: bytes, tenant=None, uploaded_by=None):
        try:
            uploaded = SimpleUploadedFile(name=filename, content=content_bytes, content_type=content_type)
            document_type = DocumentType.INVOICE
            filename_lower = (filename or "").lower()
            if "quot" in filename_lower or "rfq" in filename_lower:
                document_type = DocumentType.PROCUREMENT_QUOTATION
            return InvoiceUploadService.upload(uploaded, uploaded_by=uploaded_by, tenant=tenant, document_type=document_type)
        except Exception:
            return None

    @staticmethod
    def _trigger_extraction(linked_upload, *, tenant=None):
        try:
            from apps.extraction.tasks import process_invoice_upload_task

            process_invoice_upload_task.delay(
                tenant_id=getattr(tenant, "pk", None),
                upload_id=linked_upload.pk,
            )
        except Exception:
            return
