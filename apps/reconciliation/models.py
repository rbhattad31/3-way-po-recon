"""Reconciliation domain models."""
from django.conf import settings
from django.db import models

from apps.core.enums import (
    ExceptionSeverity,
    ExceptionType,
    MatchStatus,
    ReconciliationMode,
    ReconciliationModeApplicability,
    ReconciliationRunStatus,
)
from apps.core.models import BaseModel, TimestampMixin


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class ReconciliationConfig(BaseModel):
    """Run-time configuration for tolerance thresholds and feature flags."""

    name = models.CharField(max_length=100, unique=True)
    quantity_tolerance_pct = models.FloatField(default=2.0)
    price_tolerance_pct = models.FloatField(default=1.0)
    amount_tolerance_pct = models.FloatField(default=1.0)
    # Wider auto-close band: PARTIAL_MATCH within these thresholds → auto-close without AI
    auto_close_qty_tolerance_pct = models.FloatField(default=5.0)
    auto_close_price_tolerance_pct = models.FloatField(default=3.0)
    auto_close_amount_tolerance_pct = models.FloatField(default=3.0)
    auto_close_on_match = models.BooleanField(default=True)
    enable_agents = models.BooleanField(default=True)
    extraction_confidence_threshold = models.FloatField(default=0.75)
    is_default = models.BooleanField(default=False, db_index=True)

    # Mode configuration
    default_reconciliation_mode = models.CharField(
        max_length=20, choices=ReconciliationMode.choices,
        default=ReconciliationMode.THREE_WAY, db_index=True,
    )
    enable_mode_resolver = models.BooleanField(
        default=True, help_text="Use policy rules to auto-resolve 2-way vs 3-way per invoice",
    )
    enable_grn_for_stock_items = models.BooleanField(
        default=True, help_text="Require GRN for stock/inventory items",
    )
    enable_two_way_for_services = models.BooleanField(
        default=True, help_text="Auto-select 2-way mode for service invoices",
    )

    # Access control
    ap_processor_sees_all_cases = models.BooleanField(
        default=False,
        help_text="When False, AP Processors only see cases for documents they uploaded",
    )

    class Meta:
        db_table = "reconciliation_config"
        ordering = ["name"]
        verbose_name = "Reconciliation Config"
        verbose_name_plural = "Reconciliation Configs"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Reconciliation Policy
# ---------------------------------------------------------------------------
class ReconciliationPolicy(BaseModel):
    """Rule-based policy to resolve reconciliation mode per invoice.

    The mode resolver evaluates active policies ordered by priority
    (lower = higher precedence) and returns the first match.
    """

    policy_code = models.CharField(max_length=50, unique=True, db_index=True)
    policy_name = models.CharField(max_length=200)
    reconciliation_mode = models.CharField(
        max_length=20, choices=ReconciliationMode.choices,
    )

    # Matching criteria — nullable means "any / not evaluated"
    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="recon_policies",
    )
    invoice_type = models.CharField(max_length=100, blank=True, default="")
    item_category = models.CharField(max_length=100, blank=True, default="")
    business_unit = models.CharField(max_length=100, blank=True, default="")
    location_code = models.CharField(max_length=100, blank=True, default="")
    is_service_invoice = models.BooleanField(null=True, blank=True)
    is_stock_invoice = models.BooleanField(null=True, blank=True)

    # Ordering and validity
    priority = models.PositiveIntegerField(
        default=100, db_index=True,
        help_text="Lower number = higher precedence",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "reconciliation_policy"
        ordering = ["priority", "policy_code"]
        verbose_name = "Reconciliation Policy"
        verbose_name_plural = "Reconciliation Policies"
        indexes = [
            models.Index(fields=["priority"], name="idx_recon_pol_priority"),
            models.Index(fields=["reconciliation_mode"], name="idx_recon_pol_mode"),
        ]

    def __str__(self) -> str:
        return f"{self.policy_code} – {self.policy_name} ({self.reconciliation_mode})"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
class ReconciliationRun(BaseModel):
    """One execution of the reconciliation engine (may cover 1+ invoices)."""

    status = models.CharField(
        max_length=20, choices=ReconciliationRunStatus.choices, default=ReconciliationRunStatus.PENDING, db_index=True
    )
    config = models.ForeignKey(
        ReconciliationConfig, on_delete=models.SET_NULL, null=True, blank=True, related_name="runs"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_invoices = models.PositiveIntegerField(default=0)
    matched_count = models.PositiveIntegerField(default=0)
    partial_count = models.PositiveIntegerField(default=0)
    unmatched_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    review_count = models.PositiveIntegerField(default=0)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="recon_runs"
    )
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # Mode metadata
    reconciliation_mode = models.CharField(
        max_length=20, choices=ReconciliationMode.choices,
        blank=True, default="", db_index=True,
        help_text="Dominant mode used across invoices in this run",
    )
    policy_name_applied = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Policy code that determined the mode (if uniform)",
    )
    grn_required_flag = models.BooleanField(
        null=True, blank=True,
        help_text="Whether GRN was required for this run",
    )
    grn_checked_flag = models.BooleanField(
        null=True, blank=True,
        help_text="Whether GRN matching was actually performed",
    )

    class Meta:
        db_table = "reconciliation_run"
        ordering = ["-created_at"]
        verbose_name = "Reconciliation Run"
        verbose_name_plural = "Reconciliation Runs"
        indexes = [
            models.Index(fields=["status"], name="idx_recon_run_status"),
        ]

    def __str__(self) -> str:
        return f"Run #{self.pk} – {self.status}"


