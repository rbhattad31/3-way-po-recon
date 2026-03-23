from django import forms
from django.contrib import admin
from django.utils.html import format_html

from apps.extraction.models import ExtractionApproval, ExtractionFieldCorrection, ExtractionResult
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount


class UserCreditAccountForm(forms.ModelForm):
    """Admin form that enforces accounting invariants on manual edits."""
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Required when changing credit balances.",
    )

    class Meta:
        model = UserCreditAccount
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        balance = cleaned.get("balance_credits", 0)
        reserved = cleaned.get("reserved_credits", 0)
        if balance is not None and reserved is not None and balance < reserved:
            raise forms.ValidationError(
                f"Balance ({balance}) must be >= reserved ({reserved}) to maintain accounting invariants."
            )
        # Require remarks when balance or reserved fields are being changed
        if self.instance.pk:
            changed_credit_fields = {"balance_credits", "reserved_credits", "monthly_limit", "monthly_used"} & set(self.changed_data)
            if changed_credit_fields and not cleaned.get("remarks", "").strip():
                raise forms.ValidationError(
                    "Remarks are required when manually adjusting credit fields."
                )
        return cleaned


@admin.register(ExtractionResult)
class ExtractionResultAdmin(admin.ModelAdmin):
    list_display = (
        "id", "document_upload", "invoice", "extraction_run",
        "engine_name", "engine_version",
        "confidence_display", "success_badge", "duration_display", "created_at",
    )
    list_filter = ("success", "engine_name", "engine_version")
    search_fields = ("document_upload__original_filename", "error_message")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Links", {
            "fields": ("document_upload", "invoice", "extraction_run"),
            "description": "extraction_run links to the authoritative ExtractionRun record "
                           "(source of truth). This is a UI-facing summary — not the execution record.",
        }),
        ("Engine", {"fields": ("engine_name", "engine_version")}),
        ("Result", {"fields": ("success", "confidence", "duration_ms", "error_message")}),
        ("Raw Data", {"fields": ("raw_response",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.confidence is None:
            return "-"
        pct = obj.confidence * 100
        colour = "#198754" if pct >= 75 else ("#ffc107" if pct >= 50 else "#dc3545")
        return format_html('<span style="color:{}">{:.0f}%</span>', colour, pct)

    @admin.display(description="OK", boolean=True)
    def success_badge(self, obj):
        return obj.success

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.duration_ms is None:
            return "-"
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms}ms"
        return f"{obj.duration_ms / 1000:.1f}s"


class ExtractionFieldCorrectionInline(admin.TabularInline):
    model = ExtractionFieldCorrection
    extra = 0
    readonly_fields = (
        "entity_type", "entity_id", "field_name",
        "original_value", "corrected_value", "corrected_by", "created_at",
    )


@admin.register(ExtractionApproval)
class ExtractionApprovalAdmin(admin.ModelAdmin):
    list_display = (
        "id", "invoice", "status_badge", "confidence_display",
        "fields_corrected_count", "is_touchless", "reviewed_by", "reviewed_at", "created_at",
    )
    list_filter = ("status", "is_touchless")
    search_fields = ("invoice__invoice_number", "invoice__raw_vendor_name")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [ExtractionFieldCorrectionInline]
    fieldsets = (
        ("Links", {"fields": ("invoice", "extraction_result")}),
        ("Decision", {"fields": ("status", "reviewed_by", "reviewed_at", "rejection_reason")}),
        ("Metrics", {"fields": ("confidence_at_review", "fields_corrected_count", "is_touchless")}),
        ("Snapshot", {"fields": ("original_values_snapshot",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "PENDING": "#ffc107",
            "APPROVED": "#198754",
            "AUTO_APPROVED": "#0dcaf0",
            "REJECTED": "#dc3545",
        }
        colour = colours.get(obj.status, "#6c757d")
        return format_html('<span style="color:{}">{}</span>', colour, obj.get_status_display())

    @admin.display(description="Confidence")
    def confidence_display(self, obj):
        if obj.confidence_at_review is None:
            return "-"
        pct = obj.confidence_at_review * 100
        colour = "#198754" if pct >= 75 else ("#ffc107" if pct >= 50 else "#dc3545")
        return format_html('<span style="color:{}">{:.0f}%</span>', colour, pct)


# ---------------------------------------------------------------------------
# Credit Management Admin
# ---------------------------------------------------------------------------

class CreditTransactionInline(admin.TabularInline):
    model = CreditTransaction
    extra = 0
    readonly_fields = (
        "transaction_type", "credits", "balance_after", "reserved_after",
        "monthly_used_after", "reference_type", "reference_id", "remarks",
        "created_by", "created_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UserCreditAccount)
class UserCreditAccountAdmin(admin.ModelAdmin):
    form = UserCreditAccountForm
    list_display = (
        "id", "user", "balance_credits", "reserved_credits",
        "available_display", "monthly_limit", "monthly_used",
        "is_active", "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("user__email", "user__first_name", "user__last_name")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 25
    inlines = [CreditTransactionInline]
    fieldsets = (
        ("Account", {"fields": ("user", "is_active")}),
        ("Credits", {"fields": ("balance_credits", "reserved_credits", "monthly_limit", "monthly_used")}),
        ("Adjustment Note", {"fields": ("remarks",), "description": "Required when changing credit fields."}),
        ("Timestamps", {"fields": ("last_reset_at", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Available")
    def available_display(self, obj):
        val = obj.available_credits
        colour = "#dc3545" if val <= 5 else ("#ffc107" if val <= 20 else "#198754")
        return format_html('<span style="color:{};font-weight:600">{}</span>', colour, val)


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "account", "transaction_type", "credits",
        "balance_after", "reserved_after", "monthly_used_after",
        "reference_type", "created_by", "created_at",
    )
    list_filter = ("transaction_type", "reference_type")
    search_fields = (
        "account__user__email", "reference_id", "remarks",
    )
    readonly_fields = (
        "account", "transaction_type", "credits", "balance_after",
        "reserved_after", "monthly_used_after", "reference_type",
        "reference_id", "remarks", "created_by", "created_at",
    )
    list_per_page = 50
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
