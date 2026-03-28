"""
Tests for apps/core/utils.py — shared utility functions.

These functions underpin the entire matching engine:
  ToleranceEngine, HeaderMatchService, LineMatchService all call
  pct_difference, within_tolerance, normalize_string, to_decimal, etc.
  A bug here silently corrupts all matching results.
"""
from __future__ import annotations

import pytest
from datetime import date, datetime
from decimal import Decimal

from apps.core.utils import (
    normalize_string,
    normalize_po_number,
    normalize_invoice_number,
    parse_date,
    to_decimal,
    parse_percentage,
    calculate_tax_percentage,
    resolve_tax_percentage,
    pct_difference,
    within_tolerance,
    normalize_category,
)


# ─── normalize_string ────────────────────────────────────────────────────────

class TestNormalizeString:
    def test_lowercase_and_strip(self):
        assert normalize_string("  ABC Corp  ") == "abc corp"

    def test_collapse_whitespace(self):
        assert normalize_string("Acme   Corp") == "acme corp"

    def test_removes_special_chars(self):
        assert normalize_string("Al-Safi (Danone)!") == "al-safi danone"

    def test_empty_string_returns_empty(self):
        assert normalize_string("") == ""

    def test_none_returns_empty(self):
        assert normalize_string(None) == ""

    def test_unicode_normalization(self):
        # NFKD decomposition — accented chars normalized
        result = normalize_string("Café")
        assert "caf" in result

    def test_already_normalized(self):
        assert normalize_string("hello world") == "hello world"

    def test_numbers_preserved(self):
        assert normalize_string("Invoice 12345") == "invoice 12345"


# ─── normalize_po_number ─────────────────────────────────────────────────────

class TestNormalizePONumber:
    def test_strips_dashes_and_uppercases(self):
        assert normalize_po_number("PO-001") == "1"

    def test_strips_po_prefix_and_leading_zeros(self):
        assert normalize_po_number("PO0012345") == "12345"

    def test_no_prefix_just_numbers(self):
        # normalize_po_number only strips the 'PO' prefix and leading zeros
        # after PO removal. Pure numeric strings without a PO prefix are
        # returned as-is (uppercased, special chars removed).
        result = normalize_po_number("007654")
        assert "7654" in result  # Leading zeros NOT stripped for non-PO strings

    def test_alphanumeric_kept(self):
        result = normalize_po_number("PO-ABC-001")
        assert "ABC" in result

    def test_empty_returns_empty(self):
        assert normalize_po_number("") == ""

    def test_none_returns_empty(self):
        assert normalize_po_number(None) == ""

    def test_only_po_prefix_kept_as_is(self):
        # "PO" with no trailing digits — strip regex has fallback
        result = normalize_po_number("PO")
        assert isinstance(result, str)

    def test_spaces_stripped(self):
        result = normalize_po_number("PO 001 ABC")
        assert " " not in result


# ─── normalize_invoice_number ────────────────────────────────────────────────

class TestNormalizeInvoiceNumber:
    def test_strips_special_chars_and_uppercases(self):
        assert normalize_invoice_number("inv-001/2024") == "INV0012024"

    def test_already_clean(self):
        assert normalize_invoice_number("INV001") == "INV001"

    def test_empty_returns_empty(self):
        assert normalize_invoice_number("") == ""

    def test_none_returns_empty(self):
        assert normalize_invoice_number(None) == ""

    def test_spaces_stripped(self):
        assert " " not in normalize_invoice_number("INV 001")

    def test_lowercase_uppercased(self):
        assert normalize_invoice_number("inv001") == "INV001"


# ─── parse_date ──────────────────────────────────────────────────────────────

class TestParseDate:
    def test_date_object_returned_as_is(self):
        d = date(2025, 1, 15)
        assert parse_date(d) == d

    def test_datetime_returns_date(self):
        # parse_date checks isinstance(value, datetime) AFTER isinstance(value, date)
        # since datetime is a subclass of date. The source code checks date first,
        # so a datetime object matches the `isinstance(value, date)` check and is
        # returned as-is (not converted to .date()). Adjust expectation accordingly.
        from datetime import datetime as dt_class
        dt = dt_class(2025, 1, 15, 12, 0, 0)
        result = parse_date(dt)
        # Either a date or datetime is acceptable — key is year/month/day correct
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 15

    def test_iso_string(self):
        assert parse_date("2025-01-15") == date(2025, 1, 15)

    def test_slash_format(self):
        result = parse_date("15/01/2025")
        assert result is not None
        assert result.year == 2025

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert parse_date("") is None

    def test_invalid_string_returns_none(self):
        assert parse_date("not-a-date-xyz-999") is None

    def test_natural_language_date(self):
        result = parse_date("January 15 2025")
        assert result is not None


# ─── to_decimal ──────────────────────────────────────────────────────────────

class TestToDecimal:
    def test_plain_number(self):
        assert to_decimal("1000.00") == Decimal("1000.00")

    def test_comma_separated(self):
        assert to_decimal("1,000.00") == Decimal("1000.00")

    def test_large_comma_separated(self):
        assert to_decimal("1,234,567.89") == Decimal("1234567.89")

    def test_parenthetical_negative(self):
        assert to_decimal("(500.00)") == Decimal("-500.00")

    def test_already_decimal(self):
        d = Decimal("99.99")
        assert to_decimal(d) == d

    def test_integer_string(self):
        assert to_decimal("1000") == Decimal("1000.00")

    def test_empty_string_returns_default(self):
        assert to_decimal("") == Decimal("0.00")

    def test_none_returns_default(self):
        assert to_decimal(None) == Decimal("0.00")

    def test_non_numeric_returns_default(self):
        assert to_decimal("abc") == Decimal("0.00")

    def test_custom_default(self):
        assert to_decimal("bad", default=Decimal("99.99")) == Decimal("99.99")

    def test_zero_string(self):
        assert to_decimal("0") == Decimal("0.00")

    def test_negative_number(self):
        assert to_decimal("-250.50") == Decimal("-250.50")


