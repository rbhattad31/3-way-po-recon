import apps.benchmarking.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("benchmarking", "0007_rfq_document_upload"),
    ]

    operations = [
        migrations.AlterField(
            model_name="benchmarkquotation",
            name="document",
            field=models.FileField(blank=True, null=True, upload_to=apps.benchmarking.models.benchmark_quotation_upload_to),
        ),
    ]
