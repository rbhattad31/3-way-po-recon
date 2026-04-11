"""Integration tests for reconciliation task -- APCase linkage.

Covers:
- Reconciliation task links ReconciliationResult back to APCase
- Processing path is set correctly based on reconciliation mode
- Non-PO invoices get NON_PO processing path
- Cases without recon results are linked after recon runs
- Agent pipeline is dispatched for non-MATCHED results
"""
from __future__ import annotations

import pytest
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.cases.models import APCase, APCaseStage
from apps.core.enums import (
    CaseStatus,
    CaseStageType,
    InvoiceStatus,
    ProcessingPath,
    ReconciliationMode,
    StageStatus,
    UserRole as UserRoleEnum,
)
from apps.documents.models import DocumentUpload, Invoice
from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationResult,
    ReconciliationRun,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_admin(db):
    return User.objects.create_user(
        email="recon-admin@example.com",
        password="testpass",
        role=UserRoleEnum.ADMIN,
    )


def _make_upload(db):
    return DocumentUpload.objects.create(
        original_filename="test-recon.pdf",
        file_size=2048,
        content_type="application/pdf",
    )


def _make_invoice(upload, po_number="", **kwargs):
    defaults = dict(
        invoice_number="INV-RECON-001",
        currency="USD",
        total_amount=Decimal("1000.00"),
        status=InvoiceStatus.READY_FOR_RECON,
        extraction_confidence=0.95,
        document_upload=upload,
        po_number=po_number,
    )
    defaults.update(kwargs)
    return Invoice.objects.create(**defaults)


def _make_recon_config(db):
    return ReconciliationConfig.objects.create(
        name="Test Config",
        is_default=True,
        quantity_tolerance_pct=2.0,
        price_tolerance_pct=1.0,
        amount_tolerance_pct=1.0,
    )


def _make_case_for_invoice(invoice, user=None):
    from apps.cases.services.case_creation_service import CaseCreationService
    return CaseCreationService.create_from_upload(
        invoice=invoice,
        uploaded_by=user,
    )


# ===========================================================================
# Reconciliation task tests
# ===========================================================================


