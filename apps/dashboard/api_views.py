"""Dashboard API views — JSON endpoints for charts & summary cards."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dashboard.serializers import (
    AgentPerformanceSerializer,
    DailyVolumeSerializer,
    DashboardSummarySerializer,
    ExceptionBreakdownSerializer,
    MatchStatusBreakdownSerializer,
    ModeBreakdownSerializer,
    RecentActivitySerializer,
)
from apps.dashboard.services import DashboardService


class DashboardSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_summary()
        return Response(DashboardSummarySerializer(data).data)


class MatchStatusBreakdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_match_status_breakdown()
        return Response(MatchStatusBreakdownSerializer(data, many=True).data)


class ExceptionBreakdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_exception_breakdown()
        return Response(ExceptionBreakdownSerializer(data, many=True).data)


class AgentPerformanceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_agent_performance()
        return Response(AgentPerformanceSerializer(data, many=True).data)


class DailyVolumeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        days = int(request.query_params.get("days", 30))
        data = DashboardService.get_daily_volume(days=min(days, 90))
        return Response(DailyVolumeSerializer(data, many=True).data)


class RecentActivityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = int(request.query_params.get("limit", 20))
        data = DashboardService.get_recent_activity(limit=min(limit, 50))
        return Response(RecentActivitySerializer(data, many=True).data)


class ModeBreakdownAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = DashboardService.get_mode_breakdown()
        return Response(ModeBreakdownSerializer(data, many=True).data)
