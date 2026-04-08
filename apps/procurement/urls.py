"""Template URL routing for procurement app -- mounted at /procurement/."""
from django.urls import path

from apps.procurement import template_views

app_name = "procurement"

urlpatterns = [
    path("", template_views.procurement_home, name="home"),
    path("requests/", template_views.request_list, name="request_list"),
    path("dashboard/", template_views.procurement_dashboard, name="procurement_dashboard"),
    path("create/", template_views.request_create, name="request_create"),
    path("hvac/create/", template_views.hvac_create, name="hvac_create"),
    path("hvac/stores/suggestions/", template_views.api_hvac_store_suggestions, name="api_hvac_store_suggestions"),
    path("hvac/stores/create/", template_views.api_hvac_store_create, name="api_hvac_store_create"),
    path("<int:pk>/", template_views.request_workspace, name="request_workspace"),
    path("<int:pk>/trigger/", template_views.trigger_analysis, name="trigger_analysis"),
    path("<int:pk>/ready/", template_views.mark_ready, name="mark_ready"),
    path("<int:pk>/quotation/", template_views.upload_quotation, name="upload_quotation"),
    path("<int:pk>/validate/", template_views.trigger_validation, name="trigger_validation"),
    path("<int:pk>/external-suggestions/", template_views.api_external_suggestions, name="api_external_suggestions"),
    # AI Market Intelligence -- direct LLM suggestions, cached in DB
    path("<int:pk>/market-intelligence/", template_views.market_intelligence_page, name="market_intelligence"),
    path("<int:pk>/market-intelligence/research/", template_views.api_perplexity_research, name="api_perplexity_research"),
    path("quotation/<int:pk>/prefill-review/", template_views.quotation_prefill_review, name="quotation_prefill_review"),
    path("run/<int:pk>/", template_views.run_detail, name="run_detail"),
    # HVAC Flow A dedicated routes
    path("hvac/requests/", template_views.hvac_request_list, name="hvac_request_list"),
    path("hvac/requests/new/", template_views.hvac_request_form, name="hvac_request_create"),
    path("hvac/requests/<int:pk>/", template_views.hvac_request_detail, name="hvac_request_detail"),
    path("hvac/benchmarks/", template_views.hvac_benchmark_list, name="benchmark_list"),
    path("hvac/config/", template_views.hvac_config, name="hvac_config"),
    # -----------------------------------------------------------------------
    # Procurement Configurations (full admin control, AJAX-backed CRUD)
    # -----------------------------------------------------------------------
    path("configurations/", template_views.proc_configurations, name="configurations"),
    # AJAX API -- External Sources
    path("api/config/sources/", template_views.api_config_sources, name="api_config_sources"),
    path("api/config/sources/<int:pk>/", template_views.api_config_source_detail, name="api_config_source_detail"),
    # AJAX API -- Validation Rule Sets
    path("api/config/rulesets/", template_views.api_config_rulesets, name="api_config_rulesets"),
    path("api/config/rulesets/<int:pk>/", template_views.api_config_ruleset_detail, name="api_config_ruleset_detail"),
    # AJAX API -- Products
    path("api/config/products/", template_views.api_config_products, name="api_config_products"),
    path("api/config/products/<int:pk>/", template_views.api_config_product_detail, name="api_config_product_detail"),
    # AJAX API -- Vendors
    path("api/config/vendors/", template_views.api_config_vendors, name="api_config_vendors"),
    path("api/config/vendors/<int:pk>/", template_views.api_config_vendor_detail, name="api_config_vendor_detail"),
    # AJAX API -- Rooms
    path("api/config/rooms/", template_views.api_config_rooms, name="api_config_rooms"),
    path("api/config/rooms/<int:pk>/", template_views.api_config_room_detail, name="api_config_room_detail"),
    # AJAX API -- HVAC Recommendation Rules
    path("api/config/hvacrules/", template_views.api_config_hvacrules, name="api_config_hvacrules"),
    path("api/config/hvacrules/<int:pk>/", template_views.api_config_hvacrule_detail, name="api_config_hvacrule_detail"),
]
