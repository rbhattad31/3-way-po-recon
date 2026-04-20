"""Template and webhook-facing views for email integration."""
from __future__ import annotations

import json
from hmac import compare_digest

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

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

    @staticmethod
    def _resolve_expected_token(mailbox) -> str:
        mailbox_token = ""
        if isinstance(mailbox.config_json, dict):
            mailbox_token = str(mailbox.config_json.get("webhook_token") or "").strip()
        if mailbox_token:
            return mailbox_token
        return str(getattr(settings, "EMAIL_WEBHOOK_SHARED_SECRET", "") or "").strip()

    @method_decorator(csrf_exempt)
    def post(self, request, mailbox_id: int, *args, **kwargs):
        mailbox = MailboxConfig.objects.filter(
            pk=mailbox_id,
            is_active=True,
            is_inbound_enabled=True,
            webhook_enabled=True,
        ).first()
        if mailbox is None:
            return JsonResponse({"accepted": False, "error": "mailbox_not_found"}, status=404)

        expected_token = self._resolve_expected_token(mailbox)
        if not expected_token:
            return JsonResponse({"accepted": False, "error": "webhook_token_not_configured"}, status=403)

        received_token = str(request.headers.get("X-Webhook-Token") or "").strip()
        if not compare_digest(received_token, expected_token):
            return JsonResponse({"accepted": False, "error": "invalid_webhook_token"}, status=403)

        try:
            payload = json.loads((request.body or b"{}").decode("utf-8"))
        except (ValueError, TypeError):
            return JsonResponse({"accepted": False, "error": "invalid_json_payload"}, status=400)

        ingest_webhook_payload_task.delay(mailbox_id=mailbox_id, payload=payload)
        return JsonResponse({"accepted": True})


class TriggerPollingView(PermissionRequiredMixin, View):
    """Manual trigger endpoint for polling fallback ingestion."""

    required_permission = "email.manage"

    def post(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        poll_mailboxes_task.delay(tenant_id=getattr(tenant, "pk", None))
        return JsonResponse({"queued": True})
