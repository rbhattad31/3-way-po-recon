"""Tenant isolation tests.

Verifies that tenant-scoped models and views cannot be accessed cross-tenant,
and that platform admins / superusers bypass scoping correctly.

Covers:
  TI-01  TenantMiddleware resolves tenant from user.company
  TI-02  TenantMiddleware returns None for unauthenticated requests
  TI-03  TenantMiddleware returns None for superusers without X-Tenant-ID header
  TI-04  TenantMiddleware resolves explicit tenant for superuser via X-Tenant-ID header
  TI-05  TenantQuerysetMixin filters to request.tenant
  TI-06  TenantQuerysetMixin returns all records for superuser
  TI-07  TenantQuerysetMixin returns all records for platform_admin
  TI-08  require_tenant() raises PermissionDenied for regular user without tenant
  TI-09  require_tenant() returns None for superuser (no check)
  TI-10  scoped_queryset() filters by tenant when tenant is not None
  TI-11  scoped_queryset() returns all records when tenant=None
  TI-12  assert_tenant_access() raises PermissionDenied on mismatched tenant
  TI-13  assert_tenant_access() passes for matching tenant
  TI-14  assert_tenant_access() passes for superuser context (tenant=None)
  TI-15  Cross-tenant object access is blocked at UserViewSet level
  TI-16  User records scoped to own tenant only
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core.middleware import TenantMiddleware
from apps.core.tenant_utils import (
    TenantQuerysetMixin,
    assert_tenant_access,
    require_tenant,
    scoped_queryset,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tenant_a(db):
    from apps.accounts.models import CompanyProfile
    return CompanyProfile.objects.create(
        name="Tenant A",
        slug="tenant-a",
        is_active=True,
    )


@pytest.fixture
def tenant_b(db):
    from apps.accounts.models import CompanyProfile
    return CompanyProfile.objects.create(
        name="Tenant B",
        slug="tenant-b",
        is_active=True,
    )


@pytest.fixture
def user_a(db, tenant_a):
    return User.objects.create_user(
        email="user-a@tenanta.com",
        password="pass",
        first_name="User",
        last_name="A",
        company=tenant_a,
    )


@pytest.fixture
def user_b(db, tenant_b):
    return User.objects.create_user(
        email="user-b@tenantb.com",
        password="pass",
        first_name="User",
        last_name="B",
        company=tenant_b,
    )


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        email="admin@platform.com",
        password="pass",
        first_name="Platform",
        last_name="Admin",
    )


@pytest.fixture
def platform_admin(db):
    return User.objects.create_user(
        email="padmin@platform.com",
        password="pass",
        first_name="Platform",
        last_name="Admin2",
        is_platform_admin=True,
    )


def _make_request(path="/dashboard/", user=None):
    rf = RequestFactory()
    request = rf.get(path)
    request.user = user or MagicMock(is_authenticated=False)
    return request


# ---------------------------------------------------------------------------
# TI-01..04  TenantMiddleware
# ---------------------------------------------------------------------------

class TestTenantMiddleware:
    """TI-01 to TI-04."""

    def _middleware(self):
        return TenantMiddleware(get_response=lambda req: HttpResponse("ok"))

    def test_resolves_tenant_from_user_company(self, user_a, tenant_a):
        """TI-01: regular user gets tenant from user.company."""
        mw = self._middleware()
        request = _make_request(user=user_a)
        request.user = user_a
        mw(request)
        assert request.tenant == tenant_a

    def test_unauthenticated_returns_none(self):
        """TI-02: unauthenticated request → tenant=None."""
        mw = self._middleware()
        request = _make_request()
        anon = MagicMock()
        anon.is_authenticated = False
        request.user = anon
        mw(request)
        assert request.tenant is None

    def test_superuser_without_header_returns_none(self, superuser):
        """TI-03: superuser with no X-Tenant-ID header → tenant=None (sees all)."""
        mw = self._middleware()
        request = _make_request(user=superuser)
        mw(request)
        assert request.tenant is None

    def test_superuser_with_header_resolves_tenant(self, superuser, tenant_a):
        """TI-04: superuser passing X-Tenant-ID header gets scoped to that tenant."""
        mw = self._middleware()
        rf = RequestFactory()
        request = rf.get("/dashboard/", HTTP_X_TENANT_ID=str(tenant_a.pk))
        request.user = superuser
        mw(request)
        assert request.tenant == tenant_a


# ---------------------------------------------------------------------------
# TI-05..07  TenantQuerysetMixin
# ---------------------------------------------------------------------------

class TestTenantQuerysetMixin:
    """TI-05 to TI-07."""

    def _view_instance(self, request, model_class):
        """Build a minimal CBV-like object using the mixin."""
        from django.views.generic import ListView

        class FakeView(TenantQuerysetMixin, ListView):
            model = model_class

        view = FakeView()
        view.request = request
        view.kwargs = {}
        return view

    def test_filters_to_request_tenant(self, db, user_a, user_b, tenant_a, tenant_b):
        """TI-05: mixin scopes queryset to request.tenant, excluding other tenant's records."""
        request = _make_request(user=user_a)
        request.tenant = tenant_a

        view = self._view_instance(request, User)
        qs = view.get_queryset()
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk not in pks

    def test_superuser_sees_all(self, db, superuser, user_a, user_b):
        """TI-06: superuser bypasses tenant filter and sees all records."""
        request = _make_request(user=superuser)
        request.tenant = None  # superuser has no tenant

        view = self._view_instance(request, User)
        qs = view.get_queryset()
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk in pks

    def test_platform_admin_sees_all(self, db, platform_admin, user_a, user_b):
        """TI-07: is_platform_admin bypasses tenant filter."""
        request = _make_request(user=platform_admin)
        request.tenant = None

        view = self._view_instance(request, User)
        qs = view.get_queryset()
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk in pks


