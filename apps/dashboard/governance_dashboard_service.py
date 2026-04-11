"""Governance Dashboard Service — dedicated observability & access analytics."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

from apps.agents.models import AgentEscalation, AgentRun, AgentRecommendation, DecisionLog
from apps.core.enums import AgentRunStatus, AgentType, ToolCallStatus, UserRole
from apps.tools.models import ToolCall


class GovernanceDashboardService:
    """Read-only aggregation service for governance & access observability.

    All methods are ADMIN/AUDITOR-gated.  The caller (API view) enforces
    role checks via ``HasAnyRole``; this service trusts the caller.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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
        if f.get("trace_id"):
            result["trace_id"] = f["trace_id"]
        if f.get("actor_role"):
            result["actor_role"] = f["actor_role"]
        if f.get("permission"):
            result["permission"] = f["permission"]
        return result

    @staticmethod
    def _base_runs_qs(filters: Optional[Dict] = None):
        """Build a base queryset for AgentRun with filters (no RBAC scoping
        — governance users see everything)."""
        qs = AgentRun.objects.all()
        f = GovernanceDashboardService._parse_filters(filters)

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

    @staticmethod
    def _audit_qs(filters: Optional[Dict] = None):
        from apps.auditlog.models import AuditEvent
        qs = AuditEvent.objects.all()
        f = GovernanceDashboardService._parse_filters(filters)
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
    # 1. Governance Summary — high-level KPIs
    # ------------------------------------------------------------------
    @staticmethod
    def get_governance_summary(filters=None) -> Dict[str, Any]:
        run_qs = GovernanceDashboardService._base_runs_qs(filters)
        audit_qs = GovernanceDashboardService._audit_qs(filters)
        total_runs = run_qs.count() or 1

        # Trace coverage
        with_trace = run_qs.exclude(Q(trace_id="") | Q(trace_id__isnull=True)).count()
        trace_coverage = round(with_trace / total_runs * 100, 1)

        # Permission compliance
        with_perm = run_qs.exclude(
            Q(permission_checked="") | Q(permission_checked__isnull=True)
        ).count()
        perm_compliance = round(with_perm / total_runs * 100, 1)

        # Decision coverage
        with_decision = run_qs.filter(decisions__isnull=False).distinct().count()
        decision_coverage = round(with_decision / total_runs * 100, 1)

        # Recommendation coverage
        with_rec = run_qs.filter(recommendations__isnull=False).distinct().count()
        rec_coverage = round(with_rec / total_runs * 100, 1)

        # Access events
        access_granted = audit_qs.filter(access_granted=True).count()
        access_denied = audit_qs.filter(access_granted=False).count()

        # Missing governance artifacts
        missing_trace = total_runs - with_trace if total_runs > 1 else run_qs.filter(
            Q(trace_id="") | Q(trace_id__isnull=True)
        ).count()
        no_decision = total_runs - with_decision if total_runs > 1 else run_qs.exclude(
            decisions__isnull=False
        ).distinct().count()
        no_rec = total_runs - with_rec if total_runs > 1 else run_qs.exclude(
            recommendations__isnull=False
        ).distinct().count()

        # Failed tool calls
        run_ids = run_qs.values_list("id", flat=True)
        failed_tools = ToolCall.objects.filter(
            agent_run_id__in=run_ids,
            status=ToolCallStatus.FAILED,
        ).count()

        # Escalation count
        escalations = AgentEscalation.objects.filter(agent_run_id__in=run_ids).count()

        return {
            "total_runs": total_runs if total_runs > 1 else run_qs.count(),
            "trace_coverage_pct": trace_coverage,
            "permission_compliance_pct": perm_compliance,
            "decision_coverage_pct": decision_coverage,
            "recommendation_coverage_pct": rec_coverage,
            "access_granted": access_granted,
            "access_denied": access_denied,
            "missing_trace": missing_trace,
            "no_decision": no_decision,
            "no_recommendation": no_rec,
            "failed_tool_calls": failed_tools,
            "escalations": escalations,
        }

    # ------------------------------------------------------------------
    # 2. Access Events — detailed access grant/deny log
    # ------------------------------------------------------------------
    @staticmethod
    def get_access_events(filters=None, limit=50) -> Dict[str, Any]:
        audit_qs = GovernanceDashboardService._audit_qs(filters)

        events = list(
            audit_qs.exclude(access_granted__isnull=True)
            .order_by("-created_at")[:limit]
            .values(
                "id", "created_at", "event_type", "entity_type", "entity_id",
                "actor_email", "actor_primary_role", "permission_checked",
                "permission_source", "access_granted", "trace_id",
                "invoice_id", "case_id",
            )
        )

        # Summary by role
        by_role = list(
            audit_qs.exclude(access_granted__isnull=True)
            .exclude(actor_primary_role="")
            .values("actor_primary_role")
            .annotate(
                granted=Count("id", filter=Q(access_granted=True)),
                denied=Count("id", filter=Q(access_granted=False)),
                total=Count("id"),
            )
            .order_by("-total")
        )

        return {"events": events, "by_role": by_role}

    # ------------------------------------------------------------------
    # 3. Permission Activity — charts for permission usage over time
    # ------------------------------------------------------------------
    @staticmethod
    def get_permission_activity(filters=None) -> Dict[str, Any]:
        audit_qs = GovernanceDashboardService._audit_qs(filters)

        # Daily access grant vs deny
        daily = list(
            audit_qs.exclude(access_granted__isnull=True)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                granted=Count("id", filter=Q(access_granted=True)),
                denied=Count("id", filter=Q(access_granted=False)),
            )
            .order_by("date")
        )

        # Top permissions checked
        top_perms = list(
            audit_qs.exclude(permission_checked="")
            .values("permission_checked")
            .annotate(
                count=Count("id"),
                denied=Count("id", filter=Q(access_granted=False)),
            )
            .order_by("-count")[:15]
        )

        # Hourly pattern
        hourly = list(
            audit_qs.exclude(access_granted__isnull=True)
            .annotate(hour=TruncHour("created_at"))
            .values("hour")
            .annotate(count=Count("id"))
            .order_by("hour")
        )
        for row in hourly:
            row["hour"] = row["hour"].isoformat() if row["hour"] else ""

        # By permission source
        by_source = list(
            audit_qs.exclude(permission_source="")
            .values("permission_source")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        return {
            "daily": daily,
            "top_permissions": top_perms,
            "hourly": hourly,
            "by_source": by_source,
        }

    # ------------------------------------------------------------------
    # 4. Trace Runs — paginated list for the trace explorer
    # ------------------------------------------------------------------
    @staticmethod
    def get_trace_runs(filters=None, limit=50) -> List[Dict[str, Any]]:
        run_qs = GovernanceDashboardService._base_runs_qs(filters)
        runs = (
            run_qs.select_related("reconciliation_result__invoice")
            .order_by("-created_at")[:limit]
        )

        result = []
        for run in runs:
            inv = (
                getattr(run.reconciliation_result, "invoice", None)
                if run.reconciliation_result else None
            )
            has_decisions = run.decisions.exists()
            has_recs = run.recommendations.exists()
            has_trace = bool(run.trace_id)

            result.append({
                "id": run.pk,
                "agent_type": run.agent_type,
                "status": run.status,
                "confidence": round((run.confidence or 0) * 100, 1),
                "duration_ms": run.duration_ms,
                "has_trace": has_trace,
                "has_decisions": has_decisions,
                "has_recommendations": has_recs,
                "trace_id": run.trace_id or "",
                "invoice_number": getattr(inv, "invoice_number", "") or "",
                "invoice_id": getattr(inv, "id", None),
                "created_at": run.created_at.isoformat() if run.created_at else "",
                "permission_checked": run.permission_checked or "",
                "actor_user_id": run.actor_user_id,
            })
        return result

    # ------------------------------------------------------------------
    # 5. Trace Detail — deep-dive for a single run (full governance view)
    # ------------------------------------------------------------------
    @staticmethod
    def get_trace_detail(run_id) -> Optional[Dict[str, Any]]:
        try:
            run = AgentRun.objects.select_related(
                "reconciliation_result__invoice",
                "agent_definition",
            ).get(pk=run_id)
        except AgentRun.DoesNotExist:
            return None

        inv = (
            getattr(run.reconciliation_result, "invoice", None)
            if run.reconciliation_result else None
        )

        data: Dict[str, Any] = {
            "id": run.pk,
            "agent_type": run.agent_type,
            "status": run.status,
            "confidence": round((run.confidence or 0) * 100, 1),
            "duration_ms": run.duration_ms,
            "started_at": run.started_at.isoformat() if run.started_at else "",
            "completed_at": run.completed_at.isoformat() if run.completed_at else "",
            "error_message": run.error_message or "",
            "summarized_reasoning": run.summarized_reasoning or "",
            "invocation_reason": run.invocation_reason or "",
            "invoice_number": getattr(inv, "invoice_number", "") or "",
            "invoice_id": getattr(inv, "id", None),
            "reconciliation_result_id": run.reconciliation_result_id,
            # Full governance fields
            "trace_id": run.trace_id or "",
            "span_id": run.span_id or "",
            "prompt_version": run.prompt_version or "",
            "actor_user_id": run.actor_user_id,
            "permission_checked": run.permission_checked or "",
            "cost_estimate": float(run.actual_cost_usd or run.cost_estimate or 0),
            "llm_model_used": run.llm_model_used or "",
            "prompt_tokens": run.prompt_tokens or 0,
            "completion_tokens": run.completion_tokens or 0,
            "total_tokens": run.total_tokens or 0,
        }

        # Timeline events
        timeline = []
        if run.started_at:
            timeline.append({
                "time": run.started_at.isoformat(),
                "event": "agent_started",
                "label": f"{run.agent_type} started",
            })

        for tc in run.tool_calls.order_by("created_at"):
            timeline.append({
                "time": tc.created_at.isoformat(),
                "event": "tool_called",
                "label": f"Tool: {tc.tool_name}",
                "status": tc.status,
                "duration_ms": tc.duration_ms,
                "input_summary": (str(tc.input_payload)[:120] + "…")
                    if tc.input_payload else "",
                "output_summary": (str(tc.output_payload)[:120] + "…")
                    if tc.output_payload else "",
                "error": tc.error_message or "",
            })

        for d in run.decisions.order_by("created_at"):
            timeline.append({
                "time": d.created_at.isoformat(),
                "event": "decision_created",
                "label": f"Decision: {d.decision_type}",
                "confidence": d.confidence,
                "rationale": d.rationale or "",
                "deterministic": d.deterministic_flag,
            })

        for rec in run.recommendations.order_by("created_at"):
            timeline.append({
                "time": rec.created_at.isoformat(),
                "event": "recommendation_created",
                "label": f"Rec: {rec.recommendation_type}",
                "confidence": rec.confidence,
                "reasoning": rec.reasoning or "",
                "accepted": rec.accepted,
            })

        for esc in run.escalations.order_by("created_at"):
            timeline.append({
                "time": esc.created_at.isoformat(),
                "event": "escalation_created",
                "label": f"Escalation: {esc.severity}",
                "reason": esc.reason or "",
                "suggested_role": esc.suggested_assignee_role or "",
            })

        if run.completed_at:
            label_suffix = "completed" if run.status == AgentRunStatus.COMPLETED else "failed"
            timeline.append({
                "time": run.completed_at.isoformat(),
                "event": f"agent_{label_suffix}",
                "label": f"{run.agent_type} {label_suffix}",
            })

        timeline.sort(key=lambda x: x["time"])
        data["timeline"] = timeline

        # Span tree — related runs sharing the same trace
        span_tree = []
        if run.trace_id:
            siblings = (
                AgentRun.objects.filter(trace_id=run.trace_id)
                .exclude(pk=run.pk)
                .order_by("created_at")
                .values("id", "agent_type", "status", "duration_ms", "span_id")
            )
            span_tree = list(siblings)
        data["span_tree"] = span_tree

        return data

    # ------------------------------------------------------------------
    # 6. Governance Health — runs missing governance artifacts
    # ------------------------------------------------------------------
    @staticmethod
    def get_governance_health(filters=None) -> Dict[str, Any]:
        run_qs = GovernanceDashboardService._base_runs_qs(filters)

        # Per agent type: how many have trace, decision, recommendation
        per_agent = list(
            run_qs.values("agent_type")
            .annotate(
                total=Count("id"),
                with_trace=Count("id", filter=~Q(trace_id="") & Q(trace_id__isnull=False)),
                with_decision=Count("id", filter=Q(decisions__isnull=False), distinct=True),
                with_recommendation=Count("id", filter=Q(recommendations__isnull=False), distinct=True),
                with_permission=Count("id", filter=~Q(permission_checked="") & Q(permission_checked__isnull=False)),
                failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
                escalated=Count("escalations", distinct=True),
            )
            .order_by("agent_type")
        )

        for row in per_agent:
            t = row["total"] or 1
            row["trace_pct"] = round(row["with_trace"] / t * 100, 1)
            row["decision_pct"] = round(row["with_decision"] / t * 100, 1)
            row["recommendation_pct"] = round(row["with_recommendation"] / t * 100, 1)
            row["permission_pct"] = round(row["with_permission"] / t * 100, 1)
            row["missing_trace"] = row["total"] - row["with_trace"]
            row["missing_decision"] = row["total"] - row["with_decision"]
            row["missing_recommendation"] = row["total"] - row["with_recommendation"]

        return {"per_agent": per_agent}

    # ------------------------------------------------------------------
    # 7. Agent RBAC Compliance — RBAC field population metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_agent_rbac_compliance(filters=None) -> Dict[str, Any]:
        """Measure how many agent runs have full RBAC metadata populated."""
        run_qs = GovernanceDashboardService._base_runs_qs(filters)
        total = run_qs.count() or 1

        with_actor = run_qs.exclude(Q(actor_user_id__isnull=True)).count()
        with_role = run_qs.exclude(Q(actor_primary_role="") | Q(actor_primary_role__isnull=True)).count()
        with_perm = run_qs.exclude(Q(permission_checked="") | Q(permission_checked__isnull=True)).count()
        with_perm_source = run_qs.exclude(Q(permission_source="") | Q(permission_source__isnull=True)).count()
        with_access = run_qs.exclude(Q(access_granted__isnull=True)).count()

        return {
            "total_runs": total if total > 1 else run_qs.count(),
            "with_actor_pct": round(with_actor / total * 100, 1),
            "with_role_pct": round(with_role / total * 100, 1),
            "with_permission_pct": round(with_perm / total * 100, 1),
            "with_permission_source_pct": round(with_perm_source / total * 100, 1),
            "with_access_granted_pct": round(with_access / total * 100, 1),
            "missing_actor": total - with_actor if total > 1 else run_qs.filter(actor_user_id__isnull=True).count(),
            "missing_role": total - with_role if total > 1 else run_qs.filter(Q(actor_primary_role="") | Q(actor_primary_role__isnull=True)).count(),
            # Breakdown by permission source (USER vs SYSTEM_AGENT)
            "by_source": list(
                run_qs.exclude(Q(permission_source="") | Q(permission_source__isnull=True))
                .values("permission_source")
                .annotate(count=Count("id"))
                .order_by("-count")
            ),
        }

    # ------------------------------------------------------------------
    # 8. Guardrail Decisions — agent-specific grant/deny audit
    # ------------------------------------------------------------------
    @staticmethod
    def get_guardrail_decisions(filters=None, limit=50) -> Dict[str, Any]:
        """Return guardrail-specific audit events (GUARDRAIL_GRANTED / DENIED)."""
        from apps.auditlog.models import AuditEvent
        from apps.core.enums import AuditEventType

        audit_qs = GovernanceDashboardService._audit_qs(filters)
        guardrail_events = audit_qs.filter(
            event_type__in=[
                AuditEventType.GUARDRAIL_GRANTED,
                AuditEventType.GUARDRAIL_DENIED,
            ]
        )

        events = list(
            guardrail_events.order_by("-created_at")[:limit].values(
                "id", "created_at", "event_type", "entity_type", "entity_id",
                "description", "actor_email", "actor_primary_role",
                "permission_checked", "access_granted", "trace_id", "metadata",
            )
        )

        summary = {
            "total": guardrail_events.count(),
            "granted": guardrail_events.filter(access_granted=True).count(),
            "denied": guardrail_events.filter(access_granted=False).count(),
            "by_action": list(
                guardrail_events.values("permission_checked")
                .annotate(
                    granted=Count("id", filter=Q(access_granted=True)),
                    denied=Count("id", filter=Q(access_granted=False)),
                    total=Count("id"),
                )
                .order_by("-total")
            ),
        }

        return {"events": events, "summary": summary}

    # ------------------------------------------------------------------
    # 9. Tool Authorization Metrics
    # ------------------------------------------------------------------
    @staticmethod
    def get_tool_authorization_metrics(filters=None) -> Dict[str, Any]:
        """Aggregate tool invocation metrics with auth grant/deny breakdown."""
        from apps.auditlog.models import AuditEvent
        from apps.core.enums import AuditEventType

        audit_qs = GovernanceDashboardService._audit_qs(filters)
        tool_events = audit_qs.filter(
            event_type__in=[
                AuditEventType.TOOL_CALL_AUTHORIZED,
                AuditEventType.TOOL_CALL_DENIED,
            ]
        )

        by_tool = list(
            tool_events.values("permission_checked")
            .annotate(
                authorized=Count("id", filter=Q(access_granted=True)),
                denied=Count("id", filter=Q(access_granted=False)),
                total=Count("id"),
            )
            .order_by("-total")
        )

        return {
            "total_tool_auth_events": tool_events.count(),
            "authorized": tool_events.filter(access_granted=True).count(),
            "denied": tool_events.filter(access_granted=False).count(),
            "by_tool": by_tool,
        }

    # ------------------------------------------------------------------
    # 10. Recommendation Authorization Audit
    # ------------------------------------------------------------------
    @staticmethod
    def get_recommendation_authorization_audit(filters=None, limit=50) -> Dict[str, Any]:
        """Track recommendation accept/reject authorization decisions."""
        from apps.auditlog.models import AuditEvent
        from apps.core.enums import AuditEventType

        audit_qs = GovernanceDashboardService._audit_qs(filters)
        rec_events = audit_qs.filter(
            event_type__in=[
                AuditEventType.RECOMMENDATION_ACCEPTED,
                AuditEventType.RECOMMENDATION_DENIED,
            ]
        )

        events = list(
            rec_events.order_by("-created_at")[:limit].values(
                "id", "created_at", "event_type", "entity_type", "entity_id",
                "description", "actor_email", "actor_primary_role",
                "permission_checked", "access_granted", "trace_id",
            )
        )

        summary = {
            "total": rec_events.count(),
            "accepted": rec_events.filter(
                event_type=AuditEventType.RECOMMENDATION_ACCEPTED,
            ).count(),
            "denied": rec_events.filter(
                event_type=AuditEventType.RECOMMENDATION_DENIED,
            ).count(),
        }

        return {"events": events, "summary": summary}
