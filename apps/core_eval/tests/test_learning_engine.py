"""Tests for LearningEngine -- controlled learning engine.

Covers:
- Signal aggregation helpers (by_key, by_field, by_module, by_prompt)
- Rule 1: field correction hotspot detection + action proposal
- Rule 2: prompt weakness detection
- Rule 3: auto-approve risk detection
- Rule 4: validation failure cluster detection
- Rule 5: vendor-specific issue detection
- Dedup: no duplicate open actions
- Cooldown: no re-proposal within cooldown window
- Dry-run: no DB writes in dry-run mode
- Idempotency: repeated runs do not create duplicates
- Management command: runs without error
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core_eval.models import EvalRun, LearningAction, LearningSignal
from apps.core_eval.services.learning_engine import (
    FIELD_CORRECTION_MIN_COUNT,
    VALIDATION_CLUSTER_MIN_COUNT,
    VENDOR_ISSUE_MIN_COUNT,
    AUTO_APPROVE_RISK_MIN_COUNT,
    LearningEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bulk_signals(
    count: int,
    *,
    app_module: str = "extraction",
    signal_type: str = "field_correction",
    field_name: str = "total_amount",
    aggregation_key: str = "",
    entity_type: str = "Invoice",
    old_value: str = "100",
    new_value: str = "200",
    confidence: float = 0.85,
    payload_json: dict | None = None,
    eval_run: EvalRun | None = None,
) -> list[LearningSignal]:
    """Create N LearningSignal records."""
    objs = []
    for i in range(count):
        objs.append(LearningSignal(
            app_module=app_module,
            signal_type=signal_type,
            field_name=field_name,
            entity_type=entity_type,
            entity_id=str(1000 + i),
            aggregation_key=aggregation_key,
            old_value=old_value,
            new_value=new_value,
            confidence=confidence,
            payload_json=payload_json or {},
            eval_run=eval_run,
        ))
    return LearningSignal.objects.bulk_create(objs)


def _make_eval_run(*, prompt_hash: str = "abc123", app_module: str = "extraction"):
    return EvalRun.objects.create(
        app_module=app_module,
        entity_type="ExtractionResult",
        entity_id="1",
        prompt_hash=prompt_hash,
        status=EvalRun.Status.COMPLETED,
    )


# ---------------------------------------------------------------------------
# Aggregation tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSignalAggregation:

    def test_aggregate_by_key(self, db):
        _bulk_signals(5, aggregation_key="approval-42", confidence=0.9)
        engine = LearningEngine(days=7)
        result = engine.aggregate_signals_by_key("approval-42")
        assert result["total_count"] == 5
        assert result["avg_confidence"] == 0.9
        assert result["unique_entities"] == 5
        assert len(result["sample_payloads"]) <= 5

    def test_aggregate_by_field(self, db):
        _bulk_signals(8, field_name="invoice_number", new_value="INV-FIXED")
        engine = LearningEngine(days=7)
        result = engine.aggregate_signals_by_field("invoice_number")
        assert result["total_count"] == 8
        assert result["field_code"] == "invoice_number"
        assert len(result["top_corrected_values"]) >= 1
        assert result["top_corrected_values"][0]["new_value"] == "INV-FIXED"

    def test_aggregate_by_module(self, db):
        _bulk_signals(3, signal_type="field_correction")
        _bulk_signals(2, signal_type="validation_failure")
        engine = LearningEngine(days=7)
        result = engine.aggregate_signals_by_module("extraction")
        assert result["total_count"] == 5
        assert len(result["by_signal_type"]) == 2

    def test_aggregate_by_prompt(self, db):
        run = _make_eval_run(prompt_hash="deadbeef1234")
        _bulk_signals(4, eval_run=run)
        engine = LearningEngine(days=7)
        result = engine.aggregate_signals_by_prompt("deadbeef1234")
        assert result["total_count"] == 4
        assert result["prompt_hash"] == "deadbeef1234"

    def test_aggregate_respects_time_window(self, db):
        """Signals older than the time window are excluded."""
        signals = _bulk_signals(3)
        # Backdate one signal beyond the window
        old = signals[0]
        LearningSignal.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )
        engine = LearningEngine(days=7)
        result = engine.aggregate_signals_by_field("total_amount")
        assert result["total_count"] == 2

    def test_aggregate_respects_min_confidence(self, db):
        _bulk_signals(3, confidence=0.9)
        _bulk_signals(2, confidence=0.1)
        engine = LearningEngine(days=7, min_confidence=0.5)
        result = engine.aggregate_signals_by_module("extraction")
        assert result["total_count"] == 3


# ---------------------------------------------------------------------------
# Rule 1: field correction hotspot
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFieldCorrectionHotspot:

    def test_creates_action_above_threshold(self, db):
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, field_name="total_amount")
        engine = LearningEngine(days=7)
        summary = engine.run()
        assert summary.actions_proposed >= 1
        action = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).first()
        assert action is not None
        assert action.action_payload_json["field_code"] == "total_amount"
        assert action.status == LearningAction.Status.PROPOSED

    def test_no_action_below_threshold(self, db):
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT - 1, field_name="total_amount")
        engine = LearningEngine(days=7)
        summary = engine.run()
        assert LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).count() == 0


# ---------------------------------------------------------------------------
# Rule 2: prompt weakness
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPromptWeakness:

    def test_creates_action_for_high_correction_rate(self, db):
        run = _make_eval_run(prompt_hash="weakprompt123")
        # Create 10 corrections (all linked to this prompt)
        _bulk_signals(10, eval_run=run, signal_type="field_correction")
        # Total runs with this prompt = 1 -> correction_rate = 10/1 = 1000%
        # Well above 30% threshold

        engine = LearningEngine(days=7)
        summary = engine.run()

        action = LearningAction.objects.filter(
            action_type="prompt_review",
        ).first()
        assert action is not None
        assert "weakprompt123" in action.action_payload_json["prompt_hash"]

    def test_no_action_for_low_correction_rate(self, db):
        run = _make_eval_run(prompt_hash="goodprompt456")
        # Create 10 corrections but 100 eval runs -> 10% rate (below 30%)
        _bulk_signals(10, eval_run=run, signal_type="field_correction")
        # Create 99 more eval runs with same prompt_hash
        for i in range(99):
            EvalRun.objects.create(
                app_module="extraction",
                entity_type="ExtractionResult",
                entity_id=str(2000 + i),
                prompt_hash="goodprompt456",
                status=EvalRun.Status.COMPLETED,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        assert LearningAction.objects.filter(
            action_type="prompt_review",
        ).count() == 0


# ---------------------------------------------------------------------------
# Rule 3: auto-approve risk
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestAutoApproveRisk:

    def test_creates_action_when_corrected_after_auto_approve(self, db):
        # Create auto-approve signals for N entities
        for i in range(AUTO_APPROVE_RISK_MIN_COUNT + 2):
            LearningSignal.objects.create(
                app_module="extraction",
                signal_type="auto_approve_outcome",
                entity_type="Invoice",
                entity_id=str(3000 + i),
                confidence=0.95,
            )
            # Also create correction signals for these same entities
            LearningSignal.objects.create(
                app_module="extraction",
                signal_type="field_correction",
                entity_type="Invoice",
                entity_id=str(3000 + i),
                field_name="total_amount",
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        action = LearningAction.objects.filter(
            action_type="threshold_tune",
        ).first()
        assert action is not None
        assert action.action_payload_json["risk_count"] >= AUTO_APPROVE_RISK_MIN_COUNT

    def test_no_action_when_few_risky_entities(self, db):
        # Only 2 auto-approved + corrected (below threshold)
        for i in range(2):
            LearningSignal.objects.create(
                app_module="extraction",
                signal_type="auto_approve_outcome",
                entity_type="Invoice",
                entity_id=str(4000 + i),
                confidence=0.95,
            )
            LearningSignal.objects.create(
                app_module="extraction",
                signal_type="field_correction",
                entity_type="Invoice",
                entity_id=str(4000 + i),
                field_name="total_amount",
            )

        engine = LearningEngine(days=7)
        engine.run()

        assert LearningAction.objects.filter(
            action_type="threshold_tune",
        ).count() == 0


# ---------------------------------------------------------------------------
# Rule 4: validation failure cluster
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestValidationFailureCluster:

    def test_creates_action_for_repeated_errors(self, db):
        _bulk_signals(
            VALIDATION_CLUSTER_MIN_COUNT,
            signal_type="validation_failure",
            field_name="",
            payload_json={"error": "Missing total_amount field"},
        )
        engine = LearningEngine(days=7)
        summary = engine.run()

        action = LearningAction.objects.filter(
            action_type="validation_rule_candidate",
        ).first()
        assert action is not None
        assert "Missing total_amount" in action.action_payload_json["error_pattern"]


# ---------------------------------------------------------------------------
# Rule 5: vendor-specific issue
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestVendorSpecificIssue:

    def test_creates_action_for_vendor_cluster(self, db):
        _bulk_signals(
            VENDOR_ISSUE_MIN_COUNT,
            aggregation_key="vendor::ACME_CORP",
            field_name="invoice_number",
        )
        engine = LearningEngine(days=7)
        summary = engine.run()

        action = LearningAction.objects.filter(
            action_type="vendor_rule_candidate",
        ).first()
        assert action is not None
        assert action.action_payload_json["aggregation_key"] == "vendor::ACME_CORP"


# ---------------------------------------------------------------------------
# Safety controls
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSafetyControls:

    def test_dedup_no_duplicate_open_actions(self, db):
        """Running engine twice does not create duplicate PROPOSED actions."""
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, field_name="total_amount")

        engine = LearningEngine(days=7)
        s1 = engine.run()
        s2 = engine.run()

        assert s1.actions_proposed >= 1
        assert s2.actions_skipped_dedup >= 1
        assert LearningAction.objects.filter(
            action_type="field_normalization_candidate",
            status=LearningAction.Status.PROPOSED,
        ).count() == 1

    def test_cooldown_after_rejection(self, db):
        """After an action is rejected, cooldown prevents re-proposal."""
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, field_name="total_amount")

        engine = LearningEngine(days=7, cooldown_days=3)
        engine.run()

        # Reject the proposed action
        action = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).first()
        action.status = LearningAction.Status.REJECTED
        action.save()

        # Run again -- should hit cooldown
        s2 = engine.run()
        assert s2.actions_skipped_cooldown >= 1
        # Still only 1 action total
        assert LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).count() == 1

    def test_dry_run_no_db_writes(self, db):
        """dry_run=True should not create any LearningAction records."""
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, field_name="total_amount")

        engine = LearningEngine(days=7)
        summary = engine.run(dry_run=True)

        assert summary.actions_proposed >= 1
        assert LearningAction.objects.count() == 0

    def test_module_filter(self, db):
        """Only signals from the specified module are processed."""
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, app_module="extraction")
        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, app_module="posting", field_name="vendor_code")

        engine = LearningEngine(days=7)
        summary = engine.run(module="extraction")

        actions = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        )
        # Should only have extraction action, not posting
        modules = set(actions.values_list("app_module", flat=True))
        assert "extraction" in modules
        assert "posting" not in modules

    def test_old_signals_excluded(self, db):
        """Signals outside the time window are not considered."""
        signals = _bulk_signals(FIELD_CORRECTION_MIN_COUNT, field_name="total_amount")
        # Backdate all signals to 30 days ago
        LearningSignal.objects.filter(
            pk__in=[s.pk for s in signals],
        ).update(created_at=timezone.now() - timedelta(days=30))

        engine = LearningEngine(days=7)
        summary = engine.run()

        assert summary.signals_scanned == 0
        assert summary.actions_proposed == 0


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestManagementCommand:

    def test_command_runs_without_error(self, db):
        """The management command should execute cleanly with no signals."""
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("run_learning_engine", "--dry-run", stdout=out)
        output = out.getvalue()
        assert "DRY RUN" in output
        assert "Running learning engine" in output

    def test_command_with_module_filter(self, db):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command(
            "run_learning_engine",
            "--module", "extraction",
            "--days", "14",
            "--dry-run",
            stdout=out,
        )
        output = out.getvalue()
        assert "module=extraction" in output

    def test_command_proposes_actions(self, db):
        """Command should propose actions when signals exist."""
        from django.core.management import call_command
        from io import StringIO

        _bulk_signals(FIELD_CORRECTION_MIN_COUNT, field_name="total_amount")

        out = StringIO()
        call_command("run_learning_engine", stdout=out)
        output = out.getvalue()
        assert "proposed" in output.lower() or "PROPOSED" in output
        assert LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).count() == 1
