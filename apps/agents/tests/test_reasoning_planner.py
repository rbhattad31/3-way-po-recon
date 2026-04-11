"""Tests for ReasoningPlanner -- LLM-backed agent execution planner.

Covers:
  RP-01: LLM returns valid plan -> plan_source="llm"
  RP-02: LLM fails -> fallback to PolicyEngine (plan_source="deterministic")
  RP-03: LLM returns invalid JSON -> fallback
  RP-04: LLM returns no valid steps -> fallback
  RP-05: CASE_SUMMARY out of position -> fallback
  RP-06: GRN_RETRIEVAL in TWO_WAY -> fallback
  RP-07: skip_agents passthrough from PolicyEngine
  RP-08: auto_close passthrough from PolicyEngine
  RP-09: plan_confidence extracted correctly
  RP-10: Unknown agent types filtered out
  RP-11: should_auto_close delegates to PolicyEngine
  RP-12: should_escalate delegates to PolicyEngine
  RP-13: Steps sorted by priority
  RP-14: Orchestrator uses PolicyEngine when flag is disabled
  RP-15: Orchestrator uses ReasoningPlanner when flag is enabled
"""
from __future__ import annotations

import json
import pytest
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import patch, MagicMock, PropertyMock

from apps.agents.services.policy_engine import AgentPlan, PolicyEngine
from apps.agents.services.reasoning_planner import ReasoningPlanner, _VALID_AGENT_TYPES
from apps.core.enums import (
    AgentType,
    ExceptionSeverity,
    ExceptionType,
    MatchStatus,
    ReconciliationMode,
)

# Shorthand: the enum member is QTY_MISMATCH (not QUANTITY_MISMATCH).
_QTY_MISMATCH = ExceptionType.QTY_MISMATCH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def recon_result(db):
    """Create a minimal ReconciliationResult for planner tests."""
    from apps.reconciliation.tests.factories import (
        ReconConfigFactory,
        InvoiceFactory,
        POFactory,
    )
    from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
    from apps.core.enums import ReconciliationRunStatus

    config = ReconConfigFactory()
    invoice = InvoiceFactory(extraction_confidence=0.85)
    po = POFactory()
    run = ReconciliationRun.objects.create(
        status=ReconciliationRunStatus.RUNNING,
        config=config,
    )
    return ReconciliationResult.objects.create(
        run=run,
        invoice=invoice,
        purchase_order=po,
        match_status=MatchStatus.PARTIAL_MATCH,
        deterministic_confidence=0.75,
        extraction_confidence=0.85,
        reconciliation_mode=ReconciliationMode.THREE_WAY,
    )


def add_exception(result, exc_type, severity=ExceptionSeverity.MEDIUM):
    from apps.reconciliation.models import ReconciliationException
    return ReconciliationException.objects.create(
        result=result,
        exception_type=exc_type,
        severity=severity,
        message=f"Test exception: {exc_type}",
    )


def _make_llm_response(payload: dict) -> MagicMock:
    """Build a mock LLMResponse with JSON content."""
    resp = MagicMock()
    resp.content = json.dumps(payload)
    return resp


def _valid_llm_payload(
    agents: list[str] | None = None,
    confidence: float = 0.85,
    reasoning: str = "LLM reasoning for test",
) -> dict:
    """Build a valid LLM JSON payload with steps."""
    if agents is None:
        agents = [
            "RECONCILIATION_ASSIST",
            "EXCEPTION_ANALYSIS",
            "REVIEW_ROUTING",
            "CASE_SUMMARY",
        ]
    steps = [
        {"agent_type": a, "rationale": f"reason for {a}", "priority": i + 1}
        for i, a in enumerate(agents)
    ]
    return {
        "overall_reasoning": reasoning,
        "confidence": confidence,
        "steps": steps,
    }


