"""Management command: seed_one_test_request
=============================================
Creates exactly ONE test HVAC procurement request + its matching store profile
so you can verify the form, city dropdown, workspace, and recommendation flow.

Usage:
    python manage.py seed_one_test_request
    python manage.py seed_one_test_request --clear   # delete & re-create
    python manage.py seed_one_test_request --user admin@example.com
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core.enums import ProcurementRequestType
from apps.procurement.models import HVACStoreProfile, ProcurementRequest
from apps.procurement.services.request_service import ProcurementRequestService

User = get_user_model()

# ---------------------------------------------------------------------------
# Single test record
# ---------------------------------------------------------------------------
STORE_ID   = "UAE-DXB-SPLASH-TEST-001"
REQ_TITLE  = "TEST -- Splash Dubai Festival City VRF Upgrade"

STORE_PROFILE = {
    "store_id":                   STORE_ID,
    "brand":                      "Splash",
    "country":                    "UAE",
    "city":                       "Dubai",
    "store_type":                 "MALL",
    "store_format":               "RETAIL",
    "area_sqft":                  8500,
    "ceiling_height_ft":          13.0,
    "operating_hours":            "10 AM - 11 PM",
    "footfall_category":          "HIGH",
    "ambient_temp_max":           48.0,
    "humidity_level":             "HIGH",
    "dust_exposure":              "LOW",
    "heat_load_category":         "HIGH",
    "fresh_air_requirement":      "MEDIUM",
    "landlord_constraints":       "No outdoor units on roof",
    "existing_hvac_type":         "VRF/VRV",
    "budget_level":               "MEDIUM",
    "energy_efficiency_priority": "HIGH",
}

REQUEST_DATA = {
    "title":            REQ_TITLE,
    "description":      (
        "Test request for verifying the HVAC procurement form and city dropdown. "
        "Splash Dubai Festival City -- 790 sqm retail floor. "
        "Existing 10-year-old VRF (R22) requires full replacement. "
        "Mall provides 3-phase power only; no chilled water at this unit. "
        "New system must be R32, with heat-recovery option."
    ),
    "request_type":     ProcurementRequestType.BOTH,
    "priority":         "HIGH",
    "geography_country":"UAE",
    "geography_city":   "Dubai",
    "currency":         "AED",
    "status":           "READY",
}

def _attr(code, label, data_type, value):
    """Build an attribute dict in the format expected by AttributeService.bulk_set_attributes."""
    d = {
        "attribute_code":  code,
        "attribute_label": label,
        "data_type":       data_type,
        "value_text":      "" if data_type == "NUMBER" else str(value),
        "value_number":    float(value) if data_type == "NUMBER" else None,
    }
    return d


REQUEST_ATTRIBUTES = [
    _attr("store_id",                  "Store ID",                    "TEXT",   STORE_ID),
    _attr("brand",                     "Brand",                       "TEXT",   "Splash"),
    _attr("country",                   "Country",                     "TEXT",   "UAE"),
    _attr("city",                      "City",                        "TEXT",   "Dubai"),
    _attr("store_type",                "Store Type",                  "TEXT",   "MALL"),
    _attr("store_format",              "Store Format",                "TEXT",   "RETAIL"),
    _attr("area_sqft",                 "Area (sq ft)",                "NUMBER", 8500),
    _attr("ceiling_height_ft",         "Ceiling Height (ft)",         "NUMBER", 13),
    _attr("operating_hours",           "Operating Hours",             "TEXT",   "10 AM - 11 PM"),
    _attr("footfall_category",         "Footfall Category",           "TEXT",   "HIGH"),
    _attr("ambient_temp_max",          "Ambient Temp Max (C)",        "NUMBER", 48),
    _attr("humidity_level",            "Humidity Level",              "TEXT",   "HIGH"),
    _attr("dust_exposure",             "Dust Exposure",               "TEXT",   "LOW"),
    _attr("heat_load_category",        "Heat Load Category",          "TEXT",   "HIGH"),
    _attr("fresh_air_requirement",     "Fresh Air Requirement",       "TEXT",   "MEDIUM"),
    _attr("landlord_constraints",      "Landlord Constraints",        "TEXT",   "No outdoor units on roof"),
    _attr("existing_hvac_type",        "Existing HVAC Type",          "TEXT",   "VRF/VRV"),
    _attr("budget_level",              "Budget Level",                "TEXT",   "MEDIUM"),
    _attr("energy_efficiency_priority","Energy Efficiency Priority",  "TEXT",   "HIGH"),
    _attr("maintenance_priority",      "Maintenance Priority",        "TEXT",   "HIGH"),
    _attr("preferred_oems",            "Preferred OEMs",              "TEXT",   "Daikin, Mitsubishi Electric"),
    _attr("required_standards",        "Required Standards",          "TEXT",   "DEWA Grade A energy compliance"),
]


class Command(BaseCommand):
    help = "Seed ONE test HVAC procurement request + store profile for form verification."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete the existing test record before re-creating it.",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Email of user to assign as creator.",
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options.get("user"))
        self.stdout.write(f"Using user: {user.email}")

        if options["clear"]:
            deleted, _ = ProcurementRequest.objects.filter(title=REQ_TITLE).delete()
            HVACStoreProfile.objects.filter(store_id=STORE_ID).delete()
            self.stdout.write(self.style.WARNING(
                f"Cleared {deleted} existing test request(s) and store profile."
            ))

        # ── 1. Upsert store profile ─────────────────────────────────────────
        profile, p_created = HVACStoreProfile.objects.update_or_create(
            store_id=STORE_PROFILE["store_id"],
            defaults={k: v for k, v in STORE_PROFILE.items() if k != "store_id"},
        )
        action = "CREATED" if p_created else "UPDATED"
        self.stdout.write(self.style.SUCCESS(
            f"  Store profile {action}: {profile.store_id} ({profile.brand}, {profile.city})"
        ))

        # ── 2. Create procurement request ───────────────────────────────────
        if ProcurementRequest.objects.filter(title=REQ_TITLE).exists():
            req = ProcurementRequest.objects.get(title=REQ_TITLE)
            self.stdout.write(self.style.WARNING(
                f"  Request already exists (pk={req.pk}). Use --clear to re-seed."
            ))
        else:
            try:
                req = ProcurementRequestService.create_request(
                    title=REQUEST_DATA["title"],
                    description=REQUEST_DATA["description"],
                    domain_code="HVAC",
                    schema_code="HVAC_PRODUCT_SELECTION_V1",
                    request_type=REQUEST_DATA["request_type"],
                    priority=REQUEST_DATA["priority"],
                    geography_country=REQUEST_DATA["geography_country"],
                    geography_city=REQUEST_DATA["geography_city"],
                    currency=REQUEST_DATA["currency"],
                    created_by=user,
                    attributes=REQUEST_ATTRIBUTES,
                )
                # Promote status from DRAFT to READY
                if REQUEST_DATA.get("status", "DRAFT") != "DRAFT":
                    req.status = REQUEST_DATA["status"]
                    req.save(update_fields=["status"])

                self.stdout.write(self.style.SUCCESS(
                    f"  Request CREATED [pk={req.pk}]: {req.title}"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  FAILED to create request: {exc}"))
                return

        # ---- 3. Generate recommendation run if needed --------------------------------
        from apps.core.enums import AnalysisRunStatus, AnalysisRunType
        from apps.procurement.services.analysis_run_service import AnalysisRunService
        from apps.procurement.tasks import run_analysis_task

        existing_run = req.analysis_runs.filter(
            run_type=AnalysisRunType.RECOMMENDATION,
            status=AnalysisRunStatus.COMPLETED,
        ).first()

        if existing_run:
            self.stdout.write(self.style.WARNING(
                f"  Recommendation already exists (run_id={existing_run.run_id}). Skipping."
            ))
        else:
            try:
                run = AnalysisRunService.create_run(
                    request=req,
                    run_type=AnalysisRunType.RECOMMENDATION,
                    triggered_by=user,
                )
                self.stdout.write(
                    f"  Analysis run created (pk={run.pk}) -- running recommendation..."
                )
                # CELERY_TASK_ALWAYS_EAGER=True means this executes synchronously
                run_analysis_task.delay(run.pk)
                self.stdout.write(self.style.SUCCESS(
                    "  Recommendation generated successfully!"
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"  Could not generate recommendation: {exc}"
                ))

        self.stdout.write("\n" + self.style.SUCCESS("Done."))
        self.stdout.write(
            f"  -> Visit http://127.0.0.1:8000/procurement/ to see the request.\n"
            f"  -> Workspace: http://127.0.0.1:8000/procurement/requests/{req.pk}/workspace/\n"
            f"  -> Store ID '{STORE_ID}' is now available in the form dropdown.\n"
            f"  -> Country = UAE  |  City = Dubai\n"
        )

    def _resolve_user(self, email: str | None):
        if email:
            try:
                return User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f"User with email '{email}' not found.")
        user = User.objects.filter(is_superuser=True).order_by("pk").first()
        if user:
            return user
        user = User.objects.filter(is_active=True).order_by("pk").first()
        if user:
            return user
        raise CommandError(
            "No users found. Create one first: python manage.py createsuperuser"
        )
