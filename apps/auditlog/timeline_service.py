"""Case timeline service — merges audit events, agent runs, recommendations, and review actions."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from apps.agents.models import AgentRecommendation, AgentRun
from apps.auditlog.models import AuditEvent
from apps.reconciliation.models import ReconciliationResult
from apps.reviews.models import ManualReviewAction, ReviewAssignment, ReviewDecision
from apps.tools.models import ToolCall

logger = logging.getLogger(__name__)


class CaseTimelineService:
    """Builds a unified, chronologically-ordered timeline for an invoice case."""

    @staticmethod
    def get_case_timeline(invoice_id: int) -> List[Dict[str, Any]]:
        """Return an ordered list of all governance events for one invoice.

        Merges:
        - Audit events (entity_type='Invoice')
        - Audit events on related ReconciliationResults
        - Agent runs
        - Agent tool calls
        - Agent recommendations
        - Review assignments, actions, and decisions
        """
        timeline: List[Dict[str, Any]] = []

        # 1. Audit events on the invoice
        invoice_events = AuditEvent.objects.filter(
            entity_type="Invoice", entity_id=invoice_id,
        ).order_by("created_at")
        for ev in invoice_events:
            timeline.append({
                "timestamp": ev.created_at,
                "event_category": "audit",
                "event_type": ev.event_type or ev.action,
                "description": ev.event_description or ev.action,
                "actor": ev.performed_by.email if ev.performed_by else ev.performed_by_agent or "system",
                "metadata": ev.metadata_json,
            })

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
        ).order_by("created_at")
        for ev in result_events:
            timeline.append({
                "timestamp": ev.created_at,
                "event_category": "audit",
                "event_type": ev.event_type or ev.action,
                "description": ev.event_description or ev.action,
                "actor": ev.performed_by.email if ev.performed_by else ev.performed_by_agent or "system",
                "metadata": ev.metadata_json,
            })

        # 4. Agent runs
        agent_runs = AgentRun.objects.filter(
            reconciliation_result_id__in=result_ids,
        ).select_related("agent_definition").order_by("created_at")
        for run in agent_runs:
            agent_name = run.agent_definition.name if run.agent_definition else run.agent_type
            timeline.append({
                "timestamp": run.started_at or run.created_at,
                "event_category": "agent_run",
                "event_type": f"AGENT_{run.status}",
                "description": f"Agent '{agent_name}' {run.status.lower()} (confidence: {run.confidence or 0:.0%})",
                "actor": agent_name,
                "metadata": {
                    "agent_run_id": run.pk,
                    "agent_type": run.agent_type,
                    "status": run.status,
                    "confidence": run.confidence,
                    "summarized_reasoning": run.summarized_reasoning[:300] if run.summarized_reasoning else "",
                    "duration_ms": run.duration_ms,
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
                    "metadata": {
                        "tool_call_id": tc.pk,
                        "tool_name": tc.tool_name,
                        "status": tc.status,
                        "duration_ms": tc.duration_ms,
                    },
                })

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

            # Review actions
            actions = ManualReviewAction.objects.filter(
                assignment=assignment,
            ).select_related("performed_by").order_by("created_at")
            for action in actions:
                timeline.append({
                    "timestamp": action.created_at,
                    "event_category": "review_action",
                    "event_type": f"REVIEW_{action.action_type}",
                    "description": f"Review action: {action.get_action_type_display()}",
                    "actor": action.performed_by.email if action.performed_by else "unknown",
                    "metadata": {
                        "action_type": action.action_type,
                        "field_name": action.field_name,
                        "reason": action.reason[:300] if action.reason else "",
                    },
                })

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

        # Sort by timestamp
        timeline.sort(key=lambda x: x["timestamp"])
        return timeline
