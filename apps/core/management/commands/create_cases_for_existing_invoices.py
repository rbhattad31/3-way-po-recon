"""Management command to create AP Cases for all existing invoices that don't have one.

Usage:
    python manage.py create_cases_for_existing_invoices
    python manage.py create_cases_for_existing_invoices --process  # also run orchestrator
"""
import logging

from django.core.management.base import BaseCommand

from apps.cases.models import APCase
from apps.cases.services.case_creation_service import CaseCreationService
from apps.core.enums import InvoiceStatus
from apps.documents.models import Invoice

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create AP Cases for existing invoices that don't have one yet"

    def add_arguments(self, parser):
        parser.add_argument(
            "--process",
            action="store_true",
            help="Also run the CaseOrchestrator for each new case",
        )
        parser.add_argument(
            "--status",
            type=str,
            default="",
            help="Only process invoices with this status (e.g. READY_FOR_RECON)",
        )

    def handle(self, *args, **options):
        do_process = options["process"]
        status_filter = options["status"]

        # Find invoices without cases
        existing_invoice_ids = set(
            APCase.objects.filter(is_active=True).values_list("invoice_id", flat=True)
        )

        qs = Invoice.objects.exclude(pk__in=existing_invoice_ids).select_related("vendor")
        if status_filter:
            qs = qs.filter(status=status_filter)

        invoices = list(qs.order_by("created_at"))
        self.stdout.write(f"\nFound {len(invoices)} invoices without AP Cases")

        created = 0
        processed = 0
        errors = 0

        for invoice in invoices:
            try:
                case = CaseCreationService.create_from_upload(
                    invoice=invoice,
                    uploaded_by=invoice.created_by,
                )
                created += 1
                self.stdout.write(
                    f"  [OK] {case.case_number} <- {invoice.invoice_number} "
                    f"({invoice.get_status_display()})"
                )

                if do_process and invoice.status in (
                    InvoiceStatus.READY_FOR_RECON,
                    InvoiceStatus.VALIDATED,
                    InvoiceStatus.EXTRACTED,
                ):
                    try:
                        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
                        orchestrator = CaseOrchestrator(case)
                        orchestrator.run()
                        processed += 1
                        self.stdout.write(
                            f"       Processed -> {case.status}"
                        )
                    except Exception as exc:
                        self.stdout.write(
                            self.style.WARNING(f"       Processing failed: {exc}")
                        )

            except Exception as exc:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(f"  [FAIL] Invoice {invoice.pk}: {exc}")
                )

        self.stdout.write(f"\n=== Summary ===")
        self.stdout.write(f"  Cases created:   {created}")
        if do_process:
            self.stdout.write(f"  Cases processed: {processed}")
        self.stdout.write(f"  Errors:          {errors}")
        self.stdout.write(self.style.SUCCESS("\nDone."))
