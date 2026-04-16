from django.contrib import admin

from apps.benchmarking.models import (
    BenchmarkRequest,
    BenchmarkQuotation,
    BenchmarkLineItem,
    BenchmarkCorridorRule,
    BenchmarkResult,
    CategoryMaster,
    VarianceThresholdConfig,
)


class BenchmarkQuotationInline(admin.TabularInline):
    model = BenchmarkQuotation
    extra = 0
    readonly_fields = ("extraction_status", "extraction_error", "blob_url", "created_at")
    fields = ("supplier_name", "quotation_ref", "document", "blob_url", "extraction_status", "created_at")


@admin.register(BenchmarkRequest)
class BenchmarkRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "geography", "scope_type", "store_type", "status", "rfq_source", "rfq_ref", "submitted_by", "created_at")
    list_filter = ("status", "geography", "scope_type")
    search_fields = ("title", "project_name")
    readonly_fields = ("rfq_document", "created_at", "updated_at")
    inlines = [BenchmarkQuotationInline]


@admin.register(BenchmarkQuotation)
class BenchmarkQuotationAdmin(admin.ModelAdmin):
    list_display = ("request", "supplier_name", "quotation_ref", "extraction_status", "blob_url", "created_at")
    list_filter = ("extraction_status",)
    search_fields = ("supplier_name", "quotation_ref")
    readonly_fields = ("extracted_text", "blob_url", "blob_name", "created_at", "updated_at")


@admin.register(BenchmarkLineItem)
class BenchmarkLineItemAdmin(admin.ModelAdmin):
    list_display = (
        "quotation", "line_number", "description", "category", "classification_source",
        "quoted_unit_rate", "variance_pct", "variance_status",
    )
    list_filter = ("category", "variance_status", "classification_source")
    search_fields = ("description",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(BenchmarkCorridorRule)
class BenchmarkCorridorRuleAdmin(admin.ModelAdmin):
    list_display = (
        "rule_code", "name", "category", "scope_type", "geography",
        "uom", "min_rate", "mid_rate", "max_rate", "currency", "priority", "is_active",
    )
    list_filter = ("category", "scope_type", "geography", "is_active")
    search_fields = ("rule_code", "name", "keywords")
    ordering = ("category", "geography", "priority")


@admin.register(BenchmarkResult)
class BenchmarkResultAdmin(admin.ModelAdmin):
    list_display = (
        "request", "total_quoted", "total_benchmark_mid",
        "overall_deviation_pct", "overall_status",
    )
    list_filter = ("overall_status",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CategoryMaster)
class CategoryMasterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "pricing_type", "sort_order", "is_active")
    list_filter = ("pricing_type", "is_active")
    search_fields = ("code", "name", "keywords_csv")
    ordering = ("sort_order", "code")


@admin.register(VarianceThresholdConfig)
class VarianceThresholdConfigAdmin(admin.ModelAdmin):
    list_display = (
        "category", "geography",
        "within_range_max_pct", "moderate_max_pct", "is_active",
    )
    list_filter = ("category", "geography", "is_active")
    ordering = ("category", "geography")




