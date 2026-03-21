"""Admin configuration for extraction_configs models."""
from django.contrib import admin

from apps.extraction_configs.models import NormalizationProfile, TaxFieldDefinition


@admin.register(TaxFieldDefinition)
class TaxFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = [
        "field_key",
        "display_name",
        "data_type",
        "category",
        "is_mandatory",
        "is_tax_field",
        "is_active",
        "sort_order",
    ]
    list_filter = ["is_active", "data_type", "category", "is_mandatory", "is_tax_field"]
    search_fields = ["field_key", "display_name", "description"]
    readonly_fields = ["created_at", "updated_at"]
    filter_horizontal = ["schemas"]
    fieldsets = (
        (None, {
            "fields": (
                "field_key", "display_name", "description",
                "data_type", "category", "sort_order",
            ),
        }),
        ("Flags", {
            "fields": ("is_mandatory", "is_tax_field", "is_active"),
        }),
        ("Validation & Normalization", {
            "fields": (
                "validation_regex", "validation_rules_json",
                "normalization_rules_json",
            ),
        }),
        ("Aliases & Schemas", {
            "fields": ("aliases", "schemas"),
        }),
        ("Configuration", {
            "fields": ("config_json",),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )


@admin.register(NormalizationProfile)
class NormalizationProfileAdmin(admin.ModelAdmin):
    list_display = [
        "jurisdiction",
        "date_output_format",
        "decimal_separator",
        "thousands_separator",
        "currency_symbol",
        "is_active",
    ]
    list_filter = ["is_active"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["jurisdiction"]
