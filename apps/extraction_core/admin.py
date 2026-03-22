"""Admin configuration for extraction_core models."""
from django.contrib import admin

from apps.extraction_core.models import (
    EntityExtractionProfile,
    ExtractionRuntimeSettings,
    ExtractionSchemaDefinition,
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
