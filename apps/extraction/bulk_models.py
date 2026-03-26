"""Models for Bulk Extraction Intake (Phase 1).

Three models track bulk extraction jobs:
- BulkSourceConnection: configured source (local folder, Google Drive, OneDrive)
- BulkExtractionJob: one manual bulk run against a source
- BulkExtractionItem: one discovered file within a job
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.enums import BulkItemStatus, BulkJobStatus, BulkSourceType
from apps.core.models import BaseModel, TimestampMixin


class BulkSourceConnection(BaseModel):
    """A configured bulk-intake source (folder, drive, etc.)."""

    name = models.CharField(max_length=200)
    source_type = models.CharField(
        max_length=30,
        choices=BulkSourceType.choices,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Source-specific config. "
            "LOCAL_FOLDER: {\"folder_path\": \"...\"} | "
            "GOOGLE_DRIVE: {\"folder_id\": \"...\", \"credentials_json\": \"...\"} | "
            "ONEDRIVE: {\"folder_id\": \"...\", \"tenant_id\": \"...\", "
            "\"client_id\": \"...\", \"client_secret\": \"...\", \"folder_path\": \"...\"}"
        ),
    )

    class Meta:
        db_table = "extraction_bulk_source_connection"
        ordering = ["-created_at"]
        verbose_name = "Bulk Source Connection"
        verbose_name_plural = "Bulk Source Connections"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_source_type_display()})"


class BulkExtractionJob(BaseModel):
    """One manual bulk extraction run against a source connection."""

    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    source_connection = models.ForeignKey(
        BulkSourceConnection,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    status = models.CharField(
        max_length=30,
        choices=BulkJobStatus.choices,
        default=BulkJobStatus.QUEUED,
        db_index=True,
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulk_extraction_jobs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Summary counters
    total_found = models.PositiveIntegerField(default=0)
    total_registered = models.PositiveIntegerField(default=0)
    total_success = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    total_skipped = models.PositiveIntegerField(default=0)
    total_credit_blocked = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "extraction_bulk_job"
        ordering = ["-created_at"]
        verbose_name = "Bulk Extraction Job"
        verbose_name_plural = "Bulk Extraction Jobs"

    def __str__(self) -> str:
        return f"BulkJob {self.job_id} ({self.get_status_display()})"


class BulkExtractionItem(TimestampMixin):
    """One discovered file within a bulk extraction job."""

    job = models.ForeignKey(
        BulkExtractionJob,
        on_delete=models.CASCADE,
        related_name="items",
    )
    source_file_id = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Cloud provider file ID or local file path",
    )
    source_name = models.CharField(max_length=500)
    source_path = models.CharField(max_length=1000, blank=True, default="")
    mime_type = models.CharField(max_length=100, blank=True, default="")
    file_size = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=30,
        choices=BulkItemStatus.choices,
        default=BulkItemStatus.DISCOVERED,
        db_index=True,
    )
    skip_reason = models.CharField(max_length=500, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    document_upload = models.ForeignKey(
        "documents.DocumentUpload",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulk_items",
    )
    extraction_run = models.ForeignKey(
        "extraction_core.ExtractionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bulk_items",
    )

    class Meta:
        db_table = "extraction_bulk_item"
        ordering = ["created_at"]
        verbose_name = "Bulk Extraction Item"
        verbose_name_plural = "Bulk Extraction Items"
        indexes = [
            models.Index(fields=["job", "status"]),
            models.Index(fields=["source_file_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.source_name} ({self.get_status_display()})"
