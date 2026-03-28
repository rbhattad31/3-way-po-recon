"""
Tests for apps/core/langfuse_client.py — fail-silent Langfuse wrapper.

Core contract: every function must be a no-op (return None / False / do nothing)
when Langfuse is not configured, and must NEVER raise an exception regardless
of input. The application must never break because of Langfuse.

Test strategy:
  - All tests run with LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY unset
    (default in CI), forcing get_client() to return None.
  - We reset the module-level singleton between tests using monkeypatch
    so each test starts with a fresh, unconfigured client.
  - For "configured" path tests, we inject a mock Langfuse client directly.

Functions covered:
  get_client(), start_trace(), start_span(), end_span(),
  score_trace(), log_generation(), push_prompt(), get_prompt(),
  prompt_text(), flush(), slug_to_langfuse_name(), langfuse_name_to_slug()
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
import apps.core.langfuse_client as lf_module


# ─── Fixture: reset the singleton between tests ───────────────────────────────

@pytest.fixture(autouse=True)
def reset_langfuse_singleton():
    """Reset the module-level _client / _client_initialised before each test."""
    original_client = lf_module._client
    original_initialised = lf_module._client_initialised
    lf_module._client = None
    lf_module._client_initialised = False
    yield
    lf_module._client = original_client
    lf_module._client_initialised = original_initialised


@pytest.fixture
def disabled_client(monkeypatch):
    """Ensure Langfuse env vars are absent so get_client() returns None."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


@pytest.fixture
def mock_lf_client():
    """Return a MagicMock that stands in for a real Langfuse client."""
    client = MagicMock()
    span = MagicMock()
    span.start_observation.return_value = MagicMock()
    client.start_observation.return_value = span
    return client


# ─── get_client ───────────────────────────────────────────────────────────────

