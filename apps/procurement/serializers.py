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
            "brand", "model", "extraction_confidence",
        ]


class SupplierQuotationListSerializer(serializers.ModelSerializer):
    line_item_count = serializers.IntegerField(source="line_items.count", read_only=True)

    class Meta:
        model = SupplierQuotation
        fields = [
            "id", "vendor_name", "quotation_number", "quotation_date",
            "total_amount", "currency", "extraction_status",
            "extraction_confidence", "line_item_count", "created_at",
        ]


class SupplierQuotationDetailSerializer(serializers.ModelSerializer):
    line_items = QuotationLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = SupplierQuotation
        fields = [
            "id", "vendor_name", "quotation_number", "quotation_date",
            "total_amount", "currency", "extraction_status",
            "extraction_confidence", "line_items", "created_at", "updated_at",
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
