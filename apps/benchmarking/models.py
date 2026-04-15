"""
Benchmarking app models -- Should-Cost benchmarking for HVAC quotations.
"""
from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.core.models import BaseModel


def benchmark_quotation_upload_to(instance, filename):
    """Store quotation files under benchmarking/<request-title>/quotations/."""
    request_obj = getattr(instance, "request", None)
    title = getattr(request_obj, "title", "request") if request_obj else "request"
    title_slug = slugify(title) or "request"
    return f"benchmarking/{title_slug}/quotations/{filename}"


# ---------------------------------------------------------------------------
# Enumerations (as plain str constants -- no TextChoices to avoid Django deps)
# ---------------------------------------------------------------------------

class Geography:
    UAE = "UAE"
    KSA = "KSA"
    QATAR = "QATAR"
    CHOICES = [
        ("UAE", "UAE (United Arab Emirates)"),
        ("KSA", "KSA (Kingdom of Saudi Arabia)"),
        ("QATAR", "Qatar"),
    ]


class ScopeType:
    SITC = "SITC"
    ITC = "ITC"
    EQUIPMENT_ONLY = "EQUIPMENT_ONLY"
    CHOICES = [
        ("SITC", "SITC (Supply, Install, Test & Commission)"),
        ("ITC", "ITC (Install, Test & Commission only)"),
        ("EQUIPMENT_ONLY", "Equipment Only"),
    ]


class RequestStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CHOICES = [
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]


class LineCategory:
    EQUIPMENT = "EQUIPMENT"
    CONTROLS = "CONTROLS"
    DUCTING = "DUCTING"
    INSULATION = "INSULATION"
    ACCESSORIES = "ACCESSORIES"
    INSTALLATION = "INSTALLATION"
    TC = "TC"
    UNCATEGORIZED = "UNCATEGORIZED"
    CHOICES = [
        ("EQUIPMENT", "Equipment"),
        ("CONTROLS", "Controls"),
        ("DUCTING", "Ducting"),
        ("INSULATION", "Insulation"),
        ("ACCESSORIES", "Accessories"),
        ("INSTALLATION", "Installation"),
        ("TC", "Testing & Commissioning"),
        ("UNCATEGORIZED", "Uncategorized"),
    ]


class VarianceStatus:
    WITHIN_RANGE = "WITHIN_RANGE"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CHOICES = [
        ("WITHIN_RANGE", "Within Range (<5%)"),
        ("MODERATE", "Moderate (5-15%)"),
        ("HIGH", "High (>15%)"),
        ("NEEDS_REVIEW", "Needs Review (no benchmark)"),
    ]


