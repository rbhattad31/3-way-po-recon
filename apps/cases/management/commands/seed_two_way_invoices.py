"""
Management command: seed_two_way_invoices

Seeds realistic McDonald's Saudi Arabia TWO_WAY PO-backed invoice data
for dev/demo/QA/UI testing.

This command creates ONLY:
  - Reference / master data (vendors, aliases, users)
  - Purchase Orders + PO line items (service-oriented)
  - Invoices + invoice line items (with extraction metadata)
  - DocumentUpload stubs

It does NOT create:
  - ReconciliationRun / ReconciliationResult / ReconciliationException
  - APCase / APCaseStage / APCaseArtifact
  - AgentRun / AgentMessage / AgentRecommendation
  - ReviewAssignment / ReviewDecision
  - AuditEvent

The seeded invoices are intended to be reconciliation-ready: trigger matching
later from the invoice detail page or via `create_cases_for_existing_invoices`.

Modes:
  --mode=demo   → 15 deterministic scenarios (default)
  --mode=qa     → 15 deterministic + 10 generated
  --mode=large  → 15 deterministic + 30 generated

Usage:
    python manage.py seed_two_way_invoices
    python manage.py seed_two_way_invoices --mode=qa
    python manage.py seed_two_way_invoices --mode=large
    python manage.py seed_two_way_invoices --reset
    python manage.py seed_two_way_invoices --seed=99
    python manage.py seed_two_way_invoices --summary

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
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.vendors.models import Vendor, VendorAlias

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deterministic RNG — reset per invocation
# ---------------------------------------------------------------------------
_rng = random.Random(42)
VAT_RATE = Decimal("0.15")

# Prefix for all seeded records — makes reset safe
PREFIX = "2W"


# ============================================================================
# Constants — TWO_WAY service vendors
# ============================================================================

TWO_WAY_VENDORS = [
    {
        "code": "V2W-001",
        "name": "Zamil Air Conditioners",
        "category": "HVAC Maintenance",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310100100100003",
        "payment_terms": "Net 30",
        "contact_email": "service@zamilac.com",
        "aliases": ["زامل للمكيفات", "Zamil AC", "Zamil HVAC Services"],
    },
    {
        "code": "V2W-002",
        "name": "Rentokil Initial Saudi Arabia",
        "category": "Pest Control",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310200200200003",
        "payment_terms": "Net 30",
        "contact_email": "ksa@rentokil.com",
        "aliases": ["رينتوكيل السعودية", "Rentokil KSA", "Rentokil Initial"],
    },
    {
        "code": "V2W-003",
        "name": "Saudi Services Co. Ltd. (SSCO)",
        "category": "Facility Maintenance",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310300300300003",
        "payment_terms": "Net 30",
        "contact_email": "contracts@ssco.com.sa",
        "aliases": ["الشركة السعودية للخدمات", "SSCO", "Saudi Services"],
    },
    {
        "code": "V2W-004",
        "name": "G4S Saudi Arabia",
        "category": "Security Services",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310400400400003",
        "payment_terms": "Net 30",
        "contact_email": "ksa@g4s.com",
        "aliases": ["جي4اس السعودية", "G4S KSA"],
    },
    {
        "code": "V2W-005",
        "name": "Al Tamimi Cleaning Services",
        "category": "Cleaning Services",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310500500500003",
        "payment_terms": "Net 30",
        "contact_email": "ops@altamimi-cleaning.com.sa",
        "aliases": ["التميمي لخدمات النظافة", "Al-Tamimi Cleaning", "Tamimi Janitorial"],
    },
    {
        "code": "V2W-006",
        "name": "Henny Penny Arabia LLC",
        "category": "Kitchen Equipment Service",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310600600600003",
        "payment_terms": "Net 30",
        "contact_email": "service.ksa@hennypenny.com",
        "aliases": ["هيني بيني العربية", "Henny Penny KSA", "HP Arabia"],
    },
    {
        "code": "V2W-007",
        "name": "Almajdouie Logistics",
        "category": "Cold Chain Logistics",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310700700700003",
        "payment_terms": "Net 30",
        "contact_email": "logistics@almajdouie.com",
        "aliases": ["المجدوعي للخدمات اللوجستية", "Almajdouie"],
    },
    {
        "code": "V2W-008",
        "name": "National Fire & Safety Co.",
        "category": "Fire Safety Services",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310800800800003",
        "payment_terms": "Net 45",
        "contact_email": "inspections@nfsc.com.sa",
        "aliases": ["الوطنية للحريق والسلامة", "NFSC", "National Fire Safety"],
    },
    {
        "code": "V2W-009",
        "name": "Pinnacle Consulting Arabia",
        "category": "Consulting Services",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "310900900900003",
        "payment_terms": "Net 45",
        "contact_email": "engagements@pinnacle-arabia.com",
        "aliases": ["بيناكل للاستشارات العربية", "Pinnacle Arabia"],
    },
    {
        "code": "V2W-010",
        "name": "Jeddah Office Admin Services",
        "category": "Office Admin Services",
        "country": "Saudi Arabia",
        "currency": "SAR",
        "tax_id": "311000100100003",
        "payment_terms": "Net 30",
        "contact_email": "admin@jeddah-office-svc.com.sa",
        "aliases": ["خدمات جدة الإدارية", "Jeddah Admin Svc"],
    },
]

# ---------------------------------------------------------------------------
# Service line items catalog (TWO_WAY categories)
# ---------------------------------------------------------------------------

SERVICE_ITEMS_CATALOG = {
    "HVAC Maintenance": [
        {"desc": "HVAC Annual Maintenance Contract – Restaurant", "uom": "VISIT", "price": 4500.00},
        {"desc": "HVAC Filter Replacement & Cleaning", "uom": "VISIT", "price": 850.00},
        {"desc": "HVAC Emergency Repair Call-Out", "uom": "VISIT", "price": 2200.00},
        {"desc": "Chiller Preventive Maintenance Visit", "uom": "VISIT", "price": 3200.00},
    ],
    "Pest Control": [
        {"desc": "Monthly Pest Control Service – Restaurant", "uom": "MONTH", "price": 750.00},
        {"desc": "Quarterly Fumigation Service", "uom": "VISIT", "price": 1800.00},
        {"desc": "Rodent Monitoring Station Maintenance", "uom": "VISIT", "price": 450.00},
    ],
    "Facility Maintenance": [
        {"desc": "Monthly Facility Maintenance Retainer", "uom": "MONTH", "price": 4500.00},
        {"desc": "Emergency Plumbing Repair", "uom": "VISIT", "price": 1200.00},
        {"desc": "Electrical Panel Maintenance Visit", "uom": "VISIT", "price": 1800.00},
        {"desc": "General Building Repair Service", "uom": "VISIT", "price": 2500.00},
    ],
    "Security Services": [
        {"desc": "Monthly Security Guard Service – Branch (2 guards)", "uom": "MONTH", "price": 8500.00},
        {"desc": "CCTV Maintenance & Monitoring – Monthly", "uom": "MONTH", "price": 1200.00},
        {"desc": "Access Control System Service Visit", "uom": "VISIT", "price": 1800.00},
    ],
    "Cleaning Services": [
        {"desc": "Cleaning Services – Monthly Contract", "uom": "MONTH", "price": 6500.00},
        {"desc": "Deep Cleaning Support Service", "uom": "VISIT", "price": 3500.00},
        {"desc": "Kitchen Deep Clean & Sanitization", "uom": "VISIT", "price": 2800.00},
    ],
    "Kitchen Equipment Service": [
        {"desc": "Fryer Deep Clean & Calibration Service", "uom": "VISIT", "price": 1800.00},
        {"desc": "Grill Maintenance & Parts Replacement", "uom": "VISIT", "price": 2400.00},
        {"desc": "Soft-Serve Machine Quarterly Service", "uom": "VISIT", "price": 1500.00},
        {"desc": "Refrigeration Preventive Maintenance", "uom": "VISIT", "price": 2600.00},
    ],
    "Cold Chain Logistics": [
        {"desc": "Refrigerated Transport – Riyadh DC to Branches", "uom": "TRIP", "price": 2800.00},
        {"desc": "Cold Storage Warehousing – Monthly", "uom": "MONTH", "price": 15000.00},
    ],
    "Fire Safety Services": [
        {"desc": "Fire Safety Inspection Service – Annual", "uom": "VISIT", "price": 3500.00},
        {"desc": "Fire Extinguisher Service & Refill", "uom": "VISIT", "price": 1200.00},
        {"desc": "Fire Alarm System Maintenance", "uom": "VISIT", "price": 2200.00},
    ],
    "Consulting Services": [
        {"desc": "Operations Consulting – Milestone Billing", "uom": "EA", "price": 35000.00},
        {"desc": "Process Improvement Advisory – Phase 1", "uom": "EA", "price": 22000.00},
        {"desc": "Food Safety Compliance Audit", "uom": "EA", "price": 15000.00},
    ],
    "Office Admin Services": [
        {"desc": "Office Administration Support – Monthly", "uom": "MONTH", "price": 4800.00},
        {"desc": "Document Management Service", "uom": "MONTH", "price": 2200.00},
    ],
}

# Branches / cost centers used
BRANCHES = [
    {"code": "BR-RUH-001", "name": "McDonald's Olaya Street", "city": "Riyadh"},
    {"code": "BR-RUH-002", "name": "McDonald's King Fahd Road", "city": "Riyadh"},
    {"code": "BR-RUH-003", "name": "McDonald's Exit 15 DT", "city": "Riyadh"},
    {"code": "BR-JED-001", "name": "McDonald's Tahlia Street", "city": "Jeddah"},
    {"code": "BR-JED-002", "name": "McDonald's Corniche", "city": "Jeddah"},
    {"code": "BR-DMM-001", "name": "McDonald's King Saud Street", "city": "Dammam"},
    {"code": "BR-DMM-002", "name": "McDonald's Dhahran Mall", "city": "Dammam"},
]

COST_CENTERS = [
    "CC-3010",  # Facilities & Maintenance
    "CC-2010",  # Store Operations
    "CC-5010",  # IT
    "CC-6010",  # Finance
]


# ============================================================================
# 15 deterministic TWO_WAY invoice scenarios
# ============================================================================

SCENARIOS = [
    # ── A. Likely matched ──────────────────────────────────────────────
    {
        "num": 1,
        "tag": "2W-HVAC-PERFECT",
        "vendor_code": "V2W-001",
        "branch": "BR-RUH-001",
        "category": "HVAC Maintenance",
        "description": "HVAC annual maintenance – Riyadh branch",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.96,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    {
        "num": 2,
        "tag": "2W-CLEANING-PERFECT",
        "vendor_code": "V2W-005",
        "branch": "BR-JED-001",
        "category": "Cleaning Services",
        "description": "Cleaning services – Jeddah restaurant cluster",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.94,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    {
        "num": 3,
        "tag": "2W-PESTCONTROL-PERFECT",
        "vendor_code": "V2W-002",
        "branch": "BR-DMM-001",
        "category": "Pest Control",
        "description": "Pest control monthly service – Dammam",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.93,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    {
        "num": 4,
        "tag": "2W-SECURITY-PERFECT",
        "vendor_code": "V2W-004",
        "branch": "BR-RUH-002",
        "category": "Security Services",
        "description": "Security services invoice – Riyadh region",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.97,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-2010",
    },
    {
        "num": 5,
        "tag": "2W-FACILITY-AMC-PERFECT",
        "vendor_code": "V2W-003",
        "branch": "BR-JED-002",
        "category": "Facility Maintenance",
        "description": "Facility maintenance retainer – Jeddah Corniche",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.95,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    # ── B. Partial-match / review-likely ───────────────────────────────
    {
        "num": 6,
        "tag": "2W-REPAIR-OCR-NOISE",
        "vendor_code": "V2W-003",
        "branch": "BR-RUH-003",
        "category": "Facility Maintenance",
        "description": "Branch repair service invoice – OCR PO noise",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.72,
        "po_noise": "swap_digit",  # last 2 digits swapped on PO
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    {
        "num": 7,
        "tag": "2W-HVAC-SURCHARGE",
        "vendor_code": "V2W-001",
        "branch": "BR-DMM-002",
        "category": "HVAC Maintenance",
        "description": "Annual maintenance invoice with surcharge – Dhahran",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.89,
        "po_noise": None,
        "amount_delta": Decimal("475.00"),  # surcharge on invoice
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    {
        "num": 8,
        "tag": "2W-CONSULTING-MILESTONE",
        "vendor_code": "V2W-009",
        "branch": "BR-RUH-001",
        "category": "Consulting Services",
        "description": "Operations consulting milestone invoice – Riyadh",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.91,
        "po_noise": None,
        "amount_delta": Decimal("-1250.00"),  # invoice slightly less than PO
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-6010",
    },
    {
        "num": 9,
        "tag": "2W-EMERGENCY-CALLOUT",
        "vendor_code": "V2W-006",
        "branch": "BR-JED-001",
        "category": "Kitchen Equipment Service",
        "description": "Emergency kitchen equipment service – Jeddah Tahlia",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.78,
        "po_noise": None,
        "amount_delta": Decimal("600.00"),  # emergency premium
        "tax_override": Decimal("0.05"),  # wrong VAT rate
        "missing_fields": ["cost_center"],
        "is_duplicate": False,
        "cost_center": "",  # intentionally blank
    },
    {
        "num": 10,
        "tag": "2W-FIRESAFETY-TAX-MISMATCH",
        "vendor_code": "V2W-008",
        "branch": "BR-RUH-002",
        "category": "Fire Safety Services",
        "description": "Fire safety inspection service – Riyadh King Fahd",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.88,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": Decimal("0.10"),  # 10% instead of 15%
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-3010",
    },
    # ── C. Fail-likely / exception-prone ───────────────────────────────
    {
        "num": 11,
        "tag": "2W-CONSULTING-BAD-PO",
        "vendor_code": "V2W-009",
        "branch": "BR-RUH-001",
        "category": "Consulting Services",
        "description": "Consulting invoice with malformed PO reference",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.65,
        "po_noise": "malformed",  # PO ref is corrupted
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-6010",
    },
    {
        "num": 12,
        "tag": "2W-FACILITY-DUPLICATE",
        "vendor_code": "V2W-003",
        "branch": "BR-JED-002",
        "category": "Facility Maintenance",
        "description": "Duplicate facility services invoice – same vendor/amount",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.92,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": True,     # intentional duplicate of scenario 5
        "duplicate_of_num": 5,
        "cost_center": "CC-3010",
    },
    {
        "num": 13,
        "tag": "2W-LOGISTICS-CORRUPTED",
        "vendor_code": "V2W-007",
        "branch": "BR-DMM-001",
        "category": "Cold Chain Logistics",
        "description": "Imported invoice with corrupted PO field – logistics",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.55,
        "po_noise": "missing",  # PO field is empty
        "amount_delta": Decimal("0"),
        "tax_override": None,
        "missing_fields": ["po_number", "currency"],
        "is_duplicate": False,
        "cost_center": "CC-2010",
    },
    {
        "num": 14,
        "tag": "2W-HIGHVALUE-CONSULTING",
        "vendor_code": "V2W-009",
        "branch": "BR-RUH-001",
        "category": "Consulting Services",
        "description": "High-value consulting engagement invoice – Riyadh HQ",
        "invoice_status": InvoiceStatus.READY_FOR_RECON,
        "confidence": 0.90,
        "po_noise": None,
        "amount_delta": Decimal("5200.00"),  # over PO value
        "tax_override": None,
        "missing_fields": [],
        "is_duplicate": False,
        "cost_center": "CC-6010",
    },
    {
        "num": 15,
        "tag": "2W-ADMIN-MISSING-TAX",
        "vendor_code": "V2W-010",
        "branch": "BR-JED-001",
        "category": "Office Admin Services",
        "description": "Office admin invoice – missing tax and cost center",
        "invoice_status": InvoiceStatus.EXTRACTED,
        "confidence": 0.70,
        "po_noise": None,
        "amount_delta": Decimal("0"),
        "tax_override": "missing",  # tax field blank
        "missing_fields": ["tax_amount", "cost_center"],
        "is_duplicate": False,
        "cost_center": "",
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
    offset = (scenario_num * 7) % 180
    return base - timedelta(days=offset)


def _pick_items(category: str, count: int) -> list[dict]:
    """Pick service line items from catalog."""
    pool = SERVICE_ITEMS_CATALOG.get(category, [])
    if not pool:
        pool = [{"desc": f"{category} – service", "uom": "EA", "price": 2500.00}]
    count = min(count, len(pool))
    return _rng.sample(pool, count)


def _apply_po_noise(po_number: str, noise_type: str | None) -> str:
    """Apply OCR noise to a PO number for testing."""
    if noise_type == "swap_digit" and len(po_number) >= 4:
        # Swap last two digits
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
    cost_center: str,
) -> dict:
    """Build a realistic raw extraction JSON payload."""
    return {
        "extraction_engine": "azure_document_intelligence",
        "extraction_model": "prebuilt-invoice",
        "overall_confidence": confidence,
        "header": {
            "invoice_number": {"value": invoice_number, "confidence": confidence},
            "vendor_name": {"value": vendor_name, "confidence": confidence - 0.02},
            "invoice_date": {"value": str(invoice_date), "confidence": confidence},
            "due_date": {
                "value": str(invoice_date + timedelta(days=30)),
                "confidence": confidence - 0.05,
            },
            "po_number": {
                "value": po_number,
                "confidence": max(confidence - 0.08, 0.40),
            },
            "currency": {"value": currency, "confidence": 0.99},
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
            "vat_rate_detected": "15%" if tax_amount is not None else None,
        },
        "service_period": {
            "text": f"Service period for {description}",
            "start_date": str(invoice_date - timedelta(days=30)),
            "end_date": str(invoice_date),
        },
        "line_items": [
            {
                "line_number": idx + 1,
                "description": {"value": li["desc"], "confidence": confidence - 0.01},
                "quantity": {"value": str(li["quantity"]), "confidence": confidence},
                "unit_price": {"value": str(li["unit_price"]), "confidence": confidence},
                "line_amount": {"value": str(li["line_amount"]), "confidence": confidence},
            }
            for idx, li in enumerate(line_items)
        ],
        "branch_code": branch_code,
        "cost_center": cost_center,
        "detected_language": "en",
        "page_count": 1,
    }


# ============================================================================
# Core creation helpers
# ============================================================================

def create_vendors(admin: User) -> dict[str, Vendor]:
    """Create or reuse TWO_WAY service vendors."""
    vendors: dict[str, Vendor] = {}
    for v in TWO_WAY_VENDORS:
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
    """Create vendor aliases for all TWO_WAY vendors."""
    total = 0
    for v_data in TWO_WAY_VENDORS:
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
    """Create a PO + line items. Returns (po, po_lines)."""
    po_num = f"PO-2W-{scenario['num']:04d}"
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
                "item_code": f"SVC-{scenario['num']:04d}-{idx:02d}",
                "description": item["desc"],
                "quantity": Decimal(str(qty)),
                "unit_price": unit_price,
                "tax_amount": line_tax,
                "line_amount": line_amount,
                "unit_of_measure": item["uom"],
                "is_service_item": True,
                "is_stock_item": False,
                "item_category": scenario["category"],
            },
        )
        po_lines.append(pl)
    return po, po_lines


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
    Create an Invoice + line items + DocumentUpload stub for a scenario.
    Applies amount deltas, tax overrides, PO noise, and missing fields.
    """
    sc_num = scenario["num"]
    inv_num = f"INV-2W-{sc_num:04d}"
    inv_date = _base_date(sc_num)
    due_date = inv_date + timedelta(days=30)
    confidence = scenario["confidence"]
    branch_code = scenario["branch"]
    branch = next((b for b in BRANCHES if b["code"] == branch_code), None)
    city = branch["city"] if branch else "Riyadh"

    # ── Compute invoice line amounts ─────────────────────────
    inv_lines_data = []
    for idx, (item, qty) in enumerate(zip(line_items, quantities)):
        unit_price = _d(item["price"])
        quantity = Decimal(str(qty))
        line_amount = (unit_price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        inv_lines_data.append({
            "desc": item["desc"],
            "uom": item["uom"],
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
        })

    subtotal = sum(d["line_amount"] for d in inv_lines_data)

    # Apply amount delta (surcharge / shortfall) — distribute to the FIRST
    # line item so that line-level matching detects the discrepancy too,
    # not just the header total.
    amount_delta = scenario.get("amount_delta", Decimal("0"))
    if amount_delta:
        inv_lines_data[0]["line_amount"] = (
            inv_lines_data[0]["line_amount"] + amount_delta
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # Adjust unit price to stay consistent with qty × price = amount
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

    # Vendor name as seen by OCR
    raw_vendor_name = vendor.name
    # For one scenario, use Arabic alias as the OCR-extracted name
    if scenario["confidence"] < 0.75 and vendor.aliases.exists():
        arabic_aliases = [a.alias_name for a in vendor.aliases.all() if any(
            "\u0600" <= c <= "\u06FF" for c in a.alias_name
        )]
        if arabic_aliases:
            raw_vendor_name = arabic_aliases[0]

    # DocumentUpload stub
    upload_filename = f"INV_{vendor.code}_{inv_date.strftime('%Y%m%d')}_{sc_num:04d}.pdf"
    doc_upload, _ = DocumentUpload.objects.get_or_create(
        original_filename=upload_filename,
        document_type=DocumentType.INVOICE,
        defaults={
            "file_size": _rng.randint(80_000, 500_000),
            "content_type": "application/pdf",
            "processing_state": FileProcessingState.COMPLETED,
            "processing_message": "Extraction completed",
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
    # If invoice already existed but has no line items (e.g. after partial reset),
    # clear and recreate them.
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
                "is_service_item": True,
                "is_stock_item": False,
            },
        )
        inv_line_objs.append(il)

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
    """Generate additional random TWO_WAY scenarios for QA/large modes."""
    rng = random.Random(rand_seed)
    vendor_codes = list(vendors.keys())
    stats = {"invoices": 0, "pos": 0, "duplicates": 0, "malformed_po": 0}

    for i in range(count):
        sc_num = start_num + i
        v_code = rng.choice(vendor_codes)
        vendor = vendors[v_code]
        v_data = next((v for v in TWO_WAY_VENDORS if v["code"] == v_code), TWO_WAY_VENDORS[0])
        category = v_data["category"]
        branch = rng.choice(BRANCHES)

        # Random characteristics
        confidence = round(rng.uniform(0.50, 0.98), 2)
        po_noise = rng.choices(
            [None, "swap_digit", "malformed", "missing"],
            weights=[70, 12, 10, 8],
        )[0]
        amount_delta = Decimal(str(rng.choice([0, 0, 0, 75, -120, 300, 500, -250, 1200])))
        tax_choice = rng.choices(
            [None, Decimal("0.05"), Decimal("0.10"), "missing"],
            weights=[75, 10, 8, 7],
        )[0]
        is_dup = rng.random() < 0.08
        cost_center = rng.choice(COST_CENTERS + [""])

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
            "tag": f"2W-GEN-{sc_num:04d}",
            "vendor_code": v_code,
            "branch": branch["code"],
            "category": category,
            "description": f"{category} service – {branch['city']} ({branch['name']})",
            "invoice_status": inv_status,
            "confidence": confidence,
            "po_noise": po_noise,
            "amount_delta": amount_delta,
            "tax_override": tax_choice,
            "missing_fields": [],
            "is_duplicate": is_dup,
            "cost_center": cost_center,
        }

        # Build missing fields list
        if tax_choice == "missing":
            scenario["missing_fields"].append("tax_amount")
        if po_noise == "missing":
            scenario["missing_fields"].append("po_number")
        if cost_center == "":
            scenario["missing_fields"].append("cost_center")

        # Create PO (unless PO is missing)
        n_lines = rng.choice([1, 2, 3])
        items = _pick_items(category, n_lines)
        quantities = [rng.randint(1, 12) for _ in items]

        po, po_lines = None, []
        if po_noise != "missing":
            po, po_lines = create_po_for_scenario(scenario, vendor, admin, items, quantities)
            stats["pos"] += 1

        invoice, _ = create_invoice_for_scenario(
            scenario, vendor, admin, po, po_lines, items, quantities,
        )
        stats["invoices"] += 1
        if is_dup:
            stats["duplicates"] += 1
        if po_noise == "malformed":
            stats["malformed_po"] += 1

    return stats


# ============================================================================
# Command
# ============================================================================

class Command(BaseCommand):
    help = "Seed TWO_WAY PO-backed invoice data for McDonald's Saudi Arabia demos/QA"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            type=str,
            default="demo",
            choices=["demo", "qa", "large"],
            help="Seed mode: demo (15), qa (+10), large (+30)",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete previously seeded TWO_WAY data before re-creating",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for generated scenarios (default: 42)",
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
            f"\n{'='*64}\n"
            f"  McDonald's KSA – TWO_WAY Invoice Seed Data\n"
            f"  Mode: {mode.upper()} | Reset: {do_reset} | Seed: {rand_seed}\n"
            f"{'='*64}\n"
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
    # Reset — only deletes 2W-prefixed records
    # ----------------------------------------------------------------

    def _reset_data(self):
        self.stdout.write(self.style.WARNING("  Resetting seeded TWO_WAY data..."))

        # Invoices & lines
        inv_qs = Invoice.objects.filter(invoice_number__startswith="INV-2W-")
        InvoiceLineItem.objects.filter(invoice__in=inv_qs).delete()
        # DocumentUploads linked to these invoices
        upload_ids = list(inv_qs.values_list("document_upload_id", flat=True))
        inv_qs.delete()
        DocumentUpload.objects.filter(id__in=[uid for uid in upload_ids if uid]).delete()

        # POs & lines
        po_qs = PurchaseOrder.objects.filter(po_number__startswith="PO-2W-")
        PurchaseOrderLineItem.objects.filter(purchase_order__in=po_qs).delete()
        po_qs.delete()

        # Vendors & aliases (only 2W-prefixed)
        v_qs = Vendor.objects.filter(code__startswith="V2W-")
        VendorAlias.objects.filter(vendor__in=v_qs).delete()
        v_qs.delete()

        self.stdout.write(self.style.SUCCESS("  Reset complete."))

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
        self.stdout.write("  [1/4] Creating TWO_WAY service vendors...")
        vendors = create_vendors(admin)
        n_aliases = create_vendor_aliases(vendors, admin)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(vendors)} vendors, {n_aliases} aliases"
        ))

        # 3. Deterministic scenarios
        self.stdout.write("  [2/4] Creating POs & Invoices (15 scenarios)...")
        invoices_created = {}
        stats = {
            "vendors": len(vendors),
            "aliases": n_aliases,
            "invoices": 0,
            "pos": 0,
            "duplicates": 0,
            "malformed_po": 0,
            "high_value": 0,
            "incomplete": 0,
        }

        for sc in SCENARIOS:
            vendor = vendors[sc["vendor_code"]]
            n_lines = _rng.choice([2, 3])
            items = _pick_items(sc["category"], n_lines)
            quantities = [_rng.randint(1, 8) for _ in items]

            # Create PO (unless PO is intentionally missing)
            po, po_lines = None, []
            if sc.get("po_noise") != "missing":
                po, po_lines = create_po_for_scenario(sc, vendor, admin, items, quantities)
                stats["pos"] += 1

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

            if sc.get("is_duplicate"):
                stats["duplicates"] += 1
            if sc.get("po_noise") in ("malformed", "missing"):
                stats["malformed_po"] += 1
            if invoice.total_amount and invoice.total_amount > Decimal("50000"):
                stats["high_value"] += 1
            if sc.get("missing_fields"):
                stats["incomplete"] += 1

        self.stdout.write(self.style.SUCCESS(
            f"        {stats['invoices']} invoices, {stats['pos']} POs created"
        ))

        # 4. Bulk generated scenarios for qa/large
        if mode in ("qa", "large"):
            extra = 10 if mode == "qa" else 30
            self.stdout.write(f"  [3/4] Generating {extra} additional random scenarios...")
            bulk_stats = _generate_random_scenarios(
                start_num=16,
                count=extra,
                vendors=vendors,
                admin=admin,
                rand_seed=rand_seed,
            )
            stats["invoices"] += bulk_stats["invoices"]
            stats["pos"] += bulk_stats["pos"]
            stats["duplicates"] += bulk_stats["duplicates"]
            stats["malformed_po"] += bulk_stats["malformed_po"]
            self.stdout.write(self.style.SUCCESS(
                f"        {bulk_stats['invoices']} additional invoices created"
            ))
        else:
            self.stdout.write("  [3/4] Skipping bulk generation (demo mode)")

        # 4. Summary stats
        self.stdout.write("  [4/4] Seed statistics:")
        self._print_stats(stats)

    # ----------------------------------------------------------------
    # Stats
    # ----------------------------------------------------------------

    def _print_stats(self, stats: dict):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n  {'─'*50}\n"
            f"  SEED SUMMARY\n"
            f"  {'─'*50}"
        ))
        self.stdout.write(f"  Vendors created/reused:       {stats['vendors']}")
        self.stdout.write(f"  Vendor aliases:               {stats['aliases']}")
        self.stdout.write(f"  Purchase Orders:              {stats['pos']}")
        self.stdout.write(f"  Invoices created:             {stats['invoices']}")
        self.stdout.write(f"  ├─ Duplicate-prone:           {stats['duplicates']}")
        self.stdout.write(f"  ├─ Malformed PO refs:         {stats['malformed_po']}")
        self.stdout.write(f"  ├─ High-value (>50k SAR):     {stats['high_value']}")
        self.stdout.write(f"  └─ Incomplete fields:         {stats['incomplete']}")

    # ----------------------------------------------------------------
    # Invoice summary table
    # ----------------------------------------------------------------

    def _print_summary(self):
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'='*120}\n"
            f"  SEEDED TWO_WAY INVOICE SUMMARY\n"
            f"{'='*120}"
        ))
        self.stdout.write(
            f"{'#':>3} {'Invoice':14} {'Vendor':32} {'PO Ref':18} "
            f"{'Total':>12} {'Status':20} {'Conf':>5} {'Flags':20}"
        )
        self.stdout.write("─" * 120)

        invoices = (
            Invoice.objects
            .filter(invoice_number__startswith="INV-2W-")
            .select_related("vendor")
            .order_by("invoice_number")
        )

        for inv in invoices:
            vendor_name = inv.vendor.name[:30] if inv.vendor else inv.raw_vendor_name[:30]
            po_ref = inv.po_number or "(none)"
            total = f"SAR {inv.total_amount:,.2f}" if inv.total_amount else "N/A"
            conf = f"{inv.extraction_confidence:.2f}" if inv.extraction_confidence else "N/A"

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

            num = inv.invoice_number.replace("INV-2W-", "")
            self.stdout.write(
                f"{num:>3} {inv.invoice_number:14} {vendor_name:32} {po_ref:18} "
                f"{total:>12} {inv.status:20} {conf:>5} {', '.join(flags) or '—':20}"
            )

        self.stdout.write(f"\nTotal: {invoices.count()} invoices")

        # Status distribution
        from django.db.models import Count
        self.stdout.write(self.style.MIGRATE_HEADING("\n  Distribution:"))
        by_status = (
            Invoice.objects
            .filter(invoice_number__startswith="INV-2W-")
            .values("status")
            .annotate(c=Count("id"))
            .order_by("-c")
        )
        self.stdout.write(
            "  By Status: " + "  |  ".join(f"{r['status']}: {r['c']}" for r in by_status)
        )
