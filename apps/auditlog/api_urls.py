"""Governance API URL routes."""
from django.urls import path

from apps.auditlog.views import (
    agent_performance_summary,
    case_stage_timeline,
    invoice_access_history,
    invoice_agent_trace,
    invoice_audit_history,
    invoice_recommendations,
    invoice_timeline,
    permission_denials,
    rbac_activity,
)

app_name = "governance_api"

urlpatterns = [
    # Invoice-level
    path("invoices/<int:invoice_id>/audit-history/", invoice_audit_history, name="invoice-audit-history"),
    path("invoices/<int:invoice_id>/agent-trace/", invoice_agent_trace, name="invoice-agent-trace"),
    path("invoices/<int:invoice_id>/recommendations/", invoice_recommendations, name="invoice-recommendations"),
    path("invoices/<int:invoice_id>/timeline/", invoice_timeline, name="invoice-timeline"),
    path("invoices/<int:invoice_id>/access-history/", invoice_access_history, name="invoice-access-history"),
    # Case-level
    path("cases/<int:case_id>/stage-timeline/", case_stage_timeline, name="case-stage-timeline"),
    # Platform-level (admin/auditor)
    path("permission-denials/", permission_denials, name="permission-denials"),
    path("rbac-activity/", rbac_activity, name="rbac-activity"),
    path("agent-performance/", agent_performance_summary, name="agent-performance"),
]
