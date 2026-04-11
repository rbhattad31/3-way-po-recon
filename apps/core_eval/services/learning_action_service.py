"""Service for LearningAction CRUD operations."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from apps.core_eval.models import LearningAction

logger = logging.getLogger(__name__)


def _audit_action_event(event_type: str, action: LearningAction, *, user=None, status_before: str = "") -> None:
    """Log an audit event for a learning action status change (fail-silent)."""
    try:
        from apps.auditlog.services import AuditService

        AuditService.log_event(
            entity_type="LearningAction",
            entity_id=action.pk,
            event_type=event_type,
            description=f"Learning action #{action.pk} ({action.action_type}) -> {action.status}",
            user=user,
            agent="LearningActionService",
            metadata={
                "action_type": action.action_type,
                "app_module": action.app_module,
            },
            status_before=status_before,
            status_after=action.status,
        )
    except Exception:
        logger.debug("Audit log for %s failed (non-fatal)", event_type)


class LearningActionService:
    """Minimal, idempotent CRUD for LearningAction records."""

    @staticmethod
    def propose(
        *,
        action_type: str,
        app_module: str = "",
        tenant_id: str = "",
        target_description: str = "",
        rationale: str = "",
        input_signals_json: Optional[dict] = None,
        action_payload_json: Optional[dict] = None,
        proposed_by=None,
        tenant=None,
    ) -> LearningAction:
        kwargs = dict(
            action_type=action_type,
            status=LearningAction.Status.PROPOSED,
            app_module=app_module,
            target_description=target_description,
            rationale=rationale,
            input_signals_json=input_signals_json or {},
            action_payload_json=action_payload_json or {},
            proposed_by=proposed_by,
        )
        if tenant is not None:
            kwargs["tenant"] = tenant
        elif tenant_id:
            kwargs["tenant_id"] = tenant_id
        return LearningAction.objects.create(**kwargs)

    @staticmethod
    def approve(action: LearningAction, *, approved_by=None) -> LearningAction:
        status_before = action.status
        action.status = LearningAction.Status.APPROVED
        action.approved_by = approved_by
        action.save(update_fields=["status", "approved_by", "updated_at"])
        _audit_action_event(
            "LEARNING_ACTION_APPROVED", action,
            user=approved_by, status_before=status_before,
        )
        return action

    @staticmethod
    def mark_applied(
        action: LearningAction,
        *,
        result_json: Optional[dict] = None,
    ) -> LearningAction:
        status_before = action.status
        action.status = LearningAction.Status.APPLIED
        action.applied_at = timezone.now()
        if result_json is not None:
            action.result_json = result_json
        action.save(update_fields=[
            "status", "applied_at", "result_json",
            "execution_log_json", "execution_error", "retry_count", "updated_at",
        ])
        _audit_action_event(
            "LEARNING_ACTION_APPLIED", action, status_before=status_before,
        )
        return action

    @staticmethod
    def mark_rejected(action: LearningAction) -> LearningAction:
        status_before = action.status
        action.status = LearningAction.Status.REJECTED
        action.save(update_fields=["status", "updated_at"])
        _audit_action_event(
            "LEARNING_ACTION_REJECTED", action, status_before=status_before,
        )
        return action

    @staticmethod
    def mark_failed(
        action: LearningAction,
        *,
        result_json: Optional[dict] = None,
    ) -> LearningAction:
        status_before = action.status
        action.status = LearningAction.Status.FAILED
        if result_json is not None:
            action.result_json = result_json
        action.save(update_fields=["status", "result_json", "updated_at"])
        _audit_action_event(
            "LEARNING_ACTION_FAILED", action, status_before=status_before,
        )
        return action

    @staticmethod
    def list_by_status(status: str, limit: int = 100):
        return LearningAction.objects.filter(status=status).order_by("-created_at")[:limit]

    @staticmethod
    def list_by_type(action_type: str, limit: int = 100):
        return LearningAction.objects.filter(action_type=action_type).order_by("-created_at")[:limit]

    @staticmethod
    def record_execution_attempt(
        action: LearningAction,
        log_entry: str,
        error: str | None = None,
    ) -> LearningAction:
        """Append an execution log entry and update status accordingly."""
        status_before = action.status
        action.execution_log_json = (action.execution_log_json or []) + [
            {"timestamp": timezone.now().isoformat(), "log": log_entry, "error": error}
        ]
        if error:
            action.execution_error = error
            action.retry_count += 1
            action.next_retry_at = timezone.now() + timedelta(minutes=30 * action.retry_count)
            action.status = LearningAction.Status.FAILED
        else:
            action.execution_error = ""
            action.next_retry_at = None
            action.status = LearningAction.Status.APPLIED
            action.applied_at = timezone.now()
        action.save(update_fields=[
            "execution_log_json", "execution_error", "retry_count",
            "next_retry_at", "status", "applied_at", "updated_at",
        ])
        _audit_action_event(
            "LEARNING_ACTION_APPLIED" if not error else "LEARNING_ACTION_FAILED",
            action, status_before=status_before,
        )
        return action
