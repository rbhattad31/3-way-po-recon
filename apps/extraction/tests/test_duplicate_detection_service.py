"""
Tests for DuplicateDetectionService — DB-backed.

Two checks (from source):
  1. Same normalized_invoice_number + vendor.normalized_name → DUPLICATE
  2. Same normalized_invoice_number + total_amount → DUPLICATE
"""
from __future__ import annotations

import pytest
from decimal import Decimal

from apps.extraction.services.duplicate_detection_service import (
    DuplicateDetectionService,
    DuplicateCheckResult,
)
from apps.extraction.services.normalization_service import NormalizedInvoice


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_vendor(name="Test Vendor"):
    from apps.vendors.models import Vendor
    import uuid
    return Vendor.objects.create(
        code=str(uuid.uuid4())[:8].upper(),
        name=name,
        normalized_name=name.lower(),
    )


def make_existing_invoice(invoice_number, vendor=None, total_amount="1000.00",
                           normalized=None):
    from apps.documents.models import Invoice
    from apps.core.utils import normalize_invoice_number
    return Invoice.objects.create(
        invoice_number=invoice_number,
        normalized_invoice_number=normalized or normalize_invoice_number(invoice_number),
        vendor=vendor,
        total_amount=Decimal(total_amount),
        currency="SAR",
        status="READY_FOR_RECON",
        is_duplicate=False,
    )


def make_normalized_inv(invoice_number="INV001", vendor_name="test vendor",
                        total_amount="1000.00"):
    return NormalizedInvoice(
        normalized_invoice_number=invoice_number,
        vendor_name_normalized=vendor_name,
        total_amount=Decimal(total_amount),
        raw_vendor_name=vendor_name.title(),
        raw_invoice_number=invoice_number,
        raw_total_amount=total_amount,
    )


svc = DuplicateDetectionService()


# ─── Check 1: same invoice number + vendor ────────────────────────────────────

@pytest.mark.django_db
class TestSameInvoiceNumberAndVendor:
    def test_duplicate_detected_same_number_and_vendor(self):
        """Same normalized invoice number + vendor → is_duplicate=True."""
        vendor = make_vendor("Acme Corp")
        existing = make_existing_invoice("INV001", vendor=vendor, total_amount="999.00")

        inv = make_normalized_inv("INV001", vendor_name="acme corp", total_amount="500.00")
        result = svc.check(inv)

        assert result.is_duplicate is True
        assert result.duplicate_invoice_id == existing.pk
        assert "vendor" in result.reason.lower() or "invoice" in result.reason.lower()

    def test_different_vendor_not_duplicate_by_vendor_check(self):
        """Same invoice number but different vendor → check 1 does not fire.

        Note: Check 2 (same number + same amount) may still fire if the amounts
        match. To isolate check 1, use a different total_amount on the normalized
        invoice so check 2 is also skipped.
        """
        vendor_a = make_vendor("Vendor A")
        make_existing_invoice("INV001", vendor=vendor_a, total_amount="1000.00")

        # Different vendor AND different amount -> neither check fires
        inv = make_normalized_inv("INV001", vendor_name="vendor b", total_amount="9999.00")
        result = svc.check(inv)

        assert result.is_duplicate is False

    def test_different_invoice_number_not_duplicate(self):
        """Different normalized invoice number → not a duplicate."""
        vendor = make_vendor("Vendor X")
        make_existing_invoice("INV001", vendor=vendor)

        inv = make_normalized_inv("INV002", vendor_name="vendor x")
        result = svc.check(inv)

        assert result.is_duplicate is False


# ─── Check 2: same invoice number + total amount ──────────────────────────────

@pytest.mark.django_db
class TestSameInvoiceNumberAndAmount:
    def test_duplicate_detected_same_number_and_amount(self):
        """Same normalized invoice number + same total_amount → is_duplicate=True."""
        existing = make_existing_invoice("INV002", vendor=None, total_amount="1500.00")

        # Different vendor name (check 1 won't fire), same amount (check 2 fires)
        inv = make_normalized_inv("INV002", vendor_name="different vendor", total_amount="1500.00")
        result = svc.check(inv)

        assert result.is_duplicate is True
        assert result.duplicate_invoice_id == existing.pk

    def test_same_number_different_amount_not_duplicate(self):
        """Same invoice number but different total → not a duplicate."""
        make_existing_invoice("INV003", total_amount="1000.00")

        inv = make_normalized_inv("INV003", vendor_name="no vendor", total_amount="2000.00")
        result = svc.check(inv)

        assert result.is_duplicate is False


# ─── Empty invoice number ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEmptyInvoiceNumber:
    def test_empty_invoice_number_skips_all_checks(self):
        """When normalized_invoice_number is empty, no check is run."""
        make_existing_invoice("INV001")

        inv = make_normalized_inv("", vendor_name="any vendor", total_amount="1000.00")
        result = svc.check(inv)

        assert result.is_duplicate is False

    def test_returns_empty_result_on_empty_number(self):
        inv = make_normalized_inv("")
        result = svc.check(inv)
        assert isinstance(result, DuplicateCheckResult)
        assert result.is_duplicate is False


# ─── Exclude self ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestExcludeSelf:
    def test_exclude_invoice_id_not_flagged_as_duplicate(self):
        """When exclude_invoice_id is set, that invoice is not counted as duplicate."""
        vendor = make_vendor("Self Vendor")
        existing = make_existing_invoice("INV-SELF", vendor=vendor)

        inv = make_normalized_inv("INV-SELF", vendor_name="self vendor")
        result = svc.check(inv, exclude_invoice_id=existing.pk)

        assert result.is_duplicate is False

    def test_other_invoice_still_detected(self):
        """exclude_invoice_id excludes only that specific invoice."""
        vendor = make_vendor("Multi Vendor")
        inv1 = make_existing_invoice("INV-MULTI", vendor=vendor)
        inv2 = make_existing_invoice("INV-MULTI-2", vendor=vendor, total_amount="1000.00")
        # Give inv2 the same normalized number
        inv2.normalized_invoice_number = "INVMULTI"
        inv2.save()

        inv = make_normalized_inv("INVMULTI", vendor_name="multi vendor")
        result = svc.check(inv, exclude_invoice_id=inv1.pk)

        assert result.is_duplicate is True


# ─── Flagged-as-duplicate invoices excluded ───────────────────────────────────

@pytest.mark.django_db
class TestAlreadyFlaggedDuplicatesExcluded:
    def test_existing_duplicate_flagged_invoice_not_counted(self):
        """Invoices already marked is_duplicate=True are excluded from checks."""
        from apps.documents.models import Invoice
        from apps.core.utils import normalize_invoice_number
        vendor = make_vendor("Clean Vendor")
        Invoice.objects.create(
            invoice_number="INV-DUP",
            normalized_invoice_number=normalize_invoice_number("INV-DUP"),
            vendor=vendor,
            total_amount=Decimal("1000.00"),
            currency="SAR",
            status="READY_FOR_RECON",
            is_duplicate=True,  # already flagged
        )
        inv = make_normalized_inv("INVDUP", vendor_name="clean vendor")
        result = svc.check(inv)
        # The already-flagged duplicate should not be counted
        assert result.is_duplicate is False


# ─── No existing invoices ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNoExistingInvoices:
    def test_no_invoices_in_db_returns_clean(self):
        """Clean DB → no duplicate."""
        inv = make_normalized_inv("FRESH001", vendor_name="new vendor")
        result = svc.check(inv)
        assert result.is_duplicate is False
        assert result.duplicate_invoice_id is None
        assert result.reason == ""
