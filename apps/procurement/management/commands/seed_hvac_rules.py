"""Management command to seed HVACRecommendationRule records."""
from __future__ import annotations

from django.core.management.base import BaseCommand

RULES = [
    # (rule_code, rule_name, store_type_filter,
    #  area_sq_ft_min, area_sq_ft_max, ambient_temp_min_c,
    #  budget_level_filter, energy_priority_filter,
    #  recommended_system, alternate_system,
    #  rationale, priority)
    (
        "R1", "Mall -- any configuration", "MALL",
        None, None, None, "", "",
        "CHILLER", "FCU",
        "Mall tenancies always use the landlord-provided chilled-water plant. "
        "Fan coil units (FCUs) distribute chilled water from the central plant.",
        10,
    ),
    (
        "R2", "Small footprint -- under 2000 sq ft", "",
        None, 2000.0, None, "", "",
        "SPLIT_AC", "CASSETTE",
        "Spaces under 2000 sq ft rarely justify complex systems. "
        "A split-AC (wall-mounted or multi-split) provides adequate capacity with "
        "minimal installation complexity.",
        20,
    ),
    (
        "R3", "Standalone large -- extreme heat, high energy priority", "STANDALONE",
        5000.0, None, 45.0, "", "HIGH",
        "VRF", "PACKAGED_DX",
        "Large standalone sites in extreme-heat climates where energy efficiency "
        "is paramount benefit from VRF inverter technology and staged compressor capacity.",
        30,
    ),
    (
        "R4", "Standalone large -- extreme heat, standard budget", "STANDALONE",
        5000.0, None, 45.0, "LOW_MEDIUM", "",
        "PACKAGED_DX", "VRF",
        "High ambient temperature demands robust, proven packaged DX. "
        "VRF is viable if CAPEX permits phased investment.",
        40,
    ),
    (
        "R5", "Mid-size -- low budget", "",
        2000.0, 5000.0, None, "LOW", "",
        "PACKAGED_DX", "",
        "Budget-constrained mid-size installations are best served by "
        "standard packaged DX rooftop / split units with low first cost and wide service availability.",
        50,
    ),
    (
        "R6", "Mid-size -- medium-high budget, high energy priority", "",
        2000.0, 5000.0, None, "MEDIUM_HIGH", "HIGH",
        "VRF", "PACKAGED_DX",
        "Mid-size sites with investment budget and high efficiency focus "
        "can leverage VRF part-load efficiency to reduce operating costs over the lifecycle.",
        60,
    ),
    (
        "R7", "Mid-size -- medium-high budget, standard energy priority", "",
        2000.0, 5000.0, None, "MEDIUM_HIGH", "LOW_MEDIUM",
        "PACKAGED_DX", "VRF",
        "Higher budget does not guarantee VRF priority unless energy efficiency is the driver. "
        "Packaged DX is reliable and easier to maintain.",
        70,
    ),
    (
        "R8", "Hospital -- any size", "HOSPITAL",
        None, None, None, "", "",
        "VRF", "CHILLER",
        "Hospital environments require precise temperature/humidity control, "
        "zoning flexibility, and low-noise operation. "
        "VRF systems with hot-gas bypass and humidity controls are preferred. "
        "Chiller-based FCU systems are the alternative for large facilities.",
        80,
    ),
    (
        "R9", "Warehouse -- large footprint", "WAREHOUSE",
        10000.0, None, None, "", "",
        "PACKAGED_DX", "",
        "Large warehouses have high open volumes, significant solar and occupancy loads, "
        "and simple zoning requirements. "
        "Packaged DX rooftop units with direct-drive fans are cost-effective and maintainable.",
        90,
    ),
    (
        "R10", "Warehouse -- small to mid footprint", "WAREHOUSE",
        None, 10000.0, None, "", "",
        "SPLIT_AC", "PACKAGED_DX",
        "Smaller warehouses with modest cooling loads can be served by "
        "multi-split or cassette units without the overhead of rooftop plant.",
        100,
    ),
    (
        "R11", "Office -- small, any budget", "OFFICE",
        None, 3000.0, None, "", "",
        "SPLIT_AC", "CASSETTE",
        "Small office spaces have limited zone diversity. "
        "Variable-speed split-ACs balance comfort, energy efficiency, and low maintenance.",
        110,
    ),
    (
        "R12", "Office -- medium, high budget and energy priority", "OFFICE",
        3000.0, 15000.0, None, "HIGH", "HIGH",
        "VRF", "CHILLER",
        "Medium-to-large offices with premium budget and strong sustainability targets "
        "are ideal candidates for VRF with individual room control, occupancy sensing, and "
        "centralised BMS integration.",
        120,
    ),
    (
        "R13", "Office -- medium, standard budget", "OFFICE",
        3000.0, 15000.0, None, "LOW_MEDIUM", "",
        "PACKAGED_DX", "VRF",
        "Most commercial offices can be well-served by packaged DX with VAV distribution "
        "at lower total cost than VRF while meeting standard comfort and code requirements.",
        130,
    ),
    (
        "R14", "Data centre -- any size", "DATA_CENTER",
        None, None, None, "", "",
        "CHILLER", "PACKAGED_DX",
        "Data centres require precision cooling, N+1 redundancy, and high sensible heat ratios. "
        "Chilled-water systems with Computer Room Air Handlers (CRAH) are the industry standard. "
        "Packaged DX CRAC units are suitable for smaller or edge sites.",
        140,
    ),
    (
        "R15", "Hypermarket -- large", "HYPERMARKET",
        20000.0, None, None, "", "",
        "CHILLER", "PACKAGED_DX",
        "Large hypermarkets combine retail, fresh food, and stockroom zones requiring "
        "diverse set-points and high capacity. Central chiller plant with AHUs offers "
        "flexibility and energy scale benefits.",
        150,
    ),
    (
        "R16", "Hypermarket -- small to mid", "HYPERMARKET",
        None, 20000.0, None, "", "",
        "PACKAGED_DX", "VRF",
        "Smaller hypermarket formats can achieve comfortable results with packaged DX "
        "rooftop units serving each zone individually.",
        160,
    ),
    (
        "R17", "Standalone -- moderate climate, high energy priority", "STANDALONE",
        None, None, None, "", "HIGH",
        "VRF", "PACKAGED_DX",
        "Standalone sites in moderate climates with energy efficiency as the primary driver "
        "benefit from VRF part-load performance and multi-zone flexibility.",
        170,
    ),
    (
        "R18", "Standalone -- moderate size, moderate budget", "STANDALONE",
        2000.0, 5000.0, None, "MEDIUM", "",
        "PACKAGED_DX", "SPLIT_AC",
        "Mid-tier standalone retail of moderate size with an average budget is reliably "
        "served by conventional packaged DX or multi-split systems.",
        180,
    ),
    (
        "R19", "FCU -- chiller-connected requirement", "",
        None, None, None, "HIGH", "LOW",
        "FCU", "VRF",
        "When a site has access to a district or campus chilled-water loop but high "
        "energy priority and a tight budget, FCU hook-up minimises CAPEX while "
        "leveraging the central plant efficiency.",
        190,
    ),
    (
        "R20", "Default fallback -- any configuration", "",
        None, None, None, "", "",
        "PACKAGED_DX", "",
        "No more-specific rule matched. Packaged DX is the conservative, widely-supported default "
        "recommendation across most climates, store types, and budget levels.",
        999,
    ),
]


class Command(BaseCommand):
    help = "Seed 20 HVACRecommendationRule records (idempotent -- updates on rule_code conflict)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            default=False,
            help="Delete all existing HVACRecommendationRule records before seeding.",
        )

    def handle(self, *args, **options):
        from apps.procurement.models import HVACRecommendationRule

        if options["flush"]:
            deleted, _ = HVACRecommendationRule.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Flushed {deleted} existing rules."))

        created_count = 0
        updated_count = 0

        for row in RULES:
            (
                rule_code, rule_name, store_type_filter,
                area_sq_ft_min, area_sq_ft_max, ambient_temp_min_c,
                budget_level_filter, energy_priority_filter,
                recommended_system, alternate_system,
                rationale, priority,
            ) = row

            obj, created = HVACRecommendationRule.objects.update_or_create(
                rule_code=rule_code,
                defaults={
                    "rule_name": rule_name,
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
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"HVAC rules seed complete: {created_count} created, {updated_count} updated."
            )
        )
