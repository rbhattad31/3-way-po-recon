"""Posting Governance Trail — mirrors posting decisions to PostingApprovalRecord.

This service is the ONLY writer of PostingApprovalRecord, following the same
pattern as GovernanceTrailService in extraction_core.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.posting_core.models import PostingApprovalRecord, PostingRun

logger = logging.getLogger(__name__)


class PostingGovernanceTrailService:
    """Writes governance mirror records for posting decisions."""

    @classmethod
    @transaction.atomic
    def record_posting_decision(
        cls,
        run: PostingRun,
        action: str,
        user=None,
        comments: str = "",
    ) -> PostingApprovalRecord:
        """Record a posting approval/rejection decision.

        Uses update_or_create inside atomic block for idempotency.
        """
        record, created = PostingApprovalRecord.objects.update_or_create(
            posting_run=run,
            defaults={
                "action": action,
                "approved_by": user,
                "comments": comments,
                "decided_at": timezone.now(),
                "tenant": getattr(run, "tenant", None),
            },
        )

        verb = "Created" if created else "Updated"
        logger.info(
            "%s PostingApprovalRecord for run %s: action=%s by=%s",
            verb, run.pk, action, user,
        )
        return record
