"""
Tests for AgentGuardrailsService — all authorize_* methods and resolve_actor.

Covers:
  - authorize_orchestration: checks agents.orchestrate permission
  - authorize_agent: per-agent-type permission mapping (8 agent types)
  - authorize_tool: per-tool permission (6 tools + unknown = open by default)
  - authorize_recommendation: per-recommendation-type permission (6 types)
  - authorize_action: per-named-action permission (5 actions)
  - ensure_permission: raises PermissionDenied when lacking
  - resolve_actor: returns request user if authenticated, else system agent
  - get_system_agent_user: creates/returns the system-agent user
  - build_rbac_snapshot: captures correct role metadata

Source:
  apps/agents/services/guardrails_service.py
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from django.core.exceptions import PermissionDenied

from apps.agents.services.guardrails_service import (
    AgentGuardrailsService,
    ORCHESTRATE_PERMISSION,
    AGENT_PERMISSIONS,
    TOOL_PERMISSIONS,
    RECOMMENDATION_PERMISSIONS,
    ACTION_PERMISSIONS,
    SYSTEM_AGENT_EMAIL,
)
from apps.accounts.tests.factories import (
    UserFactory, RoleFactory, PermissionFactory,
    RolePermissionFactory, UserRoleFactory,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_user_with_perm(perm_code: str, role_code: str = "AP_PROCESSOR"):
    """Create a user that has a specific permission via a role."""
    user = UserFactory(role=role_code)
    role = RoleFactory(code=role_code + "_G_" + perm_code.replace(".", "_")[:15])
    parts = perm_code.split(".")
    perm = PermissionFactory(code=perm_code, module=parts[0],
                             action=parts[1] if len(parts) > 1 else "action")
    RolePermissionFactory(role=role, permission=perm, is_allowed=True)
    UserRoleFactory(user=user, role=role, is_primary=True, is_active=True)
    return user


def make_user_without_perm(role_code: str = "REVIEWER"):
    """Create a user with no permissions at all."""
    user = UserFactory(role=role_code)
    role = RoleFactory(code=role_code + "_EMPTY_G")
    UserRoleFactory(user=user, role=role, is_primary=True, is_active=True)
    return user


# ─── authorize_orchestration ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthorizeOrchestration:
    def test_granted_with_agents_orchestrate_perm(self):
        """User with agents.orchestrate permission is granted orchestration."""
        user = make_user_with_perm("agents.orchestrate")
        assert AgentGuardrailsService.authorize_orchestration(user) is True

    def test_denied_without_agents_orchestrate_perm(self):
        """User without agents.orchestrate permission is denied orchestration."""
        user = make_user_without_perm()
        assert AgentGuardrailsService.authorize_orchestration(user) is False

    def test_admin_always_granted(self):
        """ADMIN user always has orchestration permission."""
        user = UserFactory(role="ADMIN")
        assert AgentGuardrailsService.authorize_orchestration(user) is True


# ─── authorize_agent ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthorizeAgent:
    @pytest.mark.parametrize("agent_type,perm_code", list(AGENT_PERMISSIONS.items()))
    def test_known_agent_type_granted_with_permission(self, agent_type, perm_code):
        """Each known agent type is granted when the user has the mapped permission."""
        user = make_user_with_perm(perm_code, role_code="AP_PROC_" + agent_type[:6])
        assert AgentGuardrailsService.authorize_agent(user, agent_type) is True

    @pytest.mark.parametrize("agent_type", list(AGENT_PERMISSIONS.keys()))
    def test_known_agent_type_denied_without_permission(self, agent_type):
        """Each known agent type is denied when the user lacks the mapped permission."""
        user = make_user_without_perm(role_code="REVIEWER_" + agent_type[:6])
        assert AgentGuardrailsService.authorize_agent(user, agent_type) is False

    def test_unknown_agent_type_denied(self):
        """An agent type not in AGENT_PERMISSIONS is always denied."""
        user = UserFactory(role="ADMIN")  # Even admin
        assert AgentGuardrailsService.authorize_agent(user, "UNKNOWN_AGENT") is False

    def test_invoice_extraction_and_understanding_share_permission(self):
        """INVOICE_EXTRACTION and INVOICE_UNDERSTANDING both map to agents.run_extraction."""
        assert AGENT_PERMISSIONS["INVOICE_EXTRACTION"] == AGENT_PERMISSIONS["INVOICE_UNDERSTANDING"]
        assert AGENT_PERMISSIONS["INVOICE_EXTRACTION"] == "agents.run_extraction"


# ─── authorize_tool ───────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthorizeTool:
    @pytest.mark.parametrize("tool_name,perm_code", list(TOOL_PERMISSIONS.items()))
    def test_known_tool_granted_with_permission(self, tool_name, perm_code):
        """Each known tool is granted when the user has the mapped permission."""
        user = make_user_with_perm(perm_code, role_code="TOOL_" + tool_name[:8])
        assert AgentGuardrailsService.authorize_tool(user, tool_name) is True

    def test_known_tool_denied_without_permission(self):
        """A tool with a mapped permission is denied when the user lacks it."""
        user = make_user_without_perm()
        # po_lookup requires purchase_orders.view
        assert AgentGuardrailsService.authorize_tool(user, "po_lookup") is False

    def test_unknown_tool_is_open_by_default(self):
        """A tool name not in TOOL_PERMISSIONS is allowed (open by default)."""
        user = make_user_without_perm()
        assert AgentGuardrailsService.authorize_tool(user, "some_new_tool_xyz") is True

    def test_tool_permission_map_has_six_entries(self):
        """Sanity check: exactly 6 tools are mapped."""
        assert len(TOOL_PERMISSIONS) == 6


# ─── authorize_recommendation ────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthorizeRecommendation:
    @pytest.mark.parametrize("rec_type,perm_code", list(RECOMMENDATION_PERMISSIONS.items()))
    def test_known_rec_type_granted_with_permission(self, rec_type, perm_code):
        """Each known recommendation type is granted with the mapped permission."""
        user = make_user_with_perm(perm_code, role_code="REC_" + rec_type[:8])
        assert AgentGuardrailsService.authorize_recommendation(user, rec_type) is True

    @pytest.mark.parametrize("rec_type", list(RECOMMENDATION_PERMISSIONS.keys()))
    def test_known_rec_type_denied_without_permission(self, rec_type):
        """Each recommendation type is denied without its mapped permission."""
        user = make_user_without_perm(role_code="RECD_" + rec_type[:6])
        assert AgentGuardrailsService.authorize_recommendation(user, rec_type) is False

    def test_unknown_rec_type_denied(self):
        """Unknown recommendation type is always denied (not in RECOMMENDATION_PERMISSIONS)."""
        user = UserFactory(role="ADMIN")
        assert AgentGuardrailsService.authorize_recommendation(user, "UNKNOWN_REC_TYPE") is False

    def test_auto_close_maps_to_auto_close_permission(self):
        """AUTO_CLOSE recommendation maps to recommendations.auto_close."""
        assert RECOMMENDATION_PERMISSIONS["AUTO_CLOSE"] == "recommendations.auto_close"


# ─── authorize_action ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthorizeAction:
    @pytest.mark.parametrize("action_name,perm_code", list(ACTION_PERMISSIONS.items()))
    def test_known_action_granted_with_permission(self, action_name, perm_code):
        """Each named action is granted with the mapped permission."""
        user = make_user_with_perm(perm_code, role_code="ACT_" + action_name[:8])
        assert AgentGuardrailsService.authorize_action(user, action_name) is True

    @pytest.mark.parametrize("action_name", list(ACTION_PERMISSIONS.keys()))
    def test_known_action_denied_without_permission(self, action_name):
        """Each named action is denied without its mapped permission."""
        user = make_user_without_perm(role_code="ACTD_" + action_name[:6])
        assert AgentGuardrailsService.authorize_action(user, action_name) is False

    def test_unknown_action_denied(self):
        """Unknown action name is always denied."""
        user = UserFactory(role="ADMIN")
        assert AgentGuardrailsService.authorize_action(user, "do_something_unregistered") is False

    def test_rerun_reconciliation_maps_to_reconciliation_run(self):
        """rerun_reconciliation maps to reconciliation.run permission."""
        assert ACTION_PERMISSIONS["rerun_reconciliation"] == "reconciliation.run"


# ─── ensure_permission ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestEnsurePermission:
    def test_ensure_permission_passes_silently_when_granted(self):
        """ensure_permission does not raise when user has the permission."""
        user = make_user_with_perm("reconciliation.view")
        AgentGuardrailsService.ensure_permission(user, "reconciliation.view")  # no raise

    def test_ensure_permission_raises_permission_denied_when_not_granted(self):
        """ensure_permission raises PermissionDenied when user lacks permission."""
        user = make_user_without_perm()
        with pytest.raises(PermissionDenied):
            AgentGuardrailsService.ensure_permission(user, "reconciliation.run")

    def test_ensure_permission_custom_error_message(self):
        """Custom error_message is included in the PermissionDenied exception."""
        user = make_user_without_perm()
        with pytest.raises(PermissionDenied, match="custom error"):
            AgentGuardrailsService.ensure_permission(
                user, "reconciliation.run", error_message="custom error"
            )


# ─── resolve_actor ────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestResolveActor:
    def test_returns_request_user_when_authenticated(self):
        """resolve_actor returns the provided user when authenticated.

        Django User.is_authenticated is a read-only property (always True for
        active users). We use a real UserFactory user which is always
        authenticated by Django convention.
        """
        user = UserFactory(is_active=True)
        # Django AbstractBaseUser: is_authenticated is True for any saved user
        assert user.is_authenticated is True
        resolved = AgentGuardrailsService.resolve_actor(request_user=user)
        assert resolved == user

    def test_returns_system_agent_when_no_user(self):
        """resolve_actor returns the system-agent user when request_user is None."""
        with patch.object(AgentGuardrailsService, "get_system_agent_user") as mock_sys:
            mock_sys.return_value = MagicMock(email=SYSTEM_AGENT_EMAIL)
            resolved = AgentGuardrailsService.resolve_actor(request_user=None)
        mock_sys.assert_called_once()
        assert resolved.email == SYSTEM_AGENT_EMAIL

    def test_returns_system_agent_when_user_not_authenticated(self):
        """resolve_actor returns system-agent when user is_authenticated=False."""
        user = MagicMock()
        user.is_authenticated = False
        with patch.object(AgentGuardrailsService, "get_system_agent_user") as mock_sys:
            mock_sys.return_value = MagicMock(email=SYSTEM_AGENT_EMAIL)
            AgentGuardrailsService.resolve_actor(request_user=user)
        mock_sys.assert_called_once()


# ─── get_system_agent_user ────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetSystemAgentUser:
    def test_creates_system_agent_user_if_not_exists(self):
        """get_system_agent_user() creates the system-agent user on first call."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        User.objects.filter(email=SYSTEM_AGENT_EMAIL).delete()

        user = AgentGuardrailsService.get_system_agent_user()
        assert user.email == SYSTEM_AGENT_EMAIL
        assert user.is_active is True

    def test_returns_existing_system_agent_user(self):
        """get_system_agent_user() returns existing user on subsequent calls."""
        # Ensure created
        user1 = AgentGuardrailsService.get_system_agent_user()
        user2 = AgentGuardrailsService.get_system_agent_user()
        assert user1.pk == user2.pk  # Same record, not duplicated


