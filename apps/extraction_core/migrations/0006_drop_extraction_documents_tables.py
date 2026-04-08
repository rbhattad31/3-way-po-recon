# Generated -- drops extraction_documents tables and removes stale
# django_migrations rows and the old document_id column from extraction_core_extraction_run.
# extraction_documents app has been removed from INSTALLED_APPS.

from django.db import migrations


def remove_extraction_documents_migration_rows(apps, schema_editor):
    """Remove django_migrations rows for the deleted extraction_documents app."""
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM django_migrations WHERE app = 'extraction_documents';"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("extraction_core", "0005_add_document_fields_to_extraction_run"),
    ]

    operations = [
        # 1. Drop tables (child FK first) -- document_id column was already
        #    removed by the partial run of the previous migration attempt.
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS extraction_documents_extraction_field_result;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS extraction_documents_extraction_document;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 2. Clean up stale migration records
        migrations.RunPython(
            remove_extraction_documents_migration_rows,
            migrations.RunPython.noop,
        ),
    ]
