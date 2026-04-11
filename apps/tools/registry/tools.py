"""Concrete agent tools — PO lookup, GRN lookup, vendor search, invoice details, exception list."""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List

from apps.core.utils import normalize_category, resolve_line_tax_percentage, resolve_tax_percentage
from apps.tools.registry.base import BaseTool, ToolResult, register_tool

logger = logging.getLogger(__name__)


def _decimal_serialise(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# PO Lookup Tool
# ---------------------------------------------------------------------------
@register_tool
class POLookupTool(BaseTool):
    name = "po_lookup"
    required_permission = "purchase_orders.view"
    description = (
        "Look up a Purchase Order by PO number, or list open POs for a vendor. "
        "Supports exact, normalized, and partial/contains matching on PO numbers. "
        "Pass vendor_id alone to list all open POs for that vendor."
    )
    when_to_use = "When you need to verify a PO number exists, get PO header/line details, or find open POs for a vendor."
    when_not_to_use = "Do not use as evidence that goods were received. PO existence does not confirm delivery."
    no_result_meaning = "No matching PO found in the system. Does not prove the PO does not exist in ERP -- it may not be synced yet."
    failure_handling_instruction = "On tool failure, do not infer PO existence from memory alone. Report uncertainty."
    authoritative_fields = ["po_number", "vendor_id", "total_amount", "status", "line_items"]
    evidence_keys_produced = ["found", "po_number", "vendor", "total_amount", "status", "line_items"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "po_number": {"type": "string", "description": "The PO number to look up (supports partial match)"},
            "vendor_id": {"type": "integer", "description": "Vendor PK — list open POs for this vendor"},
        },
        "required": [],
    }

    def run(self, *, po_number: str = "", vendor_id: int = 0, **kwargs) -> ToolResult:
        from apps.core.utils import normalize_po_number
        from apps.documents.models import PurchaseOrder

        # If vendor_id given without po_number, list POs for that vendor
        if vendor_id and not po_number:
            pos = self._scoped(PurchaseOrder.objects.filter(vendor_id=vendor_id, status="OPEN"))[:10]
            if not pos:
                return ToolResult(success=True, data={
                    "found": False, "vendor_id": vendor_id,
                    "message": "No open POs found for this vendor",
                })
            po_list = []
            for p in pos:
                po_list.append({
                    "po_number": p.po_number,
                    "total_amount": str(p.total_amount),
                    "po_date": str(p.po_date) if p.po_date else None,
                })
            return ToolResult(success=True, data={
                "found": True, "vendor_id": vendor_id,
                "po_count": len(po_list), "purchase_orders": po_list,
            })

        if not po_number:
            return ToolResult(success=False, error="po_number or vendor_id is required")

        # Resolve via ERP integration layer (API → DB fallback)
        resolution_result = self._resolve_via_erp(po_number, vendor_id, **kwargs)
        if resolution_result is not None:
            return resolution_result

        # Direct DB lookup (legacy path -- only reached if resolver import fails)
        po = self._scoped(PurchaseOrder.objects.filter(po_number=po_number)).first()
        if not po:
            norm = normalize_po_number(po_number)
            po = self._scoped(PurchaseOrder.objects.filter(normalized_po_number=norm)).first()

        # Fallback: contains match (e.g., "2601001" matches "PO-MCD-2601001")
        if not po:
            candidates = self._scoped(PurchaseOrder.objects.filter(po_number__icontains=po_number))
            if vendor_id:
                candidates = candidates.filter(vendor_id=vendor_id)
            po = candidates.first()

        if not po:
            return ToolResult(success=True, data={"found": False, "po_number": po_number})

        lines = list(po.line_items.all().values(
            "line_number", "item_code", "description", "quantity",
            "unit_price", "tax_amount", "line_amount", "unit_of_measure",
        ))
        return ToolResult(success=True, data={
            "found": True,
            "po_number": po.po_number,
            "vendor": po.vendor.name if po.vendor else None,
            "vendor_id": po.vendor_id,
            "po_date": str(po.po_date) if po.po_date else None,
            "currency": po.currency,
            "total_amount": str(po.total_amount),
            "tax_amount": str(po.tax_amount),
            "status": po.status,
            "line_items": json.loads(json.dumps(lines, default=_decimal_serialise)),
        })

    def _resolve_via_erp(self, po_number: str, vendor_id: int = 0, **kwargs):
        """Attempt resolution via ERPResolutionService.

        Returns a ToolResult if resolved (found or not found), or None to
        fall through to legacy direct DB lookup.
        """
        try:
            from apps.erp_integration.services.resolution_service import ERPResolutionService

            svc = ERPResolutionService.with_default_connector()
            result = svc.resolve_po(
                po_number=po_number,
                reconciliation_result_id=kwargs.get("reconciliation_result_id"),
                lf_parent_span=kwargs.get("lf_parent_span"),
            )

            if not result.resolved:
                return ToolResult(success=True, data={
                    "found": False,
                    "po_number": po_number,
                    "_erp_source": result.source_type,
                    "_erp_fallback_used": result.fallback_used,
                })

            data = result.value or {}
            data["_erp_source"] = result.source_type
            data["_erp_confidence"] = result.confidence
            data["_erp_fallback_used"] = result.fallback_used
            data["_erp_is_stale"] = result.is_stale
            data["found"] = True
            return ToolResult(success=True, data=data)
        except Exception:
            logger.debug("ERPResolutionService not available for PO lookup", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# GRN Lookup Tool
# ---------------------------------------------------------------------------
@register_tool
class GRNLookupTool(BaseTool):
    name = "grn_lookup"
    required_permission = "grns.view"
    description = (
        "Look up Goods Receipt Notes for a given PO number. "
        "Returns GRN headers and received quantities."
    )
    when_to_use = "When you need to confirm whether goods were physically received for a given PO in 3-WAY mode."
    when_not_to_use = "Do not use in TWO_WAY reconciliation mode. GRN is not applicable there."
    no_result_meaning = "No GRN found for this PO. Goods may not have been received yet or GRN not yet recorded."
    failure_handling_instruction = "On tool failure, do not assume goods were or were not received. Escalate if receipt status is critical."
    authoritative_fields = ["grn_number", "receipt_date", "status", "line_items.quantity_received"]
    evidence_keys_produced = ["found", "po_number", "grn_count", "grns"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "po_number": {"type": "string", "description": "The PO number to find GRNs for"},
        },
        "required": ["po_number"],
    }

    def run(self, *, po_number: str = "", **kwargs) -> ToolResult:
        from apps.documents.models import PurchaseOrder, GoodsReceiptNote

        if not po_number:
            return ToolResult(success=False, error="po_number is required")

        # Resolve via ERP integration layer (API → DB fallback)
        resolution_result = self._resolve_via_erp(po_number, **kwargs)
        if resolution_result is not None:
            return resolution_result

        # Direct DB lookup (legacy path -- only reached if resolver import fails)
        po = self._scoped(PurchaseOrder.objects.filter(po_number=po_number)).first()
        if not po:
            return ToolResult(success=True, data={"found": False, "po_number": po_number})

        grns = GoodsReceiptNote.objects.filter(purchase_order=po).prefetch_related("line_items")
        grn_data = []
        for grn in grns:
            lines = list(grn.line_items.all().values(
                "line_number", "item_code", "description",
                "quantity_received", "quantity_accepted", "quantity_rejected",
            ))
            grn_data.append({
                "grn_number": grn.grn_number,
                "receipt_date": str(grn.receipt_date) if grn.receipt_date else None,
                "status": grn.status,
                "warehouse": grn.warehouse,
                "line_items": json.loads(json.dumps(lines, default=_decimal_serialise)),
            })

        return ToolResult(success=True, data={
            "found": True,
            "po_number": po_number,
            "grn_count": len(grn_data),
            "grns": grn_data,
        })

    def _resolve_via_erp(self, po_number: str, **kwargs):
        """Attempt resolution via ERPResolutionService.

        Returns a ToolResult if resolved (found or not found), or None to
        fall through to legacy direct DB lookup.
        """
        try:
            from apps.erp_integration.services.resolution_service import ERPResolutionService

            svc = ERPResolutionService.with_default_connector()
            result = svc.resolve_grn(
                po_number=po_number,
                reconciliation_result_id=kwargs.get("reconciliation_result_id"),
                lf_parent_span=kwargs.get("lf_parent_span"),
            )

            if not result.resolved:
                return ToolResult(success=True, data={
                    "found": False,
                    "po_number": po_number,
                    "_erp_source": result.source_type,
                    "_erp_fallback_used": result.fallback_used,
                })

            data = result.value or {}
            data["_erp_source"] = result.source_type
            data["_erp_confidence"] = result.confidence
            data["_erp_fallback_used"] = result.fallback_used
            data["_erp_is_stale"] = result.is_stale
            data["found"] = True
            data["po_number"] = po_number
            return ToolResult(success=True, data=data)
        except Exception:
            logger.debug("ERPResolutionService not available for GRN lookup", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Vendor Search Tool
# ---------------------------------------------------------------------------
@register_tool
class VendorSearchTool(BaseTool):
    name = "vendor_search"
    required_permission = "vendors.view"
    description = (
        "Search for a vendor by name, code, or alias. "
        "Use when the invoice vendor doesn't match the PO vendor."
    )
    when_to_use = "When the invoice vendor name does not match the PO vendor and you need to check for aliases or alternate names."
    when_not_to_use = "Do not use to confirm payment terms or spending limits -- this tool only returns identity data."
    no_result_meaning = "No vendor match found. The vendor may be unknown, inactive, or using a name not yet registered as an alias."
    failure_handling_instruction = "On failure, do not assume vendor identity from invoice text alone. Flag for manual vendor verification."
    authoritative_fields = ["vendor_id", "code", "name", "match_type"]
    evidence_keys_produced = ["query", "count", "vendors"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Vendor name, code, or alias to search for"},
        },
        "required": ["query"],
    }

    def run(self, *, query: str = "", **kwargs) -> ToolResult:
        from apps.core.utils import normalize_string
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        if not query:
            return ToolResult(success=False, error="query is required")

        norm = normalize_string(query)
        results = []

        # Name / code search
        vendors = self._scoped(Vendor.objects.filter(
            is_active=True,
        )).filter(
            models_q_name_code(query, norm)
        )[:10]
        for v in vendors:
            results.append({
                "vendor_id": v.pk,
                "code": v.code,
                "name": v.name,
                "match_type": "direct",
            })

        # Alias search
        aliases = self._scoped(VendorAliasMapping.objects.filter(
            normalized_alias=norm, is_active=True,
        )).select_related("vendor")[:5]
        seen = {r["vendor_id"] for r in results}
        for a in aliases:
            if a.vendor and a.vendor_id not in seen:
                results.append({
                    "vendor_id": a.vendor_id,
                    "code": a.vendor.code,
                    "name": a.vendor.name,
                    "alias": a.alias_text,
                    "match_type": "alias",
                })

        return ToolResult(success=True, data={
            "query": query,
            "count": len(results),
            "vendors": results,
        })


