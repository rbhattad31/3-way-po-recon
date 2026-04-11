"""
seed_recommendation_demo.py
----------------------------
Creates exactly 2 HVAC procurement requests to demonstrate the two
recommendation code paths:

  REQUEST A -- RULES ENGINE FIRES (confident=True)
    All 5 required attributes present + store_type=STANDALONE + high ambient.
    HVACRulesEngine fires RULE_S1_STANDALONE_HIGH_AMB_VRF, returns
    confident=True, system=VRF_SYSTEM.  AI / web search are NOT called.

  REQUEST B -- AI FALLBACK (confident=False)
    Required attribute "chilled_water_available" is intentionally omitted.
    HVACRulesEngine._missing_required() returns ["chilled_water_available"],
    so the engine returns confident=False -> recommendation_service falls
    through to the LangGraph AI pipeline (with web search context).

Usage:
    python manage.py seed_recommendation_demo
    python manage.py seed_recommendation_demo --user sridhar.s@bradsol.com
    python manage.py seed_recommendation_demo --clear
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.procurement.models import ProcurementRequest, ProcurementRequestType
from apps.procurement.services.request_service import ProcurementRequestService

User = get_user_model()

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

DEMO_REQUESTS = [

    # -----------------------------------------------------------------------
    # REQUEST A
    # Rules engine path: store_type=STANDALONE, ambient_temp_max=48 (>=46),
    # zone_count=4 (>=3), chilled_water_available=NO
    # => fires RULE_S1_STANDALONE_HIGH_AMB_VRF  confident=True  conf=0.92
    # => AI is NOT invoked
    # -----------------------------------------------------------------------
    {
        "title": "LMG Al Barsha Standalone -- VRF System (Rules Demo)",
        "description": (
            "Standalone Landmark Group retail store at Al Barsha 1, Dubai. "
            "Single-level, 800 sqm showroom requiring full HVAC refresh. "
            "No chilled water supply in the building -- relies on self-contained refrigerant circuit. "
            "Located in a high-ambient coastal zone (peak 48 deg-C in July/Aug). "
            "4 functional zones: sales floor, fitting rooms, stockroom, back-office. "
            "This request is configured so the Deterministic Rules Engine finds a match "
            "confidently (VRF_SYSTEM) without needing AI assistance."
        ),
        "request_type": ProcurementRequestType.RECOMMENDATION,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "status": "READY",
        "attributes": [
            # --- 5 required fields (all present -> rules engine will run fully) ---
            {
                "attribute_code": "store_type",
                "attribute_label": "Store / Facility Type",
                "data_type": "SELECT",
                "value_text": "STANDALONE",
                "is_required": True,
            },
            {
                "attribute_code": "area_sqm",
                "attribute_label": "Conditioned Area (sqm)",
                "data_type": "NUMBER",
                "value_text": "800",
                "is_required": True,
            },
            {
                "attribute_code": "zone_count",
                "attribute_label": "Number of Zones",
                "data_type": "NUMBER",
                "value_text": "4",
                "is_required": True,
            },
            {
                "attribute_code": "ambient_temp_max",
                "attribute_label": "Max Ambient Temp (deg-C)",
                "data_type": "NUMBER",
                "value_text": "48",
                "is_required": True,
            },
            {
                "attribute_code": "chilled_water_available",
                "attribute_label": "Chilled Water Available",
                "data_type": "SELECT",
                "value_text": "NO",
                "is_required": True,
            },
            # --- additional context attributes ---
            {
                "attribute_code": "cooling_load_tr",
                "attribute_label": "Cooling Load (TR)",
                "data_type": "NUMBER",
                "value_text": "28",
                "is_required": False,
            },
            {
                "attribute_code": "dust_level",
                "attribute_label": "Dust / Sandstorm Level",
                "data_type": "SELECT",
                "value_text": "HIGH",
                "is_required": False,
            },
            {
                "attribute_code": "efficiency_priority",
                "attribute_label": "Energy Efficiency Priority",
                "data_type": "SELECT",
                "value_text": "HIGH",
                "is_required": False,
            },
            {
                "attribute_code": "budget_category",
                "attribute_label": "Budget Category",
                "data_type": "SELECT",
                "value_text": "MIDRANGE",
                "is_required": False,
            },
            {
                "attribute_code": "budget_aed",
                "attribute_label": "Budget (AED)",
                "data_type": "NUMBER",
                "value_text": "350000",
                "is_required": False,
            },
            {
                "attribute_code": "outdoor_unit_restriction",
                "attribute_label": "Outdoor Unit Restriction",
                "data_type": "SELECT",
                "value_text": "ROOFTOP_ONLY",
                "is_required": False,
            },
            {
                "attribute_code": "brand_preference",
                "attribute_label": "Preferred Brand",
                "data_type": "SELECT",
                "value_text": "DAIKIN",
                "is_required": False,
            },
            {
                "attribute_code": "noise_sensitivity",
                "attribute_label": "Noise Sensitivity",
                "data_type": "SELECT",
                "value_text": "MEDIUM",
                "is_required": False,
            },
            {
                "attribute_code": "maintenance_contract",
                "attribute_label": "Maintenance Contract",
                "data_type": "SELECT",
                "value_text": "YES",
                "is_required": False,
            },
            {
                "attribute_code": "operating_hours",
                "attribute_label": "Operating Hours/day",
                "data_type": "NUMBER",
                "value_text": "16",
                "is_required": False,
            },
            {
                "attribute_code": "floor_count",
                "attribute_label": "Number of Floors",
                "data_type": "NUMBER",
                "value_text": "1",
                "is_required": False,
            },
            {
                "attribute_code": "ceiling_height_m",
                "attribute_label": "Ceiling Height (m)",
                "data_type": "NUMBER",
                "value_text": "3.8",
                "is_required": False,
            },
            {
                "attribute_code": "geography_zone",
                "attribute_label": "Geography Zone",
                "data_type": "SELECT",
                "value_text": "UAE_COASTAL",
                "is_required": False,
            },
            {
                "attribute_code": "landmark_brand",
                "attribute_label": "Landmark Business Unit",
                "data_type": "SELECT",
                "value_text": "MAX_FASHION",
                "is_required": False,
            },
            {
                "attribute_code": "special_requirements",
                "attribute_label": "Special Requirements",
                "data_type": "TEXT",
                "value_text": (
                    "DEWA Grade A energy rating compliance mandatory. "
                    "Heat recovery option preferable to reduce electricity bills. "
                    "All refrigerant piping to be concealed in false ceiling void."
                ),
                "is_required": False,
            },
        ],
    },

    # -----------------------------------------------------------------------
    # REQUEST B
    # AI fallback path: "chilled_water_available" is intentionally OMITTED.
    # HVACRulesEngine._missing_required() detects ["chilled_water_available"]
    # and returns confident=False immediately.
    # recommendation_service then calls the LangGraph pipeline which includes
    # the WebSearchService.search_product_info() node for live market data.
    # -----------------------------------------------------------------------
    {
        "title": "LMG Food Hall Abu Dhabi -- HVAC Assessment (AI Demo)",
        "description": (
            "Mixed-use food hall and dining zone within the Landmark Group complex, Abu Dhabi. "
            "Area: 550 sqm across two open-plan dining levels plus a central kitchen zone. "
            "Unusual combination of high occupancy, grease-laden air extraction, and ambient "
            "temperatures reaching 47 deg-C in summer. "
            "Chilled water availability from the building plant is NOT confirmed yet -- "
            "site survey is pending. This request is configured so the Deterministic Rules "
            "Engine returns confident=False (missing required attribute) and the system "
            "falls back to the AI recommendation engine with live web market data."
        ),
        "request_type": ProcurementRequestType.RECOMMENDATION,
        "priority": "MEDIUM",
        "geography_country": "UAE",
        "geography_city": "Abu Dhabi",
        "currency": "AED",
        "status": "READY",
        "attributes": [
            # --- only 4 of the 5 required fields are present ---
            # "chilled_water_available" is INTENTIONALLY OMITTED
            # so the rules engine fires _missing_required() -> confident=False
            {
                "attribute_code": "store_type",
                "attribute_label": "Store / Facility Type",
                "data_type": "SELECT",
                "value_text": "RESTAURANT",
                "is_required": True,
            },
            {
                "attribute_code": "area_sqm",
                "attribute_label": "Conditioned Area (sqm)",
                "data_type": "NUMBER",
                "value_text": "550",
                "is_required": True,
            },
            {
                "attribute_code": "zone_count",
                "attribute_label": "Number of Zones",
                "data_type": "NUMBER",
                "value_text": "3",
                "is_required": True,
            },
            {
                "attribute_code": "ambient_temp_max",
                "attribute_label": "Max Ambient Temp (deg-C)",
                "data_type": "NUMBER",
                "value_text": "47",
                "is_required": True,
            },
            # "chilled_water_available" deliberately absent here
            # --- additional context for the AI to work with ---
            {
                "attribute_code": "cooling_load_tr",
                "attribute_label": "Cooling Load (TR)",
                "data_type": "NUMBER",
                "value_text": "20",
                "is_required": False,
            },
            {
                "attribute_code": "noise_sensitivity",
                "attribute_label": "Noise Sensitivity",
                "data_type": "SELECT",
                "value_text": "HIGH",
                "is_required": False,
            },
            {
                "attribute_code": "dust_level",
                "attribute_label": "Dust / Sandstorm Level",
                "data_type": "SELECT",
                "value_text": "MEDIUM",
                "is_required": False,
            },
            {
                "attribute_code": "efficiency_priority",
                "attribute_label": "Energy Efficiency Priority",
                "data_type": "SELECT",
                "value_text": "HIGH",
                "is_required": False,
            },
            {
                "attribute_code": "budget_category",
                "attribute_label": "Budget Category",
                "data_type": "SELECT",
                "value_text": "MIDRANGE",
                "is_required": False,
            },
            {
                "attribute_code": "budget_aed",
                "attribute_label": "Budget (AED)",
                "data_type": "NUMBER",
                "value_text": "210000",
                "is_required": False,
            },
            {
                "attribute_code": "maintenance_contract",
                "attribute_label": "Maintenance Contract",
                "data_type": "SELECT",
                "value_text": "PREFERABLE",
                "is_required": False,
            },
            {
                "attribute_code": "operating_hours",
                "attribute_label": "Operating Hours/day",
                "data_type": "NUMBER",
                "value_text": "18",
                "is_required": False,
            },
            {
                "attribute_code": "floor_count",
                "attribute_label": "Number of Floors",
                "data_type": "NUMBER",
                "value_text": "2",
                "is_required": False,
            },
            {
                "attribute_code": "ceiling_height_m",
                "attribute_label": "Ceiling Height (m)",
                "data_type": "NUMBER",
                "value_text": "3.5",
                "is_required": False,
            },
            {
                "attribute_code": "geography_zone",
                "attribute_label": "Geography Zone",
                "data_type": "SELECT",
                "value_text": "UAE_COASTAL",
                "is_required": False,
            },
            {
                "attribute_code": "landmark_brand",
                "attribute_label": "Landmark Business Unit",
                "data_type": "SELECT",
                "value_text": "LANDMARK_HOSPITALITY",
                "is_required": False,
            },
            {
                "attribute_code": "special_requirements",
                "attribute_label": "Special Requirements",
                "data_type": "TEXT",
                "value_text": (
                    "Kitchen extraction system must be tightly integrated with HVAC. "
                    "High humidity load from cooking operations -- dehumidification capacity required. "
                    "Health & Safety: HACCP compliance for kitchen zone air quality. "
                    "Chilled water confirmation pending site survey -- solution must work with or without CW."
                ),
                "is_required": False,
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Seed 2 HVAC demo requests: "
        "(A) deterministic rules engine fires confidently (REQUEST A), "
        "(B) rules return confident=False so AI fallback is triggered (REQUEST B)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing demo requests (matched by title) before seeding.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default="sridhar.s@bradsol.com",
            help="Email of the user to assign as request creator (default: sridhar.s@bradsol.com).",
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options["user"])
        self.stdout.write(f"Using creator: {user.email} (pk={user.pk})")

        if options["clear"]:
            for demo in DEMO_REQUESTS:
                deleted_count, _ = ProcurementRequest.objects.filter(
                    title=demo["title"]
                ).delete()
                if deleted_count:
                    self.stdout.write(
                        self.style.WARNING(f"  DELETED: {demo['title']}")
                    )

        self.stdout.write("")
        created = 0
        skipped = 0

        for idx, demo in enumerate(DEMO_REQUESTS, start=1):
            label = "A -- RULES ENGINE (confident=True)" if idx == 1 else "B -- AI FALLBACK (confident=False)"
            self.stdout.write(f"[{label}]")

            if ProcurementRequest.objects.filter(title=demo["title"]).exists():
                self.stdout.write(
                    self.style.WARNING(f"  SKIP (already exists): {demo['title']}")
                )
                skipped += 1
                continue

            try:
                req = ProcurementRequestService.create_request(
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
                    attributes=demo["attributes"],
                )

                # Service creates requests as DRAFT; promote to READY
                if demo.get("status", "DRAFT") != "DRAFT":
                    req.status = demo["status"]
                    req.save(update_fields=["status"])

                created += 1
                attr_codes = [a["attribute_code"] for a in demo["attributes"]]
                missing_required = [
                    c for c in ("store_type", "area_sqm", "zone_count", "ambient_temp_max", "chilled_water_available")
                    if c not in attr_codes
                ]
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  CREATED [{demo['status']}]: {req.title} (pk={req.pk})"
                    )
                )
                if missing_required:
                    self.stdout.write(
                        f"  Missing required attrs: {missing_required}"
                        f"  -> Rules will return confident=False -> AI fallback will run"
                    )
                else:
                    self.stdout.write(
                        f"  All 5 required attrs present -> Rules will fire -> confident=True"
                    )

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(f"  FAILED: {exc}")
                )

            self.stdout.write("")

        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created}, skipped {skipped} of {len(DEMO_REQUESTS)} demo requests."
            )
        )
        self.stdout.write(
            "\nNext steps:"
            "\n  1. Open the browser at /procurement/"
            "\n  2. Find 'LMG Al Barsha Standalone...' -- run Recommendation -> rules engine fires, no AI"
            "\n  3. Find 'LMG Food Hall Abu Dhabi...'  -- run Recommendation -> AI + web search activate"
        )

    # -----------------------------------------------------------------------

    def _resolve_user(self, email: str):
        """Return the user matching the given email, or raise CommandError."""
        try:
            return User.objects.get(email=email)
        except User.DoesNotExist:
            # List available users to help the developer
            available = list(
                User.objects.filter(is_active=True).values_list("email", flat=True)[:10]
            )
            raise CommandError(
                f"User with email '{email}' not found.\n"
                f"Available users (first 10): {available}\n"
                f"Use --user <email> to specify a different user."
            )
