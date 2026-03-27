"""ERP Integration admin — register models for Django admin interface."""
from django.contrib import admin

from apps.erp_integration.models import (
    ERPConnection,
    ERPReferenceCacheRecord,
    ERPResolutionLog,
    ERPSubmissionLog,
)


@admin.register(ERPConnection)
class ERPConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "connector_type", "status", "is_default", "base_url", "created_at")
    list_filter = ("connector_type", "status", "is_default")
    search_fields = ("name", "base_url")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ERPReferenceCacheRecord)
class ERPReferenceCacheRecordAdmin(admin.ModelAdmin):
    list_display = ("cache_key", "resolution_type", "connector_name", "source_type", "expires_at", "created_at")
    list_filter = ("resolution_type", "source_type")
    search_fields = ("cache_key",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ERPResolutionLog)
class ERPResolutionLogAdmin(admin.ModelAdmin):
    list_display = (
        "resolution_type", "lookup_key", "source_type", "resolved",
        "fallback_used", "confidence", "connector_name", "duration_ms", "created_at",
    )
    list_filter = ("resolution_type", "source_type", "resolved", "fallback_used")
    search_fields = ("lookup_key", "reason")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("related_invoice", "related_reconciliation_result", "related_posting_run")


@admin.register(ERPSubmissionLog)
class ERPSubmissionLogAdmin(admin.ModelAdmin):
    list_display = (
        "submission_type", "status", "connector_name",
        "erp_document_number", "duration_ms", "created_at",
    )
    list_filter = ("submission_type", "status")
    search_fields = ("erp_document_number", "error_message")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("related_invoice", "related_posting_run")
