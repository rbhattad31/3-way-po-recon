"""Audit & Governance template URL routes."""
from django.urls import path

from apps.auditlog.template_views import audit_event_list, invoice_governance

app_name = "governance"

urlpatterns = [
    path("", audit_event_list, name="audit_event_list"),
    path("invoices/<int:invoice_id>/", invoice_governance, name="invoice_governance"),
]
