"""URL routes for email integration operational endpoints."""
from django.urls import path

from apps.email_integration.template_views import EmailIntegrationDashboardView
from apps.email_integration.views import EmailIntegrationStatusView, EmailWebhookIngestView, TriggerPollingView

app_name = "email_integration"

urlpatterns = [
    path("", EmailIntegrationDashboardView.as_view(), name="dashboard"),
    path("status/", EmailIntegrationStatusView.as_view(), name="status"),
    path("webhook/<int:mailbox_id>/", EmailWebhookIngestView.as_view(), name="webhook_ingest"),
    path("poll/trigger/", TriggerPollingView.as_view(), name="trigger_polling"),
]
