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
        Extraction stage: delegates to existing extraction pipeline.

        The actual extraction (Azure DI + GPT-4o) is triggered via the existing
        extraction task. This stage monitors completion and validates quality.
        """
        # Extraction is typically already done by the time orchestrator runs
        # (triggered separately by upload task). We validate the output here.
        invoice = case.invoice

        output = {
            "invoice_id": invoice.id,
            "extraction_confidence": invoice.extraction_confidence,
            "status": invoice.status,
        }

        case.extraction_confidence = invoice.extraction_confidence
        case.save(update_fields=["extraction_confidence", "updated_at"])

        CaseStateMachine.transition(case, CaseStatus.EXTRACTION_COMPLETED, PerformedByType.SYSTEM)
        return output

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
        PO retrieval: deterministic lookup + agent fallback.
        """
        from apps.reconciliation.services.po_lookup_service import POLookupService

        invoice = case.invoice
        po_result = POLookupService().lookup(invoice)

        if po_result.found:
            case.purchase_order = po_result.purchase_order
            case.save(update_fields=["purchase_order", "updated_at"])
            return {"po_found": True, "po_number": po_result.purchase_order.po_number}

        # Agent fallback: PO Retrieval Agent
        # TODO: Invoke PO Retrieval Agent via agent orchestrator
        return {"po_found": False, "agent_attempted": False}

    @staticmethod
    def _execute_two_way_matching(case: APCase) -> Dict:
        """
        2-Way matching: reuses existing ReconciliationRunnerService.
        """
        from apps.reconciliation.services.runner_service import ReconciliationRunnerService

        runner = ReconciliationRunnerService()
        run = runner.run(invoices=[case.invoice], triggered_by=case.created_by)

        # Link result to case
        result = run.results.filter(invoice=case.invoice).first()
        if result:
            case.reconciliation_result = result
            case.save(update_fields=["reconciliation_result", "updated_at"])

            if result.match_status == MatchStatus.MATCHED:
                CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.DETERMINISTIC)

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
        from apps.cases.services.non_po_validation_service import NonPOValidationService

        result = NonPOValidationService.validate(case)

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

            if orch_result.final_recommendation == "AUTO_CLOSE":
                CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.AGENT)
            elif orch_result.final_recommendation == "ESCALATE_TO_MANAGER":
                CaseStateMachine.transition(case, CaseStatus.ESCALATED, PerformedByType.AGENT)
            else:
                CaseStateMachine.transition(case, CaseStatus.READY_FOR_REVIEW, PerformedByType.AGENT)

            return {
                "agents_executed": orch_result.agents_executed,
                "final_recommendation": orch_result.final_recommendation,
                "confidence": orch_result.confidence,
            }

        # Non-PO cases without reconciliation result
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
