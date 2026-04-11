"""
Management command: seed_benchmark_full

End-to-end seed for the Should-Cost Benchmarking module:

  Step 1: CategoryMaster records (8 HVAC categories)
  Step 2: VarianceThresholdConfig records (global + per-category overrides + geo-specific)
  Step 3: BenchmarkCorridorRule records (delegates to seed_benchmark_corridors logic)
  Step 4: BenchmarkRequest (5 realistic GCC projects)
  Step 5: BenchmarkQuotation (1-2 per request, with generated PDF files)
  Step 6: BenchmarkLineItem (8-14 per quotation, pre-classified + benchmarked)
  Step 7: BenchmarkResult (aggregated totals + negotiation notes)

Usage:
    python manage.py seed_benchmark_full
    python manage.py seed_benchmark_full --clear   # wipe transactional data first
    python manage.py seed_benchmark_full --skip-corridors  # skip re-seeding corridors
"""
import os
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    BenchmarkResult,
    CategoryMaster,
    LineCategory,
    Geography,
    ScopeType,
    VarianceStatus,
    VarianceThresholdConfig,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# CategoryMaster seed data
# ---------------------------------------------------------------------------

CATEGORY_MASTER_DATA = [
    {
        "code": "EQUIPMENT",
        "name": "HVAC Equipment",
        "description": (
            "Main mechanical plant items including VRF/VRV systems, chillers, AHUs, "
            "FCUs, split AC units, cooling towers, and associated primary equipment. "
            "Includes outdoor and indoor units."
        ),
        "keywords_csv": (
            "vrf,vrv,chiller,ahu,fcu,fan coil,packaged,dx unit,split ac,split unit,"
            "cassette,condenser,compressor,evaporator,outdoor unit,indoor unit,"
            "air handling,rtu,cooling tower,pump,heat pump"
        ),
        "sort_order": 10,
    },
    {
        "code": "CONTROLS",
        "name": "BMS & Controls",
        "description": (
            "Building Management System (BMS) components, DDC panels, SCADA, PLCs, "
            "thermostats, BACnet/Modbus integration, and control wiring. "
            "Includes programming and commissioning controllers."
        ),
        "keywords_csv": (
            "bms,bas,control panel,programming,scada,ddc,plc,thermostat,"
            "automation,modbus,bacnet,lonworks,commissioning controller,control wiring,"
            "sensor,actuator,vav controller"
        ),
        "sort_order": 20,
    },
    {
        "code": "DUCTING",
        "name": "Ductwork & Air Distribution",
        "description": (
            "Galvanised iron (GI) ductwork, flexible duct, oval/round duct, "
            "air diffusers, grilles, registers, volume control dampers (VCDs), "
            "and fire/smoke dampers."
        ),
        "keywords_csv": (
            "duct,ducting,ductwork,plenum,flex duct,spiro,gi duct,sheet metal,"
            "oval duct,round duct,air diffuser,grille,register,volume control,damper,"
            "vcd,fsd,smoke damper"
        ),
        "sort_order": 30,
    },
    {
        "code": "INSULATION",
        "name": "Thermal & Acoustic Insulation",
        "description": (
            "Pipe insulation (Armaflex, Kaiflex, nitrile foam), duct insulation, "
            "glass wool, rock wool, vapour barriers, and thermal wrapping. "
            "Includes acoustic insulation for plant rooms."
        ),
        "keywords_csv": (
            "insulation,insulate,armaflex,kaiflex,nitrile,glass wool,rock wool,foam,"
            "vapour barrier,thermal wrap,acoustic insulation,pipe lagging"
        ),
        "sort_order": 40,
    },
    {
        "code": "ACCESSORIES",
        "name": "Fittings & Accessories",
        "description": (
            "Hangers and supports, vibration isolators, flexible connectors, "
            "valves (ball, butterfly, gate, check), strainers, pressure gauges, "
            "thermometers, and other ancillary fittings."
        ),
        "keywords_csv": (
            "hanger,support,vibration isolator,flexible connector,valve,strainer,"
            "pressure gauge,thermometer,flow switch,ball valve,butterfly valve,"
            "gate valve,check valve,y-strainer,expansion vessel"
        ),
        "sort_order": 50,
    },
    {
        "code": "INSTALLATION",
        "name": "Installation & Civil Works",
        "description": (
            "Labour for equipment installation, fixing, mounting, and erection. "
            "Civil works including core cutting, grouting, slab penetrations. "
            "Electrical connections, cabling, and containment."
        ),
        "keywords_csv": (
            "installation,install,fixing,mounting,erection,labour,manpower,"
            "civil works,core cutting,grouting,cabling,electrical connection,"
            "cable tray,conduit,scaffolding"
        ),
        "sort_order": 60,
    },
    {
        "code": "TC",
        "name": "Testing & Commissioning",
        "description": (
            "Factory Acceptance Testing (FAT), Site Acceptance Testing (SAT), "
            "balancing of air and water systems, snagging, handover documentation, "
            "and HVAC commissioning reports."
        ),
        "keywords_csv": (
            "testing,commissioning,t&c,fat,sat,snagging,balancing,handover,"
            "hvac testing,air balancing,water balancing,commissioning report"
        ),
        "sort_order": 70,
    },
    {
        "code": "UNCATEGORIZED",
        "name": "Uncategorized / Miscellaneous",
        "description": (
            "Line items that do not fit any standard HVAC category. "
            "Review manually before finalising variance analysis."
        ),
        "keywords_csv": "",
        "sort_order": 99,
    },
]


# ---------------------------------------------------------------------------
# VarianceThresholdConfig seed data
# ---------------------------------------------------------------------------

