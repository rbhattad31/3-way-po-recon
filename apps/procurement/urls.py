"""Template URL routing for procurement app — mounted at /procurement/."""
from django.urls import path

from apps.procurement import template_views

app_name = "procurement"

urlpatterns = [
    path("", template_views.procurement_home, name="home"),
    path("requests/", template_views.request_list, name="request_list"),
    path("dashboard/", template_views.procurement_dashboard, name="procurement_dashboard"),
    path("create/", template_views.request_create, name="request_create"),
    path("hvac/create/", template_views.hvac_create, name="hvac_create"),
    path("<int:pk>/", template_views.request_workspace, name="request_workspace"),
    path("<int:pk>/trigger/", template_views.trigger_analysis, name="trigger_analysis"),
    path("<int:pk>/ready/", template_views.mark_ready, name="mark_ready"),
    path("<int:pk>/quotation/", template_views.upload_quotation, name="upload_quotation"),
    path("<int:pk>/validate/", template_views.trigger_validation, name="trigger_validation"),
    path("quotation/<int:pk>/prefill-review/", template_views.quotation_prefill_review, name="quotation_prefill_review"),
    path("run/<int:pk>/", template_views.run_detail, name="run_detail"),
]
