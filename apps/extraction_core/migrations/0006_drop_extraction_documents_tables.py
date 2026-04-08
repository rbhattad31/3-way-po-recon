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
        # 0. Drop FK from extraction_core_extraction_run -> extraction_documents
        #    so the table can be dropped without constraint errors.
        migrations.RunSQL(
            sql=(
                "SET @fk_exists = (SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE CONSTRAINT_NAME = 'extraction_core_extr_document_id_a684ab1b_fk_extractio' "
                "AND TABLE_SCHEMA = DATABASE()); "
                "SET @sql = IF(@fk_exists > 0, "
                "'ALTER TABLE extraction_core_extraction_run "
                "DROP FOREIGN KEY extraction_core_extr_document_id_a684ab1b_fk_extractio', "
                "'SELECT 1'); "
                "PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        # Also drop the document_id column if it still exists
        migrations.RunSQL(
            sql=(
                "SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_NAME = 'extraction_core_extraction_run' "
                "AND COLUMN_NAME = 'document_id' AND TABLE_SCHEMA = DATABASE()); "
                "SET @sql = IF(@col_exists > 0, "
                "'ALTER TABLE extraction_core_extraction_run DROP COLUMN document_id', "
                "'SELECT 1'); "
                "PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 1. Drop tables (child FK first)
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
