"""Extraction approval service — human-in-the-loop gate post-extraction.

Manages the approval lifecycle:
  PENDING → APPROVED / REJECTED / AUTO_APPROVED

Tracks every field correction for touchless-processing analytics.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    ExtractionApprovalStatus,
    InvoiceStatus,
)
from apps.documents.models import Invoice, InvoiceLineItem
from apps.extraction.models import (
    ExtractionApproval,
    ExtractionFieldCorrection,
    ExtractionResult,
)
from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)

# Fields that humans are allowed to correct
HEADER_FIELDS = {
    "invoice_number", "po_number", "invoice_date", "currency",
    "subtotal", "tax_amount", "total_amount", "raw_vendor_name",
}
LINE_FIELDS = {
    "description", "quantity", "unit_price", "tax_amount", "line_amount",
}


class ExtractionApprovalService:
    """Stateless service for extraction approval lifecycle."""

    # ------------------------------------------------------------------
    # Create approval record
    # ------------------------------------------------------------------
    @classmethod
    @observed_service("extraction.create_pending_approval", entity_type="ExtractionApproval", audit_event="EXTRACTION_APPROVAL_PENDING")
    def create_pending_approval(
        cls,
        invoice: Invoice,
        extraction_result: Optional[ExtractionResult] = None,
    ) -> ExtractionApproval:
        """Create a PENDING approval for a successfully extracted invoice.

        Takes a snapshot of the current extracted values so that later
        corrections can be diffed for analytics.
        """
        snapshot = cls._build_values_snapshot(invoice)

        approval = ExtractionApproval.objects.create(
            invoice=invoice,
            extraction_result=extraction_result,
            status=ExtractionApprovalStatus.PENDING,
            confidence_at_review=invoice.extraction_confidence,
            original_values_snapshot=snapshot,
        )

        logger.info(
            "Created pending extraction approval %s for invoice %s",
            approval.pk, invoice.pk,
        )
        return approval

    # ------------------------------------------------------------------
    # Auto-approve check
    # ------------------------------------------------------------------
    @classmethod
    @observed_service("extraction.try_auto_approve", entity_type="ExtractionApproval")
    def try_auto_approve(
        cls,
        invoice: Invoice,
        extraction_result: Optional[ExtractionResult] = None,
    ) -> Optional[ExtractionApproval]:
        """Auto-approve if confidence meets the configured threshold.

        Returns the ExtractionApproval if auto-approved, else None.
        """
        enabled = getattr(settings, "EXTRACTION_AUTO_APPROVE_ENABLED", False)
        threshold = getattr(settings, "EXTRACTION_AUTO_APPROVE_THRESHOLD", 1.1)

        if not enabled:
            return None

        confidence = invoice.extraction_confidence or 0.0
        if confidence < threshold:
            return None

        snapshot = cls._build_values_snapshot(invoice)

        approval = ExtractionApproval.objects.create(
            invoice=invoice,
            extraction_result=extraction_result,
            status=ExtractionApprovalStatus.AUTO_APPROVED,
            reviewed_at=timezone.now(),
            confidence_at_review=confidence,
            original_values_snapshot=snapshot,
            is_touchless=True,
        )

        # Transition invoice to READY_FOR_RECON
        invoice.status = InvoiceStatus.READY_FOR_RECON
        invoice.save(update_fields=["status", "updated_at"])

        # Audit
        cls._log_audit(
            invoice,
            AuditEventType.EXTRACTION_AUTO_APPROVED,
            f"Extraction auto-approved (confidence {confidence:.0%} >= threshold {threshold:.0%})",
            user=None,
            metadata={"confidence": confidence, "threshold": threshold},
        )

        logger.info(
            "Auto-approved extraction for invoice %s (confidence=%.2f)",
            invoice.pk, confidence,
        )
        return approval

    # ------------------------------------------------------------------
    # Human approve
    # ------------------------------------------------------------------
    @classmethod
    @observed_service("extraction.approve", entity_type="ExtractionApproval", audit_event="EXTRACTION_APPROVED")
    @transaction.atomic
    def approve(
        cls,
        approval: ExtractionApproval,
        user,
        corrections: Optional[dict] = None,
    ) -> ExtractionApproval:
        """Approve an extraction after optional field corrections.

        ``corrections`` format::

            {
                "header": {"field_name": "new_value", ...},
                "lines": [
                    {"pk": 123, "field_name": "new_value", ...},
                    ...
                ]
            }
        """
        if approval.status != ExtractionApprovalStatus.PENDING:
            raise ValueError(f"Approval {approval.pk} is already {approval.status}")

        invoice = approval.invoice

        # ── Duplicate guard ──────────────────────────────────────
        # If this invoice is a duplicate, only allow approval when
        # the original invoice has NOT been approved yet.
        if invoice.is_duplicate and invoice.duplicate_of_id:
            original = Invoice.objects.get(pk=invoice.duplicate_of_id)
            original_approval = ExtractionApproval.objects.filter(
                invoice=original,
                status__in=[ExtractionApprovalStatus.APPROVED, ExtractionApprovalStatus.AUTO_APPROVED],
            ).exists()
            if original_approval:
                raise ValueError(
                    f"Cannot approve: original Invoice #{original.invoice_number} "
                    f"is already approved. Approving this duplicate would risk "
                    f"duplicate payment."
                )
        correction_records = []

        if corrections:
            correction_records = cls._apply_corrections(
                approval, invoice, corrections, user,
            )

        approval.status = ExtractionApprovalStatus.APPROVED
        approval.reviewed_by = user
        approval.reviewed_at = timezone.now()
        approval.fields_corrected_count = len(correction_records)
        approval.is_touchless = len(correction_records) == 0
        approval.save(update_fields=[
            "status", "reviewed_by", "reviewed_at",
            "fields_corrected_count", "is_touchless", "updated_at",
        ])

        # Transition invoice to READY_FOR_RECON
        invoice.status = InvoiceStatus.READY_FOR_RECON
        invoice.save(update_fields=["status", "updated_at"])

        # ── If this was a duplicate, supersede the original ──────
        if invoice.is_duplicate and invoice.duplicate_of_id:
            try:
                original = Invoice.objects.get(pk=invoice.duplicate_of_id)
                old_status = original.status
                original.status = InvoiceStatus.SUPERSEDED
                original.save(update_fields=["status", "updated_at"])
                # Reject original's pending approval if any
                ExtractionApproval.objects.filter(
                    invoice=original,
                    status=ExtractionApprovalStatus.PENDING,
                ).update(
                    status=ExtractionApprovalStatus.REJECTED,
                    reviewed_at=timezone.now(),
                )
                cls._log_audit(
                    original,
                    AuditEventType.EXTRACTION_REJECTED,
                    f"Invoice superseded by duplicate Invoice #{invoice.invoice_number} (approved by {user})",
                    user=user,
                    metadata={
                        "superseded_by_invoice_id": invoice.pk,
                        "previous_status": old_status,
                    },
                )
                logger.info(
                    "Original invoice %s superseded by duplicate %s",
                    original.pk, invoice.pk,
                )
            except Invoice.DoesNotExist:
                logger.warning("Duplicate-of invoice %s not found", invoice.duplicate_of_id)

        event_type = AuditEventType.EXTRACTION_APPROVED
        desc = f"Extraction approved by {user} for Invoice {invoice.invoice_number}"
        if correction_records:
            desc += f" ({len(correction_records)} field(s) corrected)"
            cls._log_audit(
                invoice,
                AuditEventType.EXTRACTION_FIELD_CORRECTED,
                f"{len(correction_records)} field(s) corrected during approval",
                user=user,
                metadata={
                    "corrections": [
                        {
                            "entity_type": c.entity_type,
                            "field": c.field_name,
                            "from": c.original_value,
                            "to": c.corrected_value,
                        }
                        for c in correction_records
                    ],
                },
            )

        cls._log_audit(invoice, event_type, desc, user=user, metadata={
            "approval_id": approval.pk,
            "fields_corrected": len(correction_records),
            "is_touchless": approval.is_touchless,
        })

        logger.info(
            "Extraction approved for invoice %s by %s (corrections=%d, touchless=%s)",
            invoice.pk, user, len(correction_records), approval.is_touchless,
        )

        # ── Governance trail: mirror decision to ExtractionApprovalRecord ──
        cls._record_governance_trail(approval, "APPROVE", user)

        return approval

    # ------------------------------------------------------------------
    # Human reject
    # ------------------------------------------------------------------
    @classmethod
    @observed_service("extraction.reject", entity_type="ExtractionApproval", audit_event="EXTRACTION_REJECTED")
    @transaction.atomic
    def reject(
        cls,
        approval: ExtractionApproval,
        user,
        reason: str = "",
    ) -> ExtractionApproval:
        """Reject an extraction — invoice stays in PENDING_APPROVAL."""
        if approval.status != ExtractionApprovalStatus.PENDING:
            raise ValueError(f"Approval {approval.pk} is already {approval.status}")

        approval.status = ExtractionApprovalStatus.REJECTED
        approval.reviewed_by = user
        approval.reviewed_at = timezone.now()
        approval.rejection_reason = reason
        approval.save(update_fields=[
            "status", "reviewed_by", "reviewed_at",
            "rejection_reason", "updated_at",
        ])

        # Mark invoice as INVALID so it can be re-extracted
        invoice = approval.invoice
        invoice.status = InvoiceStatus.INVALID
        invoice.save(update_fields=["status", "updated_at"])

        cls._log_audit(
            invoice,
            AuditEventType.EXTRACTION_REJECTED,
            f"Extraction rejected by {user}: {reason}",
            user=user,
            metadata={"approval_id": approval.pk, "reason": reason},
        )

        logger.info("Extraction rejected for invoice %s by %s", invoice.pk, user)

        # ── Governance trail: mirror decision to ExtractionApprovalRecord ──
        cls._record_governance_trail(approval, "REJECT", user, reason)

        return approval

    # ------------------------------------------------------------------
    # Analytics helpers
    # ------------------------------------------------------------------
    @classmethod
    def get_approval_analytics(cls) -> dict:
        """Return aggregate metrics for touchless vs human-in-the-loop."""
        from django.db.models import Avg, Count, Q

        qs = ExtractionApproval.objects.all()
        total = qs.count()
        if total == 0:
            return {
                "total": 0,
                "pending": 0,
                "approved": 0,
                "auto_approved": 0,
                "rejected": 0,
                "touchless_count": 0,
                "human_corrected_count": 0,
                "touchless_rate": 0.0,
                "avg_corrections_per_review": 0.0,
                "most_corrected_fields": [],
            }

        stats = qs.aggregate(
            pending=Count("pk", filter=Q(status=ExtractionApprovalStatus.PENDING)),
            approved=Count("pk", filter=Q(status=ExtractionApprovalStatus.APPROVED)),
            auto_approved=Count("pk", filter=Q(status=ExtractionApprovalStatus.AUTO_APPROVED)),
            rejected=Count("pk", filter=Q(status=ExtractionApprovalStatus.REJECTED)),
            touchless=Count("pk", filter=Q(is_touchless=True)),
            avg_corrections=Avg("fields_corrected_count"),
        )

        resolved = stats["approved"] + stats["auto_approved"]
        touchless_rate = stats["touchless"] / resolved if resolved else 0.0

        # Most-corrected fields
        top_fields = (
            ExtractionFieldCorrection.objects
            .values("field_name", "entity_type")
            .annotate(count=Count("pk"))
            .order_by("-count")[:10]
        )

        return {
            "total": total,
            "pending": stats["pending"],
            "approved": stats["approved"],
            "auto_approved": stats["auto_approved"],
            "rejected": stats["rejected"],
            "touchless_count": stats["touchless"],
            "human_corrected_count": resolved - stats["touchless"],
            "touchless_rate": round(touchless_rate, 4),
            "avg_corrections_per_review": round(stats["avg_corrections"] or 0, 2),
            "most_corrected_fields": list(top_fields),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @classmethod
    def _apply_corrections(
        cls,
        approval: ExtractionApproval,
        invoice: Invoice,
        corrections: dict,
        user,
    ) -> list[ExtractionFieldCorrection]:
        """Apply corrections to invoice/line items and create correction records."""
        records: list[ExtractionFieldCorrection] = []

        # Header corrections
        header = corrections.get("header", {})
        update_fields = ["updated_at"]

        for field_name, new_value in header.items():
            if field_name not in HEADER_FIELDS:
                continue
            old_value = str(getattr(invoice, field_name, "") or "")
            new_value_str = str(new_value).strip()

            if old_value == new_value_str:
                continue

            # Apply the correction to the invoice
            if field_name == "invoice_date":
                try:
                    parsed = datetime.strptime(new_value_str, "%Y-%m-%d").date() if new_value_str else None
                    setattr(invoice, field_name, parsed)
                except ValueError:
                    continue
            elif field_name in ("subtotal", "tax_amount", "total_amount"):
                try:
                    setattr(invoice, field_name, Decimal(new_value_str) if new_value_str else None)
                except InvalidOperation:
                    continue
            else:
                setattr(invoice, field_name, new_value_str)

            update_fields.append(field_name)
            records.append(ExtractionFieldCorrection(
                approval=approval,
                entity_type="header",
                entity_id=None,
                field_name=field_name,
                original_value=old_value,
                corrected_value=new_value_str,
                corrected_by=user,
            ))

        if len(update_fields) > 1:
            invoice.save(update_fields=update_fields)

        # Line item corrections
        for line_data in corrections.get("lines", []):
            line_pk = line_data.get("pk")
            if not line_pk:
                continue
            try:
                line_item = InvoiceLineItem.objects.get(pk=line_pk, invoice=invoice)
            except InvoiceLineItem.DoesNotExist:
                continue

            line_update_fields = ["updated_at"]
            for field_name, new_value in line_data.items():
                if field_name == "pk":
                    continue
                if field_name not in LINE_FIELDS:
                    continue
                old_value = str(getattr(line_item, field_name, "") or "")
                new_value_str = str(new_value).strip()

                if old_value == new_value_str:
                    continue

                if field_name in ("quantity", "unit_price"):
                    try:
                        setattr(line_item, field_name, Decimal(new_value_str) if new_value_str else None)
                    except InvalidOperation:
                        continue
                elif field_name in ("tax_amount", "line_amount"):
                    try:
                        setattr(line_item, field_name, Decimal(new_value_str) if new_value_str else None)
                    except InvalidOperation:
                        continue
                else:
                    setattr(line_item, field_name, new_value_str)

                line_update_fields.append(field_name)
                records.append(ExtractionFieldCorrection(
                    approval=approval,
                    entity_type="line_item",
                    entity_id=line_item.pk,
                    field_name=field_name,
                    original_value=old_value,
                    corrected_value=new_value_str,
                    corrected_by=user,
                ))

            if len(line_update_fields) > 1:
                line_item.save(update_fields=line_update_fields)

        if records:
            ExtractionFieldCorrection.objects.bulk_create(records)

        return records

    @staticmethod
    def _build_values_snapshot(invoice: Invoice) -> dict:
        """Build a JSON-serializable snapshot of current invoice values."""
        header = {
            "invoice_number": invoice.invoice_number,
            "po_number": invoice.po_number,
            "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else "",
            "currency": invoice.currency,
            "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else "",
            "tax_amount": str(invoice.tax_amount) if invoice.tax_amount is not None else "",
            "total_amount": str(invoice.total_amount) if invoice.total_amount is not None else "",
            "raw_vendor_name": invoice.raw_vendor_name,
            "extraction_confidence": invoice.extraction_confidence,
        }
        lines = []
        for li in invoice.line_items.order_by("line_number"):
            lines.append({
                "pk": li.pk,
                "line_number": li.line_number,
                "description": li.description,
                "quantity": str(li.quantity) if li.quantity is not None else "",
                "unit_price": str(li.unit_price) if li.unit_price is not None else "",
                "tax_amount": str(li.tax_amount) if li.tax_amount is not None else "",
                "line_amount": str(li.line_amount) if li.line_amount is not None else "",
            })
        return {"header": header, "lines": lines}

    @staticmethod
    def _log_audit(invoice, event_type, description, user=None, metadata=None):
        """Log an audit event."""
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=invoice.pk,
                event_type=event_type,
                description=description,
                user=user,
                metadata=metadata or {},
            )
        except Exception:
            logger.exception("Failed to log audit event for invoice %s", invoice.pk)

    @staticmethod
    def _record_governance_trail(approval, action: str, user, comments: str = ""):
        """Mirror an approval decision to ExtractionApprovalRecord via GovernanceTrailService.

        Silently skips if no ExtractionRun is linked (legacy records).
        """
        try:
            ext_result = approval.extraction_result
            run = getattr(ext_result, "extraction_run", None) if ext_result else None
            if run is None:
                logger.warning(
                    "Skipping governance trail for approval %s — no ExtractionRun linked",
                    approval.pk,
                )
                return
            from apps.extraction_core.services.governance_trail import GovernanceTrailService
            GovernanceTrailService.record_approval_decision(
                run=run, action=action, user=user, comments=comments,
            )
        except Exception:
            logger.exception(
                "GovernanceTrailService.record_approval_decision failed for approval %s",
                approval.pk,
            )
