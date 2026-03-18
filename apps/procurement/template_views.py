"""Template views for the Procurement Intelligence UI."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
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
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Request List
# ---------------------------------------------------------------------------
@login_required
@permission_required_code("procurement.view")
def request_list(request):
    """List all procurement requests with filters."""
    qs = ProcurementRequest.objects.select_related("created_by", "assigned_to").annotate(
        attribute_count=Count("attributes"),
        quotation_count=Count("quotations"),
        run_count=Count("analysis_runs"),
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

    return render(request, "procurement/request_workspace.html", {
        "proc_request": proc_request,
        "attributes": attributes,
        "quotations": quotations,
        "runs": runs,
        "recommendation": recommendation,
        "benchmarks": benchmarks,
        "compliance": compliance,
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

    if run.run_type == AnalysisRunType.RECOMMENDATION:
        recommendation = RecommendationResult.objects.filter(run=run).first()
    elif run.run_type == AnalysisRunType.BENCHMARK:
        benchmarks = BenchmarkResult.objects.filter(run=run).prefetch_related("lines", "quotation")

    compliance = ComplianceResult.objects.filter(run=run).first()

    # Audit events for this run
    audit_events = AuditService.fetch_entity_history("AnalysisRun", run.pk)

    return render(request, "procurement/run_detail.html", {
        "run": run,
        "recommendation": recommendation,
        "benchmarks": benchmarks,
        "compliance": compliance,
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
