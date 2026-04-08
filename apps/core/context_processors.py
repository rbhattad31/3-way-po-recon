"""Template context processors."""
from django.conf import settings

from apps.core.enums import ReviewStatus
from apps.reviews.models import ReviewAssignment


def static_version(request):
    """Inject STATIC_VERSION into templates for cache-busting."""
    return {"static_version": getattr(settings, "STATIC_VERSION", "1")}


def pending_reviews(request):
    if request.user.is_authenticated:
        count = ReviewAssignment.objects.filter(
            status__in=[ReviewStatus.PENDING, ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW]
        ).count()
        return {"pending_review_count": count}
    return {"pending_review_count": 0}


def rbac_context(request):
    """Inject RBAC permissions and role codes into every template context.

    Provides:
        user_permissions  – frozenset of effective permission codes
        user_role_codes   – set of active role codes
        is_admin          – bool shortcut
    """
    user = getattr(request, "user", None)
    if user and user.is_authenticated and hasattr(user, "get_effective_permissions"):
        perms = user.get_effective_permissions()
        role_codes = user.get_role_codes()
        is_admin = (
            getattr(user, "is_platform_admin", False)
            or "SUPER_ADMIN" in role_codes
            or "ADMIN" in role_codes
            or getattr(user, "role", "") in ("ADMIN", "SUPER_ADMIN")
        )
        return {
            "user_permissions": perms,
            "user_role_codes": role_codes,
            "is_admin": is_admin,
        }
    return {
        "user_permissions": frozenset(),
        "user_role_codes": set(),
        "is_admin": False,
    }
