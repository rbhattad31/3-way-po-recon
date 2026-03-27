"""Zoho ERP Connector — stub for future implementation."""
from __future__ import annotations

from typing import Any, Dict

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
    ERPSubmissionResult,
)


class ZohoConnector(BaseERPConnector):
    """Stub connector for Zoho Books / Zoho Inventory."""

    connector_name = "zoho"

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

    def lookup_vendor(self, vendor_code: str = "", vendor_name: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def lookup_po(self, po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def lookup_grn(self, po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def lookup_item(self, item_code: str = "", description: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def lookup_tax(self, tax_code: str = "", rate: float = 0.0, **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def lookup_cost_center(self, cost_center_code: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def check_duplicate_invoice(self, invoice_number: str = "", vendor_code: str = "",
                                fiscal_year: str = "", **kwargs) -> ERPResolutionResult:
        return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE,
                                   reason="Zoho connector not yet implemented", connector_name=self.connector_name)

    def create_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return ERPSubmissionResult(success=False, status="UNSUPPORTED",
                                   error_message="Zoho connector not yet implemented", connector_name=self.connector_name)

    def park_invoice(self, payload: Dict[str, Any]) -> ERPSubmissionResult:
        return ERPSubmissionResult(success=False, status="UNSUPPORTED",
                                   error_message="Zoho connector not yet implemented", connector_name=self.connector_name)

    def get_posting_status(self, erp_document_number: str) -> ERPSubmissionResult:
        return ERPSubmissionResult(success=False, status="UNSUPPORTED",
                                   error_message="Zoho connector not yet implemented", connector_name=self.connector_name)
