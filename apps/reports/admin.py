from django.contrib import admin
from apps.reports.models import GeneratedReport


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = ("id", "report_type", "title", "format", "record_count", "generated_by", "success", "created_at")
    list_filter = ("report_type", "format", "success")
    search_fields = ("title",)
    readonly_fields = ("created_at", "updated_at")
