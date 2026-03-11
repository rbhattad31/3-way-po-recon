"""Audit service — records and queries governance events."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.auditlog.models import AuditEvent

logger = logging.getLogger(__name__)


class AuditService:
    """Write and query audit events for governance traceability."""

    @staticmethod
    def log_event(
        entity_type: str,
        entity_id: int,
        event_type: str,
        description: str = "",
        user=None,
        agent: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        """Create a governance audit event.

        Args:
            entity_type: The business entity type (e.g. 'Invoice', 'ReconciliationResult').
            entity_id: PK of the entity.
            event_type: AuditEventType enum value.
            description: Human-readable event description.
            user: Optional Django User instance who performed the action.
            agent: Optional agent name if action performed by an agent.
            metadata: Additional structured context (stored as JSON).
        """
        event = AuditEvent.objects.create(
            entity_type=entity_type,
            entity_id=entity_id,
            action=event_type,
            event_type=event_type,
            event_description=description,
            performed_by=user,
            performed_by_agent=agent,
            metadata_json=metadata,
        )
        logger.info(
            "AuditEvent: %s on %s#%s by %s",
            event_type, entity_type, entity_id,
            agent or (user.email if user else "system"),
        )
        return event

    @staticmethod
    def fetch_entity_history(
        entity_type: str,
        entity_id: int,
    ) -> List[Dict[str, Any]]:
        """Return all audit events for a given entity, ordered chronologically."""
        return list(
            AuditEvent.objects.filter(
                entity_type=entity_type,
                entity_id=entity_id,
            ).values(
                "id", "action", "event_type", "event_description",
                "performed_by__email", "performed_by_agent",
                "metadata_json", "created_at",
            ).order_by("created_at")
        )

    @staticmethod
    def fetch_invoice_history(invoice_id: int) -> List[Dict[str, Any]]:
        """Return all audit events for an invoice (entity_type='Invoice')."""
        return AuditService.fetch_entity_history("Invoice", invoice_id)
