"""Management command: seed_completed_both

Creates 4 HVAC procurement requests with request_type=BOTH (Recommendation + Benchmarking),
status=COMPLETED, with full analysis runs, recommendation results, supplier quotations,
and benchmark results -- all seeded in COMPLETED state so the dashboard shows them
as finished requests.

Usage:
    python manage.py seed_completed_both
    python manage.py seed_completed_both --clear
    python manage.py seed_completed_both --user admin@example.com
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    AttributeDataType,
    BenchmarkRiskLevel,
    ComplianceStatus,
    ExtractionSourceType,
    ExtractionStatus,
    ProcurementRequestStatus,
    ProcurementRequestType,
    VarianceStatus,
)
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    BenchmarkResultLine,
    ProcurementRequest,
    ProcurementRequestAttribute,
    QuotationLineItem,
    RecommendationResult,
    SupplierQuotation,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# SEED DATA
# Four COMPLETED "BOTH" (recommendation + benchmark) HVAC procurement cases.
# ---------------------------------------------------------------------------

CASES = [

    # ═══════════════════════════════════════════════════════════════════════
    # CASE 1: Dubai Mall Centrepoint -- FCU on Chilled Water
    # ═══════════════════════════════════════════════════════════════════════
    {
        "title": "Centrepoint Dubai Mall -- FCU Chilled Water Fit-Out FY2026",
        "description": (
            "New HVAC fit-out for a 12,000 sqft fashion retail store on Level 2 of Dubai Mall. "
            "Mall provides chilled water backbone (6/12 deg-C). No outdoor units permitted on facade. "
            "Full FCU design including cassette selection, CHW piping sizing, room-by-room airflow "
            "schedule, and BMS integration required. Budget AED 850,000."
        ),
        "request_type": ProcurementRequestType.BOTH,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attributes": [
            ("store_type",               "Store / Facility Type",        "SELECT", "MALL"),
            ("store_format",             "Store Format",                 "SELECT", "RETAIL"),
            ("area_sqft",                "Area (sqft)",                  "NUMBER", "12000"),
            ("area_sqm",                 "Conditioned Area (sqm)",       "NUMBER", "1115"),
            ("ceiling_height_ft",        "Ceiling Height (ft)",          "NUMBER", "11"),
            ("ambient_temp_max",         "Max Ambient Temp (deg-C)",     "NUMBER", "46"),
            ("humidity_level",           "Humidity Level",               "SELECT", "HIGH"),
            ("chilled_water_available",  "Chilled Water Available",      "SELECT", "YES"),
            ("landlord_constraints",     "Landlord Constraints",         "TEXT",
             "Chilled water from mall central plant at 6/12 deg-C. No outdoor condensing units on facade or roof per Emaar fit-out guide."),
            ("existing_hvac_type",       "Existing HVAC Type",           "TEXT",   "Chilled water interface"),
            ("budget_level",             "Budget Level",                 "SELECT", "HIGH"),
            ("budget_aed",               "Budget (AED)",                 "NUMBER", "850000"),
            ("energy_efficiency_priority", "Energy Efficiency Priority", "SELECT", "HIGH"),
            ("footfall_category",        "Footfall Category",            "SELECT", "HIGH"),
            ("product_type",             "HVAC Product Type",            "SELECT", "FCU_CW"),
            ("preferred_oems",           "Preferred OEMs",               "TEXT",   "Daikin, Carrier"),
            ("operating_hours",          "Operating Hours",              "TEXT",   "10 AM - 12 AM"),
            ("dust_exposure",            "Dust Exposure",                "SELECT", "LOW"),
            ("maintenance_priority",     "Maintenance Priority",         "SELECT", "MEDIUM"),
            ("required_standards",       "Required Standards",           "TEXT",   "ASHRAE 62.1, ASHRAE 90.1, Dubai Mall fit-out guide, DEWA efficiency standards"),
        ],
        # Recommendation output
        "recommended_option": "FCU_CHILLED_WATER",
        "recommendation_summary": (
            "Rule RULE_M1_MALL_FCU_CW fired: MALL store with confirmed chilled water backbone at 6/12 deg-C. "
            "Recommended system: Fan Coil Unit (FCU) on chilled water. "
            "No outdoor condensing units required -- integrates fully with Emaar central plant. "
            "Specified FCU model: Daikin FWS series ceiling cassette, 4-pipe configuration, R-CHW. "
            "Estimated total capacity: 36 TR across 24 FCU units. "
            "BMS integration via BACNET/IP. DEWA Grade A efficiency compliant."
        ),
        "recommendation_confidence": 0.95,
        "recommendation_details": {
            "rule_fired": "RULE_M1_MALL_FCU_CW",
            "system_type": "FCU_CHILLED_WATER",
            "method": "DETERMINISTIC",
            "units": [
                {"zone": "Sales Floor", "type": "4-way cassette FCU", "model": "Daikin FWS04ATF", "capacity_tr": 2.0, "qty": 10},
                {"zone": "Fitting Rooms", "type": "Concealed duct FCU", "model": "Daikin FWF02ATF", "capacity_tr": 1.5, "qty": 6},
                {"zone": "Stock Room", "type": "Concealed duct FCU", "model": "Daikin FWF015ATF", "capacity_tr": 1.0, "qty": 4},
                {"zone": "Back Office", "type": "2-way cassette FCU", "model": "Daikin FWS015ATF", "capacity_tr": 0.75, "qty": 4},
            ],
            "total_capacity_tr": 36,
            "total_units": 24,
            "power_supply": "3-Phase 380V / 50Hz",
            "bms_protocol": "BACNET/IP",
            "standards": ["ASHRAE 62.1-2019", "ASHRAE 90.1-2019", "Dubai Mall fit-out guide"],
        },
        # Benchmark inputs
        "vendor_name": "Al Shirawi HVAC Contracting LLC",
        "quotation_number": "ASHC-2026-0412",
        "quotation_date": "2026-01-15",
        "quotation_currency": "AED",
        "quotation_lines": [
            {
                "line_number": 1,
                "description": "Daikin FWS04ATF 4-way ceiling cassette FCU 2TR -- supply, install, commission",
                "quantity": 10,
                "unit": "EA",
                "unit_rate": Decimal("14800.00"),
                "total_amount": Decimal("148000.00"),
                "brand": "Daikin",
                "model": "FWS04ATF",
                "benchmark_min": Decimal("11500.00"),
                "benchmark_avg": Decimal("13800.00"),
                "benchmark_max": Decimal("16200.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "7% above benchmark avg -- within acceptable range for high-spec mall install.",
            },
            {
                "line_number": 2,
                "description": "Daikin FWF02ATF concealed duct FCU 1.5TR -- supply, install, commission",
                "quantity": 6,
                "unit": "EA",
                "unit_rate": Decimal("11200.00"),
                "total_amount": Decimal("67200.00"),
                "brand": "Daikin",
                "model": "FWF02ATF",
                "benchmark_min": Decimal("9000.00"),
                "benchmark_avg": Decimal("10800.00"),
                "benchmark_max": Decimal("12600.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "3.7% above benchmark avg -- acceptable.",
            },
            {
                "line_number": 3,
                "description": "Daikin FWF015ATF concealed duct FCU 1TR -- supply, install, commission",
                "quantity": 4,
                "unit": "EA",
                "unit_rate": Decimal("9400.00"),
                "total_amount": Decimal("37600.00"),
                "brand": "Daikin",
                "model": "FWF015ATF",
                "benchmark_min": Decimal("7500.00"),
                "benchmark_avg": Decimal("9000.00"),
                "benchmark_max": Decimal("10800.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.4% above benchmark avg.",
            },
            {
                "line_number": 4,
                "description": "CHW piping, insulation, GI duct, balancing valves, controls -- full install",
                "quantity": 1,
                "unit": "LOT",
                "unit_rate": Decimal("195000.00"),
                "total_amount": Decimal("195000.00"),
                "brand": "",
                "model": "",
                "benchmark_min": Decimal("155000.00"),
                "benchmark_avg": Decimal("185000.00"),
                "benchmark_max": Decimal("225000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "5.4% above benchmark avg -- within range for tier-1 mall project.",
            },
        ],
        "benchmark_risk": BenchmarkRiskLevel.LOW,
        "benchmark_summary": {
            "verdict": "COMPETITIVE",
            "total_variance_pct": 6.5,
            "notes": (
                "All line items within benchmark range. "
                "Al Shirawi quote is 6.5% above GCC market average for equivalent FCU-on-CHW mall install -- "
                "acceptable given Emaar premium and night-shift installation surcharge. "
                "Recommend acceptance with caveat: request 2% cash discount for early LOI."
            ),
        },
    },

    # ═══════════════════════════════════════════════════════════════════════
    # CASE 2: Riyadh Splash Panorama Mall -- VRF System Replacement
    # ═══════════════════════════════════════════════════════════════════════
    {
        "title": "Splash Riyadh Panorama Mall -- VRF Heat Recovery Replacement FY2026",
        "description": (
            "Full VRF replacement at Splash clothing store in Panorama Mall, Riyadh KSA. "
            "Existing R22 Mitsubishi system is beyond service life and must be replaced with R32 compliant unit. "
            "Store area 650 sqm, chilled water not available, outdoor units on designated mall roof zone. "
            "SASO certification and KSA municipality HVAC license mandatory for contractor."
        ),
        "request_type": ProcurementRequestType.BOTH,
        "priority": "HIGH",
        "geography_country": "KSA",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "attributes": [
            ("store_type",               "Store / Facility Type",        "SELECT", "MALL"),
            ("store_format",             "Store Format",                 "SELECT", "RETAIL"),
            ("area_sqft",                "Area (sqft)",                  "NUMBER", "7000"),
            ("area_sqm",                 "Conditioned Area (sqm)",       "NUMBER", "650"),
            ("ceiling_height_ft",        "Ceiling Height (ft)",          "NUMBER", "12"),
            ("ambient_temp_max",         "Max Ambient Temp (deg-C)",     "NUMBER", "52"),
            ("humidity_level",           "Humidity Level",               "SELECT", "LOW"),
            ("chilled_water_available",  "Chilled Water Available",      "SELECT", "NO"),
            ("landlord_constraints",     "Landlord Constraints",         "TEXT",
             "Outdoor units on designated rooftop zone only. No wall-mounted condensers. SASO certification required."),
            ("existing_hvac_type",       "Existing HVAC Type",           "TEXT",   "VRF R22 (Mitsubishi, 12 years old)"),
            ("budget_level",             "Budget Level",                 "SELECT", "MEDIUM"),
            ("budget_aed",               "Budget (SAR)",                 "NUMBER", "310000"),
            ("energy_efficiency_priority", "Energy Efficiency Priority", "SELECT", "HIGH"),
            ("footfall_category",        "Footfall Category",            "SELECT", "MEDIUM"),
            ("product_type",             "HVAC Product Type",            "SELECT", "VRF_VRV"),
            ("preferred_oems",           "Preferred OEMs",               "TEXT",   "Mitsubishi Electric, Daikin"),
            ("operating_hours",          "Operating Hours",              "TEXT",   "10 AM - 11 PM"),
            ("dust_exposure",            "Dust Exposure",                "SELECT", "MEDIUM"),
            ("maintenance_priority",     "Maintenance Priority",         "SELECT", "MEDIUM"),
            ("required_standards",       "Required Standards",           "TEXT",   "SASO 2870, SASO 4820, ASHRAE 90.1, SBC"),
        ],
        "recommended_option": "VRF_SYSTEM",
        "recommendation_summary": (
            "Deterministic rule RULE_S1_STANDALONE_HIGH_AMB_VRF selected. "
            "For a 650 sqm mall store with no chilled water, high ambient (52 deg-C summer peak), "
            "and high efficiency priority, a VRF Heat Recovery system is the optimal choice. "
            "Recommended: Mitsubishi Electric City Multi R2-series, R32 refrigerant, "
            "3 outdoor units (36HP total) + 18 indoor units (mix of cassette and slim duct). "
            "Full SASO compliance. Estimated total TR: 24. ECOP > 4.0 at part load."
        ),
        "recommendation_confidence": 0.91,
        "recommendation_details": {
            "rule_fired": "RULE_S1_STANDALONE_HIGH_AMB_VRF",
            "system_type": "VRF_SYSTEM",
            "method": "DETERMINISTIC",
            "outdoor_units": [
                {"model": "Mitsubishi PURY-WP250YNW-A1", "capacity_hp": 10, "qty": 2},
                {"model": "Mitsubishi PURY-WP200YNW-A1", "capacity_hp": 8,  "qty": 1},
            ],
            "indoor_units": [
                {"zone": "Sales Floor",    "type": "4-way cassette", "model": "Mitsubishi PLFY-P45VEM-E", "qty": 10},
                {"zone": "Fitting Rooms",  "type": "Slim duct",      "model": "Mitsubishi PFFY-P15VLRM-E", "qty": 5},
                {"zone": "Stock / Office", "type": "Slim duct",      "model": "Mitsubishi PFFY-P20VLRM-E", "qty": 3},
            ],
            "total_tr": 24,
            "refrigerant": "R32",
            "target_ecop": 4.1,
            "standards": ["SASO 2870", "ASHRAE 90.1-2019"],
        },
        "vendor_name": "Saudi ACM Contracting Est.",
        "quotation_number": "SACM-2026-RUH-0188",
        "quotation_date": "2026-01-22",
        "quotation_currency": "SAR",
        "quotation_lines": [
            {
                "line_number": 1,
                "description": "Mitsubishi PURY-WP250YNW-A1 10HP R32 VRF outdoor unit -- supply, install",
                "quantity": 2,
                "unit": "EA",
                "unit_rate": Decimal("48500.00"),
                "total_amount": Decimal("97000.00"),
                "brand": "Mitsubishi Electric",
                "model": "PURY-WP250YNW-A1",
                "benchmark_min": Decimal("41000.00"),
                "benchmark_avg": Decimal("46500.00"),
                "benchmark_max": Decimal("53000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.3% above benchmark avg for KSA R32 VRF ODU -- acceptable.",
            },
            {
                "line_number": 2,
                "description": "Mitsubishi PURY-WP200YNW-A1 8HP R32 VRF outdoor unit -- supply, install",
                "quantity": 1,
                "unit": "EA",
                "unit_rate": Decimal("39500.00"),
                "total_amount": Decimal("39500.00"),
                "brand": "Mitsubishi Electric",
                "model": "PURY-WP200YNW-A1",
                "benchmark_min": Decimal("33000.00"),
                "benchmark_avg": Decimal("37800.00"),
                "benchmark_max": Decimal("43500.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.5% above benchmark avg.",
            },
            {
                "line_number": 3,
                "description": "Mitsubishi PLFY-P45VEM-E 4-way cassette IDU -- supply, install, commission (x10)",
                "quantity": 10,
                "unit": "EA",
                "unit_rate": Decimal("8800.00"),
                "total_amount": Decimal("88000.00"),
                "brand": "Mitsubishi Electric",
                "model": "PLFY-P45VEM-E",
                "benchmark_min": Decimal("7200.00"),
                "benchmark_avg": Decimal("8400.00"),
                "benchmark_max": Decimal("9800.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.8% above benchmark avg.",
            },
            {
                "line_number": 4,
                "description": "VRF refrigerant piping, insulation, GI duct, controls, commissioning -- LOT",
                "quantity": 1,
                "unit": "LOT",
                "unit_rate": Decimal("58000.00"),
                "total_amount": Decimal("58000.00"),
                "brand": "",
                "model": "",
                "benchmark_min": Decimal("45000.00"),
                "benchmark_avg": Decimal("55000.00"),
                "benchmark_max": Decimal("66000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "5.5% above benchmark avg -- within range for licensed KSA contractor.",
            },
        ],
        "benchmark_risk": BenchmarkRiskLevel.LOW,
        "benchmark_summary": {
            "verdict": "COMPETITIVE",
            "total_variance_pct": 4.7,
            "notes": (
                "All four line items are within the GCC benchmark range for R32 VRF in KSA. "
                "Overall quote is 4.7% above market average -- LOW risk. "
                "Contractor has valid SASO license and Riyadh Municipality HVAC permit. "
                "Recommend approval."
            ),
        },
    },

    # ═══════════════════════════════════════════════════════════════════════
    # CASE 3: Muscat Home Centre -- Packaged DX Warehouse Cooling
    # ═══════════════════════════════════════════════════════════════════════
    {
        "title": "Home Centre Muscat Al Seeb -- Packaged DX Distribution Centre FY2026",
        "description": (
            "New HVAC installation at Home Centre logistics distribution centre in Al Seeb Industrial Zone, Muscat. "
            "42,000 sqft (3,900 sqm) temperature-controlled warehouse storing furniture. "
            "Rooftop packaged DX units required. Salt-air coastal location mandates anti-corrosion specification. "
            "RS Oman energy standards compliance required."
        ),
        "request_type": ProcurementRequestType.BOTH,
        "priority": "HIGH",
        "geography_country": "OMAN",
        "geography_city": "Muscat",
        "currency": "OMR",
        "attributes": [
            ("store_type",               "Store / Facility Type",        "SELECT", "WAREHOUSE"),
            ("store_format",             "Store Format",                 "SELECT", "OTHER"),
            ("area_sqft",                "Area (sqft)",                  "NUMBER", "42000"),
            ("area_sqm",                 "Conditioned Area (sqm)",       "NUMBER", "3900"),
            ("ceiling_height_ft",        "Ceiling Height (ft)",          "NUMBER", "25"),
            ("ambient_temp_max",         "Max Ambient Temp (deg-C)",     "NUMBER", "45"),
            ("humidity_level",           "Humidity Level",               "SELECT", "HIGH"),
            ("chilled_water_available",  "Chilled Water Available",      "SELECT", "NO"),
            ("landlord_constraints",     "Landlord Constraints",         "TEXT",
             "Rooftop access confirmed. Salt-air coastal -- anti-corrosion coating mandatory on all coils and casing."),
            ("existing_hvac_type",       "Existing HVAC Type",           "TEXT",   "None (new installation)"),
            ("budget_level",             "Budget Level",                 "SELECT", "MEDIUM"),
            ("budget_aed",               "Budget (OMR)",                 "NUMBER", "145000"),
            ("energy_efficiency_priority", "Energy Efficiency Priority", "SELECT", "MEDIUM"),
            ("footfall_category",        "Footfall Category",            "SELECT", "LOW"),
            ("product_type",             "HVAC Product Type",            "SELECT", "PACKAGED_DX"),
            ("preferred_oems",           "Preferred OEMs",               "TEXT",   "York, Carrier, Trane"),
            ("operating_hours",          "Operating Hours",              "TEXT",   "7 AM - 7 PM"),
            ("dust_exposure",            "Dust Exposure",                "SELECT", "HIGH"),
            ("maintenance_priority",     "Maintenance Priority",         "SELECT", "MEDIUM"),
            ("required_standards",       "Required Standards",           "TEXT",   "ASHRAE 90.1-2019, RS Oman Energy Standards, ISO 16813"),
        ],
        "recommended_option": "PACKAGED_DX_UNIT",
        "recommendation_summary": (
            "Rule RULE_W2_WAREHOUSE_PACKAGED fired. "
            "3,900 sqm warehouse at 130W/sqm GCC rule yields estimated load of 144 TR -- "
            "within the 50-200 TR packaged DX range. "
            "Recommended: York YAZ 60-ton packaged units x3 + York YAZ 30-ton x1. "
            "All units require Daikin Blue Fin anti-corrosion coil coating for Muscat coastal environment. "
            "Full GI ductwork, MERV-11 pre-filters, BMS zone control included. "
            "Estimated ECOP: 3.4 at KSA AHRI test conditions."
        ),
        "recommendation_confidence": 0.87,
        "recommendation_details": {
            "rule_fired": "RULE_W2_WAREHOUSE_PACKAGED",
            "system_type": "PACKAGED_DX_UNIT",
            "method": "DETERMINISTIC",
            "units": [
                {"zone": "Warehouse Zone A/B/C", "model": "York YAZ-060", "capacity_tr": 60, "qty": 3},
                {"zone": "Receiving / Dispatch",  "model": "York YAZ-030", "capacity_tr": 30, "qty": 1},
            ],
            "estimated_total_tr": 210,
            "refrigerant": "R410A",
            "anti_corrosion": "Blue Fin epoxy coil coating (mandatory for coastal)",
            "filtration": "MERV-11 washable pre-filter frames on all ODUs",
            "standards": ["ASHRAE 90.1-2019", "RS Oman Energy Standards", "ISO 16813"],
        },
        "vendor_name": "Gulf Commercial Services LLC",
        "quotation_number": "GCS-MCT-2026-0320",
        "quotation_date": "2026-02-05",
        "quotation_currency": "OMR",
        "quotation_lines": [
            {
                "line_number": 1,
                "description": "York YAZ-060 60-ton packaged DX rooftop unit -- supply, install, Blue Fin coil treatment",
                "quantity": 3,
                "unit": "EA",
                "unit_rate": Decimal("28500.00"),
                "total_amount": Decimal("85500.00"),
                "brand": "York",
                "model": "YAZ-060",
                "benchmark_min": Decimal("23000.00"),
                "benchmark_avg": Decimal("27200.00"),
                "benchmark_max": Decimal("32000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.8% above benchmark avg -- includes coastal anti-corrosion premium.",
            },
            {
                "line_number": 2,
                "description": "York YAZ-030 30-ton packaged DX rooftop unit -- supply, install",
                "quantity": 1,
                "unit": "EA",
                "unit_rate": Decimal("15800.00"),
                "total_amount": Decimal("15800.00"),
                "brand": "York",
                "model": "YAZ-030",
                "benchmark_min": Decimal("12500.00"),
                "benchmark_avg": Decimal("14800.00"),
                "benchmark_max": Decimal("17500.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "6.8% above benchmark avg -- within range.",
            },
            {
                "line_number": 3,
                "description": "GI ductwork supply and install -- 3,900 sqm warehouse, including MERV-11 filter frames",
                "quantity": 1,
                "unit": "LOT",
                "unit_rate": Decimal("28000.00"),
                "total_amount": Decimal("28000.00"),
                "brand": "",
                "model": "",
                "benchmark_min": Decimal("22000.00"),
                "benchmark_avg": Decimal("26500.00"),
                "benchmark_max": Decimal("32000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "5.7% above benchmark avg.",
            },
            {
                "line_number": 4,
                "description": "Rooftop base frame, crane hire, commissioning, BMS integration -- LOT",
                "quantity": 1,
                "unit": "LOT",
                "unit_rate": Decimal("11200.00"),
                "total_amount": Decimal("11200.00"),
                "brand": "",
                "model": "",
                "benchmark_min": Decimal("9000.00"),
                "benchmark_avg": Decimal("10500.00"),
                "benchmark_max": Decimal("13000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "6.7% above benchmark avg -- crane hire included.",
            },
        ],
        "benchmark_risk": BenchmarkRiskLevel.LOW,
        "benchmark_summary": {
            "verdict": "COMPETITIVE",
            "total_variance_pct": 5.5,
            "notes": (
                "GCS quote is 5.5% above GCC market average -- LOW risk overall. "
                "The anti-corrosion coating uplift on ODUs is justified for Muscat coastal location. "
                "Contractor has RS Oman mechanical engineering license. Recommend approval."
            ),
        },
    },

    # ═══════════════════════════════════════════════════════════════════════
    # CASE 4: Doha Home Centre -- VRF Large Standalone (AI-assisted)
    # ═══════════════════════════════════════════════════════════════════════
    {
        "title": "Home Centre Doha Hyatt Plaza -- VRF Fit-Out FY2026 (65,000 sqft Standalone)",
        "description": (
            "Flagship Home Centre standalone store in Doha fitted with a full VRF heat-recovery system. "
            "65,000 sqft (6,038 sqm) across single floor with 16 ft ceiling. "
            "Dedicated rooftop plant deck. Ambient temperature peaks at 47 deg-C in Qatari summer. "
            "Energy efficiency priority HIGH. Full GSAS compliance and Kahramaa regulations apply."
        ),
        "request_type": ProcurementRequestType.BOTH,
        "priority": "HIGH",
        "geography_country": "QATAR",
        "geography_city": "Doha",
        "currency": "QAR",
        "attributes": [
            ("store_type",               "Store / Facility Type",        "SELECT", "STANDALONE"),
            ("store_format",             "Store Format",                 "SELECT", "FURNITURE"),
            ("area_sqft",                "Area (sqft)",                  "NUMBER", "65000"),
            ("area_sqm",                 "Conditioned Area (sqm)",       "NUMBER", "6038"),
            ("ceiling_height_ft",        "Ceiling Height (ft)",          "NUMBER", "16"),
            ("ambient_temp_max",         "Max Ambient Temp (deg-C)",     "NUMBER", "47"),
            ("humidity_level",           "Humidity Level",               "SELECT", "MEDIUM"),
            ("chilled_water_available",  "Chilled Water Available",      "SELECT", "NO"),
            ("landlord_constraints",     "Landlord Constraints",         "TEXT",
             "Standalone property. Dedicated rooftop plant deck available. All equipment GSAS compliant. Kahramaa utility metering at equipment level mandatory."),
            ("existing_hvac_type",       "Existing HVAC Type",           "TEXT",   "None (new build)"),
            ("budget_level",             "Budget Level",                 "SELECT", "HIGH"),
            ("budget_aed",               "Budget (QAR)",                 "NUMBER", "1850000"),
            ("energy_efficiency_priority", "Energy Efficiency Priority", "SELECT", "HIGH"),
            ("footfall_category",        "Footfall Category",            "SELECT", "MEDIUM"),
            ("product_type",             "HVAC Product Type",            "SELECT", "VRF_VRV"),
            ("preferred_oems",           "Preferred OEMs",               "TEXT",   "Daikin VRV-IV, Mitsubishi City Multi R2"),
            ("operating_hours",          "Operating Hours",              "TEXT",   "10 AM - 10 PM"),
            ("dust_exposure",            "Dust Exposure",                "SELECT", "MEDIUM"),
            ("maintenance_priority",     "Maintenance Priority",         "SELECT", "MEDIUM"),
            ("required_standards",       "Required Standards",           "TEXT",   "ASHRAE 90.1-2019, GSAS (GORD) 2021, ASHRAE 62.1, ISO 16813, Kahramaa Regulations"),
        ],
        "recommended_option": "VRF_SYSTEM",
        "recommendation_summary": (
            "Rule RULE_S2_LARGE_STANDALONE_HIEFF_VRF fired. "
            "6,038 sqm standalone store with ambient 47 deg-C and HIGH energy efficiency priority. "
            "Recommended: Daikin VRV-IV Heat Recovery system. "
            "Outdoor units: 8 x Daikin RYYQ22T 22HP units (176HP total) on rooftop plant deck. "
            "Indoor units: 48 x concealed duct FDQ series (showroom) + 22 x cassette FXFQ (display zones). "
            "Total estimated capacity: 220 TR. "
            "GSAS Mechanical credit available for VRF heat recovery topology. "
            "Kahramaa sub-metering per outdoor bank."
        ),
        "recommendation_confidence": 0.93,
        "recommendation_details": {
            "rule_fired": "RULE_S2_LARGE_STANDALONE_HIEFF_VRF",
            "system_type": "VRF_SYSTEM",
            "method": "DETERMINISTIC",
            "outdoor_units": [
                {"model": "Daikin RYYQ22T", "capacity_hp": 22, "qty": 8},
            ],
            "indoor_units": [
                {"zone": "Main Showroom",    "type": "Concealed duct FDQ", "model": "Daikin FDQ45B8V1", "qty": 48},
                {"zone": "Display Islands",  "type": "4-way cassette",      "model": "Daikin FXFQ32A2VEB", "qty": 22},
            ],
            "total_tr": 220,
            "refrigerant": "R410A",
            "heat_recovery": True,
            "gsas_credit": "GSAS-3.4 Mechanical Energy Credit applicable",
            "standards": ["ASHRAE 90.1-2019", "GSAS (GORD) 2021", "Kahramaa Regulations"],
        },
        "vendor_name": "Gulf Contracting Company W.L.L.",
        "quotation_number": "GCC-DOH-2026-0155",
        "quotation_date": "2026-02-12",
        "quotation_currency": "QAR",
        "quotation_lines": [
            {
                "line_number": 1,
                "description": "Daikin RYYQ22T 22HP VRV-IV Heat Recovery outdoor unit -- supply, install, commission",
                "quantity": 8,
                "unit": "EA",
                "unit_rate": Decimal("98000.00"),
                "total_amount": Decimal("784000.00"),
                "brand": "Daikin",
                "model": "RYYQ22T",
                "benchmark_min": Decimal("84000.00"),
                "benchmark_avg": Decimal("93500.00"),
                "benchmark_max": Decimal("108000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.8% above benchmark avg for Qatar VRF large ODU -- acceptable.",
            },
            {
                "line_number": 2,
                "description": "Daikin FDQ45B8V1 concealed duct IDU -- supply, install (x48)",
                "quantity": 48,
                "unit": "EA",
                "unit_rate": Decimal("9200.00"),
                "total_amount": Decimal("441600.00"),
                "brand": "Daikin",
                "model": "FDQ45B8V1",
                "benchmark_min": Decimal("7800.00"),
                "benchmark_avg": Decimal("8800.00"),
                "benchmark_max": Decimal("10200.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.5% above benchmark avg.",
            },
            {
                "line_number": 3,
                "description": "Daikin FXFQ32A2VEB 4-way cassette IDU -- supply, install (x22)",
                "quantity": 22,
                "unit": "EA",
                "unit_rate": Decimal("7900.00"),
                "total_amount": Decimal("173800.00"),
                "brand": "Daikin",
                "model": "FXFQ32A2VEB",
                "benchmark_min": Decimal("6800.00"),
                "benchmark_avg": Decimal("7600.00"),
                "benchmark_max": Decimal("8900.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "3.9% above benchmark avg.",
            },
            {
                "line_number": 4,
                "description": "VRF refrigerant piping, GI duct, insulation, Kahramaa sub-meters, BMS, commissioning -- LOT",
                "quantity": 1,
                "unit": "LOT",
                "unit_rate": Decimal("320000.00"),
                "total_amount": Decimal("320000.00"),
                "brand": "",
                "model": "",
                "benchmark_min": Decimal("270000.00"),
                "benchmark_avg": Decimal("305000.00"),
                "benchmark_max": Decimal("360000.00"),
                "variance_status": VarianceStatus.WITHIN_RANGE,
                "remarks": "4.9% above benchmark avg -- Kahramaa sub-metering included.",
            },
        ],
        "benchmark_risk": BenchmarkRiskLevel.LOW,
        "benchmark_summary": {
            "verdict": "COMPETITIVE",
            "total_variance_pct": 4.6,
            "notes": (
                "GCC quote is 4.6% above GCC market average for equivalent large-format VRF in Qatar -- LOW risk. "
                "Contractor GCC WLL holds valid Qatar MEP license and GSAS commissioning authority. "
                "Recommend approval. Request GSAS compliance certificate before final payment."
            ),
        },
    },
]


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Seed 4 COMPLETED 'BOTH' HVAC procurement requests (recommendation + benchmark) for dashboard demo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing seeded requests (matched by title) before re-seeding.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Email of user to assign as creator (defaults to first superuser).",
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options.get("user"))
        self.stdout.write(f"Using user: {user.email}")

        if options["clear"]:
            for case in CASES:
                deleted, _ = ProcurementRequest.objects.filter(title=case["title"]).delete()
                if deleted:
                    self.stdout.write(self.style.WARNING(f"  Deleted: {case['title']}"))

        now = timezone.now()
        created = 0

        for idx, case in enumerate(CASES):
            if ProcurementRequest.objects.filter(title=case["title"]).exists():
                self.stdout.write(f"  SKIP (exists): {case['title']}")
                continue

            try:
                self._seed_case(case, user, now, idx)
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  CREATED: {case['title']}"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  FAILED '{case['title']}': {exc}"))
                import traceback
                traceback.print_exc()

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created {created}/{len(CASES)} completed BOTH requests. "
                f"Visit /procurement/ to see them on the dashboard."
            )
        )

    def _seed_case(self, case: dict, user, now, idx: int):
        """Create one fully-completed procurement request with all related records."""
        from django.db import transaction

        # Stagger timestamps so ordering looks natural in the dashboard
        created_at = now - timedelta(days=30 - idx * 7)
        completed_at = created_at + timedelta(days=5)
        run_started = created_at + timedelta(hours=1)
        run_ended = run_started + timedelta(minutes=45)
        bench_started = run_ended + timedelta(minutes=5)
        bench_ended = bench_started + timedelta(minutes=30)

        with transaction.atomic():
            # ── 1. ProcurementRequest ─────────────────────────────────
            req = ProcurementRequest.objects.create(
                title=case["title"],
                description=case["description"],
                domain_code="HVAC",
                schema_code="HVAC_GCC_V1",
                request_type=case["request_type"],
                status=ProcurementRequestStatus.COMPLETED,
                priority=case["priority"],
                geography_country=case["geography_country"],
                geography_city=case["geography_city"],
                currency=case["currency"],
                created_by=user,
                assigned_to=user,
                trace_id=uuid.uuid4().hex,
            )

            # ── 2. Attributes ─────────────────────────────────────────
            for attr_code, attr_label, data_type, value in case["attributes"]:
                dtype_map = {
                    "SELECT": AttributeDataType.TEXT,
                    "NUMBER": AttributeDataType.NUMBER,
                    "TEXT":   AttributeDataType.TEXT,
                }
                ProcurementRequestAttribute.objects.create(
                    request=req,
                    attribute_code=attr_code,
                    attribute_label=attr_label,
                    data_type=dtype_map.get(data_type, AttributeDataType.TEXT),
                    value_text=value if data_type in ("SELECT", "TEXT") else "",
                    value_number=Decimal(value) if data_type == "NUMBER" else None,
                    is_required=attr_code in (
                        "store_type", "area_sqm", "ambient_temp_max",
                        "chilled_water_available", "product_type",
                    ),
                    extraction_source=ExtractionSourceType.MANUAL,
                )

            # ── 3. RECOMMENDATION AnalysisRun ─────────────────────────
            recon_run = AnalysisRun.objects.create(
                request=req,
                run_type=AnalysisRunType.RECOMMENDATION,
                status=AnalysisRunStatus.COMPLETED,
                started_at=run_started,
                completed_at=run_ended,
                triggered_by=user,
                confidence_score=case["recommendation_confidence"],
                output_summary=case["recommendation_summary"],
                trace_id=uuid.uuid4().hex,
                thought_process_log=[
                    {"step": 1, "stage": "ATTRIBUTE_LOAD",    "decision": "All required attributes present.", "reasoning": "22 attributes loaded from request."},
                    {"step": 2, "stage": "RULE_EVALUATION",   "decision": "Deterministic rule fired.",        "reasoning": f"Rule {case['recommendation_details']['rule_fired']} matched -- returning result."},
                    {"step": 3, "stage": "OUTPUT_GENERATION", "decision": f"HVAC type: {case['recommended_option']}", "reasoning": case['recommendation_summary']},
                ],
                input_snapshot_json={"domain": "HVAC", "request_id": str(req.request_id)},
            )

            # ── 4. RecommendationResult ───────────────────────────────
            RecommendationResult.objects.create(
                run=recon_run,
                recommended_option=case["recommended_option"],
                reasoning_summary=case["recommendation_summary"],
                reasoning_details_json=case["recommendation_details"],
                confidence_score=case["recommendation_confidence"],
                compliance_status=ComplianceStatus.PASS,
                output_payload_json={
                    "system_type":  case["recommended_option"],
                    "method":       case["recommendation_details"]["method"],
                    "confidence":   case["recommendation_confidence"],
                },
                constraints_json={
                    "chilled_water": case["attributes"][7][3],  # approx
                    "ambient_max": next(
                        (v for c, _, _, v in case["attributes"] if c == "ambient_temp_max"), ""
                    ),
                },
            )

            # ── 5. Supplier Quotation ─────────────────────────────────
            quotation_total = sum(
                ln["total_amount"] for ln in case["quotation_lines"]
            )
            try:
                import datetime
                qdate = datetime.date.fromisoformat(case["quotation_date"])
            except Exception:
                qdate = None

            quotation = SupplierQuotation.objects.create(
                request=req,
                vendor_name=case["vendor_name"],
                quotation_number=case["quotation_number"],
                quotation_date=qdate,
                total_amount=quotation_total,
                currency=case["quotation_currency"],
                extraction_status=ExtractionStatus.COMPLETED,
                extraction_confidence=0.99,
            )

            # ── 6. QuotationLineItems ─────────────────────────────────
            line_objects = {}
            for ln_data in case["quotation_lines"]:
                line_obj = QuotationLineItem.objects.create(
                    quotation=quotation,
                    line_number=ln_data["line_number"],
                    description=ln_data["description"],
                    quantity=Decimal(str(ln_data["quantity"])),
                    unit=ln_data["unit"],
                    unit_rate=ln_data["unit_rate"],
                    total_amount=ln_data["total_amount"],
                    brand=ln_data.get("brand", ""),
                    model=ln_data.get("model", ""),
                    extraction_confidence=0.99,
                    extraction_source=ExtractionSourceType.MANUAL,
                )
                line_objects[ln_data["line_number"]] = line_obj

            # ── 7. BENCHMARK AnalysisRun ──────────────────────────────
            bench_run = AnalysisRun.objects.create(
                request=req,
                run_type=AnalysisRunType.BENCHMARK,
                status=AnalysisRunStatus.COMPLETED,
                started_at=bench_started,
                completed_at=bench_ended,
                triggered_by=user,
                confidence_score=round(
                    1.0 - abs(case["benchmark_summary"]["total_variance_pct"]) / 100, 2
                ),
                output_summary=case["benchmark_summary"]["notes"],
                trace_id=uuid.uuid4().hex,
                input_snapshot_json={"quotation_id": quotation.pk, "request_id": str(req.request_id)},
            )

            # ── 8. BenchmarkResult ────────────────────────────────────
            total_quoted = quotation_total
            total_benchmark_avg = sum(
                ln["benchmark_avg"] * Decimal(str(ln["quantity"]))
                for ln in case["quotation_lines"]
            )
            overall_variance_pct = Decimal(
                str(case["benchmark_summary"]["total_variance_pct"])
            )

            bench_result = BenchmarkResult.objects.create(
                run=bench_run,
                quotation=quotation,
                total_quoted_amount=total_quoted,
                total_benchmark_amount=total_benchmark_avg,
                variance_pct=overall_variance_pct,
                risk_level=case["benchmark_risk"],
                summary_json=case["benchmark_summary"],
            )

            # ── 9. BenchmarkResultLines ───────────────────────────────
            for ln_data in case["quotation_lines"]:
                line_obj = line_objects[ln_data["line_number"]]
                BenchmarkResultLine.objects.create(
                    benchmark_result=bench_result,
                    quotation_line=line_obj,
                    benchmark_min=ln_data["benchmark_min"],
                    benchmark_avg=ln_data["benchmark_avg"],
                    benchmark_max=ln_data["benchmark_max"],
                    quoted_value=ln_data["unit_rate"],
                    variance_pct=round(
                        (ln_data["unit_rate"] - ln_data["benchmark_avg"])
                        / ln_data["benchmark_avg"] * 100,
                        2,
                    ),
                    variance_status=ln_data["variance_status"],
                    remarks=ln_data.get("remarks", ""),
                )

            # ── 10. Update request timestamps to match completed state ─
            ProcurementRequest.objects.filter(pk=req.pk).update(
                created_at=created_at,
                updated_at=completed_at,
            )

    def _resolve_user(self, email: str | None):
        if email:
            try:
                return User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f"User '{email}' not found.")
        user = User.objects.filter(is_superuser=True).order_by("pk").first()
        if user:
            return user
        user = User.objects.filter(is_active=True).order_by("pk").first()
        if user:
            return user
        raise CommandError("No users in the database. Run: python manage.py createsuperuser")
