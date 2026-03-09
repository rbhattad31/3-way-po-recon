from django.contrib import admin
from apps.documents.models import (
    DocumentUpload,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
    GoodsReceiptNote,
    GRNLineItem,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    fields = ("line_number", "description", "quantity", "unit_price", "tax_amount", "line_amount")
    readonly_fields = ("created_at",)


class POLineInline(admin.TabularInline):
    model = PurchaseOrderLineItem
    extra = 0
    fields = ("line_number", "item_code", "description", "quantity", "unit_price", "tax_amount", "line_amount")


class GRNLineInline(admin.TabularInline):
    model = GRNLineItem
    extra = 0
    fields = ("line_number", "item_code", "description", "quantity_received", "quantity_accepted", "quantity_rejected")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@admin.register(DocumentUpload)
class DocumentUploadAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "document_type", "processing_state", "uploaded_by", "created_at")
    list_filter = ("document_type", "processing_state")
    search_fields = ("original_filename", "file_hash")
    readonly_fields = ("created_at", "updated_at", "file_hash", "file_size")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "id", "invoice_number", "vendor", "po_number", "total_amount",
        "currency", "status", "is_duplicate", "extraction_confidence", "created_at",
    )
    list_filter = ("status", "is_duplicate", "currency")
    search_fields = ("invoice_number", "normalized_invoice_number", "po_number", "raw_vendor_name")
    inlines = [InvoiceLineInline]
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("invoice_number", "normalized_invoice_number", "vendor", "document_upload")}),
        ("Raw Extracted", {"fields": (
            "raw_vendor_name", "raw_invoice_number", "raw_invoice_date",
            "raw_po_number", "raw_currency", "raw_subtotal", "raw_tax_amount", "raw_total_amount",
        )}),
        ("Normalized", {"fields": (
            "invoice_date", "po_number", "normalized_po_number", "currency",
            "subtotal", "tax_amount", "total_amount",
        )}),
        ("Status & Flags", {"fields": ("status", "is_duplicate", "duplicate_of", "reprocessed", "reprocessed_from")}),
        ("Extraction", {"fields": ("extraction_confidence", "extraction_remarks", "extraction_raw_json")}),
        ("Audit", {"fields": ("notes", "created_at", "updated_at", "created_by", "updated_by")}),
    )


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("po_number", "vendor", "po_date", "total_amount", "currency", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("po_number", "normalized_po_number", "vendor__name")
    inlines = [POLineInline]
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")


@admin.register(GoodsReceiptNote)
class GoodsReceiptNoteAdmin(admin.ModelAdmin):
    list_display = ("grn_number", "purchase_order", "vendor", "receipt_date", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("grn_number", "purchase_order__po_number", "vendor__name")
    inlines = [GRNLineInline]
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
