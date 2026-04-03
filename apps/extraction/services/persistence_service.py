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

        # Check for existing invoice on same upload (reprocessing case)
        existing_invoice = (
            Invoice.objects
            .filter(document_upload=upload)
            .order_by("-created_at")
            .first()
        )

        field_values = dict(
            vendor=vendor,
            # Raw
            raw_vendor_name=normalized.raw_vendor_name,
            raw_vendor_tax_id=normalized.raw_vendor_tax_id,
            raw_buyer_name=normalized.raw_buyer_name,
            raw_invoice_number=normalized.raw_invoice_number,
            raw_invoice_date=normalized.raw_invoice_date,
            raw_due_date=normalized.raw_due_date,
            raw_po_number=normalized.raw_po_number,
            raw_currency=normalized.raw_currency,
            raw_subtotal=normalized.raw_subtotal,
            raw_tax_amount=normalized.raw_tax_amount,
            raw_total_amount=normalized.raw_total_amount,
            # Normalized
            invoice_number=normalized.invoice_number,
            normalized_invoice_number=normalized.normalized_invoice_number,
            invoice_date=normalized.invoice_date,
            due_date=normalized.due_date,
            po_number=normalized.po_number,
            normalized_po_number=normalized.normalized_po_number,
            currency=normalized.currency,
            subtotal=normalized.subtotal,
            tax_percentage=normalized.tax_percentage,
            tax_amount=normalized.tax_amount,
            tax_breakdown=normalized.tax_breakdown,
            total_amount=normalized.total_amount,
            vendor_tax_id=normalized.vendor_tax_id,
            buyer_name=normalized.buyer_name,
            # Meta
            status=status,
            extraction_confidence=normalized.confidence,
            extraction_remarks="\n".join(remarks_parts),
            extraction_raw_json=extraction_raw_json,
        )

        if existing_invoice:
            # Update existing invoice in-place
            for attr, value in field_values.items():
                setattr(existing_invoice, attr, value)
            # Reset duplicate flags -- will be re-evaluated below
            existing_invoice.is_duplicate = False
            existing_invoice.duplicate_of_id = None
            invoice = existing_invoice
        else:
            invoice = Invoice(document_upload=upload, **field_values)

        # Duplicate handling
        if duplicate_result and duplicate_result.is_duplicate:
            invoice.is_duplicate = True
            invoice.duplicate_of_id = duplicate_result.duplicate_invoice_id
            invoice.extraction_remarks += f"\nDUPLICATE: {duplicate_result.reason}"

        invoice.save()

        # On reprocess, remove old line items before creating fresh ones
        if existing_invoice:
            InvoiceLineItem.objects.filter(invoice=invoice).delete()

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
                item_category=li.item_category,
                normalized_description=li.normalized_description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                tax_percentage=li.tax_percentage,
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
        sum is GREATER than the extracted total AND the line sum is
        closer to total_amount than the header subtotal.  When line
        items diverge significantly from total_amount, it usually means
        the line amounts were mis-extracted (OCR table misalignment),
        so we keep the original header total.
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

        # Only override when line items sum to MORE than header --
        # this indicates the header was misread or truncated.
        # When line items sum to LESS, the LLM likely missed items.
        if computed_subtotal < stored_subtotal:
            logger.info(
                "Invoice %s: line items sum (%s) < extracted subtotal (%s) -- "
                "keeping header total (likely missing line items)",
                invoice.pk, computed_subtotal, stored_subtotal,
            )
            return

        # Guard: when the delta is large (>10%), check which value is
        # closer to total_amount.  If the header subtotal is closer,
        # the line amounts are likely wrong (OCR table misalignment).
        total = invoice.total_amount or Decimal("0.00")
        if stored_subtotal > 0 and total > 0:
            delta_pct = abs(float(computed_subtotal - stored_subtotal)) / float(stored_subtotal) * 100
            if delta_pct > 10.0:
                header_gap = abs(float(stored_subtotal - total))
                line_gap = abs(float(computed_subtotal - total))
                if header_gap < line_gap:
                    logger.info(
                        "Invoice %s: line sum (%s) diverges %.1f%% from header "
                        "subtotal (%s); header is closer to total (%s) -- "
                        "keeping header value (likely line extraction errors)",
                        invoice.pk, computed_subtotal, delta_pct, stored_subtotal, total,
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
        """Try to match vendor by normalised name, then by VendorAliasMapping."""
        if not normalized_vendor_name:
            return None
        vendor = Vendor.objects.filter(normalized_name=normalized_vendor_name, is_active=True).first()
        if vendor:
            return vendor
        from apps.posting_core.models import VendorAliasMapping
        alias = VendorAliasMapping.objects.filter(
            normalized_alias=normalized_vendor_name, is_active=True
        ).select_related("vendor").first()
        if alias and alias.vendor:
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

        field_vals = dict(
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
            ocr_text=getattr(extraction_response, 'ocr_text', '') or '',
        )

        # Reuse existing ExtractionResult for same upload (reprocessing)
        existing = (
            ExtractionResult.objects
            .filter(document_upload=upload)
            .order_by("-created_at")
            .first()
        )
        if existing:
            for attr, value in field_vals.items():
                setattr(existing, attr, value)
            existing.save()
            return existing

        return ExtractionResult.objects.create(
            document_upload=upload, **field_vals
        )
