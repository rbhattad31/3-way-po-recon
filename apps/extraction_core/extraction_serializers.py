"""DRF serializers for enhanced extraction pipeline models."""
from rest_framework import serializers

from apps.extraction_core.models import (
    CountryPack,
    ExtractionAnalyticsSnapshot,
    ExtractionApprovalRecord,
    ExtractionCorrection,
    ExtractionEvidence,
    ExtractionFieldValue,
    ExtractionIssue,
    ExtractionLineItem,
    ExtractionRun,
)


# ---------------------------------------------------------------------------
# ExtractionRun
# ---------------------------------------------------------------------------


class ExtractionRunListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer for ExtractionRun."""

    class Meta:
        model = ExtractionRun
        fields = [
            "id",
            "document",
            "status",
            "country_code",
            "regime_code",
            "jurisdiction_source",
            "schema_code",
            "schema_version",
            "overall_confidence",
            "extraction_method",
            "review_queue",
            "requires_review",
            "duration_ms",
            "created_at",
        ]


class ExtractionRunDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer for ExtractionRun."""

    approval = serializers.SerializerMethodField()

    class Meta:
        model = ExtractionRun
        fields = [
            "id",
            "document",
            "status",
            "country_code",
            "regime_code",
            "jurisdiction_source",
            "jurisdiction",
            "schema_code",
            "schema_version",
            "schema",
            "prompt_code",
            "prompt_version",
            "overall_confidence",
            "header_confidence",
            "tax_confidence",
            "line_item_confidence",
            "jurisdiction_confidence",
            "extraction_method",
            "extracted_data_json",
            "review_queue",
            "requires_review",
            "review_reasons_json",
            "started_at",
            "completed_at",
            "duration_ms",
            "error_message",
            "field_count",
            "mandatory_coverage_pct",
            "field_coverage_pct",
            "approval",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_approval(self, obj):
        try:
            approval = obj.approval
            return ExtractionApprovalRecordSerializer(approval).data
        except ExtractionApprovalRecord.DoesNotExist:
            return None


class ExtractionRunSummarySerializer(serializers.Serializer):
    """Summary response for GET /extraction/{id}/summary."""

    id = serializers.IntegerField()
    status = serializers.CharField()
    country_code = serializers.CharField()
    regime_code = serializers.CharField()
    jurisdiction_source = serializers.CharField()
    schema_code = serializers.CharField()
    schema_version = serializers.CharField()
    overall_confidence = serializers.FloatField()
    extraction_method = serializers.CharField()
    review_queue = serializers.CharField()
    requires_review = serializers.BooleanField()
    review_reasons = serializers.ListField(child=serializers.CharField())
    field_count = serializers.IntegerField()
    field_coverage_pct = serializers.FloatField()
    mandatory_coverage_pct = serializers.FloatField()
    duration_ms = serializers.IntegerField()
    has_approval = serializers.BooleanField()
    issue_count = serializers.IntegerField()
    evidence_count = serializers.IntegerField()
    correction_count = serializers.IntegerField()


# ---------------------------------------------------------------------------
# ExtractionFieldValue
# ---------------------------------------------------------------------------


class ExtractionFieldValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionFieldValue
        fields = [
            "id",
            "field_code",
            "value",
            "normalized_value",
            "confidence",
            "extraction_method",
            "is_corrected",
            "corrected_value",
            "category",
            "line_item_index",
            "is_valid",
            "validation_message",
        ]


# ---------------------------------------------------------------------------
# ExtractionLineItem
# ---------------------------------------------------------------------------


class ExtractionLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionLineItem
        fields = [
            "id",
            "line_index",
            "data_json",
            "confidence",
            "page_number",
            "is_valid",
        ]


# ---------------------------------------------------------------------------
# ExtractionEvidence
# ---------------------------------------------------------------------------


class ExtractionEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionEvidence
        fields = [
            "id",
            "field_code",
            "page_number",
            "snippet",
            "bounding_box",
            "extraction_method",
            "confidence",
            "line_item_index",
        ]


# ---------------------------------------------------------------------------
# ExtractionIssue
# ---------------------------------------------------------------------------


class ExtractionIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionIssue
        fields = [
            "id",
            "severity",
            "field_code",
            "check_type",
            "message",
            "details_json",
        ]


# ---------------------------------------------------------------------------
# ExtractionApprovalRecord
# ---------------------------------------------------------------------------


class ExtractionApprovalRecordSerializer(serializers.ModelSerializer):
    approved_by_email = serializers.EmailField(
        source="approved_by.email", read_only=True, default=""
    )

    class Meta:
        model = ExtractionApprovalRecord
        fields = [
            "id",
            "extraction_run",
            "action",
            "approved_by",
            "approved_by_email",
            "comments",
            "decided_at",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# ExtractionCorrection
# ---------------------------------------------------------------------------


class ExtractionCorrectionSerializer(serializers.ModelSerializer):
    corrected_by_email = serializers.EmailField(
        source="corrected_by.email", read_only=True, default=""
    )

    class Meta:
        model = ExtractionCorrection
        fields = [
            "id",
            "extraction_run",
            "field_code",
            "original_value",
            "corrected_value",
            "correction_reason",
            "corrected_by",
            "corrected_by_email",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# ExtractionAnalyticsSnapshot
# ---------------------------------------------------------------------------


class ExtractionAnalyticsSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractionAnalyticsSnapshot
        fields = [
            "id",
            "snapshot_type",
            "country_code",
            "regime_code",
            "period_start",
            "period_end",
            "data_json",
            "run_count",
            "correction_count",
            "average_confidence",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# CountryPack
# ---------------------------------------------------------------------------


class CountryPackSerializer(serializers.ModelSerializer):
    country_code = serializers.CharField(
        source="jurisdiction.country_code", read_only=True
    )
    country_name = serializers.CharField(
        source="jurisdiction.country_name", read_only=True
    )
    regime = serializers.CharField(
        source="jurisdiction.tax_regime", read_only=True
    )

    class Meta:
        model = CountryPack
        fields = [
            "id",
            "jurisdiction",
            "country_code",
            "country_name",
            "regime",
            "pack_status",
            "schema_version",
            "validation_profile_version",
            "normalization_profile_version",
            "activated_at",
            "deactivated_at",
            "config_json",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Action request serializers
# ---------------------------------------------------------------------------


class CorrectFieldRequestSerializer(serializers.Serializer):
    """Input for POST correct-field action."""
    field_code = serializers.CharField(max_length=100)
    corrected_value = serializers.CharField()
    correction_reason = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class ApproveRejectRequestSerializer(serializers.Serializer):
    """Input for POST approve / reject actions."""
    comments = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class EscalateRequestSerializer(serializers.Serializer):
    """Input for POST escalate action."""
    comments = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    target_queue = serializers.CharField(
        required=False, allow_blank=True, default=""
    )


class RunPipelineRequestSerializer(serializers.Serializer):
    """Input for POST run-pipeline action."""
    extraction_document_id = serializers.IntegerField()
    ocr_text = serializers.CharField(max_length=200_000)
    document_type = serializers.CharField(
        required=False, default="INVOICE", max_length=50,
    )
    vendor_id = serializers.IntegerField(
        required=False, default=None, allow_null=True,
    )
    enable_llm = serializers.BooleanField(required=False, default=False)
