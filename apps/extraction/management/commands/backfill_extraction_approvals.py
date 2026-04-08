"""Backfill ExtractionApproval records for invoices that were extracted
before the approval gate was introduced.

Invoices in VALIDATED / READY_FOR_RECON / RECONCILED statuses that already
have an ExtractionResult but no ExtractionApproval get an AUTO_APPROVED
approval record (they were implicitly approved by the old flow).

Invoices in EXTRACTED / PENDING_APPROVAL that lack an approval record get a
PENDING record so they appear in the approval queue.

Usage:
    python manage.py backfill_extraction_approvals
    python manage.py backfill_extraction_approvals --dry-run
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.enums import ExtractionApprovalStatus, InvoiceStatus
from apps.documents.models import Invoice
from apps.extraction.models import ExtractionApproval, ExtractionResult
from apps.extraction.services.approval_service import ExtractionApprovalService


# Statuses that mean the invoice already passed through extraction successfully
_ALREADY_APPROVED_STATUSES = {
    InvoiceStatus.VALIDATED,
    InvoiceStatus.READY_FOR_RECON,
    InvoiceStatus.RECONCILED,
}

# Statuses that mean extraction is done but approval is still needed
_NEEDS_APPROVAL_STATUSES = {
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.PENDING_APPROVAL,
}


class Command(BaseCommand):
    help = "Backfill ExtractionApproval records for pre-existing extractions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without actually creating records.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Invoices that have an ExtractionResult but no ExtractionApproval
        existing_approval_ids = set(
            ExtractionApproval.objects.values_list("invoice_id", flat=True)
        )

        # Get ExtractionResults linked to invoices (pick latest per upload)
        # ExtractionResult no longer has a direct invoice FK; look up via
        # document_upload -> invoices reverse relation.
        er_map = {}  # invoice_id -> ExtractionResult
        for er in (
            ExtractionResult.objects
            .select_related("document_upload")
            .order_by("-created_at")
        ):
            inv = er.invoice  # property: document_upload.invoices.first()
            if inv is None:
                continue
            inv_id = inv.pk
            if inv_id not in er_map:
                er_map[inv_id] = er

        auto_approved = 0
        pending = 0
        skipped = 0

        for invoice_id, ext_result in er_map.items():
            if invoice_id in existing_approval_ids:
                skipped += 1
                continue

            invoice = ext_result.invoice
            status = invoice.status

            if status in _ALREADY_APPROVED_STATUSES:
                if dry_run:
                    self.stdout.write(
                        f"  [DRY-RUN] Would AUTO_APPROVE invoice {invoice.pk} "
                        f"({invoice.invoice_number}, status={status})"
                    )
                else:
                    snapshot = ExtractionApprovalService._build_values_snapshot(invoice)
                    ExtractionApproval.objects.create(
                        invoice=invoice,
                        extraction_result=ext_result,
                        status=ExtractionApprovalStatus.AUTO_APPROVED,
                        reviewed_at=invoice.updated_at or timezone.now(),
                        confidence_at_review=invoice.extraction_confidence,
                        original_values_snapshot=snapshot,
                        is_touchless=True,
                    )
                auto_approved += 1

            elif status in _NEEDS_APPROVAL_STATUSES:
                if dry_run:
                    self.stdout.write(
                        f"  [DRY-RUN] Would create PENDING approval for invoice {invoice.pk} "
                        f"({invoice.invoice_number}, status={status})"
                    )
                else:
                    ExtractionApprovalService.create_pending_approval(
                        invoice, ext_result
                    )
                    # Ensure the invoice status is PENDING_APPROVAL
                    if status != InvoiceStatus.PENDING_APPROVAL:
                        invoice.status = InvoiceStatus.PENDING_APPROVAL
                        invoice.save(update_fields=["status", "updated_at"])
                pending += 1
            else:
                skipped += 1

        prefix = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"\n{prefix}Backfill complete: "
            f"{auto_approved} auto-approved, {pending} pending, {skipped} skipped."
        ))
