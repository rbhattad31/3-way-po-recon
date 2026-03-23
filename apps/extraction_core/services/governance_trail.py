"""GovernanceTrailService — sole writer of ExtractionApprovalRecord.

Writes governance mirror records to ExtractionApprovalRecord.
Called by ExtractionApprovalService after business state transitions.
This is the only permitted writer of ExtractionApprovalRecord.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from apps.extraction_core.models import ExtractionApprovalRecord, ExtractionRun

logger = logging.getLogger(__name__)


class GovernanceTrailService:
    """
    Writes governance mirror records to ExtractionApprovalRecord.
    Called by ExtractionApprovalService after business state transitions.
    This is the only permitted writer of ExtractionApprovalRecord.
    """

    @staticmethod
    def record_approval_decision(
        run: ExtractionRun,
        action: str,           # APPROVE | REJECT | ESCALATE | SEND_BACK
        user,
        comments: str = "",
    ) -> ExtractionApprovalRecord:
        """
        Creates a new ExtractionApprovalRecord for the given run and action.
        On reprocess, a new row is always created — previous records are
        retained as history and never updated.
        """
        # Map business action names to ExtractionApprovalAction enum values
        action_map = {
            "APPROVE": "APPROVED",
            "APPROVED": "APPROVED",
            "REJECT": "REJECTED",
            "REJECTED": "REJECTED",
            "ESCALATE": "ESCALATED",
            "ESCALATED": "ESCALATED",
            "SEND_BACK": "REPROCESSED",
            "REPROCESSED": "REPROCESSED",
        }
        mapped_action = action_map.get(action.upper(), action.upper())

        record = ExtractionApprovalRecord.objects.create(
            extraction_run=run,
            action=mapped_action,
            approved_by=user,
            comments=comments,
            decided_at=timezone.now(),
        )

        logger.info(
            "Governance trail: recorded %s for ExtractionRun %s by %s",
            mapped_action, run.pk, user,
        )
        return record
