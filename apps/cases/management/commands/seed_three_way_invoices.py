"""
Management command: seed_three_way_invoices

Seeds realistic McDonald's Saudi Arabia THREE_WAY PO-backed goods/stock
invoice data for dev/demo/QA/UI testing.

This command creates ONLY:
  - Reference / master data (vendors, aliases)
  - Purchase Orders + PO line items (goods / stock oriented)
  - GRN records + GRN line items (receipt data for 3-way matching)
  - Invoices + invoice line items (with extraction metadata)
  - DocumentUpload stubs
  - ExtractionResult stubs

It does NOT create:
  - ReconciliationRun / ReconciliationResult / ReconciliationException
  - APCase / APCaseStage / APCaseArtifact / APCaseDecision
  - AgentRun / AgentMessage / AgentRecommendation
  - ReviewAssignment / ReviewDecision
  - AuditEvent

The seeded invoices are intended to be reconciliation-ready: trigger matching
later from the invoice detail page or via `create_cases_for_existing_invoices`.

Modes:
  --mode=demo   → 20 deterministic scenarios (default)
  --mode=qa     → 20 deterministic + 15 generated
  --mode=large  → 20 deterministic + 40 generated

Usage:
    python manage.py seed_three_way_invoices
    python manage.py seed_three_way_invoices --mode=qa
    python manage.py seed_three_way_invoices --mode=large
    python manage.py seed_three_way_invoices --reset
    python manage.py seed_three_way_invoices --seed=42
    python manage.py seed_three_way_invoices --summary

Prerequisites:
    Run `python manage.py seed_config` first to create users, agent definitions,
    tool definitions, and reconciliation config/policies.
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deterministic RNG — reset per invocation
# ---------------------------------------------------------------------------
_rng = random.Random(42)
VAT_RATE = Decimal("0.15")

# Prefix for all seeded records — makes reset safe
PREFIX = "3W"


# ============================================================================
# Constants — THREE_WAY goods / stock vendors
# ============================================================================

THREE_WAY_VENDORS = [
    {
        "code": "V3W-001",
        "name": "Americana Foods Company",
        "category": "Frozen Foods & Proteins",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320100100100003",
        "payment_terms": "Net 30",
        "contact_email": "supply@americana-foods.com.sa",
        "aliases": [
            "أمريكانا للأغذية",
            "Americana Foods",
            "Americana Group KSA",
        ],
    },
    {
        "code": "V3W-002",
        "name": "SADAFCO (Saudia Dairy & Foodstuff Co.)",
        "category": "Beverages & Dairy",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320200200200003",
        "payment_terms": "Net 30",
        "contact_email": "orders@sadafco.com",
        "aliases": [
            "سدافكو",
            "SADAFCO",
            "Saudia Dairy",
            "Saudi Dairy & Foodstuff",
        ],
    },
    {
        "code": "V3W-003",
        "name": "Al Marai Company",
        "category": "Bakery & Buns",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320300300300003",
        "payment_terms": "Net 30",
        "contact_email": "b2b@almarai.com",
        "aliases": [
            "المراعي",
            "Almarai",
            "Al-Marai Co.",
        ],
    },
    {
        "code": "V3W-004",
        "name": "Gulf Packaging Industries",
        "category": "Packaging Materials",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320400400400003",
        "payment_terms": "Net 45",
        "contact_email": "sales@gulfpackaging.com.sa",
        "aliases": [
            "صناعات التغليف الخليجية",
            "Gulf Pack",
            "GPI Saudi",
        ],
    },
    {
        "code": "V3W-005",
        "name": "Diversey Arabia LLC",
        "category": "Cleaning Chemicals",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320500500500003",
        "payment_terms": "Net 30",
        "contact_email": "ksa.orders@diversey.com",
        "aliases": [
            "ديفرسي العربية",
            "Diversey KSA",
            "Diversey Hygiene",
        ],
    },
    {
        "code": "V3W-006",
        "name": "Binzagr Coca-Cola Saudi",
        "category": "Beverages & Dry Goods",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320600600600003",
        "payment_terms": "Net 30",
        "contact_email": "distribution@binzagr-coke.com.sa",
        "aliases": [
            "بن زقر كوكاكولا",
            "Binzagr CocaCola",
            "Binzagr Beverages",
        ],
    },
    {
        "code": "V3W-007",
        "name": "Red Sea Uniforms & Workwear",
        "category": "Uniforms & Housekeeping Stock",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320700700700003",
        "payment_terms": "Net 60",
        "contact_email": "corporate@redsea-uniforms.com.sa",
        "aliases": [
            "يونيفورم البحر الأحمر",
            "Red Sea Uniforms",
            "RS Workwear",
        ],
    },
    {
        "code": "V3W-008",
        "name": "Henny Penny Parts Arabia",
        "category": "Spare Parts & Equipment",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320800800800003",
        "payment_terms": "Net 45",
        "contact_email": "parts@hennypenny-arabia.com",
        "aliases": [
            "هيني بيني قطع غيار",
            "HP Parts Arabia",
            "Henny Penny Equipment KSA",
        ],
    },
    {
        "code": "V3W-009",
        "name": "Frozen Express Cold Chain Co.",
        "category": "Frozen Goods & Cold Chain",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "320900900900003",
        "payment_terms": "Net 30",
        "contact_email": "logistics@frozenexpress.com.sa",
        "aliases": [
            "فروزن اكسبرس للتبريد",
            "Frozen Express",
            "FE Cold Chain",
        ],
    },
    {
        "code": "V3W-010",
        "name": "Arabian Paper Products Co.",
        "category": "Paper & Takeaway Packaging",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "321000100100003",
        "payment_terms": "Net 30",
        "contact_email": "orders@arabianpaper.com.sa",
        "aliases": [
            "الشركة العربية للمنتجات الورقية",
            "Arabian Paper",
            "APP Saudi",
        ],
    },
]

# ---------------------------------------------------------------------------
# Stock / goods line items catalog (THREE_WAY categories)
# ---------------------------------------------------------------------------

STOCK_ITEMS_CATALOG: dict[str, list[dict[str, Any]]] = {
    "Frozen Foods & Proteins": [
        {"desc": "Frozen French Fries (9mm cut) – 10kg carton", "uom": "CTN", "price": 85.00, "code": "FRZ-001"},
        {"desc": "Chicken Patties (Regular) – 5kg box", "uom": "BOX", "price": 125.00, "code": "FRZ-002"},
        {"desc": "Beef Burger Patties (Quarter Pounder) – 5kg", "uom": "BOX", "price": 165.00, "code": "FRZ-003"},
        {"desc": "Chicken McNuggets (Frozen) – 3kg bag", "uom": "BAG", "price": 95.00, "code": "FRZ-004"},
        {"desc": "Fish Fillet Portions (Breaded) – 4kg", "uom": "BOX", "price": 140.00, "code": "FRZ-005"},
        {"desc": "Hash Browns (Frozen) – 6kg carton", "uom": "CTN", "price": 72.00, "code": "FRZ-006"},
    ],
    "Beverages & Dairy": [
        {"desc": "Coca-Cola Syrup BIB – 20L", "uom": "BIB", "price": 280.00, "code": "BEV-001"},
        {"desc": "Sprite Syrup BIB – 20L", "uom": "BIB", "price": 260.00, "code": "BEV-002"},
        {"desc": "Orange Juice Concentrate – 10L", "uom": "CTN", "price": 145.00, "code": "BEV-003"},
        {"desc": "Fresh Milk (Full Fat) – 12×1L pack", "uom": "PKG", "price": 58.00, "code": "BEV-004"},
        {"desc": "Ice Cream Mix (Vanilla) – 10L", "uom": "CTN", "price": 110.00, "code": "BEV-005"},
        {"desc": "Coffee Beans (Arabica Blend) – 5kg bag", "uom": "BAG", "price": 195.00, "code": "BEV-006"},
    ],
    "Bakery & Buns": [
        {"desc": "Sesame Seed Burger Buns – 48 pack", "uom": "PKG", "price": 42.00, "code": "BAK-001"},
        {"desc": "Big Mac Buns (3-piece) – 36 pack", "uom": "PKG", "price": 48.00, "code": "BAK-002"},
        {"desc": "English Muffins (Breakfast) – 60 pack", "uom": "PKG", "price": 38.00, "code": "BAK-003"},
        {"desc": "Tortilla Wraps (Large) – 80 pack", "uom": "PKG", "price": 55.00, "code": "BAK-004"},
    ],
    "Packaging Materials": [
        {"desc": "Paper Cups 16oz (Medium) – 1000 pcs", "uom": "CTN", "price": 180.00, "code": "PKG-001"},
        {"desc": "Paper Cups 22oz (Large) – 800 pcs", "uom": "CTN", "price": 195.00, "code": "PKG-002"},
        {"desc": "Cup Lids (Dome) – 1000 pcs", "uom": "CTN", "price": 95.00, "code": "PKG-003"},
        {"desc": "Burger Clamshell Box – 500 pcs", "uom": "CTN", "price": 145.00, "code": "PKG-004"},
        {"desc": "French Fry Cartons (Medium) – 2000 pcs", "uom": "CTN", "price": 120.00, "code": "PKG-005"},
        {"desc": "Brown Paper Bags (Takeaway) – 1000 pcs", "uom": "CTN", "price": 88.00, "code": "PKG-006"},
        {"desc": "McNugget Boxes (6-piece) – 1500 pcs", "uom": "CTN", "price": 110.00, "code": "PKG-007"},
    ],
    "Cleaning Chemicals": [
        {"desc": "All-Purpose Kitchen Degreaser – 20L", "uom": "DRUM", "price": 320.00, "code": "CLN-001"},
        {"desc": "Sanitizer Solution (Food-Grade) – 10L", "uom": "CTN", "price": 185.00, "code": "CLN-002"},
        {"desc": "Floor Cleaning Chemical – 25L drum", "uom": "DRUM", "price": 275.00, "code": "CLN-003"},
        {"desc": "Handwash Soap (Antibacterial) – 5L refill", "uom": "CTN", "price": 95.00, "code": "CLN-004"},
        {"desc": "Glass Cleaner Concentrate – 10L", "uom": "CTN", "price": 145.00, "code": "CLN-005"},
    ],
    "Beverages & Dry Goods": [
        {"desc": "Coca-Cola Syrup BIB – 20L", "uom": "BIB", "price": 280.00, "code": "DRY-001"},
        {"desc": "Fanta Orange Syrup BIB – 20L", "uom": "BIB", "price": 255.00, "code": "DRY-002"},
        {"desc": "Ketchup Sachets (Bulk) – 500 pcs", "uom": "CTN", "price": 135.00, "code": "DRY-003"},
        {"desc": "Mayonnaise Sachets (Bulk) – 500 pcs", "uom": "CTN", "price": 145.00, "code": "DRY-004"},
        {"desc": "Salt Sachets – 2000 pcs", "uom": "CTN", "price": 42.00, "code": "DRY-005"},
        {"desc": "Sugar Sachets – 2000 pcs", "uom": "CTN", "price": 48.00, "code": "DRY-006"},
    ],
    "Uniforms & Housekeeping Stock": [
        {"desc": "Crew Polo Shirts (Logo) – Size M", "uom": "EA", "price": 85.00, "code": "UNI-001"},
        {"desc": "Crew Polo Shirts (Logo) – Size L", "uom": "EA", "price": 85.00, "code": "UNI-002"},
        {"desc": "Kitchen Aprons (Heavy Duty) – pack of 10", "uom": "PKG", "price": 180.00, "code": "UNI-003"},
        {"desc": "Non-Slip Safety Shoes – Size 42", "uom": "PR", "price": 145.00, "code": "UNI-004"},
        {"desc": "Disposable Gloves (L) – 1000 pcs", "uom": "CTN", "price": 65.00, "code": "UNI-005"},
        {"desc": "Hair Nets (Disposable) – 500 pcs", "uom": "CTN", "price": 35.00, "code": "UNI-006"},
        {"desc": "Manager Uniforms Set – Size L", "uom": "SET", "price": 220.00, "code": "UNI-007"},
    ],
    "Spare Parts & Equipment": [
        {"desc": "Fryer Heating Element – Model FP-900", "uom": "EA", "price": 1450.00, "code": "SPR-001"},
        {"desc": "Grill Plate Assembly – Model GR-400", "uom": "EA", "price": 2200.00, "code": "SPR-002"},
        {"desc": "Ice Machine Compressor – Model IM-250", "uom": "EA", "price": 3800.00, "code": "SPR-003"},
        {"desc": "Drive-Thru Headset Replacement", "uom": "EA", "price": 680.00, "code": "SPR-004"},
        {"desc": "POS Terminal Thermal Printer Head", "uom": "EA", "price": 420.00, "code": "SPR-005"},
        {"desc": "Refrigeration Door Gasket Set", "uom": "SET", "price": 350.00, "code": "SPR-006"},
    ],
    "Frozen Goods & Cold Chain": [
        {"desc": "Frozen Apple Pies – 4kg carton", "uom": "CTN", "price": 115.00, "code": "CC-001"},
        {"desc": "Frozen McFlurry Toppings (Oreo) – 3kg", "uom": "CTN", "price": 98.00, "code": "CC-002"},
        {"desc": "Frozen Onion Rings – 5kg", "uom": "CTN", "price": 88.00, "code": "CC-003"},
        {"desc": "Frozen Mozzarella Sticks – 4kg", "uom": "CTN", "price": 125.00, "code": "CC-004"},
        {"desc": "Frozen Cheese Slices (Cheddar) – 500 pcs", "uom": "CTN", "price": 210.00, "code": "CC-005"},
        {"desc": "Frozen Lettuce Shred (Pre-Cut) – 5kg bag", "uom": "BAG", "price": 65.00, "code": "CC-006"},
    ],
    "Paper & Takeaway Packaging": [
        {"desc": "Happy Meal Boxes (Printed) – 1000 pcs", "uom": "CTN", "price": 220.00, "code": "PP-001"},
        {"desc": "Napkins (Logo Print) – 5000 pcs", "uom": "CTN", "price": 78.00, "code": "PP-002"},
        {"desc": "Tray Liners (Promotional) – 3000 pcs", "uom": "CTN", "price": 95.00, "code": "PP-003"},
        {"desc": "Delivery Bags (Insulated) – 200 pcs", "uom": "CTN", "price": 360.00, "code": "PP-004"},
        {"desc": "Straw Wrapping Paper – 10000 pcs", "uom": "CTN", "price": 55.00, "code": "PP-005"},
        {"desc": "Sauce Cups (Small) with Lids – 2000 pcs", "uom": "CTN", "price": 125.00, "code": "PP-006"},
    ],
}

# Branches / warehouses / cost centers
BRANCHES = [
    {"code": "BR-RUH-001", "name": "McDonald's Olaya Street", "city": "Riyadh", "warehouse": "WH-RUH-CENTRAL"},
    {"code": "BR-RUH-002", "name": "McDonald's King Fahd Road", "city": "Riyadh", "warehouse": "WH-RUH-CENTRAL"},
    {"code": "BR-RUH-003", "name": "McDonald's Exit 15 DT", "city": "Riyadh", "warehouse": "WH-RUH-SOUTH"},
    {"code": "BR-JED-001", "name": "McDonald's Tahlia Street", "city": "Jeddah", "warehouse": "WH-JED-MAIN"},
    {"code": "BR-JED-002", "name": "McDonald's Corniche", "city": "Jeddah", "warehouse": "WH-JED-MAIN"},
    {"code": "BR-DMM-001", "name": "McDonald's King Saud Street", "city": "Dammam", "warehouse": "WH-DMM-DC"},
    {"code": "BR-DMM-002", "name": "McDonald's Dhahran Mall", "city": "Dammam", "warehouse": "WH-DMM-DC"},
]

WAREHOUSES = {
    "WH-RUH-CENTRAL": "Riyadh Central Distribution Center",
    "WH-RUH-SOUTH": "Riyadh South Warehouse",
    "WH-JED-MAIN": "Jeddah Main Distribution Center",
    "WH-DMM-DC": "Dammam Distribution Center",
}

COST_CENTERS = [
    "CC-1010",  # Food & Beverage
    "CC-1020",  # Packaging & Supplies
    "CC-1030",  # Cleaning & Hygiene
    "CC-2010",  # Store Operations
    "CC-4010",  # Equipment & Maintenance
]


# ============================================================================
# GRN behaviour configuration — controls what GRN data is created per scenario
# to produce specific 3-way matching outcomes when reconciliation later runs.
# ============================================================================

class GRNBehaviour:
    """Enum-like class describing how GRN data should be seeded per scenario."""
    FULL_RECEIPT = "full_receipt"              # GRN qty == PO qty (match)
    NO_GRN = "no_grn"                         # No GRN created at all
    PARTIAL_RECEIPT = "partial_receipt"        # GRN qty < PO qty (shortage)
    OVER_RECEIPT = "over_receipt"              # GRN qty > PO qty
    MULTI_GRN = "multi_grn"                   # Two partial GRNs that together cover PO
    DELAYED_RECEIPT = "delayed_receipt"        # GRN exists but receipt_date is very late
    LOCATION_MISMATCH = "location_mismatch"   # GRN warehouse differs from invoice


# ============================================================================
# 20 deterministic THREE_WAY invoice scenarios
# ============================================================================

SCENARIOS: list[dict[str, Any]] = [
    # ── A. Likely matched THREE_WAY invoices (5) ──────────────────────
    {
        "num": 1,
        "tag": "3W-FRIES-PERFECT",
        "vendor_code": "V3W-001",
        "branch": "BR-RUH-001",
        "category": "Frozen Foods & Proteins",
        "description": "Frozen fries stock replenishment – Riyadh warehouse",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.96,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Perfect 3-way match: PO ↔ Invoice ↔ GRN all aligned",
    },
    {
        "num": 2,
        "tag": "3W-PACKAGING-PERFECT",
        "vendor_code": "V3W-004",
        "branch": "BR-JED-001",
        "category": "Packaging Materials",
        "description": "Burger packaging materials invoice – Jeddah DC",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.94,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1020",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Perfect match – packaging stock order",
    },
    {
        "num": 3,
        "tag": "3W-BEVERAGE-PERFECT",
        "vendor_code": "V3W-006",
        "branch": "BR-DMM-001",
        "category": "Beverages & Dry Goods",
        "description": "Beverages stock replenishment – Dammam DC",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.95,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Perfect match – syrups and condiments",
    },
    {
        "num": 4,
        "tag": "3W-CLEANING-PERFECT",
        "vendor_code": "V3W-005",
        "branch": "BR-RUH-002",
        "category": "Cleaning Chemicals",
        "description": "Cleaning chemicals bulk supply – Riyadh cluster",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.93,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1030",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Perfect match – hygiene products",
    },
    {
        "num": 5,
        "tag": "3W-CUPS-PERFECT",
        "vendor_code": "V3W-010",
        "branch": "BR-JED-002",
        "category": "Paper & Takeaway Packaging",
        "description": "Paper cups and lids stock supply – Jeddah",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.97,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1020",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Perfect match – takeaway cups & lids",
    },

    # ── B. Partial-match / GRN-review-likely (8) ──────────────────────
    {
        "num": 6,
        "tag": "3W-CHICKEN-OCR-NOISE",
        "vendor_code": "V3W-001",
        "branch": "BR-RUH-003",
        "category": "Frozen Foods & Proteins",
        "description": "Chicken patty supply invoice – OCR noise on PO",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.74,
        "po_noise": "swap_digit",
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Invoice PO ref has swapped digits — PO retrieval noise",
    },
    {
        "num": 7,
        "tag": "3W-BUNS-PARTIAL-GRN",
        "vendor_code": "V3W-003",
        "branch": "BR-JED-001",
        "category": "Bakery & Buns",
        "description": "Buns and bakery supply invoice – partial receipt",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.91,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.PARTIAL_RECEIPT,
        "notes": "GRN shows 80% of PO qty received — receipt shortage",
    },
    {
        "num": 8,
        "tag": "3W-CONDIMENTS-MULTI-GRN",
        "vendor_code": "V3W-006",
        "branch": "BR-RUH-001",
        "category": "Beverages & Dry Goods",
        "description": "Kitchen consumables stock invoice – multi-GRN",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.88,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.MULTI_GRN,
        "notes": "Two partial GRNs — aggregation needed for full match",
    },
    {
        "num": 9,
        "tag": "3W-FROZEN-DELAYED-GRN",
        "vendor_code": "V3W-009",
        "branch": "BR-DMM-002",
        "category": "Frozen Goods & Cold Chain",
        "description": "Cold chain goods replenishment – delayed receipt",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.87,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.DELAYED_RECEIPT,
        "notes": "GRN received 45 days after PO date — delayed-receipt flag",
    },
    {
        "num": 10,
        "tag": "3W-DAIRY-CLOSE-AMT",
        "vendor_code": "V3W-002",
        "branch": "BR-RUH-001",
        "category": "Beverages & Dairy",
        "description": "Dairy supply invoice – amount close but slightly off",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.89,
        "po_noise": None,
        "amount_delta": Decimal("32.50"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Invoice total slightly higher than PO — may auto-close within tolerance",
    },
    {
        "num": 11,
        "tag": "3W-UNIFORM-LOC-MISMATCH",
        "vendor_code": "V3W-007",
        "branch": "BR-DMM-001",
        "category": "Uniforms & Housekeeping Stock",
        "description": "Restaurant uniforms stock invoice – location mismatch",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.85,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-2010",
        "grn_behaviour": GRNBehaviour.LOCATION_MISMATCH,
        "notes": "GRN warehouse is Jeddah but invoice says Dammam — location mismatch",
    },
    {
        "num": 12,
        "tag": "3W-PACKAGING-OVER-RECEIPT",
        "vendor_code": "V3W-004",
        "branch": "BR-RUH-002",
        "category": "Packaging Materials",
        "description": "Takeaway packaging replenishment – over-receipt",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.90,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1020",
        "grn_behaviour": GRNBehaviour.OVER_RECEIPT,
        "notes": "GRN quantity 110% of PO — over-receipt scenario",
    },
    {
        "num": 13,
        "tag": "3W-PROTEIN-NO-GRN",
        "vendor_code": "V3W-001",
        "branch": "BR-JED-002",
        "category": "Frozen Foods & Proteins",
        "description": "Frozen protein supply – GRN not yet received",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.92,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.NO_GRN,
        "notes": "No GRN exists for this PO — GRN_NOT_FOUND exception expected",
    },

    # ── C. Fail-likely / exception-prone (7) ──────────────────────────
    {
        "num": 14,
        "tag": "3W-PACKAGING-DUPLICATE",
        "vendor_code": "V3W-004",
        "branch": "BR-JED-001",
        "category": "Packaging Materials",
        "description": "Duplicate packaging invoice – same vendor/amount",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.92,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": True,
        "duplicate_of_num": 2,
        "cost_center": "CC-1020",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Intentional duplicate of scenario 2 — DUPLICATE_INVOICE exception",
    },
    {
        "num": 15,
        "tag": "3W-COLDCHAIN-BAD-PO",
        "vendor_code": "V3W-009",
        "branch": "BR-DMM-001",
        "category": "Frozen Goods & Cold Chain",
        "description": "Imported stock invoice with corrupted PO field",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.62,
        "po_noise": "malformed",
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Malformed PO reference — PO_NOT_FOUND + low confidence",
    },
    {
        "num": 16,
        "tag": "3W-SPARES-HIGH-VALUE",
        "vendor_code": "V3W-008",
        "branch": "BR-RUH-001",
        "category": "Spare Parts & Equipment",
        "description": "High-value spare parts goods invoice – Riyadh",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.90,
        "po_noise": None,
        "amount_delta": Decimal("4200.00"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-4010",
        "grn_behaviour": GRNBehaviour.PARTIAL_RECEIPT,
        "notes": "High-value invoice with surcharge + partial GRN — review required",
    },
    {
        "num": 17,
        "tag": "3W-BEVERAGE-WEAK-VENDOR",
        "vendor_code": "V3W-002",
        "branch": "BR-JED-002",
        "category": "Beverages & Dairy",
        "description": "Invoice with weak vendor extraction – Arabic OCR",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.58,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": Decimal("0.10"),
        "missing_fields": ["cost_center"],
        "is_duplicate": False,
        "cost_center": "",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Low confidence + Arabic OCR vendor name + wrong tax rate",
    },
    {
        "num": 18,
        "tag": "3W-PAPER-MISSING-PO",
        "vendor_code": "V3W-010",
        "branch": "BR-DMM-002",
        "category": "Paper & Takeaway Packaging",
        "description": "Paper products invoice – missing PO reference entirely",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.52,
        "po_noise": "missing",
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": ["po_number", "currency"],
        "is_duplicate": False,
        "cost_center": "CC-1020",
        "grn_behaviour": GRNBehaviour.NO_GRN,
        "notes": "PO field completely missing — PO_NOT_FOUND + missing currency",
    },
    {
        "num": 19,
        "tag": "3W-CHEMICAL-TAX-MISMATCH",
        "vendor_code": "V3W-005",
        "branch": "BR-RUH-003",
        "category": "Cleaning Chemicals",
        "description": "Cleaning supply invoice – tax and amount mismatch",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.86,
        "po_noise": None,
        "amount_delta": Decimal("-150.00"),
        "tax_override": Decimal("0.05"),
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1030",
        "grn_behaviour": GRNBehaviour.FULL_RECEIPT,
        "notes": "Invoice uses 5% VAT instead of 15% + amount shortfall — TAX_MISMATCH + AMOUNT_MISMATCH",
    },
    {
        "num": 20,
        "tag": "3W-FROZEN-QTY-EXCEEDS",
        "vendor_code": "V3W-009",
        "branch": "BR-RUH-001",
        "category": "Frozen Goods & Cold Chain",
        "description": "Frozen goods invoice – qty exceeds received",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.88,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-1010",
        "grn_behaviour": GRNBehaviour.PARTIAL_RECEIPT,
        "notes": "Invoice claims full qty but GRN received only 60% — INVOICE_QTY_EXCEEDS_RECEIVED",
    },
]


# ============================================================================
# Helper utilities
# ============================================================================

def _d(val) -> Decimal:
    """Coerce to Decimal, rounded to 2 dp."""
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _base_date(scenario_num: int) -> date:
    """Stagger invoice dates over the past ~6 months for realistic aging."""
    base = date(2026, 3, 1)
    offset = (scenario_num * 9) % 180
    return base - timedelta(days=offset)


def _pick_items(category: str, count: int) -> list[dict]:
    """Pick stock line items from catalog."""
    pool = STOCK_ITEMS_CATALOG.get(category, [])
    if not pool:
        pool = [{"desc": f"{category} – stock item", "uom": "EA", "price": 250.00, "code": "GEN-001"}]
    count = min(count, len(pool))
    return _rng.sample(pool, count)


def _apply_po_noise(po_number: str, noise_type: str | None) -> str:
    """Apply OCR noise to a PO number for testing."""
    if noise_type == "swap_digit" and len(po_number) >= 4:
        return po_number[:-2] + po_number[-1] + po_number[-2]
    if noise_type == "malformed":
        return f"P0-{po_number[3:]}-X"  # Replace PO prefix with P0 + add garbage suffix
    if noise_type == "missing":
        return ""
    return po_number


def _build_raw_extraction_json(
    invoice_number: str,
    vendor_name: str,
    po_number: str,
    invoice_date: date,
    subtotal: Decimal,
    tax_amount: Decimal | None,
    total_amount: Decimal,
    currency: str,
    line_items: list[dict],
    confidence: float,
    description: str,
    branch_code: str,
    warehouse_code: str,
    cost_center: str,
) -> dict:
    """Build a realistic raw extraction JSON payload for an invoice."""
    return {
        "extraction_engine": "azure_document_intelligence",
        "extraction_model": "prebuilt-invoice",
        "overall_confidence": confidence,
        "header": {
            "invoice_number": {"value": invoice_number, "confidence": confidence},
            "vendor_name": {"value": vendor_name, "confidence": max(confidence - 0.03, 0.40)},
            "invoice_date": {"value": str(invoice_date), "confidence": confidence},
            "due_date": {
                "value": str(invoice_date + timedelta(days=30)),
                "confidence": confidence - 0.05,
            },
            "po_number": {
                "value": po_number,
                "confidence": max(confidence - 0.08, 0.35) if po_number else 0.0,
            },
            "currency": {"value": currency, "confidence": 0.99 if currency else 0.0},
        },
        "vendor_block": {
            "name": vendor_name,
            "address": "Saudi Arabia",
            "tax_id_detected": True,
        },
        "totals": {
            "subtotal": {"value": str(subtotal), "confidence": confidence},
            "tax_amount": {
                "value": str(tax_amount) if tax_amount is not None else None,
                "confidence": confidence - 0.03 if tax_amount is not None else 0.0,
            },
            "total_amount": {"value": str(total_amount), "confidence": confidence},
            "vat_rate_detected": "15%" if tax_amount else None,
        },
        "receiving_info": {
            "warehouse_code": warehouse_code,
            "warehouse_name": WAREHOUSES.get(warehouse_code, ""),
            "branch_code": branch_code,
        },
        "line_items": [
            {
                "line_number": idx + 1,
                "item_code": {"value": li.get("item_code", ""), "confidence": confidence - 0.02},
                "description": {"value": li["desc"], "confidence": confidence - 0.01},
                "quantity": {"value": str(li["quantity"]), "confidence": confidence},
                "unit_price": {"value": str(li["unit_price"]), "confidence": confidence},
                "line_amount": {"value": str(li["line_amount"]), "confidence": confidence},
                "unit_of_measure": li.get("uom", "EA"),
                "is_stock_item": True,
            }
            for idx, li in enumerate(line_items)
        ],
        "cost_center": cost_center,
        "detected_language": "en",
        "page_count": _rng.randint(1, 2),
        "document_type": "goods_invoice",
    }


# ============================================================================
# Core creation helpers
# ============================================================================

def create_vendors(admin: User) -> dict[str, Vendor]:
    """Create or reuse THREE_WAY goods/stock vendors."""
    vendors: dict[str, Vendor] = {}
    for v in THREE_WAY_VENDORS:
        vendor, _ = Vendor.objects.get_or_create(
            code=v["code"],
            defaults={
                "name": v["name"],
                "normalized_name": v["name"].upper().strip(),
                "tax_id": v.get("tax_id", ""),
                "country": v.get("country", "Saudi Arabia"),
                "currency": v.get("currency", "SAR"),
                "payment_terms": v.get("payment_terms", ""),
                "contact_email": v.get("contact_email", ""),
                "address": f"{v['category']} supplier, Saudi Arabia",
                "created_by": admin,
            },
        )
        vendors[v["code"]] = vendor
    return vendors


def create_vendor_aliases(vendors: dict[str, Vendor], admin: User) -> int:
    """Create vendor aliases for all THREE_WAY vendors."""
    total = 0
    for v_data in THREE_WAY_VENDORS:
        vendor = vendors.get(v_data["code"])
        if not vendor:
            continue
        for alias_name in v_data.get("aliases", []):
            _, created = VendorAlias.objects.get_or_create(
                vendor=vendor,
                normalized_alias=alias_name.upper().strip(),
                defaults={
                    "alias_name": alias_name,
                    "source": "manual",
                    "created_by": admin,
                },
            )
            if created:
                total += 1
    return total


def create_po_for_scenario(
    scenario: dict,
    vendor: Vendor,
    admin: User,
    line_items: list[dict],
    quantities: list[int],
) -> tuple[PurchaseOrder, list[PurchaseOrderLineItem]]:
    """Create a PO + line items for a THREE_WAY scenario."""
    po_num = f"PO-3W-{scenario['num']:04d}"
    inv_date = _base_date(scenario["num"])
    po_date = inv_date - timedelta(days=_rng.randint(10, 45))

    subtotal = Decimal("0")
    for item, qty in zip(line_items, quantities):
        subtotal += _d(item["price"]) * qty

    tax_amount = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
            "buyer_name": "Procurement – McDonald's KSA",
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
                "item_code": item.get("code", f"STK-{scenario['num']:04d}-{idx:02d}"),
                "description": item["desc"],
                "quantity": Decimal(str(qty)),
                "unit_price": unit_price,
                "tax_amount": line_tax,
                "line_amount": line_amount,
                "unit_of_measure": item.get("uom", "EA"),
                "is_service_item": False,
                "is_stock_item": True,
                "item_category": scenario["category"],
            },
        )
        po_lines.append(pl)
    return po, po_lines


def create_grn_for_scenario(
    scenario: dict,
    po: PurchaseOrder,
    po_lines: list[PurchaseOrderLineItem],
    vendor: Vendor,
    admin: User,
) -> list[GoodsReceiptNote]:
    """
    Create GRN(s) + line items based on the scenario's grn_behaviour.
    Returns list of created GRNs (may be empty for NO_GRN).
    """
    behaviour = scenario.get("grn_behaviour", GRNBehaviour.FULL_RECEIPT)
    inv_date = _base_date(scenario["num"])
    po_date = po.po_date or (inv_date - timedelta(days=20))
    branch = next((b for b in BRANCHES if b["code"] == scenario["branch"]), BRANCHES[0])
    warehouse_code = branch.get("warehouse", "WH-RUH-CENTRAL")

    if behaviour == GRNBehaviour.NO_GRN:
        return []

    if behaviour == GRNBehaviour.MULTI_GRN:
        return _create_multi_grn(scenario, po, po_lines, vendor, admin, warehouse_code, po_date)

    # Single GRN for all other behaviours
    grn_num = f"GRN-3W-{scenario['num']:04d}"

    # Receipt date logic
    if behaviour == GRNBehaviour.DELAYED_RECEIPT:
        receipt_date = po_date + timedelta(days=_rng.randint(40, 60))
    else:
        receipt_date = po_date + timedelta(days=_rng.randint(3, 12))

    # Warehouse override for location mismatch
    if behaviour == GRNBehaviour.LOCATION_MISMATCH:
        mismatched_warehouses = [wh for wh in WAREHOUSES if wh != warehouse_code]
        warehouse_code = _rng.choice(mismatched_warehouses) if mismatched_warehouses else warehouse_code

    grn, _ = GoodsReceiptNote.objects.get_or_create(
        grn_number=grn_num,
        defaults={
            "purchase_order": po,
            "vendor": vendor,
            "receipt_date": receipt_date,
            "status": "RECEIVED",
            "warehouse": warehouse_code,
            "receiver_name": f"Warehouse Ops – {WAREHOUSES.get(warehouse_code, warehouse_code)}",
            "created_by": admin,
        },
    )

    # Create GRN line items
    for idx, po_line in enumerate(po_lines, start=1):
        po_qty = po_line.quantity

        if behaviour == GRNBehaviour.FULL_RECEIPT:
            qty_received = po_qty
            qty_accepted = po_qty
            qty_rejected = Decimal("0")
        elif behaviour == GRNBehaviour.PARTIAL_RECEIPT:
            # Receive 60-85% of PO qty
            factor = Decimal(str(_rng.uniform(0.60, 0.85))).quantize(Decimal("0.01"))
            qty_received = (po_qty * factor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            qty_accepted = qty_received
            qty_rejected = Decimal("0")
        elif behaviour == GRNBehaviour.OVER_RECEIPT:
            # Receive 105-115% of PO qty
            factor = Decimal(str(_rng.uniform(1.05, 1.15))).quantize(Decimal("0.01"))
            qty_received = (po_qty * factor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            qty_accepted = qty_received
            qty_rejected = Decimal("0")
        elif behaviour == GRNBehaviour.DELAYED_RECEIPT:
            qty_received = po_qty
            qty_accepted = po_qty
            qty_rejected = Decimal("0")
        elif behaviour == GRNBehaviour.LOCATION_MISMATCH:
            qty_received = po_qty
            qty_accepted = po_qty
            qty_rejected = Decimal("0")
        else:
            qty_received = po_qty
            qty_accepted = po_qty
            qty_rejected = Decimal("0")

        GRNLineItem.objects.get_or_create(
            grn=grn,
            line_number=idx,
            defaults={
                "po_line": po_line,
                "item_code": po_line.item_code,
                "description": po_line.description,
                "quantity_received": qty_received,
                "quantity_accepted": qty_accepted,
                "quantity_rejected": qty_rejected,
                "unit_of_measure": po_line.unit_of_measure,
            },
        )

    return [grn]


def _create_multi_grn(
    scenario: dict,
    po: PurchaseOrder,
    po_lines: list[PurchaseOrderLineItem],
    vendor: Vendor,
    admin: User,
    warehouse_code: str,
    po_date: date,
) -> list[GoodsReceiptNote]:
    """Create two partial GRNs that together should cover PO quantities."""
    grns_created = []

    for grn_idx, (suffix, day_offset, factor) in enumerate([
        ("A", _rng.randint(3, 8), Decimal("0.55")),
        ("B", _rng.randint(12, 20), Decimal("0.45")),
    ], start=1):
        grn_num = f"GRN-3W-{scenario['num']:04d}-{suffix}"
        receipt_date = po_date + timedelta(days=day_offset)

        grn, _ = GoodsReceiptNote.objects.get_or_create(
            grn_number=grn_num,
            defaults={
                "purchase_order": po,
                "vendor": vendor,
                "receipt_date": receipt_date,
                "status": "RECEIVED",
                "warehouse": warehouse_code,
                "receiver_name": f"Warehouse Ops – Batch {suffix}",
                "created_by": admin,
            },
        )

        for idx, po_line in enumerate(po_lines, start=1):
            qty_received = (po_line.quantity * factor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            GRNLineItem.objects.get_or_create(
                grn=grn,
                line_number=idx,
                defaults={
                    "po_line": po_line,
                    "item_code": po_line.item_code,
                    "description": po_line.description,
                    "quantity_received": qty_received,
                    "quantity_accepted": qty_received,
                    "quantity_rejected": Decimal("0"),
                    "unit_of_measure": po_line.unit_of_measure,
                },
            )

        grns_created.append(grn)

    return grns_created


def create_invoice_for_scenario(
    scenario: dict,
    vendor: Vendor,
    admin: User,
    po: PurchaseOrder | None,
    po_lines: list[PurchaseOrderLineItem],
    line_items: list[dict],
    quantities: list[int],
    duplicate_of_invoice: Invoice | None = None,
) -> tuple[Invoice, list[InvoiceLineItem]]:
    """
    Create an Invoice + line items + DocumentUpload stub + ExtractionResult
    for a THREE_WAY scenario.
    Applies amount deltas, tax overrides, PO noise, and missing fields.
    """
    sc_num = scenario["num"]
    inv_num = f"INV-3W-{sc_num:04d}"
    inv_date = _base_date(sc_num)
    due_date = inv_date + timedelta(days=30)
    confidence = scenario["confidence"]
    branch_code = scenario["branch"]
    branch = next((b for b in BRANCHES if b["code"] == branch_code), BRANCHES[0])
    city = branch["city"]
    warehouse_code = branch.get("warehouse", "WH-RUH-CENTRAL")

    # ── Compute invoice line amounts ─────────────────────────
    inv_lines_data = []
    for idx, (item, qty) in enumerate(zip(line_items, quantities)):
        unit_price = _d(item["price"])
        quantity = Decimal(str(qty))
        line_amount = (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        inv_lines_data.append({
            "desc": item["desc"],
            "uom": item.get("uom", "EA"),
            "item_code": item.get("code", ""),
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
        })

    subtotal = sum(d["line_amount"] for d in inv_lines_data)

    # Apply amount delta (surcharge / shortfall) — distribute to the FIRST
    # line to make line-level matching detect the discrepancy.
    amount_delta = scenario.get("amount_delta", Decimal("0"))
    if amount_delta:
        inv_lines_data[0]["line_amount"] = (
            inv_lines_data[0]["line_amount"] + amount_delta
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        qty = inv_lines_data[0]["quantity"]
        if qty:
            inv_lines_data[0]["unit_price"] = (
                inv_lines_data[0]["line_amount"] / qty
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        subtotal = sum(d["line_amount"] for d in inv_lines_data)

    # Determine tax
    tax_override = scenario.get("tax_override")
    if tax_override == "missing":
        tax_amount = None
    elif tax_override is not None:
        tax_amount = (subtotal * Decimal(str(tax_override))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        tax_amount = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total_amount = subtotal + (tax_amount or Decimal("0"))

    # PO reference on the invoice
    po_number_raw = po.po_number if po else ""
    po_number_on_invoice = _apply_po_noise(po_number_raw, scenario.get("po_noise"))

    # Missing fields
    missing = set(scenario.get("missing_fields", []))
    invoice_currency = "" if "currency" in missing else "SAR"
    cost_center = scenario.get("cost_center", "")

    # Vendor name as seen by OCR — use Arabic alias for low-confidence scenarios
    raw_vendor_name = vendor.name
    if scenario["confidence"] < 0.70 and vendor.aliases.exists():
        arabic_aliases = [
            a.alias_name for a in vendor.aliases.all()
            if any("\u0600" <= c <= "\u06FF" for c in a.alias_name)
        ]
        if arabic_aliases:
            raw_vendor_name = arabic_aliases[0]

    # DocumentUpload stub
    upload_filename = f"INV_{vendor.code}_{inv_date.strftime('%Y%m%d')}_{sc_num:04d}.pdf"
    doc_upload, _ = DocumentUpload.objects.get_or_create(
        original_filename=upload_filename,
        document_type=DocumentType.INVOICE,
        defaults={
            "file_size": _rng.randint(100_000, 750_000),
            "content_type": "application/pdf",
            "processing_state": FileProcessingState.COMPLETED,
            "processing_message": "Extraction completed successfully",
            "uploaded_by": admin,
            "created_by": admin,
        },
    )

    # Build raw extraction JSON
    raw_json = _build_raw_extraction_json(
        invoice_number=inv_num,
        vendor_name=raw_vendor_name,
        po_number=po_number_on_invoice,
        invoice_date=inv_date,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        currency=invoice_currency or "SAR",
        line_items=inv_lines_data,
        confidence=confidence,
        description=scenario["description"],
        branch_code=branch_code,
        warehouse_code=warehouse_code,
        cost_center=cost_center,
    )

    invoice, created = Invoice.objects.get_or_create(
        invoice_number=inv_num,
        defaults={
            "normalized_invoice_number": inv_num.upper(),
            "document_upload": doc_upload,
            "vendor": vendor,
            "raw_vendor_name": raw_vendor_name,
            "raw_invoice_number": inv_num,
            "raw_invoice_date": str(inv_date),
            "raw_po_number": po_number_on_invoice,
            "raw_currency": invoice_currency or "SAR",
            "raw_subtotal": str(subtotal),
            "raw_tax_amount": str(tax_amount) if tax_amount is not None else "",
            "raw_total_amount": str(total_amount),
            "invoice_date": inv_date,
            "po_number": po_number_on_invoice if "po_number" not in missing else "",
            "normalized_po_number": po_number_on_invoice.upper() if (
                po_number_on_invoice and "po_number" not in missing
            ) else "",
            "currency": invoice_currency,
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "status": scenario["invoice_status"],
            "extraction_confidence": confidence,
            "extraction_raw_json": raw_json,
            "is_duplicate": scenario.get("is_duplicate", False),
            "duplicate_of": duplicate_of_invoice,
            "created_by": admin,
        },
    )

    # ── Invoice line items ───────────────────────────────────
    if not created and invoice.line_items.count() == 0:
        invoice.line_items.all().delete()

    inv_line_objs = []
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
                "extraction_confidence": confidence,
                "item_category": scenario["category"],
                "is_service_item": False,
                "is_stock_item": True,
            },
        )
        inv_line_objs.append(il)

    # ── ExtractionResult stub ────────────────────────────────
    if created:
        ExtractionResult.objects.get_or_create(
            document_upload=doc_upload,
            invoice=invoice,
            defaults={
                "engine_name": "azure_document_intelligence",
                "engine_version": "2024-02-29-preview",
                "raw_response": raw_json,
                "confidence": confidence,
                "duration_ms": _rng.randint(1200, 4500),
                "success": True,
                "created_by": admin,
            },
        )

    return invoice, inv_line_objs


# ============================================================================
# Bulk random scenario generator
# ============================================================================

def _generate_random_scenarios(
    start_num: int,
    count: int,
    vendors: dict[str, Vendor],
    admin: User,
    rand_seed: int,
) -> dict:
    """Generate additional random THREE_WAY scenarios for QA/large modes."""
    rng = random.Random(rand_seed + 1000)  # Offset seed to avoid overlap
    vendor_codes = list(vendors.keys())
    stats = {
        "invoices": 0, "pos": 0, "grns": 0,
        "duplicates": 0, "malformed_po": 0,
        "high_value": 0, "incomplete": 0,
    }

    grn_behaviours = [
        GRNBehaviour.FULL_RECEIPT,
        GRNBehaviour.FULL_RECEIPT,
        GRNBehaviour.FULL_RECEIPT,
        GRNBehaviour.PARTIAL_RECEIPT,
        GRNBehaviour.NO_GRN,
        GRNBehaviour.OVER_RECEIPT,
        GRNBehaviour.MULTI_GRN,
        GRNBehaviour.DELAYED_RECEIPT,
        GRNBehaviour.LOCATION_MISMATCH,
    ]

    for i in range(count):
        sc_num = start_num + i
        v_code = rng.choice(vendor_codes)
        vendor = vendors[v_code]
        v_data = next((v for v in THREE_WAY_VENDORS if v["code"] == v_code), THREE_WAY_VENDORS[0])
        category = v_data["category"]
        branch = rng.choice(BRANCHES)

        # Random characteristics
        confidence = round(rng.uniform(0.48, 0.98), 2)
        po_noise = rng.choices(
            [None, "swap_digit", "malformed", "missing"],
            weights=[70, 12, 10, 8],
        )[0]
        amount_delta = Decimal(str(rng.choice([0, 0, 0, 0, 95, -80, 250, 420, -300, 1800])))
        tax_choice = rng.choices(
            [None, Decimal("0.05"), Decimal("0.10"), "missing"],
            weights=[72, 10, 10, 8],
        )[0]
        is_dup = rng.random() < 0.08
        cost_center = rng.choice(COST_CENTERS + [""])
        grn_behaviour = rng.choice(grn_behaviours)

        # Determine invoice status
        if confidence < 0.60 or po_noise == "missing":
            inv_status = InvoiceStatus.EXTRACTED
        elif po_noise == "malformed":
            inv_status = InvoiceStatus.EXTRACTED
        else:
            inv_status = rng.choice([
                InvoiceStatus.EXTRACTED,
                InvoiceStatus.VALIDATED,
                InvoiceStatus.READY_FOR_RECON,
                InvoiceStatus.READY_FOR_RECON,
                InvoiceStatus.READY_FOR_RECON,
            ])

        scenario = {
            "num": sc_num,
            "tag": f"3W-GEN-{sc_num:04d}",
            "vendor_code": v_code,
            "branch": branch["code"],
            "category": category,
            "description": f"{category} stock supply – {branch['city']} ({branch['name']})",
            "invoice_status": inv_status,
            "confidence": confidence,
            "po_noise": po_noise,
            "amount_delta": amount_delta,
            "tax_override": tax_choice,
            "missing_fields": [],
            "is_duplicate": is_dup,
            "cost_center": cost_center,
            "grn_behaviour": grn_behaviour if po_noise != "missing" else GRNBehaviour.NO_GRN,
        }

        # Build missing fields list
        if tax_choice == "missing":
            scenario["missing_fields"].append("tax_amount")
        if po_noise == "missing":
            scenario["missing_fields"].append("po_number")
        if cost_center == "":
            scenario["missing_fields"].append("cost_center")

        # Create PO (unless PO is intentionally missing)
        n_lines = rng.choice([2, 3, 4])
        items = _pick_items(category, n_lines)
        quantities = [rng.randint(5, 60) for _ in items]

        po, po_lines = None, []
        if po_noise != "missing":
            po, po_lines = create_po_for_scenario(scenario, vendor, admin, items, quantities)
            stats["pos"] += 1

            # Create GRN(s)
            grns = create_grn_for_scenario(scenario, po, po_lines, vendor, admin)
            stats["grns"] += len(grns)

        invoice, _ = create_invoice_for_scenario(
            scenario, vendor, admin, po, po_lines, items, quantities,
        )
        stats["invoices"] += 1
        if is_dup:
            stats["duplicates"] += 1
        if po_noise == "malformed":
            stats["malformed_po"] += 1
        if invoice.total_amount and invoice.total_amount > Decimal("50000"):
            stats["high_value"] += 1
        if scenario.get("missing_fields"):
            stats["incomplete"] += 1

    return stats


# ============================================================================
# Command
# ============================================================================

class Command(BaseCommand):
    help = (
        "Seed THREE_WAY PO-backed goods/stock invoice data for "
        "McDonald's Saudi Arabia demos/QA. Creates vendors, POs, GRNs, "
        "and invoices with realistic extraction metadata."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            type=str,
            default="demo",
            choices=["demo", "qa", "large"],
            help="Seed mode: demo (20), qa (+15), large (+40)",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete previously seeded THREE_WAY data before re-creating",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for deterministic generation (default: 42)",
        )
        parser.add_argument(
            "--summary",
            action="store_true",
            help="Print invoice summary table after seeding",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        do_reset = options["reset"]
        rand_seed = options["seed"]
        show_summary = options["summary"]

        # Reset deterministic RNG
        global _rng
        _rng = random.Random(rand_seed)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'='*70}\n"
            f"  McDonald's KSA - THREE_WAY Goods/Stock Invoice Seed Data\n"
            f"  Mode: {mode.upper()} | Reset: {do_reset} | Seed: {rand_seed}\n"
            f"{'='*70}\n"
        ))

        start = time.time()

        if do_reset:
            self._reset_data()

        with transaction.atomic():
            self._seed(mode, rand_seed)

        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(
            f"\n  Seeding completed in {elapsed:.1f}s"
        ))

        if show_summary or mode == "demo":
            self._print_summary()

    # ----------------------------------------------------------------
    # Reset — only deletes 3W-prefixed records
    # ----------------------------------------------------------------

    def _reset_data(self):
        self.stdout.write(self.style.WARNING("  Resetting seeded THREE_WAY data..."))

        # GRN lines & GRNs
        grn_qs = GoodsReceiptNote.objects.filter(grn_number__startswith="GRN-3W-")
        GRNLineItem.objects.filter(grn__in=grn_qs).delete()
        grn_qs.delete()

        # Invoices, lines, extraction results
        inv_qs = Invoice.objects.filter(invoice_number__startswith="INV-3W-")
        InvoiceLineItem.objects.filter(invoice__in=inv_qs).delete()
        ExtractionResult.objects.filter(invoice__in=inv_qs).delete()
        upload_ids = list(inv_qs.values_list("document_upload_id", flat=True))
        inv_qs.delete()
        DocumentUpload.objects.filter(id__in=[uid for uid in upload_ids if uid]).delete()

        # POs & lines
        po_qs = PurchaseOrder.objects.filter(po_number__startswith="PO-3W-")
        PurchaseOrderLineItem.objects.filter(purchase_order__in=po_qs).delete()
        po_qs.delete()

        # Vendors & aliases (only 3W-prefixed)
        v_qs = Vendor.objects.filter(code__startswith="V3W-")
        VendorAlias.objects.filter(vendor__in=v_qs).delete()
        v_qs.delete()

        self.stdout.write(self.style.SUCCESS("  Reset complete.\n"))

    # ----------------------------------------------------------------
    # Seed
    # ----------------------------------------------------------------

    def _seed(self, mode: str, rand_seed: int):
        # 1. Get or create admin user
        admin = User.objects.filter(role=UserRole.ADMIN).first()
        if not admin:
            admin, _ = User.objects.get_or_create(
                email="admin@mcd-ksa.com",
                defaults={
                    "first_name": "System",
                    "last_name": "Admin",
                    "role": UserRole.ADMIN,
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            admin.set_password("SeedPass123!")
            admin.save(update_fields=["password"])

        # 2. Vendors & aliases
        self.stdout.write("  [1/5] Creating THREE_WAY goods/stock vendors...")
        vendors = create_vendors(admin)
        n_aliases = create_vendor_aliases(vendors, admin)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(vendors)} vendors, {n_aliases} aliases"
        ))

        # 3. Deterministic scenarios — POs, GRNs, Invoices
        self.stdout.write(f"  [2/5] Creating POs for {len(SCENARIOS)} scenarios...")
        invoices_created: dict[int, Invoice] = {}
        stats = {
            "vendors": len(vendors),
            "aliases": n_aliases,
            "invoices": 0,
            "pos": 0,
            "grns": 0,
            "duplicates": 0,
            "malformed_po": 0,
            "high_value": 0,
            "incomplete": 0,
            "no_grn": 0,
            "partial_receipt": 0,
            "over_receipt": 0,
            "multi_grn": 0,
            "delayed_receipt": 0,
            "location_mismatch": 0,
        }

        for sc in SCENARIOS:
            vendor = vendors[sc["vendor_code"]]
            n_lines = _rng.choice([2, 3, 4])
            items = _pick_items(sc["category"], n_lines)
            quantities = [_rng.randint(8, 50) for _ in items]

            # Create PO (unless PO is intentionally missing)
            po, po_lines = None, []
            if sc.get("po_noise") != "missing":
                po, po_lines = create_po_for_scenario(sc, vendor, admin, items, quantities)
                stats["pos"] += 1

            # Create GRN(s) if applicable
            grns = []
            if po and po_lines:
                grns = create_grn_for_scenario(sc, po, po_lines, vendor, admin)
                stats["grns"] += len(grns)

            # Handle duplicate linkage
            dup_invoice = None
            if sc.get("is_duplicate") and sc.get("duplicate_of_num"):
                dup_invoice = invoices_created.get(sc["duplicate_of_num"])

            invoice, inv_lines = create_invoice_for_scenario(
                sc, vendor, admin, po, po_lines, items, quantities,
                duplicate_of_invoice=dup_invoice,
            )
            invoices_created[sc["num"]] = invoice
            stats["invoices"] += 1

            # Track stats
            if sc.get("is_duplicate"):
                stats["duplicates"] += 1
            if sc.get("po_noise") in ("malformed", "missing"):
                stats["malformed_po"] += 1
            if invoice.total_amount and invoice.total_amount > Decimal("50000"):
                stats["high_value"] += 1
            if sc.get("missing_fields"):
                stats["incomplete"] += 1

            grn_beh = sc.get("grn_behaviour", "")
            if grn_beh == GRNBehaviour.NO_GRN:
                stats["no_grn"] += 1
            elif grn_beh == GRNBehaviour.PARTIAL_RECEIPT:
                stats["partial_receipt"] += 1
            elif grn_beh == GRNBehaviour.OVER_RECEIPT:
                stats["over_receipt"] += 1
            elif grn_beh == GRNBehaviour.MULTI_GRN:
                stats["multi_grn"] += 1
            elif grn_beh == GRNBehaviour.DELAYED_RECEIPT:
                stats["delayed_receipt"] += 1
            elif grn_beh == GRNBehaviour.LOCATION_MISMATCH:
                stats["location_mismatch"] += 1

        self.stdout.write(self.style.SUCCESS(
            f"  [3/5] {stats['invoices']} invoices, {stats['pos']} POs, "
            f"{stats['grns']} GRNs created"
        ))

        # 4. Bulk generated scenarios for qa/large
        if mode in ("qa", "large"):
            extra = 15 if mode == "qa" else 40
            self.stdout.write(f"  [4/5] Generating {extra} additional random scenarios...")
            bulk_stats = _generate_random_scenarios(
                start_num=21,
                count=extra,
                vendors=vendors,
                admin=admin,
                rand_seed=rand_seed,
            )
            for k in ("invoices", "pos", "grns", "duplicates", "malformed_po",
                       "high_value", "incomplete"):
                stats[k] += bulk_stats.get(k, 0)
            self.stdout.write(self.style.SUCCESS(
                f"        {bulk_stats['invoices']} additional invoices, "
                f"{bulk_stats['pos']} POs, {bulk_stats['grns']} GRNs"
            ))
        else:
            self.stdout.write("  [4/5] Skipping bulk generation (demo mode)")

        # 5. Print summary stats
        self.stdout.write("  [5/5] Seed statistics:")
        self._print_stats(stats)

    # ----------------------------------------------------------------
    # Stats
    # ----------------------------------------------------------------

    def _print_stats(self, stats: dict):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  {'-'*58}\n"
            f"  THREE_WAY SEED SUMMARY\n"
            f"  {'-'*58}"
        ))
        self.stdout.write(f"  Vendors created/reused:          {stats['vendors']}")
        self.stdout.write(f"  Vendor aliases:                  {stats['aliases']}")
        self.stdout.write(f"  Purchase Orders:                 {stats['pos']}")
        self.stdout.write(f"  Goods Receipt Notes:             {stats['grns']}")
        self.stdout.write(f"  Invoices created:                {stats['invoices']}")
        self.stdout.write(f"    - Duplicate-prone:             {stats['duplicates']}")
        self.stdout.write(f"    - Malformed/missing PO refs:   {stats['malformed_po']}")
        self.stdout.write(f"    - High-value (>50k SAR):       {stats['high_value']}")
        self.stdout.write(f"    - Incomplete fields:            {stats['incomplete']}")
        self.stdout.write(f"  GRN Behaviours (deterministic):")
        self.stdout.write(f"    - No GRN (GRN_NOT_FOUND):      {stats['no_grn']}")
        self.stdout.write(f"    - Partial receipt (shortage):   {stats['partial_receipt']}")
        self.stdout.write(f"    - Over-receipt:                 {stats['over_receipt']}")
        self.stdout.write(f"    - Multi-GRN:                    {stats['multi_grn']}")
        self.stdout.write(f"    - Delayed receipt:              {stats['delayed_receipt']}")
        self.stdout.write(f"    - Location mismatch:            {stats['location_mismatch']}")

    # ----------------------------------------------------------------
    # Invoice summary table
    # ----------------------------------------------------------------

    def _print_summary(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'='*140}\n"
            f"  SEEDED THREE_WAY INVOICE SUMMARY\n"
            f"{'='*140}"
        ))
        self.stdout.write(
            f"{'#':>3} {'Invoice':14} {'Vendor':30} {'PO Ref':18} "
            f"{'Total':>12} {'Status':20} {'Conf':>5} {'GRN':6} {'Flags':22}"
        )
        self.stdout.write("-" * 140)

        invoices = (
            Invoice.objects
            .filter(invoice_number__startswith="INV-3W-")
            .select_related("vendor")
            .order_by("invoice_number")
        )

        # Build a quick GRN count lookup via PO
        grn_counts = {}
        pos = PurchaseOrder.objects.filter(po_number__startswith="PO-3W-").prefetch_related("grns")
        for po in pos:
            grn_counts[po.po_number] = po.grns.count()

        for inv in invoices:
            vendor_name = inv.vendor.name[:28] if inv.vendor else inv.raw_vendor_name[:28]
            po_ref = inv.po_number or "(none)"
            total = f"SAR {inv.total_amount:,.2f}" if inv.total_amount else "N/A"
            conf = f"{inv.extraction_confidence:.2f}" if inv.extraction_confidence else "N/A"

            # GRN count for this invoice's PO
            actual_po = inv.normalized_po_number or inv.po_number or ""
            n_grn = grn_counts.get(actual_po, 0)
            grn_str = str(n_grn) if actual_po else "-"

            flags = []
            if inv.is_duplicate:
                flags.append("DUP")
            if not inv.po_number:
                flags.append("NO-PO")
            if inv.extraction_confidence and inv.extraction_confidence < 0.70:
                flags.append("LOW-CONF")
            if not inv.tax_amount:
                flags.append("NO-TAX")
            if not inv.currency:
                flags.append("NO-CCY")
            if inv.total_amount and inv.total_amount > Decimal("50000"):
                flags.append("HIGH-VAL")
            if n_grn == 0 and actual_po:
                flags.append("NO-GRN")

            num = inv.invoice_number.replace("INV-3W-", "")
            self.stdout.write(
                f"{num:>3} {inv.invoice_number:14} {vendor_name:30} {po_ref:18} "
                f"{total:>12} {inv.status:20} {conf:>5} {grn_str:>6} {', '.join(flags) or '-':22}"
            )

        self.stdout.write(f"\nTotal: {invoices.count()} invoices")

        # Status and confidence distribution
        from django.db.models import Count, Avg
        self.stdout.write(self.style.MIGRATE_HEADING("\n  Distribution:"))

        by_status = (
            Invoice.objects
            .filter(invoice_number__startswith="INV-3W-")
            .values("status")
            .annotate(c=Count("id"))
            .order_by("-c")
        )
        self.stdout.write(
            "  By Status:     " + "  |  ".join(f"{r['status']}: {r['c']}" for r in by_status)
        )

        avg_conf = (
            Invoice.objects
            .filter(invoice_number__startswith="INV-3W-")
            .aggregate(avg=Avg("extraction_confidence"))
        )
        self.stdout.write(f"  Avg Confidence: {avg_conf['avg']:.2f}" if avg_conf["avg"] else "")

        # Confidence buckets
        low = Invoice.objects.filter(
            invoice_number__startswith="INV-3W-", extraction_confidence__lt=0.70
        ).count()
        mid = Invoice.objects.filter(
            invoice_number__startswith="INV-3W-",
            extraction_confidence__gte=0.70,
            extraction_confidence__lt=0.90,
        ).count()
        high = Invoice.objects.filter(
            invoice_number__startswith="INV-3W-", extraction_confidence__gte=0.90
        ).count()
        self.stdout.write(f"  Confidence:    Low(<0.70): {low}  |  Medium(0.70-0.89): {mid}  |  High(>=0.90): {high}")
