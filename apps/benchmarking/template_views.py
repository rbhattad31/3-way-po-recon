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
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    Geography,
    LineCategory,
    ScopeType,
    VarianceStatus,
)
from apps.benchmarking.services.benchmark_service import BenchmarkEngine
from apps.benchmarking.services.export_service import ExportService

try:
    from apps.procurement.models import GeneratedRFQ as _GeneratedRFQ
except ImportError:
    _GeneratedRFQ = None

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


def _get_generated_rfqs():
    """Return list of all generated RFQ records for the create-request dropdown."""
    if _GeneratedRFQ is None:
        return []
    try:
        return list(
            _GeneratedRFQ.objects
            .select_related("request")
            .order_by("-created_at")
            .values("rfq_ref", "system_label", "request__title")
        )
    except Exception:
        return []


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
        rfq_source = request.POST.get("rfq_source", "manual")
        if rfq_source == "system":
            quotation_ref = request.POST.get("rfq_system_ref", "").strip()
        else:
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
                posted=request.POST,
                page_title="New Benchmarking Request",
                generated_rfq_list=_get_generated_rfqs(),
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
        generated_rfq_list=_get_generated_rfqs(),
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
    """Benchmark Configurations -- Category Master, Benchmark Table, Variance Thresholds."""
    stats = {
        "category_total": len(LineCategory.CHOICES),
        "category_active": len(LineCategory.CHOICES),
        "corridor_total": BenchmarkCorridorRule.objects.count(),
        "corridor_active": BenchmarkCorridorRule.objects.filter(is_active=True).count(),
        "threshold_total": 4,
        "threshold_active": 4,
    }
    scope_choices_with_all = ScopeType.CHOICES + [("ALL", "All Scopes")]
    geo_choices_with_all = Geography.CHOICES + [("ALL", "All Geographies")]
    ctx = _base_ctx(
        stats=stats,
        scope_choices_with_all=scope_choices_with_all,
        geo_choices_with_all=geo_choices_with_all,
        page_title="Benchmark Configurations",
        active_menu="configurations",
        active_tab="categories",
    )
    return render(request, "benchmarking/configurations.html", ctx)


# --------------------------------------------------------------------------- #
# 8a. Configurations -- Category Master API
# --------------------------------------------------------------------------- #

_CAT_DESCRIPTIONS = {
    "EQUIPMENT":     "Main HVAC units: VRF/VRV, Chillers, Split ACs, Packaged Units, FCUs, AHUs",
    "CONTROLS":      "BMS/DDC controls, control panels, cabling, sensors, actuators",
    "DUCTING":       "GI ductwork, flexible ducts, grilles, diffusers, louvres",
    "INSULATION":    "Pipe insulation (Armaflex/NBR), duct insulation (glass wool, foam)",
    "ACCESSORIES":   "Pipes, fittings, valves, dampers, supports, hangers, drain trays",
    "INSTALLATION":  "Labour for mechanical installation, fix & fit, pipework, electrical works",
    "TC":            "Testing, balancing, commissioning, startup, handover",
    "UNCATEGORIZED": "Items not yet classified into a specific category",
}


@login_required
def api_bench_categories(request):
    """Return all LineCategory enum values with active corridor rule counts."""
    from django.db.models import Count
    q = request.GET.get("q", "").lower()
    counts = dict(
        BenchmarkCorridorRule.objects.filter(is_active=True)
        .values_list("category")
        .annotate(cnt=Count("id"))
        .values_list("category", "cnt")
    )
    items = []
    for code, label in LineCategory.CHOICES:
        if q and q not in code.lower() and q not in label.lower():
            continue
        sample_kw_qs = list(
            BenchmarkCorridorRule.objects.filter(category=code, is_active=True)
            .exclude(keywords="")
            .values_list("keywords", flat=True)[:3]
        )
        # flatten first ~6 keywords
        all_kw = []
        for kws in sample_kw_qs:
            all_kw.extend([k.strip() for k in kws.split(",") if k.strip()])
        sample_keywords = ", ".join(all_kw[:6]) if all_kw else ""
        items.append({
            "code": code,
            "label": label,
            "description": _CAT_DESCRIPTIONS.get(code, ""),
            "rule_count": counts.get(code, 0),
            "sample_keywords": sample_keywords,
        })
    return JsonResponse({"items": items})


# --------------------------------------------------------------------------- #
# 8b. Configurations -- Benchmark Table API (CRUD)
# --------------------------------------------------------------------------- #

def _corridor_to_dict(r):
    # Build display labels manually (model uses plain str constants, not TextChoices)
    cat_map = dict(LineCategory.CHOICES)
    geo_map = dict(Geography.CHOICES + [("ALL", "All Geographies")])
    scope_map = dict(ScopeType.CHOICES + [("ALL", "All Scopes")])
    return {
        "id": r.pk,
        "rule_code": r.rule_code,
        "name": r.name,
        "category": r.category,
        "category_display": cat_map.get(r.category, r.category),
        "geography": r.geography,
        "geography_display": geo_map.get(r.geography, r.geography),
        "scope_type": r.scope_type,
        "scope_display": scope_map.get(r.scope_type, r.scope_type),
        "uom": r.uom,
        "min_rate": str(r.min_rate),
        "mid_rate": str(r.mid_rate),
        "max_rate": str(r.max_rate),
        "currency": r.currency,
        "keywords": r.keywords,
        "notes": r.notes,
        "priority": r.priority,
        "is_active": r.is_active,
    }