VARIANCE_THRESHOLD_DATA = [
    # -- Global default (applies to ALL categories and ALL geographies) --
    {
        "category": "ALL",
        "geography": "ALL",
        "within_range_max_pct": 5.0,
        "moderate_max_pct": 15.0,
        "notes": "Global default variance thresholds for all HVAC categories",
    },
    # -- EQUIPMENT overrides (tighter: higher value items demand closer scrutiny) --
    {
        "category": "EQUIPMENT",
        "geography": "ALL",
        "within_range_max_pct": 5.0,
        "moderate_max_pct": 12.0,
        "notes": "Equipment items: moderate band tighter (>12% flags HIGH) due to high unit values",
    },
    {
        "category": "EQUIPMENT",
        "geography": "UAE",
        "within_range_max_pct": 4.0,
        "moderate_max_pct": 10.0,
        "notes": "UAE is a competitive, transparent market -- tightest equipment thresholds",
    },
    {
        "category": "EQUIPMENT",
        "geography": "QATAR",
        "within_range_max_pct": 7.0,
        "moderate_max_pct": 18.0,
        "notes": "Qatar: higher import duties and project complexity allow wider band",
    },
    # -- CONTROLS overrides --
    {
        "category": "CONTROLS",
        "geography": "ALL",
        "within_range_max_pct": 8.0,
        "moderate_max_pct": 20.0,
        "notes": "BMS/controls: wide variance band due to bespoke integration requirements",
    },
    # -- INSTALLATION overrides --
    {
        "category": "INSTALLATION",
        "geography": "ALL",
        "within_range_max_pct": 10.0,
        "moderate_max_pct": 25.0,
        "notes": "Labour/installation: significant variance driven by site conditions",
    },
    {
        "category": "INSTALLATION",
        "geography": "KSA",
        "within_range_max_pct": 12.0,
        "moderate_max_pct": 28.0,
        "notes": "KSA: expatriate labour costs and Saudisation requirements widen band",
    },
    # -- TC overrides --
    {
        "category": "TC",
        "geography": "ALL",
        "within_range_max_pct": 10.0,
        "moderate_max_pct": 25.0,
        "notes": "T&C costs vary significantly by system complexity and scope",
    },
]


# ---------------------------------------------------------------------------
# Full seed request / quotation / line item data
# ---------------------------------------------------------------------------

