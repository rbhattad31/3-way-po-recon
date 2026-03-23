"""Extraction Core models — jurisdiction profiles, schema registry, and runtime settings."""
from django.db import models

from apps.core.enums import (
    CountryPackStatus,
    ExtractionApprovalAction,
    ExtractionIssueSeverity,
    ExtractionRunStatus,
    FieldExtractionMethod,
    JurisdictionMode,
    JurisdictionSource,
    ReviewQueue,
)
from apps.core.models import BaseModel, TimestampMixin


class TaxJurisdictionProfile(BaseModel):
    """
    Represents a tax jurisdiction (e.g. India-GST, UAE-VAT, Saudi-VAT).

    Each profile stores the country/regime configuration used by the
    JurisdictionResolverService to determine which extraction schema
    and validation rules apply to a given document.
    """

    country_code = models.CharField(
        max_length=3,
        db_index=True,
        help_text="ISO 3166-1 alpha-2 or alpha-3 country code (e.g. IN, AE, SA)",
    )
    country_name = models.CharField(
        max_length=100,
        help_text="Human-readable country name",
    )
    tax_regime = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Tax regime identifier (e.g. GST, VAT, ZATCA)",
    )
    regime_full_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Full name of the tax regime (e.g. Goods and Services Tax)",
    )
    default_currency = models.CharField(
        max_length=3,
        help_text="ISO 4217 currency code (e.g. INR, AED, SAR)",
    )
    tax_id_label = models.CharField(
        max_length=50,
        help_text="Label for the primary tax registration number (e.g. GSTIN, TRN, VAT ID)",
    )
    tax_id_regex = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Regex pattern to validate the tax registration number",
    )
    date_formats = models.JSONField(
        default=list,
        blank=True,
        help_text="Accepted date format patterns for this jurisdiction (e.g. ['DD/MM/YYYY', 'DD-MM-YYYY'])",
    )
    locale_code = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="Locale identifier for number/date parsing (e.g. en_IN, ar_SA)",
    )
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1,
        help_text="Month when the fiscal year starts (1=Jan, 4=Apr for India)",
    )
    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional jurisdiction-specific configuration (e.g. reverse_charge_supported, e-invoicing rules)",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_core_tax_jurisdiction_profile"
        unique_together = [("country_code", "tax_regime")]
        ordering = ["country_code", "tax_regime"]
        verbose_name = "Tax Jurisdiction Profile"
        verbose_name_plural = "Tax Jurisdiction Profiles"

    def __str__(self) -> str:
        return f"{self.country_name} — {self.tax_regime}"


class ExtractionSchemaDefinition(BaseModel):
    """
    Versioned extraction schema bound to a jurisdiction + document type.

    Defines which fields should be extracted for a given combination of
    jurisdiction and document type. The schema version allows non-breaking
    evolution of extraction logic.
    """

    jurisdiction = models.ForeignKey(
        TaxJurisdictionProfile,
        on_delete=models.PROTECT,
        related_name="schemas",
        help_text="Jurisdiction this schema applies to",
    )
    document_type = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Document type this schema applies to (use DocumentType enum values)",
    )
    schema_version = models.CharField(
        max_length=20,
        default="1.0",
        help_text="Semantic version of the schema definition",
    )
    name = models.CharField(
        max_length=200,
        help_text="Human-readable schema name (e.g. India GST Invoice Schema v1.0)",
    )
    description = models.TextField(
        blank=True,
        default="",
    )
    header_fields_json = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of header-level field keys expected in extraction output",
    )
    line_item_fields_json = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of line-item-level field keys expected in extraction output",
    )
    tax_fields_json = models.JSONField(
        default=list,
        blank=True,
        help_text="Tax-specific field keys (e.g. cgst_rate, sgst_amount, vat_amount)",
    )
    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional schema configuration (e.g. extraction hints, LLM prompt overrides)",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_core_extraction_schema_definition"
        unique_together = [("jurisdiction", "document_type", "schema_version")]
        ordering = ["jurisdiction", "document_type", "-schema_version"]
        verbose_name = "Extraction Schema Definition"
        verbose_name_plural = "Extraction Schema Definitions"

    def __str__(self) -> str:
        return f"{self.name} (v{self.schema_version})"

    def get_all_field_keys(self) -> list[str]:
        """Return combined list of all field keys across header, line, and tax."""
        return list(
            dict.fromkeys(  # preserve order, deduplicate
                (self.header_fields_json or [])
                + (self.line_item_fields_json or [])
                + (self.tax_fields_json or [])
            )
        )


