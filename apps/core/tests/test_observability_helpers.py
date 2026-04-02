"""
Tests for apps/core/observability_helpers.py -- cross-flow correlation helpers.

Covers:
  - derive_session_id: priority-based session_id derivation
  - build_observability_context: cross-linking metadata dict
  - merge_trace_metadata: dict merging with None filtering
  - sanitize_langfuse_metadata: PII redaction and truncation
  - sanitize_summary_text: non-ASCII stripping for LLM output
  - latency_ok: threshold comparison
  - score_latency: fail-silent latency score emission
  - Eval metadata builders: extraction, recon, agent, case, posting, erp
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from apps.core.observability_helpers import (
    derive_session_id,
    build_observability_context,
    merge_trace_metadata,
    sanitize_langfuse_metadata,
    sanitize_summary_text,
    latency_ok,
    score_latency,
    build_extraction_eval_metadata,
    build_recon_eval_metadata,
    build_agent_eval_metadata,
    build_case_eval_metadata,
    build_posting_eval_metadata,
    build_erp_span_metadata,
)


# ---- derive_session_id -------------------------------------------------------

class TestDeriveSessionId:
    def test_invoice_id_takes_priority(self):
        result = derive_session_id(invoice_id=42, document_upload_id=10, case_id=5)
        assert result == "invoice-42"

    def test_upload_id_second_priority(self):
        result = derive_session_id(document_upload_id=10, case_id=5)
        assert result == "upload-10"

    def test_case_id_third_priority(self):
        result = derive_session_id(case_id=5)
        assert result == "case-5"

    def test_returns_none_when_nothing_provided(self):
        result = derive_session_id()
        assert result is None

    def test_zero_invoice_id_falls_through(self):
        """invoice_id=0 is falsy, so should fall through."""
        result = derive_session_id(invoice_id=0, case_id=3)
        assert result == "case-3"


# ---- build_observability_context ---------------------------------------------

class TestBuildObservabilityContext:
    def test_includes_provided_fields(self):
        ctx = build_observability_context(
            invoice_id=1,
            po_number="PO-100",
            reconciliation_mode="TWO_WAY",
        )
        assert ctx["invoice_id"] == 1
        assert ctx["po_number"] == "PO-100"
        assert ctx["reconciliation_mode"] == "TWO_WAY"

    def test_excludes_none_values(self):
        ctx = build_observability_context(invoice_id=1, vendor_name=None)
        assert "vendor_name" not in ctx

    def test_excludes_empty_string_values(self):
        ctx = build_observability_context(invoice_id=1, vendor_name="")
        assert "vendor_name" not in ctx

    def test_returns_empty_dict_when_nothing_provided(self):
        ctx = build_observability_context()
        assert ctx == {}

    def test_all_fields_included_when_populated(self):
        ctx = build_observability_context(
            invoice_id=1,
            document_upload_id=2,
            extraction_result_id=3,
            extraction_run_id=4,
            reconciliation_result_id=5,
            reconciliation_run_id=6,
            case_id=7,
            case_number="CASE-001",
            posting_run_id=8,
            actor_user_id=9,
            trigger="manual",
            po_number="PO-100",
            vendor_code="V001",
            vendor_name="Acme",
            reconciliation_mode="TWO_WAY",
            match_status="MATCHED",
            case_stage="MATCHING",
            posting_stage="MAPPING",
            source="test",
        )
        assert len(ctx) == 19


# ---- merge_trace_metadata ----------------------------------------------------

class TestMergeTraceMetadata:
    def test_merges_two_dicts(self):
        result = merge_trace_metadata({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_later_dict_wins_on_conflict(self):
        result = merge_trace_metadata({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_filters_none_values(self):
        result = merge_trace_metadata({"a": 1}, {"b": None})
        assert result == {"a": 1}

    def test_handles_none_base(self):
        result = merge_trace_metadata(None, {"b": 2})
        assert result == {"b": 2}

    def test_handles_empty_extras(self):
        result = merge_trace_metadata({"a": 1})
        assert result == {"a": 1}


# ---- sanitize_langfuse_metadata ----------------------------------------------

class TestSanitizeLangfuseMetadata:
    def test_strips_sensitive_keys(self):
        meta = {"api_key": "secret123", "invoice_id": 1}
        result = sanitize_langfuse_metadata(meta)
        assert "api_key" not in result
        assert result["invoice_id"] == 1

    def test_strips_password_key(self):
        meta = {"password": "hunter2", "status": "ok"}
        result = sanitize_langfuse_metadata(meta)
        assert "password" not in result

    def test_truncates_large_text_fields(self):
        meta = {"ocr_text": "x" * 5000}
        result = sanitize_langfuse_metadata(meta)
        assert len(result["ocr_text"]) < 5000
        assert "truncated" in result["ocr_text"]

    def test_truncates_long_strings(self):
        meta = {"description": "y" * 3000}
        result = sanitize_langfuse_metadata(meta)
        assert len(result["description"]) <= 2100  # 2000 + "... [truncated]"

    def test_truncates_large_lists(self):
        meta = {"items": list(range(100))}
        result = sanitize_langfuse_metadata(meta)
        assert len(result["items"]) == 51  # 50 items + truncated marker

    def test_handles_nested_dicts(self):
        meta = {"outer": {"api_key": "secret", "safe": True}}
        result = sanitize_langfuse_metadata(meta)
        assert "api_key" not in result["outer"]
        assert result["outer"]["safe"] is True

    def test_returns_empty_dict_for_none(self):
        assert sanitize_langfuse_metadata(None) == {}

    def test_returns_empty_dict_for_empty(self):
        assert sanitize_langfuse_metadata({}) == {}

    def test_never_raises_on_bad_input(self):
        """Even broken input should not raise."""
        result = sanitize_langfuse_metadata({"key": object()})
        assert isinstance(result, dict)


# ---- sanitize_summary_text ---------------------------------------------------

class TestSanitizeSummaryText:
    def test_strips_non_ascii(self):
        text = "Arrow -> next \u2192 fancy"
        result = sanitize_summary_text(text)
        assert "\u2192" not in result
        assert "Arrow -> next" in result

    def test_truncates_long_text(self):
        result = sanitize_summary_text("a" * 5000, max_length=100)
        assert len(result) <= 120  # 100 + "... [truncated]"

    def test_returns_empty_for_none(self):
        assert sanitize_summary_text(None) == ""

    def test_returns_empty_for_empty_string(self):
        assert sanitize_summary_text("") == ""

    def test_preserves_plain_ascii(self):
        text = "Simple ASCII text with numbers 123"
        assert sanitize_summary_text(text) == text


# ---- latency_ok --------------------------------------------------------------

class TestLatencyOk:
    def test_within_threshold_returns_1(self):
        assert latency_ok(100, 200) == 1.0

    def test_at_threshold_returns_1(self):
        assert latency_ok(200, 200) == 1.0

    def test_over_threshold_returns_0(self):
        assert latency_ok(201, 200) == 0.0

    def test_zero_latency(self):
        assert latency_ok(0, 100) == 1.0

    def test_handles_float_inputs(self):
        assert latency_ok(99.5, 100.0) == 1.0

    def test_handles_invalid_input(self):
        """Invalid input should return 0.0 (fail-silent)."""
        assert latency_ok("bad", 100) == 0.0


# ---- score_latency -----------------------------------------------------------

class TestScoreLatency:
    def test_no_op_when_observation_is_none(self):
        """score_latency(None, ...) should be a silent no-op."""
        score_latency(None, 100, 200)  # must not raise

    def test_calls_score_observation_safe(self):
        obs = MagicMock()
        with patch("apps.core.langfuse_client.score_observation_safe") as mock_score:
            score_latency(obs, 100, 200, score_name="test_latency")
            mock_score.assert_called_once()

    def test_never_raises_on_import_error(self):
        """Even if score_observation_safe import fails, must not raise."""
        obs = MagicMock()
        with patch(
            "apps.core.langfuse_client.score_observation_safe",
            side_effect=RuntimeError("broken"),
        ):
            score_latency(obs, 100, 200)  # must not raise


# ---- Eval metadata builders --------------------------------------------------

class TestBuildExtractionEvalMetadata:
    def test_includes_provided_fields(self):
        meta = build_extraction_eval_metadata(
            extraction_success=True,
            final_confidence=0.95,
        )
        assert meta["extraction_success"] is True
        assert meta["final_confidence"] == 0.95

    def test_excludes_none_fields(self):
        meta = build_extraction_eval_metadata()
        assert "prompt_source" not in meta


class TestBuildReconEvalMetadata:
    def test_includes_provided_fields(self):
        meta = build_recon_eval_metadata(
            po_found=True,
            reconciliation_mode="TWO_WAY",
            final_match_status="MATCHED",
        )
        assert meta["po_found"] is True
        assert meta["reconciliation_mode"] == "TWO_WAY"

    def test_defaults_are_sensible(self):
        meta = build_recon_eval_metadata()
        assert meta["po_found"] is False
        assert meta["exception_count"] == 0


class TestBuildAgentEvalMetadata:
    def test_includes_agents_list(self):
        meta = build_agent_eval_metadata(
            planned_agents=["exception_analysis", "po_retrieval"],
            executed_agents=["exception_analysis"],
        )
        assert len(meta["planned_agents"]) == 2
        assert len(meta["executed_agents"]) == 1


class TestBuildCaseEvalMetadata:
    def test_includes_case_fields(self):
        meta = build_case_eval_metadata(
            case_id=42,
            case_number="CASE-001",
            review_required=True,
        )
        assert meta["case_id"] == 42
        assert meta["review_required"] is True


class TestBuildPostingEvalMetadata:
    def test_includes_posting_fields(self):
        meta = build_posting_eval_metadata(
            is_touchless=True,
            issue_count=3,
        )
        assert meta["is_touchless"] is True
        assert meta["issue_count"] == 3


class TestBuildErpSpanMetadata:
    def test_includes_erp_fields(self):
        meta = build_erp_span_metadata(
            source_used="API",
            connector_type="dynamics",
            result_present=True,
        )
        assert meta["source_used"] == "API"
        assert meta["result_present"] is True

    def test_excludes_none_fields(self):
        meta = build_erp_span_metadata()
        assert "sanitized_error_type" not in meta


# ---- ERP error/source/freshness constants ------------------------------------

class TestErpConstants:
    def test_error_constants_are_strings(self):
        from apps.core.observability_helpers import (
            ERP_ERROR_TIMEOUT,
            ERP_ERROR_UNAUTHORIZED,
            ERP_ERROR_RATE_LIMITED,
            ERP_ERROR_UNKNOWN,
        )
        assert isinstance(ERP_ERROR_TIMEOUT, str)
        assert isinstance(ERP_ERROR_UNAUTHORIZED, str)
        assert isinstance(ERP_ERROR_RATE_LIMITED, str)
        assert isinstance(ERP_ERROR_UNKNOWN, str)

    def test_source_constants_are_strings(self):
        from apps.core.observability_helpers import (
            SOURCE_CACHE,
            SOURCE_LIVE_API,
            SOURCE_MIRROR_DB,
            SOURCE_DB_FALLBACK,
            SOURCE_NONE,
        )
        assert SOURCE_CACHE == "CACHE"
        assert SOURCE_LIVE_API == "API"
        assert SOURCE_MIRROR_DB == "MIRROR_DB"
        assert SOURCE_DB_FALLBACK == "DB_FALLBACK"
        assert SOURCE_NONE == "NONE"

    def test_freshness_constants_are_strings(self):
        from apps.core.observability_helpers import (
            FRESHNESS_FRESH,
            FRESHNESS_STALE,
            FRESHNESS_UNKNOWN,
        )
        assert FRESHNESS_FRESH == "fresh"
        assert FRESHNESS_STALE == "stale"
        assert FRESHNESS_UNKNOWN == "unknown"