class PricingType:
    MARKET = "MARKET"
    BENCHMARK = "BENCHMARK"
    CHOICES = [
        ("MARKET", "Market (live external research)"),
        ("BENCHMARK", "Benchmark (internal configured corridor)"),
    ]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BenchmarkRequest(BaseModel):
    """Top-level should-cost benchmarking request."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )

    title = models.CharField(max_length=255, help_text="Short label for this request")
    project_name = models.CharField(max_length=255, blank=True, default="")
    geography = models.CharField(
        max_length=20,
        choices=Geography.CHOICES,
        default="UAE",
        db_index=True,
    )
    scope_type = models.CharField(
        max_length=30,
        choices=ScopeType.CHOICES,
        default="SITC",
        db_index=True,
    )
    store_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional store/project type (MALL, HYPERMARKET, WAREHOUSE, etc.)",
    )
    status = models.CharField(
        max_length=20,
        choices=RequestStatus.CHOICES,
        default="PENDING",
        db_index=True,
    )
    notes = models.TextField(blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    rfq_document = models.FileField(
        upload_to="benchmarking/rfq_uploads/",
        blank=True,
        null=True,
        help_text="Externally uploaded RFQ document (PDF) provided by the user.",
    )
    rfq_source = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Source of the RFQ: 'system', 'upload', or 'manual'.",
    )
    rfq_ref = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="RFQ reference string (system ref or filename from uploaded RFQ).",
    )
    rfq_blob_path = models.CharField(max_length=512, blank=True, default="", help_text="Azure Blob path for uploaded external RFQ document.")
    rfq_blob_url = models.URLField(max_length=1024, blank=True, default="", help_text="Azure Blob URL for uploaded external RFQ document.")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="benchmark_requests",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Benchmark Request"
        verbose_name_plural = "Benchmark Requests"

    def __str__(self):
        return f"{self.title} ({self.geography} / {self.scope_type})"

    def get_status_badge_class(self):
        mapping = {
            "PENDING": "secondary",
            "PROCESSING": "warning",
            "COMPLETED": "success",
            "FAILED": "danger",
        }
        return mapping.get(self.status, "secondary")


class BenchmarkQuotation(BaseModel):
    """Uploaded supplier quotation PDF attached to a BenchmarkRequest."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )

    request = models.ForeignKey(
        BenchmarkRequest,
        on_delete=models.CASCADE,
        related_name="quotations",
    )
    supplier_name = models.CharField(max_length=255, blank=True, default="")
    quotation_ref = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Supplier quotation reference number",
    )
    document = models.FileField(upload_to=benchmark_quotation_upload_to, blank=True, null=True)
    blob_url = models.URLField(
        max_length=512,
        blank=True,
        default="",
        help_text="Azure Blob Storage URL after upload (set by BlobStorageService)",
    )
    blob_name = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Blob name (path) within the benchmarking container",
    )
    extracted_text = models.TextField(blank=True, default="")
    extraction_status = models.CharField(
        max_length=20,
        choices=[
            ("PENDING", "Pending"),
            ("DONE", "Done"),
            ("FAILED", "Failed"),
        ],
        default="PENDING",
    )
    extraction_error = models.TextField(blank=True, default="")
    di_extraction_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw Azure Document Intelligence API response (tables, key-value pairs)",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Benchmark Quotation"
        verbose_name_plural = "Benchmark Quotations"

    def __str__(self):
        return f"Quotation for {self.request.title} -- {self.supplier_name or 'Unknown'}"


