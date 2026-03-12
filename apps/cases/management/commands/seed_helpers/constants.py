"""
McDonald's Saudi Arabia AP Seed Data — Constants & Master Data Definitions.

All deterministic data used across seed helpers lives here:
vendors, branches, regions, departments, cost centers, user profiles,
scenario definitions, and QSR-specific line item catalogs.
"""
from __future__ import annotations

# ============================================================
# REGIONS & LOCATIONS
# ============================================================

REGIONS = [
    {"code": "RUH", "name": "Riyadh Region"},
    {"code": "JED", "name": "Jeddah / Western Region"},
    {"code": "DMM", "name": "Dammam / Eastern Region"},
    {"code": "MKH", "name": "Makkah Region"},
    {"code": "MDN", "name": "Madinah Region"},
]

# Restaurant branches  (code → name, region_code, city)
BRANCHES = [
    {"code": "BR-RUH-001", "name": "McDonald's Olaya Street", "region": "RUH", "city": "Riyadh"},
    {"code": "BR-RUH-002", "name": "McDonald's King Fahd Road", "region": "RUH", "city": "Riyadh"},
    {"code": "BR-RUH-003", "name": "McDonald's Exit 15 DT", "region": "RUH", "city": "Riyadh"},
    {"code": "BR-RUH-004", "name": "McDonald's Al Malqa", "region": "RUH", "city": "Riyadh"},
    {"code": "BR-JED-001", "name": "McDonald's Tahlia Street", "region": "JED", "city": "Jeddah"},
    {"code": "BR-JED-002", "name": "McDonald's Corniche", "region": "JED", "city": "Jeddah"},
    {"code": "BR-JED-003", "name": "McDonald's Prince Sultan Road", "region": "JED", "city": "Jeddah"},
    {"code": "BR-DMM-001", "name": "McDonald's King Saud Street", "region": "DMM", "city": "Dammam"},
    {"code": "BR-DMM-002", "name": "McDonald's Dhahran Mall", "region": "DMM", "city": "Dammam"},
    {"code": "BR-MKH-001", "name": "McDonald's Aziziyah", "region": "MKH", "city": "Makkah"},
    {"code": "BR-MDN-001", "name": "McDonald's Central Madinah", "region": "MDN", "city": "Madinah"},
    {"code": "BR-MDN-002", "name": "McDonald's Quba Road", "region": "MDN", "city": "Madinah"},
]

# Distribution centers / warehouses
WAREHOUSES = [
    {"code": "WH-RUH-01", "name": "Riyadh Central Distribution Center", "region": "RUH", "city": "Riyadh"},
    {"code": "WH-JED-01", "name": "Jeddah Distribution Center", "region": "JED", "city": "Jeddah"},
    {"code": "WH-DMM-01", "name": "Dammam Logistics Hub", "region": "DMM", "city": "Dammam"},
]

# Departments / Cost Centers
DEPARTMENTS = [
    {"code": "DEPT-PROC", "name": "Procurement", "cost_center": "CC-1010"},
    {"code": "DEPT-SC", "name": "Supply Chain", "cost_center": "CC-1020"},
    {"code": "DEPT-OPS", "name": "Store Operations", "cost_center": "CC-2010"},
    {"code": "DEPT-FAC", "name": "Facilities & Maintenance", "cost_center": "CC-3010"},
    {"code": "DEPT-MKT", "name": "Marketing", "cost_center": "CC-4010"},
    {"code": "DEPT-IT", "name": "Information Technology", "cost_center": "CC-5010"},
    {"code": "DEPT-FIN", "name": "Finance & Accounting", "cost_center": "CC-6010"},
    {"code": "DEPT-HR", "name": "Human Resources", "cost_center": "CC-7010"},
    {"code": "DEPT-QA", "name": "Quality Assurance", "cost_center": "CC-8010"},
]

# ============================================================
# VENDORS — 30 realistic Saudi Arabia QSR vendors
# ============================================================

