"""GRN Resolver — resolves Goods Receipt Notes via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.grn_fallback import GRNDBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class GRNResolver(BaseResolver):
    resolution_type = ERPResolutionType.GRN

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_grn_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_grn(
            po_number=params.get("po_number", ""),
            grn_number=params.get("grn_number", ""),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return GRNDBFallback.lookup(
            po_number=params.get("po_number", ""),
            grn_number=params.get("grn_number", ""),
        )
