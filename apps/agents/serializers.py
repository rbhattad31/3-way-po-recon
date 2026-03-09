"""Agent & tool API serializers."""
from rest_framework import serializers

from apps.agents.models import (
    AgentDefinition,
    AgentEscalation,
    AgentRecommendation,
    AgentRun,
    AgentStep,
    DecisionLog,
)
from apps.tools.models import ToolCall, ToolDefinition


# ---------------------------------------------------------------------------
# Agent Definition
# ---------------------------------------------------------------------------
class AgentDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentDefinition
        fields = [
            "id", "agent_type", "name", "description", "enabled",
            "llm_model", "max_retries", "timeout_seconds", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ---------------------------------------------------------------------------
# Agent Run
# ---------------------------------------------------------------------------
class AgentRunListSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(
        source="agent_definition.name", read_only=True, default=""
    )
    invoice_number = serializers.CharField(
        source="reconciliation_result.invoice.invoice_number", read_only=True, default=""
    )

    class Meta:
        model = AgentRun
        fields = [
            "id", "agent_type", "agent_name", "status",
            "confidence", "invoice_number",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "duration_ms", "started_at", "completed_at", "created_at",
        ]


class AgentStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentStep
        fields = [
            "id", "step_number", "action", "input_data",
            "output_data", "success", "duration_ms", "created_at",
        ]


class ToolCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolCall
        fields = [
            "id", "tool_name", "status", "input_payload",
            "output_payload", "error_message", "duration_ms", "created_at",
        ]


class DecisionLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DecisionLog
        fields = [
            "id", "decision", "rationale", "confidence",
            "evidence_refs", "created_at",
        ]


class AgentRecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentRecommendation
        fields = [
            "id", "recommendation_type", "confidence", "reasoning",
            "evidence", "accepted", "created_at",
        ]


class AgentEscalationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentEscalation
        fields = [
            "id", "severity", "reason", "suggested_assignee_role",
            "resolved", "created_at",
        ]


class AgentRunDetailSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(
        source="agent_definition.name", read_only=True, default=""
    )
    steps = AgentStepSerializer(many=True, read_only=True)
    tool_calls = ToolCallSerializer(many=True, read_only=True)
    decisions = DecisionLogSerializer(many=True, read_only=True)
    recommendations = AgentRecommendationSerializer(many=True, read_only=True)
    escalations = AgentEscalationSerializer(many=True, read_only=True)

    class Meta:
        model = AgentRun
        fields = [
            "id", "agent_type", "agent_name", "status",
            "input_payload", "output_payload", "summarized_reasoning",
            "confidence", "llm_model_used",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "duration_ms", "error_message",
            "started_at", "completed_at",
            "steps", "tool_calls", "decisions",
            "recommendations", "escalations",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# Tool Definition
# ---------------------------------------------------------------------------
class ToolDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolDefinition
        fields = [
            "id", "name", "description", "input_schema",
            "output_schema", "enabled", "created_at",
        ]
        read_only_fields = ["id", "created_at"]
