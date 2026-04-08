"""Seed 10 HVAC procurement requests -- one per rule (R1-R10).

Each request is crafted so that the first HVACRecommendationRule to
match (by priority order) is exactly the intended rule.

Usage:
    python manage.py seed_hvac_requests            # flush existing + create 10
    python manage.py seed_hvac_requests --no-flush # keep existing, add 10 more
"""
from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

# Import rule names for reasoning_details_json
from apps.procurement.management.commands.seed_hvac_rules import RULES as _HVAC_RULES

_RULE_NAME_MAP = {r[0]: r[1] for r in _HVAC_RULES}

User = get_user_model()

# ---------------------------------------------------------------------------
# Request definitions -- each entry is a dict consumed by _make_request().
#
# Column mapping:
#   title, description, priority, currency,
#   country, city, store_type, area_sqft, ambient_temp_max,
#   budget_level, energy_efficiency_priority,
#   store_format, operating_hours, footfall_category, ceiling_height_ft,
#   humidity_level, dust_exposure, heat_load_category,
#   expected_rule, expected_system
#
# Attribute codes intentionally mirror the HTML form field names so that
# HVACRecommendationRule.matches() receives the keys it reads.
# ---------------------------------------------------------------------------
REQUESTS = [
    # ---- R1: Mall -- any configuration -> CHILLER ----------------------
    {
        "title": "Dubai Mall Expansion -- HVAC Replacement FY2026",
        "description": (
            "Full HVAC replacement for a new ground-floor mall tenancy. "
            "Landlord provides chilled-water plant. Sizing for 12,000 sq ft."
        ),
        "priority": "HIGH",
        "currency": "AED",
        "country": "UAE",
        "city": "Dubai",
        "store_type": "MALL",
        "area_sqft": 12000,
        "ambient_temp_max": 46,
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
        "store_format": "RETAIL",
        "operating_hours": "10 AM - 10 PM",
        "footfall_category": "HIGH",
        "ceiling_height_ft": 14,
        "humidity_level": "MEDIUM",
        "dust_exposure": "LOW",
        "heat_load_category": "HIGH",
        "expected_rule": "R1",
        "expected_system": "CHILLER",
        "system_label": "Chilled Water System",
    },
    # ---- R2: Small footprint < 2000 sq ft -> SPLIT_AC ------------------
    {
        "title": "Abu Dhabi Booth -- Small Kiosk HVAC FY2026",
        "description": (
            "Small standalone kiosk installation under 2000 sq ft. "
            "Simple split AC sufficient; no complex infrastructure required."
        ),
        "priority": "LOW",
        "currency": "AED",
        "country": "UAE",
        "city": "Abu Dhabi",
        "store_type": "STANDALONE",
        "area_sqft": 1500,
        "ambient_temp_max": 44,
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "MEDIUM",
        "store_format": "RETAIL",
        "operating_hours": "9 AM - 10 PM",
        "footfall_category": "LOW",
        "ceiling_height_ft": 9,
        "humidity_level": "MEDIUM",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "LOW",
        "expected_rule": "R2",
        "expected_system": "SPLIT_AC",
        "system_label": "Split AC",
    },
    # ---- R3: GCC standalone large, extreme heat, high energy -> VRF ----
    {
        "title": "Qatar Doha Standalone Flagship -- VRF Energy Efficiency",
        "description": (
            "Large standalone flagship store in Qatar extreme-heat zone. "
            "High energy-efficiency mandate demands VRF inverter technology "
            "to minimise operating costs under sustained 46 C ambient."
        ),
        "priority": "HIGH",
        "currency": "QAR",
        "country": "QATAR",
        "city": "Doha",
        "store_type": "STANDALONE",
        "area_sqft": 6500,
        "ambient_temp_max": 46,
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
        "store_format": "RETAIL",
        "operating_hours": "9 AM - 11 PM",
        "footfall_category": "HIGH",
        "ceiling_height_ft": 13,
        "humidity_level": "HIGH",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "HIGH",
        "expected_rule": "R3",
        "expected_system": "VRF",
        "system_label": "VRF System",
    },
    # ---- R4: GCC standalone large, extreme heat, low budget -> PACKAGED_DX
    {
        "title": "KSA Jeddah Standalone -- Budget HVAC for Large Store",
        "description": (
            "Large standalone store in Jeddah with tight CapEx budget. "
            "Packaged DX is the cost-effective solution for extreme heat "
            "where high-efficiency VRF investment cannot be justified."
        ),
        "priority": "MEDIUM",
        "currency": "SAR",
        "country": "KSA",
        "city": "Jeddah",
        "store_type": "STANDALONE",
        "area_sqft": 7000,
        "ambient_temp_max": 47,
        "budget_level": "LOW",
        "energy_efficiency_priority": "MEDIUM",
        "store_format": "FURNITURE",
        "operating_hours": "10 AM - 11 PM",
        "footfall_category": "MEDIUM",
        "ceiling_height_ft": 14,
        "humidity_level": "HIGH",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "HIGH",
        "expected_rule": "R4",
        "expected_system": "PACKAGED_DX",
        "system_label": "Packaged DX Unit",
    },
    # ---- R5: Mid-size, hot climate, low budget -> PACKAGED_DX ----------
    {
        "title": "Oman Muscat Warehouse -- Budget Mid-Size HVAC",
        "description": (
            "Mid-size retail warehouse in Muscat operating under 42 C peak. "
            "Low CapEx budget limits system choice to packaged DX units "
            "with wide service availability and low installation cost."
        ),
        "priority": "MEDIUM",
        "currency": "OMR",
        "country": "OMAN",
        "city": "Muscat",
        "store_type": "WAREHOUSE",
        "area_sqft": 3500,
        "ambient_temp_max": 42,
        "budget_level": "LOW",
        "energy_efficiency_priority": "MEDIUM",
        "store_format": "RETAIL",
        "operating_hours": "8 AM - 6 PM",
        "footfall_category": "MEDIUM",
        "ceiling_height_ft": 18,
        "humidity_level": "LOW",
        "dust_exposure": "HIGH",
        "heat_load_category": "MEDIUM",
        "expected_rule": "R5",
        "expected_system": "PACKAGED_DX",
        "system_label": "Packaged DX Unit",
    },
    # ---- R6: Mid-size, hot, med/high budget, high energy -> VRF --------
    {
        "title": "Kuwait City Office -- VRF Efficiency Upgrade",
        "description": (
            "Mid-size office building in Kuwait City. Medium-high investment "
            "budget with high energy-efficiency target. VRF part-load savings "
            "deliver strong lifecycle ROI under 43 C peak ambient."
        ),
        "priority": "MEDIUM",
        "currency": "KWD",
        "country": "KUWAIT",
        "city": "Kuwait City",
        "store_type": "OFFICE",
        "area_sqft": 4000,
        "ambient_temp_max": 43,
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "HIGH",
        "store_format": "OTHER",
        "operating_hours": "8 AM - 6 PM",
        "footfall_category": "MEDIUM",
        "ceiling_height_ft": 11,
        "humidity_level": "MEDIUM",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "MEDIUM",
        "expected_rule": "R6",
        "expected_system": "VRF",
        "system_label": "VRF System",
    },
    # ---- R7: Dubai UAE, large non-standalone, extreme heat, high energy -> VRF
    {
        "title": "Dubai Warehouse Hub -- High-Efficiency VRF Fitout",
        "description": (
            "Large logistics warehouse in Dubai. Not mall or standalone. "
            "Area exceeds 6000 sq ft with peak ambient of 47 C. High energy "
            "efficiency commitment drives VRF inverter selection."
        ),
        "priority": "HIGH",
        "currency": "AED",
        "country": "UAE",
        "city": "Dubai",
        "store_type": "WAREHOUSE",
        "area_sqft": 6000,
        "ambient_temp_max": 47,
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
        "store_format": "OTHER",
        "operating_hours": "6 AM - 10 PM",
        "footfall_category": "LOW",
        "ceiling_height_ft": 20,
        "humidity_level": "MEDIUM",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "HIGH",
        "expected_rule": "R7",
        "expected_system": "VRF",
        "system_label": "VRF System",
    },
    # ---- R8: KSA Riyadh, large, extreme heat -> PACKAGED_DX ------------
    {
        "title": "Riyadh Distribution Centre -- Heavy-Duty HVAC",
        "description": (
            "Large distribution centre in Riyadh operating under 47 C peak. "
            "Packaged DX is the primary recommendation for reliability in "
            "extreme heat; VRF is the high-efficiency alternative."
        ),
        "priority": "HIGH",
        "currency": "SAR",
        "country": "KSA",
        "city": "Riyadh",
        "store_type": "WAREHOUSE",
        "area_sqft": 5500,
        "ambient_temp_max": 47,
        "budget_level": "HIGH",
        "energy_efficiency_priority": "LOW",
        "store_format": "OTHER",
        "operating_hours": "6 AM - 10 PM",
        "footfall_category": "LOW",
        "ceiling_height_ft": 22,
        "humidity_level": "LOW",
        "dust_exposure": "HIGH",
        "heat_load_category": "HIGH",
        "expected_rule": "R8",
        "expected_system": "PACKAGED_DX",
        "system_label": "Packaged DX Unit",
    },
    # ---- R9: Extreme ambient >= 50 C -> PACKAGED_DX --------------------
    {
        "title": "Al Ain Industrial Zone -- Extreme Climate HVAC",
        "description": (
            "Industrial facility in Al Ain exposed to sustained peak temps "
            "of 52 C. Only heavy-duty packaged units rated for extreme climates "
            "are suitable; standard split and VRF equipment may derate above 50 C."
        ),
        "priority": "CRITICAL",
        "currency": "AED",
        "country": "UAE",
        "city": "Al Ain",
        "store_type": "WAREHOUSE",
        "area_sqft": 3000,
        "ambient_temp_max": 52,
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "MEDIUM",
        "store_format": "OTHER",
        "operating_hours": "24 Hours",
        "footfall_category": "LOW",
        "ceiling_height_ft": 16,
        "humidity_level": "LOW",
        "dust_exposure": "HIGH",
        "heat_load_category": "HIGH",
        "expected_rule": "R9",
        "expected_system": "PACKAGED_DX",
        "system_label": "Packaged DX Unit",
    },
    # ---- R10: Fallback -- no specific rule matched -> PACKAGED_DX ------
    {
        "title": "Bahrain Manama Office -- General HVAC Procurement",
        "description": (
            "Standard office refurbishment in Bahrain. Moderate temperature, "
            "medium budget and efficiency requirements. No specific rule applies; "
            "packaged DX is the conservative default recommendation."
        ),
        "priority": "LOW",
        "currency": "BHD",
        "country": "BAHRAIN",
        "city": "Manama",
        "store_type": "OFFICE",
        "area_sqft": 2500,
        "ambient_temp_max": 38,
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "MEDIUM",
        "store_format": "OTHER",
        "operating_hours": "8 AM - 6 PM",
        "footfall_category": "MEDIUM",
        "ceiling_height_ft": 10,
        "humidity_level": "MEDIUM",
        "dust_exposure": "LOW",
        "heat_load_category": "LOW",
        "expected_rule": "R10",
        "expected_system": "PACKAGED_DX",
        "system_label": "Packaged DX Unit",
    },
]

