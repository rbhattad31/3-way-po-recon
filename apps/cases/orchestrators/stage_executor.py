"""
StageExecutor — dispatches individual case stages to the correct service or agent.

Each stage delegates to either a deterministic service, an agent, or both.
Returns output payload dict for persistence in APCaseStage.output_payload.
"""

import logging
from typing import Any, Dict, Optional

from apps.cases.models import APCase
from apps.cases.state_machine.case_state_machine import CaseStateMachine
from apps.core.enums import (
    CaseStageType,
    CaseStatus,
    InvoiceStatus,
    MatchStatus,
    PerformedByType,
    ProcessingPath,
)

logger = logging.getLogger(__name__)


class StageExecutor:
    """
    Routes stage execution to the appropriate service/agent.

    Each _execute_* method follows the pattern:
    1. Run deterministic service (if applicable)
    2. Run agent (if applicable and enabled)
    3. Update case status
    4. Return output payload
    """

    @staticmethod
    def execute(case: APCase, stage_name: str) -> Optional[Dict[str, Any]]:
        """Dispatch to the correct stage handler."""
        handlers = {
            CaseStageType.INTAKE: StageExecutor._execute_intake,
            CaseStageType.EXTRACTION: StageExecutor._execute_extraction,
            CaseStageType.PATH_RESOLUTION: StageExecutor._execute_path_resolution,
            CaseStageType.PO_RETRIEVAL: StageExecutor._execute_po_retrieval,
            CaseStageType.TWO_WAY_MATCHING: StageExecutor._execute_two_way_matching,
            CaseStageType.THREE_WAY_MATCHING: StageExecutor._execute_three_way_matching,
            CaseStageType.GRN_ANALYSIS: StageExecutor._execute_grn_analysis,
            CaseStageType.NON_PO_VALIDATION: StageExecutor._execute_non_po_validation,
            CaseStageType.EXCEPTION_ANALYSIS: StageExecutor._execute_exception_analysis,
            CaseStageType.REVIEW_ROUTING: StageExecutor._execute_review_routing,
            CaseStageType.CASE_SUMMARY: StageExecutor._execute_case_summary,
        }

        handler = handlers.get(stage_name)
        if not handler:
            logger.warning("No handler for stage %s", stage_name)
            return None

        return handler(case)

    @staticmethod
    def _execute_intake(case: APCase) -> Dict:
        """
        Intake stage: validate upload, classify document.

        Deterministic: file type validation, basic header checks.
        Agent (optional): Invoice Intake Agent for complex classification.
        """
        CaseStateMachine.transition(case, CaseStatus.INTAKE_IN_PROGRESS, PerformedByType.SYSTEM)

        invoice = case.invoice
        output = {
            "invoice_id": invoice.id,
            "has_vendor": invoice.vendor is not None,
            "has_po_number": bool((invoice.po_number or "").strip()),
            "document_uploaded": True,
        }

        # Advance to extraction
        CaseStateMachine.transition(case, CaseStatus.EXTRACTION_IN_PROGRESS, PerformedByType.SYSTEM)
        return output

    @staticmethod
    def _execute_extraction(case: APCase) -> Dict:
        """
        Extraction stage: records quality and runs Invoice Understanding Agent
        for low-confidence extractions.

        The Invoice Extraction Agent (OCR + GPT-4o) has already run in the
        extraction task.  Here we capture confidence and, if it is below
        threshold, invoke the Invoice Understanding Agent to validate.
        """
        from apps.core.constants import EXTRACTION_CONFIDENCE_THRESHOLD

        invoice = case.invoice
        confidence = float(invoice.extraction_confidence or 0)

        output = {
            "invoice_id": invoice.id,
            "extraction_confidence": confidence,
            "status": invoice.status,
        }

        case.extraction_confidence = confidence
        case.save(update_fields=["extraction_confidence", "updated_at"])

        # Low confidence → run Invoice Understanding Agent to validate
        if confidence < EXTRACTION_CONFIDENCE_THRESHOLD:
            agent_output = StageExecutor._run_invoice_understanding_agent(case)
            output["agent_analysis"] = agent_output

        CaseStateMachine.transition(case, CaseStatus.EXTRACTION_COMPLETED, PerformedByType.SYSTEM)
        return output

    @staticmethod
    def _run_invoice_understanding_agent(case: APCase) -> Dict:
        """Invoke the Invoice Understanding Agent to validate low-confidence extraction."""
        try:
            from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
            from apps.agents.services.base_agent import AgentContext
            from apps.core.enums import AgentRunStatus, AgentType
            from apps.reconciliation.models import ReconciliationConfig

            # Respect the enable_agents config flag
            config = ReconciliationConfig.objects.filter(is_default=True).first()
            if config and not config.enable_agents:
                logger.info("Agents disabled — skipping Invoice Understanding for case %s", case.case_number)
                return {"skipped": True, "reason": "agents_disabled"}

            agent_cls = AGENT_CLASS_REGISTRY.get(AgentType.INVOICE_UNDERSTANDING)
            if not agent_cls:
                logger.warning("Invoice Understanding Agent class not found in registry")
                return {"skipped": True, "reason": "agent_not_registered"}

            invoice = case.invoice
            ctx = AgentContext(
                reconciliation_result=None,
                invoice_id=invoice.pk,
                po_number=invoice.po_number,
                extra={
                    "extraction_confidence": float(invoice.extraction_confidence or 0),
                    "vendor_name": invoice.vendor.name if invoice.vendor else invoice.raw_vendor_name,
                    "total_amount": str(invoice.total_amount) if invoice.total_amount else "unknown",
                    "case_number": case.case_number,
                    "stage": "extraction_validation",
                },
            )

            agent = agent_cls()
            agent_run = agent.run(ctx)

            if agent_run.status != AgentRunStatus.COMPLETED:
                logger.warning(
                    "Invoice Understanding Agent did not complete for case %s: status=%s",
                    case.case_number, agent_run.status,
                )
                return {"completed": False, "agent_run_id": agent_run.pk}

            output = agent_run.output_payload or {}
            recommendation = output.get("recommendation_type", "")
            confidence = output.get("confidence", 0)

            logger.info(
                "Invoice Understanding Agent for case %s: recommendation=%s confidence=%.2f",
                case.case_number, recommendation, confidence,
            )
            return {
                "completed": True,
                "agent_run_id": agent_run.pk,
                "recommendation": recommendation,
                "confidence": confidence,
                "reasoning": output.get("reasoning", ""),
            }

        except Exception:
            logger.exception("Invoice Understanding Agent error for case %s", case.case_number)
            return {"completed": False, "error": True}

    @staticmethod
    def _execute_path_resolution(case: APCase) -> Dict:
        """
        Path resolution: determine TWO_WAY, THREE_WAY, or NON_PO.
        """
        CaseStateMachine.transition(
            case, CaseStatus.PATH_RESOLUTION_IN_PROGRESS, PerformedByType.SYSTEM
        )

        from apps.cases.services.case_routing_service import CaseRoutingService

        path = CaseRoutingService.resolve_path(case)

        status_map = {
            ProcessingPath.TWO_WAY: CaseStatus.TWO_WAY_IN_PROGRESS,
            ProcessingPath.THREE_WAY: CaseStatus.THREE_WAY_IN_PROGRESS,
            ProcessingPath.NON_PO: CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
        }

        next_status = status_map.get(path)
        if next_status:
            CaseStateMachine.transition(case, next_status, PerformedByType.DETERMINISTIC)

        return {"resolved_path": path}

    @staticmethod
    def _execute_po_retrieval(case: APCase) -> Dict:
        """
        PO retrieval: deterministic lookup + PO Retrieval Agent + vendor+amount fallback.

        Flow:
        1. Exact + normalized PO lookup (quick deterministic check)
        2. PO Retrieval Agent (LLM-based fuzzy matching)
        3. Vendor + amount discovery (deterministic fallback)
        """
        from apps.reconciliation.services.po_lookup_service import POLookupService

        invoice = case.invoice
        lookup_svc = POLookupService()

        # Step 1: strict lookup (exact + normalized only)
        po_result = lookup_svc.lookup(invoice, skip_vendor_amount=True)
        if po_result.found:
            case.purchase_order = po_result.purchase_order
            case.save(update_fields=["purchase_order", "updated_at"])
            StageExecutor._enrich_invoice_lines_from_po(invoice, po_result.purchase_order)
            return {
                "po_found": True,
                "po_number": po_result.purchase_order.po_number,
                "method": po_result.lookup_method,
            }

        # Step 2: Invoke PO Retrieval Agent (LLM-based fuzzy matching)
        agent_result = StageExecutor._run_po_retrieval_agent(case)
        if agent_result.get("po_found"):
            return agent_result

        # Step 3: Vendor + amount discovery (deterministic fallback)
        po_result = lookup_svc._discover_by_vendor_amount(invoice)
        if po_result.found:
            case.purchase_order = po_result.purchase_order
            case.save(update_fields=["purchase_order", "updated_at"])
            StageExecutor._enrich_invoice_lines_from_po(invoice, po_result.purchase_order)
            logger.info(
                "PO found via vendor+amount fallback for case %s: PO %s",
                case.case_number, po_result.purchase_order.po_number,
            )
            return {
                "po_found": True,
                "po_number": po_result.purchase_order.po_number,
                "method": "vendor_amount_fallback",
                "agent_attempted": True,
            }

        return {"po_found": False, "agent_attempted": True}

    @staticmethod
    def _run_po_retrieval_agent(case: APCase) -> Dict:
        """Invoke the PO Retrieval Agent to attempt fuzzy PO matching."""
        try:
            from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
            from apps.agents.services.base_agent import AgentContext
            from apps.core.enums import AgentRunStatus, AgentType
            from apps.documents.models import PurchaseOrder

            agent_cls = AGENT_CLASS_REGISTRY.get(AgentType.PO_RETRIEVAL)
            if not agent_cls:
                logger.warning("PO Retrieval Agent class not found in registry")
                return {"po_found": False, "agent_attempted": False}

            invoice = case.invoice
            ctx = AgentContext(
                reconciliation_result=None,
                invoice_id=invoice.pk,
                po_number=invoice.po_number,
                extra={
                    "vendor_name": invoice.vendor.name if invoice.vendor else invoice.raw_vendor_name,
                    "total_amount": str(invoice.total_amount) if invoice.total_amount else "unknown",
                    "case_number": case.case_number,
                },
            )

            agent = agent_cls()
            agent_run = agent.run(ctx)

            if agent_run.status != AgentRunStatus.COMPLETED:
                logger.warning(
                    "PO Retrieval Agent did not complete for case %s: status=%s",
                    case.case_number, agent_run.status,
                )
                return {"po_found": False, "agent_attempted": True}

            # Parse agent output for a found PO number
            output = agent_run.output_payload or {}
            evidence = output.get("evidence", {})
            found_po_number = (
                evidence.get("po_number")
                or evidence.get("found_po_number")
                or evidence.get("matched_po_number")
            )

            if found_po_number:
                po = PurchaseOrder.objects.filter(po_number=found_po_number).first()
                if po:
                    case.purchase_order = po
                    case.save(update_fields=["purchase_order", "updated_at"])
                    StageExecutor._enrich_invoice_lines_from_po(case.invoice, po)
                    logger.info(
                        "PO Retrieval Agent found PO %s for case %s",
                        po.po_number, case.case_number,
                    )
                    return {
                        "po_found": True,
                        "po_number": po.po_number,
                        "method": "agent",
                        "agent_attempted": True,
                        "agent_confidence": output.get("confidence"),
                    }

            logger.info("PO Retrieval Agent did not find a PO for case %s", case.case_number)
            return {"po_found": False, "agent_attempted": True}

        except Exception:
            logger.exception("PO Retrieval Agent error for case %s", case.case_number)
            return {"po_found": False, "agent_attempted": True, "agent_error": True}

    @staticmethod
    def _enrich_invoice_lines_from_po(invoice, purchase_order) -> None:
        """Copy is_service_item/is_stock_item/item_category from PO lines
        to matching invoice lines when the invoice line flags are blank.
        """
        from apps.documents.models import InvoiceLineItem, PurchaseOrderLineItem

        inv_lines = list(
            InvoiceLineItem.objects.filter(invoice=invoice)
            .filter(is_service_item__isnull=True, is_stock_item__isnull=True)
        )
        if not inv_lines:
            return

        po_lines = {
            li.line_number: li
            for li in PurchaseOrderLineItem.objects.filter(purchase_order=purchase_order)
        }
        updated = []
        for il in inv_lines:
            po_line = po_lines.get(il.line_number)
            if not po_line:
                continue
            changed = False
            if po_line.is_service_item is not None and il.is_service_item is None:
                il.is_service_item = po_line.is_service_item
                changed = True
            if po_line.is_stock_item is not None and il.is_stock_item is None:
                il.is_stock_item = po_line.is_stock_item
                changed = True
            if po_line.item_category and not il.item_category:
                il.item_category = po_line.item_category
                changed = True
            if changed:
                updated.append(il)

        if updated:
            InvoiceLineItem.objects.bulk_update(
                updated, ["is_service_item", "is_stock_item", "item_category", "updated_at"],
            )
            logger.info(
                "Enriched %d invoice line items from PO %s for invoice %s",
                len(updated), purchase_order.po_number, invoice.pk,
            )

    @staticmethod
    def _execute_two_way_matching(case: APCase) -> Dict:
        """
        2-Way matching: reuses existing ReconciliationRunnerService.
        """
        from apps.reconciliation.services.runner_service import ReconciliationRunnerService

        # Clear stale VALIDATION_RESULT artifacts from prior runs so the UI
        # does not display outdated validation checks after reprocessing.
        case.artifacts.filter(artifact_type="VALIDATION_RESULT").delete()

        # Sync invoice PO number if the case has a linked PO from PO_RETRIEVAL
        # so the runner's own PO lookup can find it.
        invoice = case.invoice
        if case.purchase_order and invoice.po_number != case.purchase_order.po_number:
            invoice.po_number = case.purchase_order.po_number
            invoice.normalized_po_number = case.purchase_order.normalized_po_number
            invoice.save(update_fields=["po_number", "normalized_po_number", "updated_at"])

        runner = ReconciliationRunnerService()
        run = runner.run(invoices=[invoice], triggered_by=case.created_by)

        # Link result to case
        result = run.results.filter(invoice=case.invoice).first()
        if result:
            case.reconciliation_result = result
            case.save(update_fields=["reconciliation_result", "updated_at"])

            # Always advance to exception analysis — the full pipeline
            # (exception analysis -> review routing -> case summary) runs
            # for all results, including MATCHED. Auto-close decisions are
            # made by the exception analysis stage, not here.
            CaseStateMachine.transition(
                case, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, PerformedByType.DETERMINISTIC
            )

        return {
            "run_id": run.id,
            "match_status": result.match_status if result else None,
            "exceptions_count": result.exceptions.count() if result else 0,
        }

    @staticmethod
    def _execute_three_way_matching(case: APCase) -> Dict:
        """
        3-Way matching: reuses existing ReconciliationRunnerService.
        Identical to 2-way — the runner handles mode selection internally.
        """
        return StageExecutor._execute_two_way_matching(case)

    @staticmethod
    def _execute_grn_analysis(case: APCase) -> Dict:
        """
        GRN analysis: delegates to GRN Specialist Agent.
        """
        # TODO: Invoke GRN Retrieval Agent via agent orchestrator
        return {"grn_analysis": "pending_agent_integration"}

    @staticmethod
    def _execute_non_po_validation(case: APCase) -> Dict:
        """
        Non-PO validation: deterministic checks + agent reasoning.
        """
        # Ensure we're in the correct status (handles rerouted UNRESOLVED cases)
        if case.status != CaseStatus.NON_PO_VALIDATION_IN_PROGRESS:
            CaseStateMachine.transition(
                case, CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, PerformedByType.DETERMINISTIC
            )

        from apps.cases.services.non_po_validation_service import NonPOValidationService

        result = NonPOValidationService.validate(case)

        # Transition invoice status -- non-PO cases skip reconciliation,
        # so we mark the invoice as RECONCILED here (validation complete).
        if case.invoice and case.invoice.status != InvoiceStatus.RECONCILED:
            case.invoice.status = InvoiceStatus.RECONCILED
            case.invoice.save(update_fields=["status", "updated_at"])

        # Advance to exception analysis
        CaseStateMachine.transition(
            case, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, PerformedByType.DETERMINISTIC
        )

        return {
            "overall_status": result.overall_status,
            "approval_ready": result.approval_ready,
            "issues_count": len(result.issues),
            "risk_score": result.risk_score,
        }

    @staticmethod
    def _execute_exception_analysis(case: APCase) -> Dict:
        """
        Exception analysis: delegates to existing agent orchestrator.
        """
        if case.reconciliation_result:
            from apps.agents.services.orchestrator import AgentOrchestrator

            orchestrator = AgentOrchestrator()
            orch_result = orchestrator.execute(case.reconciliation_result)
            # Note: request_user omitted — stage executor runs inside Celery
            # or system context, so the orchestrator resolves to system-agent.

            # Handle auto-close: when the orchestrator skips agents because
            # the result is MATCHED or within the auto-close tolerance band,
            # the result's match_status is already upgraded to MATCHED.
            # Summary refresh is handled by CASE_SUMMARY stage which always runs.
            if orch_result.skipped and case.reconciliation_result.match_status == MatchStatus.MATCHED:
                CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.DETERMINISTIC)
            elif orch_result.final_recommendation == "AUTO_CLOSE":
                CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.AGENT)
            elif orch_result.final_recommendation == "ESCALATE_TO_MANAGER":
                CaseStateMachine.transition(case, CaseStatus.ESCALATED, PerformedByType.AGENT)
            else:
                CaseStateMachine.transition(case, CaseStatus.READY_FOR_REVIEW, PerformedByType.AGENT)

            return {
                "agents_executed": orch_result.agents_executed,
                "final_recommendation": orch_result.final_recommendation,
                "confidence": orch_result.final_confidence,
                "skipped": orch_result.skipped,
                "auto_closed": orch_result.skipped and case.reconciliation_result.match_status == MatchStatus.MATCHED,
            }

        # Non-PO cases without reconciliation result — send to review deterministically
        CaseStateMachine.transition(case, CaseStatus.READY_FOR_REVIEW, PerformedByType.DETERMINISTIC)
        return {"non_po": True, "sent_to_review": True}

    @staticmethod
    def _execute_review_routing(case: APCase) -> Dict:
        """
        Review routing: create assignment using CaseAssignmentService.
        """
        from apps.cases.services.case_assignment_service import CaseAssignmentService

        assignment = CaseAssignmentService.assign_for_review(case)
        return {
            "assignment_id": assignment.id,
            "assigned_role": assignment.assigned_role,
            "queue": assignment.queue_name,
        }

    @staticmethod
    def _execute_case_summary(case: APCase) -> Dict:
        """
        Case summary: build deterministic summary, optionally invoke Case Summary Agent.
        """
        from apps.cases.services.case_summary_service import CaseSummaryService

        summary = CaseSummaryService.build_summary(case)
        return {"summary_id": summary.id, "summary_length": len(summary.latest_summary)}