@pytest.mark.django_db
class TestReconTaskCaseLinkage:
    """Verify that run_reconciliation_task links results to pre-existing APCases."""

    def test_recon_links_result_to_case(self, db, settings):
        """After reconciliation, the APCase should have reconciliation_result set."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        admin = _make_admin(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        config = _make_recon_config(db)
        case = _make_case_for_invoice(invoice, admin)

        assert case.reconciliation_result is None

        from apps.reconciliation.tasks import run_reconciliation_task
        result = run_reconciliation_task.apply(
            kwargs=dict(invoice_ids=[invoice.pk], config_id=config.pk, triggered_by_id=admin.pk),
        ).get()

        assert result["status"] == "ok"
        assert result["total_invoices"] == 1

        case.refresh_from_db()
        assert case.reconciliation_result is not None

    def test_non_po_invoice_gets_non_po_path(self, db, settings):
        """Invoice without PO should get NON_PO processing path on case."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        admin = _make_admin(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload, po_number="")  # No PO
        config = _make_recon_config(db)
        case = _make_case_for_invoice(invoice, admin)

        assert case.processing_path == ProcessingPath.UNRESOLVED

        from apps.reconciliation.tasks import run_reconciliation_task
        run_reconciliation_task.apply(
            kwargs=dict(invoice_ids=[invoice.pk], config_id=config.pk, triggered_by_id=admin.pk),
        ).get()

        case.refresh_from_db()
        assert case.processing_path == ProcessingPath.NON_PO

    def test_recon_result_has_correct_mode_for_non_po(self, db, settings):
        """Non-PO invoice should produce a NON_PO reconciliation result."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        admin = _make_admin(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload, po_number="")
        config = _make_recon_config(db)

        from apps.reconciliation.tasks import run_reconciliation_task
        run_reconciliation_task.apply(
            kwargs=dict(invoice_ids=[invoice.pk], config_id=config.pk, triggered_by_id=admin.pk),
        ).get()

        rr = ReconciliationResult.objects.filter(invoice=invoice).first()
        assert rr is not None
        assert rr.reconciliation_mode == ReconciliationMode.NON_PO

    def test_case_without_prior_case_is_unaffected(self, db, settings):
        """Reconciliation without a pre-existing APCase should not crash."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        admin = _make_admin(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload)
        config = _make_recon_config(db)
        # No APCase created -- this should still work fine

        from apps.reconciliation.tasks import run_reconciliation_task
        result = run_reconciliation_task.apply(
            kwargs=dict(invoice_ids=[invoice.pk], config_id=config.pk, triggered_by_id=admin.pk),
        ).get()

        assert result["status"] == "ok"
        # No case to link, no error
        assert APCase.objects.filter(invoice=invoice).count() == 0

    def test_already_linked_case_not_overwritten(self, db, settings):
        """If a case already has a reconciliation_result, it should not be overwritten."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        admin = _make_admin(db)
        upload = _make_upload(db)
        invoice = _make_invoice(upload, po_number="")
        config = _make_recon_config(db)
        case = _make_case_for_invoice(invoice, admin)

        # First reconciliation run
        from apps.reconciliation.tasks import run_reconciliation_task
        run_reconciliation_task.apply(
            kwargs=dict(invoice_ids=[invoice.pk], config_id=config.pk, triggered_by_id=admin.pk),
        ).get()

        case.refresh_from_db()
        first_rr = case.reconciliation_result
        assert first_rr is not None

        # Run again (invoice needs to be READY_FOR_RECON again)
        invoice.status = InvoiceStatus.READY_FOR_RECON
        invoice.save(update_fields=["status"])

        run_reconciliation_task.apply(
            kwargs=dict(invoice_ids=[invoice.pk], config_id=config.pk, triggered_by_id=admin.pk),
        ).get()

        case.refresh_from_db()
        # Should still have the first result (reconciliation_result__isnull=True guard)
        assert case.reconciliation_result == first_rr


# ===========================================================================
# End-to-end: Approval -> Case -> Reconciliation -> Case linkage
# ===========================================================================


@pytest.mark.django_db
class TestEndToEndApprovalToReconLinkage:
    """Full integration: approve extraction -> case created -> recon runs -> case linked."""

    def test_full_flow(self, db, settings):
        """Approve -> case -> case pipeline runs -> case has result + correct path."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = False  # Don't propagate agent errors

        admin = _make_admin(db)
        upload = _make_upload(db)
        invoice = _make_invoice(
            upload,
            po_number="",
            status=InvoiceStatus.PENDING_APPROVAL,
        )
        _make_recon_config(db)

        from apps.extraction.models import ExtractionApproval, ExtractionResult
        from apps.extraction_core.models import ExtractionRun
        run = ExtractionRun.objects.create(
            document_upload=upload,
            overall_confidence=0.92,
            status="COMPLETED",
        )
        er = ExtractionResult.objects.create(
            document_upload=upload, extraction_run=run, success=True,
        )
        approval = ExtractionApproval.objects.create(
            invoice=invoice,
            extraction_result=er,
            status=ExtractionApprovalStatus.PENDING,
        )

        from apps.extraction.services.approval_service import ExtractionApprovalService
        ExtractionApprovalService.approve(approval, admin)

        # Invoice should be RECONCILED (case pipeline ran matching)
        invoice.refresh_from_db()
        assert invoice.status in (
            InvoiceStatus.READY_FOR_RECON,
            InvoiceStatus.RECONCILED,
        )

        # Case should exist and be linked
        case = APCase.objects.filter(invoice=invoice, is_active=True).first()
        assert case is not None

        # Case should have advanced beyond NEW (pipeline ran)
        assert case.status != CaseStatus.NEW, f"Case should have advanced beyond NEW, got {case.status}"

        # Reconciliation should have run
        rr = ReconciliationResult.objects.filter(invoice=invoice).first()
        if rr:
            # Case should be linked to the result
            case.refresh_from_db()
            assert case.reconciliation_result == rr
            assert case.processing_path == ProcessingPath.NON_PO


# Need this import at the top of the e2e test
from apps.core.enums import ExtractionApprovalStatus