class ExtractionRuntimeSettings(BaseModel):
    """
    System-level singleton settings governing jurisdiction resolution behaviour.

    Only one active record should exist at a time (enforced by the service
    layer; ``get_active()`` returns the first ``is_active=True`` row ordered
    by ``-updated_at``).
    """

    name = models.CharField(
        max_length=100,
        default="Default",
        help_text="Human label for this settings profile",
    )
    jurisdiction_mode = models.CharField(
        max_length=20,
        choices=JurisdictionMode.choices,
        default=JurisdictionMode.AUTO,
        help_text="How the system resolves jurisdiction (AUTO / FIXED / HYBRID)",
    )
    default_country_code = models.CharField(
        max_length=3,
        blank=True,
        default="",
        help_text="Default ISO country code when mode is FIXED or HYBRID",
    )
    default_regime_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Default tax regime code when mode is FIXED or HYBRID (e.g. GST, VAT)",
    )
    enable_jurisdiction_detection = models.BooleanField(
        default=True,
        help_text="Whether auto-detection is enabled (always True for AUTO, configurable for HYBRID)",
    )
    allow_manual_override = models.BooleanField(
        default=True,
        help_text="Whether document-level overrides are permitted",
    )
    confidence_threshold_for_detection = models.FloatField(
        default=0.70,
        help_text="Minimum detection confidence to accept auto-detected jurisdiction (0.0–1.0)",
    )
    fallback_to_detection_on_schema_miss = models.BooleanField(
        default=True,
        help_text="In FIXED/HYBRID mode, fall back to detection if no schema exists for configured jurisdiction",
    )
    # --- Extraction runtime ---
    ocr_enabled = models.BooleanField(
        default=True,
        help_text="Whether OCR processing is enabled",
    )
    llm_extraction_enabled = models.BooleanField(
        default=True,
        help_text="Whether LLM-based extraction is enabled",
    )
    retry_count = models.PositiveSmallIntegerField(
        default=2,
        help_text="Number of extraction retries on failure",
    )
    timeout_seconds = models.PositiveIntegerField(
        default=120,
        help_text="Maximum seconds per extraction run",
    )
    max_pages = models.PositiveSmallIntegerField(
        default=50,
        help_text="Maximum pages to process per document",
    )
    multi_document_split_enabled = models.BooleanField(
        default=False,
        help_text="Whether multi-document splitting is enabled",
    )
    # --- Review settings ---
    auto_approval_enabled = models.BooleanField(
        default=False,
        help_text="Whether auto-approval is enabled for high-confidence extractions",
    )
    auto_approval_threshold = models.FloatField(
        default=0.95,
        help_text="Minimum confidence for auto-approval (0.0–1.0)",
    )
    review_confidence_threshold = models.FloatField(
        default=0.70,
        help_text="Below this confidence, extraction is routed for review (0.0–1.0)",
    )
    # --- Enrichment settings ---
    vendor_matching_enabled = models.BooleanField(
        default=True,
        help_text="Whether vendor matching enrichment is enabled",
    )
    vendor_fuzzy_threshold = models.FloatField(
        default=0.80,
        help_text="Minimum fuzzy match score for vendor matching (0.0–1.0)",
    )
    po_lookup_enabled = models.BooleanField(
        default=True,
        help_text="Whether PO lookup enrichment is enabled",
    )
    contract_lookup_enabled = models.BooleanField(
        default=False,
        help_text="Whether contract lookup enrichment is enabled",
    )
    # --- Learning / analytics ---
    correction_tracking_enabled = models.BooleanField(
        default=True,
        help_text="Whether correction tracking for learning is enabled",
    )
    analytics_enabled = models.BooleanField(
        default=True,
        help_text="Whether analytics snapshot generation is enabled",
    )

    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional runtime configuration",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_core_runtime_settings"
        ordering = ["-updated_at"]
        verbose_name = "Extraction Runtime Settings"
        verbose_name_plural = "Extraction Runtime Settings"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_jurisdiction_mode_display()})"

    @classmethod
    def get_active(cls) -> "ExtractionRuntimeSettings | None":
        """Return the current active settings record (or None)."""
        return cls.objects.filter(is_active=True).first()


