from django.urls import path

from apps.dashboard.api_views_governance import (
    GovAuthorizationView,
    GovCoverageTrendView,
    GovDashSummaryView,
    GovDenialsView,
    GovIdentityView,
    GovProtectedActionsView,
    GovRecommendationsView,
    GovSystemAgentView,
    GovToolAuthorizationView,
    GovTraceDetailView,
    GovTraceRunListView,
)

app_name = "dashboard_agents_governance_api"

urlpatterns = [
    path("summary/", GovDashSummaryView.as_view(), name="gov-dash-summary"),
    path("identity/", GovIdentityView.as_view(), name="gov-identity"),
    path("authorization/", GovAuthorizationView.as_view(), name="gov-authorization"),
    path("tools/", GovToolAuthorizationView.as_view(), name="gov-tools"),
    path("recommendations/", GovRecommendationsView.as_view(), name="gov-recommendations"),
    path("protected-actions/", GovProtectedActionsView.as_view(), name="gov-protected-actions"),
    path("denials/", GovDenialsView.as_view(), name="gov-denials"),
    path("coverage-trend/", GovCoverageTrendView.as_view(), name="gov-coverage-trend"),
    path("system-agent/", GovSystemAgentView.as_view(), name="gov-system-agent"),
    path("runs/", GovTraceRunListView.as_view(), name="gov-trace-runs"),
    path("trace/<int:run_id>/", GovTraceDetailView.as_view(), name="gov-trace-detail"),
]
