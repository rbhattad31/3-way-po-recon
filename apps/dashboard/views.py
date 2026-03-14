"""Dashboard template views."""
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Q, Sum
from django.shortcuts import render

from apps.agents.models import AgentRun
from apps.cases.models import APCase, APCaseStage
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.core.enums import (
    AgentRunStatus,
    AgentType,
    CasePriority,
    CaseStatus,
    ProcessingPath,
    StageStatus,
)
from apps.dashboard.services import DashboardService


@login_required
def command_center(request):
    """Agentic AP Command Center — AI Operations dashboard."""
    user_role = getattr(request.user, "role", "")
    return render(request, "dashboard/agentic_command_center.html", {
        "user_role": user_role,
    })


@login_required
def analytics(request):
    summary = DashboardService.get_summary(user=request.user)
    recent_activity = DashboardService.get_recent_activity(limit=15, user=request.user)
    return render(request, "dashboard/index.html", {
        "summary": summary,
        "recent_activity": recent_activity,
    })


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------
_STATUS_GROUPS = {
    "in_flight": [
        CaseStatus.NEW,
        CaseStatus.INTAKE_IN_PROGRESS,
        CaseStatus.EXTRACTION_IN_PROGRESS,
        CaseStatus.EXTRACTION_COMPLETED,
        CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
        CaseStatus.TWO_WAY_IN_PROGRESS,
        CaseStatus.THREE_WAY_IN_PROGRESS,
        CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
        CaseStatus.GRN_ANALYSIS_IN_PROGRESS,
        CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
    ],
    "review": [
        CaseStatus.READY_FOR_REVIEW,
        CaseStatus.IN_REVIEW,
        CaseStatus.REVIEW_COMPLETED,
    ],
    "approval": [
        CaseStatus.READY_FOR_APPROVAL,
        CaseStatus.APPROVAL_IN_PROGRESS,
        CaseStatus.READY_FOR_GL_CODING,
        CaseStatus.READY_FOR_POSTING,
    ],
    "closed": [CaseStatus.CLOSED],
    "exception": [CaseStatus.FAILED, CaseStatus.ESCALATED, CaseStatus.REJECTED],
}


@login_required
def agent_monitor(request):
    """Case Operations Dashboard — case-centric view with agent activity."""

    # --- Filters ---
    path_filter = request.GET.get("path")
    status_filter = request.GET.get("status")
    priority_filter = request.GET.get("priority")

    case_qs = APCase.objects.select_related(
        "invoice", "vendor", "purchase_order", "assigned_to",
    )
    case_qs = CaseSelectors.scope_for_user(case_qs, request.user)

    if path_filter:
        case_qs = case_qs.filter(processing_path=path_filter)
    if status_filter:
        case_qs = case_qs.filter(status=status_filter)
    if priority_filter:
        case_qs = case_qs.filter(priority=priority_filter)

    # ---- KPI aggregates ----
    total_cases = case_qs.count()
    kpis = case_qs.aggregate(
        in_flight=Count("id", filter=Q(status__in=_STATUS_GROUPS["in_flight"])),
        review=Count("id", filter=Q(status__in=_STATUS_GROUPS["review"])),
        approval=Count("id", filter=Q(status__in=_STATUS_GROUPS["approval"])),
        closed=Count("id", filter=Q(status__in=_STATUS_GROUPS["closed"])),
        exception=Count("id", filter=Q(status__in=_STATUS_GROUPS["exception"])),
        avg_risk=Avg("risk_score"),
        needs_human=Count("id", filter=Q(requires_human_review=True)),
    )

    # ---- By processing path ----
    path_breakdown = (
        case_qs.values("processing_path")
        .annotate(
            count=Count("id"),
            closed=Count("id", filter=Q(status=CaseStatus.CLOSED)),
            in_review=Count("id", filter=Q(status__in=_STATUS_GROUPS["review"])),
            failed=Count("id", filter=Q(status=CaseStatus.FAILED)),
            avg_risk=Avg("risk_score"),
        )
        .order_by("processing_path")
    )

    # ---- By status ----
    status_breakdown = (
        case_qs.values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    # ---- By priority ----
    priority_breakdown = (
        case_qs.values("priority")
        .annotate(count=Count("id"))
        .order_by("priority")
    )

    # ---- Agent activity summary (over same case set) ----
    # Get invoice IDs from the filtered cases, then find agent runs via reconciliation_result
    case_invoice_ids = case_qs.values_list("invoice_id", flat=True)
    agent_qs = AgentRun.objects.filter(
        reconciliation_result__invoice_id__in=case_invoice_ids,
    )
    agent_stats = agent_qs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
        failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
        total_tokens=Sum("total_tokens"),
        avg_duration=Avg("duration_ms"),
        avg_confidence=Avg("confidence"),
    )

    # per-type breakdown
    agent_type_breakdown = (
        agent_qs.values("agent_type")
        .annotate(
            count=Count("id"),
            completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
            failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
            avg_confidence=Avg("confidence"),
        )
        .order_by("agent_type")
    )

    # ---- Recent cases ----
    recent_cases = case_qs.order_by("-created_at")[:50]

    return render(request, "dashboard/agent_monitor.html", {
        "total_cases": total_cases,
        "kpis": kpis,
        "path_breakdown": path_breakdown,
        "status_breakdown": status_breakdown,
        "priority_breakdown": priority_breakdown,
        "agent_stats": agent_stats,
        "agent_type_breakdown": agent_type_breakdown,
        "recent_cases": recent_cases,
        # Filter support
        "processing_paths": ProcessingPath.choices,
        "case_statuses": CaseStatus.choices,
        "priorities": CasePriority.choices,
        "selected_path": path_filter or "",
        "selected_status": status_filter or "",
        "selected_priority": priority_filter or "",
    })
