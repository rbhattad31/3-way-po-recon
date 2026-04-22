from django.urls import path

from apps.benchmarking import template_views as views

app_name = "benchmarking"

urlpatterns = [
    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    # All Requests
    path("", views.request_list, name="request_list"),
    path("<int:pk>/delete/", views.request_delete, name="request_delete"),
    # New Request
    path("new/", views.request_create, name="request_create"),
    # Detail / Results
    path("<int:pk>/", views.request_detail, name="request_detail"),
    # Reprocess
    path("<int:pk>/reprocess/", views.request_reprocess, name="request_reprocess"),
    path("<int:pk>/quotations/add/", views.request_add_quotations, name="request_add_quotations"),
    # Export CSV
    path("<int:pk>/export/", views.request_export, name="request_export"),
    # Export PDF
    path("<int:pk>/export-pdf/", views.request_export_pdf, name="request_export_pdf"),
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
    # Configurations -- AJAX API
    path("configurations/api/categories/", views.api_bench_categories, name="api_bench_categories"),
    path("configurations/api/corridors/", views.api_bench_corridors, name="api_bench_corridors"),
    path("configurations/api/corridors/<int:pk>/", views.api_bench_corridor_detail, name="api_bench_corridor_detail"),
    path("configurations/api/thresholds/", views.api_bench_thresholds, name="api_bench_thresholds"),
    # End-to-End lifecycle timeline
    path("<int:pk>/e2e-timeline/", views.benchmark_e2e_timeline, name="e2e_timeline"),
]
