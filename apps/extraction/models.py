"""Extraction-specific models (status tracking lives on Invoice/DocumentUpload)."""
from django.conf import settings
from django.db import models

from apps.core.enums import ExtractionApprovalStatus
from apps.core.models import BaseModel

# Import credit models so Django discovers them for migrations
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount  # noqa: F401


class ExtractionResult(BaseModel):
    """Thin linking record between DocumentUpload and ExtractionRun.

    All execution state, timing, confidence, OCR data, and extracted
    payloads now live on ExtractionRun (apps/extraction_core).
    This model exists for backward-compatible FK references only.
    """

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    document_upload = models.ForeignKey(
        "documents.DocumentUpload", on_delete=models.CASCADE, related_name="extraction_results"
    )
    extraction_run = models.OneToOneField(
        "extraction_core.ExtractionRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="summary_result",
    )
    engine_name = models.CharField(max_length=100, default="default", help_text="Extraction engine identifier")
    engine_version = models.CharField(max_length=50, blank=True, default="")
    success = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")
    langfuse_trace_id = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="Langfuse root trace ID for the extraction pipeline run",
    )

    class Meta:
        db_table = "extraction_result"
        ordering = ["-created_at"]
        verbose_name = "Extraction Result"
        verbose_name_plural = "Extraction Results"

    def __str__(self) -> str:
        return f"Extraction #{self.pk} – upload {self.document_upload_id}"

    # ------------------------------------------------------------------
    # Backward-compatible read-only properties
    # Delegate to extraction_run so downstream code keeps working
    # while callers are migrated to read from ExtractionRun directly.
    # ------------------------------------------------------------------

    @property
    def confidence(self):
        override = self.__dict__.get("_confidence_override")
        if override is not None:
            return override
        run = self.extraction_run
        return run.overall_confidence if run else None

    @confidence.setter
    def confidence(self, value):
        # Allow in-memory override for template annotation.
        self.__dict__["_confidence_override"] = value

    @property
    def duration_ms(self):
        run = self.extraction_run
        return run.duration_ms if run else None

    @property
    def raw_response(self):
        run = self.extraction_run
        return run.extracted_data_json if run else None

    @property
    def invoice(self):
        try:
            if self.document_upload_id:
                return self.document_upload.invoices.first()
        except Exception:
            pass
        return None

    @property
    def invoice_id(self):
        inv = self.invoice
        return inv.pk if inv else None

    @property
    def agent_run_id(self):
        return None

    @property
    def ocr_page_count(self):
        run = self.extraction_run
        if run:
            try:
                return run.ocr_text_record.ocr_page_count
            except Exception:
                return 0
        return 0

    @property
    def ocr_duration_ms(self):
        run = self.extraction_run
        if run:
            try:
                return run.ocr_text_record.ocr_duration_ms
            except Exception:
                return None
        return None

    @property
    def ocr_char_count(self):
        run = self.extraction_run
        if run:
            try:
                return run.ocr_text_record.ocr_char_count
            except Exception:
                return 0
        return 0

    @property
    def ocr_text(self):
        run = self.extraction_run
        if run:
            try:
                return run.ocr_text_record.ocr_text
            except Exception:
                return ""
        return ""


class ExtractionApproval(BaseModel):
    """Human-in-the-loop approval gate for extraction results.

    Tracks approval status, reviewer, field corrections, and touchless
    analytics for each extracted invoice.
    """

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
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
    confidence_at_review = models.FloatField(
        null=True,
        blank=True,
        help_text="Extraction confidence at time of review",
    )
    original_values_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of invoice values before corrections",
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
    fields_corrected_count = models.PositiveIntegerField(default=0)
    is_touchless = models.BooleanField(
        default=False,
        help_text="True if approved without any field corrections",
    )

    class Meta:
        db_table = "extraction_approval"
        ordering = ["-created_at"]
        verbose_name = "Extraction Approval"
        verbose_name_plural = "Extraction Approvals"

    def __str__(self) -> str:
        return f"Approval #{self.pk} -- invoice {self.invoice_id} ({self.status})"


class ExtractionFieldCorrection(BaseModel):
    """Audit trail for field corrections made during extraction approval."""

    approval = models.ForeignKey(
        ExtractionApproval,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    entity_type = models.CharField(
        max_length=20,
        help_text="'header' or 'line_item'",
    )
    entity_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="PK of InvoiceLineItem (null for header corrections)",
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
        ordering = ["-created_at"]
        verbose_name = "Extraction Field Correction"
        verbose_name_plural = "Extraction Field Corrections"

    def __str__(self) -> str:
        return f"Correction #{self.pk} -- {self.entity_type}.{self.field_name}"
