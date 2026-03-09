"""Reconciliation domain models."""
from django.conf import settings
from django.db import models

from apps.core.enums import (
    ExceptionSeverity,
    ExceptionType,
    MatchStatus,
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
    auto_close_on_match = models.BooleanField(default=True)
    enable_agents = models.BooleanField(default=True)
    extraction_confidence_threshold = models.FloatField(default=0.75)
    is_default = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "reconciliation_config"
        ordering = ["name"]
        verbose_name = "Reconciliation Config"
        verbose_name_plural = "Reconciliation Configs"

    def __str__(self) -> str:
        return self.name


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

    class Meta:
        db_table = "reconciliation_result"
        ordering = ["-created_at"]
        verbose_name = "Reconciliation Result"
        verbose_name_plural = "Reconciliation Results"
        indexes = [
            models.Index(fields=["match_status"], name="idx_recon_result_status"),
            models.Index(fields=["requires_review"], name="idx_recon_result_review"),
            models.Index(fields=["run", "invoice"], name="idx_recon_result_run_inv"),
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