class EntityExtractionProfile(BaseModel):
    """
    Per-entity (vendor / organisation) extraction preferences.

    Allows setting a fixed or hybrid jurisdiction per vendor so that
    documents from a known vendor do not need auto-detection.
    """

    entity = models.OneToOneField(
        "vendors.Vendor",
        on_delete=models.CASCADE,
        related_name="extraction_profile",
        help_text="Vendor / entity this profile belongs to",
    )
    default_country_code = models.CharField(
        max_length=3,
        blank=True,
        default="",
        help_text="Default ISO country code for this entity",
    )
    default_regime_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Default tax regime code for this entity (e.g. GST, VAT)",
    )
    default_document_language = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="ISO 639-1 language code (e.g. en, ar, hi)",
    )
    jurisdiction_mode = models.CharField(
        max_length=20,
        choices=JurisdictionMode.choices,
        default=JurisdictionMode.AUTO,
        help_text="Jurisdiction resolution mode for this entity",
    )
    schema_override_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Schema name or version pin (bypasses normal schema lookup)",
    )
    validation_profile_override_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Validation profile code override",
    )
    normalization_profile_override_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Normalization profile code override",
    )
    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional entity-specific extraction configuration",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_core_entity_extraction_profile"
        ordering = ["entity__name"]
        verbose_name = "Entity Extraction Profile"
        verbose_name_plural = "Entity Extraction Profiles"

    def __str__(self) -> str:
        return f"Extraction profile — {self.entity}"


# ---------------------------------------------------------------------------
# Extraction Run — The primary extraction execution record
# ---------------------------------------------------------------------------


