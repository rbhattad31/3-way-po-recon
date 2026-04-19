"""Tool to return deterministic body preview summary for an email."""
from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import EmailMessage


class EmailBodySummaryTool(BaseTool):
    name = "email_body_summary"
    description = "Return deterministic preview summary for an email body."
    required_permission = "email.view"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer"},
        },
        "required": ["email_message_id"],
    }

    def run(self, *, email_message_id: int, **kwargs) -> ToolResult:
        message = self._scoped(EmailMessage.objects.filter(pk=email_message_id)).first()
        if not message:
            return ToolResult(success=True, data={"found": False, "email_message_id": email_message_id})
        return ToolResult(success=True, data={"found": True, "summary": message.body_preview, "subject": message.subject})
