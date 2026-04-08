"""Models for the Procurement Intelligence platform.

Hierarchy:
  ProcurementRequest
    ├── ProcurementRequestAttribute
    ├── SupplierQuotation ──> QuotationLineItem
    └── AnalysisRun
          ├── RecommendationResult
          ├── BenchmarkResult ──> BenchmarkResultLine
          └── ComplianceResult
"""
import uuid

from django.conf import settings
from django.db import models

from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    AttributeDataType,
    BenchmarkRiskLevel,
    ComplianceStatus,
    ExtractionSourceType,
    ExtractionStatus,
    PrefillStatus,
    ProcurementRequestStatus,
    ProcurementRequestType,
    SourceDocumentType,
    ValidationEvaluationMode,
    ValidationItemStatus,
    ValidationNextAction,
    ValidationOverallStatus,
    ValidationRuleType,
    ValidationSeverity,
    ValidationSourceType,
    ValidationType,
    VarianceStatus,
)
from apps.core.models import BaseModel, TimestampMixin


# ---------------------------------------------------------------------------
# 1. ProcurementRequest
# ---------------------------------------------------------------------------
class ProcurementRequest(BaseModel):
    """Top-level procurement request entity."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    domain_code = models.CharField(
        max_length=100, db_index=True,
        help_text="Business domain (e.g. HVAC, IT, FACILITIES)",
    )
    schema_code = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Attribute schema identifier for dynamic forms",
    )
    request_type = models.CharField(
        max_length=20,
        choices=ProcurementRequestType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ProcurementRequestStatus.choices,
        default=ProcurementRequestStatus.DRAFT,
        db_index=True,
    )
    priority = models.CharField(
        max_length=10,
        choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"), ("CRITICAL", "Critical")],
        default="MEDIUM",
    )
    geography_country = models.CharField(max_length=100, blank=True, default="")
    geography_city = models.CharField(max_length=100, blank=True, default="")
    currency = models.CharField(max_length=3, default="USD")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="procurement_assigned_requests",
    )
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # PDF-led prefill fields
    uploaded_document = models.ForeignKey(
        "documents.DocumentUpload",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="procurement_requests",
    )
    source_document_type = models.CharField(
        max_length=30,
        choices=SourceDocumentType.choices,
        blank=True, default="",
        help_text="Type of source document (RFQ, BOQ, etc.)",
    )
    prefill_status = models.CharField(
        max_length=20,
        choices=PrefillStatus.choices,
        default=PrefillStatus.NOT_STARTED,
    )
    prefill_confidence = models.FloatField(null=True, blank=True)
    prefill_payload_json = models.JSONField(
        null=True, blank=True,
        help_text="Raw extracted prefill payload before user confirmation",
    )

    class Meta:
        db_table = "procurement_request"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "request_type"]),
            models.Index(fields=["domain_code", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.request_id} — {self.title}"


# ---------------------------------------------------------------------------
# 2. ProcurementRequestAttribute
# ---------------------------------------------------------------------------
class ProcurementRequestAttribute(TimestampMixin):
    """Dynamic key-value attributes for a procurement request."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    request = models.ForeignKey(
        ProcurementRequest,
        on_delete=models.CASCADE,
        related_name="attributes",
    )
    attribute_code = models.CharField(max_length=120, db_index=True)
    attribute_label = models.CharField(max_length=200)
    data_type = models.CharField(
        max_length=20,
        choices=AttributeDataType.choices,
        default=AttributeDataType.TEXT,
    )
    value_text = models.TextField(blank=True, default="")
    value_number = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    value_json = models.JSONField(null=True, blank=True)
    is_required = models.BooleanField(default=False)
    normalized_value = models.TextField(blank=True, default="")
    extraction_source = models.CharField(
        max_length=20,
        choices=ExtractionSourceType.choices,
        default=ExtractionSourceType.MANUAL,
    )
    confidence_score = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "procurement_request_attribute"
        unique_together = [("request", "attribute_code")]
        ordering = ["attribute_code"]

    def __str__(self) -> str:
        return f"{self.attribute_code}: {self.value_text or self.value_number or ''}"


