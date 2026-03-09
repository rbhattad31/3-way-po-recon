from django.contrib import admin
from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationRun,
    ReconciliationResult,
    ReconciliationResultLine,
    ReconciliationException,
)


class ResultLineInline(admin.TabularInline):
    model = ReconciliationResultLine
    extra = 0
    readonly_fields = (
        "invoice_line", "po_line", "match_status",
        "qty_invoice", "qty_po", "qty_received", "qty_difference", "qty_within_tolerance",
        "price_invoice", "price_po", "price_difference", "price_within_tolerance",
        "amount_invoice", "amount_po", "amount_difference", "amount_within_tolerance",
    )


class ExceptionInline(admin.TabularInline):
    model = ReconciliationException
    extra = 0
    readonly_fields = ("exception_type", "severity", "message", "resolved", "resolved_by", "resolved_at")


class ResultInline(admin.TabularInline):
    model = ReconciliationResult
    extra = 0
    fields = ("invoice", "purchase_order", "match_status", "requires_review")
    readonly_fields = fields
    show_change_link = True


@admin.register(ReconciliationConfig)
class ReconciliationConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "quantity_tolerance_pct", "price_tolerance_pct", "amount_tolerance_pct", "is_default", "enable_agents")
    list_filter = ("is_default", "enable_agents")


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id", "status", "started_at", "completed_at", "total_invoices",
        "matched_count", "partial_count", "unmatched_count", "error_count", "review_count",
    )
    list_filter = ("status",)
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [ResultInline]


@admin.register(ReconciliationResult)
class ReconciliationResultAdmin(admin.ModelAdmin):
    list_display = (
        "id", "run", "invoice", "purchase_order", "match_status",
        "requires_review", "deterministic_confidence", "created_at",
    )
    list_filter = ("match_status", "requires_review")
    search_fields = ("invoice__invoice_number", "purchase_order__po_number")
    readonly_fields = ("created_at", "updated_at")
    inlines = [ResultLineInline, ExceptionInline]


@admin.register(ReconciliationException)
class ReconciliationExceptionAdmin(admin.ModelAdmin):
    list_display = ("id", "result", "exception_type", "severity", "resolved", "created_at")
    list_filter = ("exception_type", "severity", "resolved")
    search_fields = ("message",)
    readonly_fields = ("created_at", "updated_at")
