"""Management command: seed_hvac_store_profiles
================================================
Ensures a baseline set of Landmark Group store profiles exists in the
HVACStoreProfile table so the HVAC request form has pre-populated options
in its Store ID dropdown.

Usage:
    python manage.py seed_hvac_store_profiles
    python manage.py seed_hvac_store_profiles --clear   # delete all & re-seed
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.procurement.models import HVACStoreProfile

# ---------------------------------------------------------------------------
# Default store profiles  (change values to match real Landmark Group data)
# ---------------------------------------------------------------------------
DEFAULT_PROFILES = [
    {
        "store_id": "UAE-DXB-MAX-001",
        "brand": "Max Fashion",
        "country": "UAE",
        "city": "Dubai",
        "store_type": "MALL",
        "store_format": "RETAIL",
        "area_sqft": 12917,
        "ceiling_height_ft": 12.0,
        "operating_hours": "10 AM - 11 PM",
        "footfall_category": "HIGH",
        "ambient_temp_max": 48.0,
        "humidity_level": "HIGH",
        "dust_exposure": "LOW",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "MEDIUM",
        "landlord_constraints": "Mall-managed chilled water system",
        "existing_hvac_type": "VRF/VRV",
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "HIGH",
    },
    {
        "store_id": "UAE-AUH-HC-002",
        "brand": "Home Centre",
        "country": "UAE",
        "city": "Abu Dhabi",
        "store_type": "MALL",
        "store_format": "FURNITURE",
        "area_sqft": 21528,
        "ceiling_height_ft": 14.0,
        "operating_hours": "10 AM - 10 PM",
        "footfall_category": "MEDIUM",
        "ambient_temp_max": 47.0,
        "humidity_level": "HIGH",
        "dust_exposure": "LOW",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "MEDIUM",
        "landlord_constraints": "No outdoor units on roof",
        "existing_hvac_type": "Chilled water interface",
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
    },
    {
        "store_id": "SAU-RUH-SPL-003",
        "brand": "Splash",
        "country": "KSA",
        "city": "Riyadh",
        "store_type": "MALL",
        "store_format": "RETAIL",
        "area_sqft": 8611,
        "ceiling_height_ft": 11.0,
        "operating_hours": "10 AM - 12 AM",
        "footfall_category": "HIGH",
        "ambient_temp_max": 50.0,
        "humidity_level": "LOW",
        "dust_exposure": "HIGH",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "MEDIUM",
        "landlord_constraints": "Mall-managed chilled water system",
        "existing_hvac_type": "Chilled water interface",
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "MEDIUM",
    },
    {
        "store_id": "QAT-DOH-EM-004",
        "brand": "Emax",
        "country": "QATAR",
        "city": "Doha",
        "store_type": "MALL",
        "store_format": "ELECTRONICS",
        "area_sqft": 16146,
        "ceiling_height_ft": 13.0,
        "operating_hours": "10 AM - 10 PM",
        "footfall_category": "HIGH",
        "ambient_temp_max": 46.0,
        "humidity_level": "HIGH",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "HIGH",
        "landlord_constraints": "Condenser units in plant room only",
        "existing_hvac_type": "Chilled water interface",
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
    },
    {
        "store_id": "OMN-MCT-BS-005",
        "brand": "Babyshop",
        "country": "OMAN",
        "city": "Muscat",
        "store_type": "MALL",
        "store_format": "RETAIL",
        "area_sqft": 5382,
        "ceiling_height_ft": 10.0,
        "operating_hours": "9 AM - 11 PM",
        "footfall_category": "MEDIUM",
        "ambient_temp_max": 47.0,
        "humidity_level": "HIGH",
        "dust_exposure": "MEDIUM",
        "heat_load_category": "MEDIUM",
        "fresh_air_requirement": "MEDIUM",
        "landlord_constraints": "",
        "existing_hvac_type": "VRF/VRV",
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "MEDIUM",
    },
    {
        "store_id": "KWT-KWC-CP-006",
        "brand": "Centrepoint",
        "country": "KUWAIT",
        "city": "Kuwait City",
        "store_type": "MALL",
        "store_format": "RETAIL",
        "area_sqft": 25833,
        "ceiling_height_ft": 14.0,
        "operating_hours": "10 AM - 11 PM",
        "footfall_category": "HIGH",
        "ambient_temp_max": 50.0,
        "humidity_level": "LOW",
        "dust_exposure": "HIGH",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "MEDIUM",
        "landlord_constraints": "Mall-managed chilled water system",
        "existing_hvac_type": "Chilled water interface",
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
    },
    {
        "store_id": "BHR-MNM-WZ-007",
        "brand": "Wellness Zone",
        "country": "BAHRAIN",
        "city": "Manama",
        "store_type": "MALL",
        "store_format": "RETAIL",
        "area_sqft": 3229,
        "ceiling_height_ft": 9.5,
        "operating_hours": "10 AM - 10 PM",
        "footfall_category": "MEDIUM",
        "ambient_temp_max": 44.0,
        "humidity_level": "HIGH",
        "dust_exposure": "LOW",
        "heat_load_category": "MEDIUM",
        "fresh_air_requirement": "MEDIUM",
        "landlord_constraints": "",
        "existing_hvac_type": "Split Systems",
        "budget_level": "LOW",
        "energy_efficiency_priority": "MEDIUM",
    },
    {
        "store_id": "UAE-DXB-WH-008",
        "brand": "Landmark Logistics",
        "country": "UAE",
        "city": "Jebel Ali",
        "store_type": "WAREHOUSE",
        "store_format": "OTHER",
        "area_sqft": 107639,
        "ceiling_height_ft": 30.0,
        "operating_hours": "24 Hours",
        "footfall_category": "LOW",
        "ambient_temp_max": 48.0,
        "humidity_level": "MEDIUM",
        "dust_exposure": "HIGH",
        "heat_load_category": "MEDIUM",
        "fresh_air_requirement": "LOW",
        "landlord_constraints": "No outdoor units on roof",
        "existing_hvac_type": "Packaged DX units",
        "budget_level": "MEDIUM",
        "energy_efficiency_priority": "LOW",
    },
    {
        "store_id": "UAE-DXB-DC-009",
        "brand": "LMG IT",
        "country": "UAE",
        "city": "Dubai",
        "store_type": "DATA_CENTER",
        "store_format": "OTHER",
        "area_sqft": 2152,
        "ceiling_height_ft": 10.0,
        "operating_hours": "24 Hours",
        "footfall_category": "LOW",
        "ambient_temp_max": 48.0,
        "humidity_level": "MEDIUM",
        "dust_exposure": "LOW",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "LOW",
        "landlord_constraints": "Restricted electrical capacity",
        "existing_hvac_type": "Precision cooling units",
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
    },
    {
        "store_id": "SAU-JED-IKEA-010",
        "brand": "IKEA",
        "country": "KSA",
        "city": "Jeddah",
        "store_type": "STANDALONE",
        "store_format": "FURNITURE",
        "area_sqft": 237847,
        "ceiling_height_ft": 32.0,
        "operating_hours": "10 AM - 12 AM",
        "footfall_category": "HIGH",
        "ambient_temp_max": 46.0,
        "humidity_level": "MEDIUM",
        "dust_exposure": "HIGH",
        "heat_load_category": "HIGH",
        "fresh_air_requirement": "HIGH",
        "landlord_constraints": "No outdoor units on roof; Chilled water available",
        "existing_hvac_type": "Central chiller plant",
        "budget_level": "HIGH",
        "energy_efficiency_priority": "HIGH",
    },
]


class Command(BaseCommand):
    help = "Seed default HVACStoreProfile records so the HVAC form dropdown is pre-populated."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing HVACStoreProfile records before seeding.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = HVACStoreProfile.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing store profile(s)."))

        created_count = 0
        updated_count = 0

        for data in DEFAULT_PROFILES:
            store_id = data.pop("store_id")
            profile, created = HVACStoreProfile.objects.update_or_create(
                store_id=store_id,
                defaults=data,
            )
            if created:
                created_count += 1
                self.stdout.write(f"  [NEW]     {store_id}")
            else:
                updated_count += 1
                self.stdout.write(f"  [EXISTS]  {store_id}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_count} created, {updated_count} already existed."
            )
        )
