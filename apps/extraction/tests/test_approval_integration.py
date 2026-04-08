"""Integration tests for extraction approval flow.

Covers:
- extraction_approve view returns correct responses (AJAX + redirect)
- extraction_reject view returns correct responses
- ExtractionApprovalService.approve() transitions invoice status
- Approval triggers case creation via _ensure_case()
- Approval triggers reconciliation via _enqueue_reconciliation()
- core_eval integration: approval/rejection creates learning signals + metrics
"""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.core.enums import (
    ExtractionApprovalStatus,
    InvoiceStatus,
    UserRole as UserRoleEnum,
)
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


def _make_invoice(upload, **kwargs):
    defaults = dict(
        invoice_number="INV-TEST-001",
        currency="USD",
        total_amount=1000,
        status=InvoiceStatus.PENDING_APPROVAL,
        extraction_confidence=0.92,
        document_upload=upload,
        po_number="",
    )
    defaults.update(kwargs)
    return Invoice.objects.create(**defaults)


def _make_extraction_result(upload, invoice):
    run = ExtractionRun.objects.create(
        document_upload=upload,
        overall_confidence=0.92,
        status="COMPLETED",
    )
    return ExtractionResult.objects.create(
        document_upload=upload,
        extraction_run=run,
        success=True,
    )


def _make_approval(invoice, extraction_result=None):
    return ExtractionApproval.objects.create(
        invoice=invoice,
        extraction_result=extraction_result,
        status=ExtractionApprovalStatus.PENDING,
        confidence_at_review=invoice.extraction_confidence,
    )


def _admin_user(db):
    return User.objects.create_user(
        email="admin-test@example.com",
        password="testpass123",
        first_name="Admin",
        last_name="Tester",
        role=UserRoleEnum.ADMIN,
    )


# ===========================================================================
# View tests
# ===========================================================================


