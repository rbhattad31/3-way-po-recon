"""Reconciliation runner — orchestrates the full deterministic 3-way match pipeline.

Flow for each invoice:
  1. PO Lookup
  2. Header Match
  3. Line Match
  4. GRN Lookup + Match
  5. Classification
  6. Exception Building
  7. Result Persistence
  8. Invoice status transition
"""
from __future__ import annotations

import logging
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import InvoiceStatus, MatchStatus, ReconciliationRunStatus
from apps.documents.models import Invoice
from apps.reconciliation.models import ReconciliationConfig, ReconciliationRun
from apps.reconciliation.services.classification_service import ClassificationService
from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
from apps.reconciliation.services.grn_lookup_service import GRNLookupService
from apps.reconciliation.services.grn_match_service import GRNMatchService
from apps.reconciliation.services.header_match_service import HeaderMatchService
from apps.reconciliation.services.line_match_service import LineMatchService
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
        self.grn_lookup = GRNLookupService()
        self.header_match = HeaderMatchService(self.tolerance)
        self.line_match = LineMatchService(self.tolerance)
        self.grn_match = GRNMatchService()
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
        from apps.core.enums import AuditEventType
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
        """Run the deterministic 3-way match for one invoice."""

        # 1. PO Lookup
        po_result = self.po_lookup.lookup(invoice)

        header_result = None
        line_result = None
        grn_result = None
        grn_summary = None

        if po_result.found:
            po = po_result.purchase_order

            # 2. Header Match
            header_result = self.header_match.match(invoice, po)

            # 3. Line Match
            line_result = self.line_match.match(invoice, po)

            # 4. GRN Lookup + Match
            grn_summary = self.grn_lookup.lookup(po)
            if grn_summary.grn_available and line_result:
                grn_result = self.grn_match.match(line_result.pairs, grn_summary)
            else:
                grn_result = None

        # 5. Classification
        match_status = self.classifier.classify(
            po_result=po_result,
            header_result=header_result,
            line_result=line_result,
            grn_result=grn_result,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
        )

        # 6. Exception building (with a placeholder result for FK)
        # We save the result first, then build exceptions, then bulk-create them
        result = self.result_service.save(
            run=run,
            invoice=invoice,
            match_status=match_status,
            po_result=po_result,
            header_result=header_result,
            line_result=line_result,
            grn_result=grn_result if grn_result else (
                type("GRNMatchResult", (), {"grn_available": False, "fully_received": None, "has_receipt_issues": False})()
                if grn_summary and not grn_summary.grn_available else grn_result
            ),
            exceptions=[],  # Build separately below to get result_line references
        )

        # Build result_line map from saved result
        result_line_map = {
            rl.invoice_line_id: rl
            for rl in result.line_results.all()
            if rl.invoice_line_id
        }

        exceptions = self.exception_builder.build(
            result=result,
            po_result=po_result,
            header_result=header_result,
            line_result=line_result,
            grn_result=grn_result,
            result_line_map=result_line_map,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
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
