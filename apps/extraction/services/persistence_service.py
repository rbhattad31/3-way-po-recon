"""Invoice persistence service — saves normalised invoice data to the database."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.db import transaction

from apps.core.enums import InvoiceStatus
from apps.core.utils import normalize_string
from apps.documents.models import DocumentUpload, Invoice, InvoiceLineItem
from apps.extraction.models import ExtractionResult
from apps.extraction.services.normalization_service import NormalizedInvoice
from apps.extraction.services.duplicate_detection_service import DuplicateCheckResult
from apps.extraction.services.validation_service import ValidationResult
from apps.vendors.models import Vendor

logger = logging.getLogger(__name__)


class InvoicePersistenceService:
    """Persist a normalised, validated invoice and its line items."""

    @transaction.atomic
    def save(
        self,
        normalized: NormalizedInvoice,
        upload: DocumentUpload,
        extraction_raw_json: dict | None = None,
        validation_result: ValidationResult | None = None,
        duplicate_result: DuplicateCheckResult | None = None,
    ) -> Invoice:
        vendor = self._resolve_vendor(normalized.vendor_name_normalized)

        status = InvoiceStatus.EXTRACTED
        if validation_result and not validation_result.is_valid:
            status = InvoiceStatus.INVALID
        elif validation_result:
            status = InvoiceStatus.VALIDATED

        remarks_parts = []
        if validation_result:
            for issue in validation_result.issues:
                remarks_parts.append(f"[{issue.severity}] {issue.field}: {issue.message}")

        invoice = Invoice(
            document_upload=upload,
            vendor=vendor,
            # Raw
            raw_vendor_name=normalized.raw_vendor_name,
            raw_invoice_number=normalized.raw_invoice_number,
            raw_invoice_date=normalized.raw_invoice_date,
            raw_po_number=normalized.raw_po_number,
            raw_currency=normalized.raw_currency,
            raw_subtotal=normalized.raw_subtotal,
            raw_tax_amount=normalized.raw_tax_amount,
            raw_total_amount=normalized.raw_total_amount,
            # Normalized
            invoice_number=normalized.invoice_number,
            normalized_invoice_number=normalized.normalized_invoice_number,
            invoice_date=normalized.invoice_date,
            po_number=normalized.po_number,
            normalized_po_number=normalized.normalized_po_number,
            currency=normalized.currency,
            subtotal=normalized.subtotal,
            tax_amount=normalized.tax_amount,
            total_amount=normalized.total_amount,
            # Meta
            status=status,
            extraction_confidence=normalized.confidence,
            extraction_remarks="\n".join(remarks_parts),
            extraction_raw_json=extraction_raw_json,
        )

        # Duplicate handling
        if duplicate_result and duplicate_result.is_duplicate:
            invoice.is_duplicate = True
            invoice.duplicate_of_id = duplicate_result.duplicate_invoice_id
            invoice.extraction_remarks += f"\nDUPLICATE: {duplicate_result.reason}"

        invoice.save()

        # Line items
        line_objs = []
        for li in normalized.line_items:
            line_objs.append(InvoiceLineItem(
                invoice=invoice,
                line_number=li.line_number,
                raw_description=li.raw_description,
                raw_quantity=li.raw_quantity,
                raw_unit_price=li.raw_unit_price,
                raw_tax_amount=li.raw_tax_amount,
                raw_line_amount=li.raw_line_amount,
                description=li.description,
                normalized_description=li.normalized_description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                tax_amount=li.tax_amount,
                line_amount=li.line_amount,
            ))
        if line_objs:
            InvoiceLineItem.objects.bulk_create(line_objs)

        # Recalculate subtotal/total from line items when they disagree
        self._reconcile_totals(invoice, line_objs)

        logger.info("Invoice saved: id=%s number=%s lines=%d status=%s", invoice.pk, invoice.invoice_number, len(line_objs), status)
        return invoice

    @staticmethod
    def _reconcile_totals(invoice: Invoice, line_objs: list) -> None:
        """Recalculate subtotal/total from line items if they disagree.

        OCR/LLM extraction sometimes misreads header totals while
        extracting line items correctly.  When the sum of line_amount
        values differs from the extracted subtotal, trust the line
        items and recompute subtotal and total_amount.
        """
        if not line_objs:
            return

        computed_subtotal = sum(
            (li.line_amount for li in line_objs if li.line_amount is not None),
            Decimal("0.00"),
        )
        if computed_subtotal == Decimal("0.00"):
            return

        stored_subtotal = invoice.subtotal or Decimal("0.00")
        if stored_subtotal == computed_subtotal:
            return

        tax = invoice.tax_amount or Decimal("0.00")
        new_total = computed_subtotal + tax

        logger.info(
            "Invoice %s: recalculating subtotal from lines "
            "(extracted=%s, computed=%s, new_total=%s)",
            invoice.pk, stored_subtotal, computed_subtotal, new_total,
        )
        invoice.subtotal = computed_subtotal
        invoice.total_amount = new_total
        invoice.save(update_fields=["subtotal", "total_amount", "updated_at"])

    @staticmethod
    def _resolve_vendor(normalized_vendor_name: str) -> Optional[Vendor]:
        """Try to match vendor by normalised name, then by alias."""
        if not normalized_vendor_name:
            return None
        vendor = Vendor.objects.filter(normalized_name=normalized_vendor_name, is_active=True).first()
        if vendor:
            return vendor
        # Check aliases
        from apps.vendors.models import VendorAlias
        alias = VendorAlias.objects.filter(normalized_alias=normalized_vendor_name).select_related("vendor").first()
        if alias:
            return alias.vendor
        return None


class ExtractionResultPersistenceService:
    """Persist extraction-engine-level metadata."""

    @staticmethod
    def save(upload: DocumentUpload, invoice: Optional[Invoice], extraction_response) -> ExtractionResult:
        return ExtractionResult.objects.create(
            document_upload=upload,
            invoice=invoice,
            engine_name=extraction_response.engine_name,
            engine_version=extraction_response.engine_version,
            raw_response=extraction_response.raw_json,
            confidence=extraction_response.confidence,
            duration_ms=extraction_response.duration_ms,
            success=extraction_response.success,
            error_message=extraction_response.error_message,
        )
