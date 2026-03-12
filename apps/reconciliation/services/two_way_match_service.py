"""Two-way match service — Invoice vs PO (no GRN verification).

Performs header-level and line-level matching only. GRN lookup and
matching are intentionally skipped. The returned result uses the same
dataclass shapes so downstream consumers (ClassificationService,
ExceptionBuilderService, ResultService) work without modification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.services.grn_match_service import GRNMatchResult
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
class TwoWayMatchOutput:
    """Unified output of a 2-way reconciliation pass."""

    po_result: POLookupResult
    header_result: Optional[HeaderMatchResult] = None
    line_result: Optional[LineMatchResult] = None
    grn_result: Optional[GRNMatchResult] = field(default=None)

    @property
    def grn_required(self) -> bool:
        return False

    @property
    def grn_checked(self) -> bool:
        return False


class TwoWayMatchService:
    """Execute a 2-way (Invoice vs PO) match pipeline.

    Steps:
      1. Header match (vendor, currency, total)
      2. Line-level match (qty, price, amount per line)

    GRN data is never consulted — the returned ``grn_result`` is always
    ``None`` so that the classifier treats receipt checks as pass-through.
    """

    def __init__(self, tolerance_engine: ToleranceEngine):
        self.header_match = HeaderMatchService(tolerance_engine)
        self.line_match = LineMatchService(tolerance_engine)

    def match(
        self,
        invoice: Invoice,
        po_result: POLookupResult,
    ) -> TwoWayMatchOutput:
        """Run 2-way matching for a single invoice.

        Args:
            invoice: The invoice to reconcile.
            po_result: The PO lookup result (must have ``found=True``).

        Returns:
            TwoWayMatchOutput with header and line results; grn_result is None.
        """
        if not po_result.found:
            logger.warning(
                "TwoWayMatchService called with no PO for invoice %s", invoice.pk,
            )
            return TwoWayMatchOutput(po_result=po_result)

        po: PurchaseOrder = po_result.purchase_order

        header_result = self.header_match.match(invoice, po)
        line_result = self.line_match.match(invoice, po)

        logger.info(
            "2-way match for invoice %s vs PO %s: header_ok=%s lines_matched=%s",
            invoice.pk,
            po.po_number,
            header_result.all_ok,
            line_result.all_lines_matched if line_result else None,
        )

        return TwoWayMatchOutput(
            po_result=po_result,
            header_result=header_result,
            line_result=line_result,
            grn_result=None,
        )
