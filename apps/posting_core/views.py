"""DRF views for posting_core."""
import os
import tempfile
import logging

from rest_framework import status, viewsets
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.tenant_utils import TenantQuerysetMixin

from apps.posting_core.models import (
    ERPCostCenterReference,
    ERPItemReference,
    ERPPOReference,
    ERPReferenceImportBatch,
    ERPTaxCodeReference,
    ERPVendorReference,
    ItemAliasMapping,
    PostingRule,
    PostingRun,
    VendorAliasMapping,
)
from apps.posting_core.serializers import (
    ERPCostCenterReferenceSerializer,
    ERPItemReferenceSerializer,
    ERPPOReferenceSerializer,
    ERPReferenceImportBatchSerializer,
    ERPReferenceUploadSerializer,
    ERPTaxCodeReferenceSerializer,
    ERPVendorReferenceSerializer,
    ItemAliasMappingSerializer,
    PostingRuleSerializer,
    PostingRunDetailSerializer,
    PostingRunListSerializer,
    VendorAliasMappingSerializer,
)
from apps.posting.tasks import import_reference_excel_task

logger = logging.getLogger(__name__)


# ── PostingRun ──────────────────────────────────────────────────────
class PostingRunViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only access to posting execution runs."""

    queryset = PostingRun.objects.select_related("invoice").order_by("-created_at")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return PostingRunListSerializer
        return PostingRunDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        invoice_id = self.request.query_params.get("invoice")
        if invoice_id:
            qs = qs.filter(invoice_id=invoice_id)
        s = self.request.query_params.get("status")
        if s:
            qs = qs.filter(status=s)
        return qs


# ── Import Batches ──────────────────────────────────────────────────
class ERPReferenceImportBatchViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    """List / retrieve ERP reference import batch records."""

    queryset = ERPReferenceImportBatch.objects.select_related(
        "imported_by",
    ).order_by("-created_at")
    serializer_class = ERPReferenceImportBatchSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        batch_type = self.request.query_params.get("batch_type")
        if batch_type:
            qs = qs.filter(batch_type=batch_type.upper())
        s = self.request.query_params.get("status")
        if s:
            qs = qs.filter(status=s.upper())
        return qs


# ── Reference data (read-only) ──────────────────────────────────────
class ERPVendorReferenceViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ERPVendorReference.objects.order_by("vendor_code")
    serializer_class = ERPVendorReferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(vendor_name__icontains=q)
        return qs


class ERPItemReferenceViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ERPItemReference.objects.order_by("item_code")
    serializer_class = ERPItemReferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(item_name__icontains=q)
        return qs


class ERPTaxCodeReferenceViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ERPTaxCodeReference.objects.order_by("tax_code")
    serializer_class = ERPTaxCodeReferenceSerializer
    permission_classes = [IsAuthenticated]


class ERPCostCenterReferenceViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ERPCostCenterReference.objects.order_by("cost_center_code")
    serializer_class = ERPCostCenterReferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(cost_center_name__icontains=q)
        return qs


class ERPPOReferenceViewSet(TenantQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ERPPOReference.objects.order_by("po_number", "po_line_number")
    serializer_class = ERPPOReferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        po = self.request.query_params.get("po_number")
        if po:
            qs = qs.filter(po_number__iexact=po)
        vendor = self.request.query_params.get("vendor_code")
        if vendor:
            qs = qs.filter(vendor_code__iexact=vendor)
        return qs


# ── Alias Mappings ──────────────────────────────────────────────────
class VendorAliasMappingViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = VendorAliasMapping.objects.select_related(
        "vendor_reference",
    ).order_by("-created_at")
    serializer_class = VendorAliasMappingSerializer
    permission_classes = [IsAuthenticated]


class ItemAliasMappingViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = ItemAliasMapping.objects.select_related(
        "item_reference",
    ).order_by("-created_at")
    serializer_class = ItemAliasMappingSerializer
    permission_classes = [IsAuthenticated]


# ── Posting Rules ───────────────────────────────────────────────────
class PostingRuleViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    queryset = PostingRule.objects.order_by("priority")
    serializer_class = PostingRuleSerializer
    permission_classes = [IsAuthenticated]


# ── Upload endpoint ─────────────────────────────────────────────────
class ERPReferenceUploadView(APIView):
    """Upload ERP reference Excel/CSV and trigger async import."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        ser = ERPReferenceUploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        uploaded = ser.validated_data["file"]
        batch_type = ser.validated_data["batch_type"]
        source_as_of = ser.validated_data.get("source_as_of")

        # Save uploaded file to temp location
        suffix = os.path.splitext(uploaded.name)[1] or ".xlsx"
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="erp_ref_",
        ) as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        import_reference_excel_task.delay(
            request.tenant.pk if request.tenant else None,
            file_path=tmp_path,
            batch_type=batch_type,
            user_id=request.user.pk,
            source_as_of=str(source_as_of) if source_as_of else None,
        )

        return Response(
            {
                "message": "Import enqueued",
                "batch_type": batch_type,
                "file_name": uploaded.name,
            },
            status=status.HTTP_202_ACCEPTED,
        )
