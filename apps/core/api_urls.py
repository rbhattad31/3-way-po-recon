"""Top-level API URL router — aggregates all app API routes."""
from django.urls import path, include

urlpatterns = [
    path("v1/accounts/", include("apps.accounts.api_urls")),
    path("v1/documents/", include("apps.documents.api_urls")),
    path("v1/extraction/", include("apps.extraction.api_urls")),
    path("v1/reconciliation/", include("apps.reconciliation.api_urls")),
    path("v1/reviews/", include("apps.reviews.api_urls")),
    path("v1/agents/", include("apps.agents.api_urls")),
    path("v1/dashboard/", include("apps.dashboard.api_urls")),
    path("v1/dashboard/governance/", include("apps.dashboard.api_urls_governance")),
    path("v1/dashboard/agents/performance/", include("apps.dashboard.api_urls_performance")),
    path("v1/dashboard/agents/governance/", include("apps.dashboard.api_urls_agent_governance")),
    path("v1/reports/", include("apps.reports.api_urls")),
    path("v1/vendors/", include("apps.vendors.api_urls")),
]
