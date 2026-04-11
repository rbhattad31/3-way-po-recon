"""Management command: seed_fashion_studio
=========================================
Removes ALL procurement requests except the Fashion Studio, then
re-creates the Fashion Studio with full rich attributes and generates
the recommendation so the workspace shows results.

Usage:
    python manage.py seed_fashion_studio
    python manage.py seed_fashion_studio --user admin@example.com
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core.enums import ProcurementRequestType
from apps.procurement.models import HVACStoreProfile, ProcurementRequest
from apps.procurement.services.request_service import (
    AttributeService,
    ProcurementRequestService,
)

User = get_user_model()

# ---------------------------------------------------------------------------
# Fashion Studio record definition
# ---------------------------------------------------------------------------
STORE_ID  = "UAE-DXB-ZARA-FASHION-001"
REQ_TITLE = "Zara Dubai Mall -- Fashion Studio HVAC Upgrade 2026"

STORE_PROFILE = {
    "store_id":                   STORE_ID,
    "brand":                      "Zara",
    "country":                    "UAE",
    "city":                       "Dubai",
    "store_type":                 "MALL",
    "store_format":               "FASHION_RETAIL",
    "area_sqft":                  7200,
    "ceiling_height_ft":          14.0,
    "operating_hours":            "10 AM - 12 AM",
    "footfall_category":          "HIGH",
    "ambient_temp_max":           46.0,
    "humidity_level":             "MEDIUM",
    "dust_exposure":              "LOW",
    "heat_load_category":         "HIGH",
    "fresh_air_requirement":      "HIGH",
    "landlord_constraints":       "Mall CW not available at this unit; no roof access",
    "existing_hvac_type":         "VRF/VRV",
    "budget_level":               "HIGH",
    "energy_efficiency_priority": "HIGH",
}


def _attr(code, label, data_type, value):
    """Return an attribute dict in the format expected by AttributeService."""
    return {
        "attribute_code":  code,
        "attribute_label": label,
        "data_type":       data_type,
        "value_text":      "" if data_type == "NUMBER" else str(value),
        "value_number":    float(value) if data_type == "NUMBER" else None,
    }


REQUEST_ATTRIBUTES = [
    _attr("store_id",                   "Store ID",                    "TEXT",   STORE_ID),
    _attr("brand",                      "Brand",                       "TEXT",   "Zara"),
    _attr("country",                    "Country",                     "TEXT",   "UAE"),
    _attr("city",                       "City",                        "TEXT",   "Dubai"),
    _attr("store_type",                 "Store Type",                  "TEXT",   "MALL"),
    _attr("store_format",               "Store Format",                "TEXT",   "FASHION_RETAIL"),
    _attr("area_sqft",                  "Area (sq ft)",                "NUMBER", 7200),
    _attr("ceiling_height_ft",          "Ceiling Height (ft)",         "NUMBER", 14),
    _attr("operating_hours",            "Operating Hours",             "TEXT",   "10 AM - 12 AM"),
    _attr("footfall_category",          "Footfall Category",           "TEXT",   "HIGH"),
    _attr("ambient_temp_max",           "Ambient Temp Max (C)",        "NUMBER", 46),
    _attr("humidity_level",             "Humidity Level",              "TEXT",   "MEDIUM"),
    _attr("dust_exposure",              "Dust Exposure",               "TEXT",   "LOW"),
    _attr("heat_load_category",         "Heat Load Category",          "TEXT",   "HIGH"),
    _attr("fresh_air_requirement",      "Fresh Air Requirement",       "TEXT",   "HIGH"),
    _attr("landlord_constraints",       "Landlord Constraints",        "TEXT",   "Mall CW not available at this unit; no roof access"),
    _attr("existing_hvac_type",         "Existing HVAC Type",          "TEXT",   "VRF/VRV"),
    _attr("budget_level",               "Budget Level",                "TEXT",   "HIGH"),
    _attr("energy_efficiency_priority", "Energy Efficiency Priority",  "TEXT",   "HIGH"),
    _attr("maintenance_priority",       "Maintenance Priority",        "TEXT",   "HIGH"),
    _attr("preferred_oems",             "Preferred OEMs",              "TEXT",   "Daikin, Mitsubishi Electric, Hitachi"),
    _attr("required_standards",         "Required Standards",          "TEXT",   "DEWA Grade A energy compliance, R32 refrigerant mandatory"),
    _attr("chilled_water_available",    "Chilled Water Available",     "TEXT",   "NO"),
    _attr("noise_sensitivity",          "Noise Sensitivity",           "TEXT",   "HIGH"),
    _attr("installation_timeline",      "Installation Timeline",       "TEXT",   "Q3 2026 -- before peak season"),
]


class Command(BaseCommand):
    help = (
        "Delete all procurement requests except Fashion Studio, "
        "re-seed Fashion Studio with full attributes, and generate recommendation."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Email of user to assign as creator.",
        )

    def handle(self, *args, **options):
        user = self._resolve_user(options.get("user"))
        self.stdout.write(f"Using user: {user.email}\n")

        # ── 1. Delete all existing requests ────────────────────────────────
        total = ProcurementRequest.objects.count()
        ProcurementRequest.objects.all().delete()
        self.stdout.write(self.style.WARNING(
            f"Deleted {total} existing request(s)."
        ))

        # ── 2. Upsert store profile ─────────────────────────────────────────
        profile, p_created = HVACStoreProfile.objects.update_or_create(
            store_id=STORE_PROFILE["store_id"],
            defaults={k: v for k, v in STORE_PROFILE.items() if k != "store_id"},
        )
        action = "CREATED" if p_created else "UPDATED"
        self.stdout.write(self.style.SUCCESS(
            f"  Store profile {action}: {profile.store_id} ({profile.brand}, {profile.city})"
        ))

        # ── 3. Create procurement request ───────────────────────────────────
        try:
            req = ProcurementRequestService.create_request(
                title=REQ_TITLE,
                description=(
                    "HVAC upgrade for Zara flagship fashion retail store at Dubai Mall (Level 2). "
                    "7,200 sqft two-floor layout with high ceiling clearance. "
                    "Existing 11-yr-old VRF (R22 refrigerant) requires full replacement. "
                    "Mall chilled water is NOT available at this unit -- VRF or packaged DX only. "
                    "New system must use R32 refrigerant, achieve DEWA Grade A rating, "
                    "and support heat-recovery for back-of-house zones. "
                    "Low noise (<38 dB) critical for premium customer experience. "
                    "Dubai Mall landlord does not permit outdoor units on the roof -- "
                    "all ODUs must be in designated ground-level plantroom."
                ),
                domain_code="HVAC",
                schema_code="HVAC_PRODUCT_SELECTION_V1",
                request_type=ProcurementRequestType.BOTH,
                priority="HIGH",
                geography_country="UAE",
                geography_city="Dubai",
                currency="AED",
                created_by=user,
                attributes=REQUEST_ATTRIBUTES,
            )
            # Promote to READY
            req.status = "READY"
            req.save(update_fields=["status"])

            self.stdout.write(self.style.SUCCESS(
                f"  Request CREATED [pk={req.pk}]: {req.title}"
            ))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"  FAILED to create request: {exc}"))
            raise

        # ── 4. Generate recommendation ──────────────────────────────────────
        from apps.core.enums import AnalysisRunStatus, AnalysisRunType
        from apps.procurement.services.analysis_run_service import AnalysisRunService
        from apps.procurement.tasks import run_analysis_task

        try:
            run = AnalysisRunService.create_run(
                request=req,
                run_type=AnalysisRunType.RECOMMENDATION,
                triggered_by=user,
            )
            self.stdout.write(
                f"  Analysis run created (pk={run.pk}) -- running recommendation..."
            )
            # CELERY_TASK_ALWAYS_EAGER=True -- executes synchronously
            result = run_analysis_task.delay(run.pk)
            self.stdout.write(self.style.SUCCESS(
                "  Recommendation generated!"
            ))
            if isinstance(result, dict):
                self.stdout.write(
                    f"  Recommended option : {result.get('recommended_option', 'N/A')}\n"
                    f"  Confidence         : {result.get('confidence', 'N/A')}"
                )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(
                f"  Could not generate recommendation: {exc}"
            ))

        self.stdout.write("\n" + self.style.SUCCESS("All done!"))
        self.stdout.write(
            f"\n  Workspace -> http://127.0.0.1:8000/procurement/requests/{req.pk}/workspace/\n"
            f"  Store ID  -> {STORE_ID}\n"
            f"  Country   -> UAE  |  City -> Dubai\n"
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
        raise CommandError("No users found. Create one: python manage.py createsuperuser")
