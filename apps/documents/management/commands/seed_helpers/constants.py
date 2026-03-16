"""
THREE_WAY PO Invoice Seed Data — Constants & Master Data Definitions.

Deterministic data for goods-oriented THREE_WAY invoice scenarios:
vendors, warehouses, cost centers, line item catalogs, and scenario definitions.

Domain: McDonald's Saudi Arabia AP automation — goods procurement path.
"""
from __future__ import annotations

# ============================================================
# COST CENTERS
# ============================================================

COST_CENTERS = [
    {"code": "OPS_RIYADH", "name": "Store Operations — Riyadh"},
    {"code": "OPS_JEDDAH", "name": "Store Operations — Jeddah"},
    {"code": "OPS_DAMMAM", "name": "Store Operations — Dammam"},
    {"code": "SUPPLY_CHAIN", "name": "Supply Chain Management"},
    {"code": "WAREHOUSE_OPS", "name": "Warehouse Operations"},
]

# ============================================================
# WAREHOUSES / RECEIVING LOCATIONS
# ============================================================

WAREHOUSES = [
    {"code": "RIYADH_DC", "name": "Riyadh Distribution Center", "city": "Riyadh",
     "aliases": ["Riyadh DC", "Riyadh Dist Center", "Riyadh Distribution Warehouse",
                 "RUH DC", "RUH Distribution Center", "Central DC Riyadh"]},
    {"code": "JEDDAH_DC", "name": "Jeddah Distribution Center", "city": "Jeddah",
     "aliases": ["Jeddah DC", "Jeddah Dist Center", "JED DC",
                 "Jeddah Distribution Warehouse", "JED Distribution Center"]},
    {"code": "DAMMAM_DC", "name": "Dammam Distribution Center", "city": "Dammam",
     "aliases": ["Dammam DC", "Dammam Dist Center", "DMM DC",
                 "Dammam Logistics Hub", "Eastern Province DC"]},
    {"code": "CENTRAL_KITCHEN", "name": "Central Kitchen — Riyadh", "city": "Riyadh",
     "aliases": ["Central Kitchen", "CK Riyadh", "Central Production Kitchen",
                 "McDonald's Central Kitchen"]},
]

# ============================================================
# BRANCHES
# ============================================================

BRANCHES = [
    {"code": "BR-RUH-001", "name": "McDonald's Olaya Street", "city": "Riyadh"},
    {"code": "BR-RUH-002", "name": "McDonald's King Fahd Road", "city": "Riyadh"},
    {"code": "BR-JED-001", "name": "McDonald's Tahlia Street", "city": "Jeddah"},
    {"code": "BR-JED-002", "name": "McDonald's Corniche", "city": "Jeddah"},
    {"code": "BR-DMM-001", "name": "McDonald's King Saud Street", "city": "Dammam"},
    {"code": "BR-DMM-002", "name": "McDonald's Dhahran Mall", "city": "Dammam"},
]

# ============================================================
# GOODS-ORIENTED VENDORS (THREE_WAY path)
# ============================================================

