"""Tests for copilot upload views: invoice_upload, upload_status, and pipeline helpers."""
import hashlib
import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.cases.models import APCase
from apps.core.enums import (
    CasePriority,
    CaseStatus,
    DocumentType,
    FileProcessingState,
    InvoiceStatus,
    InvoiceType,
    ProcessingPath,
    SourceChannel,
)
from apps.documents.models import DocumentUpload, Invoice

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_admin_user(email="admin@test.com", with_credits=False):
    user = User.objects.create_user(
        email=email,
        password="testpass123",
        first_name="Admin",
        last_name="Tester",
        role="ADMIN",
    )
    if with_credits:
        from apps.extraction.services.credit_service import CreditService
        acct = CreditService.get_or_create_account(user)
        acct.balance_credits = 100
        acct.save()
    return user


def _make_regular_user(email="user@test.com"):
    return User.objects.create_user(
        email=email,
        password="testpass123",
        first_name="Regular",
        last_name="User",
        role="AP_PROCESSOR",
    )


def _make_upload(user, state=FileProcessingState.PROCESSING, message=""):
    return DocumentUpload.objects.create(
        original_filename="invoice.pdf",
        file_size=1024,
        file_hash="abc123",
        content_type="application/pdf",
        document_type=DocumentType.INVOICE,
        processing_state=state,
        processing_message=message,
        uploaded_by=user,
    )


def _make_invoice(upload, **kwargs):
    defaults = {
        "document_upload": upload,
        "invoice_number": "INV-001",
        "currency": "USD",
        "total_amount": Decimal("1000.00"),
        "status": InvoiceStatus.EXTRACTED,
        "extraction_confidence": 0.88,
    }
    defaults.update(kwargs)
    return Invoice.objects.create(**defaults)


def _make_case(upload=None, invoice=None, case_status=CaseStatus.NEW):
    return APCase.objects.create(
        case_number=f"CASE-{APCase.objects.count() + 1:04d}",
        document_upload=upload,
        invoice=invoice,
        source_channel=SourceChannel.WEB_UPLOAD,
        invoice_type=InvoiceType.UNKNOWN,
        processing_path=ProcessingPath.UNRESOLVED,
        status=case_status,
        current_stage="",
        priority=CasePriority.MEDIUM,
    )


