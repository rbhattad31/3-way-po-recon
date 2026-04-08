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
from apps.core.evaluation_constants import (
    EXTRACTION_APPROVAL_CONFIDENCE,
    EXTRACTION_APPROVAL_DECISION,
    EXTRACTION_AUTO_APPROVE_CONFIDENCE,
    EXTRACTION_CORRECTIONS_COUNT,
)

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
        """Create (or reset) a PENDING approval for a successfully extracted invoice.

        On reprocessing the invoice already has an ExtractionApproval.  Rather
        than raising an IntegrityError, we update the existing record back to
        PENDING so the reviewer sees fresh data.
        """
        snapshot = cls._build_values_snapshot(invoice)

        approval, created = ExtractionApproval.objects.update_or_create(
            invoice=invoice,
            defaults={
                "extraction_result": extraction_result,
                "status": ExtractionApprovalStatus.PENDING,
                "confidence_at_review": invoice.extraction_confidence,
                "original_values_snapshot": snapshot,
                # Reset review metadata so it looks fresh
                "reviewed_by": None,
                "reviewed_at": None,
                "is_touchless": False,
                "tenant": getattr(invoice, 'tenant', None),
            },
        )

        action = "Created" if created else "Reset"
        logger.info(
            "%s pending extraction approval %s for invoice %s",
            action, approval.pk, invoice.pk,
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
        lf_trace_id: Optional[str] = None,
        lf_span=None,
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

        approval, created = ExtractionApproval.objects.update_or_create(
            invoice=invoice,
            defaults={
                "extraction_result": extraction_result,
                "status": ExtractionApprovalStatus.AUTO_APPROVED,
                "reviewed_at": timezone.now(),
                "confidence_at_review": confidence,
                "original_values_snapshot": snapshot,
                "is_touchless": True,
                "reviewed_by": None,
                "tenant": getattr(invoice, 'tenant', None),
            },
        )

        # Transition invoice to READY_FOR_RECON
        invoice.status = InvoiceStatus.READY_FOR_RECON

        # Re-attempt vendor resolution in case vendor was created after extraction
        cls._try_resolve_vendor(invoice)

        invoice.save(update_fields=["status", "vendor", "updated_at"])

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
        # ── Create AP Case and trigger case pipeline ──
        cls._ensure_case_and_process(invoice, user=None)

        # ── core_eval: persist auto-approval learning signal ──
        try:
            from apps.extraction.services.eval_adapter import ExtractionEvalAdapter
            ExtractionEvalAdapter.sync_for_approval(approval, user=None)
        except Exception:
            logger.debug("core_eval auto-approve sync failed (non-fatal)")

        try:
            from apps.core.langfuse_client import score_trace
            _score_tid = lf_trace_id or f"approval-{approval.pk}"
            score_trace(
                _score_tid,
                EXTRACTION_AUTO_APPROVE_CONFIDENCE,
                float(confidence),
                span=lf_span,
                comment=(
                    f"invoice={invoice.pk} "
                    f"threshold={threshold:.2f} "
                    f"touchless=True"
                ),
            )
        except Exception:
            pass

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
        lf_trace_id: Optional[str] = None,
        lf_span=None,
    ) -> ExtractionApproval:
        """Approve an extraction after optional field corrections.

        Concurrency: Locks the ExtractionApproval row via select_for_update()
        inside the atomic block.  Only PENDING → APPROVED is allowed.
        Simultaneous approve/reject calls will serialize on the row lock;
        the second caller will see a non-PENDING status and raise ValueError.

        ``corrections`` format::

            {
                "header": {"field_name": "new_value", ...},
                "lines": [
                    {"pk": 123, "field_name": "new_value", ...},
                    ...
                ]
            }
        """
        # Lock the row to prevent concurrent approve/reject/reprocess
        approval = (
            ExtractionApproval.objects
            .select_for_update()
            .get(pk=approval.pk)
        )
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

        # Also pick up any pre-existing corrections saved before the
        # approval click (user edits fields then clicks Save, then Approve).
        pre_existing = list(
            ExtractionFieldCorrection.objects.filter(approval=approval)
            .exclude(pk__in=[c.pk for c in correction_records])
        )
        all_correction_records = list(correction_records) + pre_existing

        approval.status = ExtractionApprovalStatus.APPROVED
        approval.reviewed_by = user
        approval.reviewed_at = timezone.now()
        approval.fields_corrected_count = len(all_correction_records)
        approval.is_touchless = len(all_correction_records) == 0
        approval.save(update_fields=[
            "status", "reviewed_by", "reviewed_at",
            "fields_corrected_count", "is_touchless", "updated_at",
        ])

        # Transition invoice to READY_FOR_RECON
        invoice.status = InvoiceStatus.READY_FOR_RECON

        # Re-attempt vendor resolution in case vendor was created after extraction
        cls._try_resolve_vendor(invoice)

        invoice.save(update_fields=["status", "vendor", "updated_at"])

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
        if all_correction_records:
            desc += f" ({len(all_correction_records)} field(s) corrected)"
            cls._log_audit(
                invoice,
                AuditEventType.EXTRACTION_FIELD_CORRECTED,
                f"{len(all_correction_records)} field(s) corrected during approval",
                user=user,
                metadata={
                    "corrections": [
                        {
                            "entity_type": c.entity_type,
                            "field": c.field_name,
                            "from": c.original_value,
                            "to": c.corrected_value,
                        }
                        for c in all_correction_records
                    ],
                },
            )

        cls._log_audit(invoice, event_type, desc, user=user, metadata={
            "approval_id": approval.pk,
            "fields_corrected": len(all_correction_records),
            "is_touchless": approval.is_touchless,
        })

        logger.info(
            "Extraction approved for invoice %s by %s (corrections=%d, touchless=%s)",
            invoice.pk, user, len(all_correction_records), approval.is_touchless,
        )

        # ── Governance trail: mirror decision to ExtractionApprovalRecord ──
        cls._record_governance_trail(approval, "APPROVE", user)

        # ── core_eval: persist approval learning signals ──
        try:
            from apps.extraction.services.eval_adapter import ExtractionEvalAdapter
            ExtractionEvalAdapter.sync_for_approval(
                approval, user=user, correction_records=all_correction_records,
            )
        except Exception:
            logger.debug("core_eval approval sync failed (non-fatal)")

        # ── Create AP Case and trigger case pipeline ──
        # The case orchestrator handles reconciliation, agents, review
        # routing, and posting internally via its stage sequence.
        cls._ensure_case_and_process(invoice, user)

        try:
            from apps.core.langfuse_client import score_trace
            _score_tid = lf_trace_id or f"approval-{approval.pk}"
            score_trace(
                _score_tid,
                EXTRACTION_APPROVAL_DECISION,
                1.0,
                span=lf_span,
                comment=(
                    f"invoice={invoice.pk} "
                    f"reviewer={getattr(user, 'pk', None)} "
                    f"corrections={len(all_correction_records)} "
                    f"touchless={approval.is_touchless}"
                ),
            )
            score_trace(
                _score_tid,
                EXTRACTION_APPROVAL_CONFIDENCE,
                float(approval.confidence_at_review or 0.0),
                span=lf_span,
                comment=f"invoice={invoice.pk}",
            )
            if all_correction_records:
                score_trace(
                    _score_tid,
                    EXTRACTION_CORRECTIONS_COUNT,
                    float(len(all_correction_records)),
                    span=lf_span,
                    comment=f"invoice={invoice.pk}",
                )
        except Exception:
            pass

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
        lf_trace_id: Optional[str] = None,
        lf_span=None,
    ) -> ExtractionApproval:
        """Reject an extraction — invoice stays in PENDING_APPROVAL.

        Concurrency: Locks the ExtractionApproval row via select_for_update().
        Only PENDING → REJECTED is allowed.
        """
        # Lock the row to prevent concurrent approve/reject/reprocess
        approval = (
            ExtractionApproval.objects
            .select_for_update()
            .get(pk=approval.pk)
        )
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

        # ── core_eval: persist rejection learning signal ──
        try:
            from apps.extraction.services.eval_adapter import ExtractionEvalAdapter
            ExtractionEvalAdapter.sync_for_approval(approval, user=user)
        except Exception:
            logger.debug("core_eval reject sync failed (non-fatal)")

        try:
            from apps.core.langfuse_client import score_trace
            _score_tid = lf_trace_id or f"approval-{approval.pk}"
            score_trace(
                _score_tid,
                EXTRACTION_APPROVAL_DECISION,
                0.0,
                span=lf_span,
                comment=(
                    f"invoice={approval.invoice.pk} "
                    f"reviewer={getattr(user, 'pk', None)} "
                    f"reason={reason[:100]}"
                ),
            )
        except Exception:
            pass

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
                tenant=getattr(approval, 'tenant', None),
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
                    tenant=getattr(approval, 'tenant', None),
                ))

            if len(line_update_fields) > 1:
                line_item.save(update_fields=line_update_fields)

        if records:
            ExtractionFieldCorrection.objects.bulk_create(records)

        return records

    @staticmethod
    def _try_resolve_vendor(invoice: Invoice) -> None:
        """Re-attempt vendor FK resolution if not already linked.

        Called at approval time so invoices extracted before a vendor record
        was created still get matched automatically.
        """
        if invoice.vendor or not invoice.raw_vendor_name:
            return
        try:
            from apps.core.utils import normalize_string
            from apps.documents.models import Invoice as _Inv
            from apps.vendors.models import Vendor
            from apps.posting_core.models import VendorAliasMapping
            norm = normalize_string(invoice.raw_vendor_name)
            vendor = Vendor.objects.filter(normalized_name=norm, is_active=True).first()
            if not vendor:
                # Fallback: case-insensitive exact name match
                vendor = Vendor.objects.filter(
                    name__iexact=invoice.raw_vendor_name.strip(), is_active=True
                ).first()
            if not vendor:
                alias = VendorAliasMapping.objects.filter(
                    normalized_alias=norm, is_active=True
                ).select_related("vendor").first()
                if alias and alias.vendor:
                    vendor = alias.vendor
            if vendor:
                invoice.vendor = vendor
                logger.info(
                    "Vendor resolved at approval time for invoice %s: %s (pk=%s)",
                    invoice.pk, vendor.name, vendor.pk,
                )
        except Exception as exc:
            logger.warning(
                "Vendor re-resolution failed at approval for invoice %s: %s",
                invoice.pk, exc,
            )

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

    @staticmethod
    def _ensure_case_and_process(invoice, user=None):
        """Resume or create an APCase and continue the case processing pipeline.

        Since cases are now created immediately after extraction, this method
        primarily resumes an existing case that is paused at the extraction
        approval gate (PENDING_EXTRACTION_APPROVAL).  If no case exists (e.g.
        for invoices extracted before this change), it falls back to creating
        one and running from scratch.

        Best-effort: failures are logged but never block the approval path.
        """
        try:
            from apps.cases.models import APCase
            from apps.cases.services.case_creation_service import CaseCreationService
            from apps.cases.tasks import process_case_task
            from apps.core.enums import CaseStatus
            from apps.core.utils import dispatch_task

            # Look for an existing case (created during extraction task)
            case = APCase.objects.filter(invoice=invoice, is_active=True).first()

            if case and case.status == CaseStatus.PENDING_EXTRACTION_APPROVAL:
                # Resume the paused pipeline from path resolution
                from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
                from apps.cases.state_machine.case_state_machine import CaseStateMachine
                from apps.core.enums import PerformedByType

                CaseStateMachine.transition(
                    case, CaseStatus.EXTRACTION_COMPLETED, PerformedByType.SYSTEM
                )
                logger.info(
                    "Resuming case %s from extraction approval gate (invoice %s approved)",
                    case.case_number, invoice.pk,
                )
                dispatch_task(process_case_task, getattr(case, 'tenant_id', None), case.pk)
            elif case:
                # Case exists but in a different status -- just trigger processing
                logger.info(
                    "Case %s exists (status=%s), triggering processing for invoice %s",
                    case.case_number, case.status, invoice.pk,
                )
                dispatch_task(process_case_task, getattr(case, 'tenant_id', None), case.pk)
            else:
                # No case yet -- create one (backward compatibility)
                uploaded_by = user or (
                    invoice.document_upload.uploaded_by
                    if invoice.document_upload_id else None
                )
                case = CaseCreationService.create_from_upload(
                    invoice=invoice,
                    uploaded_by=uploaded_by,
                )
                logger.info("Created AP Case %s for invoice %s", case.case_number, invoice.pk)
                dispatch_task(process_case_task, getattr(case, 'tenant_id', None), case.pk)
        except Exception:
            logger.exception(
                "Failed to resume/create AP Case for invoice %s", invoice.pk,
            )

    @staticmethod
    def _ensure_case(invoice, user=None):
        """Create an APCase for this invoice if one doesn't exist yet.

        Every invoice must have a case for end-to-end tracking.
        Best-effort: failures are logged but never block the approval path.
        """
        try:
            from apps.cases.services.case_creation_service import CaseCreationService
            uploaded_by = user or (
                invoice.document_upload.uploaded_by
                if invoice.document_upload_id else None
            )
            case = CaseCreationService.create_from_upload(
                invoice=invoice,
                uploaded_by=uploaded_by,
            )
            logger.info("Ensured AP Case %s for invoice %s", case.case_number, invoice.pk)
        except Exception:
            logger.exception(
                "Failed to create AP Case for invoice %s", invoice.pk,
            )

    @staticmethod
    def _enqueue_reconciliation(invoice, user=None):
        """Enqueue reconciliation for the newly-approved invoice.

        Best-effort: failures are logged but never block the approval path.
        The reconciliation task will pick up the invoice (now READY_FOR_RECON),
        run matching, and automatically trigger the agent pipeline for
        non-MATCHED results.
        """
        try:
            from apps.reconciliation.tasks import run_reconciliation_task
            user_id = user.pk if user else None
            run_reconciliation_task.delay(
                invoice.tenant_id if invoice.tenant_id else None,
                invoice_ids=[invoice.pk],
                triggered_by_id=user_id,
            )
            logger.info("Enqueued reconciliation for invoice %s", invoice.pk)
        except Exception:
            logger.exception(
                "Failed to enqueue reconciliation for invoice %s", invoice.pk,
            )

    @staticmethod
    def _enqueue_posting(invoice, user=None):
        """Enqueue a posting pipeline run for the newly-approved invoice.

        Best-effort: failures are logged but never block the approval path.
        """
        try:
            from apps.posting.tasks import prepare_posting_task
            user_id = user.pk if user else None
            trigger = "approval" if user else "auto_approval"
            prepare_posting_task.delay(
                invoice.tenant_id if invoice.tenant_id else None,
                invoice_id=invoice.pk,
                user_id=user_id,
                trigger=trigger,
            )
            logger.info("Enqueued posting pipeline for invoice %s", invoice.pk)
        except Exception:
            logger.exception(
                "Failed to enqueue posting pipeline for invoice %s", invoice.pk,
            )
