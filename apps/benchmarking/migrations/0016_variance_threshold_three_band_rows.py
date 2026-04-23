from django.db import migrations, models


PDF_RULE_NOTE = (
    "Client-approved global variance rule from "
    "requirement_documents/Flow_B_Detailed_Configuration_Document.pdf: "
    "0-5% Optimal, 5-15% Moderate, 15-100% High"
)


def convert_to_three_band_rows(apps, schema_editor):
    VarianceThresholdConfig = apps.get_model("benchmarking", "VarianceThresholdConfig")

    baseline = (
        VarianceThresholdConfig.objects
        .filter(category="ALL", geography="ALL", is_active=True)
        .order_by("pk")
        .first()
    )

    within_max = 5.0
    moderate_max = 15.0
    if baseline:
        try:
            within_max = float(baseline.within_range_max_pct)
        except Exception:
            within_max = 5.0
        try:
            moderate_max = float(baseline.moderate_max_pct)
        except Exception:
            moderate_max = 15.0

    VarianceThresholdConfig.objects.all().delete()

    VarianceThresholdConfig.objects.create(
        category="ALL",
        geography="ALL",
        variance_status="WITHIN_RANGE",
        within_range_max_pct=0.0,
        moderate_max_pct=within_max,
        notes=PDF_RULE_NOTE,
        is_active=True,
    )
    VarianceThresholdConfig.objects.create(
        category="ALL",
        geography="ALL",
        variance_status="MODERATE",
        within_range_max_pct=within_max,
        moderate_max_pct=moderate_max,
        notes=PDF_RULE_NOTE,
        is_active=True,
    )
    VarianceThresholdConfig.objects.create(
        category="ALL",
        geography="ALL",
        variance_status="HIGH",
        within_range_max_pct=moderate_max,
        moderate_max_pct=100.0,
        notes=PDF_RULE_NOTE,
        is_active=True,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0015_replace_variance_thresholds_with_global_pdf_rule"),
    ]

    operations = [
        migrations.AddField(
            model_name="variancethresholdconfig",
            name="variance_status",
            field=models.CharField(
                choices=[
                    ("WITHIN_RANGE", "Optimal"),
                    ("MODERATE", "Moderate"),
                    ("HIGH", "High"),
                ],
                db_index=True,
                default="WITHIN_RANGE",
                help_text="Variance band represented by this row",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="variancethresholdconfig",
            name="within_range_max_pct",
            field=models.FloatField(
                default=0.0,
                help_text="Minimum absolute variance % for this band",
            ),
        ),
        migrations.AlterField(
            model_name="variancethresholdconfig",
            name="moderate_max_pct",
            field=models.FloatField(
                default=5.0,
                help_text="Maximum absolute variance % for this band",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="variancethresholdconfig",
            name="uniq_variance_threshold_cat_geo",
        ),
        migrations.AddConstraint(
            model_name="variancethresholdconfig",
            constraint=models.UniqueConstraint(
                fields=("category", "geography", "variance_status"),
                name="uniq_variance_threshold_cat_geo_status",
            ),
        ),
        migrations.RunPython(convert_to_three_band_rows, noop_reverse),
    ]
