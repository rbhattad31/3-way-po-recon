"""User, company profile, and role models."""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.enums import UserRole
from apps.core.models import TimestampMixin
from apps.accounts.managers import UserManager


# ---------------------------------------------------------------------------
# Company Profile -- the organisation that owns this platform instance
# ---------------------------------------------------------------------------

class CompanyProfile(TimestampMixin):
    """Organisation profile for self-company detection during extraction.

    Stores the buyer/owning company's identifiers (legal name, trading
    names, GSTIN/VAT numbers) so the extraction pipeline can distinguish
    the vendor (who issued the invoice) from the buyer (our company).

    Typically one active profile exists per deployment, but the model
    supports multiple profiles for group companies / subsidiaries.
    """

    name = models.CharField(max_length=255, help_text="Primary display name")
    legal_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Registered legal name (for exact matching)",
    )
    tax_id = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Primary GSTIN / VAT / TIN of the company",
    )
    country = models.CharField(
        max_length=10, blank=True, default="",
        help_text="ISO country code (IN, AE, US, ...)",
    )
    state_code = models.CharField(
        max_length=10, blank=True, default="",
        help_text="State code (for India GST)",
    )
    address = models.TextField(blank=True, default="")
    currency = models.CharField(max_length=10, blank=True, default="INR")
    website = models.URLField(blank=True, default="")

    is_default = models.BooleanField(
        default=False,
        help_text="Mark as the primary company profile for extraction",
    )
    is_active = models.BooleanField(default=True)

    # ------------------------------------------------------------------
    # Tenant / SaaS fields
    # ------------------------------------------------------------------
    slug = models.SlugField(
        max_length=80, unique=True, db_index=True, blank=True,
        help_text="URL-safe identifier used for subdomain or path routing.",
    )
    plan_type = models.CharField(
        max_length=30,
        choices=[
            ("trial", "Trial"),
            ("starter", "Starter"),
            ("professional", "Professional"),
            ("enterprise", "Enterprise"),
        ],
        default="trial",
    )
    timezone = models.CharField(max_length=60, default="UTC")
    max_users = models.PositiveSmallIntegerField(default=10)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tenants",
    )
    onboarded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_company_profile"
        ordering = ["-is_default", "name"]
        verbose_name = "Tenant / Company Profile"
        verbose_name_plural = "Tenants / Company Profiles"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        # Ensure only one default profile
        if self.is_default:
            CompanyProfile.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_default(cls) -> "CompanyProfile | None":
        """Return the default (primary) company profile, or None."""
        return cls.objects.filter(is_default=True, is_active=True).first()


class CompanyAlias(TimestampMixin):
    """Alternate names / trading names for a company.

    Used during extraction to detect when the LLM picks the buyer's
    company name as the vendor.  Examples: abbreviations, former names,
    division names, subsidiary names.
    """

    company = models.ForeignKey(
        CompanyProfile, on_delete=models.CASCADE, related_name="aliases",
    )
    alias_name = models.CharField(max_length=255)
    normalized_alias = models.CharField(
        max_length=255, blank=True, db_index=True,
        help_text="Auto-generated lowercase stripped version for matching",
    )

    class Meta:
        db_table = "accounts_company_alias"
        ordering = ["company", "alias_name"]
        verbose_name = "Company Alias"
        verbose_name_plural = "Company Aliases"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "normalized_alias"],
                name="uq_company_alias_norm",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.alias_name} -> {self.company.name}"

    def save(self, *args, **kwargs):
        if not self.normalized_alias:
            self.normalized_alias = self.alias_name.strip().lower()
        super().save(*args, **kwargs)


