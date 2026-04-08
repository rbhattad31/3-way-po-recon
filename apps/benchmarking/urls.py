from django.urls import path

from apps.benchmarking import template_views as views

app_name = "benchmarking"

urlpatterns = [
    # All Requests
    path("", views.request_list, name="request_list"),
    # New Request
    path("new/", views.request_create, name="request_create"),
    # Detail / Results
    path("<int:pk>/", views.request_detail, name="request_detail"),
    # Reprocess
    path("<int:pk>/reprocess/", views.request_reprocess, name="request_reprocess"),
    # Export CSV
    path("<int:pk>/export/", views.request_export, name="request_export"),
    # Status JSON
    path("<int:pk>/status/", views.request_status, name="request_status"),
    path("<int:pk>/live-enrich/", views.request_live_enrich, name="request_live_enrich"),
    path("<int:pk>/live-enrich/ajax/", views.request_live_enrich_ajax, name="request_live_enrich_ajax"),
    # Quotations
    path("quotations/", views.quotation_list, name="quotation_list"),
    path("quotations/<int:pk>/", views.quotation_detail, name="quotation_detail"),
    # Reports
    path("reports/", views.reports, name="reports"),
    # Configurations
    path("configurations/", views.configurations, name="configurations"),
]
