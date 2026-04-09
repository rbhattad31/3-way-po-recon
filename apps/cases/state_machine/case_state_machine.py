"""
Case state machine — defines allowed status transitions for APCase.

Each transition specifies:
- from_status → to_status
- trigger_type: who/what can trigger it (SYSTEM, DETERMINISTIC, AGENT, HUMAN)
"""

import logging

from apps.core.enums import CaseStatus, PerformedByType

logger = logging.getLogger(__name__)

# (from_status, to_status, allowed_trigger_types)
CASE_TRANSITIONS = [
    # --- Intake ---
    (CaseStatus.NEW, CaseStatus.INTAKE_IN_PROGRESS, {PerformedByType.SYSTEM}),
    (CaseStatus.INTAKE_IN_PROGRESS, CaseStatus.EXTRACTION_IN_PROGRESS, {PerformedByType.SYSTEM, PerformedByType.AGENT}),
    (CaseStatus.INTAKE_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- Extraction ---
    (CaseStatus.EXTRACTION_IN_PROGRESS, CaseStatus.EXTRACTION_COMPLETED, {PerformedByType.SYSTEM}),
    (CaseStatus.EXTRACTION_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- Extraction approval gate ---
    (CaseStatus.EXTRACTION_COMPLETED, CaseStatus.PENDING_EXTRACTION_APPROVAL, {PerformedByType.SYSTEM}),
    (CaseStatus.EXTRACTION_COMPLETED, CaseStatus.PATH_RESOLUTION_IN_PROGRESS, {PerformedByType.SYSTEM}),
    (CaseStatus.PENDING_EXTRACTION_APPROVAL, CaseStatus.PATH_RESOLUTION_IN_PROGRESS, {PerformedByType.SYSTEM, PerformedByType.HUMAN}),
    (CaseStatus.PENDING_EXTRACTION_APPROVAL, CaseStatus.EXTRACTION_COMPLETED, {PerformedByType.SYSTEM, PerformedByType.HUMAN}),
    (CaseStatus.PENDING_EXTRACTION_APPROVAL, CaseStatus.REJECTED, {PerformedByType.SYSTEM}),

    # --- Path resolution ---
    (CaseStatus.EXTRACTION_COMPLETED, CaseStatus.PATH_RESOLUTION_IN_PROGRESS, {PerformedByType.SYSTEM}),
    (CaseStatus.PATH_RESOLUTION_IN_PROGRESS, CaseStatus.TWO_WAY_IN_PROGRESS, {PerformedByType.DETERMINISTIC}),
    (CaseStatus.PATH_RESOLUTION_IN_PROGRESS, CaseStatus.THREE_WAY_IN_PROGRESS, {PerformedByType.DETERMINISTIC}),
    (CaseStatus.PATH_RESOLUTION_IN_PROGRESS, CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, {PerformedByType.DETERMINISTIC}),
    (CaseStatus.PATH_RESOLUTION_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- 2-Way matching ---
    (CaseStatus.TWO_WAY_IN_PROGRESS, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, {PerformedByType.DETERMINISTIC, PerformedByType.AGENT}),
    (CaseStatus.TWO_WAY_IN_PROGRESS, CaseStatus.CLOSED, {PerformedByType.DETERMINISTIC}),  # auto-close on MATCHED
    (CaseStatus.TWO_WAY_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- 3-Way matching ---
    (CaseStatus.THREE_WAY_IN_PROGRESS, CaseStatus.GRN_ANALYSIS_IN_PROGRESS, {PerformedByType.AGENT}),
    (CaseStatus.THREE_WAY_IN_PROGRESS, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, {PerformedByType.DETERMINISTIC, PerformedByType.AGENT}),
    (CaseStatus.THREE_WAY_IN_PROGRESS, CaseStatus.CLOSED, {PerformedByType.DETERMINISTIC}),
    (CaseStatus.THREE_WAY_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- GRN analysis ---
    (CaseStatus.GRN_ANALYSIS_IN_PROGRESS, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, {PerformedByType.AGENT}),
    (CaseStatus.GRN_ANALYSIS_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- Non-PO validation ---
    (CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, {PerformedByType.DETERMINISTIC, PerformedByType.AGENT}),
    (CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, CaseStatus.FAILED, {PerformedByType.SYSTEM}),

    # --- Exception analysis ---
    (CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, CaseStatus.READY_FOR_REVIEW, {PerformedByType.AGENT, PerformedByType.DETERMINISTIC}),
    (CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, CaseStatus.CLOSED, {PerformedByType.AGENT, PerformedByType.DETERMINISTIC}),  # auto-close safe
    (CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, CaseStatus.ESCALATED, {PerformedByType.AGENT}),

    # --- Review ---
    (CaseStatus.READY_FOR_REVIEW, CaseStatus.IN_REVIEW, {PerformedByType.HUMAN}),
    (CaseStatus.IN_REVIEW, CaseStatus.REVIEW_COMPLETED, {PerformedByType.HUMAN}),
    (CaseStatus.IN_REVIEW, CaseStatus.ESCALATED, {PerformedByType.HUMAN}),
    (CaseStatus.IN_REVIEW, CaseStatus.READY_FOR_REVIEW, {PerformedByType.HUMAN}),

    # --- Post-review ---
    (CaseStatus.REVIEW_COMPLETED, CaseStatus.READY_FOR_APPROVAL, {PerformedByType.SYSTEM}),
    (CaseStatus.REVIEW_COMPLETED, CaseStatus.CLOSED, {PerformedByType.HUMAN}),
    (CaseStatus.REVIEW_COMPLETED, CaseStatus.REJECTED, {PerformedByType.HUMAN}),

    # --- Approval (future) ---
    (CaseStatus.READY_FOR_APPROVAL, CaseStatus.APPROVAL_IN_PROGRESS, {PerformedByType.SYSTEM}),
    (CaseStatus.APPROVAL_IN_PROGRESS, CaseStatus.READY_FOR_GL_CODING, {PerformedByType.HUMAN}),
    (CaseStatus.APPROVAL_IN_PROGRESS, CaseStatus.REJECTED, {PerformedByType.HUMAN}),
    (CaseStatus.APPROVAL_IN_PROGRESS, CaseStatus.ESCALATED, {PerformedByType.HUMAN}),

    # --- GL Coding (future) ---
    (CaseStatus.READY_FOR_GL_CODING, CaseStatus.READY_FOR_POSTING, {PerformedByType.AGENT, PerformedByType.HUMAN}),
    (CaseStatus.READY_FOR_GL_CODING, CaseStatus.REJECTED, {PerformedByType.HUMAN}),

    # --- Posting (future) ---
    (CaseStatus.READY_FOR_POSTING, CaseStatus.CLOSED, {PerformedByType.SYSTEM}),

    # --- Recovery ---
    (CaseStatus.FAILED, CaseStatus.NEW, {PerformedByType.HUMAN}),
    (CaseStatus.ESCALATED, CaseStatus.IN_REVIEW, {PerformedByType.HUMAN}),
    (CaseStatus.ESCALATED, CaseStatus.REJECTED, {PerformedByType.HUMAN}),
]

# Build lookup for fast validation
_TRANSITION_MAP: dict[str, set[str]] = {}
_TRIGGER_MAP: dict[tuple[str, str], set[str]] = {}

for _from, _to, _triggers in CASE_TRANSITIONS:
    _TRANSITION_MAP.setdefault(_from, set()).add(_to)
    _TRIGGER_MAP[(_from, _to)] = _triggers

TERMINAL_STATES = {CaseStatus.CLOSED, CaseStatus.REJECTED}


class CaseStateMachine:
    """Validates and executes APCase status transitions."""

    @staticmethod
    def can_transition(from_status: str, to_status: str, trigger_type: str = "") -> bool:
        allowed = _TRANSITION_MAP.get(from_status, set())
        if to_status not in allowed:
            return False
        if trigger_type:
            allowed_triggers = _TRIGGER_MAP.get((from_status, to_status), set())
            return trigger_type in allowed_triggers
        return True

    @staticmethod
    def get_allowed_transitions(from_status: str) -> set[str]:
        return _TRANSITION_MAP.get(from_status, set())

    @staticmethod
    def is_terminal(status: str) -> bool:
        return status in TERMINAL_STATES

    @staticmethod
    def transition(case, to_status: str, trigger_type: str = "") -> None:
        """
        Transition case to new status. Raises ValueError if transition is not allowed.
        Logs an AuditEvent for significant transitions (terminal states + key milestones).

        Args:
            case: APCase instance
            to_status: Target CaseStatus value
            trigger_type: PerformedByType value (optional validation)
        """
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType

        from_status = case.status
        if not CaseStateMachine.can_transition(from_status, to_status, trigger_type):
            raise ValueError(
                f"Invalid transition: {from_status} → {to_status} "
                f"(trigger: {trigger_type})"
            )
        case.status = to_status
        case.save(update_fields=["status", "updated_at"])

        # Map terminal / significant statuses to audit event types
        _EVENT_MAP = {
            CaseStatus.CLOSED: AuditEventType.CASE_CLOSED,
            CaseStatus.REJECTED: AuditEventType.CASE_REJECTED,
            CaseStatus.ESCALATED: AuditEventType.CASE_ESCALATED,
            CaseStatus.FAILED: AuditEventType.CASE_FAILED,
        }

        event_type = _EVENT_MAP.get(to_status)
        if event_type:
            try:
                AuditService.log_event(
                    entity_type="APCase",
                    entity_id=case.pk,
                    event_type=event_type,
                    description=(
                        f"Case {case.pk} transitioned {from_status} → {to_status} "
                        f"(trigger: {trigger_type or 'unspecified'})"
                    ),
                    invoice_id=case.invoice_id,
                    case_id=case.pk,
                    status_before=from_status,
                    status_after=to_status,
                    metadata={"trigger_type": trigger_type},
                )
            except Exception:
                logger.exception("Failed to log audit event for case %s transition", case.pk)
