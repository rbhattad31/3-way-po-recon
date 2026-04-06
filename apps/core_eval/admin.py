from django.contrib import admin

from .models import (
    EvalFieldOutcome,
    EvalMetric,
    EvalRun,
    LearningAction,
    LearningSignal,
)


@admin.register(EvalRun)
class EvalRunAdmin(admin.ModelAdmin):
    list_display = ("id", "app_module", "entity_type", "entity_id", "run_key", "status", "tenant_id", "created_at")
    list_filter = ("app_module", "status", "tenant_id", "created_at")
    search_fields = ("app_module", "entity_type", "entity_id", "run_key", "prompt_hash", "tenant_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EvalMetric)
class EvalMetricAdmin(admin.ModelAdmin):
    list_display = ("id", "eval_run", "metric_name", "metric_value", "string_value", "tenant_id", "created_at")
    list_filter = ("metric_name", "tenant_id", "created_at")
    search_fields = ("metric_name", "tenant_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EvalFieldOutcome)
class EvalFieldOutcomeAdmin(admin.ModelAdmin):
    list_display = ("id", "eval_run", "field_name", "status", "tenant_id", "created_at")
    list_filter = ("status", "tenant_id", "created_at")
    search_fields = ("field_name", "tenant_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LearningSignal)
class LearningSignalAdmin(admin.ModelAdmin):
    list_display = (
        "id", "app_module", "signal_type", "entity_type", "entity_id",
        "aggregation_key", "confidence", "tenant_id", "created_at",
    )
    list_filter = ("app_module", "signal_type", "tenant_id", "created_at")
    search_fields = ("app_module", "signal_type", "entity_type", "entity_id", "aggregation_key", "tenant_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LearningAction)
class LearningActionAdmin(admin.ModelAdmin):
    list_display = ("id", "action_type", "status", "app_module", "tenant_id", "created_at")
    list_filter = ("action_type", "status", "tenant_id", "created_at")
    search_fields = ("action_type", "app_module", "tenant_id")
    readonly_fields = ("created_at", "updated_at")
