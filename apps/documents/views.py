"""Document API viewsets — Invoices, POs, GRNs, Uploads."""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import IsAdminOrReadOnly
from apps.core.tenant_utils import TenantQuerysetMixin
from apps.documents.models import (
    DocumentUpload,
    GoodsReceiptNote,
    Invoice,
    PurchaseOrder,
)
from apps.documents.serializers import (
    DocumentUploadSerializer,
    GRNDetailSerializer,
    GRNListSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    PurchaseOrderDetailSerializer,
    PurchaseOrderListSerializer,
)


class DocumentUploadViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = DocumentUpload.objects.select_related("uploaded_by").order_by("-created_at")
    serializer_class = DocumentUploadSerializer
    permission_classes = [IsAdminOrReadOnly]
    parser_classes = [MultiPartParser]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["document_type", "processing_state"]
    ordering_fields = ["created_at", "file_size"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        uploaded_file = self.request.FILES.get("file")
        serializer.save(
            uploaded_by=self.request.user,
            original_filename=uploaded_file.name if uploaded_file else "",
            file_size=uploaded_file.size if uploaded_file else 0,
            content_type=uploaded_file.content_type if uploaded_file else "",
        )


class InvoiceViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Invoice.objects.select_related("vendor", "document_upload").order_by("-created_at")
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "is_duplicate", "currency"]
    search_fields = ["invoice_number", "po_number", "raw_vendor_name"]
    ordering_fields = ["invoice_date", "total_amount", "created_at", "status"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return InvoiceListSerializer
        return InvoiceDetailSerializer


class PurchaseOrderViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = PurchaseOrder.objects.select_related("vendor").order_by("-po_date")
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "currency"]
    search_fields = ["po_number", "buyer_name", "department"]
    ordering_fields = ["po_date", "total_amount", "created_at"]
    ordering = ["-po_date"]

    def get_serializer_class(self):
        if self.action == "list":
            return PurchaseOrderListSerializer
        return PurchaseOrderDetailSerializer


class GRNViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = GoodsReceiptNote.objects.select_related(
        "purchase_order", "vendor"
    ).order_by("-receipt_date")
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["grn_number", "purchase_order__po_number"]
    ordering_fields = ["receipt_date", "created_at"]
    ordering = ["-receipt_date"]

    def get_serializer_class(self):
        if self.action == "list":
            return GRNListSerializer
        return GRNDetailSerializer
