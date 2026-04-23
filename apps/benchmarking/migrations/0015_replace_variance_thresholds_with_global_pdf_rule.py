from django.db import migrations


PDF_RULE_NOTE = (
    "Client-approved global variance rule from "
    "requirement_documents/Flow_B_Detailed_Configuration_Document.pdf: "
    "0-5% Optimal, 5-15% Moderate, 15-100% High"
)


def replace_variance_thresholds_with_global_pdf_rule(apps, schema_editor):
    VarianceThresholdConfig = apps.get_model("benchmarking", "VarianceThresholdConfig")

    VarianceThresholdConfig.objects.all().delete()
    VarianceThresholdConfig.objects.create(
        category="ALL",
        geography="ALL",
        within_range_max_pct=5.0,
        moderate_max_pct=15.0,
        notes=PDF_RULE_NOTE,
        is_active=True,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0014_seed_missing_variance_thresholds_for_category_master"),
    ]

    operations = [
        migrations.RunPython(replace_variance_thresholds_with_global_pdf_rule, noop_reverse),
    ]
