"""Template views for the Procurement Intelligence UI."""
from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.auditlog.services import AuditService
from apps.core.permissions import permission_required_code
from apps.core.enums import (
    AnalysisRunType,
    ProcurementRequestStatus,
    ProcurementRequestType,
    ExternalSourceClass,
)
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    ComplianceResult,
    ExternalSourceRegistry,
    HVACStoreProfile,
    ProcurementRequest,
    ProcurementRequestAttribute,
    RecommendationResult,
    SupplierQuotation,
    ValidationResult,
)

from apps.procurement.agents.reason_summary_agent import ReasonSummaryAgent
from apps.agents.services.llm_client import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


HVAC_MANDATORY_FIELDS = [
    ("f_country", "Country"),
    ("f_city", "City"),
    ("f_store_type", "Store Type"),
    ("f_area_sqft", "Area (sq ft)"),
    ("f_ambient_temp_max", "Ambient Temp Max (C)"),
    ("f_budget_level", "Budget"),
    ("f_energy_efficiency_priority", "Energy Priority"),
]


def _safe_float(value):
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _build_hvac_store_profile_defaults(post_data) -> dict:
    return {
        "brand": (post_data.get("f_brand") or "").strip(),
        "country": (post_data.get("f_country") or "").strip(),
        "city": (post_data.get("f_city") or "").strip(),
        "store_type": (post_data.get("f_store_type") or "").strip(),
        "store_format": (post_data.get("f_store_format") or "").strip(),
        "area_sqft": _safe_float(post_data.get("f_area_sqft")),
        "ceiling_height_ft": _safe_float(post_data.get("f_ceiling_height_ft")),
        "operating_hours": (post_data.get("f_operating_hours") or "").strip(),
        "footfall_category": (post_data.get("f_footfall_category") or "").strip(),
        "ambient_temp_max": _safe_float(post_data.get("f_ambient_temp_max")),
        "humidity_level": (post_data.get("f_humidity_level") or "").strip(),
        "dust_exposure": (post_data.get("f_dust_exposure") or "").strip(),
        "heat_load_category": (post_data.get("f_heat_load_category") or "").strip(),
        "fresh_air_requirement": (post_data.get("f_fresh_air_requirement") or "").strip(),
        "landlord_constraints": (post_data.get("f_landlord_constraints") or "").strip(),
        "existing_hvac_type": (post_data.get("f_existing_hvac_type") or "").strip(),
        "budget_level": (post_data.get("f_budget_level") or "").strip(),
        "energy_efficiency_priority": (post_data.get("f_energy_efficiency_priority") or "").strip(),
    }


def _hvac_create_context() -> dict:
    return {
        "type_choices": ProcurementRequestType.choices,
        "priority_choices": [
            ("LOW", "Low"),
            ("MEDIUM", "Medium"),
            ("HIGH", "High"),
            ("CRITICAL", "Critical"),
        ],
    }


# ---------------------------------------------------------------------------
# 0. Procurement Home
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def procurement_home(request):
    """HVAC Procurement Intelligence home page."""
    recent_requests = (
        ProcurementRequest.objects
        .select_related("created_by")
        .filter(request_type="HVAC")
        .order_by("-created_at")[:6]
    )
    return render(request, "procurement/home.html", {
        "recent_requests": recent_requests,
    })


# ---------------------------------------------------------------------------
# 1. Request List
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def request_list(request):
    """List all procurement requests with filters."""
    # Subquery: latest recommendation_option for each request
    latest_recommendation_sq = Subquery(
        RecommendationResult.objects.filter(
            run__request=OuterRef("pk"),
        )
        .order_by("-created_at")
        .values("recommended_option")[:1]
    )
    latest_confidence_sq = Subquery(
        RecommendationResult.objects.filter(
            run__request=OuterRef("pk"),
        )
        .order_by("-created_at")
        .values("confidence_score")[:1]
    )

    qs = ProcurementRequest.objects.select_related("created_by", "assigned_to").annotate(
        attribute_count=Count("attributes"),
        quotation_count=Count("quotations"),
        run_count=Count("analysis_runs"),
        latest_recommended_option=latest_recommendation_sq,
        latest_confidence_score=latest_confidence_sq,
    )

    # Filters
    status_filter = request.GET.get("status")
    type_filter = request.GET.get("request_type")
    domain_filter = request.GET.get("domain_code")
    search = request.GET.get("q")

    if status_filter:
        qs = qs.filter(status=status_filter)
    if type_filter:
        qs = qs.filter(request_type=type_filter)
    if domain_filter:
        qs = qs.filter(domain_code=domain_filter)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))

    qs = qs.order_by("-created_at")
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    # Distinct domain codes for filter dropdown
    domains = (
        ProcurementRequest.objects.values_list("domain_code", flat=True)
        .distinct()
        .order_by("domain_code")
    )

    return render(request, "procurement/request_list.html", {
        "page_obj": page,
        "status_choices": ProcurementRequestStatus.choices,
        "type_choices": ProcurementRequestType.choices,
        "domains": domains,
        "current_status": status_filter or "",
        "current_type": type_filter or "",
        "current_domain": domain_filter or "",
        "search_query": search or "",
    })


