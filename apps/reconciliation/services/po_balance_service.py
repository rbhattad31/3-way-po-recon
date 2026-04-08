"""PO balance service -- computes cumulative invoiced amounts per PO.

Supports milestone / partial invoicing by calculating how much of a PO
has already been invoiced so the matching engine can compare the current
invoice against the *remaining* PO balance rather than the full PO total.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Optional

from django.db.models import Sum

from apps.documents.models import Invoice, PurchaseOrder

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


@dataclass
class POLineBalance:
    """Remaining balance for a single PO line item."""
    po_line_id: int
    ordered_qty: Decimal
    ordered_amount: Decimal
    prior_invoiced_qty: Decimal = ZERO
    prior_invoiced_amount: Decimal = ZERO

    @property
    def remaining_qty(self) -> Decimal:
        return max(self.ordered_qty - self.prior_invoiced_qty, ZERO)

    @property
    def remaining_amount(self) -> Decimal:
        return max(self.ordered_amount - self.prior_invoiced_amount, ZERO)


@dataclass
class POBalance:
    """Aggregate PO balance: total ordered vs total already invoiced."""
    po_id: int
    po_total: Decimal
    po_tax: Optional[Decimal]
    prior_invoiced_total: Decimal = ZERO
    prior_invoiced_tax: Decimal = ZERO
    prior_invoice_count: int = 0
    line_balances: Dict[int, POLineBalance] = field(default_factory=dict)
    # True when the current invoice total is well below the PO total,
    # indicating a partial/milestone invoice even if no prior invoices exist.
    is_first_partial: bool = False
    # The current invoice total used for first-partial detection.
    current_invoice_total: Decimal = ZERO

    @property
    def remaining_total(self) -> Decimal:
        return max(self.po_total - self.prior_invoiced_total, ZERO)

    @property
    def remaining_tax(self) -> Optional[Decimal]:
        if self.po_tax is None:
            return None
        return max(self.po_tax - self.prior_invoiced_tax, ZERO)

    @property
    def is_partial(self) -> bool:
        """True if this is a partial invoice -- either prior invoices exist
        or the current invoice total is well below the PO total."""
        return self.prior_invoice_count > 0 or self.is_first_partial

    @property
    def invoice_covers_pct(self) -> Decimal:
        """Percentage of PO total covered by the current invoice."""
        if not self.po_total:
            return Decimal("100.00")
        return (self.current_invoice_total / self.po_total * 100).quantize(Decimal("0.01"))


class POBalanceService:
    """Compute how much of a PO remains un-invoiced.

    Queries all *prior* invoices linked to the same PO (excluding the
    invoice currently being reconciled) and sums their totals and line
    amounts.  GRN receipts are NOT considered here -- that is handled
    separately by the GRN match service.
    """

    @staticmethod
    def compute(
        po: PurchaseOrder,
        exclude_invoice: Invoice,
        partial_threshold_pct: float = 50.0,
    ) -> POBalance:
        """Return the remaining balance on *po* excluding *exclude_invoice*.

        Only invoices with status in a set of "counted" statuses contribute.
        Draft / rejected / duplicate invoices are ignored.

        When *partial_threshold_pct* is set and the current invoice total
        is less than that percentage of the PO total, the invoice is
        flagged as a likely first partial even without prior invoices.
        """
        from apps.core.enums import InvoiceStatus
        from apps.documents.models import InvoiceLineItem, PurchaseOrderLineItem

        # Statuses that represent real invoiced value
        _COUNTED_STATUSES = {
            InvoiceStatus.EXTRACTED,
            InvoiceStatus.VALIDATED,
            InvoiceStatus.PENDING_APPROVAL,
            InvoiceStatus.READY_FOR_RECON,
            InvoiceStatus.RECONCILED,
        }

        # Prior invoices against the same PO (by normalized PO number)
        norm_po = po.normalized_po_number or po.po_number
        prior_qs = (
            Invoice.objects
            .filter(
                normalized_po_number=norm_po,
                is_duplicate=False,
            )
            .exclude(pk=exclude_invoice.pk)
            .filter(status__in=[s.value for s in _COUNTED_STATUSES])
        )

        agg = prior_qs.aggregate(
            total=Sum("total_amount"),
            tax=Sum("tax_amount"),
        )
        prior_total = agg["total"] or ZERO
        prior_tax = agg["tax"] or ZERO
        prior_count = prior_qs.count()

        balance = POBalance(
            po_id=po.pk,
            po_total=po.total_amount or ZERO,
            po_tax=po.tax_amount,
            prior_invoiced_total=prior_total,
            prior_invoiced_tax=prior_tax,
            prior_invoice_count=prior_count,
        )

        # Per-line balances
        po_lines = PurchaseOrderLineItem.objects.filter(purchase_order=po)
        prior_invoice_ids = list(prior_qs.values_list("pk", flat=True))

        for po_line in po_lines:
            line_bal = POLineBalance(
                po_line_id=po_line.pk,
                ordered_qty=po_line.quantity or ZERO,
                ordered_amount=po_line.line_amount or ZERO,
            )

            if prior_invoice_ids:
                # Sum quantities and amounts from prior invoice lines
                # that matched this PO line by line_number
                line_agg = (
                    InvoiceLineItem.objects
                    .filter(
                        invoice_id__in=prior_invoice_ids,
                        line_number=po_line.line_number,
                    )
                    .aggregate(
                        qty=Sum("quantity"),
                        amount=Sum("line_amount"),
                    )
                )
                line_bal.prior_invoiced_qty = line_agg["qty"] or ZERO
                line_bal.prior_invoiced_amount = line_agg["amount"] or ZERO

            balance.line_balances[po_line.pk] = line_bal

        # Detect first partial invoice: current invoice total is well below
        # the PO total (e.g. milestone billing, partial deliveries).
        inv_total = exclude_invoice.total_amount or ZERO
        balance.current_invoice_total = inv_total
        if (
            prior_count == 0
            and balance.po_total > ZERO
            and inv_total > ZERO
            and inv_total < balance.po_total
        ):
            covers_pct = float(inv_total / balance.po_total * 100)
            if covers_pct < partial_threshold_pct:
                balance.is_first_partial = True
                logger.info(
                    "PO %s: first partial invoice detected -- invoice %s covers %.1f%% "
                    "of PO total (threshold=%.1f%%)",
                    po.po_number, exclude_invoice.pk, covers_pct, partial_threshold_pct,
                )

        if prior_count > 0:
            logger.info(
                "PO %s balance: total=%s prior_invoiced=%s remaining=%s "
                "(prior_invoices=%d, excluding inv=%s)",
                po.po_number, balance.po_total, balance.prior_invoiced_total,
                balance.remaining_total, prior_count, exclude_invoice.pk,
            )

        return balance
