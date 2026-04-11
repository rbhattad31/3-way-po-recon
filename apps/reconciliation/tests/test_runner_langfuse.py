"""
Tests for ReconciliationRunnerService Langfuse integration.

Verifies two things:
  1. The runner works correctly when Langfuse is DISABLED (lf_trace=None path).
     All Langfuse spans are no-ops and must not affect the match result.
  2. The runner calls score_trace() after each invoice is reconciled.
     score_trace() is fail-silent — any SDK error must not break reconciliation.

Score mapping verified (from runner source):
  MATCHED       -> 1.0
  PARTIAL_MATCH -> 0.5
  REQUIRES_REVIEW -> 0.3
  UNMATCHED     -> 0.0

Test strategy:
  - Use mocks for the entire sub-service layer (POLookupService, ExecutionRouter, etc.)
    so tests don't need a full DB setup with invoices, POs, and GRNs.
  - Patch apps.core.langfuse_client functions directly.
  - Confirm that Langfuse errors never propagate to the caller.
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

from apps.core.enums import MatchStatus, ReconciliationMode
from apps.core.evaluation_constants import RECON_RECONCILIATION_MATCH
from apps.core.evaluation_constants import RECON_INVOICE_ERROR, RECON_ROUTED_TO_REVIEW


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_mock_invoice(pk=1, status="READY_FOR_RECON", confidence=0.95,
                      po_number="PO-001", vendor_id=1, total_amount="1000.00"):
    inv = MagicMock()
    inv.pk = pk
    inv.status = status
    inv.extraction_confidence = confidence
    inv.po_number = po_number
    inv.raw_po_number = po_number
    inv.vendor_id = vendor_id
    inv.total_amount = Decimal(total_amount)
    inv.is_duplicate = False
    inv.vendor = MagicMock()
    inv.document_upload = MagicMock()
    return inv


def make_po_result(found=True):
    r = MagicMock()
    r.found = found
    r.purchase_order = MagicMock(po_number="PO-001", po_date=None)
    r.lookup_method = "exact"
    return r


def make_mode_resolution(mode=ReconciliationMode.TWO_WAY):
    r = MagicMock()
    r.mode = mode
    r.policy_code = ""
    r.policy_name = ""
    r.reason = "test"
    r.grn_required = mode == ReconciliationMode.THREE_WAY
    r.resolution_method = "default"
    return r


def make_routed(match_status=MatchStatus.MATCHED):
    r = MagicMock()
    r.po_result = make_po_result()
    r.header_result = MagicMock(
        all_ok=True, vendor_match=True, currency_match=True,
        po_total_match=True, total_difference=0,
    )
    r.line_result = MagicMock(
        all_lines_matched=True, all_within_tolerance=True,
        unmatched_invoice_lines=[], unmatched_po_lines=[],
        total_invoice_lines=1, line_pairs=[],
    )
    r.grn_result = None
    r.grn_checked = False
    return r


@pytest.fixture
def patched_runner(db):
    """
    Build a ReconciliationRunnerService with all sub-services mocked.
    Returns (runner, mock_config).
    """
    from apps.reconciliation.services.runner_service import ReconciliationRunnerService
    from apps.reconciliation.tests.factories import ReconConfigFactory

    config = ReconConfigFactory(
        extraction_confidence_threshold=0.75,
        name="TestConfig",
    )

    with patch("apps.reconciliation.services.runner_service.POLookupService") as MockPO, \
         patch("apps.reconciliation.services.runner_service.ReconciliationModeResolver") as MockMode, \
         patch("apps.reconciliation.services.runner_service.ReconciliationExecutionRouter") as MockRouter, \
         patch("apps.reconciliation.services.runner_service.ClassificationService") as MockClass, \
         patch("apps.reconciliation.services.runner_service.ExceptionBuilderService") as MockExcBuilder, \
         patch("apps.reconciliation.services.runner_service.ReconciliationResultService") as MockResult, \
         patch("apps.auditlog.services.AuditService.log_event"), \
         patch("apps.cases.services.review_workflow_service.ReviewWorkflowService.create_assignment", return_value=None):

        runner = ReconciliationRunnerService(config=config)

        # Configure mock returns
        runner.po_lookup.lookup.return_value = make_po_result(found=True)
        runner.mode_resolver.resolve.return_value = make_mode_resolution()
        runner.router.execute.return_value = make_routed()
        runner.classifier.classify.return_value = MatchStatus.MATCHED
        runner.exception_builder.build.return_value = []

        # result_service.save() must return a mock result with .line_results.all()
        mock_saved_result = MagicMock()
        mock_saved_result.pk = 1
        mock_saved_result.line_results.all.return_value = []
        runner.result_service.save.return_value = mock_saved_result

        yield runner, config


# ─── Langfuse disabled path ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestRunnerLangfuseDisabled:
    def test_runner_completes_when_langfuse_disabled(self, patched_runner):
        """Runner produces a ReconciliationRun result with Langfuse fully disabled."""
        runner, config = patched_runner
        invoice = make_mock_invoice(pk=1)

        with patch("apps.core.langfuse_client.get_client", return_value=None), \
             patch("apps.reconciliation.services.runner_service.Invoice.objects") as mock_inv_qs:
            mock_inv_qs.filter.return_value.select_related.return_value = []
            recon_run = runner.run(invoices=[invoice])

        assert recon_run is not None
        assert recon_run.pk is not None

    def test_runner_counts_matched_correctly(self, patched_runner):
        """Runner counts match status correctly when Langfuse is disabled."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = MatchStatus.MATCHED
        invoice = make_mock_invoice(pk=2)

        with patch("apps.core.langfuse_client.get_client", return_value=None):
            recon_run = runner.run(invoices=[invoice])

        assert recon_run.matched_count == 1
        assert recon_run.error_count == 0

    def test_lf_trace_none_does_not_affect_classification(self, patched_runner):
        """Passing lf_trace=None explicitly still runs classification normally."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = MatchStatus.PARTIAL_MATCH
        invoice = make_mock_invoice(pk=3)

        with patch("apps.core.langfuse_client.get_client", return_value=None):
            recon_run = runner.run(invoices=[invoice], lf_trace=None)

        assert recon_run.partial_count == 1

    def test_empty_invoice_list_with_langfuse_disabled(self, patched_runner):
        """Runner handles empty invoice list gracefully with no Langfuse."""
        runner, config = patched_runner

        with patch("apps.core.langfuse_client.get_client", return_value=None):
            recon_run = runner.run(invoices=[])

        assert recon_run.total_invoices == 0
        assert recon_run.matched_count == 0


# ─── score_trace integration ──────────────────────────────────────────────────

@pytest.mark.django_db
class TestRunnerScoreTrace:
    @pytest.mark.parametrize("match_status,expected_score", [
        (MatchStatus.MATCHED, 1.0),
        (MatchStatus.PARTIAL_MATCH, 0.5),
        (MatchStatus.REQUIRES_REVIEW, 0.3),
        (MatchStatus.UNMATCHED, 0.0),
    ])
    def test_score_trace_called_with_correct_value(self, patched_runner,
                                                    match_status, expected_score):
        """score_trace_safe() emits the correct RECON_RECONCILIATION_MATCH score."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = match_status

        if match_status == MatchStatus.UNMATCHED:
            runner.po_lookup.lookup.return_value = make_po_result(found=False)

        invoice = make_mock_invoice(pk=10)

        with patch("apps.core.langfuse_client.score_trace") as mock_score, \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            runner.run(invoices=[invoice])

        # Runner now emits multiple score_trace calls per invoice.
        # Find the RECON_RECONCILIATION_MATCH call specifically.
        match_calls = [
            c for c in mock_score.call_args_list
            if len(c.args) >= 2 and c.args[1] == RECON_RECONCILIATION_MATCH
        ]
        assert len(match_calls) == 1, (
            f"Expected 1 {RECON_RECONCILIATION_MATCH} call, got {len(match_calls)}"
        )
        assert match_calls[0].args[2] == expected_score

    def test_score_trace_exception_does_not_break_runner(self, patched_runner):
        """If score_trace() raises, the runner completes normally."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = MatchStatus.MATCHED
        invoice = make_mock_invoice(pk=11)

        with patch("apps.core.langfuse_client.score_trace",
                   side_effect=RuntimeError("Langfuse down")), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            recon_run = runner.run(invoices=[invoice])

        assert recon_run.matched_count == 1  # result unaffected

    def test_score_trace_sdk_error_does_not_break_runner(self, patched_runner):
        """Even a network/SDK error in score_trace() is silently swallowed."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = MatchStatus.MATCHED
        invoice = make_mock_invoice(pk=12)

        broken_client = MagicMock()
        broken_client.create_score.side_effect = RuntimeError("503 Service Unavailable")

        with patch("apps.core.langfuse_client.get_client", return_value=broken_client):
            recon_run = runner.run(invoices=[invoice])

        assert recon_run.matched_count == 1  # reconciliation unaffected