class BenchmarkLineItem(BaseModel):
    """
    A single line item extracted from a supplier quotation.
    Carries both the raw extracted values and the benchmark corridor result.
    """

    quotation = models.ForeignKey(
        BenchmarkQuotation,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    # Raw extracted fields
    description = models.TextField()
    uom = models.CharField(max_length=50, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    quoted_unit_rate = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    line_number = models.PositiveIntegerField(default=0)
    extraction_confidence = models.FloatField(default=0.0)

    # Classification
    CLASSIFICATION_SOURCE_KEYWORD = "KEYWORD"
    CLASSIFICATION_SOURCE_AI = "AI"
    CLASSIFICATION_SOURCE_MANUAL = "MANUAL"
    CLASSIFICATION_SOURCE_CHOICES = [
        ("KEYWORD", "Keyword Rule"),
        ("AI", "AI (OpenAI)"),
        ("MANUAL", "Manual Override"),
    ]
    classification_source = models.CharField(
        max_length=20,
        choices=CLASSIFICATION_SOURCE_CHOICES,
        default="KEYWORD",
        help_text="How this line item was classified",
    )
    category = models.CharField(
        max_length=30,
        choices=LineCategory.CHOICES,
        default="UNCATEGORIZED",
        db_index=True,
    )
    classification_confidence = models.FloatField(default=0.0)

    # Benchmark corridor (populated after benchmark run)
    benchmark_min = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    benchmark_mid = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    benchmark_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    corridor_rule_code = models.CharField(max_length=50, blank=True, default="")

    # Variance analysis
    variance_pct = models.FloatField(
        null=True,
        blank=True,
        help_text="(quoted_unit_rate - benchmark_mid) / benchmark_mid * 100",
    )
    variance_status = models.CharField(
        max_length=20,
        choices=VarianceStatus.CHOICES,
        default="NEEDS_REVIEW",
        db_index=True,
    )
    variance_note = models.TextField(blank=True, default="")

    # Live-pricing enrichment
    BENCHMARK_SOURCE_CORRIDOR = "CORRIDOR_DB"
    BENCHMARK_SOURCE_PERPLEXITY = "PERPLEXITY_LIVE"
    BENCHMARK_SOURCE_CHOICES = [
        ("CORRIDOR_DB",     "Corridor Database"),
        ("PERPLEXITY_LIVE", "Perplexity Live Pricing"),
        ("MANUAL",          "Manual Override"),
        ("NONE",            "No Benchmark"),
    ]
    benchmark_source = models.CharField(
        max_length=20,
        choices=BENCHMARK_SOURCE_CHOICES,
        default="NONE",
        db_index=True,
        help_text="Where the benchmark min/mid/max rates came from",
    )
    live_price_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw Perplexity pricing response for this line item",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["line_number"]
        verbose_name = "Benchmark Line Item"
        verbose_name_plural = "Benchmark Line Items"

    def __str__(self):
        return f"Line {self.line_number}: {self.description[:60]}"

    def get_variance_badge_class(self):
        mapping = {
            "WITHIN_RANGE": "success",
            "MODERATE": "warning",
            "HIGH": "danger",
            "NEEDS_REVIEW": "secondary",
        }
        return mapping.get(self.variance_status, "secondary")

    def get_variance_display(self):
        if self.variance_pct is None:
            return "N/A"
        return f"{self.variance_pct:+.1f}%"


class BenchmarkCorridorRule(BaseModel):
    """
    Reference benchmark price corridor per category + scope + geography.
    Managed via the Configurations page and seeded via management command.
    """

    rule_code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=LineCategory.CHOICES, db_index=True)
    scope_type = models.CharField(
        max_length=30,
        choices=ScopeType.CHOICES + [("ALL", "All Scopes")],
        default="ALL",
        db_index=True,
    )
    geography = models.CharField(
        max_length=20,
        choices=Geography.CHOICES + [("ALL", "All Geographies")],
        default="ALL",
        db_index=True,
    )
    uom = models.CharField(max_length=50, blank=True, default="", help_text="Unit of measure (AED/ton, AED/m2, etc.)")
    min_rate = models.DecimalField(max_digits=14, decimal_places=2)
    mid_rate = models.DecimalField(max_digits=14, decimal_places=2)
    max_rate = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=10, default="AED")
    keywords = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated keywords used to match line descriptions to this corridor",
    )
    notes = models.TextField(blank=True, default="")
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["category", "geography", "priority"]
        verbose_name = "Benchmark Corridor Rule"
        verbose_name_plural = "Benchmark Corridor Rules"

    def __str__(self):
        return f"{self.rule_code} | {self.category} | {self.geography} | {self.uom}"

    def keyword_list(self):
        """Return list of lowercase keywords for matching."""
        if not self.keywords:
            return []
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]


# ---------------------------------------------------------------------------
# CategoryMaster
# ---------------------------------------------------------------------------

class CategoryMaster(BaseModel):
    """
    Platform-managed category definitions for HVAC line-item classification.
    Administrators can add descriptions, additional keywords, and control which
    categories are active.  The AI agent reads this table at classification time
    so prompt tuning does not require code deployments.
    """

    code = models.CharField(
        max_length=30,
        unique=True,
        help_text="e.g. EQUIPMENT, CONTROLS, DUCTING -- must match LineCategory constant",
    )
    name = models.CharField(max_length=100, help_text="Human-readable category name")
    description = models.TextField(
        blank=True,
        default="",
        help_text="Procurement definition used as AI context during classification",
    )
    keywords_csv = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated classification keywords (augments built-in rules)",
    )
    pricing_type = models.CharField(
        max_length=20,
        choices=PricingType.CHOICES,
        default=PricingType.BENCHMARK,
        db_index=True,
        help_text="Pricing source type: MARKET uses live external research, BENCHMARK uses corridor table",
    )
    sort_order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "Category Master"
        verbose_name_plural = "Category Master"

    def __str__(self):
        return f"{self.code} -- {self.name}"

    def keyword_list(self) -> list:
        """Return list of lowercase, stripped keywords."""
        if not self.keywords_csv:
            return []
        return [k.strip().lower() for k in self.keywords_csv.split(",") if k.strip()]


