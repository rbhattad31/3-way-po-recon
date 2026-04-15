from django.db import migrations, models


def forwards_map_rfq_statuses(apps, schema_editor):
    ProcurementRequest = apps.get_model("procurement", "ProcurementRequest")
    GeneratedRFQ = apps.get_model("procurement", "GeneratedRFQ")

    generated_request_ids = set(
        GeneratedRFQ.objects.values_list("request_id", flat=True).distinct()
    )

    for proc_request in ProcurementRequest.objects.all().only("id", "status"):
        has_rfq = proc_request.id in generated_request_ids
        if proc_request.status == "FAILED":
            new_status = "FAILED"
        elif has_rfq:
            new_status = "READY_RFQ"
        else:
            new_status = "PENDING_RFQ"

        if proc_request.status != new_status:
            proc_request.status = new_status
            proc_request.save(update_fields=["status"])


def backwards_map_rfq_statuses(apps, schema_editor):
    ProcurementRequest = apps.get_model("procurement", "ProcurementRequest")

    for proc_request in ProcurementRequest.objects.all().only("id", "status"):
        if proc_request.status == "READY_RFQ":
            new_status = "READY"
        elif proc_request.status == "PENDING_RFQ":
            new_status = "DRAFT"
        else:
            new_status = proc_request.status

        if proc_request.status != new_status:
            proc_request.status = new_status
            proc_request.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0022_add_duplicate_detection_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="procurementrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING_RFQ", "Pending RFQ"),
                    ("READY_RFQ", "Ready RFQ"),
                    ("COMPLETED", "Completed"),
                    ("FAILED", "Failed"),
                ],
                db_index=True,
                default="PENDING_RFQ",
                max_length=20,
            ),
        ),
        migrations.RunPython(forwards_map_rfq_statuses, backwards_map_rfq_statuses),
    ]