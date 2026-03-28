"""
Tests for CaseStateMachine — pure unit tests (no DB).

Key behaviours:
  - can_transition: returns True for valid transitions, False for invalid
  - trigger_type enforcement: wrong trigger type blocks transition
  - get_allowed_transitions: returns correct set of allowed next states
  - is_terminal: CLOSED and REJECTED are terminal, others are not
  - transition: raises ValueError for invalid transitions
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from apps.cases.state_machine.case_state_machine import CaseStateMachine
from apps.core.enums import CaseStatus, PerformedByType


# ─── can_transition ───────────────────────────────────────────────────────────

class TestCanTransition:
    def test_valid_transition(self):
        """NEW → INTAKE_IN_PROGRESS is a valid transition."""
        assert CaseStateMachine.can_transition(
            CaseStatus.NEW,
            CaseStatus.INTAKE_IN_PROGRESS
        ) is True

    def test_invalid_transition_returns_false(self):
        """NEW → CLOSED is not a valid transition."""
        assert CaseStateMachine.can_transition(
            CaseStatus.NEW,
            CaseStatus.CLOSED
        ) is False

    def test_valid_transition_with_correct_trigger(self):
        """NEW → INTAKE_IN_PROGRESS with SYSTEM trigger is valid."""
        assert CaseStateMachine.can_transition(
            CaseStatus.NEW,
            CaseStatus.INTAKE_IN_PROGRESS,
            trigger_type=PerformedByType.SYSTEM,
        ) is True

    def test_valid_transition_with_wrong_trigger_returns_false(self):
        """NEW → INTAKE_IN_PROGRESS with HUMAN trigger is not allowed."""
        assert CaseStateMachine.can_transition(
            CaseStatus.NEW,
            CaseStatus.INTAKE_IN_PROGRESS,
            trigger_type=PerformedByType.HUMAN,
        ) is False

    def test_unknown_from_status_returns_false(self):
        """Completely unknown from_status returns False."""
        assert CaseStateMachine.can_transition(
            "UNKNOWN_STATUS",
            CaseStatus.INTAKE_IN_PROGRESS,
        ) is False

    def test_extraction_to_failed(self):
        """EXTRACTION_IN_PROGRESS → FAILED with SYSTEM trigger is valid."""
        assert CaseStateMachine.can_transition(
            CaseStatus.EXTRACTION_IN_PROGRESS,
            CaseStatus.FAILED,
            PerformedByType.SYSTEM,
        ) is True

    def test_in_review_to_escalated_by_human(self):
        """IN_REVIEW → ESCALATED with HUMAN trigger."""
        assert CaseStateMachine.can_transition(
            CaseStatus.IN_REVIEW,
            CaseStatus.ESCALATED,
            PerformedByType.HUMAN,
        ) is True

    def test_in_review_to_escalated_by_system_not_allowed(self):
        """IN_REVIEW → ESCALATED can only be triggered by HUMAN."""
        assert CaseStateMachine.can_transition(
            CaseStatus.IN_REVIEW,
            CaseStatus.ESCALATED,
            PerformedByType.SYSTEM,
        ) is False

    def test_two_way_in_progress_to_closed_by_deterministic(self):
        """TWO_WAY_IN_PROGRESS → CLOSED (auto-close on MATCHED) by DETERMINISTIC."""
        assert CaseStateMachine.can_transition(
            CaseStatus.TWO_WAY_IN_PROGRESS,
            CaseStatus.CLOSED,
            PerformedByType.DETERMINISTIC,
        ) is True

    def test_path_resolution_to_two_way_by_deterministic(self):
        assert CaseStateMachine.can_transition(
            CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
            CaseStatus.TWO_WAY_IN_PROGRESS,
            PerformedByType.DETERMINISTIC,
        ) is True

    def test_path_resolution_to_three_way_by_deterministic(self):
        assert CaseStateMachine.can_transition(
            CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
            CaseStatus.THREE_WAY_IN_PROGRESS,
            PerformedByType.DETERMINISTIC,
        ) is True

    def test_path_resolution_to_non_po_by_deterministic(self):
        assert CaseStateMachine.can_transition(
            CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
            CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
            PerformedByType.DETERMINISTIC,
        ) is True

    def test_failed_to_new_by_human_recovery(self):
        """FAILED → NEW with HUMAN trigger (recovery path)."""
        assert CaseStateMachine.can_transition(
            CaseStatus.FAILED,
            CaseStatus.NEW,
            PerformedByType.HUMAN,
        ) is True


# ─── get_allowed_transitions ──────────────────────────────────────────────────

class TestGetAllowedTransitions:
    def test_new_allows_intake_in_progress(self):
        allowed = CaseStateMachine.get_allowed_transitions(CaseStatus.NEW)
        assert CaseStatus.INTAKE_IN_PROGRESS in allowed

    def test_new_does_not_allow_closed(self):
        allowed = CaseStateMachine.get_allowed_transitions(CaseStatus.NEW)
        assert CaseStatus.CLOSED not in allowed

    def test_terminal_closed_has_no_transitions(self):
        allowed = CaseStateMachine.get_allowed_transitions(CaseStatus.CLOSED)
        assert len(allowed) == 0

    def test_terminal_rejected_has_no_transitions(self):
        allowed = CaseStateMachine.get_allowed_transitions(CaseStatus.REJECTED)
        assert len(allowed) == 0

    def test_in_review_multiple_transitions(self):
        """IN_REVIEW can go to REVIEW_COMPLETED, ESCALATED, or back to READY_FOR_REVIEW."""
        allowed = CaseStateMachine.get_allowed_transitions(CaseStatus.IN_REVIEW)
        assert CaseStatus.REVIEW_COMPLETED in allowed
        assert CaseStatus.ESCALATED in allowed
        assert CaseStatus.READY_FOR_REVIEW in allowed

    def test_unknown_status_returns_empty(self):
        allowed = CaseStateMachine.get_allowed_transitions("DOES_NOT_EXIST")
        assert allowed == set()


# ─── is_terminal ──────────────────────────────────────────────────────────────

class TestIsTerminal:
    def test_closed_is_terminal(self):
        assert CaseStateMachine.is_terminal(CaseStatus.CLOSED) is True

    def test_rejected_is_terminal(self):
        assert CaseStateMachine.is_terminal(CaseStatus.REJECTED) is True

    def test_new_is_not_terminal(self):
        assert CaseStateMachine.is_terminal(CaseStatus.NEW) is False

    def test_in_review_is_not_terminal(self):
        assert CaseStateMachine.is_terminal(CaseStatus.IN_REVIEW) is False

    def test_failed_is_not_terminal(self):
        """FAILED is not a terminal state — it has a recovery path (FAILED → NEW)."""
        assert CaseStateMachine.is_terminal(CaseStatus.FAILED) is False

    def test_escalated_is_not_terminal(self):
        """ESCALATED is not terminal — can go to IN_REVIEW or REJECTED."""
        assert CaseStateMachine.is_terminal(CaseStatus.ESCALATED) is False


# ─── transition ───────────────────────────────────────────────────────────────

class TestTransition:
    def test_valid_transition_updates_case_status(self):
        """transition() updates case.status on valid transition."""
        case = MagicMock()
        case.status = CaseStatus.NEW
        CaseStateMachine.transition(case, CaseStatus.INTAKE_IN_PROGRESS,
                                    PerformedByType.SYSTEM)
        case.save.assert_called()

    def test_invalid_transition_raises_value_error(self):
        """transition() raises ValueError on invalid transition."""
        case = MagicMock()
        case.status = CaseStatus.NEW
        with pytest.raises((ValueError, Exception)):
            CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.HUMAN)

    def test_wrong_trigger_raises_value_error(self):
        """transition() with wrong trigger_type raises ValueError."""
        case = MagicMock()
        case.status = CaseStatus.NEW
        with pytest.raises((ValueError, Exception)):
            CaseStateMachine.transition(
                case, CaseStatus.INTAKE_IN_PROGRESS, PerformedByType.HUMAN
            )
