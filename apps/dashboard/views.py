"""Dashboard template views."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.agents.models import AgentRun
from apps.core.enums import AgentRunStatus
from apps.dashboard.services import DashboardService


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
    runs = AgentRun.objects.select_related("agent_definition").order_by("-created_at")[:50]
    total = runs.count() if runs else 0
    return render(request, "dashboard/agent_monitor.html", {
        "agent_runs": runs,
        "total_runs": AgentRun.objects.count(),
        "success_runs": AgentRun.objects.filter(status=AgentRunStatus.COMPLETED).count(),
        "failed_runs": AgentRun.objects.filter(status=AgentRunStatus.FAILED).count(),
        "total_tokens": sum(r.total_tokens or 0 for r in AgentRun.objects.all()[:500]),
    })
