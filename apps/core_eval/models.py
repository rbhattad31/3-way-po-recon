"""Generic evaluation framework models.

These models are domain-agnostic -- they store eval runs, metrics, field-level
outcomes, learning signals, and learning actions for any pipeline or module.
Business-specific wiring is done externally by the consuming app.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimestampMixin


# ---------------------------------------------------------------------------
# EvalRun -- one evaluation pass against an entity
# ---------------------------------------------------------------------------
class EvalRun(TimestampMixin):
    """Tracks a single evaluation execution against any entity."""

    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        PENDING = "PENDING", "Pending"  # legacy compat
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    # -- tenant --
    tenant_id = models.CharField(
        max_length=100, db_index=True, blank=True, default="",
        help_text="Lightweight tenant identifier for multi-tenant isolation.",
    )

    # -- scoping / routing --
    app_module = models.CharField(
        max_length=120, db_index=True,
        help_text="Originating module (e.g. extraction, reconciliation, posting).",
    )
    entity_type = models.CharField(
        max_length=120,
        help_text="Type of entity being evaluated (e.g. Invoice, ReconciliationResult).",
    )
    entity_id = models.CharField(
        max_length=255,
        help_text="PK or composite key of the evaluated entity.",
    )
    run_key = models.CharField(
        max_length=255, db_index=True, blank=True, default="",
        help_text="Unique key to distinguish retries / versions for the same entity.",
    )

    # -- prompt provenance --
    prompt_hash = models.CharField(
        max_length=64, blank=True, default="",
        help_text="SHA-256 (or similar) hash of the prompt template used.",
    )
    prompt_slug = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Slug of the PromptTemplate used, if applicable.",
    )

    # -- execution context --
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True,
    )
    trace_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Distributed trace ID for correlation.",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="core_eval_evalrun_triggered",
    )

    # -- flexible payloads --
    config_json = models.JSONField(default=dict, blank=True, help_text="Run-level configuration.")
    input_snapshot_json = models.JSONField(default=dict, blank=True, help_text="Snapshot of inputs evaluated.")
    result_json = models.JSONField(default=dict, blank=True, help_text="Aggregated results / summary.")
    error_json = models.JSONField(default=dict, blank=True, help_text="Error details if run failed.")

    # -- timing --
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "core_eval_eval_run"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["app_module"], name="idx_evalrun_app_module"),
            models.Index(fields=["entity_type", "entity_id"], name="idx_evalrun_entity"),
            models.Index(fields=["prompt_hash"], name="idx_evalrun_prompt_hash"),
            models.Index(fields=["created_at"], name="idx_evalrun_created_at"),
            models.Index(
                fields=["app_module", "entity_type", "entity_id", "run_key"],
                name="idx_evalrun_entity_runkey",
            ),
            models.Index(fields=["tenant_id"], name="idx_evalrun_tenant"),
        ]
        verbose_name = "Eval Run"
        verbose_name_plural = "Eval Runs"

    def __str__(self) -> str:
        suffix = f" [{self.run_key}]" if self.run_key else ""
        return f"EvalRun#{self.pk} {self.app_module}/{self.entity_type}:{self.entity_id}{suffix}"


# ---------------------------------------------------------------------------
# EvalMetric -- one named measurement within an EvalRun
# ---------------------------------------------------------------------------
class EvalMetric(TimestampMixin):
    """A single named metric produced during an EvalRun.

    Supports three value types -- at most one should be populated per record:
    ``metric_value`` (numeric), ``string_value`` (text), ``json_value`` (structured).
    """

    # -- tenant --
    tenant_id = models.CharField(
        max_length=100, db_index=True, blank=True, default="",
        help_text="Lightweight tenant identifier for multi-tenant isolation.",
    )

    eval_run = models.ForeignKey(
        EvalRun, on_delete=models.CASCADE, related_name="metrics",
        null=True, blank=True,
    )

    metric_name = models.CharField(max_length=200, db_index=True)
    metric_value = models.FloatField(null=True, blank=True, help_text="Numeric metric value.")
    string_value = models.TextField(blank=True, default="", help_text="Text metric value.")
    json_value = models.JSONField(null=True, blank=True, default=None, help_text="Structured metric value.")
    unit = models.CharField(max_length=50, blank=True, default="", help_text="e.g. percent, seconds, count.")

    # -- optional dimensional tags --
    dimension_json = models.JSONField(
        default=dict, blank=True,
        help_text="Arbitrary key-value pairs for slicing (field, category, vendor, etc.).",
    )
    metadata_json = models.JSONField(default=dict, blank=True, help_text="Extra context.")

    class Meta:
        db_table = "core_eval_eval_metric"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["metric_name"], name="idx_evalmetric_name"),
            models.Index(fields=["tenant_id"], name="idx_evalmetric_tenant"),
        ]
        verbose_name = "Eval Metric"
        verbose_name_plural = "Eval Metrics"

    def __str__(self) -> str:
        if self.metric_value is not None:
            return f"{self.metric_name}={self.metric_value}"
        if self.string_value:
            return f"{self.metric_name}={self.string_value!r}"
        if self.json_value is not None:
            return f"{self.metric_name}=[json]"
        return f"{self.metric_name}=(empty)"

    def clean(self) -> None:
        """Validate that at most one value field is populated."""
        populated = []
        if self.metric_value is not None:
            populated.append("metric_value")
        if self.string_value:
            populated.append("string_value")
        if self.json_value is not None:
            populated.append("json_value")
        if len(populated) > 1:
            raise ValidationError(
                f"At most one value field may be populated; got: {', '.join(populated)}."
            )


# ---------------------------------------------------------------------------
# EvalFieldOutcome -- per-field accuracy record
# ---------------------------------------------------------------------------
class EvalFieldOutcome(TimestampMixin):
    """Per-field comparison between predicted and ground-truth values."""

    class Status(models.TextChoices):
        CORRECT = "CORRECT", "Correct"
        INCORRECT = "INCORRECT", "Incorrect"
        MISSING = "MISSING", "Missing"
        EXTRA = "EXTRA", "Extra"
        SKIPPED = "SKIPPED", "Skipped"

    # -- tenant --
    tenant_id = models.CharField(
        max_length=100, db_index=True, blank=True, default="",
        help_text="Lightweight tenant identifier for multi-tenant isolation.",
    )

    eval_run = models.ForeignKey(
        EvalRun, on_delete=models.CASCADE, related_name="field_outcomes",
        null=True, blank=True,
    )

    field_name = models.CharField(max_length=200, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)

    predicted_value = models.TextField(blank=True, default="")
    ground_truth_value = models.TextField(blank=True, default="")
    confidence = models.FloatField(null=True, blank=True, help_text="0.0-1.0 model confidence.")

    # -- flexible payload --
    detail_json = models.JSONField(
        default=dict, blank=True,
        help_text="Additional detail (normalisation applied, similarity score, etc.).",
    )

    class Meta:
        db_table = "core_eval_eval_field_outcome"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["field_name"], name="idx_fieldoutcome_field"),
            models.Index(fields=["status"], name="idx_fieldoutcome_status"),
            models.Index(fields=["tenant_id"], name="idx_fieldoutcome_tenant"),
        ]
        verbose_name = "Eval Field Outcome"
        verbose_name_plural = "Eval Field Outcomes"

    def __str__(self) -> str:
        return f"{self.field_name}: {self.status}"


# ---------------------------------------------------------------------------
# LearningSignal -- raw correction / feedback event
# ---------------------------------------------------------------------------
class LearningSignal(TimestampMixin):
    """Captures a raw learning signal from any module (correction, rejection, etc.)."""

    # -- tenant --
    tenant_id = models.CharField(
        max_length=100, db_index=True, blank=True, default="",
        help_text="Lightweight tenant identifier for multi-tenant isolation.",
    )

    app_module = models.CharField(
        max_length=120, db_index=True,
        help_text="Originating module.",
    )
    signal_type = models.CharField(
        max_length=120, db_index=True,
        help_text="Type of signal (e.g. field_correction, approval_rejection, confidence_override).",
    )

    entity_type = models.CharField(max_length=120, blank=True, default="")
    entity_id = models.CharField(max_length=255, blank=True, default="")

    # -- grouping / pattern detection --
    aggregation_key = models.CharField(
        max_length=255, db_index=True, blank=True, default="",
        help_text="Key for grouping related signals (e.g. vendor_name::ABC_LTD).",
    )
    confidence = models.FloatField(
        default=0.0,
        help_text="Signal strength for future filtering / prioritisation (0.0-1.0).",
    )

    # -- who / what --
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="core_eval_learningsignal_actor",
    )

    # -- the signal data --
    field_name = models.CharField(max_length=200, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    payload_json = models.JSONField(default=dict, blank=True, help_text="Full signal payload.")

    # -- optional link to an eval run --
    eval_run = models.ForeignKey(
        EvalRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="learning_signals",
    )

    class Meta:
        db_table = "core_eval_learning_signal"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["app_module"], name="idx_learnsig_app_module"),
            models.Index(fields=["signal_type"], name="idx_learnsig_signal_type"),
            models.Index(fields=["entity_type", "entity_id"], name="idx_learnsig_entity"),
            models.Index(fields=["created_at"], name="idx_learnsig_created_at"),
            models.Index(fields=["aggregation_key"], name="idx_learnsig_agg_key"),
            models.Index(fields=["tenant_id"], name="idx_learnsig_tenant"),
        ]
        verbose_name = "Learning Signal"
        verbose_name_plural = "Learning Signals"

    def __str__(self) -> str:
        return f"LearningSignal#{self.pk} {self.signal_type} ({self.app_module})"


# ---------------------------------------------------------------------------
# LearningAction -- action taken based on aggregated signals
# ---------------------------------------------------------------------------
class LearningAction(TimestampMixin):
    """Records an action taken (or proposed) based on accumulated learning signals."""

    class Status(models.TextChoices):
        PROPOSED = "PROPOSED", "Proposed"
        APPROVED = "APPROVED", "Approved"
        APPLIED = "APPLIED", "Applied"
        REJECTED = "REJECTED", "Rejected"
        FAILED = "FAILED", "Failed"

    # -- tenant --
    tenant_id = models.CharField(
        max_length=100, db_index=True, blank=True, default="",
        help_text="Lightweight tenant identifier for multi-tenant isolation.",
    )

    action_type = models.CharField(
        max_length=120, db_index=True,
        help_text="Type of action (e.g. prompt_update, threshold_adjustment, alias_creation).",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROPOSED, db_index=True,
    )

    # -- context --
    app_module = models.CharField(max_length=120, blank=True, default="")
    target_description = models.TextField(
        blank=True, default="",
        help_text="Human-readable description of what this action targets.",
    )
    rationale = models.TextField(blank=True, default="", help_text="Why this action was proposed.")

    # -- flexible payloads --
    input_signals_json = models.JSONField(
        default=dict, blank=True,
        help_text="Summary of learning signals that triggered this action.",
    )
    action_payload_json = models.JSONField(
        default=dict, blank=True,
        help_text="The action itself (e.g. new prompt content, new threshold value).",
    )
    result_json = models.JSONField(default=dict, blank=True, help_text="Outcome after application.")

    # -- who --
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="core_eval_learningaction_proposed",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="core_eval_learningaction_approved",
    )
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_eval_learning_action"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action_type"], name="idx_learnact_action_type"),
            models.Index(fields=["status"], name="idx_learnact_status"),
            models.Index(fields=["tenant_id"], name="idx_learnact_tenant"),
        ]
        verbose_name = "Learning Action"
        verbose_name_plural = "Learning Actions"

    def __str__(self) -> str:
        return f"LearningAction#{self.pk} {self.action_type} [{self.status}]"
