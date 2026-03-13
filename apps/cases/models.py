"""
AP Case models — central business objects for the case-centric AP processing platform.

APCase is the single orchestration anchor. Every invoice upload creates an APCase.
All downstream processing (extraction, matching, validation, review, approval) is
tracked through APCaseStage, APCaseDecision, APCaseArtifact, and related models.
"""

from django.conf import settings
from django.db import models

from apps.core.enums import (
    ArtifactType,
    AssignmentStatus,
    AssignmentType,
    BudgetCheckStatus,
    CasePriority,
    CaseStageType,
    CaseStatus,
    CodingStatus,
    DecisionSource,
    DecisionType,
    InvoiceType,
    PerformedByType,
    ProcessingPath,
    ReconciliationMode,
    SourceChannel,
    StageStatus,
    UserRole,
)
from apps.core.mixins import SoftDeleteMixin
from apps.core.models import BaseModel, TimestampMixin


class APCase(BaseModel, SoftDeleteMixin):
    """
    Central business object for AP invoice processing.

    Every incoming invoice creates exactly one APCase. The case tracks the full
    lifecycle: intake → extraction → path resolution → matching/validation →
    exception analysis → review → (approval) → (GL coding) → close.
    """

    case_number = models.CharField(
        max_length=50, unique=True, db_index=True,
        help_text="Auto-generated case identifier, e.g. AP-000123",
    )

    # --- Linked documents ---
    invoice = models.OneToOneField(
        "documents.Invoice",
        on_delete=models.PROTECT,
        related_name="ap_case",
    )
    vendor = models.ForeignKey(
        "vendors.Vendor",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="ap_cases",
    )
    purchase_order = models.ForeignKey(
        "documents.PurchaseOrder",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="ap_cases",
    )
    reconciliation_result = models.ForeignKey(
        "reconciliation.ReconciliationResult",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="ap_cases",
    )
    review_assignment = models.ForeignKey(
        "reviews.ReviewAssignment",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="ap_cases",
    )

    # --- Classification ---
    source_channel = models.CharField(
        max_length=30,
        choices=SourceChannel.choices,
        default=SourceChannel.WEB_UPLOAD,
    )
    invoice_type = models.CharField(
        max_length=20,
        choices=InvoiceType.choices,
        default=InvoiceType.UNKNOWN,
    )
    processing_path = models.CharField(
        max_length=20,
        choices=ProcessingPath.choices,
        default=ProcessingPath.UNRESOLVED,
        db_index=True,
    )

    # --- Status tracking ---
    status = models.CharField(
        max_length=50,
        choices=CaseStatus.choices,
        default=CaseStatus.NEW,
        db_index=True,
    )
    current_stage = models.CharField(
        max_length=50,
        choices=CaseStageType.choices,
        blank=True,
    )
    priority = models.CharField(
        max_length=20,
        choices=CasePriority.choices,
        default=CasePriority.MEDIUM,
        db_index=True,
    )

    # --- Risk & confidence ---
    risk_score = models.FloatField(null=True, blank=True)
    extraction_confidence = models.FloatField(null=True, blank=True)

    # --- Flags ---
    requires_human_review = models.BooleanField(default=False)
    requires_approval = models.BooleanField(default=False)
    eligible_for_posting = models.BooleanField(default=False)
    duplicate_risk_flag = models.BooleanField(default=False)

    # --- Assignment ---
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_cases",
    )
    assigned_role = models.CharField(
        max_length=30, choices=UserRole.choices, blank=True,
    )

    # --- Mode & validation status ---
    reconciliation_mode = models.CharField(
        max_length=20, choices=ReconciliationMode.choices, blank=True,
    )
    budget_check_status = models.CharField(
        max_length=30, choices=BudgetCheckStatus.choices, blank=True,
    )
    coding_status = models.CharField(
        max_length=30, choices=CodingStatus.choices, blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "AP Case"
        verbose_name_plural = "AP Cases"
        indexes = [
            models.Index(fields=["status", "processing_path"]),
            models.Index(fields=["priority", "-created_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return f"{self.case_number} ({self.get_status_display()})"


class APCaseStage(TimestampMixin):
    """
    Tracks each processing stage executed on a case.

    A case progresses through multiple stages (INTAKE → EXTRACTION → PATH_RESOLUTION → ...).
    Each stage records who/what performed it, timing, payloads, and retry count.
    """

    case = models.ForeignKey(
        APCase, on_delete=models.CASCADE, related_name="stages",
    )
    stage_name = models.CharField(
        max_length=50, choices=CaseStageType.choices,
    )
    stage_status = models.CharField(
        max_length=30,
        choices=StageStatus.choices,
        default=StageStatus.PENDING,
    )
    performed_by_type = models.CharField(
        max_length=30, choices=PerformedByType.choices, blank=True,
    )
    performed_by_agent = models.ForeignKey(
        "agents.AgentRun",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="case_stages",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    input_payload = models.JSONField(default=dict, blank=True)
    output_payload = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    # Traceability
    trace_id = models.CharField(max_length=64, blank=True, default="")
    span_id = models.CharField(max_length=64, blank=True, default="")
    parent_span_id = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    config_snapshot_json = models.JSONField(null=True, blank=True, help_text="Config/tolerance snapshot at execution time")

    class Meta:
        ordering = ["created_at"]
        unique_together = [["case", "stage_name", "retry_count"]]
        verbose_name = "AP Case Stage"

    def __str__(self):
        return f"{self.case.case_number} / {self.get_stage_name_display()} ({self.get_stage_status_display()})"


class APCaseArtifact(TimestampMixin):
    """
    Stores evidence and outputs linked to a case.

    Artifacts are versioned — e.g. extraction result v1 vs v2 after re-extraction.
    The linked_object_type + linked_object_id allow generic FK-like references
    to any model (Invoice, PO, ReconciliationResult, etc.) without hard FKs.
    """

    case = models.ForeignKey(
        APCase, on_delete=models.CASCADE, related_name="artifacts",
    )
    artifact_type = models.CharField(
        max_length=50, choices=ArtifactType.choices,
    )
    linked_object_type = models.CharField(
        max_length=100, blank=True,
        help_text="e.g. 'documents.Invoice', 'reconciliation.ReconciliationResult'",
    )
    linked_object_id = models.PositiveIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "AP Case Artifact"

    def __str__(self):
        return f"{self.case.case_number} / {self.get_artifact_type_display()} v{self.version}"


class APCaseDecision(TimestampMixin):
    """
    Records every significant decision made on a case.

    Decisions come from deterministic engines, policy rules, agents, or humans.
    Each includes a confidence score (for agent decisions) and evidence payload.
    """

    case = models.ForeignKey(
        APCase, on_delete=models.CASCADE, related_name="decisions",
    )
    decision_type = models.CharField(
        max_length=50, choices=DecisionType.choices,
    )
    decision_source = models.CharField(
        max_length=30, choices=DecisionSource.choices,
    )
    decision_value = models.CharField(max_length=200)
    confidence = models.FloatField(null=True, blank=True)
    rationale = models.TextField(blank=True, default="")
    evidence = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "AP Case Decision"

    def __str__(self):
        return f"{self.case.case_number} / {self.get_decision_type_display()}: {self.decision_value}"


class APCaseAssignment(TimestampMixin):
    """
    Tracks work assignments for a case (review, approval, investigation, correction).
    """

    case = models.ForeignKey(
        APCase, on_delete=models.CASCADE, related_name="assignments",
    )
    assignment_type = models.CharField(
        max_length=30, choices=AssignmentType.choices,
    )
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="case_assignments",
    )
    assigned_role = models.CharField(
        max_length=30, choices=UserRole.choices, blank=True,
    )
    queue_name = models.CharField(max_length=100, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=30,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.PENDING,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "AP Case Assignment"

    def __str__(self):
        return f"{self.case.case_number} / {self.get_assignment_type_display()} → {self.assigned_user or self.assigned_role}"


class APCaseSummary(TimestampMixin):
    """
    Stores generated summaries for a case.

    Updated by the Case Summary Agent. Contains role-specific summaries
    (reviewer-focused, finance-focused) and the current recommendation.
    """

    case = models.OneToOneField(
        APCase, on_delete=models.CASCADE, related_name="summary",
    )
    latest_summary = models.TextField(blank=True, default="")
    reviewer_summary = models.TextField(blank=True, default="")
    finance_summary = models.TextField(blank=True, default="")
    recommendation = models.TextField(blank=True, default="")
    generated_by_agent_run = models.ForeignKey(
        "agents.AgentRun",
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = "AP Case Summary"

    def __str__(self):
        return f"Summary for {self.case.case_number}"


class APCaseComment(TimestampMixin):
    """User comments on a case (review notes, questions, etc.)."""

    case = models.ForeignKey(
        APCase, on_delete=models.CASCADE, related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "AP Case Comment"

    def __str__(self):
        return f"Comment on {self.case.case_number} by {self.author}"


class APCaseActivity(TimestampMixin):
    """
    Lightweight activity log for the case.

    Captures UI-level events (copilot chats, field views, etc.) that are not
    full audit events but useful for UX and analytics.
    """

    case = models.ForeignKey(
        APCase, on_delete=models.CASCADE, related_name="activities",
    )
    activity_type = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "AP Case Activity"
        verbose_name_plural = "AP Case Activities"

    def __str__(self):
        return f"{self.case.case_number} / {self.activity_type}"
