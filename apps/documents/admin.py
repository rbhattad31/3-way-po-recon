from django.contrib import admin
from django.utils.html import format_html

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
    fields = (
        "line_number", "description", "quantity", "unit_price",
        "tax_amount", "line_amount", "extraction_confidence",
    )
    readonly_fields = ("created_at",)
    show_change_link = True


class POLineInline(admin.TabularInline):
    model = PurchaseOrderLineItem
    extra = 0
    fields = (
        "line_number", "item_code", "description", "quantity",
        "unit_price", "tax_amount", "line_amount", "unit_of_measure",
    )
    show_change_link = True


class GRNLineInline(admin.TabularInline):
    model = GRNLineItem
    extra = 0
    fields = (
        "line_number", "item_code", "description",
        "quantity_received", "quantity_accepted", "quantity_rejected",
        "po_line", "unit_of_measure",
    )
    show_change_link = True


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@admin.register(DocumentUpload)
class DocumentUploadAdmin(admin.ModelAdmin):
    list_display = (
        "id", "original_filename", "document_type", "state_badge",
        "file_size_display", "uploaded_by", "created_at",
    )
    list_filter = ("document_type", "processing_state")
    search_fields = ("original_filename", "file_hash")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "file_hash", "file_size", "content_type", "created_by", "updated_by")
    fieldsets = (
        ("File", {"fields": ("file", "original_filename", "content_type", "file_size", "file_hash")}),
        ("Classification", {"fields": ("document_type", "processing_state", "processing_message")}),
        ("Audit", {"fields": ("uploaded_by", "created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="State")
    def state_badge(self, obj):
        colours = {
            "QUEUED": "#6c757d", "PROCESSING": "#0d6efd",
            "COMPLETED": "#198754", "FAILED": "#dc3545",
        }
        c = colours.get(obj.processing_state, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_processing_state_display(),
        )

    @admin.display(description="Size")
    def file_size_display(self, obj):
        if obj.file_size < 1024:
            return f"{obj.file_size} B"
        elif obj.file_size < 1024 * 1024:
            return f"{obj.file_size / 1024:.1f} KB"
        return f"{obj.file_size / (1024 * 1024):.2f} MB"


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "id", "invoice_number", "vendor", "po_number", "total_amount",
        "currency", "status_badge", "duplicate_flag", "confidence_display", "created_at",
    )
    list_filter = ("status", "is_duplicate", "currency", "reprocessed")
    search_fields = ("invoice_number", "normalized_invoice_number", "po_number", "raw_vendor_name", "vendor__name")
    list_per_page = 25
    date_hierarchy = "created_at"
    inlines = [InvoiceLineInline]
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": (
            "invoice_number", "normalized_invoice_number", "vendor", "document_upload",
        )}),
        ("Raw Extracted", {"fields": (
            "raw_vendor_name", "raw_invoice_number", "raw_invoice_date",
            "raw_po_number", "raw_currency", "raw_subtotal", "raw_tax_amount", "raw_total_amount",
        ), "classes": ("collapse",)}),
        ("Normalized", {"fields": (
            "invoice_date", "po_number", "normalized_po_number", "currency",
            "subtotal", "tax_amount", "total_amount",
        )}),
        ("Status & Flags", {"fields": ("status", "is_duplicate", "duplicate_of", "reprocessed", "reprocessed_from")}),
        ("Extraction", {"fields": ("extraction_confidence", "extraction_remarks", "extraction_raw_json"), "classes": ("collapse",)}),
        ("Audit", {"fields": ("notes", "created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )
    actions = ["mark_ready_for_recon"]

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "UPLOADED": "#6c757d", "EXTRACTION_IN_PROGRESS": "#0d6efd",
            "EXTRACTED": "#17a2b8", "VALIDATED": "#20c997",
            "INVALID": "#dc3545", "READY_FOR_RECON": "#ffc107",
            "RECONCILED": "#198754", "FAILED": "#dc3545",
        }
        c = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_status_display(),
        )

    @admin.display(description="Dup", boolean=True)
    def duplicate_flag(self, obj):
        return obj.is_duplicate

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.extraction_confidence is None:
            return "-"
        pct = obj.extraction_confidence * 100
        colour = "#198754" if pct >= 75 else ("#ffc107" if pct >= 50 else "#dc3545")
        return format_html('<span style="color:{}">{:.0f}%</span>', colour, pct)

    @admin.action(description="Mark selected as Ready for Reconciliation")
    def mark_ready_for_recon(self, request, queryset):
        queryset.filter(status__in=["VALIDATED", "EXTRACTED"]).update(status="READY_FOR_RECON")


@admin.register(InvoiceLineItem)
class InvoiceLineItemAdmin(admin.ModelAdmin):
    list_display = ("id", "invoice", "line_number", "description_short", "quantity", "unit_price", "line_amount")
    list_filter = ("invoice__status",)
    search_fields = ("description", "raw_description", "invoice__invoice_number")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Description")
    def description_short(self, obj):
        return (obj.description or obj.raw_description)[:80]


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "po_number", "vendor", "po_date", "total_amount", "currency",
        "status", "line_count", "grn_count", "created_at",
    )
    list_filter = ("status", "currency")
    search_fields = ("po_number", "normalized_po_number", "vendor__name", "vendor__code", "buyer_name")
    list_per_page = 25
    date_hierarchy = "po_date"
    inlines = [POLineInline]
    readonly_fields = ("normalized_po_number", "created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("po_number", "normalized_po_number", "vendor", "po_date")}),
        ("Financials", {"fields": ("currency", "total_amount", "tax_amount")}),
        ("Details", {"fields": ("status", "buyer_name", "department")}),
        ("Audit", {"fields": ("notes", "created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Lines")
    def line_count(self, obj):
        return obj.line_items.count()

    @admin.display(description="GRNs")
    def grn_count(self, obj):
        return obj.grns.count()


@admin.register(PurchaseOrderLineItem)
class PurchaseOrderLineItemAdmin(admin.ModelAdmin):
    list_display = ("id", "purchase_order", "line_number", "item_code", "description_short", "quantity", "unit_price", "line_amount")
    search_fields = ("item_code", "description", "purchase_order__po_number")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Description")
    def description_short(self, obj):
        return obj.description[:80]


@admin.register(GoodsReceiptNote)
class GoodsReceiptNoteAdmin(admin.ModelAdmin):
    list_display = (
        "grn_number", "purchase_order", "vendor", "receipt_date",
        "status", "warehouse", "line_count", "created_at",
    )
    list_filter = ("status",)
    search_fields = ("grn_number", "purchase_order__po_number", "vendor__name", "warehouse")
    list_per_page = 25
    date_hierarchy = "receipt_date"
    inlines = [GRNLineInline]
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("grn_number", "purchase_order", "vendor", "receipt_date")}),
        ("Details", {"fields": ("status", "warehouse", "receiver_name")}),
        ("Audit", {"fields": ("notes", "created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Lines")
    def line_count(self, obj):
        return obj.line_items.count()


@admin.register(GRNLineItem)
class GRNLineItemAdmin(admin.ModelAdmin):
    list_display = ("id", "grn", "line_number", "item_code", "quantity_received", "quantity_accepted", "quantity_rejected")
    search_fields = ("item_code", "description", "grn__grn_number")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")
