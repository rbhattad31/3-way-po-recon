"""Root URL configuration for PO Reconciliation project."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("api/", include("apps.core.api_urls")),
    path("api/v1/governance/", include("apps.auditlog.api_urls")),
    path("api/v1/cases/", include("apps.cases.api_urls")),
    path("cases/", include("apps.cases.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("invoices/", include("apps.documents.urls")),
    path("extraction/", include("apps.extraction.urls")),
    path("reconciliation/", include("apps.reconciliation.urls")),
    path("reviews/", include("apps.reviews.urls")),
    path("reports/", include("apps.reports.urls")),
    path("agents/", include("apps.agents.urls")),
    path("vendors/", include("apps.vendors.urls")),
    path("governance/", include("apps.auditlog.urls")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
