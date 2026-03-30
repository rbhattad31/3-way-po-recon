"""Tests for RecoveryLaneService — policy evaluation and invoke behavior.

Policy rules:
  - evaluate() returns should_invoke=True only for named trigger codes.
  - Generic low-confidence codes (e.g. LOW_CONFIDENCE_CRITICAL_FIELD alone) do NOT trigger.
  - trigger_codes and recovery_actions are correctly derived per code.
  - invoke() is fail-silent: exceptions produce a failed RecoveryResult, not a raise.
  - invoke() calls InvoiceUnderstandingAgent exactly once when triggered.
"""
import pytest
from unittest.mock import MagicMock, patch

from apps.extraction.services.recovery_lane_service import (
    RecoveryLaneService,
    RecoveryDecision,
    RecoveryResult,
    RECOVERY_TRIGGER_CODES,
    _CODE_TO_ACTIONS,
)
from apps.extraction import decision_codes as dc


# ── RecoveryLaneService.evaluate() ───────────────────────────────────────────

class TestEvaluatePolicy:
    def test_no_codes_does_not_trigger(self):
        decision = RecoveryLaneService.evaluate([])
        assert decision.should_invoke is False
        assert decision.trigger_codes == []

    def test_generic_low_confidence_does_not_trigger(self):
        """LOW_CONFIDENCE_CRITICAL_FIELD alone is not a named trigger mode."""
        decision = RecoveryLaneService.evaluate([dc.LOW_CONFIDENCE_CRITICAL_FIELD])
        assert decision.should_invoke is False

    def test_line_sum_mismatch_does_not_trigger(self):
        """LINE_SUM_MISMATCH is a warning code — not a named recovery trigger."""
        decision = RecoveryLaneService.evaluate([dc.LINE_SUM_MISMATCH])
        assert decision.should_invoke is False

    def test_inv_num_unrecoverable_triggers(self):
        decision = RecoveryLaneService.evaluate([dc.INV_NUM_UNRECOVERABLE])
        assert decision.should_invoke is True
        assert dc.INV_NUM_UNRECOVERABLE in decision.trigger_codes

    def test_total_mismatch_hard_triggers(self):
        decision = RecoveryLaneService.evaluate([dc.TOTAL_MISMATCH_HARD])
        assert decision.should_invoke is True
        assert dc.TOTAL_MISMATCH_HARD in decision.trigger_codes

    def test_tax_alloc_ambiguous_triggers(self):
        decision = RecoveryLaneService.evaluate([dc.TAX_ALLOC_AMBIGUOUS])
        assert decision.should_invoke is True

    def test_vendor_match_low_triggers(self):
        decision = RecoveryLaneService.evaluate([dc.VENDOR_MATCH_LOW])
        assert decision.should_invoke is True

    def test_line_table_incomplete_triggers(self):
        decision = RecoveryLaneService.evaluate([dc.LINE_TABLE_INCOMPLETE])
        assert decision.should_invoke is True

    def test_prompt_composition_fallback_triggers(self):
        decision = RecoveryLaneService.evaluate([dc.PROMPT_COMPOSITION_FALLBACK_USED])
        assert decision.should_invoke is True

    def test_multiple_triggers_collected(self):
        codes = [dc.INV_NUM_UNRECOVERABLE, dc.VENDOR_MATCH_LOW]
        decision = RecoveryLaneService.evaluate(codes)
        assert decision.should_invoke is True
        assert dc.INV_NUM_UNRECOVERABLE in decision.trigger_codes
        assert dc.VENDOR_MATCH_LOW in decision.trigger_codes

    def test_mixed_codes_with_non_trigger(self):
        """Non-trigger codes alongside trigger codes → still triggered."""
        codes = [dc.LOW_CONFIDENCE_CRITICAL_FIELD, dc.INV_NUM_UNRECOVERABLE, dc.LINE_SUM_MISMATCH]
        decision = RecoveryLaneService.evaluate(codes)
        assert decision.should_invoke is True
        assert decision.trigger_codes == [dc.INV_NUM_UNRECOVERABLE]

    def test_recovery_actions_populated_for_trigger(self):
        decision = RecoveryLaneService.evaluate([dc.VENDOR_MATCH_LOW])
        assert "vendor_lookup" in decision.recovery_actions or "verify_vendor_name" in decision.recovery_actions

    def test_recovery_actions_deduped_for_multiple_triggers(self):
        codes = [dc.TOTAL_MISMATCH_HARD, dc.TAX_ALLOC_AMBIGUOUS]
        decision = RecoveryLaneService.evaluate(codes)
        # Actions should not be duplicated
        assert len(decision.recovery_actions) == len(set(decision.recovery_actions))

    def test_all_trigger_codes_are_in_recovery_trigger_codes_set(self):
        """Sanity: all codes in _CODE_TO_ACTIONS should be in RECOVERY_TRIGGER_CODES."""
        for code in _CODE_TO_ACTIONS:
            assert code in RECOVERY_TRIGGER_CODES, f"{code} in _CODE_TO_ACTIONS but not in RECOVERY_TRIGGER_CODES"

    def test_reason_contains_trigger_code(self):
        decision = RecoveryLaneService.evaluate([dc.INV_NUM_UNRECOVERABLE])
        assert dc.INV_NUM_UNRECOVERABLE in decision.reason


