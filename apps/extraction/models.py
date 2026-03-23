"""Extraction-specific models (status tracking lives on Invoice/DocumentUpload)."""
from django.conf import settings
from django.db import models

from apps.core.enums import ExtractionApprovalStatus
from apps.core.models import BaseModel, TimestampMixin

# Import credit models so Django discovers them for migrations
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount  # noqa: F401


class ExtractionResult(BaseModel):
    """Stores per-extraction-run metadata for audit and reprocessing."""

    document_upload = models.ForeignKey(
        "documents.DocumentUpload", on_delete=models.CASCADE, related_name="extraction_results"
    )
    invoice = models.ForeignKey(
        "documents.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="extraction_results"
    )
    engine_name = models.CharField(max_length=100, default="default", help_text="Extraction engine identifier")
    engine_version = models.CharField(max_length=50, blank=True, default="")
    raw_response = models.JSONField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")
    agent_run_id = models.BigIntegerField(null=True, blank=True, db_index=True,
                                          help_text="FK to AgentRun that performed extraction")
    ocr_page_count = models.PositiveIntegerField(default=0, help_text="Number of pages processed by OCR")
    ocr_duration_ms = models.PositiveIntegerField(null=True, blank=True, help_text="OCR processing time in ms")
    ocr_char_count = models.PositiveIntegerField(default=0, help_text="Characters extracted by OCR")

    class Meta:
        db_table = "extraction_result"
        ordering = ["-created_at"]
        verbose_name = "Extraction Result"
        verbose_name_plural = "Extraction Results"

    def __str__(self) -> str:
        return f"Extraction #{self.pk} – upload {self.document_upload_id}"


# ---------------------------------------------------------------------------
# Extraction Approval — human-in-the-loop gate post-extraction
# ---------------------------------------------------------------------------
class ExtractionApproval(BaseModel):
    """Tracks human approval or auto-approval of an extraction result.

    Every successful extraction creates an ExtractionApproval in PENDING
    state.  A human reviewer inspects the extracted data, optionally
    corrects fields, then approves/rejects.  When confidence exceeds the
    configured auto-approval threshold the system may auto-approve.

    Analytics queries on ``is_touchless`` and ``fields_corrected_count``
    provide touchless-processing vs human-in-the-loop metrics.
    """

    invoice = models.OneToOneField(
        "documents.Invoice",
        on_delete=models.CASCADE,
        related_name="extraction_approval",
    )
    extraction_result = models.ForeignKey(
        ExtractionResult,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approvals",
    )
    status = models.CharField(
        max_length=20,
        choices=ExtractionApprovalStatus.choices,
        default=ExtractionApprovalStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_approvals_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    # Snapshot of extraction confidence at approval time
    confidence_at_review = models.FloatField(null=True, blank=True)

    # Snapshot of extracted values BEFORE any human corrections
    original_values_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Invoice header + line item values as extracted (pre-correction).",
    )

    # Summary counters for analytics
    fields_corrected_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of individual fields corrected by human.",
    )
    is_touchless = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if approved without any human field corrections.",
    )

    class Meta:
        db_table = "extraction_approval"
        ordering = ["-created_at"]
        verbose_name = "Extraction Approval"
        verbose_name_plural = "Extraction Approvals"
        indexes = [
            models.Index(fields=["status"], name="idx_extappr_status"),
            models.Index(fields=["is_touchless"], name="idx_extappr_touchless"),
        ]

    def __str__(self) -> str:
        return f"Approval #{self.pk} – Invoice {self.invoice_id} ({self.status})"


class ExtractionFieldCorrection(TimestampMixin):
    """Records a single field correction made during extraction approval.

    Each row captures the before/after value for one field, enabling
    granular analytics on which fields the model gets wrong most often.
    """

    approval = models.ForeignKey(
        ExtractionApproval,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    entity_type = models.CharField(
        max_length=20,
        help_text="'header' or 'line_item'",
    )
    entity_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="PK of the InvoiceLineItem (null for header corrections).",
    )
    field_name = models.CharField(max_length=100)
    original_value = models.TextField(blank=True, default="")
    corrected_value = models.TextField(blank=True, default="")
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_field_corrections",
    )

    class Meta:
        db_table = "extraction_field_correction"
        ordering = ["approval", "entity_type", "field_name"]
        verbose_name = "Extraction Field Correction"
        verbose_name_plural = "Extraction Field Corrections"
        indexes = [
            models.Index(fields=["field_name"], name="idx_extcorr_field"),
            models.Index(fields=["entity_type"], name="idx_extcorr_entity"),
        ]

    def __str__(self) -> str:
        return f"Correction: {self.entity_type}.{self.field_name} on Approval #{self.approval_id}"
