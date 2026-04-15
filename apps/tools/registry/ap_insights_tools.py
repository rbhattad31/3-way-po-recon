"""AP Insights tools -- expose system-wide dashboard and analytics data.

These tools give the SupervisorAgent the ability to answer questions about
overall AP performance, match rates, exception trends, agent health,
extraction quality, and processing volumes -- in addition to its existing
case-level analysis capabilities.

All tools follow the existing BaseTool / @register_tool pattern and are
tenant-scoped via the inherited ``_scoped()`` helper.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from apps.tools.registry.base import BaseTool, ToolResult, register_tool

logger = logging.getLogger(__name__)


# ============================================================================
# Dashboard summary tools
# ============================================================================


@register_tool
class GetAPDashboardSummaryTool(BaseTool):
    name = "get_ap_dashboard_summary"
    required_permission = "dashboard.view"
    description = (
        "Get a high-level AP dashboard summary: total invoices, POs, GRNs, "
        "vendors, pending reviews, open exceptions, match percentage, "
        "average confidence, extraction and reconciliation counts."
    )
    when_to_use = (
        "When asked about overall AP health, KPIs, or summary statistics."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.services import DashboardService

            summary = DashboardService.get_summary(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data=summary)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetMatchStatusBreakdownTool(BaseTool):
    name = "get_match_status_breakdown"
    required_permission = "dashboard.view"
    description = (
        "Get breakdown of reconciliation results by match status "
        "(MATCHED, PARTIAL_MATCH, UNMATCHED, REQUIRES_REVIEW, ERROR) "
        "with counts and percentages."
    )
    when_to_use = (
        "When asked about match rates, how many invoices matched vs failed, "
        "or reconciliation outcome distribution."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.services import DashboardService

            breakdown = DashboardService.get_match_status_breakdown(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data={"breakdown": breakdown})
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetExceptionBreakdownTool(BaseTool):
    name = "get_exception_breakdown"
    required_permission = "dashboard.view"
    description = (
        "Get breakdown of open reconciliation exceptions by type "
        "(e.g. QUANTITY_MISMATCH, PRICE_MISMATCH, PO_NOT_FOUND) "
        "with counts. Shows which exception types are most frequent."
    )
    when_to_use = (
        "When asked about exception trends, most common issues, or "
        "what types of problems invoices are experiencing."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.services import DashboardService

            breakdown = DashboardService.get_exception_breakdown(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data={"exception_types": breakdown})
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetModeBreakdownTool(BaseTool):
    name = "get_mode_breakdown"
    required_permission = "dashboard.view"
    description = (
        "Get breakdown of reconciliation results by mode (TWO_WAY vs "
        "THREE_WAY) with match rates and average confidence per mode."
    )
    when_to_use = (
        "When asked about 2-way vs 3-way reconciliation performance, "
        "or how different matching modes compare."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.services import DashboardService

            breakdown = DashboardService.get_mode_breakdown(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data={"modes": breakdown})
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetDailyVolumeTrendTool(BaseTool):
    name = "get_daily_volume_trend"
    required_permission = "dashboard.view"
    description = (
        "Get daily processing volume trends over the last N days. "
        "Shows invoices uploaded, reconciled, and exceptions raised per day."
    )
    when_to_use = (
        "When asked about processing trends, daily volumes, throughput, "
        "or whether volumes are increasing or decreasing."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to look back. Default 30.",
            },
        },
    }

    def run(self, *, days: int = 30, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.services import DashboardService

            days = max(1, min(days, 365))
            trend = DashboardService.get_daily_volume(
                days=days,
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            # Serialize date objects
            for row in trend:
                if hasattr(row.get("date"), "isoformat"):
                    row["date"] = row["date"].isoformat()

            return ToolResult(success=True, data={
                "days": days,
                "data_points": len(trend),
                "trend": trend[-30:],  # Cap at 30 to keep context reasonable
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetRecentActivityTool(BaseTool):
    name = "get_recent_activity"
    required_permission = "dashboard.view"
    description = (
        "Get recent activity feed (latest invoices, reconciliations, "
        "and reviews) across the system."
    )
    when_to_use = (
        "When asked about what happened recently, latest activity, "
        "or what is currently being processed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max items to return. Default 15.",
            },
        },
    }

    def run(self, *, limit: int = 15, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.services import DashboardService

            limit = max(1, min(limit, 50))
            activity = DashboardService.get_recent_activity(
                limit=limit,
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            # Serialize timestamps
            for row in activity:
                ts = row.get("timestamp")
                if hasattr(ts, "isoformat"):
                    row["timestamp"] = ts.isoformat()

            return ToolResult(success=True, data={
                "count": len(activity),
                "activity": activity,
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


# ============================================================================
# Agent performance tools
# ============================================================================


@register_tool
class GetAgentPerformanceSummaryTool(BaseTool):
    name = "get_agent_performance_summary"
    required_permission = "dashboard.view"
    description = (
        "Get agent performance KPIs: total runs, success rate, escalation "
        "rate, average runtime, total tokens, and estimated cost."
    )
    when_to_use = (
        "When asked about agent performance, success rates, how well "
        "agents are performing, or AI operational health."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.agent_performance_service import (
                AgentPerformanceDashboardService,
            )

            summary = AgentPerformanceDashboardService.get_summary(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data=summary)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetAgentReliabilityMatrixTool(BaseTool):
    name = "get_agent_reliability_matrix"
    required_permission = "dashboard.view"
    description = (
        "Get per-agent reliability matrix: success rate, failure rate, "
        "escalations, average confidence, and average duration for each "
        "agent type."
    )
    when_to_use = (
        "When asked which agents are most/least reliable, agent-level "
        "comparison, or which agent types are failing."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.agent_performance_service import (
                AgentPerformanceDashboardService,
            )

            matrix = AgentPerformanceDashboardService.get_reliability(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data={"agents": matrix})
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetAgentTokenCostTool(BaseTool):
    name = "get_agent_token_cost"
    required_permission = "dashboard.view"
    description = (
        "Get token usage and cost metrics across all agents: total prompt "
        "tokens, completion tokens, and cost breakdown per agent type."
    )
    when_to_use = (
        "When asked about LLM costs, token usage, which agents cost the "
        "most, or budget-related questions."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.agent_performance_service import (
                AgentPerformanceDashboardService,
            )

            tokens = AgentPerformanceDashboardService.get_tokens(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data=tokens)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetRecommendationIntelligenceTool(BaseTool):
    name = "get_recommendation_intelligence"
    required_permission = "dashboard.view"
    description = (
        "Get agent recommendation analytics: breakdown by type (AUTO_CLOSE, "
        "SEND_TO_AP_REVIEW, etc.) with acceptance/rejection rates."
    )
    when_to_use = (
        "When asked about what agents recommend, acceptance rates, "
        "or how recommendations are distributed."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.dashboard.agent_performance_service import (
                AgentPerformanceDashboardService,
            )

            intel = AgentPerformanceDashboardService.get_recommendation_intelligence(
                user=getattr(self, "_actor_user", None),
                tenant=self._tenant,
            )
            return ToolResult(success=True, data=intel)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


# ============================================================================
# Extraction quality tools
# ============================================================================


@register_tool
class GetExtractionApprovalAnalyticsTool(BaseTool):
    name = "get_extraction_approval_analytics"
    required_permission = "extraction.view"
    description = (
        "Get extraction approval analytics: touchless rate, human-corrected "
        "count, average corrections per review, and most-corrected fields."
    )
    when_to_use = (
        "When asked about extraction quality, touchless processing rate, "
        "which fields get corrected the most, or approval statistics."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.extraction.services.approval_service import (
                ExtractionApprovalService,
            )

            analytics = ExtractionApprovalService.get_approval_analytics(
                tenant=self._tenant,
            )
            return ToolResult(success=True, data=analytics)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


@register_tool
class GetReviewQueueStatusTool(BaseTool):
    name = "get_review_queue_status"
    required_permission = "reviews.view"
    description = (
        "Get current review queue status: pending, assigned, and in-review "
        "counts broken down by status. Shows review workload."
    )
    when_to_use = (
        "When asked about pending reviews, review backlog, how many "
        "invoices are waiting for review, or reviewer workload."
    )
    parameters_schema = {
        "type": "object",
        "properties": {},
    }

    def run(self, **kwargs) -> ToolResult:
        try:
            from apps.cases.models import ReviewAssignment
            from apps.core.enums import ReviewStatus
            from django.db.models import Count

            qs = ReviewAssignment.objects.filter(is_active=True)
            if self._tenant is not None:
                qs = qs.filter(tenant=self._tenant)

            open_statuses = [
                ReviewStatus.PENDING,
                ReviewStatus.ASSIGNED,
                ReviewStatus.IN_REVIEW,
            ]
            open_qs = qs.filter(status__in=open_statuses)

            breakdown = list(
                open_qs.values("status")
                .annotate(count=Count("id"))
                .order_by("status")
            )

            total_open = sum(r["count"] for r in breakdown)

            # Oldest unassigned
            oldest_pending = (
                qs.filter(status=ReviewStatus.PENDING)
                .order_by("created_at")
                .values("id", "created_at")
                .first()
            )

            return ToolResult(success=True, data={
                "total_open": total_open,
                "breakdown": breakdown,
                "oldest_pending_id": oldest_pending["id"] if oldest_pending else None,
                "oldest_pending_created": (
                    oldest_pending["created_at"].isoformat()
                    if oldest_pending and oldest_pending.get("created_at")
                    else None
                ),
            })
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
