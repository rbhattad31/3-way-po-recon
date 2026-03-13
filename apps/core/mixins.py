"""Reusable model mixins and view mixins."""
from django.db import models


class SoftDeleteMixin(models.Model):
    """Supports soft deletion via is_active flag."""

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True

    def soft_delete(self) -> None:
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


class NotesMixin(models.Model):
    """Adds a free-text notes field."""

    notes = models.TextField(blank=True, default="")

    class Meta:
        abstract = True


# ============================================================================
# RBAC-observed view mixins — capture authorization context in audit trail
# ============================================================================

class ObservedViewMixin:
    """Mixin for Django CBVs / DRF views that records RBAC context.

    Populates ``request.trace_context`` with the user's authorization
    snapshot before dispatching the view.

    Set ``observed_action_name`` and ``observed_permission`` on the view class.
    """

    observed_action_name: str = ""
    observed_permission: str = ""

    def dispatch(self, request, *args, **kwargs):
        from apps.core.trace import TraceContext
        from apps.core.decorators import _resolve_permission_source, _check_permission
        from apps.core.metrics import MetricsService

        ctx = getattr(request, "trace_context", None) or TraceContext.current_or_empty()

        user = getattr(request, "user", None)
        perm = self.observed_permission or getattr(self, "required_permission", "")

        if user and getattr(user, "is_authenticated", False) and perm:
            source = _resolve_permission_source(user, perm)
            granted = _check_permission(user, perm)
            ctx = ctx.with_rbac(
                user,
                permission_checked=perm,
                permission_source=source,
                access_granted=granted,
            )
            MetricsService.rbac_permission_check(perm, granted)
            request.trace_context = ctx
            TraceContext.set_current(ctx)
        elif user and getattr(user, "is_authenticated", False):
            ctx = ctx.with_rbac(user)
            request.trace_context = ctx
            TraceContext.set_current(ctx)

        return super().dispatch(request, *args, **kwargs)


class ObservedAPIViewMixin:
    """DRF APIView mixin that enriches TraceContext with RBAC info on initial().

    Set ``observed_action_name`` and ``observed_permission`` on your ViewSet.
    """

    observed_action_name: str = ""
    observed_permission: str = ""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)

        from apps.core.trace import TraceContext
        from apps.core.decorators import _resolve_permission_source, _check_permission
        from apps.core.metrics import MetricsService

        ctx = getattr(request, "trace_context", None) or TraceContext.current_or_empty()
        user = request.user
        perm = self.observed_permission or getattr(self, "required_permission", "")

        if user and getattr(user, "is_authenticated", False) and perm:
            source = _resolve_permission_source(user, perm)
            granted = _check_permission(user, perm)
            ctx = ctx.with_rbac(
                user,
                permission_checked=perm,
                permission_source=source,
                access_granted=granted,
            )
            MetricsService.rbac_permission_check(perm, granted)
        elif user and getattr(user, "is_authenticated", False):
            ctx = ctx.with_rbac(user)

        request.trace_context = ctx
        TraceContext.set_current(ctx)
