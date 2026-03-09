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
        logger.info("Created review assignment %s for result %s", assignment.pk, result.pk)
        return assignment

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------
    @staticmethod
    def assign_reviewer(assignment: ReviewAssignment, user) -> ReviewAssignment:
        assignment.assigned_to = user
        assignment.status = ReviewStatus.ASSIGNED
        assignment.save(update_fields=["assigned_to", "status", "updated_at"])
        return assignment

    @staticmethod
    def start_review(assignment: ReviewAssignment) -> ReviewAssignment:
        assignment.status = ReviewStatus.IN_REVIEW
        assignment.save(update_fields=["status", "updated_at"])
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
        return ManualReviewAction.objects.create(
            assignment=assignment,
            performed_by=user,
            action_type=action_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )

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
    def approve(cls, assignment: ReviewAssignment, user, reason: str = "") -> ReviewDecision:
        return cls._finalise(assignment, user, ReviewStatus.APPROVED, reason)

    @classmethod
    def reject(cls, assignment: ReviewAssignment, user, reason: str = "") -> ReviewDecision:
        return cls._finalise(assignment, user, ReviewStatus.REJECTED, reason)

    @classmethod
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
        logger.info("Review %s decided: %s by %s", assignment.pk, decision_status, user)
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
