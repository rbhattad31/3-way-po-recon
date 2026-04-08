"""Classification service — determines final match status from comparison evidence."""
from __future__ import annotations

import logging
from typing import Optional

from apps.core.enums import MatchStatus, ReconciliationMode
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchResult
from apps.reconciliation.services.grn_match_service import GRNMatchResult
from apps.reconciliation.services.po_lookup_service import POLookupResult

logger = logging.getLogger(__name__)


class ClassificationService:
    """Classify the overall reconciliation outcome as MATCHED / PARTIAL / UNMATCHED / etc.

    Decision tree (deterministic, no AI):
      1. PO not found → UNMATCHED
      2. Low extraction confidence → REQUIRES_REVIEW
      3. Header all OK + all lines matched + all within tolerance + no GRN issues → MATCHED
         (In 2-way mode GRN checks are skipped entirely)
      4. Header all OK + some tolerance breaches but within escalation range → PARTIAL_MATCH
      5. Significant mismatches or missing lines → REQUIRES_REVIEW
      6. Any hard error → ERROR
    """

    def classify(
        self,
        po_result: POLookupResult,
        header_result: Optional[HeaderMatchResult],
        line_result: Optional[LineMatchResult],
        grn_result: Optional[GRNMatchResult],
        extraction_confidence: Optional[float] = None,
        confidence_threshold: float = 0.75,
        reconciliation_mode: str = "",
        invoice=None,
    ) -> MatchStatus:
        is_two_way = reconciliation_mode == ReconciliationMode.TWO_WAY

        # Gate 1: PO not found
        if not po_result.found:
            logger.info("Classification: UNMATCHED (PO not found)")
            return MatchStatus.UNMATCHED

        # Gate 2: Duplicate invoice → automatic review
        if invoice is not None and getattr(invoice, 'is_duplicate', False):
            logger.info("Classification: REQUIRES_REVIEW (duplicate invoice)")
            return MatchStatus.REQUIRES_REVIEW

        # Gate 3: Low extraction confidence → automatic review
        if extraction_confidence is not None and extraction_confidence < confidence_threshold:
            logger.info(
                "Classification: REQUIRES_REVIEW (low confidence %.2f < %.2f)",
                extraction_confidence, confidence_threshold,
            )
            return MatchStatus.REQUIRES_REVIEW

        # In 2-way mode, GRN issues are irrelevant.
        # For partial invoices in 3-way mode, missing GRN is expected
        # (GRN may arrive with subsequent invoices) -- only flag receipt
        # issues on GRNs that actually exist.
        is_partial = header_result.is_partial_invoice if header_result else False
        grn_ok = True
        if not is_two_way and grn_result is not None:
            if is_partial:
                # Partial: missing GRN is acceptable; only flag receipt issues
                grn_ok = not grn_result.has_receipt_issues
            else:
                # Full invoice: missing GRN is a critical issue in 3-way mode
                grn_ok = grn_result.grn_available and not grn_result.has_receipt_issues

        # Gate 3: Full match
        # First-partial invoices (no prior invoices on this PO) use
        # self-comparison so tolerances always pass.  Classify them as
        # PARTIAL_MATCH so a human can verify the partial billing amount
        # before the case is closed.
        is_first_partial = (
            is_partial
            and header_result is not None
            and header_result.prior_invoice_count == 0
        )
        if (
            header_result
            and header_result.all_ok
            and line_result
            and line_result.all_lines_matched
            and line_result.all_within_tolerance
            and grn_ok
        ):
            if is_first_partial:
                logger.info(
                    "Classification: PARTIAL_MATCH (first partial invoice -- "
                    "amounts compared against self, needs human verification)"
                )
                return MatchStatus.PARTIAL_MATCH
            mode_label = "2-way" if is_two_way else "3-way"
            logger.info("Classification: MATCHED (full %s deterministic match)", mode_label)
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

        # Gate 6: GRN receipt issues (3-way only)
        if not is_two_way and grn_result and grn_result.has_receipt_issues:
            logger.info("Classification: REQUIRES_REVIEW (GRN receipt issues)")
            return MatchStatus.REQUIRES_REVIEW

        # Gate 7: Unmatched lines
        if line_result and (line_result.unmatched_invoice_lines or line_result.unmatched_po_lines):
            logger.info("Classification: REQUIRES_REVIEW (unmatched lines)")
            return MatchStatus.REQUIRES_REVIEW

        # Default fallback
        logger.info("Classification: REQUIRES_REVIEW (default fallback)")
        return MatchStatus.REQUIRES_REVIEW
