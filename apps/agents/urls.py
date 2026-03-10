from django.urls import path

from apps.agents.template_views import agent_reference

app_name = "agents"

urlpatterns = [
    path("reference/", agent_reference, name="agent_reference"),
]
