"""GovernanceTrailService — sole writer of ExtractionApprovalRecord.

ExtractionApprovalRecord is the immutable governed audit decision trail for
ExtractionRun.  All writes MUST go through this service — no viewset, view,
or other service should create/update ExtractionApprovalRecord directly.

Business approval state lives in ExtractionApproval (apps.extraction.models).
This record mirrors decisions for governance / audit purposes only.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.extraction_core.models import ExtractionApprovalRecord, ExtractionRun

logger = logging.getLogger(__name__)


class GovernanceTrailService:
    """
    Sole writer of ExtractionApprovalRecord.

    ExtractionApproval  = business-facing approval state machine (pending → approved/rejected)
    ExtractionApprovalRecord = governed execution trail (immutable audit mirror)

    Call the appropriate helper for each business action:
      record_approval_decision()   — approve / reject
      record_reprocess_decision()  — reprocess / send-back
      record_escalation()          — escalate to different queue
    """

    # Map caller-supplied action strings to model enum values
    _ACTION_MAP = {
        "APPROVE": "APPROVED",
        "APPROVED": "APPROVED",
        "REJECT": "REJECTED",
        "REJECTED": "REJECTED",
        "ESCALATE": "ESCALATED",
        "ESCALATED": "ESCALATED",
        "SEND_BACK": "REPROCESSED",
        "REPROCESSED": "REPROCESSED",
    }

    # ------------------------------------------------------------------
    # Primary entry point (approve / reject)
    # ------------------------------------------------------------------

    @classmethod
    def record_approval_decision(
        cls,
        run: ExtractionRun,
        action: str,           # APPROVE | REJECT | ESCALATE | SEND_BACK
        user,
        comments: str = "",
    ) -> ExtractionApprovalRecord:
        """Record an approval or rejection decision for the given run.

        Uses update_or_create so that re-decisions (e.g. post-reprocess
        second approval) update the existing record rather than violating
        the OneToOne constraint.
        """
        mapped_action = cls._ACTION_MAP.get(action.upper(), action.upper())

        with transaction.atomic():
            record, created = ExtractionApprovalRecord.objects.update_or_create(
                extraction_run=run,
                defaults={
                    "action": mapped_action,
                    "approved_by": user,
                    "comments": comments,
                    "decided_at": timezone.now(),
                },
            )

        verb = "Created" if created else "Updated"
        logger.info(
            "Governance trail: %s %s for ExtractionRun %s by %s",
            verb, mapped_action, run.pk, user,
        )
        return record

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @classmethod
    def record_reprocess_decision(
        cls,
        run: ExtractionRun,
        user,
        comments: str = "",
    ) -> ExtractionApprovalRecord:
        """Record a reprocess / send-back decision."""
        return cls.record_approval_decision(
            run=run, action="SEND_BACK", user=user, comments=comments,
        )

    @classmethod
    def record_escalation(
        cls,
        run: ExtractionRun,
        user,
        target_queue: str = "",
        comments: str = "",
    ) -> ExtractionApprovalRecord:
        """Record an escalation to a different review queue."""
        full_comments = f"Escalated to {target_queue}. {comments}".strip() if target_queue else comments
        return cls.record_approval_decision(
            run=run, action="ESCALATE", user=user, comments=full_comments,
        )
