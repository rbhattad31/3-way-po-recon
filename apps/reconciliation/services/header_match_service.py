"""Header-level matching service — compares invoice header to PO header."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from apps.core.utils import normalize_string, within_tolerance
from apps.documents.models import Invoice, PurchaseOrder
from apps.reconciliation.services.tolerance_engine import FieldComparison, ToleranceEngine

logger = logging.getLogger(__name__)


@dataclass
class HeaderMatchResult:
    """Outcome of header-level comparison."""

    vendor_match: Optional[bool] = None
    currency_match: Optional[bool] = None
    po_total_match: Optional[bool] = None
    total_comparison: Optional[FieldComparison] = None
    all_ok: bool = False


class HeaderMatchService:
    """Deterministic header-level comparisons between Invoice and PO."""

    def __init__(self, tolerance_engine: ToleranceEngine):
        self.engine = tolerance_engine

    def match(self, invoice: Invoice, po: PurchaseOrder) -> HeaderMatchResult:
        result = HeaderMatchResult()

        # 1. Vendor match
        result.vendor_match = self._check_vendor(invoice, po)

        # 2. Currency match
        inv_currency = (invoice.currency or "").strip().upper()
        po_currency = (po.currency or "").strip().upper()
        result.currency_match = inv_currency == po_currency if inv_currency and po_currency else None

        # 3. Total amount comparison
        result.total_comparison = self.engine.compare_amount(
            invoice.total_amount, po.total_amount
        )
        result.po_total_match = result.total_comparison.within_tolerance

        # Overall header pass
        result.all_ok = all([
            result.vendor_match is True,
            result.currency_match is True,
            result.po_total_match is True,
        ])

        logger.info(
            "Header match for invoice %s vs PO %s: vendor=%s currency=%s total=%s all_ok=%s",
            invoice.pk, po.po_number,
            result.vendor_match, result.currency_match,
            result.po_total_match, result.all_ok,
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _check_vendor(invoice: Invoice, po: PurchaseOrder) -> Optional[bool]:
        """Compare vendor at multiple levels: FK, normalised name."""
        # Direct FK match
        if invoice.vendor_id and po.vendor_id:
            return invoice.vendor_id == po.vendor_id

        # Normalised name fallback
        inv_name = normalize_string(invoice.raw_vendor_name) if invoice.raw_vendor_name else ""
        po_name = ""
        if po.vendor:
            po_name = po.vendor.normalized_name or normalize_string(po.vendor.name)
        if inv_name and po_name:
            return inv_name == po_name

        return None  # Inconclusive
