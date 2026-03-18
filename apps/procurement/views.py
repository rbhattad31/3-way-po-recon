"""DRF API views for the Procurement Intelligence platform."""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.permissions import HasPermissionCode
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    ComplianceResult,
    ProcurementRequest,
    ProcurementRequestAttribute,
    RecommendationResult,
    SupplierQuotation,
)
from apps.procurement.serializers import (
    AnalysisRunSerializer,
    BenchmarkResultSerializer,
    ComplianceResultSerializer,
    ProcurementRequestAttributeSerializer,
    ProcurementRequestDetailSerializer,
    ProcurementRequestListSerializer,
    ProcurementRequestWriteSerializer,
    RecommendationResultSerializer,
    SupplierQuotationDetailSerializer,
    SupplierQuotationListSerializer,
    SupplierQuotationWriteSerializer,
)


class ProcurementRequestViewSet(viewsets.ModelViewSet):
    """CRUD + actions for ProcurementRequest."""

    queryset = ProcurementRequest.objects.select_related("created_by", "assigned_to")
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "request_type", "domain_code", "priority"]
    search_fields = ["title", "description", "domain_code"]
    ordering_fields = ["created_at", "updated_at", "priority", "status"]
    ordering = ["-created_at"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [HasPermissionCode("procurement.view")]
        if self.action in ("create",):
            return [HasPermissionCode("procurement.create")]
        if self.action in ("update", "partial_update"):
            return [HasPermissionCode("procurement.edit")]
        if self.action in ("destroy",):
            return [HasPermissionCode("procurement.delete")]
        if self.action == "attributes":
            if self.request and self.request.method == "POST":
                return [HasPermissionCode("procurement.edit")]
            return [HasPermissionCode("procurement.view")]
        if self.action == "runs":
            if self.request and self.request.method == "POST":
                return [HasPermissionCode("procurement.run_analysis")]
            return [HasPermissionCode("procurement.view")]
        if self.action in ("recommendation", "benchmark"):
            return [HasPermissionCode("procurement.view_results")]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "list":
            return ProcurementRequestListSerializer
        if self.action in ("create", "update", "partial_update"):
            return ProcurementRequestWriteSerializer
        return ProcurementRequestDetailSerializer

    # ---------- nested attributes ----------
    @action(detail=True, methods=["get", "post"], url_path="attributes")
    def attributes(self, request, pk=None):
        proc_request = self.get_object()
        if request.method == "GET":
            attrs = proc_request.attributes.all()
            return Response(ProcurementRequestAttributeSerializer(attrs, many=True).data)

        # POST — bulk set attributes
        from apps.procurement.services.request_service import AttributeService
        serializer = ProcurementRequestAttributeSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        AttributeService.bulk_set_attributes(proc_request, request.data)
        return Response(
            ProcurementRequestAttributeSerializer(proc_request.attributes.all(), many=True).data,
            status=status.HTTP_200_OK,
        )

    # ---------- nested runs ----------
    @action(detail=True, methods=["get", "post"], url_path="runs")
    def runs(self, request, pk=None):
        proc_request = self.get_object()
        if request.method == "GET":
            runs = proc_request.analysis_runs.all()
            return Response(AnalysisRunSerializer(runs, many=True).data)

        # POST — trigger a new analysis run
        run_type = request.data.get("run_type")
        if run_type not in ("RECOMMENDATION", "BENCHMARK"):
            return Response(
                {"error": "run_type must be RECOMMENDATION or BENCHMARK"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.procurement.tasks import run_analysis_task
        from apps.procurement.services.analysis_run_service import AnalysisRunService

        run = AnalysisRunService.create_run(
            request=proc_request,
            run_type=run_type,
            triggered_by=request.user,
        )
        # Fire Celery task
        run_analysis_task.delay(run.pk)

        return Response(AnalysisRunSerializer(run).data, status=status.HTTP_201_CREATED)

    # ---------- recommendation result ----------
    @action(detail=True, methods=["get"], url_path="recommendation")
    def recommendation(self, request, pk=None):
        proc_request = self.get_object()
        results = RecommendationResult.objects.filter(
            run__request=proc_request,
        ).select_related("run").order_by("-created_at")
        if not results.exists():
            return Response({"detail": "No recommendation results yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RecommendationResultSerializer(results.first()).data)

    # ---------- benchmark result ----------
    @action(detail=True, methods=["get"], url_path="benchmark")
    def benchmark(self, request, pk=None):
        proc_request = self.get_object()
        results = BenchmarkResult.objects.filter(
            run__request=proc_request,
        ).select_related("run", "quotation").prefetch_related("lines").order_by("-created_at")
        if not results.exists():
            return Response({"detail": "No benchmark results yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BenchmarkResultSerializer(results, many=True).data)


class SupplierQuotationViewSet(viewsets.ModelViewSet):
    """CRUD for SupplierQuotation."""

    queryset = SupplierQuotation.objects.select_related("request", "created_by")
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["extraction_status", "currency"]
    search_fields = ["vendor_name", "quotation_number"]
    ordering = ["-created_at"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [HasPermissionCode("procurement.view")]
        return [HasPermissionCode("procurement.manage_quotations")]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return SupplierQuotationWriteSerializer
        if self.action == "list":
            return SupplierQuotationListSerializer
        return SupplierQuotationDetailSerializer
