from django.contrib import admin
from django.utils.html import format_html

from apps.auditlog.models import ProcessingLog, AuditEvent, FileProcessingStatus


@admin.register(ProcessingLog)
class ProcessingLogAdmin(admin.ModelAdmin):
    list_display = ("id", "level_badge", "source", "event", "message_short", "invoice_id", "trace_id", "created_at")
    list_filter = ("level", "source")
    search_fields = ("event", "message", "trace_id")
    list_per_page = 50
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Log Entry", {"fields": ("level", "source", "event", "message")}),
        ("Context", {"fields": ("invoice_id", "reconciliation_result_id", "agent_run_id", "trace_id", "user")}),
        ("Details", {"fields": ("details",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Level")
    def level_badge(self, obj):
        colours = {
            "DEBUG": "#6c757d", "INFO": "#0d6efd",
            "WARNING": "#ffc107", "ERROR": "#dc3545", "CRITICAL": "#dc3545",
        }
        c = colours.get(obj.level, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            c, obj.level,
        )

    @admin.display(description="Message")
    def message_short(self, obj):
        return obj.message[:120]


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "entity_type", "entity_id", "action", "performed_by", "ip_address", "created_at")
    list_filter = ("entity_type", "action")
    search_fields = ("entity_type", "action", "ip_address")
    list_per_page = 50
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Entity", {"fields": ("entity_type", "entity_id", "action")}),
        ("Changes", {"fields": ("old_values", "new_values")}),
        ("Actor", {"fields": ("performed_by", "ip_address", "user_agent")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(FileProcessingStatus)
class FileProcessingStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "document_upload", "stage", "status_badge", "started_at", "completed_at", "duration_display")
    list_filter = ("stage", "status")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("File", {"fields": ("document_upload",)}),
        ("Processing", {"fields": ("stage", "status", "message")}),
        ("Timing", {"fields": ("started_at", "completed_at")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {"QUEUED": "#6c757d", "PROCESSING": "#0d6efd", "COMPLETED": "#198754", "FAILED": "#dc3545"}
        c = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            c, obj.status,
        )

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.started_at and obj.completed_at:
            delta = obj.completed_at - obj.started_at
            return f"{delta.total_seconds():.1f}s"
        return "-"
