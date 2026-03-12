"""
Enterprise RBAC permission classes for Django REST Framework and template views.

Backward-compatible: all original permission class names preserved.
Under the hood, they now delegate to the new RBAC engine on User model.

Permission precedence:
1. ADMIN role → always True (bypass)
2. User-specific DENY override → blocks
3. User-specific ALLOW override → grants
4. Role-level permissions → union of all roles
5. Default → False
"""
from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.core.enums import UserRole


# ============================================================================
# Helpers
# ============================================================================

def _is_admin(user) -> bool:
    """Check if user has ADMIN role via legacy field or RBAC."""
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "role", None) == UserRole.ADMIN:
        return True
    if hasattr(user, "get_role_codes"):
        return "ADMIN" in user.get_role_codes()
    return False


def _has_role(user, *role_codes) -> bool:
    """Check if user has any of the given role codes."""
    if not user or not user.is_authenticated:
        return False
    if _is_admin(user):
        return True
    if hasattr(user, "has_any_role"):
        return user.has_any_role(role_codes)
    # Legacy fallback
    return getattr(user, "role", None) in role_codes


def _has_permission_code(user, code: str) -> bool:
    """Check if user has a specific permission code via RBAC."""
    if not user or not user.is_authenticated:
        return False
    if _is_admin(user):
        return True
    if hasattr(user, "has_permission"):
        return user.has_permission(code)
    return False


def _has_any_permission_code(user, codes) -> bool:
    """Check if user has any of the given permission codes."""
    if not user or not user.is_authenticated:
        return False
    if _is_admin(user):
        return True
    if hasattr(user, "has_any_permission"):
        return user.has_any_permission(codes)
    return False


# ============================================================================
# RBAC-aware DRF permission classes (new)
# ============================================================================

class HasPermissionCode(BasePermission):
    """DRF permission class that checks a single RBAC permission code.

    Usage on ViewSets/APIViews:
        permission_classes = [HasPermissionCode]
        required_permission = "invoices.view"
    """
    def has_permission(self, request, view):
        code = getattr(view, "required_permission", None)
        if not code:
            return True
        return _has_permission_code(request.user, code)


class HasAnyPermission(BasePermission):
    """DRF permission class checking any of multiple permission codes.

    Usage:
        permission_classes = [HasAnyPermission]
        required_permissions = ["invoices.view", "reconciliation.view"]
    """
    def has_permission(self, request, view):
        codes = getattr(view, "required_permissions", [])
        if not codes:
            return True
        return _has_any_permission_code(request.user, codes)


class HasRole(BasePermission):
    """DRF permission checking a single role code.

    Usage:
        permission_classes = [HasRole]
        required_role = "FINANCE_MANAGER"
    """
    def has_permission(self, request, view):
        role_code = getattr(view, "required_role", None)
        if not role_code:
            return True
        return _has_role(request.user, role_code)


# ============================================================================
# Backward-compatible role-based permission classes
# ============================================================================

class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return _is_admin(request.user)


class IsAPProcessor(BasePermission):
    """AP Processor + Admin."""
    def has_permission(self, request, view):
        return _has_role(request.user, UserRole.AP_PROCESSOR, UserRole.ADMIN)


class IsReviewer(BasePermission):
    """Reviewer, Finance Manager, or Admin."""
    def has_permission(self, request, view):
        return _has_role(
            request.user, UserRole.REVIEWER, UserRole.FINANCE_MANAGER, UserRole.ADMIN,
        )


class IsFinanceManager(BasePermission):
    def has_permission(self, request, view):
        return _has_role(request.user, UserRole.FINANCE_MANAGER, UserRole.ADMIN)


class IsAuditor(BasePermission):
    def has_permission(self, request, view):
        return _has_role(request.user, UserRole.AUDITOR, UserRole.ADMIN)


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return request.user and request.user.is_authenticated
        return _is_admin(request.user)


# ---------------------------------------------------------------------------
# Object-level: review owner check
# ---------------------------------------------------------------------------
class IsReviewAssignee(BasePermission):
    """Allow only the assigned reviewer (or Admin/FinanceManager) to act."""
    def has_object_permission(self, request, view, obj):
        if _has_role(request.user, UserRole.ADMIN, UserRole.FINANCE_MANAGER):
            return True
        return getattr(obj, "assigned_to_id", None) == request.user.pk


# ---------------------------------------------------------------------------
# Composite helper (backward compatible)
# ---------------------------------------------------------------------------
class HasAnyRole(BasePermission):
    """Configurable — set `allowed_roles` on the view."""
    def has_permission(self, request, view):
        allowed = getattr(view, "allowed_roles", [])
        if not allowed:
            return True
        return _has_role(request.user, *allowed)


# ============================================================================
# Case permission classes (RBAC-backed)
# ============================================================================

class CanViewCase(BasePermission):
    """User can view cases."""
    def has_permission(self, request, view):
        return _has_permission_code(request.user, "cases.view")


class CanEditCase(BasePermission):
    """User can edit cases."""
    def has_permission(self, request, view):
        return _has_permission_code(request.user, "cases.edit")


class CanAssignCase(BasePermission):
    """User can assign cases."""
    def has_permission(self, request, view):
        return _has_permission_code(request.user, "cases.assign")


class CanUseCopilot(BasePermission):
    """User can use AI copilot."""
    def has_permission(self, request, view):
        return _has_permission_code(request.user, "agents.use_copilot")


# ============================================================================
# Django view mixins (for template/class-based views)
# ============================================================================

class PermissionRequiredMixin(AccessMixin):
    """Mixin for Django CBVs requiring a specific RBAC permission code.

    Usage:
        class MyView(PermissionRequiredMixin, TemplateView):
            required_permission = "invoices.view"
    """
    required_permission = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.required_permission and not _has_permission_code(
            request.user, self.required_permission
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class AnyPermissionRequiredMixin(AccessMixin):
    """Mixin requiring any one of multiple permission codes.

    Usage:
        class MyView(AnyPermissionRequiredMixin, TemplateView):
            required_permissions = ["invoices.view", "reconciliation.view"]
    """
    required_permissions = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.required_permissions and not _has_any_permission_code(
            request.user, self.required_permissions
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class RoleRequiredMixin(AccessMixin):
    """Mixin requiring specific role(s).

    Usage:
        class MyView(RoleRequiredMixin, TemplateView):
            required_roles = ["ADMIN", "FINANCE_MANAGER"]
    """
    required_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.required_roles and not _has_role(request.user, *self.required_roles):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


# ============================================================================
# Function-based view decorators
# ============================================================================

def permission_required_code(permission_code):
    """Decorator for FBVs requiring a specific RBAC permission.

    Usage:
        @permission_required_code("invoices.view")
        def my_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not _has_permission_code(request.user, permission_code):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def role_required(*role_codes):
    """Decorator for FBVs requiring specific role(s).

    Usage:
        @role_required("ADMIN", "FINANCE_MANAGER")
        def my_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not _has_role(request.user, *role_codes):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
