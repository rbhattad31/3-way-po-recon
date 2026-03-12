"""Custom middleware for the PO Reconciliation application."""
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
