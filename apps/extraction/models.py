"""Extraction-specific models (status tracking lives on Invoice/DocumentUpload)."""
from django.db import models

from apps.core.models import BaseModel


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

    class Meta:
        db_table = "extraction_result"
        ordering = ["-created_at"]
        verbose_name = "Extraction Result"
        verbose_name_plural = "Extraction Results"

    def __str__(self) -> str:
        return f"Extraction #{self.pk} – upload {self.document_upload_id}"
