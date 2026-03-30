"""
Tests for NormalizationService — pure unit tests (no DB).

Key behaviours:
  - vendor_name_normalized: lowercase, stripped, whitespace collapsed
  - normalized_invoice_number: uppercase, special chars stripped
  - normalized_po_number: PO prefix and leading zeros stripped
  - currency: 3-char ISO code, invalid → 'USD'
  - amounts: comma-separated strings → Decimal
  - dates: multi-format string → date object
  - line items: each field normalized individually
"""
from __future__ import annotations

import pytest
from decimal import Decimal

from apps.extraction.services.normalization_service import NormalizationService
from apps.extraction.services.parser_service import ParsedInvoice, ParsedLineItem


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_parsed(
    vendor="Acme Corp", invoice_number="INV-001", po_number="PO-001",
    invoice_date="2025-01-15", currency="SAR", subtotal="900.00",
    tax_amount="100.00", total_amount="1000.00",
    tax_percentage="", confidence=0.95, line_items=None, **kwargs
):
    return ParsedInvoice(
        raw_vendor_name=vendor,
        raw_vendor_tax_id=kwargs.get("vendor_tax_id", ""),
        raw_buyer_name=kwargs.get("buyer_name", ""),
        raw_invoice_number=invoice_number,
        raw_invoice_date=invoice_date,
        raw_due_date=kwargs.get("due_date", ""),
        raw_po_number=po_number,
        raw_currency=currency,
        raw_subtotal=subtotal,
        raw_tax_percentage=tax_percentage,
        raw_tax_amount=tax_amount,
        raw_tax_breakdown=kwargs.get("tax_breakdown", {}),
        raw_total_amount=total_amount,
        confidence=confidence,
        line_items=line_items or [],
    )


def make_line(description="Item A", quantity="10", unit_price="100", line_amount="1000",
              tax_amount="", tax_percentage="", item_category="Food", line_number=1):
    return ParsedLineItem(
        line_number=line_number,
        raw_description=description,
        raw_item_category=item_category,
        raw_quantity=quantity,
        raw_unit_price=unit_price,
        raw_tax_percentage=tax_percentage,
        raw_tax_amount=tax_amount,
        raw_line_amount=line_amount,
    )


svc = NormalizationService()


# ─── Vendor normalization ─────────────────────────────────────────────────────

class TestVendorNormalization:
    def test_vendor_lowercased_and_stripped(self):
        result = svc.normalize(make_parsed(vendor="  ACME CORP  "))
        assert result.vendor_name_normalized == "acme corp"

    def test_vendor_whitespace_collapsed(self):
        result = svc.normalize(make_parsed(vendor="Al   Safi   Danone"))
        assert result.vendor_name_normalized == "al safi danone"

    def test_vendor_raw_value_preserved(self):
        result = svc.normalize(make_parsed(vendor="ACME Corp"))
        assert result.raw_vendor_name == "ACME Corp"


# ─── Invoice number normalization ─────────────────────────────────────────────

class TestInvoiceNumberNormalization:
    def test_invoice_number_uppercased(self):
        result = svc.normalize(make_parsed(invoice_number="inv-001"))
        assert result.normalized_invoice_number == "INV001"

    def test_invoice_number_special_chars_stripped(self):
        result = svc.normalize(make_parsed(invoice_number="INV/001/2025"))
        assert result.normalized_invoice_number == "INV0012025"

    def test_invoice_number_raw_preserved(self):
        result = svc.normalize(make_parsed(invoice_number="inv-001"))
        assert result.invoice_number == "inv-001"


# ─── PO number normalization ──────────────────────────────────────────────────

class TestPONormalization:
    def test_po_number_normalized(self):
        result = svc.normalize(make_parsed(po_number="PO-001"))
        # normalize_po_number strips PO prefix and leading zeros
        assert result.normalized_po_number == "1"

    def test_po_number_raw_preserved(self):
        result = svc.normalize(make_parsed(po_number="PO-001"))
        assert result.po_number == "PO-001"

    def test_empty_po_number(self):
        result = svc.normalize(make_parsed(po_number=""))
        assert result.normalized_po_number == ""


# ─── Currency normalization ───────────────────────────────────────────────────

