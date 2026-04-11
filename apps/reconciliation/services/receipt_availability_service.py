"""Receipt availability service -- computes available receipt quantity per PO line.

For partial-invoice 3-way matching, the key question is not "how much was
received in total?" but "how much receipt is still *available* (unconsumed)
for this invoice?"

    available_qty = cumulative_received_qty - previously_consumed_qty

Where:
- cumulative_received_qty: total GRN-received for the PO line (from GRNSummary)
- previously_consumed_qty: sum of qty_invoice on prior ReconciliationResultLine
  records that matched the same po_line and belong to a counted reconciliation
  result (MATCHED / PARTIAL_MATCH / REQUIRES_REVIEW).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from django.db.models import Sum

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# ReconciliationResult match statuses whose line consumption counts.
# MATCHED and PARTIAL_MATCH mean the invoice was accepted (at least partially).
# REQUIRES_REVIEW means a human hasn't rejected it yet, so the receipt is
# provisionally consumed to prevent double-billing while review is pending.
_COUNTED_MATCH_STATUSES = {"MATCHED", "PARTIAL_MATCH", "REQUIRES_REVIEW"}


@dataclass
class LineReceiptAvailability:
    """Receipt availability for a single PO line."""

    po_line_id: int
    cumulative_received_qty: Decimal = ZERO
    previously_consumed_qty: Decimal = ZERO
    contributing_grn_line_ids: List[int] = field(default_factory=list)

    @property
    def available_qty(self) -> Decimal:
        return max(self.cumulative_received_qty - self.previously_consumed_qty, ZERO)


@dataclass
class ReceiptAvailability:
    """Receipt availability across all PO lines."""

    by_po_line: Dict[int, LineReceiptAvailability] = field(default_factory=dict)

    def get(self, po_line_id: int) -> Optional[LineReceiptAvailability]:
        return self.by_po_line.get(po_line_id)


class ReceiptAvailabilityService:
    """Compute per-PO-line available receipt quantity.

    Combines:
    - GRN-received quantities (from ``GRNSummary.total_received_by_po_line``)
    - Prior consumption (from ``ReconciliationResultLine`` rows that paired
      against the same ``po_line`` in reconciliations with counted statuses)
    """

    @staticmethod
    def compute(
        po_id: int,
        total_received_by_po_line: Dict[int, Decimal],
        exclude_result_id: Optional[int] = None,
        grn_line_ids_by_po_line: Optional[Dict[int, List[int]]] = None,
    ) -> ReceiptAvailability:
        """Return receipt availability for every PO line that has GRN data.

        Args:
            po_id: PK of the PurchaseOrder (used to scope prior result lines).
            total_received_by_po_line: {po_line_pk: cumulative_received_qty}
                as computed by ``GRNLookupService``.
            exclude_result_id: PK of the current ``ReconciliationResult`` to
                exclude from the prior-consumption query (prevents self-count).
            grn_line_ids_by_po_line: optional map of {po_line_pk: [grn_line_pk, ...]}
                for provenance tracking.

        Returns:
            ReceiptAvailability with an entry per PO line in the received map.
        """
        from apps.reconciliation.models import ReconciliationResultLine

        po_line_ids = list(total_received_by_po_line.keys())
        if not po_line_ids:
            return ReceiptAvailability()

        # Query prior consumption: sum of qty_invoice on result lines that
        # are paired to the same po_line, in results with counted statuses,
        # belonging to the same PO.
        prior_qs = (
            ReconciliationResultLine.objects
            .filter(
                po_line_id__in=po_line_ids,
                result__purchase_order_id=po_id,
                result__match_status__in=_COUNTED_MATCH_STATUSES,
            )
        )
        if exclude_result_id is not None:
            prior_qs = prior_qs.exclude(result_id=exclude_result_id)

        consumed_agg = (
            prior_qs
            .values("po_line_id")
            .annotate(consumed=Sum("qty_invoice"))
        )

        consumed_map: Dict[int, Decimal] = {
            row["po_line_id"]: row["consumed"] or ZERO
            for row in consumed_agg
        }

        result = ReceiptAvailability()
        for po_line_id, received in total_received_by_po_line.items():
            consumed = consumed_map.get(po_line_id, ZERO)
            grn_ids = (
                (grn_line_ids_by_po_line or {}).get(po_line_id, [])
            )
            avail = LineReceiptAvailability(
                po_line_id=po_line_id,
                cumulative_received_qty=received,
                previously_consumed_qty=consumed,
                contributing_grn_line_ids=grn_ids,
            )
            result.by_po_line[po_line_id] = avail

            if consumed > ZERO:
                logger.debug(
                    "PO line %d: received=%s consumed=%s available=%s",
                    po_line_id, received, consumed, avail.available_qty,
                )

        return result
