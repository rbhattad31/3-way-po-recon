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
    Room,
    Product,
    Vendor,
    VendorProduct,
    PurchaseHistory,
    RecommendationLog,
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

# =============================================================================
# RoomWise Pre-Procurement Recommender Serializers
# =============================================================================


class RoomSerializer(serializers.ModelSerializer):
    """Serializer for Room model."""
    class Meta:
        model = Room
        fields = [
            "id", "room_id", "room_code", "building_name", "floor_number",
            "location_description", "area_sqm", "ceiling_height_m",
            "usage_type", "design_temp_c", "temp_tolerance_c",
            "design_cooling_load_kw", "design_humidity_pct", "noise_limit_db",
            "current_hvac_type", "current_hvac_age_years", "access_constraints",
            "contact_name", "contact_email", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "room_id", "created_at", "updated_at"]


class ProductSerializer(serializers.ModelSerializer):
    """Serializer for Product model."""
    class Meta:
        model = Product
        fields = [
            "id", "product_id", "sku", "manufacturer", "product_name",
            "system_type", "capacity_kw", "sound_level_db_full_load",
            "sound_level_db_part_load", "power_input_kw", "refrigerant_type",
            "cop_rating", "seer_rating", "length_mm", "width_mm", "height_mm",
            "weight_kg", "warranty_months", "installation_support_required",
            "approved_use_cases", "efficiency_compliance", "datasheet_url",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "product_id", "created_at", "updated_at"]


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for Vendor model."""
    class Meta:
        model = Vendor
        fields = [
            "id", "vendor_id", "vendor_name", "country", "city", "address",
            "contact_email", "contact_phone", "average_lead_time_days",
            "payment_terms", "min_order_qty", "bulk_discount_available",
            "rush_order_capable", "preferred_vendor", "reliability_score",
            "total_purchases", "on_time_delivery_pct", "quality_issues_count",
            "notes", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "vendor_id", "created_at", "updated_at"]


class VendorProductDetailSerializer(serializers.ModelSerializer):
    """Serializer for VendorProduct with nested vendor and product details."""
    vendor = VendorSerializer(read_only=True)
    product = ProductSerializer(read_only=True)

    class Meta:
        model = VendorProduct
        fields = [
            "id", "vendor_product_id", "vendor", "product", "vendor_sku",
            "unit_price", "currency", "stock_available", "lead_time_days",
            "bulk_discount_pct", "installation_cost", "warranty_months_extended",
            "last_quoted", "quote_validity_days", "is_preferred", "is_active",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "vendor_product_id", "created_at", "updated_at"]


class VendorProductSerializer(serializers.ModelSerializer):
    """Lightweight serializer for VendorProduct."""
    class Meta:
        model = VendorProduct
        fields = [
            "id", "vendor_product_id", "vendor_id", "product_id", "vendor_sku",
            "unit_price", "currency", "stock_available", "lead_time_days",
            "bulk_discount_pct", "installation_cost", "warranty_months_extended",
            "is_preferred", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "vendor_product_id", "created_at", "updated_at"]


class PurchaseHistorySerializer(serializers.ModelSerializer):
    """Serializer for PurchaseHistory model."""
    vendor_name = serializers.CharField(source="vendor.vendor_name", read_only=True)
    product_name = serializers.CharField(source="product.product_name", read_only=True)
    room_code = serializers.CharField(source="room.room_code", read_only=True)

    class Meta:
        model = PurchaseHistory
        fields = [
            "id", "po_id", "po_number", "room_id", "room_code", "product_id",
            "product_name", "vendor_id", "vendor_name", "vendor_product_id",
            "quantity", "unit_price", "total_cost", "currency", "po_date",
            "promised_delivery_date", "actual_delivery_date", "po_status",
            "performance_rating", "meets_spec", "issues_reported",
            "delivered_by", "installer_name", "installation_date",
            "created_by_id", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "po_id", "created_at", "updated_at"]


class RecommendationResultSerializer(serializers.Serializer):
    """Single recommendation result in the ranked list."""
    rank = serializers.IntegerField()
    vendor_product_id = serializers.CharField()
    vendor_name = serializers.CharField()
    product_sku = serializers.CharField()
    product_name = serializers.CharField()
    capacity_kw = serializers.DecimalField(max_digits=8, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
    lead_time_days = serializers.IntegerField()
    noise_db = serializers.IntegerField()
    composite_score = serializers.DecimalField(max_digits=5, decimal_places=2)
    price_score = serializers.DecimalField(max_digits=5, decimal_places=2)
    performance_score = serializers.DecimalField(max_digits=5, decimal_places=2)
    delivery_score = serializers.DecimalField(max_digits=5, decimal_places=2)
    vendor_score = serializers.DecimalField(max_digits=5, decimal_places=2)
    fit_score = serializers.DecimalField(max_digits=5, decimal_places=2)
    reason = serializers.CharField()
    risk_tags = serializers.ListField(child=serializers.CharField())


class RecommendationLogSerializer(serializers.ModelSerializer):
    """Serializer for RecommendationLog."""
    room_code = serializers.CharField(source="room.room_code", read_only=True)
    recommended_products = RecommendationResultSerializer(
        source="recommended_products_json", many=True, read_only=True
    )

    class Meta:
        model = RecommendationLog
        fields = [
            "id", "recommendation_id", "room_id", "room_code",
            "requirement_text", "recommendation_input_json",
            "recommended_products", "recommendation_method",
            "top_ranked_vendor_product_id", "top_ranked_score",
            "num_options_generated", "requested_by_id", "user_feedback",
            "is_accepted", "outcome_purchase_order_id",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "recommendation_id", "recommended_products",
            "created_at", "updated_at",
        ]


class RunRecommendationSerializer(serializers.Serializer):
    """Input serializer for triggering a recommendation run."""
    room_id = serializers.CharField(required=False, allow_blank=True)
    requirement_text = serializers.CharField(allow_blank=True, required=False)
    budget_max = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
        help_text="Maximum budget in local currency"
    )
    preferred_lead_time_days = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="Preferred delivery timeline"
    )
    exclude_vendors = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text="Vendor IDs to exclude from results"
    )
    preferred_system_types = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text="Preferred HVAC system types (VRF, SPLIT_AC, etc.)"
    )