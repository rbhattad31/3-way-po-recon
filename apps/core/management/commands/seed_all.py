"""Unified seed entry point for available project seed commands."""
from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run available project seed commands in sequence"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=None,
            help="Tenant (CompanyProfile) PK to scope tenant-aware seed commands.",
        )
        parser.add_argument(
            "--skip-email",
            action="store_true",
            help="Skip email integration demo data seeding.",
        )
        parser.add_argument(
            "--skip-erp",
            action="store_true",
            help="Skip voucher SQL Server ERP connection seeding.",
        )
        parser.add_argument(
            "--flush-email",
            action="store_true",
            help="Flush email seed data before re-seeding it.",
        )
        parser.add_argument(
            "--erp-set-default",
            action="store_true",
            help="Mark the seeded voucher SQL Server ERP connection as default.",
        )
        parser.add_argument(
            "--erp-activate",
            action="store_true",
            help="Create the seeded voucher SQL Server ERP connection in ACTIVE status.",
        )

    def handle(self, *args, **options):
        tenant_id = options.get("tenant")
        executed = []

        if not options.get("skip_email"):
            email_args = []
            if options.get("flush_email"):
                email_args.append("--flush")
            if tenant_id is not None:
                email_args.extend(["--tenant", str(tenant_id)])
            call_command("seed_email_data", *email_args)
            executed.append("seed_email_data")

        if not options.get("skip_erp"):
            erp_args = []
            if tenant_id is not None:
                erp_args.extend(["--tenant", str(tenant_id)])
            if options.get("erp_set_default"):
                erp_args.append("--set-default")
            if options.get("erp_activate"):
                erp_args.append("--activate")
            call_command("seed_voucher_sqlserver_connection", *erp_args)
            executed.append("seed_voucher_sqlserver_connection")

        if not executed:
            self.stdout.write(self.style.WARNING("Nothing executed. All seed groups were skipped."))
            return

        self.stdout.write(self.style.SUCCESS(f"Seed flow complete: {', '.join(executed)}"))