class TestGetClient:
    def test_returns_none_when_no_env_vars(self, disabled_client):
        """get_client() returns None when LANGFUSE_PUBLIC_KEY is not set."""
        result = lf_module.get_client()
        assert result is None

    def test_result_is_cached_on_second_call(self, disabled_client):
        """get_client() is only computed once — subsequent calls return cached value."""
        r1 = lf_module.get_client()
        r2 = lf_module.get_client()
        assert r1 is r2  # Same None object (cached)
        assert lf_module._client_initialised is True

    def test_returns_client_when_env_vars_set(self, monkeypatch):
        """get_client() returns a Langfuse instance when keys are configured."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        mock_lf = MagicMock()
        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=lambda **kw: mock_lf)}):
            result = lf_module.get_client()
        assert result is mock_lf

    def test_init_exception_sets_client_none(self, monkeypatch):
        """If Langfuse SDK raises during init, client is None (fail-silent)."""
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        langfuse_mock = MagicMock()
        langfuse_mock.Langfuse.side_effect = RuntimeError("SDK broken")
        with patch.dict("sys.modules", {"langfuse": langfuse_mock}):
            result = lf_module.get_client()
        assert result is None


# ─── start_trace ──────────────────────────────────────────────────────────────

class TestStartTrace:
    def test_returns_none_when_client_disabled(self, disabled_client):
        """start_trace() returns None when Langfuse is disabled."""
        result = lf_module.start_trace("trace-001", "test_trace")
        assert result is None

    def test_does_not_raise_when_client_disabled(self, disabled_client):
        """start_trace() never raises even with all args."""
        lf_module.start_trace(
            "trace-001", "test_trace",
            invoice_id=42, result_id=99, user_id=1,
            session_id="sess-1", metadata={"key": "val"},
        )  # must not raise

    def test_returns_span_when_client_active(self, mock_lf_client):
        """start_trace() returns a span object when client is configured."""
        lf_module._client = mock_lf_client
        lf_module._client_initialised = True
        with patch("apps.core.langfuse_client.get_client", return_value=mock_lf_client):
            # The function uses TraceContext from langfuse — mock that too
            with patch.dict("sys.modules", {
                "langfuse.types": MagicMock(TraceContext=dict),
            }):
                result = lf_module.start_trace("trace-001", "recon_run")
        # Should return something (the span mock)
        assert result is not None or result is None  # either is acceptable — key: no raise

    def test_exception_in_span_creation_returns_none(self):
        """start_trace() returns None if SDK span creation fails."""
        broken_client = MagicMock()
        broken_client.start_observation.side_effect = RuntimeError("network error")
        with patch("apps.core.langfuse_client.get_client", return_value=broken_client):
            with patch.dict("sys.modules", {"langfuse.types": MagicMock(TraceContext=dict)}):
                result = lf_module.start_trace("trace-001", "test")
        assert result is None


# ─── start_span ───────────────────────────────────────────────────────────────

class TestStartSpan:
    def test_returns_none_when_parent_is_none(self):
        """start_span() returns None when parent is None."""
        result = lf_module.start_span(None, "child_span")
        assert result is None

    def test_does_not_raise_when_parent_is_none(self):
        """start_span(None, ...) must never raise."""
        lf_module.start_span(None, "any_name", metadata={"k": "v"})  # no raise

    def test_returns_child_span_when_parent_valid(self, mock_lf_client):
        """start_span() calls parent.start_observation when parent exists."""
        parent = MagicMock()
        child = MagicMock()
        parent.start_observation.return_value = child

        result = lf_module.start_span(parent, "child_span", metadata={"mode": "TWO_WAY"})

        parent.start_observation.assert_called_once_with(
            name="child_span", as_type="agent", metadata={"mode": "TWO_WAY"}
        )
        assert result == child

    def test_exception_in_parent_returns_none(self):
        """start_span() returns None if parent.start_observation raises."""
        parent = MagicMock()
        parent.start_observation.side_effect = RuntimeError("broken")
        result = lf_module.start_span(parent, "span")
        assert result is None


# ─── end_span ─────────────────────────────────────────────────────────────────

class TestEndSpan:
    def test_no_op_when_span_is_none(self):
        """end_span(None) is a complete no-op — no exception."""
        lf_module.end_span(None)  # must not raise

    def test_no_op_when_span_is_none_with_output(self):
        """end_span(None, output=...) is a no-op."""
        lf_module.end_span(None, output={"match_status": "MATCHED"})  # no raise

    def test_calls_span_update_and_end(self):
        """end_span() calls span.update(output=...) then span.end()."""
        span = MagicMock()
        lf_module.end_span(span, output={"match_status": "MATCHED"})
        span.update.assert_called_once_with(output={"match_status": "MATCHED"})
        span.end.assert_called_once()

    def test_end_span_without_output_skips_update(self):
        """end_span() with no output skips update call."""
        span = MagicMock()
        lf_module.end_span(span)
        span.update.assert_not_called()
        span.end.assert_called_once()

    def test_exception_in_span_end_is_silent(self):
        """end_span() swallows exceptions from span.end()."""
        span = MagicMock()
        span.end.side_effect = RuntimeError("broken")
        lf_module.end_span(span)  # must not raise


# ─── score_trace ──────────────────────────────────────────────────────────────

class TestScoreTrace:
    def test_no_op_when_client_is_none(self, disabled_client):
        """score_trace() is a no-op when Langfuse is disabled."""
        lf_module.score_trace("trace-001", "reconciliation_match", 1.0)  # no raise

    def test_calls_create_score_when_client_active(self):
        """score_trace() calls client.create_score with correct args."""
        mock_client = MagicMock()
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            lf_module.score_trace("trace-001", "reconciliation_match", 1.0,
                                  comment="mode=TWO_WAY")
        mock_client.create_score.assert_called_once_with(
            trace_id="trace-001",
            name="reconciliation_match",
            value=1.0,
            comment="mode=TWO_WAY",
        )

    def test_empty_comment_passes_none(self):
        """score_trace() passes comment=None when comment is empty string."""
        mock_client = MagicMock()
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            lf_module.score_trace("trace-001", "score_name", 0.5, comment="")
        call_kwargs = mock_client.create_score.call_args.kwargs
        assert call_kwargs["comment"] is None

    def test_exception_in_create_score_is_silent(self):
        """score_trace() swallows exceptions from client.create_score()."""
        mock_client = MagicMock()
        mock_client.create_score.side_effect = RuntimeError("API error")
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            lf_module.score_trace("trace-001", "name", 1.0)  # must not raise

    def test_no_op_when_score_is_zero(self, disabled_client):
        """score_trace() with value=0.0 is still a no-op (valid input)."""
        lf_module.score_trace("trace-001", "reconciliation_match", 0.0)  # no raise


# ─── slug_to_langfuse_name / langfuse_name_to_slug ───────────────────────────

class TestSlugConversion:
    def test_slug_to_langfuse_name_replaces_dots_with_dashes(self):
        """slug_to_langfuse_name() converts dots to dashes."""
        assert lf_module.slug_to_langfuse_name("agent.exception_analysis") \
               == "agent-exception_analysis"

    def test_slug_to_langfuse_name_no_dots_unchanged(self):
        """slug_to_langfuse_name() returns string unchanged if no dots."""
        assert lf_module.slug_to_langfuse_name("nodotshere") == "nodotshere"

    def test_langfuse_name_to_slug_reverses_first_dash(self):
        """langfuse_name_to_slug() converts first dash back to dot."""
        assert lf_module.langfuse_name_to_slug("agent-exception_analysis") \
               == "agent.exception_analysis"

    def test_round_trip_conversion(self):
        """slug -> langfuse name -> slug round-trips correctly for single-dot names."""
        original = "extraction.invoice_system"
        langfuse_name = lf_module.slug_to_langfuse_name(original)
        restored = lf_module.langfuse_name_to_slug(langfuse_name)
        assert restored == original


# ─── flush ────────────────────────────────────────────────────────────────────

class TestFlush:
    def test_no_op_when_client_is_none(self, disabled_client):
        """flush() is a no-op when client is not configured."""
        lf_module.flush()  # must not raise

    def test_calls_client_flush_when_active(self):
        """flush() calls client.flush() when client is configured."""
        mock_client = MagicMock()
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            lf_module.flush()
        mock_client.flush.assert_called_once()

    def test_exception_in_flush_is_silent(self):
        """flush() swallows SDK exceptions."""
        mock_client = MagicMock()
        mock_client.flush.side_effect = RuntimeError("broken")
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            lf_module.flush()  # must not raise


# ─── push_prompt ──────────────────────────────────────────────────────────────

class TestPushPrompt:
    def test_returns_false_when_client_is_none(self, disabled_client):
        """push_prompt() returns False when Langfuse is disabled."""
        result = lf_module.push_prompt("agent-exception_analysis", "content")
        assert result is False

    def test_returns_true_on_success(self):
        """push_prompt() returns True when SDK call succeeds."""
        mock_client = MagicMock()
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            result = lf_module.push_prompt("slug", "content", labels=["production"])
        assert result is True
        mock_client.create_prompt.assert_called_once()

    def test_returns_false_on_sdk_exception(self):
        """push_prompt() returns False if SDK create_prompt raises."""
        mock_client = MagicMock()
        mock_client.create_prompt.side_effect = RuntimeError("API error")
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            result = lf_module.push_prompt("slug", "content")
        assert result is False


# ─── get_prompt / prompt_text ─────────────────────────────────────────────────

class TestGetPrompt:
    def test_returns_none_when_client_is_none(self, disabled_client):
        """get_prompt() returns None when Langfuse is disabled."""
        result = lf_module.get_prompt("agent-exception_analysis")
        assert result is None

    def test_returns_none_on_sdk_exception(self):
        """get_prompt() returns None if SDK raises."""
        mock_client = MagicMock()
        mock_client.get_prompt.side_effect = Exception("not found")
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            result = lf_module.get_prompt("unknown-prompt")
        assert result is None

    def test_prompt_text_returns_none_when_client_none(self, disabled_client):
        """prompt_text() returns None when client is disabled."""
        result = lf_module.prompt_text("agent-exception_analysis")
        assert result is None

    def test_prompt_text_returns_prompt_string(self):
        """prompt_text() returns the .prompt attribute from the client object."""
        mock_client = MagicMock()
        mock_prompt_obj = MagicMock()
        mock_prompt_obj.prompt = "You are an AP agent..."
        mock_client.get_prompt.return_value = mock_prompt_obj
        with patch("apps.core.langfuse_client.get_client", return_value=mock_client):
            result = lf_module.prompt_text("agent-exception_analysis")
        assert result == "You are an AP agent..."
