from django.urls import path

from apps.dashboard.views import agent_monitor, command_center, index

app_name = "dashboard"

urlpatterns = [
    path("", index, name="index"),
    path("agents/", agent_monitor, name="agent_monitor"),
    path("command-center/", command_center, name="command_center"),
]
