"""
Tests for GRNLookupService — DB-backed.

Key behaviours (from source):
  - No GRNs → GRNSummary(grn_available=False)
  - Multiple GRNs for same PO → quantities summed per PO line
  - Uses quantity_accepted if not None, else quantity_received
  - fully_received = True only when all PO lines fully covered
  - latest_receipt_date = max across all GRNs
  - grn_count = number of GRN records
"""
from __future__ import annotations

import pytest
from datetime import date
from decimal import Decimal

from apps.reconciliation.services.grn_lookup_service import GRNLookupService


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_vendor():
    from apps.vendors.models import Vendor
    import uuid
    return Vendor.objects.create(
        code=str(uuid.uuid4())[:8].upper(),
        name="Test GRN Vendor",
        normalized_name="test grn vendor",
    )


def make_po(vendor=None, po_number=None):
    from apps.documents.models import PurchaseOrder
    import uuid
    return PurchaseOrder.objects.create(
        po_number=po_number or f"PO-GRN-{uuid.uuid4().hex[:6].upper()}",
        vendor=vendor,
        total_amount=Decimal("1000.00"),
        currency="SAR",
        status="OPEN",
    )


def make_po_line(po, line_number=1, quantity="10.0000"):
    from apps.documents.models import PurchaseOrderLineItem
    return PurchaseOrderLineItem.objects.create(
        purchase_order=po,
        line_number=line_number,
        description=f"Item {line_number}",
        quantity=Decimal(quantity),
        unit_price=Decimal("100.0000"),
        line_amount=Decimal("1000.00"),
    )


def make_grn(po, grn_number=None, receipt_date=None):
    from apps.documents.models import GoodsReceiptNote
    import uuid
    return GoodsReceiptNote.objects.create(
        grn_number=grn_number or f"GRN-{uuid.uuid4().hex[:6].upper()}",
        purchase_order=po,
        receipt_date=receipt_date or date(2025, 1, 15),
        status="RECEIVED",
    )


def make_grn_line(grn, po_line, qty_received="10.0000", qty_accepted=None):
    from apps.documents.models import GRNLineItem
    return GRNLineItem.objects.create(
        grn=grn,
        po_line=po_line,
        line_number=1,
        description="Test Item",
        quantity_received=Decimal(qty_received),
        quantity_accepted=Decimal(qty_accepted) if qty_accepted else None,
    )


# ─── No GRNs ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNoGRNs:
    def test_no_grns_returns_not_available(self):
        """PO with no GRN records → grn_available=False."""
        po = make_po()
        result = GRNLookupService().lookup(po)
        assert result.grn_available is False
        assert result.grn_count == 0
        assert result.fully_received is False

    def test_no_grns_returns_empty_received_map(self):
        """No GRNs → total_received_by_po_line is empty."""
        po = make_po()
        result = GRNLookupService().lookup(po)
        assert result.total_received_by_po_line == {}


# ─── Single GRN ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestSingleGRN:
    def test_single_grn_exact_match_fully_received(self):
        """One GRN with qty_received == PO line qty → fully_received=True."""
        po = make_po()
        pol = make_po_line(po, line_number=1, quantity="10.0000")
        grn = make_grn(po, receipt_date=date(2025, 3, 1))
        make_grn_line(grn, pol, qty_received="10.0000")

        result = GRNLookupService().lookup(po)

        assert result.grn_available is True
        assert result.grn_count == 1
        assert result.fully_received is True
        assert result.total_received_by_po_line[pol.pk] == Decimal("10.0000")

    def test_single_grn_partial_receipt_not_fully_received(self):
        """One GRN with qty_received < PO line qty → fully_received=False."""
        po = make_po()
        pol = make_po_line(po, line_number=1, quantity="10.0000")
        grn = make_grn(po)
        make_grn_line(grn, pol, qty_received="8.0000")

        result = GRNLookupService().lookup(po)

        assert result.fully_received is False
        assert result.total_received_by_po_line[pol.pk] == Decimal("8.0000")

    def test_receipt_date_populated(self):
        """latest_receipt_date is the GRN receipt_date."""
        po = make_po()
        pol = make_po_line(po)
        expected_date = date(2025, 6, 15)
        grn = make_grn(po, receipt_date=expected_date)
        make_grn_line(grn, pol)

        result = GRNLookupService().lookup(po)
        assert result.latest_receipt_date == expected_date

    def test_uses_quantity_accepted_over_received(self):
        """When quantity_accepted is set, use it instead of quantity_received."""
        po = make_po()
        pol = make_po_line(po, quantity="10.0000")
        grn = make_grn(po)
        make_grn_line(grn, pol, qty_received="10.0000", qty_accepted="9.0000")

        result = GRNLookupService().lookup(po)
        # Should use qty_accepted=9, not qty_received=10
        assert result.total_received_by_po_line[pol.pk] == Decimal("9.0000")


