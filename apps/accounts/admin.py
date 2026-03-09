from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "first_name", "last_name", "role_badge", "department", "is_active", "is_staff", "created_at")
    list_filter = ("role", "is_active", "is_staff", "department")
    search_fields = ("email", "first_name", "last_name", "department")
    ordering = ("email",)
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "last_login")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "department")}),
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
