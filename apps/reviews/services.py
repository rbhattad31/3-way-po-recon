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
from apps.core.evaluation_constants import (
    REVIEW_APPROVED,
    REVIEW_ASSIGNMENT_CREATED,
    REVIEW_DECISION,
    REVIEW_FIELDS_CORRECTED_COUNT,
    REVIEW_HAD_CORRECTIONS,
    REVIEW_PRIORITY,
    REVIEW_REJECTED,
    REVIEW_REPROCESS_REQUESTED,
)
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
        tenant=None,
    ) -> ReviewAssignment:
        assignment = ReviewAssignment.objects.create(
            reconciliation_result=result,
            assigned_to=assigned_to,
            status=ReviewStatus.ASSIGNED if assigned_to else ReviewStatus.PENDING,
            priority=priority,
            notes=notes,
            tenant=tenant,
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
            from apps.core.langfuse_client import start_trace_safe, score_trace_safe
            _lf_trace_id = f"review-{assignment.pk}"
            _match_status = result.match_status or ""
            _exc_count = result.exceptions.count() if hasattr(result, "exceptions") else 0
            _lf_trace = start_trace_safe(
                _lf_trace_id,
                "review_assignment",
                invoice_id=result.invoice_id,
                user_id=getattr(assigned_to, "pk", None),
                session_id=f"invoice-{result.invoice_id}",
                metadata={
                    "assignment_pk": assignment.pk,
                    "reconciliation_result_id": result.pk,
                    "assigned_to": getattr(assignment.assigned_to, "pk", None),
                    "review_type": getattr(assignment, "review_type", None),
                    "match_status": _match_status,
                    "exception_count": _exc_count,
                    "priority": priority,
                    "invoice_id": result.invoice_id,
                    "po_number": getattr(result, "po_number", "") or "",
                    "source": "review",
                },
            )
            score_trace_safe(
                _lf_trace_id,
                REVIEW_PRIORITY,
                float(priority) / 10.0,
                comment=(
                    f"assignment={assignment.pk} "
                    f"invoice={result.invoice_id} "
                    f"match_status={_match_status} "
                    f"exceptions={_exc_count}"
                ),
                span=_lf_trace,
            )
            score_trace_safe(
                _lf_trace_id,
                REVIEW_ASSIGNMENT_CREATED,
                1.0,
                comment=f"assignment={assignment.pk}",
                span=_lf_trace,
            )
        except Exception:
            pass

        # core_eval: sync review assignment context (best-effort)
        try:
            from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter
            ReconciliationEvalAdapter.sync_for_review_assignment(assignment)
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
            _lf = get_client()
            if _lf:
                _lf_span = _lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_assign_reviewer",
                    metadata={
                        "reviewer_id": getattr(user, "pk", None),
                        "reviewer_email": getattr(user, "email", ""),
                        "assignment_id": assignment.pk,
                        "status_before": assignment.status or "",
                    },
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
                from apps.core.langfuse_client import end_span_safe
                end_span_safe(_lf_span, output={"status": assignment.status, "reviewer_assigned": True})
            except Exception:
                pass

        return assignment

    @staticmethod
    def start_review(assignment: ReviewAssignment) -> ReviewAssignment:
        _lf_span = None
        try:
            from apps.core.langfuse_client import get_client
            _lf = get_client()
            if _lf:
                _lf_span = _lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_start",
                    metadata={
                        "assignment_id": assignment.pk,
                        "reviewer_id": getattr(assignment.assigned_to, "pk", None),
                        "status_before": assignment.status or "",
                    },
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
                from apps.core.langfuse_client import end_span_safe
                end_span_safe(_lf_span, output={"status": assignment.status, "review_started": True})
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
        tenant=None,
    ) -> ManualReviewAction:
        action = ManualReviewAction.objects.create(
            assignment=assignment,
            performed_by=user,
            action_type=action_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            tenant=tenant,
        )

        # -- Langfuse: record action span
        try:
            from apps.core.langfuse_client import get_client, end_span_safe, score_trace_safe
            _lf = get_client()
            _lf_span = None
            if _lf:
                _lf_span = _lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_record_action",
                    metadata={
                        "action_type": action_type,
                        "field_name": field_name,
                        "user_id": getattr(user, "pk", None),
                    },
                )
            end_span_safe(_lf_span, output={
                "action_id": action.pk,
                "action_type": action_type,
                "field_name": field_name,
                "is_correction": action_type == ReviewActionType.CORRECT_FIELD,
            })
            if action_type == ReviewActionType.CORRECT_FIELD:
                # Running count of corrections -- emit as score for eval
                _correction_count = ManualReviewAction.objects.filter(
                    assignment=assignment, action_type=ReviewActionType.CORRECT_FIELD,
                ).count()
                score_trace_safe(
                    f"review-{assignment.pk}",
                    REVIEW_FIELDS_CORRECTED_COUNT,
                    float(_correction_count),
                    comment=f"field={field_name}",
                    span=_lf_span,
                )
        except Exception:
            pass

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
        tenant=None,
    ) -> ReviewComment:
        comment = ReviewComment.objects.create(
            assignment=assignment,
            author=user,
            body=body,
            is_internal=is_internal,
            tenant=tenant,
        )
        try:
            from apps.core.langfuse_client import get_client, end_span_safe
            _lf = get_client()
            _lf_span = None
            if _lf:
                _lf_span = _lf.span(
                    trace_id=f"review-{assignment.pk}",
                    name="review_add_comment",
                    metadata={
                        "user_id": getattr(user, "pk", None),
                        "is_internal": is_internal,
                    },
                )
            end_span_safe(_lf_span, output={"comment_id": comment.pk})
        except Exception:
            pass
        return comment

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
        _trace_id = f"review-{assignment.pk}"

        # Gather pre-decision metrics for metadata
        _action_count = ManualReviewAction.objects.filter(assignment=assignment).count()
        _comment_count = ReviewComment.objects.filter(assignment=assignment).count()
        _corrections_count = ManualReviewAction.objects.filter(
            assignment=assignment, action_type=ReviewActionType.CORRECT_FIELD,
        ).count()

        try:
            from apps.core.langfuse_client import get_client
            _lf = get_client()
            if _lf:
                _lf_span = _lf.span(
                    trace_id=_trace_id,
                    name="review_finalise",
                    metadata={
                        "decision_status": decision_status,
                        "user_id": getattr(user, "pk", None),
                        "status_before": assignment.status or "",
                        "action_count": _action_count,
                        "comment_count": _comment_count,
                        "fields_corrected_count": _corrections_count,
                        "invoice_id": getattr(assignment.reconciliation_result, "invoice_id", None),
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
                "tenant": getattr(assignment, "tenant", None),
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
            from apps.core.langfuse_client import score_trace_safe
            _decision_score = {
                ReviewStatus.APPROVED: 1.0,
                ReviewStatus.REJECTED: 0.0,
                ReviewStatus.REPROCESSED: 0.5,
            }.get(decision_status, 0.5)
            _invoice_id = getattr(
                getattr(assignment, "reconciliation_result", None),
                "invoice_id", None
            )
            score_trace_safe(
                _trace_id,
                REVIEW_DECISION,
                _decision_score,
                comment=(
                    f"decision={decision_status} "
                    f"invoice={_invoice_id} "
                    f"reviewer={getattr(user, 'pk', None)}"
                ),
                span=_lf_span,
            )
            # Additional eval scores
            score_trace_safe(
                _trace_id,
                REVIEW_APPROVED,
                1.0 if decision_status == ReviewStatus.APPROVED else 0.0,
                span=_lf_span,
            )
            score_trace_safe(
                _trace_id,
                REVIEW_REJECTED,
                1.0 if decision_status == ReviewStatus.REJECTED else 0.0,
                span=_lf_span,
            )
            score_trace_safe(
                _trace_id,
                REVIEW_REPROCESS_REQUESTED,
                1.0 if decision_status == ReviewStatus.REPROCESSED else 0.0,
                span=_lf_span,
            )
            score_trace_safe(
                _trace_id,
                REVIEW_HAD_CORRECTIONS,
                1.0 if _corrections_count > 0 else 0.0,
                comment=f"corrections={_corrections_count}",
                span=_lf_span,
            )
        except Exception:
            pass

        try:
            from apps.core.langfuse_client import end_span_safe
            end_span_safe(_lf_span, output={
                "status": decision_status,
                "action_count": _action_count,
                "corrections_count": _corrections_count,
                "comment_count": _comment_count,
            })
        except Exception:
            pass

        # core_eval: sync review outcome (best-effort)
        try:
            from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter
            ReconciliationEvalAdapter.sync_for_review_outcome(assignment)
        except Exception:
            pass

        return decision

    @staticmethod
    def _record_action(assignment, user, decision_status, reason, tenant=None):
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
            tenant=tenant,
        )
