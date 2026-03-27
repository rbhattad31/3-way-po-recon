"""PO Resolver — resolves Purchase Orders via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.po_fallback import PODBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class POResolver(BaseResolver):
    resolution_type = ERPResolutionType.PO

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_po_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_po(
            po_number=params.get("po_number", ""),
            vendor_code=params.get("vendor_code", ""),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return PODBFallback.lookup(
            po_number=params.get("po_number", ""),
            vendor_code=params.get("vendor_code", ""),
        )