# ---------------------------------------------------------------------------
# Result (header-level)
# ---------------------------------------------------------------------------
class ReconciliationResult(BaseModel):
    """Header-level reconciliation outcome for one invoice."""

    run = models.ForeignKey(ReconciliationRun, on_delete=models.CASCADE, related_name="results")
    invoice = models.ForeignKey(
        "documents.Invoice", on_delete=models.CASCADE, related_name="recon_results"
    )
    purchase_order = models.ForeignKey(
        "documents.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="recon_results"
    )

    # Outcome
    match_status = models.CharField(max_length=30, choices=MatchStatus.choices, db_index=True)
    requires_review = models.BooleanField(default=False, db_index=True)

    # Deterministic comparison evidence
    vendor_match = models.BooleanField(null=True)
    currency_match = models.BooleanField(null=True)
    po_total_match = models.BooleanField(null=True)
    invoice_total_vs_po = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    total_amount_difference = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    total_amount_difference_pct = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    grn_available = models.BooleanField(default=False)
    grn_fully_received = models.BooleanField(null=True)

    # Confidence inputs
    extraction_confidence = models.FloatField(null=True, blank=True)
    deterministic_confidence = models.FloatField(null=True, blank=True, help_text="Computed header confidence 0-1")

    # Summary
    summary = models.TextField(blank=True, default="")

    # Mode metadata
    reconciliation_mode = models.CharField(
        max_length=20, choices=ReconciliationMode.choices,
        blank=True, default="", db_index=True,
    )
    grn_required_flag = models.BooleanField(
        null=True, blank=True,
        help_text="Whether GRN verification was required for this invoice",
    )
    grn_checked_flag = models.BooleanField(
        null=True, blank=True,
        help_text="Whether GRN matching was actually executed",
    )
    mode_resolution_reason = models.TextField(
        blank=True, default="",
        help_text="Explanation of why this mode was selected",
    )
    policy_applied = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Policy code that determined the reconciliation mode",
    )
    is_two_way_result = models.BooleanField(default=False, db_index=True)
    is_three_way_result = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "reconciliation_result"
        ordering = ["-created_at"]
        verbose_name = "Reconciliation Result"
        verbose_name_plural = "Reconciliation Results"
        indexes = [
            models.Index(fields=["match_status"], name="idx_recon_result_status"),
            models.Index(fields=["requires_review"], name="idx_recon_result_review"),
            models.Index(fields=["run", "invoice"], name="idx_recon_result_run_inv"),
            models.Index(fields=["reconciliation_mode"], name="idx_recon_result_mode"),
        ]

    def __str__(self) -> str:
        return f"Result #{self.pk} – Invoice {self.invoice_id} – {self.match_status}"


# ---------------------------------------------------------------------------
# Result Line (line-level)
# ---------------------------------------------------------------------------
class ReconciliationResultLine(TimestampMixin):
    """Line-level comparison result."""

    result = models.ForeignKey(ReconciliationResult, on_delete=models.CASCADE, related_name="line_results")
    invoice_line = models.ForeignKey(
        "documents.InvoiceLineItem", on_delete=models.SET_NULL, null=True, blank=True
    )
    po_line = models.ForeignKey(
        "documents.PurchaseOrderLineItem", on_delete=models.SET_NULL, null=True, blank=True
    )
    match_status = models.CharField(max_length=30, choices=MatchStatus.choices)

    qty_invoice = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    qty_po = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    qty_received = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    qty_difference = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    qty_within_tolerance = models.BooleanField(null=True)

    price_invoice = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    price_po = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    price_difference = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    price_within_tolerance = models.BooleanField(null=True)

    amount_invoice = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_po = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_difference = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_within_tolerance = models.BooleanField(null=True)

    tax_invoice = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_po = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_difference = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    description_similarity = models.FloatField(null=True, blank=True, help_text="0-100 fuzzy score")

    class Meta:
        db_table = "reconciliation_result_line"
        ordering = ["result", "id"]
        verbose_name = "Reconciliation Result Line"
        verbose_name_plural = "Reconciliation Result Lines"
        indexes = [
            models.Index(fields=["result"], name="idx_recon_rline_result"),
            models.Index(fields=["match_status"], name="idx_recon_rline_status"),
        ]

    def __str__(self) -> str:
        return f"ResultLine #{self.pk} – {self.match_status}"


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------
class ReconciliationException(TimestampMixin):
    """Structured exception raised during reconciliation."""

    result = models.ForeignKey(ReconciliationResult, on_delete=models.CASCADE, related_name="exceptions")
    result_line = models.ForeignKey(
        ReconciliationResultLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="exceptions"
    )
    exception_type = models.CharField(max_length=40, choices=ExceptionType.choices, db_index=True)
    severity = models.CharField(max_length=20, choices=ExceptionSeverity.choices, default=ExceptionSeverity.MEDIUM)
    message = models.TextField()
    details = models.JSONField(null=True, blank=True, help_text="Structured context for agent consumption")
    resolved = models.BooleanField(default=False, db_index=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Mode applicability
    applies_to_mode = models.CharField(
        max_length=20, choices=ReconciliationModeApplicability.choices,
        default=ReconciliationModeApplicability.BOTH, db_index=True,
        help_text="Which reconciliation mode(s) this exception is relevant for",
    )

    class Meta:
        db_table = "reconciliation_exception"
        ordering = ["-created_at"]
        verbose_name = "Reconciliation Exception"
        verbose_name_plural = "Reconciliation Exceptions"
        indexes = [
            models.Index(fields=["exception_type"], name="idx_recon_exc_type"),
            models.Index(fields=["severity"], name="idx_recon_exc_sev"),
            models.Index(fields=["resolved"], name="idx_recon_exc_resolved"),
        ]

    def __str__(self) -> str:
        return f"{self.exception_type} – {self.severity} – Result #{self.result_id}"
