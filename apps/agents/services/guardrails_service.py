"""Agent Guardrails Service — centralised RBAC enforcement for the agent subsystem.

Every agent-related action flows through this service before execution:
- orchestration triggers
- individual agent runs
- tool invocations
- recommendation acceptance
- protected status transitions (auto-close, escalation, re-reconciliation)

Design principles:
- Single responsibility: RBAC checks + audit, no business logic.
- Fail-closed: if identity cannot be resolved, deny by default.
- System-agent identity: autonomous operations run under a dedicated,
  least-privilege ``SYSTEM_AGENT`` role (never admin bypass).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.core.exceptions import PermissionDenied
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission maps — centralised, single source of truth
# ---------------------------------------------------------------------------

ORCHESTRATE_PERMISSION = "agents.orchestrate"

AGENT_PERMISSIONS: Dict[str, str] = {
    "INVOICE_EXTRACTION": "agents.run_extraction",
    "INVOICE_UNDERSTANDING": "agents.run_extraction",
    "PO_RETRIEVAL": "agents.run_po_retrieval",
    "GRN_RETRIEVAL": "agents.run_grn_retrieval",
    "RECONCILIATION_ASSIST": "agents.run_reconciliation_assist",
    "EXCEPTION_ANALYSIS": "agents.run_exception_analysis",
    "REVIEW_ROUTING": "agents.run_review_routing",
    "CASE_SUMMARY": "agents.run_case_summary",
}

TOOL_PERMISSIONS: Dict[str, str] = {
    "po_lookup": "purchase_orders.view",
    "grn_lookup": "grns.view",
    "vendor_search": "vendors.view",
    "invoice_details": "invoices.view",
    "exception_list": "reconciliation.view",
    "reconciliation_summary": "reconciliation.view",
}

RECOMMENDATION_PERMISSIONS: Dict[str, str] = {
    "AUTO_CLOSE": "recommendations.auto_close",
    "SEND_TO_AP_REVIEW": "recommendations.route_review",
    "ESCALATE_TO_MANAGER": "recommendations.escalate",
    "REPROCESS_EXTRACTION": "recommendations.reprocess",
    "SEND_TO_PROCUREMENT": "recommendations.route_procurement",
    "SEND_TO_VENDOR_CLARIFICATION": "recommendations.vendor_clarification",
}

ACTION_PERMISSIONS: Dict[str, str] = {
    "auto_close_result": "recommendations.auto_close",
    "assign_review": "reviews.assign",
    "escalate_case": "cases.escalate",
    "reprocess_extraction": "extraction.reprocess",
    "rerun_reconciliation": "reconciliation.run",
}

# Internal email used to identify the system-agent user.
SYSTEM_AGENT_EMAIL = "system-agent@internal"
SYSTEM_AGENT_ROLE_CODE = "SYSTEM_AGENT"


class AgentGuardrailsService:
    """Centralised RBAC enforcement for the AI agent architecture."""

    # ------------------------------------------------------------------
    # System agent identity
    # ------------------------------------------------------------------
    @classmethod
    def get_system_agent_user(cls):
        """Return (or create) the dedicated system-agent user.

        This user is assigned the ``SYSTEM_AGENT`` role with scoped
        permissions — it is **not** an admin bypass.
        """
        from apps.accounts.models import User

        user = User.objects.filter(email=SYSTEM_AGENT_EMAIL).first()
        if user:
            return user

        user = User.objects.create_user(
            email=SYSTEM_AGENT_EMAIL,
            password=None,
            first_name="System",
            last_name="Agent",
            is_active=True,
            is_staff=False,
        )
        # Assign SYSTEM_AGENT role if it exists
        cls._assign_system_agent_role(user)
        logger.info("Created system-agent user: %s", user.email)
        return user

    @classmethod
    def _assign_system_agent_role(cls, user) -> None:
        """Assign the SYSTEM_AGENT role to the user if not already assigned."""
        from apps.accounts.rbac_models import Role, UserRole

        role = Role.objects.filter(code=SYSTEM_AGENT_ROLE_CODE, is_active=True).first()
        if not role:
            logger.warning("SYSTEM_AGENT role not found — run seed_rbac first")
            return
        UserRole.objects.get_or_create(
            user=user,
            role=role,
            defaults={"is_primary": True, "is_active": True},
        )

    # ------------------------------------------------------------------
    # Actor resolution
    # ------------------------------------------------------------------
    @classmethod
    def resolve_actor(cls, request_user=None):
        """Resolve the acting user: explicit request user or system agent.

        Returns a Django User instance — never ``None``.
        """
        if request_user and getattr(request_user, "is_authenticated", False):
            return request_user
        return cls.get_system_agent_user()

    # ------------------------------------------------------------------
    # Authorization checks (fail-closed: raise PermissionDenied)
    # ------------------------------------------------------------------
    @classmethod
    def authorize_orchestration(cls, user) -> bool:
        """Check ``agents.orchestrate`` permission."""
        return user.has_permission(ORCHESTRATE_PERMISSION)

    @classmethod
    def authorize_agent(cls, user, agent_type: str) -> bool:
        """Check per-agent permission.  Unknown agents are denied."""
        perm = AGENT_PERMISSIONS.get(agent_type)
        if not perm:
            return False
        return user.has_permission(perm)

    @classmethod
    def authorize_tool(cls, user, tool_name: str) -> bool:
        """Check per-tool permission.  Unlisted tools are *allowed* (open by default)."""
        perm = TOOL_PERMISSIONS.get(tool_name)
        if not perm:
            return True  # Tools without mapped permissions are unrestricted
        return user.has_permission(perm)

    @classmethod
    def authorize_recommendation(cls, user, recommendation_type: str) -> bool:
        """Check recommendation-type permission."""
        perm = RECOMMENDATION_PERMISSIONS.get(recommendation_type)
        if not perm:
            return False
        return user.has_permission(perm)

    @classmethod
    def authorize_action(cls, user, action_name: str) -> bool:
        """Check a named protected-action permission."""
        perm = ACTION_PERMISSIONS.get(action_name)
        if not perm:
            return False
        return user.has_permission(perm)

    @classmethod
    def ensure_permission(cls, user, permission_code: str, error_message: str = "") -> None:
        """Raise ``PermissionDenied`` if the user lacks the permission."""
        if not user.has_permission(permission_code):
            msg = error_message or f"Permission denied: {permission_code}"
            raise PermissionDenied(msg)

    # ------------------------------------------------------------------
    # RBAC snapshot builders
    # ------------------------------------------------------------------
    @classmethod
    def build_rbac_snapshot(cls, user) -> Dict[str, Any]:
        """Capture the current RBAC state for audit trail storage."""
        primary_role = None
        roles_snapshot: List[str] = []
        try:
            pr = user.get_primary_role()
            primary_role = pr.code if pr else getattr(user, "role", "")
        except Exception:
            primary_role = getattr(user, "role", "")
        try:
            roles_snapshot = list(user.get_role_codes())
        except Exception:
            roles_snapshot = [primary_role] if primary_role else []

        is_system = getattr(user, "email", "") == SYSTEM_AGENT_EMAIL
        permission_source = "SYSTEM_AGENT" if is_system else "USER"

        return {
            "actor_user_id": user.pk,
            "actor_email": getattr(user, "email", ""),
            "actor_primary_role": primary_role or "",
            "actor_roles_snapshot": roles_snapshot,
            "permission_source": permission_source,
        }

    @classmethod
    def build_trace_context_for_agent(cls, user, *, permission_checked: str = "", access_granted: bool = True):
        """Create a TraceContext child enriched with agent RBAC metadata."""
        from apps.core.trace import TraceContext

        parent = TraceContext.get_current() or TraceContext.new_root(
            source_service="agents.guardrails",
            source_layer="AGENT",
        )
        snapshot = cls.build_rbac_snapshot(user)
        return parent.child(
            source_service="agents.orchestrator",
            source_layer="AGENT",
            actor_user_id=snapshot["actor_user_id"],
            actor_email=snapshot["actor_email"],
            actor_primary_role=snapshot["actor_primary_role"],
            actor_roles_snapshot=snapshot["actor_roles_snapshot"],
            permission_checked=permission_checked,
            permission_source=snapshot["permission_source"],
            access_granted=access_granted,
        )

    # ------------------------------------------------------------------
    # Guardrail audit logging
    # ------------------------------------------------------------------
    @classmethod
    def log_guardrail_decision(
        cls,
        *,
        user,
        action: str,
        permission_code: str,
        granted: bool,
        entity_type: str = "",
        entity_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write an AuditEvent recording a guardrail allow / deny decision."""
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType

        snapshot = cls.build_rbac_snapshot(user)
        event_type = (
            AuditEventType.GUARDRAIL_GRANTED
            if granted
            else AuditEventType.GUARDRAIL_DENIED
        )
        description = (
            f"{'Granted' if granted else 'Denied'}: {action} "
            f"(permission={permission_code}, actor={snapshot['actor_email']}, "
            f"role={snapshot['actor_primary_role']})"
        )

        # Build a trace context to ensure RBAC fields populate on AuditEvent
        trace_ctx = cls.build_trace_context_for_agent(
            user,
            permission_checked=permission_code,
            access_granted=granted,
        )

        AuditService.log_event(
            entity_type=entity_type or "AgentGuardrail",
            entity_id=entity_id or 0,
            event_type=event_type,
            description=description,
            user=user,
            trace_ctx=trace_ctx,
            metadata={
                **(metadata or {}),
                "guardrail_action": action,
                "permission_code": permission_code,
                "granted": granted,
                "actor_primary_role": snapshot["actor_primary_role"],
                "actor_roles_snapshot": snapshot["actor_roles_snapshot"],
            },
        )
