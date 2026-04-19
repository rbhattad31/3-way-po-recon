"""Base interface for email provider adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseEmailProviderAdapter(ABC):
    """Abstract adapter contract for inbound/outbound provider implementations."""

    @abstractmethod
    def subscribe_mailbox(self, mailbox_config) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def poll_messages(self, mailbox_config, since_cursor: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_message(self, mailbox_config, provider_message_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_attachments(self, mailbox_config, provider_message_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def send_message(self, mailbox_config, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
