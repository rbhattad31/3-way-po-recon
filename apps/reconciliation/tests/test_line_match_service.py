"""
Tests for LineMatchService (Week 1 — LM-01 to LM-11)

All tests are DB-backed because LineMatchService calls
InvoiceLineItem.objects.filter() and PurchaseOrderLineItem.objects.filter().

Key scoring rules (from source):
  line_number match  +0.20
  desc >= 80 (FUZZY_MATCH_THRESHOLD)  +0.30
  desc >= 50  +0.15
  qty within tolerance   +0.20  (else 0.05)
  price within tolerance +0.15  (else 0.03)
  amount within tolerance +0.15 (else 0.03)
  minimum score to be matched = 0.30

Tolerance defaults (default engine): qty 2%, price 1%, amount 1%.
"""
from __future__ import annotations

import pytest
from decimal import Decimal

from apps.reconciliation.services.line_match_service import LineMatchService
from apps.reconciliation.services.tolerance_engine import ToleranceEngine, ToleranceThresholds


# ─── Engine factory ───────────────────────────────────────────────────────────

def make_engine(qty=2.0, price=1.0, amount=1.0) -> ToleranceEngine:
    engine = ToleranceEngine.__new__(ToleranceEngine)
    engine.thresholds = ToleranceThresholds(
        quantity_pct=qty,
        price_pct=price,
        amount_pct=amount,
    )
    return engine


# ─── DB fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def svc():
    return LineMatchService(make_engine())


@pytest.fixture
def invoice(db):
    from apps.reconciliation.tests.factories import InvoiceFactory
    return InvoiceFactory()


@pytest.fixture
def po(db):
    from apps.reconciliation.tests.factories import POFactory
    return POFactory()


def make_inv_line(invoice, line_number=1, description="Test Item",
                  qty="10.00", price="100.00", amount="1000.00",
                  raw_description="", tax_amount=None,
                  is_service_item=None, is_stock_item=None):
    from apps.reconciliation.tests.factories import InvoiceLineItemFactory
    return InvoiceLineItemFactory(
        invoice=invoice,
        line_number=line_number,
        description=description,
        raw_description=raw_description or description,
        normalized_description=description.lower(),
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        line_amount=Decimal(amount),
        tax_amount=Decimal(tax_amount) if tax_amount else None,
        is_service_item=is_service_item,
        is_stock_item=is_stock_item,
    )


def make_po_line(po, line_number=1, description="Test Item",
                 qty="10.0000", price="100.0000", amount="1000.00",
                 tax_amount=None, is_service_item=None, is_stock_item=None):
    from apps.reconciliation.tests.factories import POLineItemFactory
    return POLineItemFactory(
        purchase_order=po,
        line_number=line_number,
        description=description,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        line_amount=Decimal(amount),
        tax_amount=Decimal(tax_amount) if tax_amount else None,
        is_service_item=is_service_item,
        is_stock_item=is_stock_item,
    )


# ─── LM-01: Single line exact match ──────────────────────────────────────────

@pytest.mark.django_db
class TestSingleLineExactMatch:
    def test_lm01_exact_single_line_all_matched(self, svc, invoice, po):
        """LM-01: 1 invoice line, 1 PO line, identical values — full match."""
        make_inv_line(invoice, line_number=1, description="Chicken Breast",
                      qty="10.00", price="50.00", amount="500.00")
        make_po_line(po, line_number=1, description="Chicken Breast",
                     qty="10.0000", price="50.0000", amount="500.00")

        result = svc.match(invoice, po)

        assert result.all_lines_matched is True
        assert result.all_within_tolerance is True
        assert len(result.unmatched_invoice_lines) == 0
        assert len(result.unmatched_po_lines) == 0
        assert len(result.pairs) == 1
        assert result.pairs[0].matched is True

    def test_lm01_pair_stores_comparisons(self, svc, invoice, po):
        """LM-01 variant: FieldComparison objects are populated on the pair."""
        make_inv_line(invoice, line_number=1, qty="10.00", price="50.00", amount="500.00")
        make_po_line(po, line_number=1, qty="10.0000", price="50.0000", amount="500.00")

        result = svc.match(invoice, po)
        pair = result.pairs[0]

        assert pair.qty_comparison is not None
        assert pair.price_comparison is not None
        assert pair.amount_comparison is not None
        assert pair.qty_comparison.within_tolerance is True
        assert pair.price_comparison.within_tolerance is True
        assert pair.amount_comparison.within_tolerance is True