class ExtractionRun(BaseModel):
    """
    Primary extraction execution record.

    Tracks a single extraction pipeline invocation end-to-end, linking
    the resolved jurisdiction, schema, prompt, and confidence metrics.
    """

    document = models.ForeignKey(
        "extraction_documents.ExtractionDocument",
        on_delete=models.CASCADE,
        related_name="extraction_runs",
        help_text="Source document being extracted",
    )
    status = models.CharField(
        max_length=30,
        choices=ExtractionRunStatus.choices,
        default=ExtractionRunStatus.PENDING,
        db_index=True,
    )
    # Jurisdiction
    country_code = models.CharField(
        max_length=3,
        blank=True,
        default="",
        db_index=True,
        help_text="Resolved ISO country code",
    )
    regime_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True,
        help_text="Resolved tax regime code",
    )
    jurisdiction_source = models.CharField(
        max_length=30,
        choices=JurisdictionSource.choices,
        blank=True,
        default="",
        help_text="How jurisdiction was resolved (FIXED, ENTITY)",
    )
    jurisdiction = models.ForeignKey(
        TaxJurisdictionProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_runs",
    )
    # Schema
    schema_code = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Schema name/code used for extraction",
    )
    schema_version = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    schema = models.ForeignKey(
        ExtractionSchemaDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_runs",
    )
    # Prompt
    prompt_code = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Prompt template code used",
    )
    prompt_version = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )
    # Confidence
    overall_confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="0.0–1.0 overall extraction confidence",
    )
    header_confidence = models.FloatField(null=True, blank=True)
    tax_confidence = models.FloatField(null=True, blank=True)
    line_item_confidence = models.FloatField(null=True, blank=True)
    jurisdiction_confidence = models.FloatField(null=True, blank=True)
    # Extraction method
    extraction_method = models.CharField(
        max_length=20,
        choices=FieldExtractionMethod.choices,
        blank=True,
        default="",
    )
    # Output
    extracted_data_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full structured extraction output",
    )
    # Review routing
    review_queue = models.CharField(
        max_length=30,
        choices=ReviewQueue.choices,
        blank=True,
        default="",
    )
    requires_review = models.BooleanField(default=False)
    review_reasons_json = models.JSONField(default=list, blank=True)
    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    # Error
    error_message = models.TextField(blank=True, default="")
    # Metrics
    field_count = models.PositiveIntegerField(default=0)
    mandatory_coverage_pct = models.FloatField(null=True, blank=True)
    field_coverage_pct = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "extraction_core_extraction_run"
        ordering = ["-created_at"]
        verbose_name = "Extraction Run"
        verbose_name_plural = "Extraction Runs"
        indexes = [
            models.Index(fields=["country_code", "regime_code"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"ExtractionRun #{self.pk} — {self.get_status_display()}"


# ---------------------------------------------------------------------------
# Extraction Field Value — Per-field extraction result
# ---------------------------------------------------------------------------


class ExtractionFieldValue(TimestampMixin):
    """
    Per-field extraction result with confidence and correction tracking.
    """

    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name="field_values",
    )
    field_code = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Machine-readable field identifier",
    )
    value = models.TextField(
        blank=True,
        default="",
        help_text="Raw extracted value",
    )
    normalized_value = models.TextField(
        blank=True,
        default="",
        help_text="Value after normalization",
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="0.0–1.0 field-level confidence",
    )
    extraction_method = models.CharField(
        max_length=20,
        choices=FieldExtractionMethod.choices,
        default=FieldExtractionMethod.DETERMINISTIC,
    )
    is_corrected = models.BooleanField(
        default=False,
        help_text="Whether this value was corrected by a human",
    )
    corrected_value = models.TextField(
        blank=True,
        default="",
        help_text="Human-corrected value (if is_corrected=True)",
    )
    category = models.CharField(
        max_length=20,
        blank=True,
        default="HEADER",
        help_text="HEADER / LINE_ITEM / TAX / PARTY",
    )
    line_item_index = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Line item index (0-based) if this is a line-item field",
    )
    is_valid = models.BooleanField(null=True)
    validation_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "extraction_core_extraction_field_value"
        ordering = ["extraction_run", "line_item_index", "field_code"]
        verbose_name = "Extraction Field Value"
        verbose_name_plural = "Extraction Field Values"
        indexes = [
            models.Index(fields=["extraction_run", "field_code"]),
        ]

    def __str__(self) -> str:
        return f"{self.field_code}={self.normalized_value or self.value}"


# ---------------------------------------------------------------------------
# Extraction Line Item — Structured line-item record
# ---------------------------------------------------------------------------


class ExtractionLineItem(TimestampMixin):
    """
    Structured line-item record extracted from a document.
    """

    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    line_index = models.PositiveIntegerField(
        help_text="0-based line item index",
    )
    data_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full line item fields as key-value pairs",
    )
    confidence = models.FloatField(null=True, blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    is_valid = models.BooleanField(null=True)

    class Meta:
        db_table = "extraction_core_extraction_line_item"
        ordering = ["extraction_run", "line_index"]
        unique_together = [("extraction_run", "line_index")]
        verbose_name = "Extraction Line Item"
        verbose_name_plural = "Extraction Line Items"

    def __str__(self) -> str:
        return f"Line {self.line_index} — Run #{self.extraction_run_id}"


# ---------------------------------------------------------------------------
# Extraction Evidence — Provenance per field
# ---------------------------------------------------------------------------


class ExtractionEvidence(TimestampMixin):
    """
    Evidence record for a single extracted field — where it came from
    in the document and how it was extracted.
    """

    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name="evidence_records",
    )
    field_code = models.CharField(
        max_length=100,
        db_index=True,
    )
    page_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="1-indexed page number",
    )
    snippet = models.TextField(
        blank=True,
        default="",
        help_text="OCR text snippet containing the field value",
    )
    bounding_box = models.JSONField(
        null=True,
        blank=True,
        help_text="Bounding box coordinates [x1, y1, x2, y2] if available",
    )
    extraction_method = models.CharField(
        max_length=20,
        choices=FieldExtractionMethod.choices,
        default=FieldExtractionMethod.DETERMINISTIC,
    )
    confidence = models.FloatField(null=True, blank=True)
    line_item_index = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "extraction_core_extraction_evidence"
        ordering = ["extraction_run", "field_code"]
        verbose_name = "Extraction Evidence"
        verbose_name_plural = "Extraction Evidence"

    def __str__(self) -> str:
        return f"Evidence: {self.field_code} (p.{self.page_number})"


