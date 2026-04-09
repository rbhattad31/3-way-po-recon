from django.contrib import admin
from django.utils.html import format_html

from apps.auditlog.models import ProcessingLog, AuditEvent


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
    list_display = ("id", "entity_type", "entity_id", "event_type_display", "action", "performed_by", "performed_by_agent", "created_at")
    list_filter = ("entity_type", "action", "event_type")
    search_fields = ("entity_type", "action", "event_type", "event_description", "performed_by_agent")
    list_per_page = 50
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "entity_type", "entity_id", "action", "event_type",
                       "event_description", "performed_by", "performed_by_agent", "metadata_json",
                       "old_values", "new_values", "ip_address", "user_agent")
    fieldsets = (
        ("Entity", {"fields": ("entity_type", "entity_id", "action")}),
        ("Governance Event", {"fields": ("event_type", "event_description", "performed_by_agent", "metadata_json")}),
        ("Changes", {"fields": ("old_values", "new_values"), "classes": ("collapse",)}),
        ("Actor", {"fields": ("performed_by", "ip_address", "user_agent")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Event Type")
    def event_type_display(self, obj):
        if obj.event_type:
            colours = {
                "INVOICE_UPLOADED": "#0d6efd",
                "EXTRACTION_COMPLETED": "#198754",
                "EXTRACTION_FAILED": "#dc3545",
                "VALIDATION_FAILED": "#ffc107",
                "RECONCILIATION_STARTED": "#0d6efd",
                "RECONCILIATION_COMPLETED": "#198754",
                "AGENT_RECOMMENDATION_CREATED": "#6f42c1",
                "REVIEW_ASSIGNED": "#0dcaf0",
                "REVIEW_APPROVED": "#198754",
                "REVIEW_REJECTED": "#dc3545",
            }
            c = colours.get(obj.event_type, "#6c757d")
            return format_html(
                '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
                c, obj.event_type,
            )
        return obj.action

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False



