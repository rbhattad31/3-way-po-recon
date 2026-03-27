"""GRN DB fallback — wraps existing GoodsReceiptNote model lookups."""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class GRNDBFallback:
    """DB fallback for GRN lookups using the documents.GoodsReceiptNote model."""

    @staticmethod
    def lookup(po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        """Look up GRNs from local database, linked via PurchaseOrder."""
        from apps.documents.models import GoodsReceiptNote, PurchaseOrder

        if not po_number and not grn_number:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="po_number or grn_number is required",
            )

        # Direct GRN lookup
        if grn_number:
            grn = GoodsReceiptNote.objects.filter(grn_number=grn_number).first()
            if grn:
                return _grn_result([grn])

        # Via PO
        if po_number:
            po = PurchaseOrder.objects.filter(po_number=po_number).first()
            if not po:
                return ERPResolutionResult(
                    resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                    reason=f"PO '{po_number}' not found — cannot look up GRNs",
                )
            grns = GoodsReceiptNote.objects.filter(
                purchase_order=po
            ).prefetch_related("line_items")
            if not grns.exists():
                return ERPResolutionResult(
                    resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                    reason=f"No GRNs found for PO '{po_number}'",
                )
            return _grn_result(list(grns))

        return ERPResolutionResult(
            resolved=False, source_type=ERPSourceType.DB_FALLBACK,
            reason="No matching GRNs found",
        )


def _grn_result(grns) -> ERPResolutionResult:
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
            "line_items": json.loads(json.dumps(lines, default=_decimal_default)),
        })
    return ERPResolutionResult(
        resolved=True,
        value={"grns": grn_data, "grn_count": len(grn_data)},
        source_type=ERPSourceType.DB_FALLBACK,
        confidence=1.0,
        reason=f"Found {len(grn_data)} GRN(s) in database",
    )
