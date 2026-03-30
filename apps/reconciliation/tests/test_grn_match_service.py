"""
Tests for GRNMatchService (GM-01 → GM-10)

Uses lightweight mocks for GRNSummary and LineMatchPair — no DB access.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock

from apps.reconciliation.services.grn_match_service import GRNMatchService, GRNMatchResult
from apps.reconciliation.services.grn_lookup_service import GRNSummary
from apps.reconciliation.services.line_match_service import LineMatchPair


# ─── Helper builders ──────────────────────────────────────────────────────────

def make_grn_summary(
    available=True,
    fully_received=True,
    total_received: dict | None = None,
    latest_receipt_date=None,
    grn_count=1,
) -> GRNSummary:
    summary = MagicMock(spec=GRNSummary)
    summary.grn_available = available
    summary.fully_received = fully_received
    summary.total_received_by_po_line = total_received or {}
    summary.latest_receipt_date = latest_receipt_date
    summary.grn_count = grn_count
    return summary


def make_line_pair(
    po_line_id: int,
    qty_ordered: Decimal,
    qty_invoiced: Decimal,
    matched=True,
) -> LineMatchPair:
    inv_line = MagicMock()
    inv_line.pk = po_line_id + 1000  # Different PK from PO line
    inv_line.quantity = qty_invoiced

    po_line = MagicMock()
    po_line.pk = po_line_id
    po_line.quantity = qty_ordered

    pair = LineMatchPair(invoice_line=inv_line, po_line=po_line, matched=matched)
    return pair


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestGRNMatchService:
    def setup_method(self):
        self.svc = GRNMatchService()

    def test_gm01_no_grn_available(self):
        """GM-01: grn_available=False → result with grn_available=False, no issues."""
        summary = make_grn_summary(available=False)
        result = self.svc.match([], summary)
        assert result.grn_available is False
        assert result.has_receipt_issues is False

    def test_gm02_exact_receipt_match(self):
        """GM-02: qty_received == qty_ordered == qty_invoiced → no issues."""
        po_line_id = 1
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("10")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("10"), qty_invoiced=Decimal("10"))]
        result = self.svc.match(pairs, summary)
        assert result.grn_available is True
        assert result.has_receipt_issues is False
        assert len(result.line_comparisons) == 1
        cmp = result.line_comparisons[0]
        assert cmp.over_receipt is False
        assert cmp.under_receipt is False
        assert cmp.invoiced_exceeds_received is False

    def test_gm03_over_receipt(self):
        """GM-03: received=11 > ordered=10 → over_receipt=True, has_issues=True."""
        po_line_id = 2
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("11")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("10"), qty_invoiced=Decimal("10"))]
        result = self.svc.match(pairs, summary)
        assert result.has_receipt_issues is True
        assert result.line_comparisons[0].over_receipt is True

    def test_gm04_under_receipt_alone_not_has_issues(self):
        """GM-04: received=8 < ordered=10, but invoiced=8 — under_receipt alone does NOT set has_issues."""
        po_line_id = 3
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("8")},
        )
        # Invoice exactly matches what was received (not exceeding)
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("10"), qty_invoiced=Decimal("8"))]
        result = self.svc.match(pairs, summary)
        cmp = result.line_comparisons[0]
        assert cmp.under_receipt is True
        # Invoice equals received — no exceeds_received issue
        assert cmp.invoiced_exceeds_received is False
        assert result.has_receipt_issues is False

    def test_gm05_invoice_exceeds_received(self):
        """GM-05: invoiced=10 > received=8 → invoiced_exceeds_received=True, has_issues=True."""
        po_line_id = 4
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("8")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("10"), qty_invoiced=Decimal("10"))]
        result = self.svc.match(pairs, summary)
        assert result.has_receipt_issues is True
        assert result.line_comparisons[0].invoiced_exceeds_received is True

    def test_gm06_delayed_receipt_flagged(self):
        """GM-06: Receipt 35 days after PO date → has_receipt_issues=True."""
        po_date = date(2025, 1, 1)
        receipt_date = date(2025, 2, 5)  # 35 days later
        assert (receipt_date - po_date).days == 35

        summary = make_grn_summary(
            available=True,
            total_received={},
            latest_receipt_date=receipt_date,
        )
        result = self.svc.match([], summary, po_date=po_date)
        assert result.has_receipt_issues is True

    def test_gm07_receipt_within_threshold(self):
        """GM-07: Receipt 29 days after PO date → NOT flagged as delayed."""
        po_date = date(2025, 1, 1)
        receipt_date = date(2025, 1, 30)  # 29 days later
        assert (receipt_date - po_date).days == 29

        summary = make_grn_summary(
            available=True,
            total_received={},
            latest_receipt_date=receipt_date,
        )
        result = self.svc.match([], summary, po_date=po_date)
        assert result.has_receipt_issues is False

    def test_gm08_exactly_at_delay_threshold(self):
        """GM-08: Receipt exactly 30 days — threshold is strict > 30, so NOT flagged."""
        po_date = date(2025, 1, 1)
        receipt_date = date(2025, 1, 31)  # exactly 30 days
        assert (receipt_date - po_date).days == 30

        summary = make_grn_summary(
            available=True,
            total_received={},
            latest_receipt_date=receipt_date,
        )
        result = self.svc.match([], summary, po_date=po_date)
        assert result.has_receipt_issues is False

    def test_gm09_unmatched_pairs_skipped(self):
        """GM-09: pair.matched=False → comparison not included in result."""
        po_line_id = 5
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("10")},
        )
        # Unmatched pair — should be skipped
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("10"), qty_invoiced=Decimal("10"), matched=False)]
        result = self.svc.match(pairs, summary)
        # Unmatched pairs have no po_line — GRNMatchService skips them
        assert len(result.line_comparisons) == 0

    def test_gm10_multi_grn_multiple_lines(self):
        """GM-10: 3 matched pairs, all GRN quantities match — no issues."""
        received = {
            1: Decimal("5"),
            2: Decimal("10"),
            3: Decimal("15"),
        }
        summary = make_grn_summary(
            available=True,
            total_received=received,
            grn_count=3,
        )
        pairs = [
            make_line_pair(1, qty_ordered=Decimal("5"), qty_invoiced=Decimal("5")),
            make_line_pair(2, qty_ordered=Decimal("10"), qty_invoiced=Decimal("10")),
            make_line_pair(3, qty_ordered=Decimal("15"), qty_invoiced=Decimal("15")),
        ]
        result = self.svc.match(pairs, summary)
        assert result.has_receipt_issues is False
        assert len(result.line_comparisons) == 3
        assert result.grn_count == 3
        for cmp in result.line_comparisons:
            assert cmp.over_receipt is False
            assert cmp.invoiced_exceeds_received is False

    def test_no_po_date_no_delay_check(self):
        """No po_date → delayed receipt check is skipped."""
        summary = make_grn_summary(
            available=True,
            total_received={},
            latest_receipt_date=date(2025, 1, 1),  # receipt date exists
        )
        # Without po_date, no comparison is made
        result = self.svc.match([], summary, po_date=None)
        assert result.has_receipt_issues is False

    def test_missing_po_line_for_pair_skipped(self):
        """If po_line is None on a 'matched' pair, it should be skipped gracefully."""
        pair = MagicMock(spec=LineMatchPair)
        pair.matched = True
        pair.po_line = None  # No PO line
        pair.invoice_line = MagicMock()

        summary = make_grn_summary(available=True, total_received={})
        result = self.svc.match([pair], summary)
        assert len(result.line_comparisons) == 0
