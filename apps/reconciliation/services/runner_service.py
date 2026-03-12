"""Reconciliation runner — orchestrates the deterministic match pipeline.

Flow for each invoice:
  1. PO Lookup
  2. Mode Resolution (TWO_WAY vs THREE_WAY)
  3. Execution Router dispatch (header + line ± GRN)
  4. Mode-aware Classification
  5. Mode-aware Exception Building
  6. Result Persistence (with mode metadata)
  7. Review assignment (if needed)
  8. Invoice status transition
"""
from __future__ import annotations

import logging
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    InvoiceStatus,
    MatchStatus,
    ReconciliationMode,
    ReconciliationRunStatus,
)
from apps.documents.models import Invoice
from apps.reconciliation.models import ReconciliationConfig, ReconciliationRun
from apps.reconciliation.services.classification_service import ClassificationService
from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
from apps.reconciliation.services.execution_router import ReconciliationExecutionRouter
from apps.reconciliation.services.mode_resolver import ModeResolutionResult, ReconciliationModeResolver
from apps.reconciliation.services.po_lookup_service import POLookupService
from apps.reconciliation.services.result_service import ReconciliationResultService
from apps.reconciliation.services.tolerance_engine import ToleranceEngine

logger = logging.getLogger(__name__)


class ReconciliationRunnerService:
    """High-level orchestrator for a batch reconciliation run."""

    def __init__(self, config: Optional[ReconciliationConfig] = None):
        self.config = config or self._default_config()
        self.tolerance = ToleranceEngine(self.config)

        # Sub-services
        self.po_lookup = POLookupService()
        self.mode_resolver = ReconciliationModeResolver(self.config)
        self.router = ReconciliationExecutionRouter(self.tolerance)
        self.classifier = ClassificationService()
        self.exception_builder = ExceptionBuilderService()
        self.result_service = ReconciliationResultService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(
        self,
        invoices: Optional[List[Invoice]] = None,
        triggered_by=None,
    ) -> ReconciliationRun:
        """Execute reconciliation for a set of invoices.

        If *invoices* is None, all invoices with status READY_FOR_RECON are
        selected automatically.
        """
        if invoices is None:
            invoices = list(
                Invoice.objects.filter(status=InvoiceStatus.READY_FOR_RECON)
                .select_related("vendor", "document_upload")
            )

        recon_run = ReconciliationRun.objects.create(
            status=ReconciliationRunStatus.RUNNING,
            config=self.config,
            started_at=timezone.now(),
            total_invoices=len(invoices),
            triggered_by=triggered_by,
        )

        # Audit: reconciliation started
        from apps.auditlog.services import AuditService
        for inv in invoices:
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=inv.pk,
                event_type=AuditEventType.RECONCILIATION_STARTED,
                description=f"Reconciliation run #{recon_run.pk} started",
                user=triggered_by,
                metadata={"run_id": recon_run.pk, "config": self.config.name},
            )

        logger.info(
            "Starting reconciliation run %s for %d invoices (config=%s)",
            recon_run.pk, len(invoices), self.config.name,
        )

        matched = partial = unmatched = errors = review = 0

        for invoice in invoices:
            try:
                status = self._reconcile_single(recon_run, invoice)
                if status == MatchStatus.MATCHED:
                    matched += 1
                elif status == MatchStatus.PARTIAL_MATCH:
                    partial += 1
                elif status == MatchStatus.UNMATCHED:
                    unmatched += 1
                elif status == MatchStatus.REQUIRES_REVIEW:
                    review += 1
                else:
                    errors += 1
            except Exception:
                logger.exception("Error reconciling invoice %s", invoice.pk)
                errors += 1

        # Finalise run
        recon_run.status = ReconciliationRunStatus.COMPLETED
        recon_run.completed_at = timezone.now()
        recon_run.matched_count = matched
        recon_run.partial_count = partial
        recon_run.unmatched_count = unmatched
        recon_run.error_count = errors
        recon_run.review_count = review
        recon_run.save()

        # Audit: reconciliation completed for each invoice
        for inv in invoices:
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=inv.pk,
                event_type=AuditEventType.RECONCILIATION_COMPLETED,
                description=f"Reconciliation run #{recon_run.pk} completed",
                user=triggered_by,
                metadata={
                    "run_id": recon_run.pk, "matched": matched,
                    "partial": partial, "unmatched": unmatched, "errors": errors,
                },
            )

        logger.info(
            "Reconciliation run %s completed: matched=%d partial=%d unmatched=%d errors=%d review=%d",
            recon_run.pk, matched, partial, unmatched, errors, review,
        )
        return recon_run

    # ------------------------------------------------------------------
    # Single-invoice pipeline
    # ------------------------------------------------------------------
    def _reconcile_single(
        self, run: ReconciliationRun, invoice: Invoice
    ) -> MatchStatus:
        """Run the deterministic match for one invoice (2-way or 3-way)."""

        # 1. PO Lookup (includes vendor+amount discovery fallback)
        po_result = self.po_lookup.lookup(invoice)

        # 1b. If PO was discovered (not by PO number), backfill invoice.po_number
        if po_result.found and po_result.lookup_method == "vendor_amount":
            invoice.po_number = po_result.purchase_order.po_number
            invoice.save(update_fields=["po_number", "updated_at"])

            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=invoice.pk,
                event_type=AuditEventType.RECONCILIATION_COMPLETED,
                description=(
                    f"PO {po_result.purchase_order.po_number} discovered deterministically "
                    f"via vendor+amount match (vendor={invoice.vendor_id}, "
                    f"amount={invoice.total_amount})"
                ),
                metadata={
                    "lookup_method": "vendor_amount",
                    "discovered_po": po_result.purchase_order.po_number,
                    "vendor_id": invoice.vendor_id,
                    "invoice_total": str(invoice.total_amount),
                    "po_total": str(po_result.purchase_order.total_amount),
                },
            )
        po_for_resolver = po_result.purchase_order if po_result.found else None
        mode_resolution = self.mode_resolver.resolve(invoice, po_for_resolver)

        # Audit: mode resolved
        from apps.auditlog.services import AuditService
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=invoice.pk,
            event_type=AuditEventType.RECONCILIATION_MODE_RESOLVED,
            description=(
                f"Mode resolved to {mode_resolution.mode} "
                f"via {mode_resolution.resolution_method}: {mode_resolution.reason}"
            ),
            metadata={
                "mode": mode_resolution.mode,
                "policy_code": mode_resolution.policy_code,
                "resolution_method": mode_resolution.resolution_method,
                "grn_required": mode_resolution.grn_required,
            },
        )

        # 3. Execute via router (dispatches to 2-way or 3-way pipeline)
        routed = self.router.execute(invoice, po_result, mode_resolution)

        # 4. Mode-aware Classification
        reconciliation_mode = mode_resolution.mode
        match_status = self.classifier.classify(
            po_result=routed.po_result,
            header_result=routed.header_result,
            line_result=routed.line_result,
            grn_result=routed.grn_result,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
            reconciliation_mode=reconciliation_mode,
        )

        # 5. Save result with mode metadata
        result = self.result_service.save(
            run=run,
            invoice=invoice,
            match_status=match_status,
            po_result=routed.po_result,
            header_result=routed.header_result,
            line_result=routed.line_result,
            grn_result=routed.grn_result,
            exceptions=[],  # Build separately below to get result_line references
            reconciliation_mode=reconciliation_mode,
            mode_resolution=mode_resolution,
        )

        # Build result_line map from saved result
        result_line_map = {
            rl.invoice_line_id: rl
            for rl in result.line_results.all()
            if rl.invoice_line_id
        }

        # 6. Mode-aware Exception building
        exceptions = self.exception_builder.build(
            result=result,
            po_result=routed.po_result,
            header_result=routed.header_result,
            line_result=routed.line_result,
            grn_result=routed.grn_result,
            result_line_map=result_line_map,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
            reconciliation_mode=reconciliation_mode,
        )
        if exceptions:
            from apps.reconciliation.models import ReconciliationException
            ReconciliationException.objects.bulk_create(exceptions)

        # 7. Auto-create review assignment for items needing human review
        if match_status == MatchStatus.REQUIRES_REVIEW:
            from apps.reviews.services import ReviewWorkflowService
            ReviewWorkflowService.create_assignment(
                result=result,
                priority=3 if exceptions else 5,
                notes=f"Auto-created: {len(exceptions)} exception(s) found during reconciliation.",
            )

        # 8. Transition invoice status
        self._transition_invoice(invoice, match_status)

        return match_status

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _transition_invoice(invoice: Invoice, status: MatchStatus) -> None:
        invoice.status = InvoiceStatus.RECONCILED
        invoice.save(update_fields=["status", "updated_at"])

    @staticmethod
    def _default_config() -> ReconciliationConfig:
        """Get or create a default ReconciliationConfig."""
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config:
            return config
        return ReconciliationConfig.objects.create(
            name="Default",
            quantity_tolerance_pct=2.0,
            price_tolerance_pct=1.0,
            amount_tolerance_pct=1.0,
            is_default=True,
        )