class CompanyTaxID(TimestampMixin):
    """Additional GSTIN / VAT / TIN numbers for a company.

    A company may have multiple GSTINs (one per Indian state) or
    multiple VAT registrations. These are all used during self-company
    detection.
    """

    company = models.ForeignKey(
        CompanyProfile, on_delete=models.CASCADE, related_name="tax_ids",
    )
    tax_id = models.CharField(max_length=50, db_index=True)
    label = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Optional label, e.g. 'Maharashtra GSTIN', 'Karnataka GSTIN'",
    )
    state_code = models.CharField(max_length=10, blank=True, default="")

    class Meta:
        db_table = "accounts_company_tax_id"
        ordering = ["company", "tax_id"]
        verbose_name = "Company Tax ID"
        verbose_name_plural = "Company Tax IDs"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "tax_id"],
                name="uq_company_tax_id",
            ),
        ]

    def __str__(self) -> str:
        label = f" ({self.label})" if self.label else ""
        return f"{self.tax_id}{label}"


class User(AbstractBaseUser, PermissionsMixin, TimestampMixin):
    """Custom user with email login and role-based access."""

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=30, choices=UserRole.choices, default=UserRole.AP_PROCESSOR)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_platform_admin = models.BooleanField(
        default=False,
        help_text="Platform-level super admin -- bypasses tenant scoping and has all permissions across all tenants.",
    )
    department = models.CharField(max_length=100, blank=True, default="")
    company = models.ForeignKey(
        CompanyProfile, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="users",
        help_text="The organisation this user belongs to",
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        db_table = "accounts_user"
        ordering = ["email"]
        verbose_name = "User"
        verbose_name_plural = "Users"
        indexes = [
            models.Index(fields=["role"], name="idx_user_role"),
        ]

    def __str__(self) -> str:
        return f"{self.get_full_name()} ({self.email})"

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_short_name(self) -> str:
        return self.first_name or self.email.split("@")[0]

    # ------------------------------------------------------------------
    # RBAC helper methods
    # ------------------------------------------------------------------

    def get_primary_role(self):
        """Return the primary Role object, falling back to legacy role field."""
        from apps.accounts.rbac_models import UserRole as UserRoleModel
        primary = (
            UserRoleModel.objects
            .filter(user=self, is_primary=True, is_active=True)
            .select_related("role")
            .first()
        )
        if primary and primary.is_effective:
            return primary.role
        # Fallback: use legacy role field to find matching Role record
        from apps.accounts.rbac_models import Role
        return Role.objects.filter(code=self.role, is_active=True).first()

    def get_all_roles(self):
        """Return queryset of all active, non-expired Role objects for this user."""
        from apps.accounts.rbac_models import UserRole as UserRoleModel, Role
        now = timezone.now()
        role_ids = (
            UserRoleModel.objects
            .filter(user=self, is_active=True)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .values_list("role_id", flat=True)
        )
        if role_ids:
            return Role.objects.filter(id__in=role_ids, is_active=True)
        # Fallback: single legacy role
        return Role.objects.filter(code=self.role, is_active=True)

    def get_role_codes(self):
        """Return set of active role codes. Cached on the instance for the request."""
        if hasattr(self, "_cached_role_codes"):
            return self._cached_role_codes
        codes = set(self.get_all_roles().values_list("code", flat=True))
        if not codes:
            codes = {self.role}  # legacy fallback
        self._cached_role_codes = codes
        return codes

    def has_role(self, role_code: str) -> bool:
        """Check if user has a specific role (active, non-expired)."""
        if self.is_platform_admin:
            return True
        if self.role == "ADMIN" or "ADMIN" in self.get_role_codes():
            return True
        return role_code in self.get_role_codes()

    def has_any_role(self, role_codes) -> bool:
        """Check if user has any of the given roles."""
        if self.is_platform_admin:
            return True
        if self.role == "ADMIN" or "ADMIN" in self.get_role_codes():
            return True
        return bool(set(role_codes) & self.get_role_codes())

    def has_permission(self, permission_code: str) -> bool:
        """Check if user has a specific permission via RBAC.

        Precedence:
        1. ADMIN → always True
        2. User DENY override → False
        3. User ALLOW override → True
        4. Role-level permission → True if any role grants it
        5. Default → False
        """
        if self.is_platform_admin:
            return True
        if self.role == "ADMIN" or "ADMIN" in self.get_role_codes():
            return True
        effective = self.get_effective_permissions()
        return permission_code in effective

    def has_any_permission(self, permission_codes) -> bool:
        """Check if user has any of the given permissions."""
        if self.is_platform_admin:
            return True
        if self.role == "ADMIN" or "ADMIN" in self.get_role_codes():
            return True
        effective = self.get_effective_permissions()
        return bool(set(permission_codes) & effective)

    def get_effective_permissions(self):
        """Compute the full set of effective permission codes.

        Result is cached on the instance for the request lifecycle.
        """
        if hasattr(self, "_cached_permissions"):
            return self._cached_permissions

        from apps.accounts.rbac_models import (
            RolePermission, UserPermissionOverride, UserRole as UserRoleModel,
        )

        now = timezone.now()

        # 1. Gather role-level permissions
        active_role_ids = (
            UserRoleModel.objects
            .filter(user=self, is_active=True)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .values_list("role_id", flat=True)
        )
        if not active_role_ids:
            # Legacy fallback: look up role by code
            from apps.accounts.rbac_models import Role
            legacy_role = Role.objects.filter(code=self.role, is_active=True).first()
            active_role_ids = [legacy_role.id] if legacy_role else []

        role_perms = set(
            RolePermission.objects
            .filter(role_id__in=active_role_ids, is_allowed=True, permission__is_active=True)
            .values_list("permission__code", flat=True)
        )

        # 2. Apply user-level overrides
        overrides = (
            UserPermissionOverride.objects
            .filter(user=self, is_active=True, permission__is_active=True)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .select_related("permission")
            .values_list("permission__code", "override_type")
        )
        for perm_code, override_type in overrides:
            if override_type == "DENY":
                role_perms.discard(perm_code)
            elif override_type == "ALLOW":
                role_perms.add(perm_code)

        self._cached_permissions = frozenset(role_perms)
        return self._cached_permissions

    def clear_permission_cache(self):
        """Clear cached permissions/roles. Call after role or permission changes."""
        for attr in ("_cached_permissions", "_cached_role_codes"):
            if hasattr(self, attr):
                delattr(self, attr)

    def sync_legacy_role_field(self):
        """Sync the legacy User.role field from the primary UserRole assignment.

        Call this after changing role assignments to keep backward compatibility.
        """
        from apps.accounts.rbac_models import UserRole as UserRoleModel
        primary = (
            UserRoleModel.objects
            .filter(user=self, is_primary=True, is_active=True)
            .select_related("role")
            .first()
        )
        if primary and primary.is_effective:
            new_code = primary.role.code
            if self.role != new_code:
                self.role = new_code
                self.save(update_fields=["role", "updated_at"])


class TenantInvitation(TimestampMixin):
    """Tracks pending email invitations for new users to join a tenant."""

    tenant = models.ForeignKey(
        CompanyProfile, on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField(db_index=True)
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )
    role_code = models.CharField(max_length=50, default="AP_PROCESSOR")
    token = models.CharField(max_length=128, unique=True, db_index=True)
    accepted = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "accounts_tenant_invitation"
        unique_together = [("tenant", "email")]
        verbose_name = "Tenant Invitation"
        verbose_name_plural = "Tenant Invitations"

    def __str__(self) -> str:
        return f"Invitation for {self.email} → {self.tenant.name}"

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone as tz
        return tz.now() > self.expires_at

    @property
    def is_usable(self) -> bool:
        return not self.accepted and not self.is_expired


# Re-export RBAC models so they are accessible from apps.accounts.models
from apps.accounts.rbac_models import (  # noqa: E402, F401
    Role,
    Permission,
    RolePermission,
    UserRole as UserRoleAssignment,
    UserPermissionOverride,
    MenuConfig,
)
