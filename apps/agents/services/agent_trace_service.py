"""Agent trace service — unified tracing interface for all agent operations.

This service is the single entry point for recording agent runs, steps,
tool calls, and decisions. All agents must use this service for governance.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from django.utils import timezone

from apps.agents.models import (
    AgentDefinition,
    AgentRun,
    AgentStep,
    DecisionLog,
)
from apps.core.enums import AgentRunStatus, ToolCallStatus
from apps.tools.models import ToolCall, ToolDefinition

logger = logging.getLogger(__name__)


class AgentTraceService:
    """Unified tracing service for agent governance and auditability.

    All agent activity — runs, steps, tool calls, decisions — must flow
    through this service to guarantee a consistent audit trail.
    """

    @staticmethod
    def start_agent_run(
        reconciliation_result_id: int,
        agent_type: str,
        agent_name: str = "",
        input_payload: Optional[Dict[str, Any]] = None,
        tenant=None,
    ) -> AgentRun:
        """Begin a new agent run and return the persisted AgentRun."""
        agent_def = AgentDefinition.objects.filter(
            agent_type=agent_type, enabled=True
        ).first()

        agent_run = AgentRun.objects.create(
            agent_definition=agent_def,
            agent_type=agent_type,
            reconciliation_result_id=reconciliation_result_id,
            status=AgentRunStatus.RUNNING,
            input_payload=input_payload,
            started_at=timezone.now(),
            tenant=tenant,
        )
        logger.info(
            "Agent run started: run=%s type=%s result=%s",
            agent_run.pk, agent_type, reconciliation_result_id,
        )
        return agent_run

    @staticmethod
    def log_agent_step(
        agent_run_id: int,
        step_name: str,
        description: str = "",
        output: Optional[Dict[str, Any]] = None,
        success: bool = True,
        duration_ms: Optional[int] = None,
        tenant=None,
    ) -> AgentStep:
        """Record a substep within an agent run."""
        last_step = (
            AgentStep.objects.filter(agent_run_id=agent_run_id)
            .order_by("-step_number")
            .values_list("step_number", flat=True)
            .first()
        ) or 0

        step = AgentStep.objects.create(
            agent_run_id=agent_run_id,
            step_number=last_step + 1,
            action=step_name,
            input_data={"description": description} if description else None,
            output_data=output,
            success=success,
            duration_ms=duration_ms,
            tenant=tenant,
        )
        logger.debug(
            "Agent step logged: run=%s step=%s action=%s",
            agent_run_id, step.step_number, step_name,
        )
        return step

    @staticmethod
    def log_tool_call(
        agent_run_id: int,
        tool_name: str,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_output: Optional[Dict[str, Any]] = None,
        success: bool = True,
        duration_ms: Optional[int] = None,
    ) -> ToolCall:
        """Record a tool invocation within an agent run."""
        tool_def = ToolDefinition.objects.filter(name=tool_name).first()
        status = ToolCallStatus.SUCCESS if success else ToolCallStatus.FAILED

        # Inherit tenant from parent AgentRun
        _tenant = None
        try:
            _tenant = AgentRun.objects.filter(pk=agent_run_id).values_list("tenant_id", flat=True).first()
        except Exception:
            pass

        tc = ToolCall.objects.create(
            agent_run_id=agent_run_id,
            tool_definition=tool_def,
            tool_name=tool_name,
            status=status,
            input_payload=tool_input,
            output_payload=tool_output,
            error_message="" if success else str(tool_output.get("error", "")) if tool_output else "",
            duration_ms=duration_ms,
            tenant_id=_tenant,
        )
        logger.debug(
            "Tool call logged: run=%s tool=%s status=%s",
            agent_run_id, tool_name, status,
        )
        return tc

    @staticmethod
    def log_agent_decision(
        agent_run_id: int,
        decision_type: str,
        summary: str,
        confidence: Optional[float] = None,
        evidence: Optional[Dict[str, Any]] = None,
        tenant=None,
    ) -> DecisionLog:
        """Record a key agent decision for audit."""
        decision = DecisionLog.objects.create(
            agent_run_id=agent_run_id,
            decision=f"[{decision_type}] {summary}"[:500],
            rationale=summary,
            confidence=confidence,
            evidence_refs=evidence,
            tenant=tenant,
        )
        logger.info(
            "Agent decision logged: run=%s type=%s confidence=%s",
            agent_run_id, decision_type, confidence,
        )
        return decision

    @staticmethod
    def finish_agent_run(
        agent_run_id: int,
        confidence_score: Optional[float] = None,
        summarized_reasoning: str = "",
        output_payload: Optional[Dict[str, Any]] = None,
        error_message: str = "",
    ) -> AgentRun:
        """Finalize an agent run with outcome data."""
        agent_run = AgentRun.objects.get(pk=agent_run_id)
        agent_run.status = AgentRunStatus.FAILED if error_message else AgentRunStatus.COMPLETED
        agent_run.completed_at = timezone.now()
        agent_run.confidence = confidence_score
        agent_run.summarized_reasoning = summarized_reasoning[:2000]
        agent_run.output_payload = output_payload
        agent_run.error_message = error_message[:2000]
        if agent_run.started_at:
            delta = (agent_run.completed_at - agent_run.started_at).total_seconds()
            agent_run.duration_ms = int(delta * 1000)
        agent_run.save()
        logger.info(
            "Agent run finished: run=%s status=%s confidence=%s",
            agent_run.pk, agent_run.status, confidence_score,
        )
        return agent_run

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    @staticmethod
    def get_trace_for_result(result_id: int, tenant=None) -> Dict[str, Any]:
        """Return the full agent trace for a reconciliation result."""
        runs = AgentRun.objects.filter(
            reconciliation_result_id=result_id,
        )
        if tenant is not None:
            runs = runs.filter(tenant=tenant)
        runs = runs.order_by("created_at")

        trace_data: List[Dict[str, Any]] = []
        for run in runs:
            steps = list(
                AgentStep.objects.filter(agent_run=run).order_by("step_number").values(
                    "id", "step_number", "action", "input_data",
                    "output_data", "success", "duration_ms", "created_at",
                )
            )
            tool_calls = list(
                ToolCall.objects.filter(agent_run=run).order_by("created_at").values(
                    "id", "tool_name", "status", "input_payload",
                    "output_payload", "error_message", "duration_ms", "created_at",
                )
            )
            decisions = list(
                DecisionLog.objects.filter(agent_run=run).order_by("created_at").values(
                    "id", "decision", "rationale", "confidence",
                    "evidence_refs", "created_at",
                )
            )
            trace_data.append({
                "agent_run_id": run.pk,
                "agent_type": run.agent_type,
                "agent_name": run.agent_definition.name if run.agent_definition else run.agent_type,
                "status": run.status,
                "confidence": run.confidence,
                "summarized_reasoning": run.summarized_reasoning,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "duration_ms": run.duration_ms,
                "steps": steps,
                "tool_calls": tool_calls,
                "decisions": decisions,
            })
        return {
            "reconciliation_result_id": result_id,
            "agent_runs": trace_data,
        }

    @staticmethod
    def get_trace_for_invoice(invoice_id: int, tenant=None) -> Dict[str, Any]:
        """Return the full agent trace for an invoice across all recon results.

        Also includes agent runs that are not linked to a reconciliation result
        (e.g., PO_RETRIEVAL runs created during the case pipeline before
        reconciliation occurs).
        """
        from apps.reconciliation.models import ReconciliationResult

        result_ids = list(
            ReconciliationResult.objects.filter(
                invoice_id=invoice_id,
            ).values_list("id", flat=True)
        )

        all_traces: List[Dict[str, Any]] = []
        seen_run_ids: set = set()

        # 1. Agent runs linked to reconciliation results (standard path)
        for result_id in result_ids:
            trace = AgentTraceService.get_trace_for_result(result_id, tenant=tenant)
            all_traces.append(trace)
            for run_data in trace.get("agent_runs", []):
                seen_run_ids.add(run_data["agent_run_id"])

        # 2. Orphaned agent runs: linked to a case stage for this invoice,
        #    or carrying invoice_id in input_payload, but not tied to a
        #    reconciliation result.
        orphan_runs = AgentRun.objects.filter(
            reconciliation_result__isnull=True,
            input_payload__invoice_id=invoice_id,
        ).exclude(pk__in=seen_run_ids).order_by("created_at")
        if tenant is not None:
            orphan_runs = orphan_runs.filter(tenant=tenant)

        # Also find runs linked via APCaseStage.performed_by_agent
        try:
            from apps.cases.models import APCase, APCaseStage
            case = APCase.objects.filter(invoice_id=invoice_id).first()
            if case:
                stage_run_ids = list(
                    APCaseStage.objects.filter(
                        case=case,
                        performed_by_agent__isnull=False,
                    ).values_list("performed_by_agent_id", flat=True)
                )
                stage_orphans = AgentRun.objects.filter(
                    pk__in=stage_run_ids,
                ).exclude(pk__in=seen_run_ids).order_by("created_at")
                orphan_runs = orphan_runs | stage_orphans
        except Exception:
            pass

        if orphan_runs.exists():
            orphan_trace_data: List[Dict[str, Any]] = []
            for run in orphan_runs.distinct():
                if run.pk in seen_run_ids:
                    continue
                seen_run_ids.add(run.pk)
                steps = list(
                    AgentStep.objects.filter(agent_run=run).order_by("step_number").values(
                        "id", "step_number", "action", "input_data",
                        "output_data", "success", "duration_ms", "created_at",
                    )
                )
                tool_calls = list(
                    ToolCall.objects.filter(agent_run=run).order_by("created_at").values(
                        "id", "tool_name", "status", "input_payload",
                        "output_payload", "error_message", "duration_ms", "created_at",
                    )
                )
                decisions = list(
                    DecisionLog.objects.filter(agent_run=run).order_by("created_at").values(
                        "id", "decision", "rationale", "confidence",
                        "evidence_refs", "created_at",
                    )
                )
                orphan_trace_data.append({
                    "agent_run_id": run.pk,
                    "agent_type": run.agent_type,
                    "agent_name": run.agent_definition.name if run.agent_definition else run.agent_type,
                    "status": run.status,
                    "confidence": run.confidence,
                    "summarized_reasoning": run.summarized_reasoning,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "duration_ms": run.duration_ms,
                    "steps": steps,
                    "tool_calls": tool_calls,
                    "decisions": decisions,
                })
            if orphan_trace_data:
                all_traces.append({
                    "reconciliation_result_id": None,
                    "label": "Pre-reconciliation agent runs",
                    "agent_runs": orphan_trace_data,
                })

        return {
            "invoice_id": invoice_id,
            "reconciliation_traces": all_traces,
        }
