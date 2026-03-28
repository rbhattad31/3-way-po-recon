"""
Tests for ExceptionBuilderService (Week 2 — EB-01 to EB-10 + extras)

ExceptionBuilderService.build() returns UNSAVED ReconciliationException instances.
It never calls .save() — the caller does bulk_create().

Key facts from source:
  - PO_NOT_FOUND: returns immediately after (no further exceptions)
  - DUPLICATE_INVOICE: added when invoice.is_duplicate=True
  - EXTRACTION_LOW_CONFIDENCE: added when confidence < threshold
  - Header exceptions: VENDOR_MISMATCH, CURRENCY_MISMATCH, AMOUNT_MISMATCH, TAX_MISMATCH
  - Line exceptions: QTY_MISMATCH, PRICE_MISMATCH, AMOUNT_MISMATCH (per line), TAX_MISMATCH, ITEM_MISMATCH
  - GRN exceptions (3-way only): GRN_NOT_FOUND, INVOICE_QTY_EXCEEDS_RECEIVED,
                                  OVER_RECEIPT, RECEIPT_SHORTAGE, DELAYED_RECEIPT
  - 2-way mode: GRN exceptions completely skipped
  - DELAYED_RECEIPT severity: HIGH if >45 days, MEDIUM if 31-45 days
  - THREE_WAY_ONLY_EXCEPTION_TYPES get applies_to_mode=THREE_WAY; others get BOTH
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from apps.core.enums import (
    ExceptionSeverity,
    ExceptionType,
    ReconciliationMode,
    ReconciliationModeApplicability,
)
from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchPair, LineMatchResult
from apps.reconciliation.services.grn_match_service import GRNLineComparison, GRNMatchResult
from apps.reconciliation.services.po_lookup_service import POLookupResult
from apps.reconciliation.services.tolerance_engine import FieldComparison


# ─── Helpers — lightweight mocks (no DB needed for most tests) ────────────────

def make_po_found(po=None):
    r = POLookupResult.__new__(POLookupResult)
    r.found = True
    r.purchase_order = po or MagicMock()
    return r


def make_po_not_found():
    r = POLookupResult.__new__(POLookupResult)
    r.found = False
    r.purchase_order = None
    return r


def make_header(vendor_match=True, currency_match=True, po_total_match=True,
                tax_match=None, total_comparison=None, tax_comparison=None):
    h = HeaderMatchResult()
    h.vendor_match = vendor_match
    h.currency_match = currency_match
    h.po_total_match = po_total_match
    h.tax_match = tax_match
    h.total_comparison = total_comparison
    h.tax_comparison = tax_comparison
    h.all_ok = vendor_match and currency_match and po_total_match and (tax_match is not False)
    return h


def make_field_comparison(inv_val, po_val, diff, diff_pct, within=False):
    fc = FieldComparison(
        invoice_value=Decimal(str(inv_val)),
        po_value=Decimal(str(po_val)),
        difference=Decimal(str(diff)),
        difference_pct=Decimal(str(diff_pct)),
        within_tolerance=within,
    )
    return fc


def make_grn_result(available=True, fully_received=True, line_comparisons=None,
                    has_receipt_issues=False, latest_receipt_date=None, grn_count=1):
    g = GRNMatchResult()
    g.grn_available = available
    g.fully_received = fully_received
    g.line_comparisons = line_comparisons or []
    g.has_receipt_issues = has_receipt_issues
    g.latest_receipt_date = latest_receipt_date
    g.grn_count = grn_count
    return g


def make_grn_line(po_line_id=1, qty_invoiced="10", qty_ordered="10", qty_received="10",
                  over_receipt=False, under_receipt=False, invoiced_exceeds_received=False):
    c = GRNLineComparison()
    c.invoice_line_id = po_line_id + 1000
    c.po_line_id = po_line_id
    c.qty_invoiced = Decimal(qty_invoiced)
    c.qty_ordered = Decimal(qty_ordered)
    c.qty_received = Decimal(qty_received)
    c.over_receipt = over_receipt
    c.under_receipt = under_receipt
    c.invoiced_exceeds_received = invoiced_exceeds_received
    return c


def make_line_pair(line_number=1, qty_ok=True, price_ok=True, amount_ok=True,
                   tax_inv=None, tax_po=None, matched=True):
    """Create a LineMatchPair with mocked invoice/PO lines."""
    inv_line = MagicMock()
    inv_line.pk = line_number * 100
    inv_line.line_number = line_number
    inv_line.tax_amount = Decimal(str(tax_inv)) if tax_inv else None
    inv_line.description = f"Line {line_number} Item"

    po_line = MagicMock()
    po_line.pk = line_number * 200
    po_line.line_number = line_number
    po_line.tax_amount = Decimal(str(tax_po)) if tax_po else None

    pair = LineMatchPair(invoice_line=inv_line, po_line=po_line, matched=matched)

    # Qty comparison
    pair.qty_comparison = FieldComparison(
        invoice_value=Decimal("10"), po_value=Decimal("10"),
        difference=Decimal("0"), difference_pct=Decimal("0"),
        within_tolerance=qty_ok,
    )
    # Price comparison
    pair.price_comparison = FieldComparison(
        invoice_value=Decimal("100"), po_value=Decimal("100"),
        difference=Decimal("0"), difference_pct=Decimal("0"),
        within_tolerance=price_ok,
    )
    # Amount comparison
    pair.amount_comparison = FieldComparison(
        invoice_value=Decimal("1000"), po_value=Decimal("1000"),
        difference=Decimal("0"), difference_pct=Decimal("0"),
        within_tolerance=amount_ok,
    )
    # Tax difference
    if tax_inv is not None and tax_po is not None:
        pair.tax_invoice = Decimal(str(tax_inv))
        pair.tax_po = Decimal(str(tax_po))
        pair.tax_difference = Decimal(str(tax_inv)) - Decimal(str(tax_po))
    else:
        pair.tax_invoice = None
        pair.tax_po = None
        pair.tax_difference = None

    return pair


def make_line_result(pairs=None, unmatched_inv=None, unmatched_po=None):
    lr = LineMatchResult()
    lr.pairs = pairs or []
    lr.unmatched_invoice_lines = unmatched_inv or []
    lr.unmatched_po_lines = unmatched_po or []
    lr.all_lines_matched = len(lr.unmatched_invoice_lines) == 0 and len(lr.unmatched_po_lines) == 0
    lr.all_within_tolerance = lr.all_lines_matched and all(
        p.qty_comparison.within_tolerance and
        p.price_comparison.within_tolerance and
        p.amount_comparison.within_tolerance
        for p in lr.pairs if p.matched
    )
    return lr


# ─── DB fixture for ReconciliationResult ─────────────────────────────────────

@pytest.fixture
def svc():
    return ExceptionBuilderService()


@pytest.fixture
def recon_result(db):
    """A minimal ReconciliationResult in the DB with all required FK relations."""
    from apps.reconciliation.tests.factories import (
        ReconConfigFactory, InvoiceFactory, POFactory,
    )
    from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
    from apps.core.enums import MatchStatus, ReconciliationRunStatus

    config = ReconConfigFactory()
    invoice = InvoiceFactory()
    po = POFactory()

    run = ReconciliationRun.objects.create(
        status=ReconciliationRunStatus.RUNNING,
        config=config,
    )
    result = ReconciliationResult.objects.create(
        run=run,
        invoice=invoice,
        purchase_order=po,
        match_status=MatchStatus.REQUIRES_REVIEW,
    )
    return result


@pytest.fixture
def recon_result_no_po(db):
    """A ReconciliationResult with no PO attached (for PO_NOT_FOUND tests)."""
    from apps.reconciliation.tests.factories import ReconConfigFactory, InvoiceFactory
    from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
    from apps.core.enums import MatchStatus, ReconciliationRunStatus

    config = ReconConfigFactory()
    invoice = InvoiceFactory(po_number="PO-MISSING")

    run = ReconciliationRun.objects.create(
        status=ReconciliationRunStatus.RUNNING,
        config=config,
    )
    result = ReconciliationResult.objects.create(
        run=run,
        invoice=invoice,
        purchase_order=None,
        match_status=MatchStatus.UNMATCHED,
    )
    return result


# ─── EB-01: PO not found ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPONotFound:
    def test_eb01_po_not_found_creates_exception(self, svc, recon_result_no_po):
        """EB-01: PO not found -> exactly one PO_NOT_FOUND exception, nothing else."""
        excs = svc.build(
            result=recon_result_no_po,
            po_result=make_po_not_found(),
            header_result=None,
            line_result=None,
            grn_result=None,
        )
        assert len(excs) == 1
        assert excs[0].exception_type == ExceptionType.PO_NOT_FOUND
        assert excs[0].severity == ExceptionSeverity.HIGH

    def test_eb01_po_not_found_returns_immediately(self, svc, recon_result_no_po):
        """EB-01: When PO not found, no further exceptions are built."""
        # Even with a header showing issues, nothing else should appear
        excs = svc.build(
            result=recon_result_no_po,
            po_result=make_po_not_found(),
            header_result=make_header(vendor_match=False),
            line_result=None,
            grn_result=None,
        )
        assert len(excs) == 1
        assert excs[0].exception_type == ExceptionType.PO_NOT_FOUND

    def test_eb01_po_number_in_message(self, svc, recon_result_no_po):
        """EB-01: PO_NOT_FOUND message references the invoice's po_number."""
        excs = svc.build(
            result=recon_result_no_po,
            po_result=make_po_not_found(),
            header_result=None,
            line_result=None,
            grn_result=None,
        )
        assert "PO-MISSING" in excs[0].message