# ---------------------------------------------------------------------------
# VarianceThresholdConfig
# ---------------------------------------------------------------------------

class VarianceThresholdConfig(BaseModel):
    """
    Configurable variance thresholds per category (or global default).
    Loaded by the benchmark engine so thresholds can be tuned without code changes.

    Priority:
      1. category-specific, geography-specific
      2. category-specific, ANY geography (geography='ALL')
      3. global (category='ALL', geography='ALL')
    """

    category = models.CharField(
        max_length=30,
        choices=LineCategory.CHOICES + [("ALL", "All Categories")],
        default="ALL",
        db_index=True,
        help_text="Category this rule applies to; ALL = global default",
    )
    geography = models.CharField(
        max_length=20,
        choices=Geography.CHOICES + [("ALL", "All Geographies")],
        default="ALL",
        db_index=True,
    )
    within_range_max_pct = models.FloatField(
        default=5.0,
        help_text="Max absolute variance% to be classified as WITHIN_RANGE",
    )
    moderate_max_pct = models.FloatField(
        default=15.0,
        help_text="Max absolute variance% to be classified as MODERATE (else HIGH)",
    )
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "geography"]
        verbose_name = "Variance Threshold Config"
        verbose_name_plural = "Variance Threshold Configs"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "geography"],
                name="uniq_variance_threshold_cat_geo",
            )
        ]

    def __str__(self):
        return (
            f"Threshold | {self.category} / {self.geography} | "
            f"within={self.within_range_max_pct}% moderate={self.moderate_max_pct}%"
        )


class BenchmarkResult(BaseModel):
    """
    Aggregated should-cost result for a BenchmarkRequest.
    One per request; created/updated after all line items are processed.
    """

    request = models.OneToOneField(
        BenchmarkRequest,
        on_delete=models.CASCADE,
        related_name="result",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    total_quoted = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    total_benchmark_mid = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    overall_deviation_pct = models.FloatField(null=True, blank=True)
    overall_status = models.CharField(
        max_length=20,
        choices=VarianceStatus.CHOICES,
        default="NEEDS_REVIEW",
    )

    # Per-category summary: {"EQUIPMENT": {"quoted": ..., "benchmark": ..., "variance_pct": ...}, ...}
    category_summary_json = models.JSONField(default=dict, blank=True)

    # Auto-generated negotiation talking points
    negotiation_notes_json = models.JSONField(default=list, blank=True)

    lines_within_range = models.PositiveIntegerField(default=0)
    lines_moderate = models.PositiveIntegerField(default=0)
    lines_high = models.PositiveIntegerField(default=0)
    lines_needs_review = models.PositiveIntegerField(default=0)

    # Live-pricing enrichment tracking
    live_enriched_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When Perplexity live pricing was last fetched for this result",
    )
    live_enrichment_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full Perplexity response metadata (citations, confidence, source notes)",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Benchmark Result"
        verbose_name_plural = "Benchmark Results"

    def __str__(self):
        return f"Result for {self.request.title}"

    def get_overall_badge_class(self):
        mapping = {
            "WITHIN_RANGE": "success",
            "MODERATE": "warning",
            "HIGH": "danger",
            "NEEDS_REVIEW": "secondary",
        }
        return mapping.get(self.overall_status, "secondary")
