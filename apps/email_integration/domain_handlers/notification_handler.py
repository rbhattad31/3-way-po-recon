"""Notification-only handler for non-transactional emails."""
from __future__ import annotations

from apps.email_integration.domain_handlers.base_handler import BaseEmailDomainHandler
from apps.email_integration.enums import EmailActionStatus, EmailActionType
from apps.email_integration.models import EmailAction
from apps.email_integration.enums import TargetDomain


class NotificationEmailHandler(BaseEmailDomainHandler):
    """Records notification-only handling with governed action logs."""

    handler_name = "notification_handler"

    def can_handle(self, email_message, routing_decision) -> bool:
        return routing_decision.target_domain in [TargetDomain.TRIAGE, TargetDomain.NOTIFICATION_ONLY]

    def handle(self, email_message, routing_decision, *, actor_user=None):
        is_triage = routing_decision.target_domain == TargetDomain.TRIAGE
        action = EmailAction.objects.create(
            tenant=email_message.tenant,
            email_message=email_message,
            thread=email_message.thread,
            action_type=EmailActionType.QUEUE_FOR_TRIAGE if is_triage else EmailActionType.IGNORE_EMAIL,
            action_status=EmailActionStatus.PENDING if is_triage else EmailActionStatus.COMPLETED,
            performed_by_user=actor_user,
            actor_primary_role=self._actor_role(actor_user),
            target_entity_type=routing_decision.target_entity_type,
            target_entity_id=routing_decision.target_entity_id,
            trace_id=email_message.trace_id,
            payload_json=self._payload_base(email_message, routing_decision),
            result_json={**self._result_base(email_message, routing_decision), "triage_required": is_triage},
        )
        return {"handled": True, "action_id": action.pk, "action_type": action.action_type}

    def process(self, email_message, routing_decision, *, actor_user=None):
        return super().process(email_message, routing_decision, actor_user=actor_user)