# ---------------------------------------------------------------------------
# Extraction Issue — Validation/extraction issues
# ---------------------------------------------------------------------------


class ExtractionIssue(TimestampMixin):
    """
    An issue found during extraction or validation.
    """

    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name="issues",
    )
    severity = models.CharField(
        max_length=10,
        choices=ExtractionIssueSeverity.choices,
        default=ExtractionIssueSeverity.WARNING,
    )
    field_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Affected field code (empty if document-level issue)",
    )
    check_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Type of check that generated this issue",
    )
    message = models.TextField(
        help_text="Human-readable issue description",
    )
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "extraction_core_extraction_issue"
        ordering = ["extraction_run", "severity"]
        verbose_name = "Extraction Issue"
        verbose_name_plural = "Extraction Issues"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.message[:80]}"


# ---------------------------------------------------------------------------
# Extraction Approval Record
# ---------------------------------------------------------------------------


class ExtractionApprovalRecord(BaseModel):
    """
    Approval gate for an extraction run.
    """

    extraction_run = models.OneToOneField(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name="approval",
    )
    action = models.CharField(
        max_length=20,
        choices=ExtractionApprovalAction.choices,
        blank=True,
        default="",
    )
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_approvals_given",
    )
    comments = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "extraction_core_extraction_approval_record"
        verbose_name = "Extraction Approval Record"
        verbose_name_plural = "Extraction Approval Records"

    def __str__(self) -> str:
        return f"Approval: Run #{self.extraction_run_id} — {self.action or 'PENDING'}"


# ---------------------------------------------------------------------------
# Extraction Correction — Field correction audit trail
# ---------------------------------------------------------------------------


class ExtractionCorrection(BaseModel):
    """
    Audit trail for field corrections during approval.
    """

    extraction_run = models.ForeignKey(
        ExtractionRun,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    field_code = models.CharField(max_length=100)
    original_value = models.TextField(blank=True, default="")
    corrected_value = models.TextField(blank=True, default="")
    correction_reason = models.TextField(blank=True, default="")
    corrected_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extraction_corrections_made",
    )

    class Meta:
        db_table = "extraction_core_extraction_correction"
        ordering = ["-created_at"]
        verbose_name = "Extraction Correction"
        verbose_name_plural = "Extraction Corrections"

    def __str__(self) -> str:
        return f"Correction: {self.field_code} on Run #{self.extraction_run_id}"


# ---------------------------------------------------------------------------
# Extraction Analytics Snapshot — Learning feedback data
# ---------------------------------------------------------------------------


class ExtractionAnalyticsSnapshot(BaseModel):
    """
    Analytics snapshot for field weakness stats and vendor patterns.
    """

    snapshot_type = models.CharField(
        max_length=50,
        help_text="Type of snapshot (e.g. field_weakness, vendor_pattern)",
    )
    country_code = models.CharField(max_length=3, blank=True, default="")
    regime_code = models.CharField(max_length=50, blank=True, default="")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    data_json = models.JSONField(
        default=dict,
        help_text="Analytics payload",
    )
    run_count = models.PositiveIntegerField(default=0)
    correction_count = models.PositiveIntegerField(default=0)
    average_confidence = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "extraction_core_extraction_analytics_snapshot"
        ordering = ["-created_at"]
        verbose_name = "Extraction Analytics Snapshot"
        verbose_name_plural = "Extraction Analytics Snapshots"

    def __str__(self) -> str:
        return f"{self.snapshot_type} — {self.country_code} ({self.period_start}–{self.period_end})"


# ---------------------------------------------------------------------------
# Country Pack — Governance for multi-country support
# ---------------------------------------------------------------------------


