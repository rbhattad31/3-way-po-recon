"""GRN DB fallback — wraps existing GoodsReceiptNote model lookups.

documents.GoodsReceiptNote is the canonical internal ERP mirror for goods
receipt data (MIRROR_DB). There is no separate ERP reference import for GRNs;
unlike POs, GRN snapshots are not imported via Excel/CSV reference batches.

The resolved value dict always includes ``grn_ids`` so callers can hydrate
full ORM objects (needed for GRN line-level matching in reconciliation).
"""
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
    """DB fallback for GRN lookups using the documents.GoodsReceiptNote model (MIRROR_DB)."""

    @staticmethod
    def lookup(po_number: str = "", grn_number: str = "", **kwargs) -> ERPResolutionResult:
        """Look up GRNs from the internal mirror (documents.GoodsReceiptNote)."""
        from apps.documents.models import GoodsReceiptNote, PurchaseOrder

        if not po_number and not grn_number:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.NONE,
                reason="po_number or grn_number is required",
            )

        grns = []

        # Direct GRN lookup by GRN number
        if grn_number:
            grn = GoodsReceiptNote.objects.filter(grn_number=grn_number).prefetch_related("line_items").first()
            if grn:
                grns = [grn]

        # Lookup all GRNs for a PO
        if not grns and po_number:
            po = PurchaseOrder.objects.filter(po_number=po_number).first()
            if not po:
                return ERPResolutionResult(
                    resolved=False, source_type=ERPSourceType.MIRROR_DB,
                    reason=f"PO '{po_number}' not found in internal mirror — cannot look up GRNs",
                )
            grns = list(
                GoodsReceiptNote.objects.filter(purchase_order=po)
                .prefetch_related("line_items")
                .order_by("receipt_date")
            )

        if not grns:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.MIRROR_DB,
                reason=(
                    f"No GRNs found for "
                    + (f"GRN '{grn_number}'" if grn_number else f"PO '{po_number}'")
                ),
            )

        return _build_grn_result(grns)


def _build_grn_result(grns) -> ERPResolutionResult:
    """Build an ERPResolutionResult from a list of GoodsReceiptNote ORM objects."""
    grn_data = []
    # Use the most-recently-updated GRN as the synced_at timestamp
    synced_at = None

    for grn in grns:
        lines = list(grn.line_items.all().values(
            "id", "line_number", "item_code", "description",
            "quantity_received", "quantity_accepted", "quantity_rejected",
            "po_line_id",
        ))
        grn_data.append({
            "grn_id": grn.pk,
            "grn_number": grn.grn_number,
            "receipt_date": str(grn.receipt_date) if grn.receipt_date else None,
            "status": grn.status,
            "warehouse": getattr(grn, "warehouse", None),
            "line_items": json.loads(json.dumps(lines, default=_decimal_default)),
        })
        grn_updated = getattr(grn, "updated_at", None) or getattr(grn, "created_at", None)
        if grn_updated and (synced_at is None or grn_updated > synced_at):
            synced_at = grn_updated

    grn_ids = [g.pk for g in grns]

    return ERPResolutionResult(
        resolved=True,
        value={
            "grns": grn_data,
            "grn_ids": grn_ids,
            "grn_count": len(grn_data),
        },
        source_type=ERPSourceType.MIRROR_DB,
        confidence=1.0,
        synced_at=synced_at,
        source_keys={"grn_ids": ",".join(str(i) for i in grn_ids)},
        reason=f"Found {len(grn_data)} GRN(s) in internal mirror",
    )
