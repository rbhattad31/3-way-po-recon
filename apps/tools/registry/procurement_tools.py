"""Procurement tools registered in the shared ToolRegistry.

These tools provide governed access paths for procurement agent lookups.
Each tool declares required_permission so AgentGuardrailsService can enforce
tool-level authorization consistently.
"""
from __future__ import annotations

from typing import Any, Dict

from apps.tools.registry.base import BaseTool, ToolResult, register_tool


@register_tool
class MarketPriceLookupTool(BaseTool):
    name = "market_price_lookup"
    description = "Resolve indicative market pricing for an equipment description."
    required_permission = "procurement.view_results"
    parameters_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "geography": {"type": "string"},
            "uom": {"type": "string"},
            "currency": {"type": "string"},
        },
        "required": ["description"],
    }

    def run(self, **kwargs) -> ToolResult:
        from apps.procurement.services.web_search_service import WebSearchService

        data = WebSearchService.search_market_rate(
            description=str(kwargs.get("description", "") or ""),
            geography=str(kwargs.get("geography", "") or "UAE"),
            uom=str(kwargs.get("uom", "") or ""),
            currency=str(kwargs.get("currency", "") or "AED"),
        )
        return ToolResult(success=True, data=data)


@register_tool
class VendorCatalogLookupTool(BaseTool):
    name = "vendor_catalog_lookup"
    description = "Lookup vendor and supplier quotation references for a request."
    required_permission = "procurement.view"
    parameters_schema = {
        "type": "object",
        "properties": {
            "request_id": {"type": "integer"},
        },
        "required": ["request_id"],
    }

    def run(self, **kwargs) -> ToolResult:
        from apps.procurement.models import SupplierQuotation

        request_id = int(kwargs["request_id"])
        queryset = self._scoped(
            SupplierQuotation.objects.filter(request_id=request_id, is_active=True)
        )
        records = [
            {
                "quotation_id": q.pk,
                "vendor_name": q.vendor_name,
                "quotation_number": q.quotation_number,
                "currency": q.currency,
                "total_amount": float(q.total_amount) if q.total_amount is not None else None,
            }
            for q in queryset[:50]
        ]
        return ToolResult(success=True, data={"count": len(records), "items": records})


@register_tool
class StandardsComplianceLookupTool(BaseTool):
    name = "standards_compliance_lookup"
    description = "Return baseline standards and compliance references by domain and geography."
    required_permission = "procurement.view_results"
    parameters_schema = {
        "type": "object",
        "properties": {
            "domain_code": {"type": "string"},
            "geography_country": {"type": "string"},
        },
        "required": ["domain_code"],
    }

    def run(self, **kwargs) -> ToolResult:
        domain = str(kwargs.get("domain_code", "") or "").upper()
        country = str(kwargs.get("geography_country", "") or "").upper()

        standards = []
        if domain == "HVAC":
            standards.extend(["ASHRAE 90.1", "ASHRAE 62.1"])
            if country in {"UAE", "KSA", "QATAR"}:
                standards.append("Regional authority pre-approval required")
        if not standards:
            standards.append("General procurement policy compliance")

        return ToolResult(success=True, data={"domain": domain, "country": country, "standards": standards})


@register_tool
class QuotationEvidenceLookupTool(BaseTool):
    name = "quotation_evidence_lookup"
    description = "Fetch quotation line evidence for a specific quotation."
    required_permission = "procurement.view_results"
    parameters_schema = {
        "type": "object",
        "properties": {
            "quotation_id": {"type": "integer"},
        },
        "required": ["quotation_id"],
    }

    def run(self, **kwargs) -> ToolResult:
        from apps.procurement.models import QuotationLineItem

        quotation_id = int(kwargs["quotation_id"])
        queryset = self._scoped(
            QuotationLineItem.objects.filter(quotation_id=quotation_id, is_active=True)
        )
        items = [
            {
                "line_id": li.pk,
                "description": li.description,
                "quantity": float(li.quantity) if li.quantity is not None else None,
                "unit_rate": float(li.unit_rate) if li.unit_rate is not None else None,
                "total_amount": float(li.total_amount) if li.total_amount is not None else None,
            }
            for li in queryset[:200]
        ]
        return ToolResult(success=True, data={"quotation_id": quotation_id, "line_items": items})


@register_tool
class RegionalRegulationLookupTool(BaseTool):
    name = "regional_regulation_lookup"
    description = "Return region-specific compliance pointers for procurement decisions."
    required_permission = "procurement.view_results"
    parameters_schema = {
        "type": "object",
        "properties": {
            "country": {"type": "string"},
        },
        "required": ["country"],
    }

    def run(self, **kwargs) -> ToolResult:
        country = str(kwargs.get("country", "") or "").upper()
        map_data: Dict[str, Any] = {
            "UAE": ["DEWA compliance", "Civil Defence coordination"],
            "KSA": ["SASO standards", "SEC utility constraints"],
            "QATAR": ["QCDD fire code", "Kahramaa requirements"],
            "INDIA": ["BEE star labeling", "state electrical approvals"],
        }
        return ToolResult(
            success=True,
            data={
                "country": country,
                "regulations": map_data.get(country, ["General local regulatory review required"]),
            },
        )
