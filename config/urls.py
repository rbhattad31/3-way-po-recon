"""Root URL configuration for PO Reconciliation project."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from django.views.generic import RedirectView

from apps.core.health import health_check, health_live, health_ready


def custom_page_not_found(request, exception=None):
    return render(request, "404.html", status=404)


def custom_server_error(request):
    return render(request, "500.html", status=500)


def custom_permission_denied(request, exception=None):
    return render(request, "403.html", status=403)


handler404 = custom_page_not_found
handler500 = custom_server_error
handler403 = custom_permission_denied

urlpatterns = [
    # Health checks (exempt from login middleware)
    path("health/", health_check, name="health_check"),
    path("health/live/", health_live, name="health_live"),
    path("health/ready/", health_ready, name="health_ready"),
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("api/", include("apps.core.api_urls")),
    path("api/v1/governance/", include("apps.auditlog.api_urls")),
    path("api/v1/cases/", include("apps.cases.api_urls")),
    path("api/v1/copilot/", include("apps.copilot.api_urls")),
    path("copilot/", include("apps.copilot.urls")),
    path("cases/", include("apps.cases.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
    path("invoices/", include("apps.documents.urls")),
    path("extraction/", include("apps.extraction.urls")),
    path("extraction/control-center/", include("apps.extraction_core.urls")),
    path("reconciliation/", include("apps.reconciliation.urls")),
    path("reviews/", include("apps.reviews.urls")),
    path("reports/", include("apps.reports.urls")),
    path("agents/", include("apps.agents.urls")),
    path("vendors/", include("apps.vendors.urls")),
    path("governance/", include("apps.auditlog.urls")),
    path("eval/", include("apps.core_eval.urls")),
    path("procurement/", include("apps.procurement.urls")),
    path("api/v1/procurement/", include("apps.procurement.api_urls")),
    path("posting/", include("apps.posting.urls")),
    path("api/v1/posting/", include("apps.posting.api_urls")),
    path("api/v1/posting-core/", include("apps.posting_core.api_urls")),
    path("erp/", include("apps.erp_integration.api_urls")),
    path("erp-connections/", include("apps.erp_integration.urls")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Serve custom 404 for unmatched URLs in DEBUG mode
    # (Django's default debug 404 bypasses handler404)
    import re
    from django.urls import re_path
    urlpatterns += [
        re_path(r"^(?!__debug__|__reload__).*$", custom_page_not_found),
    ]
