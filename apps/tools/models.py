"""Tool registry and tool-call tracking models."""
from django.db import models

from apps.core.enums import ToolCallStatus
from apps.core.models import BaseModel, TimestampMixin


class ToolDefinition(BaseModel):
    """Registry entry for an available tool that agents can invoke."""

    name = models.CharField(max_length=100, unique=True, db_index=True)
    description = models.TextField(blank=True, default="")
    input_schema = models.JSONField(null=True, blank=True, help_text="JSON Schema for tool input")
    output_schema = models.JSONField(null=True, blank=True, help_text="JSON Schema for tool output")
    enabled = models.BooleanField(default=True, db_index=True)
    module_path = models.CharField(max_length=300, blank=True, default="", help_text="Python dotted path to tool class")

    class Meta:
        db_table = "tools_definition"
        ordering = ["name"]
        verbose_name = "Tool Definition"
        verbose_name_plural = "Tool Definitions"

    def __str__(self) -> str:
        return self.name


class ToolCall(TimestampMixin):
    """Audit record for every tool invocation by an agent."""

    agent_run = models.ForeignKey(
        "agents.AgentRun", on_delete=models.CASCADE, related_name="tool_calls"
    )
    tool_definition = models.ForeignKey(
        ToolDefinition, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls"
    )
    tool_name = models.CharField(max_length=100, db_index=True)
    status = models.CharField(max_length=20, choices=ToolCallStatus.choices, default=ToolCallStatus.REQUESTED)
    input_payload = models.JSONField(null=True, blank=True)
    output_payload = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "tools_call"
        ordering = ["-created_at"]
        verbose_name = "Tool Call"
        verbose_name_plural = "Tool Calls"
        indexes = [
            models.Index(fields=["tool_name"], name="idx_toolcall_name"),
            models.Index(fields=["status"], name="idx_toolcall_status"),
            models.Index(fields=["agent_run"], name="idx_toolcall_agentrun"),
        ]

    def __str__(self) -> str:
        return f"ToolCall {self.tool_name} – {self.status} – AgentRun #{self.agent_run_id}"
