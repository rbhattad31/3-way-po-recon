from django.contrib import admin
from apps.auditlog.models import ProcessingLog, AuditEvent, FileProcessingStatus


@admin.register(ProcessingLog)
class ProcessingLogAdmin(admin.ModelAdmin):
    list_display = ("id", "level", "source", "event", "message_short", "invoice_id", "trace_id", "created_at")
    list_filter = ("level", "source")
    search_fields = ("event", "message", "trace_id")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Message")
    def message_short(self, obj):
        return obj.message[:120]


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "entity_type", "entity_id", "action", "performed_by", "created_at")
    list_filter = ("entity_type", "action")
    search_fields = ("entity_type", "action")
    readonly_fields = ("created_at", "updated_at")


@admin.register(FileProcessingStatus)
class FileProcessingStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "document_upload", "stage", "status", "started_at", "completed_at")
    list_filter = ("stage", "status")
    readonly_fields = ("created_at", "updated_at")
