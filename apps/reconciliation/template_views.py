"""Reconciliation template views (server-side rendered)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.agents.models import AgentRecommendation
from apps.core.enums import InvoiceStatus, MatchStatus
from apps.documents.models import Invoice
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
    recommendations = AgentRecommendation.objects.filter(reconciliation_result=result).order_by("-confidence")
    return render(request, "reconciliation/result_detail.html", {
        "result": result,
        "exceptions": result.exceptions.all(),
        "line_results": result.line_results.all(),
        "recommendations": recommendations,
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
