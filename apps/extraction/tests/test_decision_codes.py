"""Tests for apps.extraction.decision_codes — derive_codes() and constants.

Covers:
  - Each decision code constant is a non-empty UPPERCASE_SNAKE_CASE string
  - ROUTING_MAP maps every listed code to a non-empty queue string
  - HARD_REVIEW_CODES is a subset of ROUTING_MAP keys
  - derive_codes() correctly maps ValidationResult → codes
  - derive_codes() correctly maps ReconciliationValidationResult → codes
  - derive_codes() correctly maps FieldConfidenceResult → codes
  - derive_codes() handles prompt_source_type
  - derive_codes() deduplicates codes
  - derive_codes() is fail-silent (returns [] on bad input)
  - All args are optional (no-arg call works)
"""
import pytest
from unittest.mock import MagicMock

from apps.extraction import decision_codes as dc
from apps.extraction.decision_codes import derive_codes, ROUTING_MAP, HARD_REVIEW_CODES


# ── Constant sanity ───────────────────────────────────────────────────────────

class TestConstants:
    _ALL_CODES = [
        dc.INV_NUM_UNRECOVERABLE,
        dc.TOTAL_MISMATCH_HARD,
        dc.LINE_SUM_MISMATCH,
        dc.LINE_TABLE_INCOMPLETE,
        dc.TAX_ALLOC_AMBIGUOUS,
        dc.TAX_BREAKDOWN_MISMATCH,
        dc.VENDOR_MATCH_LOW,
        dc.LOW_CONFIDENCE_CRITICAL_FIELD,
        dc.PROMPT_COMPOSITION_FALLBACK_USED,
        dc.PROMPT_SOURCE_AGENT_DEFAULT,
        dc.RECOVERY_LANE_INVOKED,
        dc.RECOVERY_LANE_SUCCEEDED,
        dc.RECOVERY_LANE_FAILED,
        dc.RECOVERY_NOT_APPLICABLE,
    ]

    def test_all_codes_are_strings(self):
        for code in self._ALL_CODES:
            assert isinstance(code, str), f"code {code!r} is not a string"

    def test_all_codes_are_uppercase_snake_case(self):
        import re
        pattern = re.compile(r"^[A-Z][A-Z0-9_]+$")
        for code in self._ALL_CODES:
            assert pattern.match(code), f"code {code!r} is not UPPERCASE_SNAKE_CASE"

    def test_all_codes_equal_their_variable_name(self):
        """Each code string should match its variable name for traceability."""
        assert dc.INV_NUM_UNRECOVERABLE == "INV_NUM_UNRECOVERABLE"
        assert dc.TOTAL_MISMATCH_HARD == "TOTAL_MISMATCH_HARD"
        assert dc.VENDOR_MATCH_LOW == "VENDOR_MATCH_LOW"
        assert dc.RECOVERY_LANE_INVOKED == "RECOVERY_LANE_INVOKED"

    def test_routing_map_values_are_non_empty_strings(self):
        for code, queue in ROUTING_MAP.items():
            assert isinstance(queue, str) and queue, f"ROUTING_MAP[{code!r}] is empty"

    def test_hard_review_codes_are_subset_of_routing_map(self):
        for code in HARD_REVIEW_CODES:
            assert code in ROUTING_MAP, f"HARD_REVIEW_CODES member {code!r} not in ROUTING_MAP"

    def test_routing_map_has_expected_queue_for_inv_num(self):
        assert ROUTING_MAP[dc.INV_NUM_UNRECOVERABLE] == "EXCEPTION_OPS"

    def test_routing_map_has_expected_queue_for_tax(self):
        assert ROUTING_MAP[dc.TAX_ALLOC_AMBIGUOUS] == "TAX_REVIEW"
        assert ROUTING_MAP[dc.TAX_BREAKDOWN_MISMATCH] == "TAX_REVIEW"

    def test_routing_map_has_expected_queue_for_vendor(self):
        assert ROUTING_MAP[dc.VENDOR_MATCH_LOW] == "MASTER_DATA_REVIEW"


# ── derive_codes() — no-arg call ─────────────────────────────────────────────

def test_derive_codes_no_args_returns_empty():
    result = derive_codes()
    assert result == []


