"""
Management command: seed_po_agent_test_data

Seeds 10 invoice test scenarios (SCN-POAG-001 through SCN-POAG-010)
specifically designed to validate the PO Retrieval Agent.

This agent runs only when deterministic PO lookup has failed.
It tries: (1) normalized PO lookup, (2) vendor-based search,
(3) amount-based matching, then returns a structured recommendation.

Creates ONLY:
  - Invoice headers
  - InvoiceLineItem records
  - Minimal additional POs/PO lines (only where required for scenario isolation)
  - Minimal additional VendorAlias records (only where required)

Does NOT create:
  - ReconciliationRun, ReconciliationResult, ReconciliationException
  - AgentRun, AgentStep, ToolCall, DecisionLog, AgentRecommendation
  - ReviewAssignment, ReviewComment, ManualReviewAction
  - AuditEvent

Assumes seed_saudi_mcd_data has already been run.

Usage:
    python manage.py seed_po_agent_test_data
    python manage.py seed_po_agent_test_data --flush
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
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.vendors.models import Vendor, VendorAlias

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VAT_RATE = Decimal("0.15")
BASE_DATE = date(2026, 2, 15)
INVOICE_DATE = BASE_DATE + timedelta(days=7)

SCENARIO_INVOICE_NUMBERS = [
    "INV-POAG-2026-001",  # SCN-POAG-001
    "INV-POAG-2026-002",  # SCN-POAG-002
    "INV-POAG-2026-003",  # SCN-POAG-003
    "INV-POAG-2026-004",  # SCN-POAG-004
    "INV-POAG-2026-005",  # SCN-POAG-005
    "INV-POAG-2026-006",  # SCN-POAG-006
    "INV-POAG-2026-007",  # SCN-POAG-007
    "INV-POAG-2026-008",  # SCN-POAG-008
    "INV-POAG-2026-009",  # SCN-POAG-009
    "INV-POAG-2026-010",  # SCN-POAG-010
]

# Additional PO numbers created by this command (for scenario isolation)
ADDITIONAL_PO_NUMBERS = [
    "PO-KSA-2001",  # SCN-POAG-002 — single open PO for vendor-based discovery
    "PO-KSA-2002",  # SCN-POAG-003 — open PO candidate A (multiple POs scenario)
    "PO-KSA-2003",  # SCN-POAG-003 — open PO candidate B
    "PO-KSA-2004",  # SCN-POAG-003 — open PO candidate C
    "PO-KSA-2005",  # SCN-POAG-004 — amount-based fallback PO
    "PO-KSA-2006",  # SCN-POAG-009 — warehouse PO
    "PO-KSA-2007",  # SCN-POAG-009 — branch PO
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
# Lookup helpers — reuse existing master data
# ---------------------------------------------------------------------------
def find_existing_vendor(code: str) -> Vendor:
    """Retrieve a vendor by code from master data."""
    return Vendor.objects.get(code=code)


def find_po(po_number: str) -> PurchaseOrder:
    """Retrieve a PO by number."""
    return PurchaseOrder.objects.get(po_number=po_number)


def find_po_candidates_for_vendor(vendor: Vendor, status: str = "OPEN") -> list:
    """Return open POs for a given vendor."""
    return list(
        PurchaseOrder.objects.filter(vendor=vendor, status=status)
        .order_by("-po_date")
    )


def get_ap_user() -> User:
    """Return the AP processor user for created_by."""
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
    raw_po_number: str = "",
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
        extraction_remarks=extraction_remarks or f"[{scenario_code}] PO Agent test scenario",
        notes=f"[{scenario_code}] {notes}",
        created_by=user,
    )
    return inv


def create_invoice_lines(invoice: Invoice, lines_data: list, confidence: float = 0.90) -> list:
    """Create InvoiceLineItems from a list of dicts."""
    created = []
    for i, ld in enumerate(lines_data, 1):
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
    """Create a PO + line items if they don't exist. Returns the PO."""
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


