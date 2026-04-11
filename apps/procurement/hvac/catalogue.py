"""HVAC Product Reference Catalogue -- Landmark Group GCC Operations.

Full product catalogue with product codes, technical specs, market rates,
and brand information. Used by:
  - Reference tab in the workspace (UI display)
  - Product type/code selectors in the HVAC create form
  - Benchmark resolver (price corridors)
  - Recommendation engine (product selection logic)

Geography: UAE, KSA, Oman, Qatar, Kuwait, Bahrain (GCC)
Currency: AED (primary), with conversion helpers
Rates updated: 2025-Q1
Data source: Landmark Group Procurement historical + GCC market surveys
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Product Type Groups (for dropdown selectors in the form)
# ---------------------------------------------------------------------------

PRODUCT_TYPE_GROUPS = [
    {
        "group_code": "SPLIT_AC",
        "group_label": "Split Air Conditioner",
        "icon": "bi-thermometer-snow",
        "description": "Wall-mounted or floor-standing split units with dedicated outdoor condenser.",
        "suitable_for": ["STANDALONE", "OFFICE", "RESTAURANT", "SMALL_RETAIL"],
        "capacity_range": "1 TR -- 5 TR",
    },
    {
        "group_code": "CASSETTE_AC",
        "group_label": "Cassette (Ceiling) Split AC",
        "icon": "bi-grid-3x3",
        "description": "Ceiling-mounted cassette units for even 360 degree air distribution. Requires false ceiling.",
        "suitable_for": ["STANDALONE", "OFFICE", "MALL", "RESTAURANT"],
        "capacity_range": "1.5 TR -- 5 TR",
    },
    {
        "group_code": "VRF_VRV",
        "group_label": "VRF / VRV Multi-Zone System",
        "icon": "bi-diagram-3",
        "description": "Variable Refrigerant Flow system. One outdoor unit serving 3-64 indoor units with individual zone control.",
        "suitable_for": ["STANDALONE", "OFFICE", "MALL_UNIT", "SHOWROOM"],
        "capacity_range": "6 TR -- 48 TR per outdoor unit",
    },
    {
        "group_code": "FCU_CW",
        "group_label": "Fan Coil Unit (Chilled Water)",
        "icon": "bi-droplet",
        "description": "FCU connected to a central chilled water plant. Ideal for mall tenants with CW backbone.",
        "suitable_for": ["MALL", "LARGE_OFFICE", "DATA_CENTER"],
        "capacity_range": "0.5 TR -- 5 TR per unit",
    },
    {
        "group_code": "AHU",
        "group_label": "Air Handling Unit (AHU)",
        "icon": "bi-wind",
        "description": "Central air handling units for large commercial spaces with duct distribution.",
        "suitable_for": ["WAREHOUSE", "HYPERMARKET", "DATA_CENTER", "ANCHOR_STORE"],
        "capacity_range": "10 TR -- 100 TR per unit",
    },
    {
        "group_code": "PACKAGED_UNIT",
        "group_label": "Packaged Rooftop / Floor Unit",
        "icon": "bi-box",
        "description": "Self-contained DX packaged units. Rooftop or floor-standing. Good for warehouses and large open spaces.",
        "suitable_for": ["WAREHOUSE", "SUPERMARKET", "LARGE_RETAIL"],
        "capacity_range": "10 TR -- 60 TR",
    },
    {
        "group_code": "CHILLER",
        "group_label": "Chiller Plant (Air / Water Cooled)",
        "icon": "bi-snow2",
        "description": "Central chiller for anchor stores, data centers, or buildings > 200 TR load.",
        "suitable_for": ["DATA_CENTER", "ANCHOR_STORE", "HYPERMARKET", "LARGE_BUILDING"],
        "capacity_range": "50 TR -- 1000+ TR",
    },
    {
        "group_code": "VENTILATION",
        "group_label": "Ventilation Fan / ERV / HRV",
        "icon": "bi-fan",
        "description": "Supply and exhaust fans, energy recovery ventilators, and heat recovery units.",
        "suitable_for": ["ALL"],
        "capacity_range": "All sizes",
    },
]

# ---------------------------------------------------------------------------
# Full Product Reference Catalogue
# Each product has:
#   product_code:      unique identifier used for benchmarking and tracking
#   type_group:        one of the groups above
#   model:             vendor model number
#   brand:             manufacturer
#   capacity_tr:       cooling capacity in Tons of Refrigeration
#   capacity_kw:       cooling capacity in kW
#   refrigerant:       refrigerant type
#   efficiency_eer:    EER rating (higher = more efficient)
#   efficiency_seer:   SEER rating where available
#   esma_star:         UAE ESMA star rating (1-5)
#   supply_only_aed:   supply-only market price (AED)
#   installed_min_aed: installed (supply + install) min market price AED
#   installed_avg_aed: installed (supply + install) avg market price AED
#   installed_max_aed: installed (supply + install) max market price AED
#   warranty_years:    manufacturer warranty
#   suitable_for:      list of facility types
#   notes:             special notes
#   tags:              list of searchable tags
# ---------------------------------------------------------------------------

HVAC_PRODUCT_CATALOGUE: List[Dict[str, Any]] = [

    # =========================================================================
    # SPLIT WALL-MOUNTED AC SYSTEMS
    # =========================================================================
    {
        "product_code": "SPLIT-WALL-1TR-DAIKIN-R32",
        "type_group": "SPLIT_AC",
        "model": "FTXB25C / RXB25C",
        "brand": "Daikin",
        "capacity_tr": Decimal("1.0"),
        "capacity_kw": Decimal("2.5"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.70"),
        "esma_star": 4,
        "supply_only_aed": Decimal("1400"),
        "installed_min_aed": Decimal("1900"),
        "installed_avg_aed": Decimal("2400"),
        "installed_max_aed": Decimal("3000"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "OFFICE", "RESTAURANT"],
        "notes": "R32 low-GWP refrigerant. ESMA 4-star. Good for small offices and back-of-house.",
        "tags": ["split", "wall", "1tr", "small", "daikin", "r32"],
    },
    {
        "product_code": "SPLIT-WALL-1.5TR-DAIKIN-R32",
        "type_group": "SPLIT_AC",
        "model": "FTXB35C / RXB35C",
        "brand": "Daikin",
        "capacity_tr": Decimal("1.5"),
        "capacity_kw": Decimal("3.5"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.65"),
        "esma_star": 5,
        "supply_only_aed": Decimal("1900"),
        "installed_min_aed": Decimal("2500"),
        "installed_avg_aed": Decimal("3200"),
        "installed_max_aed": Decimal("4200"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "OFFICE", "SMALL_RETAIL"],
        "notes": "R32 refrigerant. ESMA 5-star. Popular choice for standalone stores and offices.",
        "tags": ["split", "wall", "1.5tr", "daikin", "r32", "5star"],
    },
    {
        "product_code": "SPLIT-WALL-2TR-DAIKIN-R32",
        "type_group": "SPLIT_AC",
        "model": "FTXB50C / RXB50C",
        "brand": "Daikin",
        "capacity_tr": Decimal("2.0"),
        "capacity_kw": Decimal("5.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.55"),
        "esma_star": 5,
        "supply_only_aed": Decimal("2700"),
        "installed_min_aed": Decimal("3500"),
        "installed_avg_aed": Decimal("4400"),
        "installed_max_aed": Decimal("5500"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "OFFICE", "RESTAURANT", "SMALL_RETAIL"],
        "notes": "2 TR wall-mount. High efficiency for GCC climate. UAE ESMA 5-star certified.",
        "tags": ["split", "wall", "2tr", "daikin", "r32"],
    },
    {
        "product_code": "SPLIT-WALL-2TR-CARRIER-R410A",
        "type_group": "SPLIT_AC",
        "model": "42QNG024D8S / 38QNG024D8S",
        "brand": "Carrier",
        "capacity_tr": Decimal("2.0"),
        "capacity_kw": Decimal("5.1"),
        "refrigerant": "R410A",
        "efficiency_eer": Decimal("3.30"),
        "esma_star": 4,
        "supply_only_aed": Decimal("2400"),
        "installed_min_aed": Decimal("3200"),
        "installed_avg_aed": Decimal("4000"),
        "installed_max_aed": Decimal("5200"),
        "warranty_years": 3,
        "suitable_for": ["STANDALONE", "OFFICE"],
        "notes": "Carrier 2T wall-mount. Well-supported in GCC with wide service network.",
        "tags": ["split", "wall", "2tr", "carrier", "r410a"],
    },
    {
        "product_code": "SPLIT-WALL-2.5TR-MITSUBISHI-R32",
        "type_group": "SPLIT_AC",
        "model": "MSZ-GF71VE / MUZ-GF71VE",
        "brand": "Mitsubishi Electric",
        "capacity_tr": Decimal("2.5"),
        "capacity_kw": Decimal("7.1"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.80"),
        "esma_star": 5,
        "supply_only_aed": Decimal("3800"),
        "installed_min_aed": Decimal("5000"),
        "installed_avg_aed": Decimal("6200"),
        "installed_max_aed": Decimal("7800"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "OFFICE", "MALL_UNIT"],
        "notes": "Mitsubishi 2.5T. Premium build quality. Hyper Heat technology. Low noise 20dB(A).",
        "tags": ["split", "wall", "2.5tr", "mitsubishi", "r32", "quiet", "premium"],
    },
    {
        "product_code": "SPLIT-WALL-3TR-LG-R32",
        "type_group": "SPLIT_AC",
        "model": "S4-W36JA3WB",
        "brand": "LG",
        "capacity_tr": Decimal("3.0"),
        "capacity_kw": Decimal("9.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.40"),
        "esma_star": 4,
        "supply_only_aed": Decimal("3200"),
        "installed_min_aed": Decimal("4200"),
        "installed_avg_aed": Decimal("5300"),
        "installed_max_aed": Decimal("6500"),
        "warranty_years": 3,
        "suitable_for": ["STANDALONE", "SMALL_RETAIL", "RESTAURANT"],
        "notes": "LG dual inverter 3 TR. ThinQ WiFi control. Good value mid-range.",
        "tags": ["split", "wall", "3tr", "lg", "r32", "inverter", "wifi"],
    },
    {
        "product_code": "SPLIT-WALL-5TR-SAMSUNG-R32",
        "type_group": "SPLIT_AC",
        "model": "AR48TXEAAWK",
        "brand": "Samsung",
        "capacity_tr": Decimal("4.0"),
        "capacity_kw": Decimal("14.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.25"),
        "esma_star": 3,
        "supply_only_aed": Decimal("5400"),
        "installed_min_aed": Decimal("6800"),
        "installed_avg_aed": Decimal("8200"),
        "installed_max_aed": Decimal("10500"),
        "warranty_years": 3,
        "suitable_for": ["STANDALONE", "OFFICE", "SMALL_RETAIL"],
        "notes": "Samsung 4T wall-mount. Wind-Free cooling technology.",
        "tags": ["split", "wall", "4tr", "samsung", "r32", "windfree"],
    },

    # =========================================================================
    # CASSETTE (CEILING) SPLIT AC SYSTEMS
    # =========================================================================
    {
        "product_code": "CASS-2TR-DAIKIN-R32",
        "type_group": "CASSETTE_AC",
        "model": "FCAG50A / RZAG50MV",
        "brand": "Daikin",
        "capacity_tr": Decimal("2.0"),
        "capacity_kw": Decimal("5.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.60"),
        "esma_star": 5,
        "supply_only_aed": Decimal("3600"),
        "installed_min_aed": Decimal("4800"),
        "installed_avg_aed": Decimal("6000"),
        "installed_max_aed": Decimal("7800"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "OFFICE", "RESTAURANT"],
        "notes": "4-way cassette. 360 degree airflow. Requires false ceiling depth >= 250mm.",
        "tags": ["cassette", "ceiling", "2tr", "daikin", "r32", "4way"],
    },
    {
        "product_code": "CASS-3TR-CARRIER-R410A",
        "type_group": "CASSETTE_AC",
        "model": "42GQG036D / 38GQG036D",
        "brand": "Carrier",
        "capacity_tr": Decimal("3.0"),
        "capacity_kw": Decimal("9.5"),
        "refrigerant": "R410A",
        "efficiency_eer": Decimal("3.20"),
        "esma_star": 4,
        "supply_only_aed": Decimal("5000"),
        "installed_min_aed": Decimal("6500"),
        "installed_avg_aed": Decimal("8200"),
        "installed_max_aed": Decimal("10500"),
        "warranty_years": 3,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "OFFICE"],
        "notes": "Carrier 3T cassette. Quiet operation. BMS-compatible via BACnet gateway.",
        "tags": ["cassette", "ceiling", "3tr", "carrier", "r410a", "bms"],
    },
    {
        "product_code": "CASS-4TR-MITSUBISHI-R32",
        "type_group": "CASSETTE_AC",
        "model": "PCA-M140KA",
        "brand": "Mitsubishi Electric",
        "capacity_tr": Decimal("4.0"),
        "capacity_kw": Decimal("14.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("3.75"),
        "esma_star": 5,
        "supply_only_aed": Decimal("8200"),
        "installed_min_aed": Decimal("10500"),
        "installed_avg_aed": Decimal("13000"),
        "installed_max_aed": Decimal("16500"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "SHOWROOM"],
        "notes": "Premium Mitsubishi cassette. Very low noise 33dB(A). Suitable for Splash, Max Fashion.",
        "tags": ["cassette", "ceiling", "4tr", "mitsubishi", "r32", "quiet", "premium"],
    },

    # =========================================================================
    # VRF / VRV OUTDOOR UNITS
    # =========================================================================
    {
        "product_code": "VRF-ODU-8TR-DAIKIN-R32",
        "type_group": "VRF_VRV",
        "model": "RXYQ8T",
        "brand": "Daikin",
        "capacity_tr": Decimal("8.0"),
        "capacity_kw": Decimal("22.4"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("4.2"),
        "esma_star": 5,
        "supply_only_aed": Decimal("18000"),
        "installed_min_aed": Decimal("24000"),
        "installed_avg_aed": Decimal("30000"),
        "installed_max_aed": Decimal("38000"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "SHOWROOM", "OFFICE"],
        "notes": "Daikin VRV-IV outdoor unit 8TR. Connects up to 13 indoor units. R32 (low GWP).",
        "tags": ["vrf", "vrv", "outdoor", "8tr", "daikin", "r32", "multizone"],
    },
    {
        "product_code": "VRF-ODU-14TR-DAIKIN-R32",
        "type_group": "VRF_VRV",
        "model": "RXYQ14T",
        "brand": "Daikin",
        "capacity_tr": Decimal("14.0"),
        "capacity_kw": Decimal("40.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("4.4"),
        "esma_star": 5,
        "supply_only_aed": Decimal("32000"),
        "installed_min_aed": Decimal("42000"),
        "installed_avg_aed": Decimal("55000"),
        "installed_max_aed": Decimal("68000"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "SHOWROOM", "MEDIUM_RETAIL"],
        "notes": "14 TR VRF. Connects up to 22 indoor units. Ideal for a full Home Centre floor.",
        "tags": ["vrf", "vrv", "outdoor", "14tr", "daikin", "r32", "multizone"],
    },
    {
        "product_code": "VRF-ODU-20TR-MITSUBISHI-R32",
        "type_group": "VRF_VRV",
        "model": "PURY-EP200YNW-A1",
        "brand": "Mitsubishi Electric",
        "capacity_tr": Decimal("20.0"),
        "capacity_kw": Decimal("56.0"),
        "refrigerant": "R32",
        "efficiency_eer": Decimal("4.6"),
        "esma_star": 5,
        "supply_only_aed": Decimal("58000"),
        "installed_min_aed": Decimal("75000"),
        "installed_avg_aed": Decimal("92000"),
        "installed_max_aed": Decimal("115000"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MEDIUM_RETAIL", "SHOWROOM", "OFFICE_BLOCK"],
        "notes": "20TR Mitsubishi City Multi VRF. Connects up to 32 indoor units. Heat recovery optional.",
        "tags": ["vrf", "vrv", "outdoor", "20tr", "mitsubishi", "r32", "heat-recovery"],
    },
    {
        "product_code": "VRF-ODU-12TR-CARRIER-R410A",
        "type_group": "VRF_VRV",
        "model": "38HDQ120 XC",
        "brand": "Carrier",
        "capacity_tr": Decimal("12.0"),
        "capacity_kw": Decimal("33.5"),
        "refrigerant": "R410A",
        "efficiency_eer": Decimal("3.9"),
        "esma_star": 4,
        "supply_only_aed": Decimal("28000"),
        "installed_min_aed": Decimal("36000"),
        "installed_avg_aed": Decimal("46000"),
        "installed_max_aed": Decimal("58000"),
        "warranty_years": 3,
        "suitable_for": ["STANDALONE", "OFFICE", "SMALL_HOTEL"],
        "notes": "Carrier 12TR VRF outdoor. Connects up to 20 indoor units. R410A.",
        "tags": ["vrf", "outdoor", "12tr", "carrier", "r410a"],
    },

    # =========================================================================
    # VRF / VRV INDOOR UNITS
    # =========================================================================
    {
        "product_code": "VRF-IDU-CASS-1TR-DAIKIN",
        "type_group": "VRF_VRV",
        "model": "FXZQ25P",
        "brand": "Daikin",
        "capacity_tr": Decimal("1.0"),
        "capacity_kw": Decimal("2.8"),
        "refrigerant": "R32",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("2200"),
        "installed_min_aed": Decimal("3000"),
        "installed_avg_aed": Decimal("3800"),
        "installed_max_aed": Decimal("5000"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "OFFICE"],
        "notes": "VRF cassette indoor unit 1TR. Compact for small zones.",
        "tags": ["vrf", "indoor", "cassette", "1tr", "daikin"],
    },
    {
        "product_code": "VRF-IDU-CASS-2TR-DAIKIN",
        "type_group": "VRF_VRV",
        "model": "FXZQ50P",
        "brand": "Daikin",
        "capacity_tr": Decimal("2.0"),
        "capacity_kw": Decimal("5.6"),
        "refrigerant": "R32",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("3400"),
        "installed_min_aed": Decimal("4500"),
        "installed_avg_aed": Decimal("5500"),
        "installed_max_aed": Decimal("7000"),
        "warranty_years": 5,
        "suitable_for": ["STANDALONE", "MALL_UNIT", "OFFICE"],
        "notes": "VRF cassette indoor 2TR. Standard zone size for retail units.",
        "tags": ["vrf", "indoor", "cassette", "2tr", "daikin"],
    },

    # =========================================================================
    # FAN COIL UNITS -- CHILLED WATER
    # =========================================================================
    {
        "product_code": "FCU-CW-1TR-CARRIER",
        "type_group": "FCU_CW",
        "model": "42CC012-050",
        "brand": "Carrier",
        "capacity_tr": Decimal("1.0"),
        "capacity_kw": Decimal("3.5"),
        "refrigerant": "Chilled Water",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("1200"),
        "installed_min_aed": Decimal("1800"),
        "installed_avg_aed": Decimal("2400"),
        "installed_max_aed": Decimal("3200"),
        "warranty_years": 2,
        "suitable_for": ["MALL", "LARGE_OFFICE"],
        "notes": "Horizontal fan coil 1TR. For malls with central chilled water plant. Low noise 36dB.",
        "tags": ["fcu", "fan-coil", "chilled-water", "1tr", "carrier", "mall"],
    },
    {
        "product_code": "FCU-CW-2TR-CARRIER",
        "type_group": "FCU_CW",
        "model": "42CC024-050",
        "brand": "Carrier",
        "capacity_tr": Decimal("2.0"),
        "capacity_kw": Decimal("7.0"),
        "refrigerant": "Chilled Water",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("2200"),
        "installed_min_aed": Decimal("3200"),
        "installed_avg_aed": Decimal("4200"),
        "installed_max_aed": Decimal("5500"),
        "warranty_years": 2,
        "suitable_for": ["MALL", "LARGE_OFFICE"],
        "notes": "Horizontal FCU 2TR. Mall standard spec. BMS integration via Modbus.",
        "tags": ["fcu", "fan-coil", "chilled-water", "2tr", "carrier"],
    },
    {
        "product_code": "FCU-CW-3TR-MCQUAY",
        "type_group": "FCU_CW",
        "model": "MFM036",
        "brand": "McQuay (Daikin Applied)",
        "capacity_tr": Decimal("3.0"),
        "capacity_kw": Decimal("10.5"),
        "refrigerant": "Chilled Water",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("3500"),
        "installed_min_aed": Decimal("5000"),
        "installed_avg_aed": Decimal("6500"),
        "installed_max_aed": Decimal("8200"),
        "warranty_years": 2,
        "suitable_for": ["MALL", "ANCHOR_STORE", "LARGE_OFFICE"],
        "notes": "McQuay 3TR fan coil. Widely specified in UAE mall builds. BACnet/Modbus BMS.",
        "tags": ["fcu", "fan-coil", "chilled-water", "3tr", "mcquay", "mall"],
    },

    # =========================================================================
    # AIR HANDLING UNITS (AHU)
    # =========================================================================
    {
        "product_code": "AHU-15TR-CARRIER-DX",
        "type_group": "AHU",
        "model": "39HQ015",
        "brand": "Carrier",
        "capacity_tr": Decimal("15.0"),
        "capacity_kw": Decimal("52.0"),
        "refrigerant": "R410A / CW optional",
        "efficiency_eer": Decimal("3.1"),
        "esma_star": None,
        "supply_only_aed": Decimal("38000"),
        "installed_min_aed": Decimal("55000"),
        "installed_avg_aed": Decimal("72000"),
        "installed_max_aed": Decimal("95000"),
        "warranty_years": 2,
        "suitable_for": ["WAREHOUSE", "LARGE_RETAIL", "ANCHOR_STORE"],
        "notes": "15TR AHU with DX coil. Suitable for warehouse cooling or large retail floors.",
        "tags": ["ahu", "air-handling", "15tr", "carrier", "warehouse"],
    },
    {
        "product_code": "AHU-30TR-YORK-CW",
        "type_group": "AHU",
        "model": "YCM0303 CW",
        "brand": "York (Johnson Controls)",
        "capacity_tr": Decimal("30.0"),
        "capacity_kw": Decimal("105.0"),
        "refrigerant": "Chilled Water",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("95000"),
        "installed_min_aed": Decimal("130000"),
        "installed_avg_aed": Decimal("165000"),
        "installed_max_aed": Decimal("210000"),
        "warranty_years": 2,
        "suitable_for": ["ANCHOR_STORE", "HYPERMARKET", "LARGE_BUILDING"],
        "notes": "30TR CW AHU. For anchor tenants with central chilled water. NEBB commissioning.",
        "tags": ["ahu", "air-handling", "30tr", "york", "chilled-water", "large"],
    },

    # =========================================================================
    # PACKAGED ROOFTOP UNITS
    # =========================================================================
    {
        "product_code": "PKG-RTU-15TR-CARRIER-R407C",
        "type_group": "PACKAGED_UNIT",
        "model": "48XP015D",
        "brand": "Carrier",
        "capacity_tr": Decimal("15.0"),
        "capacity_kw": Decimal("52.0"),
        "refrigerant": "R407C",
        "efficiency_eer": Decimal("2.9"),
        "esma_star": None,
        "supply_only_aed": Decimal("52000"),
        "installed_min_aed": Decimal("72000"),
        "installed_avg_aed": Decimal("92000"),
        "installed_max_aed": Decimal("120000"),
        "warranty_years": 2,
        "suitable_for": ["WAREHOUSE", "LARGE_RETAIL", "SUPERMARKET"],
        "notes": "15TR rooftop DX packaged unit. Serves ductwork up to 1200 m2. GCC hot-ambient rated.",
        "tags": ["packaged", "rooftop", "15tr", "carrier", "warehouse", "duct"],
    },
    {
        "product_code": "PKG-RTU-25TR-YORK-R410A",
        "type_group": "PACKAGED_UNIT",
        "model": "YVAA025",
        "brand": "York (Johnson Controls)",
        "capacity_tr": Decimal("25.0"),
        "capacity_kw": Decimal("88.0"),
        "refrigerant": "R410A",
        "efficiency_eer": Decimal("3.0"),
        "esma_star": None,
        "supply_only_aed": Decimal("88000"),
        "installed_min_aed": Decimal("120000"),
        "installed_avg_aed": Decimal("152000"),
        "installed_max_aed": Decimal("195000"),
        "warranty_years": 2,
        "suitable_for": ["WAREHOUSE", "SUPERMARKET", "ANCHOR_STORE"],
        "notes": "25TR packaged unit. Suitable for Home Centre warehouse or large grocery.",
        "tags": ["packaged", "rooftop", "25tr", "york", "supermarket", "duct"],
    },

    # =========================================================================
    # CHILLER PLANTS
    # =========================================================================
    {
        "product_code": "CHILLER-AIRCOOLED-100TR-CARRIER",
        "type_group": "CHILLER",
        "model": "30XA-102",
        "brand": "Carrier",
        "capacity_tr": Decimal("100.0"),
        "capacity_kw": Decimal("351.0"),
        "refrigerant": "R134a",
        "efficiency_eer": Decimal("3.2"),
        "esma_star": None,
        "supply_only_aed": Decimal("380000"),
        "installed_min_aed": Decimal("520000"),
        "installed_avg_aed": Decimal("650000"),
        "installed_max_aed": Decimal("830000"),
        "warranty_years": 2,
        "suitable_for": ["ANCHOR_STORE", "DATA_CENTER", "LARGE_BUILDING"],
        "notes": "100TR air-cooled chiller. GCC hot-ambient rated to 52C. Suitable for anchor stores.",
        "tags": ["chiller", "air-cooled", "100tr", "carrier", "large", "data-center"],
    },
    {
        "product_code": "CHILLER-AIRCOOLED-200TR-MCQUAY",
        "type_group": "CHILLER",
        "model": "AGS-0200",
        "brand": "McQuay (Daikin Applied)",
        "capacity_tr": Decimal("200.0"),
        "capacity_kw": Decimal("703.0"),
        "refrigerant": "R134a",
        "efficiency_eer": Decimal("3.5"),
        "esma_star": None,
        "supply_only_aed": Decimal("750000"),
        "installed_min_aed": Decimal("1050000"),
        "installed_avg_aed": Decimal("1300000"),
        "installed_max_aed": Decimal("1650000"),
        "warranty_years": 3,
        "suitable_for": ["ANCHOR_STORE", "HYPERMARKET", "HOSPITALITY"],
        "notes": "200TR McQuay air-cooled scroll chiller. LEED-eligible. For L-Mega or anchor locations.",
        "tags": ["chiller", "air-cooled", "200tr", "mcquay", "hypermarket", "large"],
    },

    # =========================================================================
    # VENTILATION
    # =========================================================================
    {
        "product_code": "VENT-AXIAL-5KW-FLAKT",
        "type_group": "VENTILATION",
        "model": "AXCBF-315-6/12",
        "brand": "Flakt Group",
        "capacity_tr": None,
        "capacity_kw": Decimal("5.0"),
        "refrigerant": "N/A",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("3800"),
        "installed_min_aed": Decimal("5500"),
        "installed_avg_aed": Decimal("7200"),
        "installed_max_aed": Decimal("9500"),
        "warranty_years": 2,
        "suitable_for": ["WAREHOUSE", "RESTAURANT", "RETAIL"],
        "notes": "Axial ventilation fan 5kW. For supply/exhaust air. Suitable for back-of-house.",
        "tags": ["ventilation", "fan", "axial", "5kw", "exhaust"],
    },
    {
        "product_code": "VENT-ERV-3000CMH-ZEHNDER",
        "type_group": "VENTILATION",
        "model": "ComfoAir Q450",
        "brand": "Zehnder",
        "capacity_tr": None,
        "capacity_kw": Decimal("1.2"),
        "refrigerant": "N/A",
        "efficiency_eer": None,
        "esma_star": None,
        "supply_only_aed": Decimal("12500"),
        "installed_min_aed": Decimal("16000"),
        "installed_avg_aed": Decimal("20000"),
        "installed_max_aed": Decimal("26000"),
        "warranty_years": 3,
        "suitable_for": ["OFFICE", "MALL_UNIT"],
        "notes": "Energy recovery ventilator (ERV). Heat recovery efficiency > 90%. Reduces HVAC load by 30%.",
        "tags": ["ventilation", "erv", "energy-recovery", "zehnder", "office"],
    },
]

# ---------------------------------------------------------------------------
# Helper: Get products by type group
# ---------------------------------------------------------------------------

def get_products_by_group(group_code: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return products filtered by type_group, or all if group_code is None."""
    if group_code:
        return [p for p in HVAC_PRODUCT_CATALOGUE if p["type_group"] == group_code]
    return HVAC_PRODUCT_CATALOGUE