def models_q_name_code(raw: str, normalized: str):
    from django.db.models import Q
    return (
        Q(code__iexact=raw)
        | Q(normalized_name=normalized)
        | Q(name__icontains=raw)
    )


# ---------------------------------------------------------------------------
# Invoice Details Tool
# ---------------------------------------------------------------------------
@register_tool
class InvoiceDetailsTool(BaseTool):
    name = "invoice_details"
    required_permission = "invoices.view"
    description = (
        "Get full details of an invoice including header, line items, and extraction metadata."
    )
    when_to_use = "When you need the full extracted invoice data including header fields, line items, and extraction confidence."
    when_not_to_use = "Do not use to look up PO or GRN data -- this tool returns invoice data only."
    no_result_meaning = "Invoice not found by ID. This is a system error -- the invoice should always exist if the context is valid."
    failure_handling_instruction = "On failure, rely only on the context already provided. Do not fabricate invoice fields."
    authoritative_fields = ["invoice_number", "vendor", "po_number", "total_amount", "extraction_confidence", "line_items"]
    evidence_keys_produced = ["invoice_id", "invoice_number", "vendor_id", "total_amount", "extraction_confidence", "line_items"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "invoice_id": {"type": "integer", "description": "The Invoice PK"},
        },
        "required": ["invoice_id"],
    }

    def run(self, *, invoice_id: int = 0, **kwargs) -> ToolResult:
        from apps.documents.models import Invoice

        try:
            inv = self._scoped(
                Invoice.objects.select_related("vendor", "document_upload")
            ).get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        raw_line_items = ((inv.extraction_raw_json or {}).get("line_items") or [])
        lines = []
        for line in inv.line_items.all():
            raw_line = raw_line_items[line.line_number - 1] if line.line_number - 1 < len(raw_line_items) and isinstance(raw_line_items[line.line_number - 1], dict) else {}
            tax_percentage = resolve_line_tax_percentage(
                raw_percentage=raw_line.get("tax_percentage"),
                tax_amount=line.tax_amount,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_amount=line.line_amount,
            )
            lines.append({
                "line_number": line.line_number,
                "description": line.description,
                "item_category": (
                    normalize_category(line.item_category)
                    or normalize_category(raw_line.get("item_category") or raw_line.get("category"))
                    or ("Service" if line.is_service_item else "")
                    or ("Stock" if line.is_stock_item else "")
                    or "Other"
                ),
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "tax_percentage": tax_percentage,
                "tax_amount": line.tax_amount,
                "line_amount": line.line_amount,
            })

        header_tax_percentage = resolve_tax_percentage(
            raw_percentage=(inv.extraction_raw_json or {}).get("tax_percentage"),
            tax_amount=inv.tax_amount,
            base_amount=inv.subtotal,
        )

        return ToolResult(success=True, data={
            "invoice_id": inv.pk,
            "invoice_number": inv.invoice_number,
            "vendor": inv.vendor.name if inv.vendor else inv.raw_vendor_name,
            "vendor_id": inv.vendor_id,
            "po_number": inv.po_number,
            "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
            "currency": inv.currency,
            "subtotal": str(inv.subtotal),
            "tax_percentage": str(header_tax_percentage) if header_tax_percentage is not None else None,
            "tax_amount": str(inv.tax_amount),
            "total_amount": str(inv.total_amount),
            "status": inv.status,
            "extraction_confidence": inv.extraction_confidence,
            "is_duplicate": inv.is_duplicate,
            "line_items": json.loads(json.dumps(lines, default=_decimal_serialise)),
        })


