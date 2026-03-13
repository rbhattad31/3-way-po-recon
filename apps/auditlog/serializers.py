"""Governance API serializers — audit, trace, recommendations, timeline, RBAC."""
from rest_framework import serializers


# ---------------------------------------------------------------------------
# RBAC Context (embedded in audit events and timeline)
# ---------------------------------------------------------------------------
class RBACBadgeSerializer(serializers.Serializer):
    actor_email = serializers.CharField(required=False)
    actor_role = serializers.CharField(required=False)
    permission_checked = serializers.CharField(required=False)
    permission_source = serializers.CharField(required=False)
    access_granted = serializers.BooleanField(required=False)
    actor_roles = serializers.JSONField(required=False)


class StatusChangeSerializer(serializers.Serializer):
    before = serializers.CharField(allow_null=True, required=False)
    after = serializers.CharField(allow_null=True, required=False)


class FieldChangeSerializer(serializers.Serializer):
    field = serializers.CharField()
    old = serializers.CharField(allow_blank=True)
    new = serializers.CharField(allow_blank=True)


# ---------------------------------------------------------------------------
# Audit Events (enhanced with RBAC + trace)
# ---------------------------------------------------------------------------
class AuditEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.CharField()
    event_type = serializers.CharField()
    event_description = serializers.CharField()
    performed_by__email = serializers.CharField(allow_null=True)
    performed_by_agent = serializers.CharField()
    metadata_json = serializers.JSONField(allow_null=True)
    created_at = serializers.DateTimeField()
    # RBAC fields
    trace_id = serializers.CharField(required=False)
    actor_email = serializers.CharField(required=False)
    actor_primary_role = serializers.CharField(required=False)
    permission_checked = serializers.CharField(required=False)
    permission_source = serializers.CharField(required=False)
    access_granted = serializers.BooleanField(allow_null=True, required=False)
    # Status change
    status_before = serializers.CharField(required=False)
    status_after = serializers.CharField(required=False)
    duration_ms = serializers.IntegerField(required=False)


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
    # Policy/rule traceability
    decision_type = serializers.CharField(required=False)
    deterministic_flag = serializers.BooleanField(required=False)
    rule_name = serializers.CharField(required=False)
    policy_code = serializers.CharField(required=False)
    recommendation_type = serializers.CharField(required=False)
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
    trace_id = serializers.CharField(required=False)
    prompt_version = serializers.CharField(required=False)
    invocation_reason = serializers.CharField(required=False)
    total_tokens = serializers.IntegerField(required=False)
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
# Timeline (enhanced with RBAC + status_change + field_change + duration)
# ---------------------------------------------------------------------------
class TimelineEventSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    event_category = serializers.CharField()
    event_type = serializers.CharField()
    description = serializers.CharField()
    actor = serializers.CharField()
    metadata = serializers.JSONField(allow_null=True)
    # Enhanced fields
    trace_id = serializers.CharField(required=False)
    duration_ms = serializers.IntegerField(required=False, allow_null=True)
    rbac = RBACBadgeSerializer(required=False)
    status_change = StatusChangeSerializer(required=False)
    field_change = FieldChangeSerializer(required=False)


# ---------------------------------------------------------------------------
# Stage Timeline
# ---------------------------------------------------------------------------
class StageTimelineSerializer(serializers.Serializer):
    stage_name = serializers.CharField()
    stage_display = serializers.CharField()
    status = serializers.CharField()
    performed_by_type = serializers.CharField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    duration_ms = serializers.IntegerField(allow_null=True)
    retry_count = serializers.IntegerField()
    error_code = serializers.CharField(required=False, allow_blank=True)
    error_message = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    trace_id = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Access History & Permission Denials
# ---------------------------------------------------------------------------
class AccessHistorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    event_description = serializers.CharField()
    actor_email = serializers.CharField(allow_null=True)
    actor_primary_role = serializers.CharField(allow_null=True)
    permission_checked = serializers.CharField()
    permission_source = serializers.CharField(allow_null=True)
    access_granted = serializers.BooleanField(allow_null=True)
    created_at = serializers.DateTimeField()
    trace_id = serializers.CharField(allow_null=True)


class RBACActivitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    event_description = serializers.CharField()
    performed_by__email = serializers.CharField(allow_null=True)
    metadata_json = serializers.JSONField(allow_null=True)
    created_at = serializers.DateTimeField()


# ---------------------------------------------------------------------------
# Agent Performance Summary (aggregated view)
# ---------------------------------------------------------------------------
class AgentPerformanceSummarySerializer(serializers.Serializer):
    agent_type = serializers.CharField()
    total_runs = serializers.IntegerField()
    completed = serializers.IntegerField()
    failed = serializers.IntegerField()
    avg_confidence = serializers.FloatField(allow_null=True)
    avg_duration_ms = serializers.FloatField(allow_null=True)
    total_tokens = serializers.IntegerField()
    total_recommendations = serializers.IntegerField()
