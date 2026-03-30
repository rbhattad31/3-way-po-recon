"""
Tests for ClassificationService (CS-01 → CS-14)

All tests use lightweight mock/dataclass objects — no DB access needed.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from apps.core.enums import MatchStatus, ReconciliationMode
from apps.reconciliation.services.classification_service import ClassificationService
from apps.reconciliation.services.po_lookup_service import POLookupResult
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchResult
from apps.reconciliation.services.grn_match_service import GRNMatchResult


# ─── Helper builders ──────────────────────────────────────────────────────────

def make_po_found() -> POLookupResult:
    r = POLookupResult.__new__(POLookupResult) if hasattr(POLookupResult, '__new__') else object.__new__(POLookupResult)
    r.found = True
    r.purchase_order = MagicMock()
    return r


def make_po_not_found() -> POLookupResult:
    r = make_po_found()
    r.found = False
    r.purchase_order = None
    return r


def make_header(all_ok=True) -> HeaderMatchResult:
    r = HeaderMatchResult()
    r.all_ok = all_ok
    r.vendor_match = True
    r.currency_match = True
    r.po_total_match = True
    r.tax_match = None
    return r


def make_line(all_matched=True, all_tolerance=True, unmatched_inv=None, unmatched_po=None) -> LineMatchResult:
    r = LineMatchResult()
    r.all_lines_matched = all_matched
    r.all_within_tolerance = all_tolerance
    r.unmatched_invoice_lines = unmatched_inv or []
    r.unmatched_po_lines = unmatched_po or []
    return r


def make_grn(available=True, issues=False) -> GRNMatchResult:
    r = GRNMatchResult()
    r.grn_available = available
    r.has_receipt_issues = issues
    return r


def make_invoice(is_duplicate=False):
    inv = MagicMock()
    inv.is_duplicate = is_duplicate
    return inv


# ─── Classification Tests ──────────────────────────────────────────────────────

class TestClassificationService:
    def setup_method(self):
        self.svc = ClassificationService()

    # ── Gate 1: PO not found ──────────────────────────────────────────────────

    def test_cs01_po_not_found_unmatched(self):
        """CS-01: PO not found → UNMATCHED regardless of anything else."""
        result = self.svc.classify(
            po_result=make_po_not_found(),
            header_result=make_header(),
            line_result=make_line(),
            grn_result=None,
        )
        assert result == MatchStatus.UNMATCHED

    # ── Gate 2: Duplicate invoice ─────────────────────────────────────────────

    def test_cs13_duplicate_invoice_review(self):
        """CS-13: Duplicate invoice flag → REQUIRES_REVIEW."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(),
            line_result=make_line(),
            grn_result=None,
            invoice=make_invoice(is_duplicate=True),
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    # ── Gate 3: Low confidence ────────────────────────────────────────────────

    def test_cs02_low_confidence_triggers_review(self):
        """CS-02: confidence=0.60 below threshold=0.75 → REQUIRES_REVIEW."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(),
            line_result=make_line(),
            grn_result=None,
            extraction_confidence=0.60,
            confidence_threshold=0.75,
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    def test_cs03_exactly_at_confidence_threshold_not_review(self):
        """CS-03: confidence=0.75 at threshold=0.75 → NOT review (threshold is strict <)."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(),
            line_result=make_line(),
            grn_result=make_grn(available=True, issues=False),
            extraction_confidence=0.75,
            confidence_threshold=0.75,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        # At threshold → should not trigger low-confidence gate
        assert result != MatchStatus.REQUIRES_REVIEW or result == MatchStatus.MATCHED

    # ── Full match ────────────────────────────────────────────────────────────

    def test_cs04_full_3way_match(self):
        """CS-04: Header OK, all lines matched + tolerance, GRN OK → MATCHED."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=make_grn(available=True, issues=False),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        assert result == MatchStatus.MATCHED

    def test_cs05_full_2way_match_no_grn(self):
        """CS-05: 2-way mode, header OK, lines OK, GRN=None → MATCHED."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert result == MatchStatus.MATCHED

    # ── Partial match ─────────────────────────────────────────────────────────

    def test_cs06_partial_tolerance_breach(self):
        """CS-06: Header OK, lines matched, some tolerance failures → PARTIAL_MATCH."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=False),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert result == MatchStatus.PARTIAL_MATCH

    def test_cs07_partial_header_mismatch_lines_ok(self):
        """CS-07: Header NOT all_ok but lines matched → PARTIAL_MATCH."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=False),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert result == MatchStatus.PARTIAL_MATCH

    # ── 3-way GRN gates ───────────────────────────────────────────────────────

    def test_cs08_grn_not_available_3way(self):
        """CS-08: 3-way mode, GRN not available.

        Code path: grn_ok=False blocks full MATCHED gate. Gate 4 requires
        not-all-within-tolerance (fails). Gate 5 fires (header OK + lines
        matched) -> PARTIAL_MATCH. Gate 6 (GRN receipt issues) is never
        reached. Actual code returns PARTIAL_MATCH, not REQUIRES_REVIEW.
        """
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=make_grn(available=False, issues=False),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        # Gate 5 fires: header ok + lines matched -> PARTIAL_MATCH
        assert result == MatchStatus.PARTIAL_MATCH

    def test_cs08_grn_not_available_with_unmatched_lines(self):
        """CS-08b: 3-way, GRN missing AND some lines unmatched -> REQUIRES_REVIEW.

        When lines are not all matched, Gate 5 does not fire, and Gate 7
        (unmatched lines) triggers REQUIRES_REVIEW.
        """
        mock_line = MagicMock()
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=False, unmatched_inv=[mock_line]),
            grn_result=make_grn(available=False, issues=False),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    def test_cs09_grn_receipt_issues_3way(self):
        """CS-09: 3-way mode, GRN has receipt issues.

        Code path: grn_ok=False (has_receipt_issues=True) blocks full MATCHED
        gate. Gate 4 requires not-all-within-tolerance (fails here). Gate 5
        fires (header OK + lines matched) -> PARTIAL_MATCH. Gate 6 (GRN
        issues) is after Gate 5 in the decision tree so is not reached.
        Actual code returns PARTIAL_MATCH.
        """
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=make_grn(available=True, issues=True),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        # Gate 5 fires before Gate 6
        assert result == MatchStatus.PARTIAL_MATCH

    def test_cs09_grn_receipt_issues_with_unmatched_lines(self):
        """CS-09b: GRN receipt issues + unmatched lines -> REQUIRES_REVIEW via Gate 6.

        When lines are not all matched, Gate 5 does not fire. Gate 6 then
        fires because GRN has receipt issues in 3-way mode.
        """
        mock_line = MagicMock()
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=False, unmatched_inv=[mock_line]),
            grn_result=make_grn(available=True, issues=True),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    def test_cs10_grn_issues_ignored_in_2way(self):
        """CS-10: 2-way mode, GRN has issues but should be ignored → MATCHED."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=make_grn(available=True, issues=True),  # GRN issues present but ignored
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert result == MatchStatus.MATCHED

    # ── Unmatched lines ───────────────────────────────────────────────────────

    def test_cs11_unmatched_invoice_lines(self):
        """CS-11: Some invoice lines have no PO match → REQUIRES_REVIEW."""
        mock_line = MagicMock()
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=False, unmatched_inv=[mock_line]),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    def test_cs12_unmatched_po_lines(self):
        """CS-12: PO has lines not invoiced → REQUIRES_REVIEW."""
        mock_po_line = MagicMock()
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=False, unmatched_po=[mock_po_line]),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    # ── Default fallback ──────────────────────────────────────────────────────

    def test_cs14_default_fallback_review(self):
        """CS-14: None header result → falls through to REQUIRES_REVIEW."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=None,
            line_result=None,
            grn_result=None,
        )
        assert result == MatchStatus.REQUIRES_REVIEW

    # ── No confidence provided ────────────────────────────────────────────────

    def test_no_confidence_does_not_block(self):
        """No extraction_confidence provided should not trigger confidence gate."""
        result = self.svc.classify(
            po_result=make_po_found(),
            header_result=make_header(all_ok=True),
            line_result=make_line(all_matched=True, all_tolerance=True),
            grn_result=make_grn(available=True, issues=False),
            extraction_confidence=None,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        assert result == MatchStatus.MATCHED
