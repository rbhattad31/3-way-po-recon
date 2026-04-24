"""Management command to import ERP reference data directly from a connector.

Usage:
    python manage.py import_erp_reference --connector "Streamline Azure" --types VENDOR ITEM PO
    python manage.py import_erp_reference --connector "Streamline Azure" --types VENDOR --tenant-id 1
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from apps.core.enums import ERPReferenceBatchType
from apps.posting_core.services.direct_erp_importer import DirectERPImportOrchestrator

User = get_user_model()


class Command(BaseCommand):
    help = "Import ERP reference data (vendors, items, etc.) directly from an ERP connector."

    def add_arguments(self, parser):
        parser.add_argument(
            "--connector",
            type=str,
            required=True,
            help="Name of the ERPConnection to import from (e.g., 'Streamline Azure')",
        )
        parser.add_argument(
            "--types",
            nargs="+",
            type=str,
            required=True,
            choices=list(ERPReferenceBatchType.values),
            help="Batch type(s) to import: VENDOR, ITEM, TAX_CODE, COST_CENTER, OPEN_PO",
        )
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=None,
            help="Tenant (CompanyProfile) ID to scope import to. If omitted, uses default.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="User ID to record as importer. If omitted, defaults to system user.",
        )
        parser.add_argument(
            "--as-of",
            type=str,
            default=None,
            help="ISO date (YYYY-MM-DD) for source_as_of field.",
        )

    def handle(self, *args, **options):
        connector_name = options["connector"].strip()
        batch_types = options["types"]
        tenant_id = options.get("tenant_id")
        user_id = options.get("user_id")
        as_of_str = options.get("as_of")

        # Get tenant
        from apps.accounts.models import CompanyProfile
        tenant = None
        if tenant_id:
            try:
                tenant = CompanyProfile.objects.get(pk=tenant_id)
                self.stdout.write(f"Using tenant: {tenant.name}")
            except CompanyProfile.DoesNotExist:
                raise CommandError(f"Tenant with ID {tenant_id} not found")
        else:
            tenant = CompanyProfile.get_default()
            if tenant:
                self.stdout.write(f"Using default tenant: {tenant.name}")

        # Get user
        user = None
        if user_id:
            try:
                user = User.objects.get(pk=user_id)
                self.stdout.write(f"Recording as user: {user.email}")
            except User.DoesNotExist:
                raise CommandError(f"User with ID {user_id} not found")

        # Parse as-of date
        source_as_of = None
        if as_of_str:
            from datetime import date
            try:
                source_as_of = date.fromisoformat(as_of_str)
                self.stdout.write(f"Source as-of: {source_as_of}")
            except ValueError:
                raise CommandError(f"Invalid date format: {as_of_str}. Use YYYY-MM-DD")

        # Run imports
        self.stdout.write(f"\nImporting from connector: {connector_name}")
        self.stdout.write(f"Batch types: {', '.join(batch_types)}\n")

        for batch_type in batch_types:
            self.stdout.write(self.style.WARNING(f"\n>>> Importing {batch_type}..."))
            try:
                batch = DirectERPImportOrchestrator.run_import(
                    batch_type=batch_type,
                    connector_name=connector_name,
                    tenant=tenant,
                    user=user,
                    source_as_of=source_as_of,
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Import completed:\n"
                        f"  Batch ID: {batch.pk}\n"
                        f"  Status: {batch.status}\n"
                        f"  Valid rows: {batch.valid_row_count}\n"
                        f"  Invalid rows: {batch.invalid_row_count}"
                    )
                )

                if batch.error_summary:
                    self.stdout.write(
                        self.style.WARNING(f"  Errors: {batch.error_summary[:200]}")
                    )

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"✗ Import failed: {str(exc)}")
                )
                raise CommandError(f"Import of {batch_type} failed: {str(exc)}")

        self.stdout.write(self.style.SUCCESS("\n✓ All imports completed!"))
