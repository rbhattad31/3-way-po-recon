"""Template and webhook-facing views for email integration."""
from __future__ import annotations

from django.http import JsonResponse
from django.views import View

from apps.core.permissions import PermissionRequiredMixin
from apps.email_integration.models import MailboxConfig
from apps.email_integration.tasks import ingest_webhook_payload_task, poll_mailboxes_task


class EmailIntegrationStatusView(PermissionRequiredMixin, View):
    """Simple operational status endpoint for mailbox integration state."""

    required_permission = "email.view"

    def get(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        mailboxes = MailboxConfig.objects.filter(is_active=True)
        if tenant is not None and not getattr(request.user, "is_platform_admin", False):
            mailboxes = mailboxes.filter(tenant=tenant)
        data = {
            "mailbox_count": mailboxes.count(),
            "inbound_enabled": mailboxes.filter(is_inbound_enabled=True).count(),
            "outbound_enabled": mailboxes.filter(is_outbound_enabled=True).count(),
        }
        return JsonResponse(data)


class EmailWebhookIngestView(View):
    """Webhook endpoint for provider push events (webhook-first ingestion)."""

    def post(self, request, mailbox_id: int, *args, **kwargs):
        import json

        payload = json.loads((request.body or b"{}").decode("utf-8"))
        ingest_webhook_payload_task.delay(mailbox_id=mailbox_id, payload=payload)
        return JsonResponse({"accepted": True})


class TriggerPollingView(PermissionRequiredMixin, View):
    """Manual trigger endpoint for polling fallback ingestion."""

    required_permission = "email.manage"

    def post(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        poll_mailboxes_task.delay(tenant_id=getattr(tenant, "pk", None))
        return JsonResponse({"queued": True})
