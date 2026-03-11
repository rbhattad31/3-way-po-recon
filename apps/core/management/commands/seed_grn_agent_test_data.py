"""
Management command: seed_grn_agent_test_data

Seeds 12 invoice test scenarios (SCN-GRNAG-001 through SCN-GRNAG-012)
specifically designed to validate the GRN Specialist Agent.

This agent runs when the deterministic engine finds GRN-related issues:
  - missing GRN, partial receipt, over-delivery
  - invoice qty > received qty, delayed receipt
  - multiple GRNs for one PO, branch/warehouse mismatch

It investigates using grn_lookup, PO quantities, and invoice quantities.

Creates ONLY:
  - Invoice headers
  - InvoiceLineItem records
  - Minimal additional POs/PO lines   (only where no suitable PO exists)
  - Minimal additional GRNs/GRN lines (only where no suitable GRN exists)

Does NOT create:
  - ReconciliationRun, ReconciliationResult, ReconciliationResultLine
  - ReconciliationException
  - AgentRun, AgentStep, ToolCall, DecisionLog, AgentRecommendation
  - ReviewAssignment, ReviewComment, ManualReviewAction
  - AuditEvent

Assumes seed_saudi_mcd_data has already been run.

Usage:
    python manage.py seed_grn_agent_test_data
    python manage.py seed_grn_agent_test_data --flush
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
    GRNLineItem,
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
BASE_DATE = date(2026, 2, 15)  # Same as master seed
INVOICE_DATE = BASE_DATE + timedelta(days=8)  # GRN agent invoices arrive ~8 days later

SCENARIO_INVOICE_NUMBERS = [
    "INV-GRNAG-2026-001",
    "INV-GRNAG-2026-002",
    "INV-GRNAG-2026-003",
    "INV-GRNAG-2026-004",
    "INV-GRNAG-2026-005",
    "INV-GRNAG-2026-006",
    "INV-GRNAG-2026-007",
    "INV-GRNAG-2026-008",
    "INV-GRNAG-2026-009",
    "INV-GRNAG-2026-010",
    "INV-GRNAG-2026-011",
    "INV-GRNAG-2026-012",
]

# Additional POs created by this command (for scenario isolation)
ADDITIONAL_PO_NUMBERS = [
    "PO-KSA-3001",  # SCN-GRNAG-002 - missing GRN scenario
    "PO-KSA-3002",  # SCN-GRNAG-003 - partial receipt
    "PO-KSA-3003",  # SCN-GRNAG-004 - invoice exceeds received
    "PO-KSA-3004",  # SCN-GRNAG-005 - multiple GRNs full receipt
    "PO-KSA-3005",  # SCN-GRNAG-006 - multiple GRNs partial receipt
    "PO-KSA-3006",  # SCN-GRNAG-007 - over-delivery
    "PO-KSA-3007",  # SCN-GRNAG-008 - delayed receipt
    "PO-KSA-3008",  # SCN-GRNAG-009 - branch vs warehouse mismatch
    "PO-KSA-3009",  # SCN-GRNAG-010 - wrong item mix
    "PO-KSA-3010",  # SCN-GRNAG-011 - service / non-GRN invoice
    "PO-KSA-3011",  # SCN-GRNAG-012 - cold-chain shortage
]

# Additional GRNs created by this command
ADDITIONAL_GRN_NUMBERS = [
    "GRN-RUH-3001-A",   # SCN-GRNAG-001 reuses existing PO-KSA-1001 / GRN-RUH-1001-A
    "GRN-RUH-3002-A",   # SCN-GRNAG-003 partial receipt
    "GRN-RUH-3003-A",   # SCN-GRNAG-004 invoice > received
    "GRN-DMM-3004-A",   # SCN-GRNAG-005 multi-GRN 1/3
    "GRN-DMM-3004-B",   # SCN-GRNAG-005 multi-GRN 2/3
    "GRN-DMM-3004-C",   # SCN-GRNAG-005 multi-GRN 3/3
    "GRN-JED-3005-A",   # SCN-GRNAG-006 multi-GRN partial 1/2
    "GRN-JED-3005-B",   # SCN-GRNAG-006 multi-GRN partial 2/2
    "GRN-RUH-3006-A",   # SCN-GRNAG-007 over-delivery
    "GRN-RUH-3007-A",   # SCN-GRNAG-008 delayed receipt
    "GRN-RUH-3008-A",   # SCN-GRNAG-009 warehouse receipt (branch expected)
    "GRN-JED-3009-A",   # SCN-GRNAG-010 wrong item mix
    "GRN-DMM-3011-A",   # SCN-GRNAG-012 cold-chain shortage
]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def _d(val) -> Decimal:
    return Decimal(str(val))


def _line_amt(qty, price) -> Decimal:
    return (_d(qty) * _d(price)).quantize(Decimal("0.01"))


def _tax(amount) -> Decimal:
    return (amount * VAT_RATE).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Lookup helpers - reuse existing master data
# ---------------------------------------------------------------------------
def find_vendor(code: str) -> Vendor:
    return Vendor.objects.get(code=code)


def find_po(po_number: str) -> PurchaseOrder:
    return PurchaseOrder.objects.get(po_number=po_number)


def find_po_line(po: PurchaseOrder, line_number: int) -> PurchaseOrderLineItem:
    return po.line_items.get(line_number=line_number)


def get_ap_user() -> User:
    return User.objects.filter(role="AP_PROCESSOR").first() or User.objects.first()


# ---------------------------------------------------------------------------
# Creation helpers
# ---------------------------------------------------------------------------
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
    extraction_confidence: float = 0.90,
    status: str = InvoiceStatus.READY_FOR_RECON,
    notes: str = "",
    raw_currency: str = "SAR",
    extraction_remarks: str = "",
    delivery_note_ref: str = "",
    raw_po_number: str = "",
) -> Invoice:
    user = get_ap_user()
    return Invoice.objects.create(
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
        extraction_remarks=extraction_remarks or f"[{scenario_code}] GRN Agent test scenario",
        notes=f"[{scenario_code}] {notes}",
        created_by=user,
    )


def create_invoice_lines(invoice: Invoice, lines: list, confidence: float = 0.90) -> list:
    created = []
    for i, ld in enumerate(lines, 1):
        amt = ld.get("line_amount") or _line_amt(ld["qty"], ld["price"])
        tax = ld.get("tax_amount") or _tax(amt)
        item = InvoiceLineItem.objects.create(
            invoice=invoice,
            line_number=i,
            raw_description=ld["raw"],
            raw_quantity=str(ld["qty"]),
            raw_unit_price=str(ld["price"]),
            raw_tax_amount=str(tax),
            raw_line_amount=str(amt),
            description=ld["desc"],
            normalized_description=normalize_string(ld["desc"]),
            quantity=_d(ld["qty"]),
            unit_price=_d(ld["price"]),
            tax_amount=tax,
            line_amount=amt,
            extraction_confidence=ld.get("confidence", confidence),
        )
        created.append(item)
    return created


def ensure_po(
    po_number: str,
    vendor: Vendor,
    po_date: date,
    lines: list,
    status: str = "OPEN",
    notes: str = "",
    department: str = "Procurement",
) -> PurchaseOrder:
    admin = get_ap_user()
    subtotal = sum(_line_amt(ln["qty"], ln["price"]) for ln in lines)
    tax_total = sum(_tax(_line_amt(ln["qty"], ln["price"])) for ln in lines)
    po, created = PurchaseOrder.objects.get_or_create(
        po_number=po_number,
        defaults={
            "normalized_po_number": normalize_po_number(po_number),
            "vendor": vendor,
            "po_date": po_date,
            "currency": "SAR",
            "total_amount": subtotal + tax_total,
            "tax_amount": tax_total,
            "status": status,
            "buyer_name": "Fatima Al-Rashid",
            "department": department,
            "notes": notes,
            "created_by": admin,
        },
    )
    if created:
        for idx, ln in enumerate(lines, 1):
            amount = _line_amt(ln["qty"], ln["price"])
            PurchaseOrderLineItem.objects.create(
                purchase_order=po,
                line_number=idx,
                item_code=ln.get("item_code", ""),
                description=ln["description"],
                quantity=_d(ln["qty"]),
                unit_price=_d(ln["price"]),
                tax_amount=_tax(amount),
                line_amount=amount,
                unit_of_measure=ln.get("uom", "EA"),
            )
    return po


def ensure_grn(
    grn_number: str,
    po: PurchaseOrder,
    vendor: Vendor,
    receipt_date: date,
    warehouse: str,
    lines: list,
    status: str = "RECEIVED",
    notes: str = "",
) -> GoodsReceiptNote:
    admin = get_ap_user()
    grn, created = GoodsReceiptNote.objects.get_or_create(
        grn_number=grn_number,
        defaults={
            "purchase_order": po,
            "vendor": vendor,
            "receipt_date": receipt_date,
            "status": status,
            "warehouse": warehouse,
            "receiver_name": "Omar Al-Ghamdi",
            "notes": notes,
            "created_by": admin,
        },
    )
    if created:
        for idx, ln in enumerate(lines, 1):
            GRNLineItem.objects.create(
                grn=grn,
                line_number=idx,
                po_line=ln.get("po_line"),
                item_code=ln.get("item_code", ""),
                description=ln["description"],
                quantity_received=_d(ln["qty_received"]),
                quantity_accepted=_d(ln.get("qty_accepted", ln["qty_received"])),
                quantity_rejected=_d(ln.get("qty_rejected", 0)),
                unit_of_measure=ln.get("uom", "EA"),
            )
    return grn


def _inv_totals(lines: list) -> tuple:
    """Return (subtotal, tax, total) for a list of line dicts."""
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines)
    tax = _tax(subtotal)
    return subtotal, tax, subtotal + tax


# ===================================================================
#  12 SCENARIO FUNCTIONS
# ===================================================================


def create_scn_grnag_001_full_receipt_exact_match() -> Invoice:
    """
    SCN-GRNAG-001 - FULL RECEIPT EXACT MATCH
    -----------------------------------------
    Reuses existing PO-KSA-1001 (Arabian Food Supplies, buns/lettuce/pickles)
    and existing GRN-RUH-1001-A (full receipt 500/200/100).
    Invoice quantities exactly match GRN received quantities.

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: full
    - Expected recommendation_type: null
    - Expected confidence: high
    - Expected evidence keys: po_number, invoice_qty, grn_qty, grn_numbers, receipt_status
    """
    vendor = find_vendor("VND-AFS-001")
    lines = [
        {"raw": "Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش",
         "desc": "Sesame Burger Bun 4 inch", "qty": 500, "price": "45.00"},
        {"raw": "Shredded Lettuce FSP / خس مقطع",
         "desc": "Shredded Lettuce Food Service Pack", "qty": 200, "price": "28.00"},
        {"raw": "Pickle Slice Jar Bulk / مخلل شرائح",
         "desc": "Pickle Slice Jar Bulk", "qty": 100, "price": "35.00"},
    ]
    sub, tax, total = _inv_totals(lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-001",
        invoice_number="INV-GRNAG-2026-001",
        vendor=vendor,
        raw_vendor_name="Arabian Food Supplies Co.",
        po_number="PO-KSA-1001",
        invoice_date=INVOICE_DATE,
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.93,
        notes="Full receipt exact match - reuses PO-KSA-1001 & GRN-RUH-1001-A",
        delivery_note_ref="DN-AFS-RUH-0201",
    )
    create_invoice_lines(inv, lines, confidence=0.93)
    return inv


def create_scn_grnag_002_missing_grn() -> Invoice:
    """
    SCN-GRNAG-002 - MISSING GRN
    ----------------------------
    New PO-KSA-3001 exists (beverage syrups, Riyadh Beverage Concentrates)
    but NO GRN has been posted. Invoice arrives before goods receipt.

    Expected GRN specialist outcome:
    - Should find GRN? No
    - Should aggregate multiple GRNs? No
    - Receipt status: missing
    - Expected recommendation_type: SEND_TO_PROCUREMENT
    - Expected confidence: medium-high
    - Expected evidence keys: po_number, invoice_qty, grn_qty=0, receipt_status=missing
    """
    vendor = find_vendor("VND-RBC-005")
    po_lines = [
        {"item_code": "RBC-SYR-001", "description": "Soft Drink Syrup Cola Bag-in-Box",
         "qty": 120, "price": "220.00", "uom": "BAG"},
        {"item_code": "RBC-SYR-002", "description": "Soft Drink Syrup Fanta Bag-in-Box",
         "qty": 80, "price": "215.00", "uom": "BAG"},
    ]
    ensure_po("PO-KSA-3001", vendor, BASE_DATE - timedelta(days=10), po_lines,
              notes="SCN-GRNAG-002: PO for beverage syrups - no GRN posted")

    inv_lines = [
        {"raw": "Soft Drink Syrup Cola BiB / مركز مشروب غازي كولا",
         "desc": "Soft Drink Syrup Cola Bag-in-Box", "qty": 120, "price": "220.00"},
        {"raw": "Soft Drink Syrup Fanta BiB / مركز مشروب فانتا",
         "desc": "Soft Drink Syrup Fanta Bag-in-Box", "qty": 80, "price": "215.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-002",
        invoice_number="INV-GRNAG-2026-002",
        vendor=vendor,
        raw_vendor_name="Riyadh Beverage Concentrates Co.",
        po_number="PO-KSA-3001",
        invoice_date=INVOICE_DATE + timedelta(days=1),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.91,
        notes="Missing GRN - PO exists but no goods receipt posted",
        delivery_note_ref="DN-RBC-RUH-0089",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.91)
    return inv


def create_scn_grnag_003_partial_receipt() -> Invoice:
    """
    SCN-GRNAG-003 - PARTIAL RECEIPT
    --------------------------------
    PO-KSA-3002: French Fries 2.5kg qty=100, Chicken Nuggets qty=100
    GRN-RUH-3002-A: Fries received=60, Nuggets received=60
    Invoice: Fries qty=100, Nuggets qty=100

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: partial
    - Expected recommendation_type: SEND_TO_PROCUREMENT
    - Expected confidence: high
    - Expected evidence keys: po_number, invoice_qty=100, grn_qty=60, qty_gap=40, receipt_status=partial
    """
    vendor = find_vendor("VND-GFF-002")
    po_lines_data = [
        {"item_code": "GFF-FRY-001", "description": "French Fries 2.5kg Frozen",
         "qty": 100, "price": "78.00", "uom": "CTN"},
        {"item_code": "GFF-NUG-001", "description": "Nuggets Premium Frozen",
         "qty": 100, "price": "145.00", "uom": "CTN"},
    ]
    po = ensure_po("PO-KSA-3002", vendor, BASE_DATE - timedelta(days=14), po_lines_data,
                    notes="SCN-GRNAG-003: partial receipt scenario - ordered 100, received 60")

    po_line1 = find_po_line(po, 1)
    po_line2 = find_po_line(po, 2)
    ensure_grn(
        "GRN-RUH-3002-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=9),
        warehouse="WH-RUH-01",
        status="PARTIAL",
        lines=[
            {"po_line": po_line1, "item_code": "GFF-FRY-001",
             "description": "French Fries 2.5kg Frozen",
             "qty_received": 60, "qty_accepted": 60, "uom": "CTN"},
            {"po_line": po_line2, "item_code": "GFF-NUG-001",
             "description": "Nuggets Premium Frozen",
             "qty_received": 60, "qty_accepted": 60, "uom": "CTN"},
        ],
        notes="SCN-GRNAG-003: partial receipt - 60/100 each line",
    )

    inv_lines = [
        {"raw": "French Fries 2.5kg Frozen / بطاطس مقلية مجمدة ٢.٥ كجم",
         "desc": "French Fries 2.5kg Frozen", "qty": 100, "price": "78.00"},
        {"raw": "Nuggets Premium Frozen / ناجتس دجاج مجمد",
         "desc": "Nuggets Premium Frozen", "qty": 100, "price": "145.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-003",
        invoice_number="INV-GRNAG-2026-003",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="PO-KSA-3002",
        invoice_date=INVOICE_DATE + timedelta(days=2),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.92,
        notes="Partial receipt - invoice 100 vs GRN 60 per line, gap=40",
        delivery_note_ref="DN-GFF-RUH-0145",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.92)
    return inv


def create_scn_grnag_004_invoice_exceeds_received() -> Invoice:
    """
    SCN-GRNAG-004 - INVOICE EXCEEDS RECEIVED QUANTITY
    --------------------------------------------------
    PO-KSA-3003: Cheese Slice qty=100, Butter Portion qty=50
    GRN: Cheese received=80, Butter received=40
    Invoice: Cheese qty=90, Butter qty=50

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: partial (invoice > received)
    - Expected recommendation_type: SEND_TO_PROCUREMENT or SEND_TO_AP_REVIEW
    - Expected confidence: high
    - Expected evidence keys: po_number, invoice_qty, grn_qty, qty_gap (cheese=10, butter=10), receipt_status
    """
    vendor = find_vendor("VND-AKD-009")
    po_lines_data = [
        {"item_code": "AKD-CHS-001", "description": "Cheese Slice Processed",
         "qty": 100, "price": "62.00", "uom": "CTN"},
        {"item_code": "AKD-BTR-001", "description": "Butter Portion Pack",
         "qty": 50, "price": "18.50", "uom": "CTN"},
    ]
    po = ensure_po("PO-KSA-3003", vendor, BASE_DATE - timedelta(days=12), po_lines_data,
                    notes="SCN-GRNAG-004: invoice > received qty")

    po_l1 = find_po_line(po, 1)
    po_l2 = find_po_line(po, 2)
    ensure_grn(
        "GRN-RUH-3003-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=7),
        warehouse="WH-RUH-01",
        lines=[
            {"po_line": po_l1, "item_code": "AKD-CHS-001",
             "description": "Cheese Slice Processed",
             "qty_received": 80, "qty_accepted": 80, "uom": "CTN"},
            {"po_line": po_l2, "item_code": "AKD-BTR-001",
             "description": "Butter Portion Pack",
             "qty_received": 40, "qty_accepted": 40, "uom": "CTN"},
        ],
        notes="SCN-GRNAG-004: cheese 80/100, butter 40/50",
    )

    inv_lines = [
        {"raw": "Cheese Slice Processed / شرائح جبن",
         "desc": "Cheese Slice Processed", "qty": 90, "price": "62.00"},
        {"raw": "Butter Portion Pack / زبدة أجزاء",
         "desc": "Butter Portion Pack", "qty": 50, "price": "18.50"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-004",
        invoice_number="INV-GRNAG-2026-004",
        vendor=vendor,
        raw_vendor_name="Al Khobar Dairy Ingredients",
        po_number="PO-KSA-3003",
        invoice_date=INVOICE_DATE + timedelta(days=3),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.90,
        notes="Invoice qty > received - cheese 90 inv vs 80 rcvd, butter 50 inv vs 40 rcvd",
        delivery_note_ref="DN-AKD-RUH-0067",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.90)
    return inv


def create_scn_grnag_005_multiple_grns_full_receipt() -> Invoice:
    """
    SCN-GRNAG-005 - MULTIPLE GRNs CUMULATIVE FULL RECEIPT
    -----------------------------------------------------
    PO-KSA-3004: McD Beef Patty 4:1 qty=100
    GRN-A received=30, GRN-B received=40, GRN-C received=30  (total=100)
    Invoice qty=100

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? Yes (3 GRNs)
    - Receipt status: full
    - Expected recommendation_type: null
    - Expected confidence: high
    - Expected evidence keys: po_number, invoice_qty=100, cumulative_grn_qty=100,
                              grn_numbers=[GRN-DMM-3004-A/B/C], receipt_status=full
    """
    vendor = find_vendor("VND-GFF-002")
    po_lines_data = [
        {"item_code": "GFF-BPT-001", "description": "McD Beef Patty 4:1 Frozen",
         "qty": 100, "price": "185.00", "uom": "CTN"},
    ]
    po = ensure_po("PO-KSA-3004", vendor, BASE_DATE - timedelta(days=20), po_lines_data,
                    notes="SCN-GRNAG-005: multi-GRN cumulative full receipt")

    po_l = find_po_line(po, 1)
    for suffix, qty, days_ago in [("A", 30, 16), ("B", 40, 12), ("C", 30, 8)]:
        ensure_grn(
            f"GRN-DMM-3004-{suffix}", po, vendor,
            receipt_date=BASE_DATE - timedelta(days=days_ago),
            warehouse="WH-DMM-01",
            lines=[
                {"po_line": po_l, "item_code": "GFF-BPT-001",
                 "description": "McD Beef Patty 4:1 Frozen",
                 "qty_received": qty, "qty_accepted": qty, "uom": "CTN"},
            ],
            notes=f"SCN-GRNAG-005: staggered delivery {suffix} - {qty} CTN",
        )

    inv_lines = [
        {"raw": "McD Beef Patty 4:1 Frozen / لحم برجر مجمد ٤:١",
         "desc": "McD Beef Patty 4:1 Frozen", "qty": 100, "price": "185.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-005",
        invoice_number="INV-GRNAG-2026-005",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="PO-KSA-3004",
        invoice_date=INVOICE_DATE + timedelta(days=4),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.94,
        notes="Multi-GRN cumulative full receipt - 30+40+30=100",
        delivery_note_ref="DN-GFF-DMM-0098",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.94)
    return inv


def create_scn_grnag_006_multiple_grns_partial_receipt() -> Invoice:
    """
    SCN-GRNAG-006 - MULTIPLE GRNs CUMULATIVE PARTIAL RECEIPT
    --------------------------------------------------------
    PO-KSA-3005: Chicken Patty Breaded qty=200, Hash Brown qty=150
    GRN-A: Chicken=80, Hash=60
    GRN-B: Chicken=50, Hash=40
    Cumulative: Chicken=130/200, Hash=100/150
    Invoice: Chicken=200, Hash=150

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? Yes (2 GRNs)
    - Receipt status: partial
    - Expected recommendation_type: SEND_TO_PROCUREMENT
    - Expected confidence: high
    - Expected evidence keys: po_number, invoice_qty, cumulative_grn_qty,
                              qty_gap (chicken=70, hash=50), grn_numbers, receipt_status=partial
    """
    vendor = find_vendor("VND-GFF-002")
    po_lines_data = [
        {"item_code": "GFF-CPT-001", "description": "Chicken Patty Breaded Frozen",
         "qty": 200, "price": "158.00", "uom": "CTN"},
        {"item_code": "GFF-HSB-001", "description": "Hash Brown Triangle Frozen",
         "qty": 150, "price": "95.00", "uom": "CTN"},
    ]
    po = ensure_po("PO-KSA-3005", vendor, BASE_DATE - timedelta(days=18), po_lines_data,
                    notes="SCN-GRNAG-006: multi-GRN still partial")

    po_l1 = find_po_line(po, 1)
    po_l2 = find_po_line(po, 2)

    ensure_grn(
        "GRN-JED-3005-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=13),
        warehouse="WH-JED-01",
        status="PARTIAL",
        lines=[
            {"po_line": po_l1, "item_code": "GFF-CPT-001",
             "description": "Chicken Patty Breaded Frozen",
             "qty_received": 80, "qty_accepted": 80, "uom": "CTN"},
            {"po_line": po_l2, "item_code": "GFF-HSB-001",
             "description": "Hash Brown Triangle Frozen",
             "qty_received": 60, "qty_accepted": 60, "uom": "CTN"},
        ],
        notes="SCN-GRNAG-006: first delivery - chicken 80, hash 60",
    )
    ensure_grn(
        "GRN-JED-3005-B", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=8),
        warehouse="WH-JED-01",
        status="PARTIAL",
        lines=[
            {"po_line": po_l1, "item_code": "GFF-CPT-001",
             "description": "Chicken Patty Breaded Frozen",
             "qty_received": 50, "qty_accepted": 50, "uom": "CTN"},
            {"po_line": po_l2, "item_code": "GFF-HSB-001",
             "description": "Hash Brown Triangle Frozen",
             "qty_received": 40, "qty_accepted": 40, "uom": "CTN"},
        ],
        notes="SCN-GRNAG-006: second delivery - chicken 50, hash 40",
    )

    inv_lines = [
        {"raw": "Chicken Patty Breaded Frozen / فيليه دجاج مجمد",
         "desc": "Chicken Patty Breaded Frozen", "qty": 200, "price": "158.00"},
        {"raw": "Hash Brown Triangle Frozen / هاش براون مجمد",
         "desc": "Hash Brown Triangle Frozen", "qty": 150, "price": "95.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-006",
        invoice_number="INV-GRNAG-2026-006",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="PO-KSA-3005",
        invoice_date=INVOICE_DATE + timedelta(days=5),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.91,
        notes="Multi-GRN partial - chicken 130/200 (gap=70), hash 100/150 (gap=50)",
        delivery_note_ref="DN-GFF-JED-0202",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.91)
    return inv


def create_scn_grnag_007_over_delivery() -> Invoice:
    """
    SCN-GRNAG-007 - OVER-DELIVERY CASE
    ------------------------------------
    PO-KSA-3006: Al Watania Poultry - Chicken Patty qty=200
    GRN: received=230 (over by 30), accepted=200, rejected=30
    Invoice qty=230 (aligns with GRN received, not PO)

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: over-delivery
    - Expected recommendation_type: SEND_TO_PROCUREMENT or SEND_TO_AP_REVIEW
    - Expected confidence: medium-high
    - Expected evidence keys: po_number, po_qty=200, grn_qty=230, invoice_qty=230,
                              over_delivery_qty=30, receipt_status=over-delivery
    """
    vendor = find_vendor("VND-AWP-003")
    po_lines_data = [
        {"item_code": "AWP-CPT-001", "description": "Chicken Patty Breaded Frozen",
         "qty": 200, "price": "158.00", "uom": "CTN"},
    ]
    po = ensure_po("PO-KSA-3006", vendor, BASE_DATE - timedelta(days=15), po_lines_data,
                    notes="SCN-GRNAG-007: over-delivery - PO 200, GRN 230")

    po_l = find_po_line(po, 1)
    ensure_grn(
        "GRN-RUH-3006-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=10),
        warehouse="WH-RUH-01",
        lines=[
            {"po_line": po_l, "item_code": "AWP-CPT-001",
             "description": "Chicken Patty Breaded Frozen",
             "qty_received": 230, "qty_accepted": 200, "qty_rejected": 30, "uom": "CTN"},
        ],
        notes="SCN-GRNAG-007: over-delivery 230 received, 200 accepted, 30 rejected",
    )

    inv_lines = [
        {"raw": "Chicken Patty Breaded Frozen / فيليه دجاج مخبز مجمد",
         "desc": "Chicken Patty Breaded Frozen", "qty": 230, "price": "158.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-007",
        invoice_number="INV-GRNAG-2026-007",
        vendor=vendor,
        raw_vendor_name="Al Watania Poultry Supply",
        po_number="PO-KSA-3006",
        invoice_date=INVOICE_DATE + timedelta(days=6),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.93,
        notes="Over-delivery - invoice qty=230 matches GRN received, PO only 200",
        delivery_note_ref="DN-AWP-RUH-0312",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.93)
    return inv


def create_scn_grnag_008_delayed_receipt() -> Invoice:
    """
    SCN-GRNAG-008 - DELAYED RECEIPT AFTER INVOICE DATE
    ---------------------------------------------------
    PO-KSA-3007: Cooking Oil Fryer Grade 20L qty=200
    Invoice date: INVOICE_DATE + 7 (= BASE_DATE + 15)
    GRN receipt date: INVOICE_DATE + 12 (= BASE_DATE + 20, 5 days AFTER invoice)
    GRN received=200 (full receipt, but late)

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: delayed (GRN date > invoice date)
    - Expected recommendation_type: null or SEND_TO_AP_REVIEW (timing mismatch)
    - Expected confidence: medium-high
    - Expected evidence keys: po_number, invoice_date, grn_receipt_date,
                              timing_mismatch=True, invoice_qty, grn_qty, receipt_status=delayed
    """
    vendor = find_vendor("VND-NEO-008")
    po_lines_data = [
        {"item_code": "NEO-OIL-001", "description": "Cooking Oil Fryer Grade 20L",
         "qty": 200, "price": "32.00", "uom": "LTR"},
    ]
    po = ensure_po("PO-KSA-3007", vendor, BASE_DATE - timedelta(days=10), po_lines_data,
                    notes="SCN-GRNAG-008: delayed receipt - GRN posted after invoice date")

    inv_date = INVOICE_DATE + timedelta(days=7)  # Invoice arrives
    grn_date = INVOICE_DATE + timedelta(days=12)  # GRN posted 5 days later

    po_l = find_po_line(po, 1)
    ensure_grn(
        "GRN-RUH-3007-A", po, vendor,
        receipt_date=grn_date,
        warehouse="WH-RUH-01",
        lines=[
            {"po_line": po_l, "item_code": "NEO-OIL-001",
             "description": "Cooking Oil Fryer Grade 20L",
             "qty_received": 200, "qty_accepted": 200, "uom": "LTR"},
        ],
        notes="SCN-GRNAG-008: delayed receipt - posted 5 days after invoice",
    )

    inv_lines = [
        {"raw": "Cooking Oil Fryer Grade 20L / زيت طبخ ممتاز ٢٠ لتر",
         "desc": "Cooking Oil Fryer Grade 20L", "qty": 200, "price": "32.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-008",
        invoice_number="INV-GRNAG-2026-008",
        vendor=vendor,
        raw_vendor_name="Najd Edible Oils Trading",
        po_number="PO-KSA-3007",
        invoice_date=inv_date,
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.89,
        notes=f"Delayed receipt - invoice {inv_date}, GRN posted {grn_date}",
        delivery_note_ref="DN-NEO-RUH-0054",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.89)
    return inv


def create_scn_grnag_009_branch_vs_warehouse_mismatch() -> Invoice:
    """
    SCN-GRNAG-009 - BRANCH VS WAREHOUSE RECEIPT MISMATCH
    -----------------------------------------------------
    PO-KSA-3008: Sanitizer + Degreaser for BR-JED-220 (Jeddah Branch 220)
    GRN posted at WH-RUH-01 (Riyadh Central Warehouse) - wrong location
    Invoice references destination BR-JED-220

    Expected GRN specialist outcome:
    - Should find GRN? Yes (but wrong location)
    - Should aggregate multiple GRNs? No
    - Receipt status: full (quantities match, location mismatch)
    - Expected recommendation_type: SEND_TO_PROCUREMENT or SEND_TO_AP_REVIEW
    - Expected confidence: medium
    - Expected evidence keys: po_number, invoice_destination=BR-JED-220,
                              grn_warehouse=WH-RUH-01, location_mismatch=True,
                              invoice_qty, grn_qty, receipt_status
    """
    vendor = find_vendor("VND-RSRC-007")
    po_lines_data = [
        {"item_code": "RSRC-SAN-001", "description": "Sanitizer Surface Use",
         "qty": 150, "price": "28.00", "uom": "LTR"},
        {"item_code": "RSRC-DEG-001", "description": "Degreaser Kitchen Heavy Duty",
         "qty": 100, "price": "45.00", "uom": "LTR"},
    ]
    po = ensure_po("PO-KSA-3008", vendor, BASE_DATE - timedelta(days=11), po_lines_data,
                    notes="SCN-GRNAG-009: branch vs warehouse - PO for BR-JED-220",
                    department="Ops Branch Jeddah")

    po_l1 = find_po_line(po, 1)
    po_l2 = find_po_line(po, 2)
    # GRN received at WRONG location (Riyadh warehouse instead of Jeddah branch)
    ensure_grn(
        "GRN-RUH-3008-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=6),
        warehouse="WH-RUH-01",  # Wrong! Invoice expects BR-JED-220
        lines=[
            {"po_line": po_l1, "item_code": "RSRC-SAN-001",
             "description": "Sanitizer Surface Use",
             "qty_received": 150, "qty_accepted": 150, "uom": "LTR"},
            {"po_line": po_l2, "item_code": "RSRC-DEG-001",
             "description": "Degreaser Kitchen Heavy Duty",
             "qty_received": 100, "qty_accepted": 100, "uom": "LTR"},
        ],
        notes="SCN-GRNAG-009: received at WH-RUH-01 instead of BR-JED-220",
    )

    inv_lines = [
        {"raw": "معقم أسطح / Sanitizer Surface Use",
         "desc": "Sanitizer Surface Use", "qty": 150, "price": "28.00"},
        {"raw": "مزيل دهون صناعي / Degreaser Kitchen Heavy Duty",
         "desc": "Degreaser Kitchen Heavy Duty", "qty": 100, "price": "45.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-009",
        invoice_number="INV-GRNAG-2026-009",
        vendor=vendor,
        raw_vendor_name="Red Sea Restaurant Consumables",
        po_number="PO-KSA-3008",
        invoice_date=INVOICE_DATE + timedelta(days=8),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.88,
        notes="Location mismatch - invoice dest BR-JED-220, GRN at WH-RUH-01",
        extraction_remarks="Delivery dest: BR-JED-220 (Jeddah Branch 220)",
        delivery_note_ref="DN-RSRC-JED-0115",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.88)
    return inv


def create_scn_grnag_010_wrong_item_mix() -> Invoice:
    """
    SCN-GRNAG-010 - RECEIPT EXISTS FOR WRONG ITEM MIX
    --------------------------------------------------
    PO-KSA-3009: Paper Cup 16oz qty=5000, Big Mac Clamshell qty=3000
    GRN: Paper Cup received=5000 (correct), Big Mac Clamshell received=1000 (short),
         plus unexpected Fries Carton received=2000 (not in PO, substitution?)
    Invoice: Paper Cup qty=5000, Big Mac Clamshell qty=3000

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: partial / item mismatch
    - Expected recommendation_type: SEND_TO_PROCUREMENT or SEND_TO_VENDOR_CLARIFICATION
    - Expected confidence: medium
    - Expected evidence keys: po_number, item_level_mismatch=True,
                              cups_match=True, clamshell_gap=2000,
                              unexpected_item=Fries Carton, receipt_status
    """
    vendor = find_vendor("VND-SPS-004")
    po_lines_data = [
        {"item_code": "SPS-CUP-001", "description": "Paper Cup 16oz",
         "qty": 5000, "price": "0.85", "uom": "PCS"},
        {"item_code": "SPS-BMC-001", "description": "Big Mac Clamshell Box",
         "qty": 3000, "price": "1.20", "uom": "PCS"},
    ]
    po = ensure_po("PO-KSA-3009", vendor, BASE_DATE - timedelta(days=13), po_lines_data,
                    notes="SCN-GRNAG-010: wrong item mix - substitution in receipt")

    po_l1 = find_po_line(po, 1)
    po_l2 = find_po_line(po, 2)
    ensure_grn(
        "GRN-JED-3009-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=8),
        warehouse="WH-JED-01",
        lines=[
            {"po_line": po_l1, "item_code": "SPS-CUP-001",
             "description": "Paper Cup 16oz",
             "qty_received": 5000, "qty_accepted": 5000, "uom": "PCS"},
            {"po_line": po_l2, "item_code": "SPS-BMC-001",
             "description": "Big Mac Clamshell Box",
             "qty_received": 1000, "qty_accepted": 1000, "uom": "PCS"},
            {"po_line": None, "item_code": "SPS-FRC-001",
             "description": "Fries Carton Medium",
             "qty_received": 2000, "qty_accepted": 2000, "uom": "PCS"},
        ],
        notes="SCN-GRNAG-010: cups OK, clamshell short 2000, fries carton substituted",
    )

    inv_lines = [
        {"raw": "Paper Cup 16oz / كوب ورقي ١٦ أونصة",
         "desc": "Paper Cup 16oz", "qty": 5000, "price": "0.85"},
        {"raw": "Big Mac Clamshell Box / علبة بيج ماك",
         "desc": "Big Mac Clamshell Box", "qty": 3000, "price": "1.20"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-010",
        invoice_number="INV-GRNAG-2026-010",
        vendor=vendor,
        raw_vendor_name="Saudi Packaging Solutions",
        po_number="PO-KSA-3009",
        invoice_date=INVOICE_DATE + timedelta(days=9),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.90,
        notes="Item mix mismatch - clamshell short 2000, fries carton substituted in GRN",
        delivery_note_ref="DN-SPS-JED-0178",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.90)
    return inv


def create_scn_grnag_011_service_non_grn_invoice() -> Invoice:
    """
    SCN-GRNAG-011 - SERVICE / NON-GRN INVOICE
    ------------------------------------------
    PO-KSA-3010: Service PO - monthly kitchen cleaning, pest control
    No GRN expected (service items, not stock).
    Invoice is for a legitimate service with no goods receipt expected.

    Expected GRN specialist outcome:
    - Should find GRN? No (and should NOT aggressively flag missing GRN)
    - Should aggregate multiple GRNs? No
    - Receipt status: non-GRN-applicable
    - Expected recommendation_type: SEND_TO_AP_REVIEW
    - Expected confidence: medium
    - Expected evidence keys: po_number, service_po=True, receipt_status=non-GRN-applicable,
                              invoice_items_are_services=True
    """
    vendor = find_vendor("VND-RSRC-007")
    po_lines_data = [
        {"item_code": "RSRC-SVC-001", "description": "Monthly Kitchen Deep Cleaning Service",
         "qty": 1, "price": "4500.00", "uom": "SVC"},
        {"item_code": "RSRC-SVC-002", "description": "Pest Control Service - Quarterly",
         "qty": 1, "price": "2800.00", "uom": "SVC"},
        {"item_code": "RSRC-SVC-003", "description": "Grease Trap Maintenance Service",
         "qty": 1, "price": "1200.00", "uom": "SVC"},
    ]
    ensure_po("PO-KSA-3010", vendor, BASE_DATE - timedelta(days=5), po_lines_data,
              notes="SCN-GRNAG-011: service PO - no GRN expected",
              department="Facilities Maintenance")

    inv_lines = [
        {"raw": "خدمة تنظيف مطبخ شاملة شهرية / Monthly Kitchen Deep Cleaning",
         "desc": "Monthly Kitchen Deep Cleaning Service", "qty": 1, "price": "4500.00"},
        {"raw": "خدمة مكافحة حشرات ربع سنوية / Pest Control Service Quarterly",
         "desc": "Pest Control Service - Quarterly", "qty": 1, "price": "2800.00"},
        {"raw": "صيانة مصيدة الشحوم / Grease Trap Maintenance",
         "desc": "Grease Trap Maintenance Service", "qty": 1, "price": "1200.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-011",
        invoice_number="INV-GRNAG-2026-011",
        vendor=vendor,
        raw_vendor_name="Red Sea Restaurant Consumables",
        po_number="PO-KSA-3010",
        invoice_date=INVOICE_DATE + timedelta(days=10),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.87,
        notes="Service invoice - no GRN expected; cleaning + pest control + grease trap",
        extraction_remarks="Invoice for services, not physical goods delivery",
        delivery_note_ref="",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.87)
    return inv


def create_scn_grnag_012_cold_chain_shortage() -> Invoice:
    """
    SCN-GRNAG-012 - COLD-CHAIN SHORTAGE SCENARIO
    ----------------------------------------------
    PO-KSA-3011: French Fries 2.5kg qty=500, Chicken Nuggets qty=300
    GRN: Fries received=420 (80 short - cold-chain loss),
         Nuggets received=250 (50 short - partial unloading)
    Invoice: Fries qty=500, Nuggets qty=300

    Expected GRN specialist outcome:
    - Should find GRN? Yes
    - Should aggregate multiple GRNs? No
    - Receipt status: partial (cold-chain shortage)
    - Expected recommendation_type: SEND_TO_PROCUREMENT
    - Expected confidence: high
    - Expected evidence keys: po_number, invoice_qty, grn_qty,
                              fries_gap=80, nuggets_gap=50, receipt_status=partial,
                              cold_chain_related=True
    """
    vendor = find_vendor("VND-DCCL-006")
    po_lines_data = [
        {"item_code": "DCCL-FRY-001", "description": "French Fries 2.5kg Frozen",
         "qty": 500, "price": "78.00", "uom": "CTN"},
        {"item_code": "DCCL-NUG-001", "description": "Nuggets Premium Frozen",
         "qty": 300, "price": "145.00", "uom": "CTN"},
    ]
    po = ensure_po("PO-KSA-3011", vendor, BASE_DATE - timedelta(days=16), po_lines_data,
                    notes="SCN-GRNAG-012: cold-chain shortage - frozen goods transport loss")

    po_l1 = find_po_line(po, 1)
    po_l2 = find_po_line(po, 2)
    ensure_grn(
        "GRN-DMM-3011-A", po, vendor,
        receipt_date=BASE_DATE - timedelta(days=11),
        warehouse="WH-DMM-01",
        status="PARTIAL",
        lines=[
            {"po_line": po_l1, "item_code": "DCCL-FRY-001",
             "description": "French Fries 2.5kg Frozen",
             "qty_received": 420, "qty_accepted": 420, "qty_rejected": 0, "uom": "CTN"},
            {"po_line": po_l2, "item_code": "DCCL-NUG-001",
             "description": "Nuggets Premium Frozen",
             "qty_received": 250, "qty_accepted": 250, "qty_rejected": 0, "uom": "CTN"},
        ],
        notes="SCN-GRNAG-012: cold-chain shortage - fries 420/500, nuggets 250/300",
    )

    inv_lines = [
        {"raw": "French Fries 2.5kg Frozen / بطاطس مقلية مجمدة ٢.٥ كجم",
         "desc": "French Fries 2.5kg Frozen", "qty": 500, "price": "78.00"},
        {"raw": "Nuggets Premium Frozen / ناجتس بريميوم مجمد",
         "desc": "Nuggets Premium Frozen", "qty": 300, "price": "145.00"},
    ]
    sub, tax, total = _inv_totals(inv_lines)
    inv = create_invoice(
        scenario_code="SCN-GRNAG-012",
        invoice_number="INV-GRNAG-2026-012",
        vendor=vendor,
        raw_vendor_name="Desert Cold Chain Logistics",
        po_number="PO-KSA-3011",
        invoice_date=INVOICE_DATE + timedelta(days=11),
        subtotal=sub, tax_amount=tax, total_amount=total,
        extraction_confidence=0.91,
        notes="Cold-chain shortage - fries gap=80, nuggets gap=50",
        extraction_remarks="Frozen goods transport - Dammam Cold Store delivery note",
        delivery_note_ref="DN-DCCL-DMM-0303",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.91)
    return inv


# ===================================================================
#  COMMAND
# ===================================================================

ALL_SCENARIO_FNS = [
    ("SCN-GRNAG-001", "Full receipt exact match", create_scn_grnag_001_full_receipt_exact_match),
    ("SCN-GRNAG-002", "Missing GRN", create_scn_grnag_002_missing_grn),
    ("SCN-GRNAG-003", "Partial receipt", create_scn_grnag_003_partial_receipt),
    ("SCN-GRNAG-004", "Invoice exceeds received qty", create_scn_grnag_004_invoice_exceeds_received),
    ("SCN-GRNAG-005", "Multiple GRNs full receipt", create_scn_grnag_005_multiple_grns_full_receipt),
    ("SCN-GRNAG-006", "Multiple GRNs partial receipt", create_scn_grnag_006_multiple_grns_partial_receipt),
    ("SCN-GRNAG-007", "Over-delivery case", create_scn_grnag_007_over_delivery),
    ("SCN-GRNAG-008", "Delayed receipt after invoice", create_scn_grnag_008_delayed_receipt),
    ("SCN-GRNAG-009", "Branch vs warehouse mismatch", create_scn_grnag_009_branch_vs_warehouse_mismatch),
    ("SCN-GRNAG-010", "Wrong item mix receipt", create_scn_grnag_010_wrong_item_mix),
    ("SCN-GRNAG-011", "Service / non-GRN invoice", create_scn_grnag_011_service_non_grn_invoice),
    ("SCN-GRNAG-012", "Cold-chain shortage", create_scn_grnag_012_cold_chain_shortage),
]


class Command(BaseCommand):
    help = "Seed 12 GRN Specialist Agent test scenarios (SCN-GRNAG-001..012)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously seeded GRNAG invoices, POs and GRNs before re-seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== GRN Specialist Agent Test Data Seeder ==="
        ))

        if options["flush"]:
            self._flush()

        # Check prerequisite master data
        if not Vendor.objects.filter(code="VND-AFS-001").exists():
            self.stderr.write(self.style.ERROR(
                "Master data not found. Run 'python manage.py seed_saudi_mcd_data' first."
            ))
            return

        results = []
        for code, label, fn in ALL_SCENARIO_FNS:
            inv_number = f"INV-GRNAG-2026-{code[-3:]}"
            if Invoice.objects.filter(invoice_number=inv_number).exists():
                self.stdout.write(f"  [{code}] {label} - already exists, skipping")
                results.append((code, label, "SKIPPED"))
                continue
            inv = fn()
            line_count = inv.line_items.count()
            self.stdout.write(self.style.SUCCESS(
                f"  [{code}] {label} - Invoice {inv.invoice_number} ({line_count} lines)"
            ))
            results.append((code, label, "CREATED"))

        self._print_summary(results)

    def _flush(self):
        inv_del, _ = Invoice.objects.filter(
            invoice_number__in=SCENARIO_INVOICE_NUMBERS
        ).delete()
        grn_del, _ = GoodsReceiptNote.objects.filter(
            grn_number__in=ADDITIONAL_GRN_NUMBERS
        ).delete()
        po_del, _ = PurchaseOrder.objects.filter(
            po_number__in=ADDITIONAL_PO_NUMBERS
        ).delete()
        self.stdout.write(self.style.WARNING(
            f"  Flushed {inv_del} invoice record(s), "
            f"{grn_del} GRN record(s), {po_del} PO record(s)"
        ))

    def _print_summary(self, results):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n--- Summary --------------------------------------------------------"
        ))
        created = sum(1 for _, _, s in results if s == "CREATED")
        skipped = sum(1 for _, _, s in results if s == "SKIPPED")

        self.stdout.write(f"  Scenarios created : {created}")
        self.stdout.write(f"  Scenarios skipped : {skipped}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("  Scenario Map:"))
        for code, label, status in results:
            icon = "+" if status == "CREATED" else "-"
            self.stdout.write(f"    {icon} {code}  {label}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n  Expected GRN Specialist Outcomes:"
        ))
        outcome_map = [
            ("SCN-GRNAG-001", "full",     "GRN found",               "null",                        "high"),
            ("SCN-GRNAG-002", "missing",  "no GRN found",            "SEND_TO_PROCUREMENT",         "medium-high"),
            ("SCN-GRNAG-003", "partial",  "qty gap=40 per line",     "SEND_TO_PROCUREMENT",         "high"),
            ("SCN-GRNAG-004", "partial",  "inv>rcvd (cheese+10)",    "SEND_TO_PROCUREMENT",         "high"),
            ("SCN-GRNAG-005", "full",     "3 GRNs aggregated",       "null",                        "high"),
            ("SCN-GRNAG-006", "partial",  "2 GRNs, gap remaining",  "SEND_TO_PROCUREMENT",         "high"),
            ("SCN-GRNAG-007", "over",     "GRN 230 > PO 200",       "SEND_TO_PROCUREMENT",         "medium-high"),
            ("SCN-GRNAG-008", "delayed",  "GRN date > invoice date", "null / SEND_TO_AP_REVIEW",    "medium-high"),
            ("SCN-GRNAG-009", "full*",    "location mismatch",       "SEND_TO_PROCUREMENT",         "medium"),
            ("SCN-GRNAG-010", "partial",  "item mix wrong",          "SEND_TO_VENDOR_CLARIFICATION","medium"),
            ("SCN-GRNAG-011", "n/a",      "service - no GRN needed", "SEND_TO_AP_REVIEW",           "medium"),
            ("SCN-GRNAG-012", "partial",  "cold-chain shortage",     "SEND_TO_PROCUREMENT",         "high"),
        ]
        self.stdout.write(
            f"    {'Code':<16} {'Receipt':<10} {'Detail':<26} {'Recommendation':<32} {'Conf'}"
        )
        self.stdout.write(f"    {'----':<16} {'-------':<10} {'------':<26} {'--------------':<32} {'----'}")
        for code, receipt, detail, rec, conf in outcome_map:
            self.stdout.write(
                f"    {code:<16} {receipt:<10} {detail:<26} {rec:<32} {conf}"
            )

        self.stdout.write(self.style.SUCCESS(
            "\n=== GRN Specialist Agent test data seeding complete ===\n"
        ))
