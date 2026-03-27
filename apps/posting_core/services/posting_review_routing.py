"""Posting Review Routing — determines which review queue(s) apply."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from apps.core.enums import PostingIssueSeverity, PostingReviewQueue
from apps.posting_core.services.posting_mapping_engine import PostingProposal


class PostingReviewRoutingService:
    """Routes posting proposals to the appropriate review queue."""

    @classmethod
    def route(
        cls,
        proposal: PostingProposal,
        issues: List[Dict[str, Any]],
        confidence: float,
    ) -> Tuple[bool, str, List[str]]:
        """Determine if review is required and which queue.

        Returns:
            (requires_review, primary_queue, reasons)
        """
        reasons: List[str] = []
        queues: List[str] = []

        # Vendor unresolved
        if not proposal.header.vendor_code:
            queues.append(PostingReviewQueue.VENDOR_MAPPING_REVIEW)
            reasons.append("Vendor code not resolved")

        # Check line-level issues
        for lp in proposal.lines:
            if not lp.erp_item_code and lp.confidence < 0.5:
                if PostingReviewQueue.ITEM_MAPPING_REVIEW not in queues:
                    queues.append(PostingReviewQueue.ITEM_MAPPING_REVIEW)
                    reasons.append(f"Item mapping unresolved for line {lp.line_index}")

            if not lp.tax_code:
                if PostingReviewQueue.TAX_REVIEW not in queues:
                    queues.append(PostingReviewQueue.TAX_REVIEW)
                    reasons.append("Tax code not assigned for one or more lines")

            if not lp.cost_center:
                if PostingReviewQueue.COST_CENTER_REVIEW not in queues:
                    queues.append(PostingReviewQueue.COST_CENTER_REVIEW)
                    reasons.append("Cost center not resolved for one or more lines")

        # Check for ERROR-severity issues
        error_issues = [i for i in issues if i.get("severity") == PostingIssueSeverity.ERROR]
        if error_issues:
            if PostingReviewQueue.POSTING_OPS not in queues:
                queues.append(PostingReviewQueue.POSTING_OPS)
                reasons.append(f"{len(error_issues)} blocking issue(s) found")

        # Low confidence
        if confidence < 0.7 and not queues:
            queues.append(PostingReviewQueue.POSTING_OPS)
            reasons.append(f"Low overall confidence: {confidence:.0%}")

        requires_review = len(queues) > 0
        primary_queue = queues[0] if queues else ""

        return requires_review, primary_queue, reasons
