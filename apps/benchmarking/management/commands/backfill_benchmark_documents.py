from django.core.management.base import BaseCommand

from apps.benchmarking.models import BenchmarkQuotation
from apps.benchmarking.services.document_recovery_service import BenchmarkDocumentRecoveryService


class Command(BaseCommand):
    help = "Recover missing benchmark quotation document links from Azure/local storage."

    def add_arguments(self, parser):
        parser.add_argument("--request-id", type=int, help="Limit recovery to a benchmark request id.")
        parser.add_argument("--dry-run", action="store_true", help="Show recoverable rows without saving changes.")

    def handle(self, *args, **options):
        request_id = options.get("request_id")
        dry_run = bool(options.get("dry_run"))

        queryset = BenchmarkQuotation.objects.filter(is_active=True).select_related("request")
        if request_id:
            queryset = queryset.filter(request_id=request_id)

        total = queryset.count()
        recovered = 0
        unresolved = 0

        self.stdout.write(self.style.NOTICE(f"Scanning {total} quotation(s)..."))

        for quotation in queryset.iterator():
            has_source = BenchmarkDocumentRecoveryService.quotation_has_document_source(quotation)
            if has_source:
                continue

            if dry_run:
                discovered = BenchmarkDocumentRecoveryService.discover_document_source(quotation)
                could_recover = bool(discovered.get("blob_name") or discovered.get("document_name"))
                if could_recover:
                    recovered += 1
                    source_kind = "blob" if discovered.get("blob_name") else "local"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[RECOVERABLE:{source_kind}] quotation_id={quotation.pk} request_id={quotation.request_id}"
                        )
                    )
                else:
                    unresolved += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"[MISSING] quotation_id={quotation.pk} request_id={quotation.request_id}"
                        )
                    )
                continue

            did_recover = BenchmarkDocumentRecoveryService.ensure_document_source(quotation)
            if did_recover:
                recovered += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[RECOVERED] quotation_id={quotation.pk} request_id={quotation.request_id}"
                    )
                )
            else:
                unresolved += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"[MISSING] quotation_id={quotation.pk} request_id={quotation.request_id}"
                    )
                )

        summary = (
            f"Done. total={total}, recovered={recovered}, unresolved={unresolved}, dry_run={dry_run}"
        )
        if unresolved == 0:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary))
