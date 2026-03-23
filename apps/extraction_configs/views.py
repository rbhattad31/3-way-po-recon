"""DRF views for extraction_configs."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.extraction_configs.models import NormalizationProfile, TaxFieldDefinition
from apps.extraction_configs.serializers import (
    NormalizationProfileSerializer,
    TaxFieldDefinitionListSerializer,
    TaxFieldDefinitionSerializer,
)
from apps.extraction_configs.services.field_registry import FieldRegistryService


class TaxFieldDefinitionViewSet(viewsets.ModelViewSet):
    """CRUD for Tax Field Definitions."""

    queryset = TaxFieldDefinition.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return TaxFieldDefinitionListSerializer
        return TaxFieldDefinitionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category__iexact=category)
        data_type = self.request.query_params.get("data_type")
        if data_type:
            qs = qs.filter(data_type__iexact=data_type)
        is_mandatory = self.request.query_params.get("is_mandatory")
        if is_mandatory is not None:
            qs = qs.filter(is_mandatory=is_mandatory.lower() in ("true", "1"))
        is_tax_field = self.request.query_params.get("is_tax_field")
        if is_tax_field is not None:
            qs = qs.filter(is_tax_field=is_tax_field.lower() in ("true", "1"))
        schema_id = self.request.query_params.get("schema")
        if schema_id:
            qs = qs.filter(schemas__id=schema_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1"))
        return qs

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        self._invalidate_field_caches(instance)

    def perform_update(self, serializer):
        instance = serializer.save(updated_by=self.request.user)
        self._invalidate_field_caches(instance)

    @staticmethod
    def _invalidate_field_caches(field_def: TaxFieldDefinition) -> None:
        """Invalidate cached snapshots for every schema this field belongs to."""
        for schema in field_def.schemas.all():
            FieldRegistryService.invalidate_cache(schema_id=schema.pk)


class NormalizationProfileViewSet(viewsets.ModelViewSet):
    """CRUD for Normalization Profiles."""

    queryset = NormalizationProfile.objects.select_related("jurisdiction").all()
    permission_classes = [IsAuthenticated]
    serializer_class = NormalizationProfileSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
