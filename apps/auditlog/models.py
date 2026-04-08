"""Audit and operational logging models."""
from django.conf import settings
from django.db import models

from apps.core.models import TimestampMixin


class ProcessingLog(TimestampMixin):
    """Operational log for pipeline steps (extraction, recon, agent, etc.).

    This is the observability layer — durations, retries, failures, queue health.
    Do NOT use this for business audit events (use AuditEvent instead).
    """

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    level = models.CharField(max_length=10, default="INFO", db_index=True)
    source = models.CharField(max_length=100, db_index=True, help_text="Module or service name")
    event = models.CharField(max_length=200, db_index=True)
    message = models.TextField()
    details = models.JSONField(null=True, blank=True)
    invoice_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    case_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    reconciliation_result_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    review_assignment_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    agent_run_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    span_id = models.CharField(max_length=64, blank=True, default="")

    # Observability fields
    task_name = models.CharField(max_length=200, blank=True, default="")
    task_id = models.CharField(max_length=100, blank=True, default="")
    service_name = models.CharField(max_length=100, blank=True, default="")
    endpoint_name = models.CharField(max_length=200, blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    success = models.BooleanField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(null=True, blank=True)
    exception_class = models.CharField(max_length=200, blank=True, default="")
    error_code = models.CharField(max_length=100, blank=True, default="")

    # RBAC context (nullable — only populated for sensitive operations)
    actor_primary_role = models.CharField(max_length=50, blank=True, default="")
    permission_checked = models.CharField(max_length=100, blank=True, default="")
    access_granted = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "auditlog_processing_log"
        ordering = ["-created_at"]
        verbose_name = "Processing Log"
        verbose_name_plural = "Processing Logs"
        indexes = [
            models.Index(fields=["source", "event"], name="idx_proclog_src_event"),
            models.Index(fields=["level"], name="idx_proclog_level"),
            models.Index(fields=["trace_id"], name="idx_proclog_trace"),
            models.Index(fields=["task_name"], name="idx_proclog_task"),
            models.Index(fields=["success"], name="idx_proclog_success"),
            models.Index(fields=["case_id"], name="idx_proclog_case"),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.source}.{self.event} – {self.message[:80]}"


class AuditEvent(TimestampMixin):
    """Compliance-grade business event history.

    Captures business-significant events only:
    - invoice uploaded, extraction completed, duplicate flagged
    - reconciliation triggered/completed, mode resolved
    - review assigned/approved/rejected, field corrected
    - override applied, reprocess requested, case rerouted/closed
    - role/permission changes, access denied for sensitive actions

    Do NOT use this for operational noise (use ProcessingLog instead).
    """

    entity_type = models.CharField(max_length=100, db_index=True, help_text="e.g. Invoice, ReconciliationResult")
    entity_id = models.BigIntegerField(db_index=True)
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
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

    # Traceability fields
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    span_id = models.CharField(max_length=64, blank=True, default="")
    parent_span_id = models.CharField(max_length=64, blank=True, default="")

    # Entity cross-references (for efficient queries)
    invoice_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    case_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    reconciliation_result_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    review_assignment_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    agent_run_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    # RBAC context snapshot (captured at action time)
    actor_email = models.EmailField(blank=True, default="")
    actor_primary_role = models.CharField(max_length=50, blank=True, default="")
    actor_roles_snapshot_json = models.JSONField(null=True, blank=True, help_text="List of active role codes at action time")
    permission_checked = models.CharField(max_length=100, blank=True, default="")
    permission_source = models.CharField(
        max_length=50, blank=True, default="",
        help_text="ROLE | USER_OVERRIDE_ALLOW | ADMIN_BYPASS | USER_OVERRIDE_DENY | NO_PERMISSION | USER_INACTIVE",
    )
    access_granted = models.BooleanField(null=True, blank=True)

    # Business context
    status_before = models.CharField(max_length=60, blank=True, default="")
    status_after = models.CharField(max_length=60, blank=True, default="")
    reason_code = models.CharField(max_length=100, blank=True, default="")

    # Payload snapshots (redacted)
    input_snapshot_json = models.JSONField(null=True, blank=True, help_text="Redacted input snapshot")
    output_snapshot_json = models.JSONField(null=True, blank=True, help_text="Redacted output snapshot")

    # Operational
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True, default="")
    is_redacted = models.BooleanField(default=False)

    class Meta:
        db_table = "auditlog_audit_event"
        ordering = ["-created_at"]
        verbose_name = "Audit Event"
        verbose_name_plural = "Audit Events"
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="idx_audit_entity"),
            models.Index(fields=["action"], name="idx_audit_action"),
            models.Index(fields=["event_type"], name="idx_audit_event_type"),
            models.Index(fields=["trace_id"], name="idx_audit_trace"),
            models.Index(fields=["invoice_id"], name="idx_audit_invoice"),
            models.Index(fields=["case_id"], name="idx_audit_case"),
            models.Index(fields=["actor_primary_role"], name="idx_audit_role"),
            models.Index(fields=["permission_checked"], name="idx_audit_perm"),
            models.Index(fields=["access_granted"], name="idx_audit_access"),
        ]

    def __str__(self) -> str:
        return f"{self.action} on {self.entity_type}#{self.entity_id}"


class FileProcessingStatus(TimestampMixin):
    """Tracks processing lifecycle for uploaded files."""

    document_upload = models.ForeignKey(
        "documents.DocumentUpload", on_delete=models.CASCADE, related_name="processing_statuses"
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
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
