"""Tests for BaseERPConnector -- abstract connector and data classes."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from apps.erp_integration.enums import ERPSourceType


class TestERPResolutionResult:
    """RR-01 to RR-06: ERPResolutionResult data class."""

    def test_default_unresolved(self):
        """RR-01: Default construction is unresolved."""
        from apps.erp_integration.services.connectors.base import ERPResolutionResult
        r = ERPResolutionResult()
        assert r.resolved is False
        assert r.value is None
        assert r.source_type == ERPSourceType.NONE

    def test_resolved_with_value(self):
        """RR-02: Resolved result carries value and source."""
        from apps.erp_integration.services.connectors.base import ERPResolutionResult
        r = ERPResolutionResult(
            resolved=True,
            value={"vendor_code": "V001"},
            source_type=ERPSourceType.API,
            connector_name="dynamics",
            confidence=0.95,
        )
        assert r.resolved is True
        assert r.value["vendor_code"] == "V001"
        assert r.connector_name == "dynamics"

    def test_to_provenance_dict(self):
        """RR-03: Provenance dict includes key fields."""
        from apps.erp_integration.services.connectors.base import ERPResolutionResult
        r = ERPResolutionResult(
            resolved=True,
            value={"code": "X"},
            source_type=ERPSourceType.API,
            connector_name="zoho",
            fallback_used=False,
            confidence=0.9,
        )
        prov = r.to_provenance_dict()
        assert prov["source_type"] == ERPSourceType.API
        assert prov["connector_name"] == "zoho"
        assert prov["confidence"] == 0.9
        assert prov["fallback_used"] is False
        assert prov["is_stale"] is False

    def test_fallback_flag(self):
        """RR-04: fallback_used flag is tracked."""
        from apps.erp_integration.services.connectors.base import ERPResolutionResult
        r = ERPResolutionResult(resolved=True, fallback_used=True, source_type=ERPSourceType.DB_FALLBACK)
        assert r.fallback_used is True

    def test_warnings_list(self):
        """RR-05: Warnings are accumulated."""
        from apps.erp_integration.services.connectors.base import ERPResolutionResult
        r = ERPResolutionResult(resolved=True, warnings=["stale data", "unverified"])
        assert len(r.warnings) == 2

    def test_metadata_dict(self):
        """RR-06: Metadata accepts arbitrary keys."""
        from apps.erp_integration.services.connectors.base import ERPResolutionResult
        r = ERPResolutionResult(metadata={"cache_key": "abc123", "ttl": 3600})
        assert r.metadata["cache_key"] == "abc123"


class TestERPSubmissionResult:
    """SR-01 to SR-03: ERPSubmissionResult data class."""

    def test_success_result(self):
        """SR-01: Successful submission."""
        from apps.erp_integration.services.connectors.base import ERPSubmissionResult
        r = ERPSubmissionResult(
            success=True,
            status="SUCCESS",
            erp_document_number="DOC-12345",
            connector_name="dynamics",
            duration_ms=450,
        )
        assert r.success is True
        assert r.erp_document_number == "DOC-12345"
        assert r.duration_ms == 450

    def test_failure_result(self):
        """SR-02: Failed submission."""
        from apps.erp_integration.services.connectors.base import ERPSubmissionResult
        r = ERPSubmissionResult(
            success=False,
            status="FAILED",
            error_code="AUTH_FAIL",
            error_message="Token expired",
        )
        assert r.success is False
        assert r.error_code == "AUTH_FAIL"

    def test_default_empty(self):
        """SR-03: Default values are safe."""
        from apps.erp_integration.services.connectors.base import ERPSubmissionResult
        r = ERPSubmissionResult()
        assert r.success is False
        assert r.erp_document_number == ""


class TestBaseConnectorDefaults:
    """BC-01 to BC-05: BaseERPConnector default implementations."""

    def _make_connector(self):
        from apps.erp_integration.services.connectors.base import BaseERPConnector

        class _TestConnector(BaseERPConnector):
            connector_name = "test"

        return _TestConnector({"base_url": "http://test"})

    def test_all_capabilities_false_by_default(self):
        """BC-01: All supports_*() return False by default."""
        c = self._make_connector()
        assert c.supports_vendor_lookup() is False
        assert c.supports_po_lookup() is False
        assert c.supports_grn_lookup() is False
        assert c.supports_item_lookup() is False
        assert c.supports_tax_lookup() is False
        assert c.supports_cost_center_lookup() is False
        assert c.supports_duplicate_check() is False
        assert c.supports_invoice_posting() is False
        assert c.supports_invoice_parking() is False

    def test_default_lookup_returns_unresolved(self):
        """BC-02: Default lookup methods return unresolved result."""
        c = self._make_connector()
        result = c.lookup_vendor(vendor_code="V001")
        assert result.resolved is False

    def test_default_submission_returns_failed(self):
        """BC-03: Default create_invoice returns failed result."""
        c = self._make_connector()
        result = c.create_invoice({"test": True})
        assert result.success is False

    def test_connector_name_attribute(self):
        """BC-04: connector_name is accessible."""
        c = self._make_connector()
        assert c.connector_name == "test"

    def test_connection_config_stored(self):
        """BC-05: Config dict is stored on instance."""
        c = self._make_connector()
        assert c.config["base_url"] == "http://test"