# ─── Multiple GRNs ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMultipleGRNs:
    def test_quantities_summed_across_grns(self):
        """Two GRNs for same PO line — quantities are summed."""
        po = make_po()
        pol = make_po_line(po, line_number=1, quantity="10.0000")
        grn1 = make_grn(po, receipt_date=date(2025, 1, 1))
        grn2 = make_grn(po, receipt_date=date(2025, 2, 1))
        make_grn_line(grn1, pol, qty_received="5.0000")
        make_grn_line(grn2, pol, qty_received="5.0000")

        result = GRNLookupService().lookup(po)

        assert result.total_received_by_po_line[pol.pk] == Decimal("10.0000")
        assert result.fully_received is True
        assert result.grn_count == 2

    def test_latest_receipt_date_is_max(self):
        """latest_receipt_date = max date across all GRNs."""
        po = make_po()
        pol = make_po_line(po, quantity="20.0000")
        grn1 = make_grn(po, receipt_date=date(2025, 1, 1))
        grn2 = make_grn(po, receipt_date=date(2025, 3, 15))
        make_grn_line(grn1, pol, qty_received="10.0000")
        make_grn_line(grn2, pol, qty_received="10.0000")

        result = GRNLookupService().lookup(po)
        assert result.latest_receipt_date == date(2025, 3, 15)

    def test_multiple_po_lines_tracked_separately(self):
        """Each PO line has its own aggregated received quantity."""
        po = make_po()
        pol1 = make_po_line(po, line_number=1, quantity="5.0000")
        pol2 = make_po_line(po, line_number=2, quantity="10.0000")
        grn = make_grn(po)
        make_grn_line(grn, pol1, qty_received="5.0000")
        make_grn_line(grn, pol2, qty_received="10.0000")

        result = GRNLookupService().lookup(po)

        assert result.total_received_by_po_line[pol1.pk] == Decimal("5.0000")
        assert result.total_received_by_po_line[pol2.pk] == Decimal("10.0000")
        assert result.fully_received is True

    def test_partially_received_multi_line(self):
        """One line fully received, one partially → fully_received=False."""
        po = make_po()
        pol1 = make_po_line(po, line_number=1, quantity="10.0000")
        pol2 = make_po_line(po, line_number=2, quantity="10.0000")
        grn = make_grn(po)
        make_grn_line(grn, pol1, qty_received="10.0000")  # full
        make_grn_line(grn, pol2, qty_received="5.0000")   # partial

        result = GRNLookupService().lookup(po)
        assert result.fully_received is False

    def test_grn_line_without_po_line_ref_not_counted(self):
        """GRN lines with no po_line FK are excluded from received map."""
        from apps.documents.models import GRNLineItem
        import uuid
        po = make_po()
        grn = make_grn(po)
        # GRN line with no po_line reference
        GRNLineItem.objects.create(
            grn=grn,
            line_number=1,
            description="Orphan line",
            quantity_received=Decimal("10.0000"),
            po_line=None,
        )

        result = GRNLookupService().lookup(po)
        assert result.total_received_by_po_line == {}
