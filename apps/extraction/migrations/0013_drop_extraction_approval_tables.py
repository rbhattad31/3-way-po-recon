# Generated -- drops ExtractionApproval and ExtractionFieldCorrection tables.
# Uses RunSQL because migration 0002 was converted to a no-op, so Django's
# internal state no longer knows about these models (DeleteModel would fail).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("extraction", "0012_merge_0011"),
    ]

    operations = [
        # Child table first (has FK to extraction_approval).
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS extraction_field_correction;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS extraction_approval;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