# ---------------------------------------------------------------------------
# 3. SupplierQuotation
# ---------------------------------------------------------------------------
class SupplierQuotation(BaseModel):
    """Supplier quotation uploaded against a procurement request."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    request = models.ForeignKey(
        ProcurementRequest,
        on_delete=models.CASCADE,
        related_name="quotations",
    )
    vendor_name = models.CharField(max_length=300, db_index=True)
    quotation_number = models.CharField(max_length=100, blank=True, default="")
    quotation_date = models.DateField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    uploaded_document = models.ForeignKey(
        "documents.DocumentUpload",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="procurement_quotations",
    )
    extraction_status = models.CharField(
        max_length=20,
        choices=ExtractionStatus.choices,
        default=ExtractionStatus.PENDING,
    )
    extraction_confidence = models.FloatField(null=True, blank=True)

    # PDF-led prefill fields
    prefill_status = models.CharField(
        max_length=20,
        choices=PrefillStatus.choices,
        default=PrefillStatus.NOT_STARTED,
    )
    prefill_payload_json = models.JSONField(
        null=True, blank=True,
        help_text="Raw extracted prefill payload before user confirmation",
    )

    class Meta:
        db_table = "procurement_supplier_quotation"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.vendor_name} — {self.quotation_number or 'N/A'}"


# ---------------------------------------------------------------------------
# 4. QuotationLineItem
# ---------------------------------------------------------------------------
class QuotationLineItem(TimestampMixin):
    """Individual line item from a supplier quotation."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    quotation = models.ForeignKey(
        SupplierQuotation,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    line_number = models.PositiveIntegerField()
    description = models.TextField()
    normalized_description = models.TextField(blank=True, default="")
    category_code = models.CharField(max_length=100, blank=True, default="")
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    unit = models.CharField(max_length=50, blank=True, default="EA")
    unit_rate = models.DecimalField(max_digits=18, decimal_places=4)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    brand = models.CharField(max_length=200, blank=True, default="")
    model = models.CharField(max_length=200, blank=True, default="")
    extraction_confidence = models.FloatField(null=True, blank=True)
    extraction_source = models.CharField(
        max_length=20,
        choices=ExtractionSourceType.choices,
        default=ExtractionSourceType.MANUAL,
    )

    class Meta:
        db_table = "procurement_quotation_line_item"
        ordering = ["line_number"]
        unique_together = [("quotation", "line_number")]

    def __str__(self) -> str:
        return f"Line {self.line_number}: {self.description[:60]}"


# ---------------------------------------------------------------------------
# 5. AnalysisRun
# ---------------------------------------------------------------------------
class AnalysisRun(BaseModel):
    """Single execution of an analysis (recommendation or benchmark)."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    run_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    request = models.ForeignKey(
        ProcurementRequest,
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )
    run_type = models.CharField(
        max_length=20,
        choices=AnalysisRunType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=AnalysisRunStatus.choices,
        default=AnalysisRunStatus.QUEUED,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="procurement_triggered_runs",
    )
    input_snapshot_json = models.JSONField(null=True, blank=True)
    output_summary = models.TextField(blank=True, default="")
    confidence_score = models.FloatField(null=True, blank=True)
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "procurement_analysis_run"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["request", "run_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"Run {self.run_id} ({self.run_type} / {self.status})"

    @property
    def duration_ms(self) -> int | None:
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds() * 1000)
        return None


# ---------------------------------------------------------------------------
# 6. RecommendationResult
# ---------------------------------------------------------------------------
class RecommendationResult(TimestampMixin):
    """Output of a recommendation analysis run."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    run = models.OneToOneField(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="recommendation_result",
    )
    recommended_option = models.CharField(max_length=500)
    reasoning_summary = models.TextField(blank=True, default="")
    reasoning_details_json = models.JSONField(null=True, blank=True)
    confidence_score = models.FloatField(null=True, blank=True)
    constraints_json = models.JSONField(null=True, blank=True)
    compliance_status = models.CharField(
        max_length=20,
        choices=ComplianceStatus.choices,
        default=ComplianceStatus.NOT_CHECKED,
    )
    output_payload_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "procurement_recommendation_result"

    def __str__(self) -> str:
        return f"Recommendation: {self.recommended_option[:80]}"