# ─── LM-02: Multi-line all matched ───────────────────────────────────────────

@pytest.mark.django_db
class TestMultiLineAllMatched:
    def test_lm02_three_lines_all_matched(self, svc, invoice, po):
        """LM-02: 3 invoice lines, 3 PO lines, all match correctly."""
        for i in range(1, 4):
            make_inv_line(invoice, line_number=i,
                          description=f"Item {i}",
                          qty=f"{i * 10}.00",
                          price="100.00",
                          amount=f"{i * 1000}.00")
            make_po_line(po, line_number=i,
                         description=f"Item {i}",
                         qty=f"{i * 10}.0000",
                         price="100.0000",
                         amount=f"{i * 1000}.00")

        result = svc.match(invoice, po)

        assert result.all_lines_matched is True
        assert len(result.pairs) == 3
        assert all(p.matched for p in result.pairs)
        assert len(result.unmatched_invoice_lines) == 0
        assert len(result.unmatched_po_lines) == 0


# ─── LM-03: Line number bonus drives matching ─────────────────────────────────

@pytest.mark.django_db
class TestLineNumberBonus:
    def test_lm03_line_number_match_drives_pairing(self, svc, invoice, po):
        """LM-03: Same line_number gives +0.20 bonus — pair should be matched.

        Two invoice lines with similar descriptions but different line numbers.
        Each should pair with the PO line sharing its line_number.
        """
        # Both descriptions similar, but line numbers differ
        make_inv_line(invoice, line_number=1, description="Frozen Chicken",
                      qty="5.00", price="100.00", amount="500.00")
        make_inv_line(invoice, line_number=2, description="Frozen Beef",
                      qty="3.00", price="150.00", amount="450.00")

        make_po_line(po, line_number=1, description="Frozen Chicken",
                     qty="5.0000", price="100.0000", amount="500.00")
        make_po_line(po, line_number=2, description="Frozen Beef",
                     qty="3.0000", price="150.0000", amount="450.00")

        result = svc.match(invoice, po)

        assert result.all_lines_matched is True
        # Check that each pair correctly matched on line number
        for pair in result.pairs:
            assert pair.matched is True
            assert pair.invoice_line.line_number == pair.po_line.line_number


# ─── LM-04: Fuzzy description match ──────────────────────────────────────────

@pytest.mark.django_db
class TestFuzzyDescriptionMatch:
    def test_lm04_near_identical_description_matched(self, svc, invoice, po):
        """LM-04: 'Chicken Breast 1KG' vs 'Chicken Breast 1 KG' — fuzzy match >= 80."""
        make_inv_line(invoice, line_number=1, description="Chicken Breast 1KG",
                      qty="10.00", price="50.00", amount="500.00")
        make_po_line(po, line_number=1, description="Chicken Breast 1 KG",
                     qty="10.0000", price="50.0000", amount="500.00")

        result = svc.match(invoice, po)

        assert result.pairs[0].matched is True
        assert result.pairs[0].description_similarity >= 80

    def test_lm04_moderate_description_similarity(self, svc, invoice, po):
        """LM-04 variant: Some similarity (50-79) still contributes +0.15 to score."""
        # line_number match (+0.20) + desc partial (+0.15) + qty (+0.20) + price (+0.15) + amount (+0.15) = 0.85
        make_inv_line(invoice, line_number=1, description="Fresh Chicken Breast Boneless",
                      qty="10.00", price="50.00", amount="500.00")
        make_po_line(po, line_number=1, description="Chicken Breast",
                     qty="10.0000", price="50.0000", amount="500.00")

        result = svc.match(invoice, po)

        # Should still be matched due to other scoring dimensions
        assert result.pairs[0].matched is True