class TestCurrencyNormalization:
    def test_valid_3char_currency_kept(self):
        result = svc.normalize(make_parsed(currency="SAR"))
        assert result.currency == "SAR"

    def test_lowercase_currency_uppercased(self):
        result = svc.normalize(make_parsed(currency="sar"))
        assert result.currency == "SAR"

    def test_invalid_currency_defaults_to_usd(self):
        result = svc.normalize(make_parsed(currency="DOLLARS"))
        assert result.currency == "USD"

    def test_empty_currency_defaults_to_usd(self):
        result = svc.normalize(make_parsed(currency=""))
        assert result.currency == "USD"

    def test_whitespace_stripped_from_currency(self):
        result = svc.normalize(make_parsed(currency=" SAR "))
        assert result.currency == "SAR"


# ─── Amount normalization ─────────────────────────────────────────────────────

class TestAmountNormalization:
    def test_plain_amount_parsed(self):
        result = svc.normalize(make_parsed(total_amount="1000.00"))
        assert result.total_amount == Decimal("1000.00")

    def test_comma_separated_amount(self):
        result = svc.normalize(make_parsed(total_amount="1,234.56"))
        assert result.total_amount == Decimal("1234.56")

    def test_empty_amount_returns_none(self):
        result = svc.normalize(make_parsed(total_amount=""))
        assert result.total_amount is None

    def test_non_numeric_amount_returns_none(self):
        result = svc.normalize(make_parsed(total_amount="N/A"))
        assert result.total_amount is None

    def test_tax_amount_parsed(self):
        result = svc.normalize(make_parsed(tax_amount="150.00"))
        assert result.tax_amount == Decimal("150.00")

    def test_subtotal_parsed(self):
        result = svc.normalize(make_parsed(subtotal="900.00"))
        assert result.subtotal == Decimal("900.00")


# ─── Date normalization ───────────────────────────────────────────────────────

class TestDateNormalization:
    def test_iso_date_parsed(self):
        from datetime import date
        result = svc.normalize(make_parsed(invoice_date="2025-01-15"))
        assert result.invoice_date == date(2025, 1, 15)

    def test_invalid_date_returns_none(self):
        result = svc.normalize(make_parsed(invoice_date="not-a-date"))
        assert result.invoice_date is None

    def test_empty_date_returns_none(self):
        result = svc.normalize(make_parsed(invoice_date=""))
        assert result.invoice_date is None


# ─── Line item normalization ──────────────────────────────────────────────────

class TestLineItemNormalization:
    def test_line_item_description_preserved(self):
        line = make_line(description="  Frozen Chicken  ")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].description == "Frozen Chicken"

    def test_line_item_description_normalized(self):
        line = make_line(description="Frozen Chicken BREAST")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].normalized_description == "frozen chicken breast"

    def test_line_item_quantity_parsed(self):
        line = make_line(quantity="10")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].quantity == Decimal("10.0000")

    def test_line_item_unit_price_parsed(self):
        line = make_line(unit_price="100.50")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].unit_price == Decimal("100.5000")

    def test_line_item_amount_parsed(self):
        line = make_line(line_amount="1000.00")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].line_amount == Decimal("1000.00")

    def test_line_item_category_normalized(self):
        line = make_line(item_category="food and beverage")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].item_category == "Food And Beverage"

    def test_multiple_line_items_all_normalized(self):
        lines = [make_line(line_number=i, description=f"Item {i}") for i in range(1, 4)]
        result = svc.normalize(make_parsed(line_items=lines))
        assert len(result.line_items) == 3

    def test_empty_quantity_returns_none(self):
        line = make_line(quantity="")
        result = svc.normalize(make_parsed(line_items=[line]))
        assert result.line_items[0].quantity is None

    def test_no_line_items(self):
        result = svc.normalize(make_parsed(line_items=[]))
        assert result.line_items == []


# ─── Tax breakdown normalization ──────────────────────────────────────────────

class TestTaxBreakdownNormalization:
    def test_tax_breakdown_coerced_to_float(self):
        result = svc.normalize(make_parsed(tax_breakdown={"cgst": "9", "sgst": "9"}))
        assert result.tax_breakdown["cgst"] == 9.0
        assert result.tax_breakdown["sgst"] == 9.0

    def test_missing_breakdown_keys_default_to_zero(self):
        result = svc.normalize(make_parsed(tax_breakdown={}))
        assert result.tax_breakdown["igst"] == 0.0
        assert result.tax_breakdown["vat"] == 0.0
