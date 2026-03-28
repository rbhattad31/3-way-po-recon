"""Tests for ReconciliationValidatorService.

Covers:
  - Clean invoice (all checks pass) → is_clean=True, 0 issues
  - Total mismatch (subtotal + tax ≠ total) → ERROR
  - Line sum mismatch (Σ lines ≠ subtotal) → WARNING
  - Line math mismatch (qty × unit_price ≠ line_amount) → WARNING
  - Tax breakdown mismatch (sum breakdown ≠ tax_amount) → WARNING
  - Tax percentage inconsistency → INFO
  - Missing fields → checks skipped gracefully
  - Fail-silent: never raises
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from apps.extraction.services.reconciliation_validator import (
    ReconciliationValidatorService,
    ReconciliationValidationResult,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _line(qty=None, unit_price=None, line_amount=None, tax_amount=None, line_number=1):
    li = MagicMock()
    li.line_number = line_number
    li.quantity = Decimal(str(qty)) if qty is not None else None
    li.unit_price = Decimal(str(unit_price)) if unit_price is not None else None
    li.line_amount = Decimal(str(line_amount)) if line_amount is not None else None
    li.tax_amount = Decimal(str(tax_amount)) if tax_amount is not None else None
    return li


def _normalized(
    subtotal="850.00",
    tax_amount="150.00",
    total_amount="1000.00",
    tax_percentage="17.65",
    tax_breakdown=None,
    line_items=None,
):
    m = MagicMock()
    m.subtotal = Decimal(subtotal) if subtotal else None
    m.tax_amount = Decimal(tax_amount) if tax_amount else None
    m.total_amount = Decimal(total_amount) if total_amount else None
    m.tax_percentage = Decimal(tax_percentage) if tax_percentage else None
    m.tax_breakdown = tax_breakdown or {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "vat": 0.0}
    m.line_items = line_items or []
    return m


# ── Tests: clean invoice ──────────────────────────────────────────────────────

def test_clean_invoice_no_issues():
    lines = [_line(qty=10, unit_price=85, line_amount=850, line_number=1)]
    norm = _normalized(line_items=lines)
    result = ReconciliationValidatorService.validate(norm)
    assert result.is_clean is True
    assert len(result.errors) == 0
    assert result.checks_run >= 2


# ── Tests: TOTAL_CHECK ───────────────────────────────────────────────────────

def test_total_mismatch_is_error():
    """subtotal=850, tax=150, total=1100 (should be 1000) → ERROR."""
    norm = _normalized(subtotal="850.00", tax_amount="150.00", total_amount="1100.00")
    result = ReconciliationValidatorService.validate(norm)
    assert result.is_clean is False
    assert any(i.issue_code == "TOTAL_MISMATCH" for i in result.issues)
    error = next(i for i in result.issues if i.issue_code == "TOTAL_MISMATCH")
    assert error.severity == "ERROR"


def test_total_within_tolerance_no_error():
    """subtotal=850, tax=150, total=1001 — 0.1% delta within 2% tolerance."""
    norm = _normalized(subtotal="850.00", tax_amount="150.00", total_amount="1001.00")
    result = ReconciliationValidatorService.validate(norm)
    assert not any(i.issue_code == "TOTAL_MISMATCH" for i in result.issues)


# ── Tests: LINE_SUM_CHECK ─────────────────────────────────────────────────────

def test_line_sum_mismatch_is_warning():
    """3 lines sum to 700, subtotal=850 → 17.6% delta → WARNING."""
    lines = [
        _line(line_amount=300, line_number=1),
        _line(line_amount=200, line_number=2),
        _line(line_amount=200, line_number=3),
    ]
    norm = _normalized(subtotal="850.00", line_items=lines)
    result = ReconciliationValidatorService.validate(norm)
    issue = next((i for i in result.issues if i.issue_code == "LINE_SUM_MISMATCH"), None)
    assert issue is not None
    assert issue.severity == "WARNING"


def test_line_sum_within_tolerance_no_warning():
    """Lines sum to 849 vs subtotal 850 — 0.12% within 5%."""
    lines = [_line(line_amount=849, line_number=1)]
    norm = _normalized(subtotal="850.00", line_items=lines)
    result = ReconciliationValidatorService.validate(norm)
    assert not any(i.issue_code == "LINE_SUM_MISMATCH" for i in result.issues)


# ── Tests: LINE_MATH_CHECK ───────────────────────────────────────────────────

def test_line_math_mismatch_is_warning():
    """qty=2, unit_price=50, line_amount=200 (should be 100) → WARNING."""
    lines = [_line(qty=2, unit_price=50, line_amount=200, line_number=1)]
    norm = _normalized(line_items=lines)
    result = ReconciliationValidatorService.validate(norm)
    issue = next((i for i in result.issues if i.issue_code == "LINE_MATH_MISMATCH"), None)
    assert issue is not None
    assert issue.severity == "WARNING"
    assert "Line 1" in issue.message


def test_line_math_correct_no_warning():
    lines = [_line(qty=5, unit_price=170, line_amount=850, line_number=1)]
    norm = _normalized(line_items=lines)
    result = ReconciliationValidatorService.validate(norm)
    assert not any(i.issue_code == "LINE_MATH_MISMATCH" for i in result.issues)


# ── Tests: TAX_BREAKDOWN_CHECK ───────────────────────────────────────────────

def test_tax_breakdown_mismatch_is_warning():
    """cgst=50, sgst=50, total bd=100, but tax_amount=150 → WARNING."""
    bd = {"cgst": 50.0, "sgst": 50.0, "igst": 0.0, "vat": 0.0}
    norm = _normalized(tax_amount="150.00", tax_breakdown=bd)
    result = ReconciliationValidatorService.validate(norm)
    issue = next((i for i in result.issues if i.issue_code == "TAX_BREAKDOWN_MISMATCH"), None)
    assert issue is not None
    assert issue.severity == "WARNING"


def test_tax_breakdown_all_zeros_skipped():
    """All-zero breakdown is skipped, not flagged as mismatch."""
    bd = {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "vat": 0.0}
    norm = _normalized(tax_amount="150.00", tax_breakdown=bd)
    result = ReconciliationValidatorService.validate(norm)
    assert not any(i.issue_code == "TAX_BREAKDOWN_MISMATCH" for i in result.issues)


# ── Tests: TAX_PCT_CHECK ──────────────────────────────────────────────────────

def test_tax_pct_inconsistent_is_info():
    """subtotal=850, tax=150 → computed=17.65%, stated=25% → INFO."""
    norm = _normalized(subtotal="850.00", tax_amount="150.00", total_amount="1000.00", tax_percentage="25.00")
    result = ReconciliationValidatorService.validate(norm)
    issue = next((i for i in result.issues if i.issue_code == "TAX_PCT_INCONSISTENT"), None)
    assert issue is not None
    assert issue.severity == "INFO"


# ── Tests: missing fields → checks skipped ───────────────────────────────────

def test_missing_subtotal_skips_total_check():
    norm = _normalized(subtotal=None)
    result = ReconciliationValidatorService.validate(norm)
    assert not any(i.issue_code == "TOTAL_MISMATCH" for i in result.issues)


def test_no_line_items_skips_line_checks():
    norm = _normalized(line_items=[])
    result = ReconciliationValidatorService.validate(norm)
    assert not any(i.issue_code in ("LINE_SUM_MISMATCH", "LINE_MATH_MISMATCH") for i in result.issues)


# ── Tests: fail-silent ───────────────────────────────────────────────────────

def test_fail_silent_on_none_input():
    result = ReconciliationValidatorService.validate(None)
    assert isinstance(result, ReconciliationValidationResult)
    assert result.is_clean is True
    assert len(result.issues) == 0


# ── Tests: to_serializable ───────────────────────────────────────────────────

def test_to_serializable_structure():
    norm = _normalized()
    result = ReconciliationValidatorService.validate(norm)
    serialized = ReconciliationValidatorService.to_serializable(result)
    assert "is_clean" in serialized
    assert "issues" in serialized
    assert "checks_run" in serialized
    assert isinstance(serialized["issues"], list)


def test_to_serializable_issue_fields():
    norm = _normalized(subtotal="850.00", tax_amount="150.00", total_amount="1100.00")
    result = ReconciliationValidatorService.validate(norm)
    serialized = ReconciliationValidatorService.to_serializable(result)
    assert len(serialized["issues"]) >= 1
    issue = serialized["issues"][0]
    for key in ("check_name", "issue_code", "severity", "message"):
        assert key in issue