VENDORS_DATA = [
    # --- Food Ingredients / Frozen Products ---
    {
        "code": "V-001", "name": "Arabian Foodstuff Co. Ltd.",
        "category": "Frozen Proteins", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "300456789100003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "ar@arabianfoodstuff.com.sa",
        "aliases": ["شركة المواد الغذائية العربية", "Arabian Foodstuff", "AFC Ltd"],
    },
    {
        "code": "V-002", "name": "Al Kabeer Frozen Foods",
        "category": "Frozen Proteins", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "300567891200003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "orders@alkabeer.com.sa",
        "aliases": ["الكبير للأغذية المجمدة", "Al-Kabeer", "Kabeer Frozen"],
    },
    # --- Bakery ---
    {
        "code": "V-003", "name": "Saudi Bakeries Co.",
        "category": "Bakery & Buns", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "300678912300003",
        "payment_terms": "Net 15", "path_tendency": "THREE_WAY",
        "contact_email": "supply@saudibakeries.com.sa",
        "aliases": ["شركة المخابز السعودية", "Saudi Bakeries", "SBC Bakery"],
    },
    # --- Dairy & Condiments ---
    {
        "code": "V-004", "name": "Almarai Company",
        "category": "Dairy & Sauces", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "300789123400003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "b2b@almarai.com",
        "aliases": ["المراعي", "Al-Marai", "Almarai Co"],
    },
    {
        "code": "V-005", "name": "Heinz Saudi Arabia",
        "category": "Condiments & Sauces", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "300891234500003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "ksa.orders@heinz.com",
        "aliases": ["هاينز السعودية", "Heinz KSA", "H.J. Heinz Arabia"],
    },
    # --- Beverages ---
    {
        "code": "V-006", "name": "Coca-Cola Bottling Co. of Saudi Arabia",
        "category": "Beverages & Syrups", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "300912345600003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "orders@cocacolaksa.com.sa",
        "aliases": ["كوكاكولا السعودية", "CCBA Saudi", "Coca Cola KSA"],
    },
    # --- Fries / Potato Products ---
    {
        "code": "V-007", "name": "Lamb Weston Arabia",
        "category": "Fries & Potato Products", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301023456700003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "ksa@lambweston.com",
        "aliases": ["لامب وستون العربية", "Lamb Weston KSA", "LW Arabia"],
    },
    # --- Packaging ---
    {
        "code": "V-008", "name": "Napco National Paper Products Co.",
        "category": "Packaging Materials", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301134567800003",
        "payment_terms": "Net 45", "path_tendency": "THREE_WAY",
        "contact_email": "packaging@napco.com.sa",
        "aliases": ["نابكو للمنتجات الورقية", "NAPCO", "Napco National"],
    },
    {
        "code": "V-009", "name": "Saudi Paper Manufacturing Co.",
        "category": "Packaging Materials", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301245678900003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "sales@saudipaper.com.sa",
        "aliases": ["شركة الورق السعودية", "SPM Co", "Saudi Paper Mfg"],
    },
    # --- Cold Chain / Logistics ---
    {
        "code": "V-010", "name": "Almajdouie Logistics",
        "category": "Cold Chain Logistics", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301356789000003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "logistics@almajdouie.com",
        "aliases": ["المجدوعي للخدمات اللوجستية", "Almajdouie", "Majdouie Logistics"],
    },
    {
        "code": "V-011", "name": "Bahri Logistics",
        "category": "Cold Chain Logistics", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301467890100003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "coldchain@bahri.sa",
        "aliases": ["باهري للخدمات اللوجستية", "Bahri", "National Shipping SA"],
    },
    # --- Cleaning & Hygiene ---
    {
        "code": "V-012", "name": "Al Wazzan Trading & Supplies",
        "category": "Cleaning & Hygiene", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301578901200003",
        "payment_terms": "Net 30", "path_tendency": "THREE_WAY",
        "contact_email": "orders@alwazzan.com.sa",
        "aliases": ["الوزان للتجارة والتوريدات", "Al-Wazzan", "Wazzan Supplies"],
    },
    # --- HVAC Maintenance ---
    {
        "code": "V-013", "name": "Zamil Air Conditioners",
        "category": "HVAC Maintenance", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301689012300003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "service@zamilac.com",
        "aliases": ["زامل للمكيفات", "Zamil AC", "Zamil HVAC Services"],
    },
    # --- Kitchen Equipment Service ---
    {
        "code": "V-014", "name": "Henny Penny Arabia LLC",
        "category": "Kitchen Equipment Service", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301790123400003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "service.ksa@hennypenny.com",
        "aliases": ["هيني بيني العربية", "Henny Penny KSA", "HP Arabia"],
    },
    {
        "code": "V-015", "name": "Middleby Saudi Arabia",
        "category": "Kitchen Equipment Service", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301801234500003",
        "payment_terms": "Net 45", "path_tendency": "TWO_WAY",
        "contact_email": "ksa.service@middleby.com",
        "aliases": ["ميدلبي السعودية", "Middleby KSA"],
    },
    # --- Pest Control ---
    {
        "code": "V-016", "name": "Rentokil Initial Saudi Arabia",
        "category": "Pest Control", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "301912345600003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "ksa@rentokil.com",
        "aliases": ["رينتوكيل السعودية", "Rentokil KSA", "Rentokil Initial"],
    },
    # --- Signage / Branding ---
    {
        "code": "V-017", "name": "Neon Arabia Signage Co.",
        "category": "Signage & Branding", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302023456700003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "projects@neonarabia.com.sa",
        "aliases": ["نيون العربية للإعلانات", "Neon Arabia", "NA Signage"],
    },
    # --- Telecom / Internet ---
    {
        "code": "V-018", "name": "Saudi Telecom Company (STC)",
        "category": "Telecom & Internet", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302134567800003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "corporate@stc.com.sa",
        "aliases": ["الاتصالات السعودية", "STC", "STC Business"],
    },
    {
        "code": "V-019", "name": "Mobily Business Solutions",
        "category": "Telecom & Internet", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302245678900003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "b2b@mobily.com.sa",
        "aliases": ["موبايلي للحلول التجارية", "Mobily", "Etihad Etisalat"],
    },
    # --- Utilities ---
    {
        "code": "V-020", "name": "Saudi Electricity Company (SEC)",
        "category": "Utilities", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302356789000003",
        "payment_terms": "Net 15", "path_tendency": "NON_PO",
        "contact_email": "corporate@se.com.sa",
        "aliases": ["الشركة السعودية للكهرباء", "SEC", "Saudi Electric"],
    },
    {
        "code": "V-021", "name": "National Water Company (NWC)",
        "category": "Utilities", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302467890100003",
        "payment_terms": "Net 15", "path_tendency": "NON_PO",
        "contact_email": "corporate@nwc.com.sa",
        "aliases": ["شركة المياه الوطنية", "NWC"],
    },
    # --- Municipality / Compliance ---
    {
        "code": "V-022", "name": "Riyadh Municipality",
        "category": "Government & Compliance", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "",
        "payment_terms": "Due on Receipt", "path_tendency": "NON_PO",
        "contact_email": "",
        "aliases": ["أمانة مدينة الرياض", "Amanat Al-Riyadh"],
    },
    {
        "code": "V-023", "name": "Jeddah Municipality",
        "category": "Government & Compliance", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "",
        "payment_terms": "Due on Receipt", "path_tendency": "NON_PO",
        "contact_email": "",
        "aliases": ["أمانة محافظة جدة", "Amanat Jeddah"],
    },
    # --- Facility Maintenance ---
    {
        "code": "V-024", "name": "Saudi Services Co. Ltd. (SSCO)",
        "category": "Facility Maintenance", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302789012300003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "contracts@ssco.com.sa",
        "aliases": ["الشركة السعودية للخدمات", "SSCO", "Saudi Services"],
    },
    # --- Security ---
    {
        "code": "V-025", "name": "G4S Saudi Arabia",
        "category": "Security Services", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302890123400003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "ksa@g4s.com",
        "aliases": ["جي4اس السعودية", "G4S KSA"],
    },
    # --- Consulting / Audit ---
    {
        "code": "V-026", "name": "KPMG Al Fozan & Partners",
        "category": "Consulting & Audit", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "302901234500003",
        "payment_terms": "Net 45", "path_tendency": "NON_PO",
        "contact_email": "ksa@kpmg.com",
        "aliases": ["كي بي إم جي الفوزان", "KPMG Saudi", "KPMG Al Fozan"],
    },
    # --- HR / Staffing ---
    {
        "code": "V-027", "name": "Olayan Manpower Services",
        "category": "Recruitment & Staffing", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "303012345600003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "staffing@olayan.com.sa",
        "aliases": ["العليان للقوى العاملة", "Olayan Manpower"],
    },
    # --- Marketing Agency ---
    {
        "code": "V-028", "name": "Leo Burnett Riyadh",
        "category": "Marketing & Agency", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "303123456700003",
        "payment_terms": "Net 45", "path_tendency": "NON_PO",
        "contact_email": "riyadh@leoburnett.com",
        "aliases": ["ليو بيرنت الرياض", "Leo Burnett KSA"],
    },
    # --- Waste Management ---
    {
        "code": "V-029", "name": "Saudi Waste Management Co. (SWMC)",
        "category": "Waste Management", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "303234567800003",
        "payment_terms": "Net 30", "path_tendency": "TWO_WAY",
        "contact_email": "service@swmc.com.sa",
        "aliases": ["الشركة السعودية لإدارة النفايات", "SWMC"],
    },
    # --- Training ---
    {
        "code": "V-030", "name": "Bayan Training Institute",
        "category": "Training & Development", "country": "Saudi Arabia",
        "currency": "SAR", "tax_id": "303345678900003",
        "payment_terms": "Net 30", "path_tendency": "NON_PO",
        "contact_email": "corporate@bayan.edu.sa",
        "aliases": ["معهد بيان للتدريب", "Bayan Institute"],
    },
]

