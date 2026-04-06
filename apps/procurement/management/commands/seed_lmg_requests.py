"""Management command: seed_lmg_requests

Wipes all procurement data and seeds exactly 15 Landmark Group requests
covering HVAC, IT, FACILITIES, SECURITY, CIVIL, RETAIL_OPS and FIRE_SAFETY
domains with supplier quotations, benchmark results, and recommendations.

Usage:
    python manage.py seed_lmg_requests
"""
from __future__ import annotations

import decimal
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    AttributeDataType,
    BenchmarkRiskLevel,
    ComplianceStatus,
    ExtractionSourceType,
    ProcurementRequestStatus,
    ProcurementRequestType,
    VarianceStatus,
)
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    BenchmarkResultLine,
    ComplianceResult,
    ProcurementRequest,
    ProcurementRequestAttribute,
    QuotationLineItem,
    RecommendationResult,
    SupplierQuotation,
)

User = get_user_model()

D = decimal.Decimal

# ---------------------------------------------------------------------------
# 15 seed templates
# ---------------------------------------------------------------------------

SEEDS = [
    # 1 -- HVAC central plant, Dubai (COMPLETED, BOTH)
    {
        "title": "HVAC Central Plant Upgrade -- Dubai Mall Superstore",
        "description": "Full replacement of central-plant HVAC for 14,500 sqm retail superstore. Carrier chillers 18 years old. Requires chilled-water plant, DEWA 5-star, BMS integration.",
        "domain_code": "HVAC",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attrs": [
            ("area_sqm", "Floor Area (sqm)", AttributeDataType.NUMBER, "", D("14500")),
            ("cooling_load_tr", "Cooling Load (TR)", AttributeDataType.NUMBER, "", D("420")),
            ("system_type", "System Type", AttributeDataType.TEXT, "Central Chilled Water", None),
            ("bms_required", "BMS Integration", AttributeDataType.TEXT, "Yes", None),
        ],
        "quotations": [
            {
                "vendor_name": "Voltas Gulf LLC",
                "quotation_number": "VGL-2025-0041",
                "total_amount": D("1850000"),
                "lines": [
                    ("Carrier 23XRV Centrifugal Chiller 250TR", "EA", D("2"), D("620000"), D("1240000"), "Carrier", "23XRV"),
                    ("Cooling Tower -- BAC VXT-1000", "EA", D("2"), D("180000"), D("360000"), "BAC", "VXT-1000"),
                    ("BMS Integration & Commissioning", "LOT", D("1"), D("250000"), D("250000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("8.5"),
            },
            {
                "vendor_name": "Daikin Middle East",
                "quotation_number": "DME-2025-3317",
                "total_amount": D("1720000"),
                "lines": [
                    ("Daikin EWAD~CZ Chiller 250TR", "EA", D("2"), D("575000"), D("1150000"), "Daikin", "EWAD-CZ"),
                    ("Cooling Tower -- Evapco LSWA-800", "EA", D("2"), D("165000"), D("330000"), "Evapco", "LSWA-800"),
                    ("BMS Integration & Commissioning", "LOT", D("1"), D("240000"), D("240000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("1.2"),
            },
        ],
        "recommendation": "Daikin Middle East (DME-2025-3317) is recommended. Quoted AED 1.72M vs market benchmark AED 1.70M (1.2% above). Daikin EWAD-CZ chillers offer 18% better energy efficiency than Carrier 23XRV for this load profile. Strong GCC after-sales network.",
        "recommendation_confidence": 0.88,
    },

    # 2 -- IT Data Centre UPS, Abu Dhabi (COMPLETED, BENCHMARK)
    {
        "title": "Data Centre UPS Replacement -- Abu Dhabi HQ",
        "description": "Replace legacy APC 200kVA UPS units in Tier-3 data centre with N+1 redundant configuration. Runtime 30 min at full load.",
        "domain_code": "IT",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "CRITICAL",
        "geography_country": "UAE",
        "geography_city": "Abu Dhabi",
        "currency": "AED",
        "attrs": [
            ("capacity_kva", "Capacity (kVA)", AttributeDataType.NUMBER, "", D("200")),
            ("redundancy", "Redundancy Model", AttributeDataType.TEXT, "N+1", None),
            ("runtime_min", "Runtime at Full Load (min)", AttributeDataType.NUMBER, "", D("30")),
            ("tier_level", "Data Centre Tier", AttributeDataType.TEXT, "Tier 3", None),
        ],
        "quotations": [
            {
                "vendor_name": "Schneider Electric UAE",
                "quotation_number": "SE-2025-0188",
                "total_amount": D("480000"),
                "lines": [
                    ("APC Galaxy VS 200kVA", "EA", D("2"), D("185000"), D("370000"), "APC", "Galaxy VS"),
                    ("Installation & Commissioning", "LOT", D("1"), D("65000"), D("65000"), "", ""),
                    ("3-Year Maintenance Contract", "LOT", D("1"), D("45000"), D("45000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("-2.1"),
            },
            {
                "vendor_name": "ABB Power Solutions",
                "quotation_number": "ABB-UPS-25041",
                "total_amount": D("510000"),
                "lines": [
                    ("ABB PowerValue 200kVA", "EA", D("2"), D("195000"), D("390000"), "ABB", "PowerValue"),
                    ("Installation & Commissioning", "LOT", D("1"), D("72000"), D("72000"), "", ""),
                    ("3-Year Maintenance Contract", "LOT", D("1"), D("48000"), D("48000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("4.1"),
            },
        ],
        "recommendation": "Schneider Electric (SE-2025-0188) is 6.3% cheaper and within benchmark range. APC Galaxy VS has proven GCC data-centre reliability. Recommend award AED 480,000.",
        "recommendation_confidence": 0.91,
    },

    # 3 -- Facilities -- Office fit-out, Riyadh (READY)
    {
        "title": "Office Fit-Out -- New Regional HQ, Riyadh Tower B",
        "description": "Full fit-out of 3,200 sqm Grade-A office space. Includes raised flooring, suspended ceiling, MEP works, furniture and AV.",
        "domain_code": "FACILITIES",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.READY,
        "priority": "HIGH",
        "geography_country": "KSA",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "attrs": [
            ("area_sqm", "Floor Area (sqm)", AttributeDataType.NUMBER, "", D("3200")),
            ("floors", "Floors", AttributeDataType.NUMBER, "", D("2")),
            ("fit_out_grade", "Fit-Out Grade", AttributeDataType.TEXT, "Category A+", None),
            ("target_completion", "Target Completion", AttributeDataType.TEXT, "Q3 2025", None),
        ],
        "quotations": [
            {
                "vendor_name": "Al Bawani Construction Co",
                "quotation_number": "ABC-FO-2025-019",
                "total_amount": D("2800000"),
                "lines": [
                    ("Raised Access Flooring (Knauf)", "SQM", D("3200"), D("380"), D("1216000"), "Knauf", "RF-600"),
                    ("Suspended Ceiling System", "SQM", D("3200"), D("210"), D("672000"), "Armstrong", "Ultima"),
                    ("MEP Works (Electrical + Plumbing)", "LOT", D("1"), D("912000"), D("912000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("3.7"),
            },
        ],
        "recommendation": "Al Bawani is the sole qualified bidder at SAR 2.8M, 3.7% above market benchmark. Recommend approval subject to SASO compliance certification.",
        "recommendation_confidence": 0.76,
    },

    # 4 -- HVAC cold chain, Kuwait (COMPLETED)
    {
        "title": "Cold Chain Refrigeration -- 12 Hypermarket Branches",
        "description": "Supply and install commercial refrigeration units (reach-in + walk-in cold rooms) across 12 hypermarket locations in Kuwait City.",
        "domain_code": "HVAC",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "HIGH",
        "geography_country": "KWT",
        "geography_city": "Kuwait City",
        "currency": "KWD",
        "attrs": [
            ("branch_count", "Branch Count", AttributeDataType.NUMBER, "", D("12")),
            ("cold_room_sqm_per_branch", "Cold Room Area/Branch (sqm)", AttributeDataType.NUMBER, "", D("35")),
            ("temp_range", "Temperature Range (C)", AttributeDataType.TEXT, "-18 to +4", None),
            ("refrigerant", "Refrigerant Type", AttributeDataType.TEXT, "R-448A (low GWP)", None),
        ],
        "quotations": [
            {
                "vendor_name": "Hussain Al-Essa & Partners",
                "quotation_number": "HAE-REF-25081",
                "total_amount": D("580000"),
                "lines": [
                    ("Walk-in Freezer Unit (-18C, 35sqm)", "EA", D("12"), D("32000"), D("384000"), "Bitzer", "CSW-6553"),
                    ("Reach-in Refrigeration Cabinet", "EA", D("24"), D("5500"), D("132000"), "True", "T-49"),
                    ("Installation & Commissioning", "LOT", D("1"), D("64000"), D("64000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("0.8"),
            },
        ],
        "recommendation": "Hussain Al-Essa awarded. KWD 580K is within 1% of market benchmark. R-448A refrigerant complies with Kuwait EPA mandate. Proceed.",
        "recommendation_confidence": 0.93,
    },

    # 5 -- IT laptop refresh (REVIEW_REQUIRED)
    {
        "title": "Laptop Fleet Refresh -- 500 Units Finance & Ops Teams",
        "description": "Replacement of 500 end-of-life laptops (Dell Latitude 5490) with current-generation business laptops. Requires TPM 2.0, Win 11 Pro, 3-year on-site warranty.",
        "domain_code": "IT",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.REVIEW_REQUIRED,
        "priority": "MEDIUM",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attrs": [
            ("quantity", "Unit Count", AttributeDataType.NUMBER, "", D("500")),
            ("screen_size", "Screen Size (inch)", AttributeDataType.NUMBER, "", D("14")),
            ("ram_gb", "RAM (GB)", AttributeDataType.NUMBER, "", D("16")),
            ("ssd_gb", "SSD (GB)", AttributeDataType.NUMBER, "", D("512")),
            ("warranty_years", "Warranty (years)", AttributeDataType.NUMBER, "", D("3")),
        ],
        "quotations": [
            {
                "vendor_name": "Eros Group ME",
                "quotation_number": "EGM-IT-25204",
                "total_amount": D("1375000"),
                "lines": [
                    ("Dell Latitude 5440 i5-1345U 16GB 512SSD", "EA", D("500"), D("2650"), D("1325000"), "Dell", "Latitude 5440"),
                    ("3-Year ProSupport On-Site Warranty", "EA", D("500"), D("100"), D("50000"), "Dell", ""),
                ],
                "risk_level": BenchmarkRiskLevel.HIGH,
                "variance_pct": D("12.3"),
            },
            {
                "vendor_name": "Logicom Distribution ME",
                "quotation_number": "LDM-2025-876",
                "total_amount": D("1210000"),
                "lines": [
                    ("HP EliteBook 640 G11 i5-1335U 16GB 512SSD", "EA", D("500"), D("2320"), D("1160000"), "HP", "EliteBook 640 G11"),
                    ("3-Year On-site Warranty Pack", "EA", D("500"), D("100"), D("50000"), "HP", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("-1.1"),
            },
        ],
        "recommendation": "Logicom HP quote at AED 1.21M is 1.1% below benchmark. Eros Dell quote is 12.3% above benchmark -- review required before award. Recommend Logicom subject to security risk assessment.",
        "recommendation_confidence": 0.79,
    },

    # 6 -- Security CCTV, Qatar (COMPLETED)
    {
        "title": "CCTV Upgrade -- 8 Fashion Stores, Doha Festival City",
        "description": "Replace analog CCTV with 4K IP cameras + NVR across 8 stores. Integrate with Landmark central security platform.",
        "domain_code": "SECURITY",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "MEDIUM",
        "geography_country": "QAT",
        "geography_city": "Doha",
        "currency": "QAR",
        "attrs": [
            ("store_count", "Store Count", AttributeDataType.NUMBER, "", D("8")),
            ("camera_count_total", "Total Cameras", AttributeDataType.NUMBER, "", D("96")),
            ("resolution", "Camera Resolution", AttributeDataType.TEXT, "4K (8MP)", None),
            ("storage_days", "NVR Retention (days)", AttributeDataType.NUMBER, "", D("90")),
        ],
        "quotations": [
            {
                "vendor_name": "Gulf Systems Integration",
                "quotation_number": "GSI-SEC-2025-044",
                "total_amount": D("385000"),
                "lines": [
                    ("Hikvision DS-2CD2T87G2 4K Cam", "EA", D("96"), D("1800"), D("172800"), "Hikvision", "DS-2CD2T87G2"),
                    ("Hikvision 32-Ch 4K NVR (8TB)", "EA", D("8"), D("12000"), D("96000"), "Hikvision", "DS-9632NI-I16"),
                    ("Installation, cabling & commissioning", "LOT", D("1"), D("116200"), D("116200"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("2.4"),
            },
        ],
        "recommendation": "GSI awarded QAR 385K. Hikvision is the approved brand on Landmark vendor list. 2.4% above benchmark is within acceptable tolerance. Recommend approval.",
        "recommendation_confidence": 0.89,
    },

    # 7 -- Civil works, Bahrain (PROCESSING)
    {
        "title": "Car Park Waterproofing -- Landmark Tower, Manama",
        "description": "Waterproofing membrane application for basement car park (2,800 sqm). Cementitious + bituminous hybrid system with 10-year performance guarantee.",
        "domain_code": "CIVIL",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.PROCESSING,
        "priority": "MEDIUM",
        "geography_country": "BHR",
        "geography_city": "Manama",
        "currency": "BHD",
        "attrs": [
            ("area_sqm", "Area (sqm)", AttributeDataType.NUMBER, "", D("2800")),
            ("system_type", "System Type", AttributeDataType.TEXT, "Cementitious + Bituminous", None),
            ("guarantee_years", "Performance Guarantee (yrs)", AttributeDataType.NUMBER, "", D("10")),
        ],
        "quotations": [
            {
                "vendor_name": "Al Moayyed Contracting",
                "quotation_number": "AMC-WP-25031",
                "total_amount": D("64000"),
                "lines": [
                    ("Cementitious Slurry Waterproofing (Mapei)", "SQM", D("2800"), D("12"), D("33600"), "Mapei", "Mapelastic"),
                    ("Bituminous Membrane (Sika)", "SQM", D("2800"), D("8"), D("22400"), "Sika", "SikaProof"),
                    ("Surface Prep & Application Labour", "LOT", D("1"), D("8000"), D("8000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("6.8"),
            },
        ],
        "recommendation": "Analysis in progress. Preliminary benchmark review shows quote is 6.8% above market rate. Awaiting 2nd quotation before recommendation.",
        "recommendation_confidence": 0.55,
    },

    # 8 -- HVAC VRF, Oman (COMPLETED)
    {
        "title": "VRF Air Conditioning -- Home Centre Muscat Grand Mall",
        "description": "VRF system for 4,200 sqm home furnishing store. 48 indoor units, R-32 refrigerant, 5-year parts warranty.",
        "domain_code": "HVAC",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "HIGH",
        "geography_country": "OMN",
        "geography_city": "Muscat",
        "currency": "OMR",
        "attrs": [
            ("area_sqm", "Floor Area (sqm)", AttributeDataType.NUMBER, "", D("4200")),
            ("indoor_units", "Indoor Units", AttributeDataType.NUMBER, "", D("48")),
            ("refrigerant", "Refrigerant", AttributeDataType.TEXT, "R-32", None),
            ("warranty_parts", "Parts Warranty (yrs)", AttributeDataType.NUMBER, "", D("5")),
        ],
        "quotations": [
            {
                "vendor_name": "Khimji Ramdas LLC",
                "quotation_number": "KR-HVAC-25091",
                "total_amount": D("148000"),
                "lines": [
                    ("Mitsubishi Electric VRF Outdoor 48HP", "EA", D("3"), D("22000"), D("66000"), "Mitsubishi", "PURY-P224YJM-A"),
                    ("Cassette Indoor Unit 1.5TR", "EA", D("48"), D("1450"), D("69600"), "Mitsubishi", "PLFY-P35VEM"),
                    ("Refrigerant Piping & Commissioning", "LOT", D("1"), D("12400"), D("12400"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("-1.8"),
            },
            {
                "vendor_name": "Universal Trading Corp",
                "quotation_number": "UTC-VRF-25044",
                "total_amount": D("163000"),
                "lines": [
                    ("Daikin VRV IV-S Outdoor 48HP", "EA", D("3"), D("24500"), D("73500"), "Daikin", "RXYQ48T"),
                    ("Cassette Indoor Unit 1.5TR", "EA", D("48"), D("1620"), D("77760"), "Daikin", "FCAG35B"),
                    ("Refrigerant Piping & Commissioning", "LOT", D("1"), D("11740"), D("11740"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("8.7"),
            },
        ],
        "recommendation": "Khimji Ramdas awarded OMR 148K (1.8% below benchmark). Mitsubishi R-32 VRF achieves COP 4.7 vs Daikin COP 4.2. Recommend Mitsubishi + Khimji Ramdas.",
        "recommendation_confidence": 0.92,
    },

    # 9 -- Retail Ops -- POS terminals (COMPLETED)
    {
        "title": "POS Terminal Rollout -- 200 Units GCC Retail Stores",
        "description": "Procurement of 200 all-in-one POS terminals with integrated payment, barcode scanner, and customer display. Android 12+ with EMV certification.",
        "domain_code": "RETAIL_OPS",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attrs": [
            ("quantity", "Unit Count", AttributeDataType.NUMBER, "", D("200")),
            ("os_platform", "OS Platform", AttributeDataType.TEXT, "Android 12+", None),
            ("payment_standards", "Payment Standards", AttributeDataType.TEXT, "EMV, NFC, QR", None),
            ("display_size", "Display Size (inch)", AttributeDataType.NUMBER, "", D("15.6")),
        ],
        "quotations": [
            {
                "vendor_name": "Ingenico Middle East",
                "quotation_number": "IME-POS-2025-170",
                "total_amount": D("680000"),
                "lines": [
                    ("Ingenico Lane/7000 AIO POS 15.6\"", "EA", D("200"), D("2800"), D("560000"), "Ingenico", "Lane 7000"),
                    ("3-Year Warranty & Support", "EA", D("200"), D("600"), D("120000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("1.9"),
            },
            {
                "vendor_name": "PAX Technology UAE",
                "quotation_number": "PAX-UAE-25088",
                "total_amount": D("620000"),
                "lines": [
                    ("PAX A920 Pro AIO Android POS", "EA", D("200"), D("2400"), D("480000"), "PAX", "A920 Pro"),
                    ("3-Year Warranty & Support", "EA", D("200"), D("700"), D("140000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("-7.6"),
            },
        ],
        "recommendation": "PAX Technology AED 620K is 7.6% below benchmark and EMV-certified. Recommend PAX A920 Pro for 150 units. Reserve Ingenico Lane 7000 for 50 flagship stores only.",
        "recommendation_confidence": 0.85,
    },

    # 10 -- Fire Safety, Kuwait (DRAFT)
    {
        "title": "Fire Suppression System -- Landmark Logistics Hub, Shuwaikh",
        "description": "Design, supply and install FM-200 clean agent fire suppression for 1,800 sqm IT server room + warehouse. NFPA 2001 compliant.",
        "domain_code": "FIRE_SAFETY",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.DRAFT,
        "priority": "CRITICAL",
        "geography_country": "KWT",
        "geography_city": "Kuwait City",
        "currency": "KWD",
        "attrs": [
            ("protected_area_sqm", "Protected Area (sqm)", AttributeDataType.NUMBER, "", D("1800")),
            ("agent_type", "Suppression Agent", AttributeDataType.TEXT, "FM-200 (HFC-227ea)", None),
            ("nfpa_standard", "NFPA Standard", AttributeDataType.TEXT, "NFPA 2001", None),
            ("cylinder_count", "Estimated Cylinders", AttributeDataType.NUMBER, "", D("8")),
        ],
        "quotations": [
            {
                "vendor_name": "Kidde Fire Systems Kuwait",
                "quotation_number": "KFS-2025-0234",
                "total_amount": D("48000"),
                "lines": [
                    ("FM-200 Cylinder 80kg + Valve Assembly", "EA", D("8"), D("2800"), D("22400"), "Kidde", "FM200-80K"),
                    ("Pipe Network, Nozzles & Brackets", "LOT", D("1"), D("14000"), D("14000"), "", ""),
                    ("Control Panel, Detectors & Commissioning", "LOT", D("1"), D("11600"), D("11600"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.HIGH,
                "variance_pct": D("14.2"),
            },
        ],
        "recommendation": "Only one bid received. Quote is 14.2% above benchmark. Request additional quotes before award. CRITICAL priority -- escalate to FM team.",
        "recommendation_confidence": 0.45,
    },

    # 11 -- IT network switch (COMPLETED)
    {
        "title": "Network Core Switch Refresh -- Landmark IT Infrastructure",
        "description": "Replace 12 end-of-life Cisco Catalyst 2960X core switches with next-gen 48-port PoE+ switches. Stacking capable, 40G uplinks.",
        "domain_code": "IT",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attrs": [
            ("switch_count", "Switch Count", AttributeDataType.NUMBER, "", D("12")),
            ("ports_per_switch", "Ports per Switch (PoE+)", AttributeDataType.NUMBER, "", D("48")),
            ("uplink_speed", "Uplink Speed", AttributeDataType.TEXT, "40G QSFP+", None),
            ("management", "Management", AttributeDataType.TEXT, "Full SNMP + NetFlow", None),
        ],
        "quotations": [
            {
                "vendor_name": "Cisco Systems UAE",
                "quotation_number": "CIS-UAE-GCC-2025-912",
                "total_amount": D("1080000"),
                "lines": [
                    ("Cisco Catalyst C9300-48P-E (PoE+)", "EA", D("12"), D("78000"), D("936000"), "Cisco", "C9300-48P-E"),
                    ("DNA Advantage 3-Year License per switch", "EA", D("12"), D("12000"), D("144000"), "Cisco", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("2.8"),
            },
            {
                "vendor_name": "Juniper Networks ME",
                "quotation_number": "JNP-ME-25-0451",
                "total_amount": D("960000"),
                "lines": [
                    ("Juniper EX4300-48P PoE+ Switch", "EA", D("12"), D("72000"), D("864000"), "Juniper", "EX4300-48P"),
                    ("Juniper Care Core 3-Year per switch", "EA", D("12"), D("8000"), D("96000"), "Juniper", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("-9.4"),
            },
        ],
        "recommendation": "Cisco Catalyst C9300 at AED 1.08M is within range and preferred for compatibility with existing Cisco WAN. Juniper is cheaper but requires retraining. Recommend Cisco.",
        "recommendation_confidence": 0.83,
    },

    # 12 -- HVAC air handling unit, KSA (REVIEW_REQUIRED)
    {
        "title": "AHU Replacement -- Centrepoint Riyadh Park Mall",
        "description": "Replace 6 aging air handling units (Trane) with energy-efficient EC motor AHUs. Mall HVAC integration required. SASO compliant.",
        "domain_code": "HVAC",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.REVIEW_REQUIRED,
        "priority": "HIGH",
        "geography_country": "KSA",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "attrs": [
            ("ahu_count", "AHU Count", AttributeDataType.NUMBER, "", D("6")),
            ("airflow_ahu_cfm", "Airflow per AHU (CFM)", AttributeDataType.NUMBER, "", D("12000")),
            ("motor_type", "Motor Type", AttributeDataType.TEXT, "EC (Electronically Commutated)", None),
            ("saso_required", "SASO Compliance", AttributeDataType.TEXT, "Yes", None),
        ],
        "quotations": [
            {
                "vendor_name": "York Middle East",
                "quotation_number": "YME-AHU-25051",
                "total_amount": D("680000"),
                "lines": [
                    ("York YF-EC-12000 AHU with EC Motor", "EA", D("6"), D("95000"), D("570000"), "York", "YF-EC-12000"),
                    ("Installation, ductwork & commissioning", "LOT", D("1"), D("110000"), D("110000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("9.6"),
            },
            {
                "vendor_name": "Trane International KSA",
                "quotation_number": "TRN-KSA-2025-0139",
                "total_amount": D("720000"),
                "lines": [
                    ("Trane M-Series EC AHU 12000 CFM", "EA", D("6"), D("100000"), D("600000"), "Trane", "M-Series EC"),
                    ("Installation, ductwork & commissioning", "LOT", D("1"), D("120000"), D("120000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.HIGH,
                "variance_pct": D("16.1"),
            },
        ],
        "recommendation": "York quote is 9.6% above benchmark -- within review threshold. Trane is 16.1% above and flagged HIGH risk. Recommend York subject to SASO certificate submission.",
        "recommendation_confidence": 0.71,
    },

    # 13 -- Retail Ops -- digital signage (COMPLETED)
    {
        "title": "Digital Signage Rollout -- 30 Stores Phase 2",
        "description": "Supply and install 4K digital signage displays + media players across 30 stores. CMS integration with Landmark content platform.",
        "domain_code": "RETAIL_OPS",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "MEDIUM",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attrs": [
            ("store_count", "Store Count", AttributeDataType.NUMBER, "", D("30")),
            ("screens_per_store", "Screens per Store", AttributeDataType.NUMBER, "", D("4")),
            ("screen_size", "Screen Size (inch)", AttributeDataType.NUMBER, "", D("55")),
            ("cms_platform", "CMS Platform", AttributeDataType.TEXT, "Landmark Proprietary", None),
        ],
        "quotations": [
            {
                "vendor_name": "Samsung Electronics Gulf",
                "quotation_number": "SEG-DS-2025-2201",
                "total_amount": D("1800000"),
                "lines": [
                    ("Samsung QH55B 4K Commercial Display 55\"", "EA", D("120"), D("8500"), D("1020000"), "Samsung", "QH55B"),
                    ("Samsung MagicINFO Player S6", "EA", D("120"), D("5500"), D("660000"), "Samsung", "MagicINFO S6"),
                    ("Mounting, cabling & CMS integration", "LOT", D("1"), D("120000"), D("120000"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("0.5"),
            },
        ],
        "recommendation": "Samsung at AED 1.8M is within 0.5% of benchmark. Native MagicINFO CMS has confirmed API integration with Landmark platform. Recommend award.",
        "recommendation_confidence": 0.95,
    },

    # 14 -- Civil -- roof waterproofing, Oman (COMPLETED)
    {
        "title": "Roof Waterproofing -- Max Fashion Muscat City Centre",
        "description": "Torch-applied SBS bituminous membrane waterproofing for 3,600 sqm flat roof. Include insulation boards, perimeter flashings and 5-year guarantee.",
        "domain_code": "CIVIL",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "MEDIUM",
        "geography_country": "OMN",
        "geography_city": "Muscat",
        "currency": "OMR",
        "attrs": [
            ("area_sqm", "Roof Area (sqm)", AttributeDataType.NUMBER, "", D("3600")),
            ("membrane_type", "Membrane Type", AttributeDataType.TEXT, "SBS Torch-Applied Bituminous", None),
            ("insulation_board", "Insulation Board", AttributeDataType.TEXT, "50mm XPS", None),
            ("guarantee_years", "Guarantee (yrs)", AttributeDataType.NUMBER, "", D("5")),
        ],
        "quotations": [
            {
                "vendor_name": "Galfar Engineering & Contracting",
                "quotation_number": "GEC-WT-2025-077",
                "total_amount": D("38500"),
                "lines": [
                    ("SBS Torch Membrane APP Grade 4mm (Sika)", "SQM", D("3600"), D("5.5"), D("19800"), "Sika", "Sikalastic"),
                    ("50mm XPS Insulation Board", "SQM", D("3600"), D("3.8"), D("13680"), "Ravago", "Ravatherm XPS"),
                    ("Flashings, drainage & labour", "LOT", D("1"), D("5020"), D("5020"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("3.1"),
            },
            {
                "vendor_name": "Redco International",
                "quotation_number": "RDC-RF-2025-041",
                "total_amount": D("41200"),
                "lines": [
                    ("SBS Torch Membrane Soprema 4mm", "SQM", D("3600"), D("6.1"), D("21960"), "Soprema", "Sopralene"),
                    ("50mm XPS Insulation Board", "SQM", D("3600"), D("4.1"), D("14760"), "Soprema", ""),
                    ("Flashings, drainage & labour", "LOT", D("1"), D("4480"), D("4480"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("10.3"),
            },
        ],
        "recommendation": "Galfar at OMR 38,500 is 3.1% above benchmark and within threshold. Redco is 10.3% above and HIGH risk. Recommend Galfar. Verify 5-year guarantee terms.",
        "recommendation_confidence": 0.87,
    },

    # 15 -- Facilities -- pest control (COMPLETED)
    {
        "title": "Annual Pest Control Contract -- 25 Retail Outlets, Bahrain",
        "description": "Annual integrated pest management (IPM) contract for 25 retail stores and 3 warehouses in Bahrain. Monthly service visits, rodent bait stations, and emergency callout.",
        "domain_code": "FACILITIES",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "LOW",
        "geography_country": "BHR",
        "geography_city": "Manama",
        "currency": "BHD",
        "attrs": [
            ("site_count", "Site Count", AttributeDataType.NUMBER, "", D("28")),
            ("visit_frequency", "Visit Frequency", AttributeDataType.TEXT, "Monthly", None),
            ("emergency_callout", "Emergency Callout SLA", AttributeDataType.TEXT, "4 hours", None),
            ("certification", "Certification Required", AttributeDataType.TEXT, "MOMRA approved", None),
        ],
        "quotations": [
            {
                "vendor_name": "Rentokil Initial Bahrain",
                "quotation_number": "RI-BH-2025-1140",
                "total_amount": D("14400"),
                "lines": [
                    ("Monthly IPM Service Visit (28 sites x 12)", "VISIT", D("336"), D("38"), D("12768"), "Rentokil", ""),
                    ("Emergency Callout (est. 6 per year)", "CALL", D("6"), D("272"), D("1632"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.LOW,
                "variance_pct": D("-4.2"),
            },
            {
                "vendor_name": "Gulf Pest Control WLL",
                "quotation_number": "GPC-2025-0881",
                "total_amount": D("16800"),
                "lines": [
                    ("Monthly IPM Service Visit (28 sites x 12)", "VISIT", D("336"), D("45"), D("15120"), "", ""),
                    ("Emergency Callout (est. 6 per year)", "CALL", D("6"), D("280"), D("1680"), "", ""),
                ],
                "risk_level": BenchmarkRiskLevel.MEDIUM,
                "variance_pct": D("11.9"),
            },
        ],
        "recommendation": "Rentokil Initial at BHD 14,400 is 4.2% below benchmark. MOMRA certified, GCC-wide contract terms. Recommend annual award with quarterly performance review.",
        "recommendation_confidence": 0.94,
    },
]


class Command(BaseCommand):
    help = "Wipe and re-seed 15 LMG procurement requests with benchmark + recommendation data."

    def handle(self, *args, **options):
        # ------------------------------------------------------------------
        # 1. Hard-delete everything in dependency order
        # ------------------------------------------------------------------
        self.stdout.write("Clearing existing procurement data...")
        BenchmarkResultLine.objects.all().delete()
        BenchmarkResult.objects.all().delete()
        RecommendationResult.objects.all().delete()
        ComplianceResult.objects.all().delete()
        AnalysisRun.objects.all().delete()
        QuotationLineItem.objects.all().delete()
        SupplierQuotation.objects.all().delete()
        ProcurementRequestAttribute.objects.all().delete()
        ProcurementRequest.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("  Cleared."))

        # ------------------------------------------------------------------
        # 2. Resolve user
        # ------------------------------------------------------------------
        user = (
            User.objects.filter(is_superuser=True).first()
            or User.objects.filter(is_staff=True).first()
            or User.objects.first()
        )
        if not user:
            self.stderr.write("No users found -- create a superuser first.")
            return

        self.stdout.write(f"  Seeding as user: {user.email}")

        now = timezone.now()
        created = 0

        for idx, tmpl in enumerate(SEEDS, start=1):
            try:
                self._create(tmpl, user, now, idx)
                created += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  [{idx:02d}/15] {tmpl['title'][:70]}")
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  [{idx:02d}/15] FAILED -- {exc}")
                )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Done. Created {created}/15 requests."))

    # ------------------------------------------------------------------

    def _create(self, tmpl, user, now, idx):
        days_ago = (16 - idx) * 4  # spread over ~60 days

        req = ProcurementRequest.objects.create(
            title=tmpl["title"],
            description=tmpl["description"],
            domain_code=tmpl["domain_code"],
            schema_code=tmpl.get("schema_code", tmpl["domain_code"]),
            request_type=tmpl["request_type"],
            status=tmpl["status"],
            priority=tmpl["priority"],
            geography_country=tmpl["geography_country"],
            geography_city=tmpl["geography_city"],
            currency=tmpl["currency"],
            created_by=user,
        )

        # Manually set created_at offset so dates look varied in the list
        ProcurementRequest.objects.filter(pk=req.pk).update(
            created_at=now - timedelta(days=days_ago)
        )

        # Attributes
        for code, label, dtype, text_val, num_val in tmpl.get("attrs", []):
            ProcurementRequestAttribute.objects.create(
                request=req,
                attribute_code=code,
                attribute_label=label,
                data_type=dtype,
                value_text=text_val or "",
                value_number=num_val,
            )

        # Only create analysis runs for non-DRAFT requests
        if tmpl["status"] == ProcurementRequestStatus.DRAFT:
            return

        # Create one AnalysisRun (BOTH covers RECOMMENDATION + BENCHMARK)
        run_status = (
            AnalysisRunStatus.COMPLETED
            if tmpl["status"] == ProcurementRequestStatus.COMPLETED
            else AnalysisRunStatus.RUNNING
            if tmpl["status"] == ProcurementRequestStatus.PROCESSING
            else AnalysisRunStatus.QUEUED
        )

        run = AnalysisRun.objects.create(
            request=req,
            run_type=(
                AnalysisRunType.BENCHMARK
                if tmpl["request_type"] == ProcurementRequestType.BENCHMARK
                else AnalysisRunType.RECOMMENDATION
            ),
            status=run_status,
            started_at=now - timedelta(days=days_ago - 1),
            completed_at=(
                now - timedelta(days=days_ago - 1, hours=-2)
                if run_status == AnalysisRunStatus.COMPLETED
                else None
            ),
            triggered_by=user,
            confidence_score=tmpl.get("recommendation_confidence"),
            output_summary=tmpl.get("recommendation", ""),
            trace_id=uuid.uuid4().hex,
        )

        # RecommendationResult
        RecommendationResult.objects.create(
            run=run,
            recommended_option=tmpl.get("recommendation", "")[:500],
            reasoning_summary=tmpl.get("recommendation", ""),
            confidence_score=tmpl.get("recommendation_confidence"),
            compliance_status=(
                ComplianceStatus.PASS
                if run_status == AnalysisRunStatus.COMPLETED
                else ComplianceStatus.NOT_CHECKED
            ),
        )

        # Quotations + BenchmarkResults
        for q_tmpl in tmpl.get("quotations", []):
            q_date = (now - timedelta(days=days_ago + 5)).date()
            quotation = SupplierQuotation.objects.create(
                request=req,
                vendor_name=q_tmpl["vendor_name"],
                quotation_number=q_tmpl["quotation_number"],
                quotation_date=q_date,
                total_amount=q_tmpl["total_amount"],
                currency=tmpl["currency"],
                extraction_status="COMPLETED",
                extraction_confidence=0.92,
            )

            # Quotation line items (line_number must be unique per quotation)
            for line_idx, (desc, unit, qty, rate, total, brand, model) in enumerate(
                q_tmpl.get("lines", []), start=1
            ):
                QuotationLineItem.objects.create(
                    quotation=quotation,
                    line_number=line_idx,
                    description=desc,
                    unit=unit,
                    quantity=qty,
                    unit_rate=rate,
                    total_amount=total,
                    brand=brand,
                    model=model,
                    extraction_source=ExtractionSourceType.MANUAL,
                    extraction_confidence=0.9,
                )

            # BenchmarkResult per quotation
            benchmark = BenchmarkResult.objects.create(
                run=run,
                quotation=quotation,
                total_quoted_amount=q_tmpl["total_amount"],
                total_benchmark_amount=q_tmpl["total_amount"] / (
                    1 + q_tmpl["variance_pct"] / 100
                ),
                variance_pct=q_tmpl["variance_pct"],
                risk_level=q_tmpl["risk_level"],
                summary_json={
                    "vendor": q_tmpl["vendor_name"],
                    "quotation_number": q_tmpl["quotation_number"],
                    "risk_level": q_tmpl["risk_level"],
                    "variance_pct": str(q_tmpl["variance_pct"]),
                },
            )

            # BenchmarkResultLines (one per line item)
            for line_idx, (desc, unit, qty, rate, total, brand, model) in enumerate(
                q_tmpl.get("lines", []), start=1
            ):
                q_line = quotation.line_items.filter(line_number=line_idx).first()
                if not q_line:
                    continue
                vp = q_tmpl["variance_pct"]
                if vp >= D("10"):
                    vstatus = VarianceStatus.SIGNIFICANTLY_ABOVE
                elif vp >= D("3"):
                    vstatus = VarianceStatus.ABOVE_BENCHMARK
                elif vp <= D("-3"):
                    vstatus = VarianceStatus.BELOW_BENCHMARK
                else:
                    vstatus = VarianceStatus.WITHIN_RANGE

                BenchmarkResultLine.objects.create(
                    benchmark_result=benchmark,
                    quotation_line=q_line,
                    benchmark_min=rate * D("0.85"),
                    benchmark_avg=rate / (1 + vp / 100),
                    benchmark_max=rate * D("1.15"),
                    quoted_value=rate,
                    variance_pct=vp,
                    variance_status=vstatus,
                    remarks=f"Market benchmark based on GCC supplier database Q1-2025.",
                )
