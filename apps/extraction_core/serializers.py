"""DRF serializers for extraction_core models."""
from rest_framework import serializers

from apps.extraction_core.models import (
    EntityExtractionProfile,
    ExtractionRuntimeSettings,
    ExtractionSchemaDefinition,
    TaxJurisdictionProfile,
)


class TaxJurisdictionProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxJurisdictionProfile
        fields = [
            "id",
            "country_code",
            "country_name",
            "tax_regime",
            "regime_full_name",
            "default_currency",
            "tax_id_label",
            "tax_id_regex",
            "date_formats",
            "locale_code",
            "fiscal_year_start_month",
            "config_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TaxJurisdictionProfileListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""

    class Meta:
        model = TaxJurisdictionProfile
        fields = [
            "id",
            "country_code",
            "country_name",
            "tax_regime",
            "default_currency",
            "tax_id_label",
            "is_active",
        ]


class ExtractionSchemaDefinitionSerializer(serializers.ModelSerializer):
    jurisdiction_name = serializers.CharField(
        source="jurisdiction.__str__", read_only=True
    )

    class Meta:
        model = ExtractionSchemaDefinition
        fields = [
            "id",
            "jurisdiction",
            "jurisdiction_name",
            "document_type",
            "schema_version",
            "name",
            "description",
            "header_fields_json",
            "line_item_fields_json",
            "tax_fields_json",
            "config_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ExtractionSchemaDefinitionListSerializer(serializers.ModelSerializer):
    jurisdiction_name = serializers.CharField(
        source="jurisdiction.__str__", read_only=True
    )

    class Meta:
        model = ExtractionSchemaDefinition
        fields = [
            "id",
            "jurisdiction",
            "jurisdiction_name",
            "document_type",
            "schema_version",
            "name",
            "is_active",
        ]


class JurisdictionResolveRequestSerializer(serializers.Serializer):
    """Input for the jurisdiction resolver endpoint."""

    ocr_text = serializers.CharField(
        help_text="Raw OCR text from the document",
        max_length=200000,
    )
    hint_country_code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional country code hint (e.g. IN, AE, SA)",
    )
    hint_tax_regime = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional tax regime hint (e.g. GST, VAT)",
    )


class SchemaLookupRequestSerializer(serializers.Serializer):
    """Input for the schema registry lookup endpoint."""

    country_code = serializers.CharField(
        max_length=3,
        help_text="ISO country code (e.g. IN, AE, SA)",
    )
    document_type = serializers.CharField(
        max_length=50,
        help_text="Document type (e.g. INVOICE)",
    )
    version = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional specific schema version (e.g. 1.0). Omit for latest.",
    )
    tax_regime = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Optional tax regime filter (e.g. GST, VAT)",
    )


# ---------------------------------------------------------------------------
# Runtime Settings
# ---------------------------------------------------------------------------


class ExtractionRuntimeSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionRuntimeSettings
        fields = [
            "id",
            "name",
            "jurisdiction_mode",
            "default_country_code",
            "default_regime_code",
            "enable_jurisdiction_detection",
            "allow_manual_override",
            "confidence_threshold_for_detection",
            "fallback_to_detection_on_schema_miss",
            "config_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Entity Extraction Profile
# ---------------------------------------------------------------------------


class EntityExtractionProfileSerializer(serializers.ModelSerializer):
    entity_name = serializers.CharField(
        source="entity.name", read_only=True
    )
    entity_code = serializers.CharField(
        source="entity.code", read_only=True
    )

    class Meta:
        model = EntityExtractionProfile
        fields = [
            "id",
            "entity",
            "entity_name",
            "entity_code",
            "default_country_code",
            "default_regime_code",
            "default_document_language",
            "jurisdiction_mode",
            "schema_override_code",
            "validation_profile_override_code",
            "normalization_profile_override_code",
            "config_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class EntityExtractionProfileListSerializer(serializers.ModelSerializer):
    entity_name = serializers.CharField(
        source="entity.name", read_only=True
    )

    class Meta:
        model = EntityExtractionProfile
        fields = [
            "id",
            "entity",
            "entity_name",
            "default_country_code",
            "default_regime_code",
            "jurisdiction_mode",
            "is_active",
        ]


# ---------------------------------------------------------------------------
# Resolution request / response
# ---------------------------------------------------------------------------


class JurisdictionResolutionRequestSerializer(serializers.Serializer):
    """Input for the full jurisdiction resolution endpoint."""

    ocr_text = serializers.CharField(
        help_text="Raw OCR text from the document",
        max_length=200000,
    )
    declared_country_code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Document-level country code override",
    )
    declared_regime_code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Document-level regime code override",
    )
    vendor_id = serializers.IntegerField(
        required=False,
        default=None,
        allow_null=True,
        help_text="Vendor PK to look up entity extraction profile",
    )


# ---------------------------------------------------------------------------
# Extraction pipeline
# ---------------------------------------------------------------------------


class ExtractionRequestSerializer(serializers.Serializer):
    """Input for the ExtractionService.extract() endpoint."""

    ocr_text = serializers.CharField(
        help_text="Raw OCR text from the document",
        max_length=200000,
    )
    document_type = serializers.CharField(
        required=False,
        default="INVOICE",
        help_text="Document type (default INVOICE)",
    )
    declared_country_code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Document-level country code override (Tier 1)",
    )
    declared_regime_code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="Document-level regime code override (Tier 1)",
    )
    vendor_id = serializers.IntegerField(
        required=False,
        default=None,
        allow_null=True,
        help_text="Vendor PK for entity profile lookup (Tier 2)",
    )
    extraction_document_id = serializers.IntegerField(
        required=False,
        default=None,
        allow_null=True,
        help_text="ExtractionDocument PK to persist results on",
    )
    enable_llm = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Enable LLM extraction for unresolved / low-confidence fields",
    )
