"""Template URL routes for the AP Copilot workspace."""
from django.urls import path

from apps.copilot import template_views

app_name = "copilot"

urlpatterns = [
    path("", template_views.copilot_workspace, name="workspace"),
    path("case/<int:case_id>/", template_views.copilot_case, name="case"),
    path("session/<uuid:session_id>/", template_views.copilot_session, name="session"),
]
