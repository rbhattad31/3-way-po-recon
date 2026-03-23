"""DRF serializers for extraction_configs models."""
from rest_framework import serializers

from apps.extraction_configs.models import NormalizationProfile, TaxFieldDefinition


class TaxFieldDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxFieldDefinition
        fields = [
            "id",
            "field_key",
            "display_name",
            "description",
            "data_type",
            "category",
            "is_mandatory",
            "is_tax_field",
            "validation_regex",
            "validation_rules_json",
            "normalization_rules_json",
            "aliases",
            "schemas",
            "sort_order",
            "config_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TaxFieldDefinitionListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""

    class Meta:
        model = TaxFieldDefinition
        fields = [
            "id",
            "field_key",
            "display_name",
            "data_type",
            "category",
            "is_mandatory",
            "is_tax_field",
            "is_active",
            "sort_order",
        ]


class NormalizationProfileSerializer(serializers.ModelSerializer):
    jurisdiction_name = serializers.CharField(
        source="jurisdiction.__str__", read_only=True
    )

    class Meta:
        model = NormalizationProfile
        fields = [
            "id",
            "jurisdiction",
            "jurisdiction_name",
            "date_input_formats",
            "date_output_format",
            "decimal_separator",
            "thousands_separator",
            "currency_symbol",
            "address_format_json",
            "custom_rules_json",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
