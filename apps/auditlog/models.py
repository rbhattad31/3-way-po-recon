"""Audit and operational logging models."""
from django.conf import settings
from django.db import models

from apps.core.models import TimestampMixin


class ProcessingLog(TimestampMixin):
    """Operational log for pipeline steps (extraction, recon, agent, etc.)."""

    level = models.CharField(max_length=10, default="INFO", db_index=True)
    source = models.CharField(max_length=100, db_index=True, help_text="Module or service name")
    event = models.CharField(max_length=200, db_index=True)
    message = models.TextField()
    details = models.JSONField(null=True, blank=True)
    invoice_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    reconciliation_result_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    agent_run_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        db_table = "auditlog_processing_log"
        ordering = ["-created_at"]
        verbose_name = "Processing Log"
        verbose_name_plural = "Processing Logs"
        indexes = [
            models.Index(fields=["source", "event"], name="idx_proclog_src_event"),
            models.Index(fields=["level"], name="idx_proclog_level"),
            models.Index(fields=["trace_id"], name="idx_proclog_trace"),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.source}.{self.event} – {self.message[:80]}"


class AuditEvent(TimestampMixin):
    """State change audit trail and governance event log on business objects.

    Supports two usage patterns:
    - State-change audits: old_values/new_values capture before/after
    - Governance events: event_type/event_description capture typed lifecycle events
    """

    entity_type = models.CharField(max_length=100, db_index=True, help_text="e.g. Invoice, ReconciliationResult")
    entity_id = models.BigIntegerField(db_index=True)
    action = models.CharField(max_length=50, db_index=True, help_text="created, updated, status_change, etc.")
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")

    # Governance extensions
    event_type = models.CharField(max_length=60, blank=True, default="", db_index=True, help_text="Typed event (AuditEventType enum)")
    event_description = models.TextField(blank=True, default="")
    performed_by_agent = models.CharField(max_length=100, blank=True, default="", help_text="Agent name if action performed by an agent")
    metadata_json = models.JSONField(null=True, blank=True, help_text="Additional structured context")

    class Meta:
        db_table = "auditlog_audit_event"
        ordering = ["-created_at"]
        verbose_name = "Audit Event"
        verbose_name_plural = "Audit Events"
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="idx_audit_entity"),
            models.Index(fields=["action"], name="idx_audit_action"),
            models.Index(fields=["event_type"], name="idx_audit_event_type"),
        ]

    def __str__(self) -> str:
        return f"{self.action} on {self.entity_type}#{self.entity_id}"


class FileProcessingStatus(TimestampMixin):
    """Tracks processing lifecycle for uploaded files."""

    document_upload = models.ForeignKey(
        "documents.DocumentUpload", on_delete=models.CASCADE, related_name="processing_statuses"
    )
    stage = models.CharField(max_length=100, db_index=True, help_text="upload, extraction, validation, recon, etc.")
    status = models.CharField(max_length=30, db_index=True)
    message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "auditlog_file_status"
        ordering = ["-created_at"]
        verbose_name = "File Processing Status"
        verbose_name_plural = "File Processing Statuses"
        indexes = [
            models.Index(fields=["document_upload", "stage"], name="idx_filestatus_doc_stage"),
        ]

    def __str__(self) -> str:
        return f"{self.stage} – {self.status} – Upload #{self.document_upload_id}"
