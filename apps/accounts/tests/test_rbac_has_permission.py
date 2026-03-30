"""
Tests for User.has_permission() — RBAC permission precedence rules.

Precedence (highest to lowest, from source):
  1. ADMIN role/field -> always True
  2. User-level DENY override -> False (blocks even if role grants it)
  3. User-level ALLOW override -> True (grants even if role doesn't)
  4. Role-level permission -> True if any active non-expired role grants it
  5. Default -> False

Also covers:
  - Expired role assignments are excluded
  - Expired overrides are not applied
  - Multiple roles: union of all granted permissions
  - Permission cache is built once per instance and cleared on demand
"""
from __future__ import annotations

import pytest
from datetime import timedelta
from django.utils import timezone
from apps.accounts.tests.factories import (
    UserFactory, RoleFactory, PermissionFactory,
    RolePermissionFactory, UserRoleFactory, UserPermissionOverrideFactory,
)
from apps.core.enums import PermissionOverrideType


# ─── Helper ──────────────────────────────────────────────────────────────────

def make_user_with_role_and_perm(perm_code="reconciliation.run", role_code="AP_PROCESSOR"):
    """Create user → role → permission chain."""
    user = UserFactory(role=role_code)
    role = RoleFactory(code=role_code)
    perm = PermissionFactory(code=perm_code, module=perm_code.split(".")[0],
                             action=perm_code.split(".")[1])
    RolePermissionFactory(role=role, permission=perm, is_allowed=True)
    UserRoleFactory(user=user, role=role, is_primary=True, is_active=True)
    return user, role, perm


# ─── Rule 1: ADMIN always passes ─────────────────────────────────────────────

@pytest.mark.django_db
class TestAdminBypass:
    def test_admin_legacy_field_bypasses_all(self):
        """User with role='ADMIN' has all permissions regardless of DB records."""
        user = UserFactory(role="ADMIN")
        assert user.has_permission("anything.at.all") is True

    def test_admin_role_code_in_user_roles_bypasses(self):
        """User assigned an ADMIN Role record also bypasses all checks."""
        user = UserFactory(role="AP_PROCESSOR")
        admin_role = RoleFactory(code="ADMIN", is_system_role=True)
        UserRoleFactory(user=user, role=admin_role, is_primary=True, is_active=True)
        user.clear_permission_cache()
        assert user.has_permission("reconciliation.run") is True

    def test_admin_not_blocked_by_deny_override(self):
        """ADMIN bypass fires before override checks — DENY override is irrelevant."""
        user = UserFactory(role="ADMIN")
        perm = PermissionFactory(code="invoices.delete")
        UserPermissionOverrideFactory(
            user=user, permission=perm,
            override_type=PermissionOverrideType.DENY,
        )
        assert user.has_permission("invoices.delete") is True


# ─── Rule 2: DENY override blocks even role-granted permissions ───────────────

