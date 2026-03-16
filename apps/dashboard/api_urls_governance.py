from django.urls import path

from apps.dashboard.governance_views import (
    GovAccessEventsAPIView,
    GovHealthAPIView,
    GovPermissionActivityAPIView,
    GovSummaryAPIView,
    GovTraceDetailAPIView,
    GovTraceRunsAPIView,
)

app_name = "dashboard_governance_api"

urlpatterns = [
    path("summary/", GovSummaryAPIView.as_view(), name="gov-summary"),
    path("access-events/", GovAccessEventsAPIView.as_view(), name="gov-access-events"),
    path("permission-activity/", GovPermissionActivityAPIView.as_view(), name="gov-permission-activity"),
    path("trace-runs/", GovTraceRunsAPIView.as_view(), name="gov-trace-runs"),
    path("trace/<int:run_id>/", GovTraceDetailAPIView.as_view(), name="gov-trace-detail"),
    path("health/", GovHealthAPIView.as_view(), name="gov-health"),
]
