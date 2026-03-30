"""Factory helpers for accounts/RBAC tests."""
from __future__ import annotations
import factory
from django.contrib.auth import get_user_model
from apps.accounts.rbac_models import Role, Permission, RolePermission, UserRole, UserPermissionOverride
from apps.core.enums import PermissionOverrideType

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    first_name = "Test"
    last_name = "User"
    role = "AP_PROCESSOR"
    is_active = True


class RoleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Role

    code = factory.Sequence(lambda n: f"ROLE_{n}")
    name = factory.Sequence(lambda n: f"Role {n}")
    is_system_role = False
    is_active = True
    rank = 50


class PermissionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Permission

    code = factory.Sequence(lambda n: f"module{n}.action")
    name = factory.Sequence(lambda n: f"Permission {n}")
    module = "reconciliation"
    action = "view"
    is_active = True


class RolePermissionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RolePermission

    role = factory.SubFactory(RoleFactory)
    permission = factory.SubFactory(PermissionFactory)
    is_allowed = True


class UserRoleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserRole

    user = factory.SubFactory(UserFactory)
    role = factory.SubFactory(RoleFactory)
    is_primary = True
    is_active = True
    expires_at = None
    scope_json = None


class UserPermissionOverrideFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserPermissionOverride

    user = factory.SubFactory(UserFactory)
    permission = factory.SubFactory(PermissionFactory)
    override_type = PermissionOverrideType.ALLOW
    is_active = True
    expires_at = None
