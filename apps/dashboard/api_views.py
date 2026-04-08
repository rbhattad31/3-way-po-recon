"""Dashboard API views — JSON endpoints for charts & summary cards."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasAnyRole
from apps.dashboard.serializers import (
    AgentPerformanceSerializer,
    DailyVolumeSerializer,
    DashboardSummarySerializer,
    ExceptionBreakdownSerializer,
    MatchStatusBreakdownSerializer,
    ModeBreakdownSerializer,
    RecentActivitySerializer,
)
from apps.dashboard.services import AgentPerformanceDashboardService, DashboardService


class DashboardSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_summary(user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(DashboardSummarySerializer(data).data)


class MatchStatusBreakdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_match_status_breakdown(user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(MatchStatusBreakdownSerializer(data, many=True).data)


class ExceptionBreakdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_exception_breakdown(user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(ExceptionBreakdownSerializer(data, many=True).data)


class AgentPerformanceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_agent_performance(user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(AgentPerformanceSerializer(data, many=True).data)


class DailyVolumeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        days = int(request.query_params.get("days", 30))
        data = DashboardService.get_daily_volume(days=min(days, 90), user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(DailyVolumeSerializer(data, many=True).data)


class RecentActivityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        data = DashboardService.get_recent_activity(limit=min(limit, 50), user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(RecentActivitySerializer(data, many=True).data)


class ModeBreakdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_mode_breakdown(user=request.user, tenant=getattr(request, 'tenant', None))
        return Response(ModeBreakdownSerializer(data, many=True).data)


# =========================================================================
# Agent Performance Command Center APIs
# =========================================================================

def _get_ap_filters(request):
    """Extract common filter params from query string."""
    return {
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "agent_type": request.query_params.get("agent_type"),
        "status": request.query_params.get("status"),
        "trace_id": request.query_params.get("trace_id"),
    }


class APSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_summary(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APUtilizationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_utilization(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APSuccessAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_success_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APLatencyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_latency_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APTokensAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_token_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APToolsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_tool_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APRecommendationsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_recommendation_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APLiveFeedAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 25)), 50)
        data = AgentPerformanceDashboardService.get_live_feed(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None), limit=limit,
        )
        return Response(data)


class APEscalationsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_escalation_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APFailuresAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_failure_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class APGovernanceAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_governance_metrics(
            filters=_get_ap_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        if data is None:
            return Response({"detail": "Insufficient permissions."}, status=403)
        return Response(data)


class APTraceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, run_id):
        data = AgentPerformanceDashboardService.get_trace_detail(
            run_id=run_id, user=request.user, tenant=getattr(request, 'tenant', None),
        )
        if data is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(data)