# ---------------------------------------------------------------------------
# Exception List Tool
# ---------------------------------------------------------------------------
@register_tool
class ExceptionListTool(BaseTool):
    name = "exception_list"
    required_permission = "reconciliation.view"
    description = (
        "Retrieve all reconciliation exceptions for a given ReconciliationResult. "
        "Use to understand what mismatches were found."
    )
    when_to_use = "When you need the full list of reconciliation exceptions for root cause analysis."
    when_not_to_use = "Do not use to retrieve invoice or PO data -- call invoice_details or po_lookup instead."
    no_result_meaning = "No exceptions found. The reconciliation result may have no discrepancies at this point."
    failure_handling_instruction = "On failure, use the exceptions already present in the agent context."
    authoritative_fields = ["exception_type", "severity", "message", "resolved"]
    evidence_keys_produced = ["reconciliation_result_id", "exceptions"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "reconciliation_result_id": {
                "type": "integer",
                "description": "The ReconciliationResult PK",
            },
        },
        "required": ["reconciliation_result_id"],
    }

    def run(self, *, reconciliation_result_id: int = 0, **kwargs) -> ToolResult:
        from apps.reconciliation.models import ReconciliationException

        excs = self._scoped(ReconciliationException.objects.filter(
            result_id=reconciliation_result_id,
        )).values(
            "id", "exception_type", "severity", "message", "resolved",
        )
        return ToolResult(success=True, data={
            "reconciliation_result_id": reconciliation_result_id,
            "exceptions": list(excs),
        })


