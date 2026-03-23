"""Extraction Documents models — document-level extraction metadata and results."""
from django.db import models

from apps.core.models import BaseModel, TimestampMixin


class ExtractionDocument(BaseModel):
    """
    Represents a document submitted for multi-country extraction.

    Links to the resolved jurisdiction and schema, and stores the
    extraction lifecycle status and results.
    """

    class ExtractionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CLASSIFYING = "CLASSIFYING", "Classifying"
        EXTRACTING = "EXTRACTING", "Extracting"
        NORMALIZING = "NORMALIZING", "Normalizing"
        VALIDATING = "VALIDATING", "Validating"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    # Link to existing DocumentUpload if coming from the PO-recon pipeline
    document_upload = models.ForeignKey(
        "documents.DocumentUpload",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_documents",
        help_text="Source document upload (if originating from PO-recon pipeline)",
    )
    file_name = models.CharField(
        max_length=500,
        help_text="Original file name",
    )
    file_path = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Storage path (blob or local)",
    )
    file_hash = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="SHA-256 hash for deduplication",
    )
    page_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of pages in the document",
    )
    # Jurisdiction resolution
    resolved_jurisdiction = models.ForeignKey(
        "extraction_core.TaxJurisdictionProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_documents",
        help_text="Jurisdiction resolved by the JurisdictionResolverService",
    )
    resolved_schema = models.ForeignKey(
        "extraction_core.ExtractionSchemaDefinition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_documents",
        help_text="Extraction schema selected for this document",
    )
    jurisdiction_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="0.0–1.0 confidence of jurisdiction resolution",
    )
    jurisdiction_signals_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Signals used for jurisdiction resolution (e.g. tax_ids_found, currency_detected)",
    )
    # --- Document-level jurisdiction overrides ---
    declared_country_code = models.CharField(
        max_length=3,
        blank=True,
        default="",
        help_text="Caller-declared country code (document-level override)",
    )
    declared_regime_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Caller-declared tax regime code (document-level override)",
    )
    jurisdiction_source = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="Source of the resolved jurisdiction (e.g. DOCUMENT_OVERRIDE, AUTO_DETECTED)",
    )
    jurisdiction_resolution_mode = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Mode used for resolution (AUTO, FIXED, HYBRID)",
    )
    jurisdiction_warning = models.TextField(
        blank=True,
        default="",
        help_text="Warning message from hybrid mode mismatch or low confidence",
    )
    # Classification
    classified_document_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Document type classification result",
    )
    classification_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="0.0–1.0 confidence of document classification",
    )
    # Extraction
    status = models.CharField(
        max_length=30,
        choices=ExtractionStatus.choices,
        default=ExtractionStatus.PENDING,
        db_index=True,
    )
    ocr_text = models.TextField(
        blank=True,
        default="",
        help_text="Raw OCR text extracted from the document",
    )
    ocr_engine = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="OCR engine used (e.g. azure_di, tesseract)",
    )
    extracted_data_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured extraction output (header + line items + tax)",
    )
    extraction_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="Overall extraction confidence 0.0–1.0",
    )
    extraction_method = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="Method used: deterministic, llm, hybrid",
    )
    # Validation
    validation_errors_json = models.JSONField(
        default=list,
        blank=True,
        help_text="List of validation errors found post-extraction",
    )
    validation_warnings_json = models.JSONField(
        default=list,
        blank=True,
        help_text="List of validation warnings",
    )
    is_valid = models.BooleanField(
        null=True,
        help_text="Whether the extraction passed validation",
    )
    # Timing
    extraction_started_at = models.DateTimeField(null=True, blank=True)
    extraction_completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    # Error
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "extraction_documents_extraction_document"
        ordering = ["-created_at"]
        verbose_name = "Extraction Document"
        verbose_name_plural = "Extraction Documents"

    def __str__(self) -> str:
        return f"{self.file_name} ({self.get_status_display()})"


class ExtractionFieldResult(TimestampMixin):
    """
    Per-field extraction result with evidence and confidence.

    Provides granular traceability: which field was extracted, what value
    was found, where in the document it came from, and how confident
    the extraction engine is.
    """

    class ExtractionMethod(models.TextChoices):
        DETERMINISTIC = "DETERMINISTIC", "Deterministic (regex/rule)"
        LLM = "LLM", "LLM-based"
        HYBRID = "HYBRID", "Hybrid (rule + LLM)"
        MANUAL = "MANUAL", "Manual Override"

    document = models.ForeignKey(
        ExtractionDocument,
        on_delete=models.CASCADE,
        related_name="field_results",
        help_text="Parent extraction document",
    )
    field_definition = models.ForeignKey(
        "extraction_configs.TaxFieldDefinition",
        on_delete=models.PROTECT,
        related_name="extraction_results",
        help_text="Field definition from the registry",
    )
    field_key = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Denormalized field key for fast queries",
    )
    raw_value = models.TextField(
        blank=True,
        default="",
        help_text="Value as extracted (before normalization)",
    )
    normalized_value = models.TextField(
        blank=True,
        default="",
        help_text="Value after normalization",
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="0.0–1.0 field-level extraction confidence",
    )
    extraction_method = models.CharField(
        max_length=20,
        choices=ExtractionMethod.choices,
        default=ExtractionMethod.DETERMINISTIC,
    )
    # Evidence
    source_text_snippet = models.TextField(
        blank=True,
        default="",
        help_text="Surrounding text from OCR output that contains this field",
    )
    page_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Page where this field was found (1-indexed)",
    )
    line_item_index = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Line item index (0-based) if this is a line-item field",
    )
    is_valid = models.BooleanField(
        null=True,
        help_text="Whether this field passed validation",
    )
    validation_message = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Validation error/warning message if applicable",
    )

    class Meta:
        db_table = "extraction_documents_extraction_field_result"
        ordering = ["document", "line_item_index", "field_key"]
        verbose_name = "Extraction Field Result"
        verbose_name_plural = "Extraction Field Results"

    def __str__(self) -> str:
        return f"{self.field_key}={self.normalized_value or self.raw_value}"
