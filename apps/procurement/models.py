"""Models for the Procurement Intelligence platform.

Hierarchy:
  ProcurementRequest
    ├── ProcurementRequestAttribute
    ├── SupplierQuotation ──> QuotationLineItem
    └── AnalysisRun
          ├── RecommendationResult
          ├── BenchmarkResult ──> BenchmarkResultLine
          ├── ComplianceResult
          └── ProcurementAgentExecutionRecord  [Phase 1 agentic bridge]
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
    ExternalSourceClass,
    ExtractionSourceType,
    ExtractionStatus,
    HVACSystemType,
    PrefillStatus,
    POStatus,
    ProcurementRequestStatus,
    ProcurementRequestType,
    RecommendationMethod,
    RoomUsageType,
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
    thought_process_log = models.JSONField(
        null=True, blank=True,
        help_text=(
            "Step-by-step agent reasoning log: list of "
            '{"step": N, "stage": str, "decision": str, "reasoning": str} '
            "entries written during AI analysis for full traceability."
        ),
    )

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
    reason_summary_json = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Cached ReasonSummaryAgent output dict (headline, reasoning_summary, "
            "top_drivers, rules_table, conditions_table, etc.). "
            "Populated on first page load; avoids repeated LLM API calls. "
            "Set to null to force regeneration."
        ),
    )

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
# 10. ProcurementAgentExecutionRecord  [Phase 1 agentic bridge]
# ---------------------------------------------------------------------------
class ProcurementAgentExecutionRecord(TimestampMixin):
    """Lightweight per-agent execution record linked to an AnalysisRun.

    Created by ProcurementAgentOrchestrator for every AI agent invocation.
    Provides standard execution traceability consistent with the wider
    agentic platform (AgentRun for reconciliation agents).

    One AnalysisRun can have multiple records when several agents are invoked
    in sequence (e.g., recommendation -> compliance).

    This model is ADDITIVE -- it never replaces AnalysisRun.  It adds
    per-agent granularity: which agent ran, what model, what confidence,
    what reasoning, and the full trace/RBAC provenance.
    """

    run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="agent_execution_records",
        help_text="The parent AnalysisRun that triggered this agent execution.",
    )

    # ------------------------------------------------------------------
    # Agent identity
    # ------------------------------------------------------------------
    agent_type = models.CharField(
        max_length=100, db_index=True,
        help_text="Short label for the agent (e.g. 'recommendation', 'benchmark', 'compliance', 'validation').",
    )

    # ------------------------------------------------------------------
    # Execution status
    # ------------------------------------------------------------------
    status = models.CharField(
        max_length=20,
        choices=AnalysisRunStatus.choices,
        default=AnalysisRunStatus.RUNNING,
        db_index=True,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    confidence_score = models.FloatField(
        null=True, blank=True,
        help_text="Confidence returned by the agent (0.0 - 1.0).",
    )
    reasoning_summary = models.TextField(
        blank=True, default="",
        help_text="Short text summary of the agent's reasoning (ASCII only, max 2000 chars).",
    )
    input_snapshot = models.JSONField(
        null=True, blank=True,
        help_text="Serializable snapshot of ProcurementAgentContext at execution time.",
    )
    output_snapshot = models.JSONField(
        null=True, blank=True,
        help_text="Serializable output dict returned by the agent.",
    )
    error_message = models.TextField(
        blank=True, default="",
        help_text="Error message if status == FAILED.",
    )

    # ------------------------------------------------------------------
    # Trace / observability
    # ------------------------------------------------------------------
    trace_id = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="Platform trace_id at time of execution (for Langfuse correlation).",
    )
    span_id = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Platform span_id at time of execution.",
    )

    # ------------------------------------------------------------------
    # RBAC provenance
    # ------------------------------------------------------------------
    actor_user_id = models.IntegerField(
        null=True, blank=True,
        help_text="PK of the user who triggered the run (null = system-triggered).",
    )
    actor_primary_role = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Primary role of the actor at execution time.",
    )

    class Meta:
        db_table = "procurement_agent_execution_record"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["run", "agent_type"]),
            models.Index(fields=["status", "started_at"]),
            models.Index(fields=["trace_id"]),
        ]

    def __str__(self) -> str:
        return f"AgentExec: {self.agent_type} / {self.status} (run={self.run_id})"

    @property
    def duration_ms(self) -> int | None:
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds() * 1000)
        return None


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
    failure_digest_text = models.TextField(
        blank=True, default="",
        help_text=(
            "Plain-English root-cause digest of every validation failure: "
            "what is missing, why each item is required, and the exact "
            "remediation step needed to fix it. Written after each run for "
            "developer and analyst debugging."
        ),
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


# =============================================================================
# RoomWise Pre-Procurement Recommender Models
# =============================================================================


class Room(BaseModel):
    """Represents a physical space (room/facility) for HVAC recommendations."""

    room_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    room_code = models.CharField(
        max_length=50, unique=True, db_index=True,
        help_text="Human-readable identifier (e.g., SRV-A, OFF-101)",
    )
    building_name = models.CharField(max_length=200, db_index=True)
    floor_number = models.IntegerField(help_text="0=ground, -1=basement")
    location_description = models.TextField(blank=True, default="")
    area_sqm = models.DecimalField(max_digits=8, decimal_places=2, help_text="Room area in square meters")
    ceiling_height_m = models.DecimalField(max_digits=5, decimal_places=2)
    usage_type = models.CharField(
        max_length=30,
        choices=RoomUsageType.choices,
        db_index=True,
    )
    design_temp_c = models.DecimalField(max_digits=4, decimal_places=1, help_text="Target temperature in Celsius")
    temp_tolerance_c = models.DecimalField(max_digits=3, decimal_places=1, help_text="Allowable tolerance (±ΔT)")
    design_cooling_load_kw = models.DecimalField(max_digits=8, decimal_places=2, help_text="Estimated cooling load in kW")
    design_humidity_pct = models.IntegerField(null=True, blank=True, help_text="Target humidity percentage if critical")
    noise_limit_db = models.IntegerField(null=True, blank=True, help_text="Maximum acceptable noise in dB")
    current_hvac_type = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Type of existing HVAC system",
    )
    current_hvac_age_years = models.IntegerField(null=True, blank=True)
    access_constraints = models.TextField(
        blank=True, default="",
        help_text="Physical/spatial constraints (e.g., low ceiling, tight ductwork)",
    )
    contact_name = models.CharField(max_length=100, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "roomwise_rooms"
        ordering = ["building_name", "floor_number", "room_code"]
        indexes = [
            models.Index(fields=["building_name", "floor_number"]),
            models.Index(fields=["usage_type", "area_sqm"]),
        ]

    def __str__(self) -> str:
        return f"{self.room_code} ({self.building_name})"


class Product(BaseModel):
    """HVAC product/equipment from manufacturers."""

    product_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    sku = models.CharField(max_length=100, unique=True, db_index=True)
    manufacturer = models.CharField(max_length=100, db_index=True, help_text="Brand/OEM")
    product_name = models.CharField(max_length=200)
    system_type = models.CharField(
        max_length=30,
        choices=HVACSystemType.choices,
        db_index=True,
    )
    capacity_kw = models.DecimalField(max_digits=8, decimal_places=2, db_index=True)
    sound_level_db_full_load = models.IntegerField(help_text="Noise at 100% capacity")
    sound_level_db_part_load = models.IntegerField(null=True, blank=True, help_text="Noise at 50% capacity")
    power_input_kw = models.DecimalField(max_digits=8, decimal_places=2)
    refrigerant_type = models.CharField(max_length=50, blank=True, default="")
    cop_rating = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    seer_rating = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    length_mm = models.IntegerField(null=True, blank=True)
    width_mm = models.IntegerField(null=True, blank=True)
    height_mm = models.IntegerField(null=True, blank=True)
    weight_kg = models.IntegerField(null=True, blank=True)
    warranty_months = models.IntegerField()
    installation_support_required = models.BooleanField(default=False)
    approved_use_cases = models.JSONField(default=list, help_text="List of RoomUsageType values")
    efficiency_compliance = models.JSONField(default=dict, help_text="Geo-specific compliance standards")
    datasheet_url = models.URLField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "roomwise_products"
        ordering = ["manufacturer", "system_type", "capacity_kw"]
        indexes = [
            models.Index(fields=["system_type", "capacity_kw"]),
            models.Index(fields=["manufacturer"]),
        ]

    def __str__(self) -> str:
        return f"{self.manufacturer} {self.product_name} ({self.capacity_kw}kW)"


class Vendor(BaseModel):
    """HVAC vendors/suppliers."""

    vendor_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    vendor_name = models.CharField(max_length=200, unique=True, db_index=True)
    country = models.CharField(max_length=100, db_index=True)
    city = models.CharField(max_length=100, db_index=True)
    address = models.TextField()
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=20)
    average_lead_time_days = models.IntegerField(help_text="Typical delivery time in days")
    payment_terms = models.CharField(
        max_length=100, blank=True, default="",
        help_text="e.g., Net 30, 50% upfront",
    )
    min_order_qty = models.IntegerField(default=1)
    bulk_discount_available = models.BooleanField(default=False)
    rush_order_capable = models.BooleanField(default=False)
    preferred_vendor = models.BooleanField(default=False, db_index=True)
    reliability_score = models.DecimalField(
        max_digits=3, decimal_places=2,
        help_text="Historical rating 0.0-5.0",
    )
    total_purchases = models.IntegerField(default=0)
    on_time_delivery_pct = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Percentage of orders delivered on time",
    )
    quality_issues_count = models.IntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "roomwise_vendors"
        ordering = ["vendor_name"]
        indexes = [
            models.Index(fields=["country", "city"]),
            models.Index(fields=["reliability_score"]),
        ]

    def __str__(self) -> str:
        return self.vendor_name


class VendorProduct(BaseModel):
    """Linking table: vendor's offer for a specific product with pricing/lead time."""

    vendor_product_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="vendor_products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="vendor_offerings")
    vendor_sku = models.CharField(max_length=100, blank=True, default="")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, db_index=True)
    currency = models.CharField(max_length=3, default="INR")
    stock_available = models.IntegerField(null=True, blank=True, help_text="Units in stock (NULL if unknown)")
    lead_time_days = models.IntegerField()
    bulk_discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    installation_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    warranty_months_extended = models.IntegerField(default=0)
    last_quoted = models.DateField(null=True, blank=True)
    quote_validity_days = models.IntegerField(default=30)
    is_preferred = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "roomwise_vendor_products"
        unique_together = ["vendor", "product"]
        ordering = ["unit_price"]
        indexes = [
            models.Index(fields=["vendor", "unit_price"]),
            models.Index(fields=["product", "lead_time_days"]),
        ]

    def __str__(self) -> str:
        return f"{self.vendor.vendor_name} - {self.product.sku}"


