"""Three-way match service — Invoice vs PO vs GRN.

Performs header match, line match, GRN lookup, and GRN match.
This encapsulates the existing 3-way pipeline into a clean service
boundary with the same output contract as TwoWayMatchService so the
ExecutionRouter can treat both uniformly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.services.grn_lookup_service import GRNLookupService, GRNSummary
from apps.reconciliation.services.grn_match_service import (
    GRNMatchResult,
    GRNMatchService,
)
from apps.reconciliation.services.header_match_service import (
    HeaderMatchResult,
    HeaderMatchService,
)
from apps.reconciliation.services.line_match_service import (
    LineMatchResult,
    LineMatchService,
)
from apps.reconciliation.services.po_lookup_service import POLookupResult
from apps.reconciliation.services.tolerance_engine import ToleranceEngine

logger = logging.getLogger(__name__)


@dataclass
class ThreeWayMatchOutput:
    """Unified output of a 3-way reconciliation pass."""

    po_result: POLookupResult
    header_result: Optional[HeaderMatchResult] = None
    line_result: Optional[LineMatchResult] = None
    grn_result: Optional[GRNMatchResult] = field(default=None)

    @property
    def grn_required(self) -> bool:
        return True

    @property
    def grn_checked(self) -> bool:
        return self.grn_result is not None


class ThreeWayMatchService:
    """Execute a 3-way (Invoice vs PO vs GRN) match pipeline.

    Steps:
      1. Header match (vendor, currency, total)
      2. Line-level match (qty, price, amount per line)
      3. GRN lookup (aggregate received quantities)
      4. GRN match (compare invoiced vs received quantities)
    """

    def __init__(self, tolerance_engine: ToleranceEngine):
        self.header_match = HeaderMatchService(tolerance_engine)
        self.line_match = LineMatchService(tolerance_engine)
        self.grn_lookup = GRNLookupService()
        self.grn_match = GRNMatchService()

    def match(
        self,
        invoice: Invoice,
        po_result: POLookupResult,
    ) -> ThreeWayMatchOutput:
        """Run 3-way matching for a single invoice.

        Args:
            invoice: The invoice to reconcile.
            po_result: The PO lookup result (must have ``found=True``).

        Returns:
            ThreeWayMatchOutput with header, line, and GRN results.
        """
        if not po_result.found:
            logger.warning(
                "ThreeWayMatchService called with no PO for invoice %s", invoice.pk,
            )
            return ThreeWayMatchOutput(po_result=po_result)

        po: PurchaseOrder = po_result.purchase_order

        # 1. Header match
        header_result = self.header_match.match(invoice, po)

        # 2. Line match
        line_result = self.line_match.match(invoice, po)

        # 3. GRN lookup
        grn_summary: GRNSummary = self.grn_lookup.lookup(po)

        # 4. GRN match (only if GRNs exist and lines were matched)
        grn_result: Optional[GRNMatchResult] = None
        if grn_summary.grn_available and line_result:
            grn_result = self.grn_match.match(line_result.pairs, grn_summary, po_date=po.po_date)
        elif not grn_summary.grn_available:
            grn_result = GRNMatchResult(grn_available=False)

        logger.info(
            "3-way match for invoice %s vs PO %s: header_ok=%s lines_matched=%s "
            "grn_available=%s grn_issues=%s",
            invoice.pk,
            po.po_number,
            header_result.all_ok,
            line_result.all_lines_matched if line_result else None,
            grn_summary.grn_available,
            grn_result.has_receipt_issues if grn_result else None,
        )

        return ThreeWayMatchOutput(
            po_result=po_result,
            header_result=header_result,
            line_result=line_result,
            grn_result=grn_result,
        )
