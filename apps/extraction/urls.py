from django.urls import path
from django.views.generic import RedirectView

from apps.extraction.template_views import (
    extraction_ajax_filter,
    extraction_approve,
    extraction_approval_analytics,
    extraction_approval_detail,
    extraction_approval_queue,
    extraction_edit_values,
    extraction_export_csv,
    extraction_export_purchase_invoice_excel_bulk,
    extraction_export_purchase_invoice_excel,
    extraction_reject,
    extraction_rerun,
    extraction_result_detail,
    extraction_result_json,
    extraction_upload,
    extraction_view_pdf,
    extraction_workbench,
    invoice_admin_delete,
)
from apps.extraction.credit_views import (
    credit_account_adjust,
    credit_account_detail,
    credit_account_list,
)
from apps.extraction.bulk_views import (
    bulk_job_detail,
    bulk_job_list,
    bulk_job_retry,
    bulk_job_start,
    bulk_source_create,
    bulk_source_delete,
    bulk_source_edit,
    bulk_source_list,
    bulk_source_test,
)

app_name = "extraction"

urlpatterns = [
    path("", extraction_workbench, name="workbench"),
    path("upload/", extraction_upload, name="upload"),
    path("filter/", extraction_ajax_filter, name="ajax_filter"),
    path("export/", extraction_export_purchase_invoice_excel_bulk, name="export_purchase_invoice_excel_bulk"),
    path("export/csv/", extraction_export_csv, name="export_csv"),
    path("export/purchase-invoice/<int:pk>/", extraction_export_purchase_invoice_excel, name="export_purchase_invoice_excel"),
    path("result/<int:pk>/", extraction_result_detail, name="result_detail"),
    path("result/<int:pk>/json/", extraction_result_json, name="result_json"),
    path("result/<int:pk>/pdf/", extraction_view_pdf, name="view_pdf"),
    path("result/<int:pk>/rerun/", extraction_rerun, name="rerun"),
    path("result/<int:pk>/edit/", extraction_edit_values, name="edit_values"),
    path("result/<int:pk>/admin-delete/", invoice_admin_delete, name="admin_delete"),
    path(
        "console/<int:pk>/",
        RedirectView.as_view(pattern_name="cases:case_agent_view", permanent=True),
        name="console",
    ),
    # Extraction approval
    path("approvals/", extraction_approval_queue, name="approval_queue"),
    path("approvals/<int:pk>/", extraction_approval_detail, name="approval_detail"),
    path("approvals/<int:pk>/approve/", extraction_approve, name="approve"),
    path("approvals/<int:pk>/reject/", extraction_reject, name="reject"),
    path("approvals/analytics/", extraction_approval_analytics, name="approval_analytics"),
    # Credit management
    path("credits/", credit_account_list, name="credit_account_list"),
    path("credits/<int:user_id>/", credit_account_detail, name="credit_account_detail"),
    path("credits/<int:user_id>/adjust/", credit_account_adjust, name="credit_adjust"),
    # Bulk extraction
    path("bulk/", bulk_job_list, name="bulk_job_list"),
    path("bulk/start/", bulk_job_start, name="bulk_job_start"),
    path("bulk/sources/", bulk_source_list, name="bulk_source_list"),
    path("bulk/source/create/", bulk_source_create, name="bulk_source_create"),
    path("bulk/source/test/", bulk_source_test, name="bulk_source_test"),
    path("bulk/source/<int:pk>/edit/", bulk_source_edit, name="bulk_source_edit"),
    path("bulk/source/<int:pk>/delete/", bulk_source_delete, name="bulk_source_delete"),
    path("bulk/<int:job_id>/", bulk_job_detail, name="bulk_job_detail"),
    path("bulk/<int:job_id>/retry/", bulk_job_retry, name="bulk_job_retry"),
]
