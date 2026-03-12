"""AP Cases template views (server-side rendered)."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.cases.models import APCase
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.core.enums import CasePriority, CaseStatus, ProcessingPath

logger = logging.getLogger(__name__)


@login_required
def case_inbox(request):
    """AP Cases inbox — main listing of all cases with filters."""
    qs = CaseSelectors.inbox(
        processing_path=request.GET.get("processing_path", ""),
        status=request.GET.get("status", ""),
        priority=request.GET.get("priority", ""),
        search=request.GET.get("q", ""),
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    stats = CaseSelectors.stats()

    return render(request, "cases/case_inbox.html", {
        "cases": page_obj,
        "page_obj": page_obj,
        "stats": stats,
        "status_choices": CaseStatus.choices,
        "path_choices": ProcessingPath.choices,
        "priority_choices": CasePriority.choices,
    })


@login_required
def case_console(request, pk):
    """Case console — deep-dive investigation view for a single AP Case."""
    case = get_object_or_404(
        APCase.objects.select_related(
            "invoice", "invoice__vendor", "invoice__document_upload",
            "vendor", "purchase_order", "reconciliation_result",
            "assigned_to",
        ).prefetch_related(
            "stages", "artifacts", "decisions",
            "assignments", "comments", "activities",
        ),
        pk=pk, is_active=True,
    )

    invoice = case.invoice
    po = case.purchase_order
    stages = list(case.stages.order_by("created_at"))
    decisions = list(case.decisions.order_by("created_at"))
    artifacts = list(case.artifacts.order_by("-created_at"))
    comments = list(case.comments.select_related("author").order_by("created_at"))

    # GRNs linked to PO
    grns = []
    if po:
        from apps.documents.models import GoodsReceiptNote
        grns = list(
            GoodsReceiptNote.objects.filter(purchase_order=po)
            .select_related("vendor")
            .prefetch_related("line_items")
        )

    # Reconciliation exceptions
    exceptions = []
    recon_result = case.reconciliation_result
    if recon_result:
        exceptions = list(recon_result.exceptions.all().order_by("-severity", "exception_type"))

    # Agent runs
    agent_runs = []
    if recon_result:
        from apps.agents.models import AgentRun
        agent_runs = list(
            AgentRun.objects.filter(reconciliation_result=recon_result)
            .select_related("agent_definition")
            .prefetch_related("steps", "tool_calls", "decisions", "recommendations")
            .order_by("created_at")
        )

    # Summary
    summary = getattr(case, "summary", None)

    # Timeline
    from apps.auditlog.timeline_service import CaseTimelineService
    timeline = CaseTimelineService.get_case_timeline(invoice.pk)

    # Security: role-aware trace visibility
    from apps.core.enums import UserRole
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    return render(request, "cases/case_console.html", {
        "case": case,
        "invoice": invoice,
        "po": po,
        "stages": stages,
        "decisions": decisions,
        "artifacts": artifacts,
        "comments": comments,
        "grns": grns,
        "exceptions": exceptions,
        "agent_runs": agent_runs,
        "summary": summary,
        "timeline": timeline,
        "show_full_trace": show_full_trace,
    })


@login_required
def reprocess_case(request, pk):
    """Reprocess a case from a specific stage."""
    if request.method != "POST":
        return redirect("cases:case_console", pk=pk)

    case = get_object_or_404(APCase, pk=pk, is_active=True)
    stage = request.POST.get("stage", "")

    if not stage:
        messages.warning(request, "No stage specified for reprocessing.")
        return redirect("cases:case_console", pk=pk)

    from django.conf import settings as django_settings
    from apps.cases.tasks import reprocess_case_from_stage_task

    if getattr(django_settings, "CELERY_TASK_ALWAYS_EAGER", False):
        try:
            reprocess_case_from_stage_task.run(case_id=case.pk, stage=stage)
            messages.success(request, f"Case {case.case_number} reprocessed from {stage}.")
        except Exception as exc:
            messages.error(request, f"Reprocessing failed: {exc}")
    else:
        reprocess_case_from_stage_task.delay(case.pk, stage)
        messages.success(request, f"Case {case.case_number} reprocessing started from {stage}.")

    return redirect("cases:case_console", pk=pk)
