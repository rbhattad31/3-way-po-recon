"""
Management command to seed the database with realistic PO, GRN, Vendor,
and ReconciliationConfig data for development and demo purposes.

Usage:
    python manage.py seed_data          # seed all data
    python manage.py seed_data --flush  # clear existing seed data first
    python manage.py seed_data --only vendors
    python manage.py seed_data --only pos
    python manage.py seed_data --only grns
    python manage.py seed_data --only config
    python manage.py seed_data --only invoices
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.agents.models import AgentDefinition
from apps.auditlog.models import AuditEvent
from apps.core.enums import AgentType, AuditEventType
from apps.tools.models import ToolDefinition
from apps.documents.models import (
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.reconciliation.models import ReconciliationConfig
from apps.vendors.models import Vendor, VendorAlias


# ───────────────────────────────────────────────────────────────────────
# Vendor seed data
# ───────────────────────────────────────────────────────────────────────
VENDORS = [
    {
        "code": "V-1001",
        "name": "Acme Industrial Supplies",
        "normalized_name": "acme industrial supplies",
        "tax_id": "US-EIN-12-3456789",
        "address": "100 Commerce Blvd, Chicago, IL 60601",
        "country": "US",
        "currency": "USD",
        "payment_terms": "Net 30",
        "contact_email": "billing@acmeindustrial.com",
        "aliases": [
            {"alias_name": "Acme Industries", "normalized_alias": "acme industries", "source": "extraction"},
            {"alias_name": "ACME IND. SUPPLIES", "normalized_alias": "acme ind supplies", "source": "extraction"},
            {"alias_name": "Acme Ind Supplies Inc", "normalized_alias": "acme ind supplies inc", "source": "manual"},
        ],
    },
    {
        "code": "V-1002",
        "name": "Global Tech Solutions",
        "normalized_name": "global tech solutions",
        "tax_id": "US-EIN-98-7654321",
        "address": "500 Innovation Dr, San Jose, CA 95112",
        "country": "US",
        "currency": "USD",
        "payment_terms": "Net 45",
        "contact_email": "ap@globaltechsol.com",
        "aliases": [
            {"alias_name": "Global Tech Sol", "normalized_alias": "global tech sol", "source": "extraction"},
            {"alias_name": "GlobalTech Solutions Inc", "normalized_alias": "globaltech solutions inc", "source": "erp"},
        ],
    },
    {
        "code": "V-1003",
        "name": "Precision Parts Manufacturing",
        "normalized_name": "precision parts manufacturing",
        "tax_id": "US-EIN-45-1234567",
        "address": "2200 Factory Ln, Detroit, MI 48201",
        "country": "US",
        "currency": "USD",
        "payment_terms": "Net 30",
        "contact_email": "invoices@precisionparts.com",
        "aliases": [
            {"alias_name": "Precision Parts Mfg", "normalized_alias": "precision parts mfg", "source": "manual"},
            {"alias_name": "PPM Inc", "normalized_alias": "ppm inc", "source": "extraction"},
        ],
    },
    {
        "code": "V-1004",
        "name": "Euro Logistics GmbH",
        "normalized_name": "euro logistics gmbh",
        "tax_id": "DE-VAT-DE123456789",
        "address": "Industriestraße 42, 80339 München, Germany",
        "country": "DE",
        "currency": "EUR",
        "payment_terms": "Net 60",
        "contact_email": "rechnungen@eurologistics.de",
        "aliases": [
            {"alias_name": "Euro Logistics", "normalized_alias": "euro logistics", "source": "extraction"},
            {"alias_name": "EuroLog GmbH", "normalized_alias": "eurolog gmbh", "source": "manual"},
        ],
    },
    {
        "code": "V-1005",
        "name": "Pacific Rim Electronics",
        "normalized_name": "pacific rim electronics",
        "tax_id": "SG-UEN-202012345A",
        "address": "88 Science Park Drive, Singapore 118261",
        "country": "SG",
        "currency": "USD",
        "payment_terms": "Net 30",
        "contact_email": "finance@pacificrimelec.sg",
        "aliases": [
            {"alias_name": "Pacific Rim Elec", "normalized_alias": "pacific rim elec", "source": "extraction"},
            {"alias_name": "PR Electronics Pte Ltd", "normalized_alias": "pr electronics pte ltd", "source": "erp"},
        ],
    },
    {
        "code": "V-1006",
        "name": "Summit Office Products",
        "normalized_name": "summit office products",
        "tax_id": "US-EIN-33-5678901",
        "address": "750 Corporate Center, Dallas, TX 75201",
        "country": "US",
        "currency": "USD",
        "payment_terms": "Net 15",
        "contact_email": "orders@summitoffice.com",
        "aliases": [
            {"alias_name": "Summit Office Prod.", "normalized_alias": "summit office prod", "source": "extraction"},
        ],
    },
    {
        "code": "V-1007",
        "name": "Northern Chemical Corp",
        "normalized_name": "northern chemical corp",
        "tax_id": "CA-BN-123456789RC0001",
        "address": "1400 Industrial Pkwy, Toronto, ON M3C 1H9, Canada",
        "country": "CA",
        "currency": "USD",
        "payment_terms": "Net 30",
        "contact_email": "ar@northernchemical.ca",
        "aliases": [
            {"alias_name": "Northern Chem Corp", "normalized_alias": "northern chem corp", "source": "extraction"},
            {"alias_name": "NCC Canada", "normalized_alias": "ncc canada", "source": "manual"},
        ],
    },
    {
        "code": "V-1008",
        "name": "BlueSky IT Services",
        "normalized_name": "bluesky it services",
        "tax_id": "US-EIN-77-8901234",
        "address": "900 Cloud Ave, Seattle, WA 98101",
        "country": "US",
        "currency": "USD",
        "payment_terms": "Net 30",
        "contact_email": "billing@blueskyit.com",
        "aliases": [
            {"alias_name": "Blue Sky IT", "normalized_alias": "blue sky it", "source": "extraction"},
            {"alias_name": "BlueSky Information Technology Services", "normalized_alias": "bluesky information technology services", "source": "erp"},
        ],
    },
]


# ───────────────────────────────────────────────────────────────────────
# Purchase Order seed data (with line items)
# ───────────────────────────────────────────────────────────────────────
PURCHASE_ORDERS = [
    # PO-1: Fully receivable, perfect match scenario
    {
        "po_number": "PO-2025-0001",
        "normalized_po_number": "PO20250001",
        "vendor_code": "V-1001",
        "po_date": date(2025, 9, 15),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "John Martinez",
        "department": "Manufacturing",
        "notes": "Standard quarterly supply order for production floor",
        "lines": [
            {"line_number": 1, "item_code": "ACM-BRG-100", "description": "Ball Bearing Assembly 100mm", "quantity": Decimal("200"), "unit_price": Decimal("45.00"), "tax_amount": Decimal("810.00"), "line_amount": Decimal("9000.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "ACM-BLT-050", "description": "Industrial Belt Drive 50cm", "quantity": Decimal("100"), "unit_price": Decimal("32.50"), "tax_amount": Decimal("292.50"), "line_amount": Decimal("3250.00"), "unit_of_measure": "EA"},
            {"line_number": 3, "item_code": "ACM-LUB-005", "description": "High-Temp Lubricant 5L Can", "quantity": Decimal("50"), "unit_price": Decimal("28.00"), "tax_amount": Decimal("126.00"), "line_amount": Decimal("1400.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-2: Partial receipt scenario
    {
        "po_number": "PO-2025-0002",
        "normalized_po_number": "PO20250002",
        "vendor_code": "V-1002",
        "po_date": date(2025, 10, 1),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "Sarah Chen",
        "department": "IT",
        "notes": "IT equipment refresh Q4 2025",
        "lines": [
            {"line_number": 1, "item_code": "GT-LAP-PRO", "description": "Business Laptop Pro 15-inch", "quantity": Decimal("25"), "unit_price": Decimal("1450.00"), "tax_amount": Decimal("3262.50"), "line_amount": Decimal("36250.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "GT-MON-27", "description": "27-inch 4K Monitor", "quantity": Decimal("25"), "unit_price": Decimal("520.00"), "tax_amount": Decimal("1170.00"), "line_amount": Decimal("13000.00"), "unit_of_measure": "EA"},
            {"line_number": 3, "item_code": "GT-DOC-STN", "description": "USB-C Docking Station", "quantity": Decimal("25"), "unit_price": Decimal("185.00"), "tax_amount": Decimal("416.25"), "line_amount": Decimal("4625.00"), "unit_of_measure": "EA"},
            {"line_number": 4, "item_code": "GT-KBM-SET", "description": "Wireless Keyboard & Mouse Set", "quantity": Decimal("25"), "unit_price": Decimal("75.00"), "tax_amount": Decimal("168.75"), "line_amount": Decimal("1875.00"), "unit_of_measure": "SET"},
        ],
    },
    # PO-3: Quantity mismatch scenario
    {
        "po_number": "PO-2025-0003",
        "normalized_po_number": "PO20250003",
        "vendor_code": "V-1003",
        "po_date": date(2025, 8, 20),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "Mike Thompson",
        "department": "Engineering",
        "notes": "Precision machined parts for Q3 assembly line upgrade",
        "lines": [
            {"line_number": 1, "item_code": "PP-SHF-200", "description": "Precision Steel Shaft 200mm", "quantity": Decimal("500"), "unit_price": Decimal("18.75"), "tax_amount": Decimal("843.75"), "line_amount": Decimal("9375.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "PP-GR-SET", "description": "Gear Assembly Set A", "quantity": Decimal("100"), "unit_price": Decimal("125.00"), "tax_amount": Decimal("1125.00"), "line_amount": Decimal("12500.00"), "unit_of_measure": "SET"},
            {"line_number": 3, "item_code": "PP-BRK-010", "description": "Brake Pad Assembly 10mm", "quantity": Decimal("300"), "unit_price": Decimal("22.00"), "tax_amount": Decimal("594.00"), "line_amount": Decimal("6600.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-4: EUR currency, international vendor
    {
        "po_number": "PO-2025-0004",
        "normalized_po_number": "PO20250004",
        "vendor_code": "V-1004",
        "po_date": date(2025, 11, 5),
        "currency": "EUR",
        "status": "OPEN",
        "buyer_name": "Anna Fischer",
        "department": "Supply Chain",
        "notes": "European logistics and warehouse supplies",
        "lines": [
            {"line_number": 1, "item_code": "EL-PLT-EU", "description": "Euro Pallet Standard 1200x800mm", "quantity": Decimal("200"), "unit_price": Decimal("25.00"), "tax_amount": Decimal("950.00"), "line_amount": Decimal("5000.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "EL-WRP-500", "description": "Stretch Wrap Film 500mm Roll", "quantity": Decimal("150"), "unit_price": Decimal("12.50"), "tax_amount": Decimal("356.25"), "line_amount": Decimal("1875.00"), "unit_of_measure": "ROL"},
            {"line_number": 3, "item_code": "EL-LBL-THM", "description": "Thermal Shipping Labels 100x150mm", "quantity": Decimal("5000"), "unit_price": Decimal("0.08"), "tax_amount": Decimal("76.00"), "line_amount": Decimal("400.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-5: Electronics - fully received, matched
    {
        "po_number": "PO-2025-0005",
        "normalized_po_number": "PO20250005",
        "vendor_code": "V-1005",
        "po_date": date(2025, 7, 10),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "David Park",
        "department": "R&D",
        "notes": "Electronic components for prototype development",
        "lines": [
            {"line_number": 1, "item_code": "PR-MCU-ARM", "description": "ARM Cortex-M4 Microcontroller Board", "quantity": Decimal("50"), "unit_price": Decimal("35.00"), "tax_amount": Decimal("157.50"), "line_amount": Decimal("1750.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "PR-SEN-TMP", "description": "Temperature Sensor Module", "quantity": Decimal("100"), "unit_price": Decimal("8.50"), "tax_amount": Decimal("76.50"), "line_amount": Decimal("850.00"), "unit_of_measure": "EA"},
            {"line_number": 3, "item_code": "PR-CAP-100", "description": "Capacitor Pack 100uF (50 pcs)", "quantity": Decimal("20"), "unit_price": Decimal("12.00"), "tax_amount": Decimal("21.60"), "line_amount": Decimal("240.00"), "unit_of_measure": "PKG"},
            {"line_number": 4, "item_code": "PR-PCB-CUS", "description": "Custom PCB Fabrication", "quantity": Decimal("30"), "unit_price": Decimal("45.00"), "tax_amount": Decimal("121.50"), "line_amount": Decimal("1350.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-6: Office supplies - small value, fully received
    {
        "po_number": "PO-2025-0006",
        "normalized_po_number": "PO20250006",
        "vendor_code": "V-1006",
        "po_date": date(2025, 10, 20),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "Lisa Wang",
        "department": "Admin",
        "notes": "Monthly office supply replenishment",
        "lines": [
            {"line_number": 1, "item_code": "SO-PPR-A4", "description": "A4 Copy Paper Ream (500 sheets)", "quantity": Decimal("100"), "unit_price": Decimal("4.50"), "tax_amount": Decimal("40.50"), "line_amount": Decimal("450.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "SO-TNR-BLK", "description": "Laser Printer Toner Black", "quantity": Decimal("10"), "unit_price": Decimal("85.00"), "tax_amount": Decimal("76.50"), "line_amount": Decimal("850.00"), "unit_of_measure": "EA"},
            {"line_number": 3, "item_code": "SO-PEN-BOX", "description": "Ballpoint Pen Box (50 pcs)", "quantity": Decimal("5"), "unit_price": Decimal("18.00"), "tax_amount": Decimal("8.10"), "line_amount": Decimal("90.00"), "unit_of_measure": "BOX"},
        ],
    },
    # PO-7: Chemicals - price mismatch scenario
    {
        "po_number": "PO-2025-0007",
        "normalized_po_number": "PO20250007",
        "vendor_code": "V-1007",
        "po_date": date(2025, 9, 1),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "Robert Kim",
        "department": "Production",
        "notes": "Chemical supplies for production line cleaning",
        "lines": [
            {"line_number": 1, "item_code": "NC-SOL-IPA", "description": "Isopropyl Alcohol 99% 20L Drum", "quantity": Decimal("20"), "unit_price": Decimal("95.00"), "tax_amount": Decimal("171.00"), "line_amount": Decimal("1900.00"), "unit_of_measure": "DRM"},
            {"line_number": 2, "item_code": "NC-DGR-HVY", "description": "Heavy Duty Degreaser 10L", "quantity": Decimal("30"), "unit_price": Decimal("42.00"), "tax_amount": Decimal("113.40"), "line_amount": Decimal("1260.00"), "unit_of_measure": "EA"},
            {"line_number": 3, "item_code": "NC-CLN-ABC", "description": "All-Purpose Surface Cleaner 5L", "quantity": Decimal("50"), "unit_price": Decimal("15.00"), "tax_amount": Decimal("67.50"), "line_amount": Decimal("750.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-8: IT services - two GRN scenario
    {
        "po_number": "PO-2025-0008",
        "normalized_po_number": "PO20250008",
        "vendor_code": "V-1008",
        "po_date": date(2025, 6, 15),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "Emily Johnson",
        "department": "IT",
        "notes": "Annual IT maintenance and cloud services",
        "lines": [
            {"line_number": 1, "item_code": "BS-CLD-ENT", "description": "Enterprise Cloud Hosting (Annual)", "quantity": Decimal("1"), "unit_price": Decimal("24000.00"), "tax_amount": Decimal("2160.00"), "line_amount": Decimal("24000.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "BS-SUP-PRM", "description": "Premium Support Package", "quantity": Decimal("1"), "unit_price": Decimal("8500.00"), "tax_amount": Decimal("765.00"), "line_amount": Decimal("8500.00"), "unit_of_measure": "EA"},
            {"line_number": 3, "item_code": "BS-SEC-ADV", "description": "Advanced Security Suite License", "quantity": Decimal("50"), "unit_price": Decimal("120.00"), "tax_amount": Decimal("540.00"), "line_amount": Decimal("6000.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-9: Multi-GRN, partial delivery scenario
    {
        "po_number": "PO-2025-0009",
        "normalized_po_number": "PO20250009",
        "vendor_code": "V-1001",
        "po_date": date(2025, 10, 10),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "John Martinez",
        "department": "Manufacturing",
        "notes": "Urgent restock of fasteners and fittings",
        "lines": [
            {"line_number": 1, "item_code": "ACM-FST-M8", "description": "M8 Hex Bolt Grade 8.8 (Box of 100)", "quantity": Decimal("50"), "unit_price": Decimal("28.00"), "tax_amount": Decimal("126.00"), "line_amount": Decimal("1400.00"), "unit_of_measure": "BOX"},
            {"line_number": 2, "item_code": "ACM-NUT-M8", "description": "M8 Hex Nut Grade 8 (Box of 100)", "quantity": Decimal("50"), "unit_price": Decimal("14.00"), "tax_amount": Decimal("63.00"), "line_amount": Decimal("700.00"), "unit_of_measure": "BOX"},
            {"line_number": 3, "item_code": "ACM-WSH-M8", "description": "M8 Flat Washer SS (Box of 200)", "quantity": Decimal("30"), "unit_price": Decimal("18.50"), "tax_amount": Decimal("49.95"), "line_amount": Decimal("555.00"), "unit_of_measure": "BOX"},
            {"line_number": 4, "item_code": "ACM-FIT-QC", "description": "Quick-Connect Fitting 1/2 inch", "quantity": Decimal("200"), "unit_price": Decimal("6.75"), "tax_amount": Decimal("121.50"), "line_amount": Decimal("1350.00"), "unit_of_measure": "EA"},
        ],
    },
    # PO-10: Vendor mismatch scenario (invoice comes from alias)
    {
        "po_number": "PO-2025-0010",
        "normalized_po_number": "PO20250010",
        "vendor_code": "V-1003",
        "po_date": date(2025, 11, 1),
        "currency": "USD",
        "status": "OPEN",
        "buyer_name": "Mike Thompson",
        "department": "Engineering",
        "notes": "Replacement parts for CNC machine",
        "lines": [
            {"line_number": 1, "item_code": "PP-CNC-SPD", "description": "CNC Spindle Motor 5HP", "quantity": Decimal("2"), "unit_price": Decimal("2800.00"), "tax_amount": Decimal("504.00"), "line_amount": Decimal("5600.00"), "unit_of_measure": "EA"},
            {"line_number": 2, "item_code": "PP-CNC-COL", "description": "ER32 Collet Set (15 pcs)", "quantity": Decimal("5"), "unit_price": Decimal("320.00"), "tax_amount": Decimal("144.00"), "line_amount": Decimal("1600.00"), "unit_of_measure": "SET"},
            {"line_number": 3, "item_code": "PP-CNC-INS", "description": "Carbide Insert CNMG120408 (Box)", "quantity": Decimal("10"), "unit_price": Decimal("85.00"), "tax_amount": Decimal("76.50"), "line_amount": Decimal("850.00"), "unit_of_measure": "BOX"},
        ],
    },
]


def _calc_po_totals(lines):
    """Calculate total_amount (grand total incl. tax) and tax_amount from lines."""
    subtotal = sum(l["line_amount"] for l in lines)
    tax = sum(l["tax_amount"] for l in lines)
    return subtotal + tax, tax


# ───────────────────────────────────────────────────────────────────────
# GRN seed data
# ───────────────────────────────────────────────────────────────────────
GOODS_RECEIPT_NOTES = [
    # GRN-1: Full receipt for PO-2025-0001 (perfect match)
    {
        "grn_number": "GRN-2025-0001",
        "po_number": "PO-2025-0001",
        "receipt_date": date(2025, 9, 28),
        "status": "RECEIVED",
        "warehouse": "Warehouse A - Chicago",
        "receiver_name": "Carlos Ramirez",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "ACM-BRG-100", "description": "Ball Bearing Assembly 100mm", "quantity_received": Decimal("200"), "quantity_accepted": Decimal("200"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "ACM-BLT-050", "description": "Industrial Belt Drive 50cm", "quantity_received": Decimal("100"), "quantity_accepted": Decimal("100"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 3, "po_line_number": 3, "item_code": "ACM-LUB-005", "description": "High-Temp Lubricant 5L Can", "quantity_received": Decimal("50"), "quantity_accepted": Decimal("50"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-2: Partial receipt for PO-2025-0002 (only laptops & monitors)
    {
        "grn_number": "GRN-2025-0002",
        "po_number": "PO-2025-0002",
        "receipt_date": date(2025, 10, 15),
        "status": "RECEIVED",
        "warehouse": "IT Storage - HQ",
        "receiver_name": "James Liu",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "GT-LAP-PRO", "description": "Business Laptop Pro 15-inch", "quantity_received": Decimal("15"), "quantity_accepted": Decimal("15"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "GT-MON-27", "description": "27-inch 4K Monitor", "quantity_received": Decimal("20"), "quantity_accepted": Decimal("20"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-3: Second receipt for PO-2025-0002 (remaining items)
    {
        "grn_number": "GRN-2025-0003",
        "po_number": "PO-2025-0002",
        "receipt_date": date(2025, 10, 25),
        "status": "RECEIVED",
        "warehouse": "IT Storage - HQ",
        "receiver_name": "James Liu",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "GT-LAP-PRO", "description": "Business Laptop Pro 15-inch", "quantity_received": Decimal("10"), "quantity_accepted": Decimal("10"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "GT-MON-27", "description": "27-inch 4K Monitor", "quantity_received": Decimal("5"), "quantity_accepted": Decimal("5"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 3, "po_line_number": 3, "item_code": "GT-DOC-STN", "description": "USB-C Docking Station", "quantity_received": Decimal("25"), "quantity_accepted": Decimal("25"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 4, "po_line_number": 4, "item_code": "GT-KBM-SET", "description": "Wireless Keyboard & Mouse Set", "quantity_received": Decimal("25"), "quantity_accepted": Decimal("25"), "quantity_rejected": Decimal("0"), "unit_of_measure": "SET"},
        ],
    },
    # GRN-4: Full receipt for PO-2025-0003 but with some rejections
    {
        "grn_number": "GRN-2025-0004",
        "po_number": "PO-2025-0003",
        "receipt_date": date(2025, 9, 5),
        "status": "RECEIVED",
        "warehouse": "Warehouse B - Engineering",
        "receiver_name": "Tom Wilson",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "PP-SHF-200", "description": "Precision Steel Shaft 200mm", "quantity_received": Decimal("500"), "quantity_accepted": Decimal("490"), "quantity_rejected": Decimal("10"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "PP-GR-SET", "description": "Gear Assembly Set A", "quantity_received": Decimal("100"), "quantity_accepted": Decimal("98"), "quantity_rejected": Decimal("2"), "unit_of_measure": "SET"},
            {"line_number": 3, "po_line_number": 3, "item_code": "PP-BRK-010", "description": "Brake Pad Assembly 10mm", "quantity_received": Decimal("300"), "quantity_accepted": Decimal("300"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-5: Full receipt for PO-2025-0004 (EUR)
    {
        "grn_number": "GRN-2025-0005",
        "po_number": "PO-2025-0004",
        "receipt_date": date(2025, 11, 18),
        "status": "RECEIVED",
        "warehouse": "EU Warehouse - Munich",
        "receiver_name": "Hans Weber",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "EL-PLT-EU", "description": "Euro Pallet Standard 1200x800mm", "quantity_received": Decimal("200"), "quantity_accepted": Decimal("200"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "EL-WRP-500", "description": "Stretch Wrap Film 500mm Roll", "quantity_received": Decimal("150"), "quantity_accepted": Decimal("150"), "quantity_rejected": Decimal("0"), "unit_of_measure": "ROL"},
            {"line_number": 3, "po_line_number": 3, "item_code": "EL-LBL-THM", "description": "Thermal Shipping Labels 100x150mm", "quantity_received": Decimal("5000"), "quantity_accepted": Decimal("5000"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-6: Full receipt for PO-2025-0005
    {
        "grn_number": "GRN-2025-0006",
        "po_number": "PO-2025-0005",
        "receipt_date": date(2025, 7, 25),
        "status": "RECEIVED",
        "warehouse": "R&D Lab Storage",
        "receiver_name": "Amy Zhang",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "PR-MCU-ARM", "description": "ARM Cortex-M4 Microcontroller Board", "quantity_received": Decimal("50"), "quantity_accepted": Decimal("50"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "PR-SEN-TMP", "description": "Temperature Sensor Module", "quantity_received": Decimal("100"), "quantity_accepted": Decimal("100"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 3, "po_line_number": 3, "item_code": "PR-CAP-100", "description": "Capacitor Pack 100uF (50 pcs)", "quantity_received": Decimal("20"), "quantity_accepted": Decimal("20"), "quantity_rejected": Decimal("0"), "unit_of_measure": "PKG"},
            {"line_number": 4, "po_line_number": 4, "item_code": "PR-PCB-CUS", "description": "Custom PCB Fabrication", "quantity_received": Decimal("30"), "quantity_accepted": Decimal("30"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-7: Full receipt for PO-2025-0006
    {
        "grn_number": "GRN-2025-0007",
        "po_number": "PO-2025-0006",
        "receipt_date": date(2025, 10, 28),
        "status": "RECEIVED",
        "warehouse": "Office Supply Room",
        "receiver_name": "Nancy Patel",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "SO-PPR-A4", "description": "A4 Copy Paper Ream (500 sheets)", "quantity_received": Decimal("100"), "quantity_accepted": Decimal("100"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "SO-TNR-BLK", "description": "Laser Printer Toner Black", "quantity_received": Decimal("10"), "quantity_accepted": Decimal("10"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 3, "po_line_number": 3, "item_code": "SO-PEN-BOX", "description": "Ballpoint Pen Box (50 pcs)", "quantity_received": Decimal("5"), "quantity_accepted": Decimal("5"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
        ],
    },
    # GRN-8: Full receipt for PO-2025-0007
    {
        "grn_number": "GRN-2025-0008",
        "po_number": "PO-2025-0007",
        "receipt_date": date(2025, 9, 15),
        "status": "RECEIVED",
        "warehouse": "Chemical Storage Facility",
        "receiver_name": "Alex Nguyen",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "NC-SOL-IPA", "description": "Isopropyl Alcohol 99% 20L Drum", "quantity_received": Decimal("20"), "quantity_accepted": Decimal("20"), "quantity_rejected": Decimal("0"), "unit_of_measure": "DRM"},
            {"line_number": 2, "po_line_number": 2, "item_code": "NC-DGR-HVY", "description": "Heavy Duty Degreaser 10L", "quantity_received": Decimal("30"), "quantity_accepted": Decimal("30"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 3, "po_line_number": 3, "item_code": "NC-CLN-ABC", "description": "All-Purpose Surface Cleaner 5L", "quantity_received": Decimal("50"), "quantity_accepted": Decimal("50"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-9: First partial receipt for PO-2025-0008
    {
        "grn_number": "GRN-2025-0009",
        "po_number": "PO-2025-0008",
        "receipt_date": date(2025, 7, 1),
        "status": "RECEIVED",
        "warehouse": "IT Data Center",
        "receiver_name": "Kevin Brown",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "BS-CLD-ENT", "description": "Enterprise Cloud Hosting (Annual)", "quantity_received": Decimal("1"), "quantity_accepted": Decimal("1"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "BS-SUP-PRM", "description": "Premium Support Package", "quantity_received": Decimal("1"), "quantity_accepted": Decimal("1"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-10: Second receipt for PO-2025-0008 (security licenses)
    {
        "grn_number": "GRN-2025-0010",
        "po_number": "PO-2025-0008",
        "receipt_date": date(2025, 7, 10),
        "status": "RECEIVED",
        "warehouse": "IT Data Center",
        "receiver_name": "Kevin Brown",
        "lines": [
            {"line_number": 1, "po_line_number": 3, "item_code": "BS-SEC-ADV", "description": "Advanced Security Suite License", "quantity_received": Decimal("50"), "quantity_accepted": Decimal("50"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-11: First partial receipt for PO-2025-0009 (bolts and nuts only)
    {
        "grn_number": "GRN-2025-0011",
        "po_number": "PO-2025-0009",
        "receipt_date": date(2025, 10, 18),
        "status": "RECEIVED",
        "warehouse": "Warehouse A - Chicago",
        "receiver_name": "Carlos Ramirez",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "ACM-FST-M8", "description": "M8 Hex Bolt Grade 8.8 (Box of 100)", "quantity_received": Decimal("30"), "quantity_accepted": Decimal("30"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
            {"line_number": 2, "po_line_number": 2, "item_code": "ACM-NUT-M8", "description": "M8 Hex Nut Grade 8 (Box of 100)", "quantity_received": Decimal("30"), "quantity_accepted": Decimal("30"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
        ],
    },
    # GRN-12: Second partial receipt for PO-2025-0009 (remainder)
    {
        "grn_number": "GRN-2025-0012",
        "po_number": "PO-2025-0009",
        "receipt_date": date(2025, 10, 28),
        "status": "RECEIVED",
        "warehouse": "Warehouse A - Chicago",
        "receiver_name": "Carlos Ramirez",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "ACM-FST-M8", "description": "M8 Hex Bolt Grade 8.8 (Box of 100)", "quantity_received": Decimal("20"), "quantity_accepted": Decimal("20"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
            {"line_number": 2, "po_line_number": 2, "item_code": "ACM-NUT-M8", "description": "M8 Hex Nut Grade 8 (Box of 100)", "quantity_received": Decimal("20"), "quantity_accepted": Decimal("20"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
            {"line_number": 3, "po_line_number": 3, "item_code": "ACM-WSH-M8", "description": "M8 Flat Washer SS (Box of 200)", "quantity_received": Decimal("30"), "quantity_accepted": Decimal("30"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
            {"line_number": 4, "po_line_number": 4, "item_code": "ACM-FIT-QC", "description": "Quick-Connect Fitting 1/2 inch", "quantity_received": Decimal("200"), "quantity_accepted": Decimal("200"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
        ],
    },
    # GRN-13: Full receipt for PO-2025-0010
    {
        "grn_number": "GRN-2025-0013",
        "po_number": "PO-2025-0010",
        "receipt_date": date(2025, 11, 10),
        "status": "RECEIVED",
        "warehouse": "Warehouse B - Engineering",
        "receiver_name": "Tom Wilson",
        "lines": [
            {"line_number": 1, "po_line_number": 1, "item_code": "PP-CNC-SPD", "description": "CNC Spindle Motor 5HP", "quantity_received": Decimal("2"), "quantity_accepted": Decimal("2"), "quantity_rejected": Decimal("0"), "unit_of_measure": "EA"},
            {"line_number": 2, "po_line_number": 2, "item_code": "PP-CNC-COL", "description": "ER32 Collet Set (15 pcs)", "quantity_received": Decimal("5"), "quantity_accepted": Decimal("5"), "quantity_rejected": Decimal("0"), "unit_of_measure": "SET"},
            {"line_number": 3, "po_line_number": 3, "item_code": "PP-CNC-INS", "description": "Carbide Insert CNMG120408 (Box)", "quantity_received": Decimal("10"), "quantity_accepted": Decimal("10"), "quantity_rejected": Decimal("0"), "unit_of_measure": "BOX"},
        ],
    },
]


# ───────────────────────────────────────────────────────────────────────
# Sample pre-extracted invoices for demo (optional --with-invoices flag)
# ───────────────────────────────────────────────────────────────────────
SAMPLE_INVOICES = [
    # INV-1: Perfect match against PO-2025-0001
    {
        "raw_vendor_name": "Acme Industrial Supplies",
        "raw_invoice_number": "INV-ACM-20251001",
        "raw_invoice_date": "2025-10-05",
        "raw_po_number": "PO-2025-0001",
        "raw_currency": "USD",
        "raw_subtotal": "13650.00",
        "raw_tax_amount": "1228.50",
        "raw_total_amount": "14878.50",
        "invoice_number": "INV-ACM-20251001",
        "normalized_invoice_number": "INVACM20251001",
        "invoice_date": date(2025, 10, 5),
        "po_number": "PO-2025-0001",
        "normalized_po_number": "PO20250001",
        "currency": "USD",
        "subtotal": Decimal("13650.00"),
        "tax_amount": Decimal("1228.50"),
        "total_amount": Decimal("14878.50"),
        "vendor_code": "V-1001",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.95,
        "extraction_remarks": "High confidence extraction. All fields clearly readable.",
        "lines": [
            {"line_number": 1, "raw_description": "Ball Bearing Assembly 100mm", "raw_quantity": "200", "raw_unit_price": "45.00", "raw_tax_amount": "810.00", "raw_line_amount": "9000.00", "description": "Ball Bearing Assembly 100mm", "normalized_description": "ball bearing assembly 100mm", "quantity": Decimal("200"), "unit_price": Decimal("45.00"), "tax_amount": Decimal("810.00"), "line_amount": Decimal("9000.00"), "extraction_confidence": 0.97},
            {"line_number": 2, "raw_description": "Industrial Belt Drive 50cm", "raw_quantity": "100", "raw_unit_price": "32.50", "raw_tax_amount": "292.50", "raw_line_amount": "3250.00", "description": "Industrial Belt Drive 50cm", "normalized_description": "industrial belt drive 50cm", "quantity": Decimal("100"), "unit_price": Decimal("32.50"), "tax_amount": Decimal("292.50"), "line_amount": Decimal("3250.00"), "extraction_confidence": 0.96},
            {"line_number": 3, "raw_description": "High-Temp Lubricant 5L Can", "raw_quantity": "50", "raw_unit_price": "28.00", "raw_tax_amount": "126.00", "raw_line_amount": "1400.00", "description": "High-Temp Lubricant 5L Can", "normalized_description": "high temp lubricant 5l can", "quantity": Decimal("50"), "unit_price": Decimal("28.00"), "tax_amount": Decimal("126.00"), "line_amount": Decimal("1400.00"), "extraction_confidence": 0.94},
        ],
    },
    # INV-2: Partial match (invoice for partial PO-2025-0002 delivery)
    {
        "raw_vendor_name": "Global Tech Sol",
        "raw_invoice_number": "GTS-2025-4421",
        "raw_invoice_date": "2025-10-20",
        "raw_po_number": "PO-2025-0002",
        "raw_currency": "USD",
        "raw_subtotal": "55750.00",
        "raw_tax_amount": "5017.50",
        "raw_total_amount": "60767.50",
        "invoice_number": "GTS-2025-4421",
        "normalized_invoice_number": "GTS20254421",
        "invoice_date": date(2025, 10, 20),
        "po_number": "PO-2025-0002",
        "normalized_po_number": "PO20250002",
        "currency": "USD",
        "subtotal": Decimal("55750.00"),
        "tax_amount": Decimal("5017.50"),
        "total_amount": Decimal("60767.50"),
        "vendor_code": "V-1002",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.91,
        "extraction_remarks": "Good extraction. Vendor name is short form alias.",
        "lines": [
            {"line_number": 1, "raw_description": "Business Laptop Pro 15\"", "raw_quantity": "25", "raw_unit_price": "1450.00", "raw_tax_amount": "3262.50", "raw_line_amount": "36250.00", "description": "Business Laptop Pro 15-inch", "normalized_description": "business laptop pro 15 inch", "quantity": Decimal("25"), "unit_price": Decimal("1450.00"), "tax_amount": Decimal("3262.50"), "line_amount": Decimal("36250.00"), "extraction_confidence": 0.93},
            {"line_number": 2, "raw_description": "27\" 4K Monitor", "raw_quantity": "25", "raw_unit_price": "520.00", "raw_tax_amount": "1170.00", "raw_line_amount": "13000.00", "description": "27-inch 4K Monitor", "normalized_description": "27 inch 4k monitor", "quantity": Decimal("25"), "unit_price": Decimal("520.00"), "tax_amount": Decimal("1170.00"), "line_amount": Decimal("13000.00"), "extraction_confidence": 0.90},
            {"line_number": 3, "raw_description": "USB-C Dock Station", "raw_quantity": "25", "raw_unit_price": "185.00", "raw_tax_amount": "416.25", "raw_line_amount": "4625.00", "description": "USB-C Docking Station", "normalized_description": "usb c docking station", "quantity": Decimal("25"), "unit_price": Decimal("185.00"), "tax_amount": Decimal("416.25"), "line_amount": Decimal("4625.00"), "extraction_confidence": 0.88},
            {"line_number": 4, "raw_description": "Wireless KB & Mouse Set", "raw_quantity": "25", "raw_unit_price": "75.00", "raw_tax_amount": "168.75", "raw_line_amount": "1875.00", "description": "Wireless Keyboard & Mouse Set", "normalized_description": "wireless keyboard mouse set", "quantity": Decimal("25"), "unit_price": Decimal("75.00"), "tax_amount": Decimal("168.75"), "line_amount": Decimal("1875.00"), "extraction_confidence": 0.87},
        ],
    },
    # INV-3: Quantity mismatch – invoicing more than PO
    {
        "raw_vendor_name": "Precision Parts Mfg",
        "raw_invoice_number": "PPM-INV-9087",
        "raw_invoice_date": "2025-09-12",
        "raw_po_number": "PO-2025-0003",
        "raw_currency": "USD",
        "raw_subtotal": "29275.00",
        "raw_tax_amount": "2634.75",
        "raw_total_amount": "31909.75",
        "invoice_number": "PPM-INV-9087",
        "normalized_invoice_number": "PPMINV9087",
        "invoice_date": date(2025, 9, 12),
        "po_number": "PO-2025-0003",
        "normalized_po_number": "PO20250003",
        "currency": "USD",
        "subtotal": Decimal("29275.00"),
        "tax_amount": Decimal("2634.75"),
        "total_amount": Decimal("31909.75"),
        "vendor_code": "V-1003",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.93,
        "extraction_remarks": "Vendor alias detected. Quantities differ from PO for lines 1 and 3.",
        "lines": [
            {"line_number": 1, "raw_description": "Precision Steel Shaft 200mm", "raw_quantity": "520", "raw_unit_price": "18.75", "raw_tax_amount": "877.50", "raw_line_amount": "9750.00", "description": "Precision Steel Shaft 200mm", "normalized_description": "precision steel shaft 200mm", "quantity": Decimal("520"), "unit_price": Decimal("18.75"), "tax_amount": Decimal("877.50"), "line_amount": Decimal("9750.00"), "extraction_confidence": 0.90},
            {"line_number": 2, "raw_description": "Gear Assembly Set A", "raw_quantity": "100", "raw_unit_price": "125.00", "raw_tax_amount": "1125.00", "raw_line_amount": "12500.00", "description": "Gear Assembly Set A", "normalized_description": "gear assembly set a", "quantity": Decimal("100"), "unit_price": Decimal("125.00"), "tax_amount": Decimal("1125.00"), "line_amount": Decimal("12500.00"), "extraction_confidence": 0.92},
            {"line_number": 3, "raw_description": "Brake Pad Assembly 10mm", "raw_quantity": "310", "raw_unit_price": "22.00", "raw_tax_amount": "613.80", "raw_line_amount": "6820.00", "description": "Brake Pad Assembly 10mm", "normalized_description": "brake pad assembly 10mm", "quantity": Decimal("310"), "unit_price": Decimal("22.00"), "tax_amount": Decimal("613.80"), "line_amount": Decimal("6820.00"), "extraction_confidence": 0.85},
        ],
    },
    # INV-4: Price mismatch scenario for chemicals
    {
        "raw_vendor_name": "Northern Chem Corp",
        "raw_invoice_number": "NCC-2025-0782",
        "raw_invoice_date": "2025-09-20",
        "raw_po_number": "PO-2025-0007",
        "raw_currency": "USD",
        "raw_subtotal": "4060.00",
        "raw_tax_amount": "365.40",
        "raw_total_amount": "4425.40",
        "invoice_number": "NCC-2025-0782",
        "normalized_invoice_number": "NCC20250782",
        "invoice_date": date(2025, 9, 20),
        "po_number": "PO-2025-0007",
        "normalized_po_number": "PO20250007",
        "currency": "USD",
        "subtotal": Decimal("4060.00"),
        "tax_amount": Decimal("365.40"),
        "total_amount": Decimal("4425.40"),
        "vendor_code": "V-1007",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.92,
        "extraction_remarks": "Clean extraction. Prices differ from PO on lines 1 and 2.",
        "lines": [
            {"line_number": 1, "raw_description": "IPA 99% 20L Drum", "raw_quantity": "20", "raw_unit_price": "98.00", "raw_tax_amount": "176.40", "raw_line_amount": "1960.00", "description": "Isopropyl Alcohol 99% 20L Drum", "normalized_description": "isopropyl alcohol 99 20l drum", "quantity": Decimal("20"), "unit_price": Decimal("98.00"), "tax_amount": Decimal("176.40"), "line_amount": Decimal("1960.00"), "extraction_confidence": 0.90},
            {"line_number": 2, "raw_description": "Heavy Duty Degreaser 10L", "raw_quantity": "30", "raw_unit_price": "44.00", "raw_tax_amount": "118.80", "raw_line_amount": "1320.00", "description": "Heavy Duty Degreaser 10L", "normalized_description": "heavy duty degreaser 10l", "quantity": Decimal("30"), "unit_price": Decimal("44.00"), "tax_amount": Decimal("118.80"), "line_amount": Decimal("1320.00"), "extraction_confidence": 0.93},
            {"line_number": 3, "raw_description": "All-Purpose Surface Cleaner 5L", "raw_quantity": "52", "raw_unit_price": "15.00", "raw_tax_amount": "70.20", "raw_line_amount": "780.00", "description": "All-Purpose Surface Cleaner 5L", "normalized_description": "all purpose surface cleaner 5l", "quantity": Decimal("52"), "unit_price": Decimal("15.00"), "tax_amount": Decimal("70.20"), "line_amount": Decimal("780.00"), "extraction_confidence": 0.91},
        ],
    },
    # INV-5: Extra line item + qty discrepancy — invoice has 5 lines vs PO's 4, and quantities differ
    # High confidence extraction but structural mismatch → REQUIRES_REVIEW (unmatched lines)
    {
        "raw_vendor_name": "Pacific Rim Electronics",
        "raw_invoice_number": "PRE/25/10032",
        "raw_invoice_date": "2025-08-01",
        "raw_po_number": "PO-2025-0005",
        "raw_currency": "USD",
        "raw_subtotal": "5090.00",
        "raw_tax_amount": "458.10",
        "raw_total_amount": "5548.10",
        "invoice_number": "PRE/25/10032",
        "normalized_invoice_number": "PRE2510032",
        "invoice_date": date(2025, 8, 1),
        "po_number": "PO-2025-0005",
        "normalized_po_number": "PO20250005",
        "currency": "USD",
        "subtotal": Decimal("5090.00"),
        "tax_amount": Decimal("458.10"),
        "total_amount": Decimal("5548.10"),
        "vendor_code": "V-1005",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.92,
        "extraction_remarks": "Clean extraction. Invoice includes extra line for expedited shipping not on PO. Qty differs on MCU boards.",
        "lines": [
            {"line_number": 1, "raw_description": "ARM Cortex M4 MCU Board", "raw_quantity": "55", "raw_unit_price": "35.00", "raw_tax_amount": "173.25", "raw_line_amount": "1925.00", "description": "ARM Cortex-M4 Microcontroller Board", "normalized_description": "arm cortex m4 microcontroller board", "quantity": Decimal("55"), "unit_price": Decimal("35.00"), "tax_amount": Decimal("173.25"), "line_amount": Decimal("1925.00"), "extraction_confidence": 0.93},
            {"line_number": 2, "raw_description": "Temp Sensor Module", "raw_quantity": "100", "raw_unit_price": "8.50", "raw_tax_amount": "76.50", "raw_line_amount": "850.00", "description": "Temperature Sensor Module", "normalized_description": "temperature sensor module", "quantity": Decimal("100"), "unit_price": Decimal("8.50"), "tax_amount": Decimal("76.50"), "line_amount": Decimal("850.00"), "extraction_confidence": 0.94},
            {"line_number": 3, "raw_description": "Cap Pack 100uF", "raw_quantity": "20", "raw_unit_price": "12.00", "raw_tax_amount": "21.60", "raw_line_amount": "240.00", "description": "Capacitor Pack 100uF (50 pcs)", "normalized_description": "capacitor pack 100uf 50 pcs", "quantity": Decimal("20"), "unit_price": Decimal("12.00"), "tax_amount": Decimal("21.60"), "line_amount": Decimal("240.00"), "extraction_confidence": 0.91},
            {"line_number": 4, "raw_description": "Custom PCB Fab", "raw_quantity": "30", "raw_unit_price": "45.00", "raw_tax_amount": "121.50", "raw_line_amount": "1350.00", "description": "Custom PCB Fabrication", "normalized_description": "custom pcb fabrication", "quantity": Decimal("30"), "unit_price": Decimal("45.00"), "tax_amount": Decimal("121.50"), "line_amount": Decimal("1350.00"), "extraction_confidence": 0.90},
            {"line_number": 5, "raw_description": "Expedited Air Freight Surcharge", "raw_quantity": "1", "raw_unit_price": "725.00", "raw_tax_amount": "65.25", "raw_line_amount": "725.00", "description": "Expedited Air Freight Surcharge", "normalized_description": "expedited air freight surcharge", "quantity": Decimal("1"), "unit_price": Decimal("725.00"), "tax_amount": Decimal("65.25"), "line_amount": Decimal("725.00"), "extraction_confidence": 0.92},
        ],
    },
    # INV-6: PO not found scenario — triggers PORetrievalAgent + ExceptionAnalysis
    {
        "raw_vendor_name": "Acme Industrial Supplies",
        "raw_invoice_number": "INV-ACM-20251105",
        "raw_invoice_date": "2025-11-05",
        "raw_po_number": "PO-2025-9999",
        "raw_currency": "USD",
        "raw_subtotal": "5400.00",
        "raw_tax_amount": "486.00",
        "raw_total_amount": "5886.00",
        "invoice_number": "INV-ACM-20251105",
        "normalized_invoice_number": "INVACM20251105",
        "invoice_date": date(2025, 11, 5),
        "po_number": "PO-2025-9999",
        "normalized_po_number": "PO20259999",
        "currency": "USD",
        "subtotal": Decimal("5400.00"),
        "tax_amount": Decimal("486.00"),
        "total_amount": Decimal("5886.00"),
        "vendor_code": "V-1001",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.93,
        "extraction_remarks": "Clean extraction. PO number not found in system — may be a typo or new PO.",
        "lines": [
            {"line_number": 1, "raw_description": "Ball Bearing Assembly 100mm", "raw_quantity": "60", "raw_unit_price": "45.00", "raw_tax_amount": "243.00", "raw_line_amount": "2700.00", "description": "Ball Bearing Assembly 100mm", "normalized_description": "ball bearing assembly 100mm", "quantity": Decimal("60"), "unit_price": Decimal("45.00"), "tax_amount": Decimal("243.00"), "line_amount": Decimal("2700.00"), "extraction_confidence": 0.95},
            {"line_number": 2, "raw_description": "Industrial Belt Drive 50cm", "raw_quantity": "50", "raw_unit_price": "32.50", "raw_tax_amount": "146.25", "raw_line_amount": "1625.00", "description": "Industrial Belt Drive 50cm", "normalized_description": "industrial belt drive 50cm", "quantity": Decimal("50"), "unit_price": Decimal("32.50"), "tax_amount": Decimal("146.25"), "line_amount": Decimal("1625.00"), "extraction_confidence": 0.94},
            {"line_number": 3, "raw_description": "High-Temp Lubricant 5L Can", "raw_quantity": "40", "raw_unit_price": "26.88", "raw_tax_amount": "96.75", "raw_line_amount": "1075.00", "description": "High-Temp Lubricant 5L Can", "normalized_description": "high temp lubricant 5l can", "quantity": Decimal("40"), "unit_price": Decimal("26.88"), "tax_amount": Decimal("96.75"), "line_amount": Decimal("1075.00"), "extraction_confidence": 0.91},
        ],
    },
    # INV-7: Vendor mismatch — invoice vendor name differs from PO vendor, triggers VendorSearchTool
    {
        "raw_vendor_name": "PPM Inc",
        "raw_invoice_number": "PPM-INV-9210",
        "raw_invoice_date": "2025-11-15",
        "raw_po_number": "PO-2025-0010",
        "raw_currency": "USD",
        "raw_subtotal": "8050.00",
        "raw_tax_amount": "724.50",
        "raw_total_amount": "8774.50",
        "invoice_number": "PPM-INV-9210",
        "normalized_invoice_number": "PPMINV9210",
        "invoice_date": date(2025, 11, 15),
        "po_number": "PO-2025-0010",
        "normalized_po_number": "PO20250010",
        "currency": "USD",
        "subtotal": Decimal("8050.00"),
        "tax_amount": Decimal("724.50"),
        "total_amount": Decimal("8774.50"),
        "vendor_code": "V-1003",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.89,
        "extraction_remarks": "Vendor name 'PPM Inc' is an alias for Precision Parts Manufacturing.",
        "lines": [
            {"line_number": 1, "raw_description": "CNC Spindle Motor 5HP", "raw_quantity": "2", "raw_unit_price": "2800.00", "raw_tax_amount": "504.00", "raw_line_amount": "5600.00", "description": "CNC Spindle Motor 5HP", "normalized_description": "cnc spindle motor 5hp", "quantity": Decimal("2"), "unit_price": Decimal("2800.00"), "tax_amount": Decimal("504.00"), "line_amount": Decimal("5600.00"), "extraction_confidence": 0.92},
            {"line_number": 2, "raw_description": "ER32 Collet Set (15 pcs)", "raw_quantity": "5", "raw_unit_price": "320.00", "raw_tax_amount": "144.00", "raw_line_amount": "1600.00", "description": "ER32 Collet Set (15 pcs)", "normalized_description": "er32 collet set 15 pcs", "quantity": Decimal("5"), "unit_price": Decimal("320.00"), "tax_amount": Decimal("144.00"), "line_amount": Decimal("1600.00"), "extraction_confidence": 0.90},
            {"line_number": 3, "raw_description": "Carbide Insert CNMG120408", "raw_quantity": "10", "raw_unit_price": "85.00", "raw_tax_amount": "76.50", "raw_line_amount": "850.00", "description": "Carbide Insert CNMG120408 (Box)", "normalized_description": "carbide insert cnmg120408 box", "quantity": Decimal("10"), "unit_price": Decimal("85.00"), "tax_amount": Decimal("76.50"), "line_amount": Decimal("850.00"), "extraction_confidence": 0.88},
        ],
    },
    # INV-8: Perfect match for office supplies — should exercise CaseSummary for auto-close
    {
        "raw_vendor_name": "Summit Office Products",
        "raw_invoice_number": "SOP-2025-33441",
        "raw_invoice_date": "2025-11-01",
        "raw_po_number": "PO-2025-0006",
        "raw_currency": "USD",
        "raw_subtotal": "1390.00",
        "raw_tax_amount": "125.10",
        "raw_total_amount": "1515.10",
        "invoice_number": "SOP-2025-33441",
        "normalized_invoice_number": "SOP202533441",
        "invoice_date": date(2025, 11, 1),
        "po_number": "PO-2025-0006",
        "normalized_po_number": "PO20250006",
        "currency": "USD",
        "subtotal": Decimal("1390.00"),
        "tax_amount": Decimal("125.10"),
        "total_amount": Decimal("1515.10"),
        "vendor_code": "V-1006",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.97,
        "extraction_remarks": "High confidence. All fields match expected patterns.",
        "lines": [
            {"line_number": 1, "raw_description": "A4 Copy Paper Ream (500 sheets)", "raw_quantity": "100", "raw_unit_price": "4.50", "raw_tax_amount": "40.50", "raw_line_amount": "450.00", "description": "A4 Copy Paper Ream (500 sheets)", "normalized_description": "a4 copy paper ream 500 sheets", "quantity": Decimal("100"), "unit_price": Decimal("4.50"), "tax_amount": Decimal("40.50"), "line_amount": Decimal("450.00"), "extraction_confidence": 0.98},
            {"line_number": 2, "raw_description": "Laser Printer Toner Black", "raw_quantity": "10", "raw_unit_price": "85.00", "raw_tax_amount": "76.50", "raw_line_amount": "850.00", "description": "Laser Printer Toner Black", "normalized_description": "laser printer toner black", "quantity": Decimal("10"), "unit_price": Decimal("85.00"), "tax_amount": Decimal("76.50"), "line_amount": Decimal("850.00"), "extraction_confidence": 0.97},
            {"line_number": 3, "raw_description": "Ballpoint Pen Box (50 pcs)", "raw_quantity": "5", "raw_unit_price": "18.00", "raw_tax_amount": "8.10", "raw_line_amount": "90.00", "description": "Ballpoint Pen Box (50 pcs)", "normalized_description": "ballpoint pen box 50 pcs", "quantity": Decimal("5"), "unit_price": Decimal("18.00"), "tax_amount": Decimal("8.10"), "line_amount": Decimal("90.00"), "extraction_confidence": 0.96},
        ],
    },
    # INV-9: IT services — fully received, good match
    {
        "raw_vendor_name": "BlueSky Information Technology Services",
        "raw_invoice_number": "BSIT-2025-07-001",
        "raw_invoice_date": "2025-07-15",
        "raw_po_number": "PO-2025-0008",
        "raw_currency": "USD",
        "raw_subtotal": "38500.00",
        "raw_tax_amount": "3465.00",
        "raw_total_amount": "41965.00",
        "invoice_number": "BSIT-2025-07-001",
        "normalized_invoice_number": "BSIT202507001",
        "invoice_date": date(2025, 7, 15),
        "po_number": "PO-2025-0008",
        "normalized_po_number": "PO20250008",
        "currency": "USD",
        "subtotal": Decimal("38500.00"),
        "tax_amount": Decimal("3465.00"),
        "total_amount": Decimal("41965.00"),
        "vendor_code": "V-1008",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.94,
        "extraction_remarks": "Vendor name is long-form alias. Clean extraction.",
        "lines": [
            {"line_number": 1, "raw_description": "Enterprise Cloud Hosting (Annual)", "raw_quantity": "1", "raw_unit_price": "24000.00", "raw_tax_amount": "2160.00", "raw_line_amount": "24000.00", "description": "Enterprise Cloud Hosting (Annual)", "normalized_description": "enterprise cloud hosting annual", "quantity": Decimal("1"), "unit_price": Decimal("24000.00"), "tax_amount": Decimal("2160.00"), "line_amount": Decimal("24000.00"), "extraction_confidence": 0.96},
            {"line_number": 2, "raw_description": "Premium Support Package", "raw_quantity": "1", "raw_unit_price": "8500.00", "raw_tax_amount": "765.00", "raw_line_amount": "8500.00", "description": "Premium Support Package", "normalized_description": "premium support package", "quantity": Decimal("1"), "unit_price": Decimal("8500.00"), "tax_amount": Decimal("765.00"), "line_amount": Decimal("8500.00"), "extraction_confidence": 0.95},
            {"line_number": 3, "raw_description": "Advanced Security Suite License", "raw_quantity": "50", "raw_unit_price": "120.00", "raw_tax_amount": "540.00", "raw_line_amount": "6000.00", "description": "Advanced Security Suite License", "normalized_description": "advanced security suite license", "quantity": Decimal("50"), "unit_price": Decimal("120.00"), "tax_amount": Decimal("540.00"), "line_amount": Decimal("6000.00"), "extraction_confidence": 0.93},
        ],
    },
    # INV-10: EUR invoice — logistics, matches PO-0004
    {
        "raw_vendor_name": "Euro Logistics",
        "raw_invoice_number": "ELG-RE-2025-0442",
        "raw_invoice_date": "2025-11-22",
        "raw_po_number": "PO-2025-0004",
        "raw_currency": "EUR",
        "raw_subtotal": "7275.00",
        "raw_tax_amount": "1382.25",
        "raw_total_amount": "8657.25",
        "invoice_number": "ELG-RE-2025-0442",
        "normalized_invoice_number": "ELGRE20250442",
        "invoice_date": date(2025, 11, 22),
        "po_number": "PO-2025-0004",
        "normalized_po_number": "PO20250004",
        "currency": "EUR",
        "subtotal": Decimal("7275.00"),
        "tax_amount": Decimal("1382.25"),
        "total_amount": Decimal("8657.25"),
        "vendor_code": "V-1004",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.90,
        "extraction_remarks": "EUR invoice, vendor alias detected. All fields readable.",
        "lines": [
            {"line_number": 1, "raw_description": "Euro Pallet Standard 1200x800mm", "raw_quantity": "200", "raw_unit_price": "25.00", "raw_tax_amount": "950.00", "raw_line_amount": "5000.00", "description": "Euro Pallet Standard 1200x800mm", "normalized_description": "euro pallet standard 1200x800mm", "quantity": Decimal("200"), "unit_price": Decimal("25.00"), "tax_amount": Decimal("950.00"), "line_amount": Decimal("5000.00"), "extraction_confidence": 0.92},
            {"line_number": 2, "raw_description": "Stretch Wrap Film 500mm Roll", "raw_quantity": "150", "raw_unit_price": "12.50", "raw_tax_amount": "356.25", "raw_line_amount": "1875.00", "description": "Stretch Wrap Film 500mm Roll", "normalized_description": "stretch wrap film 500mm roll", "quantity": Decimal("150"), "unit_price": Decimal("12.50"), "tax_amount": Decimal("356.25"), "line_amount": Decimal("1875.00"), "extraction_confidence": 0.91},
            {"line_number": 3, "raw_description": "Thermal Shipping Labels 100x150mm", "raw_quantity": "5000", "raw_unit_price": "0.08", "raw_tax_amount": "76.00", "raw_line_amount": "400.00", "description": "Thermal Shipping Labels 100x150mm", "normalized_description": "thermal shipping labels 100x150mm", "quantity": Decimal("5000"), "unit_price": Decimal("0.08"), "tax_amount": Decimal("76.00"), "line_amount": Decimal("400.00"), "extraction_confidence": 0.88},
        ],
    },
    # INV-11: Partial delivery invoice for fasteners — agents analyze GRN partial receipt
    {
        "raw_vendor_name": "Acme Ind. Supplies",
        "raw_invoice_number": "INV-ACM-20251028",
        "raw_invoice_date": "2025-10-28",
        "raw_po_number": "PO-2025-0009",
        "raw_currency": "USD",
        "raw_subtotal": "4005.00",
        "raw_tax_amount": "360.45",
        "raw_total_amount": "4365.45",
        "invoice_number": "INV-ACM-20251028",
        "normalized_invoice_number": "INVACM20251028",
        "invoice_date": date(2025, 10, 28),
        "po_number": "PO-2025-0009",
        "normalized_po_number": "PO20250009",
        "currency": "USD",
        "subtotal": Decimal("4005.00"),
        "tax_amount": Decimal("360.45"),
        "total_amount": Decimal("4365.45"),
        "vendor_code": "V-1001",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.91,
        "extraction_remarks": "Vendor alias detected. Invoice covers full PO quantity but GRN has partial receipts only.",
        "lines": [
            {"line_number": 1, "raw_description": "M8 Hex Bolt Grade 8.8 (Box of 100)", "raw_quantity": "50", "raw_unit_price": "28.00", "raw_tax_amount": "126.00", "raw_line_amount": "1400.00", "description": "M8 Hex Bolt Grade 8.8 (Box of 100)", "normalized_description": "m8 hex bolt grade 8 8 box of 100", "quantity": Decimal("50"), "unit_price": Decimal("28.00"), "tax_amount": Decimal("126.00"), "line_amount": Decimal("1400.00"), "extraction_confidence": 0.93},
            {"line_number": 2, "raw_description": "M8 Hex Nut Grade 8 (Box of 100)", "raw_quantity": "50", "raw_unit_price": "14.00", "raw_tax_amount": "63.00", "raw_line_amount": "700.00", "description": "M8 Hex Nut Grade 8 (Box of 100)", "normalized_description": "m8 hex nut grade 8 box of 100", "quantity": Decimal("50"), "unit_price": Decimal("14.00"), "tax_amount": Decimal("63.00"), "line_amount": Decimal("700.00"), "extraction_confidence": 0.92},
            {"line_number": 3, "raw_description": "M8 Flat Washer SS (Box of 200)", "raw_quantity": "30", "raw_unit_price": "18.50", "raw_tax_amount": "49.95", "raw_line_amount": "555.00", "description": "M8 Flat Washer SS (Box of 200)", "normalized_description": "m8 flat washer ss box of 200", "quantity": Decimal("30"), "unit_price": Decimal("18.50"), "tax_amount": Decimal("49.95"), "line_amount": Decimal("555.00"), "extraction_confidence": 0.90},
            {"line_number": 4, "raw_description": "Quick-Connect Fitting 1/2 inch", "raw_quantity": "200", "raw_unit_price": "6.75", "raw_tax_amount": "121.50", "raw_line_amount": "1350.00", "description": "Quick-Connect Fitting 1/2 inch", "normalized_description": "quick connect fitting 1 2 inch", "quantity": Decimal("200"), "unit_price": Decimal("6.75"), "tax_amount": Decimal("121.50"), "line_amount": Decimal("1350.00"), "extraction_confidence": 0.89},
        ],
    },
    # INV-12: Duplicate invoice — same normalized number as INV-1 but different date
    {
        "raw_vendor_name": "Acme Industrial Supplies",
        "raw_invoice_number": "INV-ACM-20251001-DUP",
        "raw_invoice_date": "2025-10-15",
        "raw_po_number": "PO-2025-0001",
        "raw_currency": "USD",
        "raw_subtotal": "13650.00",
        "raw_tax_amount": "1228.50",
        "raw_total_amount": "14878.50",
        "invoice_number": "INV-ACM-20251001-DUP",
        "normalized_invoice_number": "INVACM20251001DUP",
        "invoice_date": date(2025, 10, 15),
        "po_number": "PO-2025-0001",
        "normalized_po_number": "PO20250001",
        "currency": "USD",
        "subtotal": Decimal("13650.00"),
        "tax_amount": Decimal("1228.50"),
        "total_amount": Decimal("14878.50"),
        "vendor_code": "V-1001",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.96,
        "extraction_remarks": "Possible duplicate of INV-ACM-20251001. Same amounts, same PO, different date.",
        "is_duplicate": True,
        "lines": [
            {"line_number": 1, "raw_description": "Ball Bearing Assembly 100mm", "raw_quantity": "200", "raw_unit_price": "45.00", "raw_tax_amount": "810.00", "raw_line_amount": "9000.00", "description": "Ball Bearing Assembly 100mm", "normalized_description": "ball bearing assembly 100mm", "quantity": Decimal("200"), "unit_price": Decimal("45.00"), "tax_amount": Decimal("810.00"), "line_amount": Decimal("9000.00"), "extraction_confidence": 0.97},
            {"line_number": 2, "raw_description": "Industrial Belt Drive 50cm", "raw_quantity": "100", "raw_unit_price": "32.50", "raw_tax_amount": "292.50", "raw_line_amount": "3250.00", "description": "Industrial Belt Drive 50cm", "normalized_description": "industrial belt drive 50cm", "quantity": Decimal("100"), "unit_price": Decimal("32.50"), "tax_amount": Decimal("292.50"), "line_amount": Decimal("3250.00"), "extraction_confidence": 0.96},
            {"line_number": 3, "raw_description": "High-Temp Lubricant 5L Can", "raw_quantity": "50", "raw_unit_price": "28.00", "raw_tax_amount": "126.00", "raw_line_amount": "1400.00", "description": "High-Temp Lubricant 5L Can", "normalized_description": "high temp lubricant 5l can", "quantity": Decimal("50"), "unit_price": Decimal("28.00"), "tax_amount": Decimal("126.00"), "line_amount": Decimal("1400.00"), "extraction_confidence": 0.95},
        ],
    },
    # INV-13: Rejected items in GRN — PO-0003 has partial rejections, triggers review
    {
        "raw_vendor_name": "Precision Parts Manufacturing",
        "raw_invoice_number": "PPM-INV-9150",
        "raw_invoice_date": "2025-09-18",
        "raw_po_number": "PO-2025-0003",
        "raw_currency": "USD",
        "raw_subtotal": "28475.00",
        "raw_tax_amount": "2562.75",
        "raw_total_amount": "31037.75",
        "invoice_number": "PPM-INV-9150",
        "normalized_invoice_number": "PPMINV9150",
        "invoice_date": date(2025, 9, 18),
        "po_number": "PO-2025-0003",
        "normalized_po_number": "PO20250003",
        "currency": "USD",
        "subtotal": Decimal("28475.00"),
        "tax_amount": Decimal("2562.75"),
        "total_amount": Decimal("31037.75"),
        "vendor_code": "V-1003",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.93,
        "extraction_remarks": "Clean extraction. PO has quality rejections in GRN — invoice bills for full quantity.",
        "lines": [
            {"line_number": 1, "raw_description": "Precision Steel Shaft 200mm", "raw_quantity": "500", "raw_unit_price": "18.75", "raw_tax_amount": "843.75", "raw_line_amount": "9375.00", "description": "Precision Steel Shaft 200mm", "normalized_description": "precision steel shaft 200mm", "quantity": Decimal("500"), "unit_price": Decimal("18.75"), "tax_amount": Decimal("843.75"), "line_amount": Decimal("9375.00"), "extraction_confidence": 0.95},
            {"line_number": 2, "raw_description": "Gear Assembly Set A", "raw_quantity": "100", "raw_unit_price": "125.00", "raw_tax_amount": "1125.00", "raw_line_amount": "12500.00", "description": "Gear Assembly Set A", "normalized_description": "gear assembly set a", "quantity": Decimal("100"), "unit_price": Decimal("125.00"), "tax_amount": Decimal("1125.00"), "line_amount": Decimal("12500.00"), "extraction_confidence": 0.94},
            {"line_number": 3, "raw_description": "Brake Pad Assembly 10mm", "raw_quantity": "300", "raw_unit_price": "22.00", "raw_tax_amount": "594.00", "raw_line_amount": "6600.00", "description": "Brake Pad Assembly 10mm", "normalized_description": "brake pad assembly 10mm", "quantity": Decimal("300"), "unit_price": Decimal("22.00"), "tax_amount": Decimal("594.00"), "line_amount": Decimal("6600.00"), "extraction_confidence": 0.93},
        ],
    },
    # INV-14: Price surcharge — vendor raised prices 5-8% above PO (well beyond 1% tolerance)
    # High confidence, all lines match by description, but prices trigger PARTIAL_MATCH → agents explain
    {
        "raw_vendor_name": "Northern Chemical Corp",
        "raw_invoice_number": "NCC-2025-0815",
        "raw_invoice_date": "2025-10-05",
        "raw_po_number": "PO-2025-0007",
        "raw_currency": "USD",
        "raw_subtotal": "4212.00",
        "raw_tax_amount": "379.08",
        "raw_total_amount": "4591.08",
        "invoice_number": "NCC-2025-0815",
        "normalized_invoice_number": "NCC20250815",
        "invoice_date": date(2025, 10, 5),
        "po_number": "PO-2025-0007",
        "normalized_po_number": "PO20250007",
        "currency": "USD",
        "subtotal": Decimal("4212.00"),
        "tax_amount": Decimal("379.08"),
        "total_amount": Decimal("4591.08"),
        "vendor_code": "V-1007",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.96,
        "extraction_remarks": "High confidence extraction. Vendor applied revised pricing — all unit prices higher than PO contract rates.",
        "lines": [
            {"line_number": 1, "raw_description": "Isopropyl Alcohol 99% 20L Drum", "raw_quantity": "20", "raw_unit_price": "102.50", "raw_tax_amount": "184.50", "raw_line_amount": "2050.00", "description": "Isopropyl Alcohol 99% 20L Drum", "normalized_description": "isopropyl alcohol 99 20l drum", "quantity": Decimal("20"), "unit_price": Decimal("102.50"), "tax_amount": Decimal("184.50"), "line_amount": Decimal("2050.00"), "extraction_confidence": 0.97},
            {"line_number": 2, "raw_description": "Heavy Duty Degreaser 10L", "raw_quantity": "30", "raw_unit_price": "45.40", "raw_tax_amount": "122.58", "raw_line_amount": "1362.00", "description": "Heavy Duty Degreaser 10L", "normalized_description": "heavy duty degreaser 10l", "quantity": Decimal("30"), "unit_price": Decimal("45.40"), "tax_amount": Decimal("122.58"), "line_amount": Decimal("1362.00"), "extraction_confidence": 0.96},
            {"line_number": 3, "raw_description": "All-Purpose Surface Cleaner 5L", "raw_quantity": "50", "raw_unit_price": "16.00", "raw_tax_amount": "72.00", "raw_line_amount": "800.00", "description": "All-Purpose Surface Cleaner 5L", "normalized_description": "all purpose surface cleaner 5l", "quantity": Decimal("50"), "unit_price": Decimal("16.00"), "tax_amount": Decimal("72.00"), "line_amount": Decimal("800.00"), "extraction_confidence": 0.95},
        ],
    },
    # INV-15: Overbilling on partially received goods — invoice bills for PO qty but GRN shows short delivery
    # PO-2025-0002 has partial receipt (15 laptops in GRN-2, 10 in GRN-3 = 25 total) but monitors only 20+5=25
    # This invoice bills for 25 laptops and 30 monitors (5 extra monitors never received)
    # High confidence, agents analyze GRN vs invoice discrepancy
    {
        "raw_vendor_name": "Global Tech Solutions",
        "raw_invoice_number": "GTS-2025-4499",
        "raw_invoice_date": "2025-11-10",
        "raw_po_number": "PO-2025-0002",
        "raw_currency": "USD",
        "raw_subtotal": "58350.00",
        "raw_tax_amount": "5251.50",
        "raw_total_amount": "63601.50",
        "invoice_number": "GTS-2025-4499",
        "normalized_invoice_number": "GTS20254499",
        "invoice_date": date(2025, 11, 10),
        "po_number": "PO-2025-0002",
        "normalized_po_number": "PO20250002",
        "currency": "USD",
        "subtotal": Decimal("58350.00"),
        "tax_amount": Decimal("5251.50"),
        "total_amount": Decimal("63601.50"),
        "vendor_code": "V-1002",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.94,
        "extraction_remarks": "Clean extraction. Invoice bills for 30 monitors but PO has 25 and GRN received 25.",
        "lines": [
            {"line_number": 1, "raw_description": "Business Laptop Pro 15-inch", "raw_quantity": "25", "raw_unit_price": "1450.00", "raw_tax_amount": "3262.50", "raw_line_amount": "36250.00", "description": "Business Laptop Pro 15-inch", "normalized_description": "business laptop pro 15 inch", "quantity": Decimal("25"), "unit_price": Decimal("1450.00"), "tax_amount": Decimal("3262.50"), "line_amount": Decimal("36250.00"), "extraction_confidence": 0.96},
            {"line_number": 2, "raw_description": "27-inch 4K Monitor", "raw_quantity": "30", "raw_unit_price": "520.00", "raw_tax_amount": "1404.00", "raw_line_amount": "15600.00", "description": "27-inch 4K Monitor", "normalized_description": "27 inch 4k monitor", "quantity": Decimal("30"), "unit_price": Decimal("520.00"), "tax_amount": Decimal("1404.00"), "line_amount": Decimal("15600.00"), "extraction_confidence": 0.95},
            {"line_number": 3, "raw_description": "USB-C Docking Station", "raw_quantity": "25", "raw_unit_price": "185.00", "raw_tax_amount": "416.25", "raw_line_amount": "4625.00", "description": "USB-C Docking Station", "normalized_description": "usb c docking station", "quantity": Decimal("25"), "unit_price": Decimal("185.00"), "tax_amount": Decimal("416.25"), "line_amount": Decimal("4625.00"), "extraction_confidence": 0.93},
            {"line_number": 4, "raw_description": "Wireless Keyboard & Mouse Set", "raw_quantity": "25", "raw_unit_price": "75.00", "raw_tax_amount": "168.75", "raw_line_amount": "1875.00", "description": "Wireless Keyboard & Mouse Set", "normalized_description": "wireless keyboard mouse set", "quantity": Decimal("25"), "unit_price": Decimal("75.00"), "tax_amount": Decimal("168.75"), "line_amount": Decimal("1875.00"), "extraction_confidence": 0.92},
        ],
    },
    # INV-16: Tax calculation error — line amounts match PO exactly but tax is computed wrong
    # High confidence extraction, vendor/PO/quantities all match, but tax amounts are inflated
    # Rules engine: header total_amount won't match (tax diff) → PARTIAL_MATCH → agents explain tax issue
    {
        "raw_vendor_name": "Summit Office Products",
        "raw_invoice_number": "SOP-2025-33567",
        "raw_invoice_date": "2025-12-01",
        "raw_po_number": "PO-2025-0006",
        "raw_currency": "USD",
        "raw_subtotal": "1390.00",
        "raw_tax_amount": "180.70",
        "raw_total_amount": "1570.70",
        "invoice_number": "SOP-2025-33567",
        "normalized_invoice_number": "SOP202533567",
        "invoice_date": date(2025, 12, 1),
        "po_number": "PO-2025-0006",
        "normalized_po_number": "PO20250006",
        "currency": "USD",
        "subtotal": Decimal("1390.00"),
        "tax_amount": Decimal("180.70"),
        "total_amount": Decimal("1570.70"),
        "vendor_code": "V-1006",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.95,
        "extraction_remarks": "High confidence. Line items match PO but tax calculation appears incorrect — charged 13% instead of 9%.",
        "lines": [
            {"line_number": 1, "raw_description": "A4 Copy Paper Ream (500 sheets)", "raw_quantity": "100", "raw_unit_price": "4.50", "raw_tax_amount": "58.50", "raw_line_amount": "450.00", "description": "A4 Copy Paper Ream (500 sheets)", "normalized_description": "a4 copy paper ream 500 sheets", "quantity": Decimal("100"), "unit_price": Decimal("4.50"), "tax_amount": Decimal("58.50"), "line_amount": Decimal("450.00"), "extraction_confidence": 0.96},
            {"line_number": 2, "raw_description": "Laser Printer Toner Black", "raw_quantity": "10", "raw_unit_price": "85.00", "raw_tax_amount": "110.50", "raw_line_amount": "850.00", "description": "Laser Printer Toner Black", "normalized_description": "laser printer toner black", "quantity": Decimal("10"), "unit_price": Decimal("85.00"), "tax_amount": Decimal("110.50"), "line_amount": Decimal("850.00"), "extraction_confidence": 0.95},
            {"line_number": 3, "raw_description": "Ballpoint Pen Box (50 pcs)", "raw_quantity": "5", "raw_unit_price": "18.00", "raw_tax_amount": "11.70", "raw_line_amount": "90.00", "description": "Ballpoint Pen Box (50 pcs)", "normalized_description": "ballpoint pen box 50 pcs", "quantity": Decimal("5"), "unit_price": Decimal("18.00"), "tax_amount": Decimal("11.70"), "line_amount": Decimal("90.00"), "extraction_confidence": 0.94},
        ],
    },
    # INV-17: Multi-issue — wrong vendor on PO (invoice from alias), quantities over-billed by 3-5%,
    # AND one line item has description that barely matches (fuzzy). Agents must analyze all three issues.
    # PO-2025-0009 is from V-1001 (Acme), this invoice uses alias "ACME IND. SUPPLIES" and overbills
    {
        "raw_vendor_name": "ACME IND. SUPPLIES",
        "raw_invoice_number": "INV-ACM-20251120",
        "raw_invoice_date": "2025-11-20",
        "raw_po_number": "PO-2025-0009",
        "raw_currency": "USD",
        "raw_subtotal": "4197.75",
        "raw_tax_amount": "377.80",
        "raw_total_amount": "4575.55",
        "invoice_number": "INV-ACM-20251120",
        "normalized_invoice_number": "INVACM20251120",
        "invoice_date": date(2025, 11, 20),
        "po_number": "PO-2025-0009",
        "normalized_po_number": "PO20250009",
        "currency": "USD",
        "subtotal": Decimal("4197.75"),
        "tax_amount": Decimal("377.80"),
        "total_amount": Decimal("4575.55"),
        "vendor_code": "V-1001",
        "status": "READY_FOR_RECON",
        "extraction_confidence": 0.90,
        "extraction_remarks": "Clean extraction. Vendor alias detected. Quantities 3-5% above PO. One line description uses shorthand.",
        "lines": [
            {"line_number": 1, "raw_description": "M8 Hex Bolt 8.8 (Box/100)", "raw_quantity": "52", "raw_unit_price": "28.00", "raw_tax_amount": "130.88", "raw_line_amount": "1456.00", "description": "M8 Hex Bolt 8.8 (Box/100)", "normalized_description": "m8 hex bolt 8 8 box 100", "quantity": Decimal("52"), "unit_price": Decimal("28.00"), "tax_amount": Decimal("130.88"), "line_amount": Decimal("1456.00"), "extraction_confidence": 0.91},
            {"line_number": 2, "raw_description": "M8 Hex Nut Gr8 (Box/100)", "raw_quantity": "52", "raw_unit_price": "14.00", "raw_tax_amount": "65.52", "raw_line_amount": "728.00", "description": "M8 Hex Nut Gr8 (Box/100)", "normalized_description": "m8 hex nut gr8 box 100", "quantity": Decimal("52"), "unit_price": Decimal("14.00"), "tax_amount": Decimal("65.52"), "line_amount": Decimal("728.00"), "extraction_confidence": 0.90},
            {"line_number": 3, "raw_description": "SS Flat Washer M8 (Box/200)", "raw_quantity": "31", "raw_unit_price": "18.50", "raw_tax_amount": "51.62", "raw_line_amount": "573.50", "description": "SS Flat Washer M8 (Box/200)", "normalized_description": "ss flat washer m8 box 200", "quantity": Decimal("31"), "unit_price": Decimal("18.50"), "tax_amount": Decimal("51.62"), "line_amount": Decimal("573.50"), "extraction_confidence": 0.89},
            {"line_number": 4, "raw_description": "QC Fitting 1/2in", "raw_quantity": "208", "raw_unit_price": "6.94", "raw_tax_amount": "129.78", "raw_line_amount": "1443.52", "description": "QC Fitting 1/2in", "normalized_description": "qc fitting 1 2in", "quantity": Decimal("208"), "unit_price": Decimal("6.94"), "tax_amount": Decimal("129.78"), "line_amount": Decimal("1443.52"), "extraction_confidence": 0.87},
        ],
    },
]


# ───────────────────────────────────────────────────────────────────────
# Reconciliation config seed data
# ───────────────────────────────────────────────────────────────────────
RECON_CONFIGS = [
    {
        "name": "Default Production",
        "quantity_tolerance_pct": 2.0,
        "price_tolerance_pct": 1.0,
        "amount_tolerance_pct": 1.0,
        "auto_close_on_match": True,
        "enable_agents": True,
        "extraction_confidence_threshold": 0.75,
        "is_default": True,
    },
    {
        "name": "Strict - No Tolerance",
        "quantity_tolerance_pct": 0.0,
        "price_tolerance_pct": 0.0,
        "amount_tolerance_pct": 0.0,
        "auto_close_on_match": False,
        "enable_agents": True,
        "extraction_confidence_threshold": 0.90,
        "is_default": False,
    },
    {
        "name": "Relaxed - High Tolerance",
        "quantity_tolerance_pct": 5.0,
        "price_tolerance_pct": 3.0,
        "amount_tolerance_pct": 3.0,
        "auto_close_on_match": True,
        "enable_agents": True,
        "extraction_confidence_threshold": 0.60,
        "is_default": False,
    },
    {
        "name": "Agents Disabled",
        "quantity_tolerance_pct": 2.0,
        "price_tolerance_pct": 1.0,
        "amount_tolerance_pct": 1.0,
        "auto_close_on_match": True,
        "enable_agents": False,
        "extraction_confidence_threshold": 0.75,
        "is_default": False,
    },
]


# ───────────────────────────────────────────────────────────────────────
# Agent definitions seed data
# ───────────────────────────────────────────────────────────────────────
AGENT_DEFINITIONS = [
    {
        "agent_type": AgentType.INVOICE_UNDERSTANDING,
        "name": "Invoice Understanding Agent",
        "description": "Inspects extracted invoice completeness, identifies ambiguous fields, flags low-confidence line items, and recommends whether extraction is sufficient for reconciliation.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are an invoice understanding specialist. Analyze the extracted invoice data and assess completeness, identify suspicious or ambiguous fields, and recommend whether the extraction is sufficient for reconciliation. Output structured JSON with your assessment.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["invoice_details", "vendor_search"],
            "confidence_threshold": 0.75,
            "focus_areas": ["extraction_confidence", "field_completeness", "vendor_validation"],
        },
    },
    {
        "agent_type": AgentType.PO_RETRIEVAL,
        "name": "PO Retrieval Agent",
        "description": "Finds the best PO candidate using exact or fuzzy matching. Falls back to alternate lookup by vendor, amount, and date patterns when PO number is unclear.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are a purchase order retrieval specialist. Use available tools to find the best PO candidate for the given invoice. Prefer exact PO number match. If uncertain, use alternate lookup by vendor, amount, and date. Never invent a PO. Output structured JSON.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["po_lookup", "vendor_search", "invoice_details"],
            "fuzzy_match_threshold": 0.8,
        },
    },
    {
        "agent_type": AgentType.GRN_RETRIEVAL,
        "name": "GRN Retrieval Agent",
        "description": "Retrieves related GRNs, summarizes receipt situation, identifies partial receipts or multiple GRNs, and explains cumulative received quantities.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are a goods receipt specialist. Retrieve and analyze GRN data for the given PO. Summarize the receipt situation, including partial receipts and multiple GRNs. Explain cumulative received quantities. Output structured JSON.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["grn_lookup", "po_lookup"],
        },
    },
    {
        "agent_type": AgentType.RECONCILIATION_ASSIST,
        "name": "Reconciliation Assist Agent",
        "description": "Consumes deterministic comparison outputs and provides user-readable reasoning explaining why the case is matched, partial, or unmatched.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are a reconciliation explanation specialist. Analyze the deterministic reconciliation results and provide clear, user-readable reasoning about the match status. Explain specific mismatches and their business implications. Output structured JSON.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["reconciliation_summary", "exception_list", "invoice_details", "po_lookup"],
        },
    },
    {
        "agent_type": AgentType.EXCEPTION_ANALYSIS,
        "name": "Exception Analysis Agent",
        "description": "Interprets structured exceptions, groups related issues, explains likely business causes, and suggests next actions.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are an exception analysis specialist. Analyze the reconciliation exceptions and group related issues. Explain the likely business cause for each exception and suggest corrective actions. Output structured JSON.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["exception_list", "reconciliation_summary", "invoice_details", "po_lookup", "grn_lookup"],
        },
    },
    {
        "agent_type": AgentType.REVIEW_ROUTING,
        "name": "Review Routing Agent",
        "description": "Decides appropriate routing recommendation: AP review, procurement review, manager escalation, reprocess extraction, or vendor clarification.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are a review routing specialist. Based on the reconciliation results, exceptions, and confidence scores, decide the appropriate routing for this case. Use rules and thresholds to recommend: AP review, procurement review, manager escalation, reprocess extraction, or vendor clarification. Output structured JSON.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["exception_list", "reconciliation_summary", "invoice_details"],
            "escalation_threshold": 10000.00,
            "auto_route_rules": {
                "low_confidence": "REPROCESS_EXTRACTION",
                "vendor_mismatch": "SEND_TO_VENDOR_CLARIFICATION",
                "price_mismatch_high_value": "ESCALATE_TO_MANAGER",
                "quantity_mismatch": "SEND_TO_PROCUREMENT",
            },
        },
    },
    {
        "agent_type": AgentType.CASE_SUMMARY,
        "name": "Case Summary Agent",
        "description": "Generates concise enterprise summary for UI, summarizing checks performed, mismatches found, recommendations, and a reviewer-ready narrative.",
        "enabled": True,
        "llm_model": "gpt-4o",
        "system_prompt": "You are a case summary specialist. Generate a concise enterprise summary suitable for reviewer UI display. Summarize: checks performed, mismatches found, recommendations made, and provide a reviewer-ready narrative. Output structured JSON.",
        "max_retries": 2,
        "timeout_seconds": 120,
        "config_json": {
            "allowed_tools": ["reconciliation_summary", "exception_list"],
            "max_summary_length": 500,
        },
    },
]


# ───────────────────────────────────────────────────────────────────────
# Tool definitions seed data
# ───────────────────────────────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "name": "po_lookup",
        "description": "Look up a Purchase Order by PO number. Returns PO header and line items.",
        "input_schema": {"type": "object", "properties": {"po_number": {"type": "string", "description": "The PO number to look up"}}, "required": ["po_number"]},
        "output_schema": {"type": "object", "properties": {"found": {"type": "boolean"}, "po_number": {"type": "string"}, "vendor": {"type": "string"}, "line_items": {"type": "array"}}},
        "enabled": True,
        "module_path": "apps.tools.registry.tools.POLookupTool",
    },
    {
        "name": "grn_lookup",
        "description": "Look up Goods Receipt Notes for a given PO number. Returns GRN headers and received quantities.",
        "input_schema": {"type": "object", "properties": {"po_number": {"type": "string", "description": "The PO number to find GRNs for"}}, "required": ["po_number"]},
        "output_schema": {"type": "object", "properties": {"found": {"type": "boolean"}, "grn_count": {"type": "integer"}, "grns": {"type": "array"}}},
        "enabled": True,
        "module_path": "apps.tools.registry.tools.GRNLookupTool",
    },
    {
        "name": "vendor_search",
        "description": "Search for a vendor by name, code, or alias. Use when the invoice vendor doesn't match the PO vendor.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Vendor name, code, or alias to search for"}}, "required": ["query"]},
        "output_schema": {"type": "object", "properties": {"count": {"type": "integer"}, "vendors": {"type": "array"}}},
        "enabled": True,
        "module_path": "apps.tools.registry.tools.VendorSearchTool",
    },
    {
        "name": "invoice_details",
        "description": "Get full details of an invoice including header, line items, and extraction metadata.",
        "input_schema": {"type": "object", "properties": {"invoice_id": {"type": "integer", "description": "The Invoice PK"}}, "required": ["invoice_id"]},
        "output_schema": {"type": "object", "properties": {"invoice_id": {"type": "integer"}, "invoice_number": {"type": "string"}, "line_items": {"type": "array"}}},
        "enabled": True,
        "module_path": "apps.tools.registry.tools.InvoiceDetailsTool",
    },
    {
        "name": "exception_list",
        "description": "Retrieve all reconciliation exceptions for a given ReconciliationResult.",
        "input_schema": {"type": "object", "properties": {"reconciliation_result_id": {"type": "integer", "description": "The ReconciliationResult PK"}}, "required": ["reconciliation_result_id"]},
        "output_schema": {"type": "object", "properties": {"reconciliation_result_id": {"type": "integer"}, "exceptions": {"type": "array"}}},
        "enabled": True,
        "module_path": "apps.tools.registry.tools.ExceptionListTool",
    },
    {
        "name": "reconciliation_summary",
        "description": "Get the reconciliation result summary including match status, confidence, and header-level evidence.",
        "input_schema": {"type": "object", "properties": {"reconciliation_result_id": {"type": "integer", "description": "The ReconciliationResult PK"}}, "required": ["reconciliation_result_id"]},
        "output_schema": {"type": "object", "properties": {"reconciliation_result_id": {"type": "integer"}, "match_status": {"type": "string"}, "confidence": {"type": "number"}}},
        "enabled": True,
        "module_path": "apps.tools.registry.tools.ReconciliationSummaryTool",
    },
]


class Command(BaseCommand):
    help = "Seed the database with realistic PO, GRN, Vendor, Config, Agent, and Tool data for development/demo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Remove existing seed data before inserting new data.",
        )
        parser.add_argument(
            "--only",
            type=str,
            choices=["vendors", "pos", "grns", "config", "invoices", "agents", "tools", "audit", "all"],
            default="all",
            help="Seed only a specific data category.",
        )
        parser.add_argument(
            "--with-invoices",
            action="store_true",
            help="Also seed sample pre-extracted invoices.",
        )

    def handle(self, *args, **options):
        target = options["only"]
        flush = options["flush"]
        with_invoices = options["with_invoices"]

        if flush:
            self._flush(target)

        try:
            with transaction.atomic():
                if target in ("all", "config"):
                    self._seed_config()
                if target in ("all", "vendors"):
                    self._seed_vendors()
                if target in ("all", "pos"):
                    self._seed_purchase_orders()
                if target in ("all", "grns"):
                    self._seed_grns()
                if target in ("all", "agents"):
                    self._seed_agent_definitions()
                if target in ("all", "tools"):
                    self._seed_tool_definitions()
                if with_invoices or target in ("all", "invoices"):
                    self._seed_invoices()
                if target in ("all", "audit"):
                    self._seed_audit_events()
        except Exception as exc:
            raise CommandError(f"Seeding failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Seed data loaded successfully."))

    # ── flush helpers ──────────────────────────────────────────────────
    def _flush(self, target: str):
        self.stdout.write(self.style.WARNING("Flushing existing seed data..."))
        if target in ("all", "invoices"):
            InvoiceLineItem.objects.all().delete()
            Invoice.objects.all().delete()
        if target in ("all", "grns"):
            GRNLineItem.objects.all().delete()
            GoodsReceiptNote.objects.all().delete()
        if target in ("all", "pos"):
            PurchaseOrderLineItem.objects.all().delete()
            PurchaseOrder.objects.all().delete()
        if target in ("all", "vendors"):
            VendorAlias.objects.all().delete()
            Vendor.objects.all().delete()
        if target in ("all", "config"):
            ReconciliationConfig.objects.all().delete()
        if target in ("all", "agents"):
            AgentDefinition.objects.all().delete()
        if target in ("all", "tools"):
            ToolDefinition.objects.all().delete()
        if target in ("all", "audit"):
            AuditEvent.objects.all().delete()
        self.stdout.write("  Flush complete.")

    # ── vendors ────────────────────────────────────────────────────────
    def _seed_vendors(self):
        self.stdout.write("Seeding vendors...")
        for v_data in VENDORS:
            aliases_data = v_data.pop("aliases", [])
            vendor, created = Vendor.objects.update_or_create(
                code=v_data["code"],
                defaults=v_data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} vendor: {vendor}")

            for a_data in aliases_data:
                VendorAlias.objects.update_or_create(
                    vendor=vendor,
                    normalized_alias=a_data["normalized_alias"],
                    defaults=a_data,
                )
            # restore aliases key for idempotency
            v_data["aliases"] = aliases_data
        self.stdout.write(self.style.SUCCESS(f"  {len(VENDORS)} vendors seeded."))

    # ── purchase orders ────────────────────────────────────────────────
    def _seed_purchase_orders(self):
        self.stdout.write("Seeding purchase orders...")
        for po_data in PURCHASE_ORDERS:
            lines_data = po_data["lines"]
            total, tax = _calc_po_totals(lines_data)

            vendor = Vendor.objects.filter(code=po_data["vendor_code"]).first()
            if not vendor:
                self.stderr.write(
                    self.style.ERROR(f"  Vendor {po_data['vendor_code']} not found — seed vendors first.")
                )
                continue

            po, created = PurchaseOrder.objects.update_or_create(
                po_number=po_data["po_number"],
                defaults={
                    "normalized_po_number": po_data["normalized_po_number"],
                    "vendor": vendor,
                    "po_date": po_data["po_date"],
                    "currency": po_data["currency"],
                    "total_amount": total,
                    "tax_amount": tax,
                    "status": po_data["status"],
                    "buyer_name": po_data["buyer_name"],
                    "department": po_data["department"],
                    "notes": po_data.get("notes", ""),
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} PO: {po}")

            # Upsert line items
            existing_lines = set(po.line_items.values_list("line_number", flat=True))
            for line_data in lines_data:
                PurchaseOrderLineItem.objects.update_or_create(
                    purchase_order=po,
                    line_number=line_data["line_number"],
                    defaults={
                        "item_code": line_data["item_code"],
                        "description": line_data["description"],
                        "quantity": line_data["quantity"],
                        "unit_price": line_data["unit_price"],
                        "tax_amount": line_data["tax_amount"],
                        "line_amount": line_data["line_amount"],
                        "unit_of_measure": line_data["unit_of_measure"],
                    },
                )
        self.stdout.write(self.style.SUCCESS(f"  {len(PURCHASE_ORDERS)} purchase orders seeded."))

    # ── goods receipt notes ────────────────────────────────────────────
    def _seed_grns(self):
        self.stdout.write("Seeding goods receipt notes...")
        for grn_data in GOODS_RECEIPT_NOTES:
            po = PurchaseOrder.objects.filter(po_number=grn_data["po_number"]).first()
            if not po:
                self.stderr.write(
                    self.style.ERROR(f"  PO {grn_data['po_number']} not found — seed POs first.")
                )
                continue

            grn, created = GoodsReceiptNote.objects.update_or_create(
                grn_number=grn_data["grn_number"],
                defaults={
                    "purchase_order": po,
                    "vendor": po.vendor,
                    "receipt_date": grn_data["receipt_date"],
                    "status": grn_data["status"],
                    "warehouse": grn_data["warehouse"],
                    "receiver_name": grn_data["receiver_name"],
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} GRN: {grn}")

            for line_data in grn_data["lines"]:
                # Link to PO line
                po_line = PurchaseOrderLineItem.objects.filter(
                    purchase_order=po,
                    line_number=line_data["po_line_number"],
                ).first()

                GRNLineItem.objects.update_or_create(
                    grn=grn,
                    line_number=line_data["line_number"],
                    defaults={
                        "po_line": po_line,
                        "item_code": line_data["item_code"],
                        "description": line_data["description"],
                        "quantity_received": line_data["quantity_received"],
                        "quantity_accepted": line_data["quantity_accepted"],
                        "quantity_rejected": line_data["quantity_rejected"],
                        "unit_of_measure": line_data["unit_of_measure"],
                    },
                )
        self.stdout.write(self.style.SUCCESS(f"  {len(GOODS_RECEIPT_NOTES)} GRNs seeded."))

    # ── reconciliation config ──────────────────────────────────────────
    def _seed_config(self):
        self.stdout.write("Seeding reconciliation configs...")
        for cfg in RECON_CONFIGS:
            obj, created = ReconciliationConfig.objects.update_or_create(
                name=cfg["name"],
                defaults=cfg,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} config: {obj}")
        self.stdout.write(self.style.SUCCESS(f"  {len(RECON_CONFIGS)} configs seeded."))

    # ── agent definitions ──────────────────────────────────────────────
    def _seed_agent_definitions(self):
        self.stdout.write("Seeding agent definitions...")
        for agent_data in AGENT_DEFINITIONS:
            obj, created = AgentDefinition.objects.update_or_create(
                agent_type=agent_data["agent_type"],
                defaults=agent_data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} agent: {obj}")
        self.stdout.write(self.style.SUCCESS(f"  {len(AGENT_DEFINITIONS)} agent definitions seeded."))

    # ── tool definitions ───────────────────────────────────────────────
    def _seed_tool_definitions(self):
        self.stdout.write("Seeding tool definitions...")
        for tool_data in TOOL_DEFINITIONS:
            obj, created = ToolDefinition.objects.update_or_create(
                name=tool_data["name"],
                defaults=tool_data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} tool: {obj}")
        self.stdout.write(self.style.SUCCESS(f"  {len(TOOL_DEFINITIONS)} tool definitions seeded."))

    # ── sample invoices ────────────────────────────────────────────────
    def _seed_invoices(self):
        self.stdout.write("Seeding sample invoices...")
        for inv_data in SAMPLE_INVOICES:
            lines_data = inv_data.pop("lines")
            vendor_code = inv_data.pop("vendor_code")
            status = inv_data.pop("status")

            vendor = Vendor.objects.filter(code=vendor_code).first()
            if not vendor:
                self.stderr.write(
                    self.style.ERROR(f"  Vendor {vendor_code} not found — seed vendors first.")
                )
                inv_data["lines"] = lines_data
                inv_data["vendor_code"] = vendor_code
                inv_data["status"] = status
                continue

            invoice, created = Invoice.objects.update_or_create(
                normalized_invoice_number=inv_data["normalized_invoice_number"],
                defaults={
                    **inv_data,
                    "vendor": vendor,
                    "status": status,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} invoice: {invoice}")

            # Upsert line items
            for line_data in lines_data:
                InvoiceLineItem.objects.update_or_create(
                    invoice=invoice,
                    line_number=line_data["line_number"],
                    defaults=line_data,
                )

            # Restore popped keys for idempotency
            inv_data["lines"] = lines_data
            inv_data["vendor_code"] = vendor_code
            inv_data["status"] = status

        self.stdout.write(self.style.SUCCESS(f"  {len(SAMPLE_INVOICES)} invoices seeded."))

    # ── audit events ───────────────────────────────────────────────────
    def _seed_audit_events(self):
        """Create realistic audit events covering the full invoice lifecycle."""
        self.stdout.write("Seeding audit events...")

        from apps.accounts.models import User

        # Pick the first admin user as actor, or None
        admin_user = User.objects.filter(role="ADMIN").first()

        invoices = list(Invoice.objects.all().order_by("id"))
        if not invoices:
            self.stderr.write(self.style.WARNING("  No invoices found — seed invoices first."))
            return

        events_to_create = []
        base_time = timezone.now() - timedelta(days=len(invoices))

        for idx, inv in enumerate(invoices):
            t = base_time + timedelta(days=idx, hours=1)

            # 1. INVOICE_UPLOADED
            events_to_create.append(AuditEvent(
                entity_type="Invoice",
                entity_id=inv.id,
                action=AuditEventType.INVOICE_UPLOADED,
                event_type=AuditEventType.INVOICE_UPLOADED,
                event_description=f"Invoice {inv.invoice_number} uploaded for vendor {inv.raw_vendor_name}",
                performed_by=admin_user,
                metadata_json={"invoice_number": inv.invoice_number, "vendor": inv.raw_vendor_name, "po_number": inv.po_number},
                created_at=t,
            ))

            # 2. EXTRACTION_COMPLETED
            t += timedelta(minutes=3)
            events_to_create.append(AuditEvent(
                entity_type="Invoice",
                entity_id=inv.id,
                action=AuditEventType.EXTRACTION_COMPLETED,
                event_type=AuditEventType.EXTRACTION_COMPLETED,
                event_description=f"Data extraction completed with {inv.extraction_confidence:.0%} confidence",
                performed_by=None,
                performed_by_agent="ExtractionPipeline",
                metadata_json={"confidence": float(inv.extraction_confidence or 0), "line_count": inv.line_items.count()},
                created_at=t,
            ))

            # 3. Events for invoices that went through reconciliation
            if inv.status in ("RECONCILED", "READY_FOR_RECON"):
                t += timedelta(minutes=10)
                events_to_create.append(AuditEvent(
                    entity_type="Invoice",
                    entity_id=inv.id,
                    action=AuditEventType.RECONCILIATION_STARTED,
                    event_type=AuditEventType.RECONCILIATION_STARTED,
                    event_description=f"Reconciliation started for {inv.invoice_number} against {inv.po_number}",
                    performed_by=admin_user,
                    metadata_json={"po_number": inv.po_number},
                    created_at=t,
                ))

                t += timedelta(minutes=2)
                events_to_create.append(AuditEvent(
                    entity_type="Invoice",
                    entity_id=inv.id,
                    action=AuditEventType.RECONCILIATION_COMPLETED,
                    event_type=AuditEventType.RECONCILIATION_COMPLETED,
                    event_description=f"Reconciliation completed for {inv.invoice_number}",
                    performed_by=admin_user,
                    metadata_json={"po_number": inv.po_number, "status": inv.status},
                    created_at=t,
                ))

            # 4. Agent events for reconciled invoices
            if inv.status == "RECONCILED":
                t += timedelta(minutes=1)
                events_to_create.append(AuditEvent(
                    entity_type="Invoice",
                    entity_id=inv.id,
                    action=AuditEventType.AGENT_RUN_STARTED,
                    event_type=AuditEventType.AGENT_RUN_STARTED,
                    event_description=f"Agent pipeline started for {inv.invoice_number}",
                    performed_by_agent="AgentOrchestrator",
                    metadata_json={"invoice_number": inv.invoice_number},
                    created_at=t,
                ))

                t += timedelta(minutes=5)
                events_to_create.append(AuditEvent(
                    entity_type="Invoice",
                    entity_id=inv.id,
                    action=AuditEventType.AGENT_RUN_COMPLETED,
                    event_type=AuditEventType.AGENT_RUN_COMPLETED,
                    event_description=f"Agent pipeline completed for {inv.invoice_number}",
                    performed_by_agent="AgentOrchestrator",
                    metadata_json={"invoice_number": inv.invoice_number},
                    created_at=t,
                ))

                t += timedelta(seconds=30)
                events_to_create.append(AuditEvent(
                    entity_type="Invoice",
                    entity_id=inv.id,
                    action=AuditEventType.AGENT_RECOMMENDATION_CREATED,
                    event_type=AuditEventType.AGENT_RECOMMENDATION_CREATED,
                    event_description=f"Agent 'CASE_SUMMARY' created recommendation for {inv.invoice_number}",
                    performed_by_agent="CASE_SUMMARY",
                    metadata_json={"recommendation_type": "ESCALATE_TO_MANAGER", "confidence": 0.85},
                    created_at=t,
                ))

        AuditEvent.objects.bulk_create(events_to_create, ignore_conflicts=False)
        self.stdout.write(self.style.SUCCESS(f"  {len(events_to_create)} audit events seeded."))
