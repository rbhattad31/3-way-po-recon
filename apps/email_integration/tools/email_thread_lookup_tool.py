"""Tool to fetch thread details by thread id."""
from apps.tools.registry.base import BaseTool, ToolResult, register_tool

from apps.email_integration.models import EmailThread


@register_tool
class EmailThreadLookupTool(BaseTool):
    name = "email_thread_lookup"
    description = "Look up email thread metadata and context by thread id."
    required_permission = "email.view"
    parameters_schema = {
        "type": "object",
        "properties": {
            "thread_id": {"type": "integer"},
        },
        "required": ["thread_id"],
    }

    def run(self, *, thread_id: int, **kwargs) -> ToolResult:
        thread = self._scoped(EmailThread.objects.filter(pk=thread_id)).first()
        if not thread:
            return ToolResult(success=True, data={"found": False, "thread_id": thread_id})
        return ToolResult(
            success=True,
            data={
                "found": True,
                "thread_id": thread.pk,
                "status": thread.status,
                "domain_context": thread.domain_context,
                "message_count": thread.message_count,
                "last_message_at": str(thread.last_message_at) if thread.last_message_at else None,
            },
        )