# ---------------------------------------------------------------------------
# 7. BenchmarkResult
# ---------------------------------------------------------------------------
class BenchmarkResult(TimestampMixin):
    """Header-level benchmark output for a quotation."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="benchmark_results",
    )
    quotation = models.ForeignKey(
        SupplierQuotation,
        on_delete=models.CASCADE,
        related_name="benchmark_results",
    )
    total_quoted_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    total_benchmark_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    variance_pct = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    risk_level = models.CharField(
        max_length=20,
        choices=BenchmarkRiskLevel.choices,
        default=BenchmarkRiskLevel.LOW,
    )
    summary_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "procurement_benchmark_result"
        unique_together = [("run", "quotation")]

    def __str__(self) -> str:
        return f"Benchmark: {self.quotation} — {self.risk_level}"


# ---------------------------------------------------------------------------
# 8. BenchmarkResultLine
# ---------------------------------------------------------------------------
class BenchmarkResultLine(TimestampMixin):
    """Per-line benchmark comparison."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    benchmark_result = models.ForeignKey(
        BenchmarkResult,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    quotation_line = models.ForeignKey(
        QuotationLineItem,
        on_delete=models.CASCADE,
        related_name="benchmark_lines",
    )
    benchmark_min = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    benchmark_avg = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    benchmark_max = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    quoted_value = models.DecimalField(max_digits=18, decimal_places=4)
    variance_pct = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    variance_status = models.CharField(
        max_length=30,
        choices=VarianceStatus.choices,
        default=VarianceStatus.WITHIN_RANGE,
    )
    remarks = models.TextField(blank=True, default="")

    class Meta:
        db_table = "procurement_benchmark_result_line"
        ordering = ["quotation_line__line_number"]

    def __str__(self) -> str:
        return f"Line {self.quotation_line.line_number}: {self.variance_status}"


