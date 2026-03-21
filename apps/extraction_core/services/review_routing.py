"""
ReviewRoutingService — Confidence-driven review routing decisions.

Evaluates a ``ConfidenceBreakdown`` and produces a ``ReviewRoutingDecision``
indicating whether the extraction needs human review and why.

This service is **data-layer only** — it does NOT create UI objects
(like ReviewAssignment) directly. Callers decide what to do with
the decision.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from apps.extraction_core.services.extraction_service import (
        ConfidenceBreakdown,
        ExtractionResult,
    )

logger = logging.getLogger(__name__)


@dataclass
class ReviewRoutingDecision:
    """Immutable routing decision produced after confidence evaluation."""

    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)
    priority: str = "NORMAL"  # LOW | NORMAL | HIGH | CRITICAL
    suggested_review_type: str = ""  # e.g. EXTRACTION_QA, TAX_SPECIALIST

    def to_dict(self) -> dict:
        return {
            "needs_review": self.needs_review,
            "reasons": self.reasons,
            "priority": self.priority,
            "suggested_review_type": self.suggested_review_type,
        }


class ReviewRoutingService:
    """Stateless routing service — all public methods are classmethods."""

    @classmethod
    def evaluate(
        cls,
        confidence: "ConfidenceBreakdown",
        result: "ExtractionResult",
    ) -> ReviewRoutingDecision:
        """
        Produce a ``ReviewRoutingDecision`` from the confidence breakdown.

        Priority tiers:
            CRITICAL  — overall < 0.40 or multiple dimensions failing
            HIGH      — overall < threshold or tax < threshold
            NORMAL    — any single dimension below threshold
            LOW       — minor warnings only
        """
        threshold = getattr(
            settings,
            "EXTRACTION_CONFIDENCE_THRESHOLD",
            0.75,
        )

        decision = ReviewRoutingDecision()
        decision.reasons = list(confidence.review_reasons)
        decision.needs_review = confidence.requires_review

        if not decision.needs_review:
            decision.priority = "LOW"
            return decision

        # Count how many dimensions are below threshold
        failing_dims = sum(
            1
            for val in [
                confidence.header,
                confidence.tax,
                confidence.line_item,
                confidence.jurisdiction,
            ]
            if val < threshold
        )

        # Determine priority
        if confidence.overall < 0.40 or failing_dims >= 3:
            decision.priority = "CRITICAL"
        elif confidence.overall < threshold or confidence.tax < threshold:
            decision.priority = "HIGH"
        elif failing_dims >= 1:
            decision.priority = "NORMAL"
        else:
            decision.priority = "LOW"

        # Suggest review type
        if confidence.tax < threshold:
            decision.suggested_review_type = "TAX_SPECIALIST"
        elif confidence.jurisdiction < threshold:
            decision.suggested_review_type = "JURISDICTION_REVIEW"
        else:
            decision.suggested_review_type = "EXTRACTION_QA"

        return decision
