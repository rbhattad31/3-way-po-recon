"""Case-specific DRF permissions."""

from rest_framework.permissions import BasePermission

from apps.core.enums import UserRole
from apps.core.permissions import _has_permission_code


class CanViewCase(BasePermission):
    """All authenticated users can view cases."""

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class CanEditCase(BasePermission):
    """AP Processors, Reviewers, and Admins can edit cases."""

    ALLOWED_ROLES = {UserRole.AP_PROCESSOR, UserRole.REVIEWER, UserRole.ADMIN}

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return getattr(request.user, "role", None) in self.ALLOWED_ROLES


class CanAssignCase(BasePermission):
    """Reviewers, Finance Managers, and Admins can assign cases."""

    ALLOWED_ROLES = {UserRole.REVIEWER, UserRole.FINANCE_MANAGER, UserRole.ADMIN}

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return getattr(request.user, "role", None) in self.ALLOWED_ROLES


class CanUseCopilot(BasePermission):
    """AP Processors, Reviewers, Finance Managers, Admins, and Auditors can use copilot."""

    ALLOWED_ROLES = {
        UserRole.AP_PROCESSOR, UserRole.REVIEWER, UserRole.FINANCE_MANAGER,
        UserRole.ADMIN, UserRole.AUDITOR,
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return getattr(request.user, "role", None) in self.ALLOWED_ROLES


class CanViewReview(BasePermission):
    """Only review-capable roles can view review assignments."""

    ALLOWED_ROLES = {
        UserRole.AP_PROCESSOR,
        UserRole.REVIEWER,
        UserRole.FINANCE_MANAGER,
        UserRole.ADMIN,
        UserRole.AUDITOR,
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if _has_permission_code(request.user, "reviews.assign") or _has_permission_code(request.user, "reviews.decide"):
            return True
        return getattr(request.user, "role", None) in self.ALLOWED_ROLES


class CanAssignReview(BasePermission):
    """Only users allowed to assign reviews can do so."""

    ALLOWED_ROLES = {UserRole.REVIEWER, UserRole.FINANCE_MANAGER, UserRole.ADMIN}

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if _has_permission_code(request.user, "reviews.assign"):
            return True
        return getattr(request.user, "role", None) in self.ALLOWED_ROLES


class IsReviewActor(BasePermission):
    """Review assignee, finance manager, or admin can act on a review."""

    PRIVILEGED_ROLES = {UserRole.FINANCE_MANAGER, UserRole.ADMIN}

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if _has_permission_code(request.user, "reviews.decide"):
            return True
        if getattr(request.user, "role", None) in self.PRIVILEGED_ROLES:
            return True
        return getattr(obj, "assigned_to_id", None) == request.user.pk
