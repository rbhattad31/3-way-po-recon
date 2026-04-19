"""Tool to list attachments for an email message."""
from apps.tools.registry.base import BaseTool, ToolResult

from apps.email_integration.models import EmailAttachment


class EmailAttachmentListTool(BaseTool):
    name = "email_attachment_list"
    description = "List normalized attachments for a given email message."
    required_permission = "email.view"
    parameters_schema = {
        "type": "object",
        "properties": {
            "email_message_id": {"type": "integer"},
        },
        "required": ["email_message_id"],
    }

    def run(self, *, email_message_id: int, **kwargs) -> ToolResult:
        attachments = self._scoped(EmailAttachment.objects.filter(email_message_id=email_message_id)).values(
            "id", "filename", "content_type", "size_bytes", "processing_status", "scan_status"
        )
        return ToolResult(success=True, data={"email_message_id": email_message_id, "attachments": list(attachments)})
