"""DRF serializers for the Procurement Intelligence platform."""
from rest_framework import serializers

from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    BenchmarkResultLine,
    ComplianceResult,
    ProcurementRequest,
    ProcurementRequestAttribute,
    QuotationLineItem,
    RecommendationResult,
    SupplierQuotation,
    ValidationResult,
    ValidationResultItem,
    ValidationRule,
    ValidationRuleSet,
)


# ---------------------------------------------------------------------------
# Attribute
# ---------------------------------------------------------------------------
class ProcurementRequestAttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcurementRequestAttribute
        fields = [
            "id", "attribute_code", "attribute_label", "data_type",
            "value_text", "value_number", "value_json",
            "is_required", "normalized_value",
            "extraction_source", "confidence_score",
        ]


class AttributeWriteSerializer(serializers.Serializer):
    attribute_code = serializers.CharField(max_length=120)
    attribute_label = serializers.CharField(max_length=200, required=False, default="")
    data_type = serializers.CharField(max_length=20, required=False, default="TEXT")
    value_text = serializers.CharField(required=False, default="", allow_blank=True)
    value_number = serializers.DecimalField(max_digits=18, decimal_places=4, required=False, allow_null=True)
    value_json = serializers.JSONField(required=False, allow_null=True)
    is_required = serializers.BooleanField(required=False, default=False)


# ---------------------------------------------------------------------------
# Quotation & Line Items
# ---------------------------------------------------------------------------
class QuotationLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuotationLineItem
        fields = [
            "id", "line_number", "description", "normalized_description",
            "category_code", "quantity", "unit", "unit_rate", "total_amount",
            "brand", "model", "extraction_confidence", "extraction_source",
        ]


class SupplierQuotationListSerializer(serializers.ModelSerializer):
    line_item_count = serializers.IntegerField(source="line_items.count", read_only=True)

    class Meta:
        model = SupplierQuotation
        fields = [
            "id", "vendor_name", "quotation_number", "quotation_date",
            "total_amount", "currency", "extraction_status",
            "extraction_confidence", "prefill_status",
            "line_item_count", "created_at",
        ]


class SupplierQuotationDetailSerializer(serializers.ModelSerializer):
    line_items = QuotationLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = SupplierQuotation
        fields = [
            "id", "vendor_name", "quotation_number", "quotation_date",
            "total_amount", "currency", "extraction_status",
            "extraction_confidence", "prefill_status", "prefill_payload_json",
            "line_items", "created_at", "updated_at",
        ]


class SupplierQuotationWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierQuotation
        fields = [
            "vendor_name", "quotation_number", "quotation_date",
            "total_amount", "currency",
        ]


# ---------------------------------------------------------------------------
# Analysis Run
# ---------------------------------------------------------------------------
class AnalysisRunSerializer(serializers.ModelSerializer):
    duration_ms = serializers.ReadOnlyField()

    class Meta:
        model = AnalysisRun
        fields = [
            "id", "run_id", "run_type", "status",
            "started_at", "completed_at", "duration_ms",
            "confidence_score", "output_summary",
            "input_snapshot_json", "error_message",
            "trace_id", "created_at",
        ]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
class RecommendationResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecommendationResult
        fields = [
            "id", "recommended_option", "reasoning_summary",
            "reasoning_details_json", "confidence_score",
            "constraints_json", "compliance_status", "output_payload_json",
            "created_at",
        ]


class BenchmarkResultLineSerializer(serializers.ModelSerializer):
    line_description = serializers.CharField(source="quotation_line.description", read_only=True)
    line_number = serializers.IntegerField(source="quotation_line.line_number", read_only=True)

    class Meta:
        model = BenchmarkResultLine
        fields = [
            "id", "line_number", "line_description",
            "benchmark_min", "benchmark_avg", "benchmark_max",
            "quoted_value", "variance_pct", "variance_status", "remarks",
        ]


class BenchmarkResultSerializer(serializers.ModelSerializer):
    lines = BenchmarkResultLineSerializer(many=True, read_only=True)
    quotation_vendor = serializers.CharField(source="quotation.vendor_name", read_only=True)

    class Meta:
        model = BenchmarkResult
        fields = [
            "id", "quotation_vendor",
            "total_quoted_amount", "total_benchmark_amount",
            "variance_pct", "risk_level", "summary_json",
            "lines", "created_at",
        ]


class ComplianceResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceResult
        fields = [
            "id", "compliance_status",
            "rules_checked_json", "violations_json", "recommendations_json",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# ProcurementRequest
# ---------------------------------------------------------------------------
class ProcurementRequestListSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default="")
    attribute_count = serializers.IntegerField(source="attributes.count", read_only=True)
    quotation_count = serializers.IntegerField(source="quotations.count", read_only=True)
    run_count = serializers.IntegerField(source="analysis_runs.count", read_only=True)

    class Meta:
        model = ProcurementRequest
        fields = [
            "id", "request_id", "title", "domain_code", "schema_code",
            "request_type", "status", "priority",
            "geography_country", "geography_city", "currency",
            "created_by_email", "attribute_count", "quotation_count", "run_count",
            "created_at", "updated_at",
        ]