# ===========================================================================
# RP-01 to RP-13: ReasoningPlanner unit tests
# ===========================================================================
@pytest.mark.django_db
class TestReasoningPlannerLLMPlan:
    """Tests that exercise _llm_plan parsing and validation."""

    def _make_planner(self, llm_return=None, llm_side_effect=None):
        with patch("apps.agents.services.reasoning_planner.LLMClient"):
            planner = ReasoningPlanner()
        planner._llm = MagicMock()
        if llm_side_effect:
            planner._llm.chat.side_effect = llm_side_effect
        elif llm_return is not None:
            planner._llm.chat.return_value = llm_return
        return planner

    # RP-01
    def test_valid_llm_plan(self, recon_result):
        """RP-01: Valid LLM response produces plan_source='llm'."""
        add_exception(recon_result, _QTY_MISMATCH)
        payload = _valid_llm_payload()
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_source == "llm"
        assert plan.plan_confidence == 0.85
        assert plan.agents == [
            "RECONCILIATION_ASSIST",
            "EXCEPTION_ANALYSIS",
            "REVIEW_ROUTING",
            "CASE_SUMMARY",
        ]
        assert plan.skip_agents is False

    # RP-02
    def test_llm_failure_falls_back_to_deterministic(self, recon_result):
        """RP-02: LLM exception -> fallback to PolicyEngine."""
        add_exception(recon_result, _QTY_MISMATCH)
        planner = self._make_planner(
            llm_side_effect=RuntimeError("API timeout")
        )

        plan = planner.plan(recon_result)

        assert plan.plan_source == "deterministic"
        assert len(plan.agents) > 0

    # RP-03
    def test_invalid_json_falls_back(self, recon_result):
        """RP-03: Non-JSON LLM response -> fallback."""
        add_exception(recon_result, _QTY_MISMATCH)
        resp = MagicMock()
        resp.content = "this is not json"
        planner = self._make_planner(llm_return=resp)

        plan = planner.plan(recon_result)

        assert plan.plan_source == "deterministic"

    # RP-04
    def test_no_valid_steps_falls_back(self, recon_result):
        """RP-04: LLM returns empty/invalid steps -> fallback."""
        add_exception(recon_result, _QTY_MISMATCH)
        payload = {
            "overall_reasoning": "hmm",
            "confidence": 0.5,
            "steps": [{"agent_type": "NONEXISTENT_AGENT", "priority": 1}],
        }
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_source == "deterministic"

    # RP-05
    def test_case_summary_out_of_position_falls_back(self, recon_result):
        """RP-05: CASE_SUMMARY not last -> validation fails -> fallback."""
        add_exception(recon_result, _QTY_MISMATCH)
        # CASE_SUMMARY at priority 1 (first), EXCEPTION_ANALYSIS at 2 (last)
        payload = _valid_llm_payload(agents=["CASE_SUMMARY", "EXCEPTION_ANALYSIS"])
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_source == "deterministic"

    # RP-06
    def test_grn_retrieval_in_two_way_falls_back(self, recon_result):
        """RP-06: GRN_RETRIEVAL in TWO_WAY mode -> validation fails -> fallback."""
        recon_result.reconciliation_mode = ReconciliationMode.TWO_WAY
        recon_result.save()
        add_exception(recon_result, _QTY_MISMATCH)

        payload = _valid_llm_payload(
            agents=["GRN_RETRIEVAL", "EXCEPTION_ANALYSIS", "CASE_SUMMARY"]
        )
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_source == "deterministic"
        assert "GRN_RETRIEVAL" not in plan.agents

    # RP-07
    def test_skip_agents_passthrough(self, recon_result):
        """RP-07: MATCHED result -> PolicyEngine says skip -> LLM never called."""
        recon_result.match_status = MatchStatus.MATCHED
        recon_result.deterministic_confidence = 0.99
        recon_result.save()

        planner = self._make_planner(
            llm_side_effect=RuntimeError("LLM should not be called")
        )

        plan = planner.plan(recon_result)

        assert plan.skip_agents is True
        planner._llm.chat.assert_not_called()

    # RP-08
    def test_auto_close_passthrough(self, recon_result):
        """RP-08: Auto-close from PolicyEngine -> LLM not called."""
        recon_result.match_status = MatchStatus.MATCHED
        recon_result.deterministic_confidence = 0.99
        recon_result.save()

        planner = self._make_planner()
        # Force PolicyEngine to return auto_close via mock
        planner._fallback = MagicMock()
        planner._fallback.plan.return_value = AgentPlan(
            skip_agents=True, auto_close=True, reason="within tolerance"
        )

        plan = planner.plan(recon_result)

        assert plan.skip_agents is True
        assert plan.auto_close is True
        planner._llm.chat.assert_not_called()

    # RP-09
    def test_plan_confidence_extracted(self, recon_result):
        """RP-09: plan_confidence comes from LLM response JSON."""
        add_exception(recon_result, _QTY_MISMATCH)
        payload = _valid_llm_payload(confidence=0.42)
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_confidence == pytest.approx(0.42)

    # RP-10
    def test_unknown_agent_types_filtered(self, recon_result):
        """RP-10: Unknown agent types are silently dropped."""
        add_exception(recon_result, _QTY_MISMATCH)
        payload = {
            "overall_reasoning": "Filter test",
            "confidence": 0.8,
            "steps": [
                {"agent_type": "FAKE_AGENT", "priority": 1, "rationale": "nope"},
                {"agent_type": "EXCEPTION_ANALYSIS", "priority": 2, "rationale": "ok"},
                {"agent_type": "CASE_SUMMARY", "priority": 3, "rationale": "ok"},
            ],
        }
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_source == "llm"
        assert "FAKE_AGENT" not in plan.agents
        assert "EXCEPTION_ANALYSIS" in plan.agents

    # RP-11
    def test_should_auto_close_delegates(self):
        """RP-11: should_auto_close delegates to PolicyEngine."""
        with patch("apps.agents.services.reasoning_planner.LLMClient"):
            planner = ReasoningPlanner()
        planner._fallback = MagicMock()
        planner._fallback.should_auto_close.return_value = True

        result = planner.should_auto_close("AUTO_CLOSE", 0.95)

        assert result is True
        planner._fallback.should_auto_close.assert_called_once_with("AUTO_CLOSE", 0.95)

    # RP-12
    def test_should_escalate_delegates(self):
        """RP-12: should_escalate delegates to PolicyEngine."""
        with patch("apps.agents.services.reasoning_planner.LLMClient"):
            planner = ReasoningPlanner()
        planner._fallback = MagicMock()
        planner._fallback.should_escalate.return_value = False

        result = planner.should_escalate("SEND_TO_AP_REVIEW", 0.5)

        assert result is False
        planner._fallback.should_escalate.assert_called_once_with("SEND_TO_AP_REVIEW", 0.5)

    # RP-13
    def test_steps_sorted_by_priority(self, recon_result):
        """RP-13: Steps are sorted by priority ascending."""
        add_exception(recon_result, _QTY_MISMATCH)
        payload = {
            "overall_reasoning": "Priority sort test",
            "confidence": 0.9,
            "steps": [
                {"agent_type": "CASE_SUMMARY", "priority": 3, "rationale": "last"},
                {"agent_type": "EXCEPTION_ANALYSIS", "priority": 1, "rationale": "first"},
                {"agent_type": "REVIEW_ROUTING", "priority": 2, "rationale": "mid"},
            ],
        }
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.agents == [
            "EXCEPTION_ANALYSIS",
            "REVIEW_ROUTING",
            "CASE_SUMMARY",
        ]

    def test_empty_llm_content_falls_back(self, recon_result):
        """LLM returns None content -> falls back."""
        add_exception(recon_result, _QTY_MISMATCH)
        resp = MagicMock()
        resp.content = None
        planner = self._make_planner(llm_return=resp)

        plan = planner.plan(recon_result)

        # Empty JSON {} -> no steps -> fallback
        assert plan.plan_source == "deterministic"

    def test_steps_not_a_list_falls_back(self, recon_result):
        """LLM returns steps as a string instead of list -> fallback."""
        add_exception(recon_result, _QTY_MISMATCH)
        payload = {
            "overall_reasoning": "Bad steps",
            "confidence": 0.8,
            "steps": "not a list",
        }
        planner = self._make_planner(llm_return=_make_llm_response(payload))

        plan = planner.plan(recon_result)

        assert plan.plan_source == "deterministic"


