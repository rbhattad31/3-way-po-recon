"""
Seed master data for case AP-260324-0002 (pk=192).

Creates: Vendor + aliases, PO + PO line items, GRN + GRN line items.
Does NOT link anything to invoice/case or touch reconciliation --
run reconciliation from the application UI.

Usage:
    python manage.py seed_case_192
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import User
from apps.documents.models import (
    GoodsReceiptNote,
    GRNLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.vendors.models import Vendor, VendorAlias


class Command(BaseCommand):
    help = "Seed master data (Vendor, PO, GRN) for case AP-260324-0002"

    def handle(self, *args, **options):
        now = timezone.now()
        admin_user = User.objects.get(email="admin@mcd-ksa.com")

        # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # 1. Vendor
        # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        vendor, _ = Vendor.objects.update_or_create(
            code="V-TSL-001",
            defaults=dict(
                name="TechServ Solutions LLP",
                normalized_name="techserv solutions llp",
                tax_id="AABCT1234F",
                address="Plot 42, Sector 18, Electronic City\nBengaluru, Karnataka 560100\nIndia",
                country="IN",
                currency="INR",
                payment_terms="Net 30",
                contact_email="accounts@techserv.co.in",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  Vendor: {vendor.name} (pk={vendor.pk})")

        for alias in [
            "TechServ Solutions",
            "TECHSERV SOLUTIONS LLP",
            "Tech Serv Solutions LLP",
            "TechServ Sol. LLP",
        ]:
            VendorAlias.objects.get_or_create(
                vendor=vendor,
                alias_name=alias,
                defaults=dict(
                    normalized_alias=alias.lower().strip(),
                    source="SEED",
                    created_by=admin_user,
                ),
            )
        self.stdout.write("  Vendor aliases: 4")

        # ---- Purchase Order ----
        po, _ = PurchaseOrder.objects.update_or_create(
            po_number="PO-BEL-2025-0112",
            defaults=dict(
                normalized_po_number="po-bel-2025-0112",
                vendor=vendor,
                po_date=now.date() - timedelta(days=45),
                currency="INR",
                total_amount=Decimal("227740.00"),
                tax_amount=Decimal("34740.00"),
                status="OPEN",
                buyer_name="Ravi Shankar",
                department="IT Infrastructure",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  PO: {po.po_number} (pk={po.pk})")

        # PO line items (designed to create interesting mismatches)
        po_line_defs = [
            {
                "line_number": 1,
                "item_code": "SVC-INFRA-001",
                "description": "IT Infrastructure Maintenance (Servers & Networking)",
                "quantity": Decimal("2"),
                "unit_price": Decimal("45000.0000"),
                "tax_amount": Decimal("16200.00"),
                "line_amount": Decimal("90000.00"),
                "unit_of_measure": "EA",
                "item_category": "IT_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "line_number": 2,
                "item_code": "SVC-LIC-002",
                "description": "Software License Management & Support Services",
                "quantity": Decimal("2"),
                "unit_price": Decimal("25000.0000"),
                "tax_amount": Decimal("9000.00"),
                "line_amount": Decimal("50000.00"),
                "unit_of_measure": "EA",
                "item_category": "SOFTWARE",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "line_number": 3,
                "item_code": "SVC-HELP-003",
                "description": "Helpdesk Support (8x5 - 50 users)",
                "quantity": Decimal("2"),
                "unit_price": Decimal("18500.0000"),
                "tax_amount": Decimal("6660.00"),  # 18% GST
                "line_amount": Decimal("37000.00"),
                "unit_of_measure": "EA",
                "item_category": "IT_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "line_number": 4,
                "item_code": "SVC-CLOUD-004",
                "description": "Cloud Backup Services (500 GB tier)",
                "quantity": Decimal("2"),
                "unit_price": Decimal("8000.0000"),
                "tax_amount": Decimal("2880.00"),  # 18% GST
                "line_amount": Decimal("16000.00"),
                "unit_of_measure": "EA",
                "item_category": "CLOUD_SERVICES",
                "is_service_item": True,
                "is_stock_item": False,
            },
        ]

        po_lines = []
        PurchaseOrderLineItem.objects.filter(purchase_order=po).delete()
        for ld in po_line_defs:
            pl = PurchaseOrderLineItem.objects.create(purchase_order=po, **ld)
            po_lines.append(pl)
        self.stdout.write(f"  PO Lines: {len(po_lines)}")

        # ---- GRN (partial delivery) ----
        grn, _ = GoodsReceiptNote.objects.update_or_create(
            grn_number="GRN-BEL-2025-0089",
            defaults=dict(
                purchase_order=po,
                vendor=vendor,
                receipt_date=now.date() - timedelta(days=12),
                status="RECEIVED",
                warehouse="BEL-DC-01 Bengaluru Data Center",
                receiver_name="Suresh Kumar",
                created_by=admin_user,
            ),
        )
        self.stdout.write(f"  GRN: {grn.grn_number} (pk={grn.pk})")

        grn_line_defs = [
            {
                "line_number": 1,
                "po_line": po_lines[0],
                "item_code": "SVC-INFRA-001",
                "description": "IT Infrastructure Maintenance (Servers & Networking)",
                "quantity_received": Decimal("2"),
                "quantity_accepted": Decimal("2"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
            {
                "line_number": 2,
                "po_line": po_lines[1],
                "item_code": "SVC-LIC-002",
                "description": "Software License Management & Support Services",
                "quantity_received": Decimal("2"),
                "quantity_accepted": Decimal("2"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
            {
                "line_number": 3,
                "po_line": po_lines[2],
                "item_code": "SVC-HELP-003",
                "description": "Helpdesk Support (8x5 - 50 users)",
                "quantity_received": Decimal("2"),
                "quantity_accepted": Decimal("2"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
            {
                "line_number": 4,
                "po_line": po_lines[3],
                "item_code": "SVC-CLOUD-004",
                "description": "Cloud Backup Services (500 GB tier)",
                "quantity_received": Decimal("2"),
                "quantity_accepted": Decimal("2"),
                "quantity_rejected": Decimal("0"),
                "unit_of_measure": "EA",
            },
        ]

        GRNLineItem.objects.filter(grn=grn).delete()
        for gld in grn_line_defs:
            GRNLineItem.objects.create(grn=grn, **gld)
        self.stdout.write(f"  GRN Lines: {len(grn_line_defs)}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Master data seeded:\n"
            f"  Vendor: {vendor.name} ({vendor.code}) + 4 aliases\n"
            f"  PO: {po.po_number} -- 4 lines, total INR 193,000\n"
            f"  GRN: {grn.grn_number} -- 4 lines (all fully received)\n"
            f"\nAll lines match invoice TSL/INV/2025/0892 exactly.\n"
            f"\nNow run reconciliation from the UI."
        ))

