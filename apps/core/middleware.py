"""Custom middleware for the PO Reconciliation application."""
import uuid

from django.shortcuts import redirect
from django.urls import reverse


class LoginRequiredMiddleware:
    """Redirect anonymous users to the login page for non-exempt paths."""

    EXEMPT_URLS = [
        "/admin/",
        "/accounts/login/",
        "/accounts/logout/",
        "/api/",
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