THREE_WAY_VENDORS = [
    # --- Frozen Food Suppliers ---
    {
        "code": "V3W-001", "name": "Arabian Foodstuff Co. Ltd.",
        "category": "Frozen Foods", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "310456789100003",
        "payment_terms": "Net 30",
        "contact_email": "ar@arabianfoodstuff.com.sa",
        "address": "Industrial Area, 2nd Phase, Riyadh 14332",
        "aliases": [
            "Arabian Foodstuff", "AFC Ltd", "Arabian Foodstuff Company",
            "شركة المواد الغذائية العربية", "ARABIAN FOODSTUF CO",  # OCR typo
        ],
    },
    {
        "code": "V3W-002", "name": "Al Kabeer Frozen Foods",
        "category": "Frozen Foods", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "310567891200003",
        "payment_terms": "Net 30",
        "contact_email": "orders@alkabeer.com.sa",
        "address": "Jeddah Industrial City, Block 7",
        "aliases": [
            "Al-Kabeer", "Kabeer Frozen", "Al Kabeer Frozen",
            "الكبير للأغذية المجمدة", "ALKABEER FROZEN FDS",  # OCR truncation
        ],
    },
    # --- Packaging Suppliers ---
    {
        "code": "V3W-003", "name": "Napco National Paper Products Co.",
        "category": "Packaging", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "310678912300003",
        "payment_terms": "Net 45",
        "contact_email": "packaging@napco.com.sa",
        "address": "Dammam 2nd Industrial City, P.O. Box 456",
        "aliases": [
            "NAPCO", "Napco National", "Napco Paper Products",
            "نابكو للمنتجات الورقية", "NAPC0 NATIONAL",  # OCR zero-for-O
        ],
    },
    {
        "code": "V3W-004", "name": "Saudi Paper Manufacturing Co.",
        "category": "Packaging", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "310789123400003",
        "payment_terms": "Net 30",
        "contact_email": "sales@saudipaper.com.sa",
        "address": "Riyadh, King Abdullah Road, Suite 206",
        "aliases": [
            "SPM Co", "Saudi Paper Mfg", "Saudi Paper",
            "شركة الورق السعودية", "SAUDI PAPER MFG CO.",
        ],
    },
    # --- Beverage Suppliers ---
    {
        "code": "V3W-005", "name": "Coca-Cola Bottling Co. of Saudi Arabia",
        "category": "Beverages", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "310891234500003",
        "payment_terms": "Net 30",
        "contact_email": "orders@cocacolaksa.com.sa",
        "address": "Jeddah, Al-Madinah Road KM 5",
        "aliases": [
            "CCBA Saudi", "Coca Cola KSA", "Coca-Cola Saudi Arabia",
            "كوكاكولا السعودية", "COCA COLA BOTTLING SA",
        ],
    },
    # --- Restaurant Consumables ---
    {
        "code": "V3W-006", "name": "Al Wazzan Trading & Supplies",
        "category": "Cleaning & Consumables", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "310912345600003",
        "payment_terms": "Net 30",
        "contact_email": "orders@alwazzan.com.sa",
        "address": "Riyadh, Al Sulay Industrial Area",
        "aliases": [
            "Al-Wazzan", "Wazzan Supplies", "Al Wazzan Trading",
            "الوزان للتجارة والتوريدات", "ALWAZZAN TRADING",
        ],
    },
    # --- Kitchen Equipment Spare Parts ---
    {
        "code": "V3W-007", "name": "Henny Penny Arabia LLC",
        "category": "Kitchen Equipment Parts", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "311023456700003",
        "payment_terms": "Net 30",
        "contact_email": "parts.ksa@hennypenny.com",
        "address": "Riyadh, Exit 5 Service Area Industrial",
        "aliases": [
            "Henny Penny KSA", "HP Arabia", "Henny Penny Parts",
            "هيني بيني العربية", "HENNY PENNY ARABIA",
        ],
    },
    # --- Uniforms & Housekeeping ---
    {
        "code": "V3W-008", "name": "Al Hokair Uniform Solutions",
        "category": "Uniforms & Housekeeping", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "311134567800003",
        "payment_terms": "Net 30",
        "contact_email": "uniforms@alhokair.com.sa",
        "address": "Jeddah, Palestine Street, Building 42",
        "aliases": [
            "Al-Hokair Uniforms", "Hokair Uniform", "Al Hokair Solutions",
            "الحكير للأزياء الموحدة", "ALHOKAIR UNIFORM",
        ],
    },
    # --- Food Ingredient Distributor ---
    {
        "code": "V3W-009", "name": "IFFCO Saudi Arabia",
        "category": "Food Ingredients", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "311245678900003",
        "payment_terms": "Net 30",
        "contact_email": "ksa.orders@iffco.com",
        "address": "Dammam, 1st Industrial City, Block 12",
        "aliases": [
            "IFFCO KSA", "IFFCO Saudi", "IFFCO Group KSA",
            "إيفكو السعودية", "IFFC0 SAUDI ARABIA",  # OCR zero-for-O
        ],
    },
    # --- Fries / Potato Products ---
    {
        "code": "V3W-010", "name": "Lamb Weston Arabia",
        "category": "Frozen Potato Products", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "311356789000003",
        "payment_terms": "Net 30",
        "contact_email": "ksa@lambweston.com",
        "address": "Riyadh, Northern Ring Road, Warehouse 15",
        "aliases": [
            "Lamb Weston KSA", "LW Arabia", "Lamb Weston",
            "لامب وستون العربية", "LAMB WEST0N ARABIA",  # OCR zero-for-O
        ],
    },
    # --- Dairy & Sauces ---
    {
        "code": "V3W-011", "name": "Almarai Company",
        "category": "Dairy & Sauces", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "311467890100003",
        "payment_terms": "Net 30",
        "contact_email": "b2b@almarai.com",
        "address": "Riyadh, Al Wasil Industrial, Zone 3",
        "aliases": [
            "Al-Marai", "Almarai Co", "Almarai Company JSC",
            "المراعي", "ALMARAI C0MPANY",  # OCR zero-for-O
        ],
    },
    # --- Bakery & Buns ---
    {
        "code": "V3W-012", "name": "Saudi Modern Bakeries Co.",
        "category": "Bakery & Buns", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "311578901200003",
        "payment_terms": "Net 15",
        "contact_email": "supply@saudibakeries.com.sa",
        "address": "Riyadh, Industrial Area Phase 3",
        "aliases": [
            "Saudi Bakeries", "SMB Co", "Saudi Modern Bakeries",
            "شركة المخابز السعودية الحديثة", "SAUDI MODERN BAKRIES",  # OCR typo
        ],
    },
]

