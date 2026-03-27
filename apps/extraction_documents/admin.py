"""Admin configuration for extraction_documents models."""
from django.contrib import admin

from apps.extraction_documents.models import ExtractionDocument, ExtractionFieldResult


class ExtractionFieldResultInline(admin.TabularInline):
    model = ExtractionFieldResult
    extra = 0
    readonly_fields = [
        "field_key", "raw_value", "normalized_value", "confidence",
        "extraction_method", "source_text_snippet", "page_number",
        "line_item_index", "is_valid", "validation_message",
    ]


@admin.register(ExtractionDocument)
class ExtractionDocumentAdmin(admin.ModelAdmin):
    list_display = [
        "file_name",
        "status",
        "classified_document_type",
        "resolved_jurisdiction",
        "extraction_confidence",
        "is_valid",
        "created_at",
    ]
    list_filter = ["status", "classified_document_type", "is_valid", "extraction_method"]
    search_fields = ["file_name", "file_hash"]
    readonly_fields = ["created_at", "updated_at", "duration_ms"]
    raw_id_fields = ["document_upload", "resolved_jurisdiction", "resolved_schema"]
    inlines = [ExtractionFieldResultInline]
    fieldsets = (
        (None, {
            "fields": (
                "file_name", "file_path", "file_hash", "page_count",
                "document_upload", "status",
            ),
        }),
        ("Jurisdiction", {
            "fields": (
                "resolved_jurisdiction", "resolved_schema",
                "jurisdiction_confidence", "jurisdiction_signals_json",
            ),
        }),
        ("Classification", {
            "fields": ("classified_document_type", "classification_confidence"),
        }),
        ("Extraction", {
            "fields": (
                "ocr_text", "ocr_engine", "extracted_data_json",
                "extraction_confidence", "extraction_method",
            ),
        }),
        ("Validation", {
            "fields": (
                "is_valid", "validation_errors_json", "validation_warnings_json",
            ),
        }),
        ("Timing", {
            "fields": (
                "extraction_started_at", "extraction_completed_at", "duration_ms",
            ),
        }),
        ("Error", {
            "fields": ("error_message",),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ExtractionFieldResult)
class ExtractionFieldResultAdmin(admin.ModelAdmin):
    list_display = [
        "document",
        "field_key",
        "raw_value",
        "normalized_value",
        "confidence",
        "extraction_method",
        "is_valid",
    ]
    list_filter = ["extraction_method", "is_valid"]
    search_fields = ["field_key", "raw_value", "normalized_value"]
    raw_id_fields = ["document", "field_definition"]
