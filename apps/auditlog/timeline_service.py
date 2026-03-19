"""Case timeline service — unified investigation timeline for governance.

Merges ALL event sources into a single chronological view:
- Audit events (with RBAC context)
- Case stage history (with durations)
- Decision logs (with rule/policy traceability)
- Agent trace (with tool calls and token usage)
- Review history (with field corrections)
- Reprocess history
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.agents.models import AgentRecommendation, AgentRun, DecisionLog
from apps.auditlog.models import AuditEvent
from apps.cases.models import APCase
from apps.reconciliation.models import ReconciliationResult
from apps.reviews.models import ManualReviewAction, ReviewAssignment, ReviewDecision
from apps.tools.models import ToolCall

logger = logging.getLogger(__name__)


def _rbac_badge(event) -> Dict[str, Any]:
    """Extract RBAC context from an AuditEvent for display."""
    data = {}
    if hasattr(event, "actor_email") and event.actor_email:
        data["actor_email"] = event.actor_email
    if hasattr(event, "actor_primary_role") and event.actor_primary_role:
        data["actor_role"] = event.actor_primary_role
    if hasattr(event, "permission_checked") and event.permission_checked:
        data["permission_checked"] = event.permission_checked
    if hasattr(event, "permission_source") and event.permission_source:
        data["permission_source"] = event.permission_source
    if hasattr(event, "access_granted") and event.access_granted is not None:
        data["access_granted"] = event.access_granted
    if hasattr(event, "actor_roles_snapshot_json") and event.actor_roles_snapshot_json:
        data["actor_roles"] = event.actor_roles_snapshot_json
    return data


def _append_orphan_agent_runs(
    timeline: List[Dict[str, Any]],
    invoice_id: int,
    seen_run_ids: set,
) -> None:
    """Append agent runs not linked to a reconciliation result.

    These are typically PO_RETRIEVAL or EXTRACTION agents that run during the
    case pipeline before a ReconciliationResult is created.
    """
    from apps.cases.models import APCaseStage

    orphan_runs = AgentRun.objects.filter(
        reconciliation_result__isnull=True,
        input_payload__invoice_id=invoice_id,
    ).exclude(pk__in=seen_run_ids).select_related("agent_definition")

    # Also pick up runs referenced by case stages
    case = APCase.objects.filter(invoice_id=invoice_id).first()
    if case:
        stage_run_ids = list(
            APCaseStage.objects.filter(
                case=case, performed_by_agent__isnull=False,
            ).values_list("performed_by_agent_id", flat=True)
        )
        if stage_run_ids:
            stage_orphans = AgentRun.objects.filter(
                pk__in=stage_run_ids,
            ).exclude(pk__in=seen_run_ids).select_related("agent_definition")
            orphan_runs = (orphan_runs | stage_orphans).distinct()

    for run in orphan_runs.order_by("created_at"):
        if run.pk in seen_run_ids:
            continue
        seen_run_ids.add(run.pk)
        agent_name = run.agent_definition.name if run.agent_definition else run.agent_type
        timeline.append({
            "timestamp": run.started_at or run.created_at,
            "event_category": "agent_run",
            "event_type": f"AGENT_{run.status}",
            "description": f"Agent '{agent_name}' {run.status.lower()} (pre-reconciliation)",
            "actor": agent_name,
            "trace_id": getattr(run, "trace_id", ""),
            "duration_ms": run.duration_ms,
            "metadata": {
                "agent_run_id": run.pk,
                "agent_type": run.agent_type,
                "status": run.status,
                "confidence": run.confidence,
                "summarized_reasoning": run.summarized_reasoning[:300] if run.summarized_reasoning else "",
                "duration_ms": run.duration_ms,
                "llm_model": run.llm_model_used,
                "total_tokens": run.total_tokens,
            },
        })

        # Tool calls and decisions for orphan runs
        tool_calls = ToolCall.objects.filter(agent_run=run).order_by("created_at")
        for tc in tool_calls:
            timeline.append({
                "timestamp": tc.created_at,
                "event_category": "tool_call",
                "event_type": f"TOOL_{tc.status}",
                "description": f"Tool '{tc.tool_name}' called ({tc.status.lower()})",
                "actor": agent_name,
                "duration_ms": tc.duration_ms,
                "metadata": {
                    "tool_call_id": tc.pk,
                    "tool_name": tc.tool_name,
                    "status": tc.status,
                    "duration_ms": tc.duration_ms,
                },
            })

        decisions = DecisionLog.objects.filter(agent_run=run).order_by("created_at")
        for dl in decisions:
            timeline.append({
                "timestamp": dl.created_at,
                "event_category": "decision",
                "event_type": f"DECISION_{dl.decision_type}" if dl.decision_type else "DECISION",
                "description": dl.decision[:300],
                "actor": agent_name,
                "metadata": {
                    "decision_type": dl.decision_type,
                    "rationale": dl.rationale[:300] if dl.rationale else "",
                    "confidence": dl.confidence,
                },
            })


class CaseTimelineService:
    """Builds a unified, chronologically-ordered timeline for an invoice case.

    This is the single-pane-of-glass investigation view combining all event sources.
    """

    @staticmethod
    def get_case_timeline(invoice_id: int) -> List[Dict[str, Any]]:
        """Return an ordered list of all governance events for one invoice."""
        timeline: List[Dict[str, Any]] = []

        # 1. Audit events linked to this invoice (entity_type + cross-ref)
        from django.db.models import Q
        audit_events = AuditEvent.objects.filter(
            Q(entity_type="Invoice", entity_id=invoice_id) |
            Q(invoice_id=invoice_id)
        ).select_related("performed_by").order_by("created_at")

        seen_audit_ids = set()
        for ev in audit_events:
            if ev.pk in seen_audit_ids:
                continue
            seen_audit_ids.add(ev.pk)
            entry = {
                "timestamp": ev.created_at,
                "event_category": "audit",
                "event_type": ev.event_type or ev.action,
                "description": ev.event_description or ev.action,
                "actor": ev.performed_by.email if ev.performed_by else ev.performed_by_agent or "system",
                "metadata": ev.metadata_json,
                "trace_id": ev.trace_id,
                "rbac": _rbac_badge(ev),
            }
            if ev.status_before or ev.status_after:
                entry["status_change"] = {"before": ev.status_before, "after": ev.status_after}
            if ev.duration_ms:
                entry["duration_ms"] = ev.duration_ms
            timeline.append(entry)

        # 2. Get all reconciliation results for this invoice
        results = ReconciliationResult.objects.filter(invoice_id=invoice_id)
        result_ids = list(results.values_list("id", flat=True))

        # 2a. Mode resolution events from results
        for result in results:
            if result.reconciliation_mode:
                mode_label = "2-Way" if result.is_two_way_result else "3-Way"
                timeline.append({
                    "timestamp": result.created_at,
                    "event_category": "mode_resolution",
                    "event_type": "RECONCILIATION_MODE_RESOLVED",
                    "description": f"Reconciliation mode resolved: {mode_label}"
                                   + (f" — {result.mode_resolution_reason}" if result.mode_resolution_reason else ""),
                    "actor": "system",
                    "metadata": {
                        "reconciliation_mode": result.reconciliation_mode,
                        "policy_applied": result.policy_applied,
                        "grn_required": result.grn_required_flag,
                        "is_two_way": result.is_two_way_result,
                    },
                })

        # 3. Audit events on reconciliation results
        result_events = AuditEvent.objects.filter(
            entity_type="ReconciliationResult", entity_id__in=result_ids,
        ).select_related("performed_by").order_by("created_at")
        for ev in result_events:
            if ev.pk in seen_audit_ids:
                continue
            seen_audit_ids.add(ev.pk)
            timeline.append({
                "timestamp": ev.created_at,
                "event_category": "audit",
                "event_type": ev.event_type or ev.action,
                "description": ev.event_description or ev.action,
                "actor": ev.performed_by.email if ev.performed_by else ev.performed_by_agent or "system",
                "metadata": ev.metadata_json,
                "trace_id": ev.trace_id,
                "rbac": _rbac_badge(ev),
            })

        # 4. Agent runs (linked to reconciliation results)
        seen_run_ids: set = set()
        agent_runs = AgentRun.objects.filter(
            reconciliation_result_id__in=result_ids,
        ).select_related("agent_definition").order_by("created_at")
        for run in agent_runs:
            seen_run_ids.add(run.pk)
            agent_name = run.agent_definition.name if run.agent_definition else run.agent_type
            timeline.append({
                "timestamp": run.started_at or run.created_at,
                "event_category": "agent_run",
                "event_type": f"AGENT_{run.status}",
                "description": f"Agent '{agent_name}' {run.status.lower()} (confidence: {run.confidence or 0:.0%})",
                "actor": agent_name,
                "trace_id": getattr(run, "trace_id", ""),
                "duration_ms": run.duration_ms,
                "metadata": {
                    "agent_run_id": run.pk,
                    "agent_type": run.agent_type,
                    "status": run.status,
                    "confidence": run.confidence,
                    "summarized_reasoning": run.summarized_reasoning[:300] if run.summarized_reasoning else "",
                    "duration_ms": run.duration_ms,
                    "llm_model": run.llm_model_used,
                    "total_tokens": run.total_tokens,
                    "prompt_version": getattr(run, "prompt_version", ""),
                    "invocation_reason": getattr(run, "invocation_reason", ""),
                },
            })

            # 4a. Tool calls within this run
            tool_calls = ToolCall.objects.filter(agent_run=run).order_by("created_at")
            for tc in tool_calls:
                timeline.append({
                    "timestamp": tc.created_at,
                    "event_category": "tool_call",
                    "event_type": f"TOOL_{tc.status}",
                    "description": f"Tool '{tc.tool_name}' called ({tc.status.lower()})",
                    "actor": agent_name,
                    "duration_ms": tc.duration_ms,
                    "metadata": {
                        "tool_call_id": tc.pk,
                        "tool_name": tc.tool_name,
                        "status": tc.status,
                        "duration_ms": tc.duration_ms,
                    },
                })

            # 4b. Decision logs within this run
            decisions = DecisionLog.objects.filter(agent_run=run).order_by("created_at")
            for dl in decisions:
                timeline.append({
                    "timestamp": dl.created_at,
                    "event_category": "decision",
                    "event_type": f"DECISION_{dl.decision_type}" if dl.decision_type else "DECISION",
                    "description": dl.decision[:300],
                    "actor": agent_name,
                    "metadata": {
                        "decision_type": dl.decision_type,
                        "rationale": dl.rationale[:300] if dl.rationale else "",
                        "confidence": dl.confidence,
                        "deterministic": dl.deterministic_flag,
                        "rule_name": dl.rule_name,
                        "policy_code": dl.policy_code,
                        "recommendation_type": dl.recommendation_type,
                    },
                })

        # 4c. Orphaned agent runs (e.g., PO_RETRIEVAL before reconciliation)
        _append_orphan_agent_runs(timeline, invoice_id, seen_run_ids)

        # 5. Agent recommendations
        recommendations = AgentRecommendation.objects.filter(
            reconciliation_result_id__in=result_ids,
        ).select_related("agent_run", "accepted_by").order_by("created_at")
        for rec in recommendations:
            agent_name = rec.agent_run.agent_type if rec.agent_run else "unknown"
            timeline.append({
                "timestamp": rec.created_at,
                "event_category": "recommendation",
                "event_type": "AGENT_RECOMMENDATION_CREATED",
                "description": f"Recommendation: {rec.get_recommendation_type_display()} (confidence: {rec.confidence or 0:.0%})",
                "actor": agent_name,
                "metadata": {
                    "recommendation_id": rec.pk,
                    "recommendation_type": rec.recommendation_type,
                    "confidence": rec.confidence,
                    "accepted": rec.accepted,
                    "accepted_by": rec.accepted_by.email if rec.accepted_by else None,
                    "reasoning": rec.reasoning[:300] if rec.reasoning else "",
                },
            })

        # 6. Review assignments, actions, and decisions
        assignments = ReviewAssignment.objects.filter(
            reconciliation_result_id__in=result_ids,
        ).select_related("assigned_to").order_by("created_at")
        for assignment in assignments:
            timeline.append({
                "timestamp": assignment.created_at,
                "event_category": "review",
                "event_type": "REVIEW_ASSIGNED",
                "description": f"Review assignment created (priority: {assignment.priority})",
                "actor": assignment.assigned_to.email if assignment.assigned_to else "unassigned",
                "metadata": {
                    "assignment_id": assignment.pk,
                    "status": assignment.status,
                    "priority": assignment.priority,
                },
            })

            # Review actions (field corrections, comments, etc.)
            actions = ManualReviewAction.objects.filter(
                assignment=assignment,
            ).select_related("performed_by").order_by("created_at")
            for action in actions:
                entry = {
                    "timestamp": action.created_at,
                    "event_category": "review_action",
                    "event_type": f"REVIEW_{action.action_type}",
                    "description": f"Review action: {action.get_action_type_display()}",
                    "actor": action.performed_by.email if action.performed_by else "unknown",
                    "metadata": {
                        "action_type": action.action_type,
                        "field_name": action.field_name,
                        "old_value": action.old_value[:200] if action.old_value else "",
                        "new_value": action.new_value[:200] if action.new_value else "",
                        "reason": action.reason[:300] if action.reason else "",
                    },
                }
                # Flag field corrections specifically
                if action.field_name:
                    entry["field_change"] = {
                        "field": action.field_name,
                        "old": action.old_value[:100] if action.old_value else "",
                        "new": action.new_value[:100] if action.new_value else "",
                    }
                timeline.append(entry)

            # Review decision
            try:
                decision = assignment.decision
                timeline.append({
                    "timestamp": decision.decided_at,
                    "event_category": "review_decision",
                    "event_type": f"REVIEW_{decision.decision}",
                    "description": f"Review decision: {decision.get_decision_display()}",
                    "actor": decision.decided_by.email if decision.decided_by else "unknown",
                    "metadata": {
                        "decision": decision.decision,
                        "reason": decision.reason[:300] if decision.reason else "",
                    },
                })
            except ReviewDecision.DoesNotExist:
                pass

        # 7. Case stages and decisions (from APCase model)
        try:
            case = APCase.objects.get(invoice_id=invoice_id, is_active=True)
        except APCase.DoesNotExist:
            case = None

        if case:
            # 7a. Case created
            timeline.append({
                "timestamp": case.created_at,
                "event_category": "case",
                "event_type": "CASE_CREATED",
                "description": f"Case {case.case_number} created",
                "actor": "system",
                "metadata": {
                    "case_id": case.pk,
                    "case_number": case.case_number,
                    "processing_path": case.processing_path,
                    "priority": case.priority,
                },
            })

            # 7b. Processing stages (with duration tracking)
            for stage in case.stages.order_by("created_at"):
                if stage.started_at:
                    timeline.append({
                        "timestamp": stage.started_at,
                        "event_category": "stage",
                        "event_type": f"STAGE_{stage.stage_name}_STARTED",
                        "description": f"{stage.get_stage_name_display()} started",
                        "actor": stage.performed_by_type or "system",
                        "metadata": {
                            "stage_name": stage.stage_name,
                            "retry_count": stage.retry_count,
                            "trace_id": getattr(stage, "trace_id", ""),
                        },
                    })
                if stage.completed_at:
                    status_label = stage.stage_status.lower()
                    duration = getattr(stage, "duration_ms", None)
                    if duration is None and stage.started_at:
                        duration = int((stage.completed_at - stage.started_at).total_seconds() * 1000)
                    timeline.append({
                        "timestamp": stage.completed_at,
                        "event_category": "stage",
                        "event_type": f"STAGE_{stage.stage_name}_{stage.stage_status}",
                        "description": f"{stage.get_stage_name_display()} {status_label}"
                                       + (f" ({duration}ms)" if duration else ""),
                        "actor": stage.performed_by_type or "system",
                        "duration_ms": duration,
                        "metadata": {
                            "stage_name": stage.stage_name,
                            "stage_status": stage.stage_status,
                            "duration_ms": duration,
                            "error_code": getattr(stage, "error_code", ""),
                            "error_message": (getattr(stage, "error_message", "") or "")[:300],
                            "notes": stage.notes[:300] if stage.notes else "",
                        },
                    })

            # 7c. Case decisions (with policy/rule traceability)
            for decision in case.decisions.order_by("created_at"):
                timeline.append({
                    "timestamp": decision.created_at,
                    "event_category": "decision",
                    "event_type": f"DECISION_{decision.decision_type}",
                    "description": f"{decision.get_decision_type_display()}: {decision.decision_value}",
                    "actor": decision.decision_source,
                    "metadata": {
                        "decision_type": decision.decision_type,
                        "decision_source": decision.decision_source,
                        "decision_value": decision.decision_value,
                        "confidence": decision.confidence,
                        "rationale": decision.rationale[:300] if decision.rationale else "",
                    },
                })

        # 8. Standalone decision logs (not from agent runs, e.g. from services)
        standalone_decisions = DecisionLog.objects.filter(
            invoice_id=invoice_id, agent_run__isnull=True
        ).order_by("created_at")
        for dl in standalone_decisions:
            timeline.append({
                "timestamp": dl.created_at,
                "event_category": "decision",
                "event_type": f"DECISION_{dl.decision_type}" if dl.decision_type else "DECISION",
                "description": dl.decision[:300],
                "actor": "system",
                "metadata": {
                    "decision_type": dl.decision_type,
                    "rationale": dl.rationale[:300] if dl.rationale else "",
                    "confidence": dl.confidence,
                    "deterministic": dl.deterministic_flag,
                    "rule_name": dl.rule_name,
                    "policy_code": dl.policy_code,
                },
            })

        # Sort by timestamp (latest first)
        timeline.sort(key=lambda x: x["timestamp"], reverse=True)
        return timeline

    @staticmethod
    def get_stage_timeline(case_id: int) -> List[Dict[str, Any]]:
        """Return a stage-focused execution timeline for a case."""
        try:
            case = APCase.objects.get(pk=case_id, is_active=True)
        except APCase.DoesNotExist:
            return []

        stages = []
        for stage in case.stages.order_by("created_at"):
            duration = getattr(stage, "duration_ms", None)
            if duration is None and stage.started_at and stage.completed_at:
                duration = int((stage.completed_at - stage.started_at).total_seconds() * 1000)
            stages.append({
                "stage_name": stage.stage_name,
                "stage_display": stage.get_stage_name_display(),
                "status": stage.stage_status,
                "performed_by_type": stage.performed_by_type,
                "started_at": stage.started_at,
                "completed_at": stage.completed_at,
                "duration_ms": duration,
                "retry_count": stage.retry_count,
                "error_code": getattr(stage, "error_code", ""),
                "error_message": (getattr(stage, "error_message", "") or "")[:300],
                "notes": stage.notes[:300] if stage.notes else "",
                "trace_id": getattr(stage, "trace_id", ""),
            })
        return stages
