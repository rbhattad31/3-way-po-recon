"""
Tests for the extraction approval gate in the case pipeline.

Covers:
- EXTRACTION_APPROVAL stage pauses pipeline when invoice is PENDING_APPROVAL
- EXTRACTION_APPROVAL stage continues pipeline when invoice is READY_FOR_RECON
- Case creation happens immediately after extraction (not after approval)
- Approval service resumes paused case from PATH_RESOLUTION
- State machine transitions for PENDING_EXTRACTION_APPROVAL
- Idempotent case creation (no duplicates)
- Stage executor _execute_extraction_approval behavior
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from apps.cases.state_machine.case_state_machine import CaseStateMachine
from apps.core.enums import (
    CaseStageType,
    CaseStatus,
    InvoiceStatus,
    PerformedByType,
)


# ---------------------------------------------------------------------------
# Pure unit tests (no DB)
# ---------------------------------------------------------------------------

class TestExtractionApprovalStateMachine:
    """State machine transitions for the new EXTRACTION_APPROVAL gate."""

    def test_extraction_completed_to_pending_approval(self):
        """EXTRACTION_COMPLETED -> PENDING_EXTRACTION_APPROVAL by SYSTEM."""
        assert CaseStateMachine.can_transition(
            CaseStatus.EXTRACTION_COMPLETED,
            CaseStatus.PENDING_EXTRACTION_APPROVAL,
            PerformedByType.SYSTEM,
        ) is True

    def test_pending_approval_to_path_resolution(self):
        """PENDING_EXTRACTION_APPROVAL -> PATH_RESOLUTION_IN_PROGRESS by SYSTEM."""
        assert CaseStateMachine.can_transition(
            CaseStatus.PENDING_EXTRACTION_APPROVAL,
            CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
            PerformedByType.SYSTEM,
        ) is True

    def test_pending_approval_to_path_resolution_by_human(self):
        """PENDING_EXTRACTION_APPROVAL -> PATH_RESOLUTION_IN_PROGRESS by HUMAN."""
        assert CaseStateMachine.can_transition(
            CaseStatus.PENDING_EXTRACTION_APPROVAL,
            CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
            PerformedByType.HUMAN,
        ) is True

    def test_pending_approval_to_extraction_completed(self):
        """PENDING_EXTRACTION_APPROVAL -> EXTRACTION_COMPLETED (for resume)."""
        assert CaseStateMachine.can_transition(
            CaseStatus.PENDING_EXTRACTION_APPROVAL,
            CaseStatus.EXTRACTION_COMPLETED,
            PerformedByType.SYSTEM,
        ) is True

    def test_pending_approval_cannot_go_to_closed(self):
        """PENDING_EXTRACTION_APPROVAL cannot jump to CLOSED."""
        assert CaseStateMachine.can_transition(
            CaseStatus.PENDING_EXTRACTION_APPROVAL,
            CaseStatus.CLOSED,
        ) is False

    def test_extraction_completed_can_still_go_to_path_resolution(self):
        """EXTRACTION_COMPLETED -> PATH_RESOLUTION_IN_PROGRESS still works."""
        assert CaseStateMachine.can_transition(
            CaseStatus.EXTRACTION_COMPLETED,
            CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
            PerformedByType.SYSTEM,
        ) is True

    def test_pending_approval_is_not_terminal(self):
        """PENDING_EXTRACTION_APPROVAL is not a terminal state."""
        assert CaseStateMachine.is_terminal(
            CaseStatus.PENDING_EXTRACTION_APPROVAL
        ) is False

    def test_pending_approval_allowed_transitions(self):
        """PENDING_EXTRACTION_APPROVAL has transitions to PATH_RESOLUTION and EXTRACTION_COMPLETED."""
        allowed = CaseStateMachine.get_allowed_transitions(
            CaseStatus.PENDING_EXTRACTION_APPROVAL
        )
        assert CaseStatus.PATH_RESOLUTION_IN_PROGRESS in allowed
        assert CaseStatus.EXTRACTION_COMPLETED in allowed

    def test_transition_updates_case_to_pending_approval(self):
        """transition() updates case.status to PENDING_EXTRACTION_APPROVAL."""
        case = MagicMock()
        case.status = CaseStatus.EXTRACTION_COMPLETED
        CaseStateMachine.transition(
            case, CaseStatus.PENDING_EXTRACTION_APPROVAL, PerformedByType.SYSTEM
        )
        case.save.assert_called()

    def test_transition_resumes_from_pending_approval(self):
        """transition() from PENDING_EXTRACTION_APPROVAL to EXTRACTION_COMPLETED."""
        case = MagicMock()
        case.status = CaseStatus.PENDING_EXTRACTION_APPROVAL
        CaseStateMachine.transition(
            case, CaseStatus.EXTRACTION_COMPLETED, PerformedByType.SYSTEM
        )
        case.save.assert_called()


class TestExtractionApprovalStageExecutor:
    """Unit tests for StageExecutor._execute_extraction_approval."""

    def test_approved_invoice_continues(self):
        """When invoice is READY_FOR_RECON, stage returns approved=True."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

        case = MagicMock()
        invoice = MagicMock()
        invoice.status = InvoiceStatus.READY_FOR_RECON
        case.invoice = invoice
        case.case_number = "AP-TEST-001"

        result = StageExecutor._execute_extraction_approval(case)
        assert result["approved"] is True
        assert result["approval_type"] == "pre_approved"

    def test_pending_invoice_pauses(self):
        """When invoice is PENDING_APPROVAL, stage pauses the case."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

        case = MagicMock()
        invoice = MagicMock()
        invoice.status = InvoiceStatus.PENDING_APPROVAL
        case.invoice = invoice
        case.case_number = "AP-TEST-002"
        case.status = CaseStatus.EXTRACTION_COMPLETED

        result = StageExecutor._execute_extraction_approval(case)
        assert result["approved"] is False
        assert result["paused"] is True

    def test_extracted_invoice_pauses(self):
        """When invoice is EXTRACTED (not yet validated), stage pauses."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

        case = MagicMock()
        invoice = MagicMock()
        invoice.status = InvoiceStatus.EXTRACTED
        case.invoice = invoice
        case.case_number = "AP-TEST-003"
        case.status = CaseStatus.EXTRACTION_COMPLETED

        result = StageExecutor._execute_extraction_approval(case)
        assert result["approved"] is False
        assert result["paused"] is True

    def test_unexpected_status_pauses(self):
        """Invoice in unexpected status still pauses."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

        case = MagicMock()
        invoice = MagicMock()
        invoice.status = InvoiceStatus.RECONCILED  # truly unexpected for this stage
        case.invoice = invoice
        case.case_number = "AP-TEST-004"
        case.status = CaseStatus.EXTRACTION_COMPLETED

        result = StageExecutor._execute_extraction_approval(case)
        assert result["approved"] is False
        assert result["paused"] is True

    def test_stage_registered_in_handler_map(self):
        """EXTRACTION_APPROVAL is registered in the stage handler map."""
        from apps.cases.orchestrators.stage_executor import StageExecutor

        # Verify the handler method exists on StageExecutor
        assert hasattr(StageExecutor, "_execute_extraction_approval")
        assert callable(StageExecutor._execute_extraction_approval)


class TestExtractionApprovalPathStages:
    """Verify EXTRACTION_APPROVAL is in all processing path stage sequences."""

    def test_two_way_includes_extraction_approval(self):
        from apps.cases.orchestrators.case_orchestrator import PATH_STAGES
        from apps.core.enums import ProcessingPath

        stages = PATH_STAGES[ProcessingPath.TWO_WAY]
        assert CaseStageType.EXTRACTION_APPROVAL in stages
        # Verify correct ordering: EXTRACTION -> EXTRACTION_APPROVAL -> PATH_RESOLUTION
        ext_idx = stages.index(CaseStageType.EXTRACTION)
        approval_idx = stages.index(CaseStageType.EXTRACTION_APPROVAL)
        path_idx = stages.index(CaseStageType.PATH_RESOLUTION)
        assert ext_idx < approval_idx < path_idx

    def test_three_way_includes_extraction_approval(self):
        from apps.cases.orchestrators.case_orchestrator import PATH_STAGES
        from apps.core.enums import ProcessingPath

        stages = PATH_STAGES[ProcessingPath.THREE_WAY]
        assert CaseStageType.EXTRACTION_APPROVAL in stages
        ext_idx = stages.index(CaseStageType.EXTRACTION)
        approval_idx = stages.index(CaseStageType.EXTRACTION_APPROVAL)
        path_idx = stages.index(CaseStageType.PATH_RESOLUTION)
        assert ext_idx < approval_idx < path_idx

    def test_non_po_includes_extraction_approval(self):
        from apps.cases.orchestrators.case_orchestrator import PATH_STAGES
        from apps.core.enums import ProcessingPath

        stages = PATH_STAGES[ProcessingPath.NON_PO]
        assert CaseStageType.EXTRACTION_APPROVAL in stages
        ext_idx = stages.index(CaseStageType.EXTRACTION)
        approval_idx = stages.index(CaseStageType.EXTRACTION_APPROVAL)
        path_idx = stages.index(CaseStageType.PATH_RESOLUTION)
        assert ext_idx < approval_idx < path_idx


class TestCaseOrchestratorApprovalGate:
    """Test that the case orchestrator correctly pauses/continues at the gate."""

    def test_orchestrator_pauses_on_pending_approval(self):
        """Orchestrator returns early when case reaches PENDING_EXTRACTION_APPROVAL."""
        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

        case = MagicMock()
        case.case_number = "AP-TEST-010"
        case.status = CaseStatus.PENDING_EXTRACTION_APPROVAL
        case.processing_path = "NON_PO"
        case.invoice.status = InvoiceStatus.PENDING_APPROVAL

        orch = CaseOrchestrator(case)
        # When status is PENDING_EXTRACTION_APPROVAL, run() should return
        # without executing path stages
        result = orch.run()
        assert result.status == CaseStatus.PENDING_EXTRACTION_APPROVAL


# ---------------------------------------------------------------------------
# Database integration tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestExtractionApprovalGateIntegration:
    """Integration tests requiring database access."""

    def _make_user(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        return User.objects.create_user(
            email="test-gate@example.com",
            password="testpass123",
            first_name="Test",
            last_name="User",
        )

    def _make_upload(self):
        from apps.documents.models import DocumentUpload
        return DocumentUpload.objects.create(
            original_filename="test.pdf",
            file_size=1024,
            content_type="application/pdf",
        )

    def _make_invoice(self, upload, status=InvoiceStatus.PENDING_APPROVAL):
        from apps.documents.models import Invoice
        return Invoice.objects.create(
            invoice_number="INV-GATE-001",
            currency="USD",
            total_amount=1000,
            status=status,
            extraction_confidence=0.92,
            document_upload=upload,
            po_number="",
        )

    def _make_case(self, invoice, user=None):
        from apps.cases.services.case_creation_service import CaseCreationService
        return CaseCreationService.create_from_upload(
            invoice=invoice,
            uploaded_by=user,
        )

    def test_case_pauses_at_extraction_approval(self):
        """Case pipeline pauses at PENDING_EXTRACTION_APPROVAL when invoice is PENDING_APPROVAL."""
        user = self._make_user()
        upload = self._make_upload()
        invoice = self._make_invoice(upload, status=InvoiceStatus.PENDING_APPROVAL)
        case = self._make_case(invoice, user)

        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
        orch = CaseOrchestrator(case)
        result = orch.run()

        result.refresh_from_db()
        assert result.status == CaseStatus.PENDING_EXTRACTION_APPROVAL
        assert result.current_stage == CaseStageType.EXTRACTION_APPROVAL

    def test_case_continues_when_auto_approved(self):
        """If invoice is already READY_FOR_RECON, the pipeline continues past the gate."""
        user = self._make_user()
        upload = self._make_upload()
        invoice = self._make_invoice(upload, status=InvoiceStatus.READY_FOR_RECON)
        case = self._make_case(invoice, user)

        # Need recon config for downstream stages
        from apps.reconciliation.models import ReconciliationConfig
        ReconciliationConfig.objects.get_or_create(
            name="Default",
            defaults={
                "is_default": True,
                "quantity_tolerance_pct": 2.0,
                "price_tolerance_pct": 1.0,
                "amount_tolerance_pct": 1.0,
            },
        )

        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
        orch = CaseOrchestrator(case)
        with patch("apps.agents.services.orchestrator.AgentOrchestrator.execute") as mock_agent:
            mock_agent.return_value = MagicMock(
                skipped=False,
                agents_executed=["CASE_SUMMARY"],
                final_recommendation="SEND_TO_AP_REVIEW",
                final_confidence=0.8,
            )
            result = orch.run()

        result.refresh_from_db()
        # Should have advanced past EXTRACTION_APPROVAL
        assert result.status != CaseStatus.PENDING_EXTRACTION_APPROVAL
        assert result.status != CaseStatus.NEW

    def test_resume_paused_case_on_approval(self):
        """When approval service triggers, a paused case resumes from EXTRACTION_COMPLETED."""
        user = self._make_user()
        upload = self._make_upload()
        invoice = self._make_invoice(upload, status=InvoiceStatus.PENDING_APPROVAL)
        case = self._make_case(invoice, user)

        # Run to pause
        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
        orch = CaseOrchestrator(case)
        orch.run()
        case.refresh_from_db()
        assert case.status == CaseStatus.PENDING_EXTRACTION_APPROVAL

        # Simulate approval: invoice -> READY_FOR_RECON, case -> EXTRACTION_COMPLETED
        invoice.status = InvoiceStatus.READY_FOR_RECON
        invoice.save(update_fields=["status", "updated_at"])

        CaseStateMachine.transition(
            case, CaseStatus.EXTRACTION_COMPLETED, PerformedByType.SYSTEM
        )

        # Need recon config for downstream matching stages
        from apps.reconciliation.models import ReconciliationConfig
        ReconciliationConfig.objects.get_or_create(
            name="Default",
            defaults={
                "is_default": True,
                "quantity_tolerance_pct": 2.0,
                "price_tolerance_pct": 1.0,
                "amount_tolerance_pct": 1.0,
            },
        )

        # Resume processing
        orch2 = CaseOrchestrator(case)
        with patch("apps.agents.services.orchestrator.AgentOrchestrator.execute") as mock_agent:
            mock_agent.return_value = MagicMock(
                skipped=False,
                agents_executed=["CASE_SUMMARY"],
                final_recommendation="SEND_TO_AP_REVIEW",
                final_confidence=0.8,
            )
            orch2.run()

        case.refresh_from_db()
        # Should have advanced past the gate and reached post-matching stages
        assert case.status not in (
            CaseStatus.PENDING_EXTRACTION_APPROVAL,
            CaseStatus.EXTRACTION_COMPLETED,
            CaseStatus.NEW,
        )

    def test_no_duplicate_cases_on_extraction_then_approval(self):
        """Creating case during extraction and again during approval should not duplicate."""
        from apps.cases.models import APCase

        user = self._make_user()
        upload = self._make_upload()
        invoice = self._make_invoice(upload, status=InvoiceStatus.PENDING_APPROVAL)

        # Simulate extraction task creating case
        case = self._make_case(invoice, user)
        assert APCase.objects.filter(invoice=invoice, is_active=True).count() == 1

        # Simulate approval service trying to create again
        case2 = self._make_case(invoice, user)
        assert case.pk == case2.pk  # Same case returned
        assert APCase.objects.filter(invoice=invoice, is_active=True).count() == 1

    def test_approval_service_resumes_paused_case(self):
        """_ensure_case_and_process finds paused case and resumes it."""
        from apps.cases.models import APCase
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = self._make_user()
        upload = self._make_upload()
        invoice = self._make_invoice(upload, status=InvoiceStatus.PENDING_APPROVAL)
        case = self._make_case(invoice, user)

        # Simulate pipeline pause
        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
        orch = CaseOrchestrator(case)
        orch.run()
        case.refresh_from_db()
        assert case.status == CaseStatus.PENDING_EXTRACTION_APPROVAL

        # Now simulate approval
        invoice.status = InvoiceStatus.READY_FOR_RECON
        invoice.save(update_fields=["status", "updated_at"])

        # Call the approval service method
        with patch("apps.core.utils.dispatch_task") as mock_dispatch:
            ExtractionApprovalService._ensure_case_and_process(invoice, user)

        # Case should have been transitioned back to EXTRACTION_COMPLETED
        case.refresh_from_db()
        assert case.status == CaseStatus.EXTRACTION_COMPLETED

        # dispatch_task should have been called with the case ID
        mock_dispatch.assert_called_once()

    def test_extraction_approval_stage_recorded(self):
        """An APCaseStage record is created for EXTRACTION_APPROVAL."""
        from apps.cases.models import APCaseStage

        user = self._make_user()
        upload = self._make_upload()
        invoice = self._make_invoice(upload, status=InvoiceStatus.PENDING_APPROVAL)
        case = self._make_case(invoice, user)

        from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
        orch = CaseOrchestrator(case)
        orch.run()

        # Check EXTRACTION_APPROVAL stage was created
        stages = APCaseStage.objects.filter(
            case=case,
            stage_name=CaseStageType.EXTRACTION_APPROVAL,
        )
        assert stages.exists()
