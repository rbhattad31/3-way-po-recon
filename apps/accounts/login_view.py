"""Rate-limited login view.

Wraps Django's built-in LoginView with a simple cache-based throttle to
prevent brute-force attacks without requiring an external dependency.

Limits: 5 failed POST attempts per IP per 5-minute window.
On breach: HTTP 429 with a plain error page.
"""
from __future__ import annotations

import hashlib
import logging

from django.contrib.auth import views as auth_views
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

# --- Throttle configuration ---
_MAX_ATTEMPTS = 5          # failed POSTs before lockout
_WINDOW_SECONDS = 300      # 5-minute sliding window
_LOCKOUT_SECONDS = 600     # 10-minute lockout after breach


def _cache_key(request) -> str:
    """Derive a cache key from the client IP."""
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR", "unknown")
    )
    # Hash the IP so it doesn't appear verbatim in cache keys
    return "login_attempts:" + hashlib.sha256(ip.encode()).hexdigest()[:16]


def _is_locked_out(request) -> bool:
    key = _cache_key(request)
    return (cache.get(key) or 0) >= _MAX_ATTEMPTS


def _record_failed_attempt(request) -> None:
    key = _cache_key(request)
    try:
        cache.add(key, 0, timeout=_LOCKOUT_SECONDS)
        count = cache.incr(key)
        if count >= _MAX_ATTEMPTS:
            logger.warning(
                "Login rate limit breached for key=%s (count=%d)",
                key, count,
            )
    except Exception:
        # Cache unavailable — fail open (don't block legitimate users)
        logger.debug("Login rate-limit cache unavailable (non-fatal)", exc_info=True)


class RateLimitedLoginView(auth_views.LoginView):
    """Django LoginView with IP-based brute-force protection."""

    template_name = "accounts/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and _is_locked_out(request):
            logger.warning("Login blocked — rate limit exceeded for request from %s", request.META.get("REMOTE_ADDR"))
            return HttpResponse(
                "<h1>Too many login attempts</h1>"
                "<p>You have been temporarily locked out. Please try again in 10 minutes.</p>",
                status=429,
                content_type="text/html",
            )
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        """Record a failed attempt before returning the invalid-form response."""
        _record_failed_attempt(self.request)
        return super().form_invalid(form)
