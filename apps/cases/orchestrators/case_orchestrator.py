"""
CaseOrchestrator — master orchestrator driving the APCase through its stages.

Coordinates deterministic services and agent reasoning in the correct sequence
for each processing path (TWO_WAY, THREE_WAY, NON_PO).
"""

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.cases.models import APCase, APCaseDecision, APCaseStage
from apps.cases.services.case_routing_service import CaseRoutingService
from apps.cases.state_machine.case_state_machine import CaseStateMachine
from apps.core.enums import (
    CaseStageType,
    CaseStatus,
    DecisionType,
    PerformedByType,
    ProcessingPath,
    StageStatus,
)
from apps.core.decorators import observed_service
from apps.core.metrics import MetricsService

logger = logging.getLogger(__name__)

# Stage sequences per processing path
PATH_STAGES = {
    ProcessingPath.TWO_WAY: [
        CaseStageType.INTAKE,
        CaseStageType.EXTRACTION,
        CaseStageType.PATH_RESOLUTION,
        CaseStageType.PO_RETRIEVAL,
        CaseStageType.TWO_WAY_MATCHING,
        CaseStageType.EXCEPTION_ANALYSIS,
        CaseStageType.REVIEW_ROUTING,
        CaseStageType.CASE_SUMMARY,
    ],
    ProcessingPath.THREE_WAY: [
        CaseStageType.INTAKE,
        CaseStageType.EXTRACTION,
        CaseStageType.PATH_RESOLUTION,
        CaseStageType.PO_RETRIEVAL,
        CaseStageType.THREE_WAY_MATCHING,
        CaseStageType.GRN_ANALYSIS,  # conditional
        CaseStageType.EXCEPTION_ANALYSIS,
        CaseStageType.REVIEW_ROUTING,
        CaseStageType.CASE_SUMMARY,
    ],
    ProcessingPath.NON_PO: [
        CaseStageType.INTAKE,
        CaseStageType.EXTRACTION,
        CaseStageType.PATH_RESOLUTION,
        CaseStageType.NON_PO_VALIDATION,
        CaseStageType.EXCEPTION_ANALYSIS,
        CaseStageType.REVIEW_ROUTING,
        CaseStageType.CASE_SUMMARY,
    ],
}

# Map stage → CaseStatus when entering that stage
STAGE_TO_STATUS = {
    CaseStageType.INTAKE: CaseStatus.INTAKE_IN_PROGRESS,
    CaseStageType.EXTRACTION: CaseStatus.EXTRACTION_IN_PROGRESS,
    CaseStageType.PATH_RESOLUTION: CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
    CaseStageType.PO_RETRIEVAL: CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
    CaseStageType.TWO_WAY_MATCHING: CaseStatus.TWO_WAY_IN_PROGRESS,
    CaseStageType.THREE_WAY_MATCHING: CaseStatus.THREE_WAY_IN_PROGRESS,
    CaseStageType.GRN_ANALYSIS: CaseStatus.GRN_ANALYSIS_IN_PROGRESS,
    CaseStageType.NON_PO_VALIDATION: CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
    CaseStageType.EXCEPTION_ANALYSIS: CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
    CaseStageType.REVIEW_ROUTING: CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
    CaseStageType.CASE_SUMMARY: CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
}


