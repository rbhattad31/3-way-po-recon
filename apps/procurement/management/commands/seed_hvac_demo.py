"""Management command: seed_hvac_demo
Creates 3 realistic Landmark Group HVAC procurement demo requests.

Usage:
    python manage.py seed_hvac_demo
    python manage.py seed_hvac_demo --clear    # delete existing demo requests first
    python manage.py seed_hvac_demo --user admin@landmark.com
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core.enums import ProcurementRequestType
from apps.procurement.models import ProcurementRequest
from apps.procurement.services.request_service import ProcurementRequestService

User = get_user_model()

# ---------------------------------------------------------------------------
# Demo requests definitions
# ---------------------------------------------------------------------------

LANDMARK_DEMO_REQUESTS = [
    # -----------------------------------------------------------------------
    # 1 -- Max Fashion Dubai Mall (VRF replacement, large, COMPLETED with recommendation)
    # -----------------------------------------------------------------------
    {
        "title": "Max Fashion Dubai Mall -- VRF System Replacement 2026",
        "description": (
            "Full replacement of aging 12-year-old VRF cooling system at Max Fashion Dubai Mall store. "
            "Store covers 1,200 sqm across ground + mezzanine floors. Existing Mitsubishi R22 system "
            "is beyond service life and refrigerant is phased out. New system must be R32 or R410A. "
            "Mall provides 3-phase power only -- no chilled water backbone at this unit."
        ),
        "request_type": ProcurementRequestType.BOTH,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "status": "READY",
        "attributes": [
            {"code": "store_type",               "label": "Store / Facility Type",        "type": "SELECT", "value": "MALL"},
            {"code": "area_sqm",                 "label": "Conditioned Area (sqm)",        "type": "NUMBER", "value": "1200"},
            {"code": "zone_count",               "label": "Number of Zones",               "type": "NUMBER", "value": "8"},
            {"code": "cooling_load_tr",          "label": "Cooling Load (TR)",             "type": "NUMBER", "value": "44"},
            {"code": "ambient_temp_max",         "label": "Max Ambient Temp (deg-C)",      "type": "NUMBER", "value": "48"},
            {"code": "chilled_water_available",  "label": "Chilled Water Available",       "type": "SELECT", "value": "NO"},
            {"code": "outdoor_unit_restriction", "label": "Outdoor Unit Restriction",      "type": "SELECT", "value": "ROOFTOP_ONLY"},
            {"code": "existing_infrastructure",  "label": "Existing Infrastructure",       "type": "SELECT", "value": "VRF_EXISTING"},
            {"code": "budget_category",          "label": "Budget Category",               "type": "SELECT", "value": "MIDRANGE"},
            {"code": "budget_aed",               "label": "Budget (AED)",                  "type": "NUMBER", "value": "380000"},
            {"code": "efficiency_priority",      "label": "Energy Efficiency Priority",    "type": "SELECT", "value": "HIGH"},
            {"code": "noise_sensitivity",        "label": "Noise Sensitivity",             "type": "SELECT", "value": "MEDIUM"},
            {"code": "maintenance_contract",     "label": "Maintenance Contract",          "type": "SELECT", "value": "YES"},
            {"code": "refrigerant_preference",   "label": "Refrigerant Preference",        "type": "SELECT", "value": "R32"},
            {"code": "lifespan_years",           "label": "Expected Lifespan (years)",     "type": "NUMBER", "value": "15"},
            {"code": "product_type",             "label": "HVAC Product Type",             "type": "SELECT", "value": "VRF_VRV"},
            {"code": "brand_preference",         "label": "Preferred Brand",               "type": "SELECT", "value": "DAIKIN"},
            {"code": "reference_product_code",   "label": "Reference Product Code",        "type": "TEXT",   "value": "VRF-ODU-14TR-DAIKIN-R32"},
            {"code": "operating_hours",          "label": "Operating Hours/day",           "type": "NUMBER", "value": "16"},
            {"code": "commissioning_required",   "label": "Commissioning Required",        "type": "SELECT", "value": "YES"},
            {"code": "geography_zone",           "label": "Geography Zone",                "type": "SELECT", "value": "UAE_COASTAL"},
            {"code": "floor_count",              "label": "Number of Floors",              "type": "NUMBER", "value": "2"},
            {"code": "ceiling_height_m",         "label": "Ceiling Height (m)",            "type": "NUMBER", "value": "3.5"},
            {"code": "landmark_brand",           "label": "Landmark Business Unit",        "type": "SELECT", "value": "MAX_FASHION"},
            {"code": "special_requirements",     "label": "Special Requirements",          "type": "TEXT",
             "value": "Heat recovery system preferred for energy saving. DEWA Grade A energy compliance required. Coordinating with Dubai Mall facilities team for rooftop access during night-time installation only."},
        ],
    },

    # -----------------------------------------------------------------------
    # 2 -- Home Centre Abu Dhabi (Cassette AC, medium, DRAFT)
    # -----------------------------------------------------------------------
    {
        "title": "Home Centre Abu Dhabi Al Wahda -- Cassette AC Upgrade",
        "description": (
            "Standalone Home Centre store at Al Wahda Mall, Abu Dhabi. "
            "Current split wall-mount units (7 years old) need upgrade to ceiling cassette units "
            "for better air distribution across the 480 sqm showroom floor. "
            "Mall provides 3-phase power; chilled water not available at this unit. "
            "False ceiling at 3.2m height is already in place."
        ),
        "request_type": ProcurementRequestType.RECOMMENDATION,
        "priority": "MEDIUM",
        "geography_country": "UAE",
        "geography_city": "Abu Dhabi",
        "currency": "AED",
        "status": "DRAFT",
        "attributes": [
            {"code": "store_type",               "label": "Store / Facility Type",        "type": "SELECT", "value": "MALL"},
            {"code": "area_sqm",                 "label": "Conditioned Area (sqm)",        "type": "NUMBER", "value": "480"},
            {"code": "zone_count",               "label": "Number of Zones",               "type": "NUMBER", "value": "3"},
            {"code": "cooling_load_tr",          "label": "Cooling Load (TR)",             "type": "NUMBER", "value": "18"},
            {"code": "ambient_temp_max",         "label": "Max Ambient Temp (deg-C)",      "type": "NUMBER", "value": "46"},
            {"code": "chilled_water_available",  "label": "Chilled Water Available",       "type": "SELECT", "value": "NO"},
            {"code": "outdoor_unit_restriction", "label": "Outdoor Unit Restriction",      "type": "SELECT", "value": "MALL_SIDE_DESIGNATED"},
            {"code": "existing_infrastructure",  "label": "Existing Infrastructure",       "type": "SELECT", "value": "SPLIT_EXISTING"},
            {"code": "budget_category",          "label": "Budget Category",               "type": "SELECT", "value": "ECONOMY"},
            {"code": "budget_aed",               "label": "Budget (AED)",                  "type": "NUMBER", "value": "92000"},
            {"code": "efficiency_priority",      "label": "Energy Efficiency Priority",    "type": "SELECT", "value": "MEDIUM"},
            {"code": "noise_sensitivity",        "label": "Noise Sensitivity",             "type": "SELECT", "value": "HIGH"},
            {"code": "maintenance_contract",     "label": "Maintenance Contract",          "type": "SELECT", "value": "PREFERABLE"},
            {"code": "refrigerant_preference",   "label": "Refrigerant Preference",        "type": "SELECT", "value": "R32"},
            {"code": "lifespan_years",           "label": "Expected Lifespan (years)",     "type": "NUMBER", "value": "12"},
            {"code": "product_type",             "label": "HVAC Product Type",             "type": "SELECT", "value": "CASSETTE_AC"},
            {"code": "brand_preference",         "label": "Preferred Brand",               "type": "SELECT", "value": "MITSUBISHI_ELECTRIC"},
            {"code": "operating_hours",          "label": "Operating Hours/day",           "type": "NUMBER", "value": "14"},
            {"code": "commissioning_required",   "label": "Commissioning Required",        "type": "SELECT", "value": "YES"},
            {"code": "geography_zone",           "label": "Geography Zone",                "type": "SELECT", "value": "UAE_COASTAL"},
            {"code": "floor_count",              "label": "Number of Floors",              "type": "NUMBER", "value": "1"},
            {"code": "ceiling_height_m",         "label": "Ceiling Height (m)",            "type": "NUMBER", "value": "3.2"},
            {"code": "landmark_brand",           "label": "Landmark Business Unit",        "type": "SELECT", "value": "HOME_CENTRE"},
            {"code": "special_requirements",     "label": "Special Requirements",          "type": "TEXT",
             "value": "Low-noise operation critical -- bedding/furniture zone adjacent. Installation during store closure hours only (Tues/Wed 11pm-7am)."},
        ],
    },

    # -----------------------------------------------------------------------
    # 3 -- Splash Riyadh KSA (FCU on CW, new mall fit-out, READY)
    # -----------------------------------------------------------------------
    {
        "title": "Splash Riyadh Panorama Mall -- FCU Chilled Water Fit-Out",
        "description": (
            "New Splash fashion retail fit-out at Panorama Mall, Riyadh, KSA. "
            "Store area: 650 sqm spread across one floor. "
            "Mall provides a central chilled water plant (7 deg-C supply / 12 deg-C return). "
            "We need to design and install FCU units on the CW backbone. "
            "High-end retail -- noise must be below 35 dB(A). "
            "Budget is tightly managed; need best-value quote with AMC."
        ),
        "request_type": ProcurementRequestType.BOTH,
        "priority": "HIGH",
        "geography_country": "KSA",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "status": "READY",
        "attributes": [
            {"code": "store_type",               "label": "Store / Facility Type",        "type": "SELECT", "value": "MALL"},
            {"code": "area_sqm",                 "label": "Conditioned Area (sqm)",        "type": "NUMBER", "value": "650"},
            {"code": "zone_count",               "label": "Number of Zones",               "type": "NUMBER", "value": "5"},
            {"code": "cooling_load_tr",          "label": "Cooling Load (TR)",             "type": "NUMBER", "value": "24"},
            {"code": "ambient_temp_max",         "label": "Max Ambient Temp (deg-C)",      "type": "NUMBER", "value": "52"},
            {"code": "chilled_water_available",  "label": "Chilled Water Available",       "type": "SELECT", "value": "YES"},
            {"code": "outdoor_unit_restriction", "label": "Outdoor Unit Restriction",      "type": "SELECT", "value": "NOT_APPLICABLE"},
            {"code": "existing_infrastructure",  "label": "Existing Infrastructure",       "type": "SELECT", "value": "NEW_BUILD"},
            {"code": "budget_category",          "label": "Budget Category",               "type": "SELECT", "value": "ECONOMY"},
            {"code": "budget_aed",               "label": "Budget (SAR equivalent AED)",   "type": "NUMBER", "value": "125000"},
            {"code": "efficiency_priority",      "label": "Energy Efficiency Priority",    "type": "SELECT", "value": "HIGH"},
            {"code": "noise_sensitivity",        "label": "Noise Sensitivity",             "type": "SELECT", "value": "HIGH"},
            {"code": "humidity_level",           "label": "Humidity Level",                "type": "SELECT", "value": "LOW"},
            {"code": "maintenance_contract",     "label": "Maintenance Contract",          "type": "SELECT", "value": "YES"},
            {"code": "product_type",             "label": "HVAC Product Type",             "type": "SELECT", "value": "FCU_CW"},
            {"code": "brand_preference",         "label": "Preferred Brand",               "type": "SELECT", "value": "CARRIER"},
            {"code": "reference_product_code",   "label": "Reference Product Code",        "type": "TEXT",   "value": "FCU-CW-2TR-CARRIER"},
            {"code": "operating_hours",          "label": "Operating Hours/day",           "type": "NUMBER", "value": "15"},
            {"code": "commissioning_required",   "label": "Commissioning Required",        "type": "SELECT", "value": "YES"},
            {"code": "geography_zone",           "label": "Geography Zone",                "type": "SELECT", "value": "KSA_INLAND"},
            {"code": "floor_count",              "label": "Number of Floors",              "type": "NUMBER", "value": "1"},
            {"code": "ceiling_height_m",         "label": "Ceiling Height (m)",            "type": "NUMBER", "value": "4.0"},
            {"code": "landmark_brand",           "label": "Landmark Business Unit",        "type": "SELECT", "value": "SPLASH"},
            {"code": "special_requirements",     "label": "Special Requirements",          "type": "TEXT",
             "value": "SASO compliance required for KSA. All equipment must carry SASO certification. Contractor must have HVAC MEP license registered with Riyadh Municipality. Roof penetrations to be sealed per mall fit-out guide."},
        ],
    },
]


class Command(BaseCommand):
    help = "Seed 3 realistic Landmark Group HVAC procurement demo requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing demo HVAC requests before seeding.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Email of the user to assign as creator (defaults to first superuser or first user).",
        )

    def handle(self, *args, **options):
        # Resolve user
        user = self._resolve_user(options.get("user"))
        self.stdout.write(f"Using user: {user.email}")

        if options["clear"]:
            deleted, _ = ProcurementRequest.objects.filter(
                domain_code="HVAC",
                description__icontains="Landmark Group HVAC Demo",
            ).delete()
            # Broader fallback -- look for our demo titles
            for demo in LANDMARK_DEMO_REQUESTS:
                ProcurementRequest.objects.filter(title=demo["title"]).delete()
            self.stdout.write(self.style.WARNING("Cleared existing demo HVAC requests."))

        created_count = 0
        for demo in LANDMARK_DEMO_REQUESTS:
            if ProcurementRequest.objects.filter(title=demo["title"]).exists():
                self.stdout.write(f"  SKIP (already exists): {demo['title']}")
                continue

            attrs_data = [
                {
                    "attribute_code": a["code"],
                    "attribute_label": a["label"],
                    "data_type": a["type"],
                    "value_text": a["value"],
                    "is_required": False,
                }
                for a in demo["attributes"]
            ]

            try:
                proc_request = ProcurementRequestService.create_request(
                    title=demo["title"],
                    description=demo["description"],
                    domain_code="HVAC",
                    schema_code="HVAC_GCC_V1",
                    request_type=demo["request_type"],
                    priority=demo["priority"],
                    geography_country=demo["geography_country"],
                    geography_city=demo["geography_city"],
                    currency=demo["currency"],
                    created_by=user,
                    attributes=attrs_data,
                )

                # Update status if not DRAFT (service creates as DRAFT)
                if demo.get("status") and demo["status"] != "DRAFT":
                    proc_request.status = demo["status"]
                    proc_request.save(update_fields=["status"])

                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  CREATED [{demo['status']}]: {proc_request.title} (pk={proc_request.pk})"
                    )
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"  FAILED to create '{demo['title']}': {exc}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created {created_count} of {len(LANDMARK_DEMO_REQUESTS)} HVAC demo requests."
            )
        )
        self.stdout.write(
            "\nNext step: Visit /procurement/ to see the demo requests."
            "\nClick 'Reference Catalogue' tab in any workspace to browse the HVAC product catalogue."
        )

    def _resolve_user(self, email: str | None):
        """Return the user to use as request creator."""
        if email:
            try:
                return User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f"User with email '{email}' not found.")
        # Default: first superuser
        user = User.objects.filter(is_superuser=True).order_by("pk").first()
        if user:
            return user
        # Fallback: any active user
        user = User.objects.filter(is_active=True).order_by("pk").first()
        if user:
            return user
        raise CommandError(
            "No users found in the database. "
            "Create a user first with: python manage.py createsuperuser"
        )
