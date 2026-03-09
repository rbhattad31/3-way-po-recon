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
