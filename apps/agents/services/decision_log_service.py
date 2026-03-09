"""Decision log service — captures and queries agent decisions for audit and analytics."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.agents.models import AgentRecommendation, AgentRun, DecisionLog
from apps.reconciliation.models import ReconciliationResult

logger = logging.getLogger(__name__)


class DecisionLogService:
    """Centralised service for recording and querying agent decisions."""

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    @staticmethod
    def log_decision(
        agent_run: AgentRun,
        decision: str,
        rationale: str = "",
        confidence: Optional[float] = None,
        evidence: Optional[dict] = None,
    ) -> DecisionLog:
        return DecisionLog.objects.create(
            agent_run=agent_run,
            decision=decision[:500],
            rationale=rationale,
            confidence=confidence,
            evidence_refs=evidence,
        )

    @staticmethod
    def log_recommendation(
        agent_run: AgentRun,
        reconciliation_result: ReconciliationResult,
        recommendation_type: str,
        confidence: float = 0.0,
        reasoning: str = "",
        evidence: Optional[dict] = None,
    ) -> AgentRecommendation:
        return AgentRecommendation.objects.create(
            agent_run=agent_run,
            reconciliation_result=reconciliation_result,
            recommendation_type=recommendation_type,
            confidence=confidence,
            reasoning=reasoning,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    @staticmethod
    def get_decisions_for_result(
        result_id: int,
    ) -> List[Dict[str, Any]]:
        """Return all decisions across all agent runs for a ReconciliationResult."""
        return list(
            DecisionLog.objects.filter(
                agent_run__reconciliation_result_id=result_id,
            ).values(
                "id", "agent_run__agent_type", "decision", "rationale",
                "confidence", "evidence_refs", "created_at",
            ).order_by("created_at")
        )

    @staticmethod
    def get_recommendations_for_result(
        result_id: int,
    ) -> List[Dict[str, Any]]:
        return list(
            AgentRecommendation.objects.filter(
                reconciliation_result_id=result_id,
            ).values(
                "id", "agent_run__agent_type", "recommendation_type",
                "confidence", "reasoning", "accepted", "created_at",
            ).order_by("-confidence")
        )
