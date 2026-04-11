"""Management command to seed the 10 official HVACRecommendationRule records."""
from __future__ import annotations

from django.core.management.base import BaseCommand

# (rule_code, rule_name,
#  country_filter, city_filter, store_type_filter,
#  area_sq_ft_min, area_sq_ft_max, ambient_temp_min_c,
#  budget_level_filter, energy_priority_filter,
#  recommended_system, alternate_system,
#  rationale, priority)
# Priority ordering (lower number = evaluated first, first match wins):
#   10  R7  City-specific rule (Dubai UAE)               -- most specific
#   20  R8  City + Country rule (Riyadh KSA)             -- very specific
#   30  R3  Country-level rule (GCC standalone, high EE) -- medium specificity
#   40  R4  Country-level rule (GCC standalone, low budget)
#   50  R5  Generic condition (mid-size, hot, low budget)
#   60  R6  Generic condition (mid-size, hot, med budget)
#   70  R2  Small-store fallback
#   80  R1  MALL generic (chiller)                       -- moved lower so city/
#                                                           country rules win first
#   90  R9  Extreme ambient temperature fallback
#  999  R10 Default fallback
RULES = [
    # -------------------------------------------------------------------------
    # Priority 10 -- most specific: city-level (Dubai, UAE)
    # -------------------------------------------------------------------------
    (
        "R7",
        "Dubai UAE -- large, extreme heat, high energy priority",
        "UAE", "Dubai", "",
        3000.0, None, 45.0,
        "", "HIGH",
        "VRF", "",
        "Dubai sites with large area and extreme heat benefit most from VRF inverter "
        "efficiency where energy efficiency is the priority.",
        10,
    ),
    # -------------------------------------------------------------------------
    # Priority 20 -- very specific: city + country (Riyadh, KSA)
    # -------------------------------------------------------------------------
    (
        "R8",
        "Riyadh KSA -- large, extreme heat",
        "KSA", "Riyadh", "",
        3000.0, None, 45.0,
        "", "",
        "PACKAGED_DX", "VRF",
        "Riyadh sites in extreme heat with large area: Packaged DX is the primary "
        "recommendation for reliability; VRF is a viable high-efficiency alternative.",
        20,
    ),
    # -------------------------------------------------------------------------
    # Priority 30 -- country-level: GCC standalone, extreme heat, high EE
    # -------------------------------------------------------------------------
    (
        "R3",
        "GCC standalone large -- extreme heat, high energy priority",
        "UAE|KSA|QATAR", "", "STANDALONE",
        5000.0, None, 45.0,
        "", "HIGH",
        "VRF", "",
        "Large standalone sites in extreme-heat GCC climates (UAE / KSA / Qatar) "
        "where energy efficiency is paramount benefit from VRF inverter technology "
        "and staged compressor capacity.",
        30,
    ),
    # -------------------------------------------------------------------------
    # Priority 40 -- country-level: GCC standalone, extreme heat, low budget
    # -------------------------------------------------------------------------
    (
        "R4",
        "GCC standalone large -- extreme heat, low/medium budget",
        "UAE|KSA|QATAR", "", "STANDALONE",
        5000.0, None, 45.0,
        "LOW_MEDIUM", "LOW_MEDIUM",
        "PACKAGED_DX", "",
        "High ambient temperature in GCC demands robust packaged DX. "
        "Low/medium budget rules out premium VRF investment.",
        40,
    ),
    # -------------------------------------------------------------------------
    # Priorities 50-60 -- generic conditions (mid-size, hot climate)
    # -------------------------------------------------------------------------
    (
        "R5",
        "Mid-size -- hot climate, low budget",
        "", "", "",
        2000.0, 5000.0, 40.0,
        "LOW", "",
        "PACKAGED_DX", "",
        "Budget-constrained mid-size installations in hot climates are best served "
        "by standard packaged DX units with low first cost and wide service availability.",
        50,
    ),
    (
        "R6",
        "Mid-size -- hot climate, medium/high budget, high energy priority",
        "", "", "",
        2000.0, 5000.0, 40.0,
        "MEDIUM_HIGH", "HIGH",
        "VRF", "",
        "Mid-size sites with investment budget and high efficiency focus can leverage "
        "VRF part-load efficiency to reduce operating costs over the lifecycle.",
        60,
    ),
    # -------------------------------------------------------------------------
    # Priority 70 -- small-store fallback
    # -------------------------------------------------------------------------
    (
        "R2",
        "Small footprint -- under 2000 sq ft",
        "", "", "",
        None, 2000.0, None,
        "", "",
        "SPLIT_AC", "",
        "Spaces under 2000 sq ft rarely justify complex systems. "
        "A split AC (wall-mounted or multi-split) provides adequate capacity "
        "with minimal installation complexity.",
        70,
    ),
    # -------------------------------------------------------------------------
    # Priority 80 -- MALL generic (chiller from landlord plant)
    # INTENTIONALLY placed AFTER all city/country-specific rules so that a
    # Riyadh KSA mall (R8, priority 20) or a Dubai UAE mall (R7, priority 10)
    # are resolved before this catch-all fires.
    # -------------------------------------------------------------------------
    (
        "R1",
        "Mall -- any configuration",
        "", "", "MALL",
        None, None, None,
        "", "",
        "CHILLER", "FCU",
        "Mall tenancies use the landlord-provided chilled-water plant. "
        "Fan coil units (FCUs) distribute chilled water from the central plant. "
        "City- or country-specific rules (R7, R8) override this for markets where "
        "packaged DX is the norm (e.g. Riyadh KSA).",
        80,
    ),
    # -------------------------------------------------------------------------
    # Priority 90 -- extreme ambient temperature (any store type)
    # -------------------------------------------------------------------------
    (
        "R9",
        "Extreme ambient temperature -- any configuration",
        "", "", "",
        None, None, 50.0,
        "", "",
        "PACKAGED_DX", "",
        "Ambient temperatures at or above 50 C require heavy-duty packaged units rated "
        "for extreme climates. Standard split and VRF equipment may not be rated for "
        "sustained operation above this threshold.",
        90,
    ),
    # -------------------------------------------------------------------------
    # Priority 999 -- last-resort default
    # -------------------------------------------------------------------------
    (
        "R10",
        "Default fallback -- any configuration",
        "", "", "",
        None, None, None,
        "", "",
        "PACKAGED_DX", "",
        "No more-specific rule matched. Packaged DX is the conservative, "
        "widely-supported default recommendation across most climates, "
        "store types, and budget levels.",
        999,
    ),
]


