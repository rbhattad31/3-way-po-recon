"""API URL routes for the AP Copilot — included under /api/v1/copilot/."""
from django.urls import path

from apps.copilot import views

app_name = "copilot_api"

urlpatterns = [
    # Session management
    path("session/start/", views.session_start, name="session_start"),
    path("sessions/", views.session_list, name="session_list"),
    path("session/<uuid:session_id>/", views.session_detail, name="session_detail"),
    path("session/<uuid:session_id>/messages/", views.session_messages, name="session_messages"),

    # Chat
    path("chat/", views.chat, name="chat"),

    # Case context
    path("case/<int:case_id>/context/", views.case_context, name="case_context"),
    path("case/<int:case_id>/timeline/", views.case_timeline, name="case_timeline"),
    path("case/<int:case_id>/evidence/", views.case_evidence, name="case_evidence"),
    path("case/<int:case_id>/governance/", views.case_governance, name="case_governance"),

    # Suggestions & search
    path("suggestions/", views.suggestions, name="suggestions"),
    path("cases/search/", views.case_search, name="case_search"),

    # Upload
    path("upload/", views.invoice_upload, name="invoice_upload"),
    path("upload/<int:upload_id>/status/", views.upload_status, name="upload_status"),

    # Case reprocess status
    path("case/<int:case_id>/reprocess-status/", views.case_reprocess_status, name="case_reprocess_status"),

    # Supervisor agent
    path("supervisor/run/", views.supervisor_run, name="supervisor_run"),
    path("supervisor/stream/", views.supervisor_run_stream, name="supervisor_run_stream"),

    # Case actions (approve, reject, escalate, reprocess, request_info)
    path("case/<int:case_id>/action/", views.case_action, name="case_action"),
]