def ensure_vendor_alias(vendor: Vendor, alias_name: str, source: str = "seed") -> VendorAlias:
    """Create a vendor alias if it doesn't exist."""
    admin = get_ap_user()
    alias, _ = VendorAlias.objects.get_or_create(
        vendor=vendor,
        normalized_alias=normalize_string(alias_name),
        defaults={
            "alias_name": alias_name,
            "source": source,
            "created_by": admin,
        },
    )
    return alias


# ===================================================================
#  10 SCENARIO FUNCTIONS
# ===================================================================


def create_scn_poag_001_reordered_po_recovery() -> Invoice:
    """
    SCN-POAG-001 — REORDERED PO SEGMENT RECOVERY
    ──────────────────────────────────────────────
    Invoice contains PO number with reordered segments:
      raw_po_number = "PO-1001-KSA" (segments reversed vs canonical "PO-KSA-1001")
    The valid PO "PO-KSA-1001" exists (from master seed, Arabian Food Supplies).

    Deterministic lookup fails:
    - Exact match: "PO-1001-KSA" ≠ "PO-KSA-1001"
    - Normalized match: "1001KSA" ≠ "KSA1001" (segment reorder survives simple normalization)

    Agent recovery via intelligent normalization:
    The LLM recognises "PO-1001-KSA" contains the same segments as "PO-KSA-1001"
    (prefix PO, region KSA, sequence 1001) and tries reordered variants until a
    match is found.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes
    - Primary strategy: intelligent PO normalization (segment reordering)
    - Expected recommendation_type: null (PO found successfully)
    - Expected confidence: high (0.90+)
    - Expected evidence keys: po_number, normalized_match, vendor_confirmed
    """
    vendor = find_existing_vendor("VND-AFS-001")

    lines_data = [
        {
            "raw": "Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش",
            "desc": "Sesame Burger Bun 4 inch",
            "qty": 500, "price": "45.00",
        },
        {
            "raw": "Shredded Lettuce FSP / خس مقطع",
            "desc": "Shredded Lettuce Food Service Pack",
            "qty": 200, "price": "28.00",
        },
        {
            "raw": "Pickle Slice Jar Bulk / مخلل شرائح",
            "desc": "Pickle Slice Jar Bulk",
            "qty": 100, "price": "35.00",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in lines_data)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-001",
        invoice_number="INV-POAG-2026-001",
        vendor=vendor,
        raw_vendor_name="Arabian Food Supplies Co.",
        po_number="PO-1001-KSA",           # segments reordered — malformed
        raw_po_number="PO-1001-KSA",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.91,
        notes="Reordered PO ref 'PO-1001-KSA'; valid PO is PO-KSA-1001",
    )
    create_invoice_lines(inv, lines_data, confidence=0.91)
    return inv


