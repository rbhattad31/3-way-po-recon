"""Governance API views — invoice-centric audit, trace, recommendations, timeline, RBAC."""
from django.db.models import Avg, Count, Q, Sum
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.agents.models import AgentRecommendation, AgentRun
from apps.agents.services.agent_trace_service import AgentTraceService
from apps.agents.services.recommendation_service import RecommendationService
from apps.auditlog.serializers import (
    AccessHistorySerializer,
    AgentPerformanceSummarySerializer,
    AuditEventSerializer,
    AgentTraceResponseSerializer,
    RBACActivitySerializer,
    RecommendationSerializer,
    StageTimelineSerializer,
    TimelineEventSerializer,
)
from apps.auditlog.services import AuditService
from apps.auditlog.timeline_service import CaseTimelineService
from apps.core.enums import UserRole
from rest_framework.permissions import IsAuthenticated


def _is_governance_viewer(user):
    """Check if user has governance.view permission or is ADMIN/AUDITOR."""
    if hasattr(user, "has_permission") and user.has_permission("governance.view"):
        return True
    role = getattr(user, "role", None)
    return role in (UserRole.ADMIN, UserRole.AUDITOR)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def invoice_audit_history(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/audit-history — full audit trail."""
    events = AuditService.fetch_invoice_history(invoice_id)
    serializer = AuditEventSerializer(events, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def invoice_agent_trace(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/agent-trace — agent runs, steps, tools, decisions."""
    trace = AgentTraceService.get_trace_for_invoice(invoice_id)
    serializer = AgentTraceResponseSerializer(trace)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def invoice_recommendations(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/recommendations — agent recommendations."""
    recs = RecommendationService.get_recommendations_for_invoice(invoice_id)
    serializer = RecommendationSerializer(recs, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def invoice_timeline(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/timeline — combined decision timeline."""
    timeline = CaseTimelineService.get_case_timeline(invoice_id)
    serializer = TimelineEventSerializer(timeline, many=True)
    return Response(serializer.data)


# ---------------------------------------------------------------------------
# New governance endpoints
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def invoice_access_history(request, invoice_id: int):
    """GET /api/v1/governance/invoices/{id}/access-history — permission-checked events."""
    events = AuditService.fetch_access_history(entity_type="Invoice", entity_id=invoice_id)
    serializer = AccessHistorySerializer(events, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def case_stage_timeline(request, case_id: int):
    """GET /api/v1/governance/cases/{id}/stage-timeline — stage execution timeline."""
    stages = CaseTimelineService.get_stage_timeline(case_id)
    serializer = StageTimelineSerializer(stages, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def permission_denials(request):
    """GET /api/v1/governance/permission-denials — recent access denied events."""
    if not _is_governance_viewer(request.user):
        return Response({"detail": "Permission denied."}, status=403)
    limit = min(int(request.query_params.get("limit", 50)), 200)
    events = AuditService.fetch_permission_denials(limit=limit)
    serializer = AccessHistorySerializer(events, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def rbac_activity(request):
    """GET /api/v1/governance/rbac-activity — role/permission change events."""
    if not _is_governance_viewer(request.user):
        return Response({"detail": "Permission denied."}, status=403)
    limit = min(int(request.query_params.get("limit", 50)), 200)
    events = AuditService.fetch_rbac_activity(limit=limit)
    serializer = RBACActivitySerializer(events, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def agent_performance_summary(request):
    """GET /api/v1/governance/agent-performance — aggregate agent stats."""
    if not _is_governance_viewer(request.user):
        return Response({"detail": "Permission denied."}, status=403)
    """GET /api/v1/governance/agent-performance — aggregate agent stats."""
    from django.db.models.functions import Coalesce

    runs = (
        AgentRun.objects
        .values("agent_type")
        .annotate(
            total_runs=Count("id"),
            completed=Count("id", filter=Q(status="COMPLETED")),
            failed=Count("id", filter=Q(status="FAILED")),
            avg_confidence=Avg("confidence"),
            avg_duration_ms=Avg("duration_ms"),
            total_tokens=Coalesce(Sum("total_tokens"), 0),
        )
        .order_by("agent_type")
    )

    # Add recommendation counts
    result = []
    for row in runs:
        rec_count = AgentRecommendation.objects.filter(
            agent_run__agent_type=row["agent_type"],
        ).count()
        row["total_recommendations"] = rec_count
        result.append(row)

    serializer = AgentPerformanceSummarySerializer(result, many=True)
    return Response(serializer.data)