# ─── LM-05: Poor description — no match ──────────────────────────────────────

@pytest.mark.django_db
class TestPoorDescriptionNoMatch:
    def test_lm05_completely_different_items_no_match(self, svc, invoice, po):
        """LM-05: 'Tomato Sauce' vs 'Chicken Breast' with different qty/price."""
        make_inv_line(invoice, line_number=1, description="Tomato Sauce",
                      qty="5.00", price="20.00", amount="100.00")
        make_po_line(po, line_number=2, description="Chicken Breast",
                     qty="10.0000", price="50.0000", amount="500.00")

        result = svc.match(invoice, po)

        # With completely different descriptions, no qty/price match, no line_number match:
        # score = 0 (desc sim < 50) + 0.05 (qty miss) + 0.03 (price miss) + 0.03 (amount miss) = 0.11
        # 0.11 < 0.30 threshold -> not matched
        assert result.pairs[0].matched is False
        assert len(result.unmatched_invoice_lines) == 1
        assert len(result.unmatched_po_lines) == 1


# ─── LM-06: Unmatched invoice line ───────────────────────────────────────────

@pytest.mark.django_db
class TestUnmatchedInvoiceLine:
    def test_lm06_extra_invoice_line_is_unmatched(self, svc, invoice, po):
        """LM-06: Invoice has 2 lines but PO only has 1 — second inv line unmatched."""
        make_inv_line(invoice, line_number=1, description="Item A",
                      qty="10.00", price="100.00", amount="1000.00")
        make_inv_line(invoice, line_number=2, description="Item B Extra",
                      qty="5.00", price="200.00", amount="1000.00")

        make_po_line(po, line_number=1, description="Item A",
                     qty="10.0000", price="100.0000", amount="1000.00")

        result = svc.match(invoice, po)

        assert result.all_lines_matched is False
        assert len(result.unmatched_invoice_lines) == 1
        assert result.unmatched_invoice_lines[0].line_number == 2

    def test_lm06_unmatched_invoice_line_in_pairs(self, svc, invoice, po):
        """LM-06 variant: The unmatched invoice line appears in pairs with matched=False."""
        make_inv_line(invoice, line_number=1, description="Item A",
                      qty="10.00", price="100.00", amount="1000.00")
        make_inv_line(invoice, line_number=2, description="ZZZZ UNIQUE ITEM",
                      qty="1.00", price="999.00", amount="999.00")

        make_po_line(po, line_number=1, description="Item A",
                     qty="10.0000", price="100.0000", amount="1000.00")

        result = svc.match(invoice, po)

        unmatched_pairs = [p for p in result.pairs if not p.matched]
        assert len(unmatched_pairs) == 1


# ─── LM-07: Unmatched PO line ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestUnmatchedPOLine:
    def test_lm07_extra_po_line_is_unmatched(self, svc, invoice, po):
        """LM-07: PO has 2 lines but invoice only has 1 — second PO line unmatched."""
        make_inv_line(invoice, line_number=1, description="Item A",
                      qty="10.00", price="100.00", amount="1000.00")

        make_po_line(po, line_number=1, description="Item A",
                     qty="10.0000", price="100.0000", amount="1000.00")
        make_po_line(po, line_number=2, description="Item B Not Invoiced",
                     qty="5.0000", price="200.0000", amount="1000.00")

        result = svc.match(invoice, po)

        assert result.all_lines_matched is False
        assert len(result.unmatched_po_lines) == 1
        assert result.unmatched_po_lines[0].line_number == 2


