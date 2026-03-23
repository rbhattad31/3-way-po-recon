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
# HVAC Attribute Schema — the 25 key fields for a procurement request
# ---------------------------------------------------------------------------
HVAC_ATTRIBUTE_SCHEMA: List[Dict[str, Any]] = [
    {"code": "store_type",           "label": "Store / Facility Type",           "data_type": "SELECT", "required": True,
     "options": ["MALL", "STANDALONE", "WAREHOUSE", "OFFICE", "DATA_CENTER", "RESTAURANT", "OTHER"],
     "help": "Type of facility where HVAC is being installed or maintained."},
    {"code": "area_sqm",             "label": "Conditioned Area (sqm)",          "data_type": "NUMBER", "required": True,
     "help": "Total floor area to be cooled/conditioned in square metres."},
    {"code": "cooling_load_tr",      "label": "Estimated Cooling Load (TR)",     "data_type": "NUMBER", "required": False,
     "help": "Total cooling load in Tons of Refrigeration. Leave blank if unknown."},
    {"code": "zone_count",           "label": "Number of Zones",                 "data_type": "NUMBER", "required": True,
     "help": "Number of independently controlled thermal zones."},
    {"code": "ambient_temp_max",     "label": "Max Outdoor/Ambient Temp (°C)",   "data_type": "NUMBER", "required": True,
     "help": "Maximum expected outdoor temperature. GCC typical: 46-52°C."},
    {"code": "chilled_water_available", "label": "Chilled Water Available?",     "data_type": "SELECT", "required": True,
     "options": ["YES", "NO", "UNKNOWN"],
     "help": "Whether the building has an existing chilled water backbone."},
    {"code": "outdoor_unit_restriction", "label": "Outdoor Unit Restriction?",   "data_type": "SELECT", "required": False,
     "options": ["YES", "NO"],
     "help": "Whether the landlord/authority restricts outdoor condensing units."},
    {"code": "existing_infrastructure", "label": "Existing Infrastructure",      "data_type": "SELECT", "required": False,
     "options": ["NONE", "CHILLED_WATER", "SPLITS", "VRF", "PACKAGED", "MIXED"],
     "help": "What HVAC infrastructure currently exists at the site."},
    {"code": "budget_aed",           "label": "Budget (AED)",                    "data_type": "NUMBER", "required": False,
     "help": "Maximum available budget in AED."},
    {"code": "budget_category",      "label": "Budget Category",                 "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH", "UNCONSTRAINED"],
     "help": "Relative budget constraint. Used when exact budget is not available."},
    {"code": "efficiency_priority",  "label": "Energy Efficiency Priority?",     "data_type": "SELECT", "required": False,
     "options": ["YES", "NO"],
     "help": "Whether minimising energy consumption is a primary project objective."},
    {"code": "dust_level",           "label": "Dust / Particulate Level",        "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Ambient dust level at site. 'HIGH' triggers filtration requirements."},
    {"code": "humidity_level",       "label": "Humidity Level",                  "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "Coastal/humid environments require anti-corrosion coated coils."},
    {"code": "occupancy_type",       "label": "Occupancy / Use Type",            "data_type": "SELECT", "required": False,
     "options": ["RETAIL", "FOOD_BEVERAGE", "APPAREL", "ELECTRONICS", "LOGISTICS", "OFFICE", "OTHER"],
     "help": "Primary use type affects fresh air requirements (ASHRAE 62.1)."},
    {"code": "operating_hours",      "label": "Daily Operating Hours",           "data_type": "NUMBER", "required": False,
     "help": "Average daily hours of operation. Used for annual energy calculation."},
    {"code": "floor_count",          "label": "Number of Floors",                "data_type": "NUMBER", "required": False,
     "help": "Number of floors in the facility."},
    {"code": "ceiling_height_m",     "label": "Average Ceiling Height (m)",      "data_type": "NUMBER", "required": False,
     "help": "Average finished ceiling height. Affects air distribution design."},
    {"code": "noise_sensitivity",    "label": "Noise Sensitivity",               "data_type": "SELECT", "required": False,
     "options": ["LOW", "MEDIUM", "HIGH"],
     "help": "HIGH triggers low-noise equipment selection (libraries, clinics, etc.)."},
    {"code": "refrigerant_preference", "label": "Refrigerant Preference",        "data_type": "SELECT", "required": False,
     "options": ["R32", "R410A", "R134a", "R454B", "NO_PREFERENCE"],
     "help": "Preferred refrigerant. R32 and R454B are lower-GWP options."},
    {"code": "brand_preference",     "label": "Preferred Brand(s)",              "data_type": "TEXT",   "required": False,
     "help": "Preferred OEM brand(s) e.g. Daikin, Mitsubishi, Carrier, McQuay."},
    {"code": "lifespan_years",       "label": "Expected Equipment Lifespan (Years)", "data_type": "NUMBER", "required": False,
     "help": "Expected useful life of equipment. Used in lifecycle cost analysis."},
    {"code": "maintenance_contract", "label": "Maintenance Contract Required?",  "data_type": "SELECT", "required": False,
     "options": ["YES", "NO", "PREFERABLE"],
     "help": "Whether an AMC (annual maintenance contract) is required."},
    {"code": "commissioning_required", "label": "T&C / Commissioning Required?", "data_type": "SELECT", "required": False,
     "options": ["YES", "NO"],
     "help": "Whether testing, commissioning, and NEBB verification is required."},
    {"code": "geography_zone",       "label": "Geography Zone",                  "data_type": "SELECT", "required": False,
     "options": ["UAE_COASTAL", "UAE_INLAND", "KSA_COASTAL", "KSA_INLAND", "OMAN", "QATAR", "KUWAIT", "BAHRAIN"],
     "help": "Geographic zone affects ambient temperature design and compliance standards."},
    {"code": "special_requirements", "label": "Special Requirements / Notes",    "data_type": "TEXT",   "required": False,
     "help": "Any additional specifications, constraints, or requirements."},
]

# Attributes that are required for recommendation to proceed
HVAC_REQUIRED_FOR_RECOMMENDATION = {
    "store_type", "area_sqm", "zone_count", "ambient_temp_max", "chilled_water_available",
}

# Attributes that are required for benchmarking to proceed
HVAC_REQUIRED_FOR_BENCHMARK = {
    "store_type", "area_sqm",
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
