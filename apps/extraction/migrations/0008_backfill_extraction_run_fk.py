"""Backfill extraction_run FK on existing ExtractionResult records.

For each ExtractionResult with a document_upload but no extraction_run,
find the most recent ExtractionRun linked through the same DocumentUpload
(via ExtractionDocument) and set the FK.
"""

from django.db import migrations


def backfill_extraction_run(apps, schema_editor):
    ExtractionResult = apps.get_model("extraction", "ExtractionResult")
    ExtractionRun = apps.get_model("extraction_core", "ExtractionRun")

    results_to_update = ExtractionResult.objects.filter(
        extraction_run__isnull=True,
        document_upload__isnull=False,
    )

    for ext_result in results_to_update.iterator(chunk_size=200):
        run = (
            ExtractionRun.objects
            .filter(document__document_upload_id=ext_result.document_upload_id)
            .order_by("-created_at")
            .values_list("pk", flat=True)
            .first()
        )
        if run:
            ExtractionResult.objects.filter(pk=ext_result.pk).update(extraction_run_id=run)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("extraction", "0006_add_extraction_run_fk"),
        ("extraction_core", "0004_add_prompt_template_routing_rule_settings_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_extraction_run, noop),
    ]