def get_product_by_code(product_code: str) -> Optional[Dict[str, Any]]:
    """Return a single product by its product_code, or None."""
    for p in HVAC_PRODUCT_CATALOGUE:
        if p["product_code"] == product_code:
            return p
    return None


def get_brands() -> List[str]:
    """Return sorted unique list of all brands in the catalogue."""
    return sorted(set(p["brand"] for p in HVAC_PRODUCT_CATALOGUE))


def get_product_code_choices() -> List[tuple]:
    """Return (product_code, label) tuples for use in form selectors."""
    return [
        (p["product_code"], f"{p['product_code']}  --  {p['brand']} {p['model']}  ({p['capacity_tr'] or ''}TR)")
        for p in HVAC_PRODUCT_CATALOGUE
    ]


# ---------------------------------------------------------------------------
# Facility Type Reference (for create form dropdown)
# Used to display contextual hints to the procurement officer
# ---------------------------------------------------------------------------

FACILITY_TYPES = [
    {
        "code": "MALL",
        "label": "Mall (Retail inside a mall)",
        "hint": "Malls typically have central chilled water. Check with mall operator for CW availability. FCU on CW is recommended.",
        "typical_system": "FCU_CW",
        "typical_capacity_per_100sqm": 3.5,
    },
    {
        "code": "STANDALONE",
        "label": "Standalone Store / High Street",
        "hint": "Standalone stores use VRF or cassette splits. No chilled water. Verify landlord restrictions on outdoor units.",
        "typical_system": "VRF_VRV",
        "typical_capacity_per_100sqm": 4.0,
    },
    {
        "code": "WAREHOUSE",
        "label": "Warehouse / Logistics",
        "hint": "Large open warehouses need packaged DX rooftop units or AHUs with ductwork. High ambient-rated equipment required for GCC.",
        "typical_system": "PACKAGED_UNIT",
        "typical_capacity_per_100sqm": 2.5,
    },
    {
        "code": "OFFICE",
        "label": "Office / HQ",
        "hint": "Offices work well with VRF for multi-zone control, or FCU on CW if in a commercial building with a chiller plant.",
        "typical_system": "VRF_VRV",
        "typical_capacity_per_100sqm": 3.5,
    },
    {
        "code": "RESTAURANT",
        "label": "Restaurant / F&B",
        "hint": "High fresh air requirements (ASHRAE 62.1). Include kitchen exhaust and makeup air. Cassette or VRF for dining area.",
        "typical_system": "CASSETTE_AC",
        "typical_capacity_per_100sqm": 6.0,
    },
    {
        "code": "DATA_CENTER",
        "label": "Data Center / Server Room",
        "hint": "Precision cooling required. N+1 redundancy mandatory. Chiller or in-row cooling. 24/7 operation spec.",
        "typical_system": "CHILLER",
        "typical_capacity_per_100sqm": 15.0,
    },
]

