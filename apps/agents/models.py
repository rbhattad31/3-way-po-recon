"""Agentic execution models: definitions, runs, steps, messages, recommendations."""
from django.conf import settings
from django.db import models

from apps.core.enums import (
    AgentRunStatus,
    AgentType,
    RecommendationType,
    ExceptionSeverity,
)
from apps.core.models import BaseModel, TimestampMixin


# ---------------------------------------------------------------------------
# Agent Definition (registry / config)
# ---------------------------------------------------------------------------
class AgentDefinition(BaseModel):
    """Registry entry describing an available agent."""

    agent_type = models.CharField(max_length=40, choices=AgentType.choices, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=True, db_index=True)
    llm_model = models.CharField(max_length=100, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    max_retries = models.PositiveIntegerField(default=2)
    timeout_seconds = models.PositiveIntegerField(default=120)
    config_json = models.JSONField(null=True, blank=True, help_text="Agent-specific configuration")

    class Meta:
        db_table = "agents_definition"
        ordering = ["agent_type"]
        verbose_name = "Agent Definition"
        verbose_name_plural = "Agent Definitions"

    def __str__(self) -> str:
        return f"{self.name} ({self.agent_type})"


# ---------------------------------------------------------------------------
# Agent Run
# ---------------------------------------------------------------------------
class AgentRun(BaseModel):
    """One execution of an agent within an orchestration pipeline."""

    agent_definition = models.ForeignKey(AgentDefinition, on_delete=models.SET_NULL, null=True, related_name="runs")
    agent_type = models.CharField(max_length=40, choices=AgentType.choices, db_index=True)
    reconciliation_result = models.ForeignKey(
        "reconciliation.ReconciliationResult", on_delete=models.CASCADE,
        related_name="agent_runs", null=True, blank=True,
    )
    status = models.CharField(max_length=20, choices=AgentRunStatus.choices, default=AgentRunStatus.PENDING, db_index=True)

    input_payload = models.JSONField(null=True, blank=True)
    output_payload = models.JSONField(null=True, blank=True)
    summarized_reasoning = models.TextField(blank=True, default="", help_text="Enterprise-safe reasoning summary")
    confidence = models.FloatField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    # Traceability
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    span_id = models.CharField(max_length=64, blank=True, default="")
    invocation_reason = models.CharField(max_length=500, blank=True, default="")
    prompt_version = models.CharField(max_length=50, blank=True, default="")
    actor_user_id = models.PositiveIntegerField(null=True, blank=True, help_text="User who triggered agent, if user-initiated")
    permission_checked = models.CharField(max_length=100, blank=True, default="")
    cost_estimate = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    # LLM usage tracking
    llm_model_used = models.CharField(max_length=100, blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)

    # Handoff
    handed_off_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="handed_off_from"
    )

    class Meta:
        db_table = "agents_run"
        ordering = ["-created_at"]
        verbose_name = "Agent Run"
        verbose_name_plural = "Agent Runs"
        indexes = [
            models.Index(fields=["agent_type"], name="idx_agentrun_type"),
            models.Index(fields=["status"], name="idx_agentrun_status"),
            models.Index(fields=["reconciliation_result"], name="idx_agentrun_result"),
        ]

    def __str__(self) -> str:
        return f"AgentRun #{self.pk} – {self.agent_type} – {self.status}"


