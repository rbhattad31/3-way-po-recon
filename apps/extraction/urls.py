from django.urls import path

from apps.extraction.template_views import (
    extraction_ajax_filter,
    extraction_approve,
    extraction_approval_analytics,
    extraction_approval_detail,
    extraction_approval_queue,
    extraction_console,
    extraction_edit_values,
    extraction_export_csv,
    extraction_reject,
    extraction_rerun,
    extraction_result_detail,
    extraction_result_json,
    extraction_upload,
    extraction_view_pdf,
    extraction_workbench,
)
from apps.extraction.credit_views import (
    credit_account_adjust,
    credit_account_detail,
    credit_account_list,
)

app_name = "extraction"

urlpatterns = [
    path("", extraction_workbench, name="workbench"),
    path("upload/", extraction_upload, name="upload"),
    path("filter/", extraction_ajax_filter, name="ajax_filter"),
    path("export/", extraction_export_csv, name="export_csv"),
    path("result/<int:pk>/", extraction_result_detail, name="result_detail"),
    path("result/<int:pk>/json/", extraction_result_json, name="result_json"),
    path("result/<int:pk>/pdf/", extraction_view_pdf, name="view_pdf"),
    path("result/<int:pk>/rerun/", extraction_rerun, name="rerun"),
    path("result/<int:pk>/edit/", extraction_edit_values, name="edit_values"),
    path("console/<int:pk>/", extraction_console, name="console"),
    # Extraction approval
    path("approvals/", extraction_approval_queue, name="approval_queue"),
    path("approvals/<int:pk>/", extraction_approval_detail, name="approval_detail"),
    path("approvals/<int:pk>/approve/", extraction_approve, name="approve"),
    path("approvals/<int:pk>/reject/", extraction_reject, name="reject"),
    path("approvals/analytics/", extraction_approval_analytics, name="approval_analytics"),
    # Credit management
    path("credits/", credit_account_list, name="credit_list"),
    path("credits/<int:user_id>/", credit_account_detail, name="credit_detail"),
    path("credits/<int:user_id>/adjust/", credit_account_adjust, name="credit_adjust"),
]
