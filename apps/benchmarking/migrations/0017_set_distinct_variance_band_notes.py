from django.db import migrations


def set_distinct_variance_band_notes(apps, schema_editor):
    VarianceThresholdConfig = apps.get_model("benchmarking", "VarianceThresholdConfig")

    notes_by_status = {
        "WITHIN_RANGE": "Optimal band (0-5%) from client-approved variance rule in Flow_B_Detailed_Configuration_Document.pdf",
        "MODERATE": "Moderate band (5-15%) from client-approved variance rule in Flow_B_Detailed_Configuration_Document.pdf",
        "HIGH": "High band (15-100%) from client-approved variance rule in Flow_B_Detailed_Configuration_Document.pdf",
    }

    for status, note in notes_by_status.items():
        (
            VarianceThresholdConfig.objects
            .filter(category="ALL", geography="ALL", variance_status=status)
            .update(notes=note)
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0016_variance_threshold_three_band_rows"),
    ]

    operations = [
        migrations.RunPython(set_distinct_variance_band_notes, noop_reverse),
    ]