# =========================================================================
# invoice_upload view tests
# =========================================================================
@pytest.mark.django_db
class TestInvoiceUpload:
    """Tests for POST /api/v1/copilot/upload/."""

    url = reverse("copilot_api:invoice_upload")

    def _pdf(self, name="test.pdf", size=100):
        return SimpleUploadedFile(name, b"%" * size, content_type="application/pdf")

    # -- Auth & permission ------------------------------------------------

    def test_anonymous_returns_401_or_403(self):
        resp = Client().post(self.url)
        assert resp.status_code in (401, 403)

    def test_no_permission_returns_403(self):
        user = _make_regular_user()
        c = Client()
        c.force_login(user)
        resp = c.post(self.url)
        assert resp.status_code == 403

    # -- Validation -------------------------------------------------------

    def test_no_file_returns_400(self):
        user = _make_admin_user()
        c = Client()
        c.force_login(user)
        resp = c.post(self.url)
        assert resp.status_code == 400
        assert "No file" in resp.json()["error"]

    def test_unsupported_content_type_returns_400(self):
        user = _make_admin_user()
        c = Client()
        c.force_login(user)
        f = SimpleUploadedFile("doc.docx", b"data", content_type="application/msword")
        resp = c.post(self.url, {"file": f})
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["error"]

    def test_file_too_large_returns_400(self):
        user = _make_admin_user()
        c = Client()
        c.force_login(user)
        f = SimpleUploadedFile(
            "big.pdf", b"x" * (21 * 1024 * 1024), content_type="application/pdf",
        )
        resp = c.post(self.url, {"file": f})
        assert resp.status_code == 400
        assert "too large" in resp.json()["error"]

    # -- Credit check -----------------------------------------------------

    @patch("apps.copilot.views.threading.Thread")
    @patch("apps.extraction.template_views._try_blob_upload")
    def test_insufficient_credits_returns_402(self, _blob, _thread):
        user = _make_admin_user()  # with_credits=False -> 0 balance
        from apps.extraction.services.credit_service import CreditService
        CreditService.get_or_create_account(user)  # ensure account exists

        c = Client()
        c.force_login(user)
        resp = c.post(self.url, {"file": self._pdf()})
        assert resp.status_code == 402

    # -- Happy path -------------------------------------------------------

    @patch("apps.copilot.views.threading.Thread")
    @patch("apps.extraction.template_views._try_blob_upload")
    def test_successful_upload_returns_202(self, _blob, _thread):
        user = _make_admin_user(with_credits=True)
        c = Client()
        c.force_login(user)
        resp = c.post(self.url, {"file": self._pdf()})
        assert resp.status_code == 202
        data = resp.json()
        assert "upload_id" in data
        assert data["filename"] == "test.pdf"

    @patch("apps.copilot.views.threading.Thread")
    @patch("apps.extraction.template_views._try_blob_upload")
    def test_upload_creates_document_upload_record(self, _blob, _thread):
        user = _make_admin_user(with_credits=True)
        c = Client()
        c.force_login(user)
        resp = c.post(self.url, {"file": self._pdf()})
        upload = DocumentUpload.objects.get(pk=resp.json()["upload_id"])
        assert upload.original_filename == "test.pdf"
        assert upload.processing_state == FileProcessingState.PROCESSING
        assert upload.uploaded_by == user

    @patch("apps.copilot.views.threading.Thread")
    @patch("apps.extraction.template_views._try_blob_upload")
    def test_upload_creates_case_before_extraction(self, _blob, _thread):
        """AP Case is pre-created before the pipeline thread starts."""
        user = _make_admin_user(with_credits=True)
        c = Client()
        c.force_login(user)
        resp = c.post(self.url, {"file": self._pdf()})
        upload = DocumentUpload.objects.get(pk=resp.json()["upload_id"])
        case = APCase.objects.filter(document_upload=upload).first()
        assert case is not None
        assert case.invoice is None  # Pre-extraction -- no invoice yet

    @patch("apps.copilot.views.threading.Thread")
    @patch("apps.extraction.template_views._try_blob_upload")
    def test_thread_receives_case_params(self, _blob, mock_thread):
        """Background thread is started with case_id and case_number kwargs."""
        user = _make_admin_user(with_credits=True)
        c = Client()
        c.force_login(user)
        c.post(self.url, {"file": self._pdf()})

        mock_thread.assert_called_once()
        call_kw = mock_thread.call_args
        # Thread is called with kwargs={"case_id": ..., "case_number": ...}
        thread_kwargs = call_kw.kwargs.get("kwargs") or call_kw[1].get("kwargs", {})
        assert thread_kwargs["case_id"] is not None
        assert thread_kwargs["case_number"] is not None

    @patch("apps.copilot.views.threading.Thread")
    @patch("apps.extraction.template_views._try_blob_upload")
    def test_sha256_hash_computed(self, _blob, _thread):
        user = _make_admin_user(with_credits=True)
        content = b"%" * 100
        expected = hashlib.sha256(content).hexdigest()

        c = Client()
        c.force_login(user)
        f = SimpleUploadedFile("inv.pdf", content, content_type="application/pdf")
        resp = c.post(self.url, {"file": f})
        upload = DocumentUpload.objects.get(pk=resp.json()["upload_id"])
        assert upload.file_hash == expected


