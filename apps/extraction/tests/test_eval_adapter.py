"""Tests for ExtractionEvalAdapter -- extraction <-> core_eval bridge.

Covers:
- sync_for_extraction_result creates EvalRun + metrics + field outcomes
- Rerun idempotency (upsert, not duplicate)
- sync_for_approval creates learning signals + updates metrics
- Field correction signals are recorded
- Legacy (no governed data) field outcomes use _field_confidence
- Fail-silent: adapter never raises
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.core.enums import ExtractionApprovalStatus, InvoiceStatus
from apps.core_eval.models import EvalFieldOutcome, EvalMetric, EvalRun, LearningSignal
from apps.documents.models import DocumentUpload, Invoice
from apps.extraction.models import ExtractionApproval, ExtractionFieldCorrection, ExtractionResult
from apps.extraction_core.models import ExtractionRun

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_upload(db):
    return DocumentUpload.objects.create(
        original_filename="test.pdf",
        file_size=1024,
        content_type="application/pdf",
    )


def _make_invoice(upload, **overrides):
    defaults = dict(
        invoice_number="INV-EVAL-001",
        currency="USD",
        total_amount=1000,
        status=InvoiceStatus.EXTRACTED,
        extraction_confidence=0.88,
        document_upload=upload,
        po_number="",
    )
    defaults.update(overrides)
    return Invoice.objects.create(**defaults)


def _make_ext_result(upload, invoice, raw_response=None):
    run = ExtractionRun.objects.create(
        document_upload=upload,
        overall_confidence=0.88,
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


def _validation_result(is_valid=True, errors=None):
    return SimpleNamespace(
        is_valid=is_valid,
        errors=errors or [],
        requires_review_override=False,
    )


def _field_conf_result(weakest=0.6, weakest_field="total_amount", low_fields=None):
    return SimpleNamespace(
        weakest_critical_score=weakest,
        weakest_critical_field=weakest_field,
        low_confidence_fields=low_fields or [],
    )


def _dup_result(is_dup=False, reason="unique"):
    return SimpleNamespace(
        is_duplicate=is_dup,
        duplicate_invoice_id=None,
        reason=reason,
    )


def _extraction_resp(was_repaired=False, qr_data=None, raw_json=None):
    return SimpleNamespace(
        was_repaired=was_repaired,
        repair_actions=[],
        qr_data=qr_data,
        raw_json=raw_json or {},
    )


# ---------------------------------------------------------------------------
# Tests: sync_for_extraction_result
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSyncForExtractionResult:
    """EvalRun + metrics creation after extraction persistence."""

    def test_creates_eval_run_and_metrics(self, db):
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice, raw_response={
            "_field_confidence": {
                "invoice_number": 0.95,
                "total_amount": 0.80,
            },
        })

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_extraction_result(
            ext_result,
            invoice,
            validation_result=_validation_result(),
            field_conf_result=_field_conf_result(),
            dup_result=_dup_result(),
            decision_codes=["LOW_CONFIDENCE", "MISSING_PO"],
            extraction_resp=_extraction_resp(),
            trace_id="test-trace-123",
        )

        # EvalRun created
        runs = EvalRun.objects.filter(
            app_module="extraction",
            entity_type="ExtractionResult",
            entity_id=str(ext_result.pk),
        )
        assert runs.count() == 1
        run = runs.first()
        assert run.status == EvalRun.Status.COMPLETED
        assert run.trace_id == "test-trace-123"
        assert run.run_key == f"extraction-{ext_result.pk}"

        # Metrics created
        metric_names = set(
            EvalMetric.objects.filter(eval_run=run).values_list("metric_name", flat=True)
        )
        assert "extraction_success" in metric_names
        assert "extraction_confidence" in metric_names
        assert "extraction_is_valid" in metric_names
        assert "extraction_is_duplicate" in metric_names
        assert "weakest_critical_field_score" in metric_names
        assert "decision_code_count" in metric_names
        assert "decision_codes" in metric_names

        # Confidence metric value
        conf_metric = EvalMetric.objects.get(eval_run=run, metric_name="extraction_confidence")
        assert abs(conf_metric.metric_value - 0.88) < 0.01

        # Decision codes stored as json_value
        dc_metric = EvalMetric.objects.get(eval_run=run, metric_name="decision_codes")
        assert dc_metric.json_value == ["LOW_CONFIDENCE", "MISSING_PO"]

    def test_field_outcomes_from_legacy(self, db):
        """Field outcomes populated from raw_response._field_confidence."""
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice, raw_response={
            "_field_confidence": {
                "invoice_number": 0.95,
                "total_amount": {"confidence": 0.80},
                "po_number": 0.50,
            },
        })

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_extraction_result(
            ext_result,
            invoice,
            validation_result=_validation_result(),
            dup_result=_dup_result(),
            trace_id="test-legacy",
        )

        run = EvalRun.objects.get(
            app_module="extraction",
            entity_id=str(ext_result.pk),
        )
        outcomes = list(
            EvalFieldOutcome.objects.filter(eval_run=run).order_by("field_name")
        )
        assert len(outcomes) == 3
        names = {o.field_name for o in outcomes}
        assert names == {"invoice_number", "total_amount", "po_number"}

        # Check confidence values parsed correctly
        inv_outcome = next(o for o in outcomes if o.field_name == "invoice_number")
        assert abs(inv_outcome.confidence - 0.95) < 0.01
        total_outcome = next(o for o in outcomes if o.field_name == "total_amount")
        assert abs(total_outcome.confidence - 0.80) < 0.01

    def test_idempotent_rerun(self, db):
        """Calling sync twice does not duplicate EvalRun or metrics."""
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice, raw_response={
            "_field_confidence": {"invoice_number": 0.90},
        })

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        kwargs = dict(
            validation_result=_validation_result(),
            dup_result=_dup_result(),
            trace_id="test-idem",
        )
        ExtractionEvalAdapter.sync_for_extraction_result(ext_result, invoice, **kwargs)
        ExtractionEvalAdapter.sync_for_extraction_result(ext_result, invoice, **kwargs)

        assert EvalRun.objects.filter(
            app_module="extraction",
            entity_id=str(ext_result.pk),
        ).count() == 1

        run = EvalRun.objects.get(
            app_module="extraction",
            entity_id=str(ext_result.pk),
        )
        # Metrics are upserted, not duplicated
        assert EvalMetric.objects.filter(
            eval_run=run, metric_name="extraction_success",
        ).count() == 1
        # Field outcomes are replaced, not duplicated
        assert EvalFieldOutcome.objects.filter(eval_run=run).count() == 1

    def test_fail_silent(self, db):
        """Adapter never raises, even when given bad data."""
        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        # Pass None for everything -- should not raise
        ExtractionEvalAdapter.sync_for_extraction_result(
            None, None,
            validation_result=None,
            trace_id="fail-silent",
        )
        # No EvalRun should be created for None input
        # (it will fail internally but not propagate)


# ---------------------------------------------------------------------------
# Tests: sync_for_approval
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSyncForApproval:
    """Learning signals from approval lifecycle."""

    def test_approval_creates_learning_signal(self, db):
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice)
        approval = _make_approval(invoice, ext_result, status=ExtractionApprovalStatus.APPROVED)
        approval.is_touchless = True

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_approval(approval, user=None)

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="approval_outcome",
        )
        assert signals.count() == 1
        sig = signals.first()
        assert sig.entity_type == "Invoice"
        assert sig.entity_id == str(invoice.pk)
        assert sig.payload_json["status"] == "APPROVED"
        assert sig.payload_json["is_touchless"] is True

    def test_auto_approve_creates_signal(self, db):
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice)
        approval = _make_approval(
            invoice, ext_result,
            status=ExtractionApprovalStatus.AUTO_APPROVED,
        )
        approval.is_touchless = True

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_approval(approval, user=None)

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="auto_approve_outcome",
        )
        assert signals.count() == 1

    def test_corrections_create_field_signals(self, db):
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice)
        approval = _make_approval(invoice, ext_result, status=ExtractionApprovalStatus.APPROVED)

        # Create correction records
        user = User.objects.create_user(
            email="reviewer@example.com", password="test123",
        )
        corr1 = ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="total_amount",
            original_value="1000",
            corrected_value="1050",
            corrected_by=user,
        )
        corr2 = ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="invoice_number",
            original_value="INV-001",
            corrected_value="INV-001A",
            corrected_by=user,
        )

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_approval(
            approval, user=user, correction_records=[corr1, corr2],
        )

        # Field correction signals
        field_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="field_correction",
        )
        assert field_signals.count() == 2

        # Review override signal (non-touchless with corrections)
        override_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="review_override",
        )
        assert override_signals.count() == 1
        assert override_signals.first().payload_json["fields_corrected"] == 2

    def test_rejection_creates_signal(self, db):
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice)
        approval = _make_approval(invoice, ext_result, status=ExtractionApprovalStatus.REJECTED)

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_approval(approval, user=None)

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="approval_outcome",
        )
        assert signals.count() == 1
        sig = signals.first()
        assert sig.payload_json["status"] == "REJECTED"

    def test_approval_sync_fail_silent(self, db):
        """Adapter never raises on bad approval data."""
        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_approval(None, user=None)


# ---------------------------------------------------------------------------
# Tests: validation failure learning signals
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestValidationFailureSignals:

    def test_validation_errors_create_signals(self, db):
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        ext_result = _make_ext_result(upload, invoice)

        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        ExtractionEvalAdapter.sync_for_extraction_result(
            ext_result,
            invoice,
            validation_result=_validation_result(
                is_valid=False,
                errors=["Missing total_amount", "Invalid date format"],
            ),
            dup_result=_dup_result(),
            trace_id="test-val-fail",
        )

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="validation_failure",
        )
        assert signals.count() == 2
