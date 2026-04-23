"""Tool to infer entity links from message content."""
from apps.tools.registry.base import BaseTool, ToolResult, register_tool

from apps.email_integration.models import EmailMessage
from apps.email_integration.services.entity_linking_service import EntityLinkingService


@register_tool
class MatchEmailToEntityTool(BaseTool):
    name = "match_email_to_entity"
    description = "Infer AP or procurement entity references from email text."
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
        data = EntityLinkingService.infer_entity(message.subject, message.body_text)
        data["found"] = bool(data.get("entity_id"))
        return ToolResult(success=True, data=data)
