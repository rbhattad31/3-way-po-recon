"""Procurement wrapper tool: link email message to a procurement request."""
from __future__ import annotations

from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import EmailMessage
from apps.email_integration.enums import EmailActionStatus, EmailActionType, EmailDomainContext, EmailLinkStatus


class AttachEmailToProcurementRequestTool(BaseTool):
    name = "attach_email_to_procurement_request"
    description = (
        "Link an email message or thread to an existing procurement request. "
        "Updates thread domain context and records an EmailAction."
    )
    required_permission = "email.route"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer", "description": "PK of the EmailMessage"},
            "procurement_request_id": {"type": "integer", "description": "PK of the ProcurementRequest"},
        },
        "required": ["email_message_id", "procurement_request_id"],
    }

    def run(self, *, email_message_id: int, procurement_request_id: int, **kwargs) -> ToolResult:
        from apps.email_integration.models import EmailAction

        message = self._scoped(EmailMessage.objects.filter(pk=email_message_id)).first()
        if not message:
            return ToolResult(success=False, error=f"EmailMessage {email_message_id} not found")

        # Lazy import to avoid circular dependency
        try:
            from apps.procurement.models import ProcurementRequest
            req = self._scoped(ProcurementRequest.objects.filter(pk=procurement_request_id)).first()
        except ImportError:
            req = None

        if req is None:
            return ToolResult(success=False, error=f"ProcurementRequest {procurement_request_id} not found")

        message.matched_entity_type = "PROCUREMENT_REQUEST"
        message.matched_entity_id = procurement_request_id
        message.save(update_fields=["matched_entity_type", "matched_entity_id"])

        if message.thread:
            thread = message.thread
            thread.domain_context = EmailDomainContext.PROCUREMENT
            thread.link_status = EmailLinkStatus.LINKED
            thread.primary_procurement_request_id = procurement_request_id
            thread.save(update_fields=["domain_context", "link_status", "primary_procurement_request_id"])

        EmailAction.objects.create(
            tenant=message.tenant,
            email_message=message,
            thread=message.thread,
            action_type=EmailActionType.LINK_TO_PROCUREMENT_REQUEST,
            action_status=EmailActionStatus.COMPLETED,
            target_entity_type="PROCUREMENT_REQUEST",
            target_entity_id=str(procurement_request_id),
            trace_id=message.trace_id or kwargs.get("trace_id", ""),
            payload_json={"email_message_id": email_message_id, "procurement_request_id": procurement_request_id},
            result_json={"linked": True},
        )

        return ToolResult(
            success=True,
            data={
                "linked": True,
                "email_message_id": email_message_id,
                "procurement_request_id": procurement_request_id,
            },
        )
