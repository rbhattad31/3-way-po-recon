"""Admin configuration for extraction_core models."""
from django.contrib import admin

from apps.extraction_core.models import (
    CountryPack,
    EntityExtractionProfile,
    ExtractionAnalyticsSnapshot,
    ExtractionApprovalRecord,
    ExtractionCorrection,
    ExtractionEvidence,
    ExtractionFieldValue,
    ExtractionIssue,
    ExtractionLineItem,
    ExtractionPromptTemplate,
    ExtractionRun,
    ExtractionRuntimeSettings,
    ExtractionSchemaDefinition,
    ReviewRoutingRule,
    TaxJurisdictionProfile,
)


@admin.register(TaxJurisdictionProfile)
class TaxJurisdictionProfileAdmin(admin.ModelAdmin):
    list_display = [
        "country_code",
        "country_name",
        "tax_regime",
        "default_currency",
        "tax_id_label",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "tax_regime", "country_code"]
    search_fields = ["country_name", "country_code", "tax_regime"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {
            "fields": (
                "country_code", "country_name", "tax_regime",
                "regime_full_name", "default_currency",
            ),
        }),
        ("Tax ID Validation", {
            "fields": ("tax_id_label", "tax_id_regex"),
        }),
        ("Locale & Fiscal", {
            "fields": ("locale_code", "date_formats", "fiscal_year_start_month"),
        }),
        ("Configuration", {
            "fields": ("config_json", "is_active"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ExtractionSchemaDefinition)
class ExtractionSchemaDefinitionAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "jurisdiction",
        "document_type",
        "schema_version",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "document_type", "jurisdiction__country_code"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["jurisdiction"]
    fieldsets = (
        (None, {
            "fields": (
                "jurisdiction", "document_type", "schema_version",
                "name", "description",
            ),
        }),
        ("Field Definitions", {
            "fields": (
                "header_fields_json", "line_item_fields_json", "tax_fields_json",
            ),
        }),
        ("Configuration", {
            "fields": ("config_json", "is_active"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ExtractionRuntimeSettings)
class ExtractionRuntimeSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "jurisdiction_mode",
        "default_country_code",
        "default_regime_code",
        "enable_jurisdiction_detection",
        "allow_manual_override",
        "is_active",
        "updated_at",
    ]
    list_filter = ["is_active", "jurisdiction_mode"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {
            "fields": (
                "name", "jurisdiction_mode",
                "default_country_code", "default_regime_code",
            ),
        }),
        ("Detection Settings", {
            "fields": (
                "enable_jurisdiction_detection",
                "allow_manual_override",
                "confidence_threshold_for_detection",
                "fallback_to_detection_on_schema_miss",
            ),
        }),
        ("Configuration", {
            "fields": ("config_json", "is_active"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


@admin.register(EntityExtractionProfile)
class EntityExtractionProfileAdmin(admin.ModelAdmin):
    list_display = [
        "entity",
        "default_country_code",
        "default_regime_code",
        "jurisdiction_mode",
        "is_active",
        "updated_at",
    ]
    list_filter = ["is_active", "jurisdiction_mode", "default_country_code"]
    search_fields = ["entity__name", "entity__code"]
    raw_id_fields = ["entity"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {
            "fields": (
                "entity", "jurisdiction_mode",
                "default_country_code", "default_regime_code",
                "default_document_language",
            ),
        }),
        ("Overrides", {
            "fields": (
                "schema_override_code",
                "validation_profile_override_code",
                "normalization_profile_override_code",
            ),
        }),
        ("Configuration", {
            "fields": ("config_json", "is_active"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


# ---------------------------------------------------------------------------
# New extraction pipeline models
# ---------------------------------------------------------------------------


@admin.register(ExtractionRun)
class ExtractionRunAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "document",
        "status",
        "country_code",
        "regime_code",
        "overall_confidence",
        "review_queue",
        "requires_review",
        "duration_ms",
        "created_at",
    ]
    list_filter = [
        "status",
        "country_code",
        "review_queue",
        "requires_review",
        "extraction_method",
    ]
    search_fields = ["country_code", "schema_code", "error_message"]
    raw_id_fields = ["document", "jurisdiction", "schema"]
    readonly_fields = ["created_at", "updated_at", "started_at", "completed_at"]
    date_hierarchy = "created_at"


@admin.register(ExtractionFieldValue)
class ExtractionFieldValueAdmin(admin.ModelAdmin):
    list_display = [
        "extraction_run",
        "field_code",
        "value",
        "confidence",
        "category",
        "is_corrected",
    ]
    list_filter = ["category", "is_corrected", "extraction_method"]
    search_fields = ["field_code", "value"]
    raw_id_fields = ["extraction_run"]


@admin.register(ExtractionLineItem)
class ExtractionLineItemAdmin(admin.ModelAdmin):
    list_display = ["extraction_run", "line_index", "confidence", "is_valid"]
    raw_id_fields = ["extraction_run"]


@admin.register(ExtractionEvidence)
class ExtractionEvidenceAdmin(admin.ModelAdmin):
    list_display = [
        "extraction_run",
        "field_code",
        "page_number",
        "extraction_method",
        "confidence",
    ]
    list_filter = ["extraction_method"]
    search_fields = ["field_code", "snippet"]
    raw_id_fields = ["extraction_run"]


@admin.register(ExtractionIssue)
class ExtractionIssueAdmin(admin.ModelAdmin):
    list_display = [
        "extraction_run",
        "severity",
        "field_code",
        "check_type",
        "message",
    ]
    list_filter = ["severity", "check_type"]
    search_fields = ["message", "field_code"]
    raw_id_fields = ["extraction_run"]


@admin.register(ExtractionApprovalRecord)
class ExtractionApprovalRecordAdmin(admin.ModelAdmin):
    list_display = [
        "extraction_run",
        "action",
        "approved_by",
        "decided_at",
    ]
    list_filter = ["action"]
    raw_id_fields = ["extraction_run", "approved_by"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ExtractionCorrection)
class ExtractionCorrectionAdmin(admin.ModelAdmin):
    list_display = [
        "extraction_run",
        "field_code",
        "original_value",
        "corrected_value",
        "corrected_by",
        "created_at",
    ]
    search_fields = ["field_code"]
    raw_id_fields = ["extraction_run", "corrected_by"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ExtractionAnalyticsSnapshot)
class ExtractionAnalyticsSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        "snapshot_type",
        "country_code",
        "regime_code",
        "period_start",
        "period_end",
        "run_count",
        "average_confidence",
        "created_at",
    ]
    list_filter = ["snapshot_type", "country_code"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(CountryPack)
class CountryPackAdmin(admin.ModelAdmin):
    list_display = [
        "jurisdiction",
        "pack_status",
        "schema_version",
        "validation_profile_version",
        "normalization_profile_version",
        "activated_at",
    ]
    list_filter = ["pack_status"]
    raw_id_fields = ["jurisdiction"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ExtractionPromptTemplate)
class ExtractionPromptTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "prompt_code",
        "prompt_category",
        "country_code",
        "regime_code",
        "document_type",
        "version",
        "status",
        "is_active",
        "updated_at",
    ]
    list_filter = ["status", "prompt_category", "country_code", "is_active"]
    search_fields = ["prompt_code", "prompt_text"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {
            "fields": (
                "prompt_code", "prompt_category", "version", "status",
            ),
        }),
        ("Scope", {
            "fields": (
                "country_code", "regime_code", "document_type", "schema_code",
            ),
        }),
        ("Content", {
            "fields": ("prompt_text", "variables_json"),
        }),
        ("Validity", {
            "fields": ("effective_from", "effective_to", "is_active"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ReviewRoutingRule)
class ReviewRoutingRuleAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "rule_code",
        "condition_type",
        "target_queue",
        "priority",
        "is_active",
        "updated_at",
    ]
    list_filter = ["is_active", "condition_type", "target_queue"]
    search_fields = ["name", "rule_code", "description"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {
            "fields": (
                "name", "rule_code", "condition_type",
                "target_queue", "priority",
            ),
        }),
        ("Configuration", {
            "fields": ("condition_config_json", "description", "is_active"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )
