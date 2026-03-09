from django.contrib import admin
from apps.extraction.models import ExtractionResult


@admin.register(ExtractionResult)
class ExtractionResultAdmin(admin.ModelAdmin):
    list_display = ("id", "document_upload", "invoice", "engine_name", "confidence", "success", "duration_ms", "created_at")
    list_filter = ("success", "engine_name")
    search_fields = ("document_upload__original_filename",)
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