class PurchaseHistory(BaseModel):
    """Historical purchase orders and their outcomes."""

    po_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    po_number = models.CharField(max_length=50, unique=True, db_index=True)
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True)
    vendor_product = models.ForeignKey(VendorProduct, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Unit price on PO date")
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    po_date = models.DateField(db_index=True)
    promised_delivery_date = models.DateField()
    actual_delivery_date = models.DateField(null=True, blank=True)
    po_status = models.CharField(
        max_length=20,
        choices=POStatus.choices,
        db_index=True,
    )
    performance_rating = models.IntegerField(null=True, blank=True, help_text="1-5 stars")
    meets_spec = models.BooleanField(null=True, blank=True)
    issues_reported = models.TextField(blank=True, default="")
    delivered_by = models.CharField(max_length=100, blank=True, default="")
    installer_name = models.CharField(max_length=100, blank=True, default="")
    installation_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="purchase_orders_created",
    )

    class Meta:
        db_table = "roomwise_purchase_history"
        ordering = ["-po_date"]
        indexes = [
            models.Index(fields=["room", "po_date"]),
            models.Index(fields=["po_status", "promised_delivery_date"]),
        ]

    def __str__(self) -> str:
        return self.po_number


class RecommendationLog(BaseModel):
    """Log each recommendation request and result for audit and ML learning."""

    recommendation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True)
    requirement_text = models.TextField(help_text="Raw user input or free-text requirement")
    recommendation_input_json = models.JSONField(help_text="Parsed room attributes")
    recommended_products_json = models.JSONField(
        default=list,
        help_text="Ranked results array with scoring details",
    )
    recommendation_method = models.CharField(
        max_length=20,
        choices=RecommendationMethod.choices,
        default=RecommendationMethod.DETERMINISTIC,
    )
    top_ranked_vendor_product = models.ForeignKey(
        VendorProduct, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="top_recommendations",
    )
    top_ranked_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Score of top recommendation (0-100)",
    )
    num_options_generated = models.IntegerField(default=0)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="recommendations_requested",
    )
    user_feedback = models.TextField(blank=True, default="", help_text="User acceptance/rejection feedback")
    outcome_purchase_order = models.ForeignKey(
        PurchaseHistory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="recommendation_source",
    )
    is_accepted = models.BooleanField(default=False, help_text="Did user act on this recommendation?")
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        db_table = "roomwise_recommendation_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["room", "created_at"]),
            models.Index(fields=["is_accepted"]),
        ]

    def __str__(self) -> str:
        return f"Recommendation {self.recommendation_id} for {self.room.room_code if self.room else 'N/A'}"