# ============================================================
# USERS / REVIEW ROLES
# ============================================================

SEED_USERS = [
    {"email": "admin@mcd-ksa.com", "first_name": "System", "last_name": "Admin",
     "role": "ADMIN", "department": "IT", "is_staff": True, "is_superuser": True},
    {"email": "ap.processor@mcd-ksa.com", "first_name": "Fatima", "last_name": "Al-Rashid",
     "role": "AP_PROCESSOR", "department": "Accounts Payable"},
    {"email": "ap.processor2@mcd-ksa.com", "first_name": "Layla", "last_name": "Al-Zahrani",
     "role": "AP_PROCESSOR", "department": "Accounts Payable"},
    {"email": "reviewer@mcd-ksa.com", "first_name": "Ahmed", "last_name": "Al-Harbi",
     "role": "REVIEWER", "department": "Procurement"},
    {"email": "reviewer.sc@mcd-ksa.com", "first_name": "Saad", "last_name": "Al-Dosari",
     "role": "REVIEWER", "department": "Supply Chain"},
    {"email": "reviewer.fac@mcd-ksa.com", "first_name": "Hassan", "last_name": "Al-Shehri",
     "role": "REVIEWER", "department": "Facilities & Maintenance"},
    {"email": "reviewer.senior@mcd-ksa.com", "first_name": "Mona", "last_name": "Al-Qahtani",
     "role": "REVIEWER", "department": "Accounts Payable"},
    {"email": "finance.mgr@mcd-ksa.com", "first_name": "Khalid", "last_name": "Al-Otaibi",
     "role": "FINANCE_MANAGER", "department": "Finance"},
    {"email": "auditor@mcd-ksa.com", "first_name": "Nora", "last_name": "Al-Sadiq",
     "role": "AUDITOR", "department": "Internal Audit"},
    {"email": "warehouse.mgr@mcd-ksa.com", "first_name": "Omar", "last_name": "Al-Ghamdi",
     "role": "REVIEWER", "department": "Warehouse"},
]


