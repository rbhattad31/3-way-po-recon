"""DRF API views for the Procurement Intelligence platform."""
import hashlib
import os

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from apps.core.constants import ALLOWED_UPLOAD_EXTENSIONS, MAX_UPLOAD_SIZE_MB
from apps.core.enums import DocumentType, FileProcessingState, PrefillStatus, SourceDocumentType
from apps.core.permissions import HasPermissionCode, _has_permission_code
from apps.core.tenant_utils import TenantQuerysetMixin, require_tenant


def _perm(code):
    """Build an ad-hoc DRF permission instance that checks *code*."""
    from rest_framework.permissions import BasePermission

    class _Check(BasePermission):
        def has_permission(self, request, view):
            return _has_permission_code(request.user, code)

    return _Check()


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
    PrefillStatusSerializer,
    ProcurementRequestAttributeSerializer,
    ProcurementRequestDetailSerializer,
    ProcurementRequestListSerializer,
    ProcurementRequestWriteSerializer,
    QuotationPrefillConfirmSerializer,
    QuotationPrefillUploadSerializer,
    RecommendationResultSerializer,
    RequestPrefillConfirmSerializer,
    RequestPrefillUploadSerializer,
    SupplierQuotationDetailSerializer,
    SupplierQuotationListSerializer,
    SupplierQuotationWriteSerializer,
    ValidationResultSerializer,
    ValidationRuleSetListSerializer,
    ValidationRuleSetSerializer,
)


class ProcurementRequestViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
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
            return [_perm("procurement.view")]
        if self.action in ("create",):
            return [_perm("procurement.create")]
        if self.action in ("update", "partial_update"):
            return [_perm("procurement.edit")]
        if self.action in ("destroy",):
            return [_perm("procurement.delete")]
        if self.action == "attributes":
            if self.request and self.request.method == "POST":
                return [_perm("procurement.edit")]
            return [_perm("procurement.view")]
        if self.action == "runs":
            if self.request and self.request.method == "POST":
                return [_perm("procurement.run_analysis")]
            return [_perm("procurement.view")]
        if self.action in ("recommendation", "benchmark", "validation"):
            return [_perm("procurement.view_results")]
        if self.action == "validate":
            return [_perm("procurement.validate")]
        if self.action in ("prefill", "prefill_status", "prefill_confirm"):
            if self.request and self.request.method == "GET":
                return [_perm("procurement.view")]
            return [_perm("procurement.create")]
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
        run_analysis_task.delay(request.tenant.pk if request.tenant else None, run.pk)

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
        run_validation_task.delay(request.tenant.pk if request.tenant else None, run.pk, agent_enabled=agent_enabled)

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

    # ---------- request prefill: upload ----------
    @action(detail=False, methods=["post"], url_path="prefill")
    def prefill(self, request):
        """Upload an RFQ / requirement PDF, create a draft request, and trigger prefill extraction."""
        from apps.documents.models import DocumentUpload
        from apps.procurement.tasks import run_request_prefill_task

        serializer = RequestPrefillUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        source_doc_type = serializer.validated_data.get("source_document_type", "RFQ")
        domain_code = serializer.validated_data.get("domain_code", "")
        title = serializer.validated_data.get("title", "") or uploaded_file.name

        # Validate file
        ext = os.path.splitext(uploaded_file.name)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return Response(
                {"error": f"Unsupported file type '{ext}'. Allowed: {ALLOWED_UPLOAD_EXTENSIONS}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if uploaded_file.size > max_bytes:
            return Response(
                {"error": f"File exceeds maximum size of {MAX_UPLOAD_SIZE_MB} MB"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Compute hash
        sha = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            sha.update(chunk)
        uploaded_file.seek(0)
        file_hash = sha.hexdigest()

        # Create DocumentUpload
        doc = DocumentUpload.objects.create(
            file=uploaded_file,
            original_filename=uploaded_file.name,
            file_size=uploaded_file.size,
            file_hash=file_hash,
            content_type=getattr(uploaded_file, "content_type", ""),
            document_type=DocumentType.PROCUREMENT_RFQ,
            processing_state=FileProcessingState.QUEUED,
            uploaded_by=request.user,
        )

        # Create draft ProcurementRequest
        proc_request = ProcurementRequest.objects.create(
            title=title,
            description="",
            domain_code=domain_code,
            request_type="RECOMMENDATION",
            status="DRAFT",
            priority="MEDIUM",
            uploaded_document=doc,
            source_document_type=source_doc_type,
            prefill_status=PrefillStatus.NOT_STARTED,
            created_by=request.user,
        )

        # Trigger async prefill
        run_request_prefill_task.delay(request.tenant.pk if request.tenant else None, proc_request.pk)

        return Response(
            {
                "id": proc_request.pk,
                "request_id": str(proc_request.request_id),
                "prefill_status": proc_request.prefill_status,
                "message": "Draft request created. Prefill extraction started.",
            },
            status=status.HTTP_201_CREATED,
        )

    # ---------- request prefill: status ----------
    @action(detail=True, methods=["get"], url_path="prefill")
    def prefill_status(self, request, pk=None):
        """Get the latest prefill payload and status for a request."""
        proc_request = self.get_object()
        return Response({
            "prefill_status": proc_request.prefill_status,
            "prefill_confidence": proc_request.prefill_confidence,
            "prefill_payload": proc_request.prefill_payload_json,
            "source_document_type": proc_request.source_document_type,
            "uploaded_document_id": proc_request.uploaded_document_id,
        })

    # ---------- request prefill: confirm ----------
    @action(detail=True, methods=["post"], url_path="prefill/confirm")
    def prefill_confirm(self, request, pk=None):
        """Submit reviewed/edited prefill data and save final request + attributes."""
        from apps.procurement.services.prefill.prefill_review_service import PrefillReviewService

        proc_request = self.get_object()
        serializer = RequestPrefillConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        proc_request._confirmed_by_email = getattr(request.user, "email", "")
        proc_request = PrefillReviewService.confirm_request_prefill(
            proc_request, serializer.validated_data,
        )

        return Response(
            ProcurementRequestDetailSerializer(proc_request).data,
            status=status.HTTP_200_OK,
        )


class SupplierQuotationViewSet(TenantQuerysetMixin, viewsets.ModelViewSet):
    """CRUD for SupplierQuotation."""

    queryset = SupplierQuotation.objects.select_related("request", "created_by")
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["extraction_status", "currency"]
    search_fields = ["vendor_name", "quotation_number"]
    ordering = ["-created_at"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [_perm("procurement.view")]
        if self.action in ("quotation_prefill_status",):
            return [_perm("procurement.view")]
        if self.action in ("quotation_prefill", "quotation_prefill_confirm"):
            return [_perm("procurement.manage_quotations")]
        return [_perm("procurement.manage_quotations")]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return SupplierQuotationWriteSerializer
        if self.action == "list":
            return SupplierQuotationListSerializer
        return SupplierQuotationDetailSerializer

    # ---------- quotation prefill: upload ----------
    @action(detail=False, methods=["post"], url_path="prefill")
    def quotation_prefill(self, request):
        """Upload a proposal / quotation PDF, create a draft quotation, and trigger prefill."""
        from apps.documents.models import DocumentUpload
        from apps.procurement.tasks import run_quotation_prefill_task

        serializer = QuotationPrefillUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data["file"]
        vendor_name = serializer.validated_data.get("vendor_name", "") or "TBD"

        # The quotation must belong to a request — check for request_id in query params
        request_id = request.query_params.get("request_id") or request.data.get("request_id")
        if not request_id:
            return Response(
                {"error": "request_id is required (query param or body field)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            proc_request = ProcurementRequest.objects.get(pk=request_id)
        except ProcurementRequest.DoesNotExist:
            return Response(
                {"error": f"ProcurementRequest {request_id} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Validate file
        ext = os.path.splitext(uploaded_file.name)[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return Response(
                {"error": f"Unsupported file type '{ext}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Compute hash
        sha = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            sha.update(chunk)
        uploaded_file.seek(0)
        file_hash = sha.hexdigest()

        # Create DocumentUpload
        doc = DocumentUpload.objects.create(
            file=uploaded_file,
            original_filename=uploaded_file.name,
            file_size=uploaded_file.size,
            file_hash=file_hash,
            content_type=getattr(uploaded_file, "content_type", ""),
            document_type=DocumentType.PROCUREMENT_QUOTATION,
            processing_state=FileProcessingState.QUEUED,
            uploaded_by=request.user,
        )

        # Create draft SupplierQuotation
        quotation = SupplierQuotation.objects.create(
            request=proc_request,
            vendor_name=vendor_name,
            uploaded_document=doc,
            prefill_status=PrefillStatus.NOT_STARTED,
            created_by=request.user,
        )

        # Trigger async prefill
        run_quotation_prefill_task.delay(request.tenant.pk if request.tenant else None, quotation.pk)

        return Response(
            {
                "id": quotation.pk,
                "request_id": proc_request.pk,
                "prefill_status": quotation.prefill_status,
                "message": "Draft quotation created. Prefill extraction started.",
            },
            status=status.HTTP_201_CREATED,
        )

    # ---------- quotation prefill: status ----------
    @action(detail=True, methods=["get"], url_path="prefill")
    def quotation_prefill_status(self, request, pk=None):
        """Get quotation prefill payload and status."""
        quotation = self.get_object()
        return Response({
            "prefill_status": quotation.prefill_status,
            "prefill_confidence": quotation.extraction_confidence,
            "prefill_payload": quotation.prefill_payload_json,
            "uploaded_document_id": quotation.uploaded_document_id,
        })

    # ---------- quotation prefill: confirm ----------
    @action(detail=True, methods=["post"], url_path="prefill/confirm")
    def quotation_prefill_confirm(self, request, pk=None):
        """Submit reviewed/edited quotation prefill data and save final quotation + line items."""
        from apps.procurement.services.prefill.prefill_review_service import PrefillReviewService

        quotation = self.get_object()
        serializer = QuotationPrefillConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        quotation.request._confirmed_by_email = getattr(request.user, "email", "")
        quotation = PrefillReviewService.confirm_quotation_prefill(
            quotation, serializer.validated_data,
        )

        return Response(
            SupplierQuotationDetailSerializer(quotation).data,
            status=status.HTTP_200_OK,
        )


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


# =============================================================================
# RoomWise Pre-Procurement Recommender ViewSets
# =============================================================================


class RoomViewSet(viewsets.ModelViewSet):
    """Manage rooms/facilities for HVAC recommendations."""

    permission_classes = [permissions.IsAuthenticated, HasPermissionCode]
    required_permission = "roomwise.manage_rooms"
    queryset = None
    serializer_class = None
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["building_name", "floor_number", "usage_type", "is_active"]
    search_fields = ["room_code", "building_name", "location_description"]
    ordering_fields = ["building_name", "room_code", "created_at"]
    ordering = ["building_name", "room_code"]
    pagination_class = None

    def get_queryset(self):
        from apps.procurement.models import Room

        return Room.objects.filter(is_active=True)

    def get_serializer_class(self):
        from apps.procurement.serializers import RoomSerializer

        return RoomSerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse HVAC products catalog."""

    permission_classes = [permissions.IsAuthenticated]
    queryset = None
    serializer_class = None
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["system_type", "manufacturer", "is_active"]
    search_fields = ["sku", "product_name", "manufacturer"]
    ordering_fields = ["capacity_kw", "unit_price", "created_at"]
    ordering = ["manufacturer", "capacity_kw"]
    pagination_class = None

    def get_queryset(self):
        from apps.procurement.models import Product

        return Product.objects.filter(is_active=True)

    def get_serializer_class(self):
        from apps.procurement.serializers import ProductSerializer

        return ProductSerializer


class VendorViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse approved HVAC vendors."""

    permission_classes = [permissions.IsAuthenticated]
    queryset = None
    serializer_class = None
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["country", "city", "preferred_vendor", "is_active"]
    search_fields = ["vendor_name", "contact_email"]
    ordering_fields = ["reliability_score", "on_time_delivery_pct", "created_at"]
    ordering = ["-reliability_score", "vendor_name"]
    pagination_class = None

    def get_queryset(self):
        from apps.procurement.models import Vendor

        return Vendor.objects.filter(is_active=True)

    def get_serializer_class(self):
        from apps.procurement.serializers import VendorSerializer

        return VendorSerializer


class VendorProductViewSet(viewsets.ReadOnlyModelViewSet):
    """Browse vendor product offerings with pricing."""

    permission_classes = [permissions.IsAuthenticated]
    queryset = None
    serializer_class = None
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["product_id", "vendor_id", "is_preferred"]
    ordering_fields = ["unit_price", "lead_time_days", "created_at"]
    ordering = ["unit_price"]
    pagination_class = None

    def get_queryset(self):
        from apps.procurement.models import VendorProduct

        return VendorProduct.objects.filter(is_active=True).select_related("vendor", "product")

    def get_serializer_class(self):
        from apps.procurement.serializers import VendorProductDetailSerializer

        return VendorProductDetailSerializer


class PurchaseHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """View historical purchase orders and outcomes."""

    permission_classes = [permissions.IsAuthenticated]
    queryset = None
    serializer_class = None
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["room_id", "vendor_id", "po_status"]
    search_fields = ["po_number", "room__room_code"]
    ordering_fields = ["po_date", "promised_delivery_date", "created_at"]
    ordering = ["-po_date"]
    pagination_class = None

    def get_queryset(self):
        from apps.procurement.models import PurchaseHistory

        return (
            PurchaseHistory.objects
            .select_related("room", "product", "vendor")
            .all()
        )

    def get_serializer_class(self):
        from apps.procurement.serializers import PurchaseHistorySerializer

        return PurchaseHistorySerializer


class RecommendationViewSet(viewsets.ViewSet):
    """Core recommendation engine endpoint."""

    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """List recent recommendations."""
        from apps.procurement.models import RecommendationLog
        from apps.procurement.serializers import RecommendationLogSerializer

        limit = request.query_params.get("limit", 20)
        logs = RecommendationLog.objects.all().order_by("-created_at")[:int(limit)]
        serializer = RecommendationLogSerializer(logs, many=True)
        return Response(serializer.data)

    def create(self, request):
        """Generate a new recommendation."""
        from apps.procurement.services.roomwise_recommender import RoomWiseRecommenderService
        from apps.procurement.serializers import RunRecommendationSerializer

        serializer = RunRecommendationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = RoomWiseRecommenderService()
        result = service.run_recommendation(
            room_id=serializer.validated_data.get("room_id") or None,
            requirement_text=serializer.validated_data.get("requirement_text", ""),
            user_id=str(request.user.id),
            budget_max=serializer.validated_data.get("budget_max"),
            preferred_lead_time_days=serializer.validated_data.get("preferred_lead_time_days"),
            exclude_vendors=serializer.validated_data.get("exclude_vendors"),
            preferred_system_types=serializer.validated_data.get("preferred_system_types"),
        )

        return Response(result, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        """Get a specific recommendation by ID."""
        from apps.procurement.models import RecommendationLog
        from apps.procurement.serializers import RecommendationLogSerializer
        from django.shortcuts import get_object_or_404

        rec_log = get_object_or_404(RecommendationLog, recommendation_id=pk)
        serializer = RecommendationLogSerializer(rec_log)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Mark a recommendation as accepted."""
        from apps.procurement.models import RecommendationLog
        from django.shortcuts import get_object_or_404

        rec_log = get_object_or_404(RecommendationLog, recommendation_id=pk)
        rec_log.is_accepted = True
        rec_log.user_feedback = request.data.get("feedback", "")
        rec_log.save()

        from apps.procurement.serializers import RecommendationLogSerializer

        return Response(
            RecommendationLogSerializer(rec_log).data,
            status=status.HTTP_200_OK,
        )
