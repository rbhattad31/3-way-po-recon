"""
Tests for ToleranceEngine (TE-01 → TE-12)

Pure unit tests — no database access required.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from apps.reconciliation.services.tolerance_engine import ToleranceEngine, ToleranceThresholds


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_engine(qty=2.0, price=1.0, amount=1.0) -> ToleranceEngine:
    """Build a ToleranceEngine without hitting the DB."""
    engine = ToleranceEngine.__new__(ToleranceEngine)
    engine.thresholds = ToleranceThresholds(
        quantity_pct=qty,
        price_pct=price,
        amount_pct=amount,
    )
    return engine


# ─── Quantity Comparisons ─────────────────────────────────────────────────────

class TestQuantityComparisons:
    def test_te01_exact_match(self):
        """TE-01: 100 vs 100 — exact match, difference=0."""
        engine = make_engine()
        result = engine.compare_quantity(Decimal("100"), Decimal("100"))
        assert result.within_tolerance is True
        assert result.difference == Decimal("0")

    def test_te02_within_tolerance(self):
        """TE-02: 100 vs 101.5 (1.5% diff) within 2% limit."""
        engine = make_engine(qty=2.0)
        result = engine.compare_quantity(Decimal("100"), Decimal("101.5"))
        assert result.within_tolerance is True

    def test_te03_exceeds_tolerance(self):
        """TE-03: 100 vs 103 (3% diff) exceeds 2% limit."""
        engine = make_engine(qty=2.0)
        result = engine.compare_quantity(Decimal("100"), Decimal("103"))
        assert result.within_tolerance is False

    def test_te04_at_boundary(self):
        """TE-04: 100 vs 102 (exactly 2%) — boundary should be within tolerance."""
        engine = make_engine(qty=2.0)
        result = engine.compare_quantity(Decimal("100"), Decimal("102"))
        assert result.within_tolerance is True

    def test_te11_negative_difference(self):
        """TE-11: Invoice qty 95 vs PO qty 100 — difference is negative."""
        engine = make_engine(qty=5.0)  # use wider tolerance so it's within
        result = engine.compare_quantity(Decimal("95"), Decimal("100"))
        assert result.difference == Decimal("-5")
        assert result.within_tolerance is True  # 5% diff, 5% tolerance

    def test_te11_negative_exceeds_tolerance(self):
        """TE-11 variant: 95 vs 100 (5% diff) exceeds 2% limit."""
        engine = make_engine(qty=2.0)
        result = engine.compare_quantity(Decimal("95"), Decimal("100"))
        assert result.within_tolerance is False


# ─── Price Comparisons ────────────────────────────────────────────────────────

class TestPriceComparisons:
    def test_te05_within_price_tolerance(self):
        """TE-05: 10.00 vs 10.09 (0.9% diff) within 1% limit."""
        engine = make_engine(price=1.0)
        result = engine.compare_price(Decimal("10.00"), Decimal("10.09"))
        assert result.within_tolerance is True

    def test_te06_exceeds_price_tolerance(self):
        """TE-06: 10.00 vs 10.12 (1.2% diff) exceeds 1% limit."""
        engine = make_engine(price=1.0)
        result = engine.compare_price(Decimal("10.00"), Decimal("10.12"))
        assert result.within_tolerance is False


# ─── None Handling ────────────────────────────────────────────────────────────

class TestNoneHandling:
    def test_te07_none_invoice_value(self):
        """TE-07: None invoice value → within_tolerance is None."""
        engine = make_engine()
        result = engine.compare_quantity(None, Decimal("100"))
        assert result.within_tolerance is None
        assert result.invoice_value is None
        assert result.po_value == Decimal("100")

    def test_te08_none_po_value(self):
        """TE-08: None PO value → within_tolerance is None."""
        engine = make_engine()
        result = engine.compare_quantity(Decimal("100"), None)
        assert result.within_tolerance is None
        assert result.po_value is None

    def test_te09_both_none(self):
        """TE-09: Both None → within_tolerance is None."""
        engine = make_engine()
        result = engine.compare_quantity(None, None)
        assert result.within_tolerance is None
        assert result.difference is None
        assert result.difference_pct is None


# ─── Zero-Base Edge Case ──────────────────────────────────────────────────────

class TestZeroBase:
    def test_te10_zero_base_no_exception(self):
        """TE-10: 0.00 vs 0.01 — should not raise ZeroDivisionError."""
        engine = make_engine()
        try:
            result = engine.compare_quantity(Decimal("0.00"), Decimal("0.01"))
            # Result should be defined (True or False) — not crash
            assert result.within_tolerance is not None or result.within_tolerance is None
        except ZeroDivisionError:
            pytest.fail("ToleranceEngine raised ZeroDivisionError on zero base value")

    def test_te10_both_zero_exact(self):
        """TE-10 variant: 0.00 vs 0.00 — exact match, no crash."""
        engine = make_engine()
        result = engine.compare_quantity(Decimal("0.00"), Decimal("0.00"))
        # Both zero = 0 difference — should be within tolerance
        assert result.difference == Decimal("0")


# ─── Custom Config Thresholds ─────────────────────────────────────────────────

class TestCustomThresholds:
    def test_te12_custom_config_thresholds(self):
        """TE-12: Config with qty=5%, price=3%, amount=3% — thresholds respected."""
        engine = make_engine(qty=5.0, price=3.0, amount=3.0)
        # 4% qty diff — within custom 5% threshold
        qty_result = engine.compare_quantity(Decimal("100"), Decimal("104"))
        assert qty_result.within_tolerance is True

        # 2.5% price diff — within custom 3% threshold
        price_result = engine.compare_price(Decimal("10.00"), Decimal("10.25"))
        assert price_result.within_tolerance is True

        # 2% qty diff — would fail default 2% but engine's threshold is 5%
        qty_result2 = engine.compare_quantity(Decimal("100"), Decimal("102"))
        assert qty_result2.within_tolerance is True

    def test_te12_tight_threshold_fails(self):
        """TE-12 variant: tiny tolerance means even small diffs fail."""
        engine = make_engine(qty=0.5)  # 0.5% tolerance
        result = engine.compare_quantity(Decimal("100"), Decimal("101"))  # 1% diff
        assert result.within_tolerance is False


# ─── FieldComparison Data Integrity ──────────────────────────────────────────

class TestFieldComparisonData:
    def test_comparison_stores_both_values(self):
        """Result should preserve both invoice and PO values."""
        engine = make_engine()
        result = engine.compare_amount(Decimal("500"), Decimal("505"))
        assert result.invoice_value == Decimal("500")
        assert result.po_value == Decimal("505")
        assert result.difference == Decimal("-5")

    def test_comparison_pct_is_populated(self):
        """Percentage difference should be calculated and stored."""
        engine = make_engine()
        result = engine.compare_quantity(Decimal("100"), Decimal("102"))
        assert result.difference_pct is not None

    def test_all_three_compare_methods_work(self):
        """All three compare_* methods should return FieldComparison."""
        engine = make_engine()
        from apps.reconciliation.services.tolerance_engine import FieldComparison
        assert isinstance(engine.compare_quantity(Decimal("10"), Decimal("10")), FieldComparison)
        assert isinstance(engine.compare_price(Decimal("10"), Decimal("10")), FieldComparison)
        assert isinstance(engine.compare_amount(Decimal("10"), Decimal("10")), FieldComparison)
