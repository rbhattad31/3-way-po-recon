"""Audit service — records and queries governance events.

Enhanced for enterprise-grade audit with:
- TraceContext integration (trace_id, span_id propagation)
- RBAC snapshot at action time (actor roles, permission, source)
- Status before/after tracking
- Redacted payload snapshots
- Efficient cross-reference queries
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.auditlog.models import AuditEvent
from apps.core.logging_utils import redact_dict, summarize_payload

logger = logging.getLogger(__name__)


class AuditService:
    """Write and query audit events for governance traceability.

    Primary entry point for all business-significant events:
    - invoice uploaded / extraction completed / duplicate flagged
    - reconciliation triggered / completed / mode resolved
    - review assigned / approved / rejected / field corrected
    - override applied / reprocess requested / case rerouted / closed
    - role/permission changes / access denied for sensitive actions

    Do NOT use this for operational noise — use ProcessingLog instead.
    """

    @staticmethod
    def log_event(
        entity_type: str,
        entity_id: int,
        event_type: str,
        description: str = "",
        user=None,
        agent: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        *,
        # Trace context
        trace_ctx=None,
        # Business context
        invoice_id: Optional[int] = None,
        case_id: Optional[int] = None,
        reconciliation_result_id: Optional[int] = None,
        review_assignment_id: Optional[int] = None,
        agent_run_id: Optional[int] = None,
        # Status tracking
        status_before: str = "",
        status_after: str = "",
        reason_code: str = "",
        # Payloads (will be redacted)
        input_snapshot: Optional[Dict[str, Any]] = None,
        output_snapshot: Optional[Dict[str, Any]] = None,
        # Operational
        duration_ms: Optional[int] = None,
        error_code: str = "",
    ) -> AuditEvent:
        """Create a compliance-grade governance audit event.

        Args:
            entity_type: Business entity (e.g. 'Invoice', 'ReconciliationResult').
            entity_id: PK of the entity.
            event_type: AuditEventType enum value.
            description: Human-readable summary.
            user: Django User who performed the action.
            agent: Agent name if performed by an agent.
            metadata: Additional structured context.
            trace_ctx: TraceContext for correlation/RBAC. Falls back to thread-local.
            invoice_id: Cross-reference to Invoice (auto-resolved from entity if possible).
            case_id: Cross-reference to APCase.
            reconciliation_result_id: Cross-reference to ReconciliationResult.
            review_assignment_id: Cross-reference to ReviewAssignment.
            agent_run_id: Cross-reference to AgentRun.
            status_before: Previous status value (for state transitions).
            status_after: New status value (for state transitions).
            reason_code: Machine-readable reason code.
            input_snapshot: Input data (will be summarized and redacted).
            output_snapshot: Output data (will be summarized and redacted).
            duration_ms: Operation duration in milliseconds.
            error_code: Error code if applicable.
        """
        # Resolve trace context
        if trace_ctx is None:
            from apps.core.trace import TraceContext
            trace_ctx = TraceContext.get_current()

        # Extract RBAC snapshot from trace context or user
        trace_id = ""
        span_id = ""
        parent_span_id = ""
        actor_email = ""
        actor_primary_role = ""
        actor_roles_snapshot = None
        permission_checked = ""
        permission_source = ""
        access_granted = None

        if trace_ctx:
            trace_id = trace_ctx.trace_id
            span_id = trace_ctx.span_id
            parent_span_id = trace_ctx.parent_span_id
            actor_email = trace_ctx.actor_email
            actor_primary_role = trace_ctx.actor_primary_role
            if trace_ctx.actor_roles_snapshot:
                actor_roles_snapshot = trace_ctx.actor_roles_snapshot
            permission_checked = trace_ctx.permission_checked
            permission_source = trace_ctx.permission_source
            access_granted = trace_ctx.access_granted
            # Auto-resolve cross-references from trace context
            invoice_id = invoice_id or trace_ctx.invoice_id
            case_id = case_id or trace_ctx.case_id
            reconciliation_result_id = reconciliation_result_id or trace_ctx.reconciliation_result_id
            review_assignment_id = review_assignment_id or trace_ctx.review_assignment_id
            agent_run_id = agent_run_id or trace_ctx.agent_run_id

        # Fallback: resolve actor from user
        if user and not actor_email:
            actor_email = getattr(user, "email", "")
        if user and not actor_primary_role:
            actor_primary_role = getattr(user, "role", "")

        # Auto-resolve invoice_id from entity if not provided
        if not invoice_id and entity_type == "Invoice":
            invoice_id = entity_id

        # Redact/summarize payloads
        safe_input = redact_dict(summarize_payload(input_snapshot)) if input_snapshot else None
        safe_output = redact_dict(summarize_payload(output_snapshot)) if output_snapshot else None

        # Resolve tenant from user
        _tenant = getattr(user, 'company', None) if user else None

        event = AuditEvent.objects.create(
            entity_type=entity_type,
            entity_id=entity_id,
            action=event_type,
            event_type=event_type,
            event_description=description[:2000] if description else "",
            performed_by=user,
            performed_by_agent=agent,
            metadata_json=metadata,
            tenant=_tenant,
            # Trace
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            # Cross-references
            invoice_id=invoice_id,
            case_id=case_id,
            reconciliation_result_id=reconciliation_result_id,
            review_assignment_id=review_assignment_id,
            agent_run_id=agent_run_id,
            # RBAC snapshot
            actor_email=actor_email,
            actor_primary_role=actor_primary_role,
            actor_roles_snapshot_json=actor_roles_snapshot,
            permission_checked=permission_checked,
            permission_source=permission_source,
            access_granted=access_granted,
            # Business context
            status_before=status_before,
            status_after=status_after,
            reason_code=reason_code,
            # Payloads
            input_snapshot_json=safe_input,
            output_snapshot_json=safe_output,
            # Operational
            duration_ms=duration_ms,
            error_code=error_code,
        )
        logger.info(
            "AuditEvent: %s on %s#%s by %s [trace=%s]",
            event_type, entity_type, entity_id,
            agent or actor_email or (user.email if user else "system"),
            trace_id[:12] if trace_id else "—",
        )
        return event

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    @staticmethod
    def fetch_entity_history(
        entity_type: str,
        entity_id: int,
        tenant=None,
    ) -> List[Dict[str, Any]]:
        """Return all audit events for a given entity, ordered chronologically."""
        qs = AuditEvent.objects.filter(
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return list(
            qs.values(
                "id", "action", "event_type", "event_description",
                "performed_by__email", "performed_by_agent",
                "metadata_json", "created_at",
                "trace_id", "actor_email", "actor_primary_role",
                "actor_roles_snapshot_json", "permission_checked",
                "permission_source", "access_granted",
                "status_before", "status_after", "reason_code",
                "duration_ms", "error_code",
            ).order_by("created_at")
        )

    @staticmethod
    def fetch_invoice_history(invoice_id: int) -> List[Dict[str, Any]]:
        """Return all audit events linked to an invoice — both entity_type='Invoice'
        and cross-reference via invoice_id field."""
        from django.db.models import Q
        return list(
            AuditEvent.objects.filter(
                Q(entity_type="Invoice", entity_id=invoice_id) |
                Q(invoice_id=invoice_id)
            ).values(
                "id", "action", "event_type", "event_description",
                "entity_type", "entity_id",
                "performed_by__email", "performed_by_agent",
                "metadata_json", "created_at",
                "trace_id", "actor_email", "actor_primary_role",
                "actor_roles_snapshot_json", "permission_checked",
                "permission_source", "access_granted",
                "status_before", "status_after", "reason_code",
                "duration_ms", "error_code",
            ).order_by("created_at").distinct()
        )

    @staticmethod
    def fetch_case_history(case_id: int, tenant=None) -> List[Dict[str, Any]]:
        """Return all audit events linked to a case."""
        qs = AuditEvent.objects.filter(case_id=case_id)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return list(
            qs.values(
                "id", "action", "event_type", "event_description",
                "entity_type", "entity_id",
                "performed_by__email", "performed_by_agent",
                "metadata_json", "created_at",
                "trace_id", "actor_email", "actor_primary_role",
                "actor_roles_snapshot_json", "permission_checked",
                "permission_source", "access_granted",
                "status_before", "status_after", "reason_code",
                "duration_ms", "error_code",
            ).order_by("created_at")
        )

    @staticmethod
    def fetch_access_history(
        invoice_id: Optional[int] = None,
        case_id: Optional[int] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        tenant=None,
    ) -> List[Dict[str, Any]]:
        """Return audit events where RBAC context was captured (sensitive actions)."""
        from django.db.models import Q
        qs = AuditEvent.objects.exclude(permission_checked="")
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        if invoice_id:
            qs = qs.filter(Q(entity_type="Invoice", entity_id=invoice_id) | Q(invoice_id=invoice_id))
        if case_id:
            qs = qs.filter(case_id=case_id)
        if entity_type and entity_id:
            qs = qs.filter(entity_type=entity_type, entity_id=entity_id)
        return list(
            qs.values(
                "id", "event_type", "event_description",
                "actor_email", "actor_primary_role", "actor_roles_snapshot_json",
                "permission_checked", "permission_source", "access_granted",
                "created_at",
            ).order_by("-created_at")
        )

    @staticmethod
    def fetch_permission_denials(limit: int = 50, tenant=None) -> List[Dict[str, Any]]:
        """Return recent permission denials for governance dashboard."""
        qs = AuditEvent.objects.filter(access_granted=False)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return list(
            qs.values(
                "id", "event_type", "event_description",
                "entity_type", "entity_id",
                "actor_email", "actor_primary_role",
                "permission_checked", "permission_source",
                "created_at",
            ).order_by("-created_at")[:limit]
        )

    @staticmethod
    def fetch_rbac_activity(limit: int = 50, tenant=None) -> List[Dict[str, Any]]:
        """Return recent RBAC-related events."""
        rbac_types = [
            "ROLE_ASSIGNED", "ROLE_REMOVED", "ROLE_PERMISSION_CHANGED",
            "USER_PERMISSION_OVERRIDE", "USER_ACTIVATED", "USER_DEACTIVATED",
            "ROLE_CREATED", "ROLE_UPDATED", "PRIMARY_ROLE_CHANGED",
        ]
        qs = AuditEvent.objects.filter(event_type__in=rbac_types)
        if tenant is not None:
            qs = qs.filter(tenant=tenant)
        return list(
            qs.values(
                "id", "event_type", "event_description",
                "actor_email", "actor_primary_role",
                "metadata_json", "created_at",
            ).order_by("-created_at")[:limit]
        )
