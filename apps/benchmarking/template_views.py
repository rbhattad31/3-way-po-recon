"""
Benchmarking template views.
Covers: All Requests, New Request (upload), Detail/Results, Quotations, Reports, Configurations.
"""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkQuotation,
    BenchmarkRequest,
    Geography,
    LineCategory,
    ScopeType,
)
from apps.benchmarking.services.benchmark_service import BenchmarkEngine
from apps.benchmarking.services.export_service import ExportService

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper context
# --------------------------------------------------------------------------- #

def _base_ctx(**extra):
    ctx = {
        "geography_choices": Geography.CHOICES,
        "scope_type_choices": ScopeType.CHOICES,
        "line_category_choices": LineCategory.CHOICES,
    }
    ctx.update(extra)
    return ctx


# --------------------------------------------------------------------------- #
# 1. All Requests
# --------------------------------------------------------------------------- #

@login_required
def request_list(request):
    """List all benchmarking requests with filters."""
    qs = BenchmarkRequest.objects.filter(is_active=True).order_by("-created_at")

    # Filters
    status_filter = request.GET.get("status", "")
    geography_filter = request.GET.get("geography", "")
    search = request.GET.get("q", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if geography_filter:
        qs = qs.filter(geography=geography_filter)
    if search:
        qs = qs.filter(title__icontains=search)

    # KPI counts
    total_count = BenchmarkRequest.objects.filter(is_active=True).count()
    completed_count = BenchmarkRequest.objects.filter(is_active=True, status="COMPLETED").count()
    processing_count = BenchmarkRequest.objects.filter(is_active=True, status="PROCESSING").count()
    failed_count = BenchmarkRequest.objects.filter(is_active=True, status="FAILED").count()

    ctx = _base_ctx(
        bench_requests=qs,
        total_count=total_count,
        completed_count=completed_count,
        processing_count=processing_count,
        failed_count=failed_count,
        status_filter=status_filter,
        geography_filter=geography_filter,
        search=search,
        status_choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("COMPLETED", "Completed"), ("FAILED", "Failed")],
        page_title="All Benchmarking Requests",
        active_menu="request_list",
    )
    return render(request, "benchmarking/request_list.html", ctx)


# --------------------------------------------------------------------------- #
# 2. New Request (upload form)
# --------------------------------------------------------------------------- #

@login_required
def request_create(request):
    """Create a new benchmarking request and upload quotation PDF."""
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        project_name = request.POST.get("project_name", "").strip()
        geography = request.POST.get("geography", "UAE")
        scope_type = request.POST.get("scope_type", "SITC")
        store_type = request.POST.get("store_type", "").strip()
        supplier_name = request.POST.get("supplier_name", "").strip()
        quotation_ref = request.POST.get("quotation_ref", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        if not title:
            errors.append("Request title is required.")
        if not request.FILES.get("quotation_pdf"):
            errors.append("Quotation PDF is required.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "benchmarking/request_create.html", _base_ctx(
                posted=request.POST, page_title="New Benchmarking Request"
            ))

        # Create request
        bench_request = BenchmarkRequest.objects.create(
            title=title,
            project_name=project_name,
            geography=geography,
            scope_type=scope_type,
            store_type=store_type,
            notes=notes,
            status="PENDING",
            submitted_by=request.user,
            created_by=request.user,
        )

        # Create quotation with uploaded file
        quotation = BenchmarkQuotation.objects.create(
            request=bench_request,
            supplier_name=supplier_name,
            quotation_ref=quotation_ref,
            document=request.FILES["quotation_pdf"],
            created_by=request.user,
        )

        # Run the benchmark pipeline (synchronous for now; can be moved to Celery)
        result = BenchmarkEngine.run(bench_request.pk, user=request.user)
        if result["success"]:
            messages.success(request, "Benchmarking analysis completed successfully.")
        else:
            messages.warning(
                request,
                f"Request saved but analysis failed: {result['error']}. You can reprocess from the detail page."
            )

        return redirect("benchmarking:request_detail", pk=bench_request.pk)

    return render(request, "benchmarking/request_create.html", _base_ctx(
        page_title="New Benchmarking Request",
        active_menu="request_list",
    ))


