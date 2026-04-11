from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0016_generated_rfq'),
    ]

    operations = [
        migrations.AddField(
            model_name='recommendationresult',
            name='reason_summary_json',
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    'Cached ReasonSummaryAgent output dict (headline, reasoning_summary, '
                    'top_drivers, rules_table, conditions_table, etc.). '
                    'Populated on first page load; avoids repeated LLM API calls. '
                    'Set to null to force regeneration.'
                ),
            ),
        ),
    ]
