"""Agent feedback service — applies agent findings back to reconciliation results.

When the PO Retrieval Agent (or GRN Retrieval Agent) locates a PO/GRN that
the deterministic engine missed, this service:
  1. Links the found PO to the ReconciliationResult and Invoice
  2. Re-runs deterministic matching (header / line / GRN)
  3. Re-classifies and updates the match status
  4. Rebuilds exceptions based on the new match
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from apps.core.enums import MatchStatus
from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationException,
    ReconciliationResult,
)
from apps.reconciliation.services.classification_service import ClassificationService
from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
from apps.reconciliation.services.grn_lookup_service import GRNLookupService
from apps.reconciliation.services.grn_match_service import GRNMatchService
from apps.reconciliation.services.header_match_service import HeaderMatchService
from apps.reconciliation.services.line_match_service import LineMatchService
from apps.reconciliation.services.po_lookup_service import POLookupResult
from apps.reconciliation.services.tolerance_engine import ToleranceEngine

logger = logging.getLogger(__name__)


class AgentFeedbackService:
    """Applies agent-recovered PO findings and re-runs deterministic matching."""

    def __init__(self, config: Optional[ReconciliationConfig] = None):
        self.config = config or self._default_config()
        self.tolerance = ToleranceEngine(self.config)
        self.header_match = HeaderMatchService(self.tolerance)
        self.line_match = LineMatchService(self.tolerance)
        self.grn_lookup = GRNLookupService()
        self.grn_match = GRNMatchService()
        self.classifier = ClassificationService()
        self.exception_builder = ExceptionBuilderService()

    @transaction.atomic
    def apply_found_po(
        self,
        result: ReconciliationResult,
        po: PurchaseOrder,
        agent_run_id: Optional[int] = None,
    ) -> MatchStatus:
        """Link *po* to the result and re-run deterministic matching.

        Returns the new match status after re-reconciliation.
        """
        invoice = result.invoice

        # 1. Link PO to result and invoice
        result.purchase_order = po
        invoice.po_number = po.po_number
        invoice.save(update_fields=["po_number", "updated_at"])

        # 2. Build a synthetic POLookupResult
        po_result = POLookupResult(
            found=True,
            purchase_order=po,
            lookup_method="agent_recovered",
        )

        # 3. Re-run header, line, and GRN matching
        header_result = self.header_match.match(invoice, po)
        line_result = self.line_match.match(invoice, po)

        grn_summary = self.grn_lookup.lookup(po)
        grn_result = None
        if grn_summary.grn_available and line_result:
            grn_result = self.grn_match.match(line_result.pairs, grn_summary)

        # 4. Re-classify
        new_status = self.classifier.classify(
            po_result=po_result,
            header_result=header_result,
            line_result=line_result,
            grn_result=grn_result,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
        )

        # 5. Remove old exceptions and line results, rebuild
        result.exceptions.all().delete()
        result.line_results.all().delete()

        # Save updated header-level fields
        tc = header_result.total_comparison if header_result and header_result.total_comparison else None
        result.match_status = new_status
        result.requires_review = new_status in (
            MatchStatus.PARTIAL_MATCH,
            MatchStatus.REQUIRES_REVIEW,
        )
        result.vendor_match = header_result.vendor_match if header_result else None
        result.currency_match = header_result.currency_match if header_result else None
        result.po_total_match = header_result.po_total_match if header_result else None
        result.invoice_total_vs_po = tc.difference if tc else None
        result.total_amount_difference = tc.difference if tc else None
        result.total_amount_difference_pct = tc.difference_pct if tc else None
        result.grn_available = grn_result.grn_available if grn_result else (
            grn_summary.grn_available if grn_summary else False
        )
        result.grn_fully_received = grn_result.fully_received if grn_result else None
        result.summary = (
            f"Agent-recovered PO {po.po_number} → re-reconciled → {new_status}"
        )
        result.save()

        # Re-create line results
        from apps.reconciliation.services.result_service import ReconciliationResultService
        result_svc = ReconciliationResultService()
        if line_result:
            result_svc._save_line_results(result, line_result)

        # Re-build exceptions
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
            ReconciliationException.objects.bulk_create(exceptions)

        # 6. Auto-create review if needed
        if new_status == MatchStatus.REQUIRES_REVIEW:
            from apps.reviews.services import ReviewWorkflowService
            ReviewWorkflowService.create_assignment(
                result=result,
                priority=3 if exceptions else 5,
                notes=f"Agent-recovered PO {po.po_number}; re-reconciled with {len(exceptions)} exception(s).",
            )

        # 7. Audit trail
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=invoice.pk,
            event_type=AuditEventType.RECONCILIATION_COMPLETED,
            description=(
                f"Agent recovered PO {po.po_number} and re-reconciled: "
                f"{result.match_status} → {new_status}"
            ),
            metadata={
                "agent_run_id": agent_run_id,
                "recovered_po": po.po_number,
                "new_match_status": new_status,
                "old_match_status": MatchStatus.UNMATCHED,
            },
        )

        logger.info(
            "Agent feedback applied for result %s: PO %s linked, new status=%s",
            result.pk, po.po_number, new_status,
        )
        return new_status

    @staticmethod
    def _default_config() -> ReconciliationConfig:
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config:
            return config
        return ReconciliationConfig.objects.create(
            name="Default",
            is_default=True,
        )
