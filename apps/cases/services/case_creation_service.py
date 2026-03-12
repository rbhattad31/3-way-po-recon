"""
CaseCreationService — creates an APCase from an invoice upload.

Called by the document upload flow after DocumentUpload is persisted.
Generates a unique case number and initializes the case in NEW status.
"""

import logging
from datetime import datetime

from django.db import transaction

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

logger = logging.getLogger(__name__)


class CaseCreationService:

    @staticmethod
    @transaction.atomic
    def create_from_upload(invoice, uploaded_by=None, source_channel=None) -> APCase:
        """
        Create an APCase for an uploaded invoice.

        Args:
            invoice: Invoice model instance
            uploaded_by: User who uploaded the invoice
            source_channel: SourceChannel value (defaults to WEB_UPLOAD)

        Returns:
            Newly created APCase in NEW status, or existing case if already created.
        """
        # Guard: don't create duplicate case for the same invoice
        existing = APCase.objects.filter(invoice=invoice, is_active=True).first()
        if existing:
            logger.info("Case %s already exists for invoice %s", existing.case_number, invoice.invoice_number)
            return existing

        case_number = CaseCreationService._generate_case_number()

        case = APCase.objects.create(
            case_number=case_number,
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
        )

        # Create initial intake stage
        APCaseStage.objects.create(
            case=case,
            stage_name=CaseStageType.INTAKE,
            stage_status=StageStatus.PENDING,
        )

        logger.info("Created AP Case %s for invoice %s", case_number, invoice.invoice_number)
        return case

    @staticmethod
    def _generate_case_number() -> str:
        """Generate a unique case number: AP-YYMMDD-NNNN."""
        today = datetime.now()
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
