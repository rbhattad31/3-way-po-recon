"""Dashboard template views."""
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import render

from apps.agents.models import AgentRun
from apps.core.enums import AgentRunStatus, AgentType
from apps.dashboard.services import DashboardService
from apps.reconciliation.models import ReconciliationRun


@login_required
def index(request):
    summary = DashboardService.get_summary()
    recent_activity = DashboardService.get_recent_activity(limit=15)
    return render(request, "dashboard/index.html", {
        "summary": summary,
        "recent_activity": recent_activity,
    })


@login_required
def agent_monitor(request):
    # --- Filters from query params ---
    run_id = request.GET.get("run")
    agent_type = request.GET.get("agent_type")
    status = request.GET.get("status")

    qs = AgentRun.objects.select_related(
        "agent_definition", "reconciliation_result", "reconciliation_result__invoice",
        "reconciliation_result__run",
    ).order_by("-created_at")

    if run_id:
        qs = qs.filter(reconciliation_result__run_id=run_id)
    if agent_type:
        qs = qs.filter(agent_type=agent_type)
    if status:
        qs = qs.filter(status=status)

    agent_runs = qs[:100]

    # --- Aggregate stats (on filtered queryset) ---
    stats = qs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
        failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
        skipped=Count("id", filter=Q(status=AgentRunStatus.SKIPPED)),
        running=Count("id", filter=Q(status=AgentRunStatus.RUNNING)),
        total_tokens=Sum("total_tokens"),
        avg_duration=Avg("duration_ms"),
        avg_confidence=Avg("confidence"),
    )

    # --- Per-agent-type breakdown ---
    type_breakdown = (
        qs.values("agent_type")
        .annotate(
            count=Count("id"),
            completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
            failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
            avg_duration=Avg("duration_ms"),
            avg_confidence=Avg("confidence"),
            tokens=Sum("total_tokens"),
        )
        .order_by("agent_type")
    )

    # --- Per reconciliation-run breakdown ---
    run_breakdown = (
        qs.values("reconciliation_result__run_id", "reconciliation_result__run__status")
        .annotate(
            agents=Count("id"),
            completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
            failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
            tokens=Sum("total_tokens"),
        )
        .order_by("-reconciliation_result__run_id")[:20]
    )

    # --- Filter dropdown options ---
    recon_runs = ReconciliationRun.objects.order_by("-created_at")[:30]

    return render(request, "dashboard/agent_monitor.html", {
        "agent_runs": agent_runs,
        "stats": stats,
        "type_breakdown": type_breakdown,
        "run_breakdown": run_breakdown,
        "recon_runs": recon_runs,
        "agent_types": AgentType.choices,
        "agent_statuses": AgentRunStatus.choices,
        "selected_run": run_id or "",
        "selected_agent_type": agent_type or "",
        "selected_status": status or "",
    })
