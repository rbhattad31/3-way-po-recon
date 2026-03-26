"""Posting Action Service — approve, reject, submit, retry posting.

All actions use transaction.atomic() + select_for_update() for concurrency safety.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    InvoicePostingStatus,
    PostingApprovalAction,
    PostingRunStatus,
)
from apps.core.decorators import observed_service
from apps.posting.models import InvoicePosting, InvoicePostingFieldCorrection
from apps.posting_core.models import PostingRun
from apps.posting_core.services.posting_audit import PostingAuditService
from apps.posting_core.services.posting_governance_trail import PostingGovernanceTrailService

logger = logging.getLogger(__name__)

# Statuses that allow approval
APPROVABLE_STATUSES = {
    InvoicePostingStatus.MAPPING_REVIEW_REQUIRED,
    InvoicePostingStatus.READY_TO_SUBMIT,
}

SUBMITTABLE_STATUSES = {
    InvoicePostingStatus.READY_TO_SUBMIT,
}

REJECTABLE_STATUSES = {
    InvoicePostingStatus.MAPPING_REVIEW_REQUIRED,
    InvoicePostingStatus.READY_TO_SUBMIT,
    InvoicePostingStatus.POST_FAILED,
}


class PostingActionService:
    """Manages posting lifecycle actions."""

    @classmethod
    @observed_service("posting.approve", entity_type="InvoicePosting", audit_event="POSTING_APPROVED")
    @transaction.atomic
    def approve_posting(
        cls,
        posting_id: int,
        user,
        corrections: Optional[dict] = None,
    ) -> InvoicePosting:
        """Approve a posting — optionally apply field corrections."""
        posting = (
            InvoicePosting.objects
            .select_for_update()
            .get(pk=posting_id)
        )

        if posting.status not in APPROVABLE_STATUSES:
            raise ValueError(
                f"Cannot approve posting {posting.pk}: status is {posting.status}"
            )

        # Apply corrections if any
        if corrections:
            cls._apply_corrections(posting, corrections, user)

        posting.status = InvoicePostingStatus.READY_TO_SUBMIT
        posting.reviewed_by = user
        posting.reviewed_at = timezone.now()
        posting.review_queue = ""
        posting.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_queue", "updated_at",
        ])

        # Governance trail
        latest_run = cls._get_latest_run(posting)
        if latest_run:
            PostingGovernanceTrailService.record_posting_decision(
                run=latest_run,
                action=PostingApprovalAction.APPROVED,
                user=user,
            )

        PostingAuditService.log_event(
            AuditEventType.POSTING_APPROVED,
            f"Posting approved by {user}",
            invoice_id=posting.invoice_id,
            posting_run_id=latest_run.pk if latest_run else None,
            user=user,
        )

        logger.info("Posting %s approved by %s", posting.pk, user)
        return posting

    @classmethod
    @observed_service("posting.reject", entity_type="InvoicePosting", audit_event="POSTING_REJECTED")
    @transaction.atomic
    def reject_posting(
        cls,
        posting_id: int,
        user,
        reason: str = "",
    ) -> InvoicePosting:
        """Reject a posting."""
        posting = (
            InvoicePosting.objects
            .select_for_update()
            .get(pk=posting_id)
        )

        if posting.status not in REJECTABLE_STATUSES:
            raise ValueError(
                f"Cannot reject posting {posting.pk}: status is {posting.status}"
            )

        posting.status = InvoicePostingStatus.REJECTED
        posting.reviewed_by = user
        posting.reviewed_at = timezone.now()
        posting.rejection_reason = reason
        posting.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at",
        ])

        latest_run = cls._get_latest_run(posting)
        if latest_run:
            PostingGovernanceTrailService.record_posting_decision(
                run=latest_run,
                action=PostingApprovalAction.REJECTED,
                user=user,
                comments=reason,
            )

        PostingAuditService.log_event(
            AuditEventType.POSTING_REJECTED,
            f"Posting rejected by {user}: {reason}",
            invoice_id=posting.invoice_id,
            posting_run_id=latest_run.pk if latest_run else None,
            user=user,
            metadata={"reason": reason},
        )

        logger.info("Posting %s rejected by %s: %s", posting.pk, user, reason)
        return posting

    @classmethod
    @observed_service("posting.submit", entity_type="InvoicePosting", audit_event="POSTING_SUBMITTED")
    @transaction.atomic
    def submit_posting(
        cls,
        posting_id: int,
        user,
    ) -> InvoicePosting:
        """Submit a posting — mock implementation for Phase 1.

        In Phase 2+, this would call the ERP connector / RPA bridge.
        """
        posting = (
            InvoicePosting.objects
            .select_for_update()
            .get(pk=posting_id)
        )

        if posting.status not in SUBMITTABLE_STATUSES:
            raise ValueError(
                f"Cannot submit posting {posting.pk}: status is {posting.status}"
            )

        posting.status = InvoicePostingStatus.SUBMISSION_IN_PROGRESS
        posting.save(update_fields=["status", "updated_at"])

        # ── Phase 1 Mock Submission ──
        # In production Phase 2+, this would:
        # 1. Call ERP connector with posting.payload_snapshot_json
        # 2. Wait for response / RPA confirmation
        # 3. Handle ERP errors and retry logic
        mock_doc_number = f"MOCK-{posting.invoice.invoice_number}-{posting.pk}"

        posting.status = InvoicePostingStatus.POSTED
        posting.erp_document_number = mock_doc_number
        posting.save(update_fields=[
            "status", "erp_document_number", "updated_at",
        ])

        PostingAuditService.log_event(
            AuditEventType.POSTING_SUCCEEDED,
            f"Posting submitted (mock): {mock_doc_number}",
            invoice_id=posting.invoice_id,
            user=user,
            metadata={"erp_document_number": mock_doc_number, "mock": True},
        )

        logger.info(
            "Posting %s submitted (mock) — doc number: %s",
            posting.pk, mock_doc_number,
        )
        return posting

    @classmethod
    @observed_service("posting.retry", entity_type="InvoicePosting")
    def retry_posting(
        cls,
        posting_id: int,
        user,
    ) -> InvoicePosting:
        """Retry posting — creates a new PostingRun while preserving history."""
        posting = InvoicePosting.objects.get(pk=posting_id)

        if posting.status not in {
            InvoicePostingStatus.POST_FAILED,
            InvoicePostingStatus.RETRY_PENDING,
            InvoicePostingStatus.MAPPING_REVIEW_REQUIRED,
        }:
            raise ValueError(
                f"Cannot retry posting {posting.pk}: status is {posting.status}"
            )

        posting.retry_count += 1
        posting.status = InvoicePostingStatus.RETRY_PENDING
        posting.save(update_fields=["retry_count", "status", "updated_at"])

        # Re-run via orchestrator
        from apps.posting.services.posting_orchestrator import PostingOrchestrator
        return PostingOrchestrator.prepare_posting(
            posting.invoice_id,
            user=user,
            trigger="retry",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_latest_run(posting: InvoicePosting) -> Optional[PostingRun]:
        """Get the latest PostingRun for this posting's invoice."""
        return (
            PostingRun.objects
            .filter(invoice_id=posting.invoice_id)
            .order_by("-created_at")
            .first()
        )

    @staticmethod
    def _apply_corrections(
        posting: InvoicePosting,
        corrections: dict,
        user,
    ) -> None:
        """Apply field corrections and create correction records."""
        records = []
        for correction in corrections.get("fields", []):
            records.append(InvoicePostingFieldCorrection(
                posting=posting,
                entity_type=correction.get("entity_type", "mapping"),
                entity_id=correction.get("entity_id"),
                field_name=correction.get("field_name", ""),
                original_value=correction.get("original_value", ""),
                corrected_value=correction.get("corrected_value", ""),
                corrected_by=user,
                reason=correction.get("reason", ""),
            ))

        if records:
            InvoicePostingFieldCorrection.objects.bulk_create(records)

            PostingAuditService.log_event(
                AuditEventType.POSTING_FIELD_CORRECTED,
                f"{len(records)} field(s) corrected during posting review",
                invoice_id=posting.invoice_id,
                user=user,
                metadata={
                    "correction_count": len(records),
                    "fields": [r.field_name for r in records],
                },
            )
