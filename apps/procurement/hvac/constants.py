"""HVAC domain constants — GCC market (UAE, KSA, Oman, Qatar, Kuwait, Bahrain).

This module contains:
  - System type definitions and descriptions
  - GCC market benchmark price corridors per HVAC category
  - Compliance standards applicable per geography
  - Attribute schema definition for HVAC procurement requests
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# HVAC System Types — recommended options output
# ---------------------------------------------------------------------------
SYSTEM_TYPES = {
    "FCU_CHILLED_WATER": {
        "name": "Fan Coil Unit (Chilled Water)",
        "description": (
            "FCU units connected to a central chilled water plant. "
            "Ideal for malls and large commercial buildings with existing CW backbone. "
            "Energy-efficient, low noise, centrally controlled."
        ),
        "typical_applications": ["Mall retail units", "Office floors", "Large commercial"],
        "compliance_standards": ["ASHRAE 90.1", "ASHRAE 55", "ISO 16813"],
        "pros": ["Energy efficient", "No outdoor units required", "Centrally controlled"],
        "cons": ["Depends on building CW infrastructure", "Higher upfront cost if CW absent"],
    },
    "VRF_SYSTEM": {
        "name": "Variable Refrigerant Flow (VRF) System",
        "description": (
            "Multi-zone system with one outdoor unit serving multiple indoor units. "
            "Excellent for standalone stores with 3+ zones. "
            "High efficiency, individual zone control, heat recovery possible."
        ),
        "typical_applications": ["Standalone retail", "Multi-zone offices", "Showrooms"],
        "compliance_standards": ["ASHRAE 90.1", "UAE ESMA", "SASO"],
        "pros": ["Multi-zone control", "High efficiency (IPLV)", "Compact outdoor unit"],
        "cons": ["Complex installation", "Higher cost than splits", "Refrigerant charge"],
    },
    "SPLIT_SYSTEM": {
        "name": "Split Air Conditioning System",
        "description": (
            "Wall-mounted or cassette split units with dedicated outdoor condensers. "
            "Cost-effective for small to medium standalone spaces up to 2 zones. "
            "Simple installation and maintenance."
        ),
        "typical_applications": ["Small retail", "Offices ≤150 sqm", "Back-of-house"],
        "compliance_standards": ["UAE ESMA 5-star minimum", "SASO (KSA)"],
        "pros": ["Low cost", "Easy installation", "Widely available service"],
        "cons": ["Multiple outdoor units", "Individual control only", "Less efficient at high ambient"],
    },
    "PACKAGED_DX_UNIT": {
        "name": "Packaged Rooftop DX Unit",
        "description": (
            "Self-contained rooftop units delivering conditioned air through ductwork. "
            "Suitable for warehouses, large open-plan retail, and industrial spaces. "
            "High capacity, centralized air distribution."
        ),
        "typical_applications": ["Warehouses", "Large retail floors", "Supermarkets"],
        "compliance_standards": ["ASHRAE 90.1", "NEBB"],
        "pros": ["High capacity", "Single equipment piece", "Good for large open spaces"],
        "cons": ["Rooftop access required", "Ductwork needed", "Higher maintenance"],
    },
    "CHILLER_PLANT": {
        "name": "Chiller Plant (Air/Water Cooled)",
        "description": (
            "Central chiller plant (McQuay, Carrier, Trane, York) for large-scale cooling. "
            "Suitable for anchor stores, hypermarkets, or data centers. "
            "Best long-term efficiency for loads > 200TR."
        ),
        "typical_applications": ["Anchor stores", "Hypermarkets", "Data centers"],
        "compliance_standards": ["ASHRAE 90.1", "ISO 50001", "CIBSE TM65"],
        "pros": ["Best efficiency at scale", "Long lifecycle", "Central monitoring"],
        "cons": ["Very high capital cost", "Complex installation", "Requires dedicated plant room"],
    },
    "CASSETTE_SPLIT": {
        "name": "Cassette Split System",
        "description": (
            "Ceiling-mounted cassette indoor units for even air distribution. "
            "Good for retail floors where wall mounting is not possible. "
            "Better aesthetics than wall-mounted units."
        ),
        "typical_applications": ["Retail showrooms", "Open-plan offices", "Restaurants"],
        "compliance_standards": ["UAE ESMA", "SASO"],
        "pros": ["360° airflow", "Better aesthetics", "Even distribution"],
        "cons": ["Requires false ceiling", "Higher cost than wall splits"],
    },
}

# ---------------------------------------------------------------------------
# GCC Market Benchmark Price Corridors (AED, 2024–2025)
#
# Covers: UAE (Dubai, Abu Dhabi, Sharjah), KSA (Riyadh, Jeddah, Dammam),
#         Oman, Qatar, Kuwait, Bahrain
#
# Prices are supply + installation (turnkey) unless noted as "supply only"
# Sources: Regional distributor rates, DEWA-approved contractor quotes,
#          Landmark Group historical procurement data
# ---------------------------------------------------------------------------
HVAC_BENCHMARK_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── Split Systems ──────────────────────────────────────────────────────
    "SPLIT_AC_WALL_1TR": {
        "description": "Wall-mounted split AC 1 TR (12,000 BTU)",
        "category_keywords": ["split", "1 tr", "12000 btu", "wall mount", "1.0 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("1800"),
        "benchmark_avg": Decimal("2400"),
        "benchmark_max": Decimal("3200"),
        "currency": "AED",
        "notes": "Supply + installation. Mitsubishi / Daikin / LG / Haier.",
    },
    "SPLIT_AC_WALL_1_5TR": {
        "description": "Wall-mounted split AC 1.5 TR (18,000 BTU)",
        "category_keywords": ["split", "1.5 tr", "18000 btu", "wall mount", "1.5 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("2500"),
        "benchmark_avg": Decimal("3500"),
        "benchmark_max": Decimal("4500"),
        "currency": "AED",
        "notes": "Supply + installation. Midea / Daikin / Mitsubishi.",
    },
    "SPLIT_AC_WALL_2TR": {
        "description": "Wall-mounted split AC 2 TR (24,000 BTU)",
        "category_keywords": ["split", "2 tr", "24000 btu", "wall mount", "2 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("3200"),
        "benchmark_avg": Decimal("4200"),
        "benchmark_max": Decimal("5500"),
        "currency": "AED",
        "notes": "Supply + installation.",
    },
    "SPLIT_AC_WALL_2_5TR": {
        "description": "Wall-mounted split AC 2.5 TR (30,000 BTU)",
        "category_keywords": ["split", "2.5 tr", "30000 btu", "wall mount", "2.5 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("4000"),
        "benchmark_avg": Decimal("5200"),
        "benchmark_max": Decimal("6500"),
        "currency": "AED",
        "notes": "Supply + installation.",
    },
    "CASSETTE_AC_2TR": {
        "description": "Ceiling cassette split AC 2 TR (ceiling-mounted)",
        "category_keywords": ["cassette", "2 tr", "ceiling", "cassette unit"],
        "unit": "Nos",
        "benchmark_min": Decimal("4500"),
        "benchmark_avg": Decimal("5800"),
        "benchmark_max": Decimal("7500"),
        "currency": "AED",
        "notes": "Supply + installation including ceiling opening works.",
    },
    "CASSETTE_AC_2_5TR": {
        "description": "Ceiling cassette split AC 2.5 TR",
        "category_keywords": ["cassette", "2.5 tr", "ceiling cassette"],
        "unit": "Nos",
        "benchmark_min": Decimal("5500"),
        "benchmark_avg": Decimal("7000"),
        "benchmark_max": Decimal("9000"),
        "currency": "AED",
    },
    # ── VRF Systems ───────────────────────────────────────────────────────
    "VRF_OUTDOOR_6TR": {
        "description": "VRF outdoor unit 6 TR (heat pump)",
        "category_keywords": ["vrf", "vrv", "outdoor", "6 tr", "heat pump"],
        "unit": "Nos",
        "benchmark_min": Decimal("12000"),
        "benchmark_avg": Decimal("16000"),
        "benchmark_max": Decimal("22000"),
        "currency": "AED",
    },
    "VRF_OUTDOOR_10TR": {
        "description": "VRF outdoor unit 10 TR (heat pump)",
        "category_keywords": ["vrf", "vrv", "outdoor", "10 tr", "10 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("18000"),
        "benchmark_avg": Decimal("24000"),
        "benchmark_max": Decimal("32000"),
        "currency": "AED",
    },
    "VRF_OUTDOOR_16TR": {
        "description": "VRF outdoor unit 16 TR",
        "category_keywords": ["vrf", "vrv", "outdoor", "16 tr", "16 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("28000"),
        "benchmark_avg": Decimal("38000"),
        "benchmark_max": Decimal("50000"),
        "currency": "AED",
    },
    "VRF_INDOOR_CASSETTE": {
        "description": "VRF indoor cassette unit (any capacity)",
        "category_keywords": ["vrf", "vrv", "indoor", "cassette", "fan coil"],
        "unit": "Nos",
        "benchmark_min": Decimal("3200"),
        "benchmark_avg": Decimal("4800"),
        "benchmark_max": Decimal("6500"),
        "currency": "AED",
    },
    "VRF_INDOOR_WALL": {
        "description": "VRF indoor wall-mounted unit",
        "category_keywords": ["vrf", "vrv", "indoor wall", "wall fan"],
        "unit": "Nos",
        "benchmark_min": Decimal("2500"),
        "benchmark_avg": Decimal("3500"),
        "benchmark_max": Decimal("5000"),
        "currency": "AED",
    },
    # ── Fan Coil Units (FCU) ──────────────────────────────────────────────
    "FCU_2TR": {
        "description": "Fan coil unit 2 TR (chilled water)",
        "category_keywords": ["fcu", "fan coil", "2 tr", "chilled water unit"],
        "unit": "Nos",
        "benchmark_min": Decimal("2800"),
        "benchmark_avg": Decimal("3800"),
        "benchmark_max": Decimal("5000"),
        "currency": "AED",
    },
    "FCU_3TR": {
        "description": "Fan coil unit 3 TR (chilled water)",
        "category_keywords": ["fcu", "fan coil", "3 tr"],
        "unit": "Nos",
        "benchmark_min": Decimal("4000"),
        "benchmark_avg": Decimal("5500"),
        "benchmark_max": Decimal("7200"),
        "currency": "AED",
    },
    # ── Chillers ──────────────────────────────────────────────────────────
    "CHILLER_100TR": {
        "description": "Air-cooled chiller 100 TR",
        "category_keywords": ["chiller", "100 tr", "100 ton", "air cooled chiller"],
        "unit": "Nos",
        "benchmark_min": Decimal("180000"),
        "benchmark_avg": Decimal("240000"),
        "benchmark_max": Decimal("310000"),
        "currency": "AED",
    },
    "CHILLER_200TR": {
        "description": "Air-cooled chiller 200 TR",
        "category_keywords": ["chiller", "200 tr", "200 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("280000"),
        "benchmark_avg": Decimal("380000"),
        "benchmark_max": Decimal("480000"),
        "currency": "AED",
    },
    "CHILLER_350TR": {
        "description": "Air/water-cooled chiller 350 TR (e.g. McQuay WD)",
        "category_keywords": ["chiller", "350 tr", "350 ton", "mcquay", "carrier"],
        "unit": "Nos",
        "benchmark_min": Decimal("450000"),
        "benchmark_avg": Decimal("600000"),
        "benchmark_max": Decimal("780000"),
        "currency": "AED",
    },
    "CHILLER_425TR": {
        "description": "Air/water-cooled chiller 425 TR (e.g. McQuay WD)",
        "category_keywords": ["chiller", "425 tr", "425 ton"],
        "unit": "Nos",
        "benchmark_min": Decimal("550000"),
        "benchmark_avg": Decimal("720000"),
        "benchmark_max": Decimal("950000"),
        "currency": "AED",
    },
    # ── Air Handling Units (AHU) ──────────────────────────────────────────
    "AHU_5000CFM": {
        "description": "Air handling unit 5,000 CFM",
        "category_keywords": ["ahu", "air handling", "5000 cfm", "5,000 cfm"],
        "unit": "Nos",
        "benchmark_min": Decimal("15000"),
        "benchmark_avg": Decimal("22000"),
        "benchmark_max": Decimal("30000"),
        "currency": "AED",
    },
    "AHU_10000CFM": {
        "description": "Air handling unit 10,000 CFM",
        "category_keywords": ["ahu", "air handling", "10000 cfm", "10,000 cfm"],
        "unit": "Nos",
        "benchmark_min": Decimal("25000"),
        "benchmark_avg": Decimal("36000"),
        "benchmark_max": Decimal("50000"),
        "currency": "AED",
    },
    # ── Ductwork ──────────────────────────────────────────────────────────
    "GI_DUCTWORK": {
        "description": "Galvanised iron (GI) ductwork supply & install",
        "category_keywords": ["gi duct", "ductwork", "galvanised", "galvanized", "gi sheet"],
        "unit": "Sqm",
        "benchmark_min": Decimal("60"),
        "benchmark_avg": Decimal("75"),
        "benchmark_max": Decimal("95"),
        "currency": "AED",
        "notes": "Per sqm of duct surface area including insulation.",
    },
    "FLEXIBLE_DUCT": {
        "description": "Flexible duct supply & installation (lump sum)",
        "category_keywords": ["flexible duct", "flex duct", "flexible ducting"],
        "unit": "LS",
        "benchmark_min": Decimal("5000"),
        "benchmark_avg": Decimal("8000"),
        "benchmark_max": Decimal("12000"),
        "currency": "AED",
        "notes": "Per store lump sum for branch connections.",
    },
    # ── Diffusers & Grilles ───────────────────────────────────────────────
    "SUPPLY_DIFFUSER": {
        "description": "Square supply air diffuser (600x600mm or similar)",
        "category_keywords": ["supply diffuser", "supply air diffuser", "diffuser"],
        "unit": "Nos",
        "benchmark_min": Decimal("150"),
        "benchmark_avg": Decimal("210"),
        "benchmark_max": Decimal("300"),
        "currency": "AED",
    },
    "RETURN_DIFFUSER": {
        "description": "Return air diffuser",
        "category_keywords": ["return diffuser", "return air diffuser"],
        "unit": "Nos",
        "benchmark_min": Decimal("80"),
        "benchmark_avg": Decimal("120"),
        "benchmark_max": Decimal("180"),
        "currency": "AED",
    },
    "LINEAR_BAR_GRILLE": {
        "description": "Linear bar grille (return/supply)",
        "category_keywords": ["linear grille", "bar grille", "linear bar", "grille"],
        "unit": "m",
        "benchmark_min": Decimal("120"),
        "benchmark_avg": Decimal("160"),
        "benchmark_max": Decimal("210"),
        "currency": "AED",
    },
    "LOUVER": {
        "description": "Louvre/louvred grille (wall-mounted fresh air)",
        "category_keywords": ["louver", "louvre", "louvered", "fresh air grille"],
        "unit": "Nos",
        "benchmark_min": Decimal("400"),
        "benchmark_avg": Decimal("600"),
        "benchmark_max": Decimal("900"),
        "currency": "AED",
    },
    "AIR_CURTAIN": {
        "description": "Air curtain (entrance / store front)",
        "category_keywords": ["air curtain", "entrance curtain"],
        "unit": "Nos",
        "benchmark_min": Decimal("1200"),
        "benchmark_avg": Decimal("1900"),
        "benchmark_max": Decimal("2800"),
        "currency": "AED",
    },
    # ── Accessories & Controls ────────────────────────────────────────────
    "THERMOSTAT_DIGITAL": {
        "description": "Digital thermostat / room temperature controller",
        "category_keywords": ["thermostat", "room thermostat", "digital thermostat", "rtc"],
        "unit": "Nos",
        "benchmark_min": Decimal("200"),
        "benchmark_avg": Decimal("300"),
        "benchmark_max": Decimal("450"),
        "currency": "AED",
    },
    "THERMOSTAT_RELOCATION": {
        "description": "Existing thermostat relocation works",
        "category_keywords": ["thermostat relocation", "relocate thermostat"],
        "unit": "Nos",
        "benchmark_min": Decimal("150"),
        "benchmark_avg": Decimal("230"),
        "benchmark_max": Decimal("320"),
        "currency": "AED",
    },
    "EXHAUST_FAN": {
        "description": "Exhaust fan (kitchen / toilet / back-of-house)",
        "category_keywords": ["exhaust fan", "extract fan", "ventilation fan"],
        "unit": "Nos",
        "benchmark_min": Decimal("350"),
        "benchmark_avg": Decimal("550"),
        "benchmark_max": Decimal("800"),
        "currency": "AED",
    },
    "TESTING_COMMISSIONING": {
        "description": "Testing & commissioning (T&C) lump sum",
        "category_keywords": ["testing", "commissioning", "t&c", "t & c"],
        "unit": "LS",
        "benchmark_min": Decimal("2500"),
        "benchmark_avg": Decimal("4000"),
        "benchmark_max": Decimal("7000"),
        "currency": "AED",
    },
    # ── Chiller Repair / Maintenance ─────────────────────────────────────
    "CHILLER_CONTROLS_UPGRADE": {
        "description": "Chiller controls & PLC upgrade (per chiller)",
        "category_keywords": ["controls upgrade", "plc upgrade", "control panel", "controls"],
        "unit": "Nos",
        "benchmark_min": Decimal("35000"),
        "benchmark_avg": Decimal("55000"),
        "benchmark_max": Decimal("80000"),
        "currency": "AED",
    },
    "CHILLER_EXV_REPLACEMENT": {
        "description": "Electronic expansion valve (EXV) & VFD drives replacement",
        "category_keywords": ["exv", "expansion valve", "vfd", "variable frequency drive"],
        "unit": "Nos",
        "benchmark_min": Decimal("25000"),
        "benchmark_avg": Decimal("40000"),
        "benchmark_max": Decimal("60000"),
        "currency": "AED",
    },
    "CHILLER_CONDENSER_REPAIR": {
        "description": "Condenser coil cleaning/repair/replacement (per chiller)",
        "category_keywords": ["condenser", "condenser coil", "coil cleaning", "condenser repair"],
        "unit": "Nos",
        "benchmark_min": Decimal("15000"),
        "benchmark_avg": Decimal("28000"),
        "benchmark_max": Decimal("50000"),
        "currency": "AED",
    },
    "CHILLER_COMPRESSOR_OVERHAUL": {
        "description": "Compressor overhaul / replacement",
        "category_keywords": ["compressor", "compressor overhaul", "compressor replace"],
        "unit": "Nos",
        "benchmark_min": Decimal("40000"),
        "benchmark_avg": Decimal("70000"),
        "benchmark_max": Decimal("120000"),
        "currency": "AED",
    },
    "PIPING_INSULATION": {
        "description": "Piping insulation (chilled water / refrigerant)",
        "category_keywords": ["insulation", "pipe insulation", "chwp insulation", "armaflex"],
        "unit": "m",
        "benchmark_min": Decimal("40"),
        "benchmark_avg": Decimal("65"),
        "benchmark_max": Decimal("100"),
        "currency": "AED",
    },
}

# ---------------------------------------------------------------------------
# HVAC Attribute Schema -- 22 fields matching the requirement document spec
# ---------------------------------------------------------------------------
HVAC_ATTRIBUTE_SCHEMA: List[Dict[str, Any]] = [
    {"code": "store_id",              "label": "Store ID",                         "data_type": "TEXT",   "required": True,
     "help": "Unique reference for the analysis case e.g. LM-UAE-001."},
    {"code": "brand",                 "label": "Brand",                            "data_type": "TEXT",   "required": True,
     "help": "Brand or business unit name e.g. Max, Home Centre."},
    {"code": "country",               "label": "Country",                          "data_type": "SELECT", "required": True,
     "options": ["UAE", "KSA", "QATAR", "OMAN", "KUWAIT", "BAHRAIN"],
     "help": "Determines climate zone and compliance standards."},
    {"code": "city",                  "label": "City",                             "data_type": "TEXT",   "required": True,
     "help": "City for municipality/mall requirement mapping e.g. Dubai."},
    {"code": "store_type",            "label": "Store Type",                       "data_type": "SELECT", "required": True,
     "options": ["MALL", "STANDALONE", "WAREHOUSE", "OFFICE", "DATA_CENTER", "RESTAURANT", "OTHER"],
     "help": "Primary driver of system family feasibility."},
    {"code": "store_format",          "label": "Store Format",                     "data_type": "SELECT", "required": True,
     "options": ["RETAIL", "HYPERMARKET", "FURNITURE", "ELECTRONICS", "FOOD_BEVERAGE", "OTHER"],
     "help": "Influences airflow profile and occupancy load."},
    {"code": "area_sqft",             "label": "Area (sq ft)",                     "data_type": "NUMBER", "required": True,
     "help": "Total conditioned floor area in square feet."},
    {"code": "ceiling_height_ft",     "label": "Ceiling Height (ft)",              "data_type": "NUMBER", "required": True,
     "help": "Average finished ceiling height in feet. Affects air volume and ducting."},
    {"code": "operating_hours",       "label": "Operating Hours",                  "data_type": "TEXT",   "required": False,
     "help": "Store operating hours e.g. 10 AM - 12 AM."},
    {"code": "footfall_category",     "label": "Footfall Category",                "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Proxy for occupancy load affecting fresh air and cooling demand."},
    {"code": "ambient_temp_max",      "label": "Ambient Temp Max (C)",             "data_type": "NUMBER", "required": True,
     "help": "Maximum expected outdoor temperature. GCC typical: 46-52C."},
    {"code": "humidity_level",        "label": "Humidity Level",                   "data_type": "SELECT", "required": True,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Affects anti-corrosion equipment selection and ventilation."},
    {"code": "dust_exposure",         "label": "Dust Exposure",                    "data_type": "SELECT", "required": True,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "HIGH triggers MERV 11+ filtration and maintenance logic."},
    {"code": "heat_load_category",    "label": "Heat Load Category",               "data_type": "SELECT", "required": True,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Primary sizing discriminator for system capacity selection."},
    {"code": "fresh_air_requirement", "label": "Fresh Air Requirement",            "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Ventilation load per ASHRAE 62.1."},
    {"code": "landlord_constraints",  "label": "Landlord Constraints",             "data_type": "TEXT",   "required": True,
     "help": "Critical for mall projects e.g. No outdoor units allowed."},
    {"code": "existing_hvac_type",    "label": "Existing HVAC Type",               "data_type": "TEXT",   "required": False,
     "help": "Useful in retrofit/replacement cases e.g. Chilled water interface."},
    {"code": "budget_level",          "label": "Budget Level",                     "data_type": "SELECT", "required": True,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Constrains commercial recommendation selection."},
    {"code": "energy_efficiency_priority", "label": "Energy Efficiency Priority",  "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Used in option prioritization and VRF/inverter selection."},
    {"code": "maintenance_priority",  "label": "Maintenance Priority",             "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Influences serviceability notes in recommendation."},
    {"code": "preferred_oems",        "label": "Preferred OEMs",                   "data_type": "TEXT",   "required": False,
     "help": "Preferred OEM brands for option ranking e.g. Daikin, Carrier."},
    {"code": "required_standards",    "label": "Required Standards / Local Notes", "data_type": "TEXT",   "required": False,
     "help": "User-driven compliance emphasis e.g. ASHRAE, mall fit-out guide."},
]

# Attributes that are required for recommendation to proceed
HVAC_REQUIRED_FOR_RECOMMENDATION = {
    "country", "city", "store_type", "area_sqft", "ambient_temp_max",
    "budget_level", "energy_efficiency_priority",
}

# Attributes that are required for benchmarking to proceed
HVAC_REQUIRED_FOR_BENCHMARK = {
    "store_type", "area_sqft",
}

# ---------------------------------------------------------------------------
# Compliance Standards by Geography
# ---------------------------------------------------------------------------
COMPLIANCE_STANDARDS_BY_GEO = {
    "UAE": ["ASHRAE 90.1-2019", "ASHRAE 55-2020", "ASHRAE 62.1-2019", "UAE ESMA 2020", "ISO 16813", "CIBSE TM65"],
    "KSA": ["ASHRAE 90.1-2019", "SASO 2870", "SASO 4820", "Saudi Building Code (SBC)", "ASHRAE 62.1"],
    "OMAN": ["ASHRAE 90.1", "RS OMAN Energy Standards", "ASHRAE 62.1"],
    "QATAR": ["ASHRAE 90.1", "GSAS (GORD)", "ASHRAE 62.1"],
    "KUWAIT": ["ASHRAE 90.1"],
    "BAHRAIN": ["ASHRAE 90.1", "BCA Energy Code"],
    "DEFAULT": ["ASHRAE 90.1-2019", "ISO 16813:2006"],
}
