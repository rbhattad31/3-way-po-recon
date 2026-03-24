"""Posting Audit — logs posting events using the existing AuditService pattern."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PostingAuditService:
    """Centralized audit logging for posting operations."""

    @staticmethod
    def log_event(
        event_type: str,
        description: str,
        *,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        batch_id: Optional[int] = None,
        user=None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a posting audit event."""
        try:
            from apps.auditlog.services import AuditService

            entity_type = "Invoice" if invoice_id else "PostingRun"
            entity_id = invoice_id or posting_run_id or 0

            AuditService.log_event(
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                description=description,
                user=user,
                metadata={
                    **(metadata or {}),
                    "posting_run_id": posting_run_id,
                    "batch_id": batch_id,
                },
            )
        except Exception:
            logger.exception(
                "Failed to log posting audit event: %s", event_type
            )
