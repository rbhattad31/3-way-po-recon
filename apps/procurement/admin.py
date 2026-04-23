from django.contrib import admin

from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    BenchmarkResultLine,
    ComplianceResult,
    HVACStoreProfile,
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


@admin.register(HVACStoreProfile)
class HVACStoreProfileAdmin(admin.ModelAdmin):
    list_display = ("store_id", "brand", "country", "city", "store_type", "area_sqft", "budget_level", "updated_at")
    list_filter = ("country", "store_type", "budget_level", "created_at")
    search_fields = ("store_id", "brand", "city")
    readonly_fields = ("created_at", "updated_at")


class ProcurementRequestAttributeInline(admin.TabularInline):
    model = ProcurementRequestAttribute
    extra = 0


class SupplierQuotationInline(admin.TabularInline):
    model = SupplierQuotation
    extra = 0
    fields = ("vendor_name", "quotation_number", "total_amount", "currency", "extraction_status")


@admin.register(ProcurementRequest)
class ProcurementRequestAdmin(admin.ModelAdmin):
    list_display = ("request_id", "title", "domain_code", "request_type", "status", "priority", "source_channel", "prefill_status", "created_at")
    list_filter = ("status", "request_type", "domain_code", "priority", "source_channel", "prefill_status")
    search_fields = ("title", "description", "request_id")
    readonly_fields = ("request_id", "created_at", "updated_at", "prefill_confidence", "prefill_payload_json")
    inlines = [ProcurementRequestAttributeInline, SupplierQuotationInline]


@admin.register(SupplierQuotation)
class SupplierQuotationAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "quotation_number", "total_amount", "currency", "source_channel", "extraction_status", "prefill_status", "created_at")
    list_filter = ("extraction_status", "currency", "source_channel", "prefill_status")
    search_fields = ("vendor_name", "quotation_number")
    readonly_fields = ("prefill_payload_json",)


class QuotationLineItemInline(admin.TabularInline):
    model = QuotationLineItem
    extra = 0


class BenchmarkResultLineInline(admin.TabularInline):
    model = BenchmarkResultLine
    extra = 0


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("run_id", "request", "run_type", "trigger_source", "status", "confidence_score", "started_at", "completed_at")
    list_filter = ("run_type", "trigger_source", "status")
    readonly_fields = ("run_id", "created_at", "updated_at")


@admin.register(RecommendationResult)
class RecommendationResultAdmin(admin.ModelAdmin):
    list_display = ("run", "recommended_option", "confidence_score", "compliance_status")
    list_filter = ("compliance_status",)


@admin.register(BenchmarkResult)
class BenchmarkResultAdmin(admin.ModelAdmin):
    list_display = ("run", "quotation", "total_quoted_amount", "total_benchmark_amount", "variance_pct", "risk_level")
    list_filter = ("risk_level",)
    inlines = [BenchmarkResultLineInline]


@admin.register(ComplianceResult)
class ComplianceResultAdmin(admin.ModelAdmin):
    list_display = ("run", "compliance_status")
    list_filter = ("compliance_status",)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class ValidationRuleInline(admin.TabularInline):
    model = ValidationRule
    extra = 0
    fields = ("rule_code", "rule_name", "rule_type", "severity", "evaluation_mode", "is_active", "display_order")


@admin.register(ValidationRuleSet)
class ValidationRuleSetAdmin(admin.ModelAdmin):
    list_display = ("rule_set_code", "rule_set_name", "domain_code", "validation_type", "is_active", "priority")
    list_filter = ("validation_type", "is_active", "domain_code")
    search_fields = ("rule_set_code", "rule_set_name")
    inlines = [ValidationRuleInline]


class ValidationResultItemInline(admin.TabularInline):
    model = ValidationResultItem
    extra = 0
    readonly_fields = ("item_code", "item_label", "category", "status", "severity", "source_type")


@admin.register(ValidationResult)
class ValidationResultAdmin(admin.ModelAdmin):
    list_display = ("run", "overall_status", "completeness_score", "readiness_for_recommendation", "readiness_for_benchmarking", "created_at")
    list_filter = ("overall_status",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [ValidationResultItemInline]
