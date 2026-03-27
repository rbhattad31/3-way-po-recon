"""Benchmark research tools — web-search-powered market intelligence.

Four tools that the BenchmarkAgent calls during its ReAct loop to gather
real-time market data before synthesising a benchmark price range.

Each tool uses the Bing Web Search API via ``WebSearchClient`` and returns
the raw search snippets so the LLM can interpret them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from apps.procurement.services.web_search_client import WebSearchClient, SearchResponse
from apps.tools.registry.base import BaseTool, ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
def _format_search_results(resp: SearchResponse) -> Dict[str, Any]:
    """Convert a SearchResponse into LLM-friendly dict."""
    if not resp.success:
        return {"found": False, "query": resp.query, "error": resp.error}

    results = []
    for r in resp.results:
        results.append({
            "title": r.title,
            "snippet": r.snippet,
            "url": r.url,
        })
    return {
        "found": bool(results),
        "query": resp.query,
        "result_count": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 1. OEM Catalogue Search Tool
# ---------------------------------------------------------------------------
class OEMCatalogueSearchTool(BaseTool):
    """Search for OEM (manufacturer) list prices and specs.

    Searches public sources for manufacturer catalogue pricing, datasheets,
    and price list references for equipment from brands like Daikin, Carrier,
    Trane, LG, Mitsubishi Electric, etc.
    """

    name = "oem_catalogue_search"
    description = (
        "Search for OEM manufacturer pricing, catalogue prices, and technical "
        "datasheets. Provide the brand, model number, and item category. "
        "Returns web search results with pricing references and specifications. "
        "Use this for major equipment items (VRF, AHU, chillers, FCUs, etc.)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "brand": {
                "type": "string",
                "description": "Manufacturer/brand name (e.g. Daikin, Carrier, Trane)",
            },
            "model": {
                "type": "string",
                "description": "Model number or series (e.g. RXYQ22TATF, VRV IV)",
            },
            "category": {
                "type": "string",
                "description": "Item category (e.g. VRF outdoor unit, cassette AC, AHU)",
            },
            "currency": {
                "type": "string",
                "description": "Target currency for price comparison (e.g. USD, AED)",
            },
        },
        "required": ["brand", "category"],
    }

    def run(self, *, brand: str = "", model: str = "", category: str = "",
            currency: str = "USD", **kwargs) -> ToolResult:
        client = WebSearchClient()

        # Build a search query that avoids anchoring on any quoted price
        query_parts = [brand]
        if model:
            query_parts.append(model)
        query_parts.append(category)
        query_parts.append("price list catalogue")
        query_parts.append(currency)
        query_parts.append("2025 OR 2026")
        query = " ".join(query_parts)

        resp = client.search(query, count=8, market="en-US")
        data = _format_search_results(resp)
        data["search_type"] = "oem_catalogue"
        data["brand"] = brand
        data["model"] = model

        return ToolResult(success=resp.success, data=data)


# ---------------------------------------------------------------------------
# 2. GCC Market Search Tool
# ---------------------------------------------------------------------------
class GCCMarketSearchTool(BaseTool):
    """Search for GCC regional market pricing and availability.

    Searches for pricing context in Gulf Cooperation Council countries
    (UAE, Saudi Arabia, Qatar, Bahrain, Kuwait, Oman) including distributor
    pricing, import duties, regional availability, and market trends.
    """

    name = "gcc_market_search"
    description = (
        "Search for regional GCC market pricing, distributor rates, and "
        "availability context. Provide item category, brand (optional), "
        "and country. Returns web results with regional pricing intelligence. "
        "Use this to understand local market conditions and price adjustments."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Item category (e.g. VRF outdoor unit, copper piping, ductwork)",
            },
            "brand": {
                "type": "string",
                "description": "Brand name if relevant (e.g. Daikin, Carrier)",
            },
            "country": {
                "type": "string",
                "description": "GCC country (e.g. UAE, Saudi Arabia, Qatar)",
            },
            "currency": {
                "type": "string",
                "description": "Target currency (e.g. USD, AED, SAR)",
            },
        },
        "required": ["category", "country"],
    }

    def run(self, *, category: str = "", brand: str = "", country: str = "",
            currency: str = "USD", **kwargs) -> ToolResult:
        client = WebSearchClient()

        # Market-context query — regional pricing, distributor, import context
        query_parts = [category]
        if brand:
            query_parts.append(brand)
        query_parts.append("price")
        query_parts.append(country)
        query_parts.append("supplier OR distributor OR market rate")
        query_parts.append("2025 OR 2026")
        query = " ".join(query_parts)

        # Use region-appropriate market code
        market_map = {
            "UAE": "en-AE", "Saudi Arabia": "en-SA", "KSA": "en-SA",
            "Qatar": "en-QA", "Bahrain": "en-BH", "Kuwait": "en-KW",
            "Oman": "en-OM",
        }
        market = market_map.get(country, "en-US")

        resp = client.search(query, count=8, market=market)
        data = _format_search_results(resp)
        data["search_type"] = "gcc_market"
        data["country"] = country

        return ToolResult(success=resp.success, data=data)


# ---------------------------------------------------------------------------
# 3. Commodity Reference Tool
# ---------------------------------------------------------------------------
class CommodityReferenceTool(BaseTool):
    """Search for commodity / raw material prices that drive installation costs.

    Looks up current market prices for copper, steel, aluminium, refrigerants,
    and other materials. Returns both raw commodity prices and typical
    installed-cost multipliers where available.
    """

    name = "commodity_reference"
    description = (
        "Search for current commodity and raw material prices. Provide the "
        "material type. Returns current market prices and typical cost "
        "multipliers for installed cost estimation. Use for piping, cables, "
        "ductwork, refrigerants, structural steel, and similar materials."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "material": {
                "type": "string",
                "description": "Material type (e.g. copper, steel, aluminium, R-410A, R-32, copper piping)",
            },
            "form": {
                "type": "string",
                "description": "Material form if applicable (e.g. piping 15mm, sheet, cable, tubing)",
            },
            "unit": {
                "type": "string",
                "description": "Unit of measure (e.g. per meter, per kg, per tonne)",
            },
        },
        "required": ["material"],
    }

    def run(self, *, material: str = "", form: str = "", unit: str = "",
            **kwargs) -> ToolResult:
        client = WebSearchClient()

        query_parts = [material]
        if form:
            query_parts.append(form)
        query_parts.append("price")
        if unit:
            query_parts.append(unit)
        query_parts.append("current market rate 2025 OR 2026")
        query = " ".join(query_parts)

        resp = client.search(query, count=8, market="en-US", freshness="Month")
        data = _format_search_results(resp)
        data["search_type"] = "commodity_reference"
        data["material"] = material

        return ToolResult(success=resp.success, data=data)


# ---------------------------------------------------------------------------
# 4. Compliance & Fit-out Context Tool
# ---------------------------------------------------------------------------
class ComplianceContextSearchTool(BaseTool):
    """Search for compliance requirements and fit-out guidelines that affect pricing.

    Looks up building codes, municipality requirements, ASHRAE/CIBSE standards,
    landlord fit-out guides, and regulatory requirements that may impact the
    scope and cost of quoted items.
    """

    name = "compliance_context_search"
    description = (
        "Search for compliance requirements, building codes, and fit-out "
        "guidelines that affect procurement scope and cost. Provide the "
        "regulation type and region. Use to understand if quoted items "
        "include required compliance costs (fire-rating, efficiency, etc.)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "requirement_type": {
                "type": "string",
                "description": (
                    "Type of requirement (e.g. fire-rated ductwork, energy efficiency, "
                    "refrigerant regulations, ventilation requirements)"
                ),
            },
            "standard": {
                "type": "string",
                "description": "Specific standard if known (e.g. ASHRAE 90.1, CIBSE, Dubai Municipality, SASO)",
            },
            "region": {
                "type": "string",
                "description": "Region or country (e.g. UAE, Dubai, Saudi Arabia)",
            },
        },
        "required": ["requirement_type", "region"],
    }

    def run(self, *, requirement_type: str = "", standard: str = "",
            region: str = "", **kwargs) -> ToolResult:
        client = WebSearchClient()

        query_parts = [requirement_type]
        if standard:
            query_parts.append(standard)
        query_parts.append(region)
        query_parts.append("requirements regulations guidelines")
        query = " ".join(query_parts)

        resp = client.search(query, count=6, market="en-US")
        data = _format_search_results(resp)
        data["search_type"] = "compliance_context"
        data["requirement_type"] = requirement_type
        data["region"] = region

        return ToolResult(success=resp.success, data=data)


# ---------------------------------------------------------------------------
# Registration — register all 4 tools into the global ToolRegistry
# ---------------------------------------------------------------------------
def register_benchmark_tools() -> None:
    """Instantiate and register all benchmark research tools."""
    for tool_cls in (
        OEMCatalogueSearchTool,
        GCCMarketSearchTool,
        CommodityReferenceTool,
        ComplianceContextSearchTool,
    ):
        instance = tool_cls()
        ToolRegistry.register(instance)
        logger.debug("Registered benchmark tool: %s", instance.name)


# Auto-register on import
register_benchmark_tools()
