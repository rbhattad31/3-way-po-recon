"""Base handler contract for routed email domain processing."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseEmailDomainHandler(ABC):
    """Domain handler contract for AP/Procurement/Notification processing."""

    handler_name = "base_handler"

    @abstractmethod
    def can_handle(self, email_message, routing_decision) -> bool:
        raise NotImplementedError

    @abstractmethod
    def handle(self, email_message, routing_decision, *, actor_user=None) -> Dict[str, Any]:
        raise NotImplementedError

    def process(self, email_message, routing_decision, *, actor_user=None):
        if not self.can_handle(email_message, routing_decision):
            raise ValueError(f"{self.handler_name} cannot handle target domain={routing_decision.target_domain}")
        return self.handle(email_message, routing_decision, actor_user=actor_user)

    @staticmethod
    def _actor_role(actor_user) -> str:
        return (getattr(actor_user, "role", "") or "") if actor_user else ""

    @staticmethod
    def _payload_base(email_message, routing_decision) -> Dict[str, Any]:
        return {
            "handler": routing_decision.target_handler,
            "classification": email_message.message_classification,
            "intent": email_message.intent_type,
            "target_entity_type": routing_decision.target_entity_type,
            "target_entity_id": routing_decision.target_entity_id,
        }

    @staticmethod
    def _result_base(email_message, routing_decision) -> Dict[str, Any]:
        return {
            "message_id": email_message.pk,
            "thread_id": email_message.thread_id,
            "target_domain": routing_decision.target_domain,
            "linked_document_upload_id": email_message.linked_document_upload_id,
        }
