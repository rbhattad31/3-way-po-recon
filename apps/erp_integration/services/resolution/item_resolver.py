"""Item Resolver — resolves items via ERP API with DB fallback."""
from __future__ import annotations

from apps.erp_integration.enums import ERPResolutionType
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)
from apps.erp_integration.services.db_fallback.item_fallback import ItemDBFallback
from apps.erp_integration.services.resolution.base import BaseResolver


class ItemResolver(BaseResolver):
    resolution_type = ERPResolutionType.ITEM

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        return connector.supports_item_lookup()

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        return connector.lookup_item(
            item_code=params.get("item_code", ""),
            description=params.get("description", ""),
        )

    def _db_fallback(self, **params) -> ERPResolutionResult:
        return ItemDBFallback.lookup(
            item_code=params.get("item_code", ""),
            description=params.get("description", ""),
        )
