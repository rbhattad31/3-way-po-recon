"""ERP Integration URL configuration."""
from django.urls import path

from apps.erp_integration.views import resolve_erp_reference

app_name = "erp_integration_api"

urlpatterns = [
    path(
        "resolve/<str:resolution_type>/",
        resolve_erp_reference,
        name="resolve-reference",
    ),
]
