"""
seed_10_form_requests.py
-------------------------
Creates 10 HVAC procurement requests that simulate the form-filling
flow ("New Request" -> HVAC form) and immediately runs the rules-based
recommendation engine on each one so the results appear in the
"All Requests" tab.

Each request targets a different decision path in HVACRulesEngine so
the All Requests table shows a variety of recommended systems:

  #  Store                              Expected system       Rule
  1  LuLu Mall Riyadh                   FCU_CHILLED_WATER     RULE_M1
  2  Carrefour Dubai Festival City      VRF_SYSTEM            RULE_M2
  3  H&M Abu Dhabi Mall                 SPLIT_SYSTEM          RULE_M3
  4  Max Kids Dubai Airport             SPLIT_SYSTEM          RULE_U1
  5  Max Fashion Muscat Avenues         PACKAGED_DX_UNIT      RULE_U2
  6  Home Centre Sharjah                VRF_SYSTEM            RULE_S_EFF
  7  Westzone Supermarket Bahrain       PACKAGED_DX_UNIT      RULE_U3
  8  IKEA Jeddah                        VRF_SYSTEM            RULE_S2_LARGE
  9  Noon Fulfilment Jebel Ali          VRF_SYSTEM            RULE_S3b
 10  Nandos Food Court Riyadh           FCU_CHILLED_WATER     RULE_R1

Usage:
    python manage.py seed_10_form_requests
    python manage.py seed_10_form_requests --user admin@example.com
    python manage.py seed_10_form_requests --clear
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.procurement.models import ProcurementRequest, ProcurementRequestType
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.recommendation_service import RecommendationService
from apps.procurement.services.request_service import ProcurementRequestService

User = get_user_model()

_SEED_TAG = "[SEED-10-FORM]"  # unique tag for easy identification / cleanup


# ---------------------------------------------------------------------------
# Helper to build a full HVAC attribute list
# ---------------------------------------------------------------------------
def _attr(code, label, data_type, value_text="", value_number=None, required=False):
    return {
        "attribute_code": code,
        "attribute_label": label,
        "data_type": data_type,
        "value_text": value_text,
        "value_number": value_number,
        "is_required": required,
    }


def _num(code, label, value, required=False):
    """Shorthand for a numeric attribute."""
    return _attr(code, label, "NUMBER", value_text=str(value), value_number=value, required=required)


def _sel(code, label, value, required=False):
    """Shorthand for a SELECT / TEXT attribute."""
    return _attr(code, label, "SELECT", value_text=value, required=required)


def _txt(code, label, value, required=False):
    """Shorthand for a free-text attribute."""
    return _attr(code, label, "TEXT", value_text=value, required=required)


# ---------------------------------------------------------------------------
# The 10 test cases
# ---------------------------------------------------------------------------
REQUESTS = [

    # ------------------------------------------------------------------
    # 1 -- MALL + Chilled Water -> FCU_CHILLED_WATER  (RULE_M1)
    #   area_sqm = 30000 * 0.0929 = 2787 sqm  (>= 2000, skip RULE_U1)
    #   "chilled water" in landlord text -> cw_available = YES -> FCU
    # ------------------------------------------------------------------
    {
        "title": f"LuLu Mall Riyadh -- HVAC Upgrade {_SEED_TAG}",
        "description": (
            "Full HVAC replacement for LuLu Hypermarket tenant unit inside "
            "Riyadh Park Mall. Mall landlord supplies chilled water at 7/12 "
            "deg-C directly to the tenant stub. No outdoor condensing units "
            "are permitted on the facade or rooftop."
        ),
        "geography_country": "SAU",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "priority": "HIGH",
        "expected_system": "FCU_CHILLED_WATER",
        "expected_rule": "RULE_M1_MALL_FCU_CW",
        "attributes": [
            # Required fields (HVAC_REQUIRED_FOR_RECOMMENDATION)
            _sel("store_id", "Store ID", "SAU-RUH-LMG-001", required=True),
            _sel("brand", "Brand / Retailer", "LuLu Hypermarket", required=True),
            _sel("country", "Country", "Saudi Arabia", required=True),
            _sel("city", "City", "Riyadh", required=True),
            _sel("store_type", "Store / Facility Type", "MALL", required=True),
            _sel("store_format", "Store Format", "HYPERMARKET", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 30000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 14, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 46, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "MEDIUM", required=True),
            _sel("heat_load_category", "Heat Load Category", "HIGH", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Chilled water from mall central plant at 7/12 deg-C available "
                "at tenant boundary. No outdoor condensing units permitted on "
                "facade or roof. Tenant to install FCUs and connect to CW stub.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "HIGH", required=True),
            # Optional enrichment
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "HIGH"),
            _sel("maintenance_priority", "Maintenance Priority", "MEDIUM"),
            _sel("footfall_category", "Footfall Category", "HIGH"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "MEDIUM"),
            _sel("operating_hours", "Operating Hours", "12"),
        ],
    },

    # ------------------------------------------------------------------
    # 2 -- MALL + No CW + HIGH heat load (3 zones) -> VRF  (RULE_M2)
    #   area_sqm = 25000 * 0.0929 = 2322 sqm
    #   No "chilled water" in landlord text -> cw_available = NO
    #   zone_count = 3 (HIGH heat_load) -> VRF
    # ------------------------------------------------------------------
    {
        "title": f"Carrefour Dubai Festival City -- HVAC New Fit-Out {_SEED_TAG}",
        "description": (
            "New HVAC installation for Carrefour supermarket at Dubai "
            "Festival City Mall. Mall provides only electrical supply; "
            "no shared cooling infrastructure. High heat load from "
            "refrigeration aisles, bakery, and customer density."
        ),
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "priority": "HIGH",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_M2_MALL_NO_CW_VRF",
        "attributes": [
            _sel("store_id", "Store ID", "UAE-DXB-CARR-002", required=True),
            _sel("brand", "Brand / Retailer", "Carrefour", required=True),
            _sel("country", "Country", "UAE", required=True),
            _sel("city", "City", "Dubai", required=True),
            _sel("store_type", "Store / Facility Type", "MALL", required=True),
            _sel("store_format", "Store Format", "SUPERMARKET", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 25000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 13, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 48, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "LOW", required=True),
            _sel("heat_load_category", "Heat Load Category", "HIGH", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Mall provides electrical supply only. Tenant is responsible "
                "for all MEP services and equipment. No shared utility "
                "infrastructure provided to tenant units.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "HIGH", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "MEDIUM"),
            _sel("maintenance_priority", "Maintenance Priority", "MEDIUM"),
            _sel("footfall_category", "Footfall Category", "HIGH"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "HIGH"),
            _sel("operating_hours", "Operating Hours", "14"),
        ],
    },

    # ------------------------------------------------------------------
    # 3 -- MALL + No CW + MEDIUM heat load (2 zones) -> SPLIT  (RULE_M3)
    #   area_sqm = 25000 * 0.0929 = 2322 sqm
    #   No "chilled water", no "no outdoor" -> cw_available=NO, outdoor=NO
    #   zone_count = 2 (MEDIUM) -> SPLIT_SYSTEM
    # ------------------------------------------------------------------
    {
        "title": f"H&M Abu Dhabi Mall -- HVAC Replacement {_SEED_TAG}",
        "description": (
            "HVAC replacement for H&M fashion retail unit at Yas Mall, "
            "Abu Dhabi. Standard mall tenancy with single sales floor "
            "and stockroom. Moderate heat load from lighting and occupancy."
        ),
        "geography_country": "UAE",
        "geography_city": "Abu Dhabi",
        "currency": "AED",
        "priority": "MEDIUM",
        "expected_system": "SPLIT_SYSTEM",
        "expected_rule": "RULE_M3_MALL_NO_CW_SPLIT",
        "attributes": [
            _sel("store_id", "Store ID", "UAE-AUH-HM-003", required=True),
            _sel("brand", "Brand / Retailer", "H&M", required=True),
            _sel("country", "Country", "UAE", required=True),
            _sel("city", "City", "Abu Dhabi", required=True),
            _sel("store_type", "Store / Facility Type", "MALL", required=True),
            _sel("store_format", "Store Format", "FASHION", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 25000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 12, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 46, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "LOW", required=True),
            _sel("heat_load_category", "Heat Load Category", "MEDIUM", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Standard mall tenancy agreement. Tenant is responsible for "
                "all MEP services and equipment. No shared cooling infrastructure.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "MEDIUM", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "MEDIUM"),
            _sel("maintenance_priority", "Maintenance Priority", "LOW"),
            _sel("footfall_category", "Footfall Category", "MEDIUM"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "LOW"),
            _sel("operating_hours", "Operating Hours", "12"),
        ],
    },

    # ------------------------------------------------------------------
    # 4 -- STANDALONE + Small area (< 2000 sqm) -> SPLIT  (RULE_U1)
    #   area_sqm = 8000 * 0.0929 = 743 sqm  -> well below 2000 threshold
    # ------------------------------------------------------------------
    {
        "title": f"Max Kids Dubai Airport T3 -- HVAC Fit-Out {_SEED_TAG}",
        "description": (
            "New HVAC fit-out for small Max Kids children's retail unit "
            "at Dubai International Airport Terminal 3. Compact standalone "
            "unit with single conditioned zone."
        ),
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "priority": "MEDIUM",
        "expected_system": "SPLIT_SYSTEM",
        "expected_rule": "RULE_U1_SMALL_AREA_SPLIT_AC",
        "attributes": [
            _sel("store_id", "Store ID", "UAE-DXB-MXKD-004", required=True),
            _sel("brand", "Brand / Retailer", "Max Kids", required=True),
            _sel("country", "Country", "UAE", required=True),
            _sel("city", "City", "Dubai", required=True),
            _sel("store_type", "Store / Facility Type", "STANDALONE", required=True),
            _sel("store_format", "Store Format", "SPECIALTY_RETAIL", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 8000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 11, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 46, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "LOW", required=True),
            _sel("heat_load_category", "Heat Load Category", "MEDIUM", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Airport authority approval required for any outdoor equipment. "
                "No constraints on indoor unit placement.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "MEDIUM", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "MEDIUM"),
            _sel("maintenance_priority", "Maintenance Priority", "LOW"),
            _sel("footfall_category", "Footfall Category", "MEDIUM"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "LOW"),
            _sel("operating_hours", "Operating Hours", "18"),
        ],
    },

    # ------------------------------------------------------------------
    # 5 -- STANDALONE + Mid area + LOW budget -> PACKAGED_DX  (RULE_U2)
    #   area_sqm = 28000 * 0.0929 = 2601 sqm  (2000-5000 band)
    #   RULE_U2: 2000 <= sqm <= 5000 AND budget = LOW -> PACKAGED_DX
    # ------------------------------------------------------------------
    {
        "title": f"Max Fashion Muscat Avenues Mall -- HVAC Replacement {_SEED_TAG}",
        "description": (
            "HVAC replacement for Max Fashion mid-size store at Muscat "
            "Grand Mall. Budget constraints require a cost-effective "
            "packaged solution. Standalone unit with single-floor "
            "layout and rooftop access available."
        ),
        "geography_country": "OMN",
        "geography_city": "Muscat",
        "currency": "OMR",
        "priority": "LOW",
        "expected_system": "PACKAGED_DX_UNIT",
        "expected_rule": "RULE_U2_MID_AREA_LOW_BUDGET_PKG",
        "attributes": [
            _sel("store_id", "Store ID", "OMN-MCT-MXFAS-005", required=True),
            _sel("brand", "Brand / Retailer", "Max Fashion", required=True),
            _sel("country", "Country", "Oman", required=True),
            _sel("city", "City", "Muscat", required=True),
            _sel("store_type", "Store / Facility Type", "STANDALONE", required=True),
            _sel("store_format", "Store Format", "FASHION", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 28000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 13, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 47, required=True),
            _sel("humidity_level", "Humidity Level", "LOW", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "MEDIUM", required=True),
            _sel("heat_load_category", "Heat Load Category", "MEDIUM", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "No constraints from landlord. Rooftop access available for "
                "packaged unit installation. Power available at rooftop level.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "LOW", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "LOW"),
            _sel("maintenance_priority", "Maintenance Priority", "LOW"),
            _sel("footfall_category", "Footfall Category", "MEDIUM"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "LOW"),
            _sel("operating_hours", "Operating Hours", "12"),
        ],
    },

    # ------------------------------------------------------------------
    # 6 -- STANDALONE + Mid area + HIGH ambient + HIGH eff -> VRF
    #         (RULE_S_MEDLARGE_HIAMB_EFF_VRF)
    #   area_sqm = 28000 * 0.0929 = 2601 sqm  (2000-5000)
    #   STANDALONE + ambient=47 (>=45) + eff=HIGH + budget=HIGH (!=LOW)
    # ------------------------------------------------------------------
    {
        "title": f"Home Centre Sharjah -- HVAC Upgrade {_SEED_TAG}",
        "description": (
            "Full HVAC upgrade for Home Centre furniture and homewares store "
            "in Sharjah. High ambient temperatures alongside a strong "
            "energy-efficiency mandate from the brand. Multi-zone operation "
            "required for showroom, warehouse, and customer areas."
        ),
        "geography_country": "UAE",
        "geography_city": "Sharjah",
        "currency": "AED",
        "priority": "HIGH",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_S_MEDLARGE_HIAMB_EFF_VRF",
        "attributes": [
            _sel("store_id", "Store ID", "UAE-SHJ-HC-006", required=True),
            _sel("brand", "Brand / Retailer", "Home Centre", required=True),
            _sel("country", "Country", "UAE", required=True),
            _sel("city", "City", "Sharjah", required=True),
            _sel("store_type", "Store / Facility Type", "STANDALONE", required=True),
            _sel("store_format", "Store Format", "HOME_FURNISHING", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 28000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 15, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 47, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "HIGH", required=True),
            _sel("heat_load_category", "Heat Load Category", "HIGH", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "No restrictions on rooftop or outdoor units. Power supply "
                "adequate for VRF or packaged solution. Rooftop slab available.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "HIGH", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "HIGH"),
            _sel("maintenance_priority", "Maintenance Priority", "MEDIUM"),
            _sel("footfall_category", "Footfall Category", "MEDIUM"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "MEDIUM"),
            _sel("operating_hours", "Operating Hours", "12"),
        ],
    },

    # ------------------------------------------------------------------
    # 7 -- STANDALONE + Mid area + HIGH ambient + MEDIUM eff -> PACKAGED_DX
    #         (RULE_U3_STANDALONE_MEDLARGE_HIAMB_PACKAGED)
    #   area_sqm = 30000 * 0.0929 = 2787 sqm (2000-5000)
    #   STANDALONE + ambient=47 (>=45) + zone_count=2 (MEDIUM heat_load)
    #   eff=MEDIUM (not HIGH) -> falls through RULE_S_EFF, fires RULE_U3
    # ------------------------------------------------------------------
    {
        "title": f"Westzone Supermarket Bahrain -- HVAC New Install {_SEED_TAG}",
        "description": (
            "New HVAC installation for Westzone standalone supermarket "
            "in Manama, Bahrain. Standard efficiency requirements with "
            "medium heat load from fresh produce and refrigerated sections. "
            "Packaged rooftop solution preferred for ease of maintenance."
        ),
        "geography_country": "BHR",
        "geography_city": "Manama",
        "currency": "BHD",
        "priority": "MEDIUM",
        "expected_system": "PACKAGED_DX_UNIT",
        "expected_rule": "RULE_U3_STANDALONE_MEDLARGE_HIAMB_PACKAGED",
        "attributes": [
            _sel("store_id", "Store ID", "BHR-MNM-WZ-007", required=True),
            _sel("brand", "Brand / Retailer", "Westzone", required=True),
            _sel("country", "Country", "Bahrain", required=True),
            _sel("city", "City", "Manama", required=True),
            _sel("store_type", "Store / Facility Type", "STANDALONE", required=True),
            _sel("store_format", "Store Format", "SUPERMARKET", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 30000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 13, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 47, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "MEDIUM", required=True),
            _sel("heat_load_category", "Heat Load Category", "MEDIUM", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "No outdoor unit restrictions. Rooftop access available for "
                "packaged DX installation. Municipality permit required.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "MEDIUM", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "MEDIUM"),
            _sel("maintenance_priority", "Maintenance Priority", "MEDIUM"),
            _sel("footfall_category", "Footfall Category", "MEDIUM"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "LOW"),
            _sel("operating_hours", "Operating Hours", "16"),
        ],
    },

    # ------------------------------------------------------------------
    # 8 -- STANDALONE + LARGE area (>= 5000 sqm) + HIGH eff -> VRF
    #         (RULE_S2_LARGE_STANDALONE_HIEFF_VRF)
    #   area_sqm = 60000 * 0.0929 = 5574 sqm  (>= 5000)
    #   STANDALONE + sqm>=5000 + ambient=46 (>=45) + eff=HIGH
    # ------------------------------------------------------------------
    {
        "title": f"IKEA Jeddah -- HVAC Full Replacement {_SEED_TAG}",
        "description": (
            "Complete HVAC overhaul for IKEA large-format retail store "
            "in Jeddah. Massive footprint with showroom, marketplace, "
            "restaurant, and warehouse sections. Brand mandates highest "
            "energy efficiency rating. Rooftop and outdoor units allowed."
        ),
        "geography_country": "SAU",
        "geography_city": "Jeddah",
        "currency": "SAR",
        "priority": "HIGH",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_S2_LARGE_STANDALONE_HIEFF_VRF",
        "attributes": [
            _sel("store_id", "Store ID", "SAU-JED-IKEA-008", required=True),
            _sel("brand", "Brand / Retailer", "IKEA", required=True),
            _sel("country", "Country", "Saudi Arabia", required=True),
            _sel("city", "City", "Jeddah", required=True),
            _sel("store_type", "Store / Facility Type", "STANDALONE", required=True),
            _sel("store_format", "Store Format", "LARGE_FORMAT", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 60000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 20, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 46, required=True),
            _sel("humidity_level", "Humidity Level", "LOW", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "HIGH", required=True),
            _sel("heat_load_category", "Heat Load Category", "HIGH", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Large format standalone store. No restrictions on outdoor "
                "or rooftop units. Dedicated transformer available. Full "
                "rooftop plant access granted.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "HIGH", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "HIGH"),
            _sel("maintenance_priority", "Maintenance Priority", "HIGH"),
            _sel("footfall_category", "Footfall Category", "HIGH"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "HIGH"),
            _sel("operating_hours", "Operating Hours", "14"),
        ],
    },

    # ------------------------------------------------------------------
    # 9 -- STANDALONE + Mid area + NORMAL ambient (40 deg-C) + HIGH eff
    #         + HIGH heat load (3 zones) -> VRF  (RULE_S3b)
    #   area_sqm = 28000 * 0.0929 = 2601 sqm
    #   ambient=40 < 45 -> high-ambient mid-area rules DO NOT fire
    #   Normal ambient path: zone_count=3 (HIGH) + eff=HIGH -> RULE_S3b
    # ------------------------------------------------------------------
    {
        "title": f"Noon Fulfilment Centre Jebel Ali -- HVAC {_SEED_TAG}",
        "description": (
            "HVAC installation for Noon.com fulfilment and returns centre "
            "at Jebel Ali Free Zone. Moderate desert ambient (cooler than "
            "coastal UAE). High heat load from equipment, people, and "
            "logistics operations across three independent thermal zones. "
            "Strong efficiency targets aligned with JAFZA sustainability goals."
        ),
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "priority": "HIGH",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_S3b_SEGMENTED_ZONES_EFFICIENCY_VRF",
        "attributes": [
            _sel("store_id", "Store ID", "UAE-JXB-NOON-009", required=True),
            _sel("brand", "Brand / Retailer", "Noon.com", required=True),
            _sel("country", "Country", "UAE", required=True),
            _sel("city", "City", "Dubai -- Jebel Ali", required=True),
            _sel("store_type", "Store / Facility Type", "STANDALONE", required=True),
            _sel("store_format", "Store Format", "FULFILMENT_CENTRE", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 28000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 18, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 40, required=True),
            _sel("humidity_level", "Humidity Level", "LOW", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "MEDIUM", required=True),
            _sel("heat_load_category", "Heat Load Category", "HIGH", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Standard JAFZA industrial estate. No restrictions on "
                "outdoor unit placement. Power supply available at building "
                "perimeter. JAFZA permit required before installation.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "HIGH", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "HIGH"),
            _sel("maintenance_priority", "Maintenance Priority", "MEDIUM"),
            _sel("footfall_category", "Footfall Category", "LOW"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "MEDIUM"),
            _sel("operating_hours", "Operating Hours", "24"),
        ],
    },

    # ------------------------------------------------------------------
    # 10 -- RESTAURANT + Chilled Water -> FCU_CHILLED_WATER  (RULE_R1)
    #    area_sqm = 22000 * 0.0929 = 2044 sqm  (>= 2000, skip RULE_U1)
    #    "chilled water" in landlord text -> cw_available=YES + RESTAURANT
    # ------------------------------------------------------------------
    {
        "title": f"Nandos Food Court Riyadh -- HVAC New Fit-Out {_SEED_TAG}",
        "description": (
            "New HVAC fit-out for Nandos full-service restaurant unit inside "
            "Mall of Arabia, Riyadh. Mall landlord supplies chilled water "
            "at 7/12 deg-C to all food-court tenants. Kitchen exhaust and "
            "make-up air system required per ASHRAE 62.1."
        ),
        "geography_country": "SAU",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "priority": "MEDIUM",
        "expected_system": "FCU_CHILLED_WATER",
        "expected_rule": "RULE_R1_RESTAURANT_FCU",
        "attributes": [
            _sel("store_id", "Store ID", "SAU-RUH-NNDOS-010", required=True),
            _sel("brand", "Brand / Retailer", "Nandos", required=True),
            _sel("country", "Country", "Saudi Arabia", required=True),
            _sel("city", "City", "Riyadh", required=True),
            _sel("store_type", "Store / Facility Type", "RESTAURANT", required=True),
            _sel("store_format", "Store Format", "CASUAL_DINING", required=True),
            _num("area_sqft", "Conditioned Area (sq ft)", 22000, required=True),
            _num("ceiling_height_ft", "Ceiling Height (ft)", 12, required=True),
            _num("ambient_temp_max", "Max Ambient Temp (deg-C)", 47, required=True),
            _sel("humidity_level", "Humidity Level", "MEDIUM", required=True),
            _sel("dust_exposure", "Dust / Sandstorm Exposure", "LOW", required=True),
            _sel("heat_load_category", "Heat Load Category", "HIGH", required=True),
            _txt(
                "landlord_constraints",
                "Landlord / Authority Constraints",
                "Chilled water from mall grid at 7/12 deg-C supplied to food "
                "court tenant boundary. No outdoor equipment allowed on facade "
                "or service corridor. Grease duct penetration approval needed.",
                required=True,
            ),
            _sel("budget_level", "Budget Level", "HIGH", required=True),
            _sel("energy_efficiency_priority", "Energy Efficiency Priority", "MEDIUM"),
            _sel("maintenance_priority", "Maintenance Priority", "LOW"),
            _sel("footfall_category", "Footfall Category", "HIGH"),
            _sel("fresh_air_requirement", "Fresh Air Requirement", "HIGH"),
            _sel("operating_hours", "Operating Hours", "16"),
        ],
    },
]


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------
class Command(BaseCommand):
    help = (
        "Create 10 HVAC procurement requests (simulating form fill) "
        "and run the rules-based recommendation engine on each. "
        "Results appear in the All Requests tab."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            default="",
            help="Email of the user to own the requests (default: first superuser).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=f"Delete all requests previously created by this seed (title contains {_SEED_TAG}).",
        )

    def handle(self, *args, **options):
        # ── Resolve user ──────────────────────────────────────────────────
        email = options["user"]
        if email:
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f"User with email '{email}' not found.")
        else:
            user = User.objects.filter(is_superuser=True).order_by("pk").first()
            if not user:
                user = User.objects.order_by("pk").first()
            if not user:
                raise CommandError(
                    "No users found in the database. "
                    "Run seed_rbac first, or specify --user <email>."
                )

        self.stdout.write(f"Acting as user: {user.email}")

        # ── Optional clear ────────────────────────────────────────────────
        if options["clear"]:
            deleted, _ = ProcurementRequest.objects.filter(title__contains=_SEED_TAG).delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} previously seeded request(s)."))

        # ── Summary table header ──────────────────────────────────────────
        col_w = [4, 42, 22, 22, 6]
        self._print_divider(col_w)
        self._print_row(
            ["#", "Title", "Expected", "Got", "OK?"], col_w
        )
        self._print_divider(col_w)

        passed = 0
        failed = 0
        errors = 0

        for idx, spec in enumerate(REQUESTS, start=1):
            title = spec["title"]
            expected = spec["expected_system"]
            expected_rule = spec["expected_rule"]

            try:
                # ── 1. Create request (simulate form submit) ──────────────
                req = ProcurementRequestService.create_request(
                    title=title,
                    description=spec["description"],
                    domain_code="HVAC",
                    schema_code="HVAC_GCC_V1",
                    request_type=ProcurementRequestType.RECOMMENDATION,
                    priority=spec["priority"],
                    geography_country=spec["geography_country"],
                    geography_city=spec["geography_city"],
                    currency=spec["currency"],
                    created_by=user,
                    attributes=spec["attributes"],
                )

                # ── 2. Mark request ready ────────────────────────────────
                # Use update_status directly (skip attribute validation for
                # non-standard is_required flags in seed data)
                from apps.core.enums import ProcurementRequestStatus
                ProcurementRequestService.update_status(
                    req, ProcurementRequestStatus.READY, user=user
                )

                # ── 3. Create analysis run ───────────────────────────────
                run = AnalysisRunService.create_run(
                    request=req,
                    run_type="RECOMMENDATION",
                    triggered_by=user,
                )

                # ── 4. Run recommendation (rules only, no AI) ────────────
                result = RecommendationService.run_recommendation(
                    req,
                    run,
                    use_ai=False,
                    request_user=user,
                )

                # ── 5. Check result ──────────────────────────────────────
                got = getattr(result, "recommended_option", "") or ""
                # recommended_option includes the full description text; extract system code
                from apps.procurement.models import RecommendationResult
                latest = (
                    RecommendationResult.objects
                    .filter(request=req)
                    .order_by("-created_at")
                    .first()
                )
                got_code = ""
                if latest:
                    raw = latest.recommended_option or ""
                    # system_type_code lives in output_payload_json (full merged result dict)
                    payload = latest.output_payload_json or {}
                    got_code = payload.get("system_type_code", "") or ""
                    if not got_code:
                        # Fallback: scan known system codes in the recommended_option text
                        _CODES = [
                            "FCU_CHILLED_WATER", "VRF_SYSTEM", "PACKAGED_DX_UNIT",
                            "SPLIT_SYSTEM", "CASSETTE_SPLIT", "CHILLER_PLANT",
                        ]
                        for c in _CODES:
                            if c.replace("_", " ").lower() in raw.lower() or c.lower() in raw.lower():
                                got_code = c
                                break
                        if not got_code:
                            got_code = raw[:20]

                ok = got_code == expected
                if ok:
                    passed += 1
                    status_str = self.style.SUCCESS("PASS")
                else:
                    failed += 1
                    status_str = self.style.ERROR("FAIL")

                conf = getattr(latest, "confidence_score", 0) if latest else 0
                self._print_row(
                    [
                        str(idx),
                        title.replace(_SEED_TAG, "").strip()[:40],
                        expected[:20],
                        (got_code or "---")[:20],
                        "PASS" if ok else "FAIL",
                    ],
                    col_w,
                    style_last=(self.style.SUCCESS if ok else self.style.ERROR),
                )

                if not ok:
                    # Print detail line with confidence and rule for debugging
                    rules_fired = []
                    if latest and latest.reasoning_details_json:
                        rules_fired = latest.reasoning_details_json.get("rules_fired", [])
                    if not rules_fired and latest and latest.output_payload_json:
                        rules_fired = (
                            (latest.output_payload_json.get("reasoning_details") or {})
                            .get("rules_fired", [])
                        )
                    self.stdout.write(
                        f"    Expected rule : {expected_rule}"
                    )
                    self.stdout.write(
                        f"    Rules fired   : {', '.join(rules_fired[:6]) or 'none'}"
                    )
                    self.stdout.write(
                        f"    Confidence    : {conf:.2f}"
                    )
                    self.stdout.write(
                        f"    Raw option    : {getattr(latest, 'recommended_option', '')[:80]}"
                    )

            except Exception as exc:
                errors += 1
                self._print_row(
                    [str(idx), title[:40], expected[:20], "ERROR", "FAIL"],
                    col_w,
                    style_last=self.style.ERROR,
                )
                self.stdout.write(
                    self.style.ERROR(f"    Exception: {exc}")
                )

        # ── Footer ────────────────────────────────────────────────────────
        self._print_divider(col_w)
        total = len(REQUESTS)
        self.stdout.write("")
        self.stdout.write(
            f"Results: {self.style.SUCCESS(str(passed))} passed / "
            f"{self.style.ERROR(str(failed))} failed / "
            f"{self.style.WARNING(str(errors))} errors  (total {total})"
        )
        self.stdout.write("")
        if failed == 0 and errors == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "All 10 requests created and recommendations verified. "
                    "Open /procurement/requests/ to see them in the All Requests tab."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"{failed + errors} request(s) did not match the expected system. "
                    "Check the FAIL rows above for rule trace details."
                )
            )

    # ── Formatting helpers ────────────────────────────────────────────────
    def _print_divider(self, widths):
        self.stdout.write("+" + "+".join("-" * (w + 2) for w in widths) + "+")

    def _print_row(self, cells, widths, style_last=None):
        parts = []
        for i, (cell, w) in enumerate(zip(cells, widths)):
            padded = str(cell).ljust(w)[:w]
            if style_last and i == len(cells) - 1:
                parts.append(f" {style_last(padded)} ")
            else:
                parts.append(f" {padded} ")
        self.stdout.write("|" + "|".join(parts) + "|")