class CaseOrchestrator:
    """
    Drives an APCase through its processing stages.

    Usage:
        orchestrator = CaseOrchestrator(case)
        orchestrator.run()               # Run from current position
        orchestrator.run_from(stage)     # Reprocess from a specific stage
    """

    def __init__(self, case: APCase):
        self.case = case
        self._lf_trace = None
        self._lf_trace_id = None
        self._stage_index = 0

    @observed_service("cases.orchestrator.run", audit_event="CASE_PROCESSING_STARTED", entity_type="APCase")
    def run(self, lf_trace=None, lf_trace_id: Optional[str] = None) -> APCase:
        """Execute the case from its current position through completion."""
        self._lf_trace = lf_trace
        self._lf_trace_id = lf_trace_id
        self._stage_index = 0
        logger.info("Orchestrating case %s (status=%s, path=%s)",
                     self.case.case_number, self.case.status, self.case.processing_path)

        # Sync vendor/PO from invoice in case they were linked after case creation
        self._sync_from_invoice()

        try:
            # Stage 1: Intake (if not yet started)
            if self.case.status == CaseStatus.NEW:
                self._execute_stage(CaseStageType.INTAKE)

            # Stage 2: Extraction (if not yet done)
            # Intake advances status to EXTRACTION_IN_PROGRESS, so check that
            if self.case.status == CaseStatus.EXTRACTION_IN_PROGRESS:
                self._execute_stage(CaseStageType.EXTRACTION)

            # Stage 3: Path resolution (if extraction completed)
            if self.case.status == CaseStatus.EXTRACTION_COMPLETED:
                self._execute_stage(CaseStageType.PATH_RESOLUTION)

            # Stage 4+: Path-specific processing
            self._execute_path_stages()

            # -- Langfuse: final case-level trace metadata and scores
            try:
                from apps.core.langfuse_client import update_trace_safe, score_trace_safe
                _is_closed = self.case.status in (CaseStatus.CLOSED, CaseStatus.AUTO_CLOSED) if hasattr(CaseStatus, "AUTO_CLOSED") else self.case.status == CaseStatus.CLOSED
                _is_terminal = CaseStateMachine.is_terminal(self.case.status)
                update_trace_safe(self._lf_trace, metadata={
                    "final_status": self.case.status,
                    "final_stage": self.case.current_stage or "",
                    "processing_path": self.case.processing_path or "",
                    "stages_executed": self._stage_index,
                    "reconciliation_result_id": self.case.reconciliation_result_id,
                    "reconciliation_mode": self.case.reconciliation_mode or "",
                }, is_root=True)
                score_trace_safe(
                    self._lf_trace_id, "case_stages_executed",
                    float(self._stage_index),
                    comment=f"path={self.case.processing_path}",
                )
                score_trace_safe(
                    self._lf_trace_id, "case_closed",
                    1.0 if _is_closed else 0.0,
                    comment=f"status={self.case.status}",
                )
                score_trace_safe(
                    self._lf_trace_id, "case_terminal",
                    1.0 if _is_terminal else 0.0,
                )
            except Exception:
                pass

        except Exception:
            logger.exception("Case %s orchestration failed", self.case.case_number)
            self.case.status = CaseStatus.FAILED
            self.case.save(update_fields=["status", "updated_at"])
            try:
                from apps.core.langfuse_client import score_trace_safe
                score_trace_safe(self._lf_trace_id, "case_processing_success", 0.0, comment="orchestration_failed")
            except Exception:
                pass
            raise

        return self.case

    # Map stage → status the case should be in before that stage runs
    STAGE_RESET_STATUS = {
        CaseStageType.INTAKE: CaseStatus.NEW,
        CaseStageType.EXTRACTION: CaseStatus.EXTRACTION_IN_PROGRESS,
        CaseStageType.PATH_RESOLUTION: CaseStatus.EXTRACTION_COMPLETED,
        CaseStageType.PO_RETRIEVAL: CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
        CaseStageType.TWO_WAY_MATCHING: CaseStatus.TWO_WAY_IN_PROGRESS,
        CaseStageType.THREE_WAY_MATCHING: CaseStatus.THREE_WAY_IN_PROGRESS,
        CaseStageType.GRN_ANALYSIS: CaseStatus.GRN_ANALYSIS_IN_PROGRESS,
        CaseStageType.NON_PO_VALIDATION: CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
        CaseStageType.EXCEPTION_ANALYSIS: CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
        CaseStageType.REVIEW_ROUTING: CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
        CaseStageType.CASE_SUMMARY: CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
    }

    def run_from(self, stage: str, lf_trace=None, lf_trace_id: Optional[str] = None) -> APCase:
        """Reprocess from a specific stage forward."""
        self._lf_trace = lf_trace
        self._lf_trace_id = lf_trace_id
        self._stage_index = 0
        logger.info("Reprocessing case %s from stage %s", self.case.case_number, stage)

        # Mark subsequent stages as skipped
        self.case.stages.filter(
            stage_name=stage, stage_status__in=[StageStatus.COMPLETED, StageStatus.FAILED]
        ).update(stage_status=StageStatus.SKIPPED)

        # Reset case status to the correct pre-stage status so the pipeline
        # can proceed through the state machine without invalid transitions.
        reset_status = self.STAGE_RESET_STATUS.get(stage)
        self.case.current_stage = stage
        if reset_status:
            self.case.status = reset_status
            self.case.save(update_fields=["current_stage", "status", "updated_at"])
        else:
            self.case.save(update_fields=["current_stage", "updated_at"])

        return self.run(lf_trace=self._lf_trace, lf_trace_id=self._lf_trace_id)

    def _execute_path_stages(self):
        """Execute the path-specific stages based on resolved processing path."""
        path = self.case.processing_path

        # On reprocess: if a PO was linked after original classification
        # (e.g. PO seeded/imported since last run), re-resolve the path
        # so the case moves from NON_PO/UNRESOLVED to TWO_WAY/THREE_WAY.
        if path in (ProcessingPath.NON_PO, ProcessingPath.UNRESOLVED) and self.case.purchase_order:
            path = self._resolve_path_from_linked_po()
            CaseRoutingService.reroute_path(
                self.case, path,
                f"PO {self.case.purchase_order.po_number} now linked; re-resolved as {path}",
            )

        if path == ProcessingPath.UNRESOLVED:
            # Try PO Retrieval Agent, then re-resolve
            self._execute_stage(CaseStageType.PO_RETRIEVAL)
            self.case.refresh_from_db()

            # If PO_RETRIEVAL linked a PO, resolve path using mode resolver
            # directly (bypasses PO number lookup which may still have noisy value)
            if self.case.purchase_order:
                path = self._resolve_path_from_linked_po()
            else:
                CaseRoutingService.resolve_path(self.case)
                self.case.refresh_from_db()
                path = self.case.processing_path

            # Transition the case status to match the resolved path via state machine
            _PATH_IN_PROGRESS = {
                ProcessingPath.TWO_WAY: CaseStatus.TWO_WAY_IN_PROGRESS,
                ProcessingPath.THREE_WAY: CaseStatus.THREE_WAY_IN_PROGRESS,
                ProcessingPath.NON_PO: CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
            }
            target_status = _PATH_IN_PROGRESS.get(path)
            if target_status and self.case.status != target_status:
                CaseStateMachine.transition(self.case, target_status, PerformedByType.DETERMINISTIC)

        if path == ProcessingPath.TWO_WAY:
            self._run_two_way_path()
        elif path == ProcessingPath.THREE_WAY:
            self._run_three_way_path()
        elif path == ProcessingPath.NON_PO:
            self._run_non_po_path()
        else:
            # Still unresolved after PO retrieval — decide based on whether
            # the invoice references a PO number.  An invoice that *has* a PO
            # number is PO-backed (TWO_WAY at minimum) even if the PO record
            # doesn't exist in the system yet.
            po_number = (self.case.invoice.po_number or "").strip()
            if po_number:
                CaseRoutingService.reroute_path(
                    self.case, ProcessingPath.TWO_WAY,
                    f"PO '{po_number}' not found in system but invoice references it; "
                    "treating as TWO_WAY (PO-backed)",
                )
                CaseStateMachine.transition(self.case, CaseStatus.TWO_WAY_IN_PROGRESS, PerformedByType.DETERMINISTIC)
                self._run_two_way_path()
            else:
                CaseRoutingService.reroute_path(
                    self.case, ProcessingPath.NON_PO,
                    "No PO reference on invoice and PO retrieval failed",
                )
                CaseStateMachine.transition(self.case, CaseStatus.NON_PO_VALIDATION_IN_PROGRESS, PerformedByType.DETERMINISTIC)
                self._run_non_po_path()

    def _run_two_way_path(self):
        """Execute 2-way matching stages."""
        self._execute_stage(CaseStageType.TWO_WAY_MATCHING)
        if CaseStateMachine.is_terminal(self.case.status):
            return
        self._run_common_tail()

    def _run_three_way_path(self):
        """Execute 3-way matching stages."""
        self._execute_stage(CaseStageType.THREE_WAY_MATCHING)
        if CaseStateMachine.is_terminal(self.case.status):
            return

        # Conditionally run GRN analysis if GRN issues found
        if self._needs_grn_analysis():
            self._execute_stage(CaseStageType.GRN_ANALYSIS)

        self._run_common_tail()

    def _run_non_po_path(self):
        """Execute non-PO validation stages."""
        self._execute_stage(CaseStageType.NON_PO_VALIDATION)
        self._run_common_tail()

    def _run_common_tail(self):
        """Execute the common tail stages: exception analysis -> routing -> summary."""
        self._execute_stage(CaseStageType.EXCEPTION_ANALYSIS)
        if not CaseStateMachine.is_terminal(self.case.status):
            self._execute_stage(CaseStageType.REVIEW_ROUTING)
        # Always run case summary so stale data is refreshed, even on auto-close
        self._execute_stage(CaseStageType.CASE_SUMMARY)

    def _execute_stage(self, stage_name: str):
        """Execute a single stage via the StageExecutor, wrapped in a Langfuse span."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

        self._stage_index += 1
        _lf_span = None

        self.case.current_stage = stage_name
        self.case.save(update_fields=["current_stage", "updated_at"])

        # Create or get stage record
        stage, created = APCaseStage.objects.get_or_create(
            case=self.case,
            stage_name=stage_name,
            retry_count=self._get_retry_count(stage_name),
            defaults={"stage_status": StageStatus.PENDING},
        )

        stage.stage_status = StageStatus.IN_PROGRESS
        stage.started_at = timezone.now()
        stage.save(update_fields=["stage_status", "started_at", "updated_at"])

        # -- Langfuse: per-stage span
        try:
            from apps.core.langfuse_client import start_span_safe
            _lf_span = start_span_safe(
                self._lf_trace,
                name=f"case_stage_{stage_name}",
                metadata={
                    "stage_index": self._stage_index,
                    "stage_name": stage_name,
                    "case_id": self.case.pk,
                    "case_number": self.case.case_number,
                    "processing_path": self.case.processing_path or "",
                    "case_status_before": self.case.status or "",
                    "retry_count": stage.retry_count,
                },
            )
        except Exception:
            pass

        try:
            output = StageExecutor.execute(self.case, stage_name)

            stage.stage_status = StageStatus.COMPLETED
            stage.completed_at = timezone.now()
            stage.output_payload = output or {}
            stage.save(update_fields=["stage_status", "completed_at", "output_payload", "updated_at"])

            # -- Langfuse: end span with output + observation scores
            try:
                from apps.core.langfuse_client import end_span_safe, score_observation_safe
                _span_output = {
                    "stage_status": "COMPLETED",
                    "case_status_after": self.case.status or "",
                }
                if output:
                    # Include key fields from stage output (limit size)
                    for k in ("match_status", "po_found", "resolved_path", "auto_closed",
                              "agents_executed", "final_recommendation", "confidence",
                              "extraction_confidence", "overall_status", "assignment_id"):
                        if k in output:
                            _span_output[k] = output[k]
                end_span_safe(_lf_span, output=_span_output)

                # Stage-specific observation scores
                if output and _lf_span:
                    self._emit_stage_scores(_lf_span, stage_name, output)
            except Exception:
                pass

        except Exception as exc:
            stage.stage_status = StageStatus.FAILED
            stage.completed_at = timezone.now()
            stage.notes = str(exc)[:1000]
            stage.save(update_fields=["stage_status", "completed_at", "notes", "updated_at"])
            try:
                from apps.core.langfuse_client import end_span_safe, score_observation_safe
                end_span_safe(_lf_span, output={"stage_status": "FAILED", "error": str(exc)[:200]}, level="ERROR")
                score_observation_safe(_lf_span, f"case_stage_{stage_name}_success", 0.0)
            except Exception:
                pass
            raise

        self.case.refresh_from_db()

    def _emit_stage_scores(self, lf_span, stage_name: str, output: dict):
        """Emit deterministic observation-level scores for a completed case stage."""
        try:
            from apps.core.langfuse_client import score_observation_safe, score_trace_safe
            # Universal: stage completed = 1.0
            score_observation_safe(lf_span, f"case_stage_{stage_name}_success", 1.0)

            if stage_name == CaseStageType.PATH_RESOLUTION:
                path = output.get("resolved_path", "")
                score_trace_safe(
                    self._lf_trace_id, "case_path_resolved",
                    1.0 if path in ("TWO_WAY", "THREE_WAY", "NON_PO") else 0.0,
                    comment=f"path={path}",
                )
            elif stage_name in (CaseStageType.TWO_WAY_MATCHING, CaseStageType.THREE_WAY_MATCHING):
                ms = output.get("match_status", "")
                _match_scores = {"MATCHED": 1.0, "PARTIAL_MATCH": 0.5, "REQUIRES_REVIEW": 0.3, "UNMATCHED": 0.0}
                score_observation_safe(lf_span, "case_match_result", _match_scores.get(ms, 0.0))
                score_trace_safe(
                    self._lf_trace_id, "case_match_status",
                    _match_scores.get(ms, 0.0),
                    comment=f"match_status={ms}",
                )
            elif stage_name == CaseStageType.PO_RETRIEVAL:
                score_observation_safe(
                    lf_span, "case_po_found",
                    1.0 if output.get("po_found") else 0.0,
                )
            elif stage_name == CaseStageType.EXCEPTION_ANALYSIS:
                auto_closed = output.get("auto_closed", False)
                score_observation_safe(
                    lf_span, "case_auto_closed",
                    1.0 if auto_closed else 0.0,
                )
                score_trace_safe(
                    self._lf_trace_id, "case_auto_closed",
                    1.0 if auto_closed else 0.0,
                    comment=f"rec={output.get('final_recommendation', '')}",
                )
                if output.get("confidence") is not None:
                    score_observation_safe(
                        lf_span, "case_agent_confidence",
                        float(output["confidence"]),
                    )
            elif stage_name == CaseStageType.REVIEW_ROUTING:
                score_trace_safe(
                    self._lf_trace_id, "case_routed_to_review",
                    1.0 if output.get("assignment_id") else 0.0,
                )
            elif stage_name == CaseStageType.NON_PO_VALIDATION:
                score_observation_safe(
                    lf_span, "case_non_po_approval_ready",
                    1.0 if output.get("approval_ready") else 0.0,
                )
                if output.get("risk_score") is not None:
                    score_observation_safe(
                        lf_span, "case_non_po_risk_score",
                        min(float(output["risk_score"]) / 100.0, 1.0),
                    )
        except Exception:
            pass

    def _needs_grn_analysis(self) -> bool:
        """Check if GRN analysis is needed based on reconciliation exceptions."""
        if not self.case.reconciliation_result:
            return False
        grn_exception_types = {
            "GRN_NOT_FOUND", "RECEIPT_SHORTAGE", "INVOICE_QTY_EXCEEDS_RECEIVED",
            "OVER_RECEIPT", "MULTI_GRN_PARTIAL_RECEIPT", "RECEIPT_LOCATION_MISMATCH",
            "DELAYED_RECEIPT",
        }
        return self.case.reconciliation_result.exceptions.filter(
            exception_type__in=grn_exception_types
        ).exists()

    def _resolve_path_from_linked_po(self) -> str:
        """Resolve processing path when PO_RETRIEVAL has already linked a PO.

        Uses the mode resolver directly (instead of re-running PO lookup
        which would fail on the original noisy PO number).
        """
        from apps.reconciliation.services.mode_resolver import ReconciliationModeResolver
        from apps.core.enums import DecisionSource, InvoiceType

        invoice = self.case.invoice
        po = self.case.purchase_order
        resolver = ReconciliationModeResolver()
        mode_result = resolver.resolve(invoice, po)

        # If GRNs exist for this PO, force THREE_WAY regardless of mode resolver
        from apps.documents.models import GoodsReceiptNote
        has_grn = GoodsReceiptNote.objects.filter(purchase_order=po).exists()
        if has_grn and mode_result.mode == "TWO_WAY":
            mode_result.mode = "THREE_WAY"
            mode_result.grn_required = True
            logger.info(
                "Case %s: GRN exists for PO %s — overriding mode to THREE_WAY",
                self.case.case_number, po.po_number,
            )

        self.case.reconciliation_mode = mode_result.mode
        self.case.invoice_type = InvoiceType.PO_BACKED
        self.case.save(update_fields=["reconciliation_mode", "invoice_type", "updated_at"])

        # Enrich invoice line item flags from PO data
        from apps.cases.orchestrators.stage_executor import StageExecutor
        StageExecutor._enrich_invoice_lines_from_po(invoice, po)

        if mode_result.mode == "TWO_WAY":
            path = ProcessingPath.TWO_WAY
        else:
            path = ProcessingPath.THREE_WAY

        self.case.processing_path = path
        self.case.save(update_fields=["processing_path", "updated_at"])

        APCaseDecision.objects.create(
            case=self.case,
            decision_type=DecisionType.PATH_SELECTED,
            decision_source=DecisionSource.DETERMINISTIC,
            decision_value=path,
            confidence=0.8,
            rationale=(
                f"PO {po.po_number} linked via PO retrieval stage; "
                f"mode resolved as {mode_result.mode} via {mode_result.resolution_method}"
            ),
            evidence={
                "po_number": po.po_number,
                "resolution_method": mode_result.resolution_method,
                "grn_required": mode_result.grn_required,
            },
        )

        logger.info(
            "Case %s path resolved from linked PO %s: %s",
            self.case.case_number, po.po_number, path,
        )
        return path

    def _get_retry_count(self, stage_name: str) -> int:
        """Get the current retry count for a stage."""
        last = self.case.stages.filter(stage_name=stage_name).order_by("-retry_count").first()
        if last and last.stage_status == StageStatus.FAILED:
            return last.retry_count + 1
        if last and last.stage_status == StageStatus.COMPLETED:
            return last.retry_count  # reuse completed count for re-run
        return 0

    def _sync_from_invoice(self):
        """Sync vendor and PO from the invoice if they were linked after case creation."""
        invoice = self.case.invoice
        changed = []

        # If invoice has no vendor FK yet, try to resolve it from raw_vendor_name
        # (covers cases where the vendor record was created after extraction/approval)
        if not invoice.vendor_id and invoice.raw_vendor_name:
            try:
                from apps.core.utils import normalize_string
                from apps.vendors.models import Vendor
                from apps.posting_core.models import VendorAliasMapping
                norm = normalize_string(invoice.raw_vendor_name)
                vendor = Vendor.objects.filter(normalized_name=norm, is_active=True).first()
                if not vendor:
                    alias = VendorAliasMapping.objects.filter(
                        normalized_alias=norm, is_active=True
                    ).select_related("vendor").first()
                    if alias and alias.vendor:
                        vendor = alias.vendor
                if vendor:
                    invoice.vendor = vendor
                    invoice.save(update_fields=["vendor", "updated_at"])
                    logger.info(
                        "Vendor resolved during reprocess for invoice %s: %s",
                        invoice.pk, vendor.name,
                    )
            except Exception as exc:
                logger.warning(
                    "Vendor re-resolution failed during reprocess for invoice %s: %s",
                    invoice.pk, exc,
                )

        if invoice.vendor_id and self.case.vendor_id != invoice.vendor_id:
            self.case.vendor_id = invoice.vendor_id
            changed.append("vendor_id")
        if invoice.po_number and not self.case.purchase_order_id:
            from apps.documents.models import PurchaseOrder
            po = PurchaseOrder.objects.filter(po_number=invoice.po_number).first()
            if po and self.case.purchase_order_id != po.pk:
                self.case.purchase_order = po
                changed.append("purchase_order_id")
        if changed:
            changed.append("updated_at")
            self.case.save(update_fields=changed)
            logger.info("Synced %s from invoice for case %s", changed, self.case.case_number)
