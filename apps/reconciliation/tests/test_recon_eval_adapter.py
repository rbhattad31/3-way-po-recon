"""Tests for ReconciliationEvalAdapter -- reconciliation <-> core_eval bridge.

Covers:
- sync_for_result creates EvalRun + predicted metrics
- Actual metrics remain blank when no review outcome exists
- Review outcome sync updates actual metrics
- wrong_match_status_prediction signal when predicted != actual
- wrong_auto_close_prediction signal when predicted != actual
- Rerun idempotency (upsert, not duplicate)
- Adapter remains safe when optional review data is missing
- EvalFieldOutcome created for structured reviewer corrections
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.core.enums import (
    InvoiceStatus,
    MatchStatus,
    ReconciliationRunStatus,
    ReviewActionType,
    ReviewStatus,
)
from apps.core_eval.models import (
    EvalFieldOutcome,
    EvalMetric,
    EvalRun,
    LearningSignal,
)
from apps.documents.models import Invoice
from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationResult,
    ReconciliationRun,
)
from apps.reviews.models import (
    ManualReviewAction,
    ReviewAssignment,
    ReviewDecision,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(db):
    return ReconciliationConfig.objects.create(
        name="eval-test-config",
        is_default=True,
    )


def _make_run(config, **overrides):
    defaults = dict(
        status=ReconciliationRunStatus.COMPLETED,
        config=config,
        total_invoices=1,
        matched_count=0,
        langfuse_trace_id="test-trace-123",
    )
    defaults.update(overrides)
    return ReconciliationRun.objects.create(**defaults)


def _make_invoice(**overrides):
    defaults = dict(
        invoice_number="INV-EVAL-001",
        currency="SAR",
        total_amount=Decimal("1000.00"),
        status=InvoiceStatus.RECONCILED,
        extraction_confidence=0.88,
    )
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


def _make_result(run, invoice, **overrides):
    defaults = dict(
        run=run,
        invoice=invoice,
        match_status=MatchStatus.PARTIAL_MATCH,
        requires_review=True,
        grn_available=False,
        reconciliation_mode="THREE_WAY",
        total_amount_difference=Decimal("50.00"),
        total_amount_difference_pct=Decimal("5.00"),
    )
    defaults.update(overrides)
    return ReconciliationResult.objects.create(**defaults)


def _make_assignment(result, **overrides):
    defaults = dict(
        reconciliation_result=result,
        status=ReviewStatus.PENDING,
        priority=3,
    )
    defaults.update(overrides)
    return ReviewAssignment.objects.create(**defaults)


def _make_decision(assignment, user, decision=ReviewStatus.APPROVED, reason=""):
    return ReviewDecision.objects.create(
        assignment=assignment,
        decided_by=user,
        decision=decision,
        reason=reason,
    )


def _make_user():
    return User.objects.create_user(
        email="reviewer@test.com",
        password="testpass123",
    )


# ---------------------------------------------------------------------------
# Tests: sync_for_result
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSyncForResult:
    """Tests for ReconciliationEvalAdapter.sync_for_result."""

    def test_creates_eval_run(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result, trace_id="test-trace-abc")

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_type="ReconciliationResult",
            entity_id=str(result.pk),
        )
        assert eval_run.status == EvalRun.Status.COMPLETED
        assert eval_run.trace_id == "test-trace-abc"
        assert eval_run.run_key == f"reconciliation_result::{result.pk}"
        assert eval_run.result_json["predicted"]["match_status"] == "PARTIAL_MATCH"
        assert eval_run.result_json["predicted"]["requires_review"] is True
        assert eval_run.result_json["actual"] == {}

    def test_stores_predicted_metrics(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice, match_status=MatchStatus.MATCHED, requires_review=False)

        ReconciliationEvalAdapter.sync_for_result(result)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        metrics = {m.metric_name: m for m in EvalMetric.objects.filter(eval_run=eval_run)}

        assert "recon_predicted_match_status" in metrics
        assert metrics["recon_predicted_match_status"].string_value == "MATCHED"
        assert metrics["recon_predicted_requires_review"].metric_value == 0.0
        assert metrics["recon_predicted_auto_close"].metric_value == 1.0
        assert metrics["recon_predicted_po_found"].metric_value == 0.0  # no PO set

    def test_stores_runtime_metrics(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        metrics = {m.metric_name: m for m in EvalMetric.objects.filter(eval_run=eval_run)}

        assert "reconciliation_match" in metrics
        assert metrics["reconciliation_match"].metric_value == 0.5  # PARTIAL_MATCH
        assert "recon_po_found" in metrics
        assert "recon_auto_close_eligible" in metrics
        assert "recon_exception_count_final" in metrics

    def test_actual_metrics_blank_without_review(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        metrics = {m.metric_name for m in EvalMetric.objects.filter(eval_run=eval_run)}

        # Actual metrics should NOT exist yet
        assert "recon_actual_match_status" not in metrics
        assert "recon_review_outcome" not in metrics
        assert "recon_match_status_correct" not in metrics

    def test_uses_langfuse_trace_from_run(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config, langfuse_trace_id="run-level-trace-456")
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        assert eval_run.trace_id == "run-level-trace-456"

    def test_input_snapshot_populated(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        snap = eval_run.input_snapshot_json
        assert snap["invoice_id"] == invoice.pk
        assert snap["reconciliation_mode"] == "THREE_WAY"
        assert snap["grn_available"] is False


# ---------------------------------------------------------------------------
# Tests: sync_for_review_outcome
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSyncForReviewOutcome:
    """Tests for review outcome syncing."""

    def _setup_with_result_and_review(self, match_status=MatchStatus.PARTIAL_MATCH):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice, match_status=match_status, requires_review=True)

        # Sync initial result
        ReconciliationEvalAdapter.sync_for_result(result)

        user = _make_user()
        assignment = _make_assignment(result)

        return config, run, invoice, result, user, assignment

    def test_review_approved_updates_actual_metrics(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review()

        # Simulate review finalization (match_status updated by _finalise)
        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.APPROVED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.APPROVED, "Looks good")

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        metrics = {m.metric_name: m for m in EvalMetric.objects.filter(eval_run=eval_run)}

        assert "recon_actual_match_status" in metrics
        assert metrics["recon_actual_match_status"].string_value == "MATCHED"
        assert "recon_review_outcome" in metrics
        assert metrics["recon_review_outcome"].string_value == "APPROVED"
        assert "recon_reprocessed" in metrics
        assert metrics["recon_reprocessed"].metric_value == 0.0

    def test_wrong_match_status_signal_created(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review(
            match_status=MatchStatus.PARTIAL_MATCH,
        )

        # Review resulted in MATCHED (predicted was PARTIAL_MATCH)
        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.APPROVED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.APPROVED)

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        signals = LearningSignal.objects.filter(
            app_module="reconciliation",
            signal_type="wrong_match_status_prediction",
            entity_id=str(result.pk),
        )
        assert signals.count() == 1
        sig = signals.first()
        assert sig.payload_json["predicted"] == "PARTIAL_MATCH"
        assert sig.payload_json["actual"] == "MATCHED"
        assert sig.confidence == 0.9

    def test_wrong_auto_close_signal_created(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review(
            match_status=MatchStatus.REQUIRES_REVIEW,
        )

        # Review approved without corrections -> actual_auto_close = True
        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.APPROVED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.APPROVED)

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        # Predicted auto_close was False (REQUIRES_REVIEW != MATCHED)
        # Actual auto_close is True (approved with 0 corrections)
        signals = LearningSignal.objects.filter(
            app_module="reconciliation",
            signal_type="wrong_auto_close_prediction",
            entity_id=str(result.pk),
        )
        assert signals.count() == 1

    def test_match_status_correct_metric(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        # Create a MATCHED result (predicted == actual)
        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(
            run, invoice, match_status=MatchStatus.REQUIRES_REVIEW, requires_review=True,
        )
        ReconciliationEvalAdapter.sync_for_result(result)

        user = _make_user()
        assignment = _make_assignment(result)

        # Rejected -> UNMATCHED
        result.match_status = MatchStatus.UNMATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.REJECTED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.REJECTED)

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        metrics = {m.metric_name: m for m in EvalMetric.objects.filter(eval_run=eval_run)}

        assert "recon_match_status_correct" in metrics
        # REQUIRES_REVIEW != UNMATCHED
        assert metrics["recon_match_status_correct"].metric_value == 0.0

    def test_reprocess_signal_created(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review()

        assignment.status = ReviewStatus.REPROCESSED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.REPROCESSED, "Need to re-run")

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        signals = LearningSignal.objects.filter(
            app_module="reconciliation",
            signal_type="reprocess_signal",
            entity_id=str(result.pk),
        )
        assert signals.count() == 1

    def test_review_with_corrections_creates_override_signal(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review()

        # Add a field correction
        ManualReviewAction.objects.create(
            assignment=assignment,
            performed_by=user,
            action_type=ReviewActionType.CORRECT_FIELD,
            field_name="po_number",
            old_value="PO-001",
            new_value="PO-002",
        )

        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.APPROVED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.APPROVED)

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        signals = LearningSignal.objects.filter(
            app_module="reconciliation",
            signal_type="review_override",
            entity_id=str(result.pk),
        )
        assert signals.count() == 1

        metrics = {m.metric_name: m for m in EvalMetric.objects.filter(
            eval_run__entity_id=str(result.pk),
        )}
        assert metrics["recon_corrected_by_reviewer"].metric_value == 1.0

    def test_field_outcomes_from_corrections(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review()

        ManualReviewAction.objects.create(
            assignment=assignment,
            performed_by=user,
            action_type=ReviewActionType.CORRECT_FIELD,
            field_name="vendor_code",
            old_value="V001",
            new_value="V002",
        )

        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.APPROVED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.APPROVED)

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        outcomes = EvalFieldOutcome.objects.filter(eval_run=eval_run)
        assert outcomes.count() == 1
        outcome = outcomes.first()
        assert outcome.field_name == "vendor_code"
        assert outcome.predicted_value == "V001"
        assert outcome.ground_truth_value == "V002"
        assert outcome.status == EvalFieldOutcome.Status.INCORRECT

    def test_result_json_updated_with_actual(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        _, _, _, result, user, assignment = self._setup_with_result_and_review()

        result.match_status = MatchStatus.MATCHED
        result.requires_review = False
        result.save(update_fields=["match_status", "requires_review", "updated_at"])

        assignment.status = ReviewStatus.APPROVED
        assignment.save(update_fields=["status", "updated_at"])
        _make_decision(assignment, user, ReviewStatus.APPROVED)

        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        actual = eval_run.result_json.get("actual", {})
        assert actual["match_status"] == "MATCHED"
        assert actual["review_outcome"] == "APPROVED"
        assert actual["reprocessed"] is False


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIdempotency:
    """Ensure reruns do not create duplicates."""

    def test_rerun_sync_for_result_no_duplicate_eval_run(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)
        ReconciliationEvalAdapter.sync_for_result(result)

        count = EvalRun.objects.filter(
            app_module="reconciliation",
            entity_id=str(result.pk),
        ).count()
        assert count == 1

    def test_rerun_sync_for_result_no_duplicate_metrics(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)
        ReconciliationEvalAdapter.sync_for_result(result)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        # Each metric name should appear exactly once
        metric_names = list(
            EvalMetric.objects.filter(eval_run=eval_run)
            .values_list("metric_name", flat=True)
        )
        assert len(metric_names) == len(set(metric_names))


# ---------------------------------------------------------------------------
# Tests: fail-safety
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFailSafety:
    """Adapter must never raise."""

    def test_sync_for_result_with_none(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter
        # Should not raise
        ReconciliationEvalAdapter.sync_for_result(None)

    def test_sync_for_review_outcome_without_eval_run(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        # No sync_for_result call -- no EvalRun exists
        user = _make_user()
        assignment = _make_assignment(result)
        _make_decision(assignment, user, ReviewStatus.APPROVED)

        # Should not raise
        ReconciliationEvalAdapter.sync_for_review_outcome(assignment)

        # No eval metrics should exist for this result
        assert EvalMetric.objects.filter(
            eval_run__entity_id=str(result.pk),
        ).count() == 0

    def test_sync_for_review_assignment_without_eval_run(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)
        assignment = _make_assignment(result)

        # Should not raise even without prior sync_for_result
        ReconciliationEvalAdapter.sync_for_review_assignment(assignment)

    def test_sync_for_reprocess_without_eval_run(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        # Should not raise
        ReconciliationEvalAdapter.sync_for_reprocess(result)


# ---------------------------------------------------------------------------
# Tests: sync_for_review_assignment
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSyncForReviewAssignment:
    """Tests for review assignment sync."""

    def test_updates_actual_review_created_metric(self):
        from apps.reconciliation.services.eval_adapter import ReconciliationEvalAdapter

        config = _make_config(True)
        run = _make_run(config)
        invoice = _make_invoice()
        result = _make_result(run, invoice)

        ReconciliationEvalAdapter.sync_for_result(result)
        assignment = _make_assignment(result)
        ReconciliationEvalAdapter.sync_for_review_assignment(assignment)

        eval_run = EvalRun.objects.get(
            app_module="reconciliation",
            entity_id=str(result.pk),
        )
        metrics = {m.metric_name: m for m in EvalMetric.objects.filter(eval_run=eval_run)}
        assert "recon_actual_review_created" in metrics
        assert metrics["recon_actual_review_created"].metric_value == 1.0

        # result_json.actual should have review_created
        actual = eval_run.result_json.get("actual", {})
        assert actual.get("review_created") is True
        assert actual.get("review_assignment_id") == assignment.pk
