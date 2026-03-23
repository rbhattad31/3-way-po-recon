from django.contrib import admin
from django.utils.html import format_html

from apps.extraction.models import ExtractionApproval, ExtractionFieldCorrection, ExtractionResult


@admin.register(ExtractionResult)
class ExtractionResultAdmin(admin.ModelAdmin):
    list_display = (
        "id", "document_upload", "invoice", "engine_name", "engine_version",
        "confidence_display", "success_badge", "duration_display", "created_at",
    )
    list_filter = ("success", "engine_name", "engine_version")
    search_fields = ("document_upload__original_filename", "error_message")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Links", {"fields": ("document_upload", "invoice")}),
        ("Engine", {"fields": ("engine_name", "engine_version")}),
        ("Result", {"fields": ("success", "confidence", "duration_ms", "error_message")}),
        ("Raw Data", {"fields": ("raw_response",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.confidence is None:
            return "-"
        pct = obj.confidence * 100
        colour = "#198754" if pct >= 75 else ("#ffc107" if pct >= 50 else "#dc3545")
        return format_html('<span style="color:{}">{:.0f}%</span>', colour, pct)

    @admin.display(description="OK", boolean=True)
    def success_badge(self, obj):
        return obj.success

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.duration_ms is None:
            return "-"
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms}ms"
        return f"{obj.duration_ms / 1000:.1f}s"


class ExtractionFieldCorrectionInline(admin.TabularInline):
    model = ExtractionFieldCorrection
    extra = 0
    readonly_fields = (
        "entity_type", "entity_id", "field_name",
        "original_value", "corrected_value", "corrected_by", "created_at",
    )


@admin.register(ExtractionApproval)
class ExtractionApprovalAdmin(admin.ModelAdmin):
    list_display = (
        "id", "invoice", "status_badge", "confidence_display",
        "fields_corrected_count", "is_touchless", "reviewed_by", "reviewed_at", "created_at",
    )
    list_filter = ("status", "is_touchless")
    search_fields = ("invoice__invoice_number", "invoice__raw_vendor_name")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [ExtractionFieldCorrectionInline]
    fieldsets = (
        ("Links", {"fields": ("invoice", "extraction_result")}),
        ("Decision", {"fields": ("status", "reviewed_by", "reviewed_at", "rejection_reason")}),
        ("Metrics", {"fields": ("confidence_at_review", "fields_corrected_count", "is_touchless")}),
        ("Snapshot", {"fields": ("original_values_snapshot",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "PENDING": "#ffc107",
            "APPROVED": "#198754",
            "AUTO_APPROVED": "#0dcaf0",
            "REJECTED": "#dc3545",
        }
        colour = colours.get(obj.status, "#6c757d")
        return format_html('<span style="color:{}">{}</span>', colour, obj.get_status_display())

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.confidence_at_review is None:
            return "-"
        pct = obj.confidence_at_review * 100
        colour = "#198754" if pct >= 75 else ("#ffc107" if pct >= 50 else "#dc3545")
        return format_html('<span style="color:{}">{:.0f}%</span>', colour, pct)
