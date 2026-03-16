"""Case-specific DRF permissions."""

from rest_framework.permissions import BasePermission

from apps.core.enums import UserRole


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