# ---------------------------------------------------------------------------
# Human-readable system labels
# ---------------------------------------------------------------------------
SYSTEM_LABELS = {
    "CHILLER":     "Chilled Water System (FCU Distribution)",
    "SPLIT_AC":    "Split AC (Wall-Mounted / Multi-Split)",
    "VRF":         "VRF System (Variable Refrigerant Flow)",
    "PACKAGED_DX": "Packaged DX Unit",
    "FCU":         "Fan Coil Unit",
}

# ---------------------------------------------------------------------------
# Sample product suggestions per system type (used for external suggestions)
# ---------------------------------------------------------------------------
SUGGESTION_TEMPLATES = {
    "CHILLER": [
        {
            "product_name": "Carrier AquaForce 30XW Series -- Water-Cooled Chiller",
            "manufacturer": "Carrier",
            "model": "30XW-300",
            "capacity_kw": 1050.0,
            "cop_rating": 6.1,
            "refrigerant": "R-134a",
            "price_range_aed": "AED 280,000 - 350,000",
            "notes": "Suitable for large mall chilled water loops. AHRI certified.",
            "url": "https://www.carrier.com/commercial/en/ae/",
        },
        {
            "product_name": "Trane CenTraVac CVHE Series",
            "manufacturer": "Trane",
            "model": "CVHE-600",
            "capacity_kw": 2100.0,
            "cop_rating": 6.8,
            "refrigerant": "R-123",
            "price_range_aed": "AED 420,000 - 550,000",
            "notes": "High COP centrifugal chiller for large mall applications.",
            "url": "https://www.trane.com/commercial/middle-east/",
        },
        {
            "product_name": "Daikin EWAP Series Air-Cooled Chiller",
            "manufacturer": "Daikin",
            "model": "EWAP-400",
            "capacity_kw": 400.0,
            "cop_rating": 3.2,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 180,000 - 240,000",
            "notes": "Air-cooled option for smaller mall tenancies without dedicated plant room.",
            "url": "https://www.daikin.com/",
        },
    ],
    "SPLIT_AC": [
        {
            "product_name": "Daikin FTXS Series Inverter Wall Split",
            "manufacturer": "Daikin",
            "model": "FTXS50K",
            "capacity_kw": 5.0,
            "cop_rating": 4.1,
            "refrigerant": "R-32",
            "price_range_aed": "AED 2,800 - 3,400",
            "notes": "Inverter-driven with R-32 refrigerant. Suitable for small retail spaces.",
            "url": "https://www.daikin.com/",
        },
        {
            "product_name": "Mitsubishi Electric MSZ-LN Wall Split",
            "manufacturer": "Mitsubishi Electric",
            "model": "MSZ-LN50VG",
            "capacity_kw": 5.0,
            "cop_rating": 4.5,
            "refrigerant": "R-32",
            "price_range_aed": "AED 3,200 - 3,900",
            "notes": "Premium inverter split with Hyper Heat technology.",
            "url": "https://www.mitsubishi-electric.com/",
        },
        {
            "product_name": "LG ARTCOOL Mirror Inverter",
            "manufacturer": "LG",
            "model": "AC18SQ",
            "capacity_kw": 5.3,
            "cop_rating": 3.9,
            "refrigerant": "R-32",
            "price_range_aed": "AED 2,500 - 3,100",
            "notes": "Stylish cassette-compatible split with low-noise operation.",
            "url": "https://www.lg.com/ae/",
        },
    ],
    "VRF": [
        {
            "product_name": "Daikin VRV IV Heat Pump System",
            "manufacturer": "Daikin",
            "model": "RYYQ20T",
            "capacity_kw": 56.0,
            "cop_rating": 4.3,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 95,000 - 140,000",
            "notes": "Market-leading VRF with wide operating range up to 52 C. ESMA 5-star.",
            "url": "https://www.daikin.com/",
        },
        {
            "product_name": "Mitsubishi Electric CITY MULTI R2 Series",
            "manufacturer": "Mitsubishi Electric",
            "model": "PUMY-P200YKM",
            "capacity_kw": 22.4,
            "cop_rating": 4.1,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 48,000 - 65,000",
            "notes": "Simultaneous H/C, suitable for mixed-zone applications.",
            "url": "https://www.mitsubishi-electric.com/",
        },
        {
            "product_name": "LG Multi V 5 VRF",
            "manufacturer": "LG",
            "model": "ARUN140LAS5",
            "capacity_kw": 40.0,
            "cop_rating": 4.0,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 72,000 - 95,000",
            "notes": "GCC-optimised high-ambient model. 18 SEER rated.",
            "url": "https://www.lg.com/ae/",
        },
    ],
    "PACKAGED_DX": [
        {
            "product_name": "Carrier 50XC Packaged Rooftop Unit",
            "manufacturer": "Carrier",
            "model": "50XC-180",
            "capacity_kw": 52.7,
            "cop_rating": 3.5,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 42,000 - 58,000",
            "notes": "GCC-spec high-ambient model rated to 55 C. Low first cost.",
            "url": "https://www.carrier.com/commercial/en/ae/",
        },
        {
            "product_name": "Trane Precedent 20T Packaged Unit",
            "manufacturer": "Trane",
            "model": "YHC240",
            "capacity_kw": 70.5,
            "cop_rating": 3.2,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 55,000 - 72,000",
            "notes": "Factory-sealed inverter compressor, wide service network in GCC.",
            "url": "https://www.trane.com/commercial/middle-east/",
        },
        {
            "product_name": "Lennox KCA Series Packaged Rooftop",
            "manufacturer": "Lennox",
            "model": "KCA300S4",
            "capacity_kw": 87.9,
            "cop_rating": 3.0,
            "refrigerant": "R-410A",
            "price_range_aed": "AED 61,000 - 80,000",
            "notes": "Heavy-duty construction for dusty desert environments.",
            "url": "https://www.lennoxinternational.com/",
        },
    ],
}

