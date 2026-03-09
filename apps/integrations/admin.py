from django.contrib import admin
from apps.integrations.models import IntegrationConfig, IntegrationLog


@admin.register(IntegrationConfig)
class IntegrationConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "integration_type", "enabled", "endpoint_url", "created_at")
    list_filter = ("integration_type", "enabled")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(IntegrationLog)
class IntegrationLogAdmin(admin.ModelAdmin):
    list_display = ("id", "integration", "direction", "status", "duration_ms", "created_at")
    list_filter = ("direction", "status")
    readonly_fields = ("created_at", "updated_at")
