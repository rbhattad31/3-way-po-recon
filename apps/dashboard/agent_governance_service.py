"""Agent Governance Dashboard Service — RBAC, authorization, and compliance monitoring.

Focused on: RBAC coverage, guardrail outcomes, authorized vs denied runs,
tool authorization, recommendation authorization, protected actions,
system-agent oversight, identity attribution, and traceability.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.agents.models import AgentEscalation, AgentRecommendation, AgentRun
from apps.core.enums import AgentRunStatus, AuditEventType, ToolCallStatus, UserRole
from apps.agents.services.guardrails_service import AGENT_PERMISSIONS, TOOL_PERMISSIONS
from apps.tools.models import ToolCall


# Recommendation types that require specific permissions
_RECOMMENDATION_PERMISSIONS = {
    "AUTO_CLOSE": "recommendations.auto_close",
    "SEND_TO_AP_REVIEW": "recommendations.route_review",
    "ESCALATE_TO_MANAGER": "cases.escalate",
    "REPROCESS_EXTRACTION": "recommendations.reprocess",
    "SEND_TO_PROCUREMENT": "recommendations.route_procurement",
    "SEND_TO_VENDOR_CLARIFICATION": "recommendations.vendor_clarification",
}


class AgentGovernanceDashboardService:
    """Read-only aggregation service for the Agent Governance Dashboard.

    ADMIN and AUDITOR get full visibility.
    FINANCE_MANAGER gets summary-level data.
    Other roles are denied at the view layer.
    """

    FULL_ACCESS_ROLES = {UserRole.ADMIN, UserRole.AUDITOR}
    SUMMARY_ROLES = {UserRole.ADMIN, UserRole.AUDITOR, UserRole.FINANCE_MANAGER}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _user_role(user):
        return getattr(user, "role", None) if user else None

    @staticmethod
    def _has_full_access(user) -> bool:
        return AgentGovernanceDashboardService._user_role(user) in (
            AgentGovernanceDashboardService.FULL_ACCESS_ROLES
        )

    @staticmethod
    def _parse_filters(filters: Optional[Dict] = None) -> Dict:
        f = filters or {}
        result = {}
        for key in (
            "date_from", "date_to", "agent_type", "status",
            "trace_id", "actor_role", "permission",
        ):
            if f.get(key):
                result[key] = f[key]
        return result

    @staticmethod
    def _base_runs_qs(filters: Optional[Dict] = None):
        """All runs — governance users see everything."""
        qs = AgentRun.objects.all()
        f = AgentGovernanceDashboardService._parse_filters(filters)

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
        if "actor_role" in f:
            qs = qs.filter(actor_primary_role=f["actor_role"])
        return qs

    @staticmethod
    def _audit_qs(filters: Optional[Dict] = None):
        from apps.auditlog.models import AuditEvent
        qs = AuditEvent.objects.all()
        f = AgentGovernanceDashboardService._parse_filters(filters)
        if "date_from" in f:
            qs = qs.filter(created_at__date__gte=f["date_from"])
        if "date_to" in f:
            qs = qs.filter(created_at__date__lte=f["date_to"])
        if "actor_role" in f:
            qs = qs.filter(actor_primary_role=f["actor_role"])
        if "permission" in f:
            qs = qs.filter(permission_checked__icontains=f["permission"])
        return qs

    # ------------------------------------------------------------------
    # Sensitive field scrubbing for non-full-access users
    # ------------------------------------------------------------------
    @staticmethod
    def _scrub_sensitive(data: Dict, user) -> Dict:
        """Remove sensitive governance fields for FINANCE_MANAGER (summary only)."""
        if AgentGovernanceDashboardService._has_full_access(user):
            return data
        sensitive_keys = {
            "actor_user_id", "actor_roles_snapshot", "permission_source",
            "prompt_version", "denial_reason", "actor_email",
        }
        return {k: v for k, v in data.items() if k not in sensitive_keys}

    # ------------------------------------------------------------------
    # 1. Governance Health KPI Summary
    # ------------------------------------------------------------------
    @staticmethod
    def get_summary(filters=None, user=None) -> Dict[str, Any]:
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)
        total = run_qs.count() or 1

        # RBAC coverage
        with_perm = run_qs.exclude(
            Q(permission_checked="") | Q(permission_checked__isnull=True)
        ).count()
        rbac_coverage = round(with_perm / total * 100, 1)

        # Authorized runs
        authorized = run_qs.filter(access_granted=True).count()
        authorized_pct = round(authorized / total * 100, 1)

        # Denied guardrail checks
        denied_guardrails = audit_qs.filter(
            event_type__in=[
                AuditEventType.GUARDRAIL_DENIED,
            ],
            access_granted=False,
        ).count()

        # Denied tool calls
        denied_tools = audit_qs.filter(
            event_type=AuditEventType.TOOL_CALL_DENIED,
        ).count()

        # Blocked recommendations
        blocked_recs = audit_qs.filter(
            event_type=AuditEventType.RECOMMENDATION_DENIED,
        ).count()

        # Protected actions denied
        protected_denied = audit_qs.filter(
            event_type__in=[
                AuditEventType.AUTO_CLOSE_DENIED,
            ],
            access_granted=False,
        ).count()

        # SYSTEM_AGENT vs user-triggered
        system_runs = run_qs.filter(permission_source="SYSTEM_AGENT").count()
        user_runs = run_qs.filter(permission_source="USER").count()
        unattributed = run_qs.filter(
            Q(permission_source="") | Q(permission_source__isnull=True)
        ).count()

        # Trace coverage
        with_trace = run_qs.exclude(
            Q(trace_id="") | Q(trace_id__isnull=True)
        ).count()
        trace_coverage = round(with_trace / total * 100, 1)

        # Total denied (guardrails + tools + recs + protected)
        access_denied = denied_guardrails + denied_tools + blocked_recs + protected_denied

        return {
            "total_runs": total if total > 1 else run_qs.count(),
            "rbac_coverage_pct": rbac_coverage,
            "authorized_runs_pct": authorized_pct,
            "access_granted": authorized,
            "access_denied": access_denied,
            "trace_coverage_pct": trace_coverage,
            "permission_compliance_pct": authorized_pct,
            "denied_guardrails": denied_guardrails,
            "denied_tool_calls": denied_tools,
            "blocked_recommendations": blocked_recs,
            "protected_actions_denied": protected_denied,
            "system_agent_runs": system_runs,
            "user_triggered_runs": user_runs,
            "unattributed_runs": unattributed,
        }

    # ------------------------------------------------------------------
    # 2. Execution Identity Breakdown
    # ------------------------------------------------------------------
    @staticmethod
    def get_execution_identity(filters=None, user=None) -> Dict[str, Any]:
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        total = run_qs.count() or 1

        # By permission source
        by_source = list(
            run_qs.exclude(
                Q(permission_source="") | Q(permission_source__isnull=True)
            )
            .values("permission_source")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # By actor primary role (with granted/denied breakdown)
        by_role = list(
            run_qs.exclude(
                Q(actor_primary_role="") | Q(actor_primary_role__isnull=True)
            )
            .values("actor_primary_role")
            .annotate(
                total=Count("id"),
                granted=Count("id", filter=Q(access_granted=True)),
                denied=Count("id", filter=Q(access_granted=False)),
            )
            .order_by("-total")
        )
        for row in by_role:
            row["role"] = row["actor_primary_role"]

        # With vs without identity
        with_identity = run_qs.exclude(actor_user_id__isnull=True).count()
        missing_identity = total - with_identity if total > 1 else (
            run_qs.filter(actor_user_id__isnull=True).count()
        )

        # Top triggering users (ADMIN/AUDITOR only)
        top_users = []
        if AgentGovernanceDashboardService._has_full_access(user):
            top_users = list(
                run_qs.exclude(actor_user_id__isnull=True)
                .values("actor_user_id", "actor_primary_role")
                .annotate(count=Count("id"))
                .order_by("-count")[:10]
            )

        return {
            "by_source": by_source,
            "by_role": by_role,
            "with_identity": with_identity,
            "missing_identity": missing_identity,
            "identity_pct": round(with_identity / total * 100, 1),
            "top_users": top_users,
        }

    # ------------------------------------------------------------------
    # 3. Agent Authorization Matrix
    # ------------------------------------------------------------------
    @staticmethod
    def get_authorization_matrix(filters=None, user=None) -> Dict[str, Any]:
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        rows = (
            run_qs.values("agent_type")
            .annotate(
                total=Count("id"),
                authorized=Count("id", filter=Q(access_granted=True)),
                denied=Count("id", filter=Q(access_granted=False)),
                system_agent=Count(
                    "id", filter=Q(permission_source="SYSTEM_AGENT")
                ),
            )
            .order_by("agent_type")
        )
        result = []
        for r in rows:
            total = r["total"] or 1
            perm_code = AGENT_PERMISSIONS.get(r["agent_type"], "")
            source = "SYSTEM_AGENT" if r["system_agent"] > r["total"] // 2 else "MIXED"
            result.append({
                "agent_type": r["agent_type"],
                "permission": perm_code or r["agent_type"],
                "checks": r["total"],
                "total": r["total"],
                "granted": r["authorized"],
                "authorized": r["authorized"],
                "denied": r["denied"],
                "authorized_pct": round(r["authorized"] / total * 100, 1),
                "source": source,
                "system_agent_runs": r["system_agent"],
            })
        return {"permissions": result}

    # ------------------------------------------------------------------
    # 4. Tool Authorization Dashboard
    # ------------------------------------------------------------------
    @staticmethod
    def get_tool_authorization(filters=None, user=None) -> Dict[str, Any]:
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)
        tool_events = audit_qs.filter(
            event_type__in=[
                AuditEventType.TOOL_CALL_AUTHORIZED,
                AuditEventType.TOOL_CALL_DENIED,
            ]
        )

        # Reverse-map permission code → tool name
        perm_to_tool = {v: k for k, v in TOOL_PERMISSIONS.items()}

        by_tool = list(
            tool_events.values("permission_checked")
            .annotate(
                total=Count("id"),
                authorized=Count("id", filter=Q(access_granted=True)),
                denied=Count("id", filter=Q(access_granted=False)),
            )
            .order_by("-total")
        )

        # Also get avg duration from ToolCall if possible
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        run_ids = run_qs.values_list("id", flat=True)
        tool_durations = dict(
            ToolCall.objects.filter(agent_run_id__in=run_ids)
            .values("tool_name")
            .annotate(avg_dur=Avg("duration_ms"))
            .values_list("tool_name", "avg_dur")
        )

        for row in by_tool:
            perm = row["permission_checked"]
            row["tool_name"] = perm_to_tool.get(perm, perm or "unknown")
            row["required_permission"] = perm
            row["calls"] = row["total"]
            row["authorization_rate"] = round(
                row["authorized"] / (row["total"] or 1) * 100, 1
            )
            row["avg_duration"] = None
            for tool_name, dur in tool_durations.items():
                if tool_name == row["tool_name"]:
                    row["avg_duration"] = round(dur, 0) if dur else None
                    break

        return {
            "total_events": tool_events.count(),
            "authorized": tool_events.filter(access_granted=True).count(),
            "denied": tool_events.filter(access_granted=False).count(),
            "tools": by_tool,
            "by_tool": by_tool,
        }

    # ------------------------------------------------------------------
    # 5. Recommendation Governance
    # ------------------------------------------------------------------
    @staticmethod
    def get_recommendation_governance(filters=None, user=None) -> Dict[str, Any]:
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)
        rec_events = audit_qs.filter(
            event_type__in=[
                AuditEventType.RECOMMENDATION_ACCEPTED,
                AuditEventType.RECOMMENDATION_DENIED,
            ]
        )

        # Also pull operational recommendation data
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        run_ids = list(run_qs.values_list("id", flat=True))
        rec_qs = AgentRecommendation.objects.filter(agent_run_id__in=run_ids)

        by_type = []
        for rec_type, perm_code in _RECOMMENDATION_PERMISSIONS.items():
            sub = rec_qs.filter(recommendation_type=rec_type)
            gen_count = sub.count()
            accepted = sub.filter(accepted=True).count()
            rejected = sub.filter(accepted=False).count()
            pending = sub.filter(accepted__isnull=True).count()

            # Auth data from audit events
            auth_granted = rec_events.filter(
                event_type=AuditEventType.RECOMMENDATION_ACCEPTED,
                permission_checked__icontains=rec_type.lower(),
            ).count()
            auth_denied = rec_events.filter(
                event_type=AuditEventType.RECOMMENDATION_DENIED,
                permission_checked__icontains=rec_type.lower(),
            ).count()

            decided = accepted + rejected
            by_type.append({
                "recommendation_type": rec_type,
                "type": rec_type,
                "required_permission": perm_code,
                "generated": gen_count,
                "total": gen_count,
                "accepted": accepted,
                "rejected": rejected,
                "pending": pending,
                "authorized": auth_granted,
                "denied": auth_denied,
                "acceptance_rate": (
                    round(accepted / decided * 100, 1) if decided else None
                ),
            })

        return {
            "by_type": by_type,
            "recommendations": by_type,
            "total_generated": rec_qs.count(),
            "total_auth_events": rec_events.count(),
        }

    # ------------------------------------------------------------------
    # 6. Protected Action Outcomes
    # ------------------------------------------------------------------
    @staticmethod
    def get_protected_action_outcomes(filters=None, user=None) -> List[Dict[str, Any]]:
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)

        actions = [
            {
                "action": "Auto Close",
                "granted_type": AuditEventType.AUTO_CLOSE_AUTHORIZED,
                "denied_type": AuditEventType.AUTO_CLOSE_DENIED,
            },
            {
                "action": "Assign Review",
                "granted_type": AuditEventType.GUARDRAIL_GRANTED,
                "denied_type": AuditEventType.GUARDRAIL_DENIED,
                "perm_filter": "reviews.assign",
            },
            {
                "action": "Escalation",
                "granted_type": AuditEventType.GUARDRAIL_GRANTED,
                "denied_type": AuditEventType.GUARDRAIL_DENIED,
                "perm_filter": "cases.escalate",
            },
            {
                "action": "Reprocess Extraction",
                "granted_type": AuditEventType.GUARDRAIL_GRANTED,
                "denied_type": AuditEventType.GUARDRAIL_DENIED,
                "perm_filter": "extraction.reprocess",
            },
        ]

        result = []
        for action in actions:
            q_granted = Q(event_type=action["granted_type"], access_granted=True)
            q_denied = Q(event_type=action["denied_type"], access_granted=False)

            if "perm_filter" in action:
                q_granted &= Q(permission_checked__icontains=action["perm_filter"])
                q_denied &= Q(permission_checked__icontains=action["perm_filter"])

            granted = audit_qs.filter(q_granted).count()
            denied = audit_qs.filter(q_denied).count()
            total = granted + denied

            result.append({
                "action": action["action"],
                "event_type": action["action"],
                "total_attempts": total,
                "authorized": granted,
                "granted": granted,
                "denied": denied,
                "authorization_rate": (
                    round(granted / total * 100, 1) if total else None
                ),
            })

        return {"actions": result}

    # ------------------------------------------------------------------
    # 7. Denied Operations
    # ------------------------------------------------------------------
    @staticmethod
    def get_denials(filters=None, user=None, limit=50) -> Dict[str, Any]:
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)
        denied_qs = audit_qs.filter(access_granted=False)

        events = list(
            denied_qs.order_by("-created_at")[:limit].values(
                "id", "created_at", "event_type", "entity_type", "entity_id",
                "actor_email", "actor_primary_role", "permission_checked",
                "permission_source", "trace_id",
                "invoice_id", "case_id", "reconciliation_result_id",
            )
        )

        # Add JS-compatible aliases
        for ev in events:
            ev["actor_role"] = ev.get("actor_primary_role", "")
            ev["permission"] = ev.get("permission_checked", "")
            ev["source"] = ev.get("permission_source", "")

        # Scrub sensitives for non-full-access users
        if not AgentGovernanceDashboardService._has_full_access(user):
            for ev in events:
                ev.pop("actor_email", None)

        # Top denied permissions
        top_denied = list(
            denied_qs.exclude(
                Q(permission_checked="") | Q(permission_checked__isnull=True)
            )
            .values("permission_checked")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # Granted vs denied totals
        granted_total = audit_qs.filter(access_granted=True).count()
        denied_total = denied_qs.count()

        return {
            "events": events,
            "top_denied_permissions": top_denied,
            "granted_total": granted_total,
            "denied_total": denied_total,
        }

    # ------------------------------------------------------------------
    # 8. Guardrail Coverage Trend
    # ------------------------------------------------------------------
    @staticmethod
    def get_coverage_trend(filters=None, user=None) -> Dict[str, Any]:
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)

        # Daily trend for runs
        daily_runs = list(
            run_qs.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                total=Count("id"),
                with_identity=Count(
                    "id", filter=~Q(actor_user_id__isnull=True)
                ),
                with_permission=Count(
                    "id",
                    filter=~Q(permission_checked="")
                    & Q(permission_checked__isnull=False),
                ),
                with_access_granted=Count(
                    "id", filter=~Q(access_granted__isnull=True)
                ),
            )
            .order_by("date")
        )
        for row in daily_runs:
            t = row["total"] or 1
            row["identity_pct"] = round(row["with_identity"] / t * 100, 1)
            row["permission_pct"] = round(row["with_permission"] / t * 100, 1)
            row["access_granted_pct"] = round(
                row["with_access_granted"] / t * 100, 1
            )
            # JS-compatible aliases
            row["rbac_pct"] = row["permission_pct"]
            row["trace_pct"] = row["identity_pct"]

        # Daily denials
        daily_denials = list(
            audit_qs.filter(access_granted=False)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )

        # Daily blocked recommendations
        daily_blocked_recs = list(
            audit_qs.filter(event_type=AuditEventType.RECOMMENDATION_DENIED)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )

        return {
            "daily": daily_runs,
            "daily_runs": daily_runs,
            "daily_denials": daily_denials,
            "daily_blocked_recommendations": daily_blocked_recs,
        }

    # ------------------------------------------------------------------
    # 9. SYSTEM_AGENT Oversight
    # ------------------------------------------------------------------
    @staticmethod
    def get_system_agent_oversight(filters=None, user=None) -> Dict[str, Any]:
        all_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        sys_qs = all_qs.filter(permission_source="SYSTEM_AGENT")
        total_all = all_qs.count() or 1

        sys_total = sys_qs.count()
        sys_pct = round(sys_total / total_all * 100, 1)

        # Status breakdown
        completed = sys_qs.filter(status=AgentRunStatus.COMPLETED).count()
        failed = sys_qs.filter(status=AgentRunStatus.FAILED).count()

        # Auto-close actions from audit events
        audit_qs = AgentGovernanceDashboardService._audit_qs(filters)
        auto_close_actions = audit_qs.filter(
            event_type=AuditEventType.AUTO_CLOSE_AUTHORIZED,
            permission_source="SYSTEM_AGENT",
        ).count()

        # By agent type with status breakdown
        by_type = list(
            sys_qs.values("agent_type")
            .annotate(
                runs=Count("id"),
                completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
                failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
                avg_duration_ms=Avg("duration_ms"),
            )
            .order_by("-runs")
        )
        for row in by_type:
            row["auto_close"] = 0
            row["avg_duration_ms"] = round(row["avg_duration_ms"] or 0, 0)
            row["count"] = row["runs"]

        # Top denied under SYSTEM_AGENT
        sys_denials = list(
            audit_qs.filter(
                permission_source="SYSTEM_AGENT", access_granted=False
            )
            .values("permission_checked")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        # Tool calls under SYSTEM_AGENT
        sys_run_ids = sys_qs.values_list("id", flat=True)
        sys_tool_calls = list(
            ToolCall.objects.filter(agent_run_id__in=sys_run_ids)
            .values("tool_name")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Cost
        cost_agg = sys_qs.aggregate(
            avg_cost=Avg("cost_estimate"),
            total_cost=Sum("cost_estimate"),
            total_tokens=Sum("total_tokens"),
        )

        return {
            "total_runs": sys_total,
            "total_system_runs": sys_total,
            "completed": completed,
            "failed": failed,
            "auto_close_actions": auto_close_actions,
            "percentage_of_total": sys_pct,
            "by_type": by_type,
            "by_agent": by_type,
            "top_denied": sys_denials,
            "tool_calls": sys_tool_calls,
            "avg_cost": float(cost_agg["avg_cost"] or 0),
            "total_cost": float(cost_agg["total_cost"] or 0),
            "total_tokens": cost_agg["total_tokens"] or 0,
        }

    # ------------------------------------------------------------------
    # 10. Trace & Authorization Detail (single run)
    # ------------------------------------------------------------------
    @staticmethod
    def get_trace_detail(run_id, user=None) -> Optional[Dict[str, Any]]:
        try:
            run = AgentRun.objects.select_related(
                "reconciliation_result__invoice",
                "agent_definition",
            ).get(pk=run_id)
        except AgentRun.DoesNotExist:
            return None

        inv = (
            getattr(run.reconciliation_result, "invoice", None)
            if run.reconciliation_result
            else None
        )

        data: Dict[str, Any] = {
            "id": run.pk,
            "agent_type": run.agent_type,
            "status": run.status,
            "confidence": round((run.confidence or 0) * 100, 1),
            "duration_ms": run.duration_ms,
            "started_at": (
                run.started_at.isoformat() if run.started_at else ""
            ),
            "completed_at": (
                run.completed_at.isoformat() if run.completed_at else ""
            ),
            "error_message": run.error_message or "",
            "summarized_reasoning": run.summarized_reasoning or "",
            "invocation_reason": run.invocation_reason or "",
            "invoice_number": getattr(inv, "invoice_number", "") or "",
            "invoice_id": getattr(inv, "id", None),
            "reconciliation_result_id": run.reconciliation_result_id,
            # Full governance fields
            "trace_id": run.trace_id or "",
            "span_id": run.span_id or "",
            "parent_span_id": getattr(run, "parent_span_id", "") or "",
            "actor_user_id": run.actor_user_id,
            "actor_primary_role": run.actor_primary_role or "",
            "actor_roles_snapshot": run.actor_roles_snapshot_json or "",
            "permission_checked": run.permission_checked or "",
            "permission_source": run.permission_source or "",
            "access_granted": run.access_granted,
            "prompt_version": run.prompt_version or "",
            "cost_estimate": float(run.cost_estimate or 0),
            "llm_model_used": run.llm_model_used or "",
            "prompt_tokens": run.prompt_tokens or 0,
            "completion_tokens": run.completion_tokens or 0,
            "total_tokens": run.total_tokens or 0,
        }

        # Scrub sensitive fields for summary-only users
        if not AgentGovernanceDashboardService._has_full_access(user):
            for key in ("actor_user_id", "actor_roles_snapshot", "prompt_version"):
                data.pop(key, None)

        # Build governance-focused timeline
        timeline = []

        if run.started_at:
            timeline.append({
                "time": run.started_at.isoformat(),
                "event": "agent_started",
                "category": "lifecycle",
                "label": f"{run.agent_type} invocation "
                + ("authorized" if run.access_granted else "checked"),
                "access_granted": run.access_granted,
            })

        for tc in run.tool_calls.order_by("created_at"):
            timeline.append({
                "time": tc.created_at.isoformat(),
                "event": "tool_authorization",
                "category": "tool",
                "label": f"Tool: {tc.tool_name}",
                "status": tc.status,
                "duration_ms": tc.duration_ms,
            })

        for d in run.decisions.order_by("created_at"):
            timeline.append({
                "time": d.created_at.isoformat(),
                "event": "decision_created",
                "category": "decision",
                "label": f"Decision: {d.decision_type}",
                "confidence": d.confidence,
                "deterministic": d.deterministic_flag,
            })

        for rec in run.recommendations.order_by("created_at"):
            timeline.append({
                "time": rec.created_at.isoformat(),
                "event": "recommendation_authorization",
                "category": "recommendation",
                "label": f"Rec: {rec.recommendation_type}",
                "accepted": rec.accepted,
                "confidence": rec.confidence,
            })

        for esc in run.escalations.order_by("created_at"):
            timeline.append({
                "time": esc.created_at.isoformat(),
                "event": "protected_action",
                "category": "escalation",
                "label": f"Escalation: {esc.severity}",
                "reason": esc.reason or "",
            })

        if run.completed_at:
            suffix = (
                "completed"
                if run.status == AgentRunStatus.COMPLETED
                else "failed"
            )
            timeline.append({
                "time": run.completed_at.isoformat(),
                "event": f"agent_{suffix}",
                "category": "lifecycle",
                "label": f"{run.agent_type} {suffix}",
            })

        timeline.sort(key=lambda x: x["time"])
        data["timeline"] = timeline

        # Related spans
        span_tree = []
        if run.trace_id:
            siblings = (
                AgentRun.objects.filter(trace_id=run.trace_id)
                .exclude(pk=run.pk)
                .order_by("created_at")
                .values(
                    "id", "agent_type", "status", "duration_ms",
                    "span_id", "access_granted", "permission_source",
                )
            )
            span_tree = list(siblings)
        data["span_tree"] = span_tree

        return data

    # ------------------------------------------------------------------
    # 11. Trace Run List (for trace explorer panel)
    # ------------------------------------------------------------------
    @staticmethod
    def get_trace_run_list(filters=None, user=None, limit=50) -> List[Dict[str, Any]]:
        """Return recent agent runs with governance fields for the trace explorer."""
        run_qs = AgentGovernanceDashboardService._base_runs_qs(filters)
        runs = (
            run_qs.select_related("reconciliation_result__invoice")
            .order_by("-created_at")[:limit]
        )
        result = []
        for run in runs:
            inv = (
                getattr(run.reconciliation_result, "invoice", None)
                if run.reconciliation_result
                else None
            )
            result.append({
                "id": run.pk,
                "agent_type": run.agent_type,
                "status": run.status,
                "confidence": round((run.confidence or 0) * 100, 1),
                "duration_ms": run.duration_ms,
                "invoice_number": getattr(inv, "invoice_number", "") or "",
                "created_at": run.created_at.isoformat() if run.created_at else "",
                "has_trace": bool(run.trace_id),
                "access_granted": run.access_granted,
                "actor_primary_role": run.actor_primary_role or "",
                "permission_source": run.permission_source or "",
            })
        return result
