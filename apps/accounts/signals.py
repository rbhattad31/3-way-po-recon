"""
Auth signal handlers — record login, logout, and failed login as AuditEvents.
"""
import logging

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from apps.auditlog.models import AuditEvent
from apps.core.enums import AuditEventType

logger = logging.getLogger(__name__)


def _build_rbac_kwargs(user):
    """Return RBAC snapshot fields for AuditEvent creation."""
    if not user:
        return {}
    email = getattr(user, "email", "")
    primary_role = getattr(user, "role", "") or ""
    roles_snapshot = None
    if hasattr(user, "get_active_role_codes"):
        try:
            roles_snapshot = list(user.get_active_role_codes())
        except Exception:
            roles_snapshot = [primary_role] if primary_role else None
    elif primary_role:
        roles_snapshot = [primary_role]

    return {
        "actor_email": email,
        "actor_primary_role": primary_role,
        "actor_roles_snapshot_json": roles_snapshot,
    }


def _request_meta(request):
    """Extract IP and user-agent from the request."""
    if not request:
        return {}
    meta = request.META or {}
    ip = (
        meta.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or meta.get("REMOTE_ADDR", "")
    )
    return {
        "ip_address": ip,
        "user_agent": meta.get("HTTP_USER_AGENT", "")[:500],
    }


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    try:
        AuditEvent.objects.create(
            entity_type="User",
            entity_id=user.pk,
            action=AuditEventType.USER_LOGIN,
            event_type=AuditEventType.USER_LOGIN,
            event_description=f"User '{user.email}' logged in",
            performed_by=user,
            metadata_json=_request_meta(request),
            **_build_rbac_kwargs(user),
        )
    except Exception:
        logger.exception("Failed to log USER_LOGIN for %s", getattr(user, "email", "?"))


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    if not user:
        return
    try:
        AuditEvent.objects.create(
            entity_type="User",
            entity_id=user.pk,
            action=AuditEventType.USER_LOGOUT,
            event_type=AuditEventType.USER_LOGOUT,
            event_description=f"User '{user.email}' logged out",
            performed_by=user,
            metadata_json=_request_meta(request),
            **_build_rbac_kwargs(user),
        )
    except Exception:
        logger.exception("Failed to log USER_LOGOUT for %s", getattr(user, "email", "?"))


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request, **kwargs):
    # credentials dict typically has 'username' (which is email in our system)
    attempted_email = credentials.get("username", credentials.get("email", "unknown"))
    meta = _request_meta(request)
    meta["attempted_email"] = attempted_email
    try:
        AuditEvent.objects.create(
            entity_type="User",
            entity_id=0,
            action=AuditEventType.USER_LOGIN_FAILED,
            event_type=AuditEventType.USER_LOGIN_FAILED,
            event_description=f"Failed login attempt for '{attempted_email}'",
            metadata_json=meta,
        )
    except Exception:
        logger.exception("Failed to log USER_LOGIN_FAILED for %s", attempted_email)