@login_required
def api_bench_corridors(request):
    """GET: list corridor rules. POST (JSON): create a new rule."""
    import json as _json
    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
        try:
            rule = BenchmarkCorridorRule.objects.create(
                rule_code=body["rule_code"].strip(),
                name=body["name"].strip(),
                category=body["category"],
                scope_type=body.get("scope_type", "ALL"),
                geography=body.get("geography", "ALL"),
                uom=body.get("uom", "").strip(),
                min_rate=body.get("min_rate") or 0,
                mid_rate=body.get("mid_rate") or 0,
                max_rate=body.get("max_rate") or 0,
                currency=(body.get("currency") or "AED").strip(),
                keywords=body.get("keywords", "").strip(),
                notes=body.get("notes", "").strip(),
                priority=int(body.get("priority") or 100),
                is_active=bool(body.get("is_active", True)),
                created_by=request.user,
            )
            return JsonResponse({"success": True, "message": "Corridor rule created.", "id": rule.pk})
        except Exception as exc:
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    # GET -- list with optional filters
    q = request.GET.get("q", "").lower()
    cat_filter = request.GET.get("category", "")
    geo_filter = request.GET.get("geography", "")
    from django.db.models import Q
    qs = BenchmarkCorridorRule.objects.all().order_by("category", "geography", "priority")
    if q:
        qs = qs.filter(Q(rule_code__icontains=q) | Q(name__icontains=q) | Q(keywords__icontains=q))
    if cat_filter:
        qs = qs.filter(category=cat_filter)
    if geo_filter:
        qs = qs.filter(geography=geo_filter)
    return JsonResponse({"items": [_corridor_to_dict(r) for r in qs]})


@login_required
def api_bench_corridor_detail(request, pk):
    """GET: single rule detail. POST (JSON): update / toggle / delete."""
    import json as _json
    rule = get_object_or_404(BenchmarkCorridorRule, pk=pk)
    if request.method == "GET":
        return JsonResponse(_corridor_to_dict(rule))
    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
        action = body.get("_action", "update")
        if action == "toggle":
            rule.is_active = not rule.is_active
            rule.save(update_fields=["is_active"])
            return JsonResponse({"success": True, "message": f"Rule {'enabled' if rule.is_active else 'disabled'}."})
        if action == "delete":
            rule.is_active = False
            rule.save(update_fields=["is_active"])
            return JsonResponse({"success": True, "message": f"Rule '{rule.rule_code}' deactivated."})
        if action == "update":
            try:
                rule.rule_code = (body.get("rule_code") or rule.rule_code).strip()
                rule.name = (body.get("name") or rule.name).strip()
                rule.category = body.get("category", rule.category)
                rule.scope_type = body.get("scope_type", rule.scope_type)
                rule.geography = body.get("geography", rule.geography)
                rule.uom = (body.get("uom") or "").strip()
                rule.min_rate = body.get("min_rate") or rule.min_rate
                rule.mid_rate = body.get("mid_rate") or rule.mid_rate
                rule.max_rate = body.get("max_rate") or rule.max_rate
                rule.currency = ((body.get("currency") or "AED")).strip()
                rule.keywords = (body.get("keywords") or "").strip()
                rule.notes = (body.get("notes") or "").strip()
                rule.priority = int(body.get("priority") or rule.priority)
                rule.is_active = bool(body.get("is_active", rule.is_active))
                rule.updated_by = request.user
                rule.save()
                return JsonResponse({"success": True, "message": "Corridor rule updated."})
            except Exception as exc:
                return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return JsonResponse({"error": "Method not allowed"}, status=405)


# --------------------------------------------------------------------------- #
# 8c. Configurations -- Variance Thresholds API
# --------------------------------------------------------------------------- #

@login_required
def api_bench_thresholds(request):
    """Return the 4 variance bands with live line-item counts."""
    from django.db.models import Count
    counts = dict(
        BenchmarkLineItem.objects.values_list("variance_status")
        .annotate(cnt=Count("id"))
        .values_list("variance_status", "cnt")
    )
    bands = [
        {
            "code": "WITHIN_RANGE",
            "label": "Within Range",
            "description": "Quoted rate is within benchmark corridor",
            "range": "< 5% above mid",
            "color": "success",
            "count": counts.get("WITHIN_RANGE", 0),
        },
        {
            "code": "MODERATE",
            "label": "Moderate Variance",
            "description": "Quoted rate slightly exceeds benchmark",
            "range": "5% - 15% above mid",
            "color": "warning",
            "count": counts.get("MODERATE", 0),
        },
        {
            "code": "HIGH",
            "label": "High Variance",
            "description": "Quoted rate significantly exceeds benchmark",
            "range": "> 15% above mid",
            "color": "danger",
            "count": counts.get("HIGH", 0),
        },
        {
            "code": "NEEDS_REVIEW",
            "label": "Needs Review",
            "description": "No benchmark corridor found for this line item",
            "range": "No benchmark",
            "color": "secondary",
            "count": counts.get("NEEDS_REVIEW", 0),
        },
    ]
    return JsonResponse({"items": bands})


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