# ---------------------------------------------------------------------------
# Reconciliation Summary Tool
# ---------------------------------------------------------------------------
@register_tool
class ReconciliationSummaryTool(BaseTool):
    name = "reconciliation_summary"
    required_permission = "reconciliation.view"
    description = (
        "Get the reconciliation result summary for a given ReconciliationResult, "
        "including match status, confidence, and header-level evidence."
    )
    when_to_use = "When you need the match status, confidence scores, and header-level comparison results for a reconciliation."
    when_not_to_use = "Do not use to get line item detail -- use invoice_details or po_lookup for that."
    no_result_meaning = "Reconciliation result not found. This is a system error if the context references a valid result ID."
    failure_handling_instruction = "On failure, use match_status and confidence from the agent context. Do not fabricate match outcomes."
    authoritative_fields = ["match_status", "deterministic_confidence", "vendor_match", "po_total_match", "total_amount_difference", "grn_available", "grn_fully_received"]
    evidence_keys_produced = ["result_id", "match_status", "vendor_match", "po_total_match", "total_amount_difference", "grn_available"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "reconciliation_result_id": {
                "type": "integer",
                "description": "The ReconciliationResult PK",
            },
        },
        "required": ["reconciliation_result_id"],
    }

    def run(self, *, reconciliation_result_id: int = 0, **kwargs) -> ToolResult:
        from apps.reconciliation.models import ReconciliationResult

        try:
            r = self._scoped(
                ReconciliationResult.objects.select_related("invoice", "purchase_order")
            ).get(pk=reconciliation_result_id)
        except ReconciliationResult.DoesNotExist:
            return ToolResult(success=False, error=f"Result {reconciliation_result_id} not found")

        return ToolResult(success=True, data={
            "result_id": r.pk,
            "invoice_id": r.invoice_id,
            "invoice_number": r.invoice.invoice_number if r.invoice else None,
            "po_number": r.purchase_order.po_number if r.purchase_order else None,
            "match_status": r.match_status,
            "requires_review": r.requires_review,
            "vendor_match": r.vendor_match,
            "currency_match": r.currency_match,
            "po_total_match": r.po_total_match,
            "total_amount_difference": str(r.total_amount_difference) if r.total_amount_difference else None,
            "grn_available": r.grn_available,
            "grn_fully_received": r.grn_fully_received,
            "extraction_confidence": r.extraction_confidence,
            "deterministic_confidence": r.deterministic_confidence,
            "summary": r.summary,
        })