REQUESTS = [
    # =========================================================================
    # Request 1: Dubai Mall -- VRF SITC (UAE)
    # =========================================================================
    {
        "title": "Dubai Mall Expansion - HVAC VRF Replacement FY2026",
        "project_name": "Dubai Mall Phase 3 Fit-Out",
        "geography": "UAE",
        "scope_type": "SITC",
        "store_type": "MALL",
        "notes": "Full VRF replacement for 4,200 sqm retail expansion. DM landlord constraint: no new roof penetrations.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Al Rostamani HVAC Solutions",
                "quotation_ref": "ARH/Q/2026/0142",
                "line_items": [
                    {"ln": 1, "desc": "Daikin VRF Outdoor Unit 20HP R410A Energy Star", "cat": "EQUIPMENT", "uom": "No", "qty": "4", "rate": "18500", "amt": "74000", "bmin": "15000", "bmid": "17500", "bmax": "21000", "rule": "BC-EQUIP-UAE-001", "var": 5.71, "vstatus": "MODERATE"},
                    {"ln": 2, "desc": "Daikin VRF Indoor Cassette Unit 2.5HP", "cat": "EQUIPMENT", "uom": "No", "qty": "24", "rate": "2800", "amt": "67200", "bmin": "2200", "bmid": "2600", "bmax": "3100", "rule": "BC-EQUIP-UAE-005", "var": 7.69, "vstatus": "MODERATE"},
                    {"ln": 3, "desc": "BMS DDC Controller Panel with Bacnet Integration", "cat": "CONTROLS", "uom": "Lot", "qty": "1", "rate": "35000", "amt": "35000", "bmin": "28000", "bmid": "33000", "bmax": "40000", "rule": "BC-CTRL-UAE-001", "var": 6.06, "vstatus": "MODERATE"},
                    {"ln": 4, "desc": "GI Rectangular Ducting 1.2mm Grade A", "cat": "DUCTING", "uom": "m2", "qty": "850", "rate": "95", "amt": "80750", "bmin": "80", "bmid": "90", "bmax": "110", "rule": "BC-DUCT-UAE-001", "var": 5.56, "vstatus": "MODERATE"},
                    {"ln": 5, "desc": "Armaflex Pipe Insulation 25mm Class O", "cat": "INSULATION", "uom": "m", "qty": "420", "rate": "28", "amt": "11760", "bmin": "22", "bmid": "27", "bmax": "35", "rule": "BC-INSUL-UAE-001", "var": 3.70, "vstatus": "WITHIN_RANGE"},
                    {"ln": 6, "desc": "Flexible GI Duct Connector 300mm dia", "cat": "ACCESSORIES", "uom": "No", "qty": "32", "rate": "185", "amt": "5920", "bmin": "150", "bmid": "175", "bmax": "220", "rule": "BC-ACC-UAE-001", "var": 5.71, "vstatus": "MODERATE"},
                    {"ln": 7, "desc": "Installation Labour - VRF System Complete", "cat": "INSTALLATION", "uom": "Lot", "qty": "1", "rate": "45000", "amt": "45000", "bmin": "35000", "bmid": "42000", "bmax": "55000", "rule": "BC-INST-UAE-001", "var": 7.14, "vstatus": "MODERATE"},
                    {"ln": 8, "desc": "Testing and Commissioning - Full HVAC Systems", "cat": "TC", "uom": "Lot", "qty": "1", "rate": "9500", "amt": "9500", "bmin": "7000", "bmid": "9000", "bmax": "12000", "rule": "BC-TC-UAE-001", "var": 5.56, "vstatus": "MODERATE"},
                ],
            },
            {
                "supplier_name": "Emirates Technical Services",
                "quotation_ref": "ETS/Q/2026/0089",
                "line_items": [
                    {"ln": 1, "desc": "Mitsubishi Electric VRF Outdoor Unit 22HP", "cat": "EQUIPMENT", "uom": "No", "qty": "4", "rate": "19200", "amt": "76800", "bmin": "15000", "bmid": "17500", "bmax": "21000", "rule": "BC-EQUIP-UAE-001", "var": 9.71, "vstatus": "MODERATE"},
                    {"ln": 2, "desc": "Mitsubishi Electric VRF Cassette Indoor 2.5HP", "cat": "EQUIPMENT", "uom": "No", "qty": "24", "rate": "2950", "amt": "70800", "bmin": "2200", "bmid": "2600", "bmax": "3100", "rule": "BC-EQUIP-UAE-005", "var": 13.46, "vstatus": "MODERATE"},
                    {"ln": 3, "desc": "Schneider Electric EcoStruxure BMS System", "cat": "CONTROLS", "uom": "Lot", "qty": "1", "rate": "42000", "amt": "42000", "bmin": "28000", "bmid": "33000", "bmax": "40000", "rule": "BC-CTRL-UAE-001", "var": 27.27, "vstatus": "HIGH"},
                    {"ln": 4, "desc": "GI Sheet Metal Ductwork Including Fittings", "cat": "DUCTING", "uom": "m2", "qty": "850", "rate": "88", "amt": "74800", "bmin": "80", "bmid": "90", "bmax": "110", "rule": "BC-DUCT-UAE-001", "var": -2.22, "vstatus": "WITHIN_RANGE"},
                    {"ln": 5, "desc": "Nitrile Foam Pipe Insulation 19mm wall", "cat": "INSULATION", "uom": "m", "qty": "420", "rate": "24", "amt": "10080", "bmin": "22", "bmid": "27", "bmax": "35", "rule": "BC-INSUL-UAE-001", "var": -11.11, "vstatus": "MODERATE"},
                    {"ln": 6, "desc": "Installation Labour Including Structural Supports", "cat": "INSTALLATION", "uom": "Lot", "qty": "1", "rate": "48000", "amt": "48000", "bmin": "35000", "bmid": "42000", "bmax": "55000", "rule": "BC-INST-UAE-001", "var": 14.29, "vstatus": "MODERATE"},
                    {"ln": 7, "desc": "T&C and Air Balancing by Certified Engineer", "cat": "TC", "uom": "Lot", "qty": "1", "rate": "11000", "amt": "11000", "bmin": "7000", "bmid": "9000", "bmax": "12000", "rule": "BC-TC-UAE-001", "var": 22.22, "vstatus": "HIGH"},
                ],
            },
        ],
    },
    # =========================================================================
    # Request 2: Riyadh Hypermarket -- Chiller ITC (KSA)
    # =========================================================================
    {
        "title": "Lulu Hypermarket Riyadh - Chiller Plant ITC",
        "project_name": "Lulu KSA Phase 2 Expansion",
        "geography": "KSA",
        "scope_type": "ITC",
        "store_type": "HYPERMARKET",
        "notes": "Install, test and commission of chilled water system (chillers and FCUs supplied by client). DLP: 12 months.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Saudi HVAC Corporation",
                "quotation_ref": "SHC/Q/2026/KSA/0311",
                "line_items": [
                    {"ln": 1, "desc": "Chilled Water Piping GMS Class C 100mm dia", "cat": "ACCESSORIES", "uom": "m", "qty": "280", "rate": "95", "amt": "26600", "bmin": "75", "bmid": "90", "bmax": "115", "rule": "BC-PIPE-KSA-001", "var": 5.56, "vstatus": "MODERATE"},
                    {"ln": 2, "desc": "GIS 200mm Butterfly Valve with Actuator", "cat": "ACCESSORIES", "uom": "No", "qty": "12", "rate": "1850", "amt": "22200", "bmin": "1500", "bmid": "1750", "bmax": "2200", "rule": "BC-ACC-KSA-001", "var": 5.71, "vstatus": "MODERATE"},
                    {"ln": 3, "desc": "GI Sheet Metal Ducting Supply and Return", "cat": "DUCTING", "uom": "m2", "qty": "1200", "rate": "85", "amt": "102000", "bmin": "70", "bmid": "82", "bmax": "100", "rule": "BC-DUCT-KSA-001", "var": 3.66, "vstatus": "WITHIN_RANGE"},
                    {"ln": 4, "desc": "Armaflex AF 32mm Pipe Insulation", "cat": "INSULATION", "uom": "m", "qty": "560", "rate": "32", "amt": "17920", "bmin": "25", "bmid": "30", "bmax": "38", "rule": "BC-INSUL-KSA-001", "var": 6.67, "vstatus": "MODERATE"},
                    {"ln": 5, "desc": "FCU Installation Labour Cassette Type", "cat": "INSTALLATION", "uom": "No", "qty": "48", "rate": "750", "amt": "36000", "bmin": "550", "bmid": "700", "bmax": "900", "rule": "BC-INST-KSA-001", "var": 7.14, "vstatus": "MODERATE"},
                    {"ln": 6, "desc": "Chiller Installation Heavy Lift and Rigging", "cat": "INSTALLATION", "uom": "No", "qty": "3", "rate": "15000", "amt": "45000", "bmin": "10000", "bmid": "13000", "bmax": "18000", "rule": "BC-INST-KSA-001", "var": 15.38, "vstatus": "HIGH"},
                    {"ln": 7, "desc": "BMS Honeywell Lyric Integration Panel", "cat": "CONTROLS", "uom": "Lot", "qty": "1", "rate": "28000", "amt": "28000", "bmin": "22000", "bmid": "26000", "bmax": "32000", "rule": "BC-CTRL-KSA-001", "var": 7.69, "vstatus": "MODERATE"},
                    {"ln": 8, "desc": "Commissioning Water and Air Balancing", "cat": "TC", "uom": "Lot", "qty": "1", "rate": "18000", "amt": "18000", "bmin": "12000", "bmid": "16000", "bmax": "22000", "rule": "BC-TC-KSA-001", "var": 12.50, "vstatus": "MODERATE"},
                    {"ln": 9, "desc": "Pipe Supports and Trapeze Hangers", "cat": "ACCESSORIES", "uom": "Lot", "qty": "1", "rate": "9500", "amt": "9500", "bmin": "7000", "bmid": "9000", "bmax": "12000", "rule": "BC-ACC-KSA-001", "var": 5.56, "vstatus": "MODERATE"},
                ],
            },
        ],
    },
    # =========================================================================
    # Request 3: Doha Hotel -- Chilled Water SITC (QATAR)
    # =========================================================================
    {
        "title": "Doha Grand Hotel HVAC Chilled Water System SITC",
        "project_name": "Doha Grand Hotel 5-Star Renovation",
        "geography": "QATAR",
        "scope_type": "SITC",
        "store_type": "HOTEL",
        "notes": "5-star hotel renovation, 280 rooms. Qatar KAHRAMAA compliant design. Stringent acoustic requirements.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Qatar MEP Contractors LLC",
                "quotation_ref": "QMEP/Q/2026/0077",
                "line_items": [
                    {"ln": 1, "desc": "York Water-Cooled Chiller 120TR Model CHPC", "cat": "EQUIPMENT", "uom": "No", "qty": "2", "rate": "175000", "amt": "350000", "bmin": "135000", "bmid": "158000", "bmax": "185000", "rule": "BC-EQUIP-QAT-002", "var": 10.76, "vstatus": "MODERATE"},
                    {"ln": 2, "desc": "Cooling Tower Marley 120TR Cross-Flow", "cat": "EQUIPMENT", "uom": "No", "qty": "2", "rate": "38000", "amt": "76000", "bmin": "28000", "bmid": "35000", "bmax": "45000", "rule": "BC-EQUIP-QAT-001", "var": 8.57, "vstatus": "MODERATE"},
                    {"ln": 3, "desc": "Chilled Water Primary Pump Set 50Hz", "cat": "EQUIPMENT", "uom": "Set", "qty": "2", "rate": "22000", "amt": "44000", "bmin": "16000", "bmid": "20000", "bmax": "27000", "rule": "BC-EQUIP-QAT-001", "var": 10.00, "vstatus": "MODERATE"},
                    {"ln": 4, "desc": "Fan Coil Unit 2TR 4-Pipe Cassette Qt", "cat": "EQUIPMENT", "uom": "No", "qty": "280", "rate": "3200", "amt": "896000", "bmin": "2500", "bmid": "2900", "bmax": "3600", "rule": "BC-EQUIP-QAT-001", "var": 10.34, "vstatus": "MODERATE"},
                    {"ln": 5, "desc": "Chilled and Condenser Water Piping PCCS PPR", "cat": "ACCESSORIES", "uom": "m", "qty": "480", "rate": "130", "amt": "62400", "bmin": "100", "bmid": "120", "bmax": "155", "rule": "BC-PIPE-QAT-001", "var": 8.33, "vstatus": "MODERATE"},
                    {"ln": 6, "desc": "Honeywell Delta Controls BMS Full Building", "cat": "CONTROLS", "uom": "Lot", "qty": "1", "rate": "85000", "amt": "85000", "bmin": "60000", "bmid": "78000", "bmax": "100000", "rule": "BC-CTRL-QAT-001", "var": 8.97, "vstatus": "MODERATE"},
                    {"ln": 7, "desc": "GI Ductwork Rectangular 1.2mm Galvanised", "cat": "DUCTING", "uom": "m2", "qty": "2400", "rate": "115", "amt": "276000", "bmin": "90", "bmid": "108", "bmax": "135", "rule": "BC-DUCT-QAT-001", "var": 6.48, "vstatus": "MODERATE"},
                    {"ln": 8, "desc": "Kaiflex Pipe and Duct Insulation Package", "cat": "INSULATION", "uom": "Lot", "qty": "1", "rate": "48000", "amt": "48000", "bmin": "35000", "bmid": "45000", "bmax": "58000", "rule": "BC-INSUL-QAT-001", "var": 6.67, "vstatus": "MODERATE"},
                    {"ln": 9, "desc": "Installation Labour Chiller Plant and FCUs", "cat": "INSTALLATION", "uom": "Lot", "qty": "1", "rate": "145000", "amt": "145000", "bmin": "100000", "bmid": "130000", "bmax": "170000", "rule": "BC-INST-QAT-001", "var": 11.54, "vstatus": "MODERATE"},
                    {"ln": 10, "desc": "Testing Commissioning and Air Balancing", "cat": "TC", "uom": "Lot", "qty": "1", "rate": "32000", "amt": "32000", "bmin": "22000", "bmid": "28000", "bmax": "38000", "rule": "BC-TC-QAT-001", "var": 14.29, "vstatus": "MODERATE"},
                    {"ln": 11, "desc": "Vibration Isolation Mounts for Chiller Set", "cat": "ACCESSORIES", "uom": "Set", "qty": "2", "rate": "4500", "amt": "9000", "bmin": "3200", "bmid": "4000", "bmax": "5500", "rule": "BC-ACC-QAT-001", "var": 12.50, "vstatus": "MODERATE"},
                ],
            },
        ],
    },
    # =========================================================================
    # Request 4: Abu Dhabi Warehouse -- Packaged Units Equipment Only (UAE)
    # =========================================================================
    {
        "title": "JAFZA Logistics Hub - Packaged DX Units Equipment Only",
        "project_name": "JAFZA Dubai Logistics Phase 1",
        "geography": "UAE",
        "scope_type": "EQUIPMENT_ONLY",
        "store_type": "WAREHOUSE",
        "notes": "Equipment-only supply for 3,800 sqm warehouse. Client handles installation with own MEP team.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Carrier Gulf Distributors",
                "quotation_ref": "CGD/Q/2026/0412",
                "line_items": [
                    {"ln": 1, "desc": "Carrier Packaged Rooftop Unit 25TR AHU-01", "cat": "EQUIPMENT", "uom": "No", "qty": "3", "rate": "26000", "amt": "78000", "bmin": "18000", "bmid": "22000", "bmax": "28000", "rule": "BC-EQUIP-UAE-004", "var": 18.18, "vstatus": "HIGH"},
                    {"ln": 2, "desc": "Carrier Packaged Rooftop Unit 20TR AHU-02", "cat": "EQUIPMENT", "uom": "No", "qty": "4", "rate": "21500", "amt": "86000", "bmin": "16000", "bmid": "20000", "bmax": "25000", "rule": "BC-EQUIP-UAE-004", "var": 7.50, "vstatus": "MODERATE"},
                    {"ln": 3, "desc": "Supply Air GI Rectangular Duct 600x400mm", "cat": "DUCTING", "uom": "m2", "qty": "320", "rate": "92", "amt": "29440", "bmin": "80", "bmid": "90", "bmax": "110", "rule": "BC-DUCT-UAE-001", "var": 2.22, "vstatus": "WITHIN_RANGE"},
                    {"ln": 4, "desc": "Supply/Return Air Grilles and Diffusers", "cat": "DUCTING", "uom": "No", "qty": "85", "rate": "220", "amt": "18700", "bmin": "160", "bmid": "200", "bmax": "260", "rule": "BC-DUCT-UAE-001", "var": 10.00, "vstatus": "MODERATE"},
                    {"ln": 5, "desc": "Motor Operated Dampers 600x400", "cat": "ACCESSORIES", "uom": "No", "qty": "14", "rate": "1250", "amt": "17500", "bmin": "950", "bmid": "1150", "bmax": "1500", "rule": "BC-ACC-UAE-001", "var": 8.70, "vstatus": "MODERATE"},
                ],
            },
            {
                "supplier_name": "Zamil Industrial HVAC",
                "quotation_ref": "ZAM/Q/2026/DXB/0228",
                "line_items": [
                    {"ln": 1, "desc": "Zamil Packaged Rooftop DX Unit 25TR Class A", "cat": "EQUIPMENT", "uom": "No", "qty": "3", "rate": "24500", "amt": "73500", "bmin": "18000", "bmid": "22000", "bmax": "28000", "rule": "BC-EQUIP-UAE-004", "var": 11.36, "vstatus": "MODERATE"},
                    {"ln": 2, "desc": "Zamil Packaged DX Unit 20TR R410A Inverter", "cat": "EQUIPMENT", "uom": "No", "qty": "4", "rate": "20500", "amt": "82000", "bmin": "16000", "bmid": "20000", "bmax": "25000", "rule": "BC-EQUIP-UAE-004", "var": 2.50, "vstatus": "WITHIN_RANGE"},
                    {"ln": 3, "desc": "GI Ducting Supply 1.0mm Galvanised", "cat": "DUCTING", "uom": "m2", "qty": "320", "rate": "86", "amt": "27520", "bmin": "80", "bmid": "90", "bmax": "110", "rule": "BC-DUCT-UAE-001", "var": -4.44, "vstatus": "WITHIN_RANGE"},
                    {"ln": 4, "desc": "Linear Bar Grilles 600x200mm Anodized", "cat": "DUCTING", "uom": "No", "qty": "85", "rate": "195", "amt": "16575", "bmin": "160", "bmid": "200", "bmax": "260", "rule": "BC-DUCT-UAE-001", "var": -2.50, "vstatus": "WITHIN_RANGE"},
                    {"ln": 5, "desc": "Volume Control Dampers 600x400mm", "cat": "ACCESSORIES", "uom": "No", "qty": "14", "rate": "1100", "amt": "15400", "bmin": "950", "bmid": "1150", "bmax": "1500", "rule": "BC-ACC-UAE-001", "var": -4.35, "vstatus": "WITHIN_RANGE"},
                ],
            },
        ],
    },
    # =========================================================================
    # Request 5: Jeddah Retail -- Split ACs SITC (KSA) - PENDING
    # =========================================================================
    {
        "title": "H&M Jeddah Mall - Split AC Replacement SITC",
        "project_name": "H&M KSA Store Refresh Program",
        "geography": "KSA",
        "scope_type": "SITC",
        "store_type": "RETAIL",
        "notes": "30-unit split AC replacement for 1,800 sqm KSA retail store. KSA SASO compliant units required.",
        "status": "PENDING",
        "quotations": [
            {
                "supplier_name": "Al-Zamil HVAC Services",
                "quotation_ref": "AZS/Q/2026/JED/0055",
                "line_items": [
                    {"ln": 1, "desc": "Gree Split AC 2TR SASO Certified Inverter", "cat": "EQUIPMENT", "uom": "No", "qty": "30", "rate": "3800", "amt": "114000", "bmin": "3200", "bmid": "3600", "bmax": "4500", "rule": "BC-EQUIP-KSA-003", "var": 5.56, "vstatus": "MODERATE"},
                    {"ln": 2, "desc": "GI Rectangular Ducting Supply 1.0mm Grade", "cat": "DUCTING", "uom": "m2", "qty": "650", "rate": "78", "amt": "50700", "bmin": "65", "bmid": "76", "bmax": "95", "rule": "BC-DUCT-KSA-001", "var": 2.63, "vstatus": "WITHIN_RANGE"},
                    {"ln": 3, "desc": "Nitrile Insulation Pipe 19mm Wall", "cat": "INSULATION", "uom": "m", "qty": "380", "rate": "28", "amt": "10640", "bmin": "22", "bmid": "27", "bmax": "34", "rule": "BC-INSUL-KSA-001", "var": 3.70, "vstatus": "WITHIN_RANGE"},
                    {"ln": 4, "desc": "Electrical Connection and Cabling per Unit", "cat": "INSTALLATION", "uom": "No", "qty": "30", "rate": "450", "amt": "13500", "bmin": "320", "bmid": "420", "bmax": "560", "rule": "BC-INST-KSA-001", "var": 7.14, "vstatus": "MODERATE"},
                    {"ln": 5, "desc": "Installation Labour Including Supports", "cat": "INSTALLATION", "uom": "No", "qty": "30", "rate": "680", "amt": "20400", "bmin": "500", "bmid": "650", "bmax": "850", "rule": "BC-INST-KSA-001", "var": 4.62, "vstatus": "WITHIN_RANGE"},
                    {"ln": 6, "desc": "Testing Commissioning and Handing Over", "cat": "TC", "uom": "Lot", "qty": "1", "rate": "8500", "amt": "8500", "bmin": "5500", "bmid": "8000", "bmax": "11000", "rule": "BC-TC-KSA-001", "var": 6.25, "vstatus": "MODERATE"},
                ],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Full end-to-end seed for the Benchmarking module: "
        "CategoryMaster, VarianceThresholdConfig, BenchmarkCorridorRule, "
        "BenchmarkRequest, BenchmarkQuotation, BenchmarkLineItem, BenchmarkResult."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing benchmarking transactional data before seeding.",
        )
        parser.add_argument(
            "--skip-corridors",
            action="store_true",
            dest="skip_corridors",
            help="Skip re-seeding BenchmarkCorridorRule records.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== seed_benchmark_full starting ==="))

        if options["clear"]:
            self._clear_data()

        user = self._get_or_create_user()

        counts = {
            "categories": 0,
            "thresholds": 0,
            "corridors": 0,
            "requests": 0,
            "quotations": 0,
            "line_items": 0,
            "results": 0,
        }

        # Step 1: CategoryMaster
        counts["categories"] = self._seed_category_master()

        # Step 2: VarianceThresholdConfig
        counts["thresholds"] = self._seed_variance_thresholds()

        # Step 3: BenchmarkCorridorRule
        if not options["skip_corridors"]:
            counts["corridors"] = self._seed_corridors()
        else:
            counts["corridors"] = BenchmarkCorridorRule.objects.count()
            self.stdout.write("  [skip] BenchmarkCorridorRule seeding skipped.")

        # Steps 4-7: Requests, Quotations, LineItems, Results
        r, q, l, res = self._seed_requests(user)
        counts["requests"] = r
        counts["quotations"] = q
        counts["line_items"] = l
        counts["results"] = res

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("=== seed_benchmark_full complete ==="))
        self.stdout.write(f"  CategoryMaster records       : {counts['categories']}")
        self.stdout.write(f"  VarianceThresholdConfig      : {counts['thresholds']}")
        self.stdout.write(f"  BenchmarkCorridorRule        : {counts['corridors']}")
        self.stdout.write(f"  BenchmarkRequest             : {counts['requests']}")
        self.stdout.write(f"  BenchmarkQuotation           : {counts['quotations']}")
        self.stdout.write(f"  BenchmarkLineItem            : {counts['line_items']}")
        self.stdout.write(f"  BenchmarkResult              : {counts['results']}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_data(self):
        self.stdout.write(self.style.WARNING("  Clearing existing benchmarking data..."))
        BenchmarkResult.objects.all().delete()
        BenchmarkLineItem.objects.all().delete()
        BenchmarkQuotation.objects.all().delete()
        BenchmarkRequest.objects.all().delete()
        CategoryMaster.objects.all().delete()
        VarianceThresholdConfig.objects.all().delete()
        self.stdout.write("  Done.")

    def _get_or_create_user(self):
        user = User.objects.filter(is_superuser=True).first()
        if not user:
            user = User.objects.filter(is_staff=True).first()
        return user

    # ------------------------------------------------------------------
    # Step 1: CategoryMaster
    # ------------------------------------------------------------------

    def _seed_category_master(self) -> int:
        self.stdout.write("  [1/7] Seeding CategoryMaster...")
        count = 0
        for data in CATEGORY_MASTER_DATA:
            _, created = CategoryMaster.objects.update_or_create(
                code=data["code"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "keywords_csv": data["keywords_csv"],
                    "sort_order": data["sort_order"],
                    "is_active": True,
                },
            )
            if created:
                count += 1
        total = CategoryMaster.objects.count()
        self.stdout.write(f"       Created {count} new | Total: {total}")
        return total

    # ------------------------------------------------------------------
    # Step 2: VarianceThresholdConfig
    # ------------------------------------------------------------------

    def _seed_variance_thresholds(self) -> int:
        self.stdout.write("  [2/7] Seeding VarianceThresholdConfig...")
        count = 0
        for data in VARIANCE_THRESHOLD_DATA:
            _, created = VarianceThresholdConfig.objects.update_or_create(
                category=data["category"],
                geography=data["geography"],
                defaults={
                    "within_range_max_pct": data["within_range_max_pct"],
                    "moderate_max_pct": data["moderate_max_pct"],
                    "notes": data["notes"],
                    "is_active": True,
                },
            )
            if created:
                count += 1
        total = VarianceThresholdConfig.objects.count()
        self.stdout.write(f"       Created {count} new | Total: {total}")
        return total

    # ------------------------------------------------------------------
    # Step 3: BenchmarkCorridorRule
    # ------------------------------------------------------------------

    def _seed_corridors(self) -> int:
        """Import corridor data from the existing seed_benchmark_corridors command."""
        self.stdout.write("  [3/7] Seeding BenchmarkCorridorRule...")
        try:
            from apps.benchmarking.management.commands.seed_benchmark_corridors import CORRIDORS
            count = 0
            for data in CORRIDORS:
                _, created = BenchmarkCorridorRule.objects.update_or_create(
                    rule_code=data["rule_code"],
                    defaults={
                        "name": data["name"],
                        "category": data["category"],
                        "scope_type": data.get("scope_type", "ALL"),
                        "geography": data.get("geography", "ALL"),
                        "uom": data.get("uom", ""),
                        "min_rate": data["min_rate"],
                        "mid_rate": data["mid_rate"],
                        "max_rate": data["max_rate"],
                        "currency": data.get("currency", "AED"),
                        "keywords": data.get("keywords", ""),
                        "notes": data.get("notes", ""),
                        "priority": data.get("priority", 100),
                        "is_active": True,
                    },
                )
                if created:
                    count += 1
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"       Could not load corridor data: {exc}"))
            count = 0
        total = BenchmarkCorridorRule.objects.count()
        self.stdout.write(f"       Created {count} new | Total: {total}")
        return total

    # ------------------------------------------------------------------
    # Steps 4-7: Requests, Quotations, LineItems, Results
    # ------------------------------------------------------------------

    def _seed_requests(self, user) -> tuple[int, int, int, int]:
        self.stdout.write("  [4/7] Seeding BenchmarkRequest + Quotations + LineItems + Results...")
        req_count = 0
        quot_count = 0
        item_count = 0
        result_count = 0

        for req_data in REQUESTS:
            # Create or update the BenchmarkRequest
            bench_request, req_created = BenchmarkRequest.objects.update_or_create(
                title=req_data["title"],
                defaults={
                    "project_name": req_data["project_name"],
                    "geography": req_data["geography"],
                    "scope_type": req_data["scope_type"],
                    "store_type": req_data.get("store_type", ""),
                    "notes": req_data.get("notes", ""),
                    "status": req_data["status"],
                    "submitted_by": user,
                    "is_active": True,
                },
            )
            if req_created:
                req_count += 1

            all_line_items = []

            for quot_data in req_data.get("quotations", []):
                # Generate a minimal PDF for the quotation
                pdf_bytes = self._make_pdf(bench_request, quot_data)

                # Build document file object
                from django.core.files.uploadedfile import SimpleUploadedFile
                pdf_file = SimpleUploadedFile(
                    name=f"{quot_data['quotation_ref'].replace('/', '_')}.pdf",
                    content=pdf_bytes,
                    content_type="application/pdf",
                )

                # Check if quotation already exists for idempotency
                existing = BenchmarkQuotation.objects.filter(
                    request=bench_request,
                    quotation_ref=quot_data["quotation_ref"],
                ).first()

                if existing:
                    quotation = existing
                else:
                    quotation = BenchmarkQuotation.objects.create(
                        request=bench_request,
                        supplier_name=quot_data["supplier_name"],
                        quotation_ref=quot_data["quotation_ref"],
                        document=pdf_file,
                        extraction_status="DONE",
                        extracted_text=self._build_extracted_text(quot_data),
                        is_active=True,
                    )
                    quot_count += 1

                # Build line items (delete existing + re-seed)
                quotation.line_items.all().delete()

                for li in quot_data.get("line_items", []):
                    item = BenchmarkLineItem.objects.create(
                        quotation=quotation,
                        line_number=li["ln"],
                        description=li["desc"],
                        uom=li.get("uom", ""),
                        quantity=Decimal(str(li["qty"])) if li.get("qty") else None,
                        quoted_unit_rate=Decimal(str(li["rate"])) if li.get("rate") else None,
                        line_amount=Decimal(str(li["amt"])) if li.get("amt") else None,
                        extraction_confidence=0.92,
                        classification_source="AI",
                        category=li["cat"],
                        classification_confidence=0.95,
                        benchmark_min=Decimal(str(li["bmin"])) if li.get("bmin") else None,
                        benchmark_mid=Decimal(str(li["bmid"])) if li.get("bmid") else None,
                        benchmark_max=Decimal(str(li["bmax"])) if li.get("bmax") else None,
                        corridor_rule_code=li.get("rule", ""),
                        variance_pct=li.get("var"),
                        variance_status=li.get("vstatus", VarianceStatus.NEEDS_REVIEW),
                        variance_note=self._variance_note(li),
                        benchmark_source="CORRIDOR_DB" if li.get("bmin") else "NONE",
                        is_active=True,
                    )
                    if user:
                        item.created_by = user
                        item.save(update_fields=["created_by"])
                    all_line_items.append(item)
                    item_count += 1

            # Build BenchmarkResult for COMPLETED requests
            if req_data["status"] == "COMPLETED" and all_line_items:
                self._build_result(bench_request, all_line_items, user)
                result_count += 1
                bench_request.status = "COMPLETED"
                bench_request.save(update_fields=["status"])

        self.stdout.write(
            f"       Requests: {req_count} new | "
            f"Quotations: {quot_count} new | "
            f"LineItems: {item_count} | "
            f"Results: {result_count}"
        )
        return req_count, quot_count, item_count, result_count

    def _build_extracted_text(self, quot_data: dict) -> str:
        """Build realistic extracted text string for a quotation."""
        lines = [
            f"QUOTATION: {quot_data['quotation_ref']}",
            f"Supplier: {quot_data['supplier_name']}",
            "",
            f"{'No.':<5} {'Description':<50} {'UOM':<8} {'Qty':<7} {'Unit Rate':>12} {'Amount':>12}",
            "-" * 98,
        ]
        for li in quot_data.get("line_items", []):
            lines.append(
                f"{str(li['ln']):<5} {li['desc'][:48]:<50} "
                f"{li.get('uom', ''):<8} {li.get('qty', ''):<7} "
                f"{li.get('rate', ''):>12} {li.get('amt', ''):>12}"
            )
        lines.append("-" * 98)
        total = sum(float(str(li.get("amt", 0)).replace(",", "")) for li in quot_data.get("line_items", []))
        lines.append(f"{'':>82} TOTAL: AED {total:>12,.2f}")
        return "\n".join(lines)

    def _make_pdf(self, bench_request, quot_data: dict) -> bytes:
        """Generate a minimal PDF for the quotation."""
        try:
            from apps.benchmarking.fixtures.pdf_generator import generate_quotation_pdf
            rows = []
            for li in quot_data.get("line_items", []):
                rows.append((
                    li["ln"],
                    li["desc"],
                    li.get("uom", ""),
                    li.get("qty", ""),
                    li.get("rate", ""),
                    li.get("amt", ""),
                ))
            return generate_quotation_pdf(
                title=bench_request.title,
                supplier_name=quot_data["supplier_name"],
                ref_number=quot_data["quotation_ref"],
                rows=rows,
                footer_text=(
                    f"Geography: {bench_request.geography} | "
                    f"Scope: {bench_request.scope_type} | "
                    f"Project: {bench_request.project_name}"
                ),
            )
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"       PDF generation failed for {quot_data['quotation_ref']}: {exc}"))
            # Return minimal valid PDF bytes
            return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \nttrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF"

    def _variance_note(self, li: dict) -> str:
        var = li.get("var")
        vstatus = li.get("vstatus", "")
        if var is None:
            return ""
        rate = li.get("rate", "?")
        bmid = li.get("bmid", "?")
        if vstatus == "HIGH":
            return (
                f"Quoted {rate} vs benchmark mid {bmid}. "
                f"ALERT: {var:+.1f}% above benchmark -- negotiate this line item."
            )
        if vstatus == "MODERATE":
            return f"Quoted {rate} is {var:+.1f}% vs benchmark mid {bmid}. Within negotiation range."
        if vstatus == "WITHIN_RANGE":
            return f"Quoted {rate} is within benchmark range ({var:+.1f}% vs mid {bmid})."
        return ""

    def _build_result(self, bench_request, line_items: list, user):
        """Aggregate line items into a BenchmarkResult."""
        total_quoted = Decimal("0")
        total_bench_mid = Decimal("0")
        counts = {k: 0 for k in [VarianceStatus.WITHIN_RANGE, VarianceStatus.MODERATE, VarianceStatus.HIGH, VarianceStatus.NEEDS_REVIEW]}
        cat_data = {}

        for item in line_items:
            amt = item.line_amount or Decimal("0")
            total_quoted += amt
            if item.benchmark_mid and item.quantity:
                total_bench_mid += item.benchmark_mid * item.quantity
            elif item.benchmark_mid:
                total_bench_mid += item.benchmark_mid
            counts[item.variance_status] = counts.get(item.variance_status, 0) + 1
            cat = item.category
            if cat not in cat_data:
                cat_data[cat] = {"quoted": Decimal("0"), "benchmark": Decimal("0"), "count": 0}
            cat_data[cat]["quoted"] += amt
            if item.benchmark_mid and item.quantity:
                cat_data[cat]["benchmark"] += item.benchmark_mid * item.quantity
            cat_data[cat]["count"] += 1

        overall_deviation = None
        if total_bench_mid > 0:
            overall_deviation = float((total_quoted - total_bench_mid) / total_bench_mid * 100)

        def status(dev):
            if dev is None:
                return VarianceStatus.NEEDS_REVIEW
            a = abs(dev)
            if a < 5.0:
                return VarianceStatus.WITHIN_RANGE
            if a < 15.0:
                return VarianceStatus.MODERATE
            return VarianceStatus.HIGH

        overall_status = status(overall_deviation)

        category_summary = {}
        for cat, dat in cat_data.items():
            q = float(dat["quoted"])
            b = float(dat["benchmark"]) if dat["benchmark"] else None
            dev = None
            if b and b > 0:
                dev = (q - b) / b * 100
            category_summary[cat] = {
                "quoted": q,
                "benchmark_mid": b,
                "deviation_pct": dev,
                "count": dat["count"],
                "status": status(dev),
            }

        # Build negotiation notes
        notes = []
        if overall_deviation is not None and overall_deviation > 15:
            notes.append(f"Overall quotation is {overall_deviation:.1f}% above benchmark. Request a revised proposal.")
        elif overall_deviation is not None and 5 <= overall_deviation <= 15:
            notes.append(f"Overall quotation is {overall_deviation:.1f}% above benchmark. Target 5-8% reduction.")
        for cat, dat in category_summary.items():
            dev = dat.get("deviation_pct")
            if dev is not None and dev > 15:
                notes.append(f"{cat} items are {dev:.1f}% above benchmark. Challenge unit rates.")
        high_lines = sorted(
            [i for i in line_items if i.variance_status == "HIGH" and i.variance_pct],
            key=lambda x: abs(x.variance_pct), reverse=True,
        )
        for item in high_lines[:3]:
            notes.append(
                f"Line {item.line_number} '{item.description[:55]}': "
                f"quoted {float(item.quoted_unit_rate or 0):,.0f} vs mid "
                f"{float(item.benchmark_mid or 0):,.0f} "
                f"({item.variance_pct:+.1f}%). Negotiate."
            )
        if not notes:
            notes.append("Quotation is within acceptable benchmark range. Proceed with standard approval.")

        BenchmarkResult.objects.update_or_create(
            request=bench_request,
            defaults={
                "total_quoted": total_quoted,
                "total_benchmark_mid": total_bench_mid if total_bench_mid > 0 else None,
                "overall_deviation_pct": overall_deviation,
                "overall_status": overall_status,
                "category_summary_json": category_summary,
                "negotiation_notes_json": notes,
                "lines_within_range": counts.get(VarianceStatus.WITHIN_RANGE, 0),
                "lines_moderate": counts.get(VarianceStatus.MODERATE, 0),
                "lines_high": counts.get(VarianceStatus.HIGH, 0),
                "lines_needs_review": counts.get(VarianceStatus.NEEDS_REVIEW, 0),
            },
        )
