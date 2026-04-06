"""Template views for the Procurement Intelligence UI."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render

from apps.auditlog.services import AuditService
from apps.core.permissions import permission_required_code
from apps.core.enums import (
    AnalysisRunType,
    ProcurementRequestStatus,
    ProcurementRequestType,
)
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    ComplianceResult,
    ProcurementRequest,
    ProcurementRequestAttribute,
    RecommendationResult,
    SupplierQuotation,
    ValidationResult,
)

logger = logging.getLogger(__name__)


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
            messages.success(
                request,
                f"HVAC Procurement Request '{proc_request.title}' created successfully.",
            )
            return redirect("procurement:request_workspace", pk=proc_request.pk)
        except Exception as exc:
            logger.exception("hvac_create failed: %s", exc)
            messages.error(request, f"Failed to create HVAC request: {exc}")

    return render(request, "procurement/request_create_hvac.html", {
        "type_choices": ProcurementRequestType.choices,
        "priority_choices": [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")],
    })


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

    return render(request, "procurement/request_workspace.html", {
        "proc_request": proc_request,
        "attributes": attributes,
        "quotations": quotations,
        "runs": runs,
        "recommendation": recommendation,
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

