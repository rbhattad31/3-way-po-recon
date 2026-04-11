"""Plugin Tool Router -- lightweight routing layer for tool implementations.

Maps standard tool names to the correct backend implementation based on the
active ERP connector configuration. This is a code-only abstraction with no
DB models, UI, or marketplace -- just a routing layer.

Usage::

    from apps.agents.plugins.plugin_router import PluginToolRouter

    # Resolve the best implementation for a tool
    result = PluginToolRouter.execute("po_lookup", po_number="PO-123", tenant=tenant)

The router checks the tenant's active ERP connector and routes to the
appropriate implementation. If no ERP connector is active, it falls back
to the default ToolRegistry implementation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.tools.registry.base import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


class PluginToolRouter:
    """Routes tool calls through the ERP integration layer when available.

    Resolution chain:
      1. Check if tenant has an active ERP connector
      2. If yes, route ERP-related tools through ERPResolutionService
      3. Fall back to default ToolRegistry implementation
    """

    # Tools that may be routed through ERP connectors
    ERP_ROUTABLE_TOOLS = frozenset({
        "po_lookup",
        "grn_lookup",
        "vendor_search",
        "verify_vendor",
        "check_duplicate",
    })

    @classmethod
    def execute(
        cls,
        tool_name: str,
        tenant: Any = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Execute a tool, routing through ERP if applicable.

        Args:
            tool_name: The registered tool name.
            tenant: CompanyProfile instance for tenant scoping.
            **kwargs: Arguments to pass to the tool.

        Returns:
            ToolResult from the resolved implementation.
        """
        # For ERP-routable tools, try connector-aware resolution first
        if tool_name in cls.ERP_ROUTABLE_TOOLS and tenant:
            erp_result = cls._try_erp_route(tool_name, tenant, **kwargs)
            if erp_result is not None:
                return erp_result

        # Default: use the standard ToolRegistry
        tool = ToolRegistry.get(tool_name)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not found in registry",
            )
        return tool.execute(tenant=tenant, **kwargs)

    @classmethod
    def _try_erp_route(
        cls,
        tool_name: str,
        tenant: Any,
        **kwargs: Any,
    ) -> Optional[ToolResult]:
        """Attempt ERP-connector routing. Returns None to fall back to default."""
        try:
            from apps.erp_integration.services.connector_factory import ConnectorFactory
            connector = ConnectorFactory.get_default_connector(tenant=tenant)
            if connector is None:
                return None  # No active ERP connector -- fall back
        except Exception:
            logger.debug(
                "ERP connector lookup failed for tool %s (non-fatal, falling back)",
                tool_name,
                exc_info=True,
            )
            return None

        # Route to ERP-aware implementations
        try:
            if tool_name == "po_lookup" and connector.supports_po_lookup():
                return cls._erp_po_lookup(connector, tenant, **kwargs)
            if tool_name == "grn_lookup" and connector.supports_grn_lookup():
                return cls._erp_grn_lookup(connector, tenant, **kwargs)
            if tool_name == "vendor_search" and connector.supports_vendor_lookup():
                return cls._erp_vendor_search(connector, tenant, **kwargs)
        except Exception:
            logger.debug(
                "ERP route failed for tool %s (non-fatal, falling back to default)",
                tool_name,
                exc_info=True,
            )
        return None  # Fall back to default ToolRegistry

    @classmethod
    def _erp_po_lookup(cls, connector, tenant, **kwargs) -> ToolResult:
        """Route PO lookup through ERP resolution service."""
        from apps.erp_integration.services.resolution_service import ERPResolutionService
        po_number = kwargs.get("po_number", "")
        if not po_number:
            return ToolResult(success=False, error="po_number required for ERP PO lookup")
        result = ERPResolutionService.resolve_po(
            po_number=po_number,
            tenant=tenant,
        )
        if result and result.get("found"):
            return ToolResult(success=True, data={
                **result,
                "_source": "erp_connector",
                "_connector_type": str(getattr(connector, "connector_type", "")),
            })
        return ToolResult(success=True, data={"found": False, "po_number": po_number})

    @classmethod
    def _erp_grn_lookup(cls, connector, tenant, **kwargs) -> ToolResult:
        """Route GRN lookup through ERP resolution service."""
        from apps.erp_integration.services.resolution_service import ERPResolutionService
        po_number = kwargs.get("po_number", "")
        if not po_number:
            return ToolResult(success=False, error="po_number required for ERP GRN lookup")
        result = ERPResolutionService.resolve_grn(
            po_number=po_number,
            tenant=tenant,
        )
        if result and result.get("found"):
            return ToolResult(success=True, data={
                **result,
                "_source": "erp_connector",
            })
        return ToolResult(success=True, data={"found": False, "po_number": po_number})

    @classmethod
    def _erp_vendor_search(cls, connector, tenant, **kwargs) -> ToolResult:
        """Route vendor search through ERP resolution service."""
        from apps.erp_integration.services.resolution_service import ERPResolutionService
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query required for ERP vendor search")
        result = ERPResolutionService.resolve_vendor(
            vendor_name=query,
            tenant=tenant,
        )
        if result and result.get("found"):
            return ToolResult(success=True, data={
                **result,
                "_source": "erp_connector",
            })
        return ToolResult(success=True, data={"found": False, "query": query})
