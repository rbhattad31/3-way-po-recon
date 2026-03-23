"""Service for extraction-focused governance log queries."""
from __future__ import annotations

from django.db.models import QuerySet

from apps.auditlog.models import AuditEvent


# Extraction-relevant event types
EXTRACTION_EVENT_TYPES = [
    "JURISDICTION_RESOLVED",
    "SCHEMA_SELECTED",
    "PROMPT_SELECTED",
    "NORMALIZATION_COMPLETED",
    "VALIDATION_COMPLETED",
    "EVIDENCE_CAPTURED",
    "REVIEW_ROUTE_ASSIGNED",
    "EXTRACTION_REPROCESSED",
    "EXTRACTION_ESCALATED",
    "EXTRACTION_COMMENT_ADDED",
    "SETTINGS_UPDATED",
    "SCHEMA_UPDATED",
    "PROMPT_UPDATED",
    "ROUTING_RULE_UPDATED",
    "ANALYTICS_SNAPSHOT_CREATED",
    "EXTRACTION_STARTED",
    "EXTRACTION_COMPLETED",
    "EXTRACTION_FAILED",
    "FIELD_CORRECTED",
    "EXTRACTION_APPROVED",
    "EXTRACTION_REJECTED",
]


class ExtractionGovernanceService:
    """Queries existing AuditEvent records for extraction-related governance."""

    @classmethod
    def list_events(cls, filters: dict | None = None, limit: int = 200) -> QuerySet:
        qs = AuditEvent.objects.filter(
            event_type__in=EXTRACTION_EVENT_TYPES
        ).order_by("-created_at")

        if filters:
            if filters.get("event_type"):
                qs = qs.filter(event_type=filters["event_type"])
            if filters.get("actor"):
                qs = qs.filter(actor_email__icontains=filters["actor"])
            if filters.get("entity_type"):
                qs = qs.filter(entity_type__iexact=filters["entity_type"])
            if filters.get("access_granted") is not None:
                qs = qs.filter(access_granted=filters["access_granted"])
            if filters.get("date_from"):
                qs = qs.filter(created_at__date__gte=filters["date_from"])
            if filters.get("date_to"):
                qs = qs.filter(created_at__date__lte=filters["date_to"])
            if filters.get("search"):
                from django.db.models import Q
                qs = qs.filter(
                    Q(description__icontains=filters["search"])
                    | Q(entity_type__icontains=filters["search"])
                    | Q(event_type__icontains=filters["search"])
                )
        return qs[:limit]

    @classmethod
    def get_event_type_choices(cls) -> list[tuple[str, str]]:
        return [(e, e.replace("_", " ").title()) for e in EXTRACTION_EVENT_TYPES]

    @classmethod
    def get_recent_governance_changes(cls, limit: int = 10) -> QuerySet:
        """Get recent config/schema/prompt changes."""
        config_events = [
            "SETTINGS_UPDATED", "SCHEMA_UPDATED", "PROMPT_UPDATED",
            "ROUTING_RULE_UPDATED", "ANALYTICS_SNAPSHOT_CREATED",
        ]
        return AuditEvent.objects.filter(
            event_type__in=config_events
        ).order_by("-created_at")[:limit]
