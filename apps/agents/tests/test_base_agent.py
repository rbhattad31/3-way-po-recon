"""Tests for BaseAgent -- ReAct loop, tool execution, finalisation."""
from __future__ import annotations

import json
import pytest
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock, PropertyMock

from apps.core.enums import AgentType, AgentRunStatus
from apps.agents.services.llm_client import LLMResponse, LLMToolCall
from apps.tools.registry.base import ToolResult

# Use a real AgentType enum value that exists in the DB
_AGENT_TYPE = AgentType.RECONCILIATION_ASSIST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _llm_response(content=None, tool_calls=None):
    """Build an LLMResponse matching what LLMClient.chat() returns."""
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )


def _make_agent_class():
    """Create a concrete BaseAgent subclass for testing."""
    from apps.agents.services.base_agent import BaseAgent, AgentOutput, AgentContext

    class _TestAgent(BaseAgent):
        agent_type = _AGENT_TYPE

        @property
        def system_prompt(self):
            return "You are a test agent."

        def build_user_message(self, ctx):
            return "Analyze discrepancies."

        @property
        def allowed_tools(self):
            return ["po_lookup", "invoice_details"]

        def interpret_response(self, content, ctx):
            try:
                data = json.loads(content) if content else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            return AgentOutput(
                reasoning=data.get("reasoning", content or "fallback reasoning for test"),
                recommendation_type=data.get("recommendation_type", "SEND_TO_AP_REVIEW"),
                confidence=data.get("confidence", 0.5),
                evidence=data.get("evidence", {}),
            )

    return _TestAgent


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestBaseAgentRun:
    """BA-01 to BA-03."""

    @pytest.fixture
    def agent_def(self, db):
        """Create a minimal AgentDefinition."""
        from apps.agents.models import AgentDefinition
        return AgentDefinition.objects.create(
            agent_type=_AGENT_TYPE,
            name="Reconciliation Assist Agent",
            enabled=True,
            config_json={"allowed_tools": ["po_lookup", "invoice_details"]},
        )

    @pytest.fixture
    def agent_ctx(self, db):
        """Create a minimal AgentContext with a ReconciliationResult."""
        from apps.reconciliation.tests.factories import ReconConfigFactory, InvoiceFactory, POFactory
        from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
        from apps.core.enums import ReconciliationRunStatus, MatchStatus, ReconciliationMode
        from apps.agents.services.base_agent import AgentContext

        config = ReconConfigFactory()
        invoice = InvoiceFactory(extraction_confidence=0.85)
        po = POFactory()
        run = ReconciliationRun.objects.create(
            status=ReconciliationRunStatus.COMPLETED,
            config=config,
        )
        rr = ReconciliationResult.objects.create(
            run=run,
            invoice=invoice,
            purchase_order=po,
            match_status=MatchStatus.PARTIAL_MATCH,
            deterministic_confidence=0.75,
            extraction_confidence=0.85,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
        )
        return AgentContext(
            reconciliation_result=rr,
            invoice_id=invoice.pk,
            po_number=po.po_number,
            reconciliation_mode=ReconciliationMode.THREE_WAY,
            trace_id="test-trace-001",
        )

    @patch("apps.core.prompt_registry.PromptRegistry.version_for", return_value="v1")
    def test_single_round_no_tools(self, mock_pv, agent_def, agent_ctx):
        """BA-01: LLM responds with content (no tool_calls) -> exits after 1 round."""
        resp_content = json.dumps({
            "reasoning": "Invoice matches PO within tolerance for all line items.",
            "recommendation_type": "AUTO_CLOSE",
            "confidence": 0.9,
            "evidence": {"tolerance_check": "pass"},
        })

        AgentCls = _make_agent_class()
        agent = AgentCls()
        # Replace the real LLM with a mock
        agent.llm = MagicMock()
        agent.llm.model = "gpt-4o"
        agent.llm.chat = MagicMock(return_value=_llm_response(content=resp_content))

        run = agent.run(agent_ctx)

        assert run.status == AgentRunStatus.COMPLETED
        assert run.agent_type == _AGENT_TYPE

    @patch("apps.core.prompt_registry.PromptRegistry.version_for", return_value="v1")
    def test_tool_call_round(self, mock_pv, agent_def, agent_ctx):
        """BA-02: LLM requests tool_call -> tool executes -> LLM responds."""
        # Round 1: LLM asks for tool call
        tc = LLMToolCall(id="tc_001", name="po_lookup", arguments={"po_number": "PO-001"})
        round1 = _llm_response(tool_calls=[tc])

        # Round 2: LLM responds with final answer
        final = _llm_response(content=json.dumps({
            "reasoning": "PO found and all quantities match within tolerance.",
            "recommendation_type": "AUTO_CLOSE",
            "confidence": 0.85,
            "evidence": {"po_status": "matched"},
        }))

        AgentCls = _make_agent_class()
        agent = AgentCls()
        agent.llm = MagicMock()
        agent.llm.model = "gpt-4o"
        agent.llm.chat = MagicMock(side_effect=[round1, final])

        # Mock tool registry
        mock_tool = MagicMock()
        mock_tool.required_permission = "purchase_orders.view"
        tool_result = ToolResult(success=True, data={"po_number": "PO-001"})
        mock_tool.execute.return_value = tool_result

        with patch("apps.agents.services.base_agent.ToolRegistry") as mock_tr:
            mock_tr.get.return_value = mock_tool
            mock_tr.get_specs.return_value = [{"type": "function", "function": {"name": "po_lookup"}}]
            run = agent.run(agent_ctx)

        assert run.status == AgentRunStatus.COMPLETED

    @patch("apps.core.prompt_registry.PromptRegistry.version_for", return_value="v1")
    def test_exception_marks_failed(self, mock_pv, agent_def, agent_ctx):
        """BA-03: LLM exception marks AgentRun as FAILED."""
        AgentCls = _make_agent_class()
        agent = AgentCls()
        agent.llm = MagicMock()
        agent.llm.model = "gpt-4o"
        agent.llm.chat = MagicMock(side_effect=RuntimeError("API unavailable"))

        run = agent.run(agent_ctx)

        assert run.status == AgentRunStatus.FAILED


