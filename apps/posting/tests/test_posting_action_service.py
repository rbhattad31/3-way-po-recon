"""Tests for PostingActionService -- approve, reject, submit, retry."""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import patch, MagicMock

from apps.core.enums import InvoicePostingStatus, InvoiceStatus, PostingRunStatus


@pytest.fixture
def _invoice(db):
    """Create a minimal invoice for posting tests."""
    from apps.documents.models import Invoice
    return Invoice.objects.create(
        invoice_number="INV-ACT-001",
        invoice_date=date(2026, 1, 15),
        currency="SAR",
        total_amount=Decimal("1000.00"),
        raw_vendor_name="Test Vendor",
        status=InvoiceStatus.RECONCILED,
    )


@pytest.fixture
def _posting(_invoice):
    """Create an InvoicePosting in MAPPING_REVIEW_REQUIRED status."""
    from apps.posting.models import InvoicePosting
    return InvoicePosting.objects.create(
        invoice=_invoice,
        status=InvoicePostingStatus.MAPPING_REVIEW_REQUIRED,
    )


@pytest.fixture
def _ready_posting(_invoice):
    """Create an InvoicePosting in READY_TO_SUBMIT status."""
    from apps.posting.models import InvoicePosting
    return InvoicePosting.objects.create(
        invoice=_invoice,
        status=InvoicePostingStatus.READY_TO_SUBMIT,
    )


@pytest.fixture
def _user(db):
    from apps.accounts.models import User
    return User.objects.create_user(
        email="action-tester@test.com",
        password="test1234",
    )


class TestApprovePosting:
    """AP-01 to AP-04: Approve posting transition."""

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_approve_review_required(self, mock_audit, mock_gov, _posting, _user):
        """AP-01: Approve from MAPPING_REVIEW_REQUIRED -> READY_TO_SUBMIT."""
        from apps.posting.services.posting_action_service import PostingActionService
        result = PostingActionService.approve_posting(_posting.pk, _user)
        result.refresh_from_db()
        assert result.status == InvoicePostingStatus.READY_TO_SUBMIT

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_approve_already_submitted_fails(self, mock_audit, mock_gov, _invoice, _user):
        """AP-02: Cannot approve a POSTED posting."""
        from apps.posting.models import InvoicePosting
        from apps.posting.services.posting_action_service import PostingActionService
        posting = InvoicePosting.objects.create(
            invoice=_invoice,
            status=InvoicePostingStatus.POSTED,
        )
        with pytest.raises(ValueError, match="[Cc]annot|[Ii]nvalid|status"):
            PostingActionService.approve_posting(posting.pk, _user)

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_approve_with_corrections(self, mock_audit, mock_gov, _posting, _user):
        """AP-03: Corrections are applied during approval."""
        from apps.posting.services.posting_action_service import PostingActionService
        from apps.posting.models import InvoicePostingFieldCorrection
        corrections = {
            "fields": [
                {
                    "entity_type": "mapping",
                    "field_name": "vendor_code",
                    "original_value": "V001",
                    "corrected_value": "V002",
                    "reason": "Wrong vendor",
                },
            ],
        }
        PostingActionService.approve_posting(_posting.pk, _user, corrections=corrections)
        correction_count = InvoicePostingFieldCorrection.objects.filter(posting=_posting).count()
        assert correction_count >= 1


class TestRejectPosting:
    """RJ-01 to RJ-03: Reject posting transition."""

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_reject_review_required(self, mock_audit, mock_gov, _posting, _user):
        """RJ-01: Reject from MAPPING_REVIEW_REQUIRED -> REJECTED."""
        from apps.posting.services.posting_action_service import PostingActionService
        result = PostingActionService.reject_posting(_posting.pk, _user, reason="Bad mapping")
        result.refresh_from_db()
        assert result.status == InvoicePostingStatus.REJECTED

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_reject_posted_fails(self, mock_audit, mock_gov, _invoice, _user):
        """RJ-02: Cannot reject a POSTED posting."""
        from apps.posting.models import InvoicePosting
        from apps.posting.services.posting_action_service import PostingActionService
        posting = InvoicePosting.objects.create(
            invoice=_invoice,
            status=InvoicePostingStatus.POSTED,
        )
        with pytest.raises(ValueError, match="[Cc]annot|[Ii]nvalid|status"):
            PostingActionService.reject_posting(posting.pk, _user)


class TestSubmitPosting:
    """SB-01 to SB-02: Submit posting (Phase 1 mock)."""

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_submit_ready(self, mock_audit, mock_gov, _invoice, _user):
        """SB-01: Submit from READY_TO_SUBMIT -> POSTED (mock)."""
        from apps.posting.models import InvoicePosting
        from apps.posting.services.posting_action_service import PostingActionService
        posting = InvoicePosting.objects.create(
            invoice=_invoice,
            status=InvoicePostingStatus.READY_TO_SUBMIT,
        )
        result = PostingActionService.submit_posting(posting.pk, _user)
        result.refresh_from_db()
        assert result.status == InvoicePostingStatus.POSTED
        assert result.erp_document_number != ""  # mock doc number assigned

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_submit_wrong_status_fails(self, mock_audit, mock_gov, _posting, _user):
        """SB-02: Cannot submit from MAPPING_REVIEW_REQUIRED."""
        from apps.posting.services.posting_action_service import PostingActionService
        with pytest.raises(ValueError, match="[Cc]annot|[Ii]nvalid|status"):
            PostingActionService.submit_posting(_posting.pk, _user)


class TestRetryPosting:
    """RT-01 to RT-02: Retry posting after failure."""

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_orchestrator.PostingOrchestrator")
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_retry_failed(self, mock_audit, mock_gov, mock_orch, _invoice, _user):
        """RT-01: Retry from POST_FAILED re-triggers orchestrator."""
        from apps.posting.models import InvoicePosting
        from apps.posting.services.posting_action_service import PostingActionService
        posting = InvoicePosting.objects.create(
            invoice=_invoice,
            status=InvoicePostingStatus.POST_FAILED,
        )
        mock_orch.prepare_posting.return_value = posting
        result = PostingActionService.retry_posting(posting.pk, _user)
        mock_orch.prepare_posting.assert_called_once()

    @pytest.mark.django_db
    @patch("apps.posting.services.posting_action_service.PostingGovernanceTrailService")
    @patch("apps.posting.services.posting_action_service.PostingAuditService")
    def test_retry_wrong_status(self, mock_audit, mock_gov, _invoice, _user):
        """RT-02: Cannot retry from POSTED status."""
        from apps.posting.models import InvoicePosting
        from apps.posting.services.posting_action_service import PostingActionService
        posting = InvoicePosting.objects.create(
            invoice=_invoice,
            status=InvoicePostingStatus.POSTED,
        )
        with pytest.raises(ValueError, match="[Cc]annot|[Ii]nvalid|status"):
            PostingActionService.retry_posting(posting.pk, _user)
