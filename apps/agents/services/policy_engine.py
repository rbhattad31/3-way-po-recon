"""Policy engine — determines which agents to run and in what order.

The policy engine is the "brain" that decides the agentic workflow based on
the deterministic reconciliation outcome.  It enforces:
  - Which agents fire for each match status / exception combination
  - Agent ordering (pipeline)
  - Confidence thresholds for auto-close vs. escalation
  - Token budget guardrails
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from apps.core.constants import AGENT_CONFIDENCE_THRESHOLD, REVIEW_AUTO_CLOSE_THRESHOLD
from apps.core.enums import AgentType, ExceptionType, MatchStatus, RecommendationType
from apps.reconciliation.models import ReconciliationResult

logger = logging.getLogger(__name__)


@dataclass
class AgentPlan:
    """The sequence of agents the orchestrator should execute."""
    agents: List[str] = field(default_factory=list)  # AgentType values
    reason: str = ""
    skip_agents: bool = False


class PolicyEngine:
    """Decide which agents to run for a given reconciliation result.

    Rules (deterministic, no LLM):
      1. MATCHED + high confidence → no agents needed (auto-close)
      2. UNMATCHED with PO_NOT_FOUND → PO_RETRIEVAL → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      3. UNMATCHED with GRN_NOT_FOUND → GRN_RETRIEVAL → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      4. PARTIAL_MATCH → RECONCILIATION_ASSIST → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      5. REQUIRES_REVIEW with low extraction confidence → INVOICE_UNDERSTANDING → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
      6. REQUIRES_REVIEW (general) → EXCEPTION_ANALYSIS → REVIEW_ROUTING → CASE_SUMMARY
    """

    def plan(self, result: ReconciliationResult) -> AgentPlan:
        status = result.match_status
        confidence = result.deterministic_confidence or 0.0
        extraction_conf = result.extraction_confidence or 0.0

        # Gather exception types
        exc_types = set(
            result.exceptions.values_list("exception_type", flat=True)
        )

        # Rule 1: Full match, high confidence → skip agents
        if status == MatchStatus.MATCHED and confidence >= REVIEW_AUTO_CLOSE_THRESHOLD:
            return AgentPlan(
                skip_agents=True,
                reason=f"Full match with confidence {confidence:.2f} >= {REVIEW_AUTO_CLOSE_THRESHOLD}",
            )

        agents: List[str] = []

        # Rule 2: PO not found
        if ExceptionType.PO_NOT_FOUND in exc_types:
            agents.append(AgentType.PO_RETRIEVAL)

        # Rule 3: GRN not found
        if ExceptionType.GRN_NOT_FOUND in exc_types:
            agents.append(AgentType.GRN_RETRIEVAL)

        # Rule 4: Low extraction confidence
        if extraction_conf < AGENT_CONFIDENCE_THRESHOLD:
            agents.append(AgentType.INVOICE_UNDERSTANDING)

        # Rule 5: Partial match → reconciliation assist
        if status == MatchStatus.PARTIAL_MATCH:
            if AgentType.RECONCILIATION_ASSIST not in agents:
                agents.append(AgentType.RECONCILIATION_ASSIST)

        # Always include exception analysis if we have exceptions
        if exc_types and AgentType.EXCEPTION_ANALYSIS not in agents:
            agents.append(AgentType.EXCEPTION_ANALYSIS)

        # Always route and summarise
        if agents:  # Only if we're running any agents
            agents.append(AgentType.REVIEW_ROUTING)
            agents.append(AgentType.CASE_SUMMARY)

        # Fallback: if REQUIRES_REVIEW but no specific agents queued
        if not agents and status in (MatchStatus.REQUIRES_REVIEW, MatchStatus.UNMATCHED, MatchStatus.ERROR):
            agents = [
                AgentType.EXCEPTION_ANALYSIS,
                AgentType.REVIEW_ROUTING,
                AgentType.CASE_SUMMARY,
            ]

        reason = (
            f"Status={status}, confidence={confidence:.2f}, "
            f"extraction_conf={extraction_conf:.2f}, "
            f"exceptions={sorted(exc_types)}"
        )

        logger.info("Policy plan for result %s: %s (%s)", result.pk, agents, reason)
        return AgentPlan(agents=agents, reason=reason)

    # ------------------------------------------------------------------
    # Post-run policy checks
    # ------------------------------------------------------------------
    @staticmethod
    def should_auto_close(recommendation_type: Optional[str], confidence: float) -> bool:
        """Return True if the recommendation + confidence warrants auto-close."""
        return (
            recommendation_type == RecommendationType.AUTO_CLOSE
            and confidence >= REVIEW_AUTO_CLOSE_THRESHOLD
        )

    @staticmethod
    def should_escalate(recommendation_type: Optional[str], confidence: float) -> bool:
        """Return True if the case should be escalated (low-confidence non-trivial issue)."""
        return (
            recommendation_type == RecommendationType.ESCALATE_TO_MANAGER
            or confidence < AGENT_CONFIDENCE_THRESHOLD
        )
