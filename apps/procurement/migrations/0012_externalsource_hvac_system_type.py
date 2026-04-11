from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0011_hvacrule_country_city_filters"),
    ]

    operations = [
        migrations.AddField(
            model_name="externalsourceregistry",
            name="hvac_system_type",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="HVAC system type this source covers e.g. VRF, SPLIT_AC, PACKAGED_DX, CHILLER, DUCTING. Blank = all types.",
                max_length=40,
            ),
        ),
    ]