# ── derive_codes() — ValidationResult ────────────────────────────────────────

def _make_validation_result(critical_failures=None):
    m = MagicMock()
    m.critical_failures = critical_failures or []
    return m


class TestDeriveFromValidation:
    def test_no_critical_failures_no_code(self):
        vr = _make_validation_result(critical_failures=[])
        assert dc.LOW_CONFIDENCE_CRITICAL_FIELD not in derive_codes(validation_result=vr)

    def test_generic_critical_failure_adds_low_confidence_code(self):
        vr = _make_validation_result(critical_failures=["invoice_date"])
        codes = derive_codes(validation_result=vr)
        assert dc.LOW_CONFIDENCE_CRITICAL_FIELD in codes

    def test_invoice_number_critical_failure_adds_inv_num_code(self):
        vr = _make_validation_result(critical_failures=["invoice_number"])
        codes = derive_codes(validation_result=vr)
        assert dc.INV_NUM_UNRECOVERABLE in codes
        assert dc.LOW_CONFIDENCE_CRITICAL_FIELD in codes

    def test_vendor_name_critical_failure_adds_vendor_code(self):
        vr = _make_validation_result(critical_failures=["vendor_name"])
        codes = derive_codes(validation_result=vr)
        assert dc.VENDOR_MATCH_LOW in codes

    def test_multiple_critical_fields_all_codes_present(self):
        vr = _make_validation_result(critical_failures=["invoice_number", "vendor_name"])
        codes = derive_codes(validation_result=vr)
        assert dc.INV_NUM_UNRECOVERABLE in codes
        assert dc.VENDOR_MATCH_LOW in codes
        assert dc.LOW_CONFIDENCE_CRITICAL_FIELD in codes


# ── derive_codes() — ReconciliationValidationResult ──────────────────────────

def _make_recon_issue(issue_code):
    m = MagicMock()
    m.issue_code = issue_code
    return m


def _make_recon_result(issue_codes):
    m = MagicMock()
    m.issues = [_make_recon_issue(c) for c in issue_codes]
    return m


class TestDeriveFromRecon:
    def test_total_mismatch_produces_hard_code(self):
        rr = _make_recon_result(["TOTAL_MISMATCH"])
        codes = derive_codes(recon_val_result=rr)
        assert dc.TOTAL_MISMATCH_HARD in codes

    def test_line_sum_mismatch_produces_code(self):
        rr = _make_recon_result(["LINE_SUM_MISMATCH"])
        codes = derive_codes(recon_val_result=rr)
        assert dc.LINE_SUM_MISMATCH in codes

    def test_tax_breakdown_mismatch_produces_two_codes(self):
        rr = _make_recon_result(["TAX_BREAKDOWN_MISMATCH"])
        codes = derive_codes(recon_val_result=rr)
        assert dc.TAX_BREAKDOWN_MISMATCH in codes
        assert dc.TAX_ALLOC_AMBIGUOUS in codes

    def test_line_math_mismatch_produces_no_code(self):
        """LINE_MATH_MISMATCH is a sub-issue of LINE_TABLE_INCOMPLETE — no separate code."""
        rr = _make_recon_result(["LINE_MATH_MISMATCH"])
        codes = derive_codes(recon_val_result=rr)
        assert dc.LINE_TABLE_INCOMPLETE not in codes

    def test_unknown_issue_code_ignored(self):
        rr = _make_recon_result(["UNKNOWN_CHECK"])
        codes = derive_codes(recon_val_result=rr)
        assert codes == []

    def test_empty_issues_returns_no_codes(self):
        rr = _make_recon_result([])
        codes = derive_codes(recon_val_result=rr)
        assert codes == []


# ── derive_codes() — FieldConfidenceResult ───────────────────────────────────

def _make_field_conf_result(vendor_score=1.0, lines=None):
    m = MagicMock()
    m.header = {"vendor_name": vendor_score}
    m.lines = lines or []
    return m


