"""Tool to send governed outbound templated email."""
from apps.tools.registry.base import BaseTool, ToolResult, register_tool

from apps.email_integration.models import MailboxConfig
from apps.email_integration.services.outbound_email_service import OutboundEmailService


@register_tool
class SendTemplatedEmailTool(BaseTool):
    name = "send_templated_email"
    description = "Send email using approved templates and mailbox configuration."
    required_permission = "email.send"
    parameters_schema = {
        "type": "object",
        "properties": {
            "mailbox_id": {"type": "integer"},
            "template_code": {"type": "string"},
            "to_recipients": {"type": "array", "items": {"type": "string"}},
            "variables": {"type": "object"},
        },
        "required": ["mailbox_id", "template_code", "to_recipients"],
    }

    def run(self, *, mailbox_id: int, template_code: str, to_recipients: list, variables=None, **kwargs) -> ToolResult:
        mailbox = self._scoped(MailboxConfig.objects.filter(pk=mailbox_id, is_active=True, is_outbound_enabled=True)).first()
        if mailbox is None:
            return ToolResult(success=False, error="Active outbound mailbox not found")
        result = OutboundEmailService.send_templated_email(
            tenant=getattr(self, "_tenant", None),
            mailbox=mailbox,
            template_code=template_code,
            variables=variables or {},
            to_recipients=to_recipients,
            actor_user=None,
            trace_id=kwargs.get("trace_id", ""),
        )
        return ToolResult(success=True, data=result)
