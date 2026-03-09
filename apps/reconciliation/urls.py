from django.urls import path

from apps.reconciliation.template_views import result_detail, result_list

app_name = "reconciliation"

urlpatterns = [
    path("", result_list, name="result_list"),
    path("<int:pk>/", result_detail, name="result_detail"),
]
