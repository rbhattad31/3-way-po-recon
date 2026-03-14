"""Review template views (server-side rendered)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.enums import MatchStatus, ReviewStatus, UserRole
from apps.core.permissions import permission_required_code
from apps.reconciliation.models import ReconciliationResult
from apps.reviews.models import ReviewAssignment
from apps.reviews.services import ReviewWorkflowService


def _scope_for_ap_processor(user, qs):
    """Filter review assignments so AP_PROCESSOR only sees own invoices."""
    if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
        return qs
    from apps.reconciliation.models import ReconciliationConfig
    config = ReconciliationConfig.objects.filter(is_default=True).first()
    if config and config.ap_processor_sees_all_cases:
        return qs
    return qs.filter(
        reconciliation_result__invoice__document_upload__uploaded_by=user
    )


@login_required
def assignment_list(request):
    qs = (
        ReviewAssignment.objects
        .select_related("reconciliation_result", "reconciliation_result__invoice", "assigned_to")
        .order_by("priority", "-created_at")
    )
    qs = _scope_for_ap_processor(request.user, qs)
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Results that need review but have no assignment yet
    assigned_result_ids = ReviewAssignment.objects.values_list("reconciliation_result_id", flat=True)
    unassigned_results = (
        ReconciliationResult.objects
        .filter(match_status=MatchStatus.REQUIRES_REVIEW)
        .exclude(pk__in=assigned_result_ids)
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .order_by("-created_at")
    )

    return render(request, "reviews/assignment_list.html", {
        "assignments": page_obj,
        "page_obj": page_obj,
        "review_status_choices": ReviewStatus.choices,
        "unassigned_results": unassigned_results,
    })


@login_required
@permission_required_code("reviews.assign")
def create_assignments(request):
    """Create review assignments for selected reconciliation results."""
    if request.method != "POST":
        return redirect("reviews:assignment_list")

    result_ids = request.POST.getlist("result_ids")
    if not result_ids:
        messages.warning(request, "No results selected.")
        return redirect("reviews:assignment_list")

    results = ReconciliationResult.objects.filter(pk__in=[int(i) for i in result_ids])
    count = 0
    for result in results:
        if not ReviewAssignment.objects.filter(reconciliation_result=result).exists():
            ReviewWorkflowService.create_assignment(result=result)
            count += 1

    messages.success(request, f"Created {count} review assignment(s).")
    return redirect("reviews:assignment_list")


@login_required
def assignment_detail(request, pk):
    assignment = get_object_or_404(
        ReviewAssignment.objects.select_related(
            "reconciliation_result",
            "reconciliation_result__invoice",
            "reconciliation_result__invoice__vendor",
            "assigned_to",
        ).prefetch_related("comments__author", "actions__performed_by"),
        pk=pk,
    )
    try:
        decision = assignment.decision
    except ReviewAssignment.decision.RelatedObjectDoesNotExist:
        decision = None

    return render(request, "reviews/assignment_detail.html", {
        "assignment": assignment,
        "comments": assignment.comments.all(),
        "actions": assignment.actions.all(),
    })


@login_required
@permission_required_code("reviews.decide")
def decide(request, pk):
    if request.method != "POST":
        return redirect("reviews:assignment_detail", pk=pk)
    assignment = get_object_or_404(ReviewAssignment, pk=pk)
    decision = request.POST.get("decision")
    reason = request.POST.get("reason", "")
    decision_map = {
        "APPROVED": ReviewWorkflowService.approve,
        "REJECTED": ReviewWorkflowService.reject,
        "REPROCESSED": ReviewWorkflowService.request_reprocess,
    }
    handler = decision_map.get(decision)
    if handler:
        handler(assignment, request.user, reason)

    # Update AP Case status if one exists
    from apps.cases.models import APCase
    ap_case = APCase.objects.filter(
        reconciliation_result=assignment.reconciliation_result, is_active=True
    ).first()
    if ap_case:
        if decision == "APPROVED":
            ap_case.status = "CLOSED"
            ap_case.save(update_fields=["status", "updated_at"])
        elif decision == "REJECTED":
            ap_case.status = "REJECTED"
            ap_case.save(update_fields=["status", "updated_at"])
        return redirect("cases:case_agent_view", pk=ap_case.pk)

    return redirect("reviews:assignment_detail", pk=pk)


@login_required
@permission_required_code("reviews.decide")
def add_comment(request, pk):
    if request.method != "POST":
        return redirect("reviews:assignment_detail", pk=pk)
    assignment = get_object_or_404(ReviewAssignment, pk=pk)
    body = request.POST.get("body", "").strip()
    if body:
        ReviewWorkflowService.add_comment(assignment, request.user, body)
    return redirect("reviews:assignment_detail", pk=pk)
