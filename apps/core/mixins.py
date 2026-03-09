"""Reusable model mixins."""
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
