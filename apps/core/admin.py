from django.contrib import admin

from apps.core.models import PromptTemplate


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "category", "version", "is_active", "updated_at")
    list_filter = ("category", "is_active")
    search_fields = ("slug", "name", "content")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("slug", "name", "category", "description", "is_active", "version")}),
        ("Content", {"fields": ("content",), "classes": ("wide",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
