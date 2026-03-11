"""Document domain models: Invoice, PO, GRN and their line items."""
from django.conf import settings
from django.db import models

from apps.core.enums import DocumentType, FileProcessingState, InvoiceStatus
from apps.core.models import BaseModel, TimestampMixin
from apps.core.mixins import NotesMixin
from apps.vendors.models import Vendor


# ---------------------------------------------------------------------------
# Document Upload
# ---------------------------------------------------------------------------
class DocumentUpload(BaseModel):
    """Tracks every file uploaded into the system."""

    file = models.FileField(upload_to="invoices/%Y/%m/")
    original_filename = models.CharField(max_length=500)
    file_size = models.PositiveIntegerField(default=0, help_text="Bytes")
    file_hash = models.CharField(max_length=64, blank=True, db_index=True, help_text="SHA-256")
    content_type = models.CharField(max_length=100, blank=True)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices, default=DocumentType.INVOICE)
    processing_state = models.CharField(
        max_length=20, choices=FileProcessingState.choices, default=FileProcessingState.QUEUED
    )
    processing_message = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="uploads"
    )

    class Meta:
        db_table = "documents_upload"
        ordering = ["-created_at"]
        verbose_name = "Document Upload"
        verbose_name_plural = "Document Uploads"
        indexes = [
            models.Index(fields=["file_hash"], name="idx_upload_hash"),
            models.Index(fields=["processing_state"], name="idx_upload_state"),
            models.Index(fields=["document_type"], name="idx_upload_doctype"),
        ]

    def __str__(self) -> str:
        return f"Upload #{self.pk} – {self.original_filename}"


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
class Invoice(BaseModel, NotesMixin):
    """Invoice header — stores both raw extracted and normalized values."""

    document_upload = models.ForeignKey(
        DocumentUpload, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")

    # Raw extracted values
    raw_vendor_name = models.CharField(max_length=500, blank=True, default="")
    raw_invoice_number = models.CharField(max_length=100, blank=True, default="")
    raw_invoice_date = models.CharField(max_length=50, blank=True, default="")
    raw_po_number = models.CharField(max_length=100, blank=True, default="")
    raw_currency = models.CharField(max_length=20, blank=True, default="")
    raw_subtotal = models.CharField(max_length=50, blank=True, default="")
    raw_tax_amount = models.CharField(max_length=50, blank=True, default="")
    raw_total_amount = models.CharField(max_length=50, blank=True, default="")

    # Normalized values
    invoice_number = models.CharField(max_length=100, blank=True, db_index=True)
    normalized_invoice_number = models.CharField(max_length=100, blank=True, db_index=True)
    invoice_date = models.DateField(null=True, blank=True)
    po_number = models.CharField(max_length=100, blank=True, db_index=True)
    normalized_po_number = models.CharField(max_length=100, blank=True, db_index=True)
    currency = models.CharField(max_length=10, blank=True, default="USD")
    subtotal = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    # Status & flags
    status = models.CharField(max_length=30, choices=InvoiceStatus.choices, default=InvoiceStatus.UPLOADED, db_index=True)
    is_duplicate = models.BooleanField(default=False, db_index=True)
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates"
    )

    # Extraction metadata
    extraction_confidence = models.FloatField(null=True, blank=True, help_text="0.0 – 1.0")
    extraction_remarks = models.TextField(blank=True, default="")
    extraction_raw_json = models.JSONField(null=True, blank=True, help_text="Raw extraction output")

    # Reprocessing support
    reprocessed = models.BooleanField(default=False)
    reprocessed_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reprocessed_versions"
    )

    class Meta:
        db_table = "documents_invoice"
        ordering = ["-created_at"]
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        indexes = [
            models.Index(fields=["invoice_number"], name="idx_inv_number"),
            models.Index(fields=["normalized_invoice_number"], name="idx_inv_norm_number"),
            models.Index(fields=["po_number"], name="idx_inv_po"),
            models.Index(fields=["normalized_po_number"], name="idx_inv_norm_po"),
            models.Index(fields=["status"], name="idx_inv_status"),
            models.Index(fields=["vendor", "invoice_number"], name="idx_inv_vendor_num"),
        ]

    def __str__(self) -> str:
        return f"Invoice {self.invoice_number or '(no number)'} – {self.vendor or self.raw_vendor_name}"


