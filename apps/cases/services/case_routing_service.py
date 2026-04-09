"""
CaseRoutingService — determines the processing path for an APCase.

Called after extraction completes. Uses PO number presence, PO lookup,
vendor rules, and ReconciliationModeResolver to determine TWO_WAY,
THREE_WAY, or NON_PO path.
"""

import logging

from apps.core.enums import (
    DecisionSource,
    DecisionType,
    InvoiceType,
    ProcessingPath,
)
from apps.core.decorators import observed_service
from apps.cases.models import APCase, APCaseDecision

logger = logging.getLogger(__name__)


class CaseRoutingService:

    @staticmethod
    @observed_service("cases.routing.resolve_path", entity_type="APCase")
    def resolve_path(case: APCase) -> str:
        """
        Determine the processing path for a case.

        Returns:
            ProcessingPath value: TWO_WAY, THREE_WAY, NON_PO, or UNRESOLVED
        """
        invoice = case.invoice
        po_number = (invoice.po_number or "").strip()

        # 1. No PO number → NON_PO
        if not po_number:
            return CaseRoutingService._record_decision(
                case,
                ProcessingPath.NON_PO,
                DecisionSource.DETERMINISTIC,
                "No PO number on invoice",
            )

        # 2. Try PO lookup (strict: exact + normalized only, no vendor+amount)
        #    Vendor+amount discovery is deferred to the PO_RETRIEVAL stage
        #    so the PO Retrieval Agent gets a chance to run for fuzzy matching.
        from apps.reconciliation.services.po_lookup_service import POLookupService

        po_result = POLookupService().lookup(invoice, skip_vendor_amount=True)

        if not po_result.found:
            # PO number present but not found
            if invoice.extraction_confidence and invoice.extraction_confidence < 0.5:
                return CaseRoutingService._record_decision(
                    case,
                    ProcessingPath.NON_PO,
                    DecisionSource.DETERMINISTIC,
                    f"PO number '{po_number}' not found; extraction confidence below 0.5",
                )
            # Will try PO Retrieval Agent
            return CaseRoutingService._record_decision(
                case,
                ProcessingPath.UNRESOLVED,
                DecisionSource.DETERMINISTIC,
                f"PO number '{po_number}' not found; PO Retrieval Agent will attempt recovery",
            )

        # 3. PO found → use mode resolver
        from apps.reconciliation.services.mode_resolver import ReconciliationModeResolver

        resolver = ReconciliationModeResolver()
        mode_result = resolver.resolve(invoice, po_result.purchase_order)

        # If GRNs exist for this PO, force THREE_WAY regardless of mode resolver
        from apps.documents.models import GoodsReceiptNote
        if GoodsReceiptNote.objects.filter(purchase_order=po_result.purchase_order).exists():
            if mode_result.mode == "TWO_WAY":
                mode_result.mode = "THREE_WAY"
                mode_result.grn_required = True

        # Link PO to case
        case.purchase_order = po_result.purchase_order
        case.reconciliation_mode = mode_result.mode
        case.invoice_type = InvoiceType.PO_BACKED
        case.save(update_fields=["purchase_order", "reconciliation_mode", "invoice_type", "updated_at"])

        # Enrich invoice line item flags from PO data
        from apps.cases.orchestrators.stage_executor import StageExecutor
        StageExecutor._enrich_invoice_lines_from_po(invoice, po_result.purchase_order)

        if mode_result.mode == "TWO_WAY":
            path = ProcessingPath.TWO_WAY
        else:
            path = ProcessingPath.THREE_WAY

        return CaseRoutingService._record_decision(
            case,
            path,
            DecisionSource.DETERMINISTIC if mode_result.resolution_method == "default"
            else DecisionSource.POLICY,
            f"Mode resolved as {mode_result.mode} via {mode_result.resolution_method}: {mode_result.reason}",
            confidence=0.9 if mode_result.resolution_method == "policy" else 0.7,
            evidence={
                "po_number": po_result.purchase_order.po_number,
                "resolution_method": mode_result.resolution_method,
                "policy_code": mode_result.policy_code,
                "grn_required": mode_result.grn_required,
            },
        )

    @staticmethod
    def reroute_path(case: APCase, new_path: str, reason: str, source: str = DecisionSource.HUMAN, tenant=None) -> str:
        """
        Reroute a case to a different processing path.

        Used when PO Retrieval fails (reroute to NON_PO) or when a PO is
        recovered for a NON_PO case (reroute to TWO_WAY/THREE_WAY).
        """
        old_path = case.processing_path
        case.processing_path = new_path
        case.save(update_fields=["processing_path", "updated_at"])

        decision = APCaseDecision.objects.create(
            case=case,
            decision_type=DecisionType.PATH_REROUTED,
            decision_source=source,
            decision_value=new_path,
            rationale=f"Rerouted from {old_path} to {new_path}: {reason}",
            evidence={"old_path": old_path, "new_path": new_path},
            tenant=tenant,
        )

        # Override any pending agent recommendations on this case's invoice
        if source == DecisionSource.HUMAN:
            try:
                from apps.agents.models import AgentRecommendation
                from apps.agents.services.recommendation_service import RecommendationService

                invoice = getattr(case, "invoice", None)
                if invoice:
                    pending_recs = AgentRecommendation.objects.filter(
                        reconciliation_result__invoice=invoice,
                        accepted__isnull=True,
                    )
                    for rec in pending_recs:
                        RecommendationService.mark_recommendation_overridden(
                            rec.pk, decision, reason,
                        )
            except Exception:
                logger.debug("Failed to override recommendations on reroute (non-fatal)")

        logger.info("Case %s rerouted: %s -> %s (%s)", case.case_number, old_path, new_path, reason)
        return new_path

    @staticmethod
    def _record_decision(case, path, source, rationale, confidence=None, evidence=None, tenant=None) -> str:
        """Record path decision and update case."""
        case.processing_path = path
        if path == ProcessingPath.NON_PO:
            case.invoice_type = InvoiceType.NON_PO
        case.save(update_fields=["processing_path", "invoice_type", "updated_at"])

        APCaseDecision.objects.create(
            case=case,
            decision_type=DecisionType.PATH_SELECTED,
            decision_source=source,
            decision_value=path,
            confidence=confidence,
            rationale=rationale,
            evidence=evidence or {},
            tenant=tenant,
        )

        logger.info("Case %s path resolved: %s", case.case_number, path)
        return path
