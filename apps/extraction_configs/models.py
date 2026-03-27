"""Extraction Configs models — field registry and normalization profiles."""
from django.db import models

from apps.core.models import BaseModel
from apps.extraction_core.models import ExtractionSchemaDefinition


class TaxFieldDefinition(BaseModel):
    """
    Registry of all extractable fields across jurisdictions.

    Each field definition specifies its data type, validation rules,
    and which schema(s) it belongs to. Fields are reusable across
    schemas — a single field like 'invoice_number' can appear in
    India-GST, UAE-VAT, and Saudi-VAT schemas.
    """

    class FieldDataType(models.TextChoices):
        STRING = "STRING", "String"
        INTEGER = "INTEGER", "Integer"
        DECIMAL = "DECIMAL", "Decimal"
        DATE = "DATE", "Date"
        BOOLEAN = "BOOLEAN", "Boolean"
        CURRENCY = "CURRENCY", "Currency Amount"
        TAX_ID = "TAX_ID", "Tax Identification Number"
        PERCENTAGE = "PERCENTAGE", "Percentage"
        ENUM = "ENUM", "Enumeration"
        ADDRESS = "ADDRESS", "Address"
        JSON = "JSON", "JSON Object"

    class FieldCategory(models.TextChoices):
        HEADER = "HEADER", "Header"
        LINE_ITEM = "LINE_ITEM", "Line Item"
        TAX = "TAX", "Tax"
        PARTY = "PARTY", "Party (Buyer/Seller)"
        PAYMENT = "PAYMENT", "Payment"
        METADATA = "METADATA", "Metadata"

    field_key = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Machine-readable field identifier (e.g. invoice_number, cgst_rate, supplier_gstin)",
    )
    display_name = models.CharField(
        max_length=200,
        help_text="Human-readable field label",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Description of what this field represents",
    )
    data_type = models.CharField(
        max_length=20,
        choices=FieldDataType.choices,
        default=FieldDataType.STRING,
        help_text="Expected data type for parsing and validation",
    )
    category = models.CharField(
        max_length=20,
        choices=FieldCategory.choices,
        default=FieldCategory.HEADER,
        help_text="Field category for grouping",
    )
    is_mandatory = models.BooleanField(
        default=False,
        help_text="Whether this field is required for a valid extraction",
    )
    is_tax_field = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this field is tax-specific",
    )
    validation_regex = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Optional regex for field-level validation",
    )
    validation_rules_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional validation rules (e.g. min_value, max_value, allowed_values)",
    )
    normalization_rules_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Normalization rules (e.g. strip_chars, uppercase, date_format_target)",
    )
    aliases = models.JSONField(
        default=list,
        blank=True,
        help_text="Alternative names this field may appear as in OCR text or LLM output",
    )
    schemas = models.ManyToManyField(
        ExtractionSchemaDefinition,
        related_name="field_definitions",
        blank=True,
        help_text="Schemas this field belongs to",
    )
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Display/processing order within its category",
    )
    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional field configuration (e.g. extraction_hint, llm_prompt_note)",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "extraction_configs_tax_field_definition"
        ordering = ["category", "sort_order", "field_key"]
        verbose_name = "Tax Field Definition"
        verbose_name_plural = "Tax Field Definitions"

    def __str__(self) -> str:
        return f"{self.field_key} ({self.get_data_type_display()})"


class NormalizationProfile(BaseModel):
    """
    Country/jurisdiction-specific normalization configuration.

    Controls how dates, currencies, addresses, and numbers are
    normalized for a given jurisdiction after extraction.
    """

    jurisdiction = models.OneToOneField(
        "extraction_core.TaxJurisdictionProfile",
        on_delete=models.CASCADE,
        related_name="normalization_profile",
        help_text="Jurisdiction this normalization profile applies to",
    )
    date_input_formats = models.JSONField(
        default=list,
        blank=True,
        help_text="Accepted input date formats (e.g. ['DD/MM/YYYY', 'DD-MMM-YYYY'])",
    )
    date_output_format = models.CharField(
        max_length=20,
        default="YYYY-MM-DD",
        help_text="Target output date format (ISO 8601 recommended)",
    )
    decimal_separator = models.CharField(
        max_length=1,
        default=".",
        help_text="Decimal separator used in this locale",
    )
    thousands_separator = models.CharField(
        max_length=1,
        blank=True,
        default=",",
        help_text="Thousands separator used in this locale",
    )
    currency_symbol = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="Currency symbol to strip during normalization (e.g. ₹, AED, SAR)",
    )
    address_format_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Address normalization hints (field order, required components)",
    )
    custom_rules_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional custom normalization rules",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "extraction_configs_normalization_profile"
        verbose_name = "Normalization Profile"
        verbose_name_plural = "Normalization Profiles"

    def __str__(self) -> str:
        return f"Normalization — {self.jurisdiction}"
