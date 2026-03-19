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
    ValidationResult,
    ValidationRuleSet,
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
    ValidationResultSerializer,
    ValidationRuleSetListSerializer,
    ValidationRuleSetSerializer,
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
        if self.action in ("recommendation", "benchmark", "validation"):
            return [HasPermissionCode("procurement.view_results")]
        if self.action == "validate":
            return [HasPermissionCode("procurement.validate")]
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
        if run_type not in ("RECOMMENDATION", "BENCHMARK", "VALIDATION"):
            return Response(
                {"error": "run_type must be RECOMMENDATION, BENCHMARK, or VALIDATION"},
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

    # ---------- trigger validation ----------
    @action(detail=True, methods=["post"], url_path="validate")
    def validate(self, request, pk=None):
        """Trigger a validation run for this procurement request."""
        from apps.core.enums import AnalysisRunType
        from apps.procurement.services.analysis_run_service import AnalysisRunService
        from apps.procurement.tasks import run_validation_task

        proc_request = self.get_object()
        agent_enabled = request.data.get("agent_enabled", False)

        run = AnalysisRunService.create_run(
            request=proc_request,
            run_type=AnalysisRunType.VALIDATION,
            triggered_by=request.user,
        )
        run_validation_task.delay(run.pk, agent_enabled=agent_enabled)

        return Response(
            {"run_id": str(run.run_id), "status": "queued", "message": "Validation run queued."},
            status=status.HTTP_201_CREATED,
        )

    # ---------- fetch latest validation result ----------
    @action(detail=True, methods=["get"], url_path="validation")
    def validation(self, request, pk=None):
        """Fetch the latest validation result for this request."""
        proc_request = self.get_object()
        result = (
            ValidationResult.objects
            .filter(run__request=proc_request)
            .select_related("run")
            .prefetch_related("items")
            .order_by("-created_at")
            .first()
        )
        if not result:
            return Response(
                {"detail": "No validation results yet."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ValidationResultSerializer(result).data)


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


class ValidationRuleSetViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only access to validation rule sets — admin/internal use."""

    queryset = ValidationRuleSet.objects.prefetch_related("rules")
    permission_classes = [permissions.IsAuthenticated, HasPermissionCode]
    required_permission = "procurement.view"
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["domain_code", "schema_code", "validation_type", "is_active"]
    search_fields = ["rule_set_code", "rule_set_name"]
    ordering = ["priority", "rule_set_code"]

    def get_serializer_class(self):
        if self.action == "list":
            return ValidationRuleSetListSerializer
        return ValidationRuleSetSerializer


class AnalysisRunValidationView(viewsets.ViewSet):
    """Fetch validation result for a specific analysis run."""

    permission_classes = [permissions.IsAuthenticated, HasPermissionCode]
    required_permission = "procurement.view_results"

    def retrieve(self, request, pk=None):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun.objects.select_related("request"), pk=pk)
        result = (
            ValidationResult.objects
            .filter(run=run)
            .prefetch_related("items")
            .first()
        )
        if not result:
            return Response(
                {"detail": "No validation result for this run."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ValidationResultSerializer(result).data)
