"""Gmail adapter placeholder implementation."""
from __future__ import annotations

from typing import Dict, List, Optional

from apps.email_integration.services.provider_adapters.base import BaseEmailProviderAdapter


class GmailEmailAdapter(BaseEmailProviderAdapter):
    """Adapter implementation scaffold for Gmail APIs."""

    def subscribe_mailbox(self, mailbox_config) -> Dict[str, object]:
        return {"subscribed": True, "provider": "GMAIL", "mailbox_id": mailbox_config.pk}

    def poll_messages(self, mailbox_config, since_cursor: Optional[str] = None) -> List[Dict[str, object]]:
        return []

    def get_message(self, mailbox_config, provider_message_id: str) -> Dict[str, object]:
        return {"id": provider_message_id}

    def get_attachments(self, mailbox_config, provider_message_id: str) -> List[Dict[str, object]]:
        return []

    def send_message(self, mailbox_config, payload: Dict[str, object]) -> Dict[str, object]:
        return {"sent": True, "provider_message_id": payload.get("provider_message_id", "")}
