from django.db import migrations


ALLOWED_CATEGORY_CODES_IN_ORDER = [
    "EQUIPMENT",
    "DUCTING",
    "PIPING",
    "ELECTRICAL",
    "CONTROLS",
    "AIR_DISTRIBUTION",
    "INSTALLATION",
    "TC",
    "ACCESSORIES",
    "INSULATION",
]


def seed_missing_thresholds(apps, schema_editor):
    VarianceThresholdConfig = apps.get_model("benchmarking", "VarianceThresholdConfig")

    for code in ALLOWED_CATEGORY_CODES_IN_ORDER:
        VarianceThresholdConfig.objects.get_or_create(
            category=code,
            geography="ALL",
            defaults={
                "within_range_max_pct": 5.0,
                "moderate_max_pct": 15.0,
                "notes": "Auto-seeded from Category Master allowed set",
                "is_active": True,
            },
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0013_sync_corridor_threshold_allowed_set"),
    ]

    operations = [
        migrations.RunPython(seed_missing_thresholds, noop_reverse),
    ]
