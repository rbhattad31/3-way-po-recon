"""
Enterprise RBAC models for role-based access control.

Design:
- Role: system and custom roles with rank ordering
- Permission: action-based permissions organized by module
- RolePermission: many-to-many mapping between roles and permissions
- UserRole: user-to-role assignments with expiry and audit
- UserPermissionOverride: per-user ALLOW/DENY overrides

Permission precedence (highest to lowest):
1. ADMIN role → always granted (bypass all checks)
2. User-level DENY override → blocks even if role grants it
3. User-level ALLOW override → grants even if no role grants it
4. Role-level permissions → union of all active role permissions
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.enums import PermissionOverrideType
from apps.core.models import TimestampMixin


class Role(TimestampMixin):
    """RBAC role definition. System roles mirror the legacy UserRole enum."""

    code = models.CharField(
        max_length=50, unique=True, db_index=True,
        help_text="Uppercase machine name, e.g. ADMIN, AP_PROCESSOR",
    )
    name = models.CharField(max_length=150, help_text="Human-readable role label")
    description = models.TextField(blank=True, default="")
    is_system_role = models.BooleanField(
        default=False, db_index=True,
        help_text="System roles cannot be deleted or have their code changed",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    rank = models.PositiveIntegerField(
        default=100,
        help_text="Lower rank = higher authority. Used for display ordering.",
    )

    class Meta:
        db_table = "accounts_role"
        ordering = ["rank", "code"]
        verbose_name = "Role"
        verbose_name_plural = "Roles"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Permission(TimestampMixin):
    """Fine-grained permission definition, organized by module.action."""

    code = models.CharField(
        max_length=100, unique=True, db_index=True,
        help_text="Permission code, e.g. invoices.view, reconciliation.run",
    )
    name = models.CharField(max_length=200, help_text="Human-readable permission label")
    module = models.CharField(
        max_length=50, db_index=True,
        help_text="Grouping module: invoices, reconciliation, cases, etc.",
    )
    action = models.CharField(
        max_length=50,
        help_text="Action within module: view, create, edit, delete, etc.",
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "accounts_permission"
        ordering = ["module", "action"]
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"
        indexes = [
            models.Index(fields=["module", "action"], name="idx_perm_module_action"),
        ]

    def __str__(self) -> str:
        return f"{self.code} – {self.name}"


class RolePermission(TimestampMixin):
    """Maps permissions to roles."""

    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="role_permissions",
    )
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions",
    )
    is_allowed = models.BooleanField(
        default=True,
        help_text="True = role grants this permission",
    )

    class Meta:
        db_table = "accounts_role_permission"
        unique_together = [("role", "permission")]
        verbose_name = "Role Permission"
        verbose_name_plural = "Role Permissions"

    def __str__(self) -> str:
        status = "ALLOWED" if self.is_allowed else "DENIED"
        return f"{self.role.code} → {self.permission.code} [{status}]"


class UserRole(TimestampMixin):
    """User-to-role assignment with optional expiry."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="user_roles",
    )
    is_primary = models.BooleanField(
        default=False, db_index=True,
        help_text="Primary role is synced to the legacy User.role field",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="roles_assigned",
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Null = never expires",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    scope_json = models.JSONField(
        null=True, blank=True,
        help_text=(
            "Optional scope restrictions for this specific role assignment. "
            "Null means unrestricted (full role scope). "
            "Supported keys: allowed_business_units (list[str]), "
            "allowed_vendor_ids (list[int]). "
            "Unsupported / pending: country, legal_entity, cost_centre "
            "(require schema extension on Invoice/PurchaseOrder)."
        ),
    )

    class Meta:
        db_table = "accounts_user_role"
        unique_together = [("user", "role")]
        verbose_name = "User Role"
        verbose_name_plural = "User Roles"
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_userrole_user_active"),
            models.Index(fields=["role", "is_active"], name="idx_userrole_role_active"),
        ]

    def __str__(self) -> str:
        primary = " [PRIMARY]" if self.is_primary else ""
        return f"{self.user} → {self.role.code}{primary}"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at

    @property
    def is_effective(self) -> bool:
        """Active and not expired."""
        return self.is_active and not self.is_expired


class UserPermissionOverride(TimestampMixin):
    """Per-user permission override (ALLOW or DENY)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="permission_overrides",
    )
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="user_overrides",
    )
    override_type = models.CharField(
        max_length=10, choices=PermissionOverrideType.choices,
    )
    reason = models.TextField(blank=True, default="")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="overrides_assigned",
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Null = never expires",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "accounts_user_permission_override"
        unique_together = [("user", "permission")]
        verbose_name = "User Permission Override"
        verbose_name_plural = "User Permission Overrides"
        indexes = [
            models.Index(
                fields=["user", "is_active"],
                name="idx_userpermovr_user_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.permission.code} [{self.override_type}]"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at

    @property
    def is_effective(self) -> bool:
        return self.is_active and not self.is_expired


class MenuConfig(TimestampMixin):
    """Controls sidebar/menu item visibility by permission code."""

    label = models.CharField(max_length=100)
    icon_class = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Bootstrap icon class, e.g. bi-speedometer2",
    )
    url_name = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Django URL name to reverse, e.g. dashboard:index",
    )
    required_permission = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Permission code required to see this menu item. Empty = visible to all.",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="children",
    )
    order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    is_separator = models.BooleanField(
        default=False,
        help_text="If true, renders as a visual separator line",
    )

    class Meta:
        db_table = "accounts_menu_config"
        ordering = ["order", "label"]
        verbose_name = "Menu Config"
        verbose_name_plural = "Menu Configs"

    def __str__(self) -> str:
        return self.label
