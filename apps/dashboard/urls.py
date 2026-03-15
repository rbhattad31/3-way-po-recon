from django.urls import path

from apps.dashboard.views import (
    agent_governance,
    agent_monitor,
    agent_performance,
    analytics,
    command_center,
)

app_name = "dashboard"

urlpatterns = [
    path("", command_center, name="index"),
    path("analytics/", analytics, name="analytics"),
    path("agents/", agent_monitor, name="agent_monitor"),
    path("agents/performance/", agent_performance, name="agent_performance"),
    path("agents/governance/", agent_governance, name="agent_governance"),
]