# Market context per rule
MARKET_CONTEXT = {
    "R1": (
        "Mall HVAC in the GCC is predominantly chilled-water based. Landlords typically "
        "charge tenants via a BTU metering arrangement, transferring operational risk. "
        "FCUs from Carrier, Trane, and Daikin dominate the tenancy fitout market."
    ),
    "R2": (
        "Small retail kiosks and booths under 2,000 sq ft are well served by consumer "
        "or light-commercial split ACs. Daikin, Mitsubishi Electric, and LG hold leading "
        "market share across the UAE. Inverter R-32 models now dominate new installations."
    ),
    "R3": (
        "Large GCC standalone stores facing extreme heat with a high energy mandate favour "
        "VRF. Daikin VRV IV and Mitsubishi CITY MULTI are the premium benchmark systems. "
        "Lifecycle cost studies show VRF payback within 5-7 years versus packaged DX."
    ),
    "R4": (
        "Budget-constrained large GCC standalone stores default to packaged DX due to "
        "lower CapEx versus VRF. Carrier, Trane, and York maintain strong service networks "
        "in KSA and UAE, reducing long-term maintenance risk."
    ),
    "R5": (
        "Mid-size installations in hot climates with limited budgets favour packaged DX for "
        "its accessible pricing and wide availability of spare parts across the GCC. "
        "Companies like Carrier and York have deep distribution in Oman and the Northern Emirates."
    ),
    "R6": (
        "Mid-size sites with investment appetite and a high energy focus gain strong ROI "
        "from VRF. Part-load efficiency at 40-50 percent capacity is 30-40 percent better "
        "than packaged DX in temperate swing seasons common in Kuwait and Bahrain."
    ),
    "R7": (
        "Dubai is the most competitive HVAC market in the GCC. High-ambient-rated VRF "
        "systems from Daikin and Mitsubishi dominate large non-mall sites. DEWA Green "
        "Building regulations favour inverter-based systems with ESMA 5-star ratings."
    ),
    "R8": (
        "Riyadh's extreme summer temperatures and dust exposure suit heavy-duty packaged "
        "DX units with sealed compressor housings. Carrier and Trane maintain dedicated "
        "KSA service centres for rapid on-site support."
    ),
    "R9": (
        "Facilities in Al Ain, parts of KSA, and UAE industrial zones regularly see peak "
        "ambient temperatures above 50 C. Standard VRF and split equipment may refuse to "
        "start or derate significantly above this threshold. Certified high-ambient "
        "packaged DX units (Carrier 55 C spec, York GCC Heavy-Duty series) are mandatory."
    ),
    "R10": (
        "For standard GCC office configurations with moderate ambient conditions, packaged "
        "DX remains the most widely selected HVAC solution. It offers proven reliability, "
        "broad supplier support, and flexibility for future capacity changes."
    ),
}


