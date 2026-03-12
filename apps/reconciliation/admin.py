from django.contrib import admin
from django.utils.html import format_html

from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationPolicy,
    ReconciliationRun,
    ReconciliationResult,
    ReconciliationResultLine,
    ReconciliationException,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class ResultLineInline(admin.TabularInline):
    model = ReconciliationResultLine
    extra = 0
    fields = (
        "invoice_line", "po_line", "match_status",
        "qty_invoice", "qty_po", "qty_within_tolerance",
        "price_invoice", "price_po", "price_within_tolerance",
        "amount_invoice", "amount_po", "amount_within_tolerance",
        "description_similarity",
    )
    readonly_fields = fields
    show_change_link = True


class ExceptionInline(admin.TabularInline):
    model = ReconciliationException
    extra = 0
    fields = ("exception_type", "severity", "message", "resolved", "resolved_by", "resolved_at")
    readonly_fields = fields
    show_change_link = True


class ResultInline(admin.TabularInline):
    model = ReconciliationResult
    extra = 0
    fields = ("invoice", "purchase_order", "match_status", "requires_review", "deterministic_confidence")
    readonly_fields = fields
    show_change_link = True


# ---------------------------------------------------------------------------
# Admin classes
# ---------------------------------------------------------------------------
@admin.register(ReconciliationConfig)
class ReconciliationConfigAdmin(admin.ModelAdmin):
    list_display = (
        "name", "default_reconciliation_mode", "quantity_tolerance_pct", "price_tolerance_pct",
        "amount_tolerance_pct", "extraction_confidence_threshold",
        "is_default", "auto_close_on_match", "enable_agents", "enable_mode_resolver",
    )
    list_filter = ("is_default", "enable_agents", "auto_close_on_match", "default_reconciliation_mode")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("name", "is_default")}),
        ("Tolerance Thresholds", {"fields": (
            "quantity_tolerance_pct", "price_tolerance_pct", "amount_tolerance_pct",
        )}),
        ("Auto-Close Tolerance (wider band)", {"fields": (
            "auto_close_qty_tolerance_pct", "auto_close_price_tolerance_pct",
            "auto_close_amount_tolerance_pct",
        )}),
        ("Behavior", {"fields": (
            "auto_close_on_match", "enable_agents", "extraction_confidence_threshold",
        )}),
        ("Reconciliation Mode", {"fields": (
            "default_reconciliation_mode", "enable_mode_resolver",
            "enable_grn_for_stock_items", "enable_two_way_for_services",
        )}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id", "status_badge", "config", "started_at", "completed_at", "total_invoices",
        "matched_count", "partial_count", "unmatched_count", "error_count", "review_count",
        "triggered_by",
    )
    list_filter = ("status", "config", "reconciliation_mode")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = (
        "created_at", "updated_at", "created_by", "updated_by",
        "started_at", "completed_at", "celery_task_id",
    )
    inlines = [ResultInline]
    fieldsets = (
        ("Run Info", {"fields": ("status", "config", "triggered_by", "celery_task_id")}),
        ("Timing", {"fields": ("started_at", "completed_at")}),
        ("Counts", {"fields": (
            "total_invoices", "matched_count", "partial_count",
            "unmatched_count", "error_count", "review_count",
        )}),
        ("Mode", {"fields": (
            "reconciliation_mode", "policy_name_applied",
            "grn_required_flag", "grn_checked_flag",
        )}),
        ("Error", {"fields": ("error_message",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "PENDING": "#6c757d", "RUNNING": "#0d6efd",
            "COMPLETED": "#198754", "FAILED": "#dc3545", "PARTIAL": "#ffc107",
        }
        c = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_status_display(),
        )


@admin.register(ReconciliationResult)
class ReconciliationResultAdmin(admin.ModelAdmin):
    list_display = (
        "id", "run", "invoice", "purchase_order", "match_badge",
        "review_flag", "vendor_match", "currency_match", "po_total_match",
        "confidence_display", "exception_count", "created_at",
    )
    list_filter = ("match_status", "requires_review", "vendor_match", "currency_match", "grn_available", "reconciliation_mode")
    search_fields = ("invoice__invoice_number", "purchase_order__po_number", "summary", "policy_applied")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [ResultLineInline, ExceptionInline]
    fieldsets = (
        ("Links", {"fields": ("run", "invoice", "purchase_order")}),
        ("Outcome", {"fields": ("match_status", "requires_review")}),
        ("Header Evidence", {"fields": (
            "vendor_match", "currency_match", "po_total_match",
            "invoice_total_vs_po", "total_amount_difference", "total_amount_difference_pct",
        )}),
        ("GRN", {"fields": ("grn_available", "grn_fully_received")}),
        ("Confidence", {"fields": ("extraction_confidence", "deterministic_confidence")}),
        ("Reconciliation Mode", {"fields": (
            "reconciliation_mode", "policy_applied", "mode_resolution_reason",
            "grn_required_flag", "grn_checked_flag",
            "is_two_way_result", "is_three_way_result",
        )}),
        ("Summary", {"fields": ("summary",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Match")
    def match_badge(self, obj):
        colours = {
            "MATCHED": "#198754", "PARTIAL_MATCH": "#ffc107",
            "UNMATCHED": "#dc3545", "ERROR": "#dc3545",
            "REQUIRES_REVIEW": "#0d6efd",
        }
        c = colours.get(obj.match_status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_match_status_display(),
        )

    @admin.display(description="Review", boolean=True)
    def review_flag(self, obj):
        return obj.requires_review

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.deterministic_confidence is None:
            return "-"
        pct = obj.deterministic_confidence * 100
        colour = "#198754" if pct >= 75 else ("#ffc107" if pct >= 50 else "#dc3545")
        return format_html('<span style="color:{}">{:.0f}%</span>', colour, pct)

    @admin.display(description="Exceptions")
    def exception_count(self, obj):
        count = obj.exceptions.count()
        if count == 0:
            return "0"
        return format_html('<span style="color:#dc3545;font-weight:bold;">{}</span>', count)


@admin.register(ReconciliationResultLine)
class ReconciliationResultLineAdmin(admin.ModelAdmin):
    list_display = (
        "id", "result", "invoice_line", "po_line", "match_status",
        "qty_within_tolerance", "price_within_tolerance",
        "amount_within_tolerance", "description_similarity",
    )
    list_filter = ("match_status", "qty_within_tolerance", "price_within_tolerance", "amount_within_tolerance")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")


@admin.register(ReconciliationException)
class ReconciliationExceptionAdmin(admin.ModelAdmin):
    list_display = ("id", "result", "type_badge", "severity_badge", "message_short", "resolved_flag", "created_at")
    list_filter = ("exception_type", "severity", "resolved", "applies_to_mode")
    search_fields = ("message",)
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Links", {"fields": ("result", "result_line")}),
        ("Exception", {"fields": ("exception_type", "severity", "message", "details", "applies_to_mode")}),
        ("Resolution", {"fields": ("resolved", "resolved_by", "resolved_at")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    actions = ["mark_resolved"]

    @admin.display(description="Type")
    def type_badge(self, obj):
        return format_html(
            '<span style="background:#17a2b8;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            obj.get_exception_type_display(),
        )

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        colours = {"LOW": "#198754", "MEDIUM": "#ffc107", "HIGH": "#fd7e14", "CRITICAL": "#dc3545"}
        c = colours.get(obj.severity, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            c, obj.get_severity_display(),
        )

    @admin.display(description="Message")
    def message_short(self, obj):
        return obj.message[:120]

    @admin.display(description="Resolved", boolean=True)
    def resolved_flag(self, obj):
        return obj.resolved

    @admin.action(description="Mark selected as resolved")
    def mark_resolved(self, request, queryset):
        from django.utils import timezone as tz
        queryset.filter(resolved=False).update(resolved=True, resolved_by=request.user, resolved_at=tz.now())


@admin.register(ReconciliationPolicy)
class ReconciliationPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "policy_code", "policy_name", "reconciliation_mode", "vendor",
        "is_service_invoice", "is_stock_invoice", "priority",
        "is_active", "effective_from", "effective_to",
    )
    list_filter = ("reconciliation_mode", "is_active", "is_service_invoice", "is_stock_invoice")
    search_fields = ("policy_code", "policy_name", "item_category", "business_unit")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("policy_code", "policy_name")}),
        ("Mode", {"fields": ("reconciliation_mode",)}),
        ("Matching Criteria", {"fields": (
            "vendor", "invoice_type", "item_category",
            "business_unit", "location_code",
            "is_service_invoice", "is_stock_invoice",
        )}),
        ("Priority & Validity", {"fields": (
            "priority", "is_active", "effective_from", "effective_to",
        )}),
        ("Notes", {"fields": ("notes",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )
