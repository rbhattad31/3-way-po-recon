"""Classification service — determines final match status from comparison evidence."""
from __future__ import annotations

import logging
from typing import Optional

from apps.core.enums import MatchStatus
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchResult
from apps.reconciliation.services.grn_match_service import GRNMatchResult
from apps.reconciliation.services.po_lookup_service import POLookupResult

logger = logging.getLogger(__name__)


class ClassificationService:
    """Classify the overall reconciliation outcome as MATCHED / PARTIAL / UNMATCHED / etc.

    Decision tree (deterministic, no AI):
      1. PO not found → UNMATCHED
      2. Header all OK + all lines matched + all within tolerance + no GRN issues → MATCHED
      3. Header all OK + some tolerance breaches but within escalation range → PARTIAL_MATCH
      4. Significant mismatches or missing lines → REQUIRES_REVIEW
      5. Any hard error → ERROR
    """

    def classify(
        self,
        po_result: POLookupResult,
        header_result: Optional[HeaderMatchResult],
        line_result: Optional[LineMatchResult],
        grn_result: Optional[GRNMatchResult],
        extraction_confidence: Optional[float] = None,
        confidence_threshold: float = 0.75,
    ) -> MatchStatus:
        # Gate 1: PO not found
        if not po_result.found:
            logger.info("Classification: UNMATCHED (PO not found)")
            return MatchStatus.UNMATCHED

        # Gate 2: Low extraction confidence → automatic review
        if extraction_confidence is not None and extraction_confidence < confidence_threshold:
            logger.info(
                "Classification: REQUIRES_REVIEW (low confidence %.2f < %.2f)",
                extraction_confidence, confidence_threshold,
            )
            return MatchStatus.REQUIRES_REVIEW

        # Gate 3: Full match
        if (
            header_result
            and header_result.all_ok
            and line_result
            and line_result.all_lines_matched
            and line_result.all_within_tolerance
            and (grn_result is None or not grn_result.has_receipt_issues)
        ):
            logger.info("Classification: MATCHED (full deterministic match)")
            return MatchStatus.MATCHED

        # Gate 4: Partial — header passes, some line issues
        if header_result and header_result.all_ok and line_result:
            if line_result.all_lines_matched and not line_result.all_within_tolerance:
                logger.info("Classification: PARTIAL_MATCH (tolerance breaches)")
                return MatchStatus.PARTIAL_MATCH

        # Gate 5: Partial — header issues but lines mostly ok
        if header_result and line_result and line_result.all_lines_matched:
            logger.info("Classification: PARTIAL_MATCH (header mismatch, lines matched)")
            return MatchStatus.PARTIAL_MATCH

        # Gate 6: GRN receipt issues
        if grn_result and grn_result.has_receipt_issues:
            logger.info("Classification: REQUIRES_REVIEW (GRN receipt issues)")
            return MatchStatus.REQUIRES_REVIEW

        # Gate 7: Unmatched lines
        if line_result and (line_result.unmatched_invoice_lines or line_result.unmatched_po_lines):
            logger.info("Classification: REQUIRES_REVIEW (unmatched lines)")
            return MatchStatus.REQUIRES_REVIEW

        # Default fallback
        logger.info("Classification: REQUIRES_REVIEW (default fallback)")
        return MatchStatus.REQUIRES_REVIEW
