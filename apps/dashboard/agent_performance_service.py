"""Agent Performance Dashboard Service — operational metrics for AI agents.

Focused on: runtime, throughput, utilization, success/failure, latency,
token/cost usage, recommendations, and live agent activity.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from apps.agents.models import AgentDefinition, AgentEscalation, AgentOrchestrationRun, AgentRecommendation, AgentRun
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

    @staticmethod
    def _available_agent_types(user=None, tenant=None) -> List[str]:
        """Return DB-driven agent types from runs + definitions (tenant scoped)."""
        from apps.dashboard.services import DashboardService

        runs_qs = DashboardService._scope_agent_runs(AgentRun.objects.all(), user, tenant)
        run_types = set(
            runs_qs.exclude(agent_type="").values_list("agent_type", flat=True).distinct()
        )

        defs_qs = AgentDefinition.objects.all()
        if tenant is not None:
            defs_qs = defs_qs.filter(Q(tenant=tenant) | Q(tenant__isnull=True)).distinct()
        def_types = set(
            defs_qs.exclude(agent_type="").values_list("agent_type", flat=True).distinct()
        )

        return sorted(run_types | def_types)

    @staticmethod
    def _estimate_cost_from_tokens(*, model_name: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        """Fallback LLM cost estimate when DB-backed run costs are missing.

        Rates are per 1k tokens in USD. These are safe defaults and can be
        overridden in runtime by recording LLMCostRate + actual_cost_usd.
        """
        model = (model_name or "").strip().lower()

        default_input_per_1k = Decimal("0.005")
        default_output_per_1k = Decimal("0.015")
        rate_map = {
            "gpt-4o": (Decimal("0.005"), Decimal("0.015")),
            "gpt-4o-mini": (Decimal("0.00015"), Decimal("0.0006")),
        }

        input_rate, output_rate = rate_map.get(model, (default_input_per_1k, default_output_per_1k))
        prompt_cost = (Decimal(int(prompt_tokens or 0)) / Decimal(1000)) * input_rate
        completion_cost = (Decimal(int(completion_tokens or 0)) / Decimal(1000)) * output_rate
        return prompt_cost + completion_cost

    @staticmethod
    def _resolved_run_cost(row: Dict[str, Any]) -> Decimal:
        """Resolve per-run cost using strongest available source.

        Priority:
        1) actual_cost_usd
        2) cost_estimate
        3) token/model fallback estimate
        """
        actual = row.get("actual_cost_usd")
        if actual is not None:
            return Decimal(actual)

        estimate = row.get("cost_estimate")
        if estimate is not None:
            return Decimal(estimate)

        prompt_tokens = row.get("prompt_tokens")
        completion_tokens = row.get("completion_tokens")
        total_tokens = row.get("total_tokens")

        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return Decimal("0")

        if prompt_tokens is None and completion_tokens is None and total_tokens is not None:
            prompt_tokens = int(total_tokens)
            completion_tokens = 0
        elif prompt_tokens is None:
            prompt_tokens = max(int(total_tokens or 0) - int(completion_tokens or 0), 0)
        elif completion_tokens is None:
            completion_tokens = max(int(total_tokens or 0) - int(prompt_tokens or 0), 0)

        return AgentPerformanceDashboardService._estimate_cost_from_tokens(
            model_name=str(row.get("llm_model_used") or ""),
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
        )

    @staticmethod
    def _resolved_total_cost(qs) -> Decimal:
        rows = qs.values(
            "actual_cost_usd",
            "cost_estimate",
            "llm_model_used",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        )
        total_cost = Decimal("0")
        for row in rows:
            total_cost += AgentPerformanceDashboardService._resolved_run_cost(row)
        return total_cost

    # ------------------------------------------------------------------
    # 1. Summary KPIs — operational focus
    # ------------------------------------------------------------------
    @staticmethod
    def get_summary(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        total = qs.count()
        completed = qs.filter(status=AgentRunStatus.COMPLETED).count()
        failed = qs.filter(status=AgentRunStatus.FAILED).count()
        active_types = len(AgentPerformanceDashboardService._available_agent_types(user=user, tenant=tenant))
        success_rate = round(completed / total * 100, 1) if total else 0

        escalation_count = AgentEscalation.objects.filter(agent_run__in=qs).count()
        escalation_rate = round(escalation_count / total * 100, 1) if total else 0

        agg = qs.aggregate(
            avg_runtime=Avg("duration_ms"),
            total_tokens=Sum("total_tokens"),
        )
        resolved_total_cost = AgentPerformanceDashboardService._resolved_total_cost(qs)

        return {
            "total_runs_today": total,
            "active_agents": active_types,
            "success_rate": success_rate,
            "escalation_rate": escalation_rate,
            "escalation_count": escalation_count,
            "avg_runtime_ms": round(agg["avg_runtime"] or 0, 0),
            "total_tokens": agg["total_tokens"] or 0,
            "estimated_cost_today": float(resolved_total_cost),
            "completed": completed,
            "failed": failed,
        }

    # ------------------------------------------------------------------
    # 2. Utilization
    # ------------------------------------------------------------------
    @staticmethod
    def get_utilization(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        counted_by_type = {
            row["agent_type"]: row["count"]
            for row in qs.values("agent_type").annotate(count=Count("id"))
        }
        by_type = [
            {"agent_type": agent_type, "count": int(counted_by_type.get(agent_type, 0) or 0)}
            for agent_type in AgentPerformanceDashboardService._available_agent_types(user=user, tenant=tenant)
        ]

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
        rows = list(
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
        rows_by_type = {row["agent_type"]: row for row in rows}

        result = []
        for agent_type in AgentPerformanceDashboardService._available_agent_types(user=user, tenant=tenant):
            r = rows_by_type.get(agent_type, {})
            total_runs = int(r.get("total_runs") or 0)
            success_count = int(r.get("success_count") or 0)
            failed_count = int(r.get("failed_count") or 0)
            total = total_runs or 1
            result.append({
                "agent_type": agent_type,
                "total_runs": total_runs,
                "success_pct": round(success_count / total * 100, 1) if total_runs else 0,
                "failed_pct": round(failed_count / total * 100, 1) if total_runs else 0,
                "escalations": int(r.get("escalation_count") or 0),
                "avg_confidence": round((r.get("avg_confidence") or 0) * 100, 1),
                "avg_duration_ms": round(r.get("avg_duration_ms") or 0, 0),
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
            )
            .order_by("agent_type")
        )

        # Fill per-agent cost with same resolver used by KPI summary.
        rows = qs.values(
            "agent_type",
            "actual_cost_usd",
            "cost_estimate",
            "llm_model_used",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        )
        cost_by_agent: Dict[str, Decimal] = {}
        for row in rows:
            agent_type = row.get("agent_type") or ""
            cost_by_agent[agent_type] = cost_by_agent.get(agent_type, Decimal("0")) + AgentPerformanceDashboardService._resolved_run_cost(row)

        for row in by_agent:
            row["cost"] = float(cost_by_agent.get(row.get("agent_type") or "", Decimal("0")))

        resolved_total_cost = AgentPerformanceDashboardService._resolved_total_cost(qs)

        return {
            "total_prompt_tokens": totals["total_prompt"] or 0,
            "total_completion_tokens": totals["total_completion"] or 0,
            "total_tokens": totals["total_tokens"] or 0,
            "total_cost": float(resolved_total_cost),
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

    # ------------------------------------------------------------------
    # 10. Planner Comparison -- Deterministic PolicyEngine vs LLM Planner
    # ------------------------------------------------------------------
    @staticmethod
    def get_planner_comparison(filters=None, user=None, tenant=None, limit=20) -> dict:
        """Aggregate AgentOrchestrationRun by plan_source to compare
        deterministic PolicyEngine plans vs LLM ReasoningPlanner output.

        Returns KPI summary, per-source breakdown, chart datasets, and
        a recent-run detail table.
        """
        import logging

        logger = logging.getLogger(__name__)
        try:
            from apps.dashboard.services import DashboardService

            # -- Scope orchestration runs to the same tenant/user as agent runs --
            base_agent_qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
            result_ids = base_agent_qs.values_list("reconciliation_result_id", flat=True).distinct()

            orch_qs = AgentOrchestrationRun.objects.filter(
                reconciliation_result_id__in=result_ids
            )
            if tenant is not None:
                orch_qs = orch_qs.filter(tenant=tenant)

            # -- Per-source aggregation --
            by_source_raw = list(
                orch_qs.values("plan_source")
                .annotate(
                    run_count=Count("id"),
                    success_count=Count(
                        "id",
                        filter=Q(status=AgentOrchestrationRun.Status.COMPLETED),
                    ),
                    partial_count=Count(
                        "id",
                        filter=Q(status=AgentOrchestrationRun.Status.PARTIAL),
                    ),
                    failed_count=Count(
                        "id",
                        filter=Q(status=AgentOrchestrationRun.Status.FAILED),
                    ),
                    avg_confidence=Avg("final_confidence"),
                    avg_plan_confidence=Avg("plan_confidence"),
                    avg_duration=Avg("duration_ms"),
                )
                .order_by("plan_source")
            )

            # -- Plan divergence: planned_agents != executed_agents --
            # Computed in Python (JSONField lists are hard to diff in SQL portably)
            divergence_by_source: Dict[str, int] = {}
            for row in (
                orch_qs
                .exclude(planned_agents__isnull=True)
                .exclude(executed_agents__isnull=True)
                .values("plan_source", "planned_agents", "executed_agents")
            ):
                src = row["plan_source"] or "unknown"
                if row["planned_agents"] != row["executed_agents"]:
                    divergence_by_source[src] = divergence_by_source.get(src, 0) + 1

            by_source = []
            for row in by_source_raw:
                src = row["plan_source"] or "unknown"
                total = int(row["run_count"] or 0)
                success = int(row["success_count"] or 0)
                failed = int(row["failed_count"] or 0)
                diverg = divergence_by_source.get(src, 0)
                by_source.append({
                    "plan_source": src,
                    "label": "LLM (ReasoningPlanner)" if src == "llm" else (
                        "Deterministic (PolicyEngine)" if src == "deterministic" else src.title()
                    ),
                    "run_count": total,
                    "success_rate": round(success / total * 100, 1) if total else 0,
                    "failed_rate": round(failed / total * 100, 1) if total else 0,
                    "avg_confidence": round((row["avg_confidence"] or 0) * 100, 1),
                    "avg_plan_confidence": round((row["avg_plan_confidence"] or 0) * 100, 1),
                    "avg_duration_ms": round(row["avg_duration"] or 0, 0),
                    "divergence_count": diverg,
                    "divergence_rate": round(diverg / total * 100, 1) if total else 0,
                })

            # -- Summary KPIs --
            total_runs = orch_qs.count()
            llm_rows = [r for r in by_source if r["plan_source"] == "llm"]
            det_rows = [r for r in by_source if r["plan_source"] == "deterministic"]
            llm_count = llm_rows[0]["run_count"] if llm_rows else 0
            det_count = det_rows[0]["run_count"] if det_rows else 0
            llm_rate = round(llm_count / total_runs * 100, 1) if total_runs else 0
            total_diverged = sum(divergence_by_source.values())
            divergence_rate = round(total_diverged / total_runs * 100, 1) if total_runs else 0

            # -- Chart datasets (for a grouped bar chart) --
            chart_labels = [r["label"] for r in by_source]
            chart_datasets = [
                {
                    "label": "Success Rate %",
                    "data": [r["success_rate"] for r in by_source],
                    "bg": "#198754",
                },
                {
                    "label": "Avg Confidence %",
                    "data": [r["avg_confidence"] for r in by_source],
                    "bg": "#0d6efd",
                },
                {
                    "label": "Plan Divergence %",
                    "data": [r["divergence_rate"] for r in by_source],
                    "bg": "#fd7e14",
                },
            ]

            # -- Recent orchestration runs detail --
            recent = list(
                orch_qs
                .select_related("reconciliation_result__invoice")
                .order_by("-created_at")[:limit]
                .values(
                    "id",
                    "plan_source",
                    "status",
                    "planned_agents",
                    "executed_agents",
                    "final_confidence",
                    "plan_confidence",
                    "duration_ms",
                    "started_at",
                    "reconciliation_result_id",
                    "reconciliation_result__invoice__invoice_number",
                    "reconciliation_result__invoice__id",
                )
            )
            recent_rows = []
            for r in recent:
                planned = r["planned_agents"] or []
                executed = r["executed_agents"] or []
                diverged = planned != executed
                recent_rows.append({
                    "id": r["id"],
                    "plan_source": r["plan_source"] or "unknown",
                    "status": r["status"],
                    "planned_agents": planned,
                    "executed_agents": executed,
                    "diverged": diverged,
                    "final_confidence": round((r["final_confidence"] or 0) * 100, 1),
                    "plan_confidence": round((r["plan_confidence"] or 0) * 100, 1),
                    "duration_ms": r["duration_ms"],
                    "started_at": r["started_at"].isoformat() if r["started_at"] else "",
                    "invoice_number": r["reconciliation_result__invoice__invoice_number"] or "",
                    "invoice_id": r["reconciliation_result__invoice__id"],
                    "result_id": r["reconciliation_result_id"],
                })

            return {
                "total_runs": total_runs,
                "llm_runs": llm_count,
                "deterministic_runs": det_count,
                "llm_rate": llm_rate,
                "total_diverged": total_diverged,
                "divergence_rate": divergence_rate,
                "by_source": by_source,
                "chart_labels": chart_labels,
                "chart_datasets": chart_datasets,
                "recent_runs": recent_rows,
            }

        except Exception as exc:  # noqa: BLE001
            logger.warning("get_planner_comparison failed: %s", exc, exc_info=True)
            return {
                "total_runs": 0,
                "llm_runs": 0,
                "deterministic_runs": 0,
                "llm_rate": 0,
                "total_diverged": 0,
                "divergence_rate": 0,
                "by_source": [],
                "chart_labels": [],
                "chart_datasets": [],
                "recent_runs": [],
            }

