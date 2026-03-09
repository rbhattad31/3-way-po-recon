"""User and role models."""
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from apps.core.enums import UserRole
from apps.core.models import TimestampMixin
from apps.accounts.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, TimestampMixin):
    """Custom user with email login and role-based access."""

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=30, choices=UserRole.choices, default=UserRole.AP_PROCESSOR)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    department = models.CharField(max_length=100, blank=True, default="")

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
