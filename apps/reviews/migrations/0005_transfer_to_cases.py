"""
Final reviews migration: remove all 4 review models from the ``reviews``
app's migration state.  The actual DB tables are untouched (db_table is
unchanged).  The ``cases`` app's companion migration re-creates these
models in its own state.

After this migration is applied, ``apps.reviews`` can be removed from
INSTALLED_APPS.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0004_add_tenant_fk"),
    ]

    operations = [
        # State-only: remove models from reviews.  Order matters -- remove
        # models that have FKs to ReviewAssignment first.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ReviewDecision"),
                migrations.DeleteModel(name="ReviewComment"),
                migrations.DeleteModel(name="ManualReviewAction"),
                migrations.DeleteModel(name="ReviewAssignment"),
            ],
            database_operations=[],
        ),
    ]

