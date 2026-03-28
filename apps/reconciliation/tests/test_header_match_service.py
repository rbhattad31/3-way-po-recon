"""
Tests for HeaderMatchService (HM-01 → HM-12)

DB-backed tests — requires @pytest.mark.django_db.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from apps.reconciliation.services.header_match_service import HeaderMatchService
from apps.reconciliation.services.tolerance_engine import ToleranceEngine, ToleranceThresholds


# ─── Helper: build engine without DB ─────────────────────────────────────────

def make_engine(amount_pct=1.0) -> ToleranceEngine:
    engine = ToleranceEngine.__new__(ToleranceEngine)
    engine.thresholds = ToleranceThresholds(
        quantity_pct=2.0,
        price_pct=1.0,
        amount_pct=amount_pct,
    )
    return engine


# ─── DB-free tests using mocked Invoice/PO ──────────────────────────────────

class TestHeaderMatchServiceMocked:
    """Tests using MagicMock objects for Invoice and PO — no DB required."""

    def setup_method(self):
        self.svc = HeaderMatchService(make_engine())

    def _make_invoice(self, **kwargs):
        inv = MagicMock()
        inv.vendor_id = kwargs.get("vendor_id", None)
        inv.raw_vendor_name = kwargs.get("raw_vendor_name", None)
        inv.currency = kwargs.get("currency", "SAR")
        inv.total_amount = kwargs.get("total_amount", Decimal("1000.00"))
        inv.tax_amount = kwargs.get("tax_amount", None)
        inv.pk = kwargs.get("pk", 1)
        return inv

    def _make_po(self, **kwargs):
        po = MagicMock()
        po.vendor_id = kwargs.get("vendor_id", None)
        po.vendor = kwargs.get("vendor", None)
        po.currency = kwargs.get("currency", "SAR")
        po.total_amount = kwargs.get("total_amount", Decimal("1000.00"))
        po.tax_amount = kwargs.get("tax_amount", None)
        po.po_number = kwargs.get("po_number", "PO-0001")
        return po

    # ── HM-01: Full header match ───────────────────────────────────────────────

    def test_hm01_full_header_match(self):
        """HM-01: Same vendor FK, currency, amounts within tolerance → all_ok=True."""
        inv = self._make_invoice(vendor_id=1, currency="SAR", total_amount=Decimal("1000"))
        po = self._make_po(vendor_id=1, currency="SAR", total_amount=Decimal("1000"))
        result = self.svc.match(inv, po)
        assert result.vendor_match is True
        assert result.currency_match is True
        assert result.po_total_match is True
        assert result.all_ok is True

    # ── HM-02: Vendor FK match ─────────────────────────────────────────────────

    def test_hm02_vendor_fk_match(self):
        """HM-02: Both have same vendor FK → vendor_match=True."""
        inv = self._make_invoice(vendor_id=5)
        po = self._make_po(vendor_id=5)
        result = self.svc.match(inv, po)
        assert result.vendor_match is True

    def test_hm02_vendor_fk_mismatch(self):
        """Variant: Different vendor FK → vendor_match=False."""
        inv = self._make_invoice(vendor_id=5)
        po = self._make_po(vendor_id=99)
        result = self.svc.match(inv, po)
        assert result.vendor_match is False
        assert result.all_ok is False

    # ── HM-03: Vendor normalized name match ───────────────────────────────────

    def test_hm03_vendor_normalized_name_match(self):
        """HM-03: No FK, normalized names match → vendor_match=True."""
        inv = self._make_invoice(vendor_id=None, raw_vendor_name="ABC Ltd")
        vendor_mock = MagicMock()
        vendor_mock.normalized_name = "abc ltd"
        vendor_mock.name = "ABC Ltd"
        po = self._make_po(vendor_id=None, vendor=vendor_mock)
        result = self.svc.match(inv, po)
        assert result.vendor_match is True

    # ── HM-04: Vendor name mismatch ───────────────────────────────────────────

    def test_hm04_vendor_name_mismatch(self):
        """HM-04: Completely different vendor names → vendor_match=False."""
        inv = self._make_invoice(vendor_id=None, raw_vendor_name="Acme Corp")
        vendor_mock = MagicMock()
        vendor_mock.normalized_name = "global supplies"
        vendor_mock.name = "Global Supplies"
        po = self._make_po(vendor_id=None, vendor=vendor_mock)
        result = self.svc.match(inv, po)
        assert result.vendor_match is False
        assert result.all_ok is False

    # ── HM-05: Vendor inconclusive ────────────────────────────────────────────

    def test_hm05_vendor_inconclusive_no_data(self):
        """HM-05: Both vendor FK and name are null → vendor_match=None."""
        inv = self._make_invoice(vendor_id=None, raw_vendor_name=None)
        po = self._make_po(vendor_id=None, vendor=None)
        result = self.svc.match(inv, po)
        assert result.vendor_match is None

    # ── HM-06: Currency case-insensitive match ─────────────────────────────────

    def test_hm06_currency_case_insensitive(self):
        """HM-06: 'sar' vs 'SAR' → currency_match=True."""
        inv = self._make_invoice(vendor_id=1, currency="sar")
        po = self._make_po(vendor_id=1, currency="SAR")
        result = self.svc.match(inv, po)
        assert result.currency_match is True

    # ── HM-07: Currency mismatch ───────────────────────────────────────────────

    def test_hm07_currency_mismatch(self):
        """HM-07: 'USD' vs 'SAR' → currency_match=False."""
        inv = self._make_invoice(vendor_id=1, currency="USD")
        po = self._make_po(vendor_id=1, currency="SAR")
        result = self.svc.match(inv, po)
        assert result.currency_match is False
        assert result.all_ok is False

    # ── HM-08: Amount within tolerance ────────────────────────────────────────

    def test_hm08_amount_within_tolerance(self):
        """HM-08: 1000 vs 1009 (0.9% diff, 1% limit) → within tolerance."""
        inv = self._make_invoice(vendor_id=1, total_amount=Decimal("1000"))
        po = self._make_po(vendor_id=1, total_amount=Decimal("1009"))
        result = self.svc.match(inv, po)
        assert result.po_total_match is True

    # ── HM-09: Amount exceeds tolerance ───────────────────────────────────────

    def test_hm09_amount_exceeds_tolerance(self):
        """HM-09: 1000 vs 1020 (2% diff, 1% limit) → NOT within tolerance."""
        inv = self._make_invoice(vendor_id=1, total_amount=Decimal("1000"))
        po = self._make_po(vendor_id=1, total_amount=Decimal("1020"))
        result = self.svc.match(inv, po)
        assert result.po_total_match is False
        assert result.all_ok is False

    # ── HM-10: Tax match ───────────────────────────────────────────────────────

    def test_hm10_tax_match(self):
        """HM-10: Both have tax amounts within tolerance → tax_match=True."""
        inv = self._make_invoice(vendor_id=1, total_amount=Decimal("1000"), tax_amount=Decimal("150"))
        po = self._make_po(vendor_id=1, total_amount=Decimal("1000"), tax_amount=Decimal("151"))
        result = self.svc.match(inv, po)
        assert result.tax_match is True  # 0.66% diff within 1%

    # ── HM-11: Tax missing on PO ──────────────────────────────────────────────

    def test_hm11_tax_missing_on_po_does_not_block(self):
        """HM-11: po.tax_amount=None → tax_match=None, all_ok not blocked."""
        inv = self._make_invoice(vendor_id=1, total_amount=Decimal("1000"), tax_amount=Decimal("150"))
        po = self._make_po(vendor_id=1, total_amount=Decimal("1000"), tax_amount=None)
        result = self.svc.match(inv, po)
        assert result.tax_match is None
        # Tax being None should NOT prevent all_ok if everything else passes
        assert result.all_ok is True

    # ── HM-12: Tax mismatch blocks all_ok ─────────────────────────────────────

    def test_hm12_tax_mismatch_blocks_all_ok(self):
        """HM-12: Tax amounts differ beyond tolerance → all_ok=False."""
        # 1% tolerance on amounts — use big tax difference
        inv = self._make_invoice(vendor_id=1, total_amount=Decimal("1000"), tax_amount=Decimal("100"))
        po = self._make_po(vendor_id=1, total_amount=Decimal("1000"), tax_amount=Decimal("115"))
        result = self.svc.match(inv, po)
        assert result.tax_match is False
        assert result.all_ok is False

    # ── Whitespace currency stripping ─────────────────────────────────────────

    def test_currency_whitespace_stripped(self):
        """Currency with leading/trailing spaces should still match."""
        inv = self._make_invoice(vendor_id=1, currency="  SAR  ")
        po = self._make_po(vendor_id=1, currency="SAR")
        result = self.svc.match(inv, po)
        assert result.currency_match is True