# ============================================================
# QSR LINE ITEM CATALOG — realistic product line items
# ============================================================

LINE_ITEMS_CATALOG = {
    "Frozen Proteins": [
        {"desc": "Frozen Beef Patties (10:1) - 4.5 kg carton", "uom": "CTN", "price": 185.00},
        {"desc": "Frozen Chicken Breast Fillet - 2 kg bag", "uom": "BAG", "price": 72.50},
        {"desc": "Frozen Crispy Chicken Strips - 3 kg box", "uom": "BOX", "price": 95.00},
        {"desc": "Frozen Fish Fillet Patty - 2.5 kg carton", "uom": "CTN", "price": 110.00},
        {"desc": "Frozen Chicken McPatty - 4 kg carton", "uom": "CTN", "price": 145.00},
    ],
    "Bakery & Buns": [
        {"desc": "Sesame Seed Burger Buns (48-ct tray)", "uom": "TRAY", "price": 42.00},
        {"desc": "Big Mac Buns with Seed (48-ct tray)", "uom": "TRAY", "price": 48.50},
        {"desc": "English Muffins Breakfast (72-ct case)", "uom": "CASE", "price": 38.00},
        {"desc": "Artisan Roll Buns (36-ct tray)", "uom": "TRAY", "price": 52.00},
    ],
    "Dairy & Sauces": [
        {"desc": "Processed Cheese Slices (200-ct)", "uom": "BOX", "price": 165.00},
        {"desc": "McFlurry Vanilla Soft-Serve Mix - 5L", "uom": "CTN", "price": 55.00},
        {"desc": "Shake Mix Chocolate - 5L bag", "uom": "BAG", "price": 48.00},
        {"desc": "UHT Creamer Portions (500-ct)", "uom": "BOX", "price": 72.00},
    ],
    "Condiments & Sauces": [
        {"desc": "Big Mac Sauce - 3L pouch", "uom": "PCH", "price": 42.00},
        {"desc": "Tomato Ketchup Sachets (500-ct)", "uom": "BOX", "price": 35.00},
        {"desc": "Mustard Sachets (500-ct)", "uom": "BOX", "price": 28.00},
        {"desc": "Mayonnaise Sachets (500-ct)", "uom": "BOX", "price": 32.00},
        {"desc": "Sweet Chilli Sauce Dip (200-ct)", "uom": "BOX", "price": 38.50},
    ],
    "Beverages & Syrups": [
        {"desc": "Coca-Cola Bag-in-Box Syrup - 20L", "uom": "BIB", "price": 225.00},
        {"desc": "Fanta Orange Syrup BiB - 20L", "uom": "BIB", "price": 210.00},
        {"desc": "Sprite Syrup BiB - 20L", "uom": "BIB", "price": 210.00},
        {"desc": "Iced Tea Syrup BiB - 10L", "uom": "BIB", "price": 135.00},
        {"desc": "CO2 Cylinder Refill - 30 kg", "uom": "CYL", "price": 95.00},
    ],
    "Fries & Potato Products": [
        {"desc": "Frozen French Fries 9mm - 12.5 kg carton", "uom": "CTN", "price": 88.00},
        {"desc": "Frozen Hash Browns (120-ct case)", "uom": "CASE", "price": 95.00},
        {"desc": "Frozen Wedges Seasoned - 10 kg carton", "uom": "CTN", "price": 78.00},
    ],
    "Packaging Materials": [
        {"desc": "Paper Cups 16oz - McDonald's Branded (1000-ct)", "uom": "CASE", "price": 120.00},
        {"desc": "Paper Cups 22oz - McDonald's Branded (1000-ct)", "uom": "CASE", "price": 135.00},
        {"desc": "Burger Wrappers - Branded (5000-ct)", "uom": "CASE", "price": 180.00},
        {"desc": "Takeaway Paper Bags - Medium (1000-ct)", "uom": "CASE", "price": 145.00},
        {"desc": "Napkins White - Branded (10000-ct)", "uom": "CASE", "price": 85.00},
        {"desc": "McFlurry Cups with Lids (500-ct)", "uom": "CASE", "price": 115.00},
        {"desc": "Fry Cartons - Medium (2000-ct)", "uom": "CASE", "price": 95.00},
        {"desc": "Salad Containers with Lid (500-ct)", "uom": "CASE", "price": 125.00},
    ],
    "Cleaning & Hygiene": [
        {"desc": "Kitchen Degreaser Concentrate - 5L", "uom": "EA", "price": 65.00},
        {"desc": "Sanitizer Surface Spray - 5L", "uom": "EA", "price": 48.00},
        {"desc": "Hand Soap Liquid - 5L canister", "uom": "EA", "price": 35.00},
        {"desc": "Disposable Vinyl Gloves - M (1000-ct)", "uom": "BOX", "price": 55.00},
        {"desc": "Floor Cleaner Industrial - 10L", "uom": "EA", "price": 72.00},
    ],
}

