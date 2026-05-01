"""Dashboard analytics service — aggregation queries for the dashboard."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Avg, Count, F, Max, Q, Sum, Value
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from apps.agents.models import AgentRun, AgentRecommendation, AgentEscalation, DecisionLog
from apps.core.enums import AgentRunStatus, AgentType, MatchStatus, RecommendationType, ReviewStatus, ToolCallStatus, UserRole
from apps.documents.models import GoodsReceiptNote, Invoice, PurchaseOrder
from apps.reconciliation.models import ReconciliationException, ReconciliationResult
from apps.cases.models import ReviewAssignment
from apps.tools.models import ToolCall
from apps.vendors.models import Vendor


class DashboardService:
    """Read-only aggregation service for the main dashboard."""

    # ------------------------------------------------------------------
    # Scoping helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ap_processor_can_see_all_cases(user=None, tenant=None) -> bool:
        if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
            return False
        from apps.reconciliation.models import ReconciliationConfig

        effective_tenant = tenant or getattr(user, "company", None)
        config = ReconciliationConfig.objects.filter(is_default=True, tenant=effective_tenant).first()
        if config is None:
            config = ReconciliationConfig.objects.filter(is_default=True, tenant__isnull=True).first()
        return bool(config and config.ap_processor_sees_all_cases)

    @staticmethod
    def _scope_invoices(qs, user=None, tenant=None):
        """Restrict invoice queryset based on user role and tenant."""
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if user is None:
            return qs
        user_role = getattr(user, "role", None)

        if user_role == UserRole.AP_PROCESSOR:
            if DashboardService._ap_processor_can_see_all_cases(user=user, tenant=tenant):
                return qs
            return qs.filter(document_upload__uploaded_by=user)

        if user_role == UserRole.REVIEWER:
            return qs.filter(
                recon_results__review_assignments__assigned_to=user
            ).distinct()

        return qs

    @staticmethod
    def _scope_recon_results(qs, user=None, tenant=None):
        """Restrict reconciliation results based on user role and tenant."""
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if user is None:
            return qs
        user_role = getattr(user, "role", None)

        if user_role == UserRole.AP_PROCESSOR:
            if DashboardService._ap_processor_can_see_all_cases(user=user, tenant=tenant):
                return qs
            return qs.filter(invoice__document_upload__uploaded_by=user)

        if user_role == UserRole.REVIEWER:
            return qs.filter(
                review_assignments__assigned_to=user
            ).distinct()

        return qs

    @staticmethod
    def _scope_exceptions(qs, user=None, tenant=None):
        """Restrict exceptions based on user role and tenant."""
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if user is None:
            return qs
        user_role = getattr(user, "role", None)

        if user_role == UserRole.AP_PROCESSOR:
            if DashboardService._ap_processor_can_see_all_cases(user=user, tenant=tenant):
                return qs
            return qs.filter(result__invoice__document_upload__uploaded_by=user)

        if user_role == UserRole.REVIEWER:
            return qs.filter(
                result__review_assignments__assigned_to=user
            ).distinct()

        return qs

    @staticmethod
    def _scope_reviews(qs, user=None, tenant=None):
        """Restrict review assignments based on user role and tenant."""
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if user is None:
            return qs
        user_role = getattr(user, "role", None)

        if user_role == UserRole.AP_PROCESSOR:
            if DashboardService._ap_processor_can_see_all_cases(user=user, tenant=tenant):
                return qs
            return qs.filter(
                reconciliation_result__invoice__document_upload__uploaded_by=user
            )

        if user_role == UserRole.REVIEWER:
            return qs.filter(assigned_to=user)

        return qs

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------
    @staticmethod
    def get_summary(user=None, tenant=None) -> Dict[str, Any]:
        invoice_qs = DashboardService._scope_invoices(Invoice.objects.all(), user, tenant)
        recon_qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user, tenant)
        exception_qs = DashboardService._scope_exceptions(ReconciliationException.objects.filter(resolved=False), user, tenant)
        review_qs = DashboardService._scope_reviews(ReviewAssignment.objects.filter(
            status__in=[ReviewStatus.PENDING, ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW]
        ), user, tenant)

        total_results = recon_qs.count()
        matched = recon_qs.filter(match_status=MatchStatus.MATCHED).count()
        avg_conf = recon_qs.aggregate(
            avg=Avg("deterministic_confidence")
        )["avg"] or 0.0

        from apps.core.enums import InvoiceStatus
        extracted = invoice_qs.exclude(
            status__in=[InvoiceStatus.UPLOADED, InvoiceStatus.EXTRACTION_IN_PROGRESS]
        ).count()
        reconciled = invoice_qs.filter(
            status=InvoiceStatus.RECONCILED
        ).count()

        return {
            "total_invoices": invoice_qs.count(),
            "total_pos": PurchaseOrder.objects.filter(tenant=tenant).count() if tenant else PurchaseOrder.objects.count(),
            "total_grns": GoodsReceiptNote.objects.filter(tenant=tenant).count() if tenant else GoodsReceiptNote.objects.count(),
            "total_vendors": Vendor.objects.filter(is_active=True, tenant=tenant).count() if tenant else Vendor.objects.filter(is_active=True).count(),
            "pending_reviews": review_qs.count(),
            "open_exceptions": exception_qs.count(),
            "matched_pct": round((matched / total_results * 100) if total_results else 0, 1),
            "avg_confidence": round(avg_conf * 100, 1),
            "extracted_count": extracted,
            "reconciled_count": total_results,
            "posted_count": reconciled,
        }

    @staticmethod
    def get_match_status_breakdown(user=None, tenant=None) -> List[Dict[str, Any]]:
        qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user, tenant)
        total = qs.count() or 1
        rows = (
            qs.values("match_status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return [
            {
                "match_status": row["match_status"],
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1),
            }
            for row in rows
        ]

    @staticmethod
    def get_exception_breakdown(user=None, tenant=None) -> List[Dict[str, Any]]:
        qs = DashboardService._scope_exceptions(
            ReconciliationException.objects.filter(resolved=False), user, tenant
        )
        return list(
            qs.values("exception_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

    @staticmethod
    def get_mode_breakdown(user=None, tenant=None) -> List[Dict[str, Any]]:
        """Breakdown of reconciliation results by mode (2-Way vs 3-Way)."""
        qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user, tenant)
        total = qs.count() or 1
        rows = (
            qs.values("reconciliation_mode")
            .annotate(
                count=Count("id"),
                matched_count=Count("id", filter=Q(match_status=MatchStatus.MATCHED)),
                avg_confidence=Avg("deterministic_confidence"),
            )
            .order_by("-count")
        )
        return [
            {
                "reconciliation_mode": row["reconciliation_mode"] or "UNSET",
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1),
                "matched_count": row["matched_count"],
                "match_rate": round(
                    (row["matched_count"] / row["count"] * 100)
                    if row["count"] else 0, 1
                ),
                "avg_confidence": round(
                    (row["avg_confidence"] or 0) * 100, 1
                ),
            }
            for row in rows
        ]

    @staticmethod
    def _scope_agent_runs(qs, user=None, tenant=None):
        """Restrict agent runs based on user role and tenant."""
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if user is None:
            return qs
        user_role = getattr(user, "role", None)

        if user_role == UserRole.AP_PROCESSOR:
            if DashboardService._ap_processor_can_see_all_cases(user=user, tenant=tenant):
                return qs
            return qs.filter(
                reconciliation_result__invoice__document_upload__uploaded_by=user
            )

        if user_role == UserRole.REVIEWER:
            return qs.filter(
                reconciliation_result__review_assignments__assigned_to=user
            ).distinct()

        return qs

    @staticmethod
    def get_agent_performance(user=None, tenant=None) -> List[Dict[str, Any]]:
        qs = DashboardService._scope_agent_runs(AgentRun.objects.all(), user, tenant)
        return list(
            qs.values("agent_type")
            .annotate(
                total_runs=Count("id"),
                success_count=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
                avg_confidence=Avg("confidence"),
                avg_duration_ms=Avg("duration_ms"),
                total_tokens=Sum("total_tokens"),
            )
            .order_by("agent_type")
        )

    @staticmethod
    def get_daily_volume(days: int = 30, user=None, tenant=None) -> List[Dict[str, Any]]:
        since = timezone.now() - timedelta(days=days)
        inv_qs = DashboardService._scope_invoices(
            Invoice.objects.filter(created_at__gte=since), user, tenant
        )
        recon_qs = DashboardService._scope_recon_results(
            ReconciliationResult.objects.filter(created_at__gte=since), user, tenant
        )
        exc_qs = DashboardService._scope_exceptions(
            ReconciliationException.objects.filter(created_at__gte=since), user, tenant
        )
        invoices = dict(
            inv_qs
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        reconciled = dict(
            recon_qs
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        exceptions = dict(
            exc_qs
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        all_dates = sorted(set(list(invoices) + list(reconciled) + list(exceptions)))
        return [
            {
                "date": d,
                "invoices": invoices.get(d, 0),
                "reconciled": reconciled.get(d, 0),
                "exceptions": exceptions.get(d, 0),
            }
            for d in all_dates
        ]

    @staticmethod
    def get_recent_activity(limit: int = 20, user=None, tenant=None) -> List[Dict[str, Any]]:
        activities: List[Dict[str, Any]] = []

        inv_qs = DashboardService._scope_invoices(Invoice.objects.all(), user, tenant)
        for inv in inv_qs.order_by("-created_at")[:limit]:
            activities.append({
                "id": inv.pk,
                "entity_type": "Invoice",
                "description": f"Invoice {inv.invoice_number or '(no number)'} uploaded",
                "status": inv.status,
                "timestamp": inv.created_at,
            })

        recon_qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user, tenant)
        for r in recon_qs.order_by("-created_at")[:limit]:
            activities.append({
                "id": r.pk,
                "entity_type": "Reconciliation",
                "description": f"Result #{r.pk} — {r.match_status}",
                "status": r.match_status,
                "timestamp": r.created_at,
            })

        review_qs = DashboardService._scope_reviews(ReviewAssignment.objects.all(), user, tenant)
        for ra in review_qs.order_by("-created_at")[:limit]:
            activities.append({
                "id": ra.pk,
                "entity_type": "Review",
                "description": f"Review #{ra.pk} — {ra.status}",
                "status": ra.status,
                "timestamp": ra.created_at,
            })

        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        return activities[:limit]


# =========================================================================
# Agent Performance Dashboard Service
# =========================================================================
class AgentPerformanceDashboardService:
    """Read-only aggregation service for the Agent Performance Command Center."""

    # Role sets for permission gating
    _GOVERNANCE_ROLES = {UserRole.ADMIN, UserRole.AUDITOR}
    _EXTENDED_ROLES = {UserRole.ADMIN, UserRole.AUDITOR, UserRole.FINANCE_MANAGER}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _user_role(user):
        return getattr(user, "role", None) if user else None

    @staticmethod
    def _can_see_governance(user):
        role = AgentPerformanceDashboardService._user_role(user)
        return role in AgentPerformanceDashboardService._GOVERNANCE_ROLES

    @staticmethod
    def _can_see_extended(user):
        role = AgentPerformanceDashboardService._user_role(user)
        return role in AgentPerformanceDashboardService._EXTENDED_ROLES

    @staticmethod
    def _parse_filters(filters: Optional[Dict] = None) -> Dict:
        """Normalize incoming filter params."""
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
        if f.get("trace_id"):
            result["trace_id"] = f["trace_id"]
        return result

    @staticmethod
    def _base_runs_qs(filters: Optional[Dict] = None, user=None, tenant=None):
        """Build a base queryset for AgentRun with common filters applied."""
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
        if "trace_id" in f:
            qs = qs.filter(trace_id__icontains=f["trace_id"])
        return qs

    # ------------------------------------------------------------------
    # 1. Summary KPIs
    # ------------------------------------------------------------------
    @staticmethod
    def get_summary(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        today = timezone.now().date()
        today_qs = qs.filter(created_at__date=today)

        total_today = today_qs.count()
        active_types = today_qs.values("agent_type").distinct().count()
        completed_today = today_qs.filter(status=AgentRunStatus.COMPLETED).count()
        failed_today = today_qs.filter(status=AgentRunStatus.FAILED).count()
        success_rate = round(completed_today / total_today * 100, 1) if total_today else 0
        escalation_count = AgentEscalation.objects.filter(
            agent_run__in=today_qs
        ).count()
        escalation_rate = round(escalation_count / total_today * 100, 1) if total_today else 0

        agg = today_qs.aggregate(
            avg_runtime=Avg("duration_ms"),
            total_cost=Sum("cost_estimate"),
        )

        # Access denied events today
        from apps.auditlog.models import AuditEvent
        denied_qs = AuditEvent.objects.filter(
            created_at__date=today,
            access_granted=False,
        )
        if tenant is not None:
            denied_qs = denied_qs.filter(tenant=tenant)
        denied_today = denied_qs.count()

        # Governed runs % — runs with trace_id AND (recommendation OR decision log)
        runs_with_trace = today_qs.exclude(trace_id="").count()
        runs_with_rec = today_qs.filter(recommendations__isnull=False).distinct().count()
        runs_with_decision = today_qs.filter(decisions__isnull=False).distinct().count()
        governed = today_qs.exclude(trace_id="").filter(
            Q(recommendations__isnull=False) | Q(decisions__isnull=False)
        ).distinct().count()
        governed_pct = round(governed / total_today * 100, 1) if total_today else 0

        return {
            "total_runs_today": total_today,
            "active_agents": active_types,
            "success_rate": success_rate,
            "escalation_rate": escalation_rate,
            "avg_runtime_ms": round(agg["avg_runtime"] or 0, 0),
            "estimated_cost_today": float(agg["total_cost"] or 0),
            "access_denied_today": denied_today,
            "governed_pct": governed_pct,
            "completed_today": completed_today,
            "failed_today": failed_today,
            "escalation_count": escalation_count,
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
        # Convert to serializable format
        for row in by_hour:
            row["hour"] = row["hour"].isoformat() if row["hour"] else ""

        return {"by_type": by_type, "by_hour": by_hour}

    # ------------------------------------------------------------------
    # 3. Success metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_success_metrics(filters=None, user=None, tenant=None) -> List[Dict[str, Any]]:
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
                has_trace=Count("id", filter=~Q(trace_id="")),
                has_decision=Count("id", filter=Q(decisions__isnull=False), distinct=True),
                has_recommendation=Count("id", filter=Q(recommendations__isnull=False), distinct=True),
            )
            .order_by("agent_type")
        )
        result = []
        for r in rows:
            total = r["total_runs"] or 1
            governed = min(r["has_trace"], r["has_decision"] + r["has_recommendation"])
            result.append({
                "agent_type": r["agent_type"],
                "total_runs": r["total_runs"],
                "success_pct": round(r["success_count"] / total * 100, 1),
                "failed_pct": round(r["failed_count"] / total * 100, 1),
                "escalations": r["escalation_count"],
                "avg_confidence": round((r["avg_confidence"] or 0) * 100, 1),
                "avg_duration_ms": round(r["avg_duration_ms"] or 0, 0),
                "governed_pct": round(governed / total * 100, 1),
                "trace_coverage_pct": round(r["has_trace"] / total * 100, 1),
            })
        return result

    # ------------------------------------------------------------------
    # 4. Latency metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_latency_metrics(filters=None, user=None, tenant=None) -> Dict[str, Any]:
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
                "started_at", "trace_id",
                "reconciliation_result__invoice__invoice_number",
                "reconciliation_result__invoice__id",
            )
        )
        for row in slowest:
            row["has_trace"] = bool(row.get("trace_id"))
            row["invoice_number"] = row.pop("reconciliation_result__invoice__invoice_number", "")
            row["invoice_id"] = row.pop("reconciliation_result__invoice__id", None)

        return {"per_agent": per_agent, "slowest_runs": slowest}

    # ------------------------------------------------------------------
    # 5. Token & cost metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_token_metrics(filters=None, user=None, tenant=None) -> Dict[str, Any]:
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
        # Decimal → float for JSON
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
    # 6. Tool metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_tool_metrics(filters=None, user=None, tenant=None) -> Dict[str, Any]:
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

        # summary helpers
        most_used = by_tool[0]["tool_name"] if by_tool else "—"
        slowest = max(by_tool, key=lambda x: x["avg_duration"] or 0)["tool_name"] if by_tool else "—"
        most_failed = max(by_tool, key=lambda x: x["failed"])["tool_name"] if by_tool else "—"

        return {
            "by_tool": by_tool,
            "most_used": most_used,
            "slowest_tool": slowest,
            "most_failed": most_failed,
        }

    # ------------------------------------------------------------------
    # 7. Recommendation metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_recommendation_metrics(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        run_ids = qs.values_list("id", flat=True)

        rec_qs = AgentRecommendation.objects.filter(agent_run_id__in=list(run_ids))
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
                "acceptance_rate": round(accepted / decided * 100, 1) if decided else None,
            })

        return {"by_type": by_type, "total": rec_qs.count()}

    # ------------------------------------------------------------------
    # 8. Live feed
    # ------------------------------------------------------------------
    @staticmethod
    def get_live_feed(filters=None, user=None, tenant=None, limit=25) -> List[Dict[str, Any]]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        runs = (
            qs.select_related("reconciliation_result__invoice")
            .order_by("-created_at")[:limit]
        )
        show_rbac = AgentPerformanceDashboardService._can_see_governance(user)
        feed = []
        for run in runs:
            inv = getattr(run.reconciliation_result, "invoice", None) if run.reconciliation_result else None
            entry = {
                "id": run.pk,
                "agent_type": run.agent_type,
                "invoice_number": getattr(inv, "invoice_number", "") or "",
                "invoice_id": getattr(inv, "id", None),
                "summary": run.summarized_reasoning[:120] if run.summarized_reasoning else "",
                "confidence": round((run.confidence or 0) * 100, 1),
                "duration_ms": run.duration_ms,
                "status": run.status,
                "has_trace": bool(run.trace_id),
                "created_at": run.created_at.isoformat() if run.created_at else "",
            }
            if show_rbac:
                entry["actor_role"] = run.permission_checked or ""
                entry["trace_id"] = run.trace_id
            feed.append(entry)
        return feed

    # ------------------------------------------------------------------
    # 9. Escalation metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_escalation_metrics(filters=None, user=None, tenant=None) -> List[Dict[str, Any]]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        run_ids = qs.values_list("id", flat=True)
        return list(
            AgentEscalation.objects.filter(agent_run_id__in=run_ids)
            .select_related("agent_run__reconciliation_result__invoice")
            .order_by("-created_at")[:20]
            .values(
                "id", "agent_run__agent_type", "reason", "severity",
                "suggested_assignee_role", "resolved", "created_at",
                "agent_run__reconciliation_result__invoice__invoice_number",
            )
        )

    # ------------------------------------------------------------------
    # 10. Failure metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_failure_metrics(filters=None, user=None, tenant=None) -> Dict[str, Any]:
        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        failed = qs.filter(status=AgentRunStatus.FAILED)

        # Categorize failures heuristically from error_message
        categories = {
            "tool_failure": 0,
            "llm_timeout": 0,
            "invalid_response": 0,
            "missing_data": 0,
            "permission_issue": 0,
            "integration_issue": 0,
            "other": 0,
        }
        for err in failed.values_list("error_message", flat=True):
            msg = (err or "").lower()
            if "tool" in msg:
                categories["tool_failure"] += 1
            elif "timeout" in msg or "timed out" in msg:
                categories["llm_timeout"] += 1
            elif "invalid" in msg or "parse" in msg or "json" in msg:
                categories["invalid_response"] += 1
            elif "missing" in msg or "not found" in msg:
                categories["missing_data"] += 1
            elif "permission" in msg or "access" in msg or "denied" in msg:
                categories["permission_issue"] += 1
            elif "integration" in msg or "external" in msg or "api" in msg:
                categories["integration_issue"] += 1
            else:
                categories["other"] += 1

        return {
            "total_failed": failed.count(),
            "categories": categories,
        }

    # ------------------------------------------------------------------
    # 11. Governance metrics (ADMIN / AUDITOR only)
    # ------------------------------------------------------------------
    @staticmethod
    def get_governance_metrics(filters=None, user=None, tenant=None) -> Optional[Dict[str, Any]]:
        if not AgentPerformanceDashboardService._can_see_governance(user):
            return None

        qs = AgentPerformanceDashboardService._base_runs_qs(filters, user, tenant)
        total = qs.count() or 1

        from apps.auditlog.models import AuditEvent

        f = AgentPerformanceDashboardService._parse_filters(filters)
        audit_qs = AuditEvent.objects.all()
        if tenant is not None:
            audit_qs = audit_qs.filter(tenant=tenant)
        if "date_from" in f:
            audit_qs = audit_qs.filter(created_at__date__gte=f["date_from"])
        if "date_to" in f:
            audit_qs = audit_qs.filter(created_at__date__lte=f["date_to"])

        granted = audit_qs.filter(access_granted=True).count()
        denied = audit_qs.filter(access_granted=False).count()

        perm_by_role = list(
            audit_qs.exclude(actor_primary_role="")
            .values("actor_primary_role")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        runs_by_role = list(
            qs.exclude(permission_checked="")
            .values("permission_checked")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        missing_trace = qs.filter(Q(trace_id="") | Q(trace_id__isnull=True)).count()
        no_recommendation = qs.exclude(recommendations__isnull=False).distinct().count()
        no_decision = qs.exclude(decisions__isnull=False).distinct().count()

        failed_tools = ToolCall.objects.filter(
            agent_run__in=qs,
            status=ToolCallStatus.FAILED,
        ).count()

        escalation_count = AgentEscalation.objects.filter(agent_run__in=qs).count()

        # Access denial feed (most recent)
        denial_feed = list(
            audit_qs.filter(access_granted=False)
            .order_by("-created_at")[:15]
            .values(
                "created_at", "actor_email", "actor_primary_role",
                "permission_checked", "permission_source", "trace_id",
            )
        )

        return {
            "access_granted": granted,
            "access_denied": denied,
            "permission_by_role": perm_by_role,
            "runs_by_actor_role": runs_by_role,
            "missing_trace": missing_trace,
            "no_recommendation": no_recommendation,
            "no_decision": no_decision,
            "failed_tool_calls": failed_tools,
            "escalation_count": escalation_count,
            "denial_feed": denial_feed,
        }

    # ------------------------------------------------------------------
    # 12. Trace detail (single run)
    # ------------------------------------------------------------------
    @staticmethod
    def get_trace_detail(run_id, user=None, tenant=None) -> Optional[Dict[str, Any]]:
        qs = DashboardService._scope_agent_runs(AgentRun.objects.all(), user, tenant)
        try:
            run = qs.select_related(
                "reconciliation_result__invoice",
                "agent_definition",
            ).get(pk=run_id)
        except AgentRun.DoesNotExist:
            return None

        role = AgentPerformanceDashboardService._user_role(user)
        is_gov = role in AgentPerformanceDashboardService._GOVERNANCE_ROLES
        is_ext = role in AgentPerformanceDashboardService._EXTENDED_ROLES

        inv = getattr(run.reconciliation_result, "invoice", None) if run.reconciliation_result else None

        data: Dict[str, Any] = {
            "id": run.pk,
            "agent_type": run.agent_type,
            "status": run.status,
            "confidence": round((run.confidence or 0) * 100, 1),
            "duration_ms": run.duration_ms,
            "started_at": run.started_at.isoformat() if run.started_at else "",
            "completed_at": run.completed_at.isoformat() if run.completed_at else "",
            "error_message": run.error_message if is_ext else "",
            "summarized_reasoning": run.summarized_reasoning,
            "invocation_reason": run.invocation_reason,
            "invoice_number": getattr(inv, "invoice_number", "") or "",
            "invoice_id": getattr(inv, "id", None),
            "reconciliation_result_id": run.reconciliation_result_id,
        }

        # Trace fields — governance only
        if is_gov:
            data.update({
                "trace_id": run.trace_id,
                "span_id": run.span_id,
                "prompt_version": run.prompt_version,
                "actor_user_id": run.actor_user_id,
                "permission_checked": run.permission_checked,
                "cost_estimate": float(run.actual_cost_usd or run.cost_estimate or 0),
                "llm_model_used": run.llm_model_used,
                "prompt_tokens": run.prompt_tokens,
                "completion_tokens": run.completion_tokens,
                "total_tokens": run.total_tokens,
            })
        elif is_ext:
            data.update({
                "trace_id": run.trace_id,
                "cost_estimate": float(run.actual_cost_usd or run.cost_estimate or 0),
                "total_tokens": run.total_tokens,
            })

        # Timeline events
        timeline = []

        # Start event
        if run.started_at:
            timeline.append({
                "time": run.started_at.isoformat(),
                "event": "agent_started",
                "label": f"{run.agent_type} started",
            })

        # Tool calls
        for tc in run.tool_calls.order_by("created_at"):
            timeline.append({
                "time": tc.created_at.isoformat(),
                "event": "tool_called",
                "label": f"Tool: {tc.tool_name}",
                "status": tc.status,
                "duration_ms": tc.duration_ms,
            })

        # Decisions
        for d in run.decisions.order_by("created_at"):
            timeline.append({
                "time": d.created_at.isoformat(),
                "event": "decision_created",
                "label": f"Decision: {d.decision_type}",
                "confidence": d.confidence,
            })

        # Recommendations
        for rec in run.recommendations.order_by("created_at"):
            timeline.append({
                "time": rec.created_at.isoformat(),
                "event": "recommendation_created",
                "label": f"Rec: {rec.recommendation_type}",
                "accepted": rec.accepted,
            })

        # Escalations
        for esc in run.escalations.order_by("created_at"):
            timeline.append({
                "time": esc.created_at.isoformat(),
                "event": "escalation_created",
                "label": f"Escalation: {esc.severity}",
                "reason": esc.reason,
            })

        # Completion
        if run.completed_at:
            timeline.append({
                "time": run.completed_at.isoformat(),
                "event": "agent_completed" if run.status == AgentRunStatus.COMPLETED else "agent_failed",
                "label": f"{run.agent_type} {run.status.lower()}",
            })

        timeline.sort(key=lambda x: x["time"])
        data["timeline"] = timeline

        return data
