"""
THREE_WAY PO Invoice Seed Helpers — Reference Data, Invoices, POs, GRNs.

Creates deterministic, idempotent seed data for goods-oriented THREE_WAY
invoice processing. No reconciliation, case, or agent records are created.

All records are attributed to the AP_PROCESSOR role for audit compliance.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.rbac_models import Role, UserRole as UserRoleModel
from apps.core.enums import (
    DocumentType,
    FileProcessingState,
    InvoiceStatus,
    UserRole,
)
from apps.documents.models import (
    DocumentUpload,
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.extraction.models import ExtractionResult
from apps.vendors.models import Vendor, VendorAlias

from .constants import (
    AP_PROCESSOR_USER,
    BRANCHES,
    COST_CENTERS,
    GOODS_LINE_ITEMS,
    PO_FORMAT_TEMPLATES,
    SCENARIOS,
    THREE_WAY_VENDORS,
    WAREHOUSES,
)

logger = logging.getLogger(__name__)

# Deterministic RNG — overridden by caller via set_seed()
_rng = random.Random(42)
VAT_RATE = Decimal("0.15")


def set_seed(seed: int) -> None:
    """Reset the module-level RNG for deterministic output."""
    global _rng
    _rng = random.Random(seed)


def _d(val) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================
# AP Processor User + RBAC
# ============================================================

def create_ap_processor_user() -> User:
    """Get or create the AP_PROCESSOR seed user and ensure RBAC role assignment."""
    data = AP_PROCESSOR_USER
    user, created = User.objects.get_or_create(
        email=data["email"],
        defaults={
            "first_name": data["first_name"],
            "last_name": data["last_name"],
            "role": data["role"],
            "department": data["department"],
            "is_staff": False,
            "is_superuser": False,
        },
    )
    if created:
        user.set_password(data["password"])
        user.save(update_fields=["password"])

    # Ensure RBAC Role assignment
    role = Role.objects.filter(code="AP_PROCESSOR", is_active=True).first()
    if role:
        UserRoleModel.objects.get_or_create(
            user=user,
            role=role,
            defaults={
                "is_primary": True,
                "is_active": True,
                "assigned_by": user,
            },
        )

    logger.info(
        "AP Processor user: %s (created=%s, role=AP_PROCESSOR)",
        user.email, created,
    )
    return user


# ============================================================
# Reference Data: Vendors, Aliases
# ============================================================

def create_vendors(ap_user: User) -> dict[str, Vendor]:
    """Create or retrieve THREE_WAY goods-oriented vendors. Returns {code: Vendor}."""
    vendors: dict[str, Vendor] = {}
    created = 0
    for v in THREE_WAY_VENDORS:
        vendor, was_created = Vendor.objects.get_or_create(
            code=v["code"],
            defaults={
                "name": v["name"],
                "normalized_name": v["name"].upper().strip(),
                "tax_id": v.get("tax_id", ""),
                "country": v.get("country", "Saudi Arabia"),
                "currency": v.get("currency", "SAR"),
                "payment_terms": v.get("payment_terms", ""),
                "contact_email": v.get("contact_email", ""),
                "address": v.get("address", ""),
                "created_by": ap_user,
                "updated_by": ap_user,
            },
        )
        if was_created:
            created += 1
        vendors[v["code"]] = vendor
    logger.info("Vendors: %d created, %d total", created, len(vendors))
    return vendors


def create_vendor_aliases(vendors: dict[str, Vendor], ap_user: User) -> int:
    """Create vendor aliases including OCR variation names."""
    total_created = 0
    for v_data in THREE_WAY_VENDORS:
        vendor = vendors.get(v_data["code"])
        if not vendor:
            continue
        for alias_name in v_data.get("aliases", []):
            normalized = alias_name.upper().strip()
            _, was_created = VendorAlias.objects.get_or_create(
                vendor=vendor,
                normalized_alias=normalized,
                defaults={
                    "alias_name": alias_name,
                    "source": "manual",
                    "created_by": ap_user,
                    "updated_by": ap_user,
                },
            )
            if was_created:
                total_created += 1
    logger.info("Vendor aliases: %d created", total_created)
    return total_created


# ============================================================
# Reference Data: Cost Centers & Warehouses are metadata only
# (stored as fields on invoices — no separate model)
# ============================================================


# ============================================================
# Helpers: PO Number, Dates, Line Items
# ============================================================

def _po_number_for_scenario(sc: dict) -> str:
    """Generate the PO number that will be stored on the PO record (canonical)."""
    return f"PO-3W-{sc['num']:04d}"


def _po_reference_on_invoice(sc: dict) -> str:
    """Generate the PO reference as it appears on the invoice (may be corrupted)."""
    fmt = sc.get("po_format", "clean")
    special = sc.get("special", {})

    if fmt == "missing":
        return ""
    if fmt == "malformed":
        return special.get("malformed_po_text", f"PO?3W?{sc['num']:04d}")
    if fmt in PO_FORMAT_TEMPLATES and PO_FORMAT_TEMPLATES[fmt]:
        tmpl = PO_FORMAT_TEMPLATES[fmt]
        return tmpl.format(num=sc["num"])
    # Override from special
    if "ocr_po_variation" in special:
        return special["ocr_po_variation"]
    return _po_number_for_scenario(sc)


def _base_date(scenario_num: int) -> date:
    """Stagger invoice dates over the past 90 days."""
    base = date(2026, 2, 15)
    offset = (scenario_num * 4) % 90
    return base - timedelta(days=offset)


def _pick_items(category: str, count: int) -> list[dict]:
    """Pick line items from the goods catalog."""
    pool = GOODS_LINE_ITEMS.get(category)
    if not pool:
        pool = [{"desc": f"{category} — goods supply", "uom": "EA",
                 "code": "GEN-001", "price": 150.00}]
    count = min(count, len(pool))
    return _rng.sample(pool, count)


# ============================================================
# Raw Extraction JSON Builder
# ============================================================

def build_raw_extraction_json(
    sc: dict,
    vendor_name: str,
    inv_number: str,
    inv_date: date,
    po_ref: str,
    warehouse_text: str,
    line_data: list[dict],
    subtotal: Decimal,
    tax: Decimal,
    total: Decimal,
    confidence: float,
) -> dict:
    """Build a realistic OCR extraction payload with field confidence scores."""
    base_conf = confidence
    _noise = lambda c: round(min(1.0, max(0.0, c + _rng.uniform(-0.08, 0.05))), 3)

    lines_payload = []
    for idx, ld in enumerate(line_data, start=1):
        lines_payload.append({
            "line_number": idx,
            "description": {"value": ld["desc"], "confidence": _noise(base_conf)},
            "quantity": {"value": str(ld["quantity"]), "confidence": _noise(base_conf)},
            "unit_price": {"value": str(ld["unit_price"]), "confidence": _noise(base_conf)},
            "amount": {"value": str(ld["line_amount"]), "confidence": _noise(base_conf)},
            "uom": {"value": ld.get("uom", "EA"), "confidence": _noise(base_conf)},
        })

    return {
        "extraction_engine": "azure_document_intelligence",
        "engine_version": "2024-02-29-preview",
        "document_type": "invoice",
        "source_type": "scan",
        "vendor_block": {
            "name": {"value": vendor_name, "confidence": _noise(base_conf)},
            "address": {"value": "Saudi Arabia", "confidence": _noise(base_conf - 0.1)},
        },
        "invoice_number": {"value": inv_number, "confidence": _noise(base_conf)},
        "invoice_date": {"value": str(inv_date), "confidence": _noise(base_conf)},
        "po_reference": {"value": po_ref, "confidence": _noise(base_conf - 0.05)},
        "warehouse_text": {"value": warehouse_text, "confidence": _noise(base_conf - 0.1)},
        "currency": {"value": "SAR", "confidence": _noise(base_conf)},
        "subtotal": {"value": str(subtotal), "confidence": _noise(base_conf)},
        "vat_block": {
            "rate": {"value": "15%", "confidence": _noise(base_conf)},
            "amount": {"value": str(tax), "confidence": _noise(base_conf)},
        },
        "total_amount": {"value": str(total), "confidence": _noise(base_conf)},
        "line_items": lines_payload,
        "overall_confidence": round(base_conf, 3),
        "page_count": 1,
        "language_detected": "en-ar",
    }


def build_normalized_json(
    vendor_name: str,
    inv_number: str,
    inv_date: date,
    po_ref: str,
    warehouse: str,
    cost_center: str | None,
    line_data: list[dict],
    subtotal: Decimal,
    tax: Decimal,
    total: Decimal,
) -> dict:
    """Build the normalized extraction output."""
    return {
        "vendor_name": vendor_name,
        "invoice_number": inv_number,
        "invoice_date": str(inv_date),
        "po_number": po_ref,
        "warehouse": warehouse,
        "cost_center": cost_center or "",
        "currency": "SAR",
        "subtotal": str(subtotal),
        "tax_amount": str(tax),
        "total_amount": str(total),
        "line_items": [
            {
                "line_number": idx,
                "description": ld["desc"],
                "quantity": str(ld["quantity"]),
                "unit_price": str(ld["unit_price"]),
                "amount": str(ld["line_amount"]),
            }
            for idx, ld in enumerate(line_data, start=1)
        ],
    }


# ============================================================
# PO Creation
# ============================================================

def _create_po(
    sc: dict,
    vendor: Vendor,
    ap_user: User,
    line_items: list[dict],
    quantities: list[int],
) -> tuple[PurchaseOrder, list[PurchaseOrderLineItem]]:
    """Create a PO and its line items for a scenario."""
    po_num = _po_number_for_scenario(sc)
    inv_date = _base_date(sc["num"])
    po_date = inv_date - timedelta(days=_rng.randint(7, 30))

    subtotal = Decimal("0")
    for item, qty in zip(line_items, quantities):
        subtotal += _d(item["price"]) * qty
    tax_amount = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    po, _ = PurchaseOrder.objects.get_or_create(
        po_number=po_num,
        defaults={
            "normalized_po_number": po_num.replace("-", "").replace("#", "").upper(),
            "po_date": po_date,
            "vendor": vendor,
            "currency": "SAR",
            "total_amount": subtotal + tax_amount,
            "tax_amount": tax_amount,
            "status": "OPEN",
            "buyer_name": "Procurement — McDonald's KSA",
            "department": sc.get("category", ""),
            "created_by": ap_user,
            "updated_by": ap_user,
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
                "item_code": item.get("code", f"ITM-3W-{sc['num']:04d}-{idx:02d}"),
                "description": item["desc"],
                "quantity": Decimal(str(qty)),
                "unit_price": unit_price,
                "tax_amount": line_tax,
                "line_amount": line_amount,
                "unit_of_measure": item.get("uom", "EA"),
                "item_category": sc.get("category", ""),
                "is_service_item": False,
                "is_stock_item": True,
            },
        )
        po_lines.append(pl)
    return po, po_lines


# ============================================================
# GRN Creation
# ============================================================

def _create_grns(
    sc: dict,
    po: PurchaseOrder,
    po_lines: list[PurchaseOrderLineItem],
    vendor: Vendor,
    ap_user: User,
) -> list[GoodsReceiptNote]:
    """Create GRN(s) for a scenario. Respects special directives."""
    special = sc.get("special", {})
    exceptions = sc.get("exceptions", [])
    inv_date = _base_date(sc["num"])
    grns = []

    # --- Skip GRN entirely ---
    if special.get("skip_grn"):
        return []

    # --- Warehouse resolution ---
    warehouse_code = sc.get("warehouse", "RIYADH_DC")
    if "grn_warehouse" in special:
        warehouse_code = special["grn_warehouse"]

    wh_name = warehouse_code
    for wh in WAREHOUSES:
        if wh["code"] == warehouse_code:
            wh_name = wh["name"]
            break

    # --- Multi-GRN drops ---
    n_drops = special.get("multi_grn_drops", 1)

    for drop in range(1, n_drops + 1):
        grn_suffix = f"-{drop}" if n_drops > 1 else ""
        grn_num = f"GRN-3W-{sc['num']:04d}{grn_suffix}"

        # Receipt date: default before invoice
        if "DELAYED_RECEIPT" in exceptions or special.get("grn_delay_days"):
            delay = special.get("grn_delay_days", 2)
            receipt_date = inv_date + timedelta(days=delay)
        elif n_drops > 1:
            receipt_date = inv_date - timedelta(days=15 - drop * 4)
        else:
            receipt_date = inv_date - timedelta(days=_rng.randint(1, 5))

        grn, _ = GoodsReceiptNote.objects.get_or_create(
            grn_number=grn_num,
            defaults={
                "purchase_order": po,
                "vendor": vendor,
                "receipt_date": receipt_date,
                "status": "RECEIVED",
                "warehouse": wh_name,
                "receiver_name": "Warehouse Team",
                "created_by": ap_user,
                "updated_by": ap_user,
            },
        )

        receipt_pct = special.get("receipt_pct", 1.0)

        for pl in po_lines:
            qty_ordered = int(pl.quantity)

            if n_drops > 1:
                # Split across drops
                base_per_drop = qty_ordered // n_drops
                if drop == n_drops:
                    qty_this_drop = qty_ordered - base_per_drop * (n_drops - 1)
                else:
                    qty_this_drop = base_per_drop
                qty_received = qty_this_drop
                qty_accepted = qty_this_drop
                qty_rejected = 0
            else:
                qty_received = max(1, int(qty_ordered * receipt_pct))
                qty_accepted = qty_received
                qty_rejected = 0

                if "INVOICE_QTY_EXCEEDS_RECEIVED" in exceptions:
                    qty_received = max(1, int(qty_ordered * receipt_pct))
                    qty_accepted = qty_received

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
        grns.append(grn)

    return grns


# ============================================================
# Invoice Amounts Computation
# ============================================================

def _compute_invoice_amounts(
    sc: dict,
    line_items: list[dict],
    quantities: list[int],
) -> tuple[list[dict], Decimal, Decimal, Decimal]:
    """
    Compute invoice line amounts, potentially introducing mismatches.
    Returns (inv_line_data_list, subtotal, tax, total).
    """
    special = sc.get("special", {})
    exceptions = sc.get("exceptions", [])
    inv_lines_data = []

    for idx, (item, qty) in enumerate(zip(line_items, quantities)):
        unit_price = _d(item["price"])
        quantity = Decimal(str(qty))

        # Price mismatch on first line
        if "PRICE_MISMATCH" in exceptions and idx == 0:
            inflate_pct = special.get("price_inflate_pct", 10)
            unit_price = (unit_price * (100 + inflate_pct) / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        # Qty exceeds — invoice shows ordered qty, GRN shows less
        # (No change to invoice qty needed — it matches PO)

        line_amount = (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        inv_lines_data.append({
            "desc": item["desc"],
            "uom": item.get("uom", "EA"),
            "code": item.get("code", ""),
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
        })

    subtotal = sum(d["line_amount"] for d in inv_lines_data)

    # Amount inflate
    if special.get("amount_inflate"):
        subtotal = subtotal + _d(special["amount_inflate"])

    # Tax calculation
    if special.get("missing_tax"):
        tax = Decimal("0.00")
    elif special.get("tax_rate_override") is not None:
        tax = (subtotal * Decimal(str(special["tax_rate_override"]))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    elif "TAX_MISMATCH" in exceptions and not special.get("tax_rate_override"):
        tax = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        tax = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total = subtotal + tax
    return inv_lines_data, subtotal, tax, total


# ============================================================
# Document Upload Creation
# ============================================================

def _create_document_upload(
    sc: dict,
    ap_user: User,
    filename: str,
) -> DocumentUpload:
    """Create a DocumentUpload record for file tracking."""
    file_hash = hashlib.sha256(
        f"three_way_seed_{sc['num']}_{filename}".encode()
    ).hexdigest()

    upload, _ = DocumentUpload.objects.get_or_create(
        file_hash=file_hash,
        defaults={
            "original_filename": filename,
            "file_size": _rng.randint(80000, 350000),
            "content_type": "application/pdf",
            "document_type": DocumentType.INVOICE,
            "processing_state": FileProcessingState.COMPLETED,
            "processing_message": "Extraction completed successfully",
            "uploaded_by": ap_user,
            "created_by": ap_user,
            "updated_by": ap_user,
        },
    )
    return upload


# ============================================================
# Extraction Result Creation
# ============================================================

def _create_extraction_result(
    upload: DocumentUpload,
    invoice: Invoice,
    raw_json: dict,
    confidence: float,
    ap_user: User,
) -> ExtractionResult:
    """Create an ExtractionResult record for audit."""
    result, _ = ExtractionResult.objects.get_or_create(
        document_upload=upload,
        invoice=invoice,
        defaults={
            "engine_name": "azure_document_intelligence",
            "engine_version": "2024-02-29-preview",
            "raw_response": raw_json,
            "confidence": confidence,
            "duration_ms": _rng.randint(1200, 4500),
            "success": True,
            "created_by": ap_user,
            "updated_by": ap_user,
        },
    )
    return result


# ============================================================
# Main Orchestrator: Create THREE_WAY Invoices
# ============================================================

def create_three_way_invoices(
    scenarios: list[dict],
    vendors: dict[str, Vendor],
    ap_user: User,
) -> dict[int, dict]:
    """
    Create POs, GRNs, Invoices, and supporting records for all scenarios.
    Returns {scenario_num: {invoice, po, grns, upload, extraction}}.
    """
    results: dict[int, dict] = {}

    # Track amounts for duplicate/mirror scenarios
    scenario_amounts: dict[int, Decimal] = {}

    for sc in scenarios:
        sc_num = sc["num"]
        vendor_code = sc["vendor_code"]
        vendor = vendors.get(vendor_code)
        if not vendor:
            logger.warning("Vendor %s not found, skipping scenario %d", vendor_code, sc_num)
            continue

        special = sc.get("special", {})
        category = sc["category"]
        inv_date = _base_date(sc_num)
        n_lines = sc.get("n_lines", 3)
        qty_lo, qty_hi = sc.get("qty_range", (10, 30))

        # Pick items and quantities
        items = _pick_items(category, n_lines)
        quantities = [_rng.randint(qty_lo, qty_hi) for _ in items]

        # --- PO creation (always for THREE_WAY unless PO is missing) ---
        po = None
        po_lines = []
        if sc.get("po_format") != "missing":
            po, po_lines = _create_po(sc, vendor, ap_user, items, quantities)

        # --- GRN creation ---
        grns = []
        if po and not special.get("skip_grn"):
            grns = _create_grns(sc, po, po_lines, vendor, ap_user)

        # --- Invoice amounts ---
        inv_lines_data, subtotal, tax, total = _compute_invoice_amounts(sc, items, quantities)

        # Handle mirror-amount scenarios
        if special.get("mirror_amounts_of_scenario"):
            mirror_num = special["mirror_amounts_of_scenario"]
            if mirror_num in scenario_amounts:
                # Adjust total to match
                total = scenario_amounts[mirror_num]
                tax = (total * VAT_RATE / (1 + VAT_RATE)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                subtotal = total - tax

        scenario_amounts[sc_num] = total

        # --- PO reference on invoice ---
        po_ref_on_invoice = _po_reference_on_invoice(sc)

        # --- Vendor name on invoice ---
        if special.get("missing_vendor_name"):
            raw_vendor_name = ""
        elif special.get("vendor_alias_on_invoice"):
            raw_vendor_name = special["vendor_alias_on_invoice"]
        else:
            raw_vendor_name = vendor.name

        # --- Warehouse text on invoice ---
        warehouse_code = sc.get("warehouse", "RIYADH_DC")
        warehouse_text = warehouse_code
        for wh in WAREHOUSES:
            if wh["code"] == warehouse_code:
                warehouse_text = wh["name"]
                break
        if special.get("warehouse_on_invoice"):
            warehouse_text = special["warehouse_on_invoice"]

        # --- Currency ---
        currency = "SAR"
        if special.get("missing_currency"):
            currency = ""

        # --- Invoice number ---
        inv_num = f"INV-3W-{sc_num:04d}"

        # Handle duplicate invoice number
        if special.get("duplicate_of_scenario"):
            dup_num = special["duplicate_of_scenario"]
            inv_num = f"INV-3W-{dup_num:04d}"
            # Need a unique number for DB — add suffix
            inv_num_db = f"INV-3W-{sc_num:04d}-DUP"
        else:
            inv_num_db = inv_num

        # --- Uploaded filename ---
        vendor_short = vendor.code.replace("V3W-", "")
        filename = f"INV_{vendor_short}_{inv_date.strftime('%Y%m%d')}_{sc['tag']}.pdf"

        # --- Invoice status ---
        inv_status = sc.get("invoice_status", InvoiceStatus.READY_FOR_RECON)

        # --- Extraction confidence ---
        conf = sc.get("extraction_confidence", 0.90)

        # --- Document Upload ---
        upload = _create_document_upload(sc, ap_user, filename)

        # --- Raw / Normalized JSON ---
        raw_json = build_raw_extraction_json(
            sc=sc,
            vendor_name=raw_vendor_name,
            inv_number=inv_num,
            inv_date=inv_date,
            po_ref=po_ref_on_invoice,
            warehouse_text=warehouse_text,
            line_data=inv_lines_data,
            subtotal=subtotal,
            tax=tax,
            total=total,
            confidence=conf,
        )
        norm_json = build_normalized_json(
            vendor_name=raw_vendor_name,
            inv_number=inv_num,
            inv_date=inv_date,
            po_ref=po_ref_on_invoice,
            warehouse=warehouse_text,
            cost_center=sc.get("cost_center"),
            line_data=inv_lines_data,
            subtotal=subtotal,
            tax=tax,
            total=total,
        )

        # --- Determine if vendor should be linked ---
        link_vendor = vendor if not special.get("missing_vendor_name") else None

        # --- Create Invoice ---
        is_dup = "DUPLICATE_INVOICE" in sc.get("exceptions", [])

        invoice, _ = Invoice.objects.get_or_create(
            invoice_number=inv_num_db,
            defaults={
                "normalized_invoice_number": inv_num_db.upper().replace("-", ""),
                "document_upload": upload,
                "vendor": link_vendor,
                "raw_vendor_name": raw_vendor_name,
                "raw_invoice_number": inv_num,
                "raw_invoice_date": str(inv_date),
                "raw_po_number": po_ref_on_invoice,
                "raw_currency": currency if currency else "",
                "raw_subtotal": str(subtotal),
                "raw_tax_amount": str(tax),
                "raw_total_amount": str(total),
                "invoice_date": inv_date,
                "po_number": po_ref_on_invoice,
                "normalized_po_number": (
                    po_ref_on_invoice.replace("-", "").replace("#", "")
                    .replace(" ", "").replace("!", "").replace("?", "").upper()
                    if po_ref_on_invoice else ""
                ),
                "currency": currency,
                "subtotal": subtotal,
                "tax_amount": tax,
                "total_amount": total,
                "status": inv_status,
                "extraction_confidence": conf,
                "extraction_raw_json": raw_json,
                "is_duplicate": is_dup,
                "created_by": ap_user,
                "updated_by": ap_user,
            },
        )

        # --- Invoice Line Items ---
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
                    "extraction_confidence": conf,
                    "item_category": category,
                    "is_service_item": False,
                    "is_stock_item": True,
                },
            )
            inv_lines.append(il)

        # --- Extraction Result ---
        extraction = _create_extraction_result(upload, invoice, raw_json, conf, ap_user)

        results[sc_num] = {
            "invoice": invoice,
            "po": po,
            "po_lines": po_lines,
            "grns": grns,
            "inv_lines": inv_lines,
            "upload": upload,
            "extraction": extraction,
            "scenario": sc,
        }

    logger.info("THREE_WAY invoices: %d scenarios processed", len(results))
    return results


# ============================================================
# Statistics Collector
# ============================================================

def collect_stats(results: dict[int, dict]) -> dict[str, Any]:
    """Collect summary statistics from seeded data."""
    stats = {
        "vendors_created": Vendor.objects.filter(code__startswith="V3W-").count(),
        "aliases_created": VendorAlias.objects.filter(vendor__code__startswith="V3W-").count(),
        "invoices_created": 0,
        "pos_created": 0,
        "grns_created": 0,
        "uploads_created": 0,
        "extractions_created": 0,
        "line_items_invoice": 0,
        "line_items_po": 0,
        "duplicate_invoices": 0,
        "malformed_po_refs": 0,
        "po_agent_trigger": 0,
        "grn_agent_trigger": 0,
        "high_value_invoices": 0,
        "warehouse_mismatch": 0,
        "incomplete_invoices": 0,
        "low_confidence": 0,
        "medium_confidence": 0,
        "high_confidence": 0,
    }

    for sc_num, data in results.items():
        sc = data["scenario"]
        special = sc.get("special", {})
        exceptions = sc.get("exceptions", [])
        conf = sc.get("extraction_confidence", 0.90)

        stats["invoices_created"] += 1
        if data.get("po"):
            stats["pos_created"] += 1
        stats["grns_created"] += len(data.get("grns", []))
        stats["uploads_created"] += 1
        stats["extractions_created"] += 1
        stats["line_items_invoice"] += len(data.get("inv_lines", []))
        stats["line_items_po"] += len(data.get("po_lines", []))

        if "DUPLICATE_INVOICE" in exceptions:
            stats["duplicate_invoices"] += 1
        if sc.get("po_format") in ("malformed", "ocr_damaged"):
            stats["malformed_po_refs"] += 1
        if sc.get("po_format") in ("missing", "ocr_damaged", "malformed", "normalized", "hash_prefix"):
            stats["po_agent_trigger"] += 1
        if special.get("skip_grn") or "GRN_NOT_FOUND" in exceptions:
            stats["grn_agent_trigger"] += 1
        if "RECEIPT_SHORTAGE" in exceptions or "OVER_RECEIPT" in exceptions:
            stats["grn_agent_trigger"] += 1
        if "DELAYED_RECEIPT" in exceptions:
            stats["grn_agent_trigger"] += 1
        if "RECEIPT_LOCATION_MISMATCH" in exceptions:
            stats["warehouse_mismatch"] += 1
        if special.get("high_value"):
            stats["high_value_invoices"] += 1
        if special.get("missing_vendor_name") or special.get("missing_cost_center") \
                or special.get("missing_tax") or special.get("missing_currency"):
            stats["incomplete_invoices"] += 1

        if conf < 0.70:
            stats["low_confidence"] += 1
        elif conf < 0.85:
            stats["medium_confidence"] += 1
        else:
            stats["high_confidence"] += 1

    return stats
