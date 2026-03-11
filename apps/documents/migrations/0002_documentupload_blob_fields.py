from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentupload",
            name="blob_container",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="documentupload",
            name="blob_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="documentupload",
            name="blob_name",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="documentupload",
            name="blob_uploaded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentupload",
            name="blob_url",
            field=models.URLField(blank=True, default="", max_length=2048),
        ),
        migrations.AlterField(
            model_name="documentupload",
            name="file",
            field=models.FileField(blank=True, null=True, upload_to="invoices/%Y/%m/"),
        ),
    ]
