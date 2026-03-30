"""Tests for FieldConfidenceService.

Covers:
  - Explicit, clean invoice_number → score 1.0
  - invoice_number recovered from OCR via repair → score 0.65
  - invoice_number excluded by repair (blank after repair) → score 0.0
  - tax_percentage recomputed by repair → score 0.55
  - Missing critical fields → score 0.0
  - Low-confidence critical field populates low_confidence_fields list
  - Service is fail-silent (returns neutral FieldConfidenceResult on bad input)
  - Line-level math check: matching qty × price
  - Line-level math check: mismatched qty × price
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from apps.extraction.services.field_confidence_service import (
    FieldConfidenceService,
    FieldConfidenceResult,
    CRITICAL_FIELDS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalized(
    raw_vendor_name="Acme Corp",
    vendor_name_normalized="acme corp",
    raw_invoice_number="INV-001",
    normalized_invoice_number="INV001",
    raw_invoice_date="2024-01-15",
    invoice_date=None,
    raw_currency="USD",
    currency="USD",
    raw_total_amount="1000.00",
    total_amount=Decimal("1000.00"),
    raw_subtotal="850.00",
    subtotal=Decimal("850.00"),
    raw_tax_amount="150.00",
    tax_amount=Decimal("150.00"),
    raw_tax_percentage="17.65",
    tax_percentage=Decimal("17.65"),
    tax_breakdown=None,
    raw_vendor_tax_id="",
    vendor_tax_id="",
    raw_buyer_name="",
    buyer_name="",
    raw_due_date="",
    due_date=None,
    raw_po_number="PO-100",
    po_number="PO-100",
    line_items=None,
):
    from datetime import date
    m = MagicMock()
    m.raw_vendor_name = raw_vendor_name
    m.vendor_name_normalized = vendor_name_normalized
    m.raw_invoice_number = raw_invoice_number
    m.normalized_invoice_number = normalized_invoice_number
    m.raw_invoice_date = raw_invoice_date
    m.invoice_date = invoice_date or date(2024, 1, 15)
    m.raw_currency = raw_currency
    m.currency = currency
    m.raw_total_amount = raw_total_amount
    m.total_amount = total_amount
    m.raw_subtotal = raw_subtotal
    m.subtotal = subtotal
    m.raw_tax_amount = raw_tax_amount
    m.tax_amount = tax_amount
    m.raw_tax_percentage = raw_tax_percentage
    m.tax_percentage = tax_percentage
    m.tax_breakdown = tax_breakdown or {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "vat": 0.0}
    m.raw_vendor_tax_id = raw_vendor_tax_id
    m.vendor_tax_id = vendor_tax_id
    m.raw_buyer_name = raw_buyer_name
    m.buyer_name = buyer_name
    m.raw_due_date = raw_due_date
    m.due_date = due_date
    m.raw_po_number = raw_po_number
    m.po_number = po_number
    m.line_items = line_items or []
    return m


def _raw_json(**overrides):
    base = {
        "vendor_name": "Acme Corp",
        "invoice_number": "INV-001",
        "invoice_date": "2024-01-15",
        "currency": "USD",
        "total_amount": "1000.00",
        "subtotal": "850.00",
        "tax_amount": "150.00",
        "tax_percentage": "17.65",
        "po_number": "PO-100",
        "vendor_tax_id": "",
        "buyer_name": "",
        "due_date": "",
        "tax_breakdown": {},
        "line_items": [],
    }
    base.update(overrides)
    return base


# ── Tests: invoice_number ────────────────────────────────────────────────────

def test_invoice_number_clean_scores_1():
    norm = _normalized()
    raw = _raw_json()
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["invoice_number"] == 1.0


def test_invoice_number_missing_scores_0():
    norm = _normalized(raw_invoice_number="", normalized_invoice_number="")
    raw = _raw_json(invoice_number="")
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["invoice_number"] == 0.0


def test_invoice_number_recovered_from_ocr_scores_065():
    norm = _normalized()
    raw = _raw_json()
    repair_actions = ["invoice_number.recovered_from_ocr"]
    result = FieldConfidenceService.score(norm, raw, repair_actions)
    assert result.header["invoice_number"] == pytest.approx(0.65)


def test_invoice_number_excluded_reference_repair_scores_below_08():
    """invoice_number modified by exclusion repair (not recovery) → 0.78."""
    norm = _normalized()
    raw = _raw_json()
    repair_actions = ["invoice_number.excluded_reference"]
    result = FieldConfidenceService.score(norm, raw, repair_actions)
    assert result.header["invoice_number"] == pytest.approx(0.78)


def test_invoice_number_present_but_normalization_stripped_scores_low():
    """LLM returned value but normalize_invoice_number returned empty."""
    norm = _normalized(normalized_invoice_number="")
    raw = _raw_json(invoice_number="REF-ONLY")
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["invoice_number"] < 0.3


# ── Tests: tax_percentage ────────────────────────────────────────────────────

def test_tax_percentage_recomputed_by_repair_scores_055():
    norm = _normalized()
    raw = _raw_json()
    repair_actions = ["tax_percentage.recomputed"]
    result = FieldConfidenceService.score(norm, raw, repair_actions)
    assert result.header["tax_percentage"] == pytest.approx(0.55)


def test_tax_percentage_clean_scores_1():
    norm = _normalized()
    raw = _raw_json()
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["tax_percentage"] == 1.0


# ── Tests: missing critical fields ──────────────────────────────────────────

def test_missing_vendor_name_scores_0():
    norm = _normalized(raw_vendor_name="", vendor_name_normalized="")
    raw = _raw_json(vendor_name="")
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["vendor_name"] == 0.0


def test_missing_total_amount_scores_0():
    norm = _normalized(raw_total_amount="", total_amount=None)
    raw = _raw_json(total_amount="")
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["total_amount"] == 0.0


def test_missing_invoice_date_scores_0():
    norm = _normalized(raw_invoice_date="", invoice_date=None)
    raw = _raw_json(invoice_date="")
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.header["invoice_date"] == 0.0


# ── Tests: low_confidence_fields list ───────────────────────────────────────

def test_low_confidence_fields_populated():
    """Missing invoice_number and vendor_name should appear in low_confidence_fields."""
    norm = _normalized(raw_invoice_number="", normalized_invoice_number="", raw_vendor_name="", vendor_name_normalized="")
    raw = _raw_json(invoice_number="", vendor_name="")
    result = FieldConfidenceService.score(norm, raw, [])
    assert "invoice_number" in result.low_confidence_fields
    assert "vendor_name" in result.low_confidence_fields


def test_weakest_critical_field_populated():
    norm = _normalized(raw_invoice_number="", normalized_invoice_number="")
    raw = _raw_json(invoice_number="")
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.weakest_critical_field == "invoice_number"
    assert result.weakest_critical_score == 0.0


# ── Tests: fail-silent ───────────────────────────────────────────────────────

def test_fail_silent_on_bad_input():
    """Service must return empty FieldConfidenceResult, never raise."""
    result = FieldConfidenceService.score(None, None, None)
    assert isinstance(result, FieldConfidenceResult)
    assert result.header == {}


# ── Tests: line-level math ───────────────────────────────────────────────────

def _make_line(qty, price, line_amount, line_number=1):
    li = MagicMock()
    li.line_number = line_number
    li.quantity = Decimal(str(qty)) if qty is not None else None
    li.unit_price = Decimal(str(price)) if price is not None else None
    li.line_amount = Decimal(str(line_amount)) if line_amount is not None else None
    li.description = "Test item"
    li.tax_percentage = None
    li.tax_amount = None
    return li


def test_line_math_matching_scores_1():
    li = _make_line(qty=2, price=50.0, line_amount=100.0)
    norm = _normalized(line_items=[li])
    raw = _raw_json(line_items=[{"quantity": 2, "unit_price": 50.0, "line_amount": 100.0}])
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.lines[0]["line_math"] == 1.0


def test_line_math_large_discrepancy_scores_low():
    li = _make_line(qty=2, price=50.0, line_amount=200.0)  # should be 100, got 200
    norm = _normalized(line_items=[li])
    raw = _raw_json(line_items=[{"quantity": 2, "unit_price": 50.0, "line_amount": 200.0}])
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.lines[0]["line_math"] < 0.5


def test_line_math_missing_qty_scores_neutral():
    li = _make_line(qty=None, price=50.0, line_amount=100.0)
    norm = _normalized(line_items=[li])
    raw = _raw_json(line_items=[{"unit_price": 50.0, "line_amount": 100.0}])
    result = FieldConfidenceService.score(norm, raw, [])
    assert result.lines[0]["line_math"] == pytest.approx(0.7)


# ── Tests: to_serializable ───────────────────────────────────────────────────

def test_to_serializable_returns_dict():
    norm = _normalized()
    raw = _raw_json()
    result = FieldConfidenceService.score(norm, raw, [])
    serialized = FieldConfidenceService.to_serializable(result)
    assert isinstance(serialized, dict)
    assert "header" in serialized
    assert "lines" in serialized
    assert "low_confidence_fields" in serialized
