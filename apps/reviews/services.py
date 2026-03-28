"""Review workflow service — manages assignment lifecycle and reviewer actions."""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    MatchStatus,
    ReviewActionType,
    ReviewStatus,
    RecommendationType,
)
from apps.core.decorators import observed_service
from apps.core.metrics import MetricsService
from apps.reconciliation.models import ReconciliationResult
from apps.reviews.models import (
    ManualReviewAction,
    ReviewAssignment,
    ReviewComment,
    ReviewDecision,
)

logger = logging.getLogger(__name__)


class ReviewWorkflowService:
    """Orchestrates the human-review lifecycle."""

    # ------------------------------------------------------------------
    # Assignment creation
    # ------------------------------------------------------------------
    @staticmethod
    def create_assignment(
        result: ReconciliationResult,
        assigned_to=None,
        priority: int = 5,
        notes: str = "",
    ) -> ReviewAssignment:
        assignment = ReviewAssignment.objects.create(
            reconciliation_result=result,
            assigned_to=assigned_to,
            status=ReviewStatus.ASSIGNED if assigned_to else ReviewStatus.PENDING,
            priority=priority,
            notes=notes,
        )
        result.requires_review = True
        result.save(update_fields=["requires_review", "updated_at"])

        # Audit: review assigned
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=result.invoice_id,
            event_type=AuditEventType.REVIEW_ASSIGNED,
            description=f"Review assignment #{assignment.pk} created (priority: {priority})",
            user=assigned_to,
            metadata={"assignment_id": assignment.pk, "result_id": result.pk},
        )

        logger.info("Created review assignment %s for result %s", assignment.pk, result.pk)

        try:
            from apps.core.langfuse_client import start_trace
            _lf_trace_id = f"review-{assignment.pk}"
            start_trace(
                _lf_trace_id,
                "review_assignment",
                metadata={
                    "assignment_pk": assignment.pk,
                    "reconciliation_result_id": assignment.reconciliation_result_id,
                    "assigned_to": getattr(assignment.assigned_to, "pk", None),
                    "review_type": getattr(assignment, "review_type", None),
                },
            )
        except Exception:
            pass

        try:
            from apps.core.langfuse_client import score_trace
            _trace_id = f"review-{assignment.pk}"
            score_trace(
                _trace_id,
                "review_priority",
                float(priority) / 10.0,
                comment=(
                    f"assignment={assignment.pk} "
                    f"invoice={result.invoice_id} "
                    f"result={result.pk}"
                ),
            )
        except Exception:
            pass

        return assignment

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------
    @staticmethod
    def assign_reviewer(assignment: ReviewAssignment, user) -> ReviewAssignment:
        _lf_span = None
        try:
            from apps.core.langfuse_client import get_client
            lf = get_client()
            if lf:
                _lf_span = lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_assign_reviewer",
                    metadata={"reviewer_id": getattr(user, "pk", None)},
                )
        except Exception:
            pass

        try:
            previous_assignee = assignment.assigned_to
            assignment.assigned_to = user
            assignment.status = ReviewStatus.ASSIGNED
            assignment.save(update_fields=["assigned_to", "status", "updated_at"])

            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType
            AuditService.log_event(
                entity_type="ReviewAssignment",
                entity_id=assignment.pk,
                event_type=AuditEventType.REVIEWER_ASSIGNED,
                description=f"Reviewer {user} assigned to review #{assignment.pk}",
                user=user,
                metadata={
                    "assignment_id": assignment.pk,
                    "invoice_id": assignment.reconciliation_result.invoice_id,
                    "previous_assignee_id": previous_assignee.pk if previous_assignee else None,
                },
            )
        finally:
            try:
                if _lf_span:
                    _lf_span.end(output={"status": assignment.status})
            except Exception:
                pass

        return assignment

    @staticmethod
    def start_review(assignment: ReviewAssignment) -> ReviewAssignment:
        _lf_span = None
        try:
            from apps.core.langfuse_client import get_client
            lf = get_client()
            if lf:
                _lf_span = lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_start",
                    metadata={"assignment_id": assignment.pk},
                )
        except Exception:
            pass

        try:
            assignment.status = ReviewStatus.IN_REVIEW
            assignment.save(update_fields=["status", "updated_at"])

            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType
            AuditService.log_event(
                entity_type="ReviewAssignment",
                entity_id=assignment.pk,
                event_type=AuditEventType.REVIEW_STARTED,
                description=f"Review #{assignment.pk} started by {assignment.assigned_to}",
                user=assignment.assigned_to,
                metadata={
                    "assignment_id": assignment.pk,
                    "invoice_id": assignment.reconciliation_result.invoice_id,
                },
            )
        finally:
            try:
                if _lf_span:
                    _lf_span.end(output={"status": assignment.status})
            except Exception:
                pass

        return assignment

    # ------------------------------------------------------------------
    # Reviewer actions
    # ------------------------------------------------------------------
    @staticmethod
    def record_action(
        assignment: ReviewAssignment,
        user,
        action_type: str,
        field_name: str = "",
        old_value: str = "",
        new_value: str = "",
        reason: str = "",
    ) -> ManualReviewAction:
        action = ManualReviewAction.objects.create(
            assignment=assignment,
            performed_by=user,
            action_type=action_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )

        # Audit: field correction
        if action_type == ReviewActionType.CORRECT_FIELD and field_name:
            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=assignment.reconciliation_result.invoice_id,
                event_type=AuditEventType.FIELD_CORRECTED,
                description=f"Field '{field_name}' corrected: '{old_value}' -> '{new_value}'",
                user=user,
                metadata={"field": field_name, "old": old_value, "new": new_value, "assignment_id": assignment.pk},
            )

        return action

    @staticmethod
    def add_comment(
        assignment: ReviewAssignment,
        user,
        body: str,
        is_internal: bool = True,
    ) -> ReviewComment:
        return ReviewComment.objects.create(
            assignment=assignment,
            author=user,
            body=body,
            is_internal=is_internal,
        )

    # ------------------------------------------------------------------
    # Final decisions
    # ------------------------------------------------------------------
    @classmethod
    @observed_service("reviews.approve", audit_event="REVIEW_APPROVED", entity_type="ReviewAssignment")
    def approve(cls, assignment: ReviewAssignment, user, reason: str = "") -> ReviewDecision:
        return cls._finalise(assignment, user, ReviewStatus.APPROVED, reason)

    @classmethod
    @observed_service("reviews.reject", audit_event="REVIEW_REJECTED", entity_type="ReviewAssignment")
    def reject(cls, assignment: ReviewAssignment, user, reason: str = "") -> ReviewDecision:
        return cls._finalise(assignment, user, ReviewStatus.REJECTED, reason)

    @classmethod
    @observed_service("reviews.request_reprocess", audit_event="RECONCILIATION_RERUN", entity_type="ReviewAssignment")
    def request_reprocess(cls, assignment: ReviewAssignment, user, reason: str = "") -> ReviewDecision:
        return cls._finalise(assignment, user, ReviewStatus.REPROCESSED, reason)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @classmethod
    @transaction.atomic
    def _finalise(
        cls,
        assignment: ReviewAssignment,
        user,
        decision_status: str,
        reason: str,
    ) -> ReviewDecision:
        _lf_span = None
        try:
            from apps.core.langfuse_client import get_client
            lf = get_client()
            if lf:
                _lf_span = lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_finalise",
                    metadata={
                        "decision_status": decision_status,
                        "user_id": getattr(user, "pk", None),
                    },
                )
        except Exception:
            pass

        assignment.status = decision_status
        assignment.save(update_fields=["status", "updated_at"])

        decision, _ = ReviewDecision.objects.update_or_create(
            assignment=assignment,
            defaults={
                "decided_by": user,
                "decision": decision_status,
                "reason": reason,
            },
        )

        # Propagate to reconciliation result
        result = assignment.reconciliation_result
        if decision_status == ReviewStatus.APPROVED:
            result.match_status = MatchStatus.MATCHED
            result.requires_review = False
        elif decision_status == ReviewStatus.REJECTED:
            result.match_status = MatchStatus.UNMATCHED
            result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        cls._record_action(assignment, user, decision_status, reason)

        # Audit: review decision
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        event_type_map = {
            ReviewStatus.APPROVED: AuditEventType.REVIEW_APPROVED,
            ReviewStatus.REJECTED: AuditEventType.REVIEW_REJECTED,
            ReviewStatus.REPROCESSED: AuditEventType.RECONCILIATION_RERUN,
        }
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=result.invoice_id,
            event_type=event_type_map.get(decision_status, decision_status),
            description=f"Review decision: {decision_status} by {user}",
            user=user,
            metadata={"assignment_id": assignment.pk, "decision": decision_status, "reason": reason[:300]},
        )

        logger.info("Review %s decided: %s by %s", assignment.pk, decision_status, user)

        try:
            from apps.core.langfuse_client import score_trace
            _decision_score = {
                ReviewStatus.APPROVED: 1.0,
                ReviewStatus.REJECTED: 0.0,
                ReviewStatus.REPROCESSED: 0.5,
            }.get(decision_status, 0.5)
            _invoice_id = getattr(
                getattr(assignment, "reconciliation_result", None),
                "invoice_id", None
            )
            _trace_id = f"review-{assignment.pk}"
            score_trace(
                _trace_id,
                "review_decision",
                _decision_score,
                comment=(
                    f"decision={decision_status} "
                    f"invoice={_invoice_id} "
                    f"reviewer={getattr(user, 'pk', None)}"
                ),
            )
        except Exception:
            pass

        try:
            if _lf_span:
                _lf_span.end(output={"status": decision_status})
        except Exception:
            pass

        return decision

    @staticmethod
    def _record_action(assignment, user, decision_status, reason):
        action_map = {
            ReviewStatus.APPROVED: ReviewActionType.APPROVE,
            ReviewStatus.REJECTED: ReviewActionType.REJECT,
            ReviewStatus.REPROCESSED: ReviewActionType.REPROCESS,
        }
        action_type = action_map.get(decision_status, ReviewActionType.APPROVE)
        ManualReviewAction.objects.create(
            assignment=assignment,
            performed_by=user,
            action_type=action_type,
            reason=reason,
        )