# ─── build_rbac_snapshot ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestBuildRBACSnapshot:
    def test_snapshot_contains_required_keys(self):
        """build_rbac_snapshot() returns dict with all required audit keys."""
        user = UserFactory(role="AP_PROCESSOR")
        snapshot = AgentGuardrailsService.build_rbac_snapshot(user)
        required_keys = {
            "actor_user_id", "actor_email", "actor_primary_role",
            "actor_roles_snapshot", "permission_source",
        }
        assert required_keys.issubset(snapshot.keys())

    def test_snapshot_has_user_pk_and_email(self):
        """Snapshot captures correct user identity fields."""
        user = UserFactory(email="test.agent@example.com", role="REVIEWER")
        snapshot = AgentGuardrailsService.build_rbac_snapshot(user)
        assert snapshot["actor_user_id"] == user.pk
        assert snapshot["actor_email"] == "test.agent@example.com"

    def test_system_agent_permission_source(self):
        """System-agent user gets permission_source='SYSTEM_AGENT'."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.filter(email=SYSTEM_AGENT_EMAIL).first() \
            or AgentGuardrailsService.get_system_agent_user()
        snapshot = AgentGuardrailsService.build_rbac_snapshot(user)
        assert snapshot["permission_source"] == "SYSTEM_AGENT"

    def test_regular_user_permission_source(self):
        """Regular user gets permission_source='USER'."""
        user = UserFactory(email="regular@example.com", role="REVIEWER")
        snapshot = AgentGuardrailsService.build_rbac_snapshot(user)
        assert snapshot["permission_source"] == "USER"