# ─── EB-02: Vendor mismatch ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestVendorMismatch:
    def test_eb02_vendor_mismatch_exception(self, svc, recon_result):
        """EB-02: vendor_match=False -> VENDOR_MISMATCH with HIGH severity."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(vendor_match=False),
            line_result=None,
            grn_result=None,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.VENDOR_MISMATCH in types
        vendor_exc = next(e for e in excs if e.exception_type == ExceptionType.VENDOR_MISMATCH)
        assert vendor_exc.severity == ExceptionSeverity.HIGH

    def test_eb02_no_vendor_exception_when_match(self, svc, recon_result):
        """EB-02 variant: vendor_match=True -> no VENDOR_MISMATCH."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(vendor_match=True),
            line_result=None,
            grn_result=None,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.VENDOR_MISMATCH not in types


# ─── EB-03: Currency mismatch ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestCurrencyMismatch:
    def test_eb03_currency_mismatch_exception(self, svc, recon_result):
        """EB-03: currency_match=False -> CURRENCY_MISMATCH with MEDIUM severity."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(currency_match=False),
            line_result=None,
            grn_result=None,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.CURRENCY_MISMATCH in types
        curr_exc = next(e for e in excs if e.exception_type == ExceptionType.CURRENCY_MISMATCH)
        assert curr_exc.severity == ExceptionSeverity.MEDIUM


# ─── EB-04: Amount mismatch (header level) ────────────────────────────────────

@pytest.mark.django_db
class TestHeaderAmountMismatch:
    def test_eb04_amount_mismatch_exception(self, svc, recon_result):
        """EB-04: po_total_match=False with total_comparison -> AMOUNT_MISMATCH HIGH."""
        tc = make_field_comparison(1000, 1020, -20, -1.96, within=False)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(po_total_match=False, total_comparison=tc),
            line_result=None,
            grn_result=None,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.AMOUNT_MISMATCH in types
        amt_exc = next(e for e in excs if e.exception_type == ExceptionType.AMOUNT_MISMATCH)
        assert amt_exc.severity == ExceptionSeverity.HIGH

    def test_eb04_amount_mismatch_details_populated(self, svc, recon_result):
        """EB-04: Amount mismatch details dict contains invoice/PO/difference values."""
        tc = make_field_comparison(1000, 1020, -20, -1.96, within=False)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(po_total_match=False, total_comparison=tc),
            line_result=None,
            grn_result=None,
        )
        amt_exc = next(e for e in excs if e.exception_type == ExceptionType.AMOUNT_MISMATCH)
        assert amt_exc.details is not None
        assert "invoice_total" in amt_exc.details
        assert "po_total" in amt_exc.details
        assert "difference" in amt_exc.details


# ─── EB-05: GRN not found exception ──────────────────────────────────────────

@pytest.mark.django_db
class TestGRNNotFound:
    def test_eb05_grn_not_found_exception(self, svc, recon_result):
        """EB-05: 3-way, grn_available=False -> GRN_NOT_FOUND exception."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=make_grn_result(available=False),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.GRN_NOT_FOUND in types

    def test_eb05_grn_not_found_returns_early_from_grn_exceptions(self, svc, recon_result):
        """EB-05: When GRN not found, no other GRN exception types are added."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=make_grn_result(available=False),
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        grn_types = [e.exception_type for e in excs if e.exception_type in {
            ExceptionType.GRN_NOT_FOUND, ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED,
            ExceptionType.OVER_RECEIPT, ExceptionType.RECEIPT_SHORTAGE,
        }]
        assert grn_types == [ExceptionType.GRN_NOT_FOUND]


# ─── EB-06: Over receipt ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestOverReceipt:
    def test_eb06_over_receipt_exception(self, svc, recon_result):
        """EB-06: GRN line has over_receipt=True -> OVER_RECEIPT exception."""
        grn = make_grn_result(
            available=True,
            line_comparisons=[make_grn_line(
                po_line_id=1,
                qty_invoiced="10", qty_ordered="10", qty_received="11",
                over_receipt=True,
            )],
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.OVER_RECEIPT in types
        over_exc = next(e for e in excs if e.exception_type == ExceptionType.OVER_RECEIPT)
        assert over_exc.severity == ExceptionSeverity.MEDIUM


# ─── EB-07: Invoice qty exceeds received ─────────────────────────────────────

@pytest.mark.django_db
class TestInvoiceExceedsReceived:
    def test_eb07_invoice_qty_exceeds_received(self, svc, recon_result):
        """EB-07: invoiced_exceeds_received=True -> INVOICE_QTY_EXCEEDS_RECEIVED."""
        grn = make_grn_result(
            available=True,
            line_comparisons=[make_grn_line(
                po_line_id=1,
                qty_invoiced="10", qty_ordered="10", qty_received="8",
                under_receipt=True,
                invoiced_exceeds_received=True,
            )],
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED in types
        exc = next(e for e in excs if e.exception_type == ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED)
        assert exc.severity == ExceptionSeverity.HIGH


# ─── EB-08: Delayed receipt ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestDelayedReceipt:
    def test_eb08_delayed_receipt_medium_severity(self, svc, recon_result):
        """EB-08: Receipt 35 days after PO date (31-45 days) -> DELAYED_RECEIPT MEDIUM."""
        po_date = date(2025, 1, 1)
        receipt_date = date(2025, 2, 5)  # 35 days later
        assert (receipt_date - po_date).days == 35

        # Set the PO date on the result's purchase_order
        recon_result.purchase_order.po_date = po_date
        recon_result.purchase_order.save()

        grn = make_grn_result(available=True, latest_receipt_date=receipt_date)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.DELAYED_RECEIPT in types
        delayed = next(e for e in excs if e.exception_type == ExceptionType.DELAYED_RECEIPT)
        assert delayed.severity == ExceptionSeverity.MEDIUM

    def test_eb08_delayed_receipt_high_severity(self, svc, recon_result):
        """EB-08 variant: Receipt >45 days after PO date -> DELAYED_RECEIPT HIGH."""
        po_date = date(2025, 1, 1)
        receipt_date = date(2025, 2, 20)  # 50 days later
        assert (receipt_date - po_date).days == 50

        recon_result.purchase_order.po_date = po_date
        recon_result.purchase_order.save()

        grn = make_grn_result(available=True, latest_receipt_date=receipt_date)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        delayed = next(e for e in excs if e.exception_type == ExceptionType.DELAYED_RECEIPT)
        assert delayed.severity == ExceptionSeverity.HIGH

    def test_eb08_no_delayed_receipt_within_30_days(self, svc, recon_result):
        """EB-08 variant: Receipt within 30 days -> no DELAYED_RECEIPT."""
        po_date = date(2025, 1, 1)
        receipt_date = date(2025, 1, 25)  # 24 days later

        recon_result.purchase_order.po_date = po_date
        recon_result.purchase_order.save()

        grn = make_grn_result(available=True, latest_receipt_date=receipt_date)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.DELAYED_RECEIPT not in types


# ─── EB-09: No exceptions on clean match ─────────────────────────────────────

@pytest.mark.django_db
class TestCleanMatch:
    def test_eb09_no_exceptions_on_perfect_match(self, svc, recon_result):
        """EB-09: All values match, GRN clean, 3-way -> zero exceptions."""
        grn = make_grn_result(
            available=True,
            fully_received=True,
            line_comparisons=[],
            has_receipt_issues=False,
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(vendor_match=True, currency_match=True,
                                      po_total_match=True, tax_match=None),
            line_result=make_line_result(
                pairs=[make_line_pair(line_number=1, qty_ok=True, price_ok=True, amount_ok=True)]
            ),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        assert excs == []

    def test_eb09_no_exceptions_2way_perfect(self, svc, recon_result):
        """EB-09 variant: Clean 2-way match -> zero exceptions."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(
                pairs=[make_line_pair(line_number=1)]
            ),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert excs == []


