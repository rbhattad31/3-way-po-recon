"""Reconciliation template views (server-side rendered)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.agents.models import AgentRecommendation, AgentRun
from apps.auditlog.timeline_service import CaseTimelineService
from apps.core.enums import InvoiceStatus, MatchStatus, UserRole
from apps.documents.models import GoodsReceiptNote, Invoice, PurchaseOrder
from apps.reconciliation.models import ReconciliationResult
from apps.reviews.models import ReviewAssignment
from apps.tools.models import ToolCall


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

    ready_invoices = (
        Invoice.objects
        .filter(status=InvoiceStatus.READY_FOR_RECON)
        .select_related("vendor")
        .order_by("-created_at")
    )

    return render(request, "reconciliation/result_list.html", {
        "results": page_obj,
        "page_obj": page_obj,
        "match_status_choices": MatchStatus.choices,
        "ready_invoices": ready_invoices,
    })


@login_required
def result_detail(request, pk):
    result = get_object_or_404(
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .prefetch_related("exceptions", "line_results"),
        pk=pk,
    )
    # Deduplicate recommendations: keep the highest-confidence entry per type
    all_recs = AgentRecommendation.objects.filter(reconciliation_result=result).order_by("-confidence")
    seen_types = set()
    recommendations = []
    for rec in all_recs:
        if rec.recommendation_type not in seen_types:
            seen_types.add(rec.recommendation_type)
            recommendations.append(rec)

    # Governance: Agent decision flow
    agent_runs = (
        AgentRun.objects.filter(reconciliation_result=result)
        .select_related("agent_definition")
        .prefetch_related("steps", "tool_calls", "decisions")
        .order_by("created_at")
    )

    # Governance: Case timeline
    timeline = CaseTimelineService.get_case_timeline(result.invoice_id)

    # Security: only admins/auditors see full trace; reviewers see summary only
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    return render(request, "reconciliation/result_detail.html", {
        "result": result,
        "exceptions": result.exceptions.all(),
        "line_results": result.line_results.all(),
        "recommendations": recommendations,
        "agent_runs": agent_runs,
        "timeline": timeline,
        "show_full_trace": show_full_trace,
    })


@login_required
def start_reconciliation(request):
    """Trigger reconciliation for selected invoices."""
    if request.method != "POST":
        return redirect("reconciliation:result_list")

    invoice_ids = request.POST.getlist("invoice_ids")
    if not invoice_ids:
        messages.warning(request, "No invoices selected for reconciliation.")
        return redirect("reconciliation:result_list")

    invoice_ids = [int(i) for i in invoice_ids]

    from django.conf import settings as django_settings

    if getattr(django_settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # Run synchronously — no broker needed
        from apps.reconciliation.services.runner_service import ReconciliationRunnerService
        from apps.documents.models import Invoice as InvoiceModel

        invoices = list(
            InvoiceModel.objects.filter(pk__in=invoice_ids)
            .select_related("vendor", "document_upload")
        )
        runner = ReconciliationRunnerService()
        run = runner.run(invoices=invoices, triggered_by=request.user)

        # Run agent pipeline for non-matched results
        from apps.agents.services.orchestrator import AgentOrchestrator
        from apps.reconciliation.models import ReconciliationResult as ReconResult

        agent_count = 0
        results_needing_agents = ReconResult.objects.filter(
            run=run,
        ).exclude(match_status=MatchStatus.MATCHED).select_related(
            "invoice", "invoice__vendor", "purchase_order",
        )
        orchestrator = AgentOrchestrator()
        for recon_result in results_needing_agents:
            try:
                orchestrator.execute(recon_result)
                agent_count += 1
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "Agent pipeline failed for result %s", recon_result.pk
                )

        agent_msg = f" Agent analysis ran on {agent_count} result(s)." if agent_count else ""
        messages.success(
            request,
            f"Reconciliation complete for {run.total_invoices} invoice(s): "
            f"{run.matched_count} matched, {run.partial_count} partial, "
            f"{run.unmatched_count} unmatched.{agent_msg}",
        )
    else:
        from apps.reconciliation.tasks import run_reconciliation_task
        run_reconciliation_task.delay(
            invoice_ids=invoice_ids,
            triggered_by_id=request.user.pk,
        )
        messages.success(
            request,
            f"Reconciliation started for {len(invoice_ids)} invoice(s). Results will appear shortly.",
        )

    return redirect("reconciliation:result_list")


@login_required
def case_console(request, pk):
    """Investigation console — single-page deep dive into one reconciliation case."""
    result = get_object_or_404(
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "invoice__document_upload", "purchase_order", "purchase_order__vendor")
        .prefetch_related("exceptions", "line_results", "line_results__invoice_line", "line_results__po_line"),
        pk=pk,
    )

    invoice = result.invoice
    po = result.purchase_order

    # GRNs linked to the PO
    grns = []
    grn_line_count = 0
    if po:
        grns = list(GoodsReceiptNote.objects.filter(purchase_order=po).select_related("vendor").prefetch_related("line_items"))
        for grn in grns:
            grn_line_count += grn.line_items.count()

    # Line results
    line_results = list(result.line_results.select_related("invoice_line", "po_line").all())
    mismatch_line_count = sum(1 for ln in line_results if ln.match_status != MatchStatus.MATCHED)

    # Exceptions
    exceptions = list(result.exceptions.all().order_by("-severity", "exception_type"))

    # Agent runs with prefetched relations
    agent_runs = list(
        AgentRun.objects.filter(reconciliation_result=result)
        .select_related("agent_definition")
        .prefetch_related("steps", "tool_calls", "decisions", "recommendations")
        .order_by("created_at")
    )

    # Deduplicated recommendations
    all_recs = AgentRecommendation.objects.filter(reconciliation_result=result).order_by("-confidence")
    seen_types = set()
    recommendations = []
    for rec in all_recs:
        if rec.recommendation_type not in seen_types:
            seen_types.add(rec.recommendation_type)
            recommendations.append(rec)

    # Primary recommendation (highest confidence)
    primary_recommendation = recommendations[0] if recommendations else None

    # Timeline
    timeline = CaseTimelineService.get_case_timeline(invoice.pk)

    # Review assignment
    review_assignment = (
        ReviewAssignment.objects
        .filter(reconciliation_result=result)
        .select_related("assigned_to")
        .prefetch_related("comments", "comments__author", "actions", "actions__performed_by")
        .order_by("-created_at")
        .first()
    )
    review_decision = None
    review_comments = []
    review_actions = []
    if review_assignment:
        try:
            review_decision = review_assignment.decision
        except Exception:
            pass
        review_comments = list(review_assignment.comments.all().order_by("created_at"))
        review_actions = list(review_assignment.actions.all().order_by("-created_at"))

    # Security: role-aware trace visibility
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    # AI case summary (from CASE_SUMMARY agent or result summary)
    case_summary_text = result.summary or ""
    case_summary_agent = None
    for run in agent_runs:
        if run.agent_type == "CASE_SUMMARY" and run.status == "COMPLETED":
            case_summary_agent = run
            if run.summarized_reasoning:
                case_summary_text = run.summarized_reasoning
            break

    context = {
        "result": result,
        "invoice": invoice,
        "po": po,
        "grns": grns,
        "grn_line_count": grn_line_count,
        "line_results": line_results,
        "mismatch_line_count": mismatch_line_count,
        "exceptions": exceptions,
        "agent_runs": agent_runs,
        "recommendations": recommendations,
        "primary_recommendation": primary_recommendation,
        "timeline": timeline,
        "review_assignment": review_assignment,
        "review_decision": review_decision,
        "review_comments": review_comments,
        "review_actions": review_actions,
        "show_full_trace": show_full_trace,
        "case_summary_text": case_summary_text,
        "case_summary_agent": case_summary_agent,
        "match_status_choices": MatchStatus.choices,
    }
    return render(request, "reconciliation/case_console.html", context)
