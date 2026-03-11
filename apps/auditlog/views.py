"""Governance API views — invoice-centric audit, trace, recommendations, timeline."""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.agents.services.agent_trace_service import AgentTraceService
from apps.agents.services.recommendation_service import RecommendationService
from apps.auditlog.serializers import (
    AuditEventSerializer,
    AgentTraceResponseSerializer,
    RecommendationSerializer,
    TimelineEventSerializer,
)
from apps.auditlog.services import AuditService
from apps.auditlog.timeline_service import CaseTimelineService
from apps.core.permissions import IsAuditor, IsReviewer


@api_view(["GET"])
@permission_classes([IsAuditor])
def invoice_audit_history(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/audit-history — full audit trail."""
    events = AuditService.fetch_invoice_history(invoice_id)
    serializer = AuditEventSerializer(events, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuditor])
def invoice_agent_trace(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/agent-trace — agent runs, steps, tools, decisions."""
    trace = AgentTraceService.get_trace_for_invoice(invoice_id)
    serializer = AgentTraceResponseSerializer(trace)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsReviewer])
def invoice_recommendations(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/recommendations — agent recommendations."""
    recs = RecommendationService.get_recommendations_for_invoice(invoice_id)
    serializer = RecommendationSerializer(recs, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsReviewer])
def invoice_timeline(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/timeline — combined decision timeline."""
    timeline = CaseTimelineService.get_case_timeline(invoice_id)
    serializer = TimelineEventSerializer(timeline, many=True)
    return Response(serializer.data)
