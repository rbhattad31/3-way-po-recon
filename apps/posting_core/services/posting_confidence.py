"""Posting Confidence — calculates overall confidence for a posting proposal."""
from __future__ import annotations

from typing import Any, Dict, List

from apps.posting_core.services.posting_mapping_engine import PostingProposal


class PostingConfidenceService:
    """Calculates confidence dimensions for a posting proposal."""

    # Dimension weights
    WEIGHTS = {
        "header_completeness": 0.15,
        "vendor_mapping": 0.25,
        "line_mapping": 0.30,
        "tax_completeness": 0.15,
        "reference_freshness": 0.15,
    }

    @classmethod
    def calculate(cls, proposal: PostingProposal, issues: List[Dict[str, Any]]) -> float:
        """Calculate overall confidence score (0.0 – 1.0)."""
        dims = cls.dimensions(proposal, issues)
        score = sum(
            dims[k] * cls.WEIGHTS[k]
            for k in cls.WEIGHTS
        )
        return round(min(max(score, 0.0), 1.0), 4)

    @classmethod
    def dimensions(cls, proposal: PostingProposal, issues: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate individual confidence dimensions."""
        return {
            "header_completeness": cls._header_completeness(proposal),
            "vendor_mapping": cls._vendor_mapping(proposal),
            "line_mapping": cls._line_mapping(proposal),
            "tax_completeness": cls._tax_completeness(proposal),
            "reference_freshness": cls._reference_freshness(issues),
        }

    @staticmethod
    def _header_completeness(proposal: PostingProposal) -> float:
        h = proposal.header
        fields = [h.invoice_number, h.invoice_date, h.currency, h.total_amount]
        present = sum(1 for f in fields if f)
        return present / len(fields)

    @staticmethod
    def _vendor_mapping(proposal: PostingProposal) -> float:
        return proposal.header.vendor_confidence

    @staticmethod
    def _line_mapping(proposal: PostingProposal) -> float:
        if not proposal.lines:
            return 0.0
        return sum(lp.confidence for lp in proposal.lines) / len(proposal.lines)

    @staticmethod
    def _tax_completeness(proposal: PostingProposal) -> float:
        if not proposal.lines:
            return 0.0
        with_tax = sum(1 for lp in proposal.lines if lp.tax_code)
        return with_tax / len(proposal.lines)

    @staticmethod
    def _reference_freshness(issues: List[Dict[str, Any]]) -> float:
        staleness_issues = [i for i in issues if i.get("check_type") == "reference_staleness"]
        if not staleness_issues:
            return 1.0
        # Degrade proportionally to number of stale references
        return max(0.3, 1.0 - 0.15 * len(staleness_issues))
