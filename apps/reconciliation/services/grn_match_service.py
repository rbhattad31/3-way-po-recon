"""GRN matching service — compares invoice/PO quantities against GRN receipts."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

import datetime

from apps.reconciliation.services.grn_lookup_service import GRNSummary
from apps.reconciliation.services.line_match_service import LineMatchPair

# Receipts arriving more than this many days after PO date are flagged.
_DELAYED_RECEIPT_THRESHOLD_DAYS = 30

logger = logging.getLogger(__name__)


@dataclass
class GRNLineComparison:
    """Comparison of a matched line pair against GRN received quantity."""

    invoice_line_id: Optional[int] = None
    po_line_id: Optional[int] = None
    qty_invoiced: Optional[Decimal] = None
    qty_ordered: Optional[Decimal] = None
    qty_received: Optional[Decimal] = None
    over_receipt: bool = False
    under_receipt: bool = False
    invoiced_exceeds_received: bool = False


@dataclass
class GRNMatchResult:
    """Aggregated GRN comparison result."""

    grn_available: bool = False
    fully_received: bool = False
    line_comparisons: List[GRNLineComparison] = field(default_factory=list)
    has_receipt_issues: bool = False
    latest_receipt_date: Optional['date'] = None
    grn_count: int = 0
    # ERP provenance (copied from GRNSummary by ThreeWayMatchService)
    erp_source_type: str = ""
    erp_provenance: dict = field(default_factory=dict)
    is_stale: bool = False


class GRNMatchService:
    """Compare invoice line quantities against GRN received quantities."""

    def match(
        self,
        line_pairs: List[LineMatchPair],
        grn_summary: GRNSummary,
        po_date: Optional[datetime.date] = None,
    ) -> GRNMatchResult:
        if not grn_summary.grn_available:
            return GRNMatchResult(grn_available=False)

        comparisons: List[GRNLineComparison] = []
        has_issues = False

        for pair in line_pairs:
            if not pair.matched or not pair.po_line:
                continue

            po_line_id = pair.po_line.pk
            qty_received = grn_summary.total_received_by_po_line.get(po_line_id, Decimal("0"))
            qty_ordered = pair.po_line.quantity
            qty_invoiced = pair.invoice_line.quantity

            cmp = GRNLineComparison(
                invoice_line_id=pair.invoice_line.pk,
                po_line_id=po_line_id,
                qty_invoiced=qty_invoiced,
                qty_ordered=qty_ordered,
                qty_received=qty_received,
            )

            # Check over-receipt (received > ordered)
            if qty_received > qty_ordered:
                cmp.over_receipt = True
                has_issues = True

            # Check under-receipt (received < ordered)
            if qty_received < qty_ordered:
                cmp.under_receipt = True

            # Check if invoice exceeds what was actually received
            if qty_invoiced is not None and qty_invoiced > qty_received:
                cmp.invoiced_exceeds_received = True
                has_issues = True

            comparisons.append(cmp)

        # Check delayed receipt: receipt date significantly after PO date
        if po_date and grn_summary.latest_receipt_date:
            days_since_po = (grn_summary.latest_receipt_date - po_date).days
            if days_since_po > _DELAYED_RECEIPT_THRESHOLD_DAYS:
                has_issues = True
                logger.info(
                    "GRN delayed receipt detected: %d days after PO date (threshold=%d)",
                    days_since_po, _DELAYED_RECEIPT_THRESHOLD_DAYS,
                )

        result = GRNMatchResult(
            grn_available=True,
            fully_received=grn_summary.fully_received,
            line_comparisons=comparisons,
            has_receipt_issues=has_issues,
            latest_receipt_date=grn_summary.latest_receipt_date,
            grn_count=grn_summary.grn_count,
        )

        logger.info(
            "GRN match: %d line comparisons, fully_received=%s, has_issues=%s",
            len(comparisons), grn_summary.fully_received, has_issues,
        )
        return result