# ---------------------------------------------------------------------------
# External Source Registry (HVAC Flow A -- Phase 2)
# ---------------------------------------------------------------------------
class ExternalSourceRegistry(BaseModel):
    """Allow-list of approved external sources for HVAC product discovery.

    Controls which web sources the AI discovery agent is permitted to query.
    A source may be active for discovery, compliance reference, or both.
    """

    source_name = models.CharField(max_length=200, help_text="Display name e.g. 'Daikin MEA Official'")
    domain = models.CharField(max_length=300, help_text="Root domain e.g. daikinmea.com")
    source_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Direct product-page URL for this source e.g. https://www.daikin.com/products/ac/",
    )
    source_type = models.CharField(
        max_length=40,
        choices=ExternalSourceClass.choices,
        default=ExternalSourceClass.OEM_OFFICIAL,
        db_index=True,
    )
    country_scope = models.JSONField(
        default=list,
        blank=True,
        help_text="List of country codes this source covers e.g. ['UAE','KSA']",
    )
    priority = models.PositiveIntegerField(default=10, help_text="Lower number = higher priority")
    trust_score = models.FloatField(
        default=0.8,
        help_text="Trust score 0.0-1.0 used in candidate ranking",
    )
    hvac_system_type = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_index=True,
        help_text="HVAC system type this source covers e.g. VRF, SPLIT_AC, PACKAGED_DX, CHILLER, DUCTING. Blank = all types.",
    )
    equipment = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_index=True,
        help_text="Equipment type within the HVAC system e.g. 'VRF Outdoor Unit', 'Chiller Unit', 'FCU / AHU Units'.",
    )
    allowed_for_discovery = models.BooleanField(
        default=True,
        help_text="Allow AI discovery agent to search this source for product candidates",
    )
    allowed_for_compliance = models.BooleanField(
        default=False,
        help_text="Allow this source to be cited as compliance / regulatory evidence",
    )
    fetch_mode = models.CharField(
        max_length=10,
        choices=[("PAGE", "Web Page"), ("PDF", "PDF Download"), ("API", "API Endpoint")],
        default="PAGE",
    )
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "procurement_external_source_registry"
        ordering = ["priority", "source_name"]
        verbose_name = "External Source Registry"
        verbose_name_plural = "External Source Registry"

    def __str__(self) -> str:
        return f"{self.source_name} ({self.source_type})"


