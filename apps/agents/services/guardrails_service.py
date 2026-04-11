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

from apps.core.evaluation_constants import RBAC_DATA_SCOPE, RBAC_GUARDRAIL

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
    "COMPLIANCE_AGENT": "agents.run_compliance",
    "REVIEW_ROUTING": "agents.run_review_routing",
    "CASE_SUMMARY": "agents.run_case_summary",
    # Deterministic system agents
    "SYSTEM_REVIEW_ROUTING": "agents.run_system_review_routing",
    "SYSTEM_CASE_SUMMARY": "agents.run_system_case_summary",
    "SYSTEM_BULK_EXTRACTION_INTAKE": "agents.run_system_bulk_extraction_intake",
    "SYSTEM_CASE_INTAKE": "agents.run_system_case_intake",
    "SYSTEM_POSTING_PREPARATION": "agents.run_system_posting_preparation",
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
            # Ensure legacy role field and RBAC assignment are correct
            if user.role != SYSTEM_AGENT_ROLE_CODE:
                user.role = SYSTEM_AGENT_ROLE_CODE
                user.save(update_fields=["role", "updated_at"])
            cls._assign_system_agent_role(user)
            return user

        user = User.objects.create_user(
            email=SYSTEM_AGENT_EMAIL,
            password=None,
            first_name="System",
            last_name="Agent",
            role=SYSTEM_AGENT_ROLE_CODE,
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
        ur, created = UserRole.objects.get_or_create(
            user=user,
            role=role,
            defaults={"is_primary": True, "is_active": True},
        )
        if not created and not ur.is_primary:
            ur.is_primary = True
            ur.save(update_fields=["is_primary"])
        # Demote any other primary role
        UserRole.objects.filter(user=user, is_primary=True).exclude(role=role).update(is_primary=False)

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
        granted = user.has_permission(perm)
        if granted:
            try:
                from apps.core.langfuse_client import score_trace, get_current_span
                from apps.core.trace import TraceContext
                _ctx = TraceContext.get_current()
                _tid = getattr(_ctx, "trace_id", "") or ""
                if _tid:
                    _role = ""
                    try:
                        _pr = user.get_primary_role()
                        _role = _pr.code if _pr else getattr(user, "role", "")
                    except Exception:
                        pass
                    score_trace(
                        _tid,
                        RBAC_GUARDRAIL,
                        1.0,
                        comment=(
                            f"rbac_guardrail GRANTED method=authorize_agent"
                            f" agent_type={agent_type} user_role={_role}"
                        ),
                        span=get_current_span(),
                    )
            except Exception:
                pass
        return granted

    @classmethod
    def authorize_tool(cls, user, tool_name: str) -> bool:
        """Check per-tool permission.  Unlisted tools are *allowed* (open by default)."""
        perm = TOOL_PERMISSIONS.get(tool_name)
        if not perm:
            return True  # Tools without mapped permissions are unrestricted
        granted = user.has_permission(perm)
        if granted:
            try:
                from apps.core.langfuse_client import score_trace, get_current_span
                from apps.core.trace import TraceContext
                _ctx = TraceContext.get_current()
                _tid = getattr(_ctx, "trace_id", "") or ""
                if _tid:
                    _role = ""
                    try:
                        _pr = user.get_primary_role()
                        _role = _pr.code if _pr else getattr(user, "role", "")
                    except Exception:
                        pass
                    score_trace(
                        _tid,
                        RBAC_GUARDRAIL,
                        1.0,
                        comment=(
                            f"rbac_guardrail GRANTED method=authorize_tool"
                            f" tool={tool_name} user_role={_role}"
                        ),
                        span=get_current_span(),
                    )
            except Exception:
                pass
        return granted

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
    # Langfuse trace ID helper
    # ------------------------------------------------------------------
    @staticmethod
    def _lf_trace_id_for_run(agent_run) -> Optional[str]:
        """Return the Langfuse trace ID for agent_run, or None if unavailable."""
        try:
            return getattr(agent_run, "trace_id", None) or str(agent_run.pk)
        except Exception:
            return None

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

        try:
            from apps.core.langfuse_client import score_trace, get_current_span
            _trace_id = getattr(trace_ctx, "trace_id", "") or ""
            if _trace_id:
                score_trace(
                    _trace_id,
                    RBAC_GUARDRAIL,
                    1.0 if granted else 0.0,
                    comment=(
                        f"action={action} "
                        f"permission={permission_code} "
                        f"role={snapshot.get('actor_primary_role', '')} "
                        f"granted={granted}"
                    ),
                    span=get_current_span(),
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Data-scope authorization (action + data boundary enforcement)
    # ------------------------------------------------------------------

    @classmethod
    def get_actor_scope(cls, actor) -> Dict[str, Any]:
        """Return the data-scope restrictions attached to this actor's roles.

        The returned dict has the following keys:
          - allowed_business_units: list[str] or None (None = unrestricted)
          - allowed_vendor_ids:     list[int] or None (None = unrestricted)

        Scope values are read from UserRole.scope_json for each active,
        non-expired role assignment.  The union of all allowed values across
        all role assignments applies (i.e. multiple roles grant additive scope).

        ADMIN and SYSTEM_AGENT actors are always unrestricted and return None
        for every dimension.

        Unsupported / pending dimensions (require schema extension on Invoice
        or PurchaseOrder before they can be evaluated here):
          - country / legal_entity
          - cost_centre
        """
        from apps.accounts.rbac_models import UserRole

        # ADMIN and SYSTEM_AGENT bypass all scope checks
        role_codes: List[str] = []
        try:
            role_codes = list(actor.get_role_codes())
        except Exception:
            pass
        if "ADMIN" in role_codes or getattr(actor, "email", "") == SYSTEM_AGENT_EMAIL:
            return {"allowed_business_units": None, "allowed_vendor_ids": None}

        allowed_bus: set = set()
        allowed_vendor_ids: set = set()
        any_bus_restriction = False
        any_vendor_restriction = False

        active_roles = (
            UserRole.objects.filter(user=actor, is_active=True)
            .select_related("role")
        )
        for ur in active_roles:
            # Respect expiry without hitting the DB again
            if ur.expires_at and ur.expires_at < timezone.now():
                continue
            scope = ur.scope_json or {}
            bus = scope.get("allowed_business_units")
            vids = scope.get("allowed_vendor_ids")
            if bus is not None:
                any_bus_restriction = True
                allowed_bus.update(str(b) for b in bus)
            if vids is not None:
                any_vendor_restriction = True
                allowed_vendor_ids.update(int(v) for v in vids)

        return {
            "allowed_business_units": sorted(allowed_bus) if any_bus_restriction else None,
            "allowed_vendor_ids": sorted(allowed_vendor_ids) if any_vendor_restriction else None,
        }

    @classmethod
    def get_result_scope(cls, result) -> Dict[str, Any]:
        """Extract data-scope dimensions available on a ReconciliationResult.

        Dimensions extracted from current schema:
          - business_unit: ReconciliationPolicy.business_unit (via result.policy_applied)
          - vendor_id:     result.invoice.vendor_id

        Dimensions NOT available in current schema (documented for future extension):
          - country / legal_entity: no country_code field on Invoice or PurchaseOrder
          - cost_centre: no cost_centre field on Invoice or PurchaseOrder
        """
        scope: Dict[str, Any] = {
            "business_unit": None,
            "vendor_id": None,
        }

        # Business unit from the applied reconciliation policy
        if getattr(result, "policy_applied", None):
            try:
                from apps.reconciliation.models import ReconciliationPolicy
                policy = (
                    ReconciliationPolicy.objects
                    .filter(policy_code=result.policy_applied)
                    .values("business_unit")
                    .first()
                )
                if policy and policy["business_unit"]:
                    scope["business_unit"] = policy["business_unit"]
            except Exception:
                pass

        # Vendor scope from the linked invoice
        try:
            scope["vendor_id"] = result.invoice.vendor_id
        except Exception:
            pass

        return scope

    @classmethod
    def _scope_value_allowed(cls, allowed_values, result_value) -> bool:
        """Return True if result_value is permitted by the actor scope list.

        Semantics:
          - allowed_values = None  -> no restriction on this dimension (pass)
          - result_value = None    -> result has no value for dim (pass-through)
          - otherwise              -> result_value must be in allowed_values
        """
        if allowed_values is None:
            return True
        if result_value is None:
            return True
        return result_value in allowed_values

    @classmethod
    def authorize_data_scope(cls, actor, result) -> bool:
        """Check whether *actor* may operate on result's data scope.

        Called immediately after action-level authorization in the orchestrator.
        Fails closed only when scope metadata is configured on the actor AND
        the result -- if neither side carries scope metadata, existing behavior
        is preserved (all pass).

        Currently enforced dimensions:
          - business_unit (from ReconciliationPolicy linked via result.policy_applied)
          - vendor_id     (from result.invoice.vendor_id)

        Pending dimensions (schema extension required before enforcement):
          - country / legal_entity
          - cost_centre

        Every allow/deny decision is written as an AuditEvent for the
        governance trail.
        """
        actor_scope = cls.get_actor_scope(actor)
        result_scope = cls.get_result_scope(result)

        denial_reasons: List[str] = []

        if not cls._scope_value_allowed(
            actor_scope.get("allowed_business_units"),
            result_scope.get("business_unit"),
        ):
            denial_reasons.append(
                "business_unit '{}' not in actor allowed set {}".format(
                    result_scope["business_unit"],
                    actor_scope["allowed_business_units"],
                )
            )

        if not cls._scope_value_allowed(
            actor_scope.get("allowed_vendor_ids"),
            result_scope.get("vendor_id"),
        ):
            denial_reasons.append(
                "vendor_id {} not in actor allowed set {}".format(
                    result_scope["vendor_id"],
                    actor_scope["allowed_vendor_ids"],
                )
            )

        granted = not denial_reasons
        reason = "; ".join(denial_reasons) if denial_reasons else "all scope checks passed"

        cls.log_guardrail_decision(
            user=actor,
            action="data_scope_check",
            permission_code="agents.data_scope",
            granted=granted,
            entity_type="ReconciliationResult",
            entity_id=getattr(result, "pk", None),
            metadata={
                "actor_scope": actor_scope,
                "result_scope": result_scope,
                "denial_reason": reason,
            },
        )

        if not granted:
            logger.warning(
                "Data scope denied: actor=%s result=%s -- %s",
                getattr(actor, "email", actor.pk),
                getattr(result, "pk", "?"),
                reason,
            )

            try:
                from apps.core.langfuse_client import score_trace, get_current_span
                from apps.core.trace import TraceContext
                _ctx = TraceContext.get_current()
                _trace_id = getattr(_ctx, "trace_id", "") or ""
                if _trace_id:
                    score_trace(
                        _trace_id,
                        RBAC_DATA_SCOPE,
                        0.0,
                        comment=(
                            f"actor={getattr(actor, 'pk', None)} "
                            f"result={getattr(result, 'pk', None)}"
                        ),
                        span=get_current_span(),
                    )
            except Exception:
                pass

        return granted