class ProcurementRequestDetailSerializer(serializers.ModelSerializer):
    attributes = ProcurementRequestAttributeSerializer(many=True, read_only=True)
    quotations = SupplierQuotationListSerializer(many=True, read_only=True)
    analysis_runs = AnalysisRunSerializer(many=True, read_only=True)
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default="")

    class Meta:
        model = ProcurementRequest
        fields = [
            "id", "request_id", "title", "description",
            "domain_code", "schema_code", "request_type", "status", "priority",
            "geography_country", "geography_city", "currency",
            "created_by_email", "trace_id",
            "attributes", "quotations", "analysis_runs",
            "created_at", "updated_at",
        ]


class ProcurementRequestWriteSerializer(serializers.ModelSerializer):
    attributes = AttributeWriteSerializer(many=True, required=False)

    class Meta:
        model = ProcurementRequest
        fields = [
            "title", "description", "domain_code", "schema_code",
            "request_type", "priority",
            "geography_country", "geography_city", "currency",
            "attributes",
        ]

    def create(self, validated_data):
        from apps.procurement.services.request_service import ProcurementRequestService
        attrs = validated_data.pop("attributes", [])
        user = self.context["request"].user
        return ProcurementRequestService.create_request(
            created_by=user,
            attributes=attrs,
            **validated_data,
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class ValidationResultItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationResultItem
        fields = [
            "id", "item_code", "item_label", "category",
            "status", "severity", "source_type", "source_reference",
            "remarks", "details_json", "created_at",
        ]


class ValidationResultSerializer(serializers.ModelSerializer):
    items = ValidationResultItemSerializer(many=True, read_only=True)
    request_id = serializers.UUIDField(source="run.request.request_id", read_only=True)
    run_id = serializers.UUIDField(source="run.run_id", read_only=True)

    class Meta:
        model = ValidationResult
        fields = [
            "id", "request_id", "run_id",
            "validation_type", "overall_status", "completeness_score",
            "summary_text",
            "readiness_for_recommendation", "readiness_for_benchmarking",
            "recommended_next_action",
            "missing_items_json", "warnings_json", "ambiguous_items_json",
            "output_payload_json",
            "items",
            "created_at", "updated_at",
        ]


class ValidationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValidationRule
        fields = [
            "id", "rule_code", "rule_name", "rule_type",
            "severity", "is_active", "evaluation_mode",
            "condition_json", "expected_value_json",
            "failure_message", "remediation_hint", "display_order",
        ]


class ValidationRuleSetSerializer(serializers.ModelSerializer):
    rules = ValidationRuleSerializer(many=True, read_only=True)
    rule_count = serializers.IntegerField(source="rules.count", read_only=True)

    class Meta:
        model = ValidationRuleSet
        fields = [
            "id", "domain_code", "schema_code",
            "rule_set_code", "rule_set_name", "description",
            "validation_type", "is_active", "priority",
            "config_json", "rule_count", "rules",
            "created_at", "updated_at",
        ]


class ValidationRuleSetListSerializer(serializers.ModelSerializer):
    rule_count = serializers.IntegerField(source="rules.count", read_only=True)

    class Meta:
        model = ValidationRuleSet
        fields = [
            "id", "domain_code", "schema_code",
            "rule_set_code", "rule_set_name", "description",
            "validation_type", "is_active", "priority",
            "rule_count", "created_at",
        ]


# ---------------------------------------------------------------------------
# Prefill serializers
# ---------------------------------------------------------------------------
class RequestPrefillUploadSerializer(serializers.Serializer):
    """Upload an RFQ / requirement PDF to create a draft request and trigger prefill."""
    file = serializers.FileField()
    title = serializers.CharField(max_length=300, required=False, default="")
    source_document_type = serializers.ChoiceField(
        choices=["RFQ", "REQUIREMENT_NOTE", "SPECIFICATION", "BOQ", "OTHER"],
        required=False,
        default="RFQ",
    )
    domain_code = serializers.CharField(max_length=100, required=False, default="")


class RequestPrefillConfirmSerializer(serializers.Serializer):
    """Submit user-reviewed prefill data for a procurement request."""
    core_fields = serializers.DictField(required=False, default=dict)
    attributes = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class QuotationPrefillUploadSerializer(serializers.Serializer):
    """Upload a proposal / quotation PDF to create a draft quotation and trigger prefill."""
    file = serializers.FileField()
    vendor_name = serializers.CharField(max_length=300, required=False, default="")


class QuotationPrefillConfirmSerializer(serializers.Serializer):
    """Submit user-reviewed prefill data for a supplier quotation."""
    header_fields = serializers.DictField(required=False, default=dict)
    line_items = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class PrefillStatusSerializer(serializers.Serializer):
    """Prefill status response for polling."""
    prefill_status = serializers.CharField()
    prefill_confidence = serializers.FloatField(allow_null=True)
    prefill_payload = serializers.DictField(allow_null=True)
