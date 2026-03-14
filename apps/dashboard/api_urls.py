from django.urls import path

from apps.dashboard.api_views import (
    AgentPerformanceAPIView,
    APEscalationsAPIView,
    APFailuresAPIView,
    APGovernanceAPIView,
    APLatencyAPIView,
    APLiveFeedAPIView,
    APRecommendationsAPIView,
    APSuccessAPIView,
    APSummaryAPIView,
    APTokensAPIView,
    APToolsAPIView,
    APTraceDetailAPIView,
    APUtilizationAPIView,
    DailyVolumeAPIView,
    DashboardSummaryAPIView,
    ExceptionBreakdownAPIView,
    MatchStatusBreakdownAPIView,
    ModeBreakdownAPIView,
    RecentActivityAPIView,
)

app_name = "dashboard_api"

urlpatterns = [
    path("summary/", DashboardSummaryAPIView.as_view(), name="summary"),
    path("match-status/", MatchStatusBreakdownAPIView.as_view(), name="match-status"),
    path("exceptions/", ExceptionBreakdownAPIView.as_view(), name="exceptions"),
    path("mode-breakdown/", ModeBreakdownAPIView.as_view(), name="mode-breakdown"),
    path("agent-performance/", AgentPerformanceAPIView.as_view(), name="agent-performance"),
    path("daily-volume/", DailyVolumeAPIView.as_view(), name="daily-volume"),
    path("recent-activity/", RecentActivityAPIView.as_view(), name="recent-activity"),
    # Agent Performance Command Center
    path("agent-performance/summary/", APSummaryAPIView.as_view(), name="ap-summary"),
    path("agent-performance/utilization/", APUtilizationAPIView.as_view(), name="ap-utilization"),
    path("agent-performance/success/", APSuccessAPIView.as_view(), name="ap-success"),
    path("agent-performance/latency/", APLatencyAPIView.as_view(), name="ap-latency"),
    path("agent-performance/tokens/", APTokensAPIView.as_view(), name="ap-tokens"),
    path("agent-performance/tools/", APToolsAPIView.as_view(), name="ap-tools"),
    path("agent-performance/recommendations/", APRecommendationsAPIView.as_view(), name="ap-recommendations"),
    path("agent-performance/live-feed/", APLiveFeedAPIView.as_view(), name="ap-live-feed"),
    path("agent-performance/escalations/", APEscalationsAPIView.as_view(), name="ap-escalations"),
    path("agent-performance/failures/", APFailuresAPIView.as_view(), name="ap-failures"),
    path("agent-performance/governance/", APGovernanceAPIView.as_view(), name="ap-governance"),
    path("agent-performance/trace/<int:run_id>/", APTraceDetailAPIView.as_view(), name="ap-trace"),
]