class InvoiceLineItem(TimestampMixin):
    """Invoice line item — raw and normalized."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="line_items")
    line_number = models.PositiveIntegerField(default=1)

    # Raw extracted
    raw_description = models.TextField(blank=True, default="")
    raw_quantity = models.CharField(max_length=50, blank=True, default="")
    raw_unit_price = models.CharField(max_length=50, blank=True, default="")
    raw_tax_amount = models.CharField(max_length=50, blank=True, default="")
    raw_line_amount = models.CharField(max_length=50, blank=True, default="")

    # Normalized
    description = models.TextField(blank=True, default="")
    normalized_description = models.TextField(blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    extraction_confidence = models.FloatField(null=True, blank=True)

    # Item classification (for reconciliation mode resolution)
    item_category = models.CharField(max_length=100, blank=True, default="")
    is_service_item = models.BooleanField(null=True, blank=True)
    is_stock_item = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "documents_invoice_line"
        ordering = ["invoice", "line_number"]
        verbose_name = "Invoice Line Item"
        verbose_name_plural = "Invoice Line Items"
        indexes = [
            models.Index(fields=["invoice", "line_number"], name="idx_invline_num"),
        ]

    def __str__(self) -> str:
        return f"InvLine #{self.line_number} – {self.description[:60]}"


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------
class PurchaseOrder(BaseModel, NotesMixin):
    """Purchase Order header."""

    po_number = models.CharField(max_length=100, unique=True, db_index=True)
    normalized_po_number = models.CharField(max_length=100, blank=True, db_index=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_orders")
    po_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=10, default="USD")
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=30, default="OPEN", db_index=True)
    buyer_name = models.CharField(max_length=255, blank=True, default="")
    department = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "documents_purchase_order"
        ordering = ["-po_date"]
        verbose_name = "Purchase Order"
        verbose_name_plural = "Purchase Orders"
        indexes = [
            models.Index(fields=["po_number"], name="idx_po_number"),
            models.Index(fields=["normalized_po_number"], name="idx_po_norm_number"),
            models.Index(fields=["vendor"], name="idx_po_vendor"),
            models.Index(fields=["status"], name="idx_po_status"),
        ]

    def __str__(self) -> str:
        return f"PO {self.po_number}"


class PurchaseOrderLineItem(TimestampMixin):
    """PO line item."""

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="line_items")
    line_number = models.PositiveIntegerField(default=1)
    item_code = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField(blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=18, decimal_places=2)
    unit_of_measure = models.CharField(max_length=30, blank=True, default="EA")

    # Item classification (for reconciliation mode resolution)
    item_category = models.CharField(max_length=100, blank=True, default="")
    is_service_item = models.BooleanField(null=True, blank=True)
    is_stock_item = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "documents_po_line"
        ordering = ["purchase_order", "line_number"]
        verbose_name = "PO Line Item"
        verbose_name_plural = "PO Line Items"
        indexes = [
            models.Index(fields=["purchase_order", "line_number"], name="idx_poline_num"),
            models.Index(fields=["item_code"], name="idx_poline_itemcode"),
        ]

    def __str__(self) -> str:
        return f"POLine #{self.line_number} – {self.description[:60]}"


# ---------------------------------------------------------------------------
# Goods Receipt Note
# ---------------------------------------------------------------------------
class GoodsReceiptNote(BaseModel, NotesMixin):
    """Goods Receipt Note header — multiple GRNs can exist per PO."""

    grn_number = models.CharField(max_length=100, unique=True, db_index=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="grns")
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True, related_name="grns")
    receipt_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, default="RECEIVED", db_index=True)
    warehouse = models.CharField(max_length=255, blank=True, default="")
    receiver_name = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "documents_grn"
        ordering = ["-receipt_date"]
        verbose_name = "Goods Receipt Note"
        verbose_name_plural = "Goods Receipt Notes"
        indexes = [
            models.Index(fields=["grn_number"], name="idx_grn_number"),
            models.Index(fields=["purchase_order"], name="idx_grn_po"),
            models.Index(fields=["status"], name="idx_grn_status"),
        ]

    def __str__(self) -> str:
        return f"GRN {self.grn_number} (PO {self.purchase_order.po_number})"


class GRNLineItem(TimestampMixin):
    """GRN line item."""

    grn = models.ForeignKey(GoodsReceiptNote, on_delete=models.CASCADE, related_name="line_items")
    line_number = models.PositiveIntegerField(default=1)
    po_line = models.ForeignKey(
        PurchaseOrderLineItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="grn_lines"
    )
    item_code = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField(blank=True, default="")
    quantity_received = models.DecimalField(max_digits=18, decimal_places=4)
    quantity_accepted = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    quantity_rejected = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_of_measure = models.CharField(max_length=30, blank=True, default="EA")

    class Meta:
        db_table = "documents_grn_line"
        ordering = ["grn", "line_number"]
        verbose_name = "GRN Line Item"
        verbose_name_plural = "GRN Line Items"
        indexes = [
            models.Index(fields=["grn", "line_number"], name="idx_grnline_num"),
            models.Index(fields=["po_line"], name="idx_grnline_poline"),
        ]

    def __str__(self) -> str:
        return f"GRNLine #{self.line_number} – {self.description[:60]}"
