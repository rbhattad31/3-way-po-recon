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

from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)


class InvoicePersistenceService:
    """Persist a normalised, validated invoice and its line items."""

    @observed_service("extraction.persist_invoice", entity_type="Invoice", audit_event="INVOICE_PERSISTED")
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

        # Audit: duplicate detection
        if duplicate_result and duplicate_result.is_duplicate:
            self._log_audit(
                invoice, "DUPLICATE_DETECTED",
                f"Duplicate invoice detected: {duplicate_result.reason}",
                {"duplicate_of_id": duplicate_result.duplicate_invoice_id},
            )

        # Audit: vendor resolution
        if vendor:
            self._log_audit(
                invoice, "VENDOR_RESOLVED",
                f"Vendor resolved: {vendor.name} (id={vendor.pk})",
                {"vendor_id": vendor.pk, "vendor_name": vendor.name},
            )

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
    def _log_audit(invoice, event_type, description, metadata=None):
        """Log an audit event for persistence actions."""
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=invoice.pk,
                event_type=event_type,
                description=description,
                metadata=metadata or {},
            )
        except Exception:
            logger.exception("Failed to log audit event for invoice %s", invoice.pk)

    @staticmethod
    def _reconcile_totals(invoice: Invoice, line_objs: list) -> None:
        """Recalculate subtotal/total from line items if they disagree.

        Only trusts line items over the header total when the line-item
        sum is GREATER than the extracted total — this indicates the
        header was misread.  When line items sum to LESS than the header
        total, it usually means the LLM missed some line items, so we
        keep the original header total.
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

        # Only override when line items sum to MORE than header —
        # this indicates the header was misread or truncated.
        # When line items sum to LESS, the LLM likely missed items.
        if computed_subtotal < stored_subtotal:
            logger.info(
                "Invoice %s: line items sum (%s) < extracted subtotal (%s) — "
                "keeping header total (likely missing line items)",
                invoice.pk, computed_subtotal, stored_subtotal,
            )
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
    """Persist extraction-engine-level metadata.

    Sets extraction_run FK when a governed ExtractionRun exists for this
    DocumentUpload, making ExtractionResult point back to the authoritative
    execution record.
    """

    @staticmethod
    @observed_service("extraction.persist_result", entity_type="ExtractionResult", audit_event="EXTRACTION_RESULT_PERSISTED")
    def save(upload: DocumentUpload, invoice: Optional[Invoice], extraction_response) -> ExtractionResult:
        # Resolve the ExtractionRun FK (governed pipeline)
        extraction_run = None
        try:
            from apps.extraction_core.models import ExtractionRun
            extraction_run = (
                ExtractionRun.objects
                .filter(document__document_upload=upload)
                .order_by("-created_at")
                .first()
            )
        except Exception:
            pass

        # Prefer deterministic confidence from invoice over LLM self-report
        confidence = extraction_response.confidence
        if invoice and invoice.extraction_confidence is not None:
            confidence = invoice.extraction_confidence

        return ExtractionResult.objects.create(
            document_upload=upload,
            invoice=invoice,
            extraction_run=extraction_run,
            engine_name=extraction_response.engine_name,
            engine_version=extraction_response.engine_version,
            raw_response=extraction_response.raw_json,
            confidence=confidence,
            duration_ms=extraction_response.duration_ms,
            success=extraction_response.success,
            error_message=extraction_response.error_message,
            agent_run_id=getattr(extraction_response, 'agent_run_id', None),
            ocr_page_count=getattr(extraction_response, 'ocr_page_count', 0),
            ocr_duration_ms=getattr(extraction_response, 'ocr_duration_ms', None),
            ocr_char_count=getattr(extraction_response, 'ocr_char_count', 0),
        )
