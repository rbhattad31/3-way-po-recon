"""
Tests for RBAC scope_json enforcement via AgentGuardrailsService.

scope_json on UserRole restricts what data an actor can operate on:
  - allowed_vendor_ids: list[int] — actor can only see these vendors
  - allowed_business_units: list[str] — actor can only see these BUs
  - null scope_json means unrestricted (all data)
  - ADMIN always bypasses scope
  - SYSTEM_AGENT always bypasses scope
  - Multiple roles: union of allowed values across all role assignments
  - Expired role assignments are excluded from scope computation

Sources:
  apps/agents/services/guardrails_service.py::get_actor_scope()
  apps/agents/services/guardrails_service.py::get_result_scope()
  apps/agents/services/guardrails_service.py::authorize_data_scope()
  apps/agents/services/guardrails_service.py::_scope_value_allowed()
"""
from __future__ import annotations

import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch
from django.utils import timezone

from apps.agents.services.guardrails_service import AgentGuardrailsService
from apps.accounts.tests.factories import UserFactory, RoleFactory, UserRoleFactory


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_result_mock(vendor_id=None, policy_applied=""):
    result = MagicMock()
    result.pk = 1
    result.policy_applied = policy_applied
    result.invoice = MagicMock()
    result.invoice.vendor_id = vendor_id
    return result


def make_scoped_user(role_code, scope_json=None, role_rank=50):
    """Create user with a role assignment that has scope_json."""
    user = UserFactory(role=role_code)
    role = RoleFactory(code=role_code + "_SCOPE", rank=role_rank)
    UserRoleFactory(user=user, role=role, is_primary=True,
                    is_active=True, scope_json=scope_json)
    return user, role


# ─── get_actor_scope ──────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetActorScope:
    def test_null_scope_json_means_unrestricted(self):
        """A UserRole with scope_json=None means unrestricted on all dimensions."""
        user, _ = make_scoped_user("AP_PROCESSOR", scope_json=None)
        scope = AgentGuardrailsService.get_actor_scope(user)
        assert scope["allowed_business_units"] is None
        assert scope["allowed_vendor_ids"] is None

    def test_vendor_id_scope_returned(self):
        """scope_json with allowed_vendor_ids is extracted correctly."""
        user, _ = make_scoped_user("AP_PROCESSOR", scope_json={
            "allowed_vendor_ids": [1, 2, 3]
        })
        scope = AgentGuardrailsService.get_actor_scope(user)
        assert scope["allowed_vendor_ids"] == [1, 2, 3]
        assert scope["allowed_business_units"] is None

    def test_business_unit_scope_returned(self):
        """scope_json with allowed_business_units is extracted correctly."""
        user, _ = make_scoped_user("AP_PROCESSOR", scope_json={
            "allowed_business_units": ["BU-NORTH", "BU-SOUTH"]
        })
        scope = AgentGuardrailsService.get_actor_scope(user)
        assert scope["allowed_business_units"] == ["BU-NORTH", "BU-SOUTH"]
        assert scope["allowed_vendor_ids"] is None

    def test_admin_always_unrestricted(self):
        """ADMIN role bypasses scope — returns None for all dimensions.

        The bypass check reads get_role_codes() which returns Role.code values
        from active UserRole assignments. We must assign a Role with code='ADMIN'.
        """
        user = UserFactory(role="ADMIN")
        # Create Role with code=ADMIN and assign to user
        admin_role = RoleFactory(code="ADMIN", is_system_role=True)
        UserRoleFactory(user=user, role=admin_role, is_primary=True,
                        is_active=True, scope_json={"allowed_vendor_ids": [99]})
        user.clear_permission_cache()
        scope = AgentGuardrailsService.get_actor_scope(user)
        assert scope["allowed_vendor_ids"] is None
        assert scope["allowed_business_units"] is None

    def test_system_agent_always_unrestricted(self):
        """SYSTEM_AGENT email bypasses scope."""
        from apps.agents.services.guardrails_service import SYSTEM_AGENT_EMAIL
        user = UserFactory(email=SYSTEM_AGENT_EMAIL, role="AP_PROCESSOR")
        scope = AgentGuardrailsService.get_actor_scope(user)
        assert scope["allowed_vendor_ids"] is None
        assert scope["allowed_business_units"] is None

    def test_multiple_roles_union_of_vendor_ids(self):
        """Two scoped role assignments — vendor_ids are merged (union)."""
        user = UserFactory(role="AP_PROCESSOR")
        role_a = RoleFactory(code="ROLE_SCOPE_A")
        role_b = RoleFactory(code="ROLE_SCOPE_B")
        UserRoleFactory(user=user, role=role_a, is_primary=True, is_active=True,
                        scope_json={"allowed_vendor_ids": [1, 2]})
        UserRoleFactory(user=user, role=role_b, is_primary=False, is_active=True,
                        scope_json={"allowed_vendor_ids": [3, 4]})
        scope = AgentGuardrailsService.get_actor_scope(user)
        assert set(scope["allowed_vendor_ids"]) == {1, 2, 3, 4}

    def test_expired_role_excluded_from_scope(self):
        """Expired UserRole assignment is not included in scope computation."""
        user = UserFactory(role="AP_PROCESSOR")
        role = RoleFactory(code="EXPIRED_SCOPE_ROLE")
        UserRoleFactory(
            user=user, role=role, is_primary=True, is_active=True,
            scope_json={"allowed_vendor_ids": [99]},
            expires_at=timezone.now() - timedelta(hours=1),
        )
        scope = AgentGuardrailsService.get_actor_scope(user)
        # Expired role excluded — no vendor restriction remains
        assert scope["allowed_vendor_ids"] is None

    def test_one_restricted_one_unrestricted_role_stays_restricted(self):
        """If one role has scope_json and another does not, restriction applies."""
        user = UserFactory(role="AP_PROCESSOR")
        role_restricted = RoleFactory(code="RESTRICTED_ROLE")
        role_open = RoleFactory(code="OPEN_ROLE")
        UserRoleFactory(user=user, role=role_restricted, is_active=True,
                        scope_json={"allowed_vendor_ids": [5]})
        UserRoleFactory(user=user, role=role_open, is_active=True,
                        scope_json=None)
        scope = AgentGuardrailsService.get_actor_scope(user)
        # The restricted role still limits — null scope_json on the other role
        # does NOT cancel the restriction (only ADMIN/SYSTEM_AGENT does)
        assert scope["allowed_vendor_ids"] is not None
        assert 5 in scope["allowed_vendor_ids"]