# --------------------------------------------------------------------------- #
# 3. Request Detail / Results
# --------------------------------------------------------------------------- #

@login_required
def request_detail(request, pk):
    """Detailed results view for a benchmarking request."""
    bench_request = get_object_or_404(BenchmarkRequest, pk=pk, is_active=True)

    # Gather all line items across all quotations
    quotations = bench_request.quotations.filter(is_active=True).prefetch_related("line_items")
    line_items = []
    for q in quotations:
        line_items.extend(q.line_items.filter(is_active=True))

    result = None
    category_summary = {}
    negotiation_notes = []
    try:
        result = bench_request.result
        category_summary = result.category_summary_json or {}
        negotiation_notes = result.negotiation_notes_json or []
    except Exception:
        pass

    # Category filter
    cat_filter = request.GET.get("category", "")
    if cat_filter:
        line_items = [i for i in line_items if i.category == cat_filter]

    # Variance filter
    var_filter = request.GET.get("variance", "")
    if var_filter:
        line_items = [i for i in line_items if i.variance_status == var_filter]

    ctx = _base_ctx(
        bench_request=bench_request,
        quotations=quotations,
        line_items=line_items,
        result=result,
        category_summary=category_summary,
        negotiation_notes=negotiation_notes,
        cat_filter=cat_filter,
        var_filter=var_filter,
        category_choices=LineCategory.CHOICES,
        variance_choices=[
            ("WITHIN_RANGE", "Within Range"),
            ("MODERATE", "Moderate"),
            ("HIGH", "High"),
            ("NEEDS_REVIEW", "Needs Review"),
        ],
        page_title=f"Results: {bench_request.title}",
        active_menu="request_list",
        category_summary_json=json.dumps(category_summary),
    )
    return render(request, "benchmarking/request_detail.html", ctx)


# --------------------------------------------------------------------------- #
# 4. Reprocess (AJAX / POST)
# --------------------------------------------------------------------------- #

@login_required
@require_http_methods(["POST"])
def request_reprocess(request, pk):
    """Re-run the benchmark engine for a request."""
    bench_request = get_object_or_404(BenchmarkRequest, pk=pk, is_active=True)
    result = BenchmarkEngine.run(bench_request.pk, user=request.user)
    if result["success"]:
        messages.success(request, "Reprocessing completed successfully.")
    else:
        messages.error(request, f"Reprocessing failed: {result['error']}")
    return redirect("benchmarking:request_detail", pk=pk)


# --------------------------------------------------------------------------- #
# 5. Export CSV
# --------------------------------------------------------------------------- #

