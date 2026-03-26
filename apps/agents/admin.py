from django.contrib import admin
from django.utils.html import format_html

from apps.agents.models import (
    AgentDefinition,
    AgentRun,
    AgentStep,
    AgentMessage,
    DecisionLog,
    AgentRecommendation,
    AgentEscalation,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class AgentStepInline(admin.TabularInline):
    model = AgentStep
    extra = 0
    fields = ("step_number", "action", "success", "duration_ms")
    readonly_fields = fields
    show_change_link = True


class AgentMessageInline(admin.TabularInline):
    model = AgentMessage
    extra = 0
    fields = ("message_index", "role", "content_short", "token_count")
    readonly_fields = fields

    @admin.display(description="Content")
    def content_short(self, obj):
        return obj.content[:200]


class DecisionLogInline(admin.TabularInline):
    model = DecisionLog
    extra = 0
    fields = ("decision", "confidence", "rationale")
    readonly_fields = fields


class RecommendationInline(admin.TabularInline):
    model = AgentRecommendation
    extra = 0
    fields = ("recommendation_type", "confidence", "reasoning", "accepted")
    readonly_fields = fields


class EscalationInline(admin.TabularInline):
    model = AgentEscalation
    extra = 0
    fields = ("severity", "reason", "suggested_assignee_role", "resolved")
    readonly_fields = fields


# ---------------------------------------------------------------------------
# Admin classes
# ---------------------------------------------------------------------------
@admin.register(AgentDefinition)
class AgentDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "agent_type", "name", "enabled_badge", "lifecycle_status",
        "requires_tool_grounding", "capability_tags", "allowed_recommendation_types",
        "default_fallback_recommendation", "llm_model", "max_retries", "timeout_seconds", "run_count",
    )
    list_filter = ("enabled", "agent_type", "lifecycle_status", "requires_tool_grounding")
    search_fields = ("name", "description", "owner_team")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Identity", {"fields": ("agent_type", "name", "description", "enabled")}),
        ("LLM", {"fields": ("llm_model", "system_prompt")}),
        ("Limits", {"fields": ("max_retries", "timeout_seconds")}),
        ("Config", {"fields": ("config_json",), "classes": ("collapse",)}),
        ("Contract", {"fields": (
            "purpose", "entry_conditions", "success_criteria",
            "prohibited_actions", "human_review_required_conditions",
        )}),
        ("Tool Grounding", {"fields": (
            "requires_tool_grounding", "min_tool_calls", "tool_failure_confidence_cap",
        )}),
        ("Recommendation Contract", {"fields": (
            "allowed_recommendation_types", "default_fallback_recommendation",
            "output_schema_name", "output_schema_version",
        )}),
        ("Governance", {"fields": (
            "lifecycle_status", "owner_team", "capability_tags", "domain_tags",
        )}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Enabled", boolean=True)
    def enabled_badge(self, obj):
        return obj.enabled

    @admin.display(description="Runs")
    def run_count(self, obj):
        return obj.runs.count()


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = (
        "id", "agent_type_display", "status_badge", "reconciliation_result",
        "confidence_display", "llm_model_used", "token_display", "duration_display", "created_at",
    )
    list_filter = ("agent_type", "status", "llm_model_used")
    search_fields = ("summarized_reasoning", "error_message")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = (
        "created_at", "updated_at", "created_by", "updated_by",
        "started_at", "completed_at",
    )
    inlines = [AgentStepInline, AgentMessageInline, DecisionLogInline, RecommendationInline, EscalationInline]
    fieldsets = (
        ("Agent", {"fields": ("agent_definition", "agent_type", "reconciliation_result")}),
        ("Status", {"fields": ("status", "confidence", "error_message")}),
        ("Timing", {"fields": ("started_at", "completed_at", "duration_ms")}),
        ("LLM Usage", {"fields": ("llm_model_used", "prompt_tokens", "completion_tokens", "total_tokens")}),
        ("Payloads", {"fields": ("input_payload", "output_payload"), "classes": ("collapse",)}),
        ("Reasoning", {"fields": ("summarized_reasoning",)}),
        ("Handoff", {"fields": ("handed_off_to",), "classes": ("collapse",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Agent")
    def agent_type_display(self, obj):
        return obj.get_agent_type_display()

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "PENDING": "#6c757d", "RUNNING": "#0d6efd",
            "COMPLETED": "#198754", "FAILED": "#dc3545", "SKIPPED": "#adb5bd",
        }
        c = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_status_display(),
        )

    @admin.display(description="Conf")
    def confidence_display(self, obj):
        if obj.confidence is None:
            return "-"
        return f"{obj.confidence:.2f}"

    @admin.display(description="Tokens")
    def token_display(self, obj):
        if obj.total_tokens:
            return f"{obj.total_tokens:,}"
        return "-"

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.duration_ms is None:
            return "-"
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms}ms"
        return f"{obj.duration_ms / 1000:.1f}s"


@admin.register(AgentStep)
class AgentStepAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_run", "step_number", "action", "success", "duration_ms", "created_at")
    list_filter = ("success",)
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentMessage)
class AgentMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_run", "message_index", "role", "content_short", "token_count")
    list_filter = ("role",)
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Content")
    def content_short(self, obj):
        return obj.content[:150]


@admin.register(DecisionLog)
class DecisionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_run", "decision_short", "confidence", "created_at")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at", "agent_run", "decision", "rationale", "confidence", "evidence_refs")

    @admin.display(description="Decision")
    def decision_short(self, obj):
        return obj.decision[:100]

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AgentRecommendation)
class AgentRecommendationAdmin(admin.ModelAdmin):
    list_display = (
        "id", "agent_run", "recommendation_type", "confidence",
        "recommended_action", "accepted_display", "accepted_by", "accepted_at", "created_at",
    )
    list_filter = ("recommendation_type", "accepted")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at", "agent_run", "reconciliation_result", "invoice",
                       "recommendation_type", "confidence", "reasoning", "evidence", "recommended_action")
    fieldsets = (
        ("Links", {"fields": ("agent_run", "reconciliation_result", "invoice")}),
        ("Recommendation", {"fields": ("recommendation_type", "confidence", "reasoning", "evidence", "recommended_action")}),
        ("Acceptance", {"fields": ("accepted", "accepted_by", "accepted_at")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Accepted")
    def accepted_display(self, obj):
        if obj.accepted is None:
            return format_html('<span style="color:#6c757d;">Pending</span>')
        if obj.accepted:
            return format_html('<span style="color:#198754;font-weight:bold;">Yes</span>')
        return format_html('<span style="color:#dc3545;font-weight:bold;">No</span>')


@admin.register(AgentEscalation)
class AgentEscalationAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_run", "severity_badge", "reason_short", "suggested_assignee_role", "resolved", "created_at")
    list_filter = ("severity", "resolved", "suggested_assignee_role")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")
    actions = ["mark_resolved"]

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        colours = {"LOW": "#198754", "MEDIUM": "#ffc107", "HIGH": "#fd7e14", "CRITICAL": "#dc3545"}
        c = colours.get(obj.severity, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;">{}</span>',
            c, obj.get_severity_display(),
        )

    @admin.display(description="Reason")
    def reason_short(self, obj):
        return obj.reason[:120]

    @admin.action(description="Mark selected as resolved")
    def mark_resolved(self, request, queryset):
        queryset.filter(resolved=False).update(resolved=True)
