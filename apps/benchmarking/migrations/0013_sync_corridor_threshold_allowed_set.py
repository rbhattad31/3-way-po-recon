from django.db import migrations


ALLOWED_CATEGORY_CODES = {
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
}


def sync_corridor_threshold_allowed_set(apps, schema_editor):
    BenchmarkCorridorRule = apps.get_model("benchmarking", "BenchmarkCorridorRule")
    VarianceThresholdConfig = apps.get_model("benchmarking", "VarianceThresholdConfig")

    BenchmarkCorridorRule.objects.exclude(category__in=ALLOWED_CATEGORY_CODES).update(is_active=False)
    VarianceThresholdConfig.objects.exclude(category__in=(list(ALLOWED_CATEGORY_CODES) + ["ALL"])).update(is_active=False)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0012_sync_category_master_allowed_set"),
    ]

    operations = [
        migrations.RunPython(sync_corridor_threshold_allowed_set, noop_reverse),
    ]