# ===========================================================================
# RP-14 / RP-15: Orchestrator flag wiring
# ===========================================================================
@pytest.mark.django_db
class TestOrchestratorPlannerSelection:
    """Verify the orchestrator respects AGENT_REASONING_ENGINE_ENABLED."""

    @patch("apps.agents.services.orchestrator.settings")
    def test_flag_disabled_uses_policy_engine(self, mock_settings):
        """RP-14: When flag is False, orchestrator uses PolicyEngine."""
        mock_settings.AGENT_REASONING_ENGINE_ENABLED = False
        from apps.agents.services.orchestrator import AgentOrchestrator

        orch = AgentOrchestrator()

        assert isinstance(orch.policy, PolicyEngine)
        assert not isinstance(orch.policy, ReasoningPlanner)

    @patch("apps.agents.services.reasoning_planner.LLMClient")
    @patch("apps.agents.services.orchestrator.settings")
    def test_flag_enabled_uses_reasoning_planner(self, mock_settings, _mock_llm):
        """RP-15: When flag is True, orchestrator uses ReasoningPlanner."""
        mock_settings.AGENT_REASONING_ENGINE_ENABLED = True
        from apps.agents.services.orchestrator import AgentOrchestrator

        orch = AgentOrchestrator()

        assert isinstance(orch.policy, ReasoningPlanner)
