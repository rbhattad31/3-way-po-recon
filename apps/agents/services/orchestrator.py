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
from apps.core.enums import AgentRunStatus, AgentType, ExceptionSeverity, MatchStatus, RecommendationType

# Only these agents should emit formal recommendations to avoid duplicates.
# Other agents contribute analysis/reasoning via summarized_reasoning on the run.
_RECOMMENDING_AGENTS = {AgentType.REVIEW_ROUTING, AgentType.CASE_SUMMARY}

# Agents whose findings can be applied back to re-run deterministic matching.
_FEEDBACK_AGENTS = {AgentType.PO_RETRIEVAL}

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

            # Auto-close by tolerance band: upgrade PARTIAL_MATCH → MATCHED
            if plan.auto_close:
                result.match_status = MatchStatus.MATCHED
                result.requires_review = False
                result.summary = (
                    f"Auto-closed: all line discrepancies within auto-close tolerance band. "
                    f"{plan.reason}"
                )
                result.save(update_fields=["match_status", "requires_review", "summary", "updated_at"])
                # Resolve tolerance-level exceptions
                result.exceptions.filter(
                    severity__in=["LOW", "MEDIUM"],
                ).update(resolved=True)
                logger.info("Auto-closed result %s by tolerance band (no AI agents)", result.pk)

            else:
                logger.info("Agents skipped for result %s: %s", result.pk, plan.reason)

            return orch_result

        if not plan.agents:
            orch_result.skipped = True
            orch_result.skip_reason = "No agents planned"
            return orch_result

        # 2. Prepare shared context
        recon_mode = plan.reconciliation_mode or getattr(result, "reconciliation_mode", "") or ""
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
            reconciliation_mode=recon_mode,
            extra={
                "vendor_name": (
                    result.invoice.vendor.name if result.invoice.vendor
                    else result.invoice.raw_vendor_name
                ),
                "total_amount": str(result.invoice.total_amount),
                "grn_available": result.grn_available,
                "grn_fully_received": result.grn_fully_received,
                "reconciliation_mode": recon_mode,
                "is_two_way": recon_mode == "TWO_WAY",
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

                # Record recommendation only for designated routing agents
                output_payload = agent_run.output_payload or {}
                rec_type = output_payload.get("recommendation_type")
                if rec_type and agent_type in _RECOMMENDING_AGENTS:
                    rec = self.decision_service.log_recommendation(
                        agent_run=agent_run,
                        reconciliation_result=result,
                        recommendation_type=rec_type,
                        confidence=agent_run.confidence or 0.0,
                        reasoning=agent_run.summarized_reasoning or "",
                        evidence=output_payload.get("evidence"),
                    )
                    # Backfill invoice FK on recommendation
                    rec.invoice_id = result.invoice_id
                    rec.save(update_fields=["invoice_id"])

                    # Audit: agent recommendation created
                    from apps.auditlog.services import AuditService
                    from apps.core.enums import AuditEventType
                    AuditService.log_event(
                        entity_type="Invoice",
                        entity_id=result.invoice_id,
                        event_type=AuditEventType.AGENT_RECOMMENDATION_CREATED,
                        description=f"Agent '{agent_type}' recommended {rec_type} (confidence: {agent_run.confidence or 0:.0%})",
                        agent=agent_type,
                        metadata={"recommendation_id": rec.pk, "recommendation_type": rec_type, "confidence": agent_run.confidence},
                    )

            except Exception as exc:
                logger.exception("Agent %s failed for result %s", agent_type, result.pk)
                orch_result.error = str(exc)[:1000]
                # Continue with remaining agents

            # --- Agent feedback loop: apply findings back to reconciliation ---
            if agent_type in _FEEDBACK_AGENTS and last_output:
                new_status = self._apply_agent_findings(
                    agent_type, last_output, result, ctx,
                )
                if new_status is not None:
                    # Refresh context for subsequent agents
                    ctx.po_number = (
                        result.purchase_order.po_number
                        if result.purchase_order else ctx.po_number
                    )
                    ctx.exceptions = list(
                        result.exceptions.values(
                            "id", "exception_type", "severity",
                            "message", "details", "resolved",
                        )
                    )
                    ctx.extra["grn_available"] = result.grn_available
                    ctx.extra["grn_fully_received"] = result.grn_fully_received

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

    # ------------------------------------------------------------------
    # Agent findings → re-reconciliation feedback loop
    # ------------------------------------------------------------------
    def _apply_agent_findings(
        self,
        agent_type: str,
        agent_run: AgentRun,
        result: ReconciliationResult,
        ctx: AgentContext,
    ) -> Optional[MatchStatus]:
        """Check if the agent found actionable data (e.g. a PO) and re-reconcile.

        Returns the new match status if re-reconciliation happened, else None.
        """
        output_payload = agent_run.output_payload or {}
        evidence = output_payload.get("evidence", {})

        if agent_type == AgentType.PO_RETRIEVAL:
            return self._apply_po_finding(agent_run, result, evidence)

        return None

    def _apply_po_finding(
        self,
        agent_run: AgentRun,
        result: ReconciliationResult,
        evidence: dict,
    ) -> Optional[MatchStatus]:
        """If the PO Retrieval Agent found a PO, link it and re-reconcile."""
        found_po_number = (
            evidence.get("found_po")
            or evidence.get("po_number")
            or evidence.get("matched_po")
        )
        if not found_po_number:
            logger.info(
                "PO Retrieval Agent for result %s did not find a PO (evidence=%s)",
                result.pk, evidence,
            )
            return None

        from apps.documents.models import PurchaseOrder
        po = PurchaseOrder.objects.filter(po_number=found_po_number).first()
        if not po:
            # Try normalized lookup
            from apps.core.utils import normalize_po_number
            norm = normalize_po_number(found_po_number)
            po = PurchaseOrder.objects.filter(normalized_po_number=norm).first()

        if not po:
            logger.warning(
                "PO Retrieval Agent reported PO '%s' but it doesn't exist in DB",
                found_po_number,
            )
            return None

        from apps.reconciliation.services.agent_feedback_service import AgentFeedbackService
        feedback = AgentFeedbackService()
        new_status = feedback.apply_found_po(
            result=result,
            po=po,
            agent_run_id=agent_run.pk,
        )
        logger.info(
            "Agent feedback: PO %s applied to result %s → new status %s",
            po.po_number, result.pk, new_status,
        )
        return new_status
