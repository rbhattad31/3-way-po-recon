from django.urls import path

from apps.agents.template_views import (
    agent_reference,
    agent_runs_list,
    agent_run_detail,
    agent_run_eval_correct,
    procurement_agent_reference,
)

app_name = "agents"

urlpatterns = [
    path("reference/", agent_reference, name="agent_reference"),
    path("procurement/reference/", procurement_agent_reference, name="procurement_agent_reference"),
    path("runs/", agent_runs_list, name="agent_runs_list"),
    path("runs/<int:pk>/", agent_run_detail, name="agent_run_detail"),
    path("runs/<int:pk>/eval-correct/", agent_run_eval_correct, name="agent_run_eval_correct"),
]
