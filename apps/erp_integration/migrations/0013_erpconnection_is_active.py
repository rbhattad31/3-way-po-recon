from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("erp_integration", "0012_add_voucher_sqlserver_connector_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="erpconnection",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