# ─── Langfuse span wrapping — tracing does not alter results ─────────────────

@pytest.mark.django_db
class TestLangfuseSpansDoNotAlterResults:
    def test_mode_resolution_result_unchanged_with_active_langfuse(self, patched_runner):
        """Langfuse span wrapping around mode resolution does not change the mode."""
        runner, config = patched_runner
        expected_mode = ReconciliationMode.THREE_WAY
        runner.mode_resolver.resolve.return_value = make_mode_resolution(
            mode=expected_mode
        )
        runner.classifier.classify.return_value = MatchStatus.MATCHED
        invoice = make_mock_invoice(pk=20)

        mock_span = MagicMock()
        with patch("apps.core.langfuse_client.get_client", return_value=MagicMock()), \
             patch("apps.core.langfuse_client.start_span", return_value=mock_span), \
             patch("apps.core.langfuse_client.end_span"), \
             patch("apps.core.langfuse_client.start_trace", return_value=mock_span), \
             patch("apps.core.langfuse_client.score_trace"), \
             patch("apps.core.langfuse_client.score_observation"):
            recon_run = runner.run(invoices=[invoice])

        # Mode resolver was called exactly once -- spans didn't intercept it
        runner.mode_resolver.resolve.assert_called_once()
        assert recon_run.matched_count == 1

    def test_multiple_invoices_each_scored_independently(self, patched_runner):
        """Each invoice gets its own RECON_RECONCILIATION_MATCH score call."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = MatchStatus.MATCHED
        invoices = [make_mock_invoice(pk=i) for i in range(1, 4)]

        with patch("apps.core.langfuse_client.score_trace") as mock_score, \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            runner.run(invoices=invoices)

        # Filter to only RECON_RECONCILIATION_MATCH calls (one per invoice)
        match_calls = [
            c for c in mock_score.call_args_list
            if len(c.args) >= 2 and c.args[1] == RECON_RECONCILIATION_MATCH
        ]
        assert len(match_calls) == 3  # One per invoice


# ─── Duplicate score prevention ──────────────────────────────────────────────

@pytest.mark.django_db
class TestNoDuplicateScores:
    def test_routed_to_review_emitted_once_per_invoice(self, patched_runner):
        """RECON_ROUTED_TO_REVIEW is emitted only from runner_service (per-invoice),
        not duplicated at the task level."""
        runner, config = patched_runner
        runner.classifier.classify.return_value = MatchStatus.REQUIRES_REVIEW
        invoice = make_mock_invoice(pk=30)

        with patch("apps.core.langfuse_client.score_trace") as mock_score, \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            runner.run(invoices=[invoice])

        review_calls = [
            c for c in mock_score.call_args_list
            if len(c.args) >= 2 and c.args[1] == RECON_ROUTED_TO_REVIEW
        ]
        assert len(review_calls) == 1  # Only from runner, not duplicated


# ─── Error visibility ────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestErrorScoring:
    def test_invoice_error_score_emitted_on_failure(self, patched_runner):
        """When _reconcile_single raises, RECON_INVOICE_ERROR is scored."""
        runner, config = patched_runner
        runner.po_lookup.lookup.side_effect = RuntimeError("PO service down")
        invoice = make_mock_invoice(pk=40)

        with patch("apps.core.langfuse_client.score_trace") as mock_score, \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            recon_run = runner.run(invoices=[invoice])

        assert recon_run.error_count == 1
        error_calls = [
            c for c in mock_score.call_args_list
            if len(c.args) >= 2 and c.args[1] == RECON_INVOICE_ERROR
        ]
        assert len(error_calls) == 1
        assert error_calls[0].args[2] == 1.0  # value

    def test_error_scoring_itself_never_breaks_runner(self, patched_runner):
        """Even if score_trace_safe raises during error scoring, the run completes."""
        runner, config = patched_runner
        runner.po_lookup.lookup.side_effect = RuntimeError("PO service down")
        invoice = make_mock_invoice(pk=41)

        with patch("apps.core.langfuse_client.score_trace",
                   side_effect=RuntimeError("Langfuse also down")), \
             patch("apps.core.langfuse_client.get_client", return_value=None):
            recon_run = runner.run(invoices=[invoice])

        assert recon_run.error_count == 1  # still counted


# ─── Guardrails: score_trace called after guardrail decision ─────────────────

class TestGuardrailsLangfuseScoring:
    """Tests that guardrail decisions emit Langfuse scores without breaking."""

    def test_log_guardrail_decision_score_trace_fail_silent(self):
        """log_guardrail_decision() does not raise if score_trace fails."""
        from apps.agents.services.guardrails_service import AgentGuardrailsService

        user = MagicMock()
        user.pk = 1
        user.email = "test@example.com"

        with patch.object(AgentGuardrailsService, "build_rbac_snapshot",
                          return_value={
                              "actor_user_id": 1,
                              "actor_email": "test@example.com",
                              "actor_primary_role": "AP_PROCESSOR",
                              "actor_roles_snapshot": ["AP_PROCESSOR"],
                              "permission_source": "USER",
                          }), \
             patch.object(AgentGuardrailsService, "build_trace_context_for_agent",
                          return_value=MagicMock(trace_id="trace-001")), \
             patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.score_trace",
                   side_effect=RuntimeError("Langfuse unavailable")):
            # Must not raise
            AgentGuardrailsService.log_guardrail_decision(
                user=user,
                action="orchestration",
                permission_code="agents.orchestrate",
                granted=True,
            )

    def test_guardrail_granted_score_is_1_0(self):
        """When guardrail is granted, score_trace is called with value=1.0."""
        from apps.agents.services.guardrails_service import AgentGuardrailsService

        user = MagicMock()
        user.pk = 1
        user.email = "test@example.com"

        with patch.object(AgentGuardrailsService, "build_rbac_snapshot",
                          return_value={
                              "actor_user_id": 1,
                              "actor_email": "test@example.com",
                              "actor_primary_role": "AP_PROCESSOR",
                              "actor_roles_snapshot": ["AP_PROCESSOR"],
                              "permission_source": "USER",
                          }), \
             patch.object(AgentGuardrailsService, "build_trace_context_for_agent",
                          return_value=MagicMock(trace_id="trace-001")), \
             patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.score_trace") as mock_score:
            AgentGuardrailsService.log_guardrail_decision(
                user=user,
                action="orchestration",
                permission_code="agents.orchestrate",
                granted=True,
            )

        mock_score.assert_called_once()
        score_value = mock_score.call_args[0][2]
        assert score_value == 1.0

    def test_guardrail_denied_score_is_0_0(self):
        """When guardrail is denied, score_trace is called with value=0.0."""
        from apps.agents.services.guardrails_service import AgentGuardrailsService

        user = MagicMock()
        user.pk = 1
        user.email = "test@example.com"

        with patch.object(AgentGuardrailsService, "build_rbac_snapshot",
                          return_value={
                              "actor_user_id": 1,
                              "actor_email": "test@example.com",
                              "actor_primary_role": "REVIEWER",
                              "actor_roles_snapshot": ["REVIEWER"],
                              "permission_source": "USER",
                          }), \
             patch.object(AgentGuardrailsService, "build_trace_context_for_agent",
                          return_value=MagicMock(trace_id="trace-001")), \
             patch("apps.auditlog.services.AuditService.log_event"), \
             patch("apps.core.langfuse_client.score_trace") as mock_score:
            AgentGuardrailsService.log_guardrail_decision(
                user=user,
                action="orchestration",
                permission_code="agents.orchestrate",
                granted=False,
            )

        mock_score.assert_called_once()
        score_value = mock_score.call_args[0][2]
        assert score_value == 0.0
