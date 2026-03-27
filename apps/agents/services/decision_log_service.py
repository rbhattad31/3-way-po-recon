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
        """Create a recommendation record, or return the existing pending one.

        Idempotency rule: if a PENDING (accepted=None) recommendation of the
        same type already exists for this reconciliation_result (from any prior
        agent_run), return it without creating a duplicate.  This prevents
        retry storms and pipeline re-runs from producing multiple identical
        recommendations in the review queue.

        Intentionally distinct decisions (e.g. a human accepts one and a new
        cycle creates a fresh recommendation) are allowed because the accepted
        filter ensures only the pending record is de-duped.
        """
        existing = AgentRecommendation.objects.filter(
            reconciliation_result=reconciliation_result,
            recommendation_type=recommendation_type,
            accepted__isnull=True,  # pending only; accepted/rejected recs are not affected
        ).first()
        if existing:
            logger.info(
                "Idempotent recommendation: result=%s type=%s -- pending rec #%s already exists, skipping create",
                reconciliation_result.pk, recommendation_type, existing.pk,
            )
            return existing

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
