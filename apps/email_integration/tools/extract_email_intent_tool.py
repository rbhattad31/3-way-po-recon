"""Tool to classify and infer intent for an email message."""
from apps.tools.registry.base import BaseTool, ToolResult, register_tool

from apps.email_integration.models import EmailMessage
from apps.email_integration.services.classification_service import ClassificationService


@register_tool
class ExtractEmailIntentTool(BaseTool):
    name = "extract_email_intent"
    description = "Extract deterministic classification and intent for email content."
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
        classification = ClassificationService.classify(message.subject, message.body_text)
        intent = ClassificationService.infer_intent(classification)
        return ToolResult(success=True, data={"found": True, "classification": classification, "intent": intent})
