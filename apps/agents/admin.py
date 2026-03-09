from django.contrib import admin
from apps.agents.models import (
    AgentDefinition,
    AgentRun,
    AgentStep,
    AgentMessage,
    DecisionLog,
    AgentRecommendation,
    AgentEscalation,
)


class AgentStepInline(admin.TabularInline):
    model = AgentStep
    extra = 0
    readonly_fields = ("step_number", "action", "input_data", "output_data", "success", "duration_ms")


class AgentMessageInline(admin.TabularInline):
    model = AgentMessage
    extra = 0
    readonly_fields = ("role", "content", "token_count", "message_index")


class DecisionLogInline(admin.TabularInline):
    model = DecisionLog
    extra = 0
    readonly_fields = ("decision", "rationale", "confidence", "evidence_refs")


class RecommendationInline(admin.TabularInline):
    model = AgentRecommendation
    extra = 0
    readonly_fields = ("recommendation_type", "confidence", "reasoning", "accepted")


@admin.register(AgentDefinition)
class AgentDefinitionAdmin(admin.ModelAdmin):
    list_display = ("agent_type", "name", "enabled", "llm_model", "max_retries", "timeout_seconds")
    list_filter = ("enabled", "agent_type")
    search_fields = ("name", "description")


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = (
        "id", "agent_type", "status", "confidence",
        "llm_model_used", "total_tokens", "duration_ms", "created_at",
    )
    list_filter = ("agent_type", "status")
    search_fields = ("summarized_reasoning",)
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [AgentStepInline, AgentMessageInline, DecisionLogInline, RecommendationInline]


@admin.register(AgentRecommendation)
class AgentRecommendationAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_run", "recommendation_type", "confidence", "accepted", "created_at")
    list_filter = ("recommendation_type", "accepted")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AgentEscalation)
class AgentEscalationAdmin(admin.ModelAdmin):
    list_display = ("id", "agent_run", "severity", "resolved", "created_at")
    list_filter = ("severity", "resolved")
    readonly_fields = ("created_at", "updated_at")
