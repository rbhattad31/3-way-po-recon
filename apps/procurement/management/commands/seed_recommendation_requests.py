"""Management command: seed_recommendation_requests
====================================================
Creates 10 varied HVAC procurement requests and immediately runs the
deterministic recommendation engine on each, printing:

  Request title  |  Expected system  |  Got  |  Rule fired  |  PASS / FAIL

Each request is designed to exercise a specific rule path in HVACRulesEngine.
All requests include ALL required attributes so the engine fires confidently
-- AI is explicitly disabled (use_ai=False) so no OpenAI key is needed.

Usage
-----
    python manage.py seed_recommendation_requests
    python manage.py seed_recommendation_requests --user admin@example.com
    python manage.py seed_recommendation_requests --clear   # wipe & re-seed
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core.enums import AnalysisRunType, ProcurementRequestType, ProcurementRequestStatus
from apps.procurement.models import ProcurementRequest
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.recommendation_service import RecommendationService
from apps.procurement.services.request_service import ProcurementRequestService

User = get_user_model()

# ---------------------------------------------------------------------------
# Helper: build a complete required-attribute list for a scenario
# ---------------------------------------------------------------------------
def _attrs(
    store_id, brand, country, city, store_type, store_format,
    area_sqft, ceiling_height_ft, ambient_temp_max,
    humidity_level, dust_exposure, heat_load_category,
    landlord_constraints, budget_level,
    # optional extras
    energy_efficiency_priority="MEDIUM",
    maintenance_priority="STANDARD",
):
    return [
        # --- Required attributes (all 14 in HVAC_REQUIRED_FOR_RECOMMENDATION) ---
        {"attribute_code": "store_id",             "attribute_label": "Store ID",
         "data_type": "TEXT",   "value_text": store_id,           "is_required": True},
        {"attribute_code": "brand",                "attribute_label": "Brand",
         "data_type": "TEXT",   "value_text": brand,              "is_required": True},
        {"attribute_code": "country",              "attribute_label": "Country",
         "data_type": "TEXT",   "value_text": country,            "is_required": True},
        {"attribute_code": "city",                 "attribute_label": "City",
         "data_type": "TEXT",   "value_text": city,               "is_required": True},
        {"attribute_code": "store_type",           "attribute_label": "Store Type",
         "data_type": "SELECT", "value_text": store_type,         "is_required": True},
        {"attribute_code": "store_format",         "attribute_label": "Store Format",
         "data_type": "TEXT",   "value_text": store_format,       "is_required": True},
        {"attribute_code": "area_sqft",            "attribute_label": "Area (sqft)",
         "data_type": "NUMBER", "value_number": Decimal(str(area_sqft)), "is_required": True},
        {"attribute_code": "ceiling_height_ft",    "attribute_label": "Ceiling Height (ft)",
         "data_type": "NUMBER", "value_number": Decimal(str(ceiling_height_ft)), "is_required": True},
        {"attribute_code": "ambient_temp_max",     "attribute_label": "Max Ambient Temp (C)",
         "data_type": "NUMBER", "value_number": Decimal(str(ambient_temp_max)),  "is_required": True},
        {"attribute_code": "humidity_level",       "attribute_label": "Humidity Level",
         "data_type": "SELECT", "value_text": humidity_level,     "is_required": True},
        {"attribute_code": "dust_exposure",        "attribute_label": "Dust Exposure",
         "data_type": "SELECT", "value_text": dust_exposure,      "is_required": True},
        {"attribute_code": "heat_load_category",   "attribute_label": "Heat Load Category",
         "data_type": "SELECT", "value_text": heat_load_category, "is_required": True},
        {"attribute_code": "landlord_constraints", "attribute_label": "Landlord Constraints",
         "data_type": "TEXT",   "value_text": landlord_constraints, "is_required": True},
        {"attribute_code": "budget_level",         "attribute_label": "Budget Level",
         "data_type": "SELECT", "value_text": budget_level,       "is_required": True},
        # --- Optional enhancers ---
        {"attribute_code": "energy_efficiency_priority", "attribute_label": "Energy Efficiency Priority",
         "data_type": "SELECT", "value_text": energy_efficiency_priority},
        {"attribute_code": "maintenance_priority",       "attribute_label": "Maintenance Priority",
         "data_type": "SELECT", "value_text": maintenance_priority},
    ]


# ---------------------------------------------------------------------------
# 10 scenario definitions
# ---------------------------------------------------------------------------
# Each entry:
#   title              -- display name
#   description        -- 1-2 line scenario context
#   country/city       -- geography (affects compliance standards)
#   expected_system    -- what the rules engine is expected to return
#   expected_rule      -- rule code expected in reasoning_details.rules_fired
#   attrs              -- output of _attrs() helper
# ---------------------------------------------------------------------------

SCENARIOS = [

    # -----------------------------------------------------------------------
    # 1. Mall + Chilled Water backbone  ->  FCU_CHILLED_WATER
    #    Rule: RULE_M1_MALL_FCU_CW
    #    landlord_constraints contains "chilled water" -> cw_available="YES"
    # -----------------------------------------------------------------------
    {
        "title": "Dubai Mall -- Max Fashion FCU Upgrade (CW available)",
        "description": (
            "Mall store where the developer provides a chilled water backbone. "
            "FCU system is the standard approach -- no outdoor units required."
        ),
        "country": "UAE", "city": "Dubai",
        "expected_system": "FCU_CHILLED_WATER",
        "expected_rule": "RULE_M1_MALL_FCU_CW",
        "attrs": _attrs(
            store_id="DXB-MAX-001", brand="Max Fashion",
            country="UAE", city="Dubai",
            store_type="MALL",
            store_format="FASHION_RETAIL",
            area_sqft=32000,
            ceiling_height_ft=15,
            ambient_temp_max=48,
            humidity_level="LOW",
            dust_exposure="LOW",
            heat_load_category="MEDIUM",
            landlord_constraints="chilled water backbone provided by Dubai Mall developer; no ODU allowed",
            budget_level="MEDIUM",
            energy_efficiency_priority="HIGH",
        ),
    },

    # -----------------------------------------------------------------------
    # 2. Small standalone store (< 2 000 sqm)  ->  SPLIT_SYSTEM
    #    Rule: RULE_U1_SMALL_AREA_SPLIT_AC
    #    area_sqft=15 000 => area_sqm ~= 1 394 sqm  (< 2 000 threshold)
    # -----------------------------------------------------------------------
    {
        "title": "Sharjah Standalone Kiosk -- Home Accessories (Small 1 400 sqm)",
        "description": (
            "Small standalone kiosk at 1,400 sqm -- below the 2,000 sqm threshold. "
            "Split AC is the most practical low-cost solution."
        ),
        "country": "UAE", "city": "Sharjah",
        "expected_system": "SPLIT_SYSTEM",
        "expected_rule": "RULE_U1_SMALL_AREA_SPLIT_AC",
        "attrs": _attrs(
            store_id="SHJ-HA-002", brand="Home Accessories",
            country="UAE", city="Sharjah",
            store_type="STANDALONE",
            store_format="GENERAL_RETAIL",
            area_sqft=15000,
            ceiling_height_ft=12,
            ambient_temp_max=45,
            humidity_level="LOW",
            dust_exposure="LOW",
            heat_load_category="LOW",
            landlord_constraints="Standard standalone; no chilled water",
            budget_level="LOW",
            energy_efficiency_priority="LOW",
        ),
    },

    # -----------------------------------------------------------------------
    # 3. Large standalone + high ambient + HIGH efficiency  ->  VRF_SYSTEM
    #    Rule: RULE_S2_LARGE_STANDALONE_HIEFF_VRF
    #    area_sqft=65 000 => area_sqm ~= 6 039 sqm (>= 5 000)
    #    ambient_temp_max=48 (>= 45), energy_efficiency_priority=HIGH
    # -----------------------------------------------------------------------
    {
        "title": "Riyadh Standalone Flagship -- Centrepoint 6 000 sqm (Large, High-Eff)",
        "description": (
            "Large standalone flagship in extreme-heat Riyadh. "
            "6 039 sqm, ambient peak 48C, HIGH energy efficiency priority -> VRF."
        ),
        "country": "KSA", "city": "Riyadh",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_S2_LARGE_STANDALONE_HIEFF_VRF",
        "attrs": _attrs(
            store_id="RUH-CP-003", brand="Centrepoint",
            country="KSA", city="Riyadh",
            store_type="STANDALONE",
            store_format="FASHION_RETAIL",
            area_sqft=65000,
            ceiling_height_ft=14,
            ambient_temp_max=48,
            humidity_level="LOW",
            dust_exposure="HIGH",
            heat_load_category="HIGH",
            landlord_constraints="No chilled water. Rooftop ODU access permitted.",
            budget_level="HIGH",
            energy_efficiency_priority="HIGH",
        ),
    },

    # -----------------------------------------------------------------------
    # 4. Mid-size standalone + LOW budget  ->  PACKAGED_DX_UNIT
    #    Rule: RULE_U2_MID_AREA_LOW_BUDGET_PKG
    #    area_sqft=35 000 => area_sqm ~= 3 252 sqm (2 000-5 000), budget=LOW
    # -----------------------------------------------------------------------
    {
        "title": "Kuwait City Standalone -- Splash Apparel 3 250 sqm (Mid, Low Budget)",
        "description": (
            "Mid-size store 3,252 sqm on a tight LOW budget. "
            "Packaged rooftop DX unit is the lowest-cost option in this range."
        ),
        "country": "KWT", "city": "Kuwait City",
        "expected_system": "PACKAGED_DX_UNIT",
        "expected_rule": "RULE_U2_MID_AREA_LOW_BUDGET_PKG",
        "attrs": _attrs(
            store_id="KWT-SPL-004", brand="Splash",
            country="KWT", city="Kuwait City",
            store_type="STANDALONE",
            store_format="FASHION_RETAIL",
            area_sqft=35000,
            ceiling_height_ft=13,
            ambient_temp_max=46,
            humidity_level="LOW",
            dust_exposure="LOW",
            heat_load_category="MEDIUM",
            landlord_constraints="No chilled water. Rooftop access permitted.",
            budget_level="LOW",
            energy_efficiency_priority="LOW",
        ),
    },

    # -----------------------------------------------------------------------
    # 5. Mid-size + high ambient + HIGH efficiency + MEDIUM budget  ->  VRF_SYSTEM
    #    Rule: RULE_S_MEDLARGE_HIAMB_EFF_VRF
    #    area_sqft=40 000 => area_sqm ~= 3 716 sqm (2 000-5 000)
    #    ambient=46 (>= 45), efficiency=HIGH, budget=MEDIUM (not LOW)
    # -----------------------------------------------------------------------
    {
        "title": "Abu Dhabi Standalone -- Shoemart 3 700 sqm (Mid, Efficiency Priority)",
        "description": (
            "Mid-size store with high ambient 46C and HIGH energy efficiency priority. "
            "VRF beats packaged DX on lifecycle energy cost in this profile."
        ),
        "country": "UAE", "city": "Abu Dhabi",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_S_MEDLARGE_HIAMB_EFF_VRF",
        "attrs": _attrs(
            store_id="AUH-SM-005", brand="Shoemart",
            country="UAE", city="Abu Dhabi",
            store_type="STANDALONE",
            store_format="FOOTWEAR_RETAIL",
            area_sqft=40000,
            ceiling_height_ft=13,
            ambient_temp_max=46,
            humidity_level="LOW",
            dust_exposure="LOW",
            heat_load_category="MEDIUM",
            landlord_constraints="No chilled water. Small rooftop for ODU.",
            budget_level="MEDIUM",
            energy_efficiency_priority="HIGH",
        ),
    },

    # -----------------------------------------------------------------------
    # 6. Mid-size + high ambient + HIGH maintenance + few zones  ->  PACKAGED_DX_UNIT
    #    Rule: RULE_S_MEDLARGE_MAINT_PACKAGED
    #    area_sqft=30 000 => area_sqm ~= 2 787 sqm (2 000-5 000)
    #    ambient=46 (>= 45), maintenance_priority=HIGH, heat_load=LOW (zone=1)
    # -----------------------------------------------------------------------
    {
        "title": "Doha Standalone -- Emax Electronics 2 800 sqm (High Maintenance Priority)",
        "description": (
            "Mid-size store where field-service convenience is paramount. "
            "High maintenance posture and 1 zone -> Packaged DX is preferred."
        ),
        "country": "QAT", "city": "Doha",
        "expected_system": "PACKAGED_DX_UNIT",
        "expected_rule": "RULE_S_MEDLARGE_MAINT_PACKAGED",
        "attrs": _attrs(
            store_id="DOH-EM-006", brand="Emax",
            country="QAT", city="Doha",
            store_type="STANDALONE",
            store_format="ELECTRONICS_RETAIL",
            area_sqft=30000,
            ceiling_height_ft=14,
            ambient_temp_max=46,
            humidity_level="LOW",
            dust_exposure="LOW",
            heat_load_category="LOW",
            landlord_constraints="No chilled water. Rooftop permitted.",
            budget_level="MEDIUM",
            energy_efficiency_priority="MEDIUM",
            maintenance_priority="HIGH",
        ),
    },

    # -----------------------------------------------------------------------
    # 7. Mall + NO Chilled Water + 3 Zones  ->  VRF_SYSTEM
    #    Rule: RULE_M2_MALL_NO_CW_VRF
    #    store_type=MALL, area_sqm ~= 2 044 sqm (> 2 000 so universal doesn't fire)
    #    CW: "chilled water" NOT in landlord text -> cw_available=NO
    #    heat_load_category=HIGH -> zone_count proxy=3.0 (>= 3)
    # -----------------------------------------------------------------------
    {
        "title": "Muscat City Centre Mall -- Babyshop 2 000 sqm (Mall, No CW, Multi-Zone)",
        "description": (
            "Mall store where the developer does NOT provide chilled water. "
            "3-zone requirement -> VRF is recommended for multi-zone mall stores without CW."
        ),
        "country": "OMN", "city": "Muscat",
        "expected_system": "VRF_SYSTEM",
        "expected_rule": "RULE_M2_MALL_NO_CW_VRF",
        "attrs": _attrs(
            store_id="MCT-BS-007", brand="Babyshop",
            country="OMN", city="Muscat",
            store_type="MALL",
            store_format="KIDS_RETAIL",
            area_sqft=22000,
            ceiling_height_ft=12,
            ambient_temp_max=44,
            humidity_level="MEDIUM",
            dust_exposure="LOW",
            heat_load_category="HIGH",
            landlord_constraints="No central plant available. Self-contained system required. ODU on rooftop.",
            budget_level="MEDIUM",
            energy_efficiency_priority="MEDIUM",
        ),
    },

    # -----------------------------------------------------------------------
    # 8. Warehouse + moderate load (~89 TR)  ->  PACKAGED_DX_UNIT
    #    Rule: RULE_W2_WAREHOUSE_PACKAGED
    #    area_sqft=26 000 => area_sqm ~= 2 415 sqm (>= 2 000, passes universal)
    #    estimated_tr = 2415 * 130 / 3517 ~= 89.3 TR  (50 < 89 < 200)
    # -----------------------------------------------------------------------
    {
        "title": "Bengaluru Distribution Centre -- Zone B Warehouse (~89 TR)",
        "description": (
            "Mid-size warehouse 2,415 sqm with estimated 89 TR cooling load. "
            "Above 50 TR threshold -> Packaged rooftop DX is recommended."
        ),
        "country": "IND", "city": "Bengaluru",
        "expected_system": "PACKAGED_DX_UNIT",
        "expected_rule": "RULE_W2_WAREHOUSE_PACKAGED",
        "attrs": _attrs(
            store_id="BLR-WH-008", brand="Landmark Logistics",
            country="IND", city="Bengaluru",
            store_type="WAREHOUSE",
            store_format="DISTRIBUTION_CENTRE",
            area_sqft=26000,
            ceiling_height_ft=30,
            ambient_temp_max=38,
            humidity_level="HIGH",
            dust_exposure="LOW",
            heat_load_category="HIGH",
            landlord_constraints="Standalone building. Rooftop access confirmed.",
            budget_level="MEDIUM",
            energy_efficiency_priority="LOW",
        ),
    },

    # -----------------------------------------------------------------------
    # 9. Warehouse + very large load (~223 TR)  ->  CHILLER_PLANT
    #    Rule: RULE_W1_WAREHOUSE_CHILLER
    #    area_sqft=65 000 => area_sqm ~= 6 039 sqm (>= 2 000, passes universal)
    #    estimated_tr = 6039 * 130 / 3517 ~= 223 TR  (> 200)
    # -----------------------------------------------------------------------
    {
        "title": "Jebel Ali Mega-Warehouse -- Main Hall (~223 TR Heavy Load)",
        "description": (
            "Very large 6,039 sqm warehouse with >200 TR estimated load. "
            "Central chiller plant is the recommended solution for this scale."
        ),
        "country": "UAE", "city": "Dubai",
        "expected_system": "CHILLER_PLANT",
        "expected_rule": "RULE_W1_WAREHOUSE_CHILLER",
        "attrs": _attrs(
            store_id="JXB-WH-009", brand="Landmark Logistics",
            country="UAE", city="Dubai",
            store_type="WAREHOUSE",
            store_format="MEGA_WAREHOUSE",
            area_sqft=65000,
            ceiling_height_ft=40,
            ambient_temp_max=46,
            humidity_level="LOW",
            dust_exposure="HIGH",
            heat_load_category="HIGH",
            landlord_constraints="Standalone facility. Full rooftop and plant room available.",
            budget_level="HIGH",
            energy_efficiency_priority="HIGH",
        ),
    },

    # -----------------------------------------------------------------------
    # 10. Data Centre  ->  CHILLER_PLANT
    #     Rule: RULE_DC_CHILLER
    #     store_type=DATA_CENTER, area_sqft=30 000 => area_sqm ~= 2 787 sqm (>= 2 000)
    #     DATA_CENTER rule fires unconditionally when not already selected.
    # -----------------------------------------------------------------------
    {
        "title": "Dubai Operations Edge Data Centre -- Precision Cooling (2 800 sqm)",
        "description": (
            "Edge data centre in Dubai with 24/7 operation and N+1 redundancy. "
            "Chiller plant with precision cooling is the mandatory recommendation."
        ),
        "country": "UAE", "city": "Dubai",
        "expected_system": "CHILLER_PLANT",
        "expected_rule": "RULE_DC_CHILLER",
        "attrs": _attrs(
            store_id="DXB-DC-010", brand="LMG IT",
            country="UAE", city="Dubai",
            store_type="DATA_CENTER",
            store_format="EDGE_DATA_CENTRE",
            area_sqft=30000,
            ceiling_height_ft=12,
            ambient_temp_max=46,
            humidity_level="LOW",
            dust_exposure="LOW",
            heat_load_category="HIGH",
            landlord_constraints="Dedicated building. Full plant room. No outdoor restrictions.",
            budget_level="HIGH",
            energy_efficiency_priority="HIGH",
        ),
    },
]


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Seed 10 HVAC procurement requests, run the deterministic recommendation "
        "engine on each, and print a PASS/FAIL table."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing requests with the same titles before seeding.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default="",
            help="Email of the user to assign as creator. Defaults to first superuser.",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        user = self._resolve_user(options["user"])
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nseed_recommendation_requests -- user: {user.email}\n"
        ))

        if options["clear"]:
            titles = [s["title"] for s in SCENARIOS]
            deleted, _ = ProcurementRequest.objects.filter(title__in=titles).delete()
            self.stdout.write(self.style.WARNING(f"  Cleared {deleted} existing request(s).\n"))

        results = []
        for idx, scenario in enumerate(SCENARIOS, start=1):
            row = self._run_scenario(idx, scenario, user)
            results.append(row)

        # ------------------------------------------------------------------
        # Print summary table
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            "=" * 100
        ))
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{'#':<3} {'Expected':20} {'Got':20} {'Conf':5} {'Rule Fired':40} {'Status'}"
        ))
        self.stdout.write(self.style.MIGRATE_HEADING("=" * 100))

        passed = failed = skipped = 0
        for row in results:
            status_label = row["status"]
            if status_label == "PASS":
                style = self.style.SUCCESS
                passed += 1
            elif status_label == "FAIL":
                style = self.style.ERROR
                failed += 1
            else:
                style = self.style.WARNING
                skipped += 1

            self.stdout.write(style(
                f"{row['idx']:<3} "
                f"{row['expected']:20} "
                f"{row['got']:20} "
                f"{row['confidence']:<5.2f} "
                f"{row['rule'][:38]:40} "
                f"{status_label}"
            ))

        self.stdout.write(self.style.MIGRATE_HEADING("=" * 100))
        self.stdout.write(
            self.style.SUCCESS(f"PASSED: {passed}")
            + "  "
            + (self.style.ERROR(f"FAILED: {failed}") if failed else f"FAILED: {failed}")
            + f"  SKIPPED: {skipped}"
            + f"  TOTAL: {len(results)}"
        )
        self.stdout.write("")

    # ------------------------------------------------------------------
    def _run_scenario(self, idx: int, scenario: dict, user) -> dict:
        title = scenario["title"]
        expected = scenario["expected_system"]
        expected_rule = scenario["expected_rule"]

        self.stdout.write(f"[{idx:02d}] {title[:70]}")

        # Skip if already exists and --clear was not used
        if ProcurementRequest.objects.filter(title=title).exists():
            self.stdout.write(self.style.WARNING("     SKIP -- already exists"))
            return {
                "idx": idx, "expected": expected, "got": "SKIPPED",
                "confidence": 0.0, "rule": "-", "status": "SKIP",
            }

        try:
            # 1. Create the request
            req = ProcurementRequestService.create_request(
                title=title,
                description=scenario.get("description", ""),
                domain_code="HVAC",
                schema_code="HVAC_GCC_V1",
                request_type=ProcurementRequestType.RECOMMENDATION,
                priority="HIGH",
                geography_country=scenario["country"],
                geography_city=scenario["city"],
                currency="AED",
                created_by=user,
                attributes=scenario["attrs"],
            )

            # Promote to READY
            req.status = ProcurementRequestStatus.READY
            req.save(update_fields=["status"])

            # 2. Create and run analysis run (deterministic only -- no AI)
            run = AnalysisRunService.create_run(
                request=req,
                run_type=AnalysisRunType.RECOMMENDATION,
                triggered_by=user,
            )

            RecommendationService.run_recommendation(
                req, run,
                use_ai=False,
                request_user=user,
            )

            # 3. Fetch persisted result (source of truth)
            # RecommendationResult links via run FK (not request directly)
            from apps.procurement.models import RecommendationResult
            latest = (
                RecommendationResult.objects
                .filter(run=run)
                .order_by("-created_at")
                .first()
            )
            if not latest:
                raise RuntimeError("No RecommendationResult persisted after run")

            confidence = float(latest.confidence_score or 0.0)
            raw_option = latest.recommended_option or ""

            # 4. Extract system type code
            #    Primary: output_payload_json["system_type_code"]
            payload = latest.output_payload_json or {}
            got_code = payload.get("system_type_code", "") or ""
            if not got_code:
                # Fallback: scan known codes in the verbose recommended_option text
                _KNOWN_CODES = [
                    "FCU_CHILLED_WATER", "VRF_SYSTEM", "PACKAGED_DX_UNIT",
                    "SPLIT_SYSTEM", "CASSETTE_SPLIT", "CHILLER_PLANT",
                ]
                for c in _KNOWN_CODES:
                    if c.replace("_", " ").lower() in raw_option.lower() or c.lower() in raw_option.lower():
                        got_code = c
                        break
                if not got_code:
                    got_code = raw_option[:30]

            # 5. Extract primary rule from reasoning_details_json
            rule_fired = "-"
            details = latest.reasoning_details_json or {}
            rules_list = details.get("rules_fired", [])
            if rules_list:
                rule_fired = rules_list[0]
            else:
                rule_fired = details.get("source", payload.get("rule_code", "-"))

            # 6. Match: exact code OR known aliases
            ALIASES = {
                "VRF_SYSTEM": "VRF",       "VRF": "VRF_SYSTEM",
                "SPLIT_SYSTEM": "SPLIT_AC", "SPLIT_AC": "SPLIT_SYSTEM",
                "FCU_CHILLED_WATER": "FCU", "FCU": "FCU_CHILLED_WATER",
                "PACKAGED_DX_UNIT": "PACKAGED_DX", "PACKAGED_DX": "PACKAGED_DX_UNIT",
                "CHILLER_PLANT": "CHILLER", "CHILLER": "CHILLER_PLANT",
                "CASSETTE_SPLIT": "CASSETTE", "CASSETTE": "CASSETTE_SPLIT",
            }
            got_up = got_code.upper()
            exp_up = expected.upper()
            match = (
                got_up == exp_up
                or ALIASES.get(got_up) == exp_up
                or got_up == ALIASES.get(exp_up)
            )

            status = "PASS" if match else "FAIL"
            if status == "PASS":
                self.stdout.write(self.style.SUCCESS(
                    f"     -> {got_code} (conf={confidence:.2f}) [{rule_fired}]  PASS"
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"     -> GOT: {got_code} | EXPECTED: {expected} (conf={confidence:.2f})  FAIL"
                ))
                self.stdout.write(
                    f"        rules_fired: {', '.join(rules_list[:4]) or 'none'}"
                )
                self.stdout.write(
                    f"        raw_option : {raw_option[:80]}"
                )

            return {
                "idx": idx,
                "expected": expected,
                "got": got_code if got_code else "NONE",
                "confidence": confidence,
                "rule": rule_fired,
                "status": status,
            }

        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"     ERROR: {exc}"))
            return {
                "idx": idx, "expected": expected, "got": f"ERROR: {exc!s:.40}",
                "confidence": 0.0, "rule": "-", "status": "FAIL",
            }

    # ------------------------------------------------------------------
    def _resolve_user(self, email: str):
        if email:
            try:
                return User.objects.get(email=email)
            except User.DoesNotExist:
                available = list(
                    User.objects.filter(is_active=True).values_list("email", flat=True)[:10]
                )
                raise CommandError(
                    f"User '{email}' not found. Available: {available}\n"
                    f"Use --user <email> to specify."
                )
        # Default: first superuser
        su = User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
        if su:
            return su
        # Fallback: any active user
        user = User.objects.filter(is_active=True).order_by("pk").first()
        if user:
            return user
        raise CommandError(
            "No active users found. Create one first with: python manage.py createsuperuser"
        )
