"""Reconciliation template views (server-side rendered)."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from apps.agents.models import AgentRecommendation
from apps.core.enums import MatchStatus
from apps.reconciliation.models import ReconciliationResult


@login_required
def result_list(request):
    qs = (
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .prefetch_related("exceptions")
        .order_by("-created_at")
    )
    match_status = request.GET.get("match_status")
    if match_status:
        qs = qs.filter(match_status=match_status)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "reconciliation/result_list.html", {
        "results": page_obj,
        "page_obj": page_obj,
        "match_status_choices": MatchStatus.choices,
    })


@login_required
def result_detail(request, pk):
    result = get_object_or_404(
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .prefetch_related("exceptions", "line_results"),
        pk=pk,
    )
    recommendations = AgentRecommendation.objects.filter(reconciliation_result=result).order_by("-confidence")
    return render(request, "reconciliation/result_detail.html", {
        "result": result,
        "exceptions": result.exceptions.all(),
        "line_results": result.line_results.all(),
        "recommendations": recommendations,
    })
