"""Governance API serializers — audit, trace, recommendations, timeline."""
from rest_framework import serializers


class AuditEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.CharField()
    event_type = serializers.CharField()
    event_description = serializers.CharField()
    performed_by__email = serializers.CharField(allow_null=True)
    performed_by_agent = serializers.CharField()
    metadata_json = serializers.JSONField(allow_null=True)
    created_at = serializers.DateTimeField()


# ---------------------------------------------------------------------------
# Agent Trace
# ---------------------------------------------------------------------------
class TraceStepSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    step_number = serializers.IntegerField()
    action = serializers.CharField()
    input_data = serializers.JSONField(allow_null=True)
    output_data = serializers.JSONField(allow_null=True)
    success = serializers.BooleanField()
    duration_ms = serializers.IntegerField(allow_null=True)
    created_at = serializers.DateTimeField()


class TraceToolCallSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    tool_name = serializers.CharField()
    status = serializers.CharField()
    input_payload = serializers.JSONField(allow_null=True)
    output_payload = serializers.JSONField(allow_null=True)
    error_message = serializers.CharField()
    duration_ms = serializers.IntegerField(allow_null=True)
    created_at = serializers.DateTimeField()


class TraceDecisionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    decision = serializers.CharField()
    rationale = serializers.CharField()
    confidence = serializers.FloatField(allow_null=True)
    evidence_refs = serializers.JSONField(allow_null=True)
    created_at = serializers.DateTimeField()


class AgentRunTraceSerializer(serializers.Serializer):
    agent_run_id = serializers.IntegerField()
    agent_type = serializers.CharField()
    agent_name = serializers.CharField()
    status = serializers.CharField()
    confidence = serializers.FloatField(allow_null=True)
    summarized_reasoning = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    duration_ms = serializers.IntegerField(allow_null=True)
    steps = TraceStepSerializer(many=True)
    tool_calls = TraceToolCallSerializer(many=True)
    decisions = TraceDecisionSerializer(many=True)


class AgentTraceResponseSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField(required=False)
    reconciliation_result_id = serializers.IntegerField(required=False)
    reconciliation_traces = AgentRunTraceSerializer(many=True, required=False)
    agent_runs = AgentRunTraceSerializer(many=True, required=False)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
class RecommendationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    agent_run__agent_type = serializers.CharField()
    recommendation_type = serializers.CharField()
    confidence = serializers.FloatField(allow_null=True)
    reasoning = serializers.CharField()
    evidence = serializers.JSONField(allow_null=True)
    recommended_action = serializers.CharField()
    accepted = serializers.BooleanField(allow_null=True)
    accepted_by__email = serializers.CharField(allow_null=True)
    accepted_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
class TimelineEventSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    event_category = serializers.CharField()
    event_type = serializers.CharField()
    description = serializers.CharField()
    actor = serializers.CharField()
    metadata = serializers.JSONField(allow_null=True)
