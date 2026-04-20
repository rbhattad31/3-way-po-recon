"""AP wrapper tool: link an email message to an existing AP case."""
from __future__ import annotations

from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import EmailMessage
from apps.email_integration.enums import EmailDomainContext, EmailLinkStatus


class AttachEmailToCaseTool(BaseTool):
    name = "attach_email_to_case"
    description = "Link an email message or thread to an existing AP case. Records an EmailAction and updates thread domain context."
    required_permission = "email.route"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer", "description": "PK of the EmailMessage to attach"},
            "ap_case_id": {"type": "integer", "description": "PK of the target APCase"},
        },
        "required": ["email_message_id", "ap_case_id"],
    }

    def run(self, *, email_message_id: int, ap_case_id: int, **kwargs) -> ToolResult:
        from apps.email_integration.enums import EmailActionStatus, EmailActionType
        from apps.email_integration.models import EmailAction
        from apps.cases.models import APCase

        message = self._scoped(EmailMessage.objects.filter(pk=email_message_id)).first()
        if not message:
            return ToolResult(success=False, error=f"EmailMessage {email_message_id} not found")

        case = self._scoped(APCase.objects.filter(pk=ap_case_id)).first()
        if not case:
            return ToolResult(success=False, error=f"APCase {ap_case_id} not found")

        # Update message link fields
        message.matched_entity_type = "AP_CASE"
        message.matched_entity_id = ap_case_id
        message.save(update_fields=["matched_entity_type", "matched_entity_id"])

        # Update thread domain context if present
        if message.thread:
            thread = message.thread
            thread.domain_context = EmailDomainContext.AP
            thread.link_status = EmailLinkStatus.LINKED
            thread.primary_case_id = ap_case_id
            thread.save(update_fields=["domain_context", "link_status", "primary_case_id"])

        # Persist action record
        EmailAction.objects.create(
            tenant=message.tenant,
            email_message=message,
            thread=message.thread,
            action_type=EmailActionType.LINK_TO_AP_CASE,
            action_status=EmailActionStatus.COMPLETED,
            target_entity_type="AP_CASE",
            target_entity_id=str(ap_case_id),
            trace_id=message.trace_id or kwargs.get("trace_id", ""),
            payload_json={"email_message_id": email_message_id, "ap_case_id": ap_case_id},
            result_json={"linked": True, "case_number": getattr(case, "case_number", None)},
        )

        return ToolResult(
            success=True,
            data={
                "linked": True,
                "email_message_id": email_message_id,
                "ap_case_id": ap_case_id,
                "case_number": getattr(case, "case_number", None),
            },
        )
