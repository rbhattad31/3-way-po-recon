from django.urls import path

from apps.extraction.template_views import (
    extraction_ajax_filter,
    extraction_approve,
    extraction_approval_analytics,
    extraction_approval_detail,
    extraction_approval_queue,
    extraction_edit_values,
    extraction_export_csv,
    extraction_reject,
    extraction_rerun,
    extraction_result_detail,
    extraction_result_json,
    extraction_upload,
    extraction_workbench,
)

app_name = "extraction"

urlpatterns = [
    path("", extraction_workbench, name="workbench"),
    path("upload/", extraction_upload, name="upload"),
    path("filter/", extraction_ajax_filter, name="ajax_filter"),
    path("export/", extraction_export_csv, name="export_csv"),
    path("result/<int:pk>/", extraction_result_detail, name="result_detail"),
    path("result/<int:pk>/json/", extraction_result_json, name="result_json"),
    path("result/<int:pk>/rerun/", extraction_rerun, name="rerun"),
    path("result/<int:pk>/edit/", extraction_edit_values, name="edit_values"),
    # Extraction approval
    path("approvals/", extraction_approval_queue, name="approval_queue"),
    path("approvals/<int:pk>/", extraction_approval_detail, name="approval_detail"),
    path("approvals/<int:pk>/approve/", extraction_approve, name="approve"),
    path("approvals/<int:pk>/reject/", extraction_reject, name="reject"),
    path("approvals/analytics/", extraction_approval_analytics, name="approval_analytics"),
]
