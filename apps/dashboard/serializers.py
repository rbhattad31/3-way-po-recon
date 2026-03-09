"""Dashboard API serializers — analytics aggregations."""
from rest_framework import serializers


class DashboardSummarySerializer(serializers.Serializer):
    total_invoices = serializers.IntegerField()
    total_pos = serializers.IntegerField()
    total_grns = serializers.IntegerField()
    total_vendors = serializers.IntegerField()
    pending_reviews = serializers.IntegerField()
    open_exceptions = serializers.IntegerField()
    matched_pct = serializers.FloatField()
    avg_confidence = serializers.FloatField()


class MatchStatusBreakdownSerializer(serializers.Serializer):
    match_status = serializers.CharField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class ExceptionBreakdownSerializer(serializers.Serializer):
    exception_type = serializers.CharField()
    count = serializers.IntegerField()


class AgentPerformanceSerializer(serializers.Serializer):
    agent_type = serializers.CharField()
    total_runs = serializers.IntegerField()
    success_count = serializers.IntegerField()
    avg_confidence = serializers.FloatField()
    avg_duration_ms = serializers.FloatField()
    total_tokens = serializers.IntegerField()


class DailyVolumeSerializer(serializers.Serializer):
    date = serializers.DateField()
    invoices = serializers.IntegerField()
    reconciled = serializers.IntegerField()
    exceptions = serializers.IntegerField()


class RecentActivitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    entity_type = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    timestamp = serializers.DateTimeField()
