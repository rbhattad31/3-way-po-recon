"""
ReviewRoutingEngine — Enhanced review routing with queue classification.

Routes extraction runs to specific review queues based on:
- Low confidence
- Tax issues
- Vendor mismatch
- Schema missing
- Country-specific compliance triggers
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from apps.core.enums import ReviewQueue
from apps.extraction_core.services.output_contract import ExtractionOutputContract

logger = logging.getLogger(__name__)

# Confidence thresholds
CRITICAL_CONFIDENCE = 0.40
LOW_CONFIDENCE = 0.65
TAX_CONFIDENCE = 0.60


@dataclass
class RoutingDecision:
    """Result of review routing evaluation."""
    needs_review: bool = False
    queue: str = ""
    priority: str = "NORMAL"
    reasons: list[str] = field(default_factory=list)
    suggested_reviewer_role: str = ""

    def to_dict(self) -> dict:
        return {
            "needs_review": self.needs_review,
            "queue": self.queue,
            "priority": self.priority,
            "reasons": self.reasons,
            "suggested_reviewer_role": self.suggested_reviewer_role,
        }


class ReviewRoutingEngine:
    """
    Enhanced review routing engine.

    Evaluates extraction output against routing rules and assigns
    to the appropriate review queue.
    """

    @classmethod
    def evaluate(
        cls,
        output: ExtractionOutputContract,
        overall_confidence: float = 0.0,
        has_tax_issues: bool = False,
        has_vendor_mismatch: bool = False,
        schema_missing: bool = False,
        decision_codes: Optional[List[str]] = None,
    ) -> RoutingDecision:
        """
        Evaluate routing for an extraction result.

        Returns a RoutingDecision with queue assignment and reasons.

        Args:
            decision_codes: Optional list of machine-readable decision codes
                (from apps.extraction.decision_codes).  When provided, code-based
                routing rules run first and take precedence over confidence rules.
        """
        decision = RoutingDecision()
        reasons: list[str] = []

        # Rule 0: Decision-code-based routing (highest precedence)
        if decision_codes:
            cls._apply_decision_codes(decision, reasons, decision_codes)
            # If decision codes already set a CRITICAL queue, return immediately
            if decision.queue == ReviewQueue.EXCEPTION_OPS and decision.priority == "CRITICAL":
                decision.reasons = reasons
                return decision

        # Rule 1: Critical confidence → EXCEPTION_OPS
        if overall_confidence < CRITICAL_CONFIDENCE:
            decision.needs_review = True
            decision.queue = ReviewQueue.EXCEPTION_OPS
            decision.priority = "CRITICAL"
            reasons.append(
                f"Critical confidence: {overall_confidence:.2%} < {CRITICAL_CONFIDENCE:.0%}"
            )
            decision.suggested_reviewer_role = "ADMIN"
            decision.reasons = reasons
            return decision

        # Rule 2: Tax issues → TAX_REVIEW
        if has_tax_issues or cls._has_tax_warnings(output):
            decision.needs_review = True
            decision.queue = ReviewQueue.TAX_REVIEW
            decision.priority = "HIGH"
            reasons.append("Tax consistency issues detected")
            decision.suggested_reviewer_role = "FINANCE_MANAGER"

        # Rule 3: Vendor mismatch → MASTER_DATA_REVIEW
        if has_vendor_mismatch or cls._has_vendor_issues(output):
            decision.needs_review = True
            if not decision.queue:
                decision.queue = ReviewQueue.MASTER_DATA_REVIEW
            decision.priority = max(decision.priority, "HIGH")
            reasons.append("Vendor mismatch or unknown vendor")
            if not decision.suggested_reviewer_role:
                decision.suggested_reviewer_role = "AP_PROCESSOR"

        # Rule 4: Schema missing → COMPLIANCE
        if schema_missing:
            decision.needs_review = True
            if not decision.queue:
                decision.queue = ReviewQueue.COMPLIANCE
            reasons.append("No extraction schema found for jurisdiction")
            if not decision.suggested_reviewer_role:
                decision.suggested_reviewer_role = "ADMIN"

        # Rule 5: Low confidence → AP_REVIEW
        if overall_confidence < LOW_CONFIDENCE:
            decision.needs_review = True
            if not decision.queue:
                decision.queue = ReviewQueue.AP_REVIEW
                decision.priority = "NORMAL"
            reasons.append(
                f"Low confidence: {overall_confidence:.2%} < {LOW_CONFIDENCE:.0%}"
            )
            if not decision.suggested_reviewer_role:
                decision.suggested_reviewer_role = "REVIEWER"

        # Rule 6: Errors in output
        if output.errors:
            decision.needs_review = True
            if not decision.queue:
                decision.queue = ReviewQueue.EXCEPTION_OPS
            reasons.append(f"Extraction errors: {len(output.errors)}")

        decision.reasons = reasons
        return decision

    @classmethod
    def _apply_decision_codes(
        cls,
        decision: RoutingDecision,
        reasons: list,
        decision_codes: List[str],
    ) -> None:
        """Apply routing rules derived from machine-readable decision codes.

        Consults ROUTING_MAP from apps.extraction.decision_codes.
        Queue assignment follows a priority ladder:
          EXCEPTION_OPS > TAX_REVIEW > MASTER_DATA_REVIEW > AP_REVIEW
        The highest-priority queue wins; lower-priority queues are noted in
        reasons but do not override a higher-priority assignment.
        """
        try:
            from apps.extraction.decision_codes import ROUTING_MAP, HARD_REVIEW_CODES
        except ImportError:
            logger.warning("decision_codes module unavailable — skipping code-based routing")
            return

        # Queue priority order (higher index = higher priority)
        _QUEUE_PRIORITY = {
            ReviewQueue.AP_REVIEW: 1,
            ReviewQueue.MASTER_DATA_REVIEW: 2,
            ReviewQueue.TAX_REVIEW: 3,
            ReviewQueue.EXCEPTION_OPS: 4,
        }

        has_hard_code = any(c in HARD_REVIEW_CODES for c in decision_codes)
        best_queue = ""
        best_priority = 0

        for code in decision_codes:
            queue_str = ROUTING_MAP.get(code)
            if not queue_str:
                continue
            # Map string → ReviewQueue value
            queue_val = getattr(ReviewQueue, queue_str, queue_str)
            q_prio = _QUEUE_PRIORITY.get(queue_val, 0)
            if q_prio > best_priority:
                best_priority = q_prio
                best_queue = queue_val
            reasons.append(f"[{code}] → {queue_str}")
            decision.needs_review = True

        if best_queue:
            decision.queue = best_queue
            decision.priority = "CRITICAL" if has_hard_code else "HIGH"
            if not decision.suggested_reviewer_role:
                _ROLE_MAP = {
                    ReviewQueue.EXCEPTION_OPS: "ADMIN",
                    ReviewQueue.TAX_REVIEW: "FINANCE_MANAGER",
                    ReviewQueue.MASTER_DATA_REVIEW: "AP_PROCESSOR",
                    ReviewQueue.AP_REVIEW: "REVIEWER",
                }
                decision.suggested_reviewer_role = _ROLE_MAP.get(best_queue, "REVIEWER")

    @classmethod
    def _has_tax_warnings(cls, output: ExtractionOutputContract) -> bool:
        """Check if output has tax-related warnings."""
        return any(
            w.code in ("TAX_CONSISTENCY", "GST_CONSISTENCY", "VAT_CONSISTENCY")
            for w in output.warnings
        )

    @classmethod
    def _has_vendor_issues(cls, output: ExtractionOutputContract) -> bool:
        """Check if output has vendor-related issues."""
        supplier = output.parties.supplier
        if not supplier:
            return True

        name_fv = supplier.get("name") or supplier.get("supplier_name")
        if not name_fv or not name_fv.value:
            return True

        if name_fv.confidence and name_fv.confidence < 0.5:
            return True

        return False
