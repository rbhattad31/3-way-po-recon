from django.contrib import admin
from django.utils.html import format_html

from apps.reports.models import GeneratedReport


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = (
        "id", "report_type", "title", "format_badge", "record_count",
        "generated_by", "success_badge", "created_at",
    )
    list_filter = ("report_type", "format", "success")
    search_fields = ("title", "report_type")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by", "celery_task_id")
    fieldsets = (
        ("Report", {"fields": ("report_type", "title", "format", "record_count")}),
        ("File", {"fields": ("file",)}),
        ("Parameters", {"fields": ("parameters",), "classes": ("collapse",)}),
        ("Status", {"fields": ("success", "error_message", "celery_task_id")}),
        ("Audit", {"fields": ("generated_by", "created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Format")
    def format_badge(self, obj):
        colour = "#198754" if obj.format == "xlsx" else "#0d6efd"
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            colour, obj.format.upper(),
        )

    @admin.display(description="OK", boolean=True)
    def success_badge(self, obj):
        return obj.success