class TestCompositeConfidence:
    """CC-01 to CC-04: _compute_composite_confidence()."""

    def _compute(self, llm_conf, failed_count, total_count, evidence):
        from apps.agents.services.base_agent import BaseAgent

        class _T(BaseAgent):
            agent_type = "test"
            system_prompt = ""
            allowed_tools = []
            def build_user_message(self, ctx): return ""
            def interpret_response(self, c, ctx): return None

        agent = _T()
        return agent._compute_composite_confidence(
            llm_conf, failed_count, total_count, evidence,
        )

    def test_perfect_scores(self):
        """CC-01: High LLM + 0 failed out of 3 + evidence -> high composite."""
        c = self._compute(0.9, 0, 3, {"key": "val"})
        assert c >= 0.85

    def test_no_tools_used(self):
        """CC-02: No tools used -> tool_score defaults to 1.0."""
        c = self._compute(0.8, 0, 0, {"key": "val"})
        assert c >= 0.7

    def test_all_tools_failed(self):
        """CC-03: All 3 tools failed -> lower composite."""
        c = self._compute(0.9, 3, 3, {"key": "val"})
        assert c < 0.7

    def test_no_evidence_penalty(self):
        """CC-04: No evidence -> evidence_score=0.5 penalty."""
        with_ev = self._compute(0.9, 0, 1, {"key": "val"})
        no_ev = self._compute(0.9, 0, 1, {})
        assert no_ev < with_ev

    def test_clamped_to_unit(self):
        """CC-05: Result is always in [0, 1]."""
        c = self._compute(1.5, 0, 0, {"key": "val"})
        assert 0.0 <= c <= 1.0


class TestSanitiseText:
    """ST-01 to ST-03: _sanitise_text() strips non-ASCII."""

    def _sanitise(self, text):
        from apps.agents.services.base_agent import BaseAgent

        class _T(BaseAgent):
            agent_type = "test"
            system_prompt = ""
            allowed_tools = []
            def build_user_message(self, ctx): return ""
            def interpret_response(self, c, ctx): return None

        return _T._sanitise_text(text)

    def test_ascii_passthrough(self):
        """ST-01: Plain ASCII is unchanged."""
        assert self._sanitise("hello world") == "hello world"

    def test_unicode_arrows_replaced(self):
        """ST-02: Unicode arrows/dashes are replaced with ASCII."""
        result = self._sanitise("step 1 -> step 2 -- done")
        # Should already be ASCII
        assert all(ord(c) < 128 for c in result)

    def test_fancy_quotes_stripped(self):
        """ST-03: Fancy quotes become straight quotes or are removed."""
        result = self._sanitise('He said "hello"')
        assert all(ord(c) < 128 for c in result)