def create_scn_poag_002_vendor_based_discovery() -> Invoice:
    """
    SCN-POAG-002 — VENDOR-BASED PO DISCOVERY
    ──────────────────────────────────────────
    Invoice PO number is blank / unreadable.
    Vendor is Gulf Frozen Foods Trading (VND-GFF-002).
    Invoice amount (SAR 88,550 excl. VAT) matches a dedicated open PO.

    We create a dedicated PO (PO-KSA-2001) for this scenario so the agent
    has exactly one open PO matching the total.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes
    - Primary strategy: vendor search → amount match
    - Expected recommendation_type: null (PO found)
    - Expected confidence: medium-high (0.75–0.90)
    - Expected evidence keys: po_number, matched_vendor, amount_match
    """
    vendor = find_existing_vendor("VND-GFF-002")

    # Create dedicated PO for this scenario
    po_lines = [
        {
            "item_code": "GFF-BPT-001",
            "description": "McD Beef Patty 4:1 Frozen",
            "qty": 350, "price": "185.00", "uom": "CTN",
        },
        {
            "item_code": "GFF-CPT-001",
            "description": "Chicken Patty Breaded Frozen",
            "qty": 150, "price": "158.00", "uom": "CTN",
        },
    ]
    ensure_po(
        po_number="PO-KSA-2001",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=14),
        lines=po_lines,
        notes="SCN-POAG-002: single open PO for vendor-based discovery test",
    )

    # Invoice lines match the PO amounts exactly
    inv_lines = [
        {
            "raw": "McD Beef Patty 4:1 Frozen / لحم برجر مجمد ٤:١",
            "desc": "McD Beef Patty 4:1 Frozen",
            "qty": 350, "price": "185.00",
        },
        {
            "raw": "Chicken Patty Breaded Frozen / فيليه دجاج مجمد",
            "desc": "Chicken Patty Breaded Frozen",
            "qty": 150, "price": "158.00",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-002",
        invoice_number="INV-POAG-2026-002",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="",                      # blank — unreadable
        raw_po_number="[unreadable]",
        invoice_date=INVOICE_DATE + timedelta(days=1),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.72,
        notes="PO number blank; vendor clear; amount matches PO-KSA-2001",
        extraction_remarks="PO field could not be extracted — smudged scan area",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.85)
    return inv


def create_scn_poag_003_multiple_open_pos() -> Invoice:
    """
    SCN-POAG-003 — VENDOR HAS MULTIPLE OPEN POs
    ─────────────────────────────────────────────
    Invoice PO number is missing.
    Vendor is Saudi Packaging Solutions (VND-SPS-004).
    Three open POs exist with similar values/items.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Ambiguous — multiple candidates
    - Primary strategy: vendor search → finds 3 candidate POs
    - Expected recommendation_type: SEND_TO_AP_REVIEW (ambiguity)
    - Expected confidence: low-medium (0.30–0.55)
    - Expected evidence keys: candidate_pos, matched_vendor, search_attempts
    """
    vendor = find_existing_vendor("VND-SPS-004")

    # Create 3 similar open POs for this vendor
    ensure_po(
        po_number="PO-KSA-2002",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=12),
        lines=[
            {"item_code": "SPS-CUP-001", "description": "Paper Cup 16oz",
             "qty": 6000, "price": "0.85", "uom": "PCS"},
            {"item_code": "SPS-LID-001", "description": "Plastic Lid 16oz",
             "qty": 6000, "price": "0.45", "uom": "PCS"},
        ],
        notes="SCN-POAG-003: candidate A — cups + lids order",
    )
    ensure_po(
        po_number="PO-KSA-2003",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=9),
        lines=[
            {"item_code": "SPS-CUP-001", "description": "Paper Cup 16oz",
             "qty": 5500, "price": "0.85", "uom": "PCS"},
            {"item_code": "SPS-LID-001", "description": "Plastic Lid 16oz",
             "qty": 5500, "price": "0.45", "uom": "PCS"},
            {"item_code": "SPS-NAP-001", "description": "Napkin Dispenser Pack",
             "qty": 1000, "price": "0.30", "uom": "PKT"},
        ],
        notes="SCN-POAG-003: candidate B — cups + lids + napkins order",
    )
    ensure_po(
        po_number="PO-KSA-2004",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=6),
        lines=[
            {"item_code": "SPS-CUP-001", "description": "Paper Cup 16oz",
             "qty": 5800, "price": "0.85", "uom": "PCS"},
            {"item_code": "SPS-LID-001", "description": "Plastic Lid 16oz",
             "qty": 5800, "price": "0.45", "uom": "PCS"},
        ],
        notes="SCN-POAG-003: candidate C — cups + lids replenishment",
    )

    # Invoice is for cups + lids, qty close to all three POs
    inv_lines = [
        {
            "raw": "Paper Cup 16oz / كوب ورقي ١٦ أونصة",
            "desc": "Paper Cup 16oz",
            "qty": 5700, "price": "0.85",
        },
        {
            "raw": "Plastic Lid 16oz / غطاء بلاستيك ١٦ أونصة",
            "desc": "Plastic Lid 16oz",
            "qty": 5700, "price": "0.45",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-003",
        invoice_number="INV-POAG-2026-003",
        vendor=vendor,
        raw_vendor_name="Saudi Packaging Solutions",
        po_number="",                      # missing
        raw_po_number="",
        invoice_date=INVOICE_DATE + timedelta(days=2),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.88,
        notes="PO missing; vendor has 3 similar open POs; ambiguous match",
        extraction_remarks="PO number field empty on scanned invoice",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.88)
    return inv


def create_scn_poag_004_amount_based_fallback() -> Invoice:
    """
    SCN-POAG-004 — AMOUNT-BASED PO FALLBACK
    ────────────────────────────────────────
    Invoice has a bad PO number ("PO/KSA/XXXX") and the vendor name
    is written as an alias variation ("الشركة السعودية لحلول التغليف")
    which can still be resolved via VendorAlias.
    Invoice total closely matches one specific PO.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes
    - Primary strategy: normalized PO fails → vendor alias resolves → amount match
    - Expected recommendation_type: null (PO found via amount)
    - Expected confidence: medium (0.60–0.80)
    - Expected evidence keys: po_number, matched_vendor, amount_match, alias_resolved
    """
    vendor = find_existing_vendor("VND-SPS-004")

    # Ensure Arabic alias exists for vendor
    ensure_vendor_alias(
        vendor,
        "الشركة السعودية لحلول التغليف",
        source="seed",
    )

    # Create a dedicated PO with a distinctive total
    po_lines = [
        {"item_code": "SPS-BMC-001", "description": "Big Mac Clamshell Box",
         "qty": 4000, "price": "1.20", "uom": "PCS"},
        {"item_code": "SPS-FRC-001", "description": "Fries Carton Medium",
         "qty": 7000, "price": "0.65", "uom": "PCS"},
    ]
    ensure_po(
        po_number="PO-KSA-2005",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=11),
        lines=po_lines,
        notes="SCN-POAG-004: distinctive total for amount-based fallback",
    )

    inv_lines = [
        {
            "raw": "علب بيج ماك / Big Mac Clamshell Box",
            "desc": "Big Mac Clamshell Box",
            "qty": 4000, "price": "1.20",
        },
        {
            "raw": "كرتون بطاطس وسط / Fries Carton Medium",
            "desc": "Fries Carton Medium",
            "qty": 7000, "price": "0.65",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-004",
        invoice_number="INV-POAG-2026-004",
        vendor=None,                       # vendor not linked — alias variation
        raw_vendor_name="الشركة السعودية لحلول التغليف",
        po_number="PO/KSA/XXXX",          # garbled PO
        raw_po_number="PO/KSA/XXXX",
        invoice_date=INVOICE_DATE + timedelta(days=3),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.68,
        notes="Bad PO ref; Arabic vendor alias; total matches PO-KSA-2005",
        extraction_remarks="PO number partially illegible; vendor name in Arabic only",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.75)
    return inv


def create_scn_poag_005_no_po_found() -> Invoice:
    """
    SCN-POAG-005 — NO PO FOUND
    ───────────────────────────
    Invoice has invalid PO number "PO-KSA-9999".
    Vendor exists (Najd Edible Oils, VND-NEO-008) but has no open POs
    matching this invoice amount (invoice total is deliberately unique).

    Expected PO Retrieval Agent outcome:
    - Should find PO? No
    - Primary strategy: all strategies fail (normalized, vendor, amount)
    - Expected recommendation_type: SEND_TO_AP_REVIEW
    - Expected confidence: low-medium (0.40–0.60)
    - Expected evidence keys: search_attempts, no_match_reason
    """
    vendor = find_existing_vendor("VND-NEO-008")

    inv_lines = [
        {
            "raw": "زيت طبخ ممتاز ٢٠ لتر / Premium Cooking Oil 20L",
            "desc": "Premium Cooking Oil 20L",
            "qty": 250, "price": "38.00",
        },
        {
            "raw": "زيت قلي خاص ١٠ لتر / Special Frying Oil 10L",
            "desc": "Special Frying Oil 10L",
            "qty": 180, "price": "22.50",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-005",
        invoice_number="INV-POAG-2026-005",
        vendor=vendor,
        raw_vendor_name="Najd Edible Oils Trading",
        po_number="PO-KSA-9999",          # nonexistent PO
        raw_po_number="PO-KSA-9999",
        invoice_date=INVOICE_DATE + timedelta(days=4),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.89,
        notes="Invalid PO; no matching open PO for this vendor/amount",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.89)
    return inv


def create_scn_poag_006_wrong_vendor_valid_like_po() -> Invoice:
    """
    SCN-POAG-006 — WRONG VENDOR WITH VALID-LIKE PO
    ────────────────────────────────────────────────
    Invoice comes from Riyadh Beverage Concentrates (VND-RBC-005)
    but references PO-KSA-1001 which belongs to Arabian Food Supplies
    (VND-AFS-001). The PO exists — but the vendor doesn't match.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes, but vendor mismatch prevents acceptance
    - Primary strategy: normalized PO lookup finds candidate, vendor check fails
    - Expected recommendation_type: SEND_TO_AP_REVIEW or SEND_TO_PROCUREMENT
    - Expected confidence: medium (0.50–0.70)
    - Expected evidence keys: candidate_po, vendor_mismatch, invoice_vendor, po_vendor
    """
    vendor = find_existing_vendor("VND-RBC-005")

    inv_lines = [
        {
            "raw": "Soft Drink Syrup Cola BiB / مركز مشروب غازي كولا",
            "desc": "Soft Drink Syrup Cola Bag-in-Box",
            "qty": 120, "price": "220.00",
        },
        {
            "raw": "Soft Drink Syrup Fanta BiB / مركز مشروب فانتا",
            "desc": "Soft Drink Syrup Fanta Bag-in-Box",
            "qty": 90, "price": "215.00",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-006",
        invoice_number="INV-POAG-2026-006",
        vendor=vendor,
        raw_vendor_name="Riyadh Beverage Concentrates Co.",
        po_number="PO-KSA-1001",          # belongs to VND-AFS-001, not RBC
        raw_po_number="PO-KSA-1001",
        invoice_date=INVOICE_DATE + timedelta(days=5),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.93,
        notes="PO-KSA-1001 belongs to Arabian Food Supplies, not Riyadh Bev",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.93)
    return inv


def create_scn_poag_007_arabic_english_vendor_alias() -> Invoice:
    """
    SCN-POAG-007 — ARABIC-ENGLISH VENDOR ALIAS CASE
    ─────────────────────────────────────────────────
    Invoice vendor name is "شركة الأغذية العربية" (Arabic for
    "Arabian Food Company") — an abbreviated Arabic alias.
    PO exists under the standard English name "Arabian Food Supplies Co."
    Alias should resolve through VendorAlias.

    This scenario reuses existing PO-KSA-1002 (buns order, VND-AFS-001).

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes
    - Primary strategy: vendor alias → vendor search → PO found
    - Expected recommendation_type: null (PO found)
    - Expected confidence: medium-high (0.70–0.85)
    - Expected evidence keys: resolved_vendor, alias_used, po_number
    """
    vendor = find_existing_vendor("VND-AFS-001")

    # Ensure Arabic alias exists
    ensure_vendor_alias(
        vendor,
        "شركة الأغذية العربية",
        source="seed",
    )

    inv_lines = [
        {
            "raw": "خبز برجر بالسمسم ٤ انش / Sesame Burger Bun 4in",
            "desc": "Sesame Burger Bun 4 inch",
            "qty": 600, "price": "45.00",
        },
        {
            "raw": "خبز برجر عادي ٤ انش / Regular Burger Bun 4in",
            "desc": "Regular Burger Bun 4 inch",
            "qty": 300, "price": "40.00",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-007",
        invoice_number="INV-POAG-2026-007",
        vendor=None,                       # vendor not linked — Arabic alias only
        raw_vendor_name="شركة الأغذية العربية",
        po_number="po_ksa_1002",           # underscore format — malformed
        raw_po_number="po_ksa_1002",
        invoice_date=INVOICE_DATE + timedelta(days=6),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.74,
        notes="Arabic vendor alias; malformed PO 'po_ksa_1002'; target PO-KSA-1002",
        extraction_remarks="Vendor name in Arabic only; PO field lowercase+underscores",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.78)
    return inv


def create_scn_poag_008_closed_po_referenced() -> Invoice:
    """
    SCN-POAG-008 — CLOSED PO REFERENCED
    ─────────────────────────────────────
    Invoice references PO-KSA-1017 which exists but has status "CLOSED"
    (fully delivered old buns order from master seed).

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes, but PO is CLOSED (not usable)
    - Primary strategy: normalized PO lookup finds PO, status check fails
    - Expected recommendation_type: SEND_TO_AP_REVIEW or SEND_TO_PROCUREMENT
    - Expected confidence: medium (0.55–0.70)
    - Expected evidence keys: po_number, po_status, po_closed_reason
    """
    vendor = find_existing_vendor("VND-AFS-001")

    inv_lines = [
        {
            "raw": "Sesame Burger Bun 4 inch / خبز برجر بالسمسم ٤ انش",
            "desc": "Sesame Burger Bun 4 inch",
            "qty": 200, "price": "44.00",
        },
        {
            "raw": "Pickle Slice Jar Bulk / مخلل شرائح",
            "desc": "Pickle Slice Jar Bulk",
            "qty": 80, "price": "35.00",
        },
        {
            "raw": "Shredded Lettuce FSP / خس مقطع",
            "desc": "Shredded Lettuce Food Service Pack",
            "qty": 100, "price": "28.00",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-008",
        invoice_number="INV-POAG-2026-008",
        vendor=vendor,
        raw_vendor_name="Arabian Food Supplies Co.",
        po_number="PO-KSA-1017",          # CLOSED PO
        raw_po_number="PO-KSA-1017",
        invoice_date=INVOICE_DATE + timedelta(days=7),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.92,
        notes="References CLOSED PO-KSA-1017; fully consumed old order",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.92)
    return inv


def create_scn_poag_009_branch_vs_warehouse_ambiguity() -> Invoice:
    """
    SCN-POAG-009 — BRANCH VS WAREHOUSE PO AMBIGUITY
    ─────────────────────────────────────────────────
    Invoice references destination branch "BR-JED-220" but two POs
    exist for the same vendor (Red Sea Restaurant Consumables):
      - PO-KSA-2006: warehouse WH-JED-01 order
      - PO-KSA-2007: branch BR-JED-220 order
    Both have similar cleaning items and comparable totals.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Possibly — destination context should help
    - Primary strategy: vendor search → multiple candidates → location filter
    - Expected recommendation_type: null if location narrows to 1, else SEND_TO_AP_REVIEW
    - Expected confidence: medium (0.50–0.75)
    - Expected evidence keys: candidate_pos, destination_code, location_match
    """
    vendor = find_existing_vendor("VND-RSRC-007")

    # Warehouse PO
    ensure_po(
        po_number="PO-KSA-2006",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=10),
        lines=[
            {"item_code": "RSRC-SAN-001", "description": "Sanitizer Surface Use",
             "qty": 400, "price": "28.00", "uom": "LTR"},
            {"item_code": "RSRC-DEG-001", "description": "Degreaser Kitchen Heavy Duty",
             "qty": 250, "price": "45.00", "uom": "LTR"},
        ],
        notes="SCN-POAG-009: warehouse order WH-JED-01",
        department="Warehouse Ops Jeddah",
    )

    # Branch PO
    ensure_po(
        po_number="PO-KSA-2007",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=8),
        lines=[
            {"item_code": "RSRC-SAN-001", "description": "Sanitizer Surface Use",
             "qty": 350, "price": "28.00", "uom": "LTR"},
            {"item_code": "RSRC-DEG-001", "description": "Degreaser Kitchen Heavy Duty",
             "qty": 200, "price": "45.00", "uom": "LTR"},
            {"item_code": "RSRC-GLV-001", "description": "Food Safe Gloves Medium",
             "qty": 500, "price": "12.50", "uom": "BOX"},
        ],
        notes="SCN-POAG-009: branch order BR-JED-220",
        department="Ops Branch Jeddah",
    )

    inv_lines = [
        {
            "raw": "معقم أسطح / Sanitizer Surface Use",
            "desc": "Sanitizer Surface Use",
            "qty": 350, "price": "28.00",
        },
        {
            "raw": "مزيل دهون صناعي / Degreaser Kitchen Heavy Duty",
            "desc": "Degreaser Kitchen Heavy Duty",
            "qty": 200, "price": "45.00",
        },
        {
            "raw": "قفازات طعام وسط / Food Safe Gloves Medium",
            "desc": "Food Safe Gloves Medium",
            "qty": 500, "price": "12.50",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-009",
        invoice_number="INV-POAG-2026-009",
        vendor=vendor,
        raw_vendor_name="Red Sea Restaurant Consumables",
        po_number="",                      # PO number missing
        raw_po_number="",
        invoice_date=INVOICE_DATE + timedelta(days=8),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.86,
        notes="Missing PO; dest BR-JED-220; two similar POs (warehouse vs branch)",
        extraction_remarks="Delivery dest: BR-JED-220 (Jeddah Branch 220). PO field blank.",
        delivery_note_ref="DN-RSRC-JED-0073",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.86)
    return inv


def create_scn_poag_010_high_confidence_exact_recovery() -> Invoice:
    """
    SCN-POAG-010 — HIGH-CONFIDENCE EXACT RECOVERY WITH ITEM CLUES
    ──────────────────────────────────────────────────────────────
    Invoice PO number is partially malformed: "PO/KSA/1003" (slashes).
    But item descriptions and total amount strongly align with
    PO-KSA-1003 (Gulf Frozen Foods, beef patties).
    Vendor name also matches.

    Expected PO Retrieval Agent outcome:
    - Should find PO? Yes
    - Primary strategy: normalized PO lookup + vendor + amount all converge
    - Expected recommendation_type: null (PO found with high confidence)
    - Expected confidence: high (0.85+)
    - Expected evidence keys: po_number, normalized_match, vendor_confirmed,
                              amount_match, item_descriptions_aligned
    """
    vendor = find_existing_vendor("VND-GFF-002")

    # Invoice matches PO-KSA-1003 lines (from master seed)
    # PO-KSA-1003: Beef Patty 4:1 300 CTN @ 185, Beef Patty 10:1 200 CTN @ 120
    inv_lines = [
        {
            "raw": "McD Beef Patty 4:1 Frozen / لحم برجر ماكدونالدز ٤:١ مجمد",
            "desc": "McD Beef Patty 4:1 Frozen",
            "qty": 300, "price": "185.00",
        },
        {
            "raw": "McD Beef Patty 10:1 Frozen / لحم برجر ماكدونالدز ١٠:١ مجمد",
            "desc": "McD Beef Patty 10:1 Frozen",
            "qty": 200, "price": "120.00",
        },
    ]
    subtotal = sum(_line_amt(l["qty"], l["price"]) for l in inv_lines)
    tax = _tax(subtotal)

    inv = create_invoice(
        scenario_code="SCN-POAG-010",
        invoice_number="INV-POAG-2026-010",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trdg.",
        po_number="PO/KSA/1003",          # slashes — malformed
        raw_po_number="PO/KSA/1003",
        invoice_date=INVOICE_DATE + timedelta(days=9),
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        extraction_confidence=0.90,
        notes="Malformed PO 'PO/KSA/1003'; items + amount align with PO-KSA-1003",
    )
    create_invoice_lines(inv, inv_lines, confidence=0.90)
    return inv


# ===================================================================
#  COMMAND
# ===================================================================

ALL_SCENARIO_FNS = [
    ("SCN-POAG-001", "Reordered PO segment recovery", create_scn_poag_001_reordered_po_recovery),
    ("SCN-POAG-002", "Vendor-based PO discovery", create_scn_poag_002_vendor_based_discovery),
    ("SCN-POAG-003", "Multiple open POs — ambiguity", create_scn_poag_003_multiple_open_pos),
    ("SCN-POAG-004", "Amount-based PO fallback", create_scn_poag_004_amount_based_fallback),
    ("SCN-POAG-005", "No PO found", create_scn_poag_005_no_po_found),
    ("SCN-POAG-006", "Wrong vendor with valid-like PO", create_scn_poag_006_wrong_vendor_valid_like_po),
    ("SCN-POAG-007", "Arabic-English vendor alias", create_scn_poag_007_arabic_english_vendor_alias),
    ("SCN-POAG-008", "Closed PO referenced", create_scn_poag_008_closed_po_referenced),
    ("SCN-POAG-009", "Branch vs warehouse PO ambiguity", create_scn_poag_009_branch_vs_warehouse_ambiguity),
    ("SCN-POAG-010", "High-confidence exact recovery", create_scn_poag_010_high_confidence_exact_recovery),
]


class Command(BaseCommand):
    help = "Seed 10 PO Retrieval Agent test scenarios (SCN-POAG-001..010)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously seeded POAG invoices and additional POs before re-seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== PO Retrieval Agent Test Data Seeder ==="
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
            # Skip if invoice already exists
            inv_number = f"INV-POAG-2026-{code[-3:]}"
            if Invoice.objects.filter(invoice_number=inv_number).exists():
                self.stdout.write(f"  [{code}] {label} — already exists, skipping")
                results.append((code, label, "SKIPPED"))
                continue
            inv = fn()
            line_count = inv.line_items.count()
            self.stdout.write(self.style.SUCCESS(
                f"  [{code}] {label} — Invoice {inv.invoice_number} ({line_count} lines)"
            ))
            results.append((code, label, "CREATED"))

        self._print_summary(results)

    def _flush(self):
        """Remove previously seeded POAG data."""
        inv_del, _ = Invoice.objects.filter(
            invoice_number__in=SCENARIO_INVOICE_NUMBERS
        ).delete()
        po_del, _ = PurchaseOrder.objects.filter(
            po_number__in=ADDITIONAL_PO_NUMBERS
        ).delete()
        self.stdout.write(self.style.WARNING(
            f"  Flushed {inv_del} invoice record(s) + {po_del} PO record(s)"
        ))

    def _print_summary(self, results):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n─── Summary ───────────────────────────────────────────────"
        ))
        created = sum(1 for _, _, s in results if s == "CREATED")
        skipped = sum(1 for _, _, s in results if s == "SKIPPED")

        self.stdout.write(f"  Scenarios created : {created}")
        self.stdout.write(f"  Scenarios skipped : {skipped}")
        self.stdout.write("")

        self.stdout.write(self.style.MIGRATE_HEADING("  Scenario Map:"))
        for code, label, status in results:
            icon = "✓" if status == "CREATED" else "–"
            self.stdout.write(f"    {icon} {code}  {label}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n  Expected Agent Strategies per Scenario:"
        ))
        strategy_map = [
            ("SCN-POAG-001", "intelligent PO normalization (segment reorder)", "null", "high"),
            ("SCN-POAG-002", "vendor search + amount match", "null", "medium-high"),
            ("SCN-POAG-003", "vendor search (3 candidates)", "SEND_TO_AP_REVIEW", "low-medium"),
            ("SCN-POAG-004", "vendor alias + amount match", "null", "medium"),
            ("SCN-POAG-005", "all strategies fail", "SEND_TO_AP_REVIEW", "low-medium"),
            ("SCN-POAG-006", "PO found but vendor mismatch", "SEND_TO_AP_REVIEW", "medium"),
            ("SCN-POAG-007", "Arabic alias + normalized PO", "null", "medium-high"),
            ("SCN-POAG-008", "PO found but CLOSED status", "SEND_TO_AP_REVIEW", "medium"),
            ("SCN-POAG-009", "vendor search + location filter", "SEND_TO_AP_REVIEW / null", "medium"),
            ("SCN-POAG-010", "normalized PO + vendor + amount", "null", "high"),
        ]
        for code, strategy, rec, conf in strategy_map:
            self.stdout.write(
                f"    {code}  strategy={strategy}  rec={rec}  conf={conf}"
            )

        self.stdout.write(self.style.SUCCESS(
            "\n=== PO Retrieval Agent test data seeding complete ===\n"
        ))