# ─── _scope_value_allowed ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestScopeValueAllowed:
    def test_none_allowed_values_passes_everything(self):
        """allowed_values=None -> unrestricted, any result_value passes."""
        assert AgentGuardrailsService._scope_value_allowed(None, 42) is True
        assert AgentGuardrailsService._scope_value_allowed(None, None) is True

    def test_none_result_value_passes_through(self):
        """result_value=None -> pass-through (no data to restrict against)."""
        assert AgentGuardrailsService._scope_value_allowed([1, 2, 3], None) is True

    def test_result_value_in_allowed_passes(self):
        """result_value in allowed_values -> True."""
        assert AgentGuardrailsService._scope_value_allowed([1, 2, 3], 2) is True

    def test_result_value_not_in_allowed_fails(self):
        """result_value not in allowed_values -> False."""
        assert AgentGuardrailsService._scope_value_allowed([1, 2, 3], 99) is False

    def test_empty_allowed_list_blocks_all(self):
        """Empty allowed_values list blocks all non-None result values."""
        assert AgentGuardrailsService._scope_value_allowed([], 1) is False
        assert AgentGuardrailsService._scope_value_allowed([], None) is True


# ─── authorize_data_scope ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAuthorizeDataScope:
    def test_unrestricted_actor_always_passes(self):
        """Actor with no scope restriction can access any result."""
        user, _ = make_scoped_user("AP_PROCESSOR", scope_json=None)
        result = make_result_mock(vendor_id=99)
        with patch.object(AgentGuardrailsService, "log_guardrail_decision"):
            granted = AgentGuardrailsService.authorize_data_scope(user, result)
        assert granted is True

    def test_vendor_scoped_actor_allowed_for_matching_vendor(self):
        """Actor restricted to vendor 1 can access a result belonging to vendor 1."""
        user, _ = make_scoped_user("AP_PROCESSOR",
                                   scope_json={"allowed_vendor_ids": [1]})
        result = make_result_mock(vendor_id=1)
        with patch.object(AgentGuardrailsService, "log_guardrail_decision"):
            granted = AgentGuardrailsService.authorize_data_scope(user, result)
        assert granted is True

    def test_vendor_scoped_actor_denied_for_different_vendor(self):
        """Actor restricted to vendor 1 cannot access a result for vendor 99."""
        user, _ = make_scoped_user("AP_PROCESSOR",
                                   scope_json={"allowed_vendor_ids": [1]})
        result = make_result_mock(vendor_id=99)
        with patch.object(AgentGuardrailsService, "log_guardrail_decision"):
            granted = AgentGuardrailsService.authorize_data_scope(user, result)
        assert granted is False

    def test_result_with_no_vendor_passes_scope_check(self):
        """Result with vendor_id=None passes scope (no data to restrict against)."""
        user, _ = make_scoped_user("AP_PROCESSOR",
                                   scope_json={"allowed_vendor_ids": [1, 2]})
        result = make_result_mock(vendor_id=None)
        with patch.object(AgentGuardrailsService, "log_guardrail_decision"):
            granted = AgentGuardrailsService.authorize_data_scope(user, result)
        assert granted is True

    def test_bu_scope_denied(self):
        """Actor restricted to BU-NORTH cannot access result for BU-SOUTH."""
        user, _ = make_scoped_user("AP_PROCESSOR",
                                   scope_json={"allowed_business_units": ["BU-NORTH"]})
        result = make_result_mock(vendor_id=None)
        result.policy_applied = "POL-SOUTH"

        # Patch get_result_scope to return a BU that's not in allowed list
        with patch.object(AgentGuardrailsService, "get_result_scope",
                          return_value={"business_unit": "BU-SOUTH", "vendor_id": None}), \
             patch.object(AgentGuardrailsService, "log_guardrail_decision"):
            granted = AgentGuardrailsService.authorize_data_scope(user, result)
        assert granted is False

    def test_authorize_data_scope_logs_guardrail_decision(self):
        """authorize_data_scope always calls log_guardrail_decision."""
        user, _ = make_scoped_user("AP_PROCESSOR", scope_json=None)
        result = make_result_mock(vendor_id=1)
        with patch.object(AgentGuardrailsService, "log_guardrail_decision") as mock_log:
            AgentGuardrailsService.authorize_data_scope(user, result)
        mock_log.assert_called_once()
