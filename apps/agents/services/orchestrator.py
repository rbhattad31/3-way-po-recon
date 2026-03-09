"""Agent orchestrator — sequences agent execution based on the policy engine plan.

Flow:
  1. Load reconciliation result + exceptions
  2. Ask the policy engine for an agent plan
  3. Execute agents in sequence, passing context forward
  4. Record recommendations and decisions
  5. Return aggregated orchestration result
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from django.utils import timezone

from apps.agents.models import AgentEscalation, AgentRecommendation, AgentRun
from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
from apps.agents.services.base_agent import AgentContext, BaseAgent
from apps.agents.services.decision_log_service import DecisionLogService
from apps.agents.services.policy_engine import PolicyEngine
from apps.core.enums import AgentRunStatus, ExceptionSeverity, MatchStatus, RecommendationType
from apps.reconciliation.models import ReconciliationResult

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Aggregated outcome of the full agentic pipeline."""
    reconciliation_result_id: int = 0
    agents_executed: List[str] = field(default_factory=list)
    agent_runs: List[AgentRun] = field(default_factory=list)
    final_recommendation: Optional[str] = None
    final_confidence: float = 0.0
    final_reasoning: str = ""
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""


class AgentOrchestrator:
    """Orchestrates the agentic layer for a single ReconciliationResult."""

    def __init__(self):
        self.policy = PolicyEngine()
        self.decision_service = DecisionLogService()

    def execute(self, result: ReconciliationResult) -> OrchestrationResult:
        """Run the full agentic pipeline for one reconciliation result."""
        orch_result = OrchestrationResult(reconciliation_result_id=result.pk)

        # 1. Build the plan
        plan = self.policy.plan(result)

        if plan.skip_agents:
            orch_result.skipped = True
            orch_result.skip_reason = plan.reason
            logger.info("Agents skipped for result %s: %s", result.pk, plan.reason)
            return orch_result

        if not plan.agents:
            orch_result.skipped = True
            orch_result.skip_reason = "No agents planned"
            return orch_result

        # 2. Prepare shared context
        exceptions = list(
            result.exceptions.values(
                "id", "exception_type", "severity", "message", "details", "resolved",
            )
        )

        ctx = AgentContext(
            reconciliation_result=result,
            invoice_id=result.invoice_id,
            po_number=result.purchase_order.po_number if result.purchase_order else None,
            exceptions=exceptions,
            extra={
                "vendor_name": (
                    result.invoice.vendor.name if result.invoice.vendor
                    else result.invoice.raw_vendor_name
                ),
                "total_amount": str(result.invoice.total_amount),
                "grn_available": result.grn_available,
                "grn_fully_received": result.grn_fully_received,
            },
        )

        # 3. Execute agents in sequence
        last_output = None
        for agent_type in plan.agents:
            agent_cls = AGENT_CLASS_REGISTRY.get(agent_type)
            if not agent_cls:
                logger.warning("No agent class for type %s", agent_type)
                continue

            # Pass forward context from previous agents
            if last_output:
                ctx.extra["prior_reasoning"] = last_output.summarized_reasoning or ""
                ctx.extra["recommendation_type"] = (
                    last_output.output_payload or {}
                ).get("recommendation_type", "")

            agent: BaseAgent = agent_cls()
            try:
                agent_run = agent.run(ctx)
                orch_result.agents_executed.append(agent_type)
                orch_result.agent_runs.append(agent_run)
                last_output = agent_run

                # Record recommendation if present
                output_payload = agent_run.output_payload or {}
                rec_type = output_payload.get("recommendation_type")
                if rec_type:
                    self.decision_service.log_recommendation(
                        agent_run=agent_run,
                        reconciliation_result=result,
                        recommendation_type=rec_type,
                        confidence=agent_run.confidence or 0.0,
                        reasoning=agent_run.summarized_reasoning or "",
                        evidence=output_payload.get("evidence"),
                    )

            except Exception as exc:
                logger.exception("Agent %s failed for result %s", agent_type, result.pk)
                orch_result.error = str(exc)[:1000]
                # Continue with remaining agents

        # 4. Determine final recommendation (from last agent with a recommendation)
        self._resolve_final_recommendation(orch_result, result)

        # 5. Auto-close or escalate
        self._apply_post_policies(orch_result, result)

        logger.info(
            "Orchestration complete for result %s: agents=%s recommendation=%s confidence=%.2f",
            result.pk, orch_result.agents_executed,
            orch_result.final_recommendation, orch_result.final_confidence,
        )
        return orch_result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _resolve_final_recommendation(
        self, orch: OrchestrationResult, result: ReconciliationResult
    ) -> None:
        """Pick the highest-confidence recommendation from all agent runs."""
        recs = AgentRecommendation.objects.filter(
            reconciliation_result=result,
            agent_run__in=orch.agent_runs,
        ).order_by("-confidence")

        best = recs.first()
        if best:
            orch.final_recommendation = best.recommendation_type
            orch.final_confidence = best.confidence or 0.0
            orch.final_reasoning = best.reasoning

    def _apply_post_policies(
        self, orch: OrchestrationResult, result: ReconciliationResult
    ) -> None:
        """Apply PolicyEngine post-run checks (auto-close, escalation)."""
        if self.policy.should_auto_close(orch.final_recommendation, orch.final_confidence):
            result.match_status = MatchStatus.MATCHED
            result.requires_review = False
            result.save(update_fields=["match_status", "requires_review", "updated_at"])
            logger.info("Auto-closed result %s (confidence=%.2f)", result.pk, orch.final_confidence)
            return

        if self.policy.should_escalate(orch.final_recommendation, orch.final_confidence):
            # Create escalation
            last_run = orch.agent_runs[-1] if orch.agent_runs else None
            if last_run:
                AgentEscalation.objects.create(
                    agent_run=last_run,
                    reconciliation_result=result,
                    severity=ExceptionSeverity.HIGH,
                    reason=orch.final_reasoning or "Low confidence — requires manager review",
                    suggested_assignee_role="FINANCE_MANAGER",
                )
            logger.info("Escalated result %s", result.pk)
