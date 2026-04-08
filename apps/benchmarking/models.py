"""
Benchmarking app models -- Should-Cost benchmarking for HVAC quotations.
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class BenchmarkRequest(BaseModel):
    """Top-level should-cost benchmarking request."""

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
    document = models.FileField(upload_to="benchmarking/quotations/%Y/%m/")
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
    # Raw extracted fields
    description = models.TextField()
    uom = models.CharField(max_length=50, blank=True, default="")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    quoted_unit_rate = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    line_number = models.PositiveIntegerField(default=0)
    extraction_confidence = models.FloatField(default=0.0)

    # Classification
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
