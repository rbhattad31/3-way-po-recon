from django.db import migrations


CATEGORY_MASTER_DEFAULTS = [
    {"code": "EQUIPMENT", "name": "Equipment", "pricing_type": "MARKET", "sort_order": 1},
    {"code": "DUCTING", "name": "Ducting", "pricing_type": "HYBRID", "sort_order": 2},
    {"code": "PIPING", "name": "Piping", "pricing_type": "HYBRID", "sort_order": 3},
    {"code": "ELECTRICAL", "name": "Electrical", "pricing_type": "BENCHMARK", "sort_order": 4},
    {"code": "CONTROLS", "name": "Controls", "pricing_type": "MARKET", "sort_order": 5},
    {"code": "AIR_DISTRIBUTION", "name": "Air Distribution", "pricing_type": "BENCHMARK", "sort_order": 6},
    {"code": "INSTALLATION", "name": "Installation", "pricing_type": "BENCHMARK", "sort_order": 7},
    {"code": "TC", "name": "Testing & Commissioning", "pricing_type": "BENCHMARK", "sort_order": 8},
    {"code": "ACCESSORIES", "name": "Accessories (Dampers, Louvers, etc.)", "pricing_type": "MARKET", "sort_order": 9},
    {"code": "INSULATION", "name": "Insulation", "pricing_type": "HYBRID", "sort_order": 10},
]


CATEGORY_DESCRIPTIONS = {
    "EQUIPMENT": "Main HVAC units: VRF/VRV, Chillers, Split ACs, Packaged Units, FCUs, AHUs",
    "DUCTING": "GI ductwork, flexible ducts, grilles, diffusers, louvres",
    "PIPING": "Copper piping, CHW piping, refrigerant piping, fittings, supports, valves and accessories",
    "ELECTRICAL": "Power cabling, MCC/DB interfaces, isolators, control wiring, panels and electrical accessories",
    "CONTROLS": "BMS/DDC controls, control panels, cabling, sensors, actuators",
    "AIR_DISTRIBUTION": "Diffusers, grilles, dampers, louvers, VAV terminals and air-side distribution accessories",
    "INSTALLATION": "Labour for mechanical installation, fix & fit, pipework, electrical works",
    "TC": "Testing, balancing, commissioning, startup, handover",
    "ACCESSORIES": "Accessories such as dampers, louvers, volume control parts and related HVAC fittings",
    "INSULATION": "Pipe insulation (Armaflex/NBR), duct insulation (glass wool, foam)",
}


def sync_category_master(apps, schema_editor):
    CategoryMaster = apps.get_model("benchmarking", "CategoryMaster")
    allowed_codes = {row["code"] for row in CATEGORY_MASTER_DEFAULTS}

    for row in CATEGORY_MASTER_DEFAULTS:
        CategoryMaster.objects.update_or_create(
            code=row["code"],
            defaults={
                "name": row["name"],
                "description": CATEGORY_DESCRIPTIONS.get(row["code"], ""),
                "pricing_type": row["pricing_type"],
                "sort_order": row["sort_order"],
                "is_active": True,
            },
        )

    CategoryMaster.objects.exclude(code__in=allowed_codes).update(is_active=False)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0011_benchmarkrunlog"),
    ]

    operations = [
        migrations.RunPython(sync_category_master, noop_reverse),
    ]
