"""Base ERP connector — abstract contract for all ERP integrations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from apps.erp_integration.enums import ERPSourceType


# ============================================================================
# Structured Result Objects
# ============================================================================


@dataclass
class ERPResolutionResult:
    """Structured result from any ERP resolution (lookup) call."""

    resolved: bool = False
    value: Optional[Dict[str, Any]] = None
    source_type: str = ERPSourceType.NONE
    fallback_used: bool = False
    confidence: float = 0.0
    freshness_timestamp: Optional[datetime] = None
    connector_name: str = ""
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ERPSubmissionResult:
    """Structured result from an ERP submission call."""

    success: bool = False
    status: str = ""
    erp_document_number: str = ""
    error_code: str = ""
    error_message: str = ""
    response_data: Dict[str, Any] = field(default_factory=dict)
    connector_name: str = ""
    duration_ms: int = 0


# ============================================================================
# Base Connector
# ============================================================================


class BaseERPConnector:
    """Abstract base class for ERP connectors.

    All connectors must implement capability checks and lookup/submission
    methods. Default implementations return unsupported results.
    """

    connector_name: str = "base"

    def __init__(self, connection_config: Dict[str, Any]) -> None:
        self.config = connection_config

    # ------------------------------------------------------------------
    # Capability checks
    # ------------------------------------------------------------------

    def supports_vendor_lookup(self) -> bool:
        return False

    def supports_po_lookup(self) -> bool:
        return False

    def supports_grn_lookup(self) -> bool:
        return False

    def supports_item_lookup(self) -> bool:
        return False

    def supports_tax_lookup(self) -> bool:
        return False

    def supports_cost_center_lookup(self) -> bool:
        return False

    def supports_duplicate_check(self) -> bool:
        return False

    def supports_invoice_posting(self) -> bool:
        return False

    def supports_invoice_parking(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    def test_connectivity(self) -> tuple:
        """Test actual connectivity to the ERP system.

        Returns:
            (success: bool, message: str)
        """
        return False, "Connectivity test not implemented for this connector."

    # ------------------------------------------------------------------
    # Reference lookups
    # ------------------------------------------------------------------

    def lookup_vendor(self, vendor_code: str = "", vendor_name: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Vendor lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_po(self, po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="PO lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_grn(self, po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="GRN lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_item(self, item_code: str = "", description: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Item lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_tax(self, tax_code: str = "", rate: float = 0.0, **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Tax lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def lookup_cost_center(self, cost_center_code: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Cost center lookup not supported by this connector",
            connector_name=self.connector_name,
        )

    def check_duplicate_invoice(
        self, invoice_number: str = "", vendor_code: str = "",
        fiscal_year: str = "", **kwargs,
    ) -> ERPResolutionResult:
        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.NONE,
            reason="Duplicate check not supported by this connector",
            connector_name=self.connector_name,
        )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def create_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return ERPSubmissionResult(
            success=False, status="UNSUPPORTED",
            error_message="Invoice creation not supported by this connector",
            connector_name=self.connector_name,
        )

    def park_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return ERPSubmissionResult(
            success=False, status="UNSUPPORTED",
            error_message="Invoice parking not supported by this connector",
            connector_name=self.connector_name,
        )

    def get_posting_status(self, erp_document_number: str) -> ERPSubmissionResult:
        return ERPSubmissionResult(
            success=False, status="UNSUPPORTED",
            error_message="Status check not supported by this connector",
            connector_name=self.connector_name,
        )
