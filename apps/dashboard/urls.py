from django.urls import path

from apps.dashboard.views import agent_monitor, index

app_name = "dashboard"

urlpatterns = [
    path("", index, name="index"),
    path("agents/", agent_monitor, name="agent_monitor"),
]