# ---------------------------------------------------------------------------
# Landmark Group Store Brands (for tagging requests by business unit)
# ---------------------------------------------------------------------------

LANDMARK_BRANDS = [
    {"code": "MAX_FASHION", "label": "Max Fashion", "category": "Retail / Apparel"},
    {"code": "HOME_CENTRE", "label": "Home Centre", "category": "Retail / Home"},
    {"code": "SPLASH", "label": "Splash", "category": "Retail / Apparel"},
    {"code": "LIFESTYLE", "label": "Lifestyle", "category": "Retail / Dept Store"},
    {"code": "CENTREPOINT", "label": "Centrepoint", "category": "Retail / Kids & Fashion"},
    {"code": "SHOEMART", "label": "Shoemart (SM)", "category": "Retail / Footwear"},
    {"code": "ICONIC", "label": "The Iconic", "category": "Retail / Premium"},
    {"code": "EMAX", "label": "Emax Electronics", "category": "Retail / Electronics"},
    {"code": "L_MEGA", "label": "LMega / Landmark Mega Store", "category": "Large Format"},
    {"code": "BABYSHOP", "label": "Babyshop", "category": "Retail / Kids"},
    {"code": "DISTRIBUTION", "label": "Distribution Centre (DC)", "category": "Logistics"},
    {"code": "HQ_OFFICE", "label": "HQ / Regional Office", "category": "Corporate"},
]
