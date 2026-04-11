"""Add country_filter and city_filter columns to HVACRecommendationRule."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0010_market_intelligence_suggestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="hvacrecommendationrule",
            name="country_filter",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Pipe-separated country names, e.g. UAE|KSA|Qatar. Blank = any.",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="hvacrecommendationrule",
            name="city_filter",
            field=models.CharField(
                blank=True,
                default="",
                help_text="City name (case-insensitive exact match). Blank = any.",
                max_length=200,
            ),
        ),
    ]
