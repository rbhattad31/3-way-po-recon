from django.urls import path

from apps.dashboard.views import agent_monitor, analytics, command_center

app_name = "dashboard"

urlpatterns = [
    path("", command_center, name="index"),
    path("analytics/", analytics, name="analytics"),
    path("agents/", agent_monitor, name="agent_monitor"),
]
