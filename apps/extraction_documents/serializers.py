"""DRF serializers for extraction_documents models."""
from rest_framework import serializers

from apps.extraction_documents.models import ExtractionDocument, ExtractionFieldResult


class ExtractionFieldResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionFieldResult
        fields = [
            "id",
            "field_key",
            "raw_value",
            "normalized_value",
            "confidence",
            "extraction_method",
            "source_text_snippet",
            "page_number",
            "line_item_index",
            "is_valid",
            "validation_message",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ExtractionDocumentSerializer(serializers.ModelSerializer):
    field_results = ExtractionFieldResultSerializer(
        source="field_results", many=True, read_only=True
    )
    jurisdiction_name = serializers.CharField(
        source="resolved_jurisdiction.__str__", read_only=True, default=None
    )
    schema_name = serializers.CharField(
        source="resolved_schema.__str__", read_only=True, default=None
    )

    class Meta:
        model = ExtractionDocument
        fields = [
            "id",
            "document_upload",
            "file_name",
            "file_path",
            "file_hash",
            "page_count",
            "resolved_jurisdiction",
            "jurisdiction_name",
            "resolved_schema",
            "schema_name",
            "jurisdiction_confidence",
            "jurisdiction_signals_json",
            "declared_country_code",
            "declared_regime_code",
            "jurisdiction_source",
            "jurisdiction_resolution_mode",
            "jurisdiction_warning",
            "classified_document_type",
            "classification_confidence",
            "status",
            "ocr_engine",
            "extracted_data_json",
            "extraction_confidence",
            "extraction_method",
            "validation_errors_json",
            "validation_warnings_json",
            "is_valid",
            "extraction_started_at",
            "extraction_completed_at",
            "duration_ms",
            "error_message",
            "field_results",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at",
            "jurisdiction_confidence", "jurisdiction_signals_json",
            "extraction_confidence", "extraction_method",
            "validation_errors_json", "validation_warnings_json",
            "is_valid", "duration_ms",
        ]


class ExtractionDocumentListSerializer(serializers.ModelSerializer):
    jurisdiction_name = serializers.CharField(
        source="resolved_jurisdiction.__str__", read_only=True, default=None
    )

    class Meta:
        model = ExtractionDocument
        fields = [
            "id",
            "file_name",
            "status",
            "classified_document_type",
            "jurisdiction_name",
            "extraction_confidence",
            "is_valid",
            "created_at",
        ]