class CountryPack(BaseModel):
    """
    Governance record for a country's extraction support.

    Tracks activation status and versioning for schemas, validation,
    and normalization profiles.
    """

    jurisdiction = models.OneToOneField(
        TaxJurisdictionProfile,
        on_delete=models.CASCADE,
        related_name="country_pack",
    )
    pack_status = models.CharField(
        max_length=20,
        choices=CountryPackStatus.choices,
        default=CountryPackStatus.DRAFT,
    )
    schema_version = models.CharField(max_length=20, blank=True, default="1.0")
    validation_profile_version = models.CharField(max_length=20, blank=True, default="1.0")
    normalization_profile_version = models.CharField(max_length=20, blank=True, default="1.0")
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    config_json = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "extraction_core_country_pack"
        ordering = ["jurisdiction__country_code"]
        verbose_name = "Country Pack"
        verbose_name_plural = "Country Packs"

    def __str__(self) -> str:
        return f"{self.jurisdiction} — {self.get_pack_status_display()}"


# ---------------------------------------------------------------------------
# Extraction Prompt Template — extraction-scoped versioned prompt
# ---------------------------------------------------------------------------


class ExtractionPromptTemplate(BaseModel):
    """
    Extraction-specific prompt template with versioning, scoping,
    and lifecycle management.

    Only one ACTIVE prompt per logical scope
    (country_code + regime_code + document_type + schema_code + prompt_code).
    """

    prompt_code = models.CharField(
        max_length=120,
        db_index=True,
        help_text="Logical prompt identifier (e.g. extraction_core_v2)",
    )
    prompt_category = models.CharField(
        max_length=50,
        default="extraction",
        db_index=True,
        help_text="Category: extraction, enrichment, validation, classification",
    )
    country_code = models.CharField(
        max_length=3,
        blank=True,
        default="",
        db_index=True,
        help_text="ISO country code scope (blank = global)",
    )
    regime_code = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Tax regime scope (blank = all regimes)",
    )
    document_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Document type scope (blank = all types)",
    )
    schema_code = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Schema name scope (blank = all schemas)",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number (auto-incremented on clone)",
    )
    status = models.CharField(
        max_length=20,
        default="DRAFT",
        db_index=True,
        help_text="DRAFT / ACTIVE / INACTIVE",
    )
    prompt_text = models.TextField(
        help_text="Full prompt text. Supports {variable} placeholders.",
    )
    variables_json = models.JSONField(
        default=list,
        blank=True,
        help_text="List of expected variables in prompt_text",
    )
    effective_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this prompt version becomes effective",
    )
    effective_to = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this prompt version expires",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_core_extraction_prompt_template"
        ordering = ["prompt_code", "-version"]
        verbose_name = "Extraction Prompt Template"
        verbose_name_plural = "Extraction Prompt Templates"
        indexes = [
            models.Index(fields=["prompt_code", "country_code", "document_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.prompt_code} v{self.version} ({self.status})"


# ---------------------------------------------------------------------------
# Review Routing Rule — Configurable routing rule
# ---------------------------------------------------------------------------


class ReviewRoutingRule(BaseModel):
    """
    Configurable review routing rule.

    Matches extraction issues/conditions to target review queues,
    with priority ordering and activation control.
    """

    name = models.CharField(
        max_length=200,
        help_text="Human-readable rule name",
    )
    rule_code = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Machine-readable rule identifier",
    )
    condition_type = models.CharField(
        max_length=50,
        help_text="Trigger condition: low_confidence, tax_issues, vendor_mismatch, schema_missing, "
        "jurisdiction_mismatch, duplicate_suspicion, unsupported_document_type",
    )
    condition_config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Condition parameters (e.g. threshold values)",
    )
    target_queue = models.CharField(
        max_length=30,
        choices=ReviewQueue.choices,
        help_text="Target review queue",
    )
    priority = models.PositiveIntegerField(
        default=100,
        help_text="Lower number = higher priority. Rules evaluated in priority order.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Human-readable explanation of what this rule does",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_core_review_routing_rule"
        ordering = ["priority", "name"]
        verbose_name = "Review Routing Rule"
        verbose_name_plural = "Review Routing Rules"

    def __str__(self) -> str:
        return f"{self.name} → {self.target_queue} (priority {self.priority})"
