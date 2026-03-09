from django.contrib import admin
from apps.vendors.models import Vendor, VendorAlias


class VendorAliasInline(admin.TabularInline):
    model = VendorAlias
    extra = 1
    fields = ("alias_name", "normalized_alias", "source")
    readonly_fields = ("normalized_alias",)


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "country", "currency", "is_active", "created_at")
    list_filter = ("is_active", "country", "currency")
    search_fields = ("code", "name", "normalized_name", "tax_id")
    inlines = [VendorAliasInline]
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")


@admin.register(VendorAlias)
class VendorAliasAdmin(admin.ModelAdmin):
    list_display = ("alias_name", "vendor", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("alias_name", "normalized_alias", "vendor__name")
    readonly_fields = ("created_at", "updated_at")