# =========================================================================
# upload_status view tests
# =========================================================================
@pytest.mark.django_db
class TestUploadStatus:
    """Tests for GET /api/v1/copilot/upload/<id>/status/."""

    def _url(self, upload_id):
        return reverse("copilot_api:upload_status", args=[upload_id])

    # -- Auth & access ---------------------------------------------------

    def test_anonymous_returns_401_or_403(self):
        resp = Client().get(self._url(999))
        assert resp.status_code in (401, 403)

    def test_other_users_upload_returns_404(self):
        owner = _make_admin_user("owner@test.com")
        other = _make_admin_user("other@test.com")
        upload = _make_upload(owner)
        c = Client()
        c.force_login(other)
        assert c.get(self._url(upload.pk)).status_code == 404

    def test_nonexistent_upload_returns_404(self):
        user = _make_admin_user()
        c = Client()
        c.force_login(user)
        assert c.get(self._url(999999)).status_code == 404

    # -- Stage: processing (no invoice) -----------------------------------

    def test_processing_shows_progress_message(self):
        user = _make_admin_user()
        upload = _make_upload(user, message="Scanning pages...")
        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        assert data["completed"] is False
        labels = [s["label"] for s in data["steps"]]
        assert "Document received" in labels
        assert "Scanning pages..." in labels

    def test_processing_default_message(self):
        user = _make_admin_user()
        upload = _make_upload(user, message="")
        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        labels = [s["label"] for s in data["steps"]]
        assert "Reading the document..." in labels

    # -- Stage: failed ----------------------------------------------------

    def test_failed_upload_shows_error(self):
        user = _make_admin_user()
        upload = _make_upload(
            user, state=FileProcessingState.FAILED, message="OCR crashed",
        )
        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        assert data["completed"] is True
        assert data["error"] == "OCR crashed"
        assert any(s.get("failed") for s in data["steps"])

    # -- Stage: case pre-created -----------------------------------------

    def test_pre_created_case_appears_before_extraction_progress(self):
        user = _make_admin_user()
        upload = _make_upload(user, message="Reading...")
        case = _make_case(upload=upload)

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        labels = [s["label"] for s in data["steps"]]

        case_label = f"AP case {case.case_number} created"
        assert case_label in labels
        assert "Reading..." in labels
        assert labels.index(case_label) < labels.index("Reading...")
        assert data["case_id"] == case.pk
        assert data["case_number"] == case.case_number

    # -- Stage: invoice extracted ----------------------------------------

    def test_invoice_extracted_shows_confidence(self):
        user = _make_admin_user()
        upload = _make_upload(user)
        invoice = _make_invoice(upload, extraction_confidence=0.92)
        _make_case(upload=upload, invoice=invoice, case_status=CaseStatus.NEW)

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        labels = [s["label"] for s in data["steps"]]
        assert any("92%" in lbl for lbl in labels)
        assert data["invoice_id"] == invoice.pk

    def test_full_step_order(self):
        """Document received -> Case created -> Extracted -> Case processing status."""
        user = _make_admin_user()
        upload = _make_upload(user)
        invoice = _make_invoice(upload)
        case = _make_case(
            upload=upload, invoice=invoice,
            case_status=CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
        )

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        labels = [s["label"] for s in data["steps"]]

        assert labels[0] == "Document received"
        assert case.case_number in labels[1]
        assert "Extracted" in labels[2]
        assert "reconciliation" in labels[3].lower()

    # -- No case (backward compat) ---------------------------------------

    def test_no_case_shows_opening_message(self):
        user = _make_admin_user()
        upload = _make_upload(user)
        _make_invoice(upload)

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        labels = [s["label"] for s in data["steps"]]
        assert "Opening an AP case..." in labels
        assert data["completed"] is False

    # -- Case via invoice FK fallback ------------------------------------

    def test_case_found_via_invoice_fk_fallback(self):
        user = _make_admin_user()
        upload = _make_upload(user)
        invoice = _make_invoice(upload)
        case = _make_case(upload=None, invoice=invoice, case_status=CaseStatus.CLOSED)

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        assert data["case_id"] == case.pk
        assert data["completed"] is True

    # -- Case-status label tests -----------------------------------------

    @pytest.mark.parametrize("cs,label_fragment,done", [
        (CaseStatus.CLOSED, "Case closed", True),
        (CaseStatus.REJECTED, "Case rejected", True),
        (CaseStatus.ESCALATED, "Case escalated", True),
        (CaseStatus.FAILED, "Case processing failed", True),
        (CaseStatus.READY_FOR_REVIEW, "Ready for review", True),
        (CaseStatus.IN_REVIEW, "In review", True),
        (CaseStatus.THREE_WAY_IN_PROGRESS, "Comparing invoice, PO, and goods receipt", False),
        (CaseStatus.TWO_WAY_IN_PROGRESS, "Comparing invoice against the purchase order", False),
        (CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS, "AI agents are analyzing", False),
        (CaseStatus.PENDING_EXTRACTION_APPROVAL, "Waiting for extraction approval", True),
    ])
    def test_case_status_labels(self, cs, label_fragment, done):
        user = _make_admin_user(f"u-{cs}@test.com")
        upload = _make_upload(user)
        invoice = _make_invoice(upload)
        _make_case(upload=upload, invoice=invoice, case_status=cs)

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        assert data["completed"] is done
        labels = [s["label"] for s in data["steps"]]
        assert any(label_fragment in lbl for lbl in labels)

    def test_failed_case_has_failed_flag(self):
        user = _make_admin_user()
        upload = _make_upload(user)
        invoice = _make_invoice(upload)
        _make_case(upload=upload, invoice=invoice, case_status=CaseStatus.FAILED)

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        failed = [s for s in data["steps"] if s.get("failed")]
        assert len(failed) == 1

    # -- Reconciliation result -------------------------------------------

    def test_reconciliation_result_included_for_terminal_status(self):
        from apps.reconciliation.models import ReconciliationResult, ReconciliationRun

        user = _make_admin_user()
        upload = _make_upload(user)
        invoice = _make_invoice(upload)
        _make_case(upload=upload, invoice=invoice, case_status=CaseStatus.CLOSED)

        run = ReconciliationRun.objects.create(status="COMPLETED")
        ReconciliationResult.objects.create(
            run=run,
            invoice=invoice,
            match_status="MATCHED",
        )

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        assert data["match_status"] == "MATCHED"
        labels = [s["label"] for s in data["steps"]]
        assert any("Matched" in lbl for lbl in labels)

    def test_pending_approval_excludes_recon_result(self):
        user = _make_admin_user()
        upload = _make_upload(user)
        invoice = _make_invoice(upload)
        _make_case(
            upload=upload, invoice=invoice,
            case_status=CaseStatus.PENDING_EXTRACTION_APPROVAL,
        )

        c = Client()
        c.force_login(user)
        data = c.get(self._url(upload.pk)).json()
        assert "match_status" not in data


