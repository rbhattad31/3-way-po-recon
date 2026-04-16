import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
        ("extraction_core", "0008_add_tenant_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="extractionrun",
            name="document_upload",
            field=models.ForeignKey(
                blank=True,
                help_text="Source document upload (if originating from PO-recon pipeline)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="extraction_runs",
                to="documents.documentupload",
            ),
        ),
    ]
