from django.urls import path

from apps.reconciliation.template_views import (
    case_console, case_export_csv, recon_settings, result_detail, result_list, start_reconciliation,
)

app_name = "reconciliation"

urlpatterns = [
    path("", result_list, name="result_list"),
    path("start/", start_reconciliation, name="start_reconciliation"),
    path("settings/", recon_settings, name="recon_settings"),
    path("<int:pk>/", result_detail, name="result_detail"),
    path("<int:pk>/console/", case_console, name="case_console"),
    path("<int:pk>/export/", case_export_csv, name="case_export_csv"),
]