# =========================================================================
# _copilot_pipeline_worker tests
# =========================================================================
@pytest.mark.django_db
class TestCopilotPipelineWorker:
    """Tests for _copilot_pipeline_worker."""

    @patch("apps.copilot.views._copilot_local_pipeline")
    def test_local_path_forwards_case_params(self, mock_local):
        from apps.copilot.views import _copilot_pipeline_worker

        user = _make_admin_user()
        upload = _make_upload(user)
        _copilot_pipeline_worker(
            upload.pk, user.pk, has_blob=False,
            case_id=42, case_number="CASE-0042",
        )
        mock_local.assert_called_once_with(
            upload.pk, user.pk,
            case_id=42, case_number="CASE-0042",
        )

    @patch("apps.extraction.tasks.process_invoice_upload_task")
    def test_blob_path_forwards_case_params(self, mock_task):
        from apps.copilot.views import _copilot_pipeline_worker

        mock_task.apply.return_value = MagicMock()
        user = _make_admin_user()
        upload = _make_upload(user)
        _copilot_pipeline_worker(
            upload.pk, user.pk, has_blob=True,
            case_id=99, case_number="CASE-0099",
        )
        mock_task.apply.assert_called_once_with(
            kwargs={
                "upload_id": upload.pk,
                "case_id": 99,
                "case_number": "CASE-0099",
                "skip_agent_pipeline": True,
            },
            throw=True,
        )

    @patch("apps.copilot.views._copilot_local_pipeline", side_effect=RuntimeError("boom"))
    def test_exception_marks_upload_failed(self, _mock):
        from apps.copilot.views import _copilot_pipeline_worker

        user = _make_admin_user()
        upload = _make_upload(user)
        _copilot_pipeline_worker(upload.pk, user.pk, has_blob=False)
        upload.refresh_from_db()
        assert upload.processing_state == FileProcessingState.FAILED
        assert "Pipeline failed" in upload.processing_message


# =========================================================================
# _copilot_local_pipeline tests
# =========================================================================
@pytest.mark.django_db
class TestCopilotLocalPipeline:
    """Tests for _copilot_local_pipeline."""

    def _upload_with_file(self, user, tmp_path, settings):
        """Create a DocumentUpload with an actual file inside MEDIA_ROOT."""
        from django.core.files.base import ContentFile
        settings.MEDIA_ROOT = str(tmp_path)
        upload = _make_upload(user)
        upload.file.save("test_invoice.pdf", ContentFile(b"%PDF-1.4 fake"), save=True)
        return upload

    @patch("apps.core.utils.dispatch_task")
    @patch("apps.extraction.services.credit_service.CreditService")
    @patch("apps.extraction.template_views._run_extraction_pipeline")
    def test_links_invoice_to_pre_created_case(
        self, mock_extract, mock_credit, mock_dispatch, tmp_path, settings,
    ):
        user = _make_admin_user()
        upload = self._upload_with_file(user, tmp_path, settings)
        case = _make_case(upload=upload)
        invoice = _make_invoice(upload)

        mock_extract.return_value = {"success": True}

        from apps.copilot.views import _copilot_local_pipeline

        _copilot_local_pipeline(
            upload.pk, user.pk,
            case_id=case.pk, case_number=case.case_number,
        )

        case.refresh_from_db()
        assert case.invoice == invoice
        mock_dispatch.assert_called_once()

    @patch("apps.core.utils.dispatch_task")
    @patch("apps.extraction.services.credit_service.CreditService")
    @patch("apps.extraction.template_views._run_extraction_pipeline")
    def test_creates_case_when_no_case_id(
        self, mock_extract, mock_credit, mock_dispatch, tmp_path, settings,
    ):
        user = _make_admin_user()
        upload = self._upload_with_file(user, tmp_path, settings)
        invoice = _make_invoice(upload)

        mock_extract.return_value = {"success": True}

        from apps.copilot.views import _copilot_local_pipeline

        _copilot_local_pipeline(upload.pk, user.pk)

        assert APCase.objects.filter(invoice=invoice).exists()

    @patch("apps.extraction.services.credit_service.CreditService")
    @patch("apps.extraction.template_views._run_extraction_pipeline")
    def test_extraction_failure_refunds_and_no_case(
        self, mock_extract, mock_credit, tmp_path, settings,
    ):
        mock_extract.return_value = {"success": False, "error": "OCR failed"}

        user = _make_admin_user()
        upload = self._upload_with_file(user, tmp_path, settings)

        from apps.copilot.views import _copilot_local_pipeline

        _copilot_local_pipeline(upload.pk, user.pk)

        mock_credit.refund.assert_called_once()
        assert APCase.objects.count() == 0
