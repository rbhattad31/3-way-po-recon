from django.contrib import admin
from django.utils.html import format_html

from apps.integrations.models import IntegrationConfig, IntegrationLog


class IntegrationLogInline(admin.TabularInline):
    model = IntegrationLog
    extra = 0
    fields = ("direction", "status", "duration_ms", "error_message", "created_at")
    readonly_fields = fields
    show_change_link = True


@admin.register(IntegrationConfig)
class IntegrationConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "integration_type", "enabled_badge", "auth_method", "endpoint_url", "log_count", "created_at")
    list_filter = ("integration_type", "enabled", "auth_method")
    search_fields = ("name", "endpoint_url")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [IntegrationLogInline]
    fieldsets = (
        ("Identity", {"fields": ("name", "integration_type", "enabled")}),
        ("Connection", {"fields": ("endpoint_url", "auth_method")}),
        ("Config", {"fields": ("config_json",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Enabled", boolean=True)
    def enabled_badge(self, obj):
        return obj.enabled

    @admin.display(description="Logs")
    def log_count(self, obj):
        return obj.logs.count()


@admin.register(IntegrationLog)
class IntegrationLogAdmin(admin.ModelAdmin):
    list_display = ("id", "integration", "direction", "status_badge", "duration_display", "created_at")
    list_filter = ("direction", "status", "integration")
    search_fields = ("error_message",)
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Links", {"fields": ("integration",)}),
        ("Call", {"fields": ("direction", "status", "duration_ms")}),
        ("Payloads", {"fields": ("request_payload", "response_payload"), "classes": ("collapse",)}),
        ("Error", {"fields": ("error_message",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colour = "#198754" if obj.status == "SUCCESS" else "#dc3545"
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            colour, obj.status,
        )

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.duration_ms is None:
            return "-"
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms}ms"
        return f"{obj.duration_ms / 1000:.1f}s"