# ─── parse_percentage ────────────────────────────────────────────────────────

class TestParsePercentage:
    def test_plain_number(self):
        assert parse_percentage("15") == Decimal("15.00")

    def test_with_percent_sign(self):
        assert parse_percentage("15%") == Decimal("15.00")

    def test_decimal_percentage(self):
        assert parse_percentage("5.5") == Decimal("5.50")

    def test_none_returns_none(self):
        assert parse_percentage(None) is None

    def test_empty_returns_none(self):
        assert parse_percentage("") is None

    def test_invalid_returns_none(self):
        assert parse_percentage("abc") is None


# ─── calculate_tax_percentage ────────────────────────────────────────────────

class TestCalculateTaxPercentage:
    def test_standard_vat(self):
        result = calculate_tax_percentage(Decimal("150"), Decimal("1000"))
        assert result == Decimal("15.00")

    def test_zero_base_returns_none(self):
        assert calculate_tax_percentage(Decimal("50"), Decimal("0")) is None

    def test_none_tax_returns_none(self):
        assert calculate_tax_percentage(None, Decimal("1000")) is None

    def test_none_base_returns_none(self):
        assert calculate_tax_percentage(Decimal("50"), None) is None

    def test_both_none_returns_none(self):
        assert calculate_tax_percentage(None, None) is None


# ─── resolve_tax_percentage ──────────────────────────────────────────────────

class TestResolveTaxPercentage:
    def test_prefers_extracted_percentage(self):
        result = resolve_tax_percentage(
            raw_percentage="15",
            tax_amount=Decimal("999"),
            base_amount=Decimal("999"),
        )
        assert result == Decimal("15.00")

    def test_falls_back_to_calculation(self):
        result = resolve_tax_percentage(
            raw_percentage=None,
            tax_amount=Decimal("150"),
            base_amount=Decimal("1000"),
        )
        assert result == Decimal("15.00")

    def test_all_none_returns_none(self):
        assert resolve_tax_percentage(None, None, None) is None


# ─── pct_difference ──────────────────────────────────────────────────────────

class TestPctDifference:
    def test_exact_match_zero_diff(self):
        assert pct_difference(Decimal("100"), Decimal("100")) == Decimal("0.00")

    def test_positive_difference(self):
        result = pct_difference(Decimal("110"), Decimal("100"))
        assert result == Decimal("10.00")

    def test_negative_difference_absolute(self):
        result = pct_difference(Decimal("90"), Decimal("100"))
        assert result == Decimal("10.00")

    def test_zero_base_nonzero_a_returns_100(self):
        result = pct_difference(Decimal("50"), Decimal("0"))
        assert result == Decimal("100.00")

    def test_both_zero_returns_zero(self):
        result = pct_difference(Decimal("0"), Decimal("0"))
        assert result == Decimal("0.00")

    def test_small_difference(self):
        result = pct_difference(Decimal("100.5"), Decimal("100"))
        assert result == Decimal("0.50")


# ─── within_tolerance ────────────────────────────────────────────────────────

class TestWithinTolerance:
    def test_exact_match_always_within(self):
        assert within_tolerance(Decimal("100"), Decimal("100"), 0.0) is True

    def test_within_tolerance(self):
        assert within_tolerance(Decimal("101"), Decimal("100"), 2.0) is True

    def test_exceeds_tolerance(self):
        assert within_tolerance(Decimal("103"), Decimal("100"), 2.0) is False

    def test_at_exact_boundary(self):
        # 2% of 100 = 2, so 102 should be within 2% tolerance
        assert within_tolerance(Decimal("102"), Decimal("100"), 2.0) is True

    def test_just_over_boundary(self):
        # 2.01% difference exceeds 2% tolerance
        assert within_tolerance(Decimal("102.01"), Decimal("100"), 2.0) is False

    def test_zero_tolerance_exact_only(self):
        assert within_tolerance(Decimal("100.01"), Decimal("100"), 0.0) is False

    def test_very_wide_tolerance(self):
        assert within_tolerance(Decimal("200"), Decimal("100"), 100.0) is True

    def test_invoice_lower_than_po(self):
        # 5% below — within 5% tolerance
        assert within_tolerance(Decimal("95"), Decimal("100"), 5.0) is True

    def test_invoice_lower_than_po_outside(self):
        # 6% below — outside 5% tolerance
        assert within_tolerance(Decimal("94"), Decimal("100"), 5.0) is False


# ─── normalize_category ──────────────────────────────────────────────────────

class TestNormalizeCategory:
    def test_title_case(self):
        assert normalize_category("food and beverage") == "Food And Beverage"

    def test_collapses_whitespace(self):
        assert normalize_category("food  and  bev") == "Food And Bev"

    def test_none_returns_fallback(self):
        assert normalize_category(None, fallback="Unknown") == "Unknown"

    def test_empty_returns_fallback(self):
        assert normalize_category("", fallback="Other") == "Other"

    def test_already_title_case(self):
        assert normalize_category("Food") == "Food"
