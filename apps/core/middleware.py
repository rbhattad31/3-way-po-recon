"""Custom middleware for the PO Reconciliation application."""
import uuid

from django.shortcuts import redirect
from django.urls import reverse


class TenantMiddleware:
    """Resolve the current tenant from the authenticated user and attach it to
    ``request.tenant``.

    Must run after Django's ``AuthenticationMiddleware`` so ``request.user``
    is already populated.  Superusers bypass tenant scoping unless they
    explicitly supply an ``X-Tenant-ID`` header (useful for admin tooling).
    """

    TENANT_EXEMPT_PATHS = [
        "/admin/",
        "/accounts/login/",
        "/accounts/logout/",
        "/health/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = self._resolve_tenant(request)
        response = self.get_response(request)
        if request.tenant:
            response["X-Tenant-ID"] = str(request.tenant.pk)
        return response

    def _resolve_tenant(self, request):
        from apps.accounts.models import CompanyProfile

        if not hasattr(request, "user") or not request.user.is_authenticated:
            return None

        if getattr(request.user, "is_platform_admin", False) or request.user.is_superuser:
            # Platform admin / superuser may pass X-Tenant-ID header to scope a specific tenant
            tenant_id = request.META.get("HTTP_X_TENANT_ID")
            if tenant_id:
                return CompanyProfile.objects.filter(pk=tenant_id).first()
            return None  # No filter -- sees everything

        if request.user.company_id:
            return request.user.company

        return None


class LoginRequiredMiddleware:
    """Redirect anonymous users to the login page for non-exempt paths."""

    EXEMPT_URLS = [
        "/admin/",
        "/accounts/login/",
        "/accounts/logout/",
        "/accounts/invite/",
        "/api/",
        "/health/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            if not any(request.path.startswith(url) for url in self.EXEMPT_URLS):
                return redirect(f"{reverse('accounts:login')}?next={request.path}")
        return self.get_response(request)


class RBACMiddleware:
    """Pre-load effective RBAC permissions onto request.user for the request lifecycle.

    This avoids N+1 queries by eagerly warming the permission and role caches
    that the User model helpers use. The caches live on the user instance and
    are garbage-collected at the end of the request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and hasattr(user, "get_effective_permissions"):
            # Warm caches — results are stored on the instance
            user.get_role_codes()
            user.get_effective_permissions()
        return self.get_response(request)


class RequestTraceMiddleware:
    """Attach a TraceContext to every request for end-to-end correlation.

    Sets ``request.trace_context`` and stores it on the current thread so
    that downstream services, logging, and audit helpers can access it via
    ``TraceContext.get_current()``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.core.trace import TraceContext

        # Determine request_id (respect upstream header if present)
        request_id = (
            request.META.get("HTTP_X_REQUEST_ID")
            or request.META.get("HTTP_X_TRACE_ID")
            or uuid.uuid4().hex
        )

        # Build root context for this request
        ctx = TraceContext.new_root(
            request_id=request_id,
            source_service="django",
            source_layer="UI" if not request.path.startswith("/api/") else "API",
        )

        # Enrich with user if authenticated
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            ctx = ctx.with_rbac(user)

        # Enrich with tenant if resolved
        tenant = getattr(request, "tenant", None)
        if tenant:
            ctx.tenant_id = str(tenant.pk)
            ctx.tenant_name = tenant.name

        # Store on request + thread-local
        request.trace_context = ctx
        TraceContext.set_current(ctx)

        response = self.get_response(request)

        # Add trace header to response
        response["X-Trace-ID"] = ctx.trace_id
        response["X-Request-ID"] = request_id

        # Clear thread-local
        TraceContext.set_current(None)

        return response