# ---------------------------------------------------------------------------
# 9. ComplianceResult
# ---------------------------------------------------------------------------
class ComplianceResult(TimestampMixin):
    """Compliance check output attached to an analysis run."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    run = models.OneToOneField(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="compliance_result",
    )
    compliance_status = models.CharField(
        max_length=20,
        choices=ComplianceStatus.choices,
        default=ComplianceStatus.NOT_CHECKED,
    )
    rules_checked_json = models.JSONField(null=True, blank=True)
    violations_json = models.JSONField(null=True, blank=True)
    recommendations_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "procurement_compliance_result"

    def __str__(self) -> str:
        return f"Compliance: {self.compliance_status}"


# ---------------------------------------------------------------------------
# 10. ValidationRuleSet
# ---------------------------------------------------------------------------
class ValidationRuleSet(BaseModel):
    """Reusable set of validation rules for a domain/schema."""

    domain_code = models.CharField(
        max_length=100, blank=True, default="",
        db_index=True,
        help_text="Business domain (blank = generic / all domains)",
    )
    schema_code = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Attribute schema identifier (blank = all schemas)",
    )
    rule_set_code = models.CharField(max_length=120, unique=True, db_index=True)
    rule_set_name = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    validation_type = models.CharField(
        max_length=40,
        choices=ValidationType.choices,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    priority = models.PositiveIntegerField(default=100)
    config_json = models.JSONField(
        null=True, blank=True,
        help_text="Domain-specific checklist/config (e.g. expected docs, categories, commercial terms)",
    )

    class Meta:
        db_table = "procurement_validation_rule_set"
        ordering = ["priority", "rule_set_code"]
        indexes = [
            models.Index(fields=["domain_code", "validation_type", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.rule_set_code} — {self.rule_set_name}"


# ---------------------------------------------------------------------------
# 11. ValidationRule
# ---------------------------------------------------------------------------
class ValidationRule(TimestampMixin):
    """Individual validation rule within a rule set."""

    rule_set = models.ForeignKey(
        ValidationRuleSet,
        on_delete=models.CASCADE,
        related_name="rules",
    )
    rule_code = models.CharField(max_length=120, db_index=True)
    rule_name = models.CharField(max_length=300)
    rule_type = models.CharField(
        max_length=30,
        choices=ValidationRuleType.choices,
    )
    severity = models.CharField(
        max_length=20,
        choices=ValidationSeverity.choices,
        default=ValidationSeverity.ERROR,
    )
    is_active = models.BooleanField(default=True)
    evaluation_mode = models.CharField(
        max_length=20,
        choices=ValidationEvaluationMode.choices,
        default=ValidationEvaluationMode.DETERMINISTIC,
    )
    condition_json = models.JSONField(
        null=True, blank=True,
        help_text="Evaluation conditions (attribute_code, pattern, etc.)",
    )
    expected_value_json = models.JSONField(
        null=True, blank=True,
        help_text="Expected value or pattern for comparison",
    )
    failure_message = models.CharField(max_length=500, blank=True, default="")
    remediation_hint = models.CharField(max_length=500, blank=True, default="")
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "procurement_validation_rule"
        ordering = ["display_order", "rule_code"]
        unique_together = [("rule_set", "rule_code")]

    def __str__(self) -> str:
        return f"{self.rule_code} ({self.rule_type})"


# ---------------------------------------------------------------------------
# 12. ValidationResult
# ---------------------------------------------------------------------------
class ValidationResult(TimestampMixin):
    """Top-level output of a validation run."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    run = models.OneToOneField(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="validation_result",
    )
    validation_type = models.CharField(
        max_length=40,
        choices=ValidationType.choices,
        default=ValidationType.ATTRIBUTE_COMPLETENESS,
        help_text="Primary validation type (or ATTRIBUTE_COMPLETENESS for combined)",
    )
    overall_status = models.CharField(
        max_length=30,
        choices=ValidationOverallStatus.choices,
        default=ValidationOverallStatus.FAIL,
    )
    completeness_score = models.FloatField(
        default=0.0,
        help_text="0-100 percentage",
    )
    summary_text = models.TextField(blank=True, default="")
    readiness_for_recommendation = models.BooleanField(default=False)
    readiness_for_benchmarking = models.BooleanField(default=False)
    recommended_next_action = models.CharField(
        max_length=40,
        choices=ValidationNextAction.choices,
        blank=True,
        default="",
    )
    missing_items_json = models.JSONField(
        null=True, blank=True,
        help_text="List of missing item dicts",
    )
    warnings_json = models.JSONField(
        null=True, blank=True,
        help_text="List of warning dicts",
    )
    ambiguous_items_json = models.JSONField(
        null=True, blank=True,
        help_text="List of ambiguous item dicts",
    )
    output_payload_json = models.JSONField(
        null=True, blank=True,
        help_text="Full structured output for API consumers",
    )

    class Meta:
        db_table = "procurement_validation_result"

    def __str__(self) -> str:
        return f"Validation: {self.overall_status} ({self.completeness_score:.0f}%)"


# ---------------------------------------------------------------------------
# 13. ValidationResultItem
# ---------------------------------------------------------------------------
class ValidationResultItem(TimestampMixin):
    """Individual finding within a validation result."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    validation_result = models.ForeignKey(
        ValidationResult,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_code = models.CharField(max_length=120)
    item_label = models.CharField(max_length=300)
    category = models.CharField(
        max_length=40,
        choices=ValidationType.choices,
        help_text="Which validation dimension this item belongs to",
    )
    status = models.CharField(
        max_length=20,
        choices=ValidationItemStatus.choices,
    )
    severity = models.CharField(
        max_length=20,
        choices=ValidationSeverity.choices,
        default=ValidationSeverity.ERROR,
    )
    source_type = models.CharField(
        max_length=20,
        choices=ValidationSourceType.choices,
        default=ValidationSourceType.RULE,
    )
    source_reference = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Rule code, attribute code, or document reference",
    )
    remarks = models.TextField(blank=True, default="")
    details_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "procurement_validation_result_item"
        ordering = ["category", "item_code"]

    def __str__(self) -> str:
        return f"{self.item_code}: {self.status}"
