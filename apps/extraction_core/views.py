"""DRF views for extraction_core."""
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.extraction_core.models import (
    EntityExtractionProfile,
    ExtractionRuntimeSettings,
    ExtractionSchemaDefinition,
    TaxJurisdictionProfile,
)
from apps.extraction_core.serializers import (
    EntityExtractionProfileListSerializer,
    EntityExtractionProfileSerializer,
    ExtractionRuntimeSettingsSerializer,
    ExtractionSchemaDefinitionListSerializer,
    ExtractionSchemaDefinitionSerializer,
    JurisdictionResolutionRequestSerializer,
    JurisdictionResolveRequestSerializer,
    SchemaLookupRequestSerializer,
    TaxJurisdictionProfileListSerializer,
    TaxJurisdictionProfileSerializer,
)
from apps.extraction_core.services.jurisdiction_resolver import JurisdictionResolverService
from apps.extraction_core.services.resolution_service import JurisdictionResolutionService
from apps.extraction_core.services.schema_registry import SchemaRegistryService


class TaxJurisdictionProfileViewSet(viewsets.ModelViewSet):
    """CRUD for Tax Jurisdiction Profiles."""

    queryset = TaxJurisdictionProfile.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return TaxJurisdictionProfileListSerializer
        return TaxJurisdictionProfileSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1"))
        country_code = self.request.query_params.get("country_code")
        if country_code:
            qs = qs.filter(country_code__iexact=country_code)
        tax_regime = self.request.query_params.get("tax_regime")
        if tax_regime:
            qs = qs.filter(tax_regime__iexact=tax_regime)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class ExtractionSchemaDefinitionViewSet(viewsets.ModelViewSet):
    """CRUD for Extraction Schema Definitions."""

    queryset = ExtractionSchemaDefinition.objects.select_related("jurisdiction").all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return ExtractionSchemaDefinitionListSerializer
        return ExtractionSchemaDefinitionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        jurisdiction_id = self.request.query_params.get("jurisdiction")
        if jurisdiction_id:
            qs = qs.filter(jurisdiction_id=jurisdiction_id)
        document_type = self.request.query_params.get("document_type")
        if document_type:
            qs = qs.filter(document_type__iexact=document_type)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1"))
        return qs

    @action(detail=True, methods=["get"])
    def field_definitions(self, request, pk=None):
        """List field definitions linked to this schema (via FieldRegistryService)."""
        schema = self.get_object()
        from apps.extraction_configs.serializers import TaxFieldDefinitionListSerializer
        from apps.extraction_configs.services.field_registry import FieldRegistryService

        snapshot = FieldRegistryService.get_fields_for_schema(schema)
        serializer = TaxFieldDefinitionListSerializer(snapshot.all_fields, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        """List all available versions for this schema's jurisdiction + doc type."""
        schema = self.get_object()
        versions = SchemaRegistryService.list_versions(
            country_code=schema.jurisdiction.country_code,
            document_type=schema.document_type,
            tax_regime=schema.jurisdiction.tax_regime,
        )
        return Response({"versions": versions})

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        SchemaRegistryService.invalidate_cache(
            instance.jurisdiction.country_code, instance.document_type
        )

    def perform_update(self, serializer):
        instance = serializer.save(updated_by=self.request.user)
        SchemaRegistryService.invalidate_cache(
            instance.jurisdiction.country_code, instance.document_type
        )


class JurisdictionResolveView(APIView):
    """
    POST /api/v1/extraction-core/resolve-jurisdiction/

    Accepts OCR text and returns the resolved jurisdiction with
    confidence and evidence signals.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = JurisdictionResolveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        resolution = JurisdictionResolverService.resolve(
            ocr_text=serializer.validated_data["ocr_text"],
            hint_country_code=serializer.validated_data.get("hint_country_code") or None,
            hint_tax_regime=serializer.validated_data.get("hint_tax_regime") or None,
        )
        return Response(resolution.to_dict(), status=status.HTTP_200_OK)


class SchemaLookupView(APIView):
    """
    POST /api/v1/extraction-core/lookup-schema/

    Resolve the appropriate extraction schema for a given country code
    and document type, with optional version pinning.  Delegates to
    SchemaRegistryService (cached).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SchemaLookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = SchemaRegistryService.get_schema(
            country_code=serializer.validated_data["country_code"],
            document_type=serializer.validated_data["document_type"],
            version=serializer.validated_data.get("version") or None,
            tax_regime=serializer.validated_data.get("tax_regime") or None,
        )

        payload = result.to_dict()
        if result.resolved and result.schema:
            payload["schema_detail"] = ExtractionSchemaDefinitionSerializer(
                result.schema
            ).data

        return Response(payload, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Runtime Settings
# ---------------------------------------------------------------------------


class ExtractionRuntimeSettingsViewSet(viewsets.ModelViewSet):
    """CRUD for system-level extraction runtime settings."""

    queryset = ExtractionRuntimeSettings.objects.all()
    serializer_class = ExtractionRuntimeSettingsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1"))
        return qs

    @action(detail=False, methods=["get"])
    def active(self, request):
        """Return the currently active settings record."""
        settings = ExtractionRuntimeSettings.get_active()
        if not settings:
            return Response(
                {"detail": "No active runtime settings found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(settings)
        return Response(serializer.data)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


# ---------------------------------------------------------------------------
# Entity Extraction Profile
# ---------------------------------------------------------------------------


class EntityExtractionProfileViewSet(viewsets.ModelViewSet):
    """CRUD for per-entity (vendor) extraction profiles."""

    queryset = EntityExtractionProfile.objects.select_related("entity").all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return EntityExtractionProfileListSerializer
        return EntityExtractionProfileSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1"))
        country_code = self.request.query_params.get("country_code")
        if country_code:
            qs = qs.filter(default_country_code__iexact=country_code)
        mode = self.request.query_params.get("jurisdiction_mode")
        if mode:
            qs = qs.filter(jurisdiction_mode__iexact=mode)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


# ---------------------------------------------------------------------------
# Jurisdiction Resolution (full pipeline)
# ---------------------------------------------------------------------------


class JurisdictionResolutionView(APIView):
    """
    POST /api/v1/extraction-core/resolve-jurisdiction-full/

    Full jurisdiction resolution using the 4-tier precedence chain:
    document override → entity profile → system settings → auto-detection.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = JurisdictionResolutionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = JurisdictionResolutionService.resolve(
            ocr_text=serializer.validated_data["ocr_text"],
            declared_country_code=serializer.validated_data.get("declared_country_code") or "",
            declared_regime_code=serializer.validated_data.get("declared_regime_code") or "",
            vendor_id=serializer.validated_data.get("vendor_id"),
        )
        return Response(result.to_dict(), status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Extraction pipeline
# ---------------------------------------------------------------------------


class ExtractionView(APIView):
    """
    POST /api/v1/extraction-core/extract/

    Run the full schema-driven extraction pipeline:
    1. Resolve jurisdiction (JurisdictionResolutionService — 4-tier)
    2. Select schema (SchemaRegistryService)
    3. Build extraction template from schema + field definitions
    4. Run deterministic field extraction
    5. Persist jurisdiction metadata + field results (if document ID given)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from apps.extraction_core.serializers import ExtractionRequestSerializer
        from apps.extraction_core.services.extraction_service import ExtractionService

        serializer = ExtractionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = ExtractionService.extract(
            ocr_text=data["ocr_text"],
            document_type=data.get("document_type") or "INVOICE",
            declared_country_code=data.get("declared_country_code") or "",
            declared_regime_code=data.get("declared_regime_code") or "",
            vendor_id=data.get("vendor_id"),
            extraction_document_id=data.get("extraction_document_id"),
            enable_llm=data.get("enable_llm", False),
        )

        http_status = (
            status.HTTP_200_OK if result.resolved else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response(result.to_dict(), status=http_status)
