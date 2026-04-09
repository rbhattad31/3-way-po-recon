"""End-to-end tests: Extraction -> EvalAdapter -> LearningEngine.

These tests verify the full chain:
1. ExtractionEvalAdapter.sync_for_extraction_result() creates EvalRun + metrics
2. ExtractionEvalAdapter.sync_for_approval()  creates LearningSignal records
3. LearningEngine.run()  detects patterns and proposes LearningAction records

No production logic is modified -- the adapter and engine are pure additive
side-channels.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from apps.core.enums import ExtractionApprovalStatus, InvoiceStatus
from apps.core_eval.models import EvalRun, LearningAction, LearningSignal
from apps.core_eval.services.learning_engine import (
    AUTO_APPROVE_RISK_MIN_COUNT,
    FIELD_CORRECTION_MIN_COUNT,
    PROMPT_WEAKNESS_CORRECTION_RATE,
    PROMPT_WEAKNESS_MIN_CORRECTIONS,
    VALIDATION_CLUSTER_MIN_COUNT,
    LearningEngine,
)
from apps.documents.models import DocumentUpload, Invoice
from apps.extraction.models import ExtractionApproval, ExtractionFieldCorrection, ExtractionResult
from apps.extraction_core.models import ExtractionRun
from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_upload(db):
    return DocumentUpload.objects.create(
        original_filename="e2e_test.pdf",
        file_size=2048,
        content_type="application/pdf",
    )


def _make_invoice(upload, *, invoice_number="INV-E2E-001", confidence=0.88, **kw):
    defaults = dict(
        invoice_number=invoice_number,
        currency="USD",
        total_amount=1000,
        status=InvoiceStatus.PENDING_APPROVAL,
        extraction_confidence=confidence,
        document_upload=upload,
        po_number="",
    )
    defaults.update(kw)
    return Invoice.objects.create(**defaults)


def _make_ext_result(upload, invoice, *, raw_response=None, confidence=None):
    run = ExtractionRun.objects.create(
        document_upload=upload,
        overall_confidence=confidence or invoice.extraction_confidence,
        extracted_data_json=raw_response or {},
        status="COMPLETED",
    )
    return ExtractionResult.objects.create(
        document_upload=upload,
        extraction_run=run,
        success=True,
    )


def _make_approval(invoice, ext_result=None, status=ExtractionApprovalStatus.PENDING):
    return ExtractionApproval.objects.create(
        invoice=invoice,
        extraction_result=ext_result,
        status=status,
        confidence_at_review=invoice.extraction_confidence,
    )


def _user(db, email="e2e-tester@example.com"):
    return User.objects.create_user(
        email=email,
        password="testpass123",
        first_name="E2E",
        last_name="Tester",
    )


def _validation_result(is_valid=True, errors=None):
    return SimpleNamespace(
        is_valid=is_valid,
        errors=errors or [],
        requires_review_override=False,
    )


def _dup_result(is_dup=False):
    return SimpleNamespace(
        is_duplicate=is_dup,
        duplicate_invoice_id=None,
        reason="unique",
    )


def _make_correction_record(approval, field_name, original, corrected, user=None):
    """Create an ExtractionFieldCorrection record (the real model)."""
    return ExtractionFieldCorrection.objects.create(
        approval=approval,
        entity_type="header",
        field_name=field_name,
        original_value=original,
        corrected_value=corrected,
        corrected_by=user,
    )


def _sync_extraction_and_approval(
    db,
    *,
    invoice_number="INV-E2E",
    confidence=0.88,
    corrections=None,
    prompt_hash="",
    validation_errors=None,
    trace_id="",
    is_auto_approve=False,
    user=None,
):
    """Run the full adapter chain for one invoice and return key objects.

    corrections: list of (field_name, old_value, new_value)
    validation_errors: list of str (if non-empty, validation_result.is_valid=False)
    """
    upload = _make_upload(db)
    invoice = _make_invoice(
        upload, invoice_number=invoice_number, confidence=confidence,
    )
    ext_result = _make_ext_result(upload, invoice, raw_response={
        "_field_confidence": {"invoice_number": confidence, "total_amount": confidence},
    })

    is_valid = not validation_errors
    val_errors = []
    if validation_errors:
        val_errors = [
            SimpleNamespace(error=e) if isinstance(e, str) else e
            for e in validation_errors
        ]

    val_result = SimpleNamespace(
        is_valid=is_valid,
        errors=val_errors,
        requires_review_override=False,
    )

    # Step 1: sync extraction result (creates EvalRun + metrics + signals)
    ExtractionEvalAdapter.sync_for_extraction_result(
        ext_result,
        invoice,
        validation_result=val_result,
        dup_result=_dup_result(),
        trace_id=trace_id or f"e2e-{invoice_number}",
    )

    # Optionally set prompt_hash on the EvalRun for prompt weakness tests
    if prompt_hash:
        eval_run = EvalRun.objects.filter(
            app_module="extraction",
            entity_id=str(ext_result.pk),
        ).first()
        if eval_run:
            eval_run.prompt_hash = prompt_hash
            eval_run.save(update_fields=["prompt_hash"])

    # Step 2: create approval and sync
    approval = _make_approval(invoice, ext_result)
    if is_auto_approve:
        approval.status = ExtractionApprovalStatus.AUTO_APPROVED
        approval.is_touchless = True
        approval.save(update_fields=["status", "is_touchless"])

    real_user = user or _user(db, email=f"user-{invoice_number}@test.com")

    correction_records = []
    if corrections:
        for field_name, old_val, new_val in corrections:
            rec = _make_correction_record(
                approval, field_name, old_val, new_val, real_user,
            )
            correction_records.append(rec)

    ExtractionEvalAdapter.sync_for_approval(
        approval, real_user, correction_records or None,
    )

    return {
        "upload": upload,
        "invoice": invoice,
        "ext_result": ext_result,
        "approval": approval,
        "user": real_user,
        "correction_records": correction_records,
    }


# ===========================================================================
# E2E: Field Correction Hotspot
# ===========================================================================


@pytest.mark.django_db
class TestE2EFieldCorrectionHotspot:
    """Extraction approvals with corrections -> engine detects hotspot."""

    def test_many_corrections_triggers_hotspot_action(self, db):
        """When >= FIELD_CORRECTION_MIN_COUNT invoices correct the same field,
        LearningEngine should propose a field_normalization_candidate action.
        """
        user = _user(db)
        count = FIELD_CORRECTION_MIN_COUNT + 2

        for i in range(count):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-HOT-{i:04d}",
                corrections=[("total_amount", "1,000.00", "1000.00")],
                user=user,
            )

        # Verify signals exist
        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="field_correction",
            field_name="total_amount",
        )
        assert signals.count() == count

        # Run engine
        engine = LearningEngine(days=7)
        summary = engine.run()

        assert summary.actions_proposed >= 1

        action = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
            app_module="extraction",
        ).first()
        assert action is not None
        assert action.status == LearningAction.Status.PROPOSED
        assert "total_amount" in action.target_description
        assert action.action_payload_json["field_code"] == "total_amount"

    def test_below_threshold_no_action(self, db):
        """Fewer than FIELD_CORRECTION_MIN_COUNT corrections -> no action."""
        user = _user(db)
        count = FIELD_CORRECTION_MIN_COUNT - 5

        for i in range(count):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-BELOW-{i:04d}",
                corrections=[("invoice_number", "OLD", "NEW")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        actions = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        )
        assert actions.count() == 0


# ===========================================================================
# E2E: Prompt Weakness
# ===========================================================================


@pytest.mark.django_db
class TestE2EPromptWeakness:
    """Extraction runs sharing a prompt_hash with high correction rate
    -> engine proposes prompt_review action.
    """

    def test_high_correction_rate_triggers_prompt_review(self, db):
        """When correction_rate > threshold, engine proposes prompt_review."""
        user = _user(db)
        prompt_hash = "abc123deadbeef"
        # We need at least PROMPT_WEAKNESS_MIN_CORRECTIONS corrections
        # and correction_rate > PROMPT_WEAKNESS_CORRECTION_RATE
        #
        # If correction_rate must be > 30%, we need corrections / total_runs > 0.3
        # E.g. 12 corrections / 12 runs = 100% (each run has >= 1 correction)
        num_corrected = max(PROMPT_WEAKNESS_MIN_CORRECTIONS + 2, 12)

        for i in range(num_corrected):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-PROMPT-{i:04d}",
                prompt_hash=prompt_hash,
                corrections=[("vendor_name", f"OLD-{i}", f"CORRECTED-{i}")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        assert summary.actions_proposed >= 1

        action = LearningAction.objects.filter(
            action_type="prompt_review",
            app_module="extraction",
        ).first()
        assert action is not None
        assert action.status == LearningAction.Status.PROPOSED
        assert prompt_hash[:12] in action.target_description
        payload = action.action_payload_json
        assert payload["prompt_hash"] == prompt_hash
        assert "correction_rate" in payload
        assert payload["correction_rate"] > PROMPT_WEAKNESS_CORRECTION_RATE
        assert "correction_count" in payload
        assert payload["correction_count"] >= PROMPT_WEAKNESS_MIN_CORRECTIONS

    def test_low_correction_rate_no_action(self, db):
        """When correction_rate < threshold, no prompt_review action."""
        user = _user(db)
        prompt_hash = "lowrate000000"

        # Create many runs without corrections to dilute the rate
        for i in range(30):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-PLOW-{i:04d}",
                prompt_hash=prompt_hash,
                corrections=None,  # no corrections
                user=user,
            )

        # Add a few with corrections (rate = 3/33 = ~9% < 30%)
        for i in range(3):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-PLOWC-{i:04d}",
                prompt_hash=prompt_hash,
                corrections=[("vendor_name", "X", "Y")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        actions = LearningAction.objects.filter(
            action_type="prompt_review",
        )
        assert actions.count() == 0


# ===========================================================================
# E2E: Auto-Approve Risk
# ===========================================================================


@pytest.mark.django_db
class TestE2EAutoApproveRisk:
    """Auto-approved invoices that are later corrected -> threshold_tune action."""

    def test_auto_approved_then_corrected_triggers_action(self, db):
        """When enough auto-approved items get corrections, engine proposes
        threshold_tune action.
        """
        user = _user(db)
        count = AUTO_APPROVE_RISK_MIN_COUNT + 2

        for i in range(count):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-AUTO-{i:04d}",
                confidence=0.95,
                is_auto_approve=True,
                corrections=[("total_amount", "1000", "1050")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        assert summary.actions_proposed >= 1

        action = LearningAction.objects.filter(
            action_type="threshold_tune",
        ).first()
        assert action is not None
        assert "auto-approved" in action.target_description
        payload = action.action_payload_json
        assert payload["risk_count"] >= AUTO_APPROVE_RISK_MIN_COUNT

    def test_auto_approved_no_corrections_no_action(self, db):
        """Auto-approved without subsequent corrections -> no risk action."""
        user = _user(db)

        for i in range(AUTO_APPROVE_RISK_MIN_COUNT + 2):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-AUTOCLEAN-{i:04d}",
                confidence=0.96,
                is_auto_approve=True,
                corrections=None,
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        actions = LearningAction.objects.filter(action_type="threshold_tune")
        assert actions.count() == 0


# ===========================================================================
# E2E: Validation Failure Cluster
# ===========================================================================


@pytest.mark.django_db
class TestE2EValidationFailureCluster:
    """Repeated validation failures with the same error pattern -> action."""

    def test_repeated_validation_errors_trigger_action(self, db):
        """When the same error appears >= threshold times, engine proposes
        validation_rule_candidate action.
        """
        user = _user(db)
        count = VALIDATION_CLUSTER_MIN_COUNT + 2
        error_text = "Missing required field: po_number"

        for i in range(count):
            upload = _make_upload(db)
            invoice = _make_invoice(
                upload, invoice_number=f"INV-VAL-{i:04d}",
            )
            ext_result = _make_ext_result(upload, invoice)

            val_result = SimpleNamespace(
                is_valid=False,
                errors=[error_text],
                requires_review_override=False,
            )

            ExtractionEvalAdapter.sync_for_extraction_result(
                ext_result,
                invoice,
                validation_result=val_result,
                dup_result=_dup_result(),
                trace_id=f"e2e-val-{i}",
            )

        # Verify validation_failure signals exist
        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="validation_failure",
        )
        assert signals.count() >= count

        engine = LearningEngine(days=7)
        summary = engine.run()

        assert summary.actions_proposed >= 1

        action = LearningAction.objects.filter(
            action_type="validation_rule_candidate",
            app_module="extraction",
        ).first()
        assert action is not None
        assert action.status == LearningAction.Status.PROPOSED
        payload = action.action_payload_json
        assert payload["error_pattern"] == error_text
        assert "occurrence_count" in payload
        assert payload["occurrence_count"] >= VALIDATION_CLUSTER_MIN_COUNT


# ===========================================================================
# E2E: Full Pipeline Round-Trip
# ===========================================================================


@pytest.mark.django_db
class TestE2EFullPipelineRoundTrip:
    """Verify the entire chain: extraction -> eval records -> engine -> actions,
    and that actions are well-formed and queryable.
    """

    def test_mixed_signals_produce_correct_actions(self, db):
        """Create a mix of field corrections + auto-approve risk signals,
        run the engine, and verify the right actions are proposed.
        """
        user = _user(db)

        # -- Generate field correction hotspot on 'currency' --
        for i in range(FIELD_CORRECTION_MIN_COUNT + 1):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-MIX-FC-{i:04d}",
                corrections=[("currency", "USD", "EUR")],
                user=user,
            )

        # -- Generate auto-approve risk --
        for i in range(AUTO_APPROVE_RISK_MIN_COUNT + 1):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-MIX-AA-{i:04d}",
                confidence=0.95,
                is_auto_approve=True,
                corrections=[("total_amount", "100", "110")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run()

        # At least one correction hotspot + one threshold tune
        assert summary.actions_proposed >= 2

        fc_action = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).first()
        assert fc_action is not None
        assert fc_action.action_payload_json["field_code"] == "currency"

        tt_action = LearningAction.objects.filter(
            action_type="threshold_tune",
        ).first()
        assert tt_action is not None
        assert tt_action.status == LearningAction.Status.PROPOSED
        tt_payload = tt_action.action_payload_json
        assert "risk_count" in tt_payload
        assert tt_payload["risk_count"] >= AUTO_APPROVE_RISK_MIN_COUNT

    def test_idempotent_engine_run(self, db):
        """Running the engine twice on the same data does not create duplicate
        actions (dedup by dedup_key in target_description).
        """
        user = _user(db)

        for i in range(FIELD_CORRECTION_MIN_COUNT + 1):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-IDEM-{i:04d}",
                corrections=[("vendor_name", "Old Vendor", "New Vendor")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary1 = engine.run()
        assert summary1.actions_proposed >= 1

        # Second run -- should be fully deduped
        engine2 = LearningEngine(days=7)
        summary2 = engine2.run()
        assert summary2.actions_skipped_dedup >= 1
        # No new actions created on second run
        assert summary2.actions_proposed == 0

    def test_dry_run_creates_no_records(self, db):
        """dry_run=True should detect patterns but NOT write LearningActions."""
        user = _user(db)

        for i in range(FIELD_CORRECTION_MIN_COUNT + 1):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-DRY-{i:04d}",
                corrections=[("vendor_name", "A", "B")],
                user=user,
            )

        engine = LearningEngine(days=7)
        summary = engine.run(dry_run=True)

        assert summary.actions_proposed >= 1
        assert LearningAction.objects.count() == 0

    def test_eval_run_links_to_learning_signals(self, db):
        """Verify that LearningSignals created by the adapter have a valid
        eval_run FK pointing to the extraction EvalRun.
        """
        user = _user(db)
        result = _sync_extraction_and_approval(
            db,
            invoice_number="INV-LINK-001",
            corrections=[("total_amount", "1000", "1050")],
            user=user,
        )

        ext_result = result["ext_result"]
        eval_run = EvalRun.objects.filter(
            app_module="extraction",
            entity_id=str(ext_result.pk),
        ).first()

        # The field_correction signal should have eval_run set
        fc_signal = LearningSignal.objects.filter(
            signal_type="field_correction",
            entity_id=str(result["invoice"].pk),
        ).first()
        assert fc_signal is not None
        assert fc_signal.eval_run == eval_run

        # The approval_outcome signal should also link to the same eval_run
        ao_signal = LearningSignal.objects.filter(
            signal_type="approval_outcome",
            entity_id=str(result["invoice"].pk),
        ).first()
        assert ao_signal is not None
        assert ao_signal.eval_run == eval_run

    def test_action_payload_contains_examples(self, db):
        """Proposed field_normalization_candidate action should include
        correction examples in action_payload_json.
        """
        user = _user(db)

        for i in range(FIELD_CORRECTION_MIN_COUNT + 1):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-EX-{i:04d}",
                corrections=[("invoice_date", "01-Jan-2026", "2026-01-01")],
                user=user,
            )

        engine = LearningEngine(days=7)
        engine.run()

        action = LearningAction.objects.filter(
            action_type="field_normalization_candidate",
        ).first()
        assert action is not None

        payload = action.action_payload_json
        assert "top_corrected_values" in payload
        assert len(payload["top_corrected_values"]) >= 1
        assert payload["top_corrected_values"][0]["value"] == "2026-01-01"

        assert "examples" in payload
        assert len(payload["examples"]) >= 1

    def test_module_filter_respects_boundary(self, db):
        """Engine with module='extraction' should only process extraction signals;
        module='reconciliation' should find nothing and propose no actions.
        """
        user = _user(db)

        for i in range(FIELD_CORRECTION_MIN_COUNT + 1):
            _sync_extraction_and_approval(
                db,
                invoice_number=f"INV-MOD-{i:04d}",
                corrections=[("total_amount", "X", "Y")],
                user=user,
            )

        engine = LearningEngine(days=7)

        # Extraction module should find signals
        summary_ext = engine.run(module="extraction")
        assert summary_ext.signals_scanned > 0

        # Reconciliation module should find nothing
        engine2 = LearningEngine(days=7)
        summary_recon = engine2.run(module="reconciliation")
        assert summary_recon.signals_scanned == 0
        assert summary_recon.actions_proposed == 0
