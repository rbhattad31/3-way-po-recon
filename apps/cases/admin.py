"""Admin registration for the cases app."""

from django.contrib import admin

from apps.cases.models import (
    APCase,
    APCaseActivity,
    APCaseArtifact,
    APCaseAssignment,
    APCaseComment,
    APCaseDecision,
    APCaseStage,
    APCaseSummary,
)


class APCaseStageInline(admin.TabularInline):
    model = APCaseStage
    extra = 0
    readonly_fields = ("stage_name", "stage_status", "started_at", "completed_at")


class APCaseDecisionInline(admin.TabularInline):
    model = APCaseDecision
    extra = 0
    readonly_fields = ("decision_type", "decision_source", "decision_value", "confidence")


class APCaseArtifactInline(admin.TabularInline):
    model = APCaseArtifact
    extra = 0
    readonly_fields = ("artifact_type", "linked_object_type", "linked_object_id", "version")


@admin.register(APCase)
class APCaseAdmin(admin.ModelAdmin):
    list_display = (
        "case_number", "invoice", "vendor", "processing_path",
        "status", "current_stage", "priority", "assigned_to", "created_at",
    )
    list_filter = ("processing_path", "status", "priority", "invoice_type")
    search_fields = ("case_number", "invoice__invoice_number", "vendor__name")
    readonly_fields = ("case_number", "created_at", "updated_at")
    inlines = [APCaseStageInline, APCaseDecisionInline, APCaseArtifactInline]


@admin.register(APCaseStage)
class APCaseStageAdmin(admin.ModelAdmin):
    list_display = ("case", "stage_name", "stage_status", "started_at", "completed_at")
    list_filter = ("stage_name", "stage_status")


@admin.register(APCaseArtifact)
class APCaseArtifactAdmin(admin.ModelAdmin):
    list_display = ("case", "artifact_type", "linked_object_type", "version", "created_at")
    list_filter = ("artifact_type",)


@admin.register(APCaseDecision)
class APCaseDecisionAdmin(admin.ModelAdmin):
    list_display = ("case", "decision_type", "decision_source", "decision_value", "confidence", "created_at")
    list_filter = ("decision_type", "decision_source")


@admin.register(APCaseAssignment)
class APCaseAssignmentAdmin(admin.ModelAdmin):
    list_display = ("case", "assignment_type", "assigned_user", "assigned_role", "status", "due_at")
    list_filter = ("assignment_type", "status")


@admin.register(APCaseSummary)
class APCaseSummaryAdmin(admin.ModelAdmin):
    list_display = ("case", "updated_at")


@admin.register(APCaseComment)
class APCaseCommentAdmin(admin.ModelAdmin):
    list_display = ("case", "author", "is_internal", "created_at")


@admin.register(APCaseActivity)
class APCaseActivityAdmin(admin.ModelAdmin):
    list_display = ("case", "activity_type", "actor", "created_at")
    list_filter = ("activity_type",)
