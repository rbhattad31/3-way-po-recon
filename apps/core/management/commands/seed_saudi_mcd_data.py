"""
Management command: seed_saudi_mcd_data

Seeds realistic master data for a Saudi Arabia McDonald's master distributor
3-way PO reconciliation system. This is Phase 1 - master data only:

  - Users (system accounts)
  - Vendors + VendorAliases
  - PurchaseOrders + PurchaseOrderLineItems
  - GoodsReceiptNotes + GRNLineItems

All records are scenario-driven to support deterministic reconciliation,
agentic decision-making, exception handling, review routing, and audit.

Usage:
    python manage.py seed_saudi_mcd_data
    python manage.py seed_saudi_mcd_data --flush   # wipe existing seed data first
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import (
    AgentDefinition,
    AgentMessage,
    AgentRecommendation,
    AgentRun,
    AgentStep,
    DecisionLog,
)
from apps.auditlog.models import AuditEvent, ProcessingLog
from apps.core.enums import AgentType, UserRole
from apps.core.utils import normalize_po_number, normalize_string
from apps.documents.models import (
    DocumentUpload,
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.reconciliation.models import (
    ReconciliationException,
    ReconciliationResult,
    ReconciliationResultLine,
    ReconciliationRun,
)
from apps.reviews.models import (
    ManualReviewAction,
    ReviewAssignment,
    ReviewComment,
    ReviewDecision,
)
from apps.tools.models import ToolCall, ToolDefinition
from apps.vendors.models import Vendor, VendorAlias

# ---------------------------------------------------------------------------
# Constants: Locations
# ---------------------------------------------------------------------------
LOCATIONS = {
    "WH-RUH-01": "Riyadh Central Warehouse",
    "WH-JED-01": "Jeddah Distribution Center",
    "WH-DMM-01": "Dammam Cold Store",
    "CK-RUH-01": "Riyadh Central Kitchen",
    "BR-RUH-101": "Riyadh Branch 101",
    "BR-RUH-102": "Riyadh Branch 102",
    "BR-JED-220": "Jeddah Branch 220",
    "BR-JED-221": "Jeddah Branch 221",
    "BR-DMM-055": "Dammam Branch 055",
    "BR-DMM-056": "Dammam Branch 056",
}

# VAT rate in Saudi Arabia (15%)
VAT_RATE = Decimal("0.15")

# Base date for seeding - approximately "today minus some days"
BASE_DATE = date(2026, 2, 15)


def _d(val) -> Decimal:
    """Shorthand Decimal constructor."""
    return Decimal(str(val))


def _line_amount(qty, price) -> Decimal:
    return (_d(qty) * _d(price)).quantize(Decimal("0.01"))


def _tax(amount) -> Decimal:
    return (amount * VAT_RATE).quantize(Decimal("0.01"))


# ===================================================================
#  USERS
# ===================================================================

USERS_DATA = [
    {
        "email": "admin@mcd-ksa.com",
        "first_name": "System",
        "last_name": "Admin",
        "role": UserRole.ADMIN,
        "is_staff": True,
        "is_superuser": True,
        "department": "IT",
    },
    {
        "email": "ap.processor@mcd-ksa.com",
        "first_name": "Fatima",
        "last_name": "Al-Rashid",
        "role": UserRole.AP_PROCESSOR,
        "department": "Accounts Payable",
    },
    {
        "email": "reviewer@mcd-ksa.com",
        "first_name": "Ahmed",
        "last_name": "Al-Harbi",
        "role": UserRole.REVIEWER,
        "department": "Procurement",
    },
    {
        "email": "finance.mgr@mcd-ksa.com",
        "first_name": "Khalid",
        "last_name": "Al-Otaibi",
        "role": UserRole.FINANCE_MANAGER,
        "department": "Finance",
    },
    {
        "email": "auditor@mcd-ksa.com",
        "first_name": "Nora",
        "last_name": "Al-Sadiq",
        "role": UserRole.AUDITOR,
        "department": "Internal Audit",
    },
    {
        "email": "warehouse.mgr@mcd-ksa.com",
        "first_name": "Omar",
        "last_name": "Al-Ghamdi",
        "role": UserRole.REVIEWER,
        "department": "Warehouse",
    },
]


def create_users() -> dict[str, User]:
    """Create or retrieve system users. Returns dict keyed by email prefix."""
    users = {}
    for data in USERS_DATA:
        email = data["email"]
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "role": data["role"],
                "is_staff": data.get("is_staff", False),
                "is_superuser": data.get("is_superuser", False),
                "department": data.get("department", ""),
            },
        )
        if created:
            user.set_password("SeedPass123!")
            user.save()
        key = email.split("@")[0].replace(".", "_")
        users[key] = user
    return users


# ===================================================================
#  VENDORS & ALIASES
# ===================================================================

VENDORS_DATA = [
    {
        "code": "VND-AFS-001",
        "name": "Arabian Food Supplies Co.",
        "tax_id": "3100123456",
        "address": "Industrial Area 2, Riyadh 12345, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 30",
        "contact_email": "orders@arabianfood.sa",
        "aliases": [
            "Arabian Food Supply Co",
            "AFS Co.",
            "Arabian FS",
            "Arabian Food Supplies KSA",
        ],
    },
    {
        "code": "VND-GFF-002",
        "name": "Gulf Frozen Foods Trading",
        "tax_id": "3100234567",
        "address": "Cold Storage Complex, Dammam 31952, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 45",
        "contact_email": "sales@gulffrozen.sa",
        "aliases": [
            "Gulf Frozen Foods",
            "GFF Trading",
        ],
    },
    {
        "code": "VND-AWP-003",
        "name": "Al Watania Poultry Supply",
        "tax_id": "3100345678",
        "address": "Poultry Industrial Park, Riyadh 14713, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 30",
        "contact_email": "procurement@alwatania.sa",
        "aliases": [
            "Al Watania Poultry",
            "Watania Poultry KSA",
        ],
    },
    {
        "code": "VND-SPS-004",
        "name": "Saudi Packaging Solutions",
        "tax_id": "3100456789",
        "address": "Second Industrial City, Jeddah 21442, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 60",
        "contact_email": "info@saudipack.sa",
        "aliases": [
            "SPS",
            "Saudi Pack Solutions",
        ],
    },
    {
        "code": "VND-RBC-005",
        "name": "Riyadh Beverage Concentrates Co.",
        "tax_id": "3100567890",
        "address": "MODON Industrial, Riyadh 14334, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 30",
        "contact_email": "supply@riyadhbev.sa",
        "aliases": [],
    },
    {
        "code": "VND-DCCL-006",
        "name": "Desert Cold Chain Logistics",
        "tax_id": "3100678901",
        "address": "Logistics Hub, King Fahd Road, Riyadh 12271, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 30",
        "contact_email": "ops@desertcold.sa",
        "aliases": [],
    },
    {
        "code": "VND-RSRC-007",
        "name": "Red Sea Restaurant Consumables",
        "tax_id": "3100789012",
        "address": "Al-Khumra, Jeddah 23761, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 45",
        "contact_email": "sales@redsea-rc.sa",
        "aliases": [],
    },
    {
        "code": "VND-NEO-008",
        "name": "Najd Edible Oils Trading",
        "tax_id": "3100890123",
        "address": "Al-Kharj Road, Riyadh 11564, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 30",
        "contact_email": "orders@najdoils.sa",
        "aliases": [],
    },
    {
        "code": "VND-AKD-009",
        "name": "Al Khobar Dairy Ingredients",
        "tax_id": "3100901234",
        "address": "Dairy Industrial Zone, Al Khobar 31952, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 30",
        "contact_email": "supply@akdairy.sa",
        "aliases": [
            "AKD Ingredients",
        ],
    },
    {
        "code": "VND-JQSS-010",
        "name": "Jeddah Quick Service Supplies",
        "tax_id": "3101012345",
        "address": "Al-Safa District, Jeddah 23452, Saudi Arabia",
        "country": "SA",
        "currency": "SAR",
        "payment_terms": "Net 45",
        "contact_email": "info@jqss.sa",
        "aliases": [],
    },
]


def create_vendors_and_aliases(admin_user: User):
    """Create vendors and their aliases. Returns dict[code] -> Vendor."""
    vendors = {}
    alias_count = 0
    for vdata in VENDORS_DATA:
        vendor, _ = Vendor.objects.get_or_create(
            code=vdata["code"],
            defaults={
                "name": vdata["name"],
                "normalized_name": normalize_string(vdata["name"]),
                "tax_id": vdata["tax_id"],
                "address": vdata["address"],
                "country": vdata["country"],
                "currency": vdata["currency"],
                "payment_terms": vdata["payment_terms"],
                "contact_email": vdata["contact_email"],
                "created_by": admin_user,
            },
        )
        vendors[vdata["code"]] = vendor

        for alias_name in vdata.get("aliases", []):
            _, created = VendorAlias.objects.get_or_create(
                vendor=vendor,
                normalized_alias=normalize_string(alias_name),
                defaults={
                    "alias_name": alias_name,
                    "source": "seed",
                    "created_by": admin_user,
                },
            )
            if created:
                alias_count += 1

    return vendors, alias_count


# ===================================================================
#  PURCHASE ORDERS
# ===================================================================

def create_purchase_orders(vendors: dict, admin_user: User):
    """
    Create 25 POs with ~62 line items across all scenarios.
    Returns (dict[po_number] -> PO, dict[po_number] -> [POLineItem...]).
    """
    v = vendors  # shorthand
    afs = v["VND-AFS-001"]
    gff = v["VND-GFF-002"]
    awp = v["VND-AWP-003"]
    sps = v["VND-SPS-004"]
    rbc = v["VND-RBC-005"]
    dccl = v["VND-DCCL-006"]
    rsrc = v["VND-RSRC-007"]
    neo = v["VND-NEO-008"]
    akd = v["VND-AKD-009"]
    jqss = v["VND-JQSS-010"]

    po_defs = _build_po_definitions(
        afs, gff, awp, sps, rbc, dccl, rsrc, neo, akd, jqss
    )

    pos = {}
    po_lines = {}

    for po_def in po_defs:
        po_number = po_def["po_number"]
        lines_data = po_def.pop("lines")

        # Compute totals from lines
        subtotal = sum(_line_amount(ln["qty"], ln["price"]) for ln in lines_data)
        tax_total = sum(
            ln.get("tax", _tax(_line_amount(ln["qty"], ln["price"])))
            for ln in lines_data
        )

        po, _ = PurchaseOrder.objects.get_or_create(
            po_number=po_number,
            defaults={
                "normalized_po_number": normalize_po_number(po_number),
                "vendor": po_def["vendor"],
                "po_date": po_def["po_date"],
                "currency": "SAR",
                "total_amount": subtotal + tax_total,
                "tax_amount": tax_total,
                "status": po_def.get("status", "OPEN"),
                "buyer_name": po_def.get("buyer_name", "Fatima Al-Rashid"),
                "department": po_def.get("department", "Procurement"),
                "notes": po_def.get("notes", ""),
                "created_by": admin_user,
            },
        )
        pos[po_number] = po

        created_lines = []
        for idx, ln in enumerate(lines_data, start=1):
            amount = _line_amount(ln["qty"], ln["price"])
            tax_amt = ln.get("tax", _tax(amount))
            pol, _ = PurchaseOrderLineItem.objects.get_or_create(
                purchase_order=po,
                line_number=idx,
                defaults={
                    "item_code": ln["item_code"],
                    "description": ln["description"],
                    "quantity": _d(ln["qty"]),
                    "unit_price": _d(ln["price"]),
                    "tax_amount": tax_amt,
                    "line_amount": amount,
                    "unit_of_measure": ln["uom"],
                },
            )
            created_lines.append(pol)
        po_lines[po_number] = created_lines

    return pos, po_lines


def _build_po_definitions(afs, gff, awp, sps, rbc, dccl, rsrc, neo, akd, jqss):
    """Return list of PO definition dicts with nested line items."""
    return [
        # ---------------------------------------------------------------
        # SCENARIO 1 - Perfect 3-way match (food supply)
        # Vendor: Arabian Food Supplies Co.
        # Warehouse: WH-RUH-01
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1001",
            "vendor": afs,
            "po_date": BASE_DATE - timedelta(days=20),
            "notes": "Scenario 1: Perfect match - buns, lettuce, pickles for Riyadh Central",
            "lines": [
                {
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty": 500,
                    "price": "45.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AFS-LET-001",
                    "description": "Shredded Lettuce Food Service Pack",
                    "qty": 200,
                    "price": "28.00",
                    "uom": "PKT",
                },
                {
                    "item_code": "AFS-PKL-001",
                    "description": "Pickle Slice Jar Bulk",
                    "qty": 100,
                    "price": "35.00",
                    "uom": "BOX",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 2 - Quantity mismatch on buns
        # Invoice will claim 650 CTN but PO says 600
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1002",
            "vendor": afs,
            "po_date": BASE_DATE - timedelta(days=18),
            "notes": "Scenario 2: Qty mismatch - invoice 650 vs PO 600 sesame buns",
            "lines": [
                {
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty": 600,
                    "price": "45.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AFS-BUN-002",
                    "description": "Regular Burger Bun 4 inch",
                    "qty": 300,
                    "price": "40.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 3 - Price mismatch on frozen patties
        # PO says 185 SAR; invoice will say 192 SAR
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1003",
            "vendor": gff,
            "po_date": BASE_DATE - timedelta(days=22),
            "notes": "Scenario 3: Price mismatch - PO 185 vs invoice 192 for beef patties",
            "lines": [
                {
                    "item_code": "GFF-BPT-001",
                    "description": "McD Beef Patty 4:1 Frozen",
                    "qty": 300,
                    "price": "185.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "GFF-BPT-002",
                    "description": "McD Beef Patty 10:1 Frozen",
                    "qty": 200,
                    "price": "120.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 4 - VAT mismatch
        # PO tax and invoice tax differ on cheese slices
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1004",
            "vendor": akd,
            "po_date": BASE_DATE - timedelta(days=15),
            "notes": "Scenario 4: VAT mismatch - cheese slices, dairy butter",
            "lines": [
                {
                    "item_code": "AKD-CHS-001",
                    "description": "Cheese Slice Processed",
                    "qty": 400,
                    "price": "62.00",
                    "uom": "CTN",
                    "tax": _d("3720.00"),  # 15% of 24800
                },
                {
                    "item_code": "AKD-BTR-001",
                    "description": "Butter Portion Pack",
                    "qty": 200,
                    "price": "18.50",
                    "uom": "CTN",
                    "tax": _d("555.00"),  # 15% of 3700
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 5 - Duplicate invoice
        # Same PO used by two invoices
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1005",
            "vendor": dccl,
            "po_date": BASE_DATE - timedelta(days=25),
            "notes": "Scenario 5: Duplicate invoice - French Fries cold chain",
            "lines": [
                {
                    "item_code": "DCCL-FRY-001",
                    "description": "French Fries 2.5kg Frozen",
                    "qty": 800,
                    "price": "78.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "DCCL-FRY-002",
                    "description": "French Fries 1kg Frozen",
                    "qty": 400,
                    "price": "36.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 6 - Missing PO
        # No PO created; invoice will reference PO-KSA-9999
        # (this scenario has no PO record)
        # ---------------------------------------------------------------
        # ---------------------------------------------------------------
        # SCENARIO 7 - Missing GRN
        # PO exists but no GRN will be created
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1007",
            "vendor": neo,
            "po_date": BASE_DATE - timedelta(days=12),
            "notes": "Scenario 7: Missing GRN - cooking oil ordered but not yet received",
            "lines": [
                {
                    "item_code": "NEO-OIL-001",
                    "description": "Cooking Oil Fryer Grade 20L",
                    "qty": 150,
                    "price": "32.00",
                    "uom": "LTR",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 8 - Multiple GRNs for one PO (staggered delivery)
        # 3 GRNs across warehouse deliveries
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1008",
            "vendor": gff,
            "po_date": BASE_DATE - timedelta(days=30),
            "notes": "Scenario 8: Multi-GRN staggered delivery - beef, chicken, nuggets, hash browns",
            "lines": [
                {
                    "item_code": "GFF-BPT-001",
                    "description": "McD Beef Patty 4:1 Frozen",
                    "qty": 500,
                    "price": "185.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "GFF-CPT-001",
                    "description": "Chicken Patty Breaded Frozen",
                    "qty": 400,
                    "price": "158.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "GFF-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty": 300,
                    "price": "145.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "GFF-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty": 250,
                    "price": "95.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 9 - Invoice exceeds received frozen stock
        # PO 400 CTN, GRN received only 350
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1009",
            "vendor": dccl,
            "po_date": BASE_DATE - timedelta(days=16),
            "notes": "Scenario 9: Invoice > GRN qty - hash browns partial receipt",
            "lines": [
                {
                    "item_code": "DCCL-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty": 400,
                    "price": "95.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "DCCL-ONR-001",
                    "description": "Onion Rings Breaded Frozen",
                    "qty": 100,
                    "price": "88.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 10 - Low-confidence scanned invoice
        # PO is normal; invoice extraction will have low confidence
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1010",
            "vendor": jqss,
            "po_date": BASE_DATE - timedelta(days=14),
            "notes": "Scenario 10: Low-confidence extraction - ketchup & mustard",
            "lines": [
                {
                    "item_code": "JQSS-KET-001",
                    "description": "Tomato Ketchup Bag-in-Box",
                    "qty": 200,
                    "price": "55.00",
                    "uom": "BOX",
                },
                {
                    "item_code": "JQSS-MUS-001",
                    "description": "Mustard Sauce Dispenser Pack",
                    "qty": 150,
                    "price": "48.00",
                    "uom": "BOX",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 11A - Ambiguous PO candidate (first PO)
        # Same vendor, very similar items - agent must disambiguate
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1011",
            "vendor": afs,
            "po_date": BASE_DATE - timedelta(days=10),
            "notes": "Scenario 11A: Ambiguous PO - sesame buns order A (300 CTN @ 45)",
            "lines": [
                {
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty": 300,
                    "price": "45.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 11B - Ambiguous PO candidate (second PO)
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1012",
            "vendor": afs,
            "po_date": BASE_DATE - timedelta(days=8),
            "notes": "Scenario 11B: Ambiguous PO - sesame buns order B (350 CTN @ 46)",
            "lines": [
                {
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty": 350,
                    "price": "46.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 12 - Mixed Arabic-English invoice
        # Normal PO; invoice will contain Arabic descriptions
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1013",
            "vendor": awp,
            "po_date": BASE_DATE - timedelta(days=19),
            "notes": "Scenario 12: Arabic-English mix - chicken patties, nuggets, hash browns",
            "lines": [
                {
                    "item_code": "AWP-CPT-001",
                    "description": "Chicken Patty Breaded Frozen",
                    "qty": 350,
                    "price": "158.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AWP-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty": 200,
                    "price": "145.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AWP-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty": 150,
                    "price": "95.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 13 - Packaging mismatch (item mismatch)
        # PO says Paper Cup 16oz + Plastic Lid 16oz; invoice will swap
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1014",
            "vendor": sps,
            "po_date": BASE_DATE - timedelta(days=17),
            "notes": "Scenario 13: Item mismatch - cups vs lids ordering confusion",
            "lines": [
                {
                    "item_code": "SPS-CUP-001",
                    "description": "Paper Cup 16oz",
                    "qty": 5000,
                    "price": "0.85",
                    "uom": "PCS",
                },
                {
                    "item_code": "SPS-LID-001",
                    "description": "Plastic Lid 16oz",
                    "qty": 5000,
                    "price": "0.45",
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 14 - Warehouse vs branch destination mismatch
        # PO for WH-RUH-01; invoice references BR-JED-220
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1015",
            "vendor": rbc,
            "po_date": BASE_DATE - timedelta(days=13),
            "department": "Beverage Supply",
            "notes": "Scenario 14: Location mismatch - PO WH-RUH-01 vs invoice BR-JED-220",
            "lines": [
                {
                    "item_code": "RBC-SYR-001",
                    "description": "Soft Drink Syrup Cola Bag-in-Box",
                    "qty": 100,
                    "price": "220.00",
                    "uom": "BAG",
                },
                {
                    "item_code": "RBC-SYR-002",
                    "description": "Soft Drink Syrup Fanta Bag-in-Box",
                    "qty": 80,
                    "price": "215.00",
                    "uom": "BAG",
                },
                {
                    "item_code": "RBC-SYR-003",
                    "description": "Soft Drink Syrup Sprite Bag-in-Box",
                    "qty": 60,
                    "price": "210.00",
                    "uom": "BAG",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO 15 - Reviewed and corrected case
        # Qty mismatch -> review -> AP corrects qty -> reconciled
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1016",
            "vendor": gff,
            "po_date": BASE_DATE - timedelta(days=21),
            "notes": "Scenario 15: Review+correct - nuggets qty mismatch then corrected",
            "lines": [
                {
                    "item_code": "GFF-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty": 250,
                    "price": "145.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "GFF-CST-001",
                    "description": "Chicken Strips Frozen",
                    "qty": 180,
                    "price": "162.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO: Closed PO referenced by invoice
        # PO already closed; invoice arrives late
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1017",
            "vendor": afs,
            "po_date": BASE_DATE - timedelta(days=60),
            "status": "CLOSED",
            "notes": "Closed PO - old buns order fully delivered & closed",
            "lines": [
                {
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty": 200,
                    "price": "44.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AFS-PKL-001",
                    "description": "Pickle Slice Jar Bulk",
                    "qty": 80,
                    "price": "35.00",
                    "uom": "BOX",
                },
                {
                    "item_code": "AFS-LET-001",
                    "description": "Shredded Lettuce Food Service Pack",
                    "qty": 100,
                    "price": "28.00",
                    "uom": "PKT",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO: Over-receipt GRN
        # GRN received more than PO ordered
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1018",
            "vendor": awp,
            "po_date": BASE_DATE - timedelta(days=14),
            "notes": "Over-receipt - GRN 520 vs PO 500 chicken patties",
            "lines": [
                {
                    "item_code": "AWP-CPT-001",
                    "description": "Chicken Patty Breaded Frozen",
                    "qty": 500,
                    "price": "158.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AWP-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty": 300,
                    "price": "145.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AWP-CWG-001",
                    "description": "Chicken Wings Frozen",
                    "qty": 150,
                    "price": "132.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO: Amount mismatch on packaging
        # Line amounts don't add up correctly
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1019",
            "vendor": sps,
            "po_date": BASE_DATE - timedelta(days=11),
            "notes": "Amount mismatch - packaging supplies total discrepancy",
            "lines": [
                {
                    "item_code": "SPS-BMC-001",
                    "description": "Big Mac Clamshell Box",
                    "qty": 3000,
                    "price": "1.20",
                    "uom": "PCS",
                },
                {
                    "item_code": "SPS-FRC-001",
                    "description": "Fries Carton Medium",
                    "qty": 5000,
                    "price": "0.65",
                    "uom": "PCS",
                },
                {
                    "item_code": "SPS-NAP-001",
                    "description": "Napkin Dispenser Pack",
                    "qty": 2000,
                    "price": "0.30",
                    "uom": "PKT",
                },
                {
                    "item_code": "SPS-BAG-001",
                    "description": "Delivery Paper Bag Large",
                    "qty": 1500,
                    "price": "0.95",
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO: Within tolerance
        # Very small variance that falls inside tolerance
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1020",
            "vendor": rsrc,
            "po_date": BASE_DATE - timedelta(days=9),
            "notes": "Within tolerance - cleaning supplies minor variance",
            "lines": [
                {
                    "item_code": "RSRC-GLV-001",
                    "description": "Food Safe Gloves Medium",
                    "qty": 1000,
                    "price": "12.50",
                    "uom": "BOX",
                },
                {
                    "item_code": "RSRC-DEG-001",
                    "description": "Degreaser Kitchen Heavy Duty",
                    "qty": 200,
                    "price": "45.00",
                    "uom": "LTR",
                },
                {
                    "item_code": "RSRC-SAN-001",
                    "description": "Sanitizer Surface Use",
                    "qty": 300,
                    "price": "28.00",
                    "uom": "LTR",
                },
            ],
        },
        # ---------------------------------------------------------------
        # SCENARIO: Multiple exceptions in one invoice
        # Qty mismatch + price mismatch + missing GRN line
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1021",
            "vendor": jqss,
            "po_date": BASE_DATE - timedelta(days=7),
            "notes": "Multiple exceptions - sauces & condiments with compound issues",
            "lines": [
                {
                    "item_code": "JQSS-MUS-001",
                    "description": "Mustard Sauce Dispenser Pack",
                    "qty": 180,
                    "price": "48.00",
                    "uom": "BOX",
                },
                {
                    "item_code": "JQSS-MAY-001",
                    "description": "Mayonnaise Food Service Pack",
                    "qty": 250,
                    "price": "52.00",
                    "uom": "BOX",
                },
                {
                    "item_code": "JQSS-SAL-001",
                    "description": "Salt Sachet",
                    "qty": 5000,
                    "price": "0.15",
                    "uom": "PCS",
                },
                {
                    "item_code": "JQSS-PEP-001",
                    "description": "Black Pepper Sachet",
                    "qty": 3000,
                    "price": "0.18",
                    "uom": "PCS",
                },
                {
                    "item_code": "JQSS-KET-002",
                    "description": "Ketchup Sachet",
                    "qty": 8000,
                    "price": "0.12",
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # ADDITIONAL PO - Dairy milkshake + soft serve
        # Full delivery; used for dashboard / reporting demos
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1022",
            "vendor": akd,
            "po_date": BASE_DATE - timedelta(days=6),
            "notes": "Dairy order - milkshake and soft serve for Dammam Cold Store",
            "lines": [
                {
                    "item_code": "AKD-MSV-001",
                    "description": "Milkshake Vanilla Mix",
                    "qty": 120,
                    "price": "85.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AKD-SSM-001",
                    "description": "Soft Serve Dairy Mix",
                    "qty": 100,
                    "price": "92.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "AKD-MSC-001",
                    "description": "Milkshake Chocolate Mix",
                    "qty": 80,
                    "price": "88.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # ADDITIONAL PO - Bulk frozen (fries + hash browns + nuggets)
        # Two staggered GRNs
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1023",
            "vendor": dccl,
            "po_date": BASE_DATE - timedelta(days=5),
            "notes": "Bulk frozen order - Ramadan stock-up for Riyadh warehouse",
            "lines": [
                {
                    "item_code": "DCCL-FRY-001",
                    "description": "French Fries 2.5kg Frozen",
                    "qty": 1200,
                    "price": "78.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "DCCL-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty": 600,
                    "price": "95.00",
                    "uom": "CTN",
                },
                {
                    "item_code": "DCCL-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty": 500,
                    "price": "145.00",
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # ADDITIONAL PO - Cooking oil bulk
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1024",
            "vendor": neo,
            "po_date": BASE_DATE - timedelta(days=4),
            "notes": "Cooking oil bulk - fryer grade for all branches",
            "lines": [
                {
                    "item_code": "NEO-OIL-001",
                    "description": "Cooking Oil Fryer Grade 20L",
                    "qty": 500,
                    "price": "32.00",
                    "uom": "LTR",
                },
                {
                    "item_code": "NEO-OIL-002",
                    "description": "Cooking Oil Fryer Grade 5L",
                    "qty": 300,
                    "price": "9.50",
                    "uom": "LTR",
                },
            ],
        },
        # ---------------------------------------------------------------
        # ADDITIONAL PO - Packaging consumables
        # ---------------------------------------------------------------
        {
            "po_number": "PO-KSA-1025",
            "vendor": sps,
            "po_date": BASE_DATE - timedelta(days=3),
            "notes": "Packaging consumables - napkins, straws, bags, cup carriers",
            "lines": [
                {
                    "item_code": "SPS-NAP-001",
                    "description": "Napkin Dispenser Pack",
                    "qty": 4000,
                    "price": "0.30",
                    "uom": "PKT",
                },
                {
                    "item_code": "SPS-STR-001",
                    "description": "Cold Drink Straw Wrapped",
                    "qty": 10000,
                    "price": "0.08",
                    "uom": "PCS",
                },
                {
                    "item_code": "SPS-BAG-001",
                    "description": "Delivery Paper Bag Large",
                    "qty": 3000,
                    "price": "0.95",
                    "uom": "PCS",
                },
                {
                    "item_code": "SPS-CPC-001",
                    "description": "Cup Carrier 4-Slot",
                    "qty": 2000,
                    "price": "0.55",
                    "uom": "PCS",
                },
            ],
        },
    ]


# ===================================================================
#  GOODS RECEIPT NOTES
# ===================================================================

def create_grns(
    pos: dict,
    po_lines: dict,
    vendors: dict,
    admin_user: User,
):
    """
    Create ~30 GRNs with ~70 line items covering all scenarios.
    Returns (dict[grn_number] -> GRN, dict[grn_number] -> [GRNLineItem...]).
    """
    v = vendors
    grn_defs = _build_grn_definitions(pos, po_lines, v)

    grns = {}
    grn_lines = {}
    warehouse_user = User.objects.filter(
        department="Warehouse"
    ).first() or admin_user

    for gdef in grn_defs:
        grn_number = gdef["grn_number"]
        lines_data = gdef.pop("lines")

        grn, _ = GoodsReceiptNote.objects.get_or_create(
            grn_number=grn_number,
            defaults={
                "purchase_order": gdef["purchase_order"],
                "vendor": gdef["vendor"],
                "receipt_date": gdef["receipt_date"],
                "status": gdef.get("status", "RECEIVED"),
                "warehouse": gdef["warehouse"],
                "receiver_name": gdef.get("receiver_name", warehouse_user.get_full_name()),
                "notes": gdef.get("notes", ""),
                "created_by": warehouse_user,
            },
        )
        grns[grn_number] = grn

        created_lines = []
        for idx, ln in enumerate(lines_data, start=1):
            qty_recv = _d(ln["qty_received"])
            qty_acc = _d(ln.get("qty_accepted", ln["qty_received"]))
            qty_rej = _d(ln.get("qty_rejected", 0))
            grn_line, _ = GRNLineItem.objects.get_or_create(
                grn=grn,
                line_number=idx,
                defaults={
                    "po_line": ln.get("po_line"),
                    "item_code": ln["item_code"],
                    "description": ln["description"],
                    "quantity_received": qty_recv,
                    "quantity_accepted": qty_acc,
                    "quantity_rejected": qty_rej,
                    "unit_of_measure": ln["uom"],
                },
            )
            created_lines.append(grn_line)
        grn_lines[grn_number] = created_lines

    return grns, grn_lines


def _pl(po_lines: dict, po_number: str, line_index: int):
    """Helper: get PO line item by PO number and 0-based index."""
    return po_lines[po_number][line_index]


def _build_grn_definitions(pos, po_lines, vendors):
    """Return list of GRN definition dicts with nested line items."""
    v = vendors
    afs = v["VND-AFS-001"]
    gff = v["VND-GFF-002"]
    awp = v["VND-AWP-003"]
    sps = v["VND-SPS-004"]
    rbc = v["VND-RBC-005"]
    dccl = v["VND-DCCL-006"]
    rsrc = v["VND-RSRC-007"]
    neo = v["VND-NEO-008"]
    akd = v["VND-AKD-009"]
    jqss = v["VND-JQSS-010"]

    return [
        # ---------------------------------------------------------------
        # GRN 1 - Scenario 1: Perfect match - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1001-A",
            "purchase_order": pos["PO-KSA-1001"],
            "vendor": afs,
            "receipt_date": BASE_DATE - timedelta(days=17),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 1: Full receipt - buns, lettuce, pickles",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1001", 0),
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty_received": 500,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1001", 1),
                    "item_code": "AFS-LET-001",
                    "description": "Shredded Lettuce Food Service Pack",
                    "qty_received": 200,
                    "uom": "PKT",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1001", 2),
                    "item_code": "AFS-PKL-001",
                    "description": "Pickle Slice Jar Bulk",
                    "qty_received": 100,
                    "uom": "BOX",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 2 - Scenario 2: Qty mismatch - GRN matches PO (600), not invoice
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1002-A",
            "purchase_order": pos["PO-KSA-1002"],
            "vendor": afs,
            "receipt_date": BASE_DATE - timedelta(days=15),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 2: Full receipt matching PO qty 600 sesame buns + 300 regular",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1002", 0),
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty_received": 600,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1002", 1),
                    "item_code": "AFS-BUN-002",
                    "description": "Regular Burger Bun 4 inch",
                    "qty_received": 300,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 3 - Scenario 3: Price mismatch - full receipt (price lives on PO)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1003-A",
            "purchase_order": pos["PO-KSA-1003"],
            "vendor": gff,
            "receipt_date": BASE_DATE - timedelta(days=19),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 3: Full receipt - beef patties 4:1 and 10:1",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1003", 0),
                    "item_code": "GFF-BPT-001",
                    "description": "McD Beef Patty 4:1 Frozen",
                    "qty_received": 300,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1003", 1),
                    "item_code": "GFF-BPT-002",
                    "description": "McD Beef Patty 10:1 Frozen",
                    "qty_received": 200,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 4 - Scenario 4: VAT mismatch - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1004-A",
            "purchase_order": pos["PO-KSA-1004"],
            "vendor": akd,
            "receipt_date": BASE_DATE - timedelta(days=12),
            "warehouse": "WH-JED-01",
            "notes": "Scenario 4: Full receipt - cheese slices and butter",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1004", 0),
                    "item_code": "AKD-CHS-001",
                    "description": "Cheese Slice Processed",
                    "qty_received": 400,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1004", 1),
                    "item_code": "AKD-BTR-001",
                    "description": "Butter Portion Pack",
                    "qty_received": 200,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 5 - Scenario 5: Duplicate invoice - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1005-A",
            "purchase_order": pos["PO-KSA-1005"],
            "vendor": dccl,
            "receipt_date": BASE_DATE - timedelta(days=22),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 5: Full receipt - French fries 2.5kg and 1kg",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1005", 0),
                    "item_code": "DCCL-FRY-001",
                    "description": "French Fries 2.5kg Frozen",
                    "qty_received": 800,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1005", 1),
                    "item_code": "DCCL-FRY-002",
                    "description": "French Fries 1kg Frozen",
                    "qty_received": 400,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # (No GRN for PO-KSA-1007 - Scenario 7: Missing GRN)
        # ---------------------------------------------------------------
        # ---------------------------------------------------------------
        # GRN 6A - Scenario 8: Multi-GRN - first delivery (Dammam)
        # Received: 300/500 beef, 400/400 chicken, 0/300 nuggets, 0/250 hash
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-DMM-1008-A",
            "purchase_order": pos["PO-KSA-1008"],
            "vendor": gff,
            "receipt_date": BASE_DATE - timedelta(days=27),
            "warehouse": "WH-DMM-01",
            "notes": "Scenario 8: First staggered delivery - partial beef + full chicken",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1008", 0),
                    "item_code": "GFF-BPT-001",
                    "description": "McD Beef Patty 4:1 Frozen",
                    "qty_received": 300,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1008", 1),
                    "item_code": "GFF-CPT-001",
                    "description": "Chicken Patty Breaded Frozen",
                    "qty_received": 400,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 6B - Scenario 8: Multi-GRN - second delivery
        # Remaining beef + full nuggets
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-DMM-1008-B",
            "purchase_order": pos["PO-KSA-1008"],
            "vendor": gff,
            "receipt_date": BASE_DATE - timedelta(days=24),
            "warehouse": "WH-DMM-01",
            "notes": "Scenario 8: Second delivery - remaining beef + nuggets",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1008", 0),
                    "item_code": "GFF-BPT-001",
                    "description": "McD Beef Patty 4:1 Frozen",
                    "qty_received": 200,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1008", 2),
                    "item_code": "GFF-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty_received": 300,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 6C - Scenario 8: Multi-GRN - third delivery
        # Hash browns delivered last
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-DMM-1008-C",
            "purchase_order": pos["PO-KSA-1008"],
            "vendor": gff,
            "receipt_date": BASE_DATE - timedelta(days=21),
            "warehouse": "WH-DMM-01",
            "notes": "Scenario 8: Third delivery - hash browns",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1008", 3),
                    "item_code": "GFF-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty_received": 250,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 7A - Scenario 9: Invoice > GRN - partial receipt
        # 350/400 hash browns, 80/100 onion rings
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1009-A",
            "purchase_order": pos["PO-KSA-1009"],
            "vendor": dccl,
            "receipt_date": BASE_DATE - timedelta(days=13),
            "warehouse": "WH-RUH-01",
            "status": "PARTIAL",
            "notes": "Scenario 9: Partial receipt - cold-chain transport shortage",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1009", 0),
                    "item_code": "DCCL-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty_received": 350,
                    "qty_accepted": 350,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1009", 1),
                    "item_code": "DCCL-ONR-001",
                    "description": "Onion Rings Breaded Frozen",
                    "qty_received": 80,
                    "qty_accepted": 80,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 7B - Scenario 9: Late follow-up delivery
        # Additional 50 hash browns + 20 onion rings
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1009-B",
            "purchase_order": pos["PO-KSA-1009"],
            "vendor": dccl,
            "receipt_date": BASE_DATE - timedelta(days=8),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 9: Follow-up delivery - late arrival partial restock",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1009", 0),
                    "item_code": "DCCL-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty_received": 50,
                    "qty_accepted": 50,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 8 - Scenario 10: Low-confidence invoice - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1010-A",
            "purchase_order": pos["PO-KSA-1010"],
            "vendor": jqss,
            "receipt_date": BASE_DATE - timedelta(days=11),
            "warehouse": "WH-JED-01",
            "notes": "Scenario 10: Full receipt - ketchup and mustard",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1010", 0),
                    "item_code": "JQSS-KET-001",
                    "description": "Tomato Ketchup Bag-in-Box",
                    "qty_received": 200,
                    "uom": "BOX",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1010", 1),
                    "item_code": "JQSS-MUS-001",
                    "description": "Mustard Sauce Dispenser Pack",
                    "qty_received": 150,
                    "uom": "BOX",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 9 - Scenario 11A: Ambiguous PO (first candidate) - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1011-A",
            "purchase_order": pos["PO-KSA-1011"],
            "vendor": afs,
            "receipt_date": BASE_DATE - timedelta(days=7),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 11A: Full receipt for ambiguous PO candidate A",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1011", 0),
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty_received": 300,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 10 - Scenario 11B: Ambiguous PO (second candidate) - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1012-A",
            "purchase_order": pos["PO-KSA-1012"],
            "vendor": afs,
            "receipt_date": BASE_DATE - timedelta(days=5),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 11B: Full receipt for ambiguous PO candidate B",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1012", 0),
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty_received": 350,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 11 - Scenario 12: Arabic-English invoice - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1013-A",
            "purchase_order": pos["PO-KSA-1013"],
            "vendor": awp,
            "receipt_date": BASE_DATE - timedelta(days=16),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 12: Full receipt - poultry items",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1013", 0),
                    "item_code": "AWP-CPT-001",
                    "description": "Chicken Patty Breaded Frozen",
                    "qty_received": 350,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1013", 1),
                    "item_code": "AWP-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty_received": 200,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1013", 2),
                    "item_code": "AWP-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty_received": 150,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 12 - Scenario 13: Packaging mismatch - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1014-A",
            "purchase_order": pos["PO-KSA-1014"],
            "vendor": sps,
            "receipt_date": BASE_DATE - timedelta(days=14),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 13: Full receipt - cups and lids as per PO",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1014", 0),
                    "item_code": "SPS-CUP-001",
                    "description": "Paper Cup 16oz",
                    "qty_received": 5000,
                    "uom": "PCS",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1014", 1),
                    "item_code": "SPS-LID-001",
                    "description": "Plastic Lid 16oz",
                    "qty_received": 5000,
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 13 - Scenario 14: Location mismatch - received at WH-RUH-01
        # (Invoice will reference BR-JED-220 instead)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1015-A",
            "purchase_order": pos["PO-KSA-1015"],
            "vendor": rbc,
            "receipt_date": BASE_DATE - timedelta(days=10),
            "warehouse": "WH-RUH-01",
            "notes": "Scenario 14: Received at Riyadh warehouse - invoice says Jeddah branch",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1015", 0),
                    "item_code": "RBC-SYR-001",
                    "description": "Soft Drink Syrup Cola Bag-in-Box",
                    "qty_received": 100,
                    "uom": "BAG",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1015", 1),
                    "item_code": "RBC-SYR-002",
                    "description": "Soft Drink Syrup Fanta Bag-in-Box",
                    "qty_received": 80,
                    "uom": "BAG",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1015", 2),
                    "item_code": "RBC-SYR-003",
                    "description": "Soft Drink Syrup Sprite Bag-in-Box",
                    "qty_received": 60,
                    "uom": "BAG",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 14 - Scenario 15: Reviewed + corrected - full receipt
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1016-A",
            "purchase_order": pos["PO-KSA-1016"],
            "vendor": gff,
            "receipt_date": BASE_DATE - timedelta(days=18),
            "warehouse": "WH-JED-01",
            "notes": "Scenario 15: Full receipt - nuggets and chicken strips",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1016", 0),
                    "item_code": "GFF-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty_received": 250,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1016", 1),
                    "item_code": "GFF-CST-001",
                    "description": "Chicken Strips Frozen",
                    "qty_received": 180,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 15 - Closed PO: old full receipt (PO-KSA-1017)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1017-A",
            "purchase_order": pos["PO-KSA-1017"],
            "vendor": afs,
            "receipt_date": BASE_DATE - timedelta(days=55),
            "warehouse": "WH-RUH-01",
            "notes": "Closed PO: Historical full receipt before PO closure",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1017", 0),
                    "item_code": "AFS-BUN-001",
                    "description": "Sesame Burger Bun 4 inch",
                    "qty_received": 200,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1017", 1),
                    "item_code": "AFS-PKL-001",
                    "description": "Pickle Slice Jar Bulk",
                    "qty_received": 80,
                    "uom": "BOX",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1017", 2),
                    "item_code": "AFS-LET-001",
                    "description": "Shredded Lettuce Food Service Pack",
                    "qty_received": 100,
                    "uom": "PKT",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 16 - Over-receipt: PO 500 chicken -> GRN 520 (PO-KSA-1018)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-DMM-1018-A",
            "purchase_order": pos["PO-KSA-1018"],
            "vendor": awp,
            "receipt_date": BASE_DATE - timedelta(days=11),
            "warehouse": "WH-DMM-01",
            "notes": "Over-receipt: Received 520 vs PO 500 chicken patties + extras",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1018", 0),
                    "item_code": "AWP-CPT-001",
                    "description": "Chicken Patty Breaded Frozen",
                    "qty_received": 520,
                    "qty_accepted": 500,
                    "qty_rejected": 20,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1018", 1),
                    "item_code": "AWP-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty_received": 310,
                    "qty_accepted": 300,
                    "qty_rejected": 10,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1018", 2),
                    "item_code": "AWP-CWG-001",
                    "description": "Chicken Wings Frozen",
                    "qty_received": 150,
                    "qty_accepted": 150,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 17 - Amount mismatch packaging: full receipt (PO-KSA-1019)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1019-A",
            "purchase_order": pos["PO-KSA-1019"],
            "vendor": sps,
            "receipt_date": BASE_DATE - timedelta(days=8),
            "warehouse": "WH-RUH-01",
            "notes": "Amount mismatch: Full receipt - packaging supplies",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1019", 0),
                    "item_code": "SPS-BMC-001",
                    "description": "Big Mac Clamshell Box",
                    "qty_received": 3000,
                    "uom": "PCS",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1019", 1),
                    "item_code": "SPS-FRC-001",
                    "description": "Fries Carton Medium",
                    "qty_received": 5000,
                    "uom": "PCS",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1019", 2),
                    "item_code": "SPS-NAP-001",
                    "description": "Napkin Dispenser Pack",
                    "qty_received": 2000,
                    "uom": "PKT",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1019", 3),
                    "item_code": "SPS-BAG-001",
                    "description": "Delivery Paper Bag Large",
                    "qty_received": 1500,
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 18 - Within tolerance: full receipt (PO-KSA-1020)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1020-A",
            "purchase_order": pos["PO-KSA-1020"],
            "vendor": rsrc,
            "receipt_date": BASE_DATE - timedelta(days=6),
            "warehouse": "WH-RUH-01",
            "notes": "Within tolerance: Full receipt - cleaning supplies",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1020", 0),
                    "item_code": "RSRC-GLV-001",
                    "description": "Food Safe Gloves Medium",
                    "qty_received": 1000,
                    "uom": "BOX",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1020", 1),
                    "item_code": "RSRC-DEG-001",
                    "description": "Degreaser Kitchen Heavy Duty",
                    "qty_received": 200,
                    "uom": "LTR",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1020", 2),
                    "item_code": "RSRC-SAN-001",
                    "description": "Sanitizer Surface Use",
                    "qty_received": 300,
                    "uom": "LTR",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 19A - Multiple exceptions: partial receipt (PO-KSA-1021)
        # Mustard full, Mayo partial, Salt full, Pepper missing, Ketchup missing
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1021-A",
            "purchase_order": pos["PO-KSA-1021"],
            "vendor": jqss,
            "receipt_date": BASE_DATE - timedelta(days=5),
            "warehouse": "WH-JED-01",
            "status": "PARTIAL",
            "notes": "Multiple exceptions: First delivery - mustard, partial mayo, salt",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1021", 0),
                    "item_code": "JQSS-MUS-001",
                    "description": "Mustard Sauce Dispenser Pack",
                    "qty_received": 180,
                    "uom": "BOX",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1021", 1),
                    "item_code": "JQSS-MAY-001",
                    "description": "Mayonnaise Food Service Pack",
                    "qty_received": 150,
                    "qty_accepted": 150,
                    "uom": "BOX",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1021", 2),
                    "item_code": "JQSS-SAL-001",
                    "description": "Salt Sachet",
                    "qty_received": 5000,
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 19B - Multiple exceptions: second partial delivery
        # Remaining mayo + pepper - ketchup sachet still missing
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1021-B",
            "purchase_order": pos["PO-KSA-1021"],
            "vendor": jqss,
            "receipt_date": BASE_DATE - timedelta(days=3),
            "warehouse": "WH-JED-01",
            "status": "PARTIAL",
            "notes": "Multiple exceptions: Second delivery - remaining mayo + pepper",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1021", 1),
                    "item_code": "JQSS-MAY-001",
                    "description": "Mayonnaise Food Service Pack",
                    "qty_received": 100,
                    "qty_accepted": 100,
                    "uom": "BOX",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1021", 3),
                    "item_code": "JQSS-PEP-001",
                    "description": "Black Pepper Sachet",
                    "qty_received": 3000,
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 20 - Dairy order: full receipt (PO-KSA-1022)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-DMM-1022-A",
            "purchase_order": pos["PO-KSA-1022"],
            "vendor": akd,
            "receipt_date": BASE_DATE - timedelta(days=4),
            "warehouse": "WH-DMM-01",
            "notes": "Dairy order: Full receipt - milkshake and soft serve mixes",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1022", 0),
                    "item_code": "AKD-MSV-001",
                    "description": "Milkshake Vanilla Mix",
                    "qty_received": 120,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1022", 1),
                    "item_code": "AKD-SSM-001",
                    "description": "Soft Serve Dairy Mix",
                    "qty_received": 100,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1022", 2),
                    "item_code": "AKD-MSC-001",
                    "description": "Milkshake Chocolate Mix",
                    "qty_received": 80,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 21A - Bulk frozen Ramadan stock: first delivery (PO-KSA-1023)
        # 800/1200 fries, 400/600 hash brown, 0/500 nuggets
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1023-A",
            "purchase_order": pos["PO-KSA-1023"],
            "vendor": dccl,
            "receipt_date": BASE_DATE - timedelta(days=3),
            "warehouse": "WH-RUH-01",
            "status": "PARTIAL",
            "notes": "Ramadan bulk: First delivery - partial fries + hash browns",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1023", 0),
                    "item_code": "DCCL-FRY-001",
                    "description": "French Fries 2.5kg Frozen",
                    "qty_received": 800,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1023", 1),
                    "item_code": "DCCL-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty_received": 400,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 21B - Bulk frozen Ramadan stock: second delivery
        # Remaining fries + hash brown + nuggets
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1023-B",
            "purchase_order": pos["PO-KSA-1023"],
            "vendor": dccl,
            "receipt_date": BASE_DATE - timedelta(days=1),
            "warehouse": "WH-RUH-01",
            "notes": "Ramadan bulk: Second delivery - remaining items",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1023", 0),
                    "item_code": "DCCL-FRY-001",
                    "description": "French Fries 2.5kg Frozen",
                    "qty_received": 400,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1023", 1),
                    "item_code": "DCCL-HSB-001",
                    "description": "Hash Brown Triangle Frozen",
                    "qty_received": 200,
                    "uom": "CTN",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1023", 2),
                    "item_code": "DCCL-NUG-001",
                    "description": "Nuggets Premium Frozen",
                    "qty_received": 500,
                    "uom": "CTN",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 22 - Cooking oil bulk: full receipt (PO-KSA-1024)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-RUH-1024-A",
            "purchase_order": pos["PO-KSA-1024"],
            "vendor": neo,
            "receipt_date": BASE_DATE - timedelta(days=2),
            "warehouse": "WH-RUH-01",
            "notes": "Oil bulk: Full receipt - 20L and 5L cooking oil",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1024", 0),
                    "item_code": "NEO-OIL-001",
                    "description": "Cooking Oil Fryer Grade 20L",
                    "qty_received": 500,
                    "uom": "LTR",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1024", 1),
                    "item_code": "NEO-OIL-002",
                    "description": "Cooking Oil Fryer Grade 5L",
                    "qty_received": 300,
                    "uom": "LTR",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 23 - Packaging consumables: full receipt (PO-KSA-1025)
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1025-A",
            "purchase_order": pos["PO-KSA-1025"],
            "vendor": sps,
            "receipt_date": BASE_DATE - timedelta(days=1),
            "warehouse": "WH-JED-01",
            "notes": "Packaging consumables: Full receipt - napkins, straws, bags, carriers",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1025", 0),
                    "item_code": "SPS-NAP-001",
                    "description": "Napkin Dispenser Pack",
                    "qty_received": 4000,
                    "uom": "PKT",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1025", 1),
                    "item_code": "SPS-STR-001",
                    "description": "Cold Drink Straw Wrapped",
                    "qty_received": 10000,
                    "uom": "PCS",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1025", 2),
                    "item_code": "SPS-BAG-001",
                    "description": "Delivery Paper Bag Large",
                    "qty_received": 3000,
                    "uom": "PCS",
                },
                {
                    "po_line": _pl(po_lines, "PO-KSA-1025", 3),
                    "item_code": "SPS-CPC-001",
                    "description": "Cup Carrier 4-Slot",
                    "qty_received": 2000,
                    "uom": "PCS",
                },
            ],
        },
        # ---------------------------------------------------------------
        # GRN 24 - Extra small delivery for PO-1004 (VAT mismatch scenario)
        # A second receipt to bring total GRN count to 30
        # ---------------------------------------------------------------
        {
            "grn_number": "GRN-JED-1004-B",
            "purchase_order": pos["PO-KSA-1004"],
            "vendor": akd,
            "receipt_date": BASE_DATE - timedelta(days=10),
            "warehouse": "WH-JED-01",
            "notes": "VAT mismatch scenario: Small replacement delivery for damaged butter",
            "lines": [
                {
                    "po_line": _pl(po_lines, "PO-KSA-1004", 1),
                    "item_code": "AKD-BTR-001",
                    "description": "Butter Portion Pack",
                    "qty_received": 10,
                    "qty_accepted": 10,
                    "uom": "CTN",
                },
            ],
        },
    ]


# ===================================================================
#  AGENT & TOOL DEFINITIONS
# ===================================================================

def create_agent_definitions(admin):
    """Create 7 agent definitions with config_json and allowed_tools."""
    agents = [
        {
            "agent_type": AgentType.INVOICE_UNDERSTANDING,
            "name": "Invoice Understanding Agent",
            "description": "Analyzes invoice structure, fields, and extraction quality. Identifies low-confidence fields and suggests corrections.",
            "config_json": {
                "allowed_tools": ["invoice_details", "vendor_search"],
                "confidence_threshold": 0.70,
            },
        },
        {
            "agent_type": AgentType.PO_RETRIEVAL,
            "name": "PO Retrieval Agent",
            "description": "Recovers missing or mismatched POs using fuzzy PO number normalization, vendor search, and amount matching.",
            "config_json": {
                "allowed_tools": ["po_lookup", "vendor_search", "invoice_details"],
                "max_candidates": 5,
            },
        },
        {
            "agent_type": AgentType.GRN_RETRIEVAL,
            "name": "GRN Specialist Agent",
            "description": "Retrieves and analyzes GRN data for a PO. Handles multi-GRN aggregation, partial receipts, and missing GRN scenarios.",
            "config_json": {
                "allowed_tools": ["grn_lookup", "po_lookup", "invoice_details"],
            },
        },
        {
            "agent_type": AgentType.RECONCILIATION_ASSIST,
            "name": "Reconciliation Assist Agent",
            "description": "Provides detailed reconciliation analysis with line-by-line comparison and variance explanation.",
            "config_json": {
                "allowed_tools": ["reconciliation_summary", "invoice_details", "po_lookup", "grn_lookup", "exception_list"],
            },
        },
        {
            "agent_type": AgentType.EXCEPTION_ANALYSIS,
            "name": "Exception Analysis Agent",
            "description": "Analyzes reconciliation exceptions, determines root causes, and recommends resolution actions.",
            "config_json": {
                "allowed_tools": ["exception_list", "invoice_details", "po_lookup", "grn_lookup", "reconciliation_summary"],
            },
        },
        {
            "agent_type": AgentType.REVIEW_ROUTING,
            "name": "Review Routing Agent",
            "description": "Determines the optimal reviewer or team for a reconciliation case based on exception types, amounts, and complexity.",
            "config_json": {
                "allowed_tools": ["exception_list", "reconciliation_summary"],
            },
        },
        {
            "agent_type": AgentType.CASE_SUMMARY,
            "name": "Case Summary Agent",
            "description": "Generates a comprehensive summary of a reconciliation case including all findings, agent decisions, and recommendations.",
            "config_json": {
                "allowed_tools": ["reconciliation_summary", "exception_list", "invoice_details", "po_lookup", "grn_lookup"],
            },
        },
    ]

    created = 0
    for agent_data in agents:
        _, was_created = AgentDefinition.objects.get_or_create(
            agent_type=agent_data["agent_type"],
            defaults={
                "name": agent_data["name"],
                "description": agent_data["description"],
                "enabled": True,
                "config_json": agent_data["config_json"],
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
    return created


def create_tool_definitions(admin):
    """Create 6 tool definitions for the agent tool registry."""
    tools = [
        {
            "name": "po_lookup",
            "description": "Look up a Purchase Order by PO number. Returns header details and line items.",
            "module_path": "apps.tools.registry.tools.POLookupTool",
            "input_schema": {"type": "object", "properties": {"po_number": {"type": "string"}}, "required": ["po_number"]},
        },
        {
            "name": "grn_lookup",
            "description": "Retrieve Goods Receipt Notes for a Purchase Order. Returns GRN details and line items.",
            "module_path": "apps.tools.registry.tools.GRNLookupTool",
            "input_schema": {"type": "object", "properties": {"po_number": {"type": "string"}}, "required": ["po_number"]},
        },
        {
            "name": "vendor_search",
            "description": "Search for vendors by name, code, or alias. Returns matching vendors with similarity scores.",
            "module_path": "apps.tools.registry.tools.VendorSearchTool",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
        {
            "name": "invoice_details",
            "description": "Get full invoice details including line items, extraction data, and status.",
            "module_path": "apps.tools.registry.tools.InvoiceDetailsTool",
            "input_schema": {"type": "object", "properties": {"invoice_id": {"type": "integer"}}, "required": ["invoice_id"]},
        },
        {
            "name": "exception_list",
            "description": "List all exceptions for a reconciliation result with type, severity, and details.",
            "module_path": "apps.tools.registry.tools.ExceptionListTool",
            "input_schema": {"type": "object", "properties": {"result_id": {"type": "integer"}}, "required": ["result_id"]},
        },
        {
            "name": "reconciliation_summary",
            "description": "Get reconciliation summary for a result including match status, scores, and line comparisons.",
            "module_path": "apps.tools.registry.tools.ReconciliationSummaryTool",
            "input_schema": {"type": "object", "properties": {"result_id": {"type": "integer"}}, "required": ["result_id"]},
        },
    ]

    created = 0
    for tool_data in tools:
        _, was_created = ToolDefinition.objects.get_or_create(
            name=tool_data["name"],
            defaults={
                "description": tool_data["description"],
                "module_path": tool_data["module_path"],
                "input_schema": tool_data["input_schema"],
                "enabled": True,
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
    return created


# ===================================================================
#  COMMAND CLASS
# ===================================================================

class Command(BaseCommand):
    help = (
        "Seed Saudi McDonald's master distributor data: "
        "vendors, POs, GRNs - Phase 1 (master data)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all seed data before re-creating (PO-KSA-*, GRN-*, VND-* records)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Saudi McD Seed Data - Phase 1: Master Data ===\n"))

        # 1. Users
        self.stdout.write("  Creating users...")
        users = create_users()
        admin = users["admin"]
        self.stdout.write(self.style.SUCCESS(f"    [OK] {len(users)} users ready"))

        # 2. Vendors + aliases
        self.stdout.write("  Creating vendors and aliases...")
        vendors, alias_count = create_vendors_and_aliases(admin)
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] {len(vendors)} vendors, {alias_count} new aliases"
        ))

        # 3. Purchase Orders
        self.stdout.write("  Creating purchase orders...")
        pos, po_lines = create_purchase_orders(vendors, admin)
        total_po_lines = sum(len(lines) for lines in po_lines.values())
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] {len(pos)} POs, {total_po_lines} PO line items"
        ))

        # 4. GRNs
        self.stdout.write("  Creating goods receipt notes...")
        grns, grn_lines = create_grns(pos, po_lines, vendors, admin)
        total_grn_lines = sum(len(lines) for lines in grn_lines.values())
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] {len(grns)} GRNs, {total_grn_lines} GRN line items"
        ))

        # 5. Agent definitions
        self.stdout.write("  Creating agent definitions...")
        agent_count = create_agent_definitions(admin)
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] {agent_count} agent definitions"
        ))

        # 6. Tool definitions
        self.stdout.write("  Creating tool definitions...")
        tool_count = create_tool_definitions(admin)
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] {tool_count} tool definitions"
        ))

        # Summary
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Seed Data Summary ==="))
        self.stdout.write(f"  Users:              {len(users)}")
        self.stdout.write(f"  Vendors:            {len(vendors)}")
        self.stdout.write(f"  Vendor Aliases:     {alias_count}")
        self.stdout.write(f"  Purchase Orders:    {len(pos)}")
        self.stdout.write(f"  PO Line Items:      {total_po_lines}")
        self.stdout.write(f"  GRNs:               {len(grns)}")
        self.stdout.write(f"  GRN Line Items:     {total_grn_lines}")
        self.stdout.write(f"  Agent Definitions:  {AgentDefinition.objects.count()}")
        self.stdout.write(f"  Tool Definitions:   {ToolDefinition.objects.count()}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("=== Locations Referenced ==="))
        for code, desc in LOCATIONS.items():
            self.stdout.write(f"  {code:14s} - {desc}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("=== Scenario Map ==="))
        scenario_map = [
            ("PO-KSA-1001", "Scenario 1:  Perfect 3-way match"),
            ("PO-KSA-1002", "Scenario 2:  Quantity mismatch on buns"),
            ("PO-KSA-1003", "Scenario 3:  Price mismatch on frozen patties"),
            ("PO-KSA-1004", "Scenario 4:  VAT mismatch on dairy"),
            ("PO-KSA-1005", "Scenario 5:  Duplicate invoice"),
            ("(no PO)",      "Scenario 6:  Missing PO - invoice refs PO-KSA-9999"),
            ("PO-KSA-1007", "Scenario 7:  Missing GRN - oil not yet received"),
            ("PO-KSA-1008", "Scenario 8:  Multiple GRNs - staggered frozen delivery"),
            ("PO-KSA-1009", "Scenario 9:  Invoice exceeds received stock"),
            ("PO-KSA-1010", "Scenario 10: Low-confidence scanned invoice"),
            ("PO-KSA-1011/12", "Scenario 11: Ambiguous PO candidates"),
            ("PO-KSA-1013", "Scenario 12: Mixed Arabic-English invoice"),
            ("PO-KSA-1014", "Scenario 13: Packaging item mismatch"),
            ("PO-KSA-1015", "Scenario 14: Warehouse vs branch destination mismatch"),
            ("PO-KSA-1016", "Scenario 15: Reviewed and corrected case"),
            ("PO-KSA-1017", "Extra:       Closed PO scenario"),
            ("PO-KSA-1018", "Extra:       Over-receipt GRN"),
            ("PO-KSA-1019", "Extra:       Amount mismatch on packaging"),
            ("PO-KSA-1020", "Extra:       Within tolerance variance"),
            ("PO-KSA-1021", "Extra:       Multiple exceptions in one invoice"),
            ("PO-KSA-1022", "Extra:       Dairy order (dashboard demo)"),
            ("PO-KSA-1023", "Extra:       Bulk frozen Ramadan stock-up"),
            ("PO-KSA-1024", "Extra:       Cooking oil bulk"),
            ("PO-KSA-1025", "Extra:       Packaging consumables"),
        ]
        for po, desc in scenario_map:
            self.stdout.write(f"  {po:16s} -> {desc}")

        self.stdout.write(self.style.SUCCESS("\n[OK] Phase 1 master data seeding complete.\n"))

    def _flush(self):
        """Remove previously seeded KSA data and ALL dependent records."""
        self.stdout.write(self.style.WARNING("  Flushing existing seed data..."))
        counts = {}

        # --- Identify seed POs and invoices that reference them ---
        po_qs = PurchaseOrder.objects.filter(po_number__startswith="PO-KSA-")
        po_ids = list(po_qs.values_list("id", flat=True))

        # Invoices linked to seed POs (by FK) or by po_number pattern
        inv_qs = Invoice.objects.filter(
            po_number__startswith="PO-KSA-"
        ) | Invoice.objects.filter(
            recon_results__purchase_order_id__in=po_ids
        )
        inv_ids = list(inv_qs.values_list("id", flat=True))

        # --- Reconciliation cascade ---
        recon_results = ReconciliationResult.objects.filter(
            purchase_order_id__in=po_ids
        ) | ReconciliationResult.objects.filter(
            invoice_id__in=inv_ids
        )
        result_ids = list(recon_results.values_list("id", flat=True))
        run_ids = list(recon_results.values_list("run_id", flat=True).distinct())

        # Agent runs linked to these reconciliation results
        agent_runs = AgentRun.objects.filter(reconciliation_result_id__in=result_ids)
        agent_run_ids = list(agent_runs.values_list("id", flat=True))

        # Delete deepest children first -> up
        counts["tool_calls"] = ToolCall.objects.filter(agent_run_id__in=agent_run_ids).delete()[0]
        counts["decision_logs"] = DecisionLog.objects.filter(agent_run_id__in=agent_run_ids).delete()[0]
        counts["agent_messages"] = AgentMessage.objects.filter(agent_run_id__in=agent_run_ids).delete()[0]
        counts["agent_steps"] = AgentStep.objects.filter(agent_run_id__in=agent_run_ids).delete()[0]
        counts["agent_recommendations"] = AgentRecommendation.objects.filter(
            agent_run_id__in=agent_run_ids
        ).delete()[0]
        counts["agent_runs"] = agent_runs.delete()[0]

        # Reviews linked to reconciliation results
        review_qs = ReviewAssignment.objects.filter(reconciliation_result_id__in=result_ids)
        review_ids = list(review_qs.values_list("id", flat=True))
        counts["review_decisions"] = ReviewDecision.objects.filter(assignment_id__in=review_ids).delete()[0]
        counts["review_actions"] = ManualReviewAction.objects.filter(assignment_id__in=review_ids).delete()[0]
        counts["review_comments"] = ReviewComment.objects.filter(assignment_id__in=review_ids).delete()[0]
        counts["review_assignments"] = review_qs.delete()[0]

        # Reconciliation result lines + exceptions + results
        counts["recon_exceptions"] = ReconciliationException.objects.filter(
            result_id__in=result_ids
        ).delete()[0]
        counts["recon_result_lines"] = ReconciliationResultLine.objects.filter(
            result_id__in=result_ids
        ).delete()[0]
        counts["recon_results"] = recon_results.delete()[0]
        counts["recon_runs"] = ReconciliationRun.objects.filter(id__in=run_ids).delete()[0]

        # Invoices and invoice lines
        counts["invoice_lines"] = InvoiceLineItem.objects.filter(invoice_id__in=inv_ids).delete()[0]
        counts["invoices"] = Invoice.objects.filter(id__in=inv_ids).delete()[0]

        # Document uploads linked to flushed invoices
        upload_ids = list(
            DocumentUpload.objects.filter(invoices__id__in=inv_ids).values_list("id", flat=True)
        )
        counts["doc_uploads"] = DocumentUpload.objects.filter(id__in=upload_ids).delete()[0]

        # Audit events referencing seed entities
        counts["audit_events"] = AuditEvent.objects.filter(
            entity_type__in=["Invoice", "PurchaseOrder", "ReconciliationResult", "ReviewAssignment"],
            entity_id__in=inv_ids + po_ids + result_ids + review_ids,
        ).delete()[0]
        counts["processing_logs"] = ProcessingLog.objects.filter(
            invoice_id__in=inv_ids
        ).delete()[0] + ProcessingLog.objects.filter(
            reconciliation_result_id__in=result_ids
        ).delete()[0] + ProcessingLog.objects.filter(
            agent_run_id__in=agent_run_ids
        ).delete()[0]

        # --- Core master data ---
        # GRN lines and GRNs
        grn_qs = GoodsReceiptNote.objects.filter(grn_number__startswith="GRN-")
        counts["grns"] = grn_qs.count()
        GRNLineItem.objects.filter(grn__in=grn_qs).delete()
        grn_qs.delete()

        # PO lines and POs
        counts["pos"] = po_qs.count()
        PurchaseOrderLineItem.objects.filter(purchase_order__in=po_qs).delete()
        po_qs.delete()

        # Vendor aliases, then vendors
        vendor_qs = Vendor.objects.filter(code__startswith="VND-")
        VendorAlias.objects.filter(vendor__in=vendor_qs).delete()
        counts["vendors"] = vendor_qs.count()
        vendor_qs.delete()

        # Agent and tool definitions
        counts["agent_defs"] = AgentDefinition.objects.all().delete()[0]
        counts["tool_defs"] = ToolDefinition.objects.all().delete()[0]

        # Seed users
        seed_emails = [u["email"] for u in USERS_DATA]
        User.objects.filter(email__in=seed_emails).delete()

        # Print summary
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] Flushed: {counts['vendors']} vendors, {counts['pos']} POs, "
            f"{counts['grns']} GRNs, {counts['invoices']} invoices"
        ))
        related = {
            k: v for k, v in counts.items()
            if k not in ('vendors', 'pos', 'grns', 'invoices') and v > 0
        }
        if related:
            parts = [f"{v} {k.replace('_', ' ')}" for k, v in related.items()]
            self.stdout.write(self.style.SUCCESS(f"    [OK] Related: {', '.join(parts)}"))