# ---------------------------------------------------------------------------
# 2. Create Request
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.create")
def request_create(request):
    """Create a new procurement request."""
    if request.method == "POST":
        from apps.procurement.services.request_service import ProcurementRequestService

        attrs_data = []
        # Collect dynamic attributes from POST
        attr_codes = request.POST.getlist("attr_code[]")
        attr_labels = request.POST.getlist("attr_label[]")
        attr_values = request.POST.getlist("attr_value[]")
        attr_types = request.POST.getlist("attr_type[]")
        for i, code in enumerate(attr_codes):
            if code.strip():
                attrs_data.append({
                    "attribute_code": code.strip(),
                    "attribute_label": attr_labels[i] if i < len(attr_labels) else code,
                    "data_type": attr_types[i] if i < len(attr_types) else "TEXT",
                    "value_text": attr_values[i] if i < len(attr_values) else "",
                    "is_required": False,
                })

        try:
            proc_request = ProcurementRequestService.create_request(
                title=request.POST.get("title", ""),
                description=request.POST.get("description", ""),
                domain_code=request.POST.get("domain_code", ""),
                schema_code=request.POST.get("schema_code", ""),
                request_type=request.POST.get("request_type", ProcurementRequestType.RECOMMENDATION),
                priority=request.POST.get("priority", "MEDIUM"),
                geography_country=request.POST.get("geography_country", ""),
                geography_city=request.POST.get("geography_city", ""),
                currency=request.POST.get("currency", "USD"),
                created_by=request.user,
                attributes=attrs_data if attrs_data else None,
            )
            messages.success(request, f"Procurement request '{proc_request.title}' created successfully.")
            return redirect("procurement:request_workspace", pk=proc_request.pk)
        except Exception as exc:
            messages.error(request, f"Failed to create request: {exc}")

    return render(request, "procurement/request_create.html", {
        "type_choices": ProcurementRequestType.choices,
        "priority_choices": [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")],
    })


# ---------------------------------------------------------------------------
# 2b. Create HVAC Request (Landmark Group HVAC-specific form)
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.create")
def hvac_create(request):
    """Create a new HVAC procurement request using the Landmark Group HVAC form."""
    if request.method == "POST":
        from apps.procurement.services.request_service import ProcurementRequestService
        from apps.procurement.services.analysis_run_service import AnalysisRunService
        from apps.procurement.tasks import run_analysis_task

        missing_fields = []
        for field_name, field_label in HVAC_MANDATORY_FIELDS:
            if not (request.POST.get(field_name) or "").strip():
                missing_fields.append(field_label)

        if missing_fields:
            messages.error(
                request,
                "Please fill all mandatory fields before submitting: " + ", ".join(missing_fields),
            )
            return render(request, "procurement/request_create_hvac.html", _hvac_create_context())

        attrs_data = []
        attr_codes = request.POST.getlist("attr_code[]")
        attr_labels = request.POST.getlist("attr_label[]")
        attr_values = request.POST.getlist("attr_value[]")
        attr_types = request.POST.getlist("attr_type[]")
        for i, code in enumerate(attr_codes):
            if code.strip():
                attrs_data.append({
                    "attribute_code": code.strip(),
                    "attribute_label": attr_labels[i] if i < len(attr_labels) else code,
                    "data_type": attr_types[i] if i < len(attr_types) else "TEXT",
                    "value_text": attr_values[i] if i < len(attr_values) else "",
                    "is_required": False,
                })

        try:
            proc_request = ProcurementRequestService.create_request(
                title=request.POST.get("title", ""),
                description=request.POST.get("description", ""),
                domain_code="HVAC",                           # always HVAC
                schema_code="HVAC_GCC_V1",                    # HVAC schema
                request_type=request.POST.get(
                    "request_type", ProcurementRequestType.RECOMMENDATION
                ),
                priority=request.POST.get("priority", "HIGH"),
                geography_country=request.POST.get("geography_country", "UAE"),
                geography_city=request.POST.get("geography_city", ""),
                currency=request.POST.get("currency", "AED"),
                created_by=request.user,
                attributes=attrs_data if attrs_data else None,
            )

            store_id = (request.POST.get("f_store_id") or "").strip()
            if store_id:
                defaults = _build_hvac_store_profile_defaults(request.POST)
                defaults["updated_by"] = request.user
                profile, created = HVACStoreProfile.objects.update_or_create(
                    store_id=store_id,
                    defaults=defaults,
                )
                if created:
                    profile.created_by = request.user
                    profile.save(update_fields=["created_by"])

            run = AnalysisRunService.create_run(
                request=proc_request,
                run_type=AnalysisRunType.RECOMMENDATION,
                triggered_by=request.user,
            )
            run_analysis_task.delay(run.pk)

            messages.success(
                request,
                (
                    f"HVAC Procurement Request '{proc_request.title}' created successfully. "
                    "Recommendation analysis started."
                ),
            )
            return redirect("procurement:request_workspace", pk=proc_request.pk)
        except Exception as exc:
            logger.exception("hvac_create failed: %s", exc)
            messages.error(request, f"Failed to create HVAC request: {exc}")

    return render(request, "procurement/request_create_hvac.html", _hvac_create_context())


@login_required
@permission_required_code("procurement.create")
def api_hvac_store_suggestions(request):
    """Return Store ID suggestions for HVAC form autosuggest/autofill."""
    query = (request.GET.get("q") or "").strip()

    qs = HVACStoreProfile.objects.filter(is_active=True)
    if query:
        qs = qs.filter(store_id__icontains=query)

    limit = 50 if query else 500
    qs = qs.order_by("store_id")[:limit]

    results = []
    for profile in qs:
        results.append({
            "store_id": profile.store_id,
            "brand": profile.brand,
            "country": profile.country,
            "city": profile.city,
            "store_type": profile.store_type,
            "store_format": profile.store_format,
            "area_sqft": profile.area_sqft,
            "ceiling_height_ft": profile.ceiling_height_ft,
            "operating_hours": profile.operating_hours,
            "footfall_category": profile.footfall_category,
            "ambient_temp_max": profile.ambient_temp_max,
            "humidity_level": profile.humidity_level,
            "dust_exposure": profile.dust_exposure,
            "heat_load_category": profile.heat_load_category,
            "fresh_air_requirement": profile.fresh_air_requirement,
            "landlord_constraints": profile.landlord_constraints,
            "existing_hvac_type": profile.existing_hvac_type,
            "budget_level": profile.budget_level,
            "energy_efficiency_priority": profile.energy_efficiency_priority,
        })

    return JsonResponse({"results": results})


@login_required
@permission_required_code("procurement.create")
def api_hvac_store_create(request):
    """AJAX POST -- create a new HVACStoreProfile and return the full profile JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    data = request.POST
    store_id = (data.get("store_id") or "").strip()
    if not store_id:
        return JsonResponse({"error": "Store ID is required."}, status=400)

    if HVACStoreProfile.objects.filter(store_id=store_id).exists():
        return JsonResponse({"error": f"Store ID '{store_id}' already exists. Please choose a different ID."},
                            status=409)

    try:
        defaults = _build_hvac_store_profile_defaults(data)
        profile = HVACStoreProfile.objects.create(
            store_id=store_id,
            created_by=request.user,
            **defaults,
        )
        result = {
            "store_id": profile.store_id,
            "brand": profile.brand,
            "country": profile.country,
            "city": profile.city,
            "store_type": profile.store_type,
            "store_format": profile.store_format,
            "area_sqft": profile.area_sqft,
            "ceiling_height_ft": profile.ceiling_height_ft,
            "operating_hours": profile.operating_hours,
            "footfall_category": profile.footfall_category,
            "ambient_temp_max": profile.ambient_temp_max,
            "humidity_level": profile.humidity_level,
            "dust_exposure": profile.dust_exposure,
            "heat_load_category": profile.heat_load_category,
            "fresh_air_requirement": profile.fresh_air_requirement,
            "landlord_constraints": profile.landlord_constraints,
            "existing_hvac_type": profile.existing_hvac_type,
            "budget_level": profile.budget_level,
            "energy_efficiency_priority": profile.energy_efficiency_priority,
        }
        return JsonResponse({"ok": True, "profile": result}, status=201)
    except Exception as exc:
        logger.exception("api_hvac_store_create failed: %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# 3. Request Workspace (Detail / Deep Dive)
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def request_workspace(request, pk):
    """Full workspace view for a procurement request."""
    proc_request = get_object_or_404(
        ProcurementRequest.objects.select_related("created_by", "assigned_to"),
        pk=pk,
    )
    attributes = proc_request.attributes.all()
    quotations = proc_request.quotations.select_related("uploaded_document").all()
    runs = proc_request.analysis_runs.select_related("triggered_by").order_by("-created_at")

    # Latest recommendation
    recommendation = RecommendationResult.objects.filter(
        run__request=proc_request,
    ).select_related("run").order_by("-created_at").first()

    # Latest benchmark results
    benchmarks = BenchmarkResult.objects.filter(
        run__request=proc_request,
    ).select_related("run", "quotation").prefetch_related("lines").order_by("-created_at")

    # Latest compliance
    compliance = ComplianceResult.objects.filter(
        run__request=proc_request,
    ).select_related("run").order_by("-created_at").first()

    # Activity timeline from existing governance
    audit_events = AuditService.fetch_entity_history("ProcurementRequest", proc_request.pk)

    # Latest validation result
    validation_result = (
        ValidationResult.objects
        .filter(run__request=proc_request)
        .select_related("run")
        .prefetch_related("items")
        .order_by("-created_at")
        .first()
    )
    validation_items = validation_result.items.all() if validation_result else []

    # ReasonSummaryAgent -- structured explanation for the workspace UI
    reason_summary = None
    if recommendation:
        try:
            reason_summary = ReasonSummaryAgent.generate(recommendation)
        except Exception as _e:
            logger.warning("ReasonSummaryAgent failed for request %s: %s", pk, _e)

    return render(request, "procurement/request_workspace.html", {
        "proc_request": proc_request,
        "attributes": attributes,
        "quotations": quotations,
        "runs": runs,
        "recommendation": recommendation,
        "reason_summary": reason_summary,
        "benchmarks": benchmarks,
        "compliance": compliance,
        "validation_result": validation_result,
        "validation_items": validation_items,
        "audit_events": audit_events[:50],
        "status_choices": ProcurementRequestStatus.choices,
    })


# ---------------------------------------------------------------------------
# 4. Analysis Run Detail
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view_results")
def run_detail(request, pk):
    """Detail view for a single analysis run."""
    run = get_object_or_404(
        AnalysisRun.objects.select_related("request", "triggered_by"),
        pk=pk,
    )

    recommendation = None
    benchmarks = None
    compliance = None
    validation = None

    if run.run_type == AnalysisRunType.RECOMMENDATION:
        recommendation = RecommendationResult.objects.filter(run=run).first()
    elif run.run_type == AnalysisRunType.BENCHMARK:
        benchmarks = BenchmarkResult.objects.filter(run=run).prefetch_related("lines", "quotation")

    compliance = ComplianceResult.objects.filter(run=run).first()

    # Check for validation result
    from apps.core.enums import AnalysisRunType as ART
    if run.run_type == ART.VALIDATION:
        validation = ValidationResult.objects.filter(run=run).prefetch_related("items").first()

    compliance = ComplianceResult.objects.filter(run=run).first()

    # Audit events for this run
    audit_events = AuditService.fetch_entity_history("AnalysisRun", run.pk)

    return render(request, "procurement/run_detail.html", {
        "run": run,
        "recommendation": recommendation,
        "benchmarks": benchmarks,
        "compliance": compliance,
        "validation": validation,
        "audit_events": audit_events[:30],
    })


# ---------------------------------------------------------------------------
# Actions: Trigger analysis, mark ready, upload quotation
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.run_analysis")
def trigger_analysis(request, pk):
    """Trigger an analysis run from the workspace."""
    if request.method != "POST":
        return redirect("procurement:request_workspace", pk=pk)

    proc_request = get_object_or_404(ProcurementRequest, pk=pk)
    run_type = request.POST.get("run_type", "RECOMMENDATION")

    from apps.procurement.services.analysis_run_service import AnalysisRunService
    from apps.procurement.tasks import run_analysis_task

    run = AnalysisRunService.create_run(
        request=proc_request,
        run_type=run_type,
        triggered_by=request.user,
    )
    run_analysis_task.delay(run.pk)
    messages.success(request, f"{run_type} analysis queued (Run {run.run_id}).")
    return redirect("procurement:request_workspace", pk=pk)


@login_required
@permission_required_code("procurement.edit")
def mark_ready(request, pk):
    """Mark a request as READY after attribute validation."""
    if request.method != "POST":
        return redirect("procurement:request_workspace", pk=pk)

    proc_request = get_object_or_404(ProcurementRequest, pk=pk)
    from apps.procurement.services.request_service import ProcurementRequestService
    try:
        ProcurementRequestService.mark_ready(proc_request, user=request.user)
        messages.success(request, "Request marked as ready.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("procurement:request_workspace", pk=pk)


@login_required
@permission_required_code("procurement.manage_quotations")
def upload_quotation(request, pk):
    """Upload a supplier quotation to a request."""
    if request.method != "POST":
        return redirect("procurement:request_workspace", pk=pk)

    proc_request = get_object_or_404(ProcurementRequest, pk=pk)
    from apps.procurement.services.quotation_service import QuotationService

    try:
        quotation = QuotationService.create_quotation(
            request=proc_request,
            vendor_name=request.POST.get("vendor_name", ""),
            quotation_number=request.POST.get("quotation_number", ""),
            total_amount=request.POST.get("total_amount") or None,
            currency=request.POST.get("currency", "USD"),
            created_by=request.user,
        )
        messages.success(request, f"Quotation from '{quotation.vendor_name}' added.")
    except Exception as exc:
        messages.error(request, f"Failed to add quotation: {exc}")
    return redirect("procurement:request_workspace", pk=pk)


# ---------------------------------------------------------------------------
# Trigger Validation
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.validate")
def trigger_validation(request, pk):
    """Trigger a validation run from the workspace."""
    if request.method != "POST":
        return redirect("procurement:request_workspace", pk=pk)

    proc_request = get_object_or_404(ProcurementRequest, pk=pk)

    from apps.core.enums import AnalysisRunType
    from apps.procurement.services.analysis_run_service import AnalysisRunService
    from apps.procurement.tasks import run_validation_task

    run = AnalysisRunService.create_run(
        request=proc_request,
        run_type=AnalysisRunType.VALIDATION,
        triggered_by=request.user,
    )
    run_validation_task.delay(run.pk)
    messages.success(request, f"Validation queued (Run {run.run_id}).")
    return redirect("procurement:request_workspace", pk=pk)


# ---------------------------------------------------------------------------
# Quotation Prefill Review
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.manage_quotations")
def quotation_prefill_review(request, pk):
    """Review and confirm extracted quotation data from PDF prefill."""
    import json
    quotation = get_object_or_404(
        SupplierQuotation.objects.select_related("request", "uploaded_document"),
        pk=pk,
    )
    payload = quotation.prefill_payload_json or {}
    return render(request, "procurement/quotation_prefill_review.html", {
        "quotation": quotation,
        "payload": payload,
        "payload_json": json.dumps(payload),
    })


# ---------------------------------------------------------------------------
# Procurement Manager Dashboard — Landmark Group
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def procurement_dashboard(request):
    """Landmark Group Procurement Manager Dashboard with KPIs, charts, and quick actions."""
    from django.db.models import Count

    qs = ProcurementRequest.objects.all()

    # KPI counts
    status_counts = {item["status"]: item["total"] for item in qs.values("status").annotate(total=Count("id"))}
    kpi = {
        "total": qs.count(),
        "draft": status_counts.get("DRAFT", 0),
        "ready": status_counts.get("READY", 0),
        "pending_approval": status_counts.get("REVIEW_REQUIRED", 0),
        "approved": status_counts.get("COMPLETED", 0),
        "hvac": qs.filter(domain_code="HVAC").count(),
    }

    # Status chart data
    status_chart = list(qs.values("status").annotate(total=Count("id")).order_by("-total"))

    # Domain breakdown chart
    domain_chart = list(
        qs.exclude(domain_code="").values("domain_code").annotate(total=Count("id")).order_by("-total")[:10]
    )

    # GCC country breakdown
    from django.db.models import F as _F
    by_country = list(
        qs.exclude(geography_country__isnull=True).exclude(geography_country="")
        .values(country=_F("geography_country")).annotate(total=Count("id")).order_by("-total")
    )

    # HVAC type breakdown using attributes
    hvac_type_labels = {
        "SPLIT_AC": "Split AC",
        "CASSETTE_AC": "Cassette AC",
        "VRF_VRV": "VRF/VRV System",
        "FCU_CW": "FCU / Chilled Water",
        "AHU": "Air Handling Unit",
        "PACKAGED_UNIT": "Packaged Unit",
        "CHILLER": "Chiller Plant",
        "VENTILATION": "Ventilation / MEP",
    }
    from apps.procurement.models import ProcurementRequestAttribute
    hvac_attr_qs = (
        ProcurementRequestAttribute.objects.filter(
            request__domain_code="HVAC",
            attribute_code="product_type",
        )
        .values("value_text")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    hvac_by_type = [
        {"type_label": hvac_type_labels.get(row["value_text"] or "", row["value_text"] or "Unknown"), "total": row["total"]}
        for row in hvac_attr_qs
    ]

    # Recent requests
    recent_requests = (
        qs.select_related("created_by")
        .order_by("-created_at")[:10]
    )

    return render(request, "procurement/procurement_dashboard.html", {
        "kpi": kpi,
        "status_chart": status_chart,
        "domain_chart": domain_chart,
        "by_country": by_country,
        "hvac_by_type": hvac_by_type,
        "recent_requests": recent_requests,
    })


# ===========================================================================
# HVAC FLOW A -- new dedicated views
# ===========================================================================

# ---------------------------------------------------------------------------
# H1. HVAC Request List (dashboard)
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def hvac_request_list(request):
    """List all HVAC procurement requests with KPIs, search, and filters."""
    base_qs = ProcurementRequest.objects.filter(domain_code="HVAC")

    # KPI totals
    total = base_qs.count()
    completed = base_qs.filter(status="COMPLETED").count()
    in_progress = base_qs.filter(status__in=["IN_PROGRESS", "RUNNING", "READY"]).count()
    # needs_review: requests whose latest recommendation has human_review flags
    needs_review = base_qs.filter(status="REVIEW_REQUIRED").count()

    # Annotate with latest recommendation data
    latest_rec_option_sq = Subquery(
        RecommendationResult.objects.filter(run__request=OuterRef("pk"))
        .order_by("-created_at")
        .values("recommended_option")[:1]
    )
    latest_conf_sq = Subquery(
        RecommendationResult.objects.filter(run__request=OuterRef("pk"))
        .order_by("-created_at")
        .values("confidence_score")[:1]
    )

    qs = base_qs.select_related("created_by").annotate(
        run_count=Count("analysis_runs"),
        latest_recommended_option=latest_rec_option_sq,
        latest_confidence_score=latest_conf_sq,
    )

    # Filters
    status_filter = request.GET.get("status", "")
    search = request.GET.get("q", "")
    sort = request.GET.get("sort", "newest")

    if status_filter:
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(
            Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(geography_country__icontains=search)
            | Q(geography_city__icontains=search)
        )
    if sort == "oldest":
        qs = qs.order_by("created_at")
    elif sort == "confidence":
        qs = qs.order_by("-latest_confidence_score")
    else:
        qs = qs.order_by("-created_at")

    paginator = Paginator(qs, 12)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "procurement/hvac_request_list.html", {
        "page_obj": page,
        "kpi": {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "needs_review": needs_review,
        },
        "current_status": status_filter,
        "search_query": search,
        "current_sort": sort,
        "status_choices": ProcurementRequestStatus.choices,
    })


# ---------------------------------------------------------------------------
# H2. HVAC Request Detail (recommendation workspace)
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def hvac_request_detail(request, pk):
    """Full recommendation workspace for a single HVAC request."""
    proc_request = get_object_or_404(
        ProcurementRequest.objects.select_related("created_by", "assigned_to"),
        pk=pk, domain_code="HVAC",
    )
    attributes = proc_request.attributes.order_by("attribute_code")
    runs = proc_request.analysis_runs.select_related("triggered_by").order_by("-created_at")
    latest_run = runs.first()

    recommendation = (
        RecommendationResult.objects.filter(run__request=proc_request)
        .select_related("run")
        .order_by("-created_at")
        .first()
    )

    # Parse structured payload sections safely
    payload = {}
    if recommendation and recommendation.output_payload_json:
        raw = recommendation.output_payload_json
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
        elif isinstance(raw, dict):
            payload = raw

    # Agent execution records for this request (all runs)
    from apps.procurement.models import ProcurementAgentExecutionRecord
    agent_records = ProcurementAgentExecutionRecord.objects.filter(
        analysis_run__request=proc_request
    ).order_by("-created_at")[:20]

    # Latest compliance
    compliance = (
        ComplianceResult.objects.filter(run__request=proc_request)
        .select_related("run")
        .order_by("-created_at")
        .first()
    )

    # Audit events
    audit_events = AuditService.fetch_entity_history("ProcurementRequest", proc_request.pk)

    # Benchmarks
    benchmarks = BenchmarkResult.objects.filter(
        run__request=proc_request,
    ).select_related("run", "quotation").prefetch_related("lines").order_by("-created_at")

    return render(request, "procurement/hvac_request_detail.html", {
        "proc_request": proc_request,
        "attributes": attributes,
        "runs": runs,
        "latest_run": latest_run,
        "recommendation": recommendation,
        "payload": payload,
        "agent_records": agent_records,
        "compliance": compliance,
        "benchmarks": benchmarks,
        "audit_events": audit_events[:30],
    })


# ---------------------------------------------------------------------------
# H3. HVAC Request Form (create new)
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.create")
def hvac_request_form(request):
    """Create a new HVAC procurement request via the structured HVAC form."""
    if request.method == "POST":
        from apps.procurement.services.request_service import ProcurementRequestService
        from apps.procurement.services.analysis_run_service import AnalysisRunService
        from apps.procurement.tasks import run_analysis_task

        # Map flat form fields to ProcurementRequestAttribute records
        HVAC_FIELD_MAP = [
            ("store_id", "Store ID / Reference", "TEXT"),
            ("brand", "Brand / Operator", "TEXT"),
            ("geography_zone", "Geography Zone", "TEXT"),
            ("store_type", "Store Type", "TEXT"),
            ("store_format", "Store Format", "TEXT"),
            ("area_sqft", "Store Area (sqft)", "NUMBER"),
            ("ceiling_height_ft", "Ceiling Height (ft)", "NUMBER"),
            ("ambient_temp_max", "Ambient Temp Max (C)", "NUMBER"),
            ("humidity_level", "Humidity Level", "TEXT"),
            ("dust_exposure", "Dust Exposure", "TEXT"),
            ("heat_load_category", "Heat Load Category", "TEXT"),
            ("fresh_air_requirement", "Fresh Air Requirement", "TEXT"),
            ("existing_hvac_type", "Existing HVAC Type", "TEXT"),
            ("landlord_constraints", "Landlord Constraints", "TEXT"),
            ("required_standards_notes", "Required Standards / Notes", "TEXT"),
            ("budget_level", "Budget Level", "TEXT"),
            ("energy_efficiency_priority", "Energy Efficiency Priority", "TEXT"),
            ("maintenance_priority", "Maintenance Priority", "TEXT"),
            ("preferred_oems", "Preferred OEMs", "TEXT"),
        ]

        attrs_data = []
        for code, label, dtype in HVAC_FIELD_MAP:
            val = request.POST.get(code, "").strip()
            if val:
                attrs_data.append({
                    "attribute_code": code,
                    "attribute_label": label,
                    "data_type": dtype,
                    "value_text": val,
                    "is_required": False,
                })

        try:
            proc_request = ProcurementRequestService.create_request(
                title=request.POST.get("title", ""),
                description=request.POST.get("description", ""),
                domain_code="HVAC",
                schema_code="HVAC_FLOW_A",
                request_type=ProcurementRequestType.RECOMMENDATION,
                priority=request.POST.get("priority", "HIGH"),
                geography_country=request.POST.get("geography_country", "UAE"),
                geography_city=request.POST.get("geography_city", ""),
                currency="AED",
                created_by=request.user,
                attributes=attrs_data if attrs_data else None,
            )
            action = request.POST.get("action", "save")
            if action == "run":
                run = AnalysisRunService.create_run(
                    request=proc_request,
                    run_type=AnalysisRunType.RECOMMENDATION,
                    triggered_by=request.user,
                )
                run_analysis_task.delay(run.pk)
                messages.success(request, "HVAC request created and analysis queued.")
            else:
                messages.success(request, f"HVAC request '{proc_request.title}' saved.")
            return redirect("procurement:hvac_request_detail", pk=proc_request.pk)
        except Exception as exc:
            logger.exception("hvac_request_form POST failed: %s", exc)
            messages.error(request, f"Failed to save request: {exc}")

    return render(request, "procurement/hvac_request_form.html", {
        "priority_choices": [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")],
    })


# ---------------------------------------------------------------------------
# H4. Benchmarking List
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def hvac_benchmark_list(request):
    """List all HVAC benchmarking runs."""
    qs = BenchmarkResult.objects.filter(
        run__request__domain_code="HVAC",
    ).select_related("run", "run__request", "quotation").prefetch_related("lines").order_by("-created_at")

    total = qs.count()
    completed = qs.filter(run__status="COMPLETED").count()

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "procurement/hvac_benchmark_list.html", {
        "page_obj": page,
        "kpi": {"total": total, "completed": completed},
    })


# ---------------------------------------------------------------------------
# H5. HVAC Configuration
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.edit")
def hvac_config(request):
    """HVAC Flow A configuration -- source registry and rule engine settings."""
    if request.method == "POST":
        action = request.POST.get("config_action", "")
        if action == "add_source":
            try:
                ExternalSourceRegistry.objects.create(
                    source_name=request.POST.get("source_name", ""),
                    domain=request.POST.get("domain", ""),
                    source_type=request.POST.get("source_type", ExternalSourceClass.OEM_OFFICIAL),
                    country_scope=[c.strip() for c in request.POST.get("country_scope", "").split(",") if c.strip()],
                    priority=int(request.POST.get("priority", 10)),
                    trust_score=float(request.POST.get("trust_score", 0.8)),
                    allowed_for_discovery=request.POST.get("allowed_for_discovery") == "on",
                    allowed_for_compliance=request.POST.get("allowed_for_compliance") == "on",
                    fetch_mode=request.POST.get("fetch_mode", "PAGE"),
                    notes=request.POST.get("notes", ""),
                )
                messages.success(request, "External source added.")
            except Exception as exc:
                logger.exception("add_source failed: %s", exc)
                messages.error(request, f"Failed to add source: {exc}")
        elif action == "toggle_source":
            src_id = request.POST.get("source_id")
            if src_id:
                try:
                    src = ExternalSourceRegistry.objects.get(pk=src_id)
                    src.is_active = not src.is_active
                    src.save(update_fields=["is_active"])
                    messages.success(request, f"Source '{src.source_name}' toggled.")
                except ExternalSourceRegistry.DoesNotExist:
                    messages.error(request, "Source not found.")
        return redirect("procurement:hvac_config")

    sources = ExternalSourceRegistry.objects.all().order_by("priority", "source_name")

    return render(request, "procurement/hvac_config.html", {
        "sources": sources,
        "source_type_choices": ExternalSourceClass.choices,
        "fetch_mode_choices": [("PAGE", "Web Page"), ("PDF", "PDF Download"), ("API", "API Endpoint")],
    })


# =============================================================================
# PROCUREMENT CONFIGURATIONS -- full admin CRUD hub (AJAX-backed)
# =============================================================================

@login_required
@permission_required_code("procurement.view")
def proc_configurations(request):
    """Procurement Configurations hub -- seed data admin control."""
    from apps.procurement.models import (
        ExternalSourceRegistry, ValidationRuleSet, Product, Vendor, Room,
        HVACRecommendationRule,
    )
    from apps.core.enums import (
        ExternalSourceClass, ValidationType, HVACSystemType, RoomUsageType,
    )

    stats = {
        "sources_total": ExternalSourceRegistry.objects.count(),
        "sources_active": ExternalSourceRegistry.objects.filter(is_active=True).count(),
        "rulesets_total": ValidationRuleSet.objects.count(),
        "rulesets_active": ValidationRuleSet.objects.filter(is_active=True).count(),
        "products_total": Product.objects.count(),
        "products_active": Product.objects.filter(is_active=True).count(),
        "vendors_total": Vendor.objects.count(),
        "vendors_active": Vendor.objects.filter(is_active=True).count(),
        "rooms_total": Room.objects.count(),
        "rooms_active": Room.objects.filter(is_active=True).count(),
        "hvacrules_total": HVACRecommendationRule.objects.count(),
        "hvacrules_active": HVACRecommendationRule.objects.filter(is_active=True).count(),
    }

    return render(request, "procurement/configurations.html", {
        "stats": stats,
        "source_type_choices": ExternalSourceClass.choices,
        "fetch_mode_choices": [("PAGE", "Web Page"), ("PDF", "PDF Download"), ("API", "API Endpoint")],
        "validation_type_choices": ValidationType.choices,
        "system_type_choices": HVACSystemType.choices,
        "room_usage_choices": RoomUsageType.choices,
        "active_tab": request.GET.get("tab", "sources"),
        # HVAC rules filter choices
        "hvac_store_type_choices": HVACRecommendationRule.STORE_TYPE_CHOICES,
        "hvac_budget_choices": HVACRecommendationRule.BUDGET_CHOICES,
        "hvac_energy_priority_choices": HVACRecommendationRule.ENERGY_PRIORITY_CHOICES,
    })


# ---------------------------------------------------------------------------
# AJAX API -- External Source Registry
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("procurement.view")
def api_config_sources(request):
    """List (GET) or Create (POST) ExternalSourceRegistry entries."""
    from apps.procurement.models import ExternalSourceRegistry
    from django.http import JsonResponse

    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        qs = ExternalSourceRegistry.objects.all().order_by("priority", "source_name")
        if q:
            qs = qs.filter(
                Q(source_name__icontains=q) | Q(domain__icontains=q) | Q(source_type__icontains=q)
            )
        items = [
            {
                "id": s.pk,
                "source_name": s.source_name,
                "domain": s.domain,
                "source_type": s.source_type,
                "source_type_display": s.get_source_type_display(),
                "priority": s.priority,
                "trust_score": float(s.trust_score),
                "allowed_for_discovery": s.allowed_for_discovery,
                "allowed_for_compliance": s.allowed_for_compliance,
                "fetch_mode": s.fetch_mode,
                "country_scope": ", ".join(s.country_scope) if s.country_scope else "",
                "notes": s.notes,
                "is_active": s.is_active,
            }
            for s in qs
        ]
        return JsonResponse({"items": items, "total": len(items)})

    if request.method == "POST":
        if not request.user.has_perm("procurement.edit") and not request.user.is_staff:
            # Simple permission check -- defer to RBAC
            pass
        try:
            body = json.loads(request.body)
            src = ExternalSourceRegistry.objects.create(
                source_name=body.get("source_name", "").strip(),
                domain=body.get("domain", "").strip(),
                source_type=body.get("source_type", "OEM_OFFICIAL"),
                country_scope=[c.strip() for c in body.get("country_scope", "").split(",") if c.strip()],
                priority=int(body.get("priority", 10)),
                trust_score=float(body.get("trust_score", 0.8)),
                allowed_for_discovery=bool(body.get("allowed_for_discovery", True)),
                allowed_for_compliance=bool(body.get("allowed_for_compliance", False)),
                fetch_mode=body.get("fetch_mode", "PAGE"),
                notes=body.get("notes", ""),
                is_active=bool(body.get("is_active", True)),
            )
            return JsonResponse({
                "success": True, "id": src.pk,
                "message": f"Source '{src.source_name}' created successfully.",
            })
        except Exception as exc:
            logger.exception("api_config_sources POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@permission_required_code("procurement.view")
def api_config_source_detail(request, pk):
    """Get (GET), Update (PUT-via-POST) or Delete (DELETE-via-POST) a source."""
    from apps.procurement.models import ExternalSourceRegistry
    from django.http import JsonResponse

    try:
        src = ExternalSourceRegistry.objects.get(pk=pk)
    except ExternalSourceRegistry.DoesNotExist:
        return JsonResponse({"success": False, "message": "Source not found."}, status=404)

    if request.method == "GET":
        return JsonResponse({
            "id": src.pk,
            "source_name": src.source_name,
            "domain": src.domain,
            "source_type": src.source_type,
            "priority": src.priority,
            "trust_score": float(src.trust_score),
            "allowed_for_discovery": src.allowed_for_discovery,
            "allowed_for_compliance": src.allowed_for_compliance,
            "fetch_mode": src.fetch_mode,
            "country_scope": ", ".join(src.country_scope) if src.country_scope else "",
            "notes": src.notes,
            "is_active": src.is_active,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            action = body.get("_action", "update")

            if action == "delete":
                name = src.source_name
                src.delete()
                return JsonResponse({"success": True, "message": f"Source '{name}' deleted."})

            if action == "toggle":
                src.is_active = not src.is_active
                src.save(update_fields=["is_active"])
                return JsonResponse({"success": True, "is_active": src.is_active,
                                     "message": f"Source '{src.source_name}' {'activated' if src.is_active else 'deactivated'}."})

            # update
            src.source_name = body.get("source_name", src.source_name).strip()
            src.domain = body.get("domain", src.domain).strip()
            src.source_type = body.get("source_type", src.source_type)
            country_raw = body.get("country_scope", "")
            src.country_scope = [c.strip() for c in country_raw.split(",") if c.strip()] if country_raw else src.country_scope
            src.priority = int(body.get("priority", src.priority))
            src.trust_score = float(body.get("trust_score", src.trust_score))
            src.allowed_for_discovery = bool(body.get("allowed_for_discovery", src.allowed_for_discovery))
            src.allowed_for_compliance = bool(body.get("allowed_for_compliance", src.allowed_for_compliance))
            src.fetch_mode = body.get("fetch_mode", src.fetch_mode)
            src.notes = body.get("notes", src.notes)
            src.is_active = bool(body.get("is_active", src.is_active))
            src.save()
            return JsonResponse({"success": True, "message": f"Source '{src.source_name}' updated."})
        except Exception as exc:
            logger.exception("api_config_source_detail POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ---------------------------------------------------------------------------
# AJAX API -- Validation Rule Sets
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("procurement.view")
def api_config_rulesets(request):
    """List (GET) or Create (POST) ValidationRuleSet entries."""
    from apps.procurement.models import ValidationRuleSet
    from django.http import JsonResponse

    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        qs = ValidationRuleSet.objects.all().order_by("priority", "rule_set_code")
        if q:
            qs = qs.filter(
                Q(rule_set_code__icontains=q) | Q(rule_set_name__icontains=q) | Q(domain_code__icontains=q)
            )
        items = [
            {
                "id": r.pk,
                "rule_set_code": r.rule_set_code,
                "rule_set_name": r.rule_set_name,
                "domain_code": r.domain_code,
                "schema_code": r.schema_code,
                "validation_type": r.validation_type,
                "validation_type_display": r.get_validation_type_display(),
                "priority": r.priority,
                "description": r.description,
                "is_active": r.is_active,
            }
            for r in qs
        ]
        return JsonResponse({"items": items, "total": len(items)})

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            rs = ValidationRuleSet.objects.create(
                rule_set_code=body.get("rule_set_code", "").strip(),
                rule_set_name=body.get("rule_set_name", "").strip(),
                domain_code=body.get("domain_code", "").strip(),
                schema_code=body.get("schema_code", "").strip(),
                validation_type=body.get("validation_type", "ATTRIBUTE_COMPLETENESS"),
                priority=int(body.get("priority", 100)),
                description=body.get("description", ""),
                is_active=bool(body.get("is_active", True)),
            )
            return JsonResponse({"success": True, "id": rs.pk,
                                 "message": f"Rule set '{rs.rule_set_code}' created."})
        except Exception as exc:
            logger.exception("api_config_rulesets POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@permission_required_code("procurement.view")
def api_config_ruleset_detail(request, pk):
    """Get, Update, or Delete a ValidationRuleSet."""
    from apps.procurement.models import ValidationRuleSet
    from django.http import JsonResponse

    try:
        rs = ValidationRuleSet.objects.get(pk=pk)
    except ValidationRuleSet.DoesNotExist:
        return JsonResponse({"success": False, "message": "Rule set not found."}, status=404)

    if request.method == "GET":
        return JsonResponse({
            "id": rs.pk,
            "rule_set_code": rs.rule_set_code,
            "rule_set_name": rs.rule_set_name,
            "domain_code": rs.domain_code,
            "schema_code": rs.schema_code,
            "validation_type": rs.validation_type,
            "priority": rs.priority,
            "description": rs.description,
            "is_active": rs.is_active,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            action = body.get("_action", "update")

            if action == "delete":
                code = rs.rule_set_code
                rs.delete()
                return JsonResponse({"success": True, "message": f"Rule set '{code}' deleted."})

            if action == "toggle":
                rs.is_active = not rs.is_active
                rs.save(update_fields=["is_active"])
                return JsonResponse({"success": True, "is_active": rs.is_active,
                                     "message": f"Rule set '{rs.rule_set_code}' {'activated' if rs.is_active else 'deactivated'}."})

            rs.rule_set_code = body.get("rule_set_code", rs.rule_set_code).strip()
            rs.rule_set_name = body.get("rule_set_name", rs.rule_set_name).strip()
            rs.domain_code = body.get("domain_code", rs.domain_code).strip()
            rs.schema_code = body.get("schema_code", rs.schema_code).strip()
            rs.validation_type = body.get("validation_type", rs.validation_type)
            rs.priority = int(body.get("priority", rs.priority))
            rs.description = body.get("description", rs.description)
            rs.is_active = bool(body.get("is_active", rs.is_active))
            rs.save()
            return JsonResponse({"success": True, "message": f"Rule set '{rs.rule_set_code}' updated."})
        except Exception as exc:
            logger.exception("api_config_ruleset_detail POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ---------------------------------------------------------------------------
# AJAX API -- Products (HVAC catalog)
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("procurement.view")
def api_config_products(request):
    """List (GET) or Create (POST) Product entries."""
    from apps.procurement.models import Product
    from django.http import JsonResponse

    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        qs = Product.objects.all().order_by("manufacturer", "system_type", "capacity_kw")
        if q:
            qs = qs.filter(
                Q(manufacturer__icontains=q) | Q(product_name__icontains=q) | Q(sku__icontains=q)
            )
        items = [
            {
                "id": p.pk,
                "sku": p.sku,
                "manufacturer": p.manufacturer,
                "product_name": p.product_name,
                "system_type": p.system_type,
                "system_type_display": p.get_system_type_display(),
                "capacity_kw": float(p.capacity_kw),
                "power_input_kw": float(p.power_input_kw),
                "sound_level_db_full_load": p.sound_level_db_full_load,
                "refrigerant_type": p.refrigerant_type,
                "warranty_months": p.warranty_months,
                "cop_rating": float(p.cop_rating) if p.cop_rating is not None else None,
                "seer_rating": float(p.seer_rating) if p.seer_rating is not None else None,
                "weight_kg": p.weight_kg,
                "is_active": p.is_active,
            }
            for p in qs
        ]
        return JsonResponse({"items": items, "total": len(items)})

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            prod = Product.objects.create(
                sku=body.get("sku", "").strip(),
                manufacturer=body.get("manufacturer", "").strip(),
                product_name=body.get("product_name", "").strip(),
                system_type=body.get("system_type", "SPLIT_AC"),
                capacity_kw=float(body.get("capacity_kw", 0)),
                power_input_kw=float(body.get("power_input_kw", 0)),
                sound_level_db_full_load=int(body.get("sound_level_db_full_load", 50)),
                sound_level_db_part_load=int(body.get("sound_level_db_part_load") or 0) or None,
                refrigerant_type=body.get("refrigerant_type", ""),
                warranty_months=int(body.get("warranty_months", 12)),
                cop_rating=float(body.get("cop_rating")) if body.get("cop_rating") else None,
                seer_rating=float(body.get("seer_rating")) if body.get("seer_rating") else None,
                weight_kg=int(body.get("weight_kg")) if body.get("weight_kg") else None,
                installation_support_required=bool(body.get("installation_support_required", False)),
                is_active=bool(body.get("is_active", True)),
            )
            return JsonResponse({"success": True, "id": prod.pk,
                                 "message": f"Product '{prod.sku}' created."})
        except Exception as exc:
            logger.exception("api_config_products POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@permission_required_code("procurement.view")
def api_config_product_detail(request, pk):
    """Get, Update, or Delete a Product."""
    from apps.procurement.models import Product
    from django.http import JsonResponse

    try:
        prod = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return JsonResponse({"success": False, "message": "Product not found."}, status=404)

    if request.method == "GET":
        return JsonResponse({
            "id": prod.pk,
            "sku": prod.sku,
            "manufacturer": prod.manufacturer,
            "product_name": prod.product_name,
            "system_type": prod.system_type,
            "capacity_kw": float(prod.capacity_kw),
            "power_input_kw": float(prod.power_input_kw),
            "sound_level_db_full_load": prod.sound_level_db_full_load,
            "sound_level_db_part_load": prod.sound_level_db_part_load,
            "refrigerant_type": prod.refrigerant_type,
            "warranty_months": prod.warranty_months,
            "cop_rating": float(prod.cop_rating) if prod.cop_rating is not None else "",
            "seer_rating": float(prod.seer_rating) if prod.seer_rating is not None else "",
            "weight_kg": prod.weight_kg or "",
            "installation_support_required": prod.installation_support_required,
            "is_active": prod.is_active,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            action = body.get("_action", "update")

            if action == "delete":
                name = prod.sku
                prod.delete()
                return JsonResponse({"success": True, "message": f"Product '{name}' deleted."})

            if action == "toggle":
                prod.is_active = not prod.is_active
                prod.save(update_fields=["is_active"])
                return JsonResponse({"success": True, "is_active": prod.is_active,
                                     "message": f"Product '{prod.sku}' {'activated' if prod.is_active else 'deactivated'}."})

            prod.sku = body.get("sku", prod.sku).strip()
            prod.manufacturer = body.get("manufacturer", prod.manufacturer).strip()
            prod.product_name = body.get("product_name", prod.product_name).strip()
            prod.system_type = body.get("system_type", prod.system_type)
            prod.capacity_kw = float(body.get("capacity_kw", prod.capacity_kw))
            prod.power_input_kw = float(body.get("power_input_kw", prod.power_input_kw))
            prod.sound_level_db_full_load = int(body.get("sound_level_db_full_load", prod.sound_level_db_full_load))
            snd_part = body.get("sound_level_db_part_load")
            prod.sound_level_db_part_load = int(snd_part) if snd_part else None
            prod.refrigerant_type = body.get("refrigerant_type", prod.refrigerant_type)
            prod.warranty_months = int(body.get("warranty_months", prod.warranty_months))
            cop = body.get("cop_rating")
            prod.cop_rating = float(cop) if cop else None
            seer = body.get("seer_rating")
            prod.seer_rating = float(seer) if seer else None
            wt = body.get("weight_kg")
            prod.weight_kg = int(wt) if wt else None
            prod.installation_support_required = bool(body.get("installation_support_required", prod.installation_support_required))
            prod.is_active = bool(body.get("is_active", prod.is_active))
            prod.save()
            return JsonResponse({"success": True, "message": f"Product '{prod.sku}' updated."})
        except Exception as exc:
            logger.exception("api_config_product_detail POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ---------------------------------------------------------------------------
# AJAX API -- Vendors (HVAC vendor master)
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("procurement.view")
def api_config_vendors(request):
    """List (GET) or Create (POST) Vendor entries."""
    from apps.procurement.models import Vendor
    from django.http import JsonResponse

    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        qs = Vendor.objects.all().order_by("vendor_name")
        if q:
            qs = qs.filter(
                Q(vendor_name__icontains=q) | Q(country__icontains=q) | Q(city__icontains=q)
            )
        items = [
            {
                "id": v.pk,
                "vendor_name": v.vendor_name,
                "country": v.country,
                "city": v.city,
                "contact_email": v.contact_email,
                "contact_phone": v.contact_phone,
                "preferred_vendor": v.preferred_vendor,
                "reliability_score": float(v.reliability_score),
                "average_lead_time_days": v.average_lead_time_days,
                "payment_terms": v.payment_terms,
                "bulk_discount_available": v.bulk_discount_available,
                "rush_order_capable": v.rush_order_capable,
                "on_time_delivery_pct": float(v.on_time_delivery_pct),
                "total_purchases": v.total_purchases,
                "notes": v.notes,
                "is_active": v.is_active,
            }
            for v in qs
        ]
        return JsonResponse({"items": items, "total": len(items)})

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            vend = Vendor.objects.create(
                vendor_name=body.get("vendor_name", "").strip(),
                country=body.get("country", "").strip(),
                city=body.get("city", "").strip(),
                address=body.get("address", ""),
                contact_email=body.get("contact_email", ""),
                contact_phone=body.get("contact_phone", ""),
                preferred_vendor=bool(body.get("preferred_vendor", False)),
                reliability_score=float(body.get("reliability_score", 3.5)),
                average_lead_time_days=int(body.get("average_lead_time_days", 7)),
                payment_terms=body.get("payment_terms", ""),
                bulk_discount_available=bool(body.get("bulk_discount_available", False)),
                rush_order_capable=bool(body.get("rush_order_capable", False)),
                on_time_delivery_pct=float(body.get("on_time_delivery_pct", 95.0)),
                notes=body.get("notes", ""),
                is_active=bool(body.get("is_active", True)),
            )
            return JsonResponse({"success": True, "id": vend.pk,
                                 "message": f"Vendor '{vend.vendor_name}' created."})
        except Exception as exc:
            logger.exception("api_config_vendors POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@permission_required_code("procurement.view")
def api_config_vendor_detail(request, pk):
    """Get, Update, or Delete a Vendor."""
    from apps.procurement.models import Vendor
    from django.http import JsonResponse

    try:
        vend = Vendor.objects.get(pk=pk)
    except Vendor.DoesNotExist:
        return JsonResponse({"success": False, "message": "Vendor not found."}, status=404)

    if request.method == "GET":
        return JsonResponse({
            "id": vend.pk,
            "vendor_name": vend.vendor_name,
            "country": vend.country,
            "city": vend.city,
            "address": vend.address,
            "contact_email": vend.contact_email,
            "contact_phone": vend.contact_phone,
            "preferred_vendor": vend.preferred_vendor,
            "reliability_score": float(vend.reliability_score),
            "average_lead_time_days": vend.average_lead_time_days,
            "payment_terms": vend.payment_terms,
            "bulk_discount_available": vend.bulk_discount_available,
            "rush_order_capable": vend.rush_order_capable,
            "on_time_delivery_pct": float(vend.on_time_delivery_pct),
            "notes": vend.notes,
            "is_active": vend.is_active,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            action = body.get("_action", "update")

            if action == "delete":
                name = vend.vendor_name
                vend.delete()
                return JsonResponse({"success": True, "message": f"Vendor '{name}' deleted."})

            if action == "toggle":
                vend.is_active = not vend.is_active
                vend.save(update_fields=["is_active"])
                return JsonResponse({"success": True, "is_active": vend.is_active,
                                     "message": f"Vendor '{vend.vendor_name}' {'activated' if vend.is_active else 'deactivated'}."})

            vend.vendor_name = body.get("vendor_name", vend.vendor_name).strip()
            vend.country = body.get("country", vend.country).strip()
            vend.city = body.get("city", vend.city).strip()
            vend.address = body.get("address", vend.address)
            vend.contact_email = body.get("contact_email", vend.contact_email)
            vend.contact_phone = body.get("contact_phone", vend.contact_phone)
            vend.preferred_vendor = bool(body.get("preferred_vendor", vend.preferred_vendor))
            vend.reliability_score = float(body.get("reliability_score", vend.reliability_score))
            vend.average_lead_time_days = int(body.get("average_lead_time_days", vend.average_lead_time_days))
            vend.payment_terms = body.get("payment_terms", vend.payment_terms)
            vend.bulk_discount_available = bool(body.get("bulk_discount_available", vend.bulk_discount_available))
            vend.rush_order_capable = bool(body.get("rush_order_capable", vend.rush_order_capable))
            vend.on_time_delivery_pct = float(body.get("on_time_delivery_pct", float(vend.on_time_delivery_pct)))
            vend.notes = body.get("notes", vend.notes)
            vend.is_active = bool(body.get("is_active", vend.is_active))
            vend.save()
            return JsonResponse({"success": True, "message": f"Vendor '{vend.vendor_name}' updated."})
        except Exception as exc:
            logger.exception("api_config_vendor_detail POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ---------------------------------------------------------------------------
# AJAX API -- Rooms (facility rooms)
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("procurement.view")
def api_config_rooms(request):
    """List (GET) or Create (POST) Room entries."""
    from apps.procurement.models import Room
    from django.http import JsonResponse

    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        qs = Room.objects.all().order_by("building_name", "floor_number", "room_code")
        if q:
            qs = qs.filter(
                Q(room_code__icontains=q) | Q(building_name__icontains=q) | Q(usage_type__icontains=q)
            )
        items = [
            {
                "id": rm.pk,
                "room_code": rm.room_code,
                "building_name": rm.building_name,
                "floor_number": rm.floor_number,
                "area_sqm": float(rm.area_sqm),
                "ceiling_height_m": float(rm.ceiling_height_m),
                "usage_type": rm.usage_type,
                "usage_type_display": rm.get_usage_type_display(),
                "design_temp_c": float(rm.design_temp_c),
                "temp_tolerance_c": float(rm.temp_tolerance_c),
                "design_cooling_load_kw": float(rm.design_cooling_load_kw),
                "design_humidity_pct": rm.design_humidity_pct,
                "noise_limit_db": rm.noise_limit_db,
                "contact_name": rm.contact_name,
                "contact_email": rm.contact_email,
                "is_active": rm.is_active,
            }
            for rm in qs
        ]
        return JsonResponse({"items": items, "total": len(items)})

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            rm = Room.objects.create(
                room_code=body.get("room_code", "").strip(),
                building_name=body.get("building_name", "").strip(),
                floor_number=int(body.get("floor_number", 0)),
                area_sqm=float(body.get("area_sqm", 50)),
                ceiling_height_m=float(body.get("ceiling_height_m", 3.0)),
                usage_type=body.get("usage_type", "OFFICE"),
                design_temp_c=float(body.get("design_temp_c", 22.0)),
                temp_tolerance_c=float(body.get("temp_tolerance_c", 1.0)),
                design_cooling_load_kw=float(body.get("design_cooling_load_kw", 5.0)),
                design_humidity_pct=int(body.get("design_humidity_pct")) if body.get("design_humidity_pct") else None,
                noise_limit_db=int(body.get("noise_limit_db")) if body.get("noise_limit_db") else None,
                location_description=body.get("location_description", ""),
                contact_name=body.get("contact_name", ""),
                contact_email=body.get("contact_email", ""),
                is_active=bool(body.get("is_active", True)),
            )
            return JsonResponse({"success": True, "id": rm.pk,
                                 "message": f"Room '{rm.room_code}' created."})
        except Exception as exc:
            logger.exception("api_config_rooms POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@permission_required_code("procurement.view")
def api_config_room_detail(request, pk):
    """Get, Update, or Delete a Room."""
    from apps.procurement.models import Room
    from django.http import JsonResponse

    try:
        rm = Room.objects.get(pk=pk)
    except Room.DoesNotExist:
        return JsonResponse({"success": False, "message": "Room not found."}, status=404)

    if request.method == "GET":
        return JsonResponse({
            "id": rm.pk,
            "room_code": rm.room_code,
            "building_name": rm.building_name,
            "floor_number": rm.floor_number,
            "area_sqm": float(rm.area_sqm),
            "ceiling_height_m": float(rm.ceiling_height_m),
            "usage_type": rm.usage_type,
            "design_temp_c": float(rm.design_temp_c),
            "temp_tolerance_c": float(rm.temp_tolerance_c),
            "design_cooling_load_kw": float(rm.design_cooling_load_kw),
            "design_humidity_pct": rm.design_humidity_pct or "",
            "noise_limit_db": rm.noise_limit_db or "",
            "location_description": rm.location_description,
            "contact_name": rm.contact_name,
            "contact_email": rm.contact_email,
            "is_active": rm.is_active,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            action = body.get("_action", "update")

            if action == "delete":
                code = rm.room_code
                rm.delete()
                return JsonResponse({"success": True, "message": f"Room '{code}' deleted."})

            if action == "toggle":
                rm.is_active = not rm.is_active
                rm.save(update_fields=["is_active"])
                return JsonResponse({"success": True, "is_active": rm.is_active,
                                     "message": f"Room '{rm.room_code}' {'activated' if rm.is_active else 'deactivated'}."})

            rm.room_code = body.get("room_code", rm.room_code).strip()
            rm.building_name = body.get("building_name", rm.building_name).strip()
            rm.floor_number = int(body.get("floor_number", rm.floor_number))
            rm.area_sqm = float(body.get("area_sqm", float(rm.area_sqm)))
            rm.ceiling_height_m = float(body.get("ceiling_height_m", float(rm.ceiling_height_m)))
            rm.usage_type = body.get("usage_type", rm.usage_type)
            rm.design_temp_c = float(body.get("design_temp_c", float(rm.design_temp_c)))
            rm.temp_tolerance_c = float(body.get("temp_tolerance_c", float(rm.temp_tolerance_c)))
            rm.design_cooling_load_kw = float(body.get("design_cooling_load_kw", float(rm.design_cooling_load_kw)))
            hum = body.get("design_humidity_pct")
            rm.design_humidity_pct = int(hum) if hum else None
            nse = body.get("noise_limit_db")
            rm.noise_limit_db = int(nse) if nse else None
            rm.location_description = body.get("location_description", rm.location_description)
            rm.contact_name = body.get("contact_name", rm.contact_name)
            rm.contact_email = body.get("contact_email", rm.contact_email)
            rm.is_active = bool(body.get("is_active", rm.is_active))
            rm.save()
            return JsonResponse({"success": True, "message": f"Room '{rm.room_code}' updated."})
        except Exception as exc:
            logger.exception("api_config_room_detail POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ---------------------------------------------------------------------------
# AJAX API -- HVAC Recommendation Rules
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("procurement.view")
def api_config_hvacrules(request):
    """List (GET) or Create (POST) HVACRecommendationRule entries."""
    from apps.procurement.models import HVACRecommendationRule
    from django.http import JsonResponse

    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        qs = HVACRecommendationRule.objects.all().order_by("priority", "rule_code")
        if q:
            qs = qs.filter(
                Q(rule_code__icontains=q) | Q(rule_name__icontains=q)
                | Q(recommended_system__icontains=q) | Q(rationale__icontains=q)
            )

        def _fmt(r):
            area_parts = []
            if r.area_sq_ft_min is not None:
                area_parts.append(f">= {r.area_sq_ft_min:,.0f}")
            if r.area_sq_ft_max is not None:
                area_parts.append(f"< {r.area_sq_ft_max:,.0f}")
            area_display = " & ".join(area_parts) if area_parts else "Any"
            temp_display = f">= {r.ambient_temp_min_c} C" if r.ambient_temp_min_c is not None else "Any"
            return {
                "id": r.pk,
                "rule_code": r.rule_code,
                "rule_name": r.rule_name,
                "store_type_filter": r.store_type_filter,
                "store_type_display": r.get_store_type_filter_display() if r.store_type_filter else "Any",
                "area_sq_ft_min": r.area_sq_ft_min,
                "area_sq_ft_max": r.area_sq_ft_max,
                "area_display": area_display,
                "ambient_temp_min_c": r.ambient_temp_min_c,
                "temp_display": temp_display,
                "budget_level_filter": r.budget_level_filter,
                "budget_display": r.get_budget_level_filter_display() if r.budget_level_filter else "Any",
                "energy_priority_filter": r.energy_priority_filter,
                "energy_priority_display": r.get_energy_priority_filter_display() if r.energy_priority_filter else "Any",
                "recommended_system": r.recommended_system,
                "recommended_system_display": r.get_recommended_system_display(),
                "alternate_system": r.alternate_system,
                "rationale": r.rationale,
                "priority": r.priority,
                "is_active": r.is_active,
                "notes": r.notes,
            }

        items = [_fmt(r) for r in qs]
        return JsonResponse({"items": items, "total": len(items)})

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            rule = HVACRecommendationRule.objects.create(
                rule_code=body.get("rule_code", "").strip(),
                rule_name=body.get("rule_name", "").strip(),
                store_type_filter=body.get("store_type_filter", ""),
                area_sq_ft_min=float(body["area_sq_ft_min"]) if body.get("area_sq_ft_min") not in (None, "") else None,
                area_sq_ft_max=float(body["area_sq_ft_max"]) if body.get("area_sq_ft_max") not in (None, "") else None,
                ambient_temp_min_c=float(body["ambient_temp_min_c"]) if body.get("ambient_temp_min_c") not in (None, "") else None,
                budget_level_filter=body.get("budget_level_filter", ""),
                energy_priority_filter=body.get("energy_priority_filter", ""),
                recommended_system=body.get("recommended_system", ""),
                alternate_system=body.get("alternate_system", ""),
                rationale=body.get("rationale", ""),
                priority=int(body.get("priority", 100)),
                is_active=bool(body.get("is_active", True)),
                notes=body.get("notes", ""),
            )
            return JsonResponse({
                "success": True, "id": rule.pk,
                "message": f"Rule '{rule.rule_code}' created successfully.",
            })
        except Exception as exc:
            logger.exception("api_config_hvacrules POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
@permission_required_code("procurement.view")
def api_config_hvacrule_detail(request, pk):
    """Get, Update, Toggle or Delete a single HVACRecommendationRule."""
    from apps.procurement.models import HVACRecommendationRule
    from django.http import JsonResponse

    try:
        rule = HVACRecommendationRule.objects.get(pk=pk)
    except HVACRecommendationRule.DoesNotExist:
        return JsonResponse({"success": False, "message": "Rule not found."}, status=404)

    if request.method == "GET":
        return JsonResponse({
            "id": rule.pk,
            "rule_code": rule.rule_code,
            "rule_name": rule.rule_name,
            "store_type_filter": rule.store_type_filter,
            "area_sq_ft_min": rule.area_sq_ft_min,
            "area_sq_ft_max": rule.area_sq_ft_max,
            "ambient_temp_min_c": rule.ambient_temp_min_c,
            "budget_level_filter": rule.budget_level_filter,
            "energy_priority_filter": rule.energy_priority_filter,
            "recommended_system": rule.recommended_system,
            "alternate_system": rule.alternate_system,
            "rationale": rule.rationale,
            "priority": rule.priority,
            "is_active": rule.is_active,
            "notes": rule.notes,
        })

    if request.method == "POST":
        try:
            body = json.loads(request.body)
            action = body.get("_action", "update")

            if action == "delete":
                code = rule.rule_code
                rule.delete()
                return JsonResponse({"success": True, "message": f"Rule '{code}' deleted."})

            if action == "toggle":
                rule.is_active = not rule.is_active
                rule.save(update_fields=["is_active"])
                return JsonResponse({"success": True, "is_active": rule.is_active,
                                     "message": f"Rule '{rule.rule_code}' {'activated' if rule.is_active else 'deactivated'}."})

            rule.rule_code = body.get("rule_code", rule.rule_code).strip()
            rule.rule_name = body.get("rule_name", rule.rule_name).strip()
            rule.store_type_filter = body.get("store_type_filter", rule.store_type_filter)
            rule.area_sq_ft_min = float(body["area_sq_ft_min"]) if body.get("area_sq_ft_min") not in (None, "") else None
            rule.area_sq_ft_max = float(body["area_sq_ft_max"]) if body.get("area_sq_ft_max") not in (None, "") else None
            rule.ambient_temp_min_c = float(body["ambient_temp_min_c"]) if body.get("ambient_temp_min_c") not in (None, "") else None
            rule.budget_level_filter = body.get("budget_level_filter", rule.budget_level_filter)
            rule.energy_priority_filter = body.get("energy_priority_filter", rule.energy_priority_filter)
            rule.recommended_system = body.get("recommended_system", rule.recommended_system)
            rule.alternate_system = body.get("alternate_system", rule.alternate_system)
            rule.rationale = body.get("rationale", rule.rationale)
            rule.priority = int(body.get("priority", rule.priority))
            rule.is_active = bool(body.get("is_active", rule.is_active))
            rule.notes = body.get("notes", rule.notes)
            rule.save()
            return JsonResponse({"success": True, "message": f"Rule '{rule.rule_code}' updated."})
        except Exception as exc:
            logger.exception("api_config_hvacrule_detail POST failed: %s", exc)
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    return JsonResponse({"error": "Method not allowed"}, status=405)


# ---------------------------------------------------------------------------
# External Suggestions API
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def api_external_suggestions(request, pk):
    """Return AI-generated market intelligence with citations for this HVAC request.

    GET /procurement/<pk>/external-suggestions/
    Calls the LLM to rephrase the request, fetch market-relevant product suggestions,
    and return structured tabular data with citations.
    """
    proc_request = get_object_or_404(ProcurementRequest, pk=pk)

    # ---- Gather all request attributes ----
    attributes = list(
        ProcurementRequestAttribute.objects
        .filter(request=proc_request)
        .values("attribute_code", "attribute_label", "value_text", "value_number")
    )
    attr_lines = []
    for a in attributes:
        val = a["value_text"] or (str(a["value_number"]) if a["value_number"] else "")
        if val:
            attr_lines.append(f"  - {a['attribute_label']}: {val}")
    attrs_block = "\n".join(attr_lines) if attr_lines else "  (no attributes recorded)"

    # ---- Gather recommendation if present ----
    recommendation = (
        RecommendationResult.objects
        .filter(run__request=proc_request)
        .order_by("-created_at")
        .first()
    )
    rec_block = "(none yet)"
    system_code = ""
    system_name = ""
    if recommendation:
        payload = recommendation.output_payload_json or {}
        system_code = payload.get("system_type_code", "")
        details = recommendation.reasoning_details_json or payload.get("reasoning_details", {})
        system_name = (
            details.get("system_type", {}).get("name", "")
            if isinstance(details, dict) else ""
        ) or system_code.replace("_", " ").title()
        rec_block = (
            f"Recommended System: {system_name} ({system_code})\n"
            f"Confidence: {int((recommendation.confidence_score or 0) * 100)}%\n"
            f"Compliance: {recommendation.compliance_status or 'N/A'}"
        )

    # ---- Build the LLM prompt ----
    SYSTEM_PROMPT = (
        "You are a senior HVAC market intelligence analyst specializing in commercial "
        "and retail HVAC systems for the GCC/Middle East region. "
        "You have deep knowledge of manufacturer product lines (Daikin, Carrier, Trane, "
        "York, Mitsubishi Electric, LG, Samsung, Gree, Midea, Voltas), pricing in AED, "
        "regional standards (ESMA, ASHRAE, Cooling India), and distributor availability. "
        "Respond ONLY with a single valid JSON object and nothing else."
    )

    USER_PROMPT = f"""Analyze the following HVAC procurement request and generate a comprehensive
market intelligence report with at least 5 product suggestions.

=== PROCUREMENT REQUEST ===
Title: {proc_request.title}
Description: {proc_request.description or '(not provided)'}
Country: {proc_request.geography_country or 'UAE'}
City: {proc_request.geography_city or ''}
Priority: {proc_request.priority}
Currency: {proc_request.currency or 'AED'}

=== REQUEST ATTRIBUTES ===
{attrs_block}

=== INTERNAL AI RECOMMENDATION ===
{rec_block}

Return a JSON object with this exact structure:
{{
  "rephrased_query": "<one sentence professional market query summarising this need>",
  "ai_summary": "<2-3 sentence executive summary of market context and key considerations>",
  "market_context": "<brief note on current market availability, lead times, or pricing trends in this region>",
  "suggestions": [
    {{
      "rank": 1,
      "product_name": "<full product/series name>",
      "manufacturer": "<brand name>",
      "model_code": "<specific model or series code>",
      "system_type": "<e.g. VRF, Chilled Water AHU, Split DX, Cassette, Rooftop>",
      "cooling_capacity": "<e.g. 8 TR - 12 TR>",
      "cop_eer": "<e.g. COP 3.8 / EER 13.0>",
      "price_range_aed": "<e.g. 45,000 - 70,000 AED supply & install>",
      "market_availability": "<availability note for this region>",
      "key_benefits": ["benefit 1", "benefit 2", "benefit 3"],
      "limitations": ["limitation 1", "limitation 2"],
      "fit_score": 88,
      "fit_rationale": "<one sentence why this fits or does not fit this request>",
      "standards_compliance": ["ASHRAE 90.1", "ESMA UAE"],
      "citation_url": "<manufacturer product page URL>",
      "citation_source": "<source name e.g. Daikin Middle East>",
      "category": "<MANUFACTURER or DISTRIBUTOR>"
    }}
  ]
}}
Provide 5 to 7 suggestions ranked by fit_score descending. Use only real product lines.
"""

    # ---- Call the LLM ----
    try:
        llm = LLMClient(temperature=0.2, max_tokens=3000)
        messages = [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(role="user", content=USER_PROMPT),
        ]
        resp = llm.chat(messages, response_format={"type": "json_object"})
        raw_content = (resp.content or "").strip()
        data = json.loads(raw_content)
    except Exception as exc:
        logger.warning("api_external_suggestions LLM call failed for pk=%s: %s", pk, exc)
        # Graceful fallback: return minimal static response
        return JsonResponse({
            "system_code": system_code,
            "system_name": system_name,
            "rephrased_query": f"Market data for {system_name or 'HVAC system'} in {proc_request.geography_country or 'UAE'}",
            "ai_summary": "AI market analysis is temporarily unavailable. Please try again shortly.",
            "market_context": "",
            "suggestions": [],
            "error": str(exc),
        }, status=200)

    # ---- Normalise / enrich suggestions ----
    _ICONS = {
        "MANUFACTURER":   "bi-building",
        "DISTRIBUTOR":    "bi-truck",
        "REGULATOR":      "bi-shield-check",
        "STANDARDS_BODY": "bi-patch-check",
        "OTHER":          "bi-link-45deg",
    }
    suggestions = data.get("suggestions", [])
    for s in suggestions:
        cat = s.get("category", "MANUFACTURER").upper()
        s["icon_class"] = _ICONS.get(cat, "bi-building")
        # Clamp fit_score
        try:
            s["fit_score"] = max(0, min(100, int(s.get("fit_score", 0))))
        except (TypeError, ValueError):
            s["fit_score"] = 0

    return JsonResponse({
        "system_code": system_code,
        "system_name": system_name,
        "rephrased_query": data.get("rephrased_query", ""),
        "ai_summary": data.get("ai_summary", ""),
        "market_context": data.get("market_context", ""),
        "suggestions": suggestions,
    })

