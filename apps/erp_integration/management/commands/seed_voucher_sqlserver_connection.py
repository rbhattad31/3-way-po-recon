"""Seed a prefilled voucher-based SQL Server ERP connection profile."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.accounts.models import CompanyProfile
from apps.erp_integration.enums import ERPConnectionStatus, ERPConnectorType
from apps.erp_integration.models import ERPConnection


DEFAULT_METADATA_PROFILE = {
    "voucher_series": {
        "purchase_invoice": "App PI%",
        "purchase_order": "App PO%",
        "grn": "ABSR%",
        "purchase_return": "APPR%",
    },
    "document_sources": {
        "vendor_master": "Master_Table + Master_MasterCodes_Table + Master_RegistrationDetails_Table",
        "purchase_orders": "Transaction_Header_Table + Transaction_ItemBody_Table",
        "grn": "EFIMRDetailsTable",
        "purchase_invoices": "Transaction_Header_Table + Transaction_ItemBody_Table + Transaction_Payments_Table",
        "duplicate_invoice_check": "Transaction_Payments_Table",
    },
    "matching_hints": {
        "po_number_field": "VoucherNo",
        "vendor_reference_field": "PartyRefDoc",
        "grn_po_link_field": "POrderNum",
        "invoice_supplier_reference_field": "SupplierInvNo",
        "prefer_efi_grn_table": True,
    },
    "notes": {
        "purpose": "Prefilled voucher-based SQL Server ERP profile",
        "schema_type": "shared_transaction_tables_with_voucher_series",
        "required_validation": [
            "Confirm live VoucherDetails_Table mappings for PI/PO/GRN series",
            "Confirm whether purchase orders also exist under a non-App PO series",
            "Confirm whether duplicate invoice detection should include fiscal-year suffix logic",
        ],
    },
}


class Command(BaseCommand):
    help = "Create or update a prefilled VOUCHER_SQLSERVER ERP connection profile"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=int,
            default=None,
            help="Tenant (CompanyProfile) PK to attach the profile to. Defaults to global profile.",
        )
        parser.add_argument(
            "--name",
            default="client-voucher-sqlserver",
            help="ERP connection profile name.",
        )
        parser.add_argument(
            "--set-default",
            action="store_true",
            help="Mark the seeded profile as the default ERP connection.",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Create the connection in ACTIVE status instead of INACTIVE.",
        )
        parser.add_argument(
            "--connection-string-env",
            default="CLIENT_ERP_SQLSERVER_CONNECTION_STRING",
            help="Env var name holding the SQL Server connection string.",
        )
        parser.add_argument(
            "--database-name",
            default="",
            help="Optional SQL Server database name.",
        )
        parser.add_argument(
            "--db-host",
            default="",
            help="Optional SQL Server host if builder mode is preferred.",
        )
        parser.add_argument(
            "--db-port",
            type=int,
            default=1433,
            help="Optional SQL Server port for builder mode.",
        )
        parser.add_argument(
            "--db-username",
            default="",
            help="Optional SQL Server username for builder mode.",
        )
        parser.add_argument(
            "--purchase-invoice-series",
            default="App PI%",
            help="Voucher series pattern for purchase invoices.",
        )
        parser.add_argument(
            "--purchase-order-series",
            default="App PO%",
            help="Voucher series pattern for purchase orders.",
        )

    def handle(self, *args, **options):
        tenant = None
        tenant_id = options.get("tenant")
        if tenant_id:
            tenant = CompanyProfile.objects.filter(pk=tenant_id).first()
            if not tenant:
                self.stderr.write(self.style.ERROR(f"Tenant {tenant_id} not found."))
                return

        status = (
            ERPConnectionStatus.ACTIVE
            if options.get("activate")
            else ERPConnectionStatus.INACTIVE
        )

        metadata_json = dict(DEFAULT_METADATA_PROFILE)
        metadata_json["voucher_series"] = dict(DEFAULT_METADATA_PROFILE["voucher_series"])
        metadata_json["voucher_series"]["purchase_invoice"] = options["purchase_invoice_series"]
        metadata_json["voucher_series"]["purchase_order"] = options["purchase_order_series"]

        defaults = {
            "connector_type": ERPConnectorType.VOUCHER_SQLSERVER,
            "status": status,
            "timeout_seconds": 30,
            "is_default": bool(options.get("set_default")),
            "base_url": "",
            "connection_string_env": options["connection_string_env"],
            "database_name": options["database_name"],
            "db_host": options["db_host"],
            "db_port": options["db_port"],
            "db_username": options["db_username"],
            "db_driver": "ODBC Driver 17 for SQL Server",
            "db_trust_cert": True,
            "metadata_json": metadata_json,
        }

        connection, created = ERPConnection.objects.update_or_create(
            tenant=tenant,
            name=options["name"],
            defaults=defaults,
        )

        action = "Created" if created else "Updated"
        tenant_label = tenant.name if tenant else "GLOBAL"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} ERP connection '{connection.name}' for {tenant_label} as {connection.connector_type}."
            )
        )

