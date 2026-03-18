from django.contrib import admin

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


class ProcurementRequestAttributeInline(admin.TabularInline):
    model = ProcurementRequestAttribute
    extra = 0


class SupplierQuotationInline(admin.TabularInline):
    model = SupplierQuotation
    extra = 0
    fields = ("vendor_name", "quotation_number", "total_amount", "currency", "extraction_status")


@admin.register(ProcurementRequest)
class ProcurementRequestAdmin(admin.ModelAdmin):
    list_display = ("request_id", "title", "domain_code", "request_type", "status", "priority", "created_at")
    list_filter = ("status", "request_type", "domain_code", "priority")
    search_fields = ("title", "description", "request_id")
    readonly_fields = ("request_id", "created_at", "updated_at")
    inlines = [ProcurementRequestAttributeInline, SupplierQuotationInline]


@admin.register(SupplierQuotation)
class SupplierQuotationAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "quotation_number", "total_amount", "currency", "extraction_status", "created_at")
    list_filter = ("extraction_status", "currency")
    search_fields = ("vendor_name", "quotation_number")


class QuotationLineItemInline(admin.TabularInline):
    model = QuotationLineItem
    extra = 0


class BenchmarkResultLineInline(admin.TabularInline):
    model = BenchmarkResultLine
    extra = 0


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("run_id", "request", "run_type", "status", "confidence_score", "started_at", "completed_at")
    list_filter = ("run_type", "status")
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
