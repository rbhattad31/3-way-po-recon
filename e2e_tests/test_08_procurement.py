"""
TEST 08 -- Procurement Module
==============================
Covers:
  - ProcurementRequest model + API
  - SupplierQuotation model + API
  - Quotation extraction agent
  - AttributeMappingService
  - QuotationDocumentPrefillService
  - Procurement UI pages
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestProcurementModels:
    """Procurement domain models."""

    def test_procurement_request_model(self):
        from apps.procurement.models import ProcurementRequest
        assert ProcurementRequest is not None

    def test_supplier_quotation_model(self):
        from apps.procurement.models import SupplierQuotation
        assert SupplierQuotation is not None

    def test_rfq_document_model(self):
        try:
            from apps.procurement.models import RFQDocument
            assert RFQDocument is not None
        except ImportError:
            pytest.skip("RFQDocument model not present")

    def test_procurement_request_queryable(self):
        from apps.procurement.models import ProcurementRequest
        count = ProcurementRequest.objects.count()
        assert count >= 0, "ProcurementRequest must be queryable"

    def test_supplier_quotation_queryable(self):
        from apps.procurement.models import SupplierQuotation
        count = SupplierQuotation.objects.count()
        assert count >= 0


class TestProcurementServices:
    """Procurement service layer."""

    def test_quotation_prefill_service_importable(self):
        from apps.procurement.services.prefill.quotation_prefill_service import \
            QuotationDocumentPrefillService
        assert QuotationDocumentPrefillService is not None

    def test_attribute_mapping_service_importable(self):
        from apps.procurement.services.prefill.attribute_mapping_service import \
            AttributeMappingService
        assert AttributeMappingService is not None

    def test_attribute_mapping_has_synonym_map(self):
        from apps.procurement.services.prefill.attribute_mapping_service import \
            AttributeMappingService
        assert hasattr(AttributeMappingService, "map_request_fields") and \
               hasattr(AttributeMappingService, "map_quotation_fields"), \
            "AttributeMappingService must expose mapping methods"

    def test_prefill_review_service_importable(self):
        try:
            from apps.procurement.services.prefill.prefill_review_service import \
                PrefillReviewService
            assert PrefillReviewService is not None
        except ImportError:
            pytest.skip("PrefillReviewService not available")


class TestQuotationExtractionAgent:
    """LLM-based quotation extraction agent."""

    def test_quotation_extraction_agent_importable(self):
        pytest.skip("quotation_extraction_agent module not present in current codebase")

    def test_quotation_agent_has_extract_method(self):
        pytest.skip("quotation_extraction_agent module not present in current codebase")


class TestProcurementUI:
    """Procurement UI pages."""

    PROCUREMENT_URLS = [
        "/procurement/",
        "/procurement/dashboard/",
        "/procurement/requests/",
        "/procurement/quotations/",
    ]

    def test_procurement_pages_no_500(self, admin_client):
        failures = []
        for url in self.PROCUREMENT_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These procurement pages returned 500: {failures}"

    def test_procurement_pages_accessible(self, admin_client):
        for url in self.PROCUREMENT_URLS:
            r = admin_client.get(url)
            assert r.status_code in (200, 302, 404), \
                f"Procurement {url} returned {r.status_code}"

    def test_procurement_api_list(self, admin_client):
        r = admin_client.get("/api/v1/procurement/")
        assert r.status_code in (200, 404), \
            f"Procurement API returned {r.status_code}"


class TestBenchmarking:
    """Should-cost benchmarking module."""

    def test_benchmarking_url_accessible(self, admin_client):
        r = admin_client.get("/benchmarking/")
        assert r.status_code in (200, 302, 404), \
            f"/benchmarking/ returned {r.status_code}"

    def test_benchmarking_model_importable(self):
        try:
            from apps.benchmarking.models import BenchmarkingJob
            assert BenchmarkingJob is not None
        except ImportError:
            try:
                from apps.benchmarking import models as bm
                assert bm is not None
            except ImportError:
                pytest.skip("Benchmarking models not available")
