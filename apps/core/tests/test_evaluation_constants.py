"""
Tests for apps/core/evaluation_constants.py -- centralized score name taxonomy.

Validates:
  - All constants are non-empty strings
  - No duplicate score name values across the entire module
  - Naming convention: constant names are UPPER_CASE, values are lower_snake_case
  - All expected domain groups are present (extraction, recon, agent, case, review,
    posting, erp, cross-cutting)
  - Latency thresholds are positive integers
"""
from __future__ import annotations

import re

import pytest

import apps.core.evaluation_constants as ec


# ---- Helpers ----------------------------------------------------------------

def _all_score_constants() -> dict[str, str]:
    """Return {CONSTANT_NAME: value} for every uppercase string constant."""
    return {
        name: getattr(ec, name)
        for name in dir(ec)
        if name.isupper()
        and isinstance(getattr(ec, name), str)
        and not name.startswith("_")
    }


def _all_threshold_constants() -> dict[str, int]:
    """Return {CONSTANT_NAME: value} for LATENCY_THRESHOLD_* integer constants."""
    return {
        name: getattr(ec, name)
        for name in dir(ec)
        if name.startswith("LATENCY_THRESHOLD_")
        and isinstance(getattr(ec, name), (int, float))
    }


# ---- No duplicate values ----------------------------------------------------

class TestNoDuplicateValues:
    # Intentional aliases (backward compat) -- same value is expected
    _KNOWN_ALIASES = {
        ("POSTING_FINAL_REQUIRES_REVIEW", "POSTING_REQUIRES_REVIEW"),
    }

    def test_all_score_values_are_unique(self):
        """Every score name constant must map to a unique string value
        (known backward-compat aliases are excluded)."""
        alias_names = set()
        for pair in self._KNOWN_ALIASES:
            alias_names.update(pair)

        consts = _all_score_constants()
        seen: dict[str, str] = {}
        duplicates = []
        for name, value in consts.items():
            if name in alias_names:
                continue
            if value in seen:
                duplicates.append(f"{name} and {seen[value]} both = '{value}'")
            else:
                seen[value] = name
        assert not duplicates, f"Duplicate score values found:\n" + "\n".join(duplicates)


# ---- Value format ------------------------------------------------------------

class TestValueFormat:
    def test_all_values_are_nonempty_strings(self):
        """Every score constant must be a non-empty string."""
        for name, value in _all_score_constants().items():
            assert isinstance(value, str), f"{name} is not a string"
            assert len(value) > 0, f"{name} is empty"

    def test_values_are_lowercase_with_underscores(self):
        """Score name values should be lowercase with underscores only."""
        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for name, value in _all_score_constants().items():
            assert pattern.match(value), (
                f"{name} = '{value}' does not match lowercase_underscore convention"
            )


# ---- Domain groups present ---------------------------------------------------

class TestDomainCoverage:
    """At least one constant must exist per expected domain prefix."""

    @pytest.mark.parametrize("prefix", [
        "EXTRACTION_",
        "RECON_",
        "AGENT_",
        "CASE_",
        "REVIEW_",
        "POSTING_",
        "ERP_",
    ])
    def test_domain_prefix_has_constants(self, prefix):
        matching = [n for n in dir(ec) if n.startswith(prefix) and n.isupper()]
        assert len(matching) >= 1, f"No constants with prefix {prefix}"


# ---- Cross-cutting constants -------------------------------------------------

class TestCrossCuttingConstants:
    def test_rbac_guardrail_exists(self):
        assert ec.RBAC_GUARDRAIL == "rbac_guardrail"

    def test_rbac_data_scope_exists(self):
        assert ec.RBAC_DATA_SCOPE == "rbac_data_scope"

    def test_copilot_session_length_exists(self):
        assert ec.COPILOT_SESSION_LENGTH == "copilot_session_length"

    def test_latency_ok_exists(self):
        assert ec.LATENCY_OK == "latency_ok"

    def test_fallback_used_exists(self):
        assert ec.FALLBACK_USED == "fallback_used"


# ---- Latency thresholds -----------------------------------------------------

class TestLatencyThresholds:
    def test_all_thresholds_are_positive(self):
        for name, value in _all_threshold_constants().items():
            assert value > 0, f"{name} = {value} is not positive"

    def test_erp_threshold_exists(self):
        assert ec.LATENCY_THRESHOLD_ERP_MS == 5000

    def test_llm_threshold_exists(self):
        assert ec.LATENCY_THRESHOLD_LLM_MS == 20000

    def test_ocr_threshold_exists(self):
        assert ec.LATENCY_THRESHOLD_OCR_MS == 30000

    def test_db_threshold_exists(self):
        assert ec.LATENCY_THRESHOLD_DB_MS == 2000


# ---- Spot-check well-known values -------------------------------------------

class TestWellKnownValues:
    """Spot-check critical score name values that external systems may depend on."""

    def test_recon_reconciliation_match(self):
        assert ec.RECON_RECONCILIATION_MATCH == "reconciliation_match"

    def test_extraction_confidence(self):
        assert ec.EXTRACTION_CONFIDENCE == "extraction_confidence"

    def test_agent_confidence(self):
        assert ec.AGENT_CONFIDENCE == "agent_confidence"

    def test_posting_confidence(self):
        assert ec.POSTING_FINAL_CONFIDENCE == "posting_confidence"

    def test_review_decision(self):
        assert ec.REVIEW_DECISION == "review_decision"

    def test_erp_resolution_success(self):
        assert ec.ERP_RESOLUTION_SUCCESS == "erp_resolution_success"

    def test_erp_submission_success(self):
        assert ec.ERP_SUBMISSION_SUCCESS == "erp_submission_success"

    def test_case_processing_success(self):
        assert ec.CASE_PROCESSING_SUCCESS == "case_processing_success"


# ---- Root trace name constants -----------------------------------------------

class TestRootTraceNames:
    """Verify TRACE_* constants exist for pipeline root traces."""

    def test_trace_constants_present(self):
        trace_names = [n for n in dir(ec) if n.startswith("TRACE_") and n.isupper()]
        assert len(trace_names) >= 1, "No TRACE_* root trace name constants found"
