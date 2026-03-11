"""
Management command: seed_mixed_mode_data

Seeds test data for **configurable 2-way / 3-way reconciliation mode**:

  - ReconciliationConfig with mode resolver enabled
  - ReconciliationPolicy rules (vendor, category, service/stock, location)
  - New vendor (Gulf Professional Services) for service invoices
  - POs for 2-way (service) and 3-way (stock) scenarios with item classification
  - GRNs ONLY for stock POs (2-way POs have no GRN - by design)
  - Invoices (SCN-MODE-001 through SCN-MODE-012) spanning all mode resolution paths
  - Back-fills item_category / is_service_item / is_stock_item on EXISTING PO lines

Depends on:
  - seed_saudi_mcd_data (vendors, base POs, GRNs)

Creates ONLY:
  - ReconciliationPolicy records
  - Vendor + alias (Gulf Professional Services)
  - PurchaseOrders (PO-KSA-3001..3012)+lines with item classification
  - GoodsReceiptNotes (GRN-MODE-*) ONLY for stock POs
  - Invoices (INV-MODE-*) + InvoiceLineItems with item classification
  - Updates ReconciliationConfig mode fields

Does NOT create:
  - ReconciliationRun, ReconciliationResult, ReconciliationException
  - AgentRun, ToolCall, ReviewAssignment, AuditEvent

Usage:
    python manage.py seed_mixed_mode_data
    python manage.py seed_mixed_mode_data --flush
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.core.enums import InvoiceStatus, ReconciliationMode
from apps.core.utils import normalize_po_number, normalize_string
from apps.documents.models import (
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.reconciliation.models import ReconciliationConfig, ReconciliationPolicy
from apps.vendors.models import Vendor, VendorAlias

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VAT_RATE = Decimal("0.15")
BASE_DATE = date(2026, 2, 15)
INVOICE_DATE = BASE_DATE + timedelta(days=5)


def _d(val) -> Decimal:
    return Decimal(str(val))


def _line_amt(qty, price) -> Decimal:
    return (_d(qty) * _d(price)).quantize(Decimal("0.01"))


def _tax(amount) -> Decimal:
    return (amount * VAT_RATE).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Invoice numbers for flush
# ---------------------------------------------------------------------------
SCENARIO_INVOICE_NUMBERS = [
    "INV-MODE-001",  # SCN-MODE-001
    "INV-MODE-002",  # SCN-MODE-002
    "INV-MODE-003",  # SCN-MODE-003
    "INV-MODE-004",  # SCN-MODE-004
    "INV-MODE-005",  # SCN-MODE-005
    "INV-MODE-006",  # SCN-MODE-006
    "INV-MODE-007",  # SCN-MODE-007
    "INV-MODE-008",  # SCN-MODE-008
    "INV-MODE-009",  # SCN-MODE-009
    "INV-MODE-010",  # SCN-MODE-010
    "INV-MODE-011",  # SCN-MODE-011
    "INV-MODE-012",  # SCN-MODE-012
]

PO_NUMBERS = [f"PO-KSA-30{i:02d}" for i in range(1, 13)]
GRN_NUMBERS = [
    "GRN-MODE-3003",
    "GRN-MODE-3004",
    "GRN-MODE-3006",
    "GRN-MODE-3007",
    "GRN-MODE-3008",
    "GRN-MODE-3009",
    "GRN-MODE-3010",
    "GRN-MODE-3011",
    "GRN-MODE-3012",
]

SERVICE_VENDOR_CODE = "VND-GPS-011"

POLICY_CODES = [
    "POL-SVC-VENDOR",
    "POL-SVC-GLOBAL",
    "POL-STOCK-GLOBAL",
    "POL-FOOD-3WAY",
    "POL-LOGISTICS-2WAY",
    "POL-WH-RUH-3WAY",
    "POL-BRANCH-2WAY",
]


# ===================================================================
#  HELPERS
# ===================================================================

def get_vendor(code: str) -> Vendor:
    return Vendor.objects.get(code=code)


def get_po(po_number: str) -> PurchaseOrder:
    return PurchaseOrder.objects.get(po_number=po_number)


def get_ap_user() -> User:
    return User.objects.filter(role="AP_PROCESSOR").first() or User.objects.first()


def get_admin_user() -> User:
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


def create_invoice(
    *,
    invoice_number: str,
    vendor: Vendor | None,
    raw_vendor_name: str,
    po_number: str,
    invoice_date: date,
    subtotal: Decimal,
    tax_amount: Decimal,
    total_amount: Decimal,
    extraction_confidence: float = 0.93,
    status: str = InvoiceStatus.READY_FOR_RECON,
    notes: str = "",
    extraction_raw_json: dict | None = None,
) -> Invoice:
    user = get_ap_user()
    return Invoice.objects.create(
        vendor=vendor,
        raw_vendor_name=raw_vendor_name,
        raw_invoice_number=invoice_number,
        raw_invoice_date=str(invoice_date),
        raw_po_number=po_number,
        raw_currency="SAR",
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
        notes=notes,
        extraction_raw_json=extraction_raw_json,
        created_by=user,
    )


def add_line(
    invoice: Invoice,
    *,
    line_number: int,
    description: str,
    quantity: Decimal,
    unit_price: Decimal,
    tax_amount: Decimal | None = None,
    line_amount: Decimal | None = None,
    is_service_item: bool | None = None,
    is_stock_item: bool | None = None,
    item_category: str = "",
    confidence: float = 0.93,
) -> InvoiceLineItem:
    amt = line_amount if line_amount is not None else _line_amt(quantity, unit_price)
    tax = tax_amount if tax_amount is not None else _tax(amt)
    return InvoiceLineItem.objects.create(
        invoice=invoice,
        line_number=line_number,
        raw_description=description,
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
        is_service_item=is_service_item,
        is_stock_item=is_stock_item,
        item_category=item_category,
    )


def create_po_with_lines(
    *,
    po_number: str,
    vendor: Vendor,
    po_date: date,
    lines: list[dict],
    department: str = "Procurement",
    notes: str = "",
    status: str = "OPEN",
) -> tuple[PurchaseOrder, list[PurchaseOrderLineItem]]:
    admin = get_admin_user()
    subtotal = sum(_line_amt(ln["qty"], ln["price"]) for ln in lines)
    tax_total = sum(_tax(_line_amt(ln["qty"], ln["price"])) for ln in lines)
    po, _ = PurchaseOrder.objects.get_or_create(
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
    created_lines = []
    for idx, ln in enumerate(lines, start=1):
        amt = _line_amt(ln["qty"], ln["price"])
        pol, _ = PurchaseOrderLineItem.objects.get_or_create(
            purchase_order=po,
            line_number=idx,
            defaults={
                "item_code": ln["item_code"],
                "description": ln["description"],
                "quantity": _d(ln["qty"]),
                "unit_price": _d(ln["price"]),
                "tax_amount": _tax(amt),
                "line_amount": amt,
                "unit_of_measure": ln.get("uom", "EA"),
                "item_category": ln.get("item_category", ""),
                "is_service_item": ln.get("is_service_item"),
                "is_stock_item": ln.get("is_stock_item"),
            },
        )
        created_lines.append(pol)
    return po, created_lines


def create_grn_with_lines(
    *,
    grn_number: str,
    po: PurchaseOrder,
    vendor: Vendor,
    receipt_date: date,
    po_lines: list[PurchaseOrderLineItem],
    qty_overrides: dict[int, Decimal] | None = None,
    warehouse: str = "WH-RUH-01",
) -> tuple[GoodsReceiptNote, list[GRNLineItem]]:
    """Create a GRN with lines matching PO lines. qty_overrides keys are 1-based line numbers."""
    admin = get_admin_user()
    grn, _ = GoodsReceiptNote.objects.get_or_create(
        grn_number=grn_number,
        defaults={
            "purchase_order": po,
            "vendor": vendor,
            "receipt_date": receipt_date,
            "status": "RECEIVED",
            "warehouse": warehouse,
            "receiver_name": "Omar Al-Ghamdi",
            "created_by": admin,
        },
    )
    overrides = qty_overrides or {}
    created_lines = []
    for idx, pol in enumerate(po_lines, start=1):
        qty = overrides.get(idx, pol.quantity)
        gl, _ = GRNLineItem.objects.get_or_create(
            grn=grn,
            line_number=idx,
            defaults={
                "po_line": pol,
                "item_code": pol.item_code,
                "description": pol.description,
                "quantity_received": qty,
                "quantity_accepted": qty,
                "quantity_rejected": _d(0),
                "unit_of_measure": pol.unit_of_measure,
            },
        )
        created_lines.append(gl)
    return grn, created_lines


# ===================================================================
#  SERVICE VENDOR
# ===================================================================

def create_service_vendor(admin: User) -> Vendor:
    vendor, _ = Vendor.objects.get_or_create(
        code=SERVICE_VENDOR_CODE,
        defaults={
            "name": "Gulf Professional Services Co.",
            "normalized_name": normalize_string("Gulf Professional Services Co."),
            "tax_id": "3101112345",
            "address": "King Fahd Road, Riyadh 12211, Saudi Arabia",
            "country": "SA",
            "currency": "SAR",
            "payment_terms": "Net 30",
            "contact_email": "invoices@gulfpro.sa",
            "created_by": admin,
        },
    )
    for alias_name in ["Gulf Pro Services", "GPS Co.", "Gulf Professional Svc"]:
        VendorAlias.objects.get_or_create(
            vendor=vendor,
            normalized_alias=normalize_string(alias_name),
            defaults={
                "alias_name": alias_name,
                "source": "seed",
                "created_by": admin,
            },
        )
    return vendor


# ===================================================================
#  RECONCILIATION POLICIES
# ===================================================================

def create_policies(admin: User, service_vendor: Vendor) -> int:
    """Create reconciliation policies covering different resolution paths.

    Returns the number of policies created.
    """
    policies = [
        # -- Priority 10: Vendor-specific service rule --
        # Gulf Professional Services -> always 2-way
        {
            "policy_code": "POL-SVC-VENDOR",
            "policy_name": "Gulf Professional Services - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "vendor": service_vendor,
            "is_service_invoice": True,
            "priority": 10,
            "notes": "All invoices from GPS are service-type; skip GRN.",
        },
        # -- Priority 20: Global service invoice rule --
        {
            "policy_code": "POL-SVC-GLOBAL",
            "policy_name": "Service Invoices - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "is_service_invoice": True,
            "priority": 20,
            "notes": "Any invoice flagged as service -> 2-Way reconciliation.",
        },
        # -- Priority 30: Global stock/inventory rule --
        {
            "policy_code": "POL-STOCK-GLOBAL",
            "policy_name": "Stock/Inventory Invoices - 3-Way",
            "reconciliation_mode": ReconciliationMode.THREE_WAY,
            "is_stock_invoice": True,
            "priority": 30,
            "notes": "Any invoice flagged as stock/inventory -> 3-Way with GRN.",
        },
        # -- Priority 40: Category-based food rule --
        {
            "policy_code": "POL-FOOD-3WAY",
            "policy_name": "Food Category - 3-Way",
            "reconciliation_mode": ReconciliationMode.THREE_WAY,
            "item_category": "Food",
            "priority": 40,
            "notes": "Food items always require GRN verification.",
        },
        # -- Priority 50: Category-based logistics / transport --
        {
            "policy_code": "POL-LOGISTICS-2WAY",
            "policy_name": "Logistics & Transport - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "item_category": "Logistics",
            "priority": 50,
            "notes": "Logistics/transport services - no GRN needed.",
        },
        # -- Priority 60: Location-based warehouse rule --
        {
            "policy_code": "POL-WH-RUH-3WAY",
            "policy_name": "Riyadh Warehouse - 3-Way",
            "reconciliation_mode": ReconciliationMode.THREE_WAY,
            "location_code": "WH-RUH-01",
            "priority": 60,
            "notes": "All shipments to Riyadh Central Warehouse require GRN.",
        },
        # -- Priority 70: Location-based branch rule --
        {
            "policy_code": "POL-BRANCH-2WAY",
            "policy_name": "Direct Branch Purchases - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "business_unit": "Branch Operations",
            "priority": 70,
            "notes": "Branch direct purchases (services/small items) - no GRN.",
        },
    ]

    created = 0
    for pdata in policies:
        vendor = pdata.pop("vendor", None)
        _, was_created = ReconciliationPolicy.objects.get_or_create(
            policy_code=pdata["policy_code"],
            defaults={
                **pdata,
                "vendor": vendor,
                "is_active": True,
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
    return created


# ===================================================================
#  RECON CONFIG UPDATE
# ===================================================================

def update_recon_config() -> ReconciliationConfig:
    """Ensure the default config has mode resolver enabled."""
    config, _ = ReconciliationConfig.objects.get_or_create(
        is_default=True,
        defaults={"name": "Default"},
    )
    config.enable_mode_resolver = True
    config.enable_two_way_for_services = True
    config.enable_grn_for_stock_items = True
    config.default_reconciliation_mode = ReconciliationMode.THREE_WAY
    config.save()
    return config


# ===================================================================
#  BACK-FILL ITEM CLASSIFICATION ON EXISTING PO LINES
# ===================================================================

def backfill_item_classification() -> int:
    """Tag existing seed PO lines (PO-KSA-1001..1025) with item
    category and service/stock flags based on known item codes.

    Returns the number of updated lines.
    """
    # Map item_code prefixes -> classification
    stock_prefixes = {
        "AFS-": ("Food", False, True),       # food
        "GFF-": ("Food", False, True),       # frozen food
        "AWP-": ("Food", False, True),       # poultry
        "RBC-": ("Beverage", False, True),   # beverages
        "NEO-": ("Food", False, True),       # edible oils
        "AKD-": ("Food", False, True),       # dairy
        "RSRC-": ("Consumables", False, True),  # restaurant consumables
    }
    packaging_prefix = "SPS-"  # packaging -> stock
    logistics_prefix = "DCCL-"  # cold chain logistics -> service-like but stock delivery

    updated = 0
    for pol in PurchaseOrderLineItem.objects.filter(
        purchase_order__po_number__startswith="PO-KSA-1"
    ):
        if pol.item_category:
            continue  # already classified

        item_code = pol.item_code or ""
        matched = False
        for prefix, (category, is_svc, is_stk) in stock_prefixes.items():
            if item_code.startswith(prefix):
                pol.item_category = category
                pol.is_service_item = is_svc
                pol.is_stock_item = is_stk
                pol.save(update_fields=["item_category", "is_service_item", "is_stock_item"])
                updated += 1
                matched = True
                break

        if not matched and item_code.startswith(packaging_prefix):
            pol.item_category = "Packaging"
            pol.is_service_item = False
            pol.is_stock_item = True
            pol.save(update_fields=["item_category", "is_service_item", "is_stock_item"])
            updated += 1

        if not matched and item_code.startswith(logistics_prefix):
            pol.item_category = "Logistics"
            pol.is_service_item = True
            pol.is_stock_item = False
            pol.save(update_fields=["item_category", "is_service_item", "is_stock_item"])
            updated += 1

    return updated


# ===================================================================
#  SCENARIO FUNCTIONS  (SCN-MODE-001 .. SCN-MODE-012)
# ===================================================================


def create_scn_mode_001_service_cleaning() -> Invoice:
    """
    SCN-MODE-001 - SERVICE: CLEANING CONTRACT (2-WAY)
    ──────────────────────────────────────────────────
    Vendor:  Gulf Professional Services (VND-GPS-011)
    PO:      PO-KSA-3001 - monthly cleaning service
    GRN:     None (service - no physical goods)
    Mode:    TWO_WAY (policy: POL-SVC-VENDOR, priority 10)
    Expected: MATCHED (Invoice vs PO only)
    """
    vendor = get_vendor(SERVICE_VENDOR_CODE)
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3001",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=10),
        department="Facilities",
        notes="SCN-MODE-001: Monthly cleaning service - no GRN expected",
        lines=[
            {
                "item_code": "GPS-CLN-001",
                "description": "Monthly Cleaning Service - Riyadh HQ",
                "qty": 1,
                "price": "8500.00",
                "uom": "SVC",
                "item_category": "Services",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "item_code": "GPS-CLN-002",
                "description": "Deep Cleaning - Kitchen Area",
                "qty": 2,
                "price": "3200.00",
                "uom": "SVC",
                "item_category": "Services",
                "is_service_item": True,
                "is_stock_item": False,
            },
        ],
    )
    # No GRN created

    subtotal = _d("8500.00") + _d("6400.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-001",
        vendor=vendor,
        raw_vendor_name="Gulf Professional Services Co.",
        po_number="PO-KSA-3001",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-001] Cleaning service - 2-way match expected",
        extraction_raw_json={"invoice_type": "SERVICE"},
    )
    add_line(inv, line_number=1, description="Monthly Cleaning Service - Riyadh HQ",
             quantity=_d(1), unit_price=_d("8500.00"),
             is_service_item=True, is_stock_item=False, item_category="Services")
    add_line(inv, line_number=2, description="Deep Cleaning - Kitchen Area",
             quantity=_d(2), unit_price=_d("3200.00"),
             is_service_item=True, is_stock_item=False, item_category="Services")
    return inv


def create_scn_mode_002_service_pest_control() -> Invoice:
    """
    SCN-MODE-002 - SERVICE: PEST CONTROL (2-WAY, KEYWORD HEURISTIC)
    ───────────────────────────────────────────────────────────────
    Vendor:  Gulf Professional Services (VND-GPS-011)
    PO:      PO-KSA-3002 - pest control quarterly
    GRN:     None
    Mode:    TWO_WAY (heuristic: 'pest control' matches service keywords)
    Expected: MATCHED
    """
    vendor = get_vendor(SERVICE_VENDOR_CODE)
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3002",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=15),
        department="Facilities",
        notes="SCN-MODE-002: Quarterly pest control - heuristic 2-way",
        lines=[
            {
                "item_code": "GPS-PST-001",
                "description": "Pest Control Service - Quarterly Treatment",
                "qty": 1,
                "price": "4500.00",
                "uom": "SVC",
                "item_category": "Services",
                "is_service_item": True,
                "is_stock_item": False,
            },
        ],
    )

    subtotal = _d("4500.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-002",
        vendor=vendor,
        raw_vendor_name="Gulf Pro Services",  # alias
        po_number="PO-KSA-3002",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-002] Pest control - 2-way via keyword heuristic",
    )
    add_line(inv, line_number=1, description="Pest Control Service - Quarterly Treatment",
             quantity=_d(1), unit_price=_d("4500.00"),
             is_service_item=True, is_stock_item=False, item_category="Services")
    return inv


def create_scn_mode_003_stock_food_perfect() -> Invoice:
    """
    SCN-MODE-003 - STOCK: FOOD SUPPLY PERFECT 3-WAY MATCH
    ─────────────────────────────────────────────────────
    Vendor:  Arabian Food Supplies (VND-AFS-001)
    PO:      PO-KSA-3003 - burger buns for warehouse
    GRN:     Full receipt matching PO
    Mode:    THREE_WAY (policy: POL-STOCK-GLOBAL, priority 30)
    Expected: MATCHED (Invoice vs PO vs GRN)
    """
    vendor = get_vendor("VND-AFS-001")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3003",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=12),
        department="Procurement",
        notes="SCN-MODE-003: Buns for warehouse - 3-way perfect match",
        lines=[
            {
                "item_code": "AFS-BUN-001",
                "description": "Sesame Burger Bun 4 inch",
                "qty": 400,
                "price": "45.00",
                "uom": "CTN",
                "item_category": "Food",
                "is_service_item": False,
                "is_stock_item": True,
            },
            {
                "item_code": "AFS-LET-001",
                "description": "Shredded Lettuce Food Service Pack",
                "qty": 150,
                "price": "28.00",
                "uom": "PKT",
                "item_category": "Food",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    create_grn_with_lines(
        grn_number="GRN-MODE-3003",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=3),
        po_lines=po_lines,
        warehouse="WH-RUH-01",
    )

    subtotal = _line_amt(400, "45.00") + _line_amt(150, "28.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-003",
        vendor=vendor,
        raw_vendor_name="Arabian Food Supplies Co.",
        po_number="PO-KSA-3003",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-003] Food stock - 3-way perfect match",
    )
    add_line(inv, line_number=1, description="Sesame Burger Bun 4 inch",
             quantity=_d(400), unit_price=_d("45.00"),
             is_service_item=False, is_stock_item=True, item_category="Food")
    add_line(inv, line_number=2, description="Shredded Lettuce Food Service Pack",
             quantity=_d(150), unit_price=_d("28.00"),
             is_service_item=False, is_stock_item=True, item_category="Food")
    return inv


def create_scn_mode_004_stock_frozen_partial() -> Invoice:
    """
    SCN-MODE-004 - STOCK: FROZEN FOOD, GRN SHORTAGE (3-WAY)
    ──────────────────────────────────────────────────────
    Vendor:  Gulf Frozen Foods (VND-GFF-002)
    PO:      PO-KSA-3004 - beef patties + nuggets
    GRN:     Partial receipt (patties 280/300, nuggets 190/200)
    Mode:    THREE_WAY (policy: POL-FOOD-3WAY, priority 40)
    Expected: PARTIAL_MATCH (RECEIPT_SHORTAGE)
    """
    vendor = get_vendor("VND-GFF-002")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3004",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=14),
        department="Procurement",
        notes="SCN-MODE-004: Frozen food - partial GRN receipt",
        lines=[
            {
                "item_code": "GFF-BPT-001",
                "description": "Beef Patty 150g Premium Frozen",
                "qty": 300,
                "price": "185.00",
                "uom": "CTN",
                "item_category": "Food",
                "is_service_item": False,
                "is_stock_item": True,
            },
            {
                "item_code": "GFF-NUG-001",
                "description": "Chicken Nuggets 6pc Frozen Pack",
                "qty": 200,
                "price": "95.00",
                "uom": "CTN",
                "item_category": "Food",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    create_grn_with_lines(
        grn_number="GRN-MODE-3004",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=2),
        po_lines=po_lines,
        qty_overrides={1: _d(280), 2: _d(190)},  # shortage
        warehouse="WH-DMM-01",
    )

    # Invoice claims full PO qty
    subtotal = _line_amt(300, "185.00") + _line_amt(200, "95.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-004",
        vendor=vendor,
        raw_vendor_name="Gulf Frozen Foods Trading",
        po_number="PO-KSA-3004",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-004] Frozen stock - GRN shortage -> PARTIAL_MATCH",
    )
    add_line(inv, line_number=1, description="Beef Patty 150g Premium Frozen",
             quantity=_d(300), unit_price=_d("185.00"),
             is_service_item=False, is_stock_item=True, item_category="Food")
    add_line(inv, line_number=2, description="Chicken Nuggets 6pc Frozen Pack",
             quantity=_d(200), unit_price=_d("95.00"),
             is_service_item=False, is_stock_item=True, item_category="Food")
    return inv


def create_scn_mode_005_service_price_mismatch() -> Invoice:
    """
    SCN-MODE-005 - SERVICE: SECURITY, PRICE MISMATCH (2-WAY)
    ──────────────────────────────────────────────────────────
    Vendor:  Gulf Professional Services (VND-GPS-011)
    PO:      PO-KSA-3005 - security guard contract
    GRN:     None
    Mode:    TWO_WAY (policy: POL-SVC-VENDOR, priority 10)
    Expected: PARTIAL_MATCH (PRICE_MISMATCH - invoice 12000 vs PO 11500)
    """
    vendor = get_vendor(SERVICE_VENDOR_CODE)
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3005",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=20),
        department="Facilities",
        notes="SCN-MODE-005: Security services - 2-way price mismatch",
        lines=[
            {
                "item_code": "GPS-SEC-001",
                "description": "Security Guard Service - Monthly",
                "qty": 1,
                "price": "11500.00",
                "uom": "SVC",
                "item_category": "Services",
                "is_service_item": True,
                "is_stock_item": False,
            },
        ],
    )

    # Invoice has higher price than PO
    subtotal = _d("12000.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-005",
        vendor=vendor,
        raw_vendor_name="Gulf Professional Services Co.",
        po_number="PO-KSA-3005",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-005] Security service - 2-way price mismatch (12000 vs 11500)",
        extraction_raw_json={"invoice_type": "SERVICE"},
    )
    add_line(inv, line_number=1, description="Security Guard Service - Monthly",
             quantity=_d(1), unit_price=_d("12000.00"),
             is_service_item=True, is_stock_item=False, item_category="Services")
    return inv


def create_scn_mode_006_stock_missing_grn() -> Invoice:
    """
    SCN-MODE-006 - STOCK: PACKAGING, MISSING GRN (3-WAY)
    ─────────────────────────────────────────────────────
    Vendor:  Saudi Packaging Solutions (VND-SPS-004)
    PO:      PO-KSA-3006 - cups & lids
    GRN:     Created but for WRONG PO line (simulates missing receipt)
    Mode:    THREE_WAY (policy: POL-STOCK-GLOBAL, priority 30)
    Expected: UNMATCHED (GRN_NOT_FOUND / mismatch)
    """
    vendor = get_vendor("VND-SPS-004")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3006",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=8),
        department="Procurement",
        notes="SCN-MODE-006: Packaging with GRN but only partial items received",
        lines=[
            {
                "item_code": "SPS-CUP-001",
                "description": "Paper Cup 12oz – McD Branded",
                "qty": 5000,
                "price": "0.85",
                "uom": "PCS",
                "item_category": "Packaging",
                "is_service_item": False,
                "is_stock_item": True,
            },
            {
                "item_code": "SPS-LID-001",
                "description": "Cup Lid 12oz Dome",
                "qty": 5000,
                "price": "0.42",
                "uom": "PCS",
                "item_category": "Packaging",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    # GRN only received cups, not lids
    create_grn_with_lines(
        grn_number="GRN-MODE-3006",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=1),
        po_lines=[po_lines[0]],  # only first line
        warehouse="WH-JED-01",
    )

    subtotal = _line_amt(5000, "0.85") + _line_amt(5000, "0.42")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-006",
        vendor=vendor,
        raw_vendor_name="Saudi Packaging Solutions",
        po_number="PO-KSA-3006",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-006] Packaging - GRN missing lids -> RECEIPT_SHORTAGE",
    )
    add_line(inv, line_number=1, description="Paper Cup 12oz – McD Branded",
             quantity=_d(5000), unit_price=_d("0.85"),
             is_service_item=False, is_stock_item=True, item_category="Packaging")
    add_line(inv, line_number=2, description="Cup Lid 12oz Dome",
             quantity=_d(5000), unit_price=_d("0.42"),
             is_service_item=False, is_stock_item=True, item_category="Packaging")
    return inv


def create_scn_mode_007_mixed_service_stock() -> Invoice:
    """
    SCN-MODE-007 - MIXED: SERVICE + STOCK LINES ON SAME INVOICE
    ────────────────────────────────────────────────────────────
    Vendor:  Desert Cold Chain Logistics (VND-DCCL-006)
    PO:      PO-KSA-3007 - transport service + cold-storage supplies
    GRN:     For stock lines only
    Mode:    THREE_WAY (default fallback - mixed items are ambiguous,
             resolver can't determine majority -> falls back to config default)
    Expected: PARTIAL_MATCH (mixed classifications)
    """
    vendor = get_vendor("VND-DCCL-006")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3007",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=11),
        department="Logistics",
        notes="SCN-MODE-007: Mixed service + stock - ambiguous -> default fallback",
        lines=[
            {
                "item_code": "DCCL-TRN-001",
                "description": "Refrigerated Transport - Riyadh to Jeddah",
                "qty": 4,
                "price": "2800.00",
                "uom": "SVC",
                "item_category": "Logistics",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "item_code": "DCCL-ICE-001",
                "description": "Dry Ice Packs for Cold Storage",
                "qty": 100,
                "price": "15.00",
                "uom": "PKT",
                "item_category": "Consumables",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    # GRN for stock line only (dry ice)
    create_grn_with_lines(
        grn_number="GRN-MODE-3007",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=2),
        po_lines=[po_lines[1]],  # only stock line
        warehouse="WH-RUH-01",
    )

    subtotal = _line_amt(4, "2800.00") + _line_amt(100, "15.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-007",
        vendor=vendor,
        raw_vendor_name="Desert Cold Chain Logistics",
        po_number="PO-KSA-3007",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-007] Mixed service+stock - ambiguous -> 3-way default fallback",
    )
    add_line(inv, line_number=1, description="Refrigerated Transport - Riyadh to Jeddah",
             quantity=_d(4), unit_price=_d("2800.00"),
             is_service_item=True, is_stock_item=False, item_category="Logistics")
    add_line(inv, line_number=2, description="Dry Ice Packs for Cold Storage",
             quantity=_d(100), unit_price=_d("15.00"),
             is_service_item=False, is_stock_item=True, item_category="Consumables")
    return inv


def create_scn_mode_008_stock_qty_mismatch() -> Invoice:
    """
    SCN-MODE-008 - STOCK: BEVERAGE, QTY MISMATCH (3-WAY)
    ─────────────────────────────────────────────────────
    Vendor:  Riyadh Beverage Concentrates (VND-RBC-005)
    PO:      PO-KSA-3008 - syrup concentrates
    GRN:     Full receipt matching PO
    Mode:    THREE_WAY (policy: POL-STOCK-GLOBAL or heuristic)
    Expected: PARTIAL_MATCH (QTY_MISMATCH - invoice 110 vs PO 100)
    """
    vendor = get_vendor("VND-RBC-005")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3008",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=9),
        department="Procurement",
        notes="SCN-MODE-008: Beverages - 3-way qty mismatch",
        lines=[
            {
                "item_code": "RBC-SYR-001",
                "description": "Cola Syrup Concentrate 20L",
                "qty": 100,
                "price": "220.00",
                "uom": "DRM",
                "item_category": "Beverage",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    create_grn_with_lines(
        grn_number="GRN-MODE-3008",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=1),
        po_lines=po_lines,
        warehouse="WH-RUH-01",
    )

    # Invoice claims 110 qty vs PO 100
    subtotal = _line_amt(110, "220.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-008",
        vendor=vendor,
        raw_vendor_name="Riyadh Beverage Concentrates Co.",
        po_number="PO-KSA-3008",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-008] Beverage - 3-way qty mismatch (110 vs PO 100)",
    )
    add_line(inv, line_number=1, description="Cola Syrup Concentrate 20L",
             quantity=_d(110), unit_price=_d("220.00"),
             is_service_item=False, is_stock_item=True, item_category="Beverage")
    return inv


def create_scn_mode_009_service_heuristic_keyword() -> Invoice:
    """
    SCN-MODE-009 - SERVICE: MAINTENANCE (2-WAY, KEYWORD HEURISTIC)
    ──────────────────────────────────────────────────────────────
    Vendor:  Jeddah Quick Service Supplies (VND-JQSS-010)
    PO:      PO-KSA-3009 - equipment maintenance contract
    GRN:     None
    Mode:    TWO_WAY (heuristic: 'maintenance' keyword)
    Expected: MATCHED
    Note:    This tests keyword-based heuristic resolution (not policy).
             Vendor is NOT the service vendor and has no explicit policy.
             Line items flagged as service + descriptions contain "maintenance".
    """
    vendor = get_vendor("VND-JQSS-010")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3009",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=7),
        department="Maintenance",
        notes="SCN-MODE-009: Equipment maintenance - heuristic keyword resolution",
        lines=[
            {
                "item_code": "JQSS-MNT-001",
                "description": "Kitchen Equipment Maintenance Service Q1",
                "qty": 1,
                "price": "6500.00",
                "uom": "SVC",
                "item_category": "Services",
                "is_service_item": True,
                "is_stock_item": False,
            },
            {
                "item_code": "JQSS-MNT-002",
                "description": "Walk-in Freezer Maintenance Service",
                "qty": 1,
                "price": "4200.00",
                "uom": "SVC",
                "item_category": "Services",
                "is_service_item": True,
                "is_stock_item": False,
            },
        ],
    )

    subtotal = _d("6500.00") + _d("4200.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-009",
        vendor=vendor,
        raw_vendor_name="Jeddah Quick Service Supplies",
        po_number="PO-KSA-3009",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-009] Maintenance - 2-way via keyword heuristic",
    )
    add_line(inv, line_number=1, description="Kitchen Equipment Maintenance Service Q1",
             quantity=_d(1), unit_price=_d("6500.00"),
             is_service_item=True, is_stock_item=False, item_category="Services")
    add_line(inv, line_number=2, description="Walk-in Freezer Maintenance Service",
             quantity=_d(1), unit_price=_d("4200.00"),
             is_service_item=True, is_stock_item=False, item_category="Services")
    return inv


def create_scn_mode_010_stock_location_policy() -> Invoice:
    """
    SCN-MODE-010 - STOCK: DAIRY, LOCATION-BASED POLICY (3-WAY)
    ────────────────────────────────────────────────────────────
    Vendor:  Al Khobar Dairy (VND-AKD-009)
    PO:      PO-KSA-3010 - cheese slices for warehouse
    GRN:     Full receipt
    Mode:    THREE_WAY (policy: POL-WH-RUH-3WAY, priority 60 - department=WH-RUH-01)
    Expected: MATCHED
    """
    vendor = get_vendor("VND-AKD-009")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3010",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=6),
        department="WH-RUH-01",  # location-based policy match
        notes="SCN-MODE-010: Dairy for warehouse - location policy -> 3-way",
        lines=[
            {
                "item_code": "AKD-CHZ-001",
                "description": "Processed Cheese Slices 200pc",
                "qty": 350,
                "price": "62.00",
                "uom": "CTN",
                "item_category": "Food",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    create_grn_with_lines(
        grn_number="GRN-MODE-3010",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=1),
        po_lines=po_lines,
        warehouse="WH-RUH-01",
    )

    subtotal = _line_amt(350, "62.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-010",
        vendor=vendor,
        raw_vendor_name="Al Khobar Dairy Ingredients",
        po_number="PO-KSA-3010",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-010] Dairy - 3-way via location policy (WH-RUH-01)",
    )
    add_line(inv, line_number=1, description="Processed Cheese Slices 200pc",
             quantity=_d(350), unit_price=_d("62.00"),
             is_service_item=False, is_stock_item=True, item_category="Food")
    return inv


def create_scn_mode_011_branch_direct_purchase() -> Invoice:
    """
    SCN-MODE-011 - BRANCH: DIRECT PURCHASE (2-WAY, BUSINESS_UNIT POLICY)
    ─────────────────────────────────────────────────────────────────────
    Vendor:  Red Sea Restaurant Consumables (VND-RSRC-007)
    PO:      PO-KSA-3011 - small branch supplies (napkins, etc.)
    GRN:     Created but branch operations -> 2-way policy applies
    Mode:    TWO_WAY (policy: POL-BRANCH-2WAY, priority 70 - business_unit match)
    Expected: MATCHED (GRN ignored in 2-way mode)
    """
    vendor = get_vendor("VND-RSRC-007")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3011",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=5),
        department="Branch Operations",  # triggers POL-BRANCH-2WAY
        notes="SCN-MODE-011: Branch supplies - 2-way via business unit policy",
        lines=[
            {
                "item_code": "RSRC-NAP-001",
                "description": "Paper Napkins Branded 500ct",
                "qty": 200,
                "price": "18.00",
                "uom": "PKT",
                "item_category": "Consumables",
                "is_service_item": False,
                "is_stock_item": True,
            },
            {
                "item_code": "RSRC-STR-001",
                "description": "Drinking Straw Biodegradable 1000ct",
                "qty": 100,
                "price": "25.00",
                "uom": "BOX",
                "item_category": "Consumables",
                "is_service_item": False,
                "is_stock_item": True,
            },
        ],
    )
    # GRN exists but should be irrelevant in 2-way mode
    create_grn_with_lines(
        grn_number="GRN-MODE-3011",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=1),
        po_lines=po_lines,
        warehouse="BR-RUH-101",
    )

    subtotal = _line_amt(200, "18.00") + _line_amt(100, "25.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-011",
        vendor=vendor,
        raw_vendor_name="Red Sea Restaurant Consumables",
        po_number="PO-KSA-3011",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-011] Branch supplies - 2-way (GRN exists but ignored)",
    )
    add_line(inv, line_number=1, description="Paper Napkins Branded 500ct",
             quantity=_d(200), unit_price=_d("18.00"),
             is_service_item=False, is_stock_item=True, item_category="Consumables")
    add_line(inv, line_number=2, description="Drinking Straw Biodegradable 1000ct",
             quantity=_d(100), unit_price=_d("25.00"),
             is_service_item=False, is_stock_item=True, item_category="Consumables")
    return inv


def create_scn_mode_012_default_fallback() -> Invoice:
    """
    SCN-MODE-012 - DEFAULT FALLBACK: NO POLICY MATCH, NO HEURISTIC
    ───────────────────────────────────────────────────────────────
    Vendor:  Najd Edible Oils (VND-NEO-008)
    PO:      PO-KSA-3012 - cooking oil (no item classification set)
    GRN:     Full receipt
    Mode:    THREE_WAY (no policy matches, no is_service/is_stock flags,
             no keyword match -> falls back to config default: THREE_WAY)
    Expected: MATCHED (Invoice vs PO vs GRN)
    Note:    Lines have NO item_category, no is_service/is_stock flags.
             This tests the "fallback to default" resolution path.
    """
    vendor = get_vendor("VND-NEO-008")
    po, po_lines = create_po_with_lines(
        po_number="PO-KSA-3012",
        vendor=vendor,
        po_date=BASE_DATE - timedelta(days=13),
        department="Procurement",
        notes="SCN-MODE-012: Unclassified items - default fallback to 3-way",
        lines=[
            {
                "item_code": "NEO-OIL-X01",
                "description": "Blend Premium Grade A 20L",
                "qty": 80,
                "price": "145.00",
                "uom": "DRM",
                # No item classification - intentionally blank
            },
            {
                "item_code": "NEO-OIL-X02",
                "description": "Blend Standard Grade B 20L",
                "qty": 50,
                "price": "120.00",
                "uom": "DRM",
                # No item classification - intentionally blank
            },
        ],
    )
    create_grn_with_lines(
        grn_number="GRN-MODE-3012",
        po=po, vendor=vendor,
        receipt_date=BASE_DATE - timedelta(days=1),
        po_lines=po_lines,
        warehouse="WH-RUH-01",
    )

    subtotal = _line_amt(80, "145.00") + _line_amt(50, "120.00")
    tax = _tax(subtotal)
    inv = create_invoice(
        invoice_number="INV-MODE-012",
        vendor=vendor,
        raw_vendor_name="Najd Edible Oils Trading",
        po_number="PO-KSA-3012",
        invoice_date=INVOICE_DATE,
        subtotal=subtotal,
        tax_amount=tax,
        total_amount=subtotal + tax,
        notes="[SCN-MODE-012] Unclassified oil - default fallback -> 3-way match",
    )
    # No item classification on invoice lines either
    add_line(inv, line_number=1, description="Blend Premium Grade A 20L",
             quantity=_d(80), unit_price=_d("145.00"))
    add_line(inv, line_number=2, description="Blend Standard Grade B 20L",
             quantity=_d(50), unit_price=_d("120.00"))
    return inv


# ===================================================================
#  COMMAND
# ===================================================================

class Command(BaseCommand):
    help = (
        "Seed mixed-mode reconciliation data: policies, service vendor, "
        "2-way & 3-way POs/GRNs/invoices (SCN-MODE-001..012)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously seeded mixed-mode data before re-creating.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== Mixed-Mode Seed Data - 12 Scenarios (SCN-MODE-001..012) ===\n"
        ))

        # Pre-flight: verify master data
        vendor_count = Vendor.objects.filter(code__startswith="VND-").count()
        if vendor_count == 0:
            self.stderr.write(self.style.ERROR(
                "  ERROR: Master data not found. Run 'seed_saudi_mcd_data' first."
            ))
            return

        self.stdout.write(f"  Master data found: {vendor_count} vendors\n")

        admin = get_admin_user()

        # 1. Service vendor
        self.stdout.write("  Creating service vendor (Gulf Professional Services)...")
        svc_vendor = create_service_vendor(admin)
        self.stdout.write(self.style.SUCCESS(f"    [OK] {svc_vendor.code} - {svc_vendor.name}"))

        # 2. Reconciliation policies
        self.stdout.write("  Creating reconciliation policies...")
        pol_count = create_policies(admin, svc_vendor)
        self.stdout.write(self.style.SUCCESS(f"    [OK] {pol_count} policies created"))

        # 3. Update recon config
        self.stdout.write("  Updating reconciliation config (mode resolver enabled)...")
        config = update_recon_config()
        self.stdout.write(self.style.SUCCESS(
            f"    [OK] Config '{config.name}': default={config.default_reconciliation_mode}, "
            f"resolver={config.enable_mode_resolver}"
        ))

        # 4. Back-fill item classification on existing PO lines
        self.stdout.write("  Back-filling item classification on existing PO lines...")
        backfill_count = backfill_item_classification()
        self.stdout.write(self.style.SUCCESS(f"    [OK] {backfill_count} PO lines classified"))

        # 5. Scenarios
        results = []

        self.stdout.write("\n  --- 2-Way (Service) Scenarios ---")

        self.stdout.write("  SCN-MODE-001: Cleaning service - 2-way match...")
        inv = create_scn_mode_001_service_cleaning()
        results.append(("SCN-MODE-001", inv.invoice_number, "Cleaning service (2-way)", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-002: Pest control - 2-way keyword heuristic...")
        inv = create_scn_mode_002_service_pest_control()
        results.append(("SCN-MODE-002", inv.invoice_number, "Pest control (2-way heuristic)", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-005: Security - 2-way price mismatch...")
        inv = create_scn_mode_005_service_price_mismatch()
        results.append(("SCN-MODE-005", inv.invoice_number, "Security service price mismatch", "PARTIAL_MATCH"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-009: Maintenance - 2-way keyword heuristic...")
        inv = create_scn_mode_009_service_heuristic_keyword()
        results.append(("SCN-MODE-009", inv.invoice_number, "Maintenance (keyword heuristic)", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-011: Branch supplies - 2-way business unit...")
        inv = create_scn_mode_011_branch_direct_purchase()
        results.append(("SCN-MODE-011", inv.invoice_number, "Branch direct purchase (2-way)", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("\n  --- 3-Way (Stock) Scenarios ---")

        self.stdout.write("  SCN-MODE-003: Food supply - 3-way perfect match...")
        inv = create_scn_mode_003_stock_food_perfect()
        results.append(("SCN-MODE-003", inv.invoice_number, "Food stock 3-way match", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-004: Frozen food - 3-way GRN shortage...")
        inv = create_scn_mode_004_stock_frozen_partial()
        results.append(("SCN-MODE-004", inv.invoice_number, "Frozen food GRN shortage", "PARTIAL_MATCH"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-006: Packaging - 3-way missing GRN lines...")
        inv = create_scn_mode_006_stock_missing_grn()
        results.append(("SCN-MODE-006", inv.invoice_number, "Packaging GRN shortage", "PARTIAL_MATCH"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-008: Beverages - 3-way qty mismatch...")
        inv = create_scn_mode_008_stock_qty_mismatch()
        results.append(("SCN-MODE-008", inv.invoice_number, "Beverage qty mismatch", "PARTIAL_MATCH"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-010: Dairy - 3-way location policy match...")
        inv = create_scn_mode_010_stock_location_policy()
        results.append(("SCN-MODE-010", inv.invoice_number, "Dairy location policy match", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("\n  --- Edge-Case Scenarios ---")

        self.stdout.write("  SCN-MODE-007: Mixed service+stock - default fallback...")
        inv = create_scn_mode_007_mixed_service_stock()
        results.append(("SCN-MODE-007", inv.invoice_number, "Mixed lines -> 3-way default", "PARTIAL_MATCH"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        self.stdout.write("  SCN-MODE-012: Unclassified - default fallback 3-way...")
        inv = create_scn_mode_012_default_fallback()
        results.append(("SCN-MODE-012", inv.invoice_number, "Unclassified -> 3-way default", "MATCHED"))
        self.stdout.write(self.style.SUCCESS(f"    [OK] {inv.invoice_number}"))

        # Summary
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Summary ==="))
        self.stdout.write(f"  Service Vendor:      1 ({svc_vendor.code})")
        self.stdout.write(f"  Policies:            {pol_count}")
        self.stdout.write(f"  Back-filled PO Lines:{backfill_count}")
        self.stdout.write(f"  Invoices:            {len(results)}")
        po_count = PurchaseOrder.objects.filter(po_number__startswith="PO-KSA-30").count()
        grn_count = GoodsReceiptNote.objects.filter(grn_number__startswith="GRN-MODE-").count()
        self.stdout.write(f"  New POs:             {po_count}")
        self.stdout.write(f"  New GRNs:            {grn_count}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Scenario Map ==="))
        two_way_count = 0
        three_way_count = 0
        for scn, inv_num, desc, expected in results:
            mode = "2-Way" if scn in ("SCN-MODE-001", "SCN-MODE-002", "SCN-MODE-005",
                                       "SCN-MODE-009", "SCN-MODE-011") else "3-Way"
            if mode == "2-Way":
                two_way_count += 1
            else:
                three_way_count += 1
            self.stdout.write(f"  {scn:14s} [{mode:5s}] {inv_num:16s} -> {desc} -> expected: {expected}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Mode Distribution ==="))
        self.stdout.write(f"  2-Way scenarios: {two_way_count}")
        self.stdout.write(f"  3-Way scenarios: {three_way_count}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Resolution Methods Covered ==="))
        self.stdout.write("  [OK] Policy: vendor-specific (SCN-MODE-001, 005)")
        self.stdout.write("  [OK] Policy: global service flag (SCN-MODE-002)")
        self.stdout.write("  [OK] Policy: global stock flag (SCN-MODE-003, 006, 008)")
        self.stdout.write("  [OK] Policy: item category (SCN-MODE-004)")
        self.stdout.write("  [OK] Policy: location code (SCN-MODE-010)")
        self.stdout.write("  [OK] Policy: business unit (SCN-MODE-011)")
        self.stdout.write("  [OK] Heuristic: keyword (SCN-MODE-002, 009)")
        self.stdout.write("  [OK] Default fallback (SCN-MODE-007, 012)")

        self.stdout.write(self.style.SUCCESS(
            "\n[OK] Mixed-mode seed data complete.\n"
        ))

    def _flush(self):
        """Remove previously seeded mixed-mode data."""
        self.stdout.write(self.style.WARNING("  Flushing mixed-mode seed data..."))

        # Invoices + lines
        inv_qs = Invoice.objects.filter(invoice_number__in=SCENARIO_INVOICE_NUMBERS)
        inv_ids = list(inv_qs.values_list("id", flat=True))
        line_del = InvoiceLineItem.objects.filter(invoice_id__in=inv_ids).delete()[0]
        inv_del = inv_qs.delete()[0]
        self.stdout.write(f"    Deleted {inv_del} invoices, {line_del} invoice lines")

        # GRNs + lines
        grn_qs = GoodsReceiptNote.objects.filter(grn_number__in=GRN_NUMBERS)
        grn_ids = list(grn_qs.values_list("id", flat=True))
        grn_line_del = GRNLineItem.objects.filter(grn_id__in=grn_ids).delete()[0]
        grn_del = grn_qs.delete()[0]
        self.stdout.write(f"    Deleted {grn_del} GRNs, {grn_line_del} GRN lines")

        # POs + lines
        po_qs = PurchaseOrder.objects.filter(po_number__in=PO_NUMBERS)
        po_ids = list(po_qs.values_list("id", flat=True))
        po_line_del = PurchaseOrderLineItem.objects.filter(purchase_order_id__in=po_ids).delete()[0]
        po_del = po_qs.delete()[0]
        self.stdout.write(f"    Deleted {po_del} POs, {po_line_del} PO lines")

        # Policies
        pol_del = ReconciliationPolicy.objects.filter(policy_code__in=POLICY_CODES).delete()[0]
        self.stdout.write(f"    Deleted {pol_del} policies")

        # Service vendor + aliases
        vendor_qs = Vendor.objects.filter(code=SERVICE_VENDOR_CODE)
        if vendor_qs.exists():
            alias_del = VendorAlias.objects.filter(vendor__code=SERVICE_VENDOR_CODE).delete()[0]
            v_del = vendor_qs.delete()[0]
            self.stdout.write(f"    Deleted {v_del} vendor, {alias_del} aliases")

        self.stdout.write(self.style.SUCCESS("    [OK] Flush complete"))
