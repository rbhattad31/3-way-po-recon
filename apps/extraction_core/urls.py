"""Template URL routes for the Extraction Control Center."""
from django.urls import path

from apps.extraction_core.template_views import (
    cc_analytics_overview,
    cc_audit_log,
    cc_corrections_explorer,
    cc_country_pack_detail,
    cc_country_pack_list,
    cc_entity_profile_detail,
    cc_entity_profiles,
    cc_overview,
    cc_prompt_compare,
    cc_prompt_create,
    cc_prompt_detail,
    cc_prompt_list,
    cc_prompt_test_console,
    cc_routing_rule_detail,
    cc_routing_rule_list,
    cc_runtime_settings,
    cc_schema_compare,
    cc_schema_detail,
    cc_schema_list,
)

app_name = "extraction_control_center"

urlpatterns = [
    # Overview
    path("", cc_overview, name="overview"),
    # Runtime Settings
    path("settings/", cc_runtime_settings, name="runtime_settings"),
    # Entity Profiles
    path("entity-profiles/", cc_entity_profiles, name="entity_profiles"),
    path("entity-profiles/<int:pk>/", cc_entity_profile_detail, name="entity_profile_detail"),
    # Prompts
    path("prompts/", cc_prompt_list, name="prompt_list"),
    path("prompts/create/", cc_prompt_create, name="prompt_create"),
    path("prompts/compare/", cc_prompt_compare, name="prompt_compare"),
    path("prompts/test/", cc_prompt_test_console, name="prompt_test_console"),
    path("prompts/<int:pk>/", cc_prompt_detail, name="prompt_detail"),
    # Schemas
    path("schemas/", cc_schema_list, name="schema_list"),
    path("schemas/compare/", cc_schema_compare, name="schema_compare"),
    path("schemas/<int:pk>/", cc_schema_detail, name="schema_detail"),
    # Country Packs
    path("country-packs/", cc_country_pack_list, name="country_pack_list"),
    path("country-packs/<int:pk>/", cc_country_pack_detail, name="country_pack_detail"),
    # Routing Rules
    path("routing-rules/", cc_routing_rule_list, name="routing_rule_list"),
    path("routing-rules/create/", cc_routing_rule_detail, name="routing_rule_create"),
    path("routing-rules/<int:pk>/", cc_routing_rule_detail, name="routing_rule_detail"),
    # Analytics
    path("analytics/", cc_analytics_overview, name="analytics_overview"),
    path("corrections/", cc_corrections_explorer, name="corrections_explorer"),
    # Audit / Governance
    path("audit/", cc_audit_log, name="audit_log"),
]
