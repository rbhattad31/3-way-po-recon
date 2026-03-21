"""Extraction Core models — jurisdiction profiles, schema registry, and runtime settings."""
from django.db import models

from apps.core.enums import JurisdictionMode, JurisdictionSource
from apps.core.models import BaseModel


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
