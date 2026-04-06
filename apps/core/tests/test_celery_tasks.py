"""Tests for Celery tasks: reconciliation, agents, extraction."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Lightweight stubs for service return values
# ---------------------------------------------------------------------------
@dataclass
class _MockRun:
    pk: int = 1
    status: str = "COMPLETED"
    total_invoices: int = 5
    matched_count: int = 3
    partial_count: int = 1
    unmatched_count: int = 0
    error_count: int = 0
    review_count: int = 1
    langfuse_trace_id: str = ""

    def save(self, **kwargs):
        pass


@dataclass
class _MockOrchestrationResult:
    reconciliation_result_id: int = 99
    agents_executed: List[str] = field(default_factory=lambda: ["DISCREPANCY_ANALYSIS"])
    final_recommendation: Optional[str] = "SEND_TO_AP_REVIEW"
    final_confidence: float = 0.72
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""


# =========================================================================
# run_reconciliation_task
# =========================================================================
@pytest.mark.django_db
class TestRunReconciliationTask:
    """RCT-01 to RCT-05."""

    @patch("apps.reconciliation.models.ReconciliationResult.objects")
    @patch("apps.core.utils.dispatch_task")
    @patch("apps.reconciliation.services.runner_service.ReconciliationRunnerService.run")
    def test_returns_summary_on_success(self, mock_run, mock_dispatch, mock_rr_mgr):
        """RCT-01: Successful run returns dict with expected keys."""
        mock_run.return_value = _MockRun()
        # The task does ReconciliationResult.objects.filter(run=run).exclude(...).values_list(...)
        mock_qs = MagicMock()
        mock_qs.exclude.return_value.values_list.return_value = []
        mock_rr_mgr.filter.return_value = mock_qs

        from apps.reconciliation.tasks import run_reconciliation_task
        result = run_reconciliation_task.apply(args=(None, None, None)).get()
        assert result["status"] == "ok"
        assert result["run_id"] == 1
        assert "matched" in result
        assert "agent_tasks_dispatched" in result

    @patch("apps.reconciliation.services.runner_service.ReconciliationRunnerService.run")
    def test_no_invoices_returns_error(self, mock_run):
        """RCT-02: Empty invoice list returns error dict."""
        from apps.reconciliation.tasks import run_reconciliation_task
        result = run_reconciliation_task.apply(args=([999999], None, None)).get()
        assert result["status"] == "error"
        mock_run.assert_not_called()


# =========================================================================
# run_agent_pipeline_task
# =========================================================================
@pytest.mark.django_db
class TestRunAgentPipelineTask:
    """APT-01 to APT-03."""

    def test_missing_result_returns_error(self):
        """APT-01: Non-existent ReconciliationResult returns error."""
        from apps.agents.tasks import run_agent_pipeline_task
        result = run_agent_pipeline_task.apply(args=(999999,)).get()
        assert "error" in result
        assert "not found" in result["error"]

    @patch("apps.agents.services.orchestrator.AgentOrchestrator.execute")
    def test_returns_outcome_dict(self, mock_execute):
        """APT-02: Successful execution returns expected keys."""
        # Create minimal DB records
        from apps.documents.models import Invoice
        from apps.reconciliation.models import ReconciliationRun, ReconciliationResult, ReconciliationConfig

        config = ReconciliationConfig.objects.create(name="test")
        run = ReconciliationRun.objects.create(
            config=config,
            status="COMPLETED",
        )
        # Create invoice first
        inv = Invoice.objects.create(
            invoice_number="INV-APT",
            status="RECONCILED",
        )
        rr = ReconciliationResult.objects.create(
            run=run,
            invoice=inv,
            match_status="PARTIAL_MATCH",
        )

        mock_execute.return_value = _MockOrchestrationResult(
            reconciliation_result_id=rr.pk,
        )

        from apps.agents.tasks import run_agent_pipeline_task
        result = run_agent_pipeline_task.apply(args=(rr.pk,)).get()
        assert result["reconciliation_result_id"] == rr.pk
        assert result["agents_executed"] == ["DISCREPANCY_ANALYSIS"]
        assert result["skipped"] is False


# =========================================================================
# process_invoice_upload_task (extraction)
# =========================================================================
@pytest.mark.django_db
class TestProcessInvoiceUploadTask:
    """EXT-01 to EXT-02."""

    def test_missing_upload_returns_error(self):
        """EXT-01: Non-existent upload returns error dict."""
        from apps.extraction.tasks import process_invoice_upload_task
        result = process_invoice_upload_task.apply(args=(999999,)).get()
        assert result["status"] == "error"
        assert "not found" in result["message"]
