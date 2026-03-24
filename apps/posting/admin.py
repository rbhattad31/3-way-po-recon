"""Admin registration for posting app models."""
from django.contrib import admin

from apps.posting.models import InvoicePosting, InvoicePostingFieldCorrection


class InvoicePostingFieldCorrectionInline(admin.TabularInline):
    model = InvoicePostingFieldCorrection
    extra = 0
    readonly_fields = ("entity_type", "entity_id", "field_name", "original_value", "corrected_value", "corrected_by", "reason", "created_at")


@admin.register(InvoicePosting)
class InvoicePostingAdmin(admin.ModelAdmin):
    list_display = ("id", "invoice", "status", "stage", "posting_confidence", "review_queue", "is_touchless", "erp_document_number", "created_at")
    list_filter = ("status", "review_queue", "is_touchless")
    search_fields = ("invoice__invoice_number", "erp_document_number")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("invoice", "extraction_result", "extraction_run", "reviewed_by")
    inlines = [InvoicePostingFieldCorrectionInline]
