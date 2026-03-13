"""Template context processors."""
from apps.core.enums import ReviewStatus
from apps.reviews.models import ReviewAssignment


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
        is_admin = "ADMIN" in role_codes or getattr(user, "role", "") == "ADMIN"
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
