"""Concrete agent tools — PO lookup, GRN lookup, vendor search, invoice details, exception list."""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List

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
            pos = PurchaseOrder.objects.filter(vendor_id=vendor_id, status="OPEN")[:10]
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

        po = PurchaseOrder.objects.filter(po_number=po_number).first()
        if not po:
            norm = normalize_po_number(po_number)
            po = PurchaseOrder.objects.filter(normalized_po_number=norm).first()

        # Fallback: contains match (e.g., "2601001" matches "PO-MCD-2601001")
        if not po:
            candidates = PurchaseOrder.objects.filter(po_number__icontains=po_number)
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

        po = PurchaseOrder.objects.filter(po_number=po_number).first()
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
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Vendor name, code, or alias to search for"},
        },
        "required": ["query"],
    }

    def run(self, *, query: str = "", **kwargs) -> ToolResult:
        from apps.core.utils import normalize_string
        from apps.vendors.models import Vendor, VendorAlias

        if not query:
            return ToolResult(success=False, error="query is required")

        norm = normalize_string(query)
        results = []

        # Name / code search
        vendors = Vendor.objects.filter(
            is_active=True,
        ).filter(
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
        aliases = VendorAlias.objects.filter(
            normalized_alias=norm,
        ).select_related("vendor")[:5]
        seen = {r["vendor_id"] for r in results}
        for a in aliases:
            if a.vendor_id not in seen:
                results.append({
                    "vendor_id": a.vendor_id,
                    "code": a.vendor.code,
                    "name": a.vendor.name,
                    "alias": a.alias_name,
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
            inv = Invoice.objects.select_related("vendor", "document_upload").get(pk=invoice_id)
        except Invoice.DoesNotExist:
            return ToolResult(success=False, error=f"Invoice {invoice_id} not found")

        lines = list(inv.line_items.all().values(
            "line_number", "description", "quantity", "unit_price",
            "tax_amount", "line_amount",
        ))

        return ToolResult(success=True, data={
            "invoice_id": inv.pk,
            "invoice_number": inv.invoice_number,
            "vendor": inv.vendor.name if inv.vendor else inv.raw_vendor_name,
            "vendor_id": inv.vendor_id,
            "po_number": inv.po_number,
            "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
            "currency": inv.currency,
            "subtotal": str(inv.subtotal),
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

        excs = ReconciliationException.objects.filter(
            result_id=reconciliation_result_id,
        ).values(
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
            r = ReconciliationResult.objects.select_related(
                "invoice", "purchase_order"
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