class Command(BaseCommand):
    help = (
        "Seed 10 HVAC procurement requests (one per rule R1-R10) with "
        "recommendation results and market intelligence suggestions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-flush",
            action="store_true",
            default=False,
            help="Do NOT delete existing ProcurementRequest records before seeding.",
        )

    def handle(self, *args, **options):
        from django.db import transaction

        from apps.procurement.models import (
            AnalysisRun,
            MarketIntelligenceSuggestion,
            ProcurementRequest,
            ProcurementRequestAttribute,
            RecommendationResult,
        )
        from apps.core.enums import (
            AnalysisRunStatus,
            AnalysisRunType,
            ProcurementRequestStatus,
            ProcurementRequestType,
        )

        if not options["no_flush"]:
            deleted, _ = ProcurementRequest.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(f"Flushed {deleted} existing ProcurementRequest records (all related data cascaded).")
            )

        # Resolve a superuser to assign as creator/trigger
        actor = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if actor is None:
            self.stderr.write(
                self.style.ERROR("No User found in DB. Run migrations and create a superuser first.")
            )
            return

        created_count = 0

        with transaction.atomic():
            for idx, spec in enumerate(REQUESTS, start=1):
                system_code = spec["expected_system"]
                system_label = SYSTEM_LABELS.get(system_code, system_code)

                # ── 1. ProcurementRequest ──────────────────────────────────
                req = ProcurementRequest.objects.create(
                    title=spec["title"],
                    description=spec["description"],
                    domain_code="HVAC",
                    schema_code="HVAC_PRODUCT_SELECTION_V1",
                    request_type=ProcurementRequestType.RECOMMENDATION,
                    status=ProcurementRequestStatus.COMPLETED,
                    priority=spec["priority"],
                    geography_country=spec["country"],
                    geography_city=spec["city"],
                    currency=spec["currency"],
                    assigned_to=actor,
                )

                # ── 2. Attributes (key-value pairs read by matches()) ──────
                attrs = [
                    ("store_id",                   f"STORE-SEED-{idx:02d}",         "Store ID",                        "TEXT"),
                    ("country",                    spec["country"],                  "Country",                         "SELECT"),
                    ("city",                       spec["city"],                     "City",                            "TEXT"),
                    ("store_type",                 spec["store_type"],               "Store Type",                      "SELECT"),
                    ("area_sqft",                  None,                             "Area (sq ft)",                    "NUMBER"),
                    ("ambient_temp_max",           None,                             "Ambient Temp Max (C)",            "NUMBER"),
                    ("budget_level",               spec["budget_level"],             "Budget Level",                    "SELECT"),
                    ("energy_efficiency_priority", spec["energy_efficiency_priority"], "Energy Efficiency Priority",    "SELECT"),
                    ("store_format",               spec["store_format"],             "Store Format",                    "SELECT"),
                    ("operating_hours",            spec["operating_hours"],          "Operating Hours",                 "TEXT"),
                    ("footfall_category",          spec["footfall_category"],        "Footfall Category",               "SELECT"),
                    ("ceiling_height_ft",          None,                             "Ceiling Height (ft)",             "NUMBER"),
                    ("humidity_level",             spec["humidity_level"],           "Humidity Level",                  "SELECT"),
                    ("dust_exposure",              spec["dust_exposure"],            "Dust Exposure",                   "SELECT"),
                    ("heat_load_category",         spec["heat_load_category"],       "Heat Load Category",              "SELECT"),
                ]
                for code, text_val, label, dtype in attrs:
                    num_val = None
                    if code == "area_sqft":
                        text_val = str(spec["area_sqft"])
                        num_val = spec["area_sqft"]
                    elif code == "ambient_temp_max":
                        text_val = str(spec["ambient_temp_max"])
                        num_val = spec["ambient_temp_max"]
                    elif code == "ceiling_height_ft":
                        text_val = str(spec["ceiling_height_ft"])
                        num_val = spec["ceiling_height_ft"]

                    ProcurementRequestAttribute.objects.create(
                        request=req,
                        attribute_code=code,
                        attribute_label=label,
                        data_type=dtype,
                        value_text=text_val or "",
                        value_number=num_val,
                    )

                # ── 3. AnalysisRun (COMPLETED) ─────────────────────────────
                now = timezone.now()
                run = AnalysisRun.objects.create(
                    request=req,
                    run_type=AnalysisRunType.RECOMMENDATION,
                    status=AnalysisRunStatus.COMPLETED,
                    started_at=now - datetime.timedelta(seconds=8),
                    completed_at=now - datetime.timedelta(seconds=2),
                    triggered_by=actor,
                    confidence_score=0.92,
                    output_summary=(
                        f"Rule {spec['expected_rule']} matched: {spec['description'][:120]}. "
                        f"Recommended system: {system_label}."
                    ),
                    thought_process_log=[
                        {
                            "step": 1,
                            "stage": "parameter_extraction",
                            "decision": "Extracted site parameters from request attributes.",
                            "reasoning": (
                                f"Country={spec['country']}, City={spec['city']}, "
                                f"StoreType={spec['store_type']}, Area={spec['area_sqft']} sqft, "
                                f"Temp={spec['ambient_temp_max']} C, "
                                f"Budget={spec['budget_level']}, Energy={spec['energy_efficiency_priority']}."
                            ),
                        },
                        {
                            "step": 2,
                            "stage": "rule_evaluation",
                            "decision": f"Rule {spec['expected_rule']} is the first matching rule.",
                            "reasoning": (
                                f"Evaluated all active HVAC rules in priority order. "
                                f"Rule {spec['expected_rule']} conditions satisfied: "
                                f"recommended system is {system_label}."
                            ),
                        },
                        {
                            "step": 3,
                            "stage": "finalisation",
                            "decision": "Recommendation confirmed with high confidence.",
                            "reasoning": "No competing rules or override conditions detected.",
                        },
                    ],
                )

                # ── 4. RecommendationResult ────────────────────────────────
                RecommendationResult.objects.create(
                    run=run,
                    recommended_option=system_label,
                    reasoning_summary=(
                        f"Rule {spec['expected_rule']} applies: {spec['description'][:200]}"
                    ),
                    reasoning_details_json={
                        "rule_code": spec["expected_rule"],
                        "rule_name": _RULE_NAME_MAP.get(spec["expected_rule"], spec["expected_rule"]),
                        "system_type": {
                            "code": system_code,
                            "name": system_label,
                        },
                        "matched_conditions": {
                            "country": spec["country"],
                            "city": spec["city"],
                            "store_type": spec["store_type"],
                            "area_sqft": spec["area_sqft"],
                            "ambient_temp_max": spec["ambient_temp_max"],
                            "budget_level": spec["budget_level"],
                            "energy_efficiency_priority": spec["energy_efficiency_priority"],
                        },
                    },
                    confidence_score=0.92,
                    compliance_status="PASS",
                    output_payload_json={
                        "system_type_code": system_code,
                        "system_type_name": system_label,
                        "rule_code": spec["expected_rule"],
                        "confidence": 0.92,
                    },
                )

                # ── 5. MarketIntelligenceSuggestion (external suggestions) --
                raw_suggestions = SUGGESTION_TEMPLATES.get(system_code, SUGGESTION_TEMPLATES["PACKAGED_DX"])
                market_ctx = MARKET_CONTEXT.get(spec["expected_rule"], "")
                rephrased = (
                    f"What are the best HVAC systems for a {spec['store_type'].lower()} "
                    f"store of {spec['area_sqft']:,} sq ft in {spec['city']}, {spec['country']} "
                    f"with {spec['ambient_temp_max']} C peak ambient? (Budget: {spec['budget_level']}, "
                    f"Energy priority: {spec['energy_efficiency_priority']})"
                )

                MarketIntelligenceSuggestion.objects.create(
                    request=req,
                    generated_by=actor,
                    rephrased_query=rephrased,
                    ai_summary=(
                        f"Based on the site parameters for {spec['city']}, {spec['country']}, "
                        f"the {system_label} is the optimal recommendation. "
                        f"{market_ctx}"
                    ),
                    market_context=market_ctx,
                    system_code=system_code,
                    system_name=system_label,
                    suggestions_json=raw_suggestions,
                    suggestion_count=len(raw_suggestions),
                )

                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  [{idx:2d}/10] {spec['expected_rule']} -> {system_code:12s}  |  {spec['title'][:55]}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created_count} requests, analysis runs, "
                f"recommendation results, and market intelligence suggestions."
            )
        )
