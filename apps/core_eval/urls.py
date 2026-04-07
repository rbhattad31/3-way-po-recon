"""URL routes for the core_eval template views."""
from django.urls import path

from apps.core_eval.template_views import (
    eval_run_detail,
    eval_run_list,
    learning_action_detail,
    learning_action_list,
    learning_signal_detail,
    learning_signal_list,
)

app_name = "core_eval"

urlpatterns = [
    path("", eval_run_list, name="eval_run_list"),
    path("runs/<int:pk>/", eval_run_detail, name="eval_run_detail"),
    path("signals/", learning_signal_list, name="learning_signal_list"),
    path("signals/<int:pk>/", learning_signal_detail, name="learning_signal_detail"),
    path("actions/", learning_action_list, name="learning_action_list"),
    path("actions/<int:pk>/", learning_action_detail, name="learning_action_detail"),
]
