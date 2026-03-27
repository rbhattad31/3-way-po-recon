"""Tax Resolver — resolves tax codes via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.tax_fallback import TaxDBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class TaxResolver(BaseResolver):
    resolution_type = ERPResolutionType.TAX

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_tax_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_tax(
            tax_code=params.get("tax_code", ""),
            rate=params.get("rate", 0.0),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return TaxDBFallback.lookup(
            tax_code=params.get("tax_code", ""),
            rate=params.get("rate", 0.0),
        )
