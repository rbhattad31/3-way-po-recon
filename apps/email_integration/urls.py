"""URL routes for email integration operational endpoints."""
from django.urls import path

from apps.email_integration.template_views import (
    EmailIntegrationActionLedgerView,
    EmailIntegrationConnectView,
    EmailIntegrationDropdownDataView,
    EmailIntegrationFailedActionsView,
    EmailIntegrationFunctionalityDashboardView,
    EmailIntegrationInboxPreviewView,
    EmailIntegrationInboxProcessingView,
    EmailIntegrationIngestView,
    EmailIntegrationMailboxHealthView,
    EmailIntegrationOutboundEmailView,
    EmailIntegrationRecentMessagesView,
    EmailIntegrationTriageQueueView,
)
from apps.email_integration.views import EmailIntegrationStatusView, EmailWebhookIngestView, TriggerPollingView

app_name = "email_integration"

urlpatterns = [
    path("", EmailIntegrationConnectView.as_view(), name="connect_mailbox"),
    path("dashboard/", EmailIntegrationFunctionalityDashboardView.as_view(), name="dashboard"),
    path("features/ingest/", EmailIntegrationIngestView.as_view(), name="ingest"),
    path("features/ingest/dropdown-data/", EmailIntegrationDropdownDataView.as_view(), name="ingest_dropdown_data"),
    path("features/ingest/inbox-preview/", EmailIntegrationInboxPreviewView.as_view(), name="ingest_inbox_preview"),
    path("features/mailbox-health/", EmailIntegrationMailboxHealthView.as_view(), name="mailbox_health"),
    path("features/inbox-processing/", EmailIntegrationInboxProcessingView.as_view(), name="inbox_processing"),
    path("features/recent-messages/", EmailIntegrationRecentMessagesView.as_view(), name="recent_messages"),
    path("features/triage-queue/", EmailIntegrationTriageQueueView.as_view(), name="triage_queue"),
    path("features/failed-actions/", EmailIntegrationFailedActionsView.as_view(), name="failed_actions"),
    path("features/action-ledger/", EmailIntegrationActionLedgerView.as_view(), name="action_ledger"),
    path("features/outbound-email/", EmailIntegrationOutboundEmailView.as_view(), name="outbound_email"),
    path("status/", EmailIntegrationStatusView.as_view(), name="status"),
    path("webhook/<int:mailbox_id>/", EmailWebhookIngestView.as_view(), name="webhook_ingest"),
    path("poll/trigger/", TriggerPollingView.as_view(), name="trigger_polling"),
]