@pytest.mark.django_db
class TestExtractionApproveView:
    """Tests for the extraction_approve view function."""

    def _call_view(self, approval, user, ajax=False, body=None):
        from apps.extraction.template_views import extraction_approve
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.messages.storage.fallback import FallbackStorage

        factory = RequestFactory()
        data = json.dumps(body).encode() if body else b""
        request = factory.post(
            f"/extraction/approvals/{approval.pk}/approve/",
            data=data,
            content_type="application/json",
        )
        request.user = user
        # Required for django.contrib.messages
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        if ajax:
            request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
        return extraction_approve(request, approval.pk)

    def test_ajax_approve_returns_json(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        response = self._call_view(approval, user, ajax=True)

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["ok"] is True
        assert data["status"] == "APPROVED"

    def test_non_ajax_approve_redirects(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        response = self._call_view(approval, user, ajax=False)

        assert response.status_code == 302
        assert "approvals" in response.url or "approval_queue" in response.url

    def test_approve_transitions_invoice_past_pending_approval(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        self._call_view(approval, user, ajax=True)

        invoice.refresh_from_db()
        # Invoice should have moved past PENDING_APPROVAL (at minimum READY_FOR_RECON)
        assert invoice.status != InvoiceStatus.PENDING_APPROVAL

    def test_approve_sets_approval_status(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        self._call_view(approval, user, ajax=True)

        approval.refresh_from_db()
        assert approval.status == ExtractionApprovalStatus.APPROVED
        assert approval.reviewed_by == user

    def test_double_approve_returns_400(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        # First approve
        self._call_view(approval, user, ajax=True)
        # Second approve
        response = self._call_view(approval, user, ajax=True)

        assert response.status_code == 400
        data = json.loads(response.content)
        assert data["ok"] is False

    def test_approve_with_corrections(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        corrections = {"header": {"invoice_number": "CORRECTED-001"}}
        self._call_view(approval, user, ajax=True, body=corrections)

        approval.refresh_from_db()
        assert approval.status == ExtractionApprovalStatus.APPROVED
        assert approval.fields_corrected_count >= 1
        assert approval.is_touchless is False

        invoice.refresh_from_db()
        assert invoice.invoice_number == "CORRECTED-001"


@pytest.mark.django_db
class TestExtractionRejectView:
    """Tests for the extraction_reject view function."""

    def _call_view(self, approval, user, ajax=False, reason=""):
        from apps.extraction.template_views import extraction_reject
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.messages.storage.fallback import FallbackStorage

        factory = RequestFactory()
        body = json.dumps({"reason": reason}).encode()
        request = factory.post(
            f"/extraction/approvals/{approval.pk}/reject/",
            data=body,
            content_type="application/json",
        )
        request.user = user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        if ajax:
            request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
        return extraction_reject(request, approval.pk)

    def test_ajax_reject_returns_json(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        approval = _make_approval(invoice)

        response = self._call_view(approval, user, ajax=True, reason="Bad data")

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["ok"] is True
        assert data["status"] == "REJECTED"

    def test_reject_sets_status(self, db):
        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        approval = _make_approval(invoice)

        self._call_view(approval, user, ajax=True, reason="Bad data")

        approval.refresh_from_db()
        assert approval.status == ExtractionApprovalStatus.REJECTED


# ===========================================================================
# Service integration tests
# ===========================================================================


@pytest.mark.django_db
class TestApprovalServiceIntegration:
    """Tests for ExtractionApprovalService.approve() integration behavior."""

    def test_approve_creates_ap_case(self, db):
        """Approving an extraction should create an APCase for the invoice."""
        from apps.cases.models import APCase

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        from apps.extraction.services.approval_service import ExtractionApprovalService
        ExtractionApprovalService.approve(approval, user)

        cases = APCase.objects.filter(invoice=invoice, is_active=True)
        assert cases.count() == 1
        case = cases.first()
        assert case.case_number.startswith("AP-")

    def test_approve_triggers_case_processing(self, db, settings):
        """Approving should trigger the case pipeline (eager mode = sync execution)."""
        from apps.cases.models import APCase

        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = False  # Don't propagate agent errors

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        # Need a recon config for the case pipeline's matching stage
        from apps.reconciliation.models import ReconciliationConfig
        ReconciliationConfig.objects.create(
            name="Default",
            is_default=True,
            quantity_tolerance_pct=2.0,
            price_tolerance_pct=1.0,
            amount_tolerance_pct=1.0,
        )

        from apps.extraction.services.approval_service import ExtractionApprovalService
        ExtractionApprovalService.approve(approval, user)

        # Case should exist and should have advanced beyond NEW
        case = APCase.objects.filter(invoice=invoice, is_active=True).first()
        assert case is not None
        assert case.status != "NEW", f"Case should have advanced beyond NEW, got {case.status}"

    def test_approve_idempotent_case_creation(self, db):
        """Calling approve twice should not create duplicate cases."""
        from apps.cases.models import APCase
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)

        # Pre-create case
        from apps.cases.services.case_creation_service import CaseCreationService
        CaseCreationService.create_from_upload(invoice=invoice, uploaded_by=user)
        assert APCase.objects.filter(invoice=invoice).count() == 1

        approval = _make_approval(invoice, er)
        ExtractionApprovalService.approve(approval, user)

        # Should still be 1
        assert APCase.objects.filter(invoice=invoice, is_active=True).count() == 1

    def test_touchless_approval_no_corrections(self, db):
        """Approval without corrections (inline or pre-saved) should be marked touchless."""
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        # Verify no pre-existing corrections
        assert ExtractionFieldCorrection.objects.filter(approval=approval).count() == 0

        ExtractionApprovalService.approve(approval, user, corrections=None)

        approval.refresh_from_db()
        assert approval.is_touchless is True
        assert approval.fields_corrected_count == 0


# ===========================================================================
# core_eval integration tests
# ===========================================================================


@pytest.mark.django_db
class TestApprovalEvalIntegration:
    """Verify that ExtractionApprovalService writes core_eval records."""

    def test_approve_creates_eval_learning_signal(self, db):
        """approve() should create an approval_outcome LearningSignal."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        ExtractionApprovalService.approve(approval, user)

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="approval_outcome",
            entity_id=str(invoice.pk),
        )
        assert signals.count() == 1
        sig = signals.first()
        assert sig.payload_json["status"] == "APPROVED"
        assert sig.payload_json["approval_id"] == approval.pk

    def test_reject_creates_eval_learning_signal(self, db):
        """reject() should create an approval_outcome LearningSignal with REJECTED."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        ExtractionApprovalService.reject(approval, user, reason="Bad OCR quality")

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="approval_outcome",
            entity_id=str(invoice.pk),
        )
        assert signals.count() == 1
        sig = signals.first()
        assert sig.payload_json["status"] == "REJECTED"

    def test_approve_with_corrections_creates_field_signals(self, db):
        """approve() with corrections should create field_correction signals."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        corrections = {
            "header": {"invoice_number": "CORRECTED-002", "currency": "EUR"},
        }
        ExtractionApprovalService.approve(approval, user, corrections=corrections)

        field_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="field_correction",
            entity_id=str(invoice.pk),
        )
        assert field_signals.count() == 2
        corrected_fields = set(field_signals.values_list("field_name", flat=True))
        assert "invoice_number" in corrected_fields
        assert "currency" in corrected_fields

    def test_approve_with_corrections_creates_review_override_signal(self, db):
        """Non-touchless approval should create a review_override signal."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        corrections = {"header": {"total_amount": "1050"}}
        ExtractionApprovalService.approve(approval, user, corrections=corrections)

        override_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="review_override",
            entity_id=str(invoice.pk),
        )
        assert override_signals.count() == 1
        sig = override_signals.first()
        assert sig.payload_json["fields_corrected"] >= 1
        assert "total_amount" in sig.payload_json["corrected_field_names"]

    def test_approve_with_corrections_updates_eval_metrics(self, db):
        """approve() with corrections should upsert extraction_corrections_count metric."""
        from apps.core_eval.models import EvalMetric, EvalRun
        from apps.extraction.services.approval_service import ExtractionApprovalService
        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)

        # Pre-create the extraction EvalRun (as tasks.py would)
        ExtractionEvalAdapter.sync_for_extraction_result(
            er, invoice,
            validation_result=type("V", (), {"is_valid": True, "errors": []})(),
            dup_result=type("D", (), {"is_duplicate": False, "reason": "unique"})(),
            trace_id="test-metric-update",
        )

        approval = _make_approval(invoice, er)
        corrections = {"header": {"invoice_number": "FIXED-001"}}
        ExtractionApprovalService.approve(approval, user, corrections=corrections)

        run = EvalRun.objects.get(
            app_module="extraction",
            entity_type="ExtractionResult",
            entity_id=str(er.pk),
        )
        corr_metric = EvalMetric.objects.filter(
            eval_run=run,
            metric_name="extraction_corrections_count",
        ).first()
        assert corr_metric is not None
        assert corr_metric.metric_value >= 1.0

        decision_metric = EvalMetric.objects.filter(
            eval_run=run,
            metric_name="extraction_approval_decision",
        ).first()
        assert decision_metric is not None
        assert decision_metric.metric_value == 1.0

    def test_touchless_approve_no_field_signals(self, db):
        """Touchless approval (no corrections) should not create field_correction signals."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        ExtractionApprovalService.approve(approval, user, corrections=None)

        field_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="field_correction",
            entity_id=str(invoice.pk),
        )
        assert field_signals.count() == 0

        override_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="review_override",
            entity_id=str(invoice.pk),
        )
        assert override_signals.count() == 0

    def test_auto_approve_creates_auto_approve_signal(self, db, settings):
        """try_auto_approve() should create an auto_approve_outcome signal."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        settings.EXTRACTION_AUTO_APPROVE_ENABLED = True
        settings.EXTRACTION_AUTO_APPROVE_THRESHOLD = 0.50

        upload = _make_upload(db)
        invoice = _make_invoice(upload, extraction_confidence=0.95)
        er = _make_extraction_result(upload, invoice)

        result = ExtractionApprovalService.try_auto_approve(invoice, er)
        assert result is not None

        signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="auto_approve_outcome",
            entity_id=str(invoice.pk),
        )
        assert signals.count() == 1
        sig = signals.first()
        assert sig.payload_json["is_touchless"] is True


# ===========================================================================
# Pre-existing corrections tests (Save-then-Approve workflow)
# ===========================================================================


@pytest.mark.django_db
class TestPreExistingCorrections:
    """Verify that corrections saved BEFORE clicking Approve are picked up.

    Real-world flow:
    1. User edits fields on the extraction detail page and clicks "Save"
       -> save_extracted_data view creates ExtractionFieldCorrection records
    2. User clicks "Approve"
       -> extraction_approve view calls approve(corrections=None)

    Previously, approve() only counted corrections passed inline via the
    ``corrections`` dict, so pre-saved corrections were silently ignored,
    leading to is_touchless=True and zero learning signals.
    """

    def test_presaved_corrections_mark_approval_not_touchless(self, db):
        """approve(corrections=None) with pre-existing ExtractionFieldCorrection
        records should set is_touchless=False and fields_corrected_count > 0."""
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        # Simulate save_extracted_data creating corrections before approve
        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="total_amount",
            original_value="1000.00",
            corrected_value="1050.00",
            corrected_by=user,
        )
        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="currency",
            original_value="USD",
            corrected_value="EUR",
            corrected_by=user,
        )

        # Approve without inline corrections (the real-world click)
        ExtractionApprovalService.approve(approval, user, corrections=None)

        approval.refresh_from_db()
        assert approval.is_touchless is False
        assert approval.fields_corrected_count == 2

    def test_presaved_corrections_create_field_signals(self, db):
        """Pre-existing corrections should produce field_correction learning signals."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="invoice_number",
            original_value="INV-001",
            corrected_value="INV-001-FIXED",
            corrected_by=user,
        )

        ExtractionApprovalService.approve(approval, user, corrections=None)

        field_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="field_correction",
            entity_id=str(invoice.pk),
        )
        assert field_signals.count() == 1
        sig = field_signals.first()
        assert sig.field_name == "invoice_number"

    def test_presaved_corrections_create_review_override_signal(self, db):
        """Pre-existing corrections should produce a review_override signal."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="total_amount",
            original_value="500",
            corrected_value="550",
            corrected_by=user,
        )

        ExtractionApprovalService.approve(approval, user, corrections=None)

        override_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="review_override",
            entity_id=str(invoice.pk),
        )
        assert override_signals.count() == 1
        sig = override_signals.first()
        assert sig.payload_json["fields_corrected"] >= 1
        assert "total_amount" in sig.payload_json["corrected_field_names"]

    def test_mixed_presaved_and_inline_corrections_merged(self, db):
        """When both pre-saved AND inline corrections exist, both should count."""
        from apps.core_eval.models import LearningSignal
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        # Pre-saved correction (from Save click)
        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="currency",
            original_value="USD",
            corrected_value="GBP",
            corrected_by=user,
        )

        # Inline correction (from Approve click with edits)
        inline_corrections = {"header": {"total_amount": "2000"}}
        ExtractionApprovalService.approve(
            approval, user, corrections=inline_corrections,
        )

        approval.refresh_from_db()
        assert approval.is_touchless is False
        assert approval.fields_corrected_count == 2  # 1 pre-saved + 1 inline

        field_signals = LearningSignal.objects.filter(
            app_module="extraction",
            signal_type="field_correction",
            entity_id=str(invoice.pk),
        )
        assert field_signals.count() == 2
        corrected_fields = set(field_signals.values_list("field_name", flat=True))
        assert "currency" in corrected_fields
        assert "total_amount" in corrected_fields

    def test_presaved_corrections_audit_event_includes_details(self, db):
        """Audit event for EXTRACTION_FIELD_CORRECTED should list pre-saved corrections."""
        from apps.auditlog.models import AuditEvent
        from apps.extraction.services.approval_service import ExtractionApprovalService

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)
        approval = _make_approval(invoice, er)

        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="vendor_name",
            original_value="Acme Inc",
            corrected_value="Acme Corp",
            corrected_by=user,
        )

        ExtractionApprovalService.approve(approval, user, corrections=None)

        from apps.core.enums import AuditEventType

        corrected_events = AuditEvent.objects.filter(
            event_type=AuditEventType.EXTRACTION_FIELD_CORRECTED,
            invoice_id=invoice.pk,
        )
        assert corrected_events.count() == 1
        evt = corrected_events.first()
        corrections_list = evt.metadata_json.get("corrections", [])
        assert len(corrections_list) == 1
        assert corrections_list[0]["field"] == "vendor_name"
        assert corrections_list[0]["from"] == "Acme Inc"
        assert corrections_list[0]["to"] == "Acme Corp"

    def test_presaved_corrections_update_eval_metrics(self, db):
        """Pre-saved corrections should update extraction_corrections_count metric."""
        from apps.core_eval.models import EvalMetric, EvalRun
        from apps.extraction.services.approval_service import ExtractionApprovalService
        from apps.extraction.services.eval_adapter import ExtractionEvalAdapter

        user = _admin_user(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        er = _make_extraction_result(upload, invoice)

        # Pre-create the EvalRun (as the extraction task would)
        ExtractionEvalAdapter.sync_for_extraction_result(
            er, invoice,
            validation_result=type("V", (), {"is_valid": True, "errors": []})(),
            dup_result=type("D", (), {"is_duplicate": False, "reason": "unique"})(),
            trace_id="test-presaved-metrics",
        )

        approval = _make_approval(invoice, er)

        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="invoice_date",
            original_value="2026-01-01",
            corrected_value="2026-01-15",
            corrected_by=user,
        )
        ExtractionFieldCorrection.objects.create(
            approval=approval,
            entity_type="header",
            field_name="total_amount",
            original_value="100",
            corrected_value="200",
            corrected_by=user,
        )

        ExtractionApprovalService.approve(approval, user, corrections=None)

        run = EvalRun.objects.get(
            app_module="extraction",
            entity_type="ExtractionResult",
            entity_id=str(er.pk),
        )
        corr_metric = EvalMetric.objects.filter(
            eval_run=run,
            metric_name="extraction_corrections_count",
        ).first()
        assert corr_metric is not None
        assert corr_metric.metric_value == 2.0