@pytest.mark.django_db
class TestDenyOverride:
    def test_deny_override_blocks_role_granted_permission(self):
        """A DENY override removes a permission that the role would otherwise grant."""
        user, role, perm = make_user_with_role_and_perm("reconciliation.run", "AP_PROCESSOR")
        # Confirm role grants it without override
        assert user.has_permission("reconciliation.run") is True

        # Now add a DENY override
        user.clear_permission_cache()
        UserPermissionOverrideFactory(
            user=user, permission=perm,
            override_type=PermissionOverrideType.DENY,
        )
        user.clear_permission_cache()
        assert user.has_permission("reconciliation.run") is False

    def test_deny_override_only_removes_specific_permission(self):
        """DENY for one permission does not affect other granted permissions."""
        user, role, perm1 = make_user_with_role_and_perm("reconciliation.run", "AP_PROCESSOR")
        perm2 = PermissionFactory(code="invoices.view", module="invoices", action="view")
        RolePermissionFactory(role=role, permission=perm2, is_allowed=True)

        # DENY on reconciliation.run only
        UserPermissionOverrideFactory(
            user=user, permission=perm1,
            override_type=PermissionOverrideType.DENY,
        )
        user.clear_permission_cache()

        assert user.has_permission("reconciliation.run") is False
        assert user.has_permission("invoices.view") is True

    def test_expired_deny_override_does_not_block(self):
        """An expired DENY override is not applied — role permission should pass."""
        user, role, perm = make_user_with_role_and_perm("reconciliation.run", "AP_PROCESSOR")
        # DENY override that expired yesterday
        UserPermissionOverrideFactory(
            user=user, permission=perm,
            override_type=PermissionOverrideType.DENY,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        user.clear_permission_cache()
        assert user.has_permission("reconciliation.run") is True


# ─── Rule 3: ALLOW override grants permission even without role ───────────────

@pytest.mark.django_db
class TestAllowOverride:
    def test_allow_override_grants_without_role(self):
        """User with no role-level permission gets access via ALLOW override."""
        user = UserFactory(role="REVIEWER")
        perm = PermissionFactory(code="reconciliation.run", module="reconciliation",
                                 action="run")
        # No RolePermission — role does NOT grant this
        UserPermissionOverrideFactory(
            user=user, permission=perm,
            override_type=PermissionOverrideType.ALLOW,
        )
        assert user.has_permission("reconciliation.run") is True

    def test_expired_allow_override_does_not_grant(self):
        """An expired ALLOW override is not applied — no role grant means denied."""
        user = UserFactory(role="REVIEWER")
        perm = PermissionFactory(code="reconciliation.run", module="reconciliation",
                                 action="run")
        UserPermissionOverrideFactory(
            user=user, permission=perm,
            override_type=PermissionOverrideType.ALLOW,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        user.clear_permission_cache()
        assert user.has_permission("reconciliation.run") is False


# ─── Rule 4: Role-level permission grant ──────────────────────────────────────

@pytest.mark.django_db
class TestRoleLevelPermission:
    def test_role_grants_permission(self):
        """User with active role assignment inherits role's permissions."""
        user, _, _ = make_user_with_role_and_perm("invoices.view", "AP_PROCESSOR")
        assert user.has_permission("invoices.view") is True

    def test_role_does_not_grant_unassigned_permission(self):
        """Permission not assigned to role is denied at role level."""
        user, _, _ = make_user_with_role_and_perm("invoices.view", "AP_PROCESSOR")
        assert user.has_permission("cases.escalate") is False

    def test_inactive_role_assignment_excluded(self):
        """An inactive UserRole assignment is not counted."""
        user = UserFactory(role="AP_PROCESSOR")
        role = RoleFactory(code="AP_PROCESSOR_INACTIVE")
        perm = PermissionFactory(code="reconciliation.run", module="reconciliation",
                                 action="run")
        RolePermissionFactory(role=role, permission=perm, is_allowed=True)
        # Assign role but mark as inactive
        UserRoleFactory(user=user, role=role, is_primary=False, is_active=False)
        assert user.has_permission("reconciliation.run") is False

    def test_expired_role_assignment_excluded(self):
        """An expired UserRole (expires_at in the past) is not counted."""
        user = UserFactory(role="AP_PROCESSOR")
        role = RoleFactory(code="AP_PROCESSOR_EXP")
        perm = PermissionFactory(code="reconciliation.run", module="reconciliation",
                                 action="run")
        RolePermissionFactory(role=role, permission=perm, is_allowed=True)
        UserRoleFactory(
            user=user, role=role, is_primary=True, is_active=True,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        user.clear_permission_cache()
        assert user.has_permission("reconciliation.run") is False

    def test_not_yet_expired_role_is_included(self):
        """A UserRole with future expires_at is still active."""
        user = UserFactory(role="AP_PROCESSOR")
        role = RoleFactory(code="AP_PROCESSOR_FUTURE")
        perm = PermissionFactory(code="reconciliation.run", module="reconciliation",
                                 action="run")
        RolePermissionFactory(role=role, permission=perm, is_allowed=True)
        UserRoleFactory(
            user=user, role=role, is_primary=True, is_active=True,
            expires_at=timezone.now() + timedelta(days=30),
        )
        assert user.has_permission("reconciliation.run") is True

    def test_multiple_roles_union_of_permissions(self):
        """User with two roles gets the union of both roles' permissions."""
        user = UserFactory(role="AP_PROCESSOR")

        role_a = RoleFactory(code="ROLE_A_MULTI")
        role_b = RoleFactory(code="ROLE_B_MULTI")

        perm_a = PermissionFactory(code="invoices.view", module="invoices", action="view")
        perm_b = PermissionFactory(code="reconciliation.run", module="reconciliation",
                                   action="run")

        RolePermissionFactory(role=role_a, permission=perm_a, is_allowed=True)
        RolePermissionFactory(role=role_b, permission=perm_b, is_allowed=True)

        UserRoleFactory(user=user, role=role_a, is_primary=True, is_active=True)
        UserRoleFactory(user=user, role=role_b, is_primary=False, is_active=True)

        assert user.has_permission("invoices.view") is True
        assert user.has_permission("reconciliation.run") is True

    def test_inactive_permission_in_role_not_granted(self):
        """A RolePermission linked to an inactive Permission record is not granted."""
        user = UserFactory(role="AP_PROCESSOR")
        role = RoleFactory(code="AP_PROCESSOR_INACTPERM")
        perm = PermissionFactory(code="invoices.delete", module="invoices",
                                 action="delete", is_active=False)
        RolePermissionFactory(role=role, permission=perm, is_allowed=True)
        UserRoleFactory(user=user, role=role, is_primary=True, is_active=True)
        assert user.has_permission("invoices.delete") is False


# ─── Rule 5: Default deny ─────────────────────────────────────────────────────

@pytest.mark.django_db
class TestDefaultDeny:
    def test_user_with_no_roles_denied(self):
        """User with no role assignments and no overrides is denied everything."""
        user = UserFactory(role="AP_PROCESSOR")
        # No UserRole, no UserPermissionOverride
        assert user.has_permission("anything.view") is False

    def test_user_with_role_but_no_matching_permission(self):
        """User has a role but that role has no permissions at all."""
        user = UserFactory(role="REVIEWER")
        role = RoleFactory(code="REVIEWER_EMPTY")
        UserRoleFactory(user=user, role=role, is_primary=True, is_active=True)
        # No RolePermission records
        assert user.has_permission("reconciliation.run") is False


# ─── Permission cache ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestPermissionCache:
    def test_permissions_cached_on_instance(self):
        """get_effective_permissions() is computed once and cached."""
        user, _, _ = make_user_with_role_and_perm("reconciliation.run", "AP_CACHE")
        # Call twice — second should use cache
        perms1 = user.get_effective_permissions()
        perms2 = user.get_effective_permissions()
        assert perms1 is perms2  # Same frozenset object

    def test_clear_permission_cache_removes_cached_attrs(self):
        """clear_permission_cache() removes _cached_permissions and _cached_role_codes."""
        user, _, _ = make_user_with_role_and_perm("reconciliation.run", "AP_CACHE2")
        _ = user.get_effective_permissions()
        assert hasattr(user, "_cached_permissions")

        user.clear_permission_cache()
        assert not hasattr(user, "_cached_permissions")
        assert not hasattr(user, "_cached_role_codes")

    def test_has_any_permission_uses_effective_set(self):
        """has_any_permission() returns True if any of the codes is in effective set."""
        user, _, _ = make_user_with_role_and_perm("invoices.view", "AP_ANYP")
        assert user.has_any_permission(["invoices.view", "cases.escalate"]) is True
        assert user.has_any_permission(["cases.escalate", "reviews.assign"]) is False
