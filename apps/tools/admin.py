from django.contrib import admin
from django.utils.html import format_html

from apps.tools.models import ToolDefinition, ToolCall


@admin.register(ToolDefinition)
class ToolDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled_badge", "module_path", "call_count", "created_at")
    list_filter = ("enabled",)
    search_fields = ("name", "description", "module_path")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("name", "description", "enabled", "module_path")}),
        ("Schemas", {"fields": ("input_schema", "output_schema"), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Enabled", boolean=True)
    def enabled_badge(self, obj):
        return obj.enabled

    @admin.display(description="Calls")
    def call_count(self, obj):
        return obj.calls.count()


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ("id", "tool_name", "agent_run", "status_badge", "duration_display", "created_at")
    list_filter = ("status", "tool_name")
    search_fields = ("tool_name", "error_message")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Links", {"fields": ("agent_run", "tool_definition")}),
        ("Call", {"fields": ("tool_name", "status", "duration_ms")}),
        ("Payloads", {"fields": ("input_payload", "output_payload"), "classes": ("collapse",)}),
        ("Error", {"fields": ("error_message",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {"REQUESTED": "#6c757d", "SUCCESS": "#198754", "FAILED": "#dc3545"}
        c = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            c, obj.get_status_display(),
        )

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.duration_ms is None:
            return "-"
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms}ms"
        return f"{obj.duration_ms / 1000:.1f}s"
