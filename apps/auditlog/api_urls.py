"""Governance API URL routes."""
from django.urls import path

from apps.auditlog.views import (
    invoice_agent_trace,
    invoice_audit_history,
    invoice_recommendations,
    invoice_timeline,
)

app_name = "governance_api"

urlpatterns = [
    path("invoices/<int:invoice_id>/audit-history/", invoice_audit_history, name="invoice-audit-history"),
    path("invoices/<int:invoice_id>/agent-trace/", invoice_agent_trace, name="invoice-agent-trace"),
    path("invoices/<int:invoice_id>/recommendations/", invoice_recommendations, name="invoice-recommendations"),
    path("invoices/<int:invoice_id>/timeline/", invoice_timeline, name="invoice-timeline"),
]
