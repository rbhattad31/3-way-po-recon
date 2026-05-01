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
    ExceptionSeverity,
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
    def _build_rbac_kwargs(case: APCase) -> dict:
        """Return RBAC + trace kwargs for AgentContext created by stage executors."""
        import uuid
        rbac: dict = {
            "trace_id": getattr(case, "trace_id", "") or uuid.uuid4().hex,
        }
        try:
            from apps.agents.services.guardrails_service import AgentGuardrailsService
            actor = AgentGuardrailsService.resolve_actor(None)
            snapshot = AgentGuardrailsService.build_rbac_snapshot(actor)
            rbac["actor_user_id"] = actor.pk
            rbac["actor_primary_role"] = snapshot.get("actor_primary_role", "")
            rbac["actor_roles_snapshot"] = snapshot.get("actor_roles_snapshot", [])
            rbac["permission_source"] = snapshot.get("permission_source", "SYSTEM_AGENT")
            rbac["permission_checked"] = "cases.process"
            rbac["access_granted"] = True
        except Exception:
            rbac.setdefault("actor_primary_role", "SYSTEM_AGENT")
            rbac.setdefault("permission_source", "SYSTEM_AGENT")
            rbac.setdefault("permission_checked", "cases.process")
            rbac.setdefault("access_granted", True)
        return rbac

    @staticmethod
    def execute(case: APCase, stage_name: str) -> Optional[Dict[str, Any]]:
        """Dispatch to the correct stage handler."""
        handlers = {
            CaseStageType.INTAKE: StageExecutor._execute_intake,
            CaseStageType.EXTRACTION: StageExecutor._execute_extraction,
            CaseStageType.EXTRACTION_APPROVAL: StageExecutor._execute_extraction_approval,
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
        Extraction stage (deterministic): records extraction quality metrics.

        The Invoice Extraction Agent (OCR + GPT-4o) has already run in the
        extraction task.  Here we simply capture the confidence score on the
        case record.  Any agent-based validation (Invoice Understanding) is
        deferred to the EXCEPTION_ANALYSIS stage where the AgentOrchestrator
        decides which agents to invoke.
        """
        invoice = case.invoice
        confidence = float(invoice.extraction_confidence or 0)

        output = {
            "invoice_id": invoice.id,
            "extraction_confidence": confidence,
            "status": invoice.status,
        }

        case.extraction_confidence = confidence
        case.save(update_fields=["extraction_confidence", "updated_at"])

        if confidence < 0.5:
            output["low_confidence"] = True
            logger.info(
                "Low extraction confidence (%.2f) for case %s -- "
                "agent validation deferred to exception analysis",
                confidence, case.case_number,
            )

        CaseStateMachine.transition(case, CaseStatus.EXTRACTION_COMPLETED, PerformedByType.SYSTEM)
        return output

    @staticmethod
    def _execute_extraction_approval(case: APCase) -> Dict:
        """
        Extraction approval gate: check whether extraction has been approved.

        If the invoice is already READY_FOR_RECON (auto-approved or manually
        approved), continue the pipeline.  If the invoice is still
        PENDING_APPROVAL, pause the case at PENDING_EXTRACTION_APPROVAL --
        the approval service will resume the case when the user approves.
        """
        from apps.core.enums import InvoiceStatus

        invoice = case.invoice
        invoice.refresh_from_db(fields=["status"])

        if invoice.status == InvoiceStatus.READY_FOR_RECON:
            # Already approved -- continue to path resolution
            logger.info(
                "Case %s: extraction already approved (invoice status=%s), continuing",
                case.case_number, invoice.status,
            )
            return {
                "approved": True,
                "approval_type": "pre_approved",
                "invoice_status": invoice.status,
            }

        if invoice.status in (
            InvoiceStatus.PENDING_APPROVAL,
            InvoiceStatus.EXTRACTED,
            InvoiceStatus.VALIDATED,
        ):
            # Not yet approved -- pause the pipeline
            CaseStateMachine.transition(
                case, CaseStatus.PENDING_EXTRACTION_APPROVAL, PerformedByType.SYSTEM
            )
            logger.info(
                "Case %s: extraction pending approval (invoice status=%s), pausing pipeline",
                case.case_number, invoice.status,
            )
            return {
                "approved": False,
                "paused": True,
                "invoice_status": invoice.status,
            }

        if invoice.status == InvoiceStatus.INVALID:
            # Invalid or duplicate invoice -- reject the case
            _reason = "duplicate invoice" if invoice.is_duplicate else "invalid extraction"
            CaseStateMachine.transition(
                case, CaseStatus.PENDING_EXTRACTION_APPROVAL, PerformedByType.SYSTEM
            )
            CaseStateMachine.transition(
                case, CaseStatus.REJECTED, PerformedByType.SYSTEM
            )
            logger.info(
                "Case %s: invoice %s is %s, rejecting case",
                case.case_number, invoice.pk, _reason,
            )
            return {
                "approved": False,
                "rejected": True,
                "invoice_status": invoice.status,
                "reason": _reason,
            }

        # Unexpected status -- pause the pipeline and let a human decide
        logger.warning(
            "Case %s: invoice in unexpected status %s, pausing pipeline",
            case.case_number, invoice.status,
        )
        return {
            "approved": False,
            "paused": True,
            "invoice_status": invoice.status,
            "reason": f"unexpected invoice status: {invoice.status}",
        }

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
        PO retrieval (deterministic): lookup PO via exact, normalized, and
        vendor+amount strategies.  No LLM calls.

        If PO is not found here, a PO_NOT_FOUND exception will be created
        during reconciliation and the AgentOrchestrator (in the
        EXCEPTION_ANALYSIS stage) will invoke the PO Retrieval Agent.

        Flow:
        1. Exact + normalized PO lookup
        2. Vendor + amount discovery (deterministic fallback)
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

        # Step 2: Vendor + amount discovery (deterministic fallback)
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
            }

        logger.info(
            "PO not found deterministically for case %s -- "
            "agent retrieval deferred to exception analysis",
            case.case_number,
        )
        return {"po_found": False}

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
        run = runner.run(invoices=[invoice], triggered_by=case.created_by, tenant=case.tenant)

        # Link result to case
        result = run.results.filter(invoice=case.invoice).first()
        if result:
            case.reconciliation_result = result
            mode_to_path = {
                "TWO_WAY": ProcessingPath.TWO_WAY,
                "THREE_WAY": ProcessingPath.THREE_WAY,
                "NON_PO": ProcessingPath.NON_PO,
            }
            resolved_path = mode_to_path.get(result.reconciliation_mode, case.processing_path)
            if resolved_path:
                case.processing_path = resolved_path
            if result.reconciliation_mode:
                case.reconciliation_mode = result.reconciliation_mode
            case.save(update_fields=[
                "reconciliation_result",
                "processing_path",
                "reconciliation_mode",
                "updated_at",
            ])

        # Always advance to exception analysis — the full pipeline
        # (exception analysis -> review routing -> case summary) runs
        # for all results, including MATCHED and ERROR.  Auto-close decisions
        # are made by the exception analysis stage, not here.
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
    def _mark_invoice_reconciled(case: APCase) -> None:
        """Mark the invoice as RECONCILED when the case reaches CLOSED status."""
        if case.invoice and case.invoice.status != InvoiceStatus.RECONCILED:
            case.invoice.status = InvoiceStatus.RECONCILED
            case.invoice.save(update_fields=["status", "updated_at"])

    @staticmethod
    def _create_non_po_recon_result(case: APCase, validation_result) -> "ReconciliationResult":
        """Create a ReconciliationResult for a NON_PO case from validation checks.

        This bridges NON_PO validation into the agent pipeline so that
        exception analysis, vendor verification, and review routing agents
        can process non-PO invoices just like PO-backed ones.
        """
        from apps.reconciliation.models import (
            ReconciliationException,
            ReconciliationResult,
            ReconciliationRun,
        )
        from apps.core.enums import (
            ExceptionSeverity,
            ExceptionType,
            ReconciliationMode,
            ReconciliationRunStatus,
        )
        from django.utils import timezone

        invoice = case.invoice

        # Create a lightweight run record
        run = ReconciliationRun.objects.create(
            status=ReconciliationRunStatus.COMPLETED,
            total_invoices=1,
            triggered_by=case.created_by,
            started_at=timezone.now(),
            completed_at=timezone.now(),
            reconciliation_mode=ReconciliationMode.NON_PO,
        )

        # Determine match status from validation outcome
        status_map = {
            "PASS": MatchStatus.MATCHED,
            "NEEDS_REVIEW": MatchStatus.REQUIRES_REVIEW,
            "FAIL": MatchStatus.REQUIRES_REVIEW,
        }
        match_status = status_map.get(
            validation_result.overall_status, MatchStatus.REQUIRES_REVIEW
        )

        vendor_match = None
        vendor_check = validation_result.checks.get("vendor")
        if vendor_check:
            vendor_match = vendor_check.status == "PASS"

        recon_result = ReconciliationResult.objects.create(
            run=run,
            invoice=invoice,
            purchase_order=None,
            match_status=match_status,
            requires_review=match_status != MatchStatus.MATCHED,
            vendor_match=vendor_match,
            extraction_confidence=float(invoice.extraction_confidence or 0),
            deterministic_confidence=max(0.0, 1.0 - validation_result.risk_score),
            reconciliation_mode=ReconciliationMode.NON_PO,
            is_two_way_result=False,
            is_three_way_result=False,
            summary=(
                f"Non-PO validation: {validation_result.overall_status}. "
                f"{len(validation_result.issues)} issue(s) found."
            ),
            mode_resolution_reason="Non-PO invoice -- no PO reference",
        )

        # Map failed validation checks to ReconciliationException records
        # so the agent pipeline can reason over them.
        _CHECK_TO_EXCEPTION = {
            "vendor": (ExceptionType.VENDOR_NOT_VERIFIED, ExceptionSeverity.HIGH),
            "duplicate": (ExceptionType.DUPLICATE_INVOICE, ExceptionSeverity.HIGH),
            "mandatory_fields": (ExceptionType.MISSING_MANDATORY_FIELDS, ExceptionSeverity.HIGH),
            "tax": (ExceptionType.TAX_MISMATCH, ExceptionSeverity.MEDIUM),
            "supporting_documents": (ExceptionType.MISSING_MANDATORY_FIELDS, ExceptionSeverity.MEDIUM),
            "policy": (ExceptionType.AMOUNT_MISMATCH, ExceptionSeverity.MEDIUM),
            "budget": (ExceptionType.AMOUNT_MISMATCH, ExceptionSeverity.LOW),
        }

        for check_name, check_result in validation_result.checks.items():
            if check_result.status not in ("FAIL", "WARNING"):
                continue

            exc_info = _CHECK_TO_EXCEPTION.get(check_name)
            if exc_info:
                exc_type, severity = exc_info
            else:
                # Informational checks (spend_category, cost_center) --
                # preserve the original message but use a low-severity
                # catch-all so they are not misclassified as AMOUNT_MISMATCH.
                exc_type = ExceptionType.MISSING_MANDATORY_FIELDS
                severity = ExceptionSeverity.LOW

            ReconciliationException.objects.create(
                result=recon_result,
                exception_type=exc_type,
                severity=severity,
                message=check_result.message,
                details={
                    "source": "non_po_validation",
                    "check_name": check_name,
                    "check_status": check_result.status,
                    **(check_result.details or {}),
                },
            )

        # Update run counters
        if match_status == MatchStatus.MATCHED:
            run.matched_count = 1
        else:
            run.review_count = 1
        run.save(update_fields=["matched_count", "review_count", "updated_at"])

        exc_count = recon_result.exceptions.count()
        logger.info(
            "Created NON_PO ReconciliationResult pk=%s for case %s "
            "(status=%s, exceptions=%d)",
            recon_result.pk, case.case_number, match_status, exc_count,
        )

        return recon_result

    @staticmethod
    def _execute_non_po_validation(case: APCase) -> Dict:
        """
        Non-PO validation: deterministic checks + agent reasoning.
        """
        # Clear stale VALIDATION_RESULT artifacts from prior runs so the UI
        # does not display outdated validation checks after reprocessing.
        case.artifacts.filter(artifact_type="VALIDATION_RESULT").delete()

        # Ensure we're in the correct status (handles rerouted UNRESOLVED cases).
        # Skip if the case is already past this stage (e.g. on re-entry after
        # the pipeline previously completed path stages).
        _PAST_NON_PO = {
            "EXCEPTION_ANALYSIS_IN_PROGRESS", "READY_FOR_REVIEW", "IN_REVIEW",
            "REVIEW_COMPLETED", "CLOSED", "REJECTED", "ESCALATED", "FAILED",
        }
        if str(case.status) in _PAST_NON_PO:
            return {"skipped": True, "reason": f"case already at {case.status}"}
        if case.status != CaseStatus.NON_PO_VALIDATION_IN_PROGRESS:
            CaseStateMachine.transition(
                case, CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, PerformedByType.DETERMINISTIC
            )

        from apps.cases.services.non_po_validation_service import NonPOValidationService

        result = NonPOValidationService.validate(case)

        # Create a ReconciliationResult so the agent pipeline can run for
        # NON_PO cases too (exception analysis, vendor verification, etc.)
        recon_result = StageExecutor._create_non_po_recon_result(case, result)
        if recon_result:
            case.reconciliation_result = recon_result
            case.save(update_fields=["reconciliation_result", "updated_at"])

        # NOTE: Invoice stays at READY_FOR_RECON until the case is actually
        # closed/approved.  The RECONCILED transition happens in
        # _mark_invoice_reconciled() when the case reaches CLOSED status.

        # Advance to exception analysis
        CaseStateMachine.transition(
            case, CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, PerformedByType.DETERMINISTIC
        )

        return {
            "overall_status": result.overall_status,
            "approval_ready": result.approval_ready,
            "issues_count": len(result.issues),
            "risk_score": result.risk_score,
            "recon_result_id": recon_result.pk if recon_result else None,
        }

    @staticmethod
    def _execute_exception_analysis(case: APCase) -> Dict:
        """
        Exception analysis: delegates to existing agent orchestrator.

        De-duplication: if the case orchestrator already ran a PO_RETRIEVAL
        stage (and it did not find a PO), resolve the PO_NOT_FOUND exception
        before delegating so the agent orchestrator does not schedule a
        redundant PO_RETRIEVAL agent run.  Same for GRN_RETRIEVAL.
        """
        if case.reconciliation_result:
            from apps.reconciliation.models import ReconciliationConfig

            config = ReconciliationConfig.get_or_create_default(
                tenant=getattr(case, "tenant", None),
            )

            if not bool(getattr(config, "enable_agents", True)):
                CaseStateMachine.transition(case, CaseStatus.READY_FOR_REVIEW, PerformedByType.DETERMINISTIC)
                return {
                    "agents_executed": [],
                    "final_recommendation": None,
                    "confidence": 0.0,
                    "skipped": True,
                    "auto_closed": False,
                    "blocked_by_high_exception": StageExecutor._has_unresolved_high_exceptions(case),
                    "posting_enqueued": False,
                    "reason": "Agent pipeline disabled by tenant ReconciliationConfig.enable_agents",
                }

            from apps.agents.services.orchestrator import AgentOrchestrator

            orchestrator = AgentOrchestrator()
            orch_result = orchestrator.execute(
                case.reconciliation_result,
                tenant=getattr(case, "tenant", None),
            )
            # Note: request_user omitted -- stage executor runs inside Celery
            # or system context, so the orchestrator resolves to system-agent.

            # Handle auto-close: when the orchestrator skips agents because
            # the result is MATCHED or within the auto-close tolerance band,
            # the result's match_status is already upgraded to MATCHED.
            # Summary refresh is handled by CASE_SUMMARY stage which always runs.
            auto_closed = False
            blocking_high_exceptions = StageExecutor._has_unresolved_high_exceptions(case)

            auto_close_allowed = bool(getattr(config, "auto_close_on_match", True))

            if (
                auto_close_allowed
                and not blocking_high_exceptions
                and orch_result.skipped
                and case.reconciliation_result.match_status == MatchStatus.MATCHED
            ):
                CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.DETERMINISTIC)
                auto_closed = True
            elif (
                auto_close_allowed
                and not blocking_high_exceptions
                and orch_result.final_recommendation == "AUTO_CLOSE"
            ):
                CaseStateMachine.transition(case, CaseStatus.CLOSED, PerformedByType.AGENT)
                auto_closed = True
            elif orch_result.final_recommendation == "ESCALATE_TO_MANAGER":
                CaseStateMachine.transition(case, CaseStatus.ESCALATED, PerformedByType.AGENT)
            else:
                CaseStateMachine.transition(case, CaseStatus.READY_FOR_REVIEW, PerformedByType.AGENT)

            if blocking_high_exceptions:
                logger.info(
                    "Case %s blocked from auto-close due to unresolved HIGH exceptions",
                    case.case_number,
                )

            # When closing, mark the invoice as RECONCILED
            if auto_closed:
                StageExecutor._mark_invoice_reconciled(case)

            # When auto-closing on a clean match, mark eligible for posting
            # and enqueue the posting pipeline so the invoice appears on the
            # posting workbench.
            if auto_closed:
                case.eligible_for_posting = True
                case.save(update_fields=["eligible_for_posting", "updated_at"])
                try:
                    from apps.core.utils import dispatch_task
                    from apps.posting.tasks import prepare_posting_task
                    dispatch_task(
                        prepare_posting_task,
                        invoice_id=case.invoice_id,
                        trigger="case_auto_close",
                    )
                    logger.info(
                        "Posting pipeline enqueued for case %s (invoice %s) after auto-close",
                        case.case_number, case.invoice_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to enqueue posting pipeline for case %s after auto-close",
                        case.case_number,
                    )

            return {
                "agents_executed": orch_result.agents_executed,
                "final_recommendation": orch_result.final_recommendation,
                "confidence": orch_result.final_confidence,
                "skipped": orch_result.skipped,
                "auto_closed": auto_closed,
                "blocked_by_high_exception": blocking_high_exceptions,
                "posting_enqueued": auto_closed,
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

    @staticmethod
    def _has_unresolved_high_exceptions(case: APCase) -> bool:
        """Return True when unresolved HIGH-severity reconciliation exceptions exist."""
        if not case.reconciliation_result_id:
            return False

        from apps.reconciliation.models import ReconciliationException

        return ReconciliationException.objects.filter(
            result_id=case.reconciliation_result_id,
            resolved=False,
            severity=ExceptionSeverity.HIGH,
        ).exists()
