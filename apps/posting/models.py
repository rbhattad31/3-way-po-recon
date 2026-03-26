"""Posting business/application layer models.

InvoicePosting is the UI-facing posting workflow record (analogous to
ExtractionApproval in the extraction layer).  It tracks posting lifecycle,
review state, and correction history.
"""
from django.conf import settings
from django.db import models

from apps.core.enums import (
    InvoicePostingStatus,
    PostingReviewQueue,
    PostingStage,
)
from apps.core.models import BaseModel, TimestampMixin


class InvoicePosting(BaseModel):
    """Business-facing posting workflow record.

    Tracks the lifecycle of an invoice through posting proposal preparation,
    review, and submission.  Analogous to ExtractionApproval for extraction.
    """

    invoice = models.OneToOneField(
        "documents.Invoice",
        on_delete=models.CASCADE,
        related_name="posting",
    )
    extraction_result = models.ForeignKey(
        "extraction.ExtractionResult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postings",
    )
    extraction_run = models.ForeignKey(
        "extraction_core.ExtractionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postings",
    )
    status = models.CharField(
        max_length=30,
        choices=InvoicePostingStatus.choices,
        default=InvoicePostingStatus.NOT_READY,
        db_index=True,
    )
    stage = models.CharField(
        max_length=30,
        choices=PostingStage.choices,
        blank=True,
        default="",
    )
    posting_confidence = models.FloatField(null=True, blank=True)
    review_queue = models.CharField(
        max_length=30,
        choices=PostingReviewQueue.choices,
        blank=True,
        default="",
    )
    is_touchless = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posting_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    mapping_summary_json = models.JSONField(default=dict, blank=True)
    payload_snapshot_json = models.JSONField(default=dict, blank=True)
    posting_snapshot_batch_refs_json = models.JSONField(default=dict, blank=True)
    erp_document_number = models.CharField(max_length=100, blank=True, default="")
    last_error_code = models.CharField(max_length=50, blank=True, default="")
    last_error_message = models.TextField(blank=True, default="")
    retry_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "posting_invoice_posting"
        ordering = ["-created_at"]
        verbose_name = "Invoice Posting"
        verbose_name_plural = "Invoice Postings"
        indexes = [
            models.Index(fields=["status"], name="idx_posting_status"),
            models.Index(fields=["status", "created_at"], name="idx_posting_status_date"),
        ]

    def __str__(self) -> str:
        inv = self.invoice.invoice_number if self.invoice_id else "(unlinked)"
        return f"Posting {self.pk} — Invoice {inv} [{self.status}]"


class InvoicePostingFieldCorrection(TimestampMixin):
    """Tracks manual field corrections made during posting review."""

    posting = models.ForeignKey(
        InvoicePosting,
        on_delete=models.CASCADE,
        related_name="field_corrections",
    )
    entity_type = models.CharField(
        max_length=20,
        help_text="header, line_item, or mapping",
    )
    entity_id = models.BigIntegerField(null=True, blank=True)
    field_name = models.CharField(max_length=100)
    original_value = models.TextField(blank=True, default="")
    corrected_value = models.TextField(blank=True, default="")
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posting_corrections",
    )
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "posting_field_correction"
        ordering = ["-created_at"]
        verbose_name = "Posting Field Correction"
        verbose_name_plural = "Posting Field Corrections"

    def __str__(self) -> str:
        return f"Correction {self.pk}: {self.entity_type}.{self.field_name}"
