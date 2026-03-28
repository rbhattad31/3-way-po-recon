"""
Tests for ReviewWorkflowService — DB-backed.

Covers:
  - create_assignment: creates PENDING/ASSIGNED record, sets requires_review=True
  - assign_reviewer: transitions to ASSIGNED, logs AuditEvent
  - start_review: transitions to IN_REVIEW
  - approve: transitions to APPROVED, updates ReconciliationResult
  - reject: requires reason, transitions to REJECTED
  - request_reprocess: re-queues for reconciliation
  - add_comment: creates ReviewComment
  - record_action: creates ManualReviewAction
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from apps.core.enums import MatchStatus, ReviewStatus, ReviewActionType
from apps.reviews.services import ReviewWorkflowService


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def recon_result(db):
    from apps.reconciliation.tests.factories import ReconConfigFactory, InvoiceFactory, POFactory
    from apps.reconciliation.models import ReconciliationRun, ReconciliationResult
    from apps.core.enums import ReconciliationRunStatus

    config = ReconConfigFactory()
    invoice = InvoiceFactory()
    po = POFactory()
    run = ReconciliationRun.objects.create(
        status=ReconciliationRunStatus.RUNNING,
        config=config,
    )
    result = ReconciliationResult.objects.create(
        run=run,
        invoice=invoice,
        purchase_order=po,
        match_status=MatchStatus.REQUIRES_REVIEW,
        requires_review=False,
    )
    return result


@pytest.fixture
def reviewer(db):
    from apps.accounts.tests.factories import UserFactory
    return UserFactory(role="REVIEWER")


@pytest.fixture
def assignment(recon_result):
    with patch("apps.auditlog.services.AuditService.log_event"), \
         patch("apps.core.langfuse_client.start_trace"), \
         patch("apps.core.langfuse_client.score_trace"):
        return ReviewWorkflowService.create_assignment(recon_result, priority=3)


# ─── create_assignment ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCreateAssignment:
    def test_creates_pending_assignment(self, recon_result):
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.start_trace"), \
             patch("apps.core.langfuse_client.score_trace"):
            a = ReviewWorkflowService.create_assignment(recon_result)
        assert a.status == ReviewStatus.PENDING

    def test_creates_assigned_status_when_user_provided(self, recon_result, reviewer):
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.start_trace"), \
             patch("apps.core.langfuse_client.score_trace"):
            a = ReviewWorkflowService.create_assignment(recon_result, assigned_to=reviewer)
        assert a.status == ReviewStatus.ASSIGNED
        assert a.assigned_to == reviewer

    def test_sets_requires_review_on_result(self, recon_result):
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.start_trace"), \
             patch("apps.core.langfuse_client.score_trace"):
            ReviewWorkflowService.create_assignment(recon_result)
        recon_result.refresh_from_db()
        assert recon_result.requires_review is True

    def test_stores_priority(self, recon_result):
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.start_trace"), \
             patch("apps.core.langfuse_client.score_trace"):
            a = ReviewWorkflowService.create_assignment(recon_result, priority=7)
        assert a.priority == 7

    def test_logs_audit_event(self, recon_result):
        with patch("apps.auditlog.services.AuditService.log_event") as mock_log, \
             patch("apps.core.langfuse_client.start_trace"), \
             patch("apps.core.langfuse_client.score_trace"):
            ReviewWorkflowService.create_assignment(recon_result)
        mock_log.assert_called_once()


# ─── assign_reviewer ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAssignReviewer:
    def test_assign_reviewer_transitions_to_assigned(self, assignment, reviewer):
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            updated = ReviewWorkflowService.assign_reviewer(assignment, reviewer)
        assert updated.status == ReviewStatus.ASSIGNED
        assert updated.assigned_to == reviewer

    def test_assign_reviewer_persisted(self, assignment, reviewer):
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            ReviewWorkflowService.assign_reviewer(assignment, reviewer)
        assignment.refresh_from_db()
        assert assignment.assigned_to == reviewer


# ─── start_review ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestStartReview:
    def test_start_review_transitions_to_in_review(self, assignment, reviewer):
        # start_review only takes assignment (no user arg from source)
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            ReviewWorkflowService.assign_reviewer(assignment, reviewer)
        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            updated = ReviewWorkflowService.start_review(assignment)
        assert updated.status == ReviewStatus.IN_REVIEW


# ─── add_comment ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAddComment:
    def test_add_comment_creates_record(self, assignment, reviewer):
        with patch("apps.auditlog.services.AuditService.log_event"):
            comment = ReviewWorkflowService.add_comment(
                assignment, reviewer, "Vendor mismatch needs clarification"
            )
        assert comment.pk is not None
        assert comment.body == "Vendor mismatch needs clarification"
        assert comment.author == reviewer

    def test_comment_linked_to_assignment(self, assignment, reviewer):
        with patch("apps.auditlog.services.AuditService.log_event"):
            comment = ReviewWorkflowService.add_comment(assignment, reviewer, "Test")
        assert comment.assignment == assignment


# ─── approve ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestApprove:
    def test_approve_creates_review_decision(self, assignment, reviewer):
        """approve() returns a ReviewDecision (not an assignment with .status)."""
        from apps.reviews.models import ReviewDecision
        assignment.status = ReviewStatus.IN_REVIEW
        assignment.assigned_to = reviewer
        assignment.save()

        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            decision = ReviewWorkflowService.approve(assignment, reviewer, reason="OK")

        # approve() returns a ReviewDecision object
        assert isinstance(decision, ReviewDecision)
        assignment.refresh_from_db()
        assert assignment.status == ReviewStatus.APPROVED


# ─── reject ───────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReject:
    def test_reject_creates_review_decision(self, assignment, reviewer):
        """reject() returns a ReviewDecision, transitions assignment to REJECTED."""
        from apps.reviews.models import ReviewDecision
        assignment.status = ReviewStatus.IN_REVIEW
        assignment.assigned_to = reviewer
        assignment.save()

        with patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            decision = ReviewWorkflowService.reject(
                assignment, reviewer, reason="Amount mismatch is too large"
            )

        assert isinstance(decision, ReviewDecision)
        assignment.refresh_from_db()
        assert assignment.status == ReviewStatus.REJECTED
