"""Tolerance engine — encapsulates threshold comparisons for qty, price, and amount."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from apps.core.utils import pct_difference, within_tolerance
from apps.reconciliation.models import ReconciliationConfig

logger = logging.getLogger(__name__)


@dataclass
class ToleranceThresholds:
    """Active tolerance percentages for a reconciliation run."""

    quantity_pct: float = 2.0
    price_pct: float = 1.0
    amount_pct: float = 1.0


@dataclass
class FieldComparison:
    """Result of comparing two decimal values with a tolerance."""

    invoice_value: Optional[Decimal] = None
    po_value: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    difference_pct: Optional[Decimal] = None
    within_tolerance: Optional[bool] = None


class ToleranceEngine:
    """Compare numeric values within configurable tolerance thresholds."""

    def __init__(self, config: Optional[ReconciliationConfig] = None):
        if config:
            self.thresholds = ToleranceThresholds(
                quantity_pct=config.quantity_tolerance_pct,
                price_pct=config.price_tolerance_pct,
                amount_pct=config.amount_tolerance_pct,
            )
        else:
            self.thresholds = ToleranceThresholds()

    # ------------------------------------------------------------------
    # Comparison helpers
    # ------------------------------------------------------------------
    def compare_quantity(
        self, inv_qty: Optional[Decimal], po_qty: Optional[Decimal]
    ) -> FieldComparison:
        return self._compare(inv_qty, po_qty, self.thresholds.quantity_pct)

    def compare_price(
        self, inv_price: Optional[Decimal], po_price: Optional[Decimal]
    ) -> FieldComparison:
        return self._compare(inv_price, po_price, self.thresholds.price_pct)

    def compare_amount(
        self, inv_amount: Optional[Decimal], po_amount: Optional[Decimal]
    ) -> FieldComparison:
        return self._compare(inv_amount, po_amount, self.thresholds.amount_pct)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    @staticmethod
    def _compare(
        a: Optional[Decimal], b: Optional[Decimal], tolerance_pct: float
    ) -> FieldComparison:
        if a is None or b is None:
            return FieldComparison(
                invoice_value=a,
                po_value=b,
                within_tolerance=None,
            )

        diff = a - b
        diff_pct = pct_difference(a, b)
        ok = within_tolerance(a, b, tolerance_pct)

        return FieldComparison(
            invoice_value=a,
            po_value=b,
            difference=diff,
            difference_pct=diff_pct,
            within_tolerance=ok,
        )
