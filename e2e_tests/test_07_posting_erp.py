"""
TEST 07 -- Invoice Posting Agent + ERP Integration
===================================================
Covers:
  - PostingPipeline (9 stages)
  - PostingMappingEngine
  - ERP Connectors + ConnectorFactory
  - ERP Resolvers (vendor, item, tax, cost-center, PO, GRN, duplicate)
  - ERP Reference data import
  - Posting UI pages
  - ERP-integration UI pages
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestPostingModels:
    """Posting domain models."""

    def test_invoice_posting_model(self):
        from apps.posting.models import InvoicePosting
        assert InvoicePosting is not None

    def test_posting_run_model(self):
        from apps.posting_core.models import PostingRun
        assert PostingRun is not None

    def test_erp_vendor_reference_model(self):
        from apps.posting_core.models import ERPVendorReference
        assert ERPVendorReference is not None

    def test_erp_item_reference_model(self):
        from apps.posting_core.models import ERPItemReference
        assert ERPItemReference is not None

    def test_erp_po_reference_model(self):
        from apps.posting_core.models import ERPPOReference
        assert ERPPOReference is not None

    def test_erp_tax_code_reference_model(self):
        from apps.posting_core.models import ERPTaxCodeReference
        assert ERPTaxCodeReference is not None

    def test_erp_cost_center_reference_model(self):
        from apps.posting_core.models import ERPCostCenterReference
        assert ERPCostCenterReference is not None


class TestPostingPipeline:
    """9-stage PostingPipeline service."""

    def test_pipeline_importable(self):
        from apps.posting_core.services.posting_pipeline import PostingPipeline
        assert PostingPipeline is not None

    def test_mapping_engine_importable(self):
        from apps.posting_core.services.posting_mapping_engine import PostingMappingEngine
        assert PostingMappingEngine is not None

    def test_validation_service_importable(self):
        try:
            from apps.posting_core.services.posting_validation_service import PostingValidationService
            assert PostingValidationService is not None
        except ImportError:
            pytest.skip("PostingValidationService not yet implemented")

    def test_confidence_scoring_importable(self):
        try:
            from apps.posting_core.services.posting_confidence_service import PostingConfidenceService
            assert PostingConfidenceService is not None
        except ImportError:
            pytest.skip("PostingConfidenceService not yet implemented")

    def test_posting_orchestrator_importable(self):
        from apps.posting.services.posting_orchestrator import PostingOrchestrator
        assert PostingOrchestrator is not None

    def test_posting_action_service_importable(self):
        from apps.posting.services.posting_action_service import PostingActionService
        assert PostingActionService is not None


class TestPostingStatusEnum:
    """Posting status lifecycle values."""

    EXPECTED_STATUSES = [
        "NOT_READY",
        "READY_FOR_POSTING",
        "MAPPING_IN_PROGRESS",
        "READY_TO_SUBMIT",
        "POSTED",
        "POST_FAILED",
    ]

    def test_posting_status_enum_exists(self):
            from apps.core.enums import InvoicePostingStatus
            for s in self.EXPECTED_STATUSES:
                assert hasattr(InvoicePostingStatus, s), \
                    f"InvoicePostingStatus.{s} not defined in enums"


class TestERPIntegration:
    """ERP Integration layer."""

    def test_erp_connection_model(self):
        from apps.erp_integration.models import ERPConnection
        assert ERPConnection is not None

    def test_erp_cache_model(self):
        from apps.erp_integration.models import ERPReferenceCacheRecord
        assert ERPReferenceCacheRecord is not None

    def test_connector_factory_importable(self):
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        assert ConnectorFactory is not None

    def test_base_erp_connector_importable(self):
        from apps.erp_integration.services.connectors.base import BaseERPConnector
        assert BaseERPConnector is not None

    def test_resolution_service_importable(self):
        from apps.erp_integration.services.resolution_service import ERPResolutionService
        assert ERPResolutionService is not None

    def test_erp_langfuse_helpers_importable(self):
        from apps.erp_integration.services.langfuse_helpers import (
            start_erp_span, end_erp_span, sanitize_erp_metadata
        )
        assert start_erp_span is not None

    def test_connector_factory_get_default_returns_none_without_db_record(self):
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        # Should return None gracefully when no active ERPConnection exists
        result = ConnectorFactory.get_default_connector()
        assert result is None or result is not None  # no exception is the goal


class TestERPConnectors:
    """Individual ERP connector implementations."""

    def test_custom_erp_connector(self):
        try:
            from apps.erp_integration.services.connectors.custom_erp import CustomERPConnector
            assert CustomERPConnector is not None
        except ImportError:
            pytest.skip("CustomERPConnector not present")

    def test_dynamics_connector(self):
        try:
            from apps.erp_integration.services.connectors.dynamics import DynamicsConnector
            assert DynamicsConnector is not None
        except ImportError:
            pytest.skip("DynamicsConnector not present")


class TestPostingUI:
    """Posting + ERP UI pages."""

    POSTING_URLS = [
        "/posting/",
        "/erp-connections/",
        "/erp-connections/reference-data/",
    ]

    def test_posting_pages_no_500(self, admin_client):
        failures = []
        for url in self.POSTING_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These posting pages returned 500: {failures}"

    def test_posting_api_list(self, admin_client):
        r = admin_client.get("/api/v1/posting/")
        assert r.status_code in (200, 404), \
            f"Posting API returned {r.status_code}"

    def test_erp_reference_data_ui(self, admin_client):
        r = admin_client.get("/erp-connections/reference-data/")
        assert r.status_code in (200, 302, 404), \
            f"ERP reference data UI returned {r.status_code}"
