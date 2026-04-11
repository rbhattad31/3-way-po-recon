from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html

from apps.accounts.models import User
from apps.accounts.models import CompanyProfile, CompanyAlias, CompanyTaxID
from apps.accounts.rbac_models import (
    Role, Permission, RolePermission, UserRole, UserPermissionOverride, MenuConfig,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "first_name", "last_name", "role_badge", "company", "department", "is_active", "is_staff", "created_at")
    list_filter = ("role", "is_active", "is_staff", "company", "department")
    search_fields = ("email", "first_name", "last_name", "department")
    ordering = ("email",)
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "department", "company")}),
        ("Role", {"fields": ("role",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Timestamps", {"fields": ("last_login", "created_at", "updated_at"), "classes": ("collapse",)}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "role", "department", "password1", "password2")}),
    )

    @admin.display(description="Role")
    def role_badge(self, obj):
        colours = {
            "ADMIN": "#dc3545",
            "AP_PROCESSOR": "#0d6efd",
            "REVIEWER": "#198754",
            "FINANCE_MANAGER": "#6f42c1",
            "AUDITOR": "#fd7e14",
        }
        colour = colours.get(obj.role, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            colour, obj.get_role_display(),
        )


# ---------------------------------------------------------------------------
# RBAC Admin registrations
# ---------------------------------------------------------------------------

class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 1
    autocomplete_fields = ["permission"]


class UserRoleInline(admin.TabularInline):
    model = UserRole
    extra = 1
    autocomplete_fields = ["role", "assigned_by"]


class UserPermissionOverrideInline(admin.TabularInline):
    model = UserPermissionOverride
    extra = 0
    autocomplete_fields = ["permission", "assigned_by"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_system_role", "is_active", "rank", "user_count")
    list_filter = ("is_system_role", "is_active")
    search_fields = ("code", "name")
    ordering = ("rank", "code")
    inlines = [RolePermissionInline]

    @admin.display(description="Users")
    def user_count(self, obj):
        return obj.user_roles.filter(is_active=True).count()


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "module", "action", "is_active")
    list_filter = ("module", "is_active")
    search_fields = ("code", "name", "module")
    ordering = ("module", "action")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission", "is_allowed")
    list_filter = ("role", "is_allowed")
    autocomplete_fields = ["role", "permission"]


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "is_primary", "is_active", "assigned_at", "expires_at")
    list_filter = ("is_primary", "is_active", "role")
    autocomplete_fields = ["user", "role", "assigned_by"]
    date_hierarchy = "assigned_at"


@admin.register(UserPermissionOverride)
class UserPermissionOverrideAdmin(admin.ModelAdmin):
    list_display = ("user", "permission", "override_type", "is_active", "assigned_at", "expires_at")
    list_filter = ("override_type", "is_active")
    autocomplete_fields = ["user", "permission", "assigned_by"]


@admin.register(MenuConfig)
class MenuConfigAdmin(admin.ModelAdmin):
    list_display = ("label", "url_name", "required_permission", "order", "is_active", "is_separator")
    list_filter = ("is_active", "is_separator")
    ordering = ("order",)


# ---------------------------------------------------------------------------
# Company Profile
# ---------------------------------------------------------------------------

class CompanyAliasInline(admin.TabularInline):
    model = CompanyAlias
    extra = 1


class CompanyTaxIDInline(admin.TabularInline):
    model = CompanyTaxID
    extra = 1


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "tax_id", "country", "is_default", "is_active", "user_count")
    list_filter = ("is_default", "is_active", "country")
    search_fields = ("name", "legal_name", "tax_id")
    inlines = [CompanyAliasInline, CompanyTaxIDInline]

    @admin.display(description="Users")
    def user_count(self, obj):
        return obj.users.count()
