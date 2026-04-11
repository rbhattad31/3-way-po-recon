"""AP Copilot models — chat persistence for the AI copilot workspace."""

import uuid

from django.conf import settings
from django.db import models

from apps.core.enums import (
    CopilotArtifactType,
    CopilotMessageType,
    CopilotSessionStatus,
)
from apps.core.models import TimestampMixin


class CopilotSession(TimestampMixin):
    """A conversation session between a user and the AP Copilot.

    Sessions can be linked to a specific case and/or invoice for contextual
    investigation.  Users can resume, pin, and archive sessions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    title = models.CharField(max_length=300, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="copilot_sessions",
    )

    # RBAC snapshot at session creation
    actor_primary_role = models.CharField(max_length=50, blank=True, default="")
    actor_roles_snapshot_json = models.JSONField(
        null=True, blank=True,
        help_text="List of active role codes at session creation",
    )

    # Case / invoice linking
    linked_case = models.ForeignKey(
        "cases.APCase",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="copilot_sessions",
    )
    linked_invoice = models.ForeignKey(
        "documents.Invoice",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="copilot_sessions",
    )

    # Status & flags
    status = models.CharField(
        max_length=20,
        choices=CopilotSessionStatus.choices,
        default=CopilotSessionStatus.ACTIVE,
        db_index=True,
    )
    is_pinned = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(null=True, blank=True)

    # Traceability
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        db_table = "copilot_session"
        ordering = ["-last_message_at", "-created_at"]
        verbose_name = "Copilot Session"
        verbose_name_plural = "Copilot Sessions"
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["linked_case"]),
            models.Index(fields=["is_pinned", "-last_message_at"]),
        ]

    def __str__(self):
        title = self.title or "Untitled"
        return f"Copilot #{str(self.id)[:8]} — {title}"


class CopilotMessage(TimestampMixin):
    """A single message (user, assistant, or system) within a copilot session."""

    session = models.ForeignKey(
        CopilotSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    message_type = models.CharField(
        max_length=20,
        choices=CopilotMessageType.choices,
        db_index=True,
    )
    content = models.TextField(help_text="Plain-text / markdown content")

    # Structured payload for assistant responses
    structured_payload_json = models.JSONField(
        null=True, blank=True,
        help_text="Structured response: summary, evidence, recommendation, follow_up_prompts",
    )
    consulted_agents_json = models.JSONField(
        null=True, blank=True,
        help_text="List of agent names consulted to build the response",
    )
    evidence_payload_json = models.JSONField(
        null=True, blank=True,
        help_text="Evidence cards: invoice, PO, GRN, exception details",
    )
    governance_payload_json = models.JSONField(
        null=True, blank=True,
        help_text="Governance metadata: trace_id, permissions, access decisions",
    )

    # Case cross-reference (may differ from session-level link)
    linked_case_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    # LLM cost tracking
    token_count = models.PositiveIntegerField(null=True, blank=True)

    # Traceability
    trace_id = models.CharField(max_length=64, blank=True, default="")
    span_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "copilot_message"
        ordering = ["created_at"]
        verbose_name = "Copilot Message"
        verbose_name_plural = "Copilot Messages"
        indexes = [
            models.Index(fields=["session", "created_at"]),
        ]

    def __str__(self):
        return f"[{self.message_type}] {self.content[:60]}"


class CopilotSessionArtifact(TimestampMixin):
    """Links a copilot session to a business object for quick reference."""

    session = models.ForeignKey(
        CopilotSession,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    artifact_type = models.CharField(
        max_length=30,
        choices=CopilotArtifactType.choices,
    )
    linked_object_type = models.CharField(
        max_length=100,
        help_text="e.g. Invoice, PurchaseOrder, ReconciliationResult",
    )
    linked_object_id = models.BigIntegerField()
    payload_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "copilot_session_artifact"
        ordering = ["-created_at"]
        verbose_name = "Copilot Session Artifact"
        verbose_name_plural = "Copilot Session Artifacts"

    def __str__(self):
        return f"{self.artifact_type}: {self.linked_object_type}#{self.linked_object_id}"
