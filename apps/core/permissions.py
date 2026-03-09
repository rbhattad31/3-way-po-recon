"""Permission helpers for role-based access."""
from rest_framework.permissions import BasePermission

from apps.core.enums import UserRole


# ---------------------------------------------------------------------------
# Role-based permissions
# ---------------------------------------------------------------------------
class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, "role", None) == UserRole.ADMIN


class IsAPProcessor(BasePermission):
    """AP Processor + Admin."""
    def has_permission(self, request, view):
        return getattr(request.user, "role", None) in (
            UserRole.AP_PROCESSOR,
            UserRole.ADMIN,
        )


class IsReviewer(BasePermission):
    """Reviewer, Finance Manager, or Admin."""
    def has_permission(self, request, view):
        return getattr(request.user, "role", None) in (
            UserRole.REVIEWER,
            UserRole.FINANCE_MANAGER,
            UserRole.ADMIN,
        )


class IsFinanceManager(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, "role", None) in (
            UserRole.FINANCE_MANAGER,
            UserRole.ADMIN,
        )


class IsAuditor(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, "role", None) in (
            UserRole.AUDITOR,
            UserRole.ADMIN,
        )


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return request.user and request.user.is_authenticated
        return getattr(request.user, "role", None) == UserRole.ADMIN


# ---------------------------------------------------------------------------
# Object-level: review owner check
# ---------------------------------------------------------------------------
class IsReviewAssignee(BasePermission):
    """Allow only the assigned reviewer (or Admin/FinanceManager) to act."""
    def has_object_permission(self, request, view, obj):
        role = getattr(request.user, "role", None)
        if role in (UserRole.ADMIN, UserRole.FINANCE_MANAGER):
            return True
        return getattr(obj, "assigned_to_id", None) == request.user.pk


# ---------------------------------------------------------------------------
# Composite helper
# ---------------------------------------------------------------------------
class HasAnyRole(BasePermission):
    """Configurable — set `allowed_roles` on the view."""
    def has_permission(self, request, view):
        allowed = getattr(view, "allowed_roles", [])
        return getattr(request.user, "role", None) in allowed
