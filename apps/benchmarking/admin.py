from django.contrib import admin

from apps.benchmarking.models import (
    BenchmarkRequest,
    BenchmarkQuotation,
    BenchmarkLineItem,
    BenchmarkCorridorRule,
    BenchmarkResult,
)


class BenchmarkQuotationInline(admin.TabularInline):
    model = BenchmarkQuotation
    extra = 0
    readonly_fields = ("extraction_status", "extraction_error", "created_at")
    fields = ("supplier_name", "quotation_ref", "document", "extraction_status", "created_at")


@admin.register(BenchmarkRequest)
class BenchmarkRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "geography", "scope_type", "store_type", "status", "submitted_by", "created_at")
    list_filter = ("status", "geography", "scope_type")
    search_fields = ("title", "project_name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [BenchmarkQuotationInline]


@admin.register(BenchmarkQuotation)
class BenchmarkQuotationAdmin(admin.ModelAdmin):
    list_display = ("request", "supplier_name", "quotation_ref", "extraction_status", "created_at")
    list_filter = ("extraction_status",)
    search_fields = ("supplier_name", "quotation_ref")
    readonly_fields = ("extracted_text", "created_at", "updated_at")


@admin.register(BenchmarkLineItem)
class BenchmarkLineItemAdmin(admin.ModelAdmin):
    list_display = (
        "quotation", "line_number", "description", "category",
        "quoted_unit_rate", "variance_pct", "variance_status",
    )
    list_filter = ("category", "variance_status")
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
