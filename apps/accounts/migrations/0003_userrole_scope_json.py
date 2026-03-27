# Generated 2026-03-26 -- Phase 4 data-scope authorization

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_rbac_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="userrole",
            name="scope_json",
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    "Optional scope restrictions for this specific role assignment. "
                    "Null means unrestricted (full role scope). "
                    "Supported keys: allowed_business_units (list[str]), "
                    "allowed_vendor_ids (list[int]). "
                    "Unsupported / pending: country, legal_entity, cost_centre "
                    "(require schema extension on Invoice/PurchaseOrder)."
                ),
            ),
        ),
    ]
