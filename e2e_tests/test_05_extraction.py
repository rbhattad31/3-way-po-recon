"""
TEST 05 -- Invoice Extraction Pipeline
=======================================
Covers:
  - Document upload model and serializer
  - Extraction task imports
  - ExtractionApprovalService (approve / reject / auto-approve)
  - Extraction field correction tracking
  - Extraction UI pages
  - ExtractionResult and ExtractionApproval models
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestDocumentModels:
    """Core document models are importable and have correct structure."""

    def test_document_upload_model(self):
        from apps.documents.models import DocumentUpload
        assert DocumentUpload is not None

    def test_invoice_model(self):
        from apps.documents.models import Invoice
        assert Invoice is not None

    def test_purchase_order_model(self):
        from apps.documents.models import PurchaseOrder
        assert PurchaseOrder is not None

    def test_grn_model(self):
        from apps.documents.models import GoodsReceiptNote
        assert GoodsReceiptNote is not None

    def test_invoice_line_item_model(self):
        from apps.documents.models import InvoiceLineItem
        assert InvoiceLineItem is not None

    def test_po_line_item_model(self):
        from apps.documents.models import PurchaseOrderLineItem
        assert PurchaseOrderLineItem is not None


class TestExtractionModels:
    """Extraction app models."""

    def test_extraction_result_model(self):
        from apps.extraction.models import ExtractionResult
        assert ExtractionResult is not None

    def test_extraction_approval_model(self):
        from apps.extraction.models import ExtractionApproval
        assert ExtractionApproval is not None

    def test_extraction_field_correction_model(self):
        from apps.extraction.models import ExtractionFieldCorrection
        assert ExtractionFieldCorrection is not None


class TestExtractionApprovalService:
    """ExtractionApprovalService key methods exist."""

    def test_approval_service_importable(self):
        from apps.extraction.services.approval_service import ExtractionApprovalService
        assert ExtractionApprovalService is not None

    def test_approval_service_has_approve_method(self):
        from apps.extraction.services.approval_service import ExtractionApprovalService
        assert hasattr(ExtractionApprovalService, "approve"), \
            "ExtractionApprovalService.approve() missing"

    def test_approval_service_has_reject_method(self):
        from apps.extraction.services.approval_service import ExtractionApprovalService
        assert hasattr(ExtractionApprovalService, "reject"), \
            "ExtractionApprovalService.reject() missing"

    def test_approval_service_has_auto_approve(self):
        from apps.extraction.services.approval_service import ExtractionApprovalService
        assert hasattr(ExtractionApprovalService, "try_auto_approve"), \
            "ExtractionApprovalService.try_auto_approve() missing"

    def test_approval_analytics_method_exists(self):
        from apps.extraction.services.approval_service import ExtractionApprovalService
        assert hasattr(ExtractionApprovalService, "get_approval_analytics"), \
            "get_approval_analytics() missing"


class TestExtractionTask:
    """Extraction Celery task is importable and structured correctly."""

    def test_extraction_task_importable(self):
        from apps.extraction.tasks import process_invoice_upload_task
        assert process_invoice_upload_task is not None

    def test_bulk_extraction_task_importable(self):
        try:
            from apps.extraction.bulk_tasks import run_bulk_job_task
            assert run_bulk_job_task is not None
        except ImportError:
            pytest.skip("Bulk extraction task not available")


class TestExtractionUIPages:
    """Extraction UI pages respond correctly."""

    EXTRACTION_URLS = [
        "/extraction/",
        "/extraction/control-center/",
        "/invoices/",
    ]

    def test_extraction_pages_no_500(self, admin_client):
        failures = []
        for url in self.EXTRACTION_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These extraction pages returned 500: {failures}"

    def test_extraction_pages_accessible(self, admin_client):
        for url in self.EXTRACTION_URLS:
            r = admin_client.get(url)
            assert r.status_code in (200, 302), \
                f"{url} returned unexpected {r.status_code}"


class TestExtractionCoreConfig:
    """extraction_core config and control center."""

    def test_extraction_config_model_importable(self):
        pytest.skip("ExtractionConfig model not in codebase")

    def test_control_center_url(self, admin_client):
        r = admin_client.get("/extraction/control-center/")
        assert r.status_code in (200, 302), \
            f"Extraction control-center returned {r.status_code}"