# ── RecoveryLaneService.invoke() ─────────────────────────────────────────────

class TestInvokeNotTriggered:
    def test_invoke_returns_not_invoked_when_should_invoke_false(self):
        decision = RecoveryDecision(should_invoke=False)
        result = RecoveryLaneService.invoke(decision, invoice_id=99)
        assert result.invoked is False
        assert result.succeeded is False


class TestInvokeWithAgent:
    def _make_decision(self):
        return RecoveryDecision(
            should_invoke=True,
            trigger_codes=[dc.INV_NUM_UNRECOVERABLE],
            recovery_actions=["verify_invoice_number"],
            reason="test",
        )

    def _make_mock_agent_run(self, reasoning="Found INV-001", confidence=0.75):
        agent_run = MagicMock()
        agent_run.pk = 42
        agent_run.output_payload = {
            "reasoning": reasoning,
            "confidence": confidence,
            "recommendation_type": "VERIFY",
            "evidence": {"found_invoice": "INV-001"},
        }
        agent_run.input_payload = {}
        return agent_run

    def test_invoke_calls_agent_once(self):
        decision = self._make_decision()
        mock_run = self._make_mock_agent_run()

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.run.return_value = mock_run

            result = RecoveryLaneService.invoke(decision, invoice_id=123)

        MockAgent.assert_called_once()
        instance.run.assert_called_once()
        assert result.invoked is True

    def test_invoke_succeeded_when_agent_produces_reasoning(self):
        decision = self._make_decision()
        mock_run = self._make_mock_agent_run(reasoning="Found vendor mismatch")

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.run.return_value = mock_run
            result = RecoveryLaneService.invoke(decision, invoice_id=1)

        assert result.succeeded is True
        assert result.agent_reasoning == "Found vendor mismatch"

    def test_invoke_succeeded_false_when_agent_empty_output(self):
        decision = self._make_decision()
        mock_run = MagicMock()
        mock_run.pk = 99
        mock_run.output_payload = {}
        mock_run.input_payload = {}

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.run.return_value = mock_run
            result = RecoveryLaneService.invoke(decision, invoice_id=1)

        assert result.invoked is True
        assert result.succeeded is False

    def test_invoke_captures_agent_run_id(self):
        decision = self._make_decision()
        mock_run = self._make_mock_agent_run()

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.run.return_value = mock_run
            result = RecoveryLaneService.invoke(decision, invoice_id=1)

        assert result.agent_run_id == 42

    def test_invoke_fail_silent_on_agent_exception(self):
        decision = self._make_decision()

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.run.side_effect = RuntimeError("LLM timeout")
            result = RecoveryLaneService.invoke(decision, invoice_id=1)

        assert result.invoked is True
        assert result.succeeded is False
        assert "LLM timeout" in result.error

    def test_invoke_fail_silent_on_agent_init_error(self):
        """If InvoiceUnderstandingAgent() constructor raises, invoke() still returns gracefully."""
        decision = self._make_decision()

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent",
            side_effect=RuntimeError("agent init failed"),
        ):
            result = RecoveryLaneService.invoke(decision, invoice_id=1)

        assert result.invoked is True
        assert result.succeeded is False

    def test_trigger_codes_propagated_to_result(self):
        decision = self._make_decision()
        mock_run = self._make_mock_agent_run()

        with patch(
            "apps.agents.services.agent_classes.InvoiceUnderstandingAgent"
        ) as MockAgent:
            instance = MockAgent.return_value
            instance.run.return_value = mock_run
            result = RecoveryLaneService.invoke(decision, invoice_id=1)

        assert result.trigger_codes == [dc.INV_NUM_UNRECOVERABLE]
        assert result.recovery_actions == ["verify_invoice_number"]


# ── RecoveryResult.to_serializable() ─────────────────────────────────────────

class TestRecoveryResultSerializable:
    def test_to_serializable_keys(self):
        r = RecoveryResult(
            invoked=True,
            succeeded=True,
            trigger_codes=["INV_NUM_UNRECOVERABLE"],
            recovery_actions=["verify_invoice_number"],
            agent_reasoning="Found the number",
            agent_confidence=0.8,
            agent_recommendation="VERIFY",
            agent_evidence={"found": "INV-001"},
            agent_run_id=7,
        )
        s = r.to_serializable()
        assert s["invoked"] is True
        assert s["succeeded"] is True
        assert s["trigger_codes"] == ["INV_NUM_UNRECOVERABLE"]
        assert s["agent_run_id"] == 7
        assert s["error"] == ""

    def test_to_serializable_truncates_long_reasoning(self):
        r = RecoveryResult(invoked=True, agent_reasoning="x" * 1000)
        s = r.to_serializable()
        assert len(s["agent_reasoning"]) <= 500
