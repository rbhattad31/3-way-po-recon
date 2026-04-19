"""Mailbox configuration and provider adapter selection service."""
from __future__ import annotations

from typing import Optional

from django.utils import timezone

from apps.core.decorators import observed_service
from apps.email_integration.enums import EmailProvider
from apps.email_integration.models import MailboxConfig
from apps.email_integration.services.provider_adapters.gmail_adapter import GmailEmailAdapter
from apps.email_integration.services.provider_adapters.microsoft_graph_adapter import MicrosoftGraphEmailAdapter


class MailboxService:
    """Operations around mailbox configs and adapter resolution."""

    @staticmethod
    def get_adapter(mailbox: MailboxConfig):
        if mailbox.provider == EmailProvider.MICROSOFT_365:
            return MicrosoftGraphEmailAdapter()
        if mailbox.provider == EmailProvider.GMAIL:
            return GmailEmailAdapter()
        raise ValueError(f"Unsupported mailbox provider: {mailbox.provider}")

    @staticmethod
    def get_mailbox(mailbox_id: int, *, tenant=None) -> Optional[MailboxConfig]:
        qs = MailboxConfig.objects.filter(pk=mailbox_id, is_active=True)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return qs.first()

    @staticmethod
    @observed_service("email.mailbox.sync")
    def sync_mailbox(mailbox: MailboxConfig) -> dict:
        adapter = MailboxService.get_adapter(mailbox)
        result = adapter.subscribe_mailbox(mailbox)
        mailbox.last_sync_at = timezone.now()
        mailbox.last_success_at = timezone.now()
        mailbox.last_error_message = ""
        mailbox.save(update_fields=["last_sync_at", "last_success_at", "last_error_message", "updated_at"])
        return result

    @staticmethod
    @observed_service("email.mailbox.test")
    def test_mailbox(mailbox: MailboxConfig) -> dict:
        adapter = MailboxService.get_adapter(mailbox)
        result = {
            "provider": mailbox.provider,
            "mailbox_id": mailbox.pk,
            "subscribed": adapter.subscribe_mailbox(mailbox).get("subscribed", False),
            "timestamp": timezone.now().isoformat(),
        }
        mailbox.last_success_at = timezone.now()
        mailbox.last_error_message = ""
        mailbox.save(update_fields=["last_success_at", "last_error_message", "updated_at"])
        return result