# ---------------------------------------------------------------------------
# HVACRecommendationRule -- DB-driven decision matrix
# ---------------------------------------------------------------------------
class HVACRecommendationRule(BaseModel):
    """Decision matrix rule for HVAC system recommendation.

    Rules are evaluated in `priority` order (lower = first).  The first rule
    whose conditions are ALL satisfied determines the recommended system.
    Blank / null condition fields mean ANY (wildcard).

    Recommended system codes map to HVACSystemType choices:
        VRF          -> VRF System
        SPLIT_AC     -> Split AC
        PACKAGED_DX  -> Packaged DX Unit
        CHILLER      -> Chilled Water System
        FCU          -> Fan Coil Unit
        CASSETTE     -> Cassette Split
    """

    STORE_TYPE_CHOICES = [
        ("", "Any"),
        ("MALL", "Mall"),
        ("STANDALONE", "Standalone"),
        ("HOSPITAL", "Hospital"),
        ("WAREHOUSE", "Warehouse"),
        ("OFFICE", "Office"),
        ("HYPERMARKET", "Hypermarket"),
        ("DATA_CENTER", "Data Center"),
    ]

    BUDGET_CHOICES = [
        ("", "Any"),
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("LOW_MEDIUM", "Low / Medium"),
        ("MEDIUM_HIGH", "Medium / High"),
    ]

    ENERGY_PRIORITY_CHOICES = [
        ("", "Any"),
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("LOW_MEDIUM", "Low / Medium"),
        ("MEDIUM_HIGH", "Medium / High"),
    ]

    rule_code = models.CharField(
        max_length=10, unique=True, db_index=True,
        help_text="Display ID, e.g. R1, R2, R3",
    )
    rule_name = models.CharField(max_length=200, help_text="Short description of the rule")

    # -- condition fields (blank/null = ANY / wildcard) -----------------------
    country_filter = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Pipe-separated country names, e.g. UAE|KSA|Qatar. Blank = any.",
    )
    city_filter = models.CharField(
        max_length=200, blank=True, default="",
        help_text="City name (case-insensitive exact match). Blank = any.",
    )
    store_type_filter = models.CharField(
        max_length=20, blank=True, default="",
        choices=STORE_TYPE_CHOICES,
        help_text="Blank = any store type",
    )
    area_sq_ft_min = models.FloatField(
        null=True, blank=True,
        help_text="Lower bound on area (sq ft inclusive); null = no lower bound",
    )
    area_sq_ft_max = models.FloatField(
        null=True, blank=True,
        help_text="Upper bound on area (sq ft exclusive); null = no upper bound",
    )
    ambient_temp_min_c = models.FloatField(
        null=True, blank=True,
        help_text="Lower bound on ambient temperature (C inclusive); null = no lower bound",
    )
    budget_level_filter = models.CharField(
        max_length=20, blank=True, default="",
        choices=BUDGET_CHOICES,
        help_text="Blank = any.  LOW_MEDIUM = matches LOW or MEDIUM.",
    )
    energy_priority_filter = models.CharField(
        max_length=20, blank=True, default="",
        choices=ENERGY_PRIORITY_CHOICES,
        help_text="Blank = any.  LOW_MEDIUM = matches LOW or MEDIUM.",
    )

    # -- outcome ---------------------------------------------------------------
    recommended_system = models.CharField(
        max_length=30,
        choices=HVACSystemType.choices,
        db_index=True,
        help_text="Recommended HVAC system type when this rule matches",
    )
    alternate_system = models.CharField(
        max_length=30, blank=True, default="",
        choices=[("", "None")] + list(HVACSystemType.choices),
        help_text="Optional fallback / alternate system type",
    )
    rationale = models.TextField(
        blank=True, default="",
        help_text="Brief plain-text explanation for this rule (shown in the UI)",
    )

    # -- control ---------------------------------------------------------------
    priority = models.PositiveIntegerField(
        default=100,
        help_text="Lower number = evaluated first; first match wins",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "procurement_hvac_recommendation_rule"
        ordering = ["priority", "rule_code"]
        verbose_name = "HVAC Recommendation Rule"
        verbose_name_plural = "HVAC Recommendation Rules"
        indexes = [
            models.Index(fields=["is_active", "priority"]),
        ]

    def __str__(self) -> str:
        return f"{self.rule_code} -> {self.recommended_system}"

    # ------------------------------------------------------------------
    def matches(self, attrs: dict) -> bool:
        """Return True if all conditions in this rule are satisfied by attrs.

        Accepted attr keys (matches the procurement request attributes dict):
          country, city, store_type, area_sqft, ambient_temp_max,
          budget_level, energy_efficiency_priority
        """
        country = str(attrs.get("country") or attrs.get("geography_country") or "").strip()
        city = str(attrs.get("city") or attrs.get("geography_city") or "").strip()
        store_type = str(attrs.get("store_type") or "").upper()
        # Support both spellings used across the codebase
        area = float(attrs.get("area_sqft") or attrs.get("area_sq_ft") or 0)
        ambient = float(attrs.get("ambient_temp_max") or attrs.get("ambient_temp_max_c") or 0)
        budget = str(attrs.get("budget_level") or "").upper()
        energy = str(attrs.get("energy_efficiency_priority") or "").upper()

        # Country filter: pipe-separated list, e.g. "UAE|KSA|Qatar"
        if self.country_filter:
            allowed_countries = [
                c.strip().upper() for c in self.country_filter.split("|")
                if c.strip()
            ]
            if country.upper() not in allowed_countries:
                return False
        # City filter: exact case-insensitive match
        if self.city_filter:
            if city.upper() != self.city_filter.strip().upper():
                return False
        if self.store_type_filter and self.store_type_filter.upper() != store_type:
            return False
        if self.area_sq_ft_min is not None and area < self.area_sq_ft_min:
            return False
        if self.area_sq_ft_max is not None and area >= self.area_sq_ft_max:
            return False
        if self.ambient_temp_min_c is not None and ambient < self.ambient_temp_min_c:
            return False
        if self.budget_level_filter:
            # e.g. "LOW_MEDIUM" matches budget in {"LOW", "MEDIUM"}
            allowed = [p.upper() for p in self.budget_level_filter.split("_") if p]
            if budget not in allowed:
                return False
        if self.energy_priority_filter:
            allowed = [p.upper() for p in self.energy_priority_filter.split("_") if p]
            if energy not in allowed:
                return False
        return True


class HVACStoreProfile(BaseModel):
    """Store profile used for HVAC form autosuggest and autofill."""

    # NOTE: is_active was included in migration 0009_hvacstoreprofile but is not
    # part of the current BaseModel. Declared here explicitly to keep the Python
    # model in sync with the DB column.
    is_active = models.BooleanField(default=True, db_index=True)

    store_id = models.CharField(max_length=120, unique=True, db_index=True)
    brand = models.CharField(max_length=200, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    store_type = models.CharField(max_length=50, blank=True, default="")
    store_format = models.CharField(max_length=50, blank=True, default="")
    area_sqft = models.FloatField(null=True, blank=True)
    ceiling_height_ft = models.FloatField(null=True, blank=True)
    operating_hours = models.CharField(max_length=120, blank=True, default="")
    footfall_category = models.CharField(max_length=50, blank=True, default="")
    ambient_temp_max = models.FloatField(null=True, blank=True)
    humidity_level = models.CharField(max_length=50, blank=True, default="")
    dust_exposure = models.CharField(max_length=50, blank=True, default="")
    heat_load_category = models.CharField(max_length=50, blank=True, default="")
    fresh_air_requirement = models.CharField(max_length=50, blank=True, default="")
    landlord_constraints = models.TextField(blank=True, default="")
    existing_hvac_type = models.CharField(max_length=200, blank=True, default="")
    budget_level = models.CharField(max_length=50, blank=True, default="")
    energy_efficiency_priority = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        db_table = "procurement_hvac_store_profile"
        ordering = ["store_id"]

    def __str__(self) -> str:
        return self.store_id


# ---------------------------------------------------------------------------
# Market Intelligence Suggestion Snapshot
# ---------------------------------------------------------------------------
class MarketIntelligenceSuggestion(BaseModel):
    """AI-generated market intelligence + product suggestions for a procurement request.

    One record is written each time the user (or the page auto-load) triggers
    api_external_suggestions.  The page always shows the latest record.
    """

    request = models.ForeignKey(
        "ProcurementRequest",
        on_delete=models.CASCADE,
        related_name="market_suggestions",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="market_suggestions_generated",
    )
    rephrased_query = models.TextField(blank=True, default="")
    ai_summary = models.TextField(blank=True, default="")
    market_context = models.TextField(blank=True, default="")
    system_code = models.CharField(max_length=100, blank=True, default="")
    system_name = models.CharField(max_length=200, blank=True, default="")
    suggestions_json = models.JSONField(
        default=list,
        help_text="Full list of suggestion dicts as returned by the LLM.",
    )
    suggestion_count = models.IntegerField(default=0)
    perplexity_citations_json = models.JSONField(
        default=list,
        blank=True,
        help_text="Raw citations list returned by Perplexity API (top-level field). "
                  "These are the real URLs Perplexity fetched during live search.",
    )

    class Meta:
        db_table = "procurement_market_intelligence_suggestion"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Market Suggestions for {self.request.title} ({self.created_at.date() if self.created_at else 'new'})"


# ---------------------------------------------------------------------------
# HVACServiceScope -- Scope matrix: one row per HVAC system type
# ---------------------------------------------------------------------------

class HVACServiceScope(BaseModel):
    """HVAC installation service scope matrix.

    Each row represents one HVAC system type and describes the full scope
    of work across Equipment, Installation, Piping/Ducting, Electrical,
    Controls/Accessories, and Testing/Commissioning.
    """

    system_type = models.CharField(
        max_length=30,
        unique=True,
        db_index=True,
        help_text="HVAC system type key (e.g. SPLIT_AC, VRF, CHILLER, DUCTING)",
    )
    display_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Override display label shown in the UI (e.g. 'Packaged Unit (Rooftop)'). Falls back to system_type if blank.",
    )
    equipment_scope = models.TextField(
        help_text="Physical equipment included (e.g. indoor & outdoor units)"
    )
    installation_services = models.TextField(
        help_text="Labor and installation work (e.g. mounting, fixing, alignment)"
    )
    piping_ducting = models.TextField(
        help_text="Refrigerant copper piping or GI ducting"
    )
    electrical_works = models.TextField(
        help_text="Power supply, cabling, panels, isolators"
    )
    controls_accessories = models.TextField(
        help_text="Control systems and small components (thermostat, dampers, sensors)"
    )
    testing_commissioning = models.TextField(
        help_text="Final verification stage (cooling test, performance check)"
    )
    sort_order = models.IntegerField(
        default=10,
        help_text="Display order -- lower value appears first",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "procurement_hvac_service_scope"
        ordering = ["sort_order", "system_type"]
        verbose_name = "HVAC Service Scope"
        verbose_name_plural = "HVAC Service Scopes"

    def __str__(self) -> str:
        return f"ServiceScope({self.display_name or self.system_type})"


# ---------------------------------------------------------------------------
# GeneratedRFQ  --  persists every RFQ file generation (both xlsx + pdf)
# ---------------------------------------------------------------------------
class GeneratedRFQ(BaseModel):
    """Tracks a generated RFQ document pair (Excel + PDF) for a procurement request.

    Files are uploaded to Azure Blob Storage under:
      rfq/<safe_title>/RFQ-<pk>-<YYYYMMDD>_<safe_title>.xlsx
      rfq/<safe_title>/RFQ-<pk>-<YYYYMMDD>_<safe_title>.pdf

    When blob storage is not configured the fields are left empty and
    files fall back to a streamed download response.
    """

    request = models.ForeignKey(
        "ProcurementRequest",
        on_delete=models.CASCADE,
        related_name="generated_rfqs",
    )
    rfq_ref = models.CharField(
        max_length=80,
        db_index=True,
        help_text="Human-readable reference e.g. RFQ-0001-20250101",
    )
    system_code = models.CharField(
        max_length=30,
        default="",
        help_text="HVAC system type code used for this RFQ (e.g. VRF, SPLIT_AC)",
    )
    system_label = models.CharField(
        max_length=150,
        default="",
        help_text="Human-readable system label at time of generation",
    )
    qty_json = models.JSONField(
        default=dict,
        help_text="Per-row quantity overrides used when building the scope table",
    )
    xlsx_blob_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Azure Blob path for the Excel file (empty if blob not configured)",
    )
    pdf_blob_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Azure Blob path for the PDF file (empty if blob not configured)",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "procurement_generated_rfq"
        ordering = ["-created_at"]
        verbose_name = "Generated RFQ"
        verbose_name_plural = "Generated RFQs"

    def __str__(self) -> str:
        return f"GeneratedRFQ({self.rfq_ref})"
