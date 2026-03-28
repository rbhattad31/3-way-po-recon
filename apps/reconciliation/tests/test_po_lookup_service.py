"""
Tests for POLookupService — DB-backed.

Lookup strategy (from source):
  1. Exact match on po_number
  2. Normalized match on normalized_po_number
  3. Vendor + amount discovery (only when invoice has NO po_number at all)
     - Resolves vendor from normalized name or alias
     - Matches open POs by total_amount within 1% tolerance
     - Ambiguous (>1 match) → not_found (deferred to AI agent)
"""
from __future__ import annotations

import pytest
from decimal import Decimal

from apps.reconciliation.services.po_lookup_service import POLookupService, POLookupResult
from apps.reconciliation.tests.factories import InvoiceFactory, POFactory


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_vendor(name="Test Vendor", code=None):
    from apps.vendors.models import Vendor
    import uuid
    return Vendor.objects.create(
        code=code or str(uuid.uuid4())[:8].upper(),
        name=name,
        normalized_name=name.lower(),
    )


def make_po(po_number, vendor=None, total_amount="1000.00",
            normalized_po_number="", status="OPEN"):
    from apps.documents.models import PurchaseOrder
    po = PurchaseOrder.objects.create(
        po_number=po_number,
        normalized_po_number=normalized_po_number or po_number.upper(),
        vendor=vendor,
        total_amount=Decimal(total_amount),
        currency="SAR",
        status=status,
    )
    return po


def make_invoice(po_number="", raw_po_number="", total_amount="1000.00",
                 vendor=None, normalized_po_number=""):
    inv = InvoiceFactory(
        po_number=po_number,
        raw_po_number=raw_po_number,
        normalized_po_number=normalized_po_number,
        total_amount=Decimal(total_amount),
        vendor=vendor,
    )
    return inv


# ─── Strategy 1: Exact match ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestExactMatch:
    def test_exact_po_number_match(self):
        """Invoice po_number exactly matches PurchaseOrder.po_number."""
        po = make_po("PO-001")
        inv = make_invoice(po_number="PO-001")

        result = POLookupService().lookup(inv)

        assert result.found is True
        assert result.purchase_order.pk == po.pk
        assert result.lookup_method == "exact"

    def test_no_exact_match_moves_to_next_strategy(self):
        """When exact match fails, service proceeds to normalized lookup."""
        make_po("PO-001")
        inv = make_invoice(po_number="PO-002")  # different number

        result = POLookupService().lookup(inv)

        assert result.found is False

    def test_empty_po_number_skips_exact_match(self):
        """Invoice with no po_number skips exact match entirely."""
        make_po("PO-001")
        inv = make_invoice(po_number="")

        result = POLookupService().lookup(inv)
        # No exact match attempted — result depends on other strategies
        assert result.lookup_method in ("vendor_amount", "not_found")


# ─── Strategy 2: Normalized match ────────────────────────────────────────────

@pytest.mark.django_db
class TestNormalizedMatch:
    def test_normalized_po_number_match(self):
        """PO-001 on invoice matches PO with normalized_po_number='1'."""
        from apps.core.utils import normalize_po_number
        raw = "PO-001"
        norm = normalize_po_number(raw)
        po = make_po("INTERNAL-001", normalized_po_number=norm)

        inv = make_invoice(po_number=raw, normalized_po_number=norm)

        result = POLookupService().lookup(inv)

        assert result.found is True
        assert result.purchase_order.pk == po.pk
        assert result.lookup_method == "normalized"

    def test_normalized_match_case_insensitive(self):
        """po_number with mixed case normalizes correctly."""
        from apps.core.utils import normalize_po_number
        raw = "po-0042"
        norm = normalize_po_number(raw)
        make_po("PO0042", normalized_po_number=norm)
        inv = make_invoice(po_number=raw, normalized_po_number=norm)

        result = POLookupService().lookup(inv)
        assert result.found is True


# ─── Strategy 3: Vendor + amount discovery ───────────────────────────────────

