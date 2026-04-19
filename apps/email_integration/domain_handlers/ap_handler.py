"""AP domain handler for routed email messages."""
from __future__ import annotations

from apps.email_integration.domain_handlers.base_handler import BaseEmailDomainHandler
from apps.email_integration.enums import EmailActionStatus, EmailActionType
from apps.email_integration.models import EmailAction
from apps.email_integration.enums import TargetDomain


class APEmailHandler(BaseEmailDomainHandler):
    """Performs governed AP-side actions without bypassing AP services."""

    handler_name = "ap_handler"

    def can_handle(self, email_message, routing_decision) -> bool:
        return routing_decision.target_domain == TargetDomain.AP

    def handle(self, email_message, routing_decision, *, actor_user=None):
        if routing_decision.target_entity_id or email_message.matched_entity_type == "AP_CASE":
            action_type = EmailActionType.LINK_TO_AP_CASE
        elif email_message.linked_document_upload_id:
            action_type = EmailActionType.TRIGGER_EXTRACTION
        else:
            action_type = EmailActionType.CREATE_DOCUMENT_UPLOAD

        action = EmailAction.objects.create(
            tenant=email_message.tenant,
            email_message=email_message,
            thread=email_message.thread,
            action_type=action_type,
            action_status=EmailActionStatus.COMPLETED,
            performed_by_user=actor_user,
            actor_primary_role=self._actor_role(actor_user),
            target_entity_type=routing_decision.target_entity_type,
            target_entity_id=routing_decision.target_entity_id,
            trace_id=email_message.trace_id,
            payload_json=self._payload_base(email_message, routing_decision),
            result_json=self._result_base(email_message, routing_decision),
        )
        return {"handled": True, "action_id": action.pk, "action_type": action.action_type}

    def process(self, email_message, routing_decision, *, actor_user=None):
        return super().process(email_message, routing_decision, actor_user=actor_user)
