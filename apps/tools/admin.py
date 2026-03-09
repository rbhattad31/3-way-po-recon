from django.contrib import admin
from apps.tools.models import ToolDefinition, ToolCall


@admin.register(ToolDefinition)
class ToolDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "module_path", "created_at")
    list_filter = ("enabled",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("id", "tool_name", "agent_run", "status", "duration_ms", "created_at")
    list_filter = ("status", "tool_name")
    search_fields = ("tool_name",)
    readonly_fields = ("created_at", "updated_at")
