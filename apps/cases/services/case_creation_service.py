"""
CaseCreationService -- creates an APCase from a document upload or invoice.

Called by the document upload flow right after DocumentUpload is persisted,
BEFORE extraction begins. This ensures a case_id is available for Langfuse
session_id tracking throughout all downstream pipelines.

If case is created pre-extraction (from upload), the invoice FK is null
and is linked later via ``link_invoice_to_case()`` after persistence.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.cases.models import APCase, APCaseStage
from apps.core.enums import (
    CasePriority,
    CaseStageType,
    CaseStatus,
    InvoiceType,
    ProcessingPath,
    SourceChannel,
    StageStatus,
)
from apps.core.decorators import observed_service
from apps.core.metrics import MetricsService

logger = logging.getLogger(__name__)


class CaseCreationService:

    @staticmethod
    @transaction.atomic
    @observed_service("cases.creation.create_from_document_upload", audit_event="CASE_CREATED", entity_type="APCase")
    def create_from_document_upload(upload, uploaded_by=None, source_channel=None, tenant=None) -> APCase:
        """Create an APCase from a DocumentUpload BEFORE extraction.

        The case is created with invoice=None. After extraction persists
        the Invoice, call ``link_invoice_to_case()`` to attach it.

        Args:
            upload: DocumentUpload model instance
            uploaded_by: User who uploaded the document
            source_channel: SourceChannel value (defaults to WEB_UPLOAD)

        Returns:
            Newly created APCase in NEW status, or existing case if one
            is already linked to this upload.
        """
        # Guard: don't create duplicate case for the same upload
        existing = APCase.objects.filter(document_upload=upload, is_active=True).first()
        if existing:
            logger.info(
                "Case %s already exists for upload %s",
                existing.case_number, upload.pk,
            )
            return existing

        case_number = CaseCreationService._generate_case_number()

        case = APCase.objects.create(
            case_number=case_number,
            document_upload=upload,
            invoice=None,
            vendor=None,
            source_channel=source_channel or SourceChannel.WEB_UPLOAD,
            invoice_type=InvoiceType.UNKNOWN,
            processing_path=ProcessingPath.UNRESOLVED,
            status=CaseStatus.NEW,
            current_stage="",
            priority=CasePriority.MEDIUM,
            created_by=uploaded_by,
            tenant=tenant,
        )

        # Create initial intake stage
        APCaseStage.objects.create(
            case=case,
            stage_name=CaseStageType.INTAKE,
            stage_status=StageStatus.PENDING,
            tenant=tenant,
        )

        logger.info(
            "Created AP Case %s for upload %s (pre-extraction)",
            case_number, upload.pk,
        )

        from apps.cases.services.case_activity_service import CaseActivityService
        CaseActivityService.log(
            case, "CASE_CREATED",
            description=f"Case {case_number} created from document upload",
            actor=uploaded_by,
            metadata={"upload_id": upload.pk, "source_channel": str(source_channel or "")},
        )

        return case

    @staticmethod
    @transaction.atomic
    def link_invoice_to_case(case, invoice):
        """Link an Invoice to an existing APCase after extraction persistence.

        Also updates vendor, invoice_type, priority, and extraction_confidence
        from the invoice data.
        """
        if case.invoice_id and case.invoice_id == invoice.pk:
            return  # Already linked

        case.invoice = invoice
        case.vendor = invoice.vendor
        case.invoice_type = CaseCreationService._infer_invoice_type(invoice)
        case.priority = CaseCreationService._assess_priority(invoice)
        case.extraction_confidence = invoice.extraction_confidence

        update_fields = [
            "invoice", "vendor", "invoice_type", "priority",
            "extraction_confidence", "updated_at",
        ]
        case.save(update_fields=update_fields)
        logger.info(
            "Linked invoice %s to case %s",
            invoice.pk, case.case_number,
        )

        from apps.cases.services.case_activity_service import CaseActivityService
        CaseActivityService.log(
            case, "INVOICE_LINKED",
            description=f"Invoice {getattr(invoice, 'invoice_number', invoice.pk)} linked to case",
            metadata={"invoice_id": invoice.pk},
        )

    @staticmethod
    @transaction.atomic
    @observed_service("cases.creation.create_from_upload", audit_event="CASE_CREATED", entity_type="APCase")
    def create_from_upload(invoice, uploaded_by=None, source_channel=None, tenant=None) -> APCase:
        """Create an APCase for an uploaded invoice (backward-compat).

        If a case already exists for this invoice, returns it.
        If a case was pre-created from the DocumentUpload, links the
        invoice and returns that case.

        Args:
            invoice: Invoice model instance
            uploaded_by: User who uploaded the invoice
            source_channel: SourceChannel value (defaults to WEB_UPLOAD)

        Returns:
            APCase linked to the invoice.
        """
        # Guard: don't create duplicate case for the same invoice
        existing = APCase.objects.filter(invoice=invoice, is_active=True).first()
        if existing:
            logger.info("Case %s already exists for invoice %s", existing.case_number, invoice.invoice_number)
            return existing

        # Check if a case was pre-created from the DocumentUpload
        if invoice.document_upload_id:
            upload_case = APCase.objects.filter(
                document_upload_id=invoice.document_upload_id,
                is_active=True,
            ).first()
            if upload_case:
                CaseCreationService.link_invoice_to_case(upload_case, invoice)
                logger.info(
                    "Linked invoice %s to pre-created case %s",
                    invoice.pk, upload_case.case_number,
                )
                return upload_case

        case_number = CaseCreationService._generate_case_number()

        case = APCase.objects.create(
            case_number=case_number,
            document_upload=invoice.document_upload,
            invoice=invoice,
            vendor=invoice.vendor,
            source_channel=source_channel or SourceChannel.WEB_UPLOAD,
            invoice_type=CaseCreationService._infer_invoice_type(invoice),
            processing_path=ProcessingPath.UNRESOLVED,
            status=CaseStatus.NEW,
            current_stage="",
            priority=CaseCreationService._assess_priority(invoice),
            extraction_confidence=invoice.extraction_confidence,
            created_by=uploaded_by,
            tenant=tenant,
        )

        # Create initial intake stage
        APCaseStage.objects.create(
            case=case,
            stage_name=CaseStageType.INTAKE,
            stage_status=StageStatus.PENDING,
            tenant=tenant,
        )

        logger.info("Created AP Case %s for invoice %s", case_number, invoice.invoice_number)
        return case

    @staticmethod
    def _generate_case_number() -> str:
        """Generate a unique case number: AP-YYMMDD-NNNN."""
        today = timezone.now()
        prefix = f"AP-{today.strftime('%y%m%d')}-"
        last_case = (
            APCase.objects.filter(case_number__startswith=prefix)
            .order_by("-case_number")
            .values_list("case_number", flat=True)
            .first()
        )
        if last_case:
            seq = int(last_case.split("-")[-1]) + 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"

    @staticmethod
    def _infer_invoice_type(invoice) -> str:
        """Infer invoice type from extracted PO number."""
        if invoice.po_number and invoice.po_number.strip():
            return InvoiceType.PO_BACKED
        return InvoiceType.UNKNOWN

    @staticmethod
    def _assess_priority(invoice) -> str:
        """Simple priority assessment based on amount."""
        amount = invoice.total_amount or 0
        if amount >= 50000:
            return CasePriority.HIGH
        if amount >= 10000:
            return CasePriority.MEDIUM
        return CasePriority.LOW