# ─── LM-08: No invoice lines ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestNoInvoiceLines:
    def test_lm08_no_invoice_lines_returns_early(self, svc, invoice, po):
        """LM-08: Invoice with zero line items — all PO lines become unmatched."""
        make_po_line(po, line_number=1, description="Item A",
                     qty="10.0000", price="100.0000", amount="1000.00")
        make_po_line(po, line_number=2, description="Item B",
                     qty="5.0000", price="200.0000", amount="1000.00")

        # No invoice lines created
        result = svc.match(invoice, po)

        assert result.all_lines_matched is False
        assert result.all_within_tolerance is False
        assert len(result.unmatched_po_lines) == 2
        assert len(result.pairs) == 0


# ─── LM-09: No PO lines ───────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNoPOLines:
    def test_lm09_no_po_lines_returns_early(self, svc, invoice, po):
        """LM-09: PO has zero line items — all invoice lines become unmatched."""
        make_inv_line(invoice, line_number=1, description="Item A",
                      qty="10.00", price="100.00", amount="1000.00")
        make_inv_line(invoice, line_number=2, description="Item B",
                      qty="5.00", price="200.00", amount="1000.00")

        # No PO lines created
        result = svc.match(invoice, po)

        assert result.all_lines_matched is False
        assert result.all_within_tolerance is False
        assert len(result.unmatched_invoice_lines) == 2
        assert len(result.pairs) == 0


# ─── LM-10: Tolerance breach → all_within_tolerance=False ────────────────────

@pytest.mark.django_db
class TestToleranceBreach:
    def test_lm10_qty_tolerance_breach_sets_flag(self, svc, invoice, po):
        """LM-10: Qty differs 5% (outside 2% limit) — all_within_tolerance=False."""
        # invoice qty=10.5, PO qty=10 → 5% diff > 2% tolerance
        make_inv_line(invoice, line_number=1, description="Chicken Breast",
                      qty="10.50", price="100.00", amount="1050.00")
        make_po_line(po, line_number=1, description="Chicken Breast",
                     qty="10.0000", price="100.0000", amount="1000.00")

        result = svc.match(invoice, po)

        assert result.pairs[0].matched is True  # still matched
        assert result.all_within_tolerance is False
        assert result.pairs[0].qty_comparison.within_tolerance is False

    def test_lm10_price_tolerance_breach(self, svc, invoice, po):
        """LM-10 variant: Price differs 2% (outside 1% limit)."""
        make_inv_line(invoice, line_number=1, description="Frozen Beef",
                      qty="10.00", price="102.00", amount="1020.00")
        make_po_line(po, line_number=1, description="Frozen Beef",
                     qty="10.0000", price="100.0000", amount="1000.00")

        result = svc.match(invoice, po)

        assert result.all_within_tolerance is False
        assert result.pairs[0].price_comparison.within_tolerance is False

    def test_lm10_all_within_tolerance_true_when_all_pass(self, svc, invoice, po):
        """LM-10 variant: When all pairs are within tolerance, flag=True."""
        make_inv_line(invoice, line_number=1, description="Item X",
                      qty="10.00", price="100.00", amount="1000.00")
        make_po_line(po, line_number=1, description="Item X",
                     qty="10.0000", price="100.0000", amount="1000.00")

        result = svc.match(invoice, po)

        assert result.all_within_tolerance is True


# ─── LM-11: PO line deduplication (one PO line matched by one invoice line) ─

