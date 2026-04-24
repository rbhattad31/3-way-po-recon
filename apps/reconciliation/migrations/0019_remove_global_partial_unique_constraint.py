from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reconciliation", "0018_tenant_scope_case_number_unique"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="reconciliationconfig",
            name="uq_reconconfig_name_global",
        ),
    ]
