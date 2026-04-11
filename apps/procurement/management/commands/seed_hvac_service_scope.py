"""Management command: seed_hvac_service_scope
==============================================
Seeds 5 HVAC system service scope rows into HVACServiceScope:

  1. Split AC
  2. Packaged Unit (Rooftop)
  3. VRF System
  4. Chilled Water System
  5. Ducting & Accessories

Usage
-----
    python manage.py seed_hvac_service_scope
    python manage.py seed_hvac_service_scope --clear    # delete all existing rows first
    python manage.py seed_hvac_service_scope --force    # update if already present
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.procurement.models import HVACServiceScope


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SCOPE_ROWS = [
    {
        "system_type": "SPLIT_AC",
        "display_name": "Split AC",
        "equipment_scope": "Indoor & Outdoor units",
        "installation_services": "Mounting, fixing, alignment",
        "piping_ducting": "Refrigerant copper piping",
        "electrical_works": "Power cabling, isolators",
        "controls_accessories": "Thermostat, remote",
        "testing_commissioning": "Cooling test, performance check",
        "sort_order": 10,
        "is_active": True,
    },
    {
        "system_type": "PACKAGED_DX",
        "display_name": "Packaged Unit (Rooftop)",
        "equipment_scope": "Packaged unit",
        "installation_services": "Unit placement, vibration isolation",
        "piping_ducting": "GI ducting, insulation",
        "electrical_works": "Power supply, panel connection",
        "controls_accessories": "Dampers, diffusers, grills",
        "testing_commissioning": "Air balancing, system testing",
        "sort_order": 20,
        "is_active": True,
    },
    {
        "system_type": "VRF",
        "display_name": "VRF System",
        "equipment_scope": "Outdoor + multiple indoor units",
        "installation_services": "Installation of indoor units",
        "piping_ducting": "Refrigerant piping network",
        "electrical_works": "Cabling, communication wiring",
        "controls_accessories": "Central controller, BMS integration",
        "testing_commissioning": "System commissioning, gas charging, performance testing",
        "sort_order": 30,
        "is_active": True,
    },
    {
        "system_type": "CHILLER",
        "display_name": "Chilled Water System",
        "equipment_scope": "Chillers, FCU/AHU units",
        "installation_services": "Equipment installation",
        "piping_ducting": "Chilled water piping, insulation",
        "electrical_works": "Pump wiring, control panels",
        "controls_accessories": "Valves, sensors, BMS",
        "testing_commissioning": "Water balancing, pressure testing, commissioning",
        "sort_order": 40,
        "is_active": True,
    },
    {
        "system_type": "DUCTING",
        "display_name": "Ducting & Accessories",
        "equipment_scope": "Diffusers, grills, louvers",
        "installation_services": "Fixing & alignment",
        "piping_ducting": "GI ducts, flexible ducts",
        "electrical_works": "Minimal (if motorized dampers)",
        "controls_accessories": "VCD, fire dampers",
        "testing_commissioning": "Airflow testing",
        "sort_order": 50,
        "is_active": True,
    },
]


class Command(BaseCommand):
    help = "Seed HVAC service scope matrix rows into HVACServiceScope."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing HVACServiceScope rows before seeding.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Update existing rows if they already exist (default: skip).",
        )

    def handle(self, *args, **options):
        clear = options["clear"]
        force = options["force"]

        if clear:
            deleted, _ = HVACServiceScope.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"  Deleted {deleted} existing rows."))

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for row in SCOPE_ROWS:
            system_type = row["system_type"]
            existing = HVACServiceScope.objects.filter(system_type=system_type).first()

            if existing:
                if force:
                    for field, value in row.items():
                        setattr(existing, field, value)
                    existing.save()
                    updated_count += 1
                    self.stdout.write(f"  Updated: {system_type}")
                else:
                    skipped_count += 1
                    self.stdout.write(f"  Skipped (already exists): {system_type}")
            else:
                HVACServiceScope.objects.create(**row)
                created_count += 1
                self.stdout.write(f"  Created: {system_type}")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created: {created_count}  Updated: {updated_count}  Skipped: {skipped_count}"
        ))
