"""
Management command: seed_invoice_test_data

Seeds 12 invoice-side test scenarios (SCN-KSA-001 through SCN-KSA-012)
for reconciliation testing against already-seeded PO/GRN master data
(from seed_saudi_mcd_data).

Creates ONLY:
  - Invoice headers
  - InvoiceLineItem records

Does NOT create:
  - ReconciliationRun, ReconciliationResult, ReconciliationException
  - AgentRun, AgentStep, ToolCall, DecisionLog
  - ReviewAssignment, ReviewComment, ManualReviewAction
  - AuditEvent

Usage:
    python manage.py seed_invoice_test_data
    python manage.py seed_invoice_test_data --flush
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.core.enums import InvoiceStatus
from apps.core.utils import normalize_po_number, normalize_string
from apps.documents.models import (
    GoodsReceiptNote,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.vendors.models import Vendor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VAT_RATE = Decimal("0.15")
BASE_DATE = date(2026, 2, 15)        # Same as master seed
INVOICE_DATE = BASE_DATE + timedelta(days=5)  # Invoices arrive ~5 days later

# All 12 scenario invoice numbers for flush/query
SCENARIO_INVOICE_NUMBERS = [
    "INV-AFS-2026-001",   # SCN-KSA-001
    "INV-AFS-2026-002",   # SCN-KSA-002
    "INV-GFF-2026-003",   # SCN-KSA-003
    "INV-SPS-2026-004",   # SCN-KSA-004
    "INV-FAKE-2026-005",  # SCN-KSA-005
    "INV-RBC-2026-006",   # SCN-KSA-006
    "INV-GFF-2026-007",   # SCN-KSA-007
    "INV-DCCL-2026-008",  # SCN-KSA-008 (original)
    "INV-DCCL-2026-008",  # SCN-KSA-008 (duplicate — same number)
    "INV-AWP-2026-009",   # SCN-KSA-009
    "INV-RBC-2026-010",   # SCN-KSA-010
    "INV-AKD-2026-011",   # SCN-KSA-011
    "INV-SPS-2026-012",   # SCN-KSA-012
]


def _d(val) -> Decimal:
    return Decimal(str(val))


def _line_amt(qty, price) -> Decimal:
    return (_d(qty) * _d(price)).quantize(Decimal("0.01"))


def _tax(amount) -> Decimal:
    return (amount * VAT_RATE).quantize(Decimal("0.01"))


# ===================================================================
#  HELPERS
# ===================================================================

def get_vendor(code: str) -> Vendor:
    """Look up a seeded vendor by code."""
    return Vendor.objects.get(code=code)


def get_po(po_number: str) -> PurchaseOrder:
    """Look up a seeded PO."""
    return PurchaseOrder.objects.get(po_number=po_number)


def get_po_lines(po_number: str) -> list[PurchaseOrderLineItem]:
    """Return PO line items ordered by line_number."""
    return list(
        PurchaseOrderLineItem.objects.filter(
            purchase_order__po_number=po_number
        ).order_by("line_number")
    )


def get_grns_for_po(po_number: str) -> list[GoodsReceiptNote]:
    """Return all GRNs for a PO."""
    return list(
        GoodsReceiptNote.objects.filter(
            purchase_order__po_number=po_number
        ).order_by("receipt_date")
    )


def get_ap_user() -> User:
    """Return the AP processor user for created_by."""
    return User.objects.filter(role="AP_PROCESSOR").first() or User.objects.first()


def create_invoice(
    *,
    scenario_code: str,
    invoice_number: str,
    vendor: Vendor | None,
    raw_vendor_name: str,
    po_number: str,
    invoice_date: date,
    subtotal: Decimal,
    tax_amount: Decimal,
    total_amount: Decimal,
    extraction_confidence: float = 0.92,
    status: str = InvoiceStatus.READY_FOR_RECON,
    notes: str = "",
    is_duplicate: bool = False,
    duplicate_of: Invoice | None = None,
    raw_po_number: str = "",
    raw_currency: str = "SAR",
    extraction_remarks: str = "",
) -> Invoice:
    """Create an Invoice header with both raw and normalized fields."""
    user = get_ap_user()
    inv = Invoice.objects.create(
        vendor=vendor,
        raw_vendor_name=raw_vendor_name,
        raw_invoice_number=invoice_number,
        raw_invoice_date=str(invoice_date),
        raw_po_number=raw_po_number or po_number,
        raw_currency=raw_currency,
        raw_subtotal=str(subtotal),
        raw_tax_amount=str(tax_amount),
        raw_total_amount=str(total_amount),
        invoice_number=invoice_number,
        normalized_invoice_number=normalize_string(invoice_number),
        invoice_date=invoice_date,
        po_number=po_number,
        normalized_po_number=normalize_po_number(po_number),
        currency="SAR",
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        status=status,
        extraction_confidence=extraction_confidence,
        extraction_remarks=extraction_remarks,
        is_duplicate=is_duplicate,
        duplicate_of=duplicate_of,
        notes=f"[{scenario_code}] {notes}",
        created_by=user,
    )
    return inv


def add_line(
    invoice: Invoice,
    *,
    line_number: int,
    raw_description: str,
    description: str,
    quantity: Decimal,
    unit_price: Decimal,
    tax_amount: Decimal | None = None,
    line_amount: Decimal | None = None,
    confidence: float = 0.92,
) -> InvoiceLineItem:
    """Create an InvoiceLineItem with raw + normalized fields."""
    amt = line_amount if line_amount is not None else _line_amt(quantity, unit_price)
    tax = tax_amount if tax_amount is not None else _tax(amt)
    return InvoiceLineItem.objects.create(
        invoice=invoice,
        line_number=line_number,
        raw_description=raw_description,
        raw_quantity=str(quantity),
        raw_unit_price=str(unit_price),
        raw_tax_amount=str(tax),
        raw_line_amount=str(amt),
        description=description,
        normalized_description=normalize_string(description),
        quantity=quantity,
        unit_price=unit_price,
        tax_amount=tax,
        line_amount=amt,
        extraction_confidence=confidence,
    )


# ===================================================================
#  12 SCENARIO FUNCTIONS
# ===================================================================


def create_scn_ksa_001_perfect_bun_match() -> Invoice:
    """
    SCN-KSA-001 — PERFECT MATCH ON BURGER BUN SUPPLY
    ─────────────────────────────────────────────────
    References PO-KSA-1001 (Arabian Food Supplies).
    Quantities and prices exactly match PO and GRN.
    Expected reconciliation: MATCHED
    """
    vendor = get_vendor("VND-AFS-001")
    po_lines = get_po_lines("PO-KSA-1001")

    lines_data = [
        {
            "raw": "Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش",
            "desc": "Sesame Burger Bun 4 inch",
            "qty": _d(500), "price": _d("45.00"),
        },
        {
            "raw": "Shredded Lettuce FSP / خس مقطع",
            "desc": "Shredded Lettuce Food Service Pack",
            "qty": _d(200), "price": _d("28.00"),
        },
        {
            "raw": "Pickle Slice Jar Bulk / مخلل شرائح",
            "desc": "Pickle Slice Jar Bulk",
            "qty": _d(100), "price": _d("35.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-001",
        invoice_number="INV-AFS-2026-001",
        vendor=vendor,
        raw_vendor_name="Arabian Food Supplies Co.",
        po_number="PO-KSA-1001",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.95,
        notes="Perfect 3-way match — buns, lettuce, pickles",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.95)

    return inv


def create_scn_ksa_002_fries_qty_mismatch() -> Invoice:
    """
    SCN-KSA-002 — QUANTITY MISMATCH ON FRENCH FRIES / BUNS
    ───────────────────────────────────────────────────────
    References PO-KSA-1002 (Arabian Food Supplies).
    Invoice claims 650 CTN sesame buns vs PO/GRN 600 CTN.
    Expected reconciliation: QTY_MISMATCH
    """
    vendor = get_vendor("VND-AFS-001")

    lines_data = [
        {
            "raw": "Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش",
            "desc": "Sesame Burger Bun 4 inch",
            "qty": _d(650),   # PO says 600 → mismatch
            "price": _d("45.00"),
        },
        {
            "raw": "Regular Burger Bun 4 inch / خبز برجر عادي ٤ انش",
            "desc": "Regular Burger Bun 4 inch",
            "qty": _d(300),
            "price": _d("40.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-002",
        invoice_number="INV-AFS-2026-002",
        vendor=vendor,
        raw_vendor_name="Arabian Food Supplies Co.",
        po_number="PO-KSA-1002",
        invoice_date=INVOICE_DATE + timedelta(days=1),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.93,
        notes="Qty mismatch — invoice 650 vs PO 600 sesame buns",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.93)

    return inv


def create_scn_ksa_003_patty_price_mismatch() -> Invoice:
    """
    SCN-KSA-003 — PRICE MISMATCH ON BEEF PATTIES
    ──────────────────────────────────────────────
    References PO-KSA-1003 (Gulf Frozen Foods).
    Invoice unit price 192 SAR vs PO unit price 185 SAR for beef patty 4:1.
    Expected reconciliation: PRICE_MISMATCH
    """
    vendor = get_vendor("VND-GFF-002")

    lines_data = [
        {
            "raw": "McD Beef Patty 4:1 Frozen / لحم برجر مجمد ٤:١",
            "desc": "McD Beef Patty 4:1 Frozen",
            "qty": _d(300),
            "price": _d("192.00"),   # PO says 185 → price mismatch
        },
        {
            "raw": "McD Beef Patty 10:1 Frozen / لحم برجر مجمد ١٠:١",
            "desc": "McD Beef Patty 10:1 Frozen",
            "qty": _d(200),
            "price": _d("120.00"),   # Matches PO
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-003",
        invoice_number="INV-GFF-2026-003",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="PO-KSA-1003",
        invoice_date=INVOICE_DATE + timedelta(days=2),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.94,
        notes="Price mismatch — invoice 192 SAR vs PO 185 SAR beef patty 4:1",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.94)

    return inv


def create_scn_ksa_004_vat_mismatch_packaging() -> Invoice:
    """
    SCN-KSA-004 — VAT MISMATCH ON PACKAGING MATERIALS
    ──────────────────────────────────────────────────
    References PO-KSA-1019 (Saudi Packaging Solutions).
    Invoice VAT is intentionally wrong: 12% instead of 15% on Big Mac boxes.
    Expected reconciliation: TAX_MISMATCH or AMOUNT_MISMATCH
    """
    vendor = get_vendor("VND-SPS-004")

    # PO-KSA-1019 lines: Big Mac Box 3000@1.20, Fries Carton 5000@0.65,
    #                     Napkin 2000@0.30, Delivery Bag 1500@0.95
    lines_data = [
        {
            "raw": "Big Mac Clamshell Box / علبة بيج ماك",
            "desc": "Big Mac Clamshell Box",
            "qty": _d(3000), "price": _d("1.20"),
            # Intentionally wrong VAT: 12% instead of 15%
            "tax_override": (_line_amt(3000, "1.20") * _d("0.12")).quantize(Decimal("0.01")),
        },
        {
            "raw": "Fries Carton Medium / كرتون بطاطس وسط",
            "desc": "Fries Carton Medium",
            "qty": _d(5000), "price": _d("0.65"),
            "tax_override": (_line_amt(5000, "0.65") * _d("0.12")).quantize(Decimal("0.01")),
        },
        {
            "raw": "Napkin Dispenser Pack / عبوة مناديل",
            "desc": "Napkin Dispenser Pack",
            "qty": _d(2000), "price": _d("0.30"),
            "tax_override": (_line_amt(2000, "0.30") * _d("0.12")).quantize(Decimal("0.01")),
        },
        {
            "raw": "Delivery Paper Bag Large / كيس ورقي كبير",
            "desc": "Delivery Paper Bag Large",
            "qty": _d(1500), "price": _d("0.95"),
            "tax_override": (_line_amt(1500, "0.95") * _d("0.12")).quantize(Decimal("0.01")),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    # Wrong total VAT at 12%
    tax = sum(l["tax_override"] for l in lines_data)

    inv = create_invoice(
        scenario_code="SCN-KSA-004",
        invoice_number="INV-SPS-2026-004",
        vendor=vendor,
        raw_vendor_name="Saudi Packaging Solutions",
        po_number="PO-KSA-1019",
        invoice_date=INVOICE_DATE + timedelta(days=1),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.91,
        notes="VAT mismatch — invoice uses 12% VAT instead of 15% on packaging",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], tax_amount=ld["tax_override"],
                 confidence=0.91)

    return inv


def create_scn_ksa_005_missing_po_cleaning() -> Invoice:
    """
    SCN-KSA-005 — MISSING PO ON CLEANING CHEMICALS INVOICE
    ───────────────────────────────────────────────────────
    Invoice references PO-KSA-9999 which does NOT exist in the database.
    Expected reconciliation: PO_NOT_FOUND
    """
    # Use RSRC vendor for cleaning chemicals but reference non-existent PO
    vendor = get_vendor("VND-RSRC-007")

    lines_data = [
        {
            "raw": "Sanitizer Surface Use / معقم أسطح",
            "desc": "Sanitizer Surface Use",
            "qty": _d(500), "price": _d("28.00"),
        },
        {
            "raw": "Degreaser Kitchen Heavy Duty / مزيل شحوم المطبخ",
            "desc": "Degreaser Kitchen Heavy Duty",
            "qty": _d(200), "price": _d("45.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-005",
        invoice_number="INV-FAKE-2026-005",
        vendor=vendor,
        raw_vendor_name="Red Sea Restaurant Consumables",
        po_number="PO-KSA-9999",                    # ← Does not exist
        raw_po_number="PO-KSA-9999",
        invoice_date=INVOICE_DATE + timedelta(days=3),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.89,
        notes="Missing PO — references non-existent PO-KSA-9999",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.89)

    return inv


def create_scn_ksa_006_missing_grn_syrup() -> Invoice:
    """
    SCN-KSA-006 — MISSING GRN ON SOFT DRINK SYRUP SHIPMENT
    ───────────────────────────────────────────────────────
    References PO-KSA-1007 (Najd Edible Oils) which has NO GRN.
    Expected reconciliation: GRN_NOT_FOUND
    """
    vendor = get_vendor("VND-NEO-008")

    lines_data = [
        {
            "raw": "Cooking Oil Fryer Grade 20L / زيت طبخ للقلي ٢٠ لتر",
            "desc": "Cooking Oil Fryer Grade 20L",
            "qty": _d(150), "price": _d("32.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-006",
        invoice_number="INV-RBC-2026-006",
        vendor=vendor,
        raw_vendor_name="Najd Edible Oils Trading",
        po_number="PO-KSA-1007",
        invoice_date=INVOICE_DATE + timedelta(days=2),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.91,
        notes="Missing GRN — PO-KSA-1007 has no GRN in database",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.91)

    return inv


def create_scn_ksa_007_multi_grn_nuggets() -> Invoice:
    """
    SCN-KSA-007 — MULTIPLE GRNS FOR FROZEN NUGGETS
    ───────────────────────────────────────────────
    References PO-KSA-1008 (Gulf Frozen Foods) which has 3 GRNs:
      GRN-DMM-1008-A: 300 beef + 400 chicken
      GRN-DMM-1008-B: 200 beef + 300 nuggets
      GRN-DMM-1008-C: 250 hash browns
    Invoice matches cumulative totals exactly.
    Expected reconciliation: MATCHED (tests GRN aggregation)
    """
    vendor = get_vendor("VND-GFF-002")

    lines_data = [
        {
            "raw": "McD Beef Patty 4:1 Frozen / لحم برجر ٤:١ مجمد",
            "desc": "McD Beef Patty 4:1 Frozen",
            "qty": _d(500), "price": _d("185.00"),     # 300+200 across GRNs
        },
        {
            "raw": "Chicken Patty Breaded Frozen / فيليه دجاج مجمد",
            "desc": "Chicken Patty Breaded Frozen",
            "qty": _d(400), "price": _d("158.00"),
        },
        {
            "raw": "Chicken Nuggets Frozen / ناجتس دجاج مجمد",
            "desc": "Nuggets Premium Frozen",
            "qty": _d(300), "price": _d("145.00"),
        },
        {
            "raw": "Hash Brown Triangle Frozen / هاش براون مثلث مجمد",
            "desc": "Hash Brown Triangle Frozen",
            "qty": _d(250), "price": _d("95.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-007",
        invoice_number="INV-GFF-2026-007",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="PO-KSA-1008",
        invoice_date=INVOICE_DATE + timedelta(days=4),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.94,
        notes="Multi-GRN aggregation — cumulative match across 3 GRNs",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.94)

    return inv


def create_scn_ksa_008_duplicate_invoice() -> tuple[Invoice, Invoice]:
    """
    SCN-KSA-008 — DUPLICATE INVOICE FROM SAME SUPPLIER
    ──────────────────────────────────────────────────
    Two invoices with identical invoice_number, vendor, PO reference, and amounts.
    References PO-KSA-1005 (Desert Cold Chain Logistics — French Fries).
    Expected reconciliation: DUPLICATE_INVOICE on the second invoice.
    """
    vendor = get_vendor("VND-DCCL-006")

    lines_data = [
        {
            "raw": "French Fries 2.5kg Frozen / بطاطس مقلية مجمدة ٢.٥ كجم",
            "desc": "French Fries 2.5kg Frozen",
            "qty": _d(800), "price": _d("78.00"),
        },
        {
            "raw": "French Fries 1kg Frozen / بطاطس مقلية مجمدة ١ كجم",
            "desc": "French Fries 1kg Frozen",
            "qty": _d(400), "price": _d("36.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    # --- First (original) invoice ---
    inv1 = create_invoice(
        scenario_code="SCN-KSA-008",
        invoice_number="INV-DCCL-2026-008",
        vendor=vendor,
        raw_vendor_name="Desert Cold Chain Logistics",
        po_number="PO-KSA-1005",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.93,
        notes="Duplicate invoice — ORIGINAL copy",
    )
    for i, ld in enumerate(lines_data, 1):
        add_line(inv1, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.93)

    # --- Second (duplicate) invoice — same number, amount, vendor ---
    inv2 = create_invoice(
        scenario_code="SCN-KSA-008",
        invoice_number="INV-DCCL-2026-008",
        vendor=vendor,
        raw_vendor_name="Desert Cold Chain Logistics",
        po_number="PO-KSA-1005",
        invoice_date=INVOICE_DATE + timedelta(days=3),  # Arrives 3 days later
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.93,
        is_duplicate=True,
        duplicate_of=inv1,
        notes="Duplicate invoice — SECOND copy (should be flagged)",
    )
    for i, ld in enumerate(lines_data, 1):
        add_line(inv2, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.93)

    return inv1, inv2


def create_scn_ksa_009_arabic_low_confidence() -> Invoice:
    """
    SCN-KSA-009 — MIXED ARABIC-ENGLISH LOW-CONFIDENCE INVOICE
    ──────────────────────────────────────────────────────────
    References PO-KSA-1013 (Al Watania Poultry).
    Invoice has Arabic-dominant descriptions and low extraction_confidence.
    Expected reconciliation: EXTRACTION_LOW_CONFIDENCE → review path
    """
    vendor = get_vendor("VND-AWP-003")

    lines_data = [
        {
            # Arabic-dominant, partial English
            "raw": "فيليه دجاج مغلف مجمد Chicken Patty Frzn",
            "desc": "Chicken Patty Breaded Frozen",
            "qty": _d(350), "price": _d("158.00"),
            "conf": 0.52,
        },
        {
            "raw": "ناجتس بريميوم مجمد Nuggets Prem.",
            "desc": "Nuggets Premium Frozen",
            "qty": _d(200), "price": _d("145.00"),
            "conf": 0.48,
        },
        {
            "raw": "هاش براون مثلث Hash Brwn Tri.",
            "desc": "Hash Brown Triangle Frozen",
            "qty": _d(150), "price": _d("95.00"),
            "conf": 0.55,
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-009",
        invoice_number="INV-AWP-2026-009",
        vendor=vendor,
        raw_vendor_name="الوطنية للدواجن",        # Arabic-only vendor name
        po_number="PO-KSA-1013",
        raw_po_number="PO-KSA 1013",               # Noisy OCR with space
        invoice_date=INVOICE_DATE + timedelta(days=1),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.51,                 # Below typical 0.75 threshold
        extraction_remarks="Low-quality scan; mixed Arabic/English; OCR artifacts detected",
        notes="Low-confidence Arabic-English invoice — should trigger review path",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=ld["conf"])

    return inv


def create_scn_ksa_010_location_mismatch() -> Invoice:
    """
    SCN-KSA-010 — BRANCH VS WAREHOUSE DESTINATION MISMATCH
    ───────────────────────────────────────────────────────
    References PO-KSA-1015 (Riyadh Beverage Concentrates).
    PO/GRN delivered to WH-RUH-01 (Riyadh Warehouse).
    Invoice references BR-JED-220 (Jeddah Branch) as delivery destination.
    Expected reconciliation: LOCATION_MISMATCH or custom exception
    """
    vendor = get_vendor("VND-RBC-005")

    lines_data = [
        {
            "raw": "Soft Drink Syrup Cola BiB / مركز مشروب غازي كولا",
            "desc": "Soft Drink Syrup Cola Bag-in-Box",
            "qty": _d(100), "price": _d("220.00"),
        },
        {
            "raw": "Soft Drink Syrup Fanta BiB / مركز مشروب غازي فانتا",
            "desc": "Soft Drink Syrup Fanta Bag-in-Box",
            "qty": _d(80), "price": _d("215.00"),
        },
        {
            "raw": "Soft Drink Syrup Sprite BiB / مركز مشروب غازي سبرايت",
            "desc": "Soft Drink Syrup Sprite Bag-in-Box",
            "qty": _d(60), "price": _d("210.00"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-010",
        invoice_number="INV-RBC-2026-010",
        vendor=vendor,
        raw_vendor_name="Riyadh Beverage Concentrates Co.",
        po_number="PO-KSA-1015",
        invoice_date=INVOICE_DATE + timedelta(days=2),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.90,
        # Delivery note references Jeddah branch, but PO/GRN is Riyadh warehouse
        extraction_remarks="Delivery Note: DN-JED-20455 | Destination: BR-JED-220",
        notes="Location mismatch — invoice says BR-JED-220 but GRN is WH-RUH-01",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.90)

    return inv


def create_scn_ksa_011_qty_exceeds_grn_cheese() -> Invoice:
    """
    SCN-KSA-011 — INVOICE EXCEEDS RECEIVED QUANTITY FOR CHEESE SLICES
    ─────────────────────────────────────────────────────────────────
    References PO-KSA-1004 (Al Khobar Dairy).
    GRN received 400 cheese + 210 butter (GRN-JED-1004-A: 400+200, GRN-JED-1004-B: 10 butter).
    Invoice claims 450 cheese slices → exceeds GRN by 50.
    Expected reconciliation: QTY_MISMATCH (GRN shortage)
    """
    vendor = get_vendor("VND-AKD-009")

    lines_data = [
        {
            "raw": "Cheese Slice Processed / شرائح جبن مطبوخة",
            "desc": "Cheese Slice Processed",
            "qty": _d(450),              # GRN has 400 → over-invoiced by 50
            "price": _d("62.00"),
        },
        {
            "raw": "Butter Portion Pack / عبوة زبدة",
            "desc": "Butter Portion Pack",
            "qty": _d(200),              # GRN has 200+10=210 accepted → within tolerance
            "price": _d("18.50"),
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-011",
        invoice_number="INV-AKD-2026-011",
        vendor=vendor,
        raw_vendor_name="Al Khobar Dairy Ingredients",
        po_number="PO-KSA-1004",
        invoice_date=INVOICE_DATE + timedelta(days=3),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.92,
        notes="Qty exceeds GRN — 450 cheese vs 400 received by warehouse",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.92)

    return inv


def create_scn_ksa_012_review_case_packaging() -> Invoice:
    """
    SCN-KSA-012 — REVIEWED AND CORRECTED CASE FOR PACKAGING SUPPLIES
    ────────────────────────────────────────────────────────────────
    References PO-KSA-1025 (Saudi Packaging Solutions).
    Invoice has ambiguous item description + slightly wrong line total
    that will require manual review/correction.
    Expected reconciliation: REQUIRES_REVIEW
    """
    vendor = get_vendor("VND-SPS-004")

    # PO-KSA-1025 lines: Napkin 4000@0.30, Straw 10000@0.08, Bag 3000@0.95, Carrier 2000@0.55
    lines_data = [
        {
            # Ambiguous description — doesn't cleanly map to PO item
            "raw": "مناديل عبوة ورقية Napkin Disp Pack",
            "desc": "Napkin Dispenser Pack",
            "qty": _d(4000), "price": _d("0.30"),
        },
        {
            "raw": "Cold Drink Straw Wrapped / شفاط مشروبات بارد",
            "desc": "Cold Drink Straw Wrapped",
            "qty": _d(10000), "price": _d("0.08"),
        },
        {
            # Slightly wrong description — "Medium" instead of "Large"
            "raw": "Delivery Paper Bag Medium / كيس ورقي وسط",
            "desc": "Delivery Paper Bag Medium",       # PO says "Large"
            "qty": _d(3000), "price": _d("0.95"),
        },
        {
            # Wrong line total — price is 0.60 instead of 0.55
            "raw": "Cup Carrier 4-Slot / حامل أكواب ٤ فتحات",
            "desc": "Cup Carrier 4-Slot",
            "qty": _d(2000),
            "price": _d("0.60"),                        # PO says 0.55 → slight mismatch
        },
    ]

    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-KSA-012",
        invoice_number="INV-SPS-2026-012",
        vendor=vendor,
        raw_vendor_name="Saudi Pack Solutions",         # Uses alias, not primary name
        po_number="PO-KSA-1025",
        invoice_date=INVOICE_DATE + timedelta(days=4),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.78,
        extraction_remarks="Description mismatch on line 3; price variance on line 4",
        notes="Review case — ambiguous description + price variance needs AP correction",
    )

    for i, ld in enumerate(lines_data, 1):
        add_line(inv, line_number=i, raw_description=ld["raw"],
                 description=ld["desc"], quantity=ld["qty"],
                 unit_price=ld["price"], confidence=0.78)

    return inv


# ===================================================================
#  COMMAND CLASS
# ===================================================================

class Command(BaseCommand):
    help = (
        "Seed 12 invoice test scenarios (SCN-KSA-001..012) for "
        "reconciliation testing against existing PO/GRN master data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously seeded invoice test data before re-creating.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== Invoice Test Data — 12 Scenarios (SCN-KSA-001..012) ===\n"
        ))

        # Pre-flight: verify master data exists
        po_count = PurchaseOrder.objects.filter(po_number__startswith="PO-KSA-").count()
        grn_count = GoodsReceiptNote.objects.filter(grn_number__startswith="GRN-").count()
        vendor_count = Vendor.objects.filter(code__startswith="VND-").count()
        if po_count == 0 or vendor_count == 0:
            self.stderr.write(self.style.ERROR(
                "  ERROR: Master data not found. Run 'seed_saudi_mcd_data' first."
            ))
            return

        self.stdout.write(
            f"  Master data found: {vendor_count} vendors, {po_count} POs, {grn_count} GRNs\n"
        )

        results = []

        # --- SCN-KSA-001 ---
        self.stdout.write("  SCN-KSA-001: Perfect match — burger bun supply...")
        inv = create_scn_ksa_001_perfect_bun_match()
        results.append(("SCN-KSA-001", inv.invoice_number, "Perfect 3-way match", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-002 ---
        self.stdout.write("  SCN-KSA-002: Quantity mismatch — sesame buns...")
        inv = create_scn_ksa_002_fries_qty_mismatch()
        results.append(("SCN-KSA-002", inv.invoice_number, "Qty mismatch 650 vs PO 600", "QTY_MISMATCH"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-003 ---
        self.stdout.write("  SCN-KSA-003: Price mismatch — beef patties...")
        inv = create_scn_ksa_003_patty_price_mismatch()
        results.append(("SCN-KSA-003", inv.invoice_number, "Price 192 vs PO 185 SAR", "PRICE_MISMATCH"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-004 ---
        self.stdout.write("  SCN-KSA-004: VAT mismatch — packaging materials...")
        inv = create_scn_ksa_004_vat_mismatch_packaging()
        results.append(("SCN-KSA-004", inv.invoice_number, "VAT 12% vs correct 15%", "TAX_MISMATCH"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-005 ---
        self.stdout.write("  SCN-KSA-005: Missing PO — cleaning chemicals...")
        inv = create_scn_ksa_005_missing_po_cleaning()
        results.append(("SCN-KSA-005", inv.invoice_number, "PO-KSA-9999 does not exist", "PO_NOT_FOUND"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-006 ---
        self.stdout.write("  SCN-KSA-006: Missing GRN — cooking oil...")
        inv = create_scn_ksa_006_missing_grn_syrup()
        results.append(("SCN-KSA-006", inv.invoice_number, "PO-KSA-1007 has no GRN", "GRN_NOT_FOUND"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-007 ---
        self.stdout.write("  SCN-KSA-007: Multi-GRN aggregation — frozen items...")
        inv = create_scn_ksa_007_multi_grn_nuggets()
        results.append(("SCN-KSA-007", inv.invoice_number, "3 GRNs cumulative match", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-008 ---
        self.stdout.write("  SCN-KSA-008: Duplicate invoice — French fries...")
        inv1, inv2 = create_scn_ksa_008_duplicate_invoice()
        results.append(("SCN-KSA-008a", inv1.invoice_number, "Original invoice", "MATCHED"))
        results.append(("SCN-KSA-008b", inv2.invoice_number, "Duplicate copy", "DUPLICATE_INVOICE"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv1.invoice_number} (original + duplicate)"))

        # --- SCN-KSA-009 ---
        self.stdout.write("  SCN-KSA-009: Arabic low-confidence invoice...")
        inv = create_scn_ksa_009_arabic_low_confidence()
        results.append(("SCN-KSA-009", inv.invoice_number, "Confidence 0.51 < threshold", "LOW_CONFIDENCE"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-010 ---
        self.stdout.write("  SCN-KSA-010: Location mismatch — syrup delivery...")
        inv = create_scn_ksa_010_location_mismatch()
        results.append(("SCN-KSA-010", inv.invoice_number, "BR-JED-220 vs GRN WH-RUH-01", "LOCATION_MISMATCH"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-011 ---
        self.stdout.write("  SCN-KSA-011: Qty exceeds GRN — cheese slices...")
        inv = create_scn_ksa_011_qty_exceeds_grn_cheese()
        results.append(("SCN-KSA-011", inv.invoice_number, "450 invoiced vs 400 received", "QTY_MISMATCH"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- SCN-KSA-012 ---
        self.stdout.write("  SCN-KSA-012: Review case — packaging supplies...")
        inv = create_scn_ksa_012_review_case_packaging()
        results.append(("SCN-KSA-012", inv.invoice_number, "Desc + price variance", "REQUIRES_REVIEW"))
        self.stdout.write(self.style.SUCCESS(f"    ✓ {inv.invoice_number}"))

        # --- Summary ---
        inv_count = Invoice.objects.filter(notes__contains="SCN-KSA-").count()
        line_count = InvoiceLineItem.objects.filter(
            invoice__notes__contains="SCN-KSA-"
        ).count()

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Seed Summary ==="))
        self.stdout.write(f"  Invoices created:     {inv_count}")
        self.stdout.write(f"  Line items created:   {line_count}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Scenario Map ==="))
        self.stdout.write(f"  {'Scenario':<14s} {'Invoice #':<24s} {'Description':<40s} {'Expected'}")
        self.stdout.write(f"  {'─'*14} {'─'*24} {'─'*40} {'─'*20}")
        for scn, inv_num, desc, expected in results:
            self.stdout.write(f"  {scn:<14s} {inv_num:<24s} {desc:<40s} {expected}")

        self.stdout.write(self.style.SUCCESS(
            "\n✓ Invoice test data seeding complete. "
            "Run reconciliation to generate results.\n"
        ))

    def _flush(self):
        """Remove previously seeded invoice test data."""
        self.stdout.write(self.style.WARNING("  Flushing SCN-KSA invoice test data..."))

        inv_qs = Invoice.objects.filter(notes__contains="SCN-KSA-")
        inv_ids = list(inv_qs.values_list("id", flat=True))

        line_count = InvoiceLineItem.objects.filter(invoice_id__in=inv_ids).delete()[0]
        inv_count = inv_qs.delete()[0]

        self.stdout.write(self.style.SUCCESS(
            f"    ✓ Flushed: {inv_count} invoices, {line_count} line items"
        ))