@pytest.mark.django_db
class TestPOLineDeduplication:
    def test_lm11_po_line_not_matched_twice(self, svc, invoice, po):
        """LM-11: Two invoice lines compete for one PO line — only one wins.

        The best-scoring invoice line claims the PO line.
        The other invoice line becomes unmatched.
        """
        # Two invoice lines with identical description/qty/price
        make_inv_line(invoice, line_number=1, description="Frozen Chicken",
                      qty="10.00", price="50.00", amount="500.00")
        make_inv_line(invoice, line_number=2, description="Frozen Chicken",
                      qty="10.00", price="50.00", amount="500.00")

        # Only one matching PO line
        make_po_line(po, line_number=1, description="Frozen Chicken",
                     qty="10.0000", price="50.0000", amount="500.00")

        result = svc.match(invoice, po)

        # Only one invoice line should be matched to the single PO line
        matched = [p for p in result.pairs if p.matched]
        unmatched = [p for p in result.pairs if not p.matched]

        assert len(matched) == 1
        assert len(unmatched) == 1
        # The unmatched pair has no po_line set (or it is not in the used set)
        assert result.all_lines_matched is False

    def test_lm11_po_line_pk_not_reused(self, svc, invoice, po):
        """LM-11 variant: used_po_lines set prevents same PO line from being claimed twice."""
        # Three invoice lines, two PO lines — should pair 1:1, leaving one invoice unmatched
        for i in range(1, 4):
            make_inv_line(invoice, line_number=i, description="Generic Item",
                          qty="5.00", price="10.00", amount="50.00")

        for i in range(1, 3):
            make_po_line(po, line_number=i, description="Generic Item",
                         qty="5.0000", price="10.0000", amount="50.00")

        result = svc.match(invoice, po)

        matched_po_pks = [p.po_line.pk for p in result.pairs if p.matched and p.po_line]
        # No PO line PK should appear twice
        assert len(matched_po_pks) == len(set(matched_po_pks))
        # One invoice line is unmatched
        assert len(result.unmatched_invoice_lines) == 1


# ─── Tax difference stored on pair ───────────────────────────────────────────

@pytest.mark.django_db
class TestTaxDifference:
    def test_tax_difference_calculated(self, svc, invoice, po):
        """Tax difference is stored on matched pair when both sides have tax."""
        make_inv_line(invoice, line_number=1, description="Item",
                      qty="10.00", price="100.00", amount="1000.00",
                      tax_amount="50.00")
        make_po_line(po, line_number=1, description="Item",
                     qty="10.0000", price="100.0000", amount="1000.00",
                     tax_amount="45.00")

        result = svc.match(invoice, po)
        pair = result.pairs[0]

        assert pair.matched is True
        assert pair.tax_invoice == Decimal("50.00")
        assert pair.tax_po == Decimal("45.00")
        assert pair.tax_difference == Decimal("5.00")

    def test_tax_difference_none_when_tax_missing(self, svc, invoice, po):
        """Tax difference is None when either side has no tax amount."""
        make_inv_line(invoice, line_number=1, description="Item",
                      qty="10.00", price="100.00", amount="1000.00",
                      tax_amount=None)
        make_po_line(po, line_number=1, description="Item",
                     qty="10.0000", price="100.0000", amount="1000.00",
                     tax_amount="45.00")

        result = svc.match(invoice, po)
        pair = result.pairs[0]

        assert pair.tax_difference is None


# ─── Minimum score threshold ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestMinimumScoreThreshold:
    def test_below_minimum_score_not_matched(self, svc, invoice, po):
        """A pair scoring below 0.30 is NOT marked as matched.

        Setup: no line_number match, desc < 50 similar, and all numeric
        comparisons miss tolerance. Score = 0.05 + 0.03 + 0.03 = 0.11 < 0.30.
        """
        make_inv_line(invoice, line_number=1, description="AAAAA",
                      qty="999.00", price="999.00", amount="999000.00")
        make_po_line(po, line_number=2, description="ZZZZZ",
                     qty="1.0000", price="1.0000", amount="1.00")

        result = svc.match(invoice, po)

        assert result.pairs[0].matched is False
        assert result.all_lines_matched is False

    def test_exactly_at_minimum_score_is_matched(self, svc, invoice, po):
        """A pair scoring exactly >= 0.30 IS matched.

        line_number match (+0.20) + qty within tol (+0.20) = 0.40 >= 0.30.
        Description similarity adds nothing (no description set).
        """
        make_inv_line(invoice, line_number=1, description="",
                      qty="10.00", price="999.00", amount="9990.00")
        make_po_line(po, line_number=1, description="",
                     qty="10.0000", price="1.0000", amount="10.00")

        result = svc.match(invoice, po)

        # line_number bonus (0.20) + qty match (0.20) = 0.40 -> matched
        assert result.pairs[0].matched is True
