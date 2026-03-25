"""Duplicate Invoice Resolver — checks for duplicate invoices via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.duplicate_invoice_fallback import DuplicateInvoiceDBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class DuplicateInvoiceResolver(BaseResolver):
    resolution_type = ERPResolutionType.DUPLICATE_INVOICE
    use_cache = False  # Duplicate checks should always be live

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_duplicate_check()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.check_duplicate_invoice(
            invoice_number=params.get("invoice_number", ""),
            vendor_code=params.get("vendor_code", ""),
            fiscal_year=params.get("fiscal_year", ""),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return DuplicateInvoiceDBFallback.lookup(
            invoice_number=params.get("invoice_number", ""),
            vendor_code=params.get("vendor_code", ""),
            fiscal_year=params.get("fiscal_year", ""),
            exclude_invoice_id=params.get("exclude_invoice_id"),
        )