# ============================================================
# AP PROCESSOR SEED USER
# ============================================================

AP_PROCESSOR_USER = {
    "email": "ap.threeway@mcd-ksa.com",
    "first_name": "Fatima",
    "last_name": "Al-Rashid",
    "role": "AP_PROCESSOR",
    "department": "Accounts Payable",
    "password": "SeedPass3Way!2026",
}

# ============================================================
# GOODS LINE ITEM CATALOG
# ============================================================

GOODS_LINE_ITEMS = {
    "Frozen Foods": [
        {"desc": "Frozen Beef Patties (10:1) — 4.5 kg carton", "uom": "CTN", "code": "FRZ-001", "price": 185.00},
        {"desc": "Frozen Chicken Breast Fillet — 2 kg bag", "uom": "BAG", "code": "FRZ-002", "price": 72.50},
        {"desc": "Frozen Crispy Chicken Strips — 3 kg box", "uom": "BOX", "code": "FRZ-003", "price": 95.00},
        {"desc": "Frozen Fish Fillet Patty — 2.5 kg carton", "uom": "CTN", "code": "FRZ-004", "price": 110.00},
        {"desc": "Frozen Chicken McPatty — 4 kg carton", "uom": "CTN", "code": "FRZ-005", "price": 145.00},
        {"desc": "Frozen Chicken Nuggets — 3 kg box", "uom": "BOX", "code": "FRZ-006", "price": 88.00},
    ],
    "Packaging": [
        {"desc": "Paper Cups 16oz McDonald's Branded (1000-ct)", "uom": "CASE", "code": "PKG-001", "price": 120.00},
        {"desc": "Paper Cups 22oz McDonald's Branded (1000-ct)", "uom": "CASE", "code": "PKG-002", "price": 135.00},
        {"desc": "Burger Wrappers — Branded (5000-ct)", "uom": "CASE", "code": "PKG-003", "price": 180.00},
        {"desc": "Takeaway Paper Bags Medium (1000-ct)", "uom": "CASE", "code": "PKG-004", "price": 145.00},
        {"desc": "Napkins White Branded (10000-ct)", "uom": "CASE", "code": "PKG-005", "price": 85.00},
        {"desc": "McFlurry Cups with Lids (500-ct)", "uom": "CASE", "code": "PKG-006", "price": 115.00},
        {"desc": "Fry Cartons Medium (2000-ct)", "uom": "CASE", "code": "PKG-007", "price": 95.00},
    ],
    "Beverages": [
        {"desc": "Coca-Cola Bag-in-Box Syrup — 20L", "uom": "BIB", "code": "BEV-001", "price": 225.00},
        {"desc": "Fanta Orange Syrup BiB — 20L", "uom": "BIB", "code": "BEV-002", "price": 210.00},
        {"desc": "Sprite Syrup BiB — 20L", "uom": "BIB", "code": "BEV-003", "price": 210.00},
        {"desc": "Iced Tea Syrup BiB — 10L", "uom": "BIB", "code": "BEV-004", "price": 135.00},
        {"desc": "CO2 Cylinder Refill — 30 kg", "uom": "CYL", "code": "BEV-005", "price": 95.00},
    ],
    "Cleaning & Consumables": [
        {"desc": "Kitchen Degreaser Concentrate — 5L", "uom": "EA", "code": "CLN-001", "price": 65.00},
        {"desc": "Sanitizer Surface Spray — 5L", "uom": "EA", "code": "CLN-002", "price": 48.00},
        {"desc": "Hand Soap Liquid — 5L canister", "uom": "EA", "code": "CLN-003", "price": 35.00},
        {"desc": "Disposable Vinyl Gloves M (1000-ct)", "uom": "BOX", "code": "CLN-004", "price": 55.00},
        {"desc": "Floor Cleaner Industrial — 10L", "uom": "EA", "code": "CLN-005", "price": 72.00},
    ],
    "Kitchen Equipment Parts": [
        {"desc": "Fryer Heating Element — Henny Penny", "uom": "EA", "code": "KEP-001", "price": 1250.00},
        {"desc": "Grill Platen Assembly — Taylor", "uom": "EA", "code": "KEP-002", "price": 2800.00},
        {"desc": "Soft-Serve Machine Pump Kit", "uom": "EA", "code": "KEP-003", "price": 950.00},
        {"desc": "Fryer Oil Filter Screen Set", "uom": "SET", "code": "KEP-004", "price": 380.00},
        {"desc": "Ice Machine Compressor Unit", "uom": "EA", "code": "KEP-005", "price": 4500.00},
    ],
    "Uniforms & Housekeeping": [
        {"desc": "Crew Uniform Polo Shirt — M/L/XL", "uom": "EA", "code": "UNI-001", "price": 45.00},
        {"desc": "Kitchen Apron — Chef Standard", "uom": "EA", "code": "UNI-002", "price": 28.00},
        {"desc": "Non-Slip Safety Shoes — Black", "uom": "PAIR", "code": "UNI-003", "price": 120.00},
        {"desc": "Disposable Hair Nets (500-ct)", "uom": "BOX", "code": "UNI-004", "price": 35.00},
        {"desc": "Industrial Mop & Bucket Set", "uom": "SET", "code": "UNI-005", "price": 185.00},
    ],
    "Food Ingredients": [
        {"desc": "Vegetable Cooking Oil — 20L drum", "uom": "DRUM", "code": "ING-001", "price": 145.00},
        {"desc": "Premium Sesame Seeds — 25 kg sack", "uom": "SACK", "code": "ING-002", "price": 280.00},
        {"desc": "Flour All-Purpose — 50 kg sack", "uom": "SACK", "code": "ING-003", "price": 95.00},
        {"desc": "Salt Iodized Fine — 25 kg sack", "uom": "SACK", "code": "ING-004", "price": 32.00},
        {"desc": "Sugar White Granulated — 50 kg sack", "uom": "SACK", "code": "ING-005", "price": 88.00},
    ],
    "Frozen Potato Products": [
        {"desc": "Frozen French Fries 9mm — 12.5 kg carton", "uom": "CTN", "code": "FPT-001", "price": 88.00},
        {"desc": "Frozen Hash Browns (120-ct case)", "uom": "CASE", "code": "FPT-002", "price": 95.00},
        {"desc": "Frozen Wedges Seasoned — 10 kg carton", "uom": "CTN", "code": "FPT-003", "price": 78.00},
        {"desc": "Frozen Curly Fries — 10 kg carton", "uom": "CTN", "code": "FPT-004", "price": 92.00},
    ],
    "Dairy & Sauces": [
        {"desc": "Processed Cheese Slices (200-ct)", "uom": "BOX", "code": "DAI-001", "price": 165.00},
        {"desc": "McFlurry Vanilla Soft-Serve Mix — 5L", "uom": "CTN", "code": "DAI-002", "price": 55.00},
        {"desc": "Shake Mix Chocolate — 5L bag", "uom": "BAG", "code": "DAI-003", "price": 48.00},
        {"desc": "Big Mac Sauce — 3L pouch", "uom": "PCH", "code": "DAI-004", "price": 42.00},
        {"desc": "UHT Creamer Portions (500-ct)", "uom": "BOX", "code": "DAI-005", "price": 72.00},
    ],
    "Bakery & Buns": [
        {"desc": "Sesame Seed Burger Buns (48-ct tray)", "uom": "TRAY", "code": "BKR-001", "price": 42.00},
        {"desc": "Big Mac Buns with Seed (48-ct tray)", "uom": "TRAY", "code": "BKR-002", "price": 48.50},
        {"desc": "English Muffins Breakfast (72-ct case)", "uom": "CASE", "code": "BKR-003", "price": 38.00},
        {"desc": "Artisan Roll Buns (36-ct tray)", "uom": "TRAY", "code": "BKR-004", "price": 52.00},
    ],
}

