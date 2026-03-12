"""
CaseOrchestrator — master orchestrator driving the APCase through its stages.

Coordinates deterministic services and agent reasoning in the correct sequence
for each processing path (TWO_WAY, THREE_WAY, NON_PO).
"""

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.cases.models import APCase, APCaseStage
from apps.cases.services.case_routing_service import CaseRoutingService
from apps.cases.state_machine.case_state_machine import CaseStateMachine
from apps.core.enums import (
    CaseStageType,
    CaseStatus,
    PerformedByType,
    ProcessingPath,
    StageStatus,
)

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

    def run(self) -> APCase:
        """Execute the case from its current position through completion."""
        logger.info("Orchestrating case %s (status=%s, path=%s)",
                     self.case.case_number, self.case.status, self.case.processing_path)

        try:
            # Stage 1: Intake (if not yet started)
            if self.case.status == CaseStatus.NEW:
                self._execute_stage(CaseStageType.INTAKE)

            # Stage 2: Extraction (if not yet done)
            if self.case.status == CaseStatus.INTAKE_IN_PROGRESS:
                self._execute_stage(CaseStageType.EXTRACTION)

            # Stage 3: Path resolution (if extraction completed)
            if self.case.status == CaseStatus.EXTRACTION_COMPLETED:
                self._execute_stage(CaseStageType.PATH_RESOLUTION)

            # Stage 4+: Path-specific processing
            self._execute_path_stages()

        except Exception:
            logger.exception("Case %s orchestration failed", self.case.case_number)
            self.case.status = CaseStatus.FAILED
            self.case.save(update_fields=["status", "updated_at"])
            raise

        return self.case

    def run_from(self, stage: str) -> APCase:
        """Reprocess from a specific stage forward."""
        logger.info("Reprocessing case %s from stage %s", self.case.case_number, stage)

        # Mark subsequent stages as skipped
        self.case.stages.filter(
            stage_name=stage, stage_status__in=[StageStatus.COMPLETED, StageStatus.FAILED]
        ).update(stage_status=StageStatus.SKIPPED)

        self.case.current_stage = stage
        self.case.save(update_fields=["current_stage", "updated_at"])

        return self.run()

    def _execute_path_stages(self):
        """Execute the path-specific stages based on resolved processing path."""
        path = self.case.processing_path
        if path == ProcessingPath.UNRESOLVED:
            # Try PO Retrieval Agent, then re-resolve
            self._execute_stage(CaseStageType.PO_RETRIEVAL)
            # After PO retrieval, re-resolve path
            CaseRoutingService.resolve_path(self.case)
            self.case.refresh_from_db()
            path = self.case.processing_path

        if path == ProcessingPath.TWO_WAY:
            self._run_two_way_path()
        elif path == ProcessingPath.THREE_WAY:
            self._run_three_way_path()
        elif path == ProcessingPath.NON_PO:
            self._run_non_po_path()
        else:
            # Still unresolved after PO retrieval — reroute to NON_PO
            CaseRoutingService.reroute_path(
                self.case, ProcessingPath.NON_PO,
                "PO retrieval failed; treating as non-PO",
            )
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
        """Execute the common tail stages: exception analysis → routing → summary."""
        self._execute_stage(CaseStageType.EXCEPTION_ANALYSIS)
        if CaseStateMachine.is_terminal(self.case.status):
            return
        self._execute_stage(CaseStageType.REVIEW_ROUTING)
        self._execute_stage(CaseStageType.CASE_SUMMARY)

    def _execute_stage(self, stage_name: str):
        """Execute a single stage via the StageExecutor."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

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

        try:
            output = StageExecutor.execute(self.case, stage_name)

            stage.stage_status = StageStatus.COMPLETED
            stage.completed_at = timezone.now()
            stage.output_payload = output or {}
            stage.save(update_fields=["stage_status", "completed_at", "output_payload", "updated_at"])

        except Exception as exc:
            stage.stage_status = StageStatus.FAILED
            stage.completed_at = timezone.now()
            stage.notes = str(exc)[:1000]
            stage.save(update_fields=["stage_status", "completed_at", "notes", "updated_at"])
            raise

        self.case.refresh_from_db()

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

    def _get_retry_count(self, stage_name: str) -> int:
        """Get the current retry count for a stage."""
        last = self.case.stages.filter(stage_name=stage_name).order_by("-retry_count").first()
        if last and last.stage_status == StageStatus.FAILED:
            return last.retry_count + 1
        if last and last.stage_status == StageStatus.COMPLETED:
            return last.retry_count  # reuse completed count for re-run
        return 0
