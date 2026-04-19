"""DRF permissions for email integration API endpoints."""
from apps.core.permissions import HasAnyPermission, HasPermissionCode


class CanViewEmailIntegration(HasPermissionCode):
    """Requires `email.view` permission."""

    def has_permission(self, request, view):
        view.required_permission = "email.view"
        return super().has_permission(request, view)


class CanManageEmailIntegration(HasPermissionCode):
    """Requires `email.manage` permission."""

    def has_permission(self, request, view):
        view.required_permission = "email.manage"
        return super().has_permission(request, view)


class CanReadEmailThread(HasAnyPermission):
    """Requires `email.read_thread` or broad email read access."""

    def has_permission(self, request, view):
        view.required_permissions = ["email.read_thread", "email.view"]
        return super().has_permission(request, view)


class CanReadEmailAttachment(HasAnyPermission):
    """Requires `email.read_attachment` or broad email read access."""

    def has_permission(self, request, view):
        view.required_permissions = ["email.read_attachment", "email.view"]
        return super().has_permission(request, view)


class CanSendEmail(HasAnyPermission):
    """Requires email send permission."""

    def has_permission(self, request, view):
        view.required_permissions = ["email.send"]
        return super().has_permission(request, view)


class CanManageMailboxes(HasAnyPermission):
    """Requires mailbox admin access with fallback to legacy manage permission."""

    def has_permission(self, request, view):
        view.required_permissions = ["email.manage_mailboxes", "email.manage"]
        return super().has_permission(request, view)


class CanRouteEmail(HasAnyPermission):
    """Requires routing permission with fallback to legacy manage permission."""

    def has_permission(self, request, view):
        view.required_permissions = ["email.route", "email.manage"]
        return super().has_permission(request, view)


class CanTriageEmail(HasAnyPermission):
    """Requires triage permission with fallback to legacy manage permission."""

    def has_permission(self, request, view):
        view.required_permissions = ["email.triage", "email.manage"]
        return super().has_permission(request, view)
