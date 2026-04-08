"""
Management command to seed default BenchmarkCorridorRule records.

Usage:
    python manage.py seed_benchmark_corridors
    python manage.py seed_benchmark_corridors --clear   # clear first
"""

from django.core.management.base import BaseCommand
from apps.benchmarking.models import BenchmarkCorridorRule


CORRIDORS = [
    # ---- EQUIPMENT -- UAE ----
    {
        "rule_code": "BC-EQUIP-UAE-VRF-001",
        "name": "VRF / VRV Outdoor Unit -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 1400,
        "mid_rate": 1800,
        "max_rate": 2400,
        "currency": "AED",
        "keywords": "vrf,vrv,outdoor unit,condensing unit",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-UAE-CHILLER-001",
        "name": "Chiller -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 2200,
        "mid_rate": 2800,
        "max_rate": 3600,
        "currency": "AED",
        "keywords": "chiller,water cooled chiller,air cooled chiller,centrifugal,screw compressor",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-UAE-SPLIT-001",
        "name": "Split / Cassette Units -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 900,
        "mid_rate": 1200,
        "max_rate": 1600,
        "currency": "AED",
        "keywords": "split,cassette,wall mounted,ceiling mounted,ductable,ducted split",
        "priority": 20,
    },
    {
        "rule_code": "BC-EQUIP-UAE-PACKAGED-001",
        "name": "Packaged DX Unit -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 1100,
        "mid_rate": 1450,
        "max_rate": 1900,
        "currency": "AED",
        "keywords": "packaged,dx unit,rooftop,ahu,air handling unit",
        "priority": 20,
    },
    # ---- EQUIPMENT -- KSA ----
    {
        "rule_code": "BC-EQUIP-KSA-VRF-001",
        "name": "VRF / VRV -- KSA",
        "category": "EQUIPMENT",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 1350,
        "mid_rate": 1750,
        "max_rate": 2300,
        "currency": "AED",
        "keywords": "vrf,vrv,outdoor unit",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-KSA-CHILLER-001",
        "name": "Chiller -- KSA",
        "category": "EQUIPMENT",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 2100,
        "mid_rate": 2700,
        "max_rate": 3500,
        "currency": "AED",
        "keywords": "chiller,screw,centrifugal",
        "priority": 10,
    },
    # ---- EQUIPMENT -- QATAR ----
    {
        "rule_code": "BC-EQUIP-QAT-VRF-001",
        "name": "VRF / VRV -- Qatar",
        "category": "EQUIPMENT",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 1450,
        "mid_rate": 1900,
        "max_rate": 2500,
        "currency": "AED",
        "keywords": "vrf,vrv,outdoor unit",
        "priority": 10,
    },
    # ---- CONTROLS ----
    {
        "rule_code": "BC-CTRL-ALL-BMS-001",
        "name": "BMS / DDC Controls -- All Geographies",
        "category": "CONTROLS",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/point",
        "min_rate": 220,
        "mid_rate": 320,
        "max_rate": 500,
        "currency": "AED",
        "keywords": "bms,ddc,building management,controller,modbus,bacnet,lon,control panel",
        "priority": 10,
    },
    {
        "rule_code": "BC-CTRL-ALL-CABLING-001",
        "name": "Control Cabling -- All Geographies",
        "category": "CONTROLS",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 12,
        "mid_rate": 18,
        "max_rate": 28,
        "currency": "AED",
        "keywords": "control cable,signal cable,screened cable",
        "priority": 20,
    },
    # ---- DUCTING ----
    {
        "rule_code": "BC-DUCT-ALL-GI-001",
        "name": "GI Ductwork (rectangular/circular) -- All",
        "category": "DUCTING",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/kg",
        "min_rate": 14,
        "mid_rate": 18,
        "max_rate": 24,
        "currency": "AED",
        "keywords": "gi duct,galvanised duct,galvanized duct,ductwork,rectangular duct,circular duct",
        "priority": 10,
    },
    {
        "rule_code": "BC-DUCT-ALL-FLEX-001",
        "name": "Flexible Ductwork -- All",
        "category": "DUCTING",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 22,
        "mid_rate": 32,
        "max_rate": 48,
        "currency": "AED",
        "keywords": "flexible duct,flex duct,insulated flexible",
        "priority": 20,
    },
    # ---- INSULATION ----
    {
        "rule_code": "BC-INSUL-ALL-PIPE-001",
        "name": "Pipe Insulation (Armaflex/NBR) -- All",
        "category": "INSULATION",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 18,
        "mid_rate": 26,
        "max_rate": 40,
        "currency": "AED",
        "keywords": "armaflex,insulation,pipe insulation,nbr foam,rubber foam",
        "priority": 10,
    },
    {
        "rule_code": "BC-INSUL-ALL-DUCT-001",
        "name": "Duct Insulation (Glass Wool) -- All",
        "category": "INSULATION",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 28,
        "mid_rate": 38,
        "max_rate": 55,
        "currency": "AED",
        "keywords": "glass wool,duct insulation,insulated duct wrap,aluminium foil",
        "priority": 20,
    },
    # ---- ACCESSORIES ----
    {
        "rule_code": "BC-ACC-ALL-PIPE-001",
        "name": "Pipe and Fittings (copper/steel) -- All",
        "category": "ACCESSORIES",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 35,
        "mid_rate": 55,
        "max_rate": 90,
        "currency": "AED",
        "keywords": "copper pipe,steel pipe,refrigerant pipe,condenser pipe,drain pipe,fitting",
        "priority": 10,
    },
    {
        "rule_code": "BC-ACC-ALL-HANGER-001",
        "name": "Supports, Hangers and Brackets -- All",
        "category": "ACCESSORIES",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/LS",
        "min_rate": 0.03,
        "mid_rate": 0.05,
        "max_rate": 0.08,
        "currency": "AED",
        "keywords": "hanger,support,bracket,unistrut,rod,clamp",
        "priority": 20,
        "notes": "Rate expressed as fraction of equipment cost (LS basis).",
    },
    {
        "rule_code": "BC-ACC-ALL-VALVE-001",
        "name": "Valves and Dampers -- All",
        "category": "ACCESSORIES",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/no.",
        "min_rate": 120,
        "mid_rate": 220,
        "max_rate": 400,
        "currency": "AED",
        "keywords": "valve,ball valve,motorised valve,vav damper,fire damper,balancing valve",
        "priority": 10,
    },
    # ---- INSTALLATION ----
    {
        "rule_code": "BC-INST-ALL-HVAC-001",
        "name": "HVAC Installation (general) -- All",
        "category": "INSTALLATION",
        "geography": "ALL",
        "scope_type": "SITC",
        "uom": "AED/TR",
        "min_rate": 400,
        "mid_rate": 600,
        "max_rate": 900,
        "currency": "AED",
        "keywords": "installation,labour,fix,fixing,erection,mechanical work,pipework",
        "priority": 10,
    },
    {
        "rule_code": "BC-INST-ALL-ELEC-001",
        "name": "Electrical Works (HVAC) -- All",
        "category": "INSTALLATION",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 150,
        "mid_rate": 250,
        "max_rate": 400,
        "currency": "AED",
        "keywords": "electrical,wiring,mcb,panel,switchboard,power supply",
        "priority": 20,
    },
    # ---- TC (Testing and Commissioning) ----
    {
        "rule_code": "BC-TC-ALL-001",
        "name": "Testing and Commissioning -- All",
        "category": "TC",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 80,
        "mid_rate": 130,
        "max_rate": 200,
        "currency": "AED",
        "keywords": "testing,commissioning,t&c,t and c,balancing,startup",
        "priority": 10,
    },
    # ---- EQUIPMENT (generic fallback, ALL geo) ----
    {
        "rule_code": "BC-EQUIP-ALL-GENERIC-001",
        "name": "Generic HVAC Equipment -- All Geographies",
        "category": "EQUIPMENT",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/TR",
        "min_rate": 1200,
        "mid_rate": 1750,
        "max_rate": 2400,
        "currency": "AED",
        "keywords": "equipment,unit,system",
        "priority": 100,
    },
]


class Command(BaseCommand):
    help = "Seed default BenchmarkCorridorRule records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing corridor rules before seeding",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = BenchmarkCorridorRule.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing corridor rules."))

        created_count = 0
        updated_count = 0

        for data in CORRIDORS:
            notes = data.pop("notes", "")
            rule_code = data["rule_code"]
            kwargs = {k: v for k, v in data.items() if k != "rule_code"}
            if notes:
                kwargs["notes"] = notes

            obj, created = BenchmarkCorridorRule.objects.update_or_create(
                rule_code=rule_code,
                defaults=kwargs,
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} created, {updated_count} updated. "
                f"Total corridor rules: {BenchmarkCorridorRule.objects.count()}"
            )
        )
