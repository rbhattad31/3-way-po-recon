"""Base / abstract models shared by all apps."""
from django.conf import settings
from django.db import models


class TimestampMixin(models.Model):
    """Adds created_at / updated_at timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditMixin(models.Model):
    """Adds created_by / updated_by tracking."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_updated",
    )

    class Meta:
        abstract = True


class BaseModel(TimestampMixin, AuditMixin):
    """Standard base model with timestamps and audit fields."""

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Prompt Template — single source of truth for all LLM prompts
# ---------------------------------------------------------------------------
class PromptTemplate(TimestampMixin):
    """Stores LLM prompt templates, editable via Admin.

    Prompts are identified by a unique ``slug`` (e.g. ``extraction.invoice_system``,
    ``agent.exception_analysis``).  The ``content`` field supports ``{variable}``
    placeholders that are filled at runtime via ``str.format_map()``.
    """

    slug = models.SlugField(
        max_length=120, unique=True, db_index=True,
        help_text="Unique identifier (e.g. extraction.invoice_system, agent.po_retrieval)",
    )
    name = models.CharField(max_length=200, help_text="Human-readable label")
    category = models.CharField(
        max_length=50, db_index=True,
        help_text="Grouping category: extraction, agent, case, misc",
    )
    content = models.TextField(help_text="Prompt text. Use {variable} for runtime placeholders.")
    description = models.TextField(blank=True, default="", help_text="Internal notes about this prompt")
    is_active = models.BooleanField(default=True, db_index=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "core_prompt_template"
        ordering = ["category", "slug"]
        verbose_name = "Prompt Template"
        verbose_name_plural = "Prompt Templates"

    def __str__(self) -> str:
        return f"{self.slug} (v{self.version})"

    def render(self, **kwargs) -> str:
        """Render the prompt with the given variables.

        Uses ``str.format_map`` with a defaultdict so missing keys are left
        as-is rather than raising ``KeyError``.
        """
        from collections import defaultdict
        safe = defaultdict(lambda: "", kwargs)
        return self.content.format_map(safe)