class Command(BaseCommand):
    help = (
        "Seed the 10 official HVACRecommendationRule records "
        "(idempotent -- updates on rule_code conflict; removes any extra rules)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            default=False,
            help="Delete ALL existing HVACRecommendationRule records before seeding.",
        )

    def handle(self, *args, **options):
        from apps.procurement.models import HVACRecommendationRule

        if options["flush"]:
            deleted, _ = HVACRecommendationRule.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Flushed {deleted} existing rules."))

        kept_codes = set()
        created_count = 0
        updated_count = 0

        for row in RULES:
            (
                rule_code, rule_name,
                country_filter, city_filter, store_type_filter,
                area_sq_ft_min, area_sq_ft_max, ambient_temp_min_c,
                budget_level_filter, energy_priority_filter,
                recommended_system, alternate_system,
                rationale, priority,
            ) = row

            obj, created = HVACRecommendationRule.objects.update_or_create(
                rule_code=rule_code,
                defaults={
                    "rule_name": rule_name,
                    "country_filter": country_filter,
                    "city_filter": city_filter,
                    "store_type_filter": store_type_filter,
                    "area_sq_ft_min": area_sq_ft_min,
                    "area_sq_ft_max": area_sq_ft_max,
                    "ambient_temp_min_c": ambient_temp_min_c,
                    "budget_level_filter": budget_level_filter,
                    "energy_priority_filter": energy_priority_filter,
                    "recommended_system": recommended_system,
                    "alternate_system": alternate_system,
                    "rationale": rationale,
                    "priority": priority,
                    "is_active": True,
                },
            )
            kept_codes.add(rule_code)
            if created:
                created_count += 1
            else:
                updated_count += 1

        # Remove any old rules not in the new set
        extra_qs = HVACRecommendationRule.objects.exclude(rule_code__in=kept_codes)
        extra_count = extra_qs.count()
        if extra_count:
            extra_qs.delete()
            self.stdout.write(
                self.style.WARNING(
                    f"Removed {extra_count} obsolete rule(s) not in the current set."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"HVAC rules seed complete: {created_count} created, "
                f"{updated_count} updated, {extra_count} removed."
            )
        )