# ---------------------------------------------------------------------------
# TI-08..09  require_tenant()
# ---------------------------------------------------------------------------

class TestRequireTenant:
    """TI-08 to TI-09."""

    def test_raises_for_regular_user_without_tenant(self, user_a):
        """TI-08: regular user with no tenant on request → PermissionDenied."""
        request = _make_request(user=user_a)
        request.tenant = None  # tenant not resolved

        with pytest.raises(PermissionDenied):
            require_tenant(request)

    def test_returns_tenant_for_regular_user(self, user_a, tenant_a):
        """TI-08b: regular user with resolved tenant → returns tenant."""
        request = _make_request(user=user_a)
        request.tenant = tenant_a

        result = require_tenant(request)
        assert result == tenant_a

    def test_superuser_passes_without_tenant(self, superuser):
        """TI-09: superuser with tenant=None returns None (no exception)."""
        request = _make_request(user=superuser)
        request.tenant = None

        result = require_tenant(request)
        assert result is None

    def test_platform_admin_passes_without_tenant(self, platform_admin):
        """TI-09b: platform_admin with tenant=None returns None."""
        request = _make_request(user=platform_admin)
        request.tenant = None

        result = require_tenant(request)
        assert result is None


# ---------------------------------------------------------------------------
# TI-10..11  scoped_queryset()
# ---------------------------------------------------------------------------

class TestScopedQueryset:
    """TI-10 to TI-11."""

    def test_filters_by_tenant(self, db, user_a, user_b, tenant_a):
        """TI-10: scoped_queryset with a tenant filters records."""
        qs = scoped_queryset(User, tenant_a)
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk not in pks

    def test_no_filter_when_tenant_none(self, db, user_a, user_b):
        """TI-11: scoped_queryset with tenant=None returns all records."""
        qs = scoped_queryset(User, None)
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk in pks


# ---------------------------------------------------------------------------
# TI-12..14  assert_tenant_access()
# ---------------------------------------------------------------------------

class TestAssertTenantAccess:
    """TI-12 to TI-14."""

    def _obj(self, tenant):
        obj = MagicMock()
        obj.pk = 999
        obj.tenant_id = tenant.pk
        return obj

    def test_raises_for_mismatched_tenant(self, tenant_a, tenant_b):
        """TI-12: object belonging to tenant_b raises when actor has tenant_a."""
        obj = self._obj(tenant_b)
        with pytest.raises(PermissionDenied) as exc_info:
            assert_tenant_access(obj, tenant_a)
        assert "999" in str(exc_info.value)

    def test_passes_for_matching_tenant(self, tenant_a):
        """TI-13: object belonging to tenant_a passes when actor has tenant_a."""
        obj = self._obj(tenant_a)
        assert_tenant_access(obj, tenant_a)  # no exception

    def test_passes_for_superuser_context(self, tenant_b):
        """TI-14: tenant=None (superuser context) always passes."""
        obj = self._obj(tenant_b)
        assert_tenant_access(obj, None)  # no exception


# ---------------------------------------------------------------------------
# TI-15..16  Cross-tenant access at view level
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCrossTenantUserAccess:
    """TI-15 to TI-16: Users in tenant A cannot see users in tenant B via API."""

    def test_user_only_sees_own_tenant_users(self, user_a, user_b, tenant_a):
        """TI-15: UserViewSet queryset for user_a must not include user_b."""
        from apps.accounts.views import UserViewSet

        request = _make_request(user=user_a)
        request.tenant = tenant_a

        vs = UserViewSet()
        vs.request = request
        vs.kwargs = {}
        vs.format_kwarg = None
        vs.action = "list"

        qs = vs.get_queryset()
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk not in pks

    def test_superuser_sees_all_tenants_users(self, db, superuser, user_a, user_b):
        """TI-16: Superuser UserViewSet queryset includes all users."""
        from apps.accounts.views import UserViewSet

        request = _make_request(user=superuser)
        request.tenant = None

        vs = UserViewSet()
        vs.request = request
        vs.kwargs = {}
        vs.format_kwarg = None
        vs.action = "list"

        qs = vs.get_queryset()
        pks = list(qs.values_list("pk", flat=True))

        assert user_a.pk in pks
        assert user_b.pk in pks
