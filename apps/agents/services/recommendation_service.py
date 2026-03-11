"""Recommendation service — manages agent recommendation lifecycle."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.utils import timezone

from apps.agents.models import AgentRecommendation

logger = logging.getLogger(__name__)


class RecommendationService:
    """Create, query, and manage agent recommendations."""

    @staticmethod
    def create_recommendation(
        invoice_id: int,
        reconciliation_result_id: int,
        agent_run_id: int,
        agent_name: str,
        recommendation_type: str,
        summary: str,
        confidence: float,
        evidence: Optional[Dict[str, Any]] = None,
        recommended_action: str = "",
    ) -> AgentRecommendation:
        """Record a new agent recommendation.

        Args:
            invoice_id: FK to Invoice.
            reconciliation_result_id: FK to ReconciliationResult.
            agent_run_id: FK to AgentRun that produced this recommendation.
            agent_name: Human-readable agent name.
            recommendation_type: RecommendationType enum value.
            summary: Summary reasoning for the recommendation.
            confidence: Confidence score 0.0–1.0.
            evidence: Supporting exception data / evidence dict.
            recommended_action: Specific action description.
        """
        rec = AgentRecommendation.objects.create(
            agent_run_id=agent_run_id,
            reconciliation_result_id=reconciliation_result_id,
            invoice_id=invoice_id,
            recommendation_type=recommendation_type,
            confidence=confidence,
            reasoning=summary,
            evidence=evidence,
            recommended_action=recommended_action,
        )
        logger.info(
            "Recommendation created: type=%s confidence=%.2f invoice=%s agent=%s",
            recommendation_type, confidence, invoice_id, agent_name,
        )
        return rec

    @staticmethod
    def get_recommendations_for_invoice(invoice_id: int) -> List[Dict[str, Any]]:
        """Return all recommendations for an invoice, highest-confidence first."""
        return list(
            AgentRecommendation.objects.filter(
                invoice_id=invoice_id,
            ).select_related("agent_run").values(
                "id", "agent_run__agent_type", "recommendation_type",
                "confidence", "reasoning", "evidence", "recommended_action",
                "accepted", "accepted_by__email", "accepted_at", "created_at",
            ).order_by("-confidence")
        )

    @staticmethod
    def get_recommendations_for_result(result_id: int) -> List[Dict[str, Any]]:
        """Return all recommendations for a reconciliation result."""
        return list(
            AgentRecommendation.objects.filter(
                reconciliation_result_id=result_id,
            ).select_related("agent_run").values(
                "id", "agent_run__agent_type", "recommendation_type",
                "confidence", "reasoning", "evidence", "recommended_action",
                "accepted", "accepted_by__email", "accepted_at", "created_at",
            ).order_by("-confidence")
        )

    @staticmethod
    def mark_recommendation_accepted(
        recommendation_id: int,
        user,
        accepted: bool = True,
    ) -> AgentRecommendation:
        """Mark a recommendation as accepted or rejected by a user."""
        rec = AgentRecommendation.objects.get(pk=recommendation_id)
        rec.accepted = accepted
        rec.accepted_by = user
        rec.accepted_at = timezone.now()
        rec.save(update_fields=["accepted", "accepted_by", "accepted_at", "updated_at"])
        logger.info(
            "Recommendation %s %s by %s",
            recommendation_id, "accepted" if accepted else "rejected", user,
        )
        return rec
