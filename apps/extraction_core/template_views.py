"""Template views for the Extraction Control Center."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.decorators import observed_action
from apps.core.permissions import permission_required_code
from apps.extraction_core.models import (
    CountryPack,
    EntityExtractionProfile,
    ExtractionPromptTemplate,
    ExtractionRuntimeSettings,
    ExtractionSchemaDefinition,
    ReviewRoutingRule,
    TaxJurisdictionProfile,
)
from apps.extraction_core.services.analytics_service import AnalyticsService, CorrectionsExplorerService
from apps.extraction_core.services.entity_profile_service import EntityProfileService, SettingsResolutionService
from apps.extraction_core.services.extraction_governance_service import ExtractionGovernanceService
from apps.extraction_core.services.overview_service import OverviewService
from apps.extraction_core.services.prompt_registry_service import PromptRegistryService
from apps.extraction_core.services.prompt_test_service import PromptTestService
from apps.extraction_core.services.review_routing_rules_service import (
    CONDITION_TYPES,
    TARGET_QUEUES,
    ReviewRoutingPreviewService,
    ReviewRoutingRulesService,
)
from apps.extraction_core.services.runtime_settings_service import RuntimeSettingsService
from apps.extraction_core.services.schema_registry_ui_service import SchemaCompareService, SchemaRegistryUIService


# ═══════════════════════════════════════════════════════════════════════════
# OVERVIEW DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.overview")
@permission_required_code("extraction.view")
def cc_overview(request):
    """Control center overview dashboard."""
    data = OverviewService.get_dashboard_data()
    return render(request, "extraction_control_center/overview.html", {"dashboard": data})


# ═══════════════════════════════════════════════════════════════════════════
# RUNTIME SETTINGS
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.runtime_settings")
@permission_required_code("extraction.settings.view")
def cc_runtime_settings(request):
    """Runtime settings view and edit."""
    settings_obj = RuntimeSettingsService.get_active_settings()
    can_edit = _has_perm(request.user, "extraction.settings.edit")

    if request.method == "POST" and can_edit:
        if not settings_obj:
            messages.error(request, "No active settings to update.")
            return redirect("extraction_control_center:runtime_settings")

        data = {}
        for field in RuntimeSettingsService.EDITABLE_FIELDS:
            val = request.POST.get(field)
            if val is not None:
                meta = ExtractionRuntimeSettings._meta.get_field(field)
                if hasattr(meta, "choices") and meta.choices:
                    data[field] = val
                elif isinstance(meta, (type(ExtractionRuntimeSettings._meta.get_field("ocr_enabled")),)):
                    data[field] = val == "on" or val == "True"
                else:
                    data[field] = val
            else:
                # Checkbox not sent means False
                try:
                    meta = ExtractionRuntimeSettings._meta.get_field(field)
                    from django.db.models import BooleanField
                    if isinstance(meta, BooleanField):
                        data[field] = False
                except Exception:
                    logger.debug("Boolean field default for '%s' could not be resolved (non-fatal)", field, exc_info=True)

        # Parse types
        for f in ["confidence_threshold_for_detection", "auto_approval_threshold",
                   "review_confidence_threshold", "vendor_fuzzy_threshold"]:
            if f in data and data[f] not in (True, False):
                try:
                    data[f] = float(data[f])
                except (ValueError, TypeError):
                    pass
        for f in ["retry_count", "timeout_seconds", "max_pages"]:
            if f in data:
                try:
                    data[f] = int(data[f])
                except (ValueError, TypeError):
                    pass

        errors = RuntimeSettingsService.validate_settings(data)
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            RuntimeSettingsService.update_settings(settings_obj, data, request.user)
            messages.success(request, "Runtime settings updated successfully.")
            return redirect("extraction_control_center:runtime_settings")

    sections = RuntimeSettingsService.get_settings_sections(settings_obj) if settings_obj else {}

    return render(request, "extraction_control_center/runtime_settings.html", {
        "settings": settings_obj,
        "sections": sections,
        "can_edit": can_edit,
    })


# ═══════════════════════════════════════════════════════════════════════════
# ENTITY EXTRACTION PROFILES
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.entity_profiles")
@permission_required_code("extraction.settings.view")
def cc_entity_profiles(request):
    """List entity extraction profiles."""
    filters = {
        "country_code": request.GET.get("country_code", ""),
        "regime_code": request.GET.get("regime_code", ""),
        "jurisdiction_mode": request.GET.get("jurisdiction_mode", ""),
        "search": request.GET.get("search", ""),
    }
    is_active = request.GET.get("is_active")
    if is_active in ("true", "false"):
        filters["is_active"] = is_active == "true"

    profiles = EntityProfileService.list_profiles(filters)
    total = EntityExtractionProfile.objects.count()
    overridden = EntityExtractionProfile.objects.exclude(
        default_country_code="", jurisdiction_mode="AUTO"
    ).count()

    return render(request, "extraction_control_center/entity_profile_list.html", {
        "profiles": profiles,
        "filters": filters,
        "total": total,
        "overridden": overridden,
        "can_edit": _has_perm(request.user, "extraction.settings.edit"),
    })


@observed_action("cc.entity_profile_detail")
@permission_required_code("extraction.settings.view")
def cc_entity_profile_detail(request, pk):
    """Entity profile detail and edit."""
    profile = get_object_or_404(EntityExtractionProfile.objects.select_related("entity"), pk=pk)
    can_edit = _has_perm(request.user, "extraction.settings.edit")
    effective = SettingsResolutionService.get_effective_settings(profile)

    if request.method == "POST" and can_edit:
        data = {}
        for field in ["default_country_code", "default_regime_code", "default_document_language",
                       "jurisdiction_mode", "schema_override_code",
                       "validation_profile_override_code", "normalization_profile_override_code"]:
            val = request.POST.get(field)
            if val is not None:
                data[field] = val
        data["is_active"] = request.POST.get("is_active") == "on"
        EntityProfileService.update_profile(profile, data, request.user)
        messages.success(request, f"Profile for {profile.entity} updated.")
        return redirect("extraction_control_center:entity_profile_detail", pk=pk)

    return render(request, "extraction_control_center/entity_profile_detail.html", {
        "profile": profile,
        "effective": effective,
        "can_edit": can_edit,
    })


# ═══════════════════════════════════════════════════════════════════════════
# PROMPT REGISTRY
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.prompt_list")
@permission_required_code("extraction.prompts.view")
def cc_prompt_list(request):
    """List extraction prompt templates."""
    filters = {
        "prompt_code": request.GET.get("prompt_code", ""),
        "prompt_category": request.GET.get("prompt_category", ""),
        "country_code": request.GET.get("country_code", ""),
        "document_type": request.GET.get("document_type", ""),
        "status": request.GET.get("status", ""),
        "search": request.GET.get("search", ""),
    }
    prompts = PromptRegistryService.list_prompts(filters)
    return render(request, "extraction_control_center/prompt_list.html", {
        "prompts": prompts,
        "filters": filters,
        "can_edit": _has_perm(request.user, "extraction.prompts.edit"),
    })


@observed_action("cc.prompt_detail")
@permission_required_code("extraction.prompts.view")
def cc_prompt_detail(request, pk):
    """Prompt detail and edit."""
    prompt = get_object_or_404(ExtractionPromptTemplate, pk=pk)
    can_edit = _has_perm(request.user, "extraction.prompts.edit")
    version_history = PromptRegistryService.get_version_history(prompt.prompt_code)

    if request.method == "POST" and can_edit:
        action = request.POST.get("action")
        if action == "save":
            data = {
                "prompt_code": request.POST.get("prompt_code", prompt.prompt_code),
                "prompt_category": request.POST.get("prompt_category", prompt.prompt_category),
                "country_code": request.POST.get("country_code", ""),
                "regime_code": request.POST.get("regime_code", ""),
                "document_type": request.POST.get("document_type", ""),
                "schema_code": request.POST.get("schema_code", ""),
                "prompt_text": request.POST.get("prompt_text", ""),
            }
            variables = request.POST.get("variables_json", "[]")
            try:
                data["variables_json"] = json.loads(variables)
            except (json.JSONDecodeError, TypeError):
                data["variables_json"] = []
            PromptRegistryService.update_prompt(pk, data, request.user)
            messages.success(request, "Prompt updated.")
            return redirect("extraction_control_center:prompt_detail", pk=pk)
        elif action == "clone":
            clone = PromptRegistryService.clone_prompt(pk, request.user)
            if clone:
                messages.success(request, f"Cloned as v{clone.version}.")
                return redirect("extraction_control_center:prompt_detail", pk=clone.pk)
        elif action == "activate":
            PromptRegistryService.activate_prompt(pk, request.user)
            messages.success(request, "Prompt activated.")
            return redirect("extraction_control_center:prompt_detail", pk=pk)
        elif action == "deactivate":
            PromptRegistryService.deactivate_prompt(pk, request.user)
            messages.success(request, "Prompt deactivated.")
            return redirect("extraction_control_center:prompt_detail", pk=pk)

    return render(request, "extraction_control_center/prompt_detail.html", {
        "prompt": prompt,
        "version_history": version_history,
        "can_edit": can_edit,
    })


@observed_action("cc.prompt_create")
@permission_required_code("extraction.prompts.view")
def cc_prompt_create(request):
    """Create a new prompt."""
    can_edit = _has_perm(request.user, "extraction.prompts.edit")
    if not can_edit:
        messages.error(request, "You don't have permission to create prompts.")
        return redirect("extraction_control_center:prompt_list")

    if request.method == "POST":
        data = {
            "prompt_code": request.POST.get("prompt_code", ""),
            "prompt_category": request.POST.get("prompt_category", "extraction"),
            "country_code": request.POST.get("country_code", ""),
            "regime_code": request.POST.get("regime_code", ""),
            "document_type": request.POST.get("document_type", ""),
            "schema_code": request.POST.get("schema_code", ""),
            "prompt_text": request.POST.get("prompt_text", ""),
        }
        variables = request.POST.get("variables_json", "[]")
        try:
            data["variables_json"] = json.loads(variables)
        except (json.JSONDecodeError, TypeError):
            data["variables_json"] = []

        if not data["prompt_code"]:
            messages.error(request, "Prompt code is required.")
        else:
            prompt = PromptRegistryService.create_prompt(data, request.user)
            messages.success(request, f"Prompt '{prompt.prompt_code}' created.")
            return redirect("extraction_control_center:prompt_detail", pk=prompt.pk)

    return render(request, "extraction_control_center/prompt_detail.html", {
        "prompt": None,
        "version_history": [],
        "can_edit": True,
        "creating": True,
    })


@observed_action("cc.prompt_compare")
@permission_required_code("extraction.prompts.view")
def cc_prompt_compare(request):
    """Compare two prompt versions."""
    v1 = request.GET.get("v1")
    v2 = request.GET.get("v2")
    comparison = None
    if v1 and v2:
        try:
            comparison = PromptRegistryService.compare_prompts(int(v1), int(v2))
        except (ValueError, TypeError):
            messages.error(request, "Invalid version IDs.")

    prompts = ExtractionPromptTemplate.objects.order_by("prompt_code", "-version")
    return render(request, "extraction_control_center/prompt_compare.html", {
        "comparison": comparison,
        "prompts": prompts,
        "v1": v1,
        "v2": v2,
    })


# ═══════════════════════════════════════════════════════════════════════════
# PROMPT TEST CONSOLE
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.prompt_test_console")
@permission_required_code("extraction.prompts.view")
def cc_prompt_test_console(request):
    """Prompt testing console."""
    result = None
    prompt_id = request.GET.get("prompt_id") or request.POST.get("prompt_id")
    prompt_obj = None
    if prompt_id:
        prompt_obj = ExtractionPromptTemplate.objects.filter(pk=prompt_id).first()

    if request.method == "POST":
        prompt_text = request.POST.get("prompt_text", "")
        ocr_text = request.POST.get("ocr_text", "")
        country_code = request.POST.get("country_code", "")
        regime_code = request.POST.get("regime_code", "")
        document_type = request.POST.get("document_type", "")
        schema_code = request.POST.get("schema_code", "")

        if prompt_text and ocr_text:
            result = PromptTestService.run_test(
                prompt_text=prompt_text,
                ocr_text=ocr_text,
                country_code=country_code,
                regime_code=regime_code,
                document_type=document_type,
                schema_code=schema_code,
            )

    return render(request, "extraction_control_center/prompt_test_console.html", {
        "result": result,
        "prompt": prompt_obj,
    })


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA REGISTRY
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.schema_list")
@permission_required_code("extraction.schemas.view")
def cc_schema_list(request):
    """List extraction schemas."""
    filters = {
        "jurisdiction_id": request.GET.get("jurisdiction_id", ""),
        "document_type": request.GET.get("document_type", ""),
        "search": request.GET.get("search", ""),
    }
    is_active = request.GET.get("is_active")
    if is_active in ("true", "false"):
        filters["is_active"] = is_active == "true"

    schemas = SchemaRegistryUIService.list_schemas(filters)
    jurisdictions = TaxJurisdictionProfile.objects.filter(is_active=True).order_by("country_code")

    return render(request, "extraction_control_center/schema_list.html", {
        "schemas": schemas,
        "filters": filters,
        "jurisdictions": jurisdictions,
        "can_edit": _has_perm(request.user, "extraction.schemas.edit"),
    })


@observed_action("cc.schema_detail")
@permission_required_code("extraction.schemas.view")
def cc_schema_detail(request, pk):
    """Schema detail view."""
    schema = get_object_or_404(ExtractionSchemaDefinition.objects.select_related("jurisdiction"), pk=pk)
    can_edit = _has_perm(request.user, "extraction.schemas.edit")
    linked_prompts = SchemaRegistryUIService.get_linked_prompts(schema)
    version_history = SchemaRegistryUIService.get_version_history(schema)
    validation_errors = SchemaRegistryUIService.validate_schema_json(schema)
    output_preview = SchemaRegistryUIService.get_output_contract_preview(schema)
    country_pack = CountryPack.objects.filter(jurisdiction=schema.jurisdiction).first()

    if request.method == "POST" and can_edit:
        action = request.POST.get("action")
        if action == "clone":
            clone = SchemaRegistryUIService.clone_schema(pk, request.user)
            if clone:
                messages.success(request, f"Schema cloned as v{clone.schema_version}.")
                return redirect("extraction_control_center:schema_detail", pk=clone.pk)
        elif action == "activate":
            schema.is_active = True
            schema.updated_by = request.user
            schema.save()
            messages.success(request, "Schema activated.")
            return redirect("extraction_control_center:schema_detail", pk=pk)
        elif action == "deactivate":
            schema.is_active = False
            schema.updated_by = request.user
            schema.save()
            messages.success(request, "Schema deactivated.")
            return redirect("extraction_control_center:schema_detail", pk=pk)

    return render(request, "extraction_control_center/schema_detail.html", {
        "schema": schema,
        "linked_prompts": linked_prompts,
        "version_history": version_history,
        "validation_errors": validation_errors,
        "output_preview": json.dumps(output_preview, indent=2),
        "country_pack": country_pack,
        "can_edit": can_edit,
    })


@observed_action("cc.schema_compare")
@permission_required_code("extraction.schemas.view")
def cc_schema_compare(request):
    """Compare two schema versions."""
    v1 = request.GET.get("v1")
    v2 = request.GET.get("v2")
    comparison = None
    if v1 and v2:
        try:
            comparison = SchemaCompareService.compare(int(v1), int(v2))
        except (ValueError, TypeError):
            messages.error(request, "Invalid schema IDs.")

    schemas = ExtractionSchemaDefinition.objects.select_related("jurisdiction").order_by(
        "jurisdiction__country_code", "document_type", "-schema_version"
    )
    return render(request, "extraction_control_center/schema_compare.html", {
        "comparison": comparison,
        "schemas": schemas,
        "v1": v1,
        "v2": v2,
    })


# ═══════════════════════════════════════════════════════════════════════════
# COUNTRY PACKS
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.country_pack_list")
@permission_required_code("extraction.view")
def cc_country_pack_list(request):
    """Country pack list."""
    packs = CountryPack.objects.select_related("jurisdiction").all()
    return render(request, "extraction_control_center/country_pack_list.html", {
        "packs": packs,
        "can_edit": _has_perm(request.user, "extraction.schemas.edit"),
    })


@observed_action("cc.country_pack_detail")
@permission_required_code("extraction.view")
def cc_country_pack_detail(request, pk):
    """Country pack detail and lifecycle management."""
    pack = get_object_or_404(CountryPack.objects.select_related("jurisdiction"), pk=pk)
    can_edit = _has_perm(request.user, "extraction.schemas.edit")

    schemas = ExtractionSchemaDefinition.objects.filter(
        jurisdiction=pack.jurisdiction, is_active=True
    )
    prompts = ExtractionPromptTemplate.objects.filter(
        country_code=pack.jurisdiction.country_code, status="ACTIVE"
    )
    has_schema = schemas.exists()
    has_validation = bool(pack.validation_profile_version)
    has_normalization = bool(pack.normalization_profile_version)

    if request.method == "POST" and can_edit:
        action = request.POST.get("action")
        if action == "activate":
            pack.pack_status = "ACTIVE"
            pack.activated_at = timezone.now()
            pack.deactivated_at = None
            pack.updated_by = request.user
            pack.save()
            messages.success(request, f"Country pack for {pack.jurisdiction} activated.")
        elif action == "deprecate":
            pack.pack_status = "DEPRECATED"
            pack.deactivated_at = timezone.now()
            pack.updated_by = request.user
            pack.save()
            messages.success(request, f"Country pack for {pack.jurisdiction} deprecated.")
        elif action == "save":
            pack.notes = request.POST.get("notes", pack.notes)
            pack.schema_version = request.POST.get("schema_version", pack.schema_version)
            pack.validation_profile_version = request.POST.get("validation_profile_version", pack.validation_profile_version)
            pack.normalization_profile_version = request.POST.get("normalization_profile_version", pack.normalization_profile_version)
            pack.updated_by = request.user
            pack.save()
            messages.success(request, "Country pack updated.")
        return redirect("extraction_control_center:country_pack_detail", pk=pk)

    return render(request, "extraction_control_center/country_pack_detail.html", {
        "pack": pack,
        "schemas": schemas,
        "prompts": prompts,
        "readiness": {
            "has_schema": has_schema,
            "has_validation": has_validation,
            "has_normalization": has_normalization,
            "ready": has_schema and has_validation and has_normalization,
        },
        "can_edit": can_edit,
    })


# ═══════════════════════════════════════════════════════════════════════════
# REVIEW ROUTING RULES
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.routing_rule_list")
@permission_required_code("extraction.settings.view")
def cc_routing_rule_list(request):
    """List review routing rules."""
    filters = {
        "condition_type": request.GET.get("condition_type", ""),
        "target_queue": request.GET.get("target_queue", ""),
        "search": request.GET.get("search", ""),
    }
    is_active = request.GET.get("is_active")
    if is_active in ("true", "false"):
        filters["is_active"] = is_active == "true"

    rules = ReviewRoutingRulesService.list_rules(filters)

    return render(request, "extraction_control_center/routing_rule_list.html", {
        "rules": rules,
        "filters": filters,
        "condition_types": CONDITION_TYPES,
        "target_queues": TARGET_QUEUES,
        "can_edit": _has_perm(request.user, "extraction.settings.edit"),
    })


@observed_action("cc.routing_rule_detail")
@permission_required_code("extraction.settings.view")
def cc_routing_rule_detail(request, pk=None):
    """Routing rule detail/create/edit."""
    rule = None
    creating = pk is None
    if not creating:
        rule = get_object_or_404(ReviewRoutingRule, pk=pk)

    can_edit = _has_perm(request.user, "extraction.settings.edit")

    if request.method == "POST" and can_edit:
        data = {
            "name": request.POST.get("name", ""),
            "rule_code": request.POST.get("rule_code", ""),
            "condition_type": request.POST.get("condition_type", ""),
            "target_queue": request.POST.get("target_queue", ""),
            "priority": int(request.POST.get("priority", 100)),
            "description": request.POST.get("description", ""),
            "is_active": request.POST.get("is_active") == "on",
        }
        threshold = request.POST.get("threshold", "")
        if threshold:
            try:
                data["condition_config_json"] = {"threshold": float(threshold)}
            except ValueError:
                pass
        else:
            data["condition_config_json"] = {}

        action = request.POST.get("action", "save")
        if action == "activate" and rule:
            ReviewRoutingRulesService.activate_rule(pk, request.user)
            messages.success(request, "Rule activated.")
            return redirect("extraction_control_center:routing_rule_detail", pk=pk)
        elif action == "deactivate" and rule:
            ReviewRoutingRulesService.deactivate_rule(pk, request.user)
            messages.success(request, "Rule deactivated.")
            return redirect("extraction_control_center:routing_rule_detail", pk=pk)
        elif creating:
            if not data["name"] or not data["rule_code"]:
                messages.error(request, "Name and rule code are required.")
            else:
                rule = ReviewRoutingRulesService.create_rule(data, request.user)
                messages.success(request, f"Rule '{rule.name}' created.")
                return redirect("extraction_control_center:routing_rule_detail", pk=rule.pk)
        else:
            ReviewRoutingRulesService.update_rule(pk, data, request.user)
            messages.success(request, "Rule updated.")
            return redirect("extraction_control_center:routing_rule_detail", pk=pk)

    explanation = ""
    if rule:
        explanation = ReviewRoutingRulesService.get_human_readable_explanation(rule)

    return render(request, "extraction_control_center/routing_rule_detail.html", {
        "rule": rule,
        "creating": creating,
        "explanation": explanation,
        "condition_types": CONDITION_TYPES,
        "target_queues": TARGET_QUEUES,
        "can_edit": can_edit,
    })


# ═══════════════════════════════════════════════════════════════════════════
# ANALYTICS & CORRECTIONS
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.analytics_overview")
@permission_required_code("extraction.analytics.view")
def cc_analytics_overview(request):
    """Analytics overview dashboard."""
    stats = AnalyticsService.get_overview_stats()
    top_corrected = AnalyticsService.get_top_corrected_fields()
    confidence_by_country = AnalyticsService.get_confidence_by_country()
    queue_dist = AnalyticsService.get_queue_distribution()
    snapshots = AnalyticsService.get_snapshot_history()

    return render(request, "extraction_control_center/analytics_overview.html", {
        "stats": stats,
        "top_corrected": top_corrected,
        "confidence_by_country": confidence_by_country,
        "queue_distribution": queue_dist,
        "snapshots": snapshots,
    })


@observed_action("cc.corrections_explorer")
@permission_required_code("extraction.analytics.view")
def cc_corrections_explorer(request):
    """Corrections explorer table."""
    filters = {
        "field_code": request.GET.get("field_code", ""),
        "country_code": request.GET.get("country_code", ""),
        "regime_code": request.GET.get("regime_code", ""),
        "search": request.GET.get("search", ""),
    }
    corrections = CorrectionsExplorerService.list_corrections(filters)

    return render(request, "extraction_control_center/corrections_explorer.html", {
        "corrections": corrections,
        "filters": filters,
    })


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT / GOVERNANCE LOG
# ═══════════════════════════════════════════════════════════════════════════


@observed_action("cc.audit_log")
@permission_required_code("extraction.audit.view")
def cc_audit_log(request):
    """Extraction governance audit log."""
    filters = {
        "event_type": request.GET.get("event_type", ""),
        "actor": request.GET.get("actor", ""),
        "entity_type": request.GET.get("entity_type", ""),
        "search": request.GET.get("search", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
    }
    denied = request.GET.get("denied_only")
    if denied == "true":
        filters["access_granted"] = False

    events = ExtractionGovernanceService.list_events(filters)
    event_types = ExtractionGovernanceService.get_event_type_choices()

    return render(request, "extraction_control_center/audit_log.html", {
        "events": events,
        "filters": filters,
        "event_types": event_types,
    })


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _has_perm(user, code: str) -> bool:
    """Check permission using RBAC helpers."""
    from apps.core.permissions import _has_permission_code, _is_admin
    return _is_admin(user) or _has_permission_code(user, code)
