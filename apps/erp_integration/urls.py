"""ERP Integration template URL routes."""
from django.urls import path

from apps.erp_integration import template_views

app_name = "erp_integration"

urlpatterns = [
    path("reference-data/", template_views.erp_reference_data, name="erp_reference_data"),
    path("connections/", template_views.erp_connection_list, name="erp_connection_list"),
    path("connections/create/", template_views.erp_connection_create, name="erp_connection_create"),
    path("connections/test-ajax/", template_views.erp_connection_test_ajax, name="erp_connection_test_ajax"),
    path("connections/<int:pk>/", template_views.erp_connection_detail, name="erp_connection_detail"),
    path("connections/<int:pk>/delete/", template_views.erp_connection_delete, name="erp_connection_delete"),
    path("connections/<int:pk>/purge/", template_views.erp_connection_purge, name="erp_connection_purge"),
    path("connections/<int:pk>/test/", template_views.erp_connection_test, name="erp_connection_test"),
]
