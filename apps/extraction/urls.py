from django.urls import path

from apps.extraction.template_views import (
    extraction_ajax_filter,
    extraction_edit_values,
    extraction_export_csv,
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
]
