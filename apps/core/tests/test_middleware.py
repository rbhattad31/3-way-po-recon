"""Tests for Django middleware: LoginRequired, RBAC, RequestTrace."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from django.http import HttpResponse, HttpRequest
from django.test import RequestFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_request(path: str = "/dashboard/", user=None, meta_extras=None):
    """Create a minimal HttpRequest with the given path and user."""
    rf = RequestFactory()
    request = rf.get(path)
    if meta_extras:
        request.META.update(meta_extras)
    if user is not None:
        request.user = user
    else:
        # Anonymous stub
        anon = MagicMock()
        anon.is_authenticated = False
        request.user = anon
    return request


def _ok_response(request):
    return HttpResponse("OK")


# =========================================================================
# LoginRequiredMiddleware
# =========================================================================
class TestLoginRequiredMiddleware:
    """LRM-01 to LRM-06."""

    def _get_middleware(self):
        from apps.core.middleware import LoginRequiredMiddleware
        return LoginRequiredMiddleware(_ok_response)

    @pytest.mark.django_db
    def test_anonymous_non_exempt_redirects(self):
        """LRM-01: Anonymous user on /dashboard/ is redirected to login."""
        mw = self._get_middleware()
        request = _make_request("/dashboard/")
        response = mw(request)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert "next=/dashboard/" in response.url

    @pytest.mark.parametrize("path", [
        "/admin/",
        "/admin/core/",
        "/accounts/login/",
        "/accounts/logout/",
        "/api/v1/invoices/",
        "/health/",
    ])
    @pytest.mark.django_db
    def test_anonymous_exempt_passes_through(self, path):
        """LRM-02: Anonymous user on exempt path gets 200."""
        mw = self._get_middleware()
        request = _make_request(path)
        response = mw(request)
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_authenticated_non_exempt_passes(self):
        """LRM-03: Authenticated user on non-exempt path passes through."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = True
        request = _make_request("/dashboard/", user=user)
        response = mw(request)
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_anonymous_nested_exempt_passes(self):
        """LRM-04: Exempt prefix check works for nested paths."""
        mw = self._get_middleware()
        request = _make_request("/api/v1/governance/timeline/")
        response = mw(request)
        assert response.status_code == 200


# =========================================================================
# RBACMiddleware
# =========================================================================
class TestRBACMiddleware:
    """RBAC-MW-01 to RBAC-MW-03."""

    def _get_middleware(self):
        from apps.core.middleware import RBACMiddleware
        return RBACMiddleware(_ok_response)

    def test_authenticated_user_caches_warmed(self):
        """RBAC-MW-01: get_role_codes and get_effective_permissions are called."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = True
        user.get_role_codes = MagicMock(return_value=["ADMIN"])
        user.get_effective_permissions = MagicMock(return_value={"invoices.view"})
        request = _make_request("/dashboard/", user=user)
        mw(request)
        user.get_role_codes.assert_called_once()
        user.get_effective_permissions.assert_called_once()

    def test_anonymous_user_no_cache_warm(self):
        """RBAC-MW-02: Anonymous users skip cache warming."""
        mw = self._get_middleware()
        request = _make_request("/dashboard/")
        response = mw(request)
        assert response.status_code == 200

    def test_user_without_rbac_methods(self):
        """RBAC-MW-03: User without get_effective_permissions is tolerated."""
        mw = self._get_middleware()
        user = MagicMock(spec=["is_authenticated"])
        user.is_authenticated = True
        request = _make_request("/dashboard/", user=user)
        response = mw(request)
        assert response.status_code == 200


# =========================================================================
# RequestTraceMiddleware
# =========================================================================
class TestRequestTraceMiddleware:
    """RTM-01 to RTM-06."""

    def _get_middleware(self):
        from apps.core.middleware import RequestTraceMiddleware
        return RequestTraceMiddleware(_ok_response)

    def test_creates_trace_context(self):
        """RTM-01: request.trace_context is set."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = False
        request = _make_request("/dashboard/", user=user)
        response = mw(request)
        assert hasattr(request, "trace_context")
        assert request.trace_context.trace_id != ""

    def test_response_has_trace_headers(self):
        """RTM-02: X-Trace-ID and X-Request-ID are set on response."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = False
        request = _make_request("/dashboard/", user=user)
        response = mw(request)
        assert "X-Trace-ID" in response
        assert "X-Request-ID" in response
        assert response["X-Trace-ID"] == request.trace_context.trace_id

    def test_respects_incoming_request_id(self):
        """RTM-03: Upstream X-Request-ID header is preserved."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = False
        incoming_id = uuid.uuid4().hex
        request = _make_request(
            "/dashboard/",
            user=user,
            meta_extras={"HTTP_X_REQUEST_ID": incoming_id},
        )
        response = mw(request)
        assert response["X-Request-ID"] == incoming_id

    def test_ui_source_layer(self):
        """RTM-04: Non-API path gets source_layer=UI."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = False
        request = _make_request("/dashboard/", user=user)
        mw(request)
        assert request.trace_context.source_layer == "UI"

    def test_api_source_layer(self):
        """RTM-05: API path gets source_layer=API."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = False
        request = _make_request("/api/v1/invoices/", user=user)
        mw(request)
        assert request.trace_context.source_layer == "API"

    def test_thread_local_cleared(self):
        """RTM-06: TraceContext.get_current() is None after response."""
        from apps.core.trace import TraceContext
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = False
        request = _make_request("/dashboard/", user=user)
        mw(request)
        assert TraceContext.get_current() is None

    def test_enriched_with_rbac_for_authenticated_user(self):
        """RTM-07: Authenticated user enriches trace context with RBAC."""
        mw = self._get_middleware()
        user = MagicMock()
        user.is_authenticated = True
        user.pk = 42
        user.email = "test@example.com"
        user.role = "ADMIN"
        user.get_role_codes = MagicMock(return_value=["ADMIN", "AUDITOR"])
        request = _make_request("/dashboard/", user=user)
        mw(request)
        assert request.trace_context.actor_user_id == 42
