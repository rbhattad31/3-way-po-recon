"""Vendor Resolver — resolves vendor via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.vendor_fallback import VendorDBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class VendorResolver(BaseResolver):
    resolution_type = ERPResolutionType.VENDOR

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_vendor_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_vendor(
            vendor_code=params.get("vendor_code", ""),
            vendor_name=params.get("vendor_name", ""),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return VendorDBFallback.lookup(
            vendor_code=params.get("vendor_code", ""),
            vendor_name=params.get("vendor_name", ""),
        )