# ─── EB-10: GRN exceptions not built for 2-way ────────────────────────────────

@pytest.mark.django_db
class TestGRNExceptionsSkippedIn2Way:
    def test_eb10_grn_exceptions_not_built_in_2way(self, svc, recon_result):
        """EB-10: 2-way mode -> GRN exceptions completely suppressed."""
        grn = make_grn_result(
            available=False,  # Would generate GRN_NOT_FOUND in 3-way
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        grn_exception_types = {
            ExceptionType.GRN_NOT_FOUND,
            ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED,
            ExceptionType.OVER_RECEIPT,
            ExceptionType.RECEIPT_SHORTAGE,
            ExceptionType.DELAYED_RECEIPT,
        }
        exc_types = {e.exception_type for e in excs}
        assert exc_types.isdisjoint(grn_exception_types)

    def test_eb10_invoice_exceeds_received_not_in_2way(self, svc, recon_result):
        """EB-10 variant: Even severe GRN issues don't appear in 2-way results."""
        grn = make_grn_result(
            available=True,
            line_comparisons=[make_grn_line(
                invoiced_exceeds_received=True,
                over_receipt=True,
            )],
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED not in types
        assert ExceptionType.OVER_RECEIPT not in types


# ─── Line-level exceptions ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLineExceptions:
    def test_qty_mismatch_on_line(self, svc, recon_result):
        """Line qty outside tolerance -> QTY_MISMATCH per line."""
        pair = make_line_pair(line_number=1, qty_ok=False, price_ok=True, amount_ok=True)
        # Build a result_line_map
        result_line_map = {pair.invoice_line.pk: None}

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(pairs=[pair]),
            grn_result=None,
            result_line_map=result_line_map,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.QTY_MISMATCH in types

    def test_price_mismatch_on_line(self, svc, recon_result):
        """Line price outside tolerance -> PRICE_MISMATCH."""
        pair = make_line_pair(line_number=1, qty_ok=True, price_ok=False, amount_ok=True)
        result_line_map = {pair.invoice_line.pk: None}

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(pairs=[pair]),
            grn_result=None,
            result_line_map=result_line_map,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.PRICE_MISMATCH in types

    def test_amount_mismatch_on_line(self, svc, recon_result):
        """Line amount outside tolerance -> AMOUNT_MISMATCH on line."""
        pair = make_line_pair(line_number=1, qty_ok=True, price_ok=True, amount_ok=False)
        result_line_map = {pair.invoice_line.pk: None}

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(pairs=[pair]),
            grn_result=None,
            result_line_map=result_line_map,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.AMOUNT_MISMATCH in types

    def test_tax_mismatch_on_line(self, svc, recon_result):
        """Non-zero tax difference on a matched line -> TAX_MISMATCH."""
        pair = make_line_pair(line_number=1, tax_inv="50.00", tax_po="45.00")
        result_line_map = {pair.invoice_line.pk: None}

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(pairs=[pair]),
            grn_result=None,
            result_line_map=result_line_map,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.TAX_MISMATCH in types

    def test_unmatched_invoice_line_creates_item_mismatch(self, svc, recon_result):
        """An unmatched invoice line -> ITEM_MISMATCH exception.

        NOTE: result_line_map must be truthy (non-empty dict) because the source
        code guards: `if line_result and result_line_map:` — an empty dict is
        falsy and would skip _line_exceptions entirely, including the unmatched
        invoice line loop. We pass a sentinel key to make the dict truthy.
        """
        inv_line = MagicMock()
        inv_line.line_number = 99
        inv_line.description = "Mystery Item"
        inv_line.raw_description = "Mystery Item"

        # Non-empty dict so the guard passes; unmatched lines don't need a key in it
        result_line_map = {"_sentinel": None}

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(unmatched_inv=[inv_line]),
            grn_result=None,
            result_line_map=result_line_map,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.ITEM_MISMATCH in types

    def test_unmatched_pair_does_not_create_line_exceptions(self, svc, recon_result):
        """A pair with matched=False produces no QTY/PRICE/AMOUNT exceptions."""
        # Even if comparisons are out of tolerance, unmatched pairs are skipped
        pair = make_line_pair(line_number=1, qty_ok=False, price_ok=False, amount_ok=False,
                              matched=False)
        result_line_map = {pair.invoice_line.pk: None}

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(pairs=[pair]),
            grn_result=None,
            result_line_map=result_line_map,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        line_exc_types = {ExceptionType.QTY_MISMATCH, ExceptionType.PRICE_MISMATCH,
                          ExceptionType.AMOUNT_MISMATCH}
        actual = {e.exception_type for e in excs}
        assert actual.isdisjoint(line_exc_types)


# ─── Duplicate invoice exception ─────────────────────────────────────────────

@pytest.mark.django_db
class TestDuplicateInvoice:
    def test_duplicate_invoice_creates_exception(self, svc, recon_result):
        """invoice.is_duplicate=True -> DUPLICATE_INVOICE exception with HIGH severity."""
        recon_result.invoice.is_duplicate = True
        recon_result.invoice.save()

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.DUPLICATE_INVOICE in types
        dup_exc = next(e for e in excs if e.exception_type == ExceptionType.DUPLICATE_INVOICE)
        assert dup_exc.severity == ExceptionSeverity.HIGH

    def test_no_duplicate_exception_when_not_duplicate(self, svc, recon_result):
        """invoice.is_duplicate=False -> no DUPLICATE_INVOICE exception."""
        recon_result.invoice.is_duplicate = False
        recon_result.invoice.save()

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.DUPLICATE_INVOICE not in types


# ─── Low confidence exception ─────────────────────────────────────────────────

@pytest.mark.django_db
class TestLowConfidence:
    def test_low_confidence_creates_exception(self, svc, recon_result):
        """confidence=0.60 < threshold=0.75 -> EXTRACTION_LOW_CONFIDENCE MEDIUM."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=None,
            extraction_confidence=0.60,
            confidence_threshold=0.75,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.EXTRACTION_LOW_CONFIDENCE in types
        low_exc = next(e for e in excs if e.exception_type == ExceptionType.EXTRACTION_LOW_CONFIDENCE)
        assert low_exc.severity == ExceptionSeverity.MEDIUM

    def test_confidence_at_threshold_no_exception(self, svc, recon_result):
        """confidence=0.75 at threshold=0.75 -> NOT flagged (strict <)."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=None,
            extraction_confidence=0.75,
            confidence_threshold=0.75,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.EXTRACTION_LOW_CONFIDENCE not in types


# ─── Mode applicability tagging ───────────────────────────────────────────────

@pytest.mark.django_db
class TestModeApplicabilityTagging:
    def test_three_way_only_exceptions_tagged_correctly(self, svc, recon_result):
        """GRN-type exceptions get applies_to_mode=THREE_WAY."""
        grn = make_grn_result(available=False)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        grn_exc = next((e for e in excs if e.exception_type == ExceptionType.GRN_NOT_FOUND), None)
        assert grn_exc is not None
        assert grn_exc.applies_to_mode == ReconciliationModeApplicability.THREE_WAY

    def test_common_exceptions_tagged_both(self, svc, recon_result):
        """VENDOR_MISMATCH and other common exceptions get applies_to_mode=BOTH."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(vendor_match=False),
            line_result=make_line_result(),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        vendor_exc = next((e for e in excs if e.exception_type == ExceptionType.VENDOR_MISMATCH), None)
        assert vendor_exc is not None
        assert vendor_exc.applies_to_mode == ReconciliationModeApplicability.BOTH


# ─── Receipt shortage ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReceiptShortage:
    def test_receipt_shortage_when_under_and_not_exceeds(self, svc, recon_result):
        """under_receipt=True + invoiced_exceeds_received=False -> RECEIPT_SHORTAGE."""
        grn = make_grn_result(
            available=True,
            line_comparisons=[make_grn_line(
                qty_invoiced="8", qty_ordered="10", qty_received="8",
                under_receipt=True,
                invoiced_exceeds_received=False,
            )],
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.RECEIPT_SHORTAGE in types

    def test_no_receipt_shortage_when_invoiced_exceeds_received(self, svc, recon_result):
        """under_receipt=True + invoiced_exceeds_received=True -> NO RECEIPT_SHORTAGE.

        When invoiced_exceeds_received, INVOICE_QTY_EXCEEDS_RECEIVED is raised instead.
        RECEIPT_SHORTAGE requires: under_receipt AND NOT invoiced_exceeds_received.
        """
        grn = make_grn_result(
            available=True,
            line_comparisons=[make_grn_line(
                qty_invoiced="10", qty_ordered="10", qty_received="8",
                under_receipt=True,
                invoiced_exceeds_received=True,
            )],
        )
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(),
            line_result=make_line_result(),
            grn_result=grn,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.RECEIPT_SHORTAGE not in types
        assert ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED in types


# ─── Tax mismatch at header level ─────────────────────────────────────────────

@pytest.mark.django_db
class TestHeaderTaxMismatch:
    def test_tax_mismatch_exception(self, svc, recon_result):
        """header.tax_match=False with tax_comparison -> TAX_MISMATCH MEDIUM."""
        txc = make_field_comparison(100, 115, -15, -13.04, within=False)
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(tax_match=False, tax_comparison=txc),
            line_result=make_line_result(),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.TAX_MISMATCH in types
        tax_exc = next(e for e in excs if e.exception_type == ExceptionType.TAX_MISMATCH
                       and e.result_line is None)  # header-level, no result_line
        assert tax_exc.severity == ExceptionSeverity.MEDIUM

    def test_tax_match_none_no_exception(self, svc, recon_result):
        """header.tax_match=None (missing tax data) -> no TAX_MISMATCH at header."""
        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(tax_match=None),
            line_result=make_line_result(),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        types = [e.exception_type for e in excs]
        assert ExceptionType.TAX_MISMATCH not in types


# ─── Return type is unsaved instances ─────────────────────────────────────────

@pytest.mark.django_db
class TestReturnedInstancesNotSaved:
    def test_exceptions_are_unsaved_instances(self, svc, recon_result):
        """build() returns unsaved ReconciliationException instances (pk=None)."""
        from apps.reconciliation.models import ReconciliationException

        excs = svc.build(
            result=recon_result,
            po_result=make_po_found(recon_result.purchase_order),
            header_result=make_header(vendor_match=False),
            line_result=make_line_result(),
            grn_result=None,
            reconciliation_mode=ReconciliationMode.TWO_WAY,
        )
        assert len(excs) > 0
        assert all(isinstance(e, ReconciliationException) for e in excs)
        # pk=None means not saved to DB
        assert all(e.pk is None for e in excs)