# Service line items (for TWO_WAY / NON_PO)
SERVICE_LINE_ITEMS = {
    "HVAC Maintenance": [
        {"desc": "HVAC Preventive Maintenance Visit - Restaurant", "uom": "VISIT", "price": 1250.00},
        {"desc": "HVAC Filter Replacement & Cleaning", "uom": "VISIT", "price": 850.00},
        {"desc": "HVAC Emergency Repair Call-Out", "uom": "VISIT", "price": 2200.00},
    ],
    "Kitchen Equipment Service": [
        {"desc": "Fryer Deep Clean & Calibration Service", "uom": "VISIT", "price": 1800.00},
        {"desc": "Grill Maintenance & Parts Replacement", "uom": "VISIT", "price": 2400.00},
        {"desc": "Soft-Serve Machine Quarterly Service", "uom": "VISIT", "price": 1500.00},
        {"desc": "Drive-Through Display Board Repair", "uom": "VISIT", "price": 3200.00},
    ],
    "Pest Control": [
        {"desc": "Monthly Pest Control Service - Restaurant", "uom": "MONTH", "price": 750.00},
        {"desc": "Quarterly Fumigation Service", "uom": "VISIT", "price": 1800.00},
    ],
    "Signage & Branding": [
        {"desc": "Restaurant Exterior Signage Replacement", "uom": "EA", "price": 12500.00},
        {"desc": "Drive-Through Menu Board Update", "uom": "EA", "price": 8500.00},
        {"desc": "Interior Promotional Signage Set", "uom": "SET", "price": 3200.00},
    ],
    "Telecom & Internet": [
        {"desc": "Monthly Fiber Internet Service - Branch", "uom": "MONTH", "price": 890.00},
        {"desc": "Monthly SIP Trunk Service - Branch", "uom": "MONTH", "price": 450.00},
        {"desc": "Monthly POS Network Line", "uom": "MONTH", "price": 350.00},
    ],
    "Cold Chain Logistics": [
        {"desc": "Refrigerated Transport - Riyadh DC to Branches", "uom": "TRIP", "price": 2800.00},
        {"desc": "Refrigerated Transport - Jeddah DC to Branches", "uom": "TRIP", "price": 3200.00},
        {"desc": "Cold Storage Warehousing - Monthly", "uom": "MONTH", "price": 15000.00},
    ],
    "Facility Maintenance": [
        {"desc": "Monthly Facility Maintenance Contract", "uom": "MONTH", "price": 4500.00},
        {"desc": "Emergency Plumbing Repair", "uom": "VISIT", "price": 1200.00},
        {"desc": "Electrical Panel Maintenance Visit", "uom": "VISIT", "price": 1800.00},
    ],
    "Security Services": [
        {"desc": "Monthly Security Guard Service - Branch (2 guards)", "uom": "MONTH", "price": 8500.00},
        {"desc": "CCTV Maintenance & Monitoring - Monthly", "uom": "MONTH", "price": 1200.00},
    ],
    "Waste Management": [
        {"desc": "Monthly Waste Collection & Disposal - Branch", "uom": "MONTH", "price": 2200.00},
        {"desc": "Grease Trap Cleaning Service", "uom": "VISIT", "price": 1500.00},
    ],
}

# Non-PO typical items
NON_PO_LINE_ITEMS = {
    "Utilities": [
        {"desc": "Electricity Bill - Monthly", "uom": "MONTH", "price": 8500.00},
        {"desc": "Water & Sewage Bill - Monthly", "uom": "MONTH", "price": 2200.00},
    ],
    "Government & Compliance": [
        {"desc": "Municipal Trade License Renewal - Annual", "uom": "EA", "price": 5500.00},
        {"desc": "Food Safety Compliance Certificate", "uom": "EA", "price": 3200.00},
        {"desc": "Civil Defense Clearance Fee", "uom": "EA", "price": 2800.00},
    ],
    "Consulting & Audit": [
        {"desc": "Quarterly Internal Audit Service", "uom": "EA", "price": 45000.00},
        {"desc": "Tax Advisory Service - VAT Filing", "uom": "EA", "price": 15000.00},
        {"desc": "Saudization Compliance Consulting", "uom": "EA", "price": 25000.00},
    ],
    "Marketing & Agency": [
        {"desc": "Ramadan Campaign Creative Production", "uom": "EA", "price": 85000.00},
        {"desc": "Social Media Management - Monthly", "uom": "MONTH", "price": 18000.00},
        {"desc": "In-Store Promotional Material Print Run", "uom": "EA", "price": 12000.00},
    ],
    "Training & Development": [
        {"desc": "Food Safety Certification Training (batch of 20)", "uom": "EA", "price": 8500.00},
        {"desc": "Crew Leadership Training Program", "uom": "EA", "price": 12000.00},
    ],
    "Recruitment & Staffing": [
        {"desc": "Temporary Staffing - Ramadan Peak (per head/month)", "uom": "MONTH", "price": 3500.00},
    ],
}


# ============================================================
# SCENARIO DEFINITIONS — 30 deterministic test cases
# ============================================================

