from django.contrib import admin
from django.utils.html import format_html

from apps.vendors.models import Vendor
from apps.posting_core.models import VendorAliasMapping


class VendorAliasMappingInline(admin.TabularInline):
    model = VendorAliasMapping
    fk_name = "vendor"
    extra = 1
    fields = ("alias_text", "normalized_alias", "source", "confidence", "is_active", "created_at")
    readonly_fields = ("normalized_alias", "created_at")


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "country", "currency", "payment_terms", "alias_count", "active_badge", "created_at")
    list_filter = ("is_active", "country", "currency")
    search_fields = ("code", "name", "normalized_name", "tax_id", "contact_email")
    list_per_page = 25
    date_hierarchy = "created_at"
    inlines = [VendorAliasMappingInline]
    readonly_fields = ("normalized_name", "created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("code", "name", "normalized_name", "tax_id")}),
        ("Contact", {"fields": ("address", "country", "contact_email")}),
        ("Financial", {"fields": ("currency", "payment_terms")}),
        ("Status", {"fields": ("is_active",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )
    actions = ["activate_vendors", "deactivate_vendors"]

    @admin.display(description="Aliases")
    def alias_count(self, obj):
        return obj.alias_mappings.count()

    @admin.display(description="Active", boolean=True)
    def active_badge(self, obj):
        return obj.is_active

    @admin.action(description="Activate selected vendors")
    def activate_vendors(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Deactivate selected vendors")
    def deactivate_vendors(self, request, queryset):
        queryset.update(is_active=False)