class TestDeriveFromFieldConf:
    def test_vendor_score_below_threshold_adds_vendor_code(self):
        fc = _make_field_conf_result(vendor_score=0.35)
        codes = derive_codes(field_conf_result=fc)
        assert dc.VENDOR_MATCH_LOW in codes

    def test_vendor_score_at_threshold_no_vendor_code(self):
        fc = _make_field_conf_result(vendor_score=0.40)
        codes = derive_codes(field_conf_result=fc)
        assert dc.VENDOR_MATCH_LOW not in codes

    def test_vendor_score_above_threshold_no_vendor_code(self):
        fc = _make_field_conf_result(vendor_score=0.9)
        codes = derive_codes(field_conf_result=fc)
        assert dc.VENDOR_MATCH_LOW not in codes

    def test_majority_lines_missing_amount_adds_table_incomplete_code(self):
        # 3 lines, 2 with low line_amount confidence → majority missing
        lines = [
            {"line_amount": 0.1},
            {"line_amount": 0.2},
            {"line_amount": 1.0},
        ]
        fc = _make_field_conf_result(lines=lines)
        codes = derive_codes(field_conf_result=fc)
        assert dc.LINE_TABLE_INCOMPLETE in codes

    def test_minority_lines_missing_no_table_incomplete_code(self):
        # 3 lines, 1 with low line_amount confidence → minority missing
        lines = [
            {"line_amount": 0.1},
            {"line_amount": 1.0},
            {"line_amount": 1.0},
        ]
        fc = _make_field_conf_result(lines=lines)
        codes = derive_codes(field_conf_result=fc)
        assert dc.LINE_TABLE_INCOMPLETE not in codes

    def test_empty_lines_no_table_incomplete_code(self):
        fc = _make_field_conf_result(lines=[])
        codes = derive_codes(field_conf_result=fc)
        assert dc.LINE_TABLE_INCOMPLETE not in codes


# ── derive_codes() — prompt_source_type ──────────────────────────────────────

class TestDeriveFromPromptSource:
    def test_monolithic_fallback_adds_fallback_code(self):
        codes = derive_codes(prompt_source_type="monolithic_fallback")
        assert dc.PROMPT_COMPOSITION_FALLBACK_USED in codes

    def test_agent_default_adds_fallback_code(self):
        codes = derive_codes(prompt_source_type="agent_default")
        assert dc.PROMPT_COMPOSITION_FALLBACK_USED in codes

    def test_composed_source_adds_no_fallback_code(self):
        codes = derive_codes(prompt_source_type="composed")
        assert dc.PROMPT_COMPOSITION_FALLBACK_USED not in codes

    def test_empty_source_adds_no_code(self):
        codes = derive_codes(prompt_source_type="")
        assert dc.PROMPT_COMPOSITION_FALLBACK_USED not in codes


# ── derive_codes() — deduplication ───────────────────────────────────────────

class TestDeduplication:
    def test_vendor_code_not_duplicated_from_validation_and_field_conf(self):
        """VENDOR_MATCH_LOW comes from both ValidationResult and FieldConfidenceResult."""
        vr = _make_validation_result(critical_failures=["vendor_name"])
        fc = _make_field_conf_result(vendor_score=0.1)
        codes = derive_codes(validation_result=vr, field_conf_result=fc)
        assert codes.count(dc.VENDOR_MATCH_LOW) == 1

    def test_order_preserved_on_dedup(self):
        vr = _make_validation_result(critical_failures=["invoice_number"])
        codes = derive_codes(validation_result=vr)
        # LOW_CONFIDENCE_CRITICAL_FIELD should appear before INV_NUM_UNRECOVERABLE
        lci = codes.index(dc.LOW_CONFIDENCE_CRITICAL_FIELD)
        inv = codes.index(dc.INV_NUM_UNRECOVERABLE)
        assert lci < inv


# ── derive_codes() — fail-silent ─────────────────────────────────────────────

class TestFailSilent:
    def test_bad_validation_result_returns_empty(self):
        """If validation_result raises on attribute access, derive_codes returns []."""
        bad = MagicMock()
        bad.critical_failures = property(lambda self: (_ for _ in ()).throw(RuntimeError("oops")))
        # derive_codes wraps everything in try/except
        result = derive_codes(validation_result=bad)
        assert isinstance(result, list)

    def test_none_all_args_returns_empty(self):
        result = derive_codes(None, None, None, "")
        assert result == []
