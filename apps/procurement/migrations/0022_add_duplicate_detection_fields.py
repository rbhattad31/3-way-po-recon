from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0021_marketintelligencesuggestion_source_reference_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="procurementrequest",
            name="is_duplicate",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="True if this request was detected as a duplicate of another",
            ),
        ),
        migrations.AddField(
            model_name="procurementrequest",
            name="duplicate_of",
            field=models.ForeignKey(
                blank=True,
                help_text="Points to the original request if this is a detected duplicate",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="duplicates",
                to="procurement.procurementrequest",
            ),
        ),
    ]
