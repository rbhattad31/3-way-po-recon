from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("email_integration", "0003_alter_emailaction_action_type"),
        ("procurement", "0022_add_duplicate_detection_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrun",
            name="trigger_source",
            field=models.CharField(
                choices=[("UI", "UI"), ("API", "API"), ("EMAIL", "Email"), ("SYSTEM", "System")],
                db_index=True,
                default="UI",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="procurementrequest",
            name="primary_email_thread",
            field=models.ForeignKey(
                blank=True,
                help_text="Primary email thread linked to this procurement request.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="procurement_requests",
                to="email_integration.emailthread",
            ),
        ),
        migrations.AddField(
            model_name="procurementrequest",
            name="source_channel",
            field=models.CharField(
                choices=[
                    ("WEB_UPLOAD", "Web Upload"),
                    ("EMAIL", "Email"),
                    ("API", "API"),
                    ("ERP_IMPORT", "ERP Import"),
                    ("SCAN", "Scan"),
                ],
                db_index=True,
                default="WEB_UPLOAD",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="supplierquotation",
            name="primary_email_thread",
            field=models.ForeignKey(
                blank=True,
                help_text="Primary supplier email thread linked to this quotation.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="supplier_quotations",
                to="email_integration.emailthread",
            ),
        ),
        migrations.AddField(
            model_name="supplierquotation",
            name="source_channel",
            field=models.CharField(
                choices=[
                    ("WEB_UPLOAD", "Web Upload"),
                    ("EMAIL", "Email"),
                    ("API", "API"),
                    ("ERP_IMPORT", "ERP Import"),
                    ("SCAN", "Scan"),
                ],
                db_index=True,
                default="WEB_UPLOAD",
                max_length=30,
            ),
        ),
    ]
