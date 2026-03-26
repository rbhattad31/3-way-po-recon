from django.urls import path

from apps.dashboard.api_views_performance import (
    PerfLatencyView,
    PerfLiveFeedView,
    PerfPlanComparisonView,
    PerfRecommendationsView,
    PerfReliabilityView,
    PerfSummaryView,
    PerfTokensView,
    PerfToolUsageView,
    PerfUtilizationView,
)

app_name = "dashboard_agents_performance_api"

urlpatterns = [
    path("summary/", PerfSummaryView.as_view(), name="perf-summary"),
    path("utilization/", PerfUtilizationView.as_view(), name="perf-utilization"),
    path("reliability/", PerfReliabilityView.as_view(), name="perf-reliability"),
    path("latency/", PerfLatencyView.as_view(), name="perf-latency"),
    path("tokens/", PerfTokensView.as_view(), name="perf-tokens"),
    path("tools/", PerfToolUsageView.as_view(), name="perf-tools"),
    path("recommendations/", PerfRecommendationsView.as_view(), name="perf-recommendations"),
    path("live-feed/", PerfLiveFeedView.as_view(), name="perf-live-feed"),
    path("plan-comparison/", PerfPlanComparisonView.as_view(), name="perf_plan_comparison"),
]
