"""
Transactional data seeder — POs, PO Lines, GRNs, GRN Lines, Invoices, Invoice Lines.

Each scenario from constants.SCENARIOS drives deterministic creation of linked records.
"""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.accounts.models import User
from apps.core.enums import InvoiceStatus
from apps.documents.models import (
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.vendors.models import Vendor

from .constants import (
    LINE_ITEMS_CATALOG,
    NON_PO_LINE_ITEMS,
    SCENARIOS,
    SERVICE_LINE_ITEMS,
)

logger = logging.getLogger(__name__)

# Deterministic random seed for repeatability
_rng = random.Random(42)

# VAT rate
VAT_RATE = Decimal("0.15")


def _d(val) -> Decimal:
    """Coerce to Decimal, rounded to 2 dp."""
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pick_items(category: str, count: int = 3) -> list[dict]:
    """Pick line items from catalog for the given category."""
    pool = (
        LINE_ITEMS_CATALOG.get(category)
        or SERVICE_LINE_ITEMS.get(category)
        or NON_PO_LINE_ITEMS.get(category)
    )
    if not pool:
        # Fallback — generic service line
        pool = [{"desc": f"{category} — service/supply", "uom": "EA", "price": 1500.00}]
    count = min(count, len(pool))
    return _rng.sample(pool, count)


def _base_date(scenario_num: int) -> date:
    """Stagger invoice dates over the past 90 days for realistic aging."""
    base = date(2026, 1, 15)
    offset = (scenario_num * 3) % 90
    return base - timedelta(days=offset)


# ============================================================
# PO Creation
# ============================================================

def _create_po(
    scenario: dict,
    vendor: Vendor,
    admin: User,
    line_items: list[dict],
    quantities: list[int],
) -> tuple[PurchaseOrder, list[PurchaseOrderLineItem]]:
    """Create a PO + line items for a scenario. Returns (po, po_lines)."""
    po_num = f"PO-MCD-{scenario['num']:04d}"
    inv_date = _base_date(scenario["num"])
    po_date = inv_date - timedelta(days=_rng.randint(7, 30))

    subtotal = Decimal("0")
    for item, qty in zip(line_items, quantities):
        subtotal += _d(item["price"]) * qty

    tax_amount = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    is_service = scenario["path"] == "TWO_WAY"

    po, _ = PurchaseOrder.objects.get_or_create(
        po_number=po_num,
        defaults={
            "normalized_po_number": po_num.upper(),
            "po_date": po_date,
            "vendor": vendor,
            "currency": "SAR",
            "total_amount": subtotal + tax_amount,
            "tax_amount": tax_amount,
            "status": "OPEN",
            "buyer_name": "Procurement - McDonald's KSA",
            "department": scenario.get("category", ""),
            "created_by": admin,
        },
    )

    po_lines = []
    for idx, (item, qty) in enumerate(zip(line_items, quantities), start=1):
        unit_price = _d(item["price"])
        line_amount = unit_price * qty
        line_tax = (line_amount * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pl, _ = PurchaseOrderLineItem.objects.get_or_create(
            purchase_order=po,
            line_number=idx,
            defaults={
                "item_code": f"ITM-{scenario['num']:04d}-{idx:02d}",
                "description": item["desc"],
                "quantity": Decimal(str(qty)),
                "unit_price": unit_price,
                "tax_amount": line_tax,
                "line_amount": line_amount,
                "unit_of_measure": item["uom"],
                "is_service_item": is_service,
                "is_stock_item": not is_service,
            },
        )
        po_lines.append(pl)
    return po, po_lines


# ============================================================
# GRN Creation
# ============================================================

def _create_grns(
    scenario: dict,
    po: PurchaseOrder,
    po_lines: list[PurchaseOrderLineItem],
    vendor: Vendor,
    admin: User,
) -> list[GoodsReceiptNote]:
    """Create GRN(s) for THREE_WAY scenarios. Returns list of GRNs."""
    tag = scenario["tag"]
    inv_date = _base_date(scenario["num"])
    grns_created = []

    exceptions = scenario.get("exceptions", [])

    # --- Multi-GRN scenario ---
    if "3W-MULTI-GRN" in tag:
        for drop in range(1, 4):
            grn_num = f"GRN-MCD-{scenario['num']:04d}-{drop}"
            receipt_date = inv_date - timedelta(days=15 - drop * 4)
            grn, _ = GoodsReceiptNote.objects.get_or_create(
                grn_number=grn_num,
                defaults={
                    "purchase_order": po,
                    "vendor": vendor,
                    "receipt_date": receipt_date,
                    "status": "RECEIVED",
                    "warehouse": scenario.get("branch", "WH-RUH-01"),
                    "receiver_name": "Warehouse Team",
                    "created_by": admin,
                },
            )
            # Each drop gets ~1/3 of each line
            for pl in po_lines:
                qty_fraction = int(pl.quantity / 3) if drop < 3 else int(pl.quantity - 2 * int(pl.quantity / 3))
                GRNLineItem.objects.get_or_create(
                    grn=grn,
                    line_number=pl.line_number,
                    defaults={
                        "po_line": pl,
                        "item_code": pl.item_code,
                        "description": pl.description,
                        "quantity_received": Decimal(str(qty_fraction)),
                        "quantity_accepted": Decimal(str(qty_fraction)),
                        "quantity_rejected": Decimal("0"),
                        "unit_of_measure": pl.unit_of_measure,
                    },
                )
            grns_created.append(grn)
        return grns_created

    # --- Missing GRN scenario: don't create any ---
    if "GRN_NOT_FOUND" in exceptions:
        return []

    # --- Single GRN for all other 3-way scenarios ---
    grn_num = f"GRN-MCD-{scenario['num']:04d}"

    # Delayed GRN
    if "DELAYED_RECEIPT" in exceptions:
        receipt_date = inv_date + timedelta(days=2)
    else:
        receipt_date = inv_date - timedelta(days=_rng.randint(1, 5))

    grn, _ = GoodsReceiptNote.objects.get_or_create(
        grn_number=grn_num,
        defaults={
            "purchase_order": po,
            "vendor": vendor,
            "receipt_date": receipt_date,
            "status": "RECEIVED",
            "warehouse": scenario.get("branch", "WH-RUH-01"),
            "receiver_name": "Warehouse Team",
            "created_by": admin,
        },
    )

    for pl in po_lines:
        qty_ordered = int(pl.quantity)
        qty_received = qty_ordered
        qty_accepted = qty_ordered
        qty_rejected = 0

        if "RECEIPT_SHORTAGE" in exceptions and "REJECTED" not in tag:
            # Partial receipt: 80%
            qty_received = int(qty_ordered * 0.8) or max(qty_ordered - 2, 1)
            qty_accepted = qty_received

        if "OVER_RECEIPT" in exceptions:
            # 10% over
            qty_received = int(qty_ordered * 1.1) or qty_ordered + 1
            qty_accepted = qty_received

        if "REJECTED" in tag:
            # Some rejected
            rejected_pct = 0.1
            qty_rejected = max(int(qty_ordered * rejected_pct), 1)
            qty_received = qty_ordered
            qty_accepted = qty_received - qty_rejected

        GRNLineItem.objects.get_or_create(
            grn=grn,
            line_number=pl.line_number,
            defaults={
                "po_line": pl,
                "item_code": pl.item_code,
                "description": pl.description,
                "quantity_received": Decimal(str(qty_received)),
                "quantity_accepted": Decimal(str(qty_accepted)),
                "quantity_rejected": Decimal(str(qty_rejected)),
                "unit_of_measure": pl.unit_of_measure,
            },
        )
    grns_created.append(grn)
    return grns_created


# ============================================================
# Invoice Creation
# ============================================================

def _compute_invoice_amounts(
    scenario: dict,
    po_lines: list[PurchaseOrderLineItem] | None,
    line_items: list[dict],
    quantities: list[int],
) -> tuple[list[dict], Decimal, Decimal, Decimal]:
    """
    Compute invoice line amounts, potentially introducing mismatches.
    Returns (inv_line_data_list, subtotal, tax, total).
    """
    tag = scenario["tag"]
    exceptions = scenario.get("exceptions", [])
    inv_lines_data = []

    for idx, (item, qty) in enumerate(zip(line_items, quantities)):
        unit_price = _d(item["price"])
        quantity = Decimal(str(qty))

        # --- Introduce price mismatch on first line ---
        if "PRICE_MISMATCH" in exceptions and idx == 0:
            unit_price = unit_price + _d(600)  # SAR 600 overage

        # --- Introduce quantity mismatch on first line ---
        if "QTY_MISMATCH" in exceptions and idx == 0 and "OVER" not in tag:
            quantity = quantity + Decimal("2")

        line_amount = (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        inv_lines_data.append({
            "desc": item["desc"],
            "uom": item["uom"],
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
        })

    subtotal = sum(d["line_amount"] for d in inv_lines_data)

    # --- Tax mismatch ---
    if "TAX_MISMATCH" in exceptions:
        tax = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # 5% instead of 15%
    else:
        tax = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # --- Amount mismatch (on total) ---
    if "AMOUNT_MISMATCH" in exceptions and "PRICE" not in tag and "QTY" not in tag:
        subtotal = subtotal + _d(75)  # SAR 75 difference

    total = subtotal + tax
    return inv_lines_data, subtotal, tax, total


def _invoice_status_for_scenario(scenario: dict) -> str:
    """Derive the Invoice status from the case status."""
    status = scenario["status"]
    if status in ("NEW", "INTAKE_IN_PROGRESS"):
        return InvoiceStatus.UPLOADED
    if status in ("EXTRACTION_IN_PROGRESS",):
        return InvoiceStatus.EXTRACTION_IN_PROGRESS
    if status in ("EXTRACTION_COMPLETED",):
        return InvoiceStatus.EXTRACTED
    if status in ("CLOSED", "REJECTED", "REVIEW_COMPLETED", "READY_FOR_APPROVAL",
                   "READY_FOR_GL_CODING", "READY_FOR_POSTING"):
        return InvoiceStatus.RECONCILED
    # Everything else — in progress
    return InvoiceStatus.READY_FOR_RECON


def create_transactional_data(
    scenarios: list[dict],
    vendors: dict[str, Vendor],
    admin: User,
) -> dict:
    """
    Create POs, GRNs, Invoices for all scenarios.
    Returns dict mapping scenario_num to {invoice, po, grns, po_lines, inv_lines}.
    """
    results = {}
    for sc in scenarios:
        sc_num = sc["num"]
        vendor_code = sc.get("vendor_code")
        vendor = vendors.get(vendor_code) if vendor_code else None
        category = sc["category"]
        path = sc["path"]
        tag = sc["tag"]
        inv_date = _base_date(sc_num)

        # Pick line items
        n_lines = _rng.choice([2, 3, 4]) if path != "NON_PO" else _rng.choice([1, 2])
        items = _pick_items(category, n_lines)
        quantities = [_rng.randint(5, 50) for _ in items]

        po = None
        po_lines = []
        grns = []

        # --- PO creation for PO-backed paths ---
        if path in ("TWO_WAY", "THREE_WAY") and "PO-NOT-FOUND" not in tag.upper().replace("_", "-"):
            po, po_lines = _create_po(sc, vendor, admin, items, quantities)

        # --- GRN creation for THREE_WAY ---
        if path == "THREE_WAY" and po:
            grns = _create_grns(sc, po, po_lines, vendor, admin)

        # --- Invoice ---
        inv_num = f"INV-MCD-{sc_num:04d}"
        inv_lines_data, subtotal, tax, total = _compute_invoice_amounts(
            sc, po_lines, items, quantities,
        )

        extraction_confidence = 0.95
        if "LOW-EXTRACTION" in tag or "EXTRACTION_LOW_CONFIDENCE" in sc.get("exceptions", []):
            extraction_confidence = 0.42
        elif "OCR-AMBIGUITY" in tag:
            extraction_confidence = 0.68

        inv_status = _invoice_status_for_scenario(sc)
        po_num_on_invoice = po.po_number if po else ""
        # Ambiguous PO reference for OCR scenario
        if "OCR-AMBIGUITY" in tag and po:
            po_num_on_invoice = po.po_number[:-2] + "84"  # e.g. PO-MCD-0027 → PO-MCD-0084

        # For "PO not found" scenario — put a fictitious PO ref
        if "PO-NOT-FOUND" in tag.upper().replace("_", "-"):
            po_num_on_invoice = f"PO-MCD-9{sc_num:03d}"

        # Duplicate scenario — reference same inv number
        is_duplicate = "DUPLICATE" in tag
        raw_vendor_name = vendor.name if vendor else "مؤسسة التبريد والإصلاح المحلية"

        invoice, inv_created = Invoice.objects.get_or_create(
            invoice_number=inv_num,
            defaults={
                "normalized_invoice_number": inv_num.upper(),
                "raw_invoice_number": inv_num,
                "raw_vendor_name": raw_vendor_name,
                "raw_invoice_date": str(inv_date),
                "raw_po_number": po_num_on_invoice,
                "raw_currency": "SAR",
                "raw_subtotal": str(subtotal),
                "raw_tax_amount": str(tax),
                "raw_total_amount": str(total),
                "invoice_date": inv_date,
                "po_number": po_num_on_invoice,
                "normalized_po_number": po_num_on_invoice.upper() if po_num_on_invoice else "",
                "currency": "SAR",
                "subtotal": subtotal,
                "tax_amount": tax,
                "total_amount": total,
                "status": inv_status,
                "vendor": vendor,
                "extraction_confidence": extraction_confidence,
                "is_duplicate": is_duplicate,
                "created_by": admin,
            },
        )

        # --- Invoice line items ---
        inv_lines = []
        for idx, ild in enumerate(inv_lines_data, start=1):
            il, _ = InvoiceLineItem.objects.get_or_create(
                invoice=invoice,
                line_number=idx,
                defaults={
                    "raw_description": ild["desc"],
                    "raw_quantity": str(ild["quantity"]),
                    "raw_unit_price": str(ild["unit_price"]),
                    "raw_line_amount": str(ild["line_amount"]),
                    "description": ild["desc"],
                    "normalized_description": ild["desc"].upper(),
                    "quantity": ild["quantity"],
                    "unit_price": ild["unit_price"],
                    "line_amount": ild["line_amount"],
                    "extraction_confidence": extraction_confidence,
                    "item_category": ild.get("category", ""),
                    "is_service_item": path == "TWO_WAY",
                    "is_stock_item": path == "THREE_WAY",
                },
            )
            inv_lines.append(il)

        results[sc_num] = {
            "invoice": invoice,
            "po": po,
            "po_lines": po_lines,
            "grns": grns,
            "inv_lines": inv_lines,
            "scenario": sc,
        }

    logger.info("Transactional data: %d scenarios processed", len(results))
    return results
