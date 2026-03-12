"""GRN lookup service — retrieves Goods Receipt Notes linked to a PO."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from apps.documents.models import (
    GoodsReceiptNote,
    GRNLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)

logger = logging.getLogger(__name__)


@dataclass
class GRNSummary:
    """Aggregated GRN data for a single PO."""

    grn_available: bool = False
    grns: List[GoodsReceiptNote] = field(default_factory=list)
    total_received_by_po_line: Dict[int, Decimal] = field(default_factory=dict)
    fully_received: bool = False
    latest_receipt_date: Optional[date] = None
    grn_count: int = 0


class GRNLookupService:
    """Look up and aggregate GRN data for a Purchase Order."""

    def lookup(self, purchase_order: PurchaseOrder) -> GRNSummary:
        grns = list(
            GoodsReceiptNote.objects.filter(purchase_order=purchase_order)
            .prefetch_related("line_items")
        )

        if not grns:
            logger.info("No GRNs found for PO %s", purchase_order.po_number)
            return GRNSummary(grn_available=False)

        # Aggregate received quantities per PO line item
        received_map: Dict[int, Decimal] = {}
        for grn in grns:
            for grn_line in grn.line_items.all():
                if grn_line.po_line_id:
                    received_map.setdefault(grn_line.po_line_id, Decimal("0"))
                    received_map[grn_line.po_line_id] += (
                        grn_line.quantity_accepted
                        if grn_line.quantity_accepted is not None
                        else grn_line.quantity_received or Decimal("0")
                    )

        # Determine if fully received
        po_lines = list(
            PurchaseOrderLineItem.objects.filter(purchase_order=purchase_order)
        )
        fully = True
        for pol in po_lines:
            received = received_map.get(pol.pk, Decimal("0"))
            if received < pol.quantity:
                fully = False
                break

        # Track latest receipt date across all GRNs
        receipt_dates = [g.receipt_date for g in grns if g.receipt_date]
        latest_date = max(receipt_dates) if receipt_dates else None

        summary = GRNSummary(
            grn_available=True,
            grns=grns,
            total_received_by_po_line=received_map,
            fully_received=fully,
            latest_receipt_date=latest_date,
            grn_count=len(grns),
        )
        logger.info(
            "GRN lookup for PO %s: %d GRNs, fully_received=%s",
            purchase_order.po_number, len(grns), fully,
        )
        return summary
