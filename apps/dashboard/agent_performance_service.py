"""Agent Performance Dashboard Service — operational metrics for AI agents.

Focused on: runtime, throughput, utilization, success/failure, latency,
token/cost usage, recommendations, and live agent activity.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from apps.agents.models import AgentEscalation, AgentRecommendation, AgentRun
from apps.agents.services.policy_engine import PolicyEngine
from apps.core.enums import AgentRunStatus, ToolCallStatus, UserRole
from apps.tools.models import ToolCall


class AgentPerformanceDashboardService:
    """Read-only aggregation service for the Agent Performance Dashboard.

    This service provides ONLY operational/performance metrics.
    Governance-specific data (RBAC, guardrails, denials) is handled
    by ``AgentGovernanceDashboardService``.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _user_role(user):
        return getattr(user, "role", None) if user else None

    @staticmethod
    def _parse_filters(filters: Optional[Dict] = None) -> Dict:
        f = filters or {}
        result = {}
        if f.get("date_from"):
            result["date_from"] = f["date_from"]
        if f.get("date_to"):
            result["date_to"] = f["date_to"]
        if f.get("agent_type"):
            result["agent_type"] = f["agent_type"]
        if f.get("status"):
            result["status"] = f["status"]
        return result

    @staticmethod
    def _base_runs_qs(filters: Optional[Dict] = None, user=None, tenant=None):
        """Build a base AgentRun queryset with filters and user scoping."""
        from apps.dashboard.services import DashboardService
        qs = DashboardService._scope_agent_runs(AgentRun.objects.all(), user, tenant)
        f = AgentPerformanceDashboardService._parse_filters(filters)

        if "date_from" in f:
            qs = qs.filter(created_at__date__gte=f["date_from"])
        if "date_to" in f:
            qs = qs.filter(created_at__date__lte=f["date_to"])
        if "agent_type" in f:
            qs = qs.filter(agent_type=f["agent_type"])
        if "status" in f:
            qs = qs.filter(status=f["status"])
        return qs

    # ------------------------------------------------------------------
    # 1. Summary KPIs — operational focus
    # ------------------------------------------------------------------
    @staticmethod
    def get_summary(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        total = qs.count()
        completed = qs.filter(status=AgentRunStatus.COMPLETED).count()
        failed = qs.filter(status=AgentRunStatus.FAILED).count()
        active_types = qs.values("agent_type").distinct().count()
        success_rate = round(completed / total * 100, 1) if total else 0

        escalation_count = AgentEscalation.objects.filter(agent_run__in=qs).count()
        escalation_rate = round(escalation_count / total * 100, 1) if total else 0

        agg = qs.aggregate(
            avg_runtime=Avg("duration_ms"),
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("cost_estimate"),
        )

        return {
            "total_runs": total,
            "active_agents": active_types,
            "success_rate": success_rate,
            "escalation_rate": escalation_rate,
            "escalation_count": escalation_count,
            "avg_runtime_ms": round(agg["avg_runtime"] or 0, 0),
            "total_tokens": agg["total_tokens"] or 0,
            "estimated_cost": float(agg["total_cost"] or 0),
            "completed": completed,
            "failed": failed,
        }

    # ------------------------------------------------------------------
    # 2. Utilization
    # ------------------------------------------------------------------
    @staticmethod
    def get_utilization(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)

        by_type = list(
            qs.values("agent_type")
            .annotate(count=Count("id"))
            .order_by("agent_type")
        )

        by_hour = list(
            qs.annotate(hour=TruncHour("created_at"))
            .values("hour")
            .annotate(count=Count("id"))
            .order_by("hour")
        )
        for row in by_hour:
            row["hour"] = row["hour"].isoformat() if row["hour"] else ""

        by_day = list(
            qs.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )

        return {"by_type": by_type, "by_hour": by_hour, "by_day": by_day}

    # ------------------------------------------------------------------
    # 3. Reliability — per-agent health matrix
    # ------------------------------------------------------------------
    @staticmethod
    def get_reliability(filters=None, user=None, tenant=None) -> List[Dict[str, Any]]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        rows = (
            qs.values("agent_type")
            .annotate(
                total_runs=Count("id"),
                success_count=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
                failed_count=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
                escalation_count=Count("escalations", distinct=True),
                avg_confidence=Avg("confidence"),
                avg_duration_ms=Avg("duration_ms"),
            )
            .order_by("agent_type")
        )
        result = []
        for r in rows:
            total = r["total_runs"] or 1
            result.append({
                "agent_type": r["agent_type"],
                "total_runs": r["total_runs"],
                "success_pct": round(r["success_count"] / total * 100, 1),
                "failed_pct": round(r["failed_count"] / total * 100, 1),
                "escalations": r["escalation_count"],
                "avg_confidence": round((r["avg_confidence"] or 0) * 100, 1),
                "avg_duration_ms": round(r["avg_duration_ms"] or 0, 0),
            })
        return result

    # ------------------------------------------------------------------
    # 4. Latency
    # ------------------------------------------------------------------
    @staticmethod
    def get_latency(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)

        per_agent = list(
            qs.values("agent_type")
            .annotate(
                avg_duration=Avg("duration_ms"),
                max_duration=Max("duration_ms"),
            )
            .order_by("agent_type")
        )

        slowest = list(
            qs.exclude(duration_ms__isnull=True)
            .select_related("reconciliation_result__invoice")
            .order_by("-duration_ms")[:10]
            .values(
                "id", "agent_type", "duration_ms", "status",
                "started_at",
                "reconciliation_result__invoice__invoice_number",
                "reconciliation_result__invoice__id",
            )
        )
        for row in slowest:
            row["invoice_number"] = row.pop(
                "reconciliation_result__invoice__invoice_number", ""
            )
            row["invoice_id"] = row.pop(
                "reconciliation_result__invoice__id", None
            )

        return {"per_agent": per_agent, "slowest_runs": slowest}

    # ------------------------------------------------------------------
    # 5. Token & cost metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_tokens(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)

        totals = qs.aggregate(
            total_prompt=Sum("prompt_tokens"),
            total_completion=Sum("completion_tokens"),
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("cost_estimate"),
        )

        by_agent = list(
            qs.values("agent_type")
            .annotate(
                prompt_tokens=Sum("prompt_tokens"),
                completion_tokens=Sum("completion_tokens"),
                total_tokens=Sum("total_tokens"),
                cost=Sum("cost_estimate"),
            )
            .order_by("agent_type")
        )
        for row in by_agent:
            row["cost"] = float(row["cost"] or 0)

        return {
            "total_prompt_tokens": totals["total_prompt"] or 0,
            "total_completion_tokens": totals["total_completion"] or 0,
            "total_tokens": totals["total_tokens"] or 0,
            "total_cost": float(totals["total_cost"] or 0),
            "by_agent": by_agent,
        }

    # ------------------------------------------------------------------
    # 6. Tool usage — operational only (no auth data)
    # ------------------------------------------------------------------
    @staticmethod
    def get_tool_usage(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        run_ids = qs.values_list("id", flat=True)

        tool_qs = ToolCall.objects.filter(agent_run_id__in=run_ids)
        by_tool = list(
            tool_qs.values("tool_name")
            .annotate(
                total=Count("id"),
                success=Count("id", filter=Q(status=ToolCallStatus.SUCCESS)),
                failed=Count("id", filter=Q(status=ToolCallStatus.FAILED)),
                avg_duration=Avg("duration_ms"),
            )
            .order_by("-total")
        )
        for row in by_tool:
            t = row["total"] or 1
            row["success_pct"] = round(row["success"] / t * 100, 1)
            row["failed_pct"] = round(row["failed"] / t * 100, 1)

        most_used = by_tool[0]["tool_name"] if by_tool else "—"
        slowest = (
            max(by_tool, key=lambda x: x["avg_duration"] or 0)["tool_name"]
            if by_tool
            else "—"
        )
        most_failed = (
            max(by_tool, key=lambda x: x["failed"])["tool_name"]
            if by_tool
            else "—"
        )

        return {
            "by_tool": by_tool,
            "most_used": most_used,
            "slowest_tool": slowest,
            "most_failed": most_failed,
        }

    # ------------------------------------------------------------------
    # 7. Recommendation intelligence — operational
    # ------------------------------------------------------------------
    @staticmethod
    def get_recommendation_intelligence(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        run_ids = list(qs.values_list("id", flat=True))

        rec_qs = AgentRecommendation.objects.filter(agent_run_id__in=run_ids)
        rows = (
            rec_qs.values("recommendation_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        by_type = []
        for row in rows:
            sub = rec_qs.filter(recommendation_type=row["recommendation_type"])
            accepted = sub.filter(accepted=True).count()
            rejected = sub.filter(accepted=False).count()
            pending = sub.filter(accepted__isnull=True).count()
            decided = accepted + rejected
            by_type.append({
                "recommendation_type": row["recommendation_type"],
                "count": row["count"],
                "accepted": accepted,
                "rejected": rejected,
                "pending": pending,
                "acceptance_rate": (
                    round(accepted / decided * 100, 1) if decided else None
                ),
            })

        return {"by_type": by_type, "total": rec_qs.count()}

    # ------------------------------------------------------------------
    # 8. Live feed — operational activity
    # ------------------------------------------------------------------
    @staticmethod
    def get_live_feed(filters=None, user=None, tenant=None, limit=25) -> List[Dict[str, Any]]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        runs = (
            qs.select_related("reconciliation_result__invoice")
            .order_by("-created_at")[:limit]
        )
        feed = []
        for run in runs:
            inv = (
                getattr(run.reconciliation_result, "invoice", None)
                if run.reconciliation_result
                else None
            )
            feed.append({
                "id": run.pk,
                "agent_type": run.agent_type,
                "invoice_number": getattr(inv, "invoice_number", "") or "",
                "invoice_id": getattr(inv, "id", None),
                "summary": (
                    run.summarized_reasoning[:120]
                    if run.summarized_reasoning
                    else ""
                ),
                "confidence": round((run.confidence or 0) * 100, 1),
                "duration_ms": run.duration_ms,
                "status": run.status,
                "created_at": (
                    run.created_at.isoformat() if run.created_at else ""
                ),
            })
        return feed

    # ------------------------------------------------------------------
    # 9. Plan comparison -- actual vs deterministic PolicyEngine
    # ------------------------------------------------------------------
    @staticmethod
    def get_plan_comparison(filters=None, user=None, tenant=None, limit=20) -> dict:
        """Compare actual agent runs against what PolicyEngine would have planned.

        Re-runs PolicyEngine.plan() on completed reconciliation results and
        checks whether the actual agent types that ran match the deterministic
        plan.  No new database models are required.
        """
        import logging
        from apps.reconciliation.models import ReconciliationResult

        logger = logging.getLogger(__name__)
        try:
            base_qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)

            # Distinct result IDs that have at least one completed AgentRun,
            # ordered by most recent run first, limited to <limit> results.
            result_ids = (
                base_qs.filter(status=AgentRunStatus.COMPLETED)
                .exclude(reconciliation_result_id__isnull=True)
                .order_by("-created_at")
                .values_list("reconciliation_result_id", flat=True)
                .distinct()
            )[:limit]

            result_ids = list(result_ids)

            policy = PolicyEngine()
            rows = []

            for result_id in result_ids:
                try:
                    result = (
                        ReconciliationResult.objects
                        .select_related("invoice", "purchase_order")
                        .prefetch_related("exceptions")
                        .get(pk=result_id)
                    )
                except ReconciliationResult.DoesNotExist:
                    continue

                # Actual agents that ran for this result, ordered by start time.
                actual_runs = (
                    base_qs
                    .filter(
                        reconciliation_result_id=result_id,
                        status=AgentRunStatus.COMPLETED,
                    )
                    .order_by("started_at")
                    .values_list("agent_type", flat=True)
                )
                actual_plan = list(actual_runs)
                run_count = len(actual_plan)

                # Deterministic plan from PolicyEngine.
                agent_plan = policy.plan(result)
                policy_plan = list(agent_plan.agents) if agent_plan.agents else []

                actual_set = set(actual_plan)
                policy_set = set(policy_plan)

                plans_match = actual_plan == policy_plan
                added_by_actual = sorted(actual_set - policy_set)
                removed_by_actual = sorted(policy_set - actual_set)

                inv = getattr(result, "invoice", None)
                rows.append({
                    "result_id": result_id,
                    "invoice_number": getattr(inv, "invoice_number", "") or "",
                    "invoice_id": getattr(inv, "id", None),
                    "match_status": result.match_status,
                    "policy_plan": policy_plan,
                    "actual_plan": actual_plan,
                    "plans_match": plans_match,
                    "added_by_actual": added_by_actual,
                    "removed_by_actual": removed_by_actual,
                    "run_count": run_count,
                })

            total_compared = len(rows)
            plans_matched = sum(1 for r in rows if r["plans_match"])
            plans_differed = total_compared - plans_matched
            match_rate = (
                round(plans_matched / total_compared * 100, 1)
                if total_compared
                else 0
            )

            return {
                "rows": rows,
                "total_compared": total_compared,
                "plans_matched": plans_matched,
                "plans_differed": plans_differed,
                "match_rate": match_rate,
            }

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_plan_comparison failed: %s", exc, exc_info=True
            )
            return {
                "rows": [],
                "total_compared": 0,
                "plans_matched": 0,
                "plans_differed": 0,
                "match_rate": 0,
            }
