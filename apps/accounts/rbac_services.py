"""
RBAC audit service — records role and permission changes via AuditEvent.
"""
from apps.auditlog.models import AuditEvent


class RBACEventService:
    """Record RBAC-related audit events using the existing AuditEvent model."""

    @staticmethod
    def log(
        event_type: str,
        entity_type: str,
        entity_id: int,
        performed_by,
        old_values=None,
        new_values=None,
        description: str = "",
        metadata: dict = None,
    ):
        AuditEvent.objects.create(
            entity_type=entity_type,
            entity_id=entity_id,
            action=event_type,
            event_type=event_type,
            event_description=description,
            old_values=old_values,
            new_values=new_values,
            performed_by=performed_by,
            metadata_json=metadata or {},
        )

    @classmethod
    def log_role_assigned(cls, user, role, assigned_by, is_primary=False):
        cls.log(
            event_type="ROLE_ASSIGNED",
            entity_type="User",
            entity_id=user.pk,
            performed_by=assigned_by,
            new_values={"role_code": role.code, "is_primary": is_primary},
            description=f"Role '{role.code}' assigned to {user.email}",
        )

    @classmethod
    def log_role_removed(cls, user, role, performed_by):
        cls.log(
            event_type="ROLE_REMOVED",
            entity_type="User",
            entity_id=user.pk,
            performed_by=performed_by,
            old_values={"role_code": role.code},
            description=f"Role '{role.code}' removed from {user.email}",
        )

    @classmethod
    def log_primary_role_changed(cls, user, old_role_code, new_role_code, performed_by):
        cls.log(
            event_type="PRIMARY_ROLE_CHANGED",
            entity_type="User",
            entity_id=user.pk,
            performed_by=performed_by,
            old_values={"primary_role": old_role_code},
            new_values={"primary_role": new_role_code},
            description=f"Primary role changed from '{old_role_code}' to '{new_role_code}' for {user.email}",
        )

    @classmethod
    def log_role_permission_changed(cls, role, permissions_added, permissions_removed, performed_by):
        cls.log(
            event_type="ROLE_PERMISSION_CHANGED",
            entity_type="Role",
            entity_id=role.pk,
            performed_by=performed_by,
            old_values={"removed": list(permissions_removed)} if permissions_removed else None,
            new_values={"added": list(permissions_added)} if permissions_added else None,
            description=f"Permissions updated for role '{role.code}'",
        )

    @classmethod
    def log_user_permission_override(cls, user, permission_code, override_type, performed_by, reason=""):
        cls.log(
            event_type="USER_PERMISSION_OVERRIDE",
            entity_type="User",
            entity_id=user.pk,
            performed_by=performed_by,
            new_values={"permission": permission_code, "override_type": override_type, "reason": reason},
            description=f"Permission override '{override_type}' for '{permission_code}' on {user.email}",
        )

    @classmethod
    def log_user_status_change(cls, user, is_active, performed_by):
        event_type = "USER_ACTIVATED" if is_active else "USER_DEACTIVATED"
        cls.log(
            event_type=event_type,
            entity_type="User",
            entity_id=user.pk,
            performed_by=performed_by,
            new_values={"is_active": is_active},
            description=f"User {user.email} {'activated' if is_active else 'deactivated'}",
        )

    @classmethod
    def log_role_created(cls, role, performed_by):
        cls.log(
            event_type="ROLE_CREATED",
            entity_type="Role",
            entity_id=role.pk,
            performed_by=performed_by,
            new_values={"code": role.code, "name": role.name},
            description=f"Role '{role.code}' created",
        )

    @classmethod
    def log_role_updated(cls, role, old_values, performed_by):
        cls.log(
            event_type="ROLE_UPDATED",
            entity_type="Role",
            entity_id=role.pk,
            performed_by=performed_by,
            old_values=old_values,
            new_values={"name": role.name, "description": role.description},
            description=f"Role '{role.code}' updated",
        )
