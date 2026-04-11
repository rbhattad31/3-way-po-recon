"""
Tests for ReceiptAvailabilityService and receipt-availability-aware GRN matching.

RA-01 through RA-10: ReceiptAvailabilityService unit tests (pure mock, no DB)
RA-11 through RA-15: ReceiptAvailabilityService DB tests (require Django DB)
GA-01 through GA-10: GRN match with receipt_availability parameter
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from apps.reconciliation.services.receipt_availability_service import (
    LineReceiptAvailability,
    ReceiptAvailability,
    ReceiptAvailabilityService,
)
from apps.reconciliation.services.grn_match_service import (
    GRNMatchService,
    GRNMatchResult,
    GRNLineComparison,
)
from apps.reconciliation.services.grn_lookup_service import GRNSummary
from apps.reconciliation.services.line_match_service import LineMatchPair

ZERO = Decimal("0")


# ===================================================================
# Helper builders (mock, no DB)
# ===================================================================

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
    inv_line.pk = po_line_id + 1000
    inv_line.quantity = qty_invoiced

    po_line = MagicMock()
    po_line.pk = po_line_id
    po_line.quantity = qty_ordered

    pair = LineMatchPair(invoice_line=inv_line, po_line=po_line, matched=matched)
    return pair


def make_receipt_availability(entries: dict) -> ReceiptAvailability:
    """Build a ReceiptAvailability from {po_line_id: (received, consumed, [grn_ids])}."""
    avail = ReceiptAvailability()
    for po_line_id, vals in entries.items():
        received, consumed = vals[0], vals[1]
        grn_ids = vals[2] if len(vals) > 2 else []
        avail.by_po_line[po_line_id] = LineReceiptAvailability(
            po_line_id=po_line_id,
            cumulative_received_qty=received,
            previously_consumed_qty=consumed,
            contributing_grn_line_ids=grn_ids,
        )
    return avail


# ===================================================================
# RA: ReceiptAvailabilityService unit tests
# ===================================================================

class TestLineReceiptAvailability:
    """Unit tests for the dataclass properties."""

    def test_ra01_available_qty_basic(self):
        """RA-01: available = received - consumed."""
        la = LineReceiptAvailability(
            po_line_id=1,
            cumulative_received_qty=Decimal("100"),
            previously_consumed_qty=Decimal("60"),
        )
        assert la.available_qty == Decimal("40")

    def test_ra02_available_qty_no_consumption(self):
        """RA-02: First invoice -- consumed=0 so available = received."""
        la = LineReceiptAvailability(
            po_line_id=1,
            cumulative_received_qty=Decimal("100"),
            previously_consumed_qty=ZERO,
        )
        assert la.available_qty == Decimal("100")

    def test_ra03_available_qty_fully_consumed(self):
        """RA-03: All receipt consumed -- available = 0."""
        la = LineReceiptAvailability(
            po_line_id=1,
            cumulative_received_qty=Decimal("100"),
            previously_consumed_qty=Decimal("100"),
        )
        assert la.available_qty == ZERO

    def test_ra04_available_qty_over_consumed_floors_to_zero(self):
        """RA-04: Consumed > received (edge case) -- available floors to 0."""
        la = LineReceiptAvailability(
            po_line_id=1,
            cumulative_received_qty=Decimal("80"),
            previously_consumed_qty=Decimal("100"),
        )
        assert la.available_qty == ZERO

    def test_ra05_receipt_availability_get_existing(self):
        """RA-05: ReceiptAvailability.get() returns entry for known PO line."""
        avail = make_receipt_availability({10: (Decimal("50"), Decimal("20"))})
        la = avail.get(10)
        assert la is not None
        assert la.available_qty == Decimal("30")

    def test_ra06_receipt_availability_get_missing(self):
        """RA-06: ReceiptAvailability.get() returns None for unknown PO line."""
        avail = make_receipt_availability({10: (Decimal("50"), Decimal("20"))})
        assert avail.get(999) is None

    def test_ra07_empty_receipt_availability(self):
        """RA-07: Empty ReceiptAvailability has no entries."""
        avail = ReceiptAvailability()
        assert len(avail.by_po_line) == 0
        assert avail.get(1) is None

    def test_ra08_contributing_grn_line_ids_tracked(self):
        """RA-08: GRN line IDs are stored for provenance."""
        la = LineReceiptAvailability(
            po_line_id=1,
            cumulative_received_qty=Decimal("100"),
            previously_consumed_qty=ZERO,
            contributing_grn_line_ids=[101, 102, 103],
        )
        assert la.contributing_grn_line_ids == [101, 102, 103]


class TestReceiptAvailabilityServiceCompute:
    """ReceiptAvailabilityService.compute() with mocked DB."""

    @patch("apps.reconciliation.models.ReconciliationResultLine.objects")
    def test_ra09_first_invoice_no_prior(self, mock_objects):
        """RA-09: First invoice on PO -- no prior consumption."""
        mock_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs
        mock_qs.values.return_value = mock_qs
        mock_qs.annotate.return_value = []  # no prior consumption

        result = ReceiptAvailabilityService.compute(
            po_id=1,
            total_received_by_po_line={10: Decimal("100"), 20: Decimal("50")},
            exclude_result_id=None,
        )

        assert len(result.by_po_line) == 2
        assert result.get(10).available_qty == Decimal("100")
        assert result.get(20).available_qty == Decimal("50")
        assert result.get(10).previously_consumed_qty == ZERO
        assert result.get(20).previously_consumed_qty == ZERO

    @patch("apps.reconciliation.models.ReconciliationResultLine.objects")
    def test_ra10_with_prior_consumption(self, mock_objects):
        """RA-10: Second invoice -- prior consumed 60 of 100 received."""
        mock_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs
        mock_qs.values.return_value = mock_qs
        mock_qs.annotate.return_value = [
            {"po_line_id": 10, "consumed": Decimal("60")},
        ]

        result = ReceiptAvailabilityService.compute(
            po_id=1,
            total_received_by_po_line={10: Decimal("100")},
            exclude_result_id=99,
        )

        la = result.get(10)
        assert la is not None
        assert la.cumulative_received_qty == Decimal("100")
        assert la.previously_consumed_qty == Decimal("60")
        assert la.available_qty == Decimal("40")

    @patch("apps.reconciliation.models.ReconciliationResultLine.objects")
    def test_ra10b_empty_received_map(self, mock_objects):
        """RA-10b: No received quantities -- returns empty ReceiptAvailability."""
        result = ReceiptAvailabilityService.compute(
            po_id=1,
            total_received_by_po_line={},
        )
        assert len(result.by_po_line) == 0

    @patch("apps.reconciliation.models.ReconciliationResultLine.objects")
    def test_ra10c_grn_line_ids_forwarded(self, mock_objects):
        """RA-10c: GRN line IDs forwarded to LineReceiptAvailability."""
        mock_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.exclude.return_value = mock_qs
        mock_qs.values.return_value = mock_qs
        mock_qs.annotate.return_value = []

        result = ReceiptAvailabilityService.compute(
            po_id=1,
            total_received_by_po_line={10: Decimal("100")},
            grn_line_ids_by_po_line={10: [501, 502]},
        )

        la = result.get(10)
        assert la.contributing_grn_line_ids == [501, 502]


# ===================================================================
# GA: GRNMatchService with receipt_availability
# ===================================================================

class TestGRNMatchWithReceiptAvailability:
    """Tests for GRNMatchService.match() with receipt_availability parameter."""

    def setup_method(self):
        self.svc = GRNMatchService()

    def test_ga01_first_invoice_all_available(self):
        """GA-01: First invoice, no prior consumption -- invoiced <= available, no issue."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("50"))]
        avail = make_receipt_availability({po_line_id: (Decimal("100"), ZERO)})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is False
        assert cmp.available_qty == Decimal("100")
        assert cmp.previously_consumed_qty == ZERO
        assert cmp.cumulative_received_qty == Decimal("100")

    def test_ga02_second_invoice_within_available(self):
        """GA-02: Second invoice. Prior consumed 40, received 100, invoiced 50 -- 50 <= 60 OK."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("50"))]
        avail = make_receipt_availability({po_line_id: (Decimal("100"), Decimal("40"))})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is False
        assert cmp.available_qty == Decimal("60")
        assert result.has_receipt_issues is False

    def test_ga03_second_invoice_exceeds_available(self):
        """GA-03: Second invoice. Prior consumed 80, received 100, invoiced 50 -- 50 > 20 OVERBILL."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("50"))]
        avail = make_receipt_availability({po_line_id: (Decimal("100"), Decimal("80"))})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is True
        assert cmp.available_qty == Decimal("20")
        assert result.has_receipt_issues is True

    def test_ga04_exactly_at_available(self):
        """GA-04: Invoice exactly equals available -- no overbilling."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("60"))]
        avail = make_receipt_availability({po_line_id: (Decimal("100"), Decimal("40"))})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is False
        assert cmp.available_qty == Decimal("60")

    def test_ga05_zero_available_any_invoice_overbills(self):
        """GA-05: Available = 0, any invoice qty > 0 is overbilling."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("1"))]
        avail = make_receipt_availability({po_line_id: (Decimal("100"), Decimal("100"))})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is True
        assert cmp.available_qty == ZERO

    def test_ga06_no_receipt_availability_backward_compat(self):
        """GA-06: receipt_availability=None -- backward compatible, no availability fields set."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("50"))]

        result = self.svc.match(pairs, summary, receipt_availability=None)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is False
        assert cmp.available_qty is None
        assert cmp.cumulative_received_qty is None
        assert cmp.previously_consumed_qty is None

    def test_ga07_multi_line_mixed_availability(self):
        """GA-07: Two PO lines -- one within available, one exceeds."""
        po_line_a = 10
        po_line_b = 20
        summary = make_grn_summary(
            available=True,
            total_received={
                po_line_a: Decimal("100"),
                po_line_b: Decimal("50"),
            },
        )
        pairs = [
            make_line_pair(po_line_a, qty_ordered=Decimal("100"), qty_invoiced=Decimal("30")),
            make_line_pair(po_line_b, qty_ordered=Decimal("50"), qty_invoiced=Decimal("40")),
        ]
        avail = make_receipt_availability({
            po_line_a: (Decimal("100"), Decimal("60")),   # available=40, invoiced=30 OK
            po_line_b: (Decimal("50"), Decimal("20")),    # available=30, invoiced=40 OVERBILL
        })

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        assert len(result.line_comparisons) == 2
        cmp_a = result.line_comparisons[0]
        cmp_b = result.line_comparisons[1]

        assert cmp_a.invoiced_exceeds_available is False
        assert cmp_b.invoiced_exceeds_available is True
        assert result.has_receipt_issues is True

    def test_ga08_grn_line_ids_propagated(self):
        """GA-08: contributing_grn_line_ids propagated from receipt availability."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("100")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("50"))]
        avail = make_receipt_availability({po_line_id: (Decimal("100"), ZERO, [501, 502])})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.contributing_grn_line_ids == [501, 502]

    def test_ga09_po_line_not_in_availability(self):
        """GA-09: PO line has no entry in receipt_availability -- availability fields stay None."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("50")},
        )
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("50"), qty_invoiced=Decimal("50"))]
        # receipt_availability has data for a different PO line
        avail = make_receipt_availability({99: (Decimal("100"), ZERO)})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_available is False
        assert cmp.available_qty is None

    def test_ga10_exceeds_received_and_exceeds_available_both_set(self):
        """GA-10: Invoice exceeds both total received AND available -- both flags set."""
        po_line_id = 10
        summary = make_grn_summary(
            available=True,
            total_received={po_line_id: Decimal("50")},
        )
        # invoiced=60 > received=50 AND available=30
        pairs = [make_line_pair(po_line_id, qty_ordered=Decimal("100"), qty_invoiced=Decimal("60"))]
        avail = make_receipt_availability({po_line_id: (Decimal("50"), Decimal("20"))})

        result = self.svc.match(pairs, summary, receipt_availability=avail)

        cmp = result.line_comparisons[0]
        assert cmp.invoiced_exceeds_received is True
        assert cmp.invoiced_exceeds_available is True
        assert cmp.available_qty == Decimal("30")


# ===================================================================
# DB-backed tests for ReceiptAvailabilityService.compute()
# ===================================================================

@pytest.mark.django_db
class TestReceiptAvailabilityServiceDB:
    """Integration tests requiring DB access.

    These tests create real reconciliation results and result lines to verify
    the prior-consumption query works correctly.
    """

    @pytest.fixture
    def po_setup(self):
        """Create a PO with 2 line items, a GRN, and helper references."""
        from apps.documents.models import (
            PurchaseOrder,
            PurchaseOrderLineItem,
            GoodsReceiptNote,
            GRNLineItem,
        )
        from apps.vendors.models import Vendor

        vendor = Vendor.objects.create(
            name="Test Vendor RA",
            code="V-RA-001",
        )
        po = PurchaseOrder.objects.create(
            po_number="PO-RA-001",
            vendor=vendor,
            total_amount=Decimal("10000"),
        )
        po_line_1 = PurchaseOrderLineItem.objects.create(
            purchase_order=po,
            line_number=1,
            description="Widget A",
            quantity=Decimal("100"),
            unit_price=Decimal("50"),
            line_amount=Decimal("5000"),
        )
        po_line_2 = PurchaseOrderLineItem.objects.create(
            purchase_order=po,
            line_number=2,
            description="Widget B",
            quantity=Decimal("50"),
            unit_price=Decimal("100"),
            line_amount=Decimal("5000"),
        )

        grn = GoodsReceiptNote.objects.create(
            grn_number="GRN-RA-001",
            purchase_order=po,
            vendor=vendor,
        )
        grn_line_1 = GRNLineItem.objects.create(
            grn=grn,
            po_line=po_line_1,
            line_number=1,
            description="Widget A",
            quantity_received=Decimal("100"),
        )
        grn_line_2 = GRNLineItem.objects.create(
            grn=grn,
            po_line=po_line_2,
            line_number=2,
            description="Widget B",
            quantity_received=Decimal("50"),
        )

        return {
            "vendor": vendor,
            "po": po,
            "po_line_1": po_line_1,
            "po_line_2": po_line_2,
            "grn": grn,
            "grn_line_1": grn_line_1,
            "grn_line_2": grn_line_2,
        }

    def _create_recon_result(self, po, match_status="MATCHED"):
        """Create a ReconciliationResult pointing to the given PO."""
        from apps.reconciliation.models import (
            ReconciliationRun,
            ReconciliationResult,
        )
        from apps.documents.models import Invoice

        inv = Invoice.objects.create(
            invoice_number=f"INV-RA-{Invoice.objects.count() + 1}",
            po_number=po.po_number,
            total_amount=Decimal("1000"),
            status="RECONCILED",
        )
        run = ReconciliationRun.objects.create(status="COMPLETED")
        result = ReconciliationResult.objects.create(
            run=run,
            invoice=inv,
            purchase_order=po,
            match_status=match_status,
        )
        return result, inv

    def test_ra11_no_prior_results(self, po_setup):
        """RA-11: No prior reconciliation results -- consumed = 0 for all lines."""
        po = po_setup["po"]
        pl1 = po_setup["po_line_1"]
        pl2 = po_setup["po_line_2"]

        avail = ReceiptAvailabilityService.compute(
            po_id=po.pk,
            total_received_by_po_line={
                pl1.pk: Decimal("100"),
                pl2.pk: Decimal("50"),
            },
        )

        assert avail.get(pl1.pk).available_qty == Decimal("100")
        assert avail.get(pl2.pk).available_qty == Decimal("50")

    def test_ra12_prior_result_consumption(self, po_setup):
        """RA-12: One prior MATCHED result consumed 40 of line 1 -- available = 60."""
        from apps.reconciliation.models import ReconciliationResultLine

        po = po_setup["po"]
        pl1 = po_setup["po_line_1"]

        result, _ = self._create_recon_result(po, match_status="MATCHED")
        ReconciliationResultLine.objects.create(
            result=result,
            po_line=pl1,
            qty_invoice=Decimal("40"),
            match_status="MATCHED",
        )

        avail = ReceiptAvailabilityService.compute(
            po_id=po.pk,
            total_received_by_po_line={pl1.pk: Decimal("100")},
        )

        la = avail.get(pl1.pk)
        assert la.cumulative_received_qty == Decimal("100")
        assert la.previously_consumed_qty == Decimal("40")
        assert la.available_qty == Decimal("60")

    def test_ra13_exclude_current_result(self, po_setup):
        """RA-13: exclude_result_id prevents self-counting."""
        from apps.reconciliation.models import ReconciliationResultLine

        po = po_setup["po"]
        pl1 = po_setup["po_line_1"]

        result, _ = self._create_recon_result(po, match_status="MATCHED")
        ReconciliationResultLine.objects.create(
            result=result,
            po_line=pl1,
            qty_invoice=Decimal("40"),
            match_status="MATCHED",
        )

        # Excluding this result's ID should show 0 consumption
        avail = ReceiptAvailabilityService.compute(
            po_id=po.pk,
            total_received_by_po_line={pl1.pk: Decimal("100")},
            exclude_result_id=result.pk,
        )

        la = avail.get(pl1.pk)
        assert la.previously_consumed_qty == ZERO
        assert la.available_qty == Decimal("100")

    def test_ra14_multiple_prior_results_cumulative(self, po_setup):
        """RA-14: Two prior results consuming 30 + 25 = 55 from same PO line."""
        from apps.reconciliation.models import ReconciliationResultLine

        po = po_setup["po"]
        pl1 = po_setup["po_line_1"]

        r1, _ = self._create_recon_result(po, match_status="MATCHED")
        ReconciliationResultLine.objects.create(
            result=r1,
            po_line=pl1,
            qty_invoice=Decimal("30"),
            match_status="MATCHED",
        )

        r2, _ = self._create_recon_result(po, match_status="PARTIAL_MATCH")
        ReconciliationResultLine.objects.create(
            result=r2,
            po_line=pl1,
            qty_invoice=Decimal("25"),
            match_status="PARTIAL_MATCH",
        )

        avail = ReceiptAvailabilityService.compute(
            po_id=po.pk,
            total_received_by_po_line={pl1.pk: Decimal("100")},
        )

        la = avail.get(pl1.pk)
        assert la.previously_consumed_qty == Decimal("55")
        assert la.available_qty == Decimal("45")

    def test_ra15_unmatched_results_not_counted(self, po_setup):
        """RA-15: UNMATCHED and ERROR results are NOT counted as consumed."""
        from apps.reconciliation.models import ReconciliationResultLine

        po = po_setup["po"]
        pl1 = po_setup["po_line_1"]

        r1, _ = self._create_recon_result(po, match_status="UNMATCHED")
        ReconciliationResultLine.objects.create(
            result=r1,
            po_line=pl1,
            qty_invoice=Decimal("50"),
            match_status="UNMATCHED",
        )

        r2, _ = self._create_recon_result(po, match_status="ERROR")
        ReconciliationResultLine.objects.create(
            result=r2,
            po_line=pl1,
            qty_invoice=Decimal("30"),
            match_status="ERROR",
        )

        avail = ReceiptAvailabilityService.compute(
            po_id=po.pk,
            total_received_by_po_line={pl1.pk: Decimal("100")},
        )

        la = avail.get(pl1.pk)
        # Neither UNMATCHED nor ERROR should be counted
        assert la.previously_consumed_qty == ZERO
        assert la.available_qty == Decimal("100")

    def test_ra16_requires_review_counted(self, po_setup):
        """RA-16: REQUIRES_REVIEW results ARE counted (provisionally consumed)."""
        from apps.reconciliation.models import ReconciliationResultLine

        po = po_setup["po"]
        pl1 = po_setup["po_line_1"]

        r1, _ = self._create_recon_result(po, match_status="REQUIRES_REVIEW")
        ReconciliationResultLine.objects.create(
            result=r1,
            po_line=pl1,
            qty_invoice=Decimal("70"),
            match_status="REQUIRES_REVIEW",
        )

        avail = ReceiptAvailabilityService.compute(
            po_id=po.pk,
            total_received_by_po_line={pl1.pk: Decimal("100")},
        )

        la = avail.get(pl1.pk)
        assert la.previously_consumed_qty == Decimal("70")
        assert la.available_qty == Decimal("30")


# ===================================================================
# Exception builder test for the new exception type
# ===================================================================

class TestExceptionBuilderReceiptAvailability:
    """Verify ExceptionBuilderService emits the correct exception types."""

    def test_exceeds_available_emits_correct_exception_type(self):
        """When invoiced_exceeds_available=True, exception type is INVOICE_QTY_EXCEEDS_AVAILABLE."""
        from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
        from apps.core.enums import ExceptionType

        builder = ExceptionBuilderService()

        grn_result = GRNMatchResult(
            grn_available=True,
            fully_received=True,
            has_receipt_issues=True,
            line_comparisons=[
                GRNLineComparison(
                    invoice_line_id=1001,
                    po_line_id=10,
                    qty_invoiced=Decimal("50"),
                    qty_ordered=Decimal("100"),
                    qty_received=Decimal("100"),
                    invoiced_exceeds_received=False,
                    invoiced_exceeds_available=True,
                    cumulative_received_qty=Decimal("100"),
                    previously_consumed_qty=Decimal("80"),
                    available_qty=Decimal("20"),
                    contributing_grn_line_ids=[501, 502],
                ),
            ],
        )

        # Patch _make to return a simple object instead of a real model instance
        made_excs = []
        original_make = ExceptionBuilderService._make

        @staticmethod
        def fake_make(**kwargs):
            obj = MagicMock()
            obj.exception_type = kwargs.get("exc_type", "")
            obj.severity = kwargs.get("severity", "")
            obj.message = kwargs.get("message", "")
            obj.details = kwargs.get("details", {})
            made_excs.append(obj)
            return obj

        builder._make = fake_make
        try:
            mock_result = MagicMock()
            mock_result.purchase_order = None
            excs = builder._grn_exceptions(mock_result, grn_result, po_balance=None)
        finally:
            builder._make = original_make

        exc_types = [e.exception_type for e in excs]
        assert ExceptionType.INVOICE_QTY_EXCEEDS_AVAILABLE in exc_types
        assert ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED not in exc_types

    def test_exceeds_received_but_not_available_emits_old_exception(self):
        """When invoiced_exceeds_received=True but availability not set, use old exc type."""
        from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
        from apps.core.enums import ExceptionType

        builder = ExceptionBuilderService()

        grn_result = GRNMatchResult(
            grn_available=True,
            fully_received=False,
            has_receipt_issues=True,
            line_comparisons=[
                GRNLineComparison(
                    invoice_line_id=1001,
                    po_line_id=10,
                    qty_invoiced=Decimal("60"),
                    qty_ordered=Decimal("100"),
                    qty_received=Decimal("50"),
                    invoiced_exceeds_received=True,
                    invoiced_exceeds_available=False,
                ),
            ],
        )

        made_excs = []
        original_make = ExceptionBuilderService._make

        @staticmethod
        def fake_make(**kwargs):
            obj = MagicMock()
            obj.exception_type = kwargs.get("exc_type", "")
            obj.severity = kwargs.get("severity", "")
            obj.message = kwargs.get("message", "")
            obj.details = kwargs.get("details", {})
            made_excs.append(obj)
            return obj

        builder._make = fake_make
        try:
            mock_result = MagicMock()
            mock_result.purchase_order = None
            excs = builder._grn_exceptions(mock_result, grn_result, po_balance=None)
        finally:
            builder._make = original_make

        exc_types = [e.exception_type for e in excs]
        assert ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED in exc_types
        assert ExceptionType.INVOICE_QTY_EXCEEDS_AVAILABLE not in exc_types