# ---------------------------------------------------------------------------
# Agent Step (substep within a run)
# ---------------------------------------------------------------------------
class AgentStep(TimestampMixin):
    """Ordered substep within an agent run."""

    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveIntegerField(default=1)
    action = models.CharField(max_length=200)
    input_data = models.JSONField(null=True, blank=True)
    output_data = models.JSONField(null=True, blank=True)
    success = models.BooleanField(default=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "agents_step"
        ordering = ["agent_run", "step_number"]
        verbose_name = "Agent Step"
        verbose_name_plural = "Agent Steps"

    def __str__(self) -> str:
        return f"Step {self.step_number} of AgentRun #{self.agent_run_id}"


# ---------------------------------------------------------------------------
# Agent Message
# ---------------------------------------------------------------------------
class AgentMessage(TimestampMixin):
    """Chat-style message within an agent run (system / user / assistant)."""

    ROLE_CHOICES = [
        ("system", "System"),
        ("user", "User"),
        ("assistant", "Assistant"),
        ("tool", "Tool"),
    ]

    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    token_count = models.PositiveIntegerField(null=True, blank=True)
    message_index = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "agents_message"
        ordering = ["agent_run", "message_index"]
        verbose_name = "Agent Message"
        verbose_name_plural = "Agent Messages"

    def __str__(self) -> str:
        return f"Msg {self.message_index} ({self.role}) – AgentRun #{self.agent_run_id}"


# ---------------------------------------------------------------------------
# Decision Log
# ---------------------------------------------------------------------------
class DecisionLog(TimestampMixin):
    """Stores key decisions for audit and explainability.

    Captures the 'why' behind every major decision — agent, deterministic,
    policy, or human. Each entry explains the decision_type, rationale,
    confidence, and the rule/policy/prompt that produced it.
    """

    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="decisions", null=True, blank=True)

    # Decision identification
    decision_type = models.CharField(max_length=100, blank=True, default="", db_index=True,
                                     help_text="E.g. path_selected, mode_resolved, match_determined, auto_closed")
    decision = models.CharField(max_length=500)
    rationale = models.TextField(blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    deterministic_flag = models.BooleanField(default=False, help_text="True if rule-based, False if LLM-generated")
    evidence_refs = models.JSONField(null=True, blank=True, help_text="References to data that support the decision")

    # Rule/policy traceability
    rule_name = models.CharField(max_length=200, blank=True, default="")
    rule_version = models.CharField(max_length=50, blank=True, default="")
    policy_code = models.CharField(max_length=100, blank=True, default="")
    policy_version = models.CharField(max_length=50, blank=True, default="")
    prompt_template_id = models.PositiveIntegerField(null=True, blank=True)
    prompt_version = models.CharField(max_length=50, blank=True, default="")
    config_snapshot_json = models.JSONField(null=True, blank=True, help_text="Config/tolerance values at decision time")
    recommendation_type = models.CharField(max_length=60, blank=True, default="")

    # RBAC context (nullable — for human decisions)
    actor_user_id = models.PositiveIntegerField(null=True, blank=True)
    actor_primary_role = models.CharField(max_length=50, blank=True, default="")
    permission_checked = models.CharField(max_length=100, blank=True, default="")
    authorization_snapshot_json = models.JSONField(null=True, blank=True)

    # Traceability
    trace_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    span_id = models.CharField(max_length=64, blank=True, default="")

    # Cross-references
    invoice_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    case_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    reconciliation_result_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "agents_decision_log"
        ordering = ["-created_at"]
        verbose_name = "Decision Log"
        verbose_name_plural = "Decision Logs"

    def __str__(self) -> str:
        return f"Decision – AgentRun #{self.agent_run_id}: {self.decision[:80]}"


# ---------------------------------------------------------------------------
# Agent Recommendation
# ---------------------------------------------------------------------------
class AgentRecommendation(TimestampMixin):
    """Recommendation produced by an agent (routing, action, etc.)."""

    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="recommendations")
    reconciliation_result = models.ForeignKey(
        "reconciliation.ReconciliationResult", on_delete=models.CASCADE, related_name="agent_recommendations"
    )
    invoice = models.ForeignKey(
        "documents.Invoice", on_delete=models.CASCADE, null=True, blank=True, related_name="agent_recommendations"
    )
    recommendation_type = models.CharField(max_length=40, choices=RecommendationType.choices, db_index=True)
    confidence = models.FloatField(null=True, blank=True)
    reasoning = models.TextField(blank=True, default="")
    evidence = models.JSONField(null=True, blank=True)
    recommended_action = models.CharField(max_length=200, blank=True, default="", help_text="Specific action description")
    accepted = models.BooleanField(null=True, help_text="null=pending, True=accepted, False=rejected")
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agents_recommendation"
        ordering = ["-created_at"]
        verbose_name = "Agent Recommendation"
        verbose_name_plural = "Agent Recommendations"
        indexes = [
            models.Index(fields=["recommendation_type"], name="idx_agentrec_type"),
            models.Index(fields=["reconciliation_result"], name="idx_agentrec_result"),
            models.Index(fields=["invoice"], name="idx_agentrec_invoice"),
        ]

    def __str__(self) -> str:
        return f"Recommendation {self.recommendation_type} – AgentRun #{self.agent_run_id}"


# ---------------------------------------------------------------------------
# Agent Escalation
# ---------------------------------------------------------------------------
class AgentEscalation(TimestampMixin):
    """Escalation produced by an agent when confidence is below threshold."""

    agent_run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="escalations")
    reconciliation_result = models.ForeignKey(
        "reconciliation.ReconciliationResult", on_delete=models.CASCADE, related_name="agent_escalations"
    )
    severity = models.CharField(max_length=20, choices=ExceptionSeverity.choices, default=ExceptionSeverity.MEDIUM)
    reason = models.TextField()
    suggested_assignee_role = models.CharField(max_length=50, blank=True, default="")
    resolved = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "agents_escalation"
        ordering = ["-created_at"]
        verbose_name = "Agent Escalation"
        verbose_name_plural = "Agent Escalations"

    def __str__(self) -> str:
        return f"Escalation ({self.severity}) – AgentRun #{self.agent_run_id}"
