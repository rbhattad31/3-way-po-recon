from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0012_externalsource_hvac_system_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="externalsourceregistry",
            name="source_url",
            field=models.URLField(
                blank=True,
                default="",
                max_length=500,
                help_text="Direct product-page URL for this source e.g. https://www.daikin.com/products/ac/",
            ),
        ),
    ]