# ============================================================
# THREE_WAY SCENARIO DEFINITIONS
# ============================================================
# Each scenario describes invoice attributes that will deterministically
# produce specific outcomes when reconciliation runs later.
#
# Scenario Buckets:
#   A (1-4)   — Clean matches
#   B (5-8)   — Agent recovery required
#   C (9-12)  — Exception-prone invoices
#   D (13-16) — GRN agent trigger scenarios
#   E (17-20) — Special test conditions
#   F (21-24) — Edge cases / stress tests

SCENARIOS = [
    # =================================================================
    # SCENARIO A — Clean Matches (expected: MATCHED)
    # =================================================================
    {
        "num": 1,
        "tag": "3W-CLEAN-FRIES-RIYADH",
        "vendor_code": "V3W-010",
        "category": "Frozen Potato Products",
        "warehouse": "RIYADH_DC",
        "cost_center": "WAREHOUSE_OPS",
        "branch": "BR-RUH-001",
        "description": "Frozen fries stock replenishment — Riyadh DC, clean PO and GRN",
        "po_format": "clean",            # PO-3W-0001 — clean reference
        "extraction_confidence": 0.96,
        "expected_outcome": "MATCHED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (10, 30),
        "exceptions": [],
        "special": {},
    },
    {
        "num": 2,
        "tag": "3W-CLEAN-PACKAGING-JEDDAH",
        "vendor_code": "V3W-003",
        "category": "Packaging",
        "warehouse": "JEDDAH_DC",
        "cost_center": "OPS_JEDDAH",
        "branch": "BR-JED-001",
        "description": "Packaging material supply — Jeddah DC, perfect match",
        "po_format": "clean",
        "extraction_confidence": 0.94,
        "expected_outcome": "MATCHED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 4,
        "qty_range": (20, 60),
        "exceptions": [],
        "special": {},
    },
    {
        "num": 3,
        "tag": "3W-CLEAN-BEVERAGE-DAMMAM",
        "vendor_code": "V3W-005",
        "category": "Beverages",
        "warehouse": "DAMMAM_DC",
        "cost_center": "OPS_DAMMAM",
        "branch": "BR-DMM-001",
        "description": "Beverage concentrate shipment — Dammam DC, full receipt",
        "po_format": "clean",
        "extraction_confidence": 0.97,
        "expected_outcome": "MATCHED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (5, 15),
        "exceptions": [],
        "special": {},
    },
    {
        "num": 4,
        "tag": "3W-CLEAN-CHEMICALS-BULK",
        "vendor_code": "V3W-006",
        "category": "Cleaning & Consumables",
        "warehouse": "RIYADH_DC",
        "cost_center": "SUPPLY_CHAIN",
        "branch": "BR-RUH-002",
        "description": "Cleaning chemicals bulk supply — Riyadh DC, all quantities match",
        "po_format": "clean",
        "extraction_confidence": 0.93,
        "expected_outcome": "MATCHED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 4,
        "qty_range": (8, 25),
        "exceptions": [],
        "special": {},
    },

    # =================================================================
    # SCENARIO B — Agent Recovery Required
    # =================================================================
    {
        "num": 5,
        "tag": "3W-AGENT-OCR-CHICKEN",
        "vendor_code": "V3W-002",
        "category": "Frozen Foods",
        "warehouse": "RIYADH_DC",
        "cost_center": "WAREHOUSE_OPS",
        "branch": "BR-RUH-001",
        "description": "Chicken patties invoice with OCR-damaged PO reference",
        "po_format": "ocr_damaged",       # P0-3W-0005 (zero for O)
        "extraction_confidence": 0.72,
        "expected_outcome": "PARTIAL_MATCH",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (10, 40),
        "exceptions": [],
        "special": {"ocr_po_variation": "P0-3W-0005"},
    },
    {
        "num": 6,
        "tag": "3W-AGENT-ALIAS-BUNS",
        "vendor_code": "V3W-012",
        "category": "Bakery & Buns",
        "warehouse": "JEDDAH_DC",
        "cost_center": "OPS_JEDDAH",
        "branch": "BR-JED-002",
        "description": "Burger buns invoice with vendor alias variation on OCR",
        "po_format": "clean",
        "extraction_confidence": 0.78,
        "expected_outcome": "PARTIAL_MATCH",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (15, 45),
        "exceptions": [],
        "special": {"vendor_alias_on_invoice": "SAUDI MODERN BAKRIES"},  # OCR typo
    },
    {
        "num": 7,
        "tag": "3W-AGENT-MISSING-PO",
        "vendor_code": "V3W-011",
        "category": "Dairy & Sauces",
        "warehouse": "DAMMAM_DC",
        "cost_center": "OPS_DAMMAM",
        "branch": "BR-DMM-002",
        "description": "Cold chain dairy invoice with missing PO number — agent must infer from vendor + amount",
        "po_format": "missing",            # No PO reference
        "extraction_confidence": 0.81,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 4,
        "qty_range": (10, 30),
        "exceptions": [],
        "special": {},
    },
    {
        "num": 8,
        "tag": "3W-AGENT-WAREHOUSE-NOISE",
        "vendor_code": "V3W-004",
        "category": "Packaging",
        "warehouse": "RIYADH_DC",
        "cost_center": "SUPPLY_CHAIN",
        "branch": "BR-RUH-001",
        "description": "Packaging invoice with warehouse text noise ('Riyadh Dist Center' instead of 'RIYADH_DC')",
        "po_format": "normalized",         # PO3W0008 (no dash)
        "extraction_confidence": 0.75,
        "expected_outcome": "PARTIAL_MATCH",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (25, 70),
        "exceptions": [],
        "special": {"warehouse_on_invoice": "Riyadh Dist Center"},
    },

    # =================================================================
    # SCENARIO C — Exception-Prone Invoices
    # =================================================================
    {
        "num": 9,
        "tag": "3W-EXCEPT-DUPLICATE-PKG",
        "vendor_code": "V3W-003",
        "category": "Packaging",
        "warehouse": "RIYADH_DC",
        "cost_center": "WAREHOUSE_OPS",
        "branch": "BR-RUH-002",
        "description": "Duplicate packaging invoice — same vendor + invoice number as scenario 2",
        "po_format": "clean",
        "extraction_confidence": 0.91,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "VALIDATED",
        "n_lines": 4,
        "qty_range": (20, 60),
        "exceptions": ["DUPLICATE_INVOICE"],
        "special": {"duplicate_of_scenario": 2},
    },
    {
        "num": 10,
        "tag": "3W-EXCEPT-MALFORMED-PO",
        "vendor_code": "V3W-009",
        "category": "Food Ingredients",
        "warehouse": "CENTRAL_KITCHEN",
        "cost_center": "SUPPLY_CHAIN",
        "branch": "BR-RUH-001",
        "description": "Imported stock invoice with malformed PO — 'PO 3W 00!0' garbled by OCR",
        "po_format": "malformed",
        "extraction_confidence": 0.58,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "EXTRACTED",
        "n_lines": 3,
        "qty_range": (5, 20),
        "exceptions": ["EXTRACTION_LOW_CONFIDENCE"],
        "special": {"malformed_po_text": "PO 3W 00!0"},
    },
    {
        "num": 11,
        "tag": "3W-EXCEPT-HIGHVAL-PARTS",
        "vendor_code": "V3W-007",
        "category": "Kitchen Equipment Parts",
        "warehouse": "RIYADH_DC",
        "cost_center": "OPS_RIYADH",
        "branch": "BR-RUH-001",
        "description": "High-value spare parts invoice — SAR 25K+, requires finance approval",
        "po_format": "clean",
        "extraction_confidence": 0.89,
        "expected_outcome": "ESCALATION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (1, 3),
        "exceptions": ["PRICE_MISMATCH"],
        "special": {"high_value": True, "price_inflate_pct": 15},
    },
    {
        "num": 12,
        "tag": "3W-EXCEPT-MISSING-VENDOR",
        "vendor_code": "V3W-001",
        "category": "Frozen Foods",
        "warehouse": "JEDDAH_DC",
        "cost_center": "OPS_JEDDAH",
        "branch": "BR-JED-001",
        "description": "Invoice with missing vendor extraction — OCR failed to read vendor block",
        "po_format": "clean",
        "extraction_confidence": 0.45,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "EXTRACTED",
        "n_lines": 3,
        "qty_range": (10, 25),
        "exceptions": ["EXTRACTION_LOW_CONFIDENCE", "VENDOR_MISMATCH"],
        "special": {"missing_vendor_name": True},
    },

    # =================================================================
    # SCENARIO D — GRN Agent Trigger Scenarios
    # =================================================================
    {
        "num": 13,
        "tag": "3W-GRN-NOT-FOUND",
        "vendor_code": "V3W-001",
        "category": "Frozen Foods",
        "warehouse": "DAMMAM_DC",
        "cost_center": "OPS_DAMMAM",
        "branch": "BR-DMM-001",
        "description": "GRN not found — frozen food invoice linked to PO but no receipt record exists",
        "po_format": "clean",
        "extraction_confidence": 0.92,
        "expected_outcome": "GRN_EXCEPTION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (15, 35),
        "exceptions": ["GRN_NOT_FOUND"],
        "special": {"skip_grn": True},
    },
    {
        "num": 14,
        "tag": "3W-GRN-RECEIPT-SHORTAGE",
        "vendor_code": "V3W-010",
        "category": "Frozen Potato Products",
        "warehouse": "RIYADH_DC",
        "cost_center": "WAREHOUSE_OPS",
        "branch": "BR-RUH-002",
        "description": "Receipt shortage — only 70% of fries cartons received at Riyadh DC",
        "po_format": "clean",
        "extraction_confidence": 0.94,
        "expected_outcome": "GRN_EXCEPTION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (20, 50),
        "exceptions": ["RECEIPT_SHORTAGE", "QTY_MISMATCH"],
        "special": {"receipt_pct": 0.70},
    },
    {
        "num": 15,
        "tag": "3W-GRN-OVER-RECEIPT",
        "vendor_code": "V3W-006",
        "category": "Cleaning & Consumables",
        "warehouse": "JEDDAH_DC",
        "cost_center": "OPS_JEDDAH",
        "branch": "BR-JED-001",
        "description": "Over receipt — cleaning supplies vendor shipped 15% extra",
        "po_format": "clean",
        "extraction_confidence": 0.93,
        "expected_outcome": "GRN_EXCEPTION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 4,
        "qty_range": (10, 30),
        "exceptions": ["OVER_RECEIPT"],
        "special": {"receipt_pct": 1.15},
    },
    {
        "num": 16,
        "tag": "3W-GRN-MULTI-PARTIAL",
        "vendor_code": "V3W-005",
        "category": "Beverages",
        "warehouse": "RIYADH_DC",
        "cost_center": "SUPPLY_CHAIN",
        "branch": "BR-RUH-001",
        "description": "Multi-GRN partial receipt — beverage syrups delivered in 3 drops across days",
        "po_format": "clean",
        "extraction_confidence": 0.95,
        "expected_outcome": "AUTO_CLOSE",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 4,
        "qty_range": (6, 18),
        "exceptions": ["MULTI_GRN_PARTIAL_RECEIPT"],
        "special": {"multi_grn_drops": 3},
    },

    # =================================================================
    # SCENARIO E — Special Test Conditions
    # =================================================================
    {
        "num": 17,
        "tag": "3W-SPECIAL-DELAYED-GRN",
        "vendor_code": "V3W-012",
        "category": "Bakery & Buns",
        "warehouse": "DAMMAM_DC",
        "cost_center": "OPS_DAMMAM",
        "branch": "BR-DMM-002",
        "description": "Delayed GRN — buns receipt posted 3 days after invoice date",
        "po_format": "clean",
        "extraction_confidence": 0.91,
        "expected_outcome": "GRN_EXCEPTION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (20, 40),
        "exceptions": ["DELAYED_RECEIPT"],
        "special": {"grn_delay_days": 3},
    },
    {
        "num": 18,
        "tag": "3W-SPECIAL-WH-MISMATCH",
        "vendor_code": "V3W-002",
        "category": "Frozen Foods",
        "warehouse": "RIYADH_DC",
        "cost_center": "WAREHOUSE_OPS",
        "branch": "BR-RUH-001",
        "description": "Warehouse mismatch — invoice says RIYADH_DC but GRN posted at JEDDAH_DC",
        "po_format": "clean",
        "extraction_confidence": 0.88,
        "expected_outcome": "GRN_EXCEPTION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (10, 30),
        "exceptions": ["RECEIPT_LOCATION_MISMATCH"],
        "special": {"grn_warehouse": "JEDDAH_DC"},
    },
    {
        "num": 19,
        "tag": "3W-SPECIAL-NO-COSTCENTER",
        "vendor_code": "V3W-008",
        "category": "Uniforms & Housekeeping",
        "warehouse": "RIYADH_DC",
        "cost_center": None,  # Missing cost center
        "branch": "BR-RUH-001",
        "description": "Missing cost center — uniform supply invoice without cost allocation",
        "po_format": "clean",
        "extraction_confidence": 0.87,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "VALIDATED",
        "n_lines": 4,
        "qty_range": (20, 80),
        "exceptions": [],
        "special": {"missing_cost_center": True},
    },
    {
        "num": 20,
        "tag": "3W-SPECIAL-NO-TAX",
        "vendor_code": "V3W-009",
        "category": "Food Ingredients",
        "warehouse": "CENTRAL_KITCHEN",
        "cost_center": "SUPPLY_CHAIN",
        "branch": "BR-RUH-002",
        "description": "Missing tax amount — ingredient invoice with blank VAT field",
        "po_format": "clean",
        "extraction_confidence": 0.83,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "VALIDATED",
        "n_lines": 3,
        "qty_range": (8, 20),
        "exceptions": ["TAX_MISMATCH"],
        "special": {"missing_tax": True},
    },

    # =================================================================
    # SCENARIO F — Edge Cases / Stress Tests
    # =================================================================
    {
        "num": 21,
        "tag": "3W-EDGE-AMOUNT-TAX-MISMATCH",
        "vendor_code": "V3W-011",
        "category": "Dairy & Sauces",
        "warehouse": "RIYADH_DC",
        "cost_center": "OPS_RIYADH",
        "branch": "BR-RUH-001",
        "description": "Amount + tax mismatch — dairy invoice totals don't reconcile with line items",
        "po_format": "clean",
        "extraction_confidence": 0.91,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 4,
        "qty_range": (10, 25),
        "exceptions": ["AMOUNT_MISMATCH", "TAX_MISMATCH"],
        "special": {"amount_inflate": 150, "tax_rate_override": 0.05},
    },
    {
        "num": 22,
        "tag": "3W-EDGE-DUP-VENDOR-AMOUNT",
        "vendor_code": "V3W-010",
        "category": "Frozen Potato Products",
        "warehouse": "JEDDAH_DC",
        "cost_center": "OPS_JEDDAH",
        "branch": "BR-JED-002",
        "description": "Duplicate vendor + same amount — second fries invoice matches scenario 1 total exactly",
        "po_format": "clean",
        "extraction_confidence": 0.90,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 3,
        "qty_range": (10, 30),
        "exceptions": ["DUPLICATE_INVOICE"],
        "special": {"mirror_amounts_of_scenario": 1},
    },
    {
        "num": 23,
        "tag": "3W-EDGE-NO-CURRENCY",
        "vendor_code": "V3W-003",
        "category": "Packaging",
        "warehouse": "DAMMAM_DC",
        "cost_center": "OPS_DAMMAM",
        "branch": "BR-DMM-001",
        "description": "Missing currency on invoice — OCR failed to detect currency field",
        "po_format": "hash_prefix",        # PO#3W0023
        "extraction_confidence": 0.68,
        "expected_outcome": "REVIEW_REQUIRED",
        "invoice_status": "EXTRACTED",
        "n_lines": 3,
        "qty_range": (15, 40),
        "exceptions": ["CURRENCY_MISMATCH"],
        "special": {"missing_currency": True},
    },
    {
        "num": 24,
        "tag": "3W-EDGE-INV-QTY-EXCEEDS",
        "vendor_code": "V3W-001",
        "category": "Frozen Foods",
        "warehouse": "RIYADH_DC",
        "cost_center": "WAREHOUSE_OPS",
        "branch": "BR-RUH-001",
        "description": "Invoice qty exceeds received — invoiced 100 cartons but only 80 received (GRN)",
        "po_format": "clean",
        "extraction_confidence": 0.92,
        "expected_outcome": "GRN_EXCEPTION",
        "invoice_status": "READY_FOR_RECON",
        "n_lines": 2,
        "qty_range": (40, 100),
        "exceptions": ["INVOICE_QTY_EXCEEDS_RECEIVED"],
        "special": {"receipt_pct": 0.80},
    },
]


# ============================================================
# PO NUMBER FORMAT VARIATIONS (for agent trigger testing)
# ============================================================

PO_FORMAT_TEMPLATES = {
    "clean": "PO-3W-{num:04d}",                   # PO-3W-0001
    "normalized": "PO3W{num:04d}",                  # PO3W0001
    "hash_prefix": "PO#3W{num:04d}",               # PO#3W0001
    "ocr_damaged": "P0-3W-{num:04d}",              # P0 (zero for O)
    "malformed": None,                               # Overridden per-scenario
    "missing": "",                                   # No PO reference
}