@pytest.mark.django_db
class TestVendorAmountDiscovery:
    def test_discovers_po_by_vendor_and_amount(self):
        """When invoice has no PO number, matches open PO via vendor + amount."""
        vendor = make_vendor("Al-Safi Danone")
        po = make_po("PO-DISC-001", vendor=vendor, total_amount="1000.00", status="OPEN")

        inv = make_invoice(po_number="", raw_po_number="",
                           total_amount="1000.00", vendor=vendor)

        result = POLookupService().lookup(inv)

        assert result.found is True
        assert result.purchase_order.pk == po.pk
        assert result.lookup_method == "vendor_amount"

    def test_discovery_within_1pct_tolerance(self):
        """Amount within 1% tolerance is matched."""
        vendor = make_vendor("Test Vendor B")
        make_po("PO-TOL-001", vendor=vendor, total_amount="1000.00", status="OPEN")
        # 0.5% difference — within 1% tolerance
        inv = make_invoice(po_number="", raw_po_number="",
                           total_amount="1005.00", vendor=vendor)

        result = POLookupService().lookup(inv)
        assert result.found is True

    def test_discovery_outside_tolerance_not_matched(self):
        """Amount outside 1% tolerance is NOT matched."""
        vendor = make_vendor("Test Vendor C")
        make_po("PO-TOL-002", vendor=vendor, total_amount="1000.00", status="OPEN")
        # 5% difference — outside 1% tolerance
        inv = make_invoice(po_number="", raw_po_number="",
                           total_amount="1050.00", vendor=vendor)

        result = POLookupService().lookup(inv)
        assert result.found is False

    def test_discovery_ambiguous_multiple_matches_not_found(self):
        """Multiple POs match — ambiguous, return not_found for agent handling."""
        vendor = make_vendor("Ambiguous Vendor")
        make_po("PO-AMB-001", vendor=vendor, total_amount="1000.00", status="OPEN")
        make_po("PO-AMB-002", vendor=vendor, total_amount="1000.00", status="OPEN")

        inv = make_invoice(po_number="", raw_po_number="",
                           total_amount="1000.00", vendor=vendor)

        result = POLookupService().lookup(inv)
        assert result.found is False

    def test_discovery_skipped_when_invoice_has_po_reference(self):
        """Vendor+amount discovery skipped if invoice has a po_number (even if no DB match)."""
        vendor = make_vendor("Test Vendor D")
        make_po("PO-MATCH-001", vendor=vendor, total_amount="1000.00", status="OPEN")

        # Invoice has a po_number that doesn't match any PO — should NOT fall back to discovery
        inv = make_invoice(po_number="PO-WRONG", raw_po_number="",
                           total_amount="1000.00", vendor=vendor)

        result = POLookupService().lookup(inv)
        assert result.found is False
        assert result.lookup_method == "not_found"

    def test_skip_vendor_amount_flag_bypasses_discovery(self):
        """skip_vendor_amount=True prevents vendor+amount discovery."""
        vendor = make_vendor("Test Vendor E")
        make_po("PO-SKIP-001", vendor=vendor, total_amount="1000.00", status="OPEN")

        inv = make_invoice(po_number="", raw_po_number="",
                           total_amount="1000.00", vendor=vendor)

        result = POLookupService().lookup(inv, skip_vendor_amount=True)
        assert result.found is False

    def test_closed_po_not_matched_in_discovery(self):
        """Discovery only considers OPEN POs — CLOSED POs are excluded."""
        vendor = make_vendor("Test Vendor F")
        make_po("PO-CLOSED-001", vendor=vendor, total_amount="1000.00", status="CLOSED")

        inv = make_invoice(po_number="", raw_po_number="",
                           total_amount="1000.00", vendor=vendor)

        result = POLookupService().lookup(inv)
        assert result.found is False


# ─── Not found ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNotFound:
    def test_no_pos_in_db_returns_not_found(self):
        """No PO in DB — returns not_found."""
        inv = make_invoice(po_number="PO-MISSING")
        result = POLookupService().lookup(inv)
        assert result.found is False
        assert result.lookup_method == "not_found"
        assert result.purchase_order is None
