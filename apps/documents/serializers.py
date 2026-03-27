"""Document API serializers — Invoices, POs, GRNs."""
from rest_framework import serializers

from apps.core.utils import normalize_category, resolve_line_tax_percentage, resolve_tax_percentage
from apps.documents.models import (
    DocumentUpload,
    GoodsReceiptNote,
    GRNLineItem,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)


# ---------------------------------------------------------------------------
# Document Upload
# ---------------------------------------------------------------------------
class DocumentUploadSerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.EmailField(source="uploaded_by.email", read_only=True, default=None)

    class Meta:
        model = DocumentUpload
        fields = [
            "id", "file", "original_filename", "file_size", "content_type",
            "document_type", "processing_state", "processing_message",
            "uploaded_by", "uploaded_by_email", "created_at",
        ]
        read_only_fields = [
            "id", "original_filename", "file_size", "content_type",
            "processing_state", "processing_message", "uploaded_by",
            "uploaded_by_email", "created_at",
        ]


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
class InvoiceLineItemSerializer(serializers.ModelSerializer):
    item_category = serializers.SerializerMethodField()
    tax_percentage = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLineItem
        fields = [
            "id", "line_number", "description", "quantity",
            "item_category", "unit_price", "tax_percentage", "tax_amount", "line_amount", "extraction_confidence",
        ]

    def get_item_category(self, obj):
        raw_line_items = ((obj.invoice.extraction_raw_json or {}).get("line_items") or [])
        raw_line = raw_line_items[obj.line_number - 1] if obj.line_number - 1 < len(raw_line_items) and isinstance(raw_line_items[obj.line_number - 1], dict) else {}
        return (
            normalize_category(obj.item_category)
            or normalize_category(raw_line.get("item_category") or raw_line.get("category"))
            or ("Service" if obj.is_service_item else "")
            or ("Stock" if obj.is_stock_item else "")
            or "Other"
        )

    def get_tax_percentage(self, obj):
        raw_line_items = ((obj.invoice.extraction_raw_json or {}).get("line_items") or [])
        raw_line = raw_line_items[obj.line_number - 1] if obj.line_number - 1 < len(raw_line_items) and isinstance(raw_line_items[obj.line_number - 1], dict) else {}
        value = resolve_line_tax_percentage(
            raw_percentage=raw_line.get("tax_percentage"),
            tax_amount=obj.tax_amount,
            quantity=obj.quantity,
            unit_price=obj.unit_price,
            line_amount=obj.line_amount,
        )
        return str(value) if value is not None else None


class InvoiceListSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")

    class Meta:
        model = Invoice
        fields = [
            "id", "invoice_number", "invoice_date", "po_number",
            "vendor_name", "total_amount", "currency", "status",
            "extraction_confidence", "is_duplicate", "created_at",
        ]


class InvoiceDetailSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    vendor_code = serializers.CharField(source="vendor.code", read_only=True, default="")
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)
    tax_percentage = serializers.SerializerMethodField()
    upload_filename = serializers.CharField(
        source="document_upload.original_filename", read_only=True, default=""
    )

    class Meta:
        model = Invoice
        fields = [
            "id", "document_upload", "upload_filename",
            "vendor", "vendor_name", "vendor_code",
            "raw_vendor_name", "raw_invoice_number", "raw_invoice_date",
            "raw_po_number", "raw_currency", "raw_total_amount",
            "invoice_number", "invoice_date", "po_number", "currency",
            "subtotal", "tax_percentage", "tax_amount", "total_amount",
            "status", "is_duplicate", "extraction_confidence",
            "extraction_remarks", "line_items",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "vendor_name", "vendor_code", "upload_filename",
            "created_at", "updated_at",
        ]

    def get_tax_percentage(self, obj):
        value = resolve_tax_percentage(
            raw_percentage=(obj.extraction_raw_json or {}).get("tax_percentage"),
            tax_amount=obj.tax_amount,
            base_amount=obj.subtotal,
        )
        return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------
class POLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrderLineItem
        fields = [
            "id", "line_number", "item_code", "description",
            "quantity", "unit_price", "tax_amount", "line_amount",
            "unit_of_measure",
        ]


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "po_number", "vendor_name", "po_date",
            "currency", "total_amount", "status", "created_at",
        ]


class PurchaseOrderDetailSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    line_items = POLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "po_number", "normalized_po_number",
            "vendor", "vendor_name", "po_date", "currency",
            "total_amount", "tax_amount", "status",
            "buyer_name", "department", "notes",
            "line_items", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "normalized_po_number", "vendor_name", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Goods Receipt Note
# ---------------------------------------------------------------------------
class GRNLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GRNLineItem
        fields = [
            "id", "line_number", "item_code", "description",
            "quantity_received", "quantity_accepted", "quantity_rejected",
            "unit_of_measure",
        ]


class GRNListSerializer(serializers.ModelSerializer):
    po_number = serializers.CharField(source="purchase_order.po_number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")

    class Meta:
        model = GoodsReceiptNote
        fields = [
            "id", "grn_number", "po_number", "vendor_name",
            "receipt_date", "status", "created_at",
        ]


class GRNDetailSerializer(serializers.ModelSerializer):
    po_number = serializers.CharField(source="purchase_order.po_number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default="")
    line_items = GRNLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = GoodsReceiptNote
        fields = [
            "id", "grn_number", "purchase_order", "po_number",
            "vendor", "vendor_name", "receipt_date", "status",
            "warehouse", "receiver_name", "notes",
            "line_items", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "po_number", "vendor_name", "created_at", "updated_at"]
