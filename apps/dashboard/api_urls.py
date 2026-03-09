from django.urls import path

from apps.dashboard.api_views import (
    AgentPerformanceAPIView,
    DailyVolumeAPIView,
    DashboardSummaryAPIView,
    ExceptionBreakdownAPIView,
    MatchStatusBreakdownAPIView,
    RecentActivityAPIView,
)

app_name = "dashboard_api"

urlpatterns = [
    path("summary/", DashboardSummaryAPIView.as_view(), name="summary"),
    path("match-status/", MatchStatusBreakdownAPIView.as_view(), name="match-status"),
    path("exceptions/", ExceptionBreakdownAPIView.as_view(), name="exceptions"),
    path("agent-performance/", AgentPerformanceAPIView.as_view(), name="agent-performance"),
    path("daily-volume/", DailyVolumeAPIView.as_view(), name="daily-volume"),
    path("recent-activity/", RecentActivityAPIView.as_view(), name="recent-activity"),
]
