"""Agent Performance API views — JSON endpoints for the performance dashboard."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dashboard.agent_performance_service import AgentPerformanceDashboardService


def _get_perf_filters(request):
    return {
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "agent_type": request.query_params.get("agent_type"),
        "status": request.query_params.get("status"),
    }


class PerfSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_summary(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfUtilizationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_utilization(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfReliabilityView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_reliability(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfLatencyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_latency(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfTokensView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_tokens(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfToolUsageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_tool_usage(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfRecommendationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_recommendation_intelligence(
            filters=_get_perf_filters(request), user=request.user,
        )
        return Response(data)


class PerfLiveFeedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 25)), 50)
        data = AgentPerformanceDashboardService.get_live_feed(
            filters=_get_perf_filters(request), user=request.user, limit=limit,
        )
        return Response(data)


class PerfPlanComparisonView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = AgentPerformanceDashboardService.get_plan_comparison(
            filters=_get_perf_filters(request), user=request.user, limit=20,
        )
        return Response(data)