@login_required
def request_export(request, pk):
    """Download CSV export of benchmarking results."""
    bench_request = get_object_or_404(BenchmarkRequest, pk=pk, is_active=True)
    csv_bytes = ExportService.export_request_csv(bench_request)
    slug = bench_request.title.lower().replace(" ", "_")[:40]
    response = HttpResponse(csv_bytes, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="benchmark_{slug}.csv"'
    return response


# --------------------------------------------------------------------------- #
# 6. Quotations list
# --------------------------------------------------------------------------- #

@login_required
def quotation_list(request):
    """List all uploaded quotations across all requests."""
    qs = BenchmarkQuotation.objects.filter(is_active=True).select_related("request").order_by("-created_at")

    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(supplier_name__icontains=search)

    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(extraction_status=status_filter)

    ctx = _base_ctx(
        quotations=qs,
        search=search,
        status_filter=status_filter,
        extraction_status_choices=[("PENDING", "Pending"), ("DONE", "Done"), ("FAILED", "Failed")],
        page_title="All Quotations",
        active_menu="quotation_list",
    )
    return render(request, "benchmarking/quotation_list.html", ctx)


# --------------------------------------------------------------------------- #
# 6b. Quotation Detail
# --------------------------------------------------------------------------- #

@login_required
def quotation_detail(request, pk):
    """Detailed view for a single quotation -- shows all extracted line items."""
    quotation = get_object_or_404(BenchmarkQuotation, pk=pk, is_active=True)
    bench_request = quotation.request

    line_items = quotation.line_items.filter(is_active=True).order_by("line_number")

    # Category filter
    cat_filter = request.GET.get("category", "")
    if cat_filter:
        line_items = line_items.filter(category=cat_filter)

    # Variance filter
    var_filter = request.GET.get("variance", "")
    if var_filter:
        line_items = line_items.filter(variance_status=var_filter)

    ctx = _base_ctx(
        quotation=quotation,
        bench_request=bench_request,
        line_items=line_items,
        cat_filter=cat_filter,
        var_filter=var_filter,
        category_choices=LineCategory.CHOICES,
        variance_choices=[
            ("WITHIN_RANGE", "Within Range"),
            ("MODERATE", "Moderate"),
            ("HIGH", "High"),
            ("NEEDS_REVIEW", "Needs Review"),
        ],
        page_title=f"Quotation: {quotation.supplier_name or 'Unknown'} -- {bench_request.title}",
        active_menu="quotation_list",
    )
    return render(request, "benchmarking/quotation_detail.html", ctx)


# --------------------------------------------------------------------------- #
# 7. Reports
# --------------------------------------------------------------------------- #

@login_required
def reports(request):
    """Reports overview -- aggregated stats across all completed requests."""
    from django.db.models import Avg, Count, Q

    completed_qs = BenchmarkRequest.objects.filter(is_active=True, status="COMPLETED")

    # Counts by status
    total_requests = BenchmarkRequest.objects.filter(is_active=True).count()
    completed_count = completed_qs.count()
    high_variance_count = completed_qs.filter(result__overall_status="HIGH").count() if completed_qs.exists() else 0

    # Counts by geography
    geo_breakdown = (
        BenchmarkRequest.objects.filter(is_active=True)
        .values("geography")
        .annotate(count=Count("id"))
        .order_by("geography")
    )

    # Counts by scope
    scope_breakdown = (
        BenchmarkRequest.objects.filter(is_active=True)
        .values("scope_type")
        .annotate(count=Count("id"))
        .order_by("scope_type")
    )

    # Recent completed requests
    recent = completed_qs.select_related("result").order_by("-updated_at")[:10]

    ctx = _base_ctx(
        total_requests=total_requests,
        completed_count=completed_count,
        high_variance_count=high_variance_count,
        geo_breakdown=list(geo_breakdown),
        scope_breakdown=list(scope_breakdown),
        recent_requests=recent,
        page_title="Benchmarking Reports",
        active_menu="reports",
    )
    return render(request, "benchmarking/reports.html", ctx)


# --------------------------------------------------------------------------- #
# 8. Configurations (Corridor Rules CRUD)
# --------------------------------------------------------------------------- #

@login_required
def configurations(request):
    """Manage BenchmarkCorridorRule records."""
    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create":
            try:
                BenchmarkCorridorRule.objects.create(
                    rule_code=request.POST["rule_code"].strip(),
                    name=request.POST["name"].strip(),
                    category=request.POST["category"],
                    scope_type=request.POST.get("scope_type", "ALL"),
                    geography=request.POST.get("geography", "ALL"),
                    uom=request.POST.get("uom", "").strip(),
                    min_rate=request.POST["min_rate"],
                    mid_rate=request.POST["mid_rate"],
                    max_rate=request.POST["max_rate"],
                    currency=request.POST.get("currency", "AED").strip(),
                    keywords=request.POST.get("keywords", "").strip(),
                    notes=request.POST.get("notes", "").strip(),
                    priority=int(request.POST.get("priority", 100)),
                    created_by=request.user,
                )
                messages.success(request, "Corridor rule created.")
            except Exception as exc:
                messages.error(request, f"Failed to create rule: {exc}")

        elif action == "toggle":
            rule_pk = request.POST.get("rule_pk")
            try:
                rule = BenchmarkCorridorRule.objects.get(pk=rule_pk)
                rule.is_active = not rule.is_active
                rule.updated_by = request.user
                rule.save(update_fields=["is_active", "updated_at", "updated_by"])
                messages.success(request, f"Rule '{rule.rule_code}' {'enabled' if rule.is_active else 'disabled'}.")
            except BenchmarkCorridorRule.DoesNotExist:
                messages.error(request, "Rule not found.")

        elif action == "delete":
            rule_pk = request.POST.get("rule_pk")
            try:
                rule = BenchmarkCorridorRule.objects.get(pk=rule_pk)
                rule.is_active = False
                rule.save(update_fields=["is_active"])
                messages.success(request, f"Rule '{rule.rule_code}' deactivated.")
            except BenchmarkCorridorRule.DoesNotExist:
                messages.error(request, "Rule not found.")

        return redirect("benchmarking:configurations")

    rules = BenchmarkCorridorRule.objects.all().order_by("category", "geography", "priority")
    scope_choices_with_all = ScopeType.CHOICES + [("ALL", "All Scopes")]
    geo_choices_with_all = Geography.CHOICES + [("ALL", "All Geographies")]

    ctx = _base_ctx(
        corridor_rules=rules,
        scope_choices_with_all=scope_choices_with_all,
        geo_choices_with_all=geo_choices_with_all,
        page_title="Benchmark Configurations",
        active_menu="configurations",
    )
    return render(request, "benchmarking/configurations.html", ctx)


# --------------------------------------------------------------------------- #
# 9. Status check API (JSON)
# --------------------------------------------------------------------------- #

@login_required
def request_status(request, pk):
    """Return JSON status of a BenchmarkRequest (for polling)."""
    bench_request = get_object_or_404(BenchmarkRequest, pk=pk, is_active=True)
    return JsonResponse({
        "pk": bench_request.pk,
        "status": bench_request.status,
        "error_message": bench_request.error_message,
    })


# --------------------------------------------------------------------------- #
# 10. Live pricing enrichment via Perplexity
# --------------------------------------------------------------------------- #

@login_required
@require_http_methods(["POST"])
def request_live_enrich(request, pk):
    """
    POST: Fetch live market pricing from Perplexity sonar-pro and re-benchmark
    all line items for this request.  Redirects back to request_detail.
    """
    bench_request = get_object_or_404(BenchmarkRequest, pk=pk, is_active=True)

    if bench_request.status == "PROCESSING":
        messages.warning(request, "Benchmarking is already in progress. Please wait.")
        return redirect("benchmarking:request_detail", pk=pk)

    result = BenchmarkEngine.run_live_enrichment(request_pk=pk, user=request.user)

    if result["success"]:
        messages.success(
            request,
            f"Live pricing updated: {result['enriched']} of {result['total']} lines "
            f"enriched with real market data from Perplexity.",
        )
    else:
        messages.error(
            request,
            f"Live pricing failed: {result.get('error', 'Unknown error')}.",
        )

    return redirect("benchmarking:request_detail", pk=pk)


# --------------------------------------------------------------------------- #
# 11. Live pricing enrichment -- AJAX status/result
# --------------------------------------------------------------------------- #

@login_required
def request_live_enrich_ajax(request, pk):
    """
    POST (AJAX): Run Perplexity live enrichment and return JSON for in-page update.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    bench_request = get_object_or_404(BenchmarkRequest, pk=pk, is_active=True)

    if bench_request.status == "PROCESSING":
        return JsonResponse({"success": False, "error": "Processing already in progress"})

    result = BenchmarkEngine.run_live_enrichment(request_pk=pk, user=request.user)
    return JsonResponse(result)
