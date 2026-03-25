"""Cost Center Resolver — resolves cost centers via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.cost_center_fallback import CostCenterDBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class CostCenterResolver(BaseResolver):
    resolution_type = ERPResolutionType.COST_CENTER

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_cost_center_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_cost_center(
            cost_center_code=params.get("cost_center_code", ""),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return CostCenterDBFallback.lookup(
            cost_center_code=params.get("cost_center_code", ""),
        )