SCENARIOS = [
    # ----- TWO_WAY scenarios (1–8) -----
    {
        "num": 1,
        "tag": "2W-TELECOM-PERFECT",
        "path": "TWO_WAY",
        "vendor_code": "V-018",  # STC
        "branch": "BR-RUH-001",
        "category": "Telecom & Internet",
        "status": "CLOSED",
        "match": "MATCHED",
        "priority": "LOW",
        "description": "Perfect match — monthly STC telecom invoice for Olaya Street branch",
        "review_required": False,
        "exceptions": [],
    },
    {
        "num": 2,
        "tag": "2W-HVAC-AMOUNT-MINOR",
        "path": "TWO_WAY",
        "vendor_code": "V-013",  # Zamil AC
        "branch": "BR-RUH-002",
        "category": "HVAC Maintenance",
        "status": "READY_FOR_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "MEDIUM",
        "description": "Facilities maintenance invoice — minor SAR 75 amount mismatch on HVAC service",
        "review_required": True,
        "exceptions": ["AMOUNT_MISMATCH"],
    },
    {
        "num": 3,
        "tag": "2W-SIGNAGE-VAT",
        "path": "TWO_WAY",
        "vendor_code": "V-017",  # Neon Arabia
        "branch": "BR-JED-001",
        "category": "Signage & Branding",
        "status": "IN_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "MEDIUM",
        "description": "Signage vendor invoice — VAT mismatch (invoiced 15% but PO has 0% for exempt item)",
        "review_required": True,
        "exceptions": ["TAX_MISMATCH"],
    },
    {
        "num": 4,
        "tag": "2W-KITCHEN-PRICE",
        "path": "TWO_WAY",
        "vendor_code": "V-014",  # Henny Penny
        "branch": "BR-DMM-001",
        "category": "Kitchen Equipment Service",
        "status": "EXCEPTION_ANALYSIS_IN_PROGRESS",
        "match": "PARTIAL_MATCH",
        "priority": "HIGH",
        "description": "Kitchen equipment service — unit price mismatch on fryer maintenance (SAR 2400 vs PO SAR 1800)",
        "review_required": True,
        "exceptions": ["PRICE_MISMATCH"],
    },
    {
        "num": 5,
        "tag": "2W-ALIAS-RESOLVED",
        "path": "TWO_WAY",
        "vendor_code": "V-016",  # Rentokil
        "branch": "BR-RUH-003",
        "category": "Pest Control",
        "status": "CLOSED",
        "match": "MATCHED",
        "priority": "LOW",
        "description": "Vendor alias ambiguity — invoice showed 'رينتوكيل السعودية' but PO matched correctly via alias",
        "review_required": False,
        "exceptions": [],
    },
    {
        "num": 6,
        "tag": "2W-PO-NOT-FOUND",
        "path": "TWO_WAY",
        "vendor_code": "V-024",  # SSCO Facility
        "branch": "BR-JED-002",
        "category": "Facility Maintenance",
        "status": "ESCALATED",
        "match": "UNMATCHED",
        "priority": "HIGH",
        "description": "Service PO not found — facility maintenance invoice references non-existent PO",
        "review_required": True,
        "exceptions": ["PO_NOT_FOUND"],
    },
    {
        "num": 7,
        "tag": "2W-MULTI-PO-CANDIDATE",
        "path": "TWO_WAY",
        "vendor_code": "V-025",  # G4S
        "branch": "BR-RUH-004",
        "category": "Security Services",
        "status": "READY_FOR_REVIEW",
        "match": "REQUIRES_REVIEW",
        "priority": "MEDIUM",
        "description": "Multiple PO candidates for same G4S security vendor/month — ambiguous match",
        "review_required": True,
        "exceptions": ["PO_NOT_FOUND"],
    },
    {
        "num": 8,
        "tag": "2W-LOGISTICS-MINOR-REVIEW",
        "path": "TWO_WAY",
        "vendor_code": "V-010",  # Almajdouie
        "branch": "WH-RUH-01",
        "category": "Cold Chain Logistics",
        "status": "IN_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "LOW",
        "description": "Logistics invoice routed to review despite minor mismatch within auto-close band",
        "review_required": True,
        "exceptions": ["AMOUNT_MISMATCH"],
    },
    # ----- THREE_WAY scenarios (9–16) -----
    {
        "num": 9,
        "tag": "3W-PACKAGING-PERFECT",
        "path": "THREE_WAY",
        "vendor_code": "V-008",  # NAPCO
        "branch": "WH-RUH-01",
        "category": "Packaging Materials",
        "status": "CLOSED",
        "match": "MATCHED",
        "priority": "LOW",
        "description": "Perfect 3-way match — NAPCO packaging materials fully received at Riyadh DC",
        "review_required": False,
        "exceptions": [],
    },
    {
        "num": 10,
        "tag": "3W-FROZEN-PARTIAL-RECEIPT",
        "path": "THREE_WAY",
        "vendor_code": "V-001",  # Arabian Foodstuff
        "branch": "WH-RUH-01",
        "category": "Frozen Proteins",
        "status": "READY_FOR_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "MEDIUM",
        "description": "Partial receipt — 80% of frozen beef patties received, remainder pending next shipment",
        "review_required": True,
        "exceptions": ["RECEIPT_SHORTAGE", "QTY_MISMATCH"],
    },
    {
        "num": 11,
        "tag": "3W-FRIES-SHORT-RECEIPT",
        "path": "THREE_WAY",
        "vendor_code": "V-007",  # Lamb Weston
        "branch": "WH-JED-01",
        "category": "Fries & Potato Products",
        "status": "EXCEPTION_ANALYSIS_IN_PROGRESS",
        "match": "PARTIAL_MATCH",
        "priority": "HIGH",
        "description": "Short receipt — fries cartons shipment 40 of 50 cases received, 10 cases short",
        "review_required": True,
        "exceptions": ["RECEIPT_SHORTAGE", "QTY_MISMATCH", "AMOUNT_MISMATCH"],
    },
    {
        "num": 12,
        "tag": "3W-CLEANING-OVER-DELIVERY",
        "path": "THREE_WAY",
        "vendor_code": "V-012",  # Al Wazzan
        "branch": "WH-DMM-01",
        "category": "Cleaning & Hygiene",
        "status": "IN_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "MEDIUM",
        "description": "Over-delivery — cleaning supplies vendor shipped 10% extra on two line items",
        "review_required": True,
        "exceptions": ["OVER_RECEIPT"],
    },
    {
        "num": 13,
        "tag": "3W-INGREDIENT-NO-GRN",
        "path": "THREE_WAY",
        "vendor_code": "V-002",  # Al Kabeer
        "branch": "WH-RUH-01",
        "category": "Frozen Proteins",
        "status": "GRN_ANALYSIS_IN_PROGRESS",
        "match": "UNMATCHED",
        "priority": "HIGH",
        "description": "Missing GRN — frozen chicken invoice received but warehouse hasn't posted GRN yet",
        "review_required": True,
        "exceptions": ["GRN_NOT_FOUND"],
    },
    {
        "num": 14,
        "tag": "3W-MULTI-GRN-AGGREGATE",
        "path": "THREE_WAY",
        "vendor_code": "V-006",  # Coca-Cola
        "branch": "WH-RUH-01",
        "category": "Beverages & Syrups",
        "status": "CLOSED",
        "match": "MATCHED",
        "priority": "LOW",
        "description": "Multi-GRN aggregation — beverage syrups delivered in 3 drops, all GRNs match PO total",
        "review_required": False,
        "exceptions": [],
    },
    {
        "num": 15,
        "tag": "3W-PACKAGING-REJECTED",
        "path": "THREE_WAY",
        "vendor_code": "V-009",  # Saudi Paper
        "branch": "WH-JED-01",
        "category": "Packaging Materials",
        "status": "READY_FOR_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "HIGH",
        "description": "Rejected quantity — 500 burger wrappers damaged on receipt, GRN shows rejected qty",
        "review_required": True,
        "exceptions": ["RECEIPT_SHORTAGE", "QTY_MISMATCH"],
    },
    {
        "num": 16,
        "tag": "3W-BAKERY-DELAYED-GRN",
        "path": "THREE_WAY",
        "vendor_code": "V-003",  # Saudi Bakeries
        "branch": "WH-RUH-01",
        "category": "Bakery & Buns",
        "status": "REVIEW_COMPLETED",
        "match": "MATCHED",
        "priority": "LOW",
        "description": "Delayed GRN — buns received 2 days after invoice date but operationally confirmed",
        "review_required": False,
        "exceptions": ["DELAYED_RECEIPT"],
    },
    # ----- NON_PO scenarios (17–24) -----
    {
        "num": 17,
        "tag": "NP-MUNICIPALITY-CLEAN",
        "path": "NON_PO",
        "vendor_code": "V-022",  # Riyadh Municipality
        "branch": "BR-RUH-001",
        "category": "Government & Compliance",
        "status": "CLOSED",
        "match": None,
        "priority": "LOW",
        "description": "Clean non-PO — Riyadh municipality trade license renewal, validated and approved",
        "review_required": False,
        "exceptions": [],
    },
    {
        "num": 18,
        "tag": "NP-PEST-DUPLICATE",
        "path": "NON_PO",
        "vendor_code": "V-016",  # Rentokil
        "branch": "BR-JED-001",
        "category": "Pest Control",
        "status": "IN_REVIEW",
        "match": None,
        "priority": "HIGH",
        "description": "Duplicate invoice suspected — pest control invoice matches existing paid invoice (same amount, vendor, month)",
        "review_required": True,
        "exceptions": ["DUPLICATE_INVOICE"],
    },
    {
        "num": 19,
        "tag": "NP-EMERGENCY-NO-DOCS",
        "path": "NON_PO",
        "vendor_code": "V-015",  # Middleby
        "branch": "BR-DMM-002",
        "category": "Kitchen Equipment Service",
        "status": "READY_FOR_REVIEW",
        "match": None,
        "priority": "HIGH",
        "description": "Missing supporting documents — emergency fryer repair invoice with no attached work order",
        "review_required": True,
        "exceptions": [],
    },
    {
        "num": 20,
        "tag": "NP-VENDOR-NOT-FOUND",
        "path": "NON_PO",
        "vendor_code": None,  # Unknown vendor
        "branch": "BR-MKH-001",
        "category": "Facility Maintenance",
        "status": "ESCALATED",
        "match": None,
        "priority": "CRITICAL",
        "description": "Vendor not found in master — local air-conditioning repair vendor not in system",
        "review_required": True,
        "exceptions": ["VENDOR_MISMATCH"],
    },
    {
        "num": 21,
        "tag": "NP-CONSULTANT-VAT",
        "path": "NON_PO",
        "vendor_code": "V-026",  # KPMG
        "branch": None,  # HO
        "category": "Consulting & Audit",
        "status": "READY_FOR_REVIEW",
        "match": None,
        "priority": "MEDIUM",
        "description": "VAT reasonability issue — consultant invoice shows 5% VAT instead of standard 15%",
        "review_required": True,
        "exceptions": ["TAX_MISMATCH"],
    },
    {
        "num": 22,
        "tag": "NP-LOCAL-REPAIR-BUDGET",
        "path": "NON_PO",
        "vendor_code": "V-024",  # SSCO
        "branch": "BR-MDN-001",
        "category": "Facility Maintenance",
        "status": "EXCEPTION_ANALYSIS_IN_PROGRESS",
        "match": None,
        "priority": "HIGH",
        "description": "Budget unavailable — ad-hoc store plumbing repair invoice, no budget allocation found",
        "review_required": True,
        "exceptions": [],
    },
    {
        "num": 23,
        "tag": "NP-HIGH-RISK-APPROVAL",
        "path": "NON_PO",
        "vendor_code": "V-028",  # Leo Burnett
        "branch": None,  # HO
        "category": "Marketing & Agency",
        "status": "READY_FOR_APPROVAL",
        "match": None,
        "priority": "HIGH",
        "description": "High-risk non-PO — SAR 85,000 Ramadan campaign invoice requiring finance approval",
        "review_required": True,
        "exceptions": [],
    },
    {
        "num": 24,
        "tag": "NP-CODING-PENDING",
        "path": "NON_PO",
        "vendor_code": "V-030",  # Bayan Training
        "branch": None,  # HO
        "category": "Training & Development",
        "status": "READY_FOR_GL_CODING",
        "match": None,
        "priority": "LOW",
        "description": "Non-PO invoice ready for GL coding — food safety training batch, review completed",
        "review_required": False,
        "exceptions": [],
    },
    # ----- CROSS-CUTTING scenarios (25–30) -----
    {
        "num": 25,
        "tag": "XC-ESCALATED-CASE",
        "path": "THREE_WAY",
        "vendor_code": "V-004",  # Almarai
        "branch": "WH-JED-01",
        "category": "Dairy & Sauces",
        "status": "ESCALATED",
        "match": "PARTIAL_MATCH",
        "priority": "CRITICAL",
        "description": "Escalated case — Almarai dairy invoice with multiple exceptions: qty, price, and VAT mismatch",
        "review_required": True,
        "exceptions": ["QTY_MISMATCH", "PRICE_MISMATCH", "TAX_MISMATCH"],
    },
    {
        "num": 26,
        "tag": "XC-LOW-EXTRACTION",
        "path": "THREE_WAY",
        "vendor_code": "V-005",  # Heinz
        "branch": "WH-DMM-01",
        "category": "Condiments & Sauces",
        "status": "EXTRACTION_COMPLETED",
        "match": None,
        "priority": "MEDIUM",
        "description": "Failed extraction confidence — poor quality Arabic/English bilingual scan, extraction confidence 0.42",
        "review_required": True,
        "exceptions": ["EXTRACTION_LOW_CONFIDENCE"],
    },
    {
        "num": 27,
        "tag": "XC-OCR-AMBIGUITY",
        "path": "TWO_WAY",
        "vendor_code": "V-019",  # Mobily
        "branch": "BR-JED-003",
        "category": "Telecom & Internet",
        "status": "TWO_WAY_IN_PROGRESS",
        "match": "REQUIRES_REVIEW",
        "priority": "MEDIUM",
        "description": "Low-confidence PO retrieval — OCR ambiguity on PO number (5041234 vs 5041284)",
        "review_required": True,
        "exceptions": ["PO_NOT_FOUND"],
    },
    {
        "num": 28,
        "tag": "XC-CLOSED-HAPPY",
        "path": "THREE_WAY",
        "vendor_code": "V-003",  # Saudi Bakeries
        "branch": "WH-JED-01",
        "category": "Bakery & Buns",
        "status": "CLOSED",
        "match": "MATCHED",
        "priority": "LOW",
        "description": "Closed case — Saudi Bakeries buns order fully matched and auto-closed",
        "review_required": False,
        "exceptions": [],
    },
    {
        "num": 29,
        "tag": "XC-REJECTED-CASE",
        "path": "NON_PO",
        "vendor_code": "V-027",  # Olayan Manpower
        "branch": None,  # HO
        "category": "Recruitment & Staffing",
        "status": "REJECTED",
        "match": None,
        "priority": "MEDIUM",
        "description": "Rejected case — staffing invoice rejected by reviewer due to contract discrepancy",
        "review_required": True,
        "exceptions": [],
    },
    {
        "num": 30,
        "tag": "XC-REQUEST-INFO",
        "path": "THREE_WAY",
        "vendor_code": "V-007",  # Lamb Weston
        "branch": "WH-RUH-01",
        "category": "Fries & Potato Products",
        "status": "IN_REVIEW",
        "match": "PARTIAL_MATCH",
        "priority": "MEDIUM",
        "description": "Reviewer-requested-info — fries invoice on hold pending warehouse manager confirmation of receipt",
        "review_required": True,
        "exceptions": ["RECEIPT_SHORTAGE"],
    },
]
