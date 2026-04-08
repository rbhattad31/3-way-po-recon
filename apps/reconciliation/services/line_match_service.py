"""Line-level matching service — matches invoice lines to PO lines."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from thefuzz import fuzz

from apps.core.constants import FUZZY_MATCH_THRESHOLD, MAX_LINE_MATCH_CANDIDATES
from apps.core.utils import normalize_string
from apps.documents.models import (
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.reconciliation.services.tolerance_engine import FieldComparison, ToleranceEngine

if TYPE_CHECKING:
    from apps.reconciliation.services.po_balance_service import POBalance

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


@dataclass
class LineMatchPair:
    """A matched (or unmatched) pair of invoice line ↔ PO line."""

    invoice_line: InvoiceLineItem
    po_line: Optional[PurchaseOrderLineItem] = None
    qty_comparison: Optional[FieldComparison] = None
    price_comparison: Optional[FieldComparison] = None
    amount_comparison: Optional[FieldComparison] = None
    tax_invoice: Optional[Decimal] = None
    tax_po: Optional[Decimal] = None
    tax_difference: Optional[Decimal] = None
    tax_rate_match: Optional[bool] = None  # line-level tax rate agreement
    tax_rate_details: Optional[Dict] = None  # invoice vs PO rate breakdown
    description_similarity: float = 0.0
    matched: bool = False


@dataclass
class LineMatchResult:
    """Aggregated result of line-level matching."""

    pairs: List[LineMatchPair] = field(default_factory=list)
    unmatched_invoice_lines: List[InvoiceLineItem] = field(default_factory=list)
    unmatched_po_lines: List[PurchaseOrderLineItem] = field(default_factory=list)
    all_lines_matched: bool = False
    all_within_tolerance: bool = False


class LineMatchService:
    """Match invoice line items to PO line items using deterministic rules.

    Matching strategy:
      1. Exact line-number match (if both sides are numbered consistently).
      2. Description similarity (fuzzy match) + quantity/price comparison.
      3. Best-candidate selection based on composite score.
    """

    def __init__(self, tolerance_engine: ToleranceEngine):
        self.engine = tolerance_engine

    def match(self, invoice: Invoice, po: PurchaseOrder, po_balance: Optional["POBalance"] = None) -> LineMatchResult:
        inv_lines = list(
            InvoiceLineItem.objects.filter(invoice=invoice).order_by("line_number")
        )
        po_lines = list(
            PurchaseOrderLineItem.objects.filter(purchase_order=po).order_by("line_number")
        )

        if not inv_lines:
            return LineMatchResult(
                unmatched_po_lines=po_lines,
                all_lines_matched=False,
                all_within_tolerance=False,
            )

        if not po_lines:
            return LineMatchResult(
                unmatched_invoice_lines=inv_lines,
                all_lines_matched=False,
                all_within_tolerance=False,
            )

        # Build candidate scores: (inv_line, po_line) -> score
        used_po_lines: set = set()
        pairs: List[LineMatchPair] = []

        for inv_line in inv_lines:
            best_pair = self._find_best_po_match(inv_line, po_lines, used_po_lines, po_balance)
            if best_pair and best_pair.matched:
                used_po_lines.add(best_pair.po_line.pk)
            pairs.append(best_pair or LineMatchPair(invoice_line=inv_line))

        unmatched_inv = [p.invoice_line for p in pairs if not p.matched]
        unmatched_po = [pl for pl in po_lines if pl.pk not in used_po_lines]

        # For partial invoices, unmatched PO lines are expected (remaining
        # lines will be invoiced later), so don't penalise them.
        is_partial = po_balance.is_partial if po_balance else False
        if is_partial:
            all_matched = len(unmatched_inv) == 0
        else:
            all_matched = len(unmatched_inv) == 0 and len(unmatched_po) == 0
        all_tol = all_matched and all(
            (p.qty_comparison and p.qty_comparison.within_tolerance is True)
            and (p.price_comparison and p.price_comparison.within_tolerance is True)
            and (p.amount_comparison and p.amount_comparison.within_tolerance is True)
            for p in pairs
            if p.matched
        )

        logger.info(
            "Line match for invoice %s vs PO %s: %d pairs, %d unmatched_inv, %d unmatched_po",
            invoice.pk, po.po_number, len(pairs), len(unmatched_inv), len(unmatched_po),
        )

        return LineMatchResult(
            pairs=pairs,
            unmatched_invoice_lines=unmatched_inv,
            unmatched_po_lines=unmatched_po,
            all_lines_matched=all_matched,
            all_within_tolerance=all_tol,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _find_best_po_match(
        self,
        inv_line: InvoiceLineItem,
        po_lines: List[PurchaseOrderLineItem],
        used: set,
        po_balance: Optional["POBalance"] = None,
    ) -> Optional[LineMatchPair]:
        """Score every candidate PO line and return the best match."""
        candidates: List[Tuple[float, LineMatchPair]] = []

        for po_line in po_lines:
            if po_line.pk in used:
                continue
            score, pair = self._score_pair(inv_line, po_line, po_balance)
            if score > 0:
                candidates.append((score, pair))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_pair = candidates[0]

        # Require minimum quality to consider it "matched"
        if best_score >= 0.3:
            best_pair.matched = True

        return best_pair

    def _score_pair(
        self, inv_line: InvoiceLineItem, po_line: PurchaseOrderLineItem,
        po_balance: Optional["POBalance"] = None,
    ) -> Tuple[float, LineMatchPair]:
        """Compute a composite matching score (0-1) for an inv/po line pair."""

        # Description similarity
        inv_desc = normalize_string(inv_line.description or inv_line.raw_description)
        po_desc = normalize_string(po_line.description)
        desc_sim = fuzz.token_sort_ratio(inv_desc, po_desc) if inv_desc and po_desc else 0.0

        # When a PO has prior invoices, compare against remaining qty/amount
        compare_qty = po_line.quantity
        compare_amount = po_line.line_amount
        if po_balance and po_balance.is_partial:
            if po_balance.prior_invoice_count > 0:
                # Subsequent partial: compare against remaining balance
                line_bal = po_balance.line_balances.get(po_line.pk)
                if line_bal:
                    compare_qty = line_bal.remaining_qty
                    compare_amount = line_bal.remaining_amount
            else:
                # First partial: invoice is less than PO by design.
                # Compare against the invoice's own values so the match
                # passes; the partial context is surfaced separately.
                compare_qty = inv_line.quantity or ZERO
                compare_amount = inv_line.line_amount or ZERO

        # Numeric comparisons
        compare_price = po_line.unit_price
        if po_balance and po_balance.is_first_partial:
            # First partial: unit price may be a fraction of the PO line
            # price (milestone billing). Accept the invoice price as-is.
            compare_price = inv_line.unit_price
        qty_cmp = self.engine.compare_quantity(inv_line.quantity, compare_qty)
        price_cmp = self.engine.compare_price(inv_line.unit_price, compare_price)
        amount_cmp = self.engine.compare_amount(inv_line.line_amount, compare_amount)

        # Tax (simple diff, not tolerance-based)
        tax_inv = inv_line.tax_amount
        tax_po = po_line.tax_amount
        if po_balance and po_balance.is_first_partial:
            # First partial: tax is proportional to billed amount, not full PO tax
            tax_po = tax_inv
        tax_diff = (tax_inv - tax_po) if tax_inv is not None and tax_po is not None else None

        # Composite score
        score = 0.0

        # Line number bonus (strong hint when consistent)
        if inv_line.line_number == po_line.line_number:
            score += 0.20

        # Description bonus
        if desc_sim >= FUZZY_MATCH_THRESHOLD:
            score += 0.30
        elif desc_sim >= 50:
            score += 0.15

        # Quantity match
        if qty_cmp.within_tolerance is True:
            score += 0.20
        elif qty_cmp.within_tolerance is False:
            score += 0.05

        # Price match
        if price_cmp.within_tolerance is True:
            score += 0.15
        elif price_cmp.within_tolerance is False:
            score += 0.03

        # Amount match
        if amount_cmp.within_tolerance is True:
            score += 0.15
        elif amount_cmp.within_tolerance is False:
            score += 0.03

        # Tax rate comparison
        tax_rate_match, tax_rate_details = _compare_line_tax_rate(inv_line, po_line)

        pair = LineMatchPair(
            invoice_line=inv_line,
            po_line=po_line,
            qty_comparison=qty_cmp,
            price_comparison=price_cmp,
            amount_comparison=amount_cmp,
            tax_invoice=tax_inv,
            tax_po=tax_po,
            tax_difference=tax_diff,
            tax_rate_match=tax_rate_match,
            tax_rate_details=tax_rate_details,
            description_similarity=desc_sim,
        )
        return score, pair


def _compare_line_tax_rate(
    inv_line: InvoiceLineItem, po_line: PurchaseOrderLineItem,
) -> Tuple[Optional[bool], Optional[Dict[str, Any]]]:
    """Compare the effective tax rate on an invoice line vs a PO line.

    PO lines may carry component-level rates (cgst_rate, sgst_rate,
    igst_rate, vat_rate).  We derive the effective PO rate from those
    components and compare against the invoice line's ``tax_percentage``.

    Returns (match_bool_or_None, details_dict_or_None).
    """
    inv_rate = inv_line.tax_percentage  # e.g. 18.0 for 18%
    if inv_rate is None:
        return None, None

    # Derive PO-side effective rate from component columns
    cgst = getattr(po_line, "cgst_rate", None) or Decimal("0")
    sgst = getattr(po_line, "sgst_rate", None) or Decimal("0")
    igst = getattr(po_line, "igst_rate", None) or Decimal("0")
    vat = getattr(po_line, "vat_rate", None) or Decimal("0")
    cess = getattr(po_line, "cess_rate", None) or Decimal("0")

    po_effective_rate = cgst + sgst + igst + vat + cess

    if po_effective_rate == Decimal("0"):
        # No component rates on PO line -- cannot compare
        return None, None

    # Allow 0.5% absolute tolerance on tax rate (e.g. rounding)
    diff = abs(Decimal(str(inv_rate)) - po_effective_rate)
    rate_match = diff <= Decimal("0.5")

    details = {
        "invoice_tax_rate": str(inv_rate),
        "po_effective_tax_rate": str(po_effective_rate),
        "po_cgst_rate": str(cgst),
        "po_sgst_rate": str(sgst),
        "po_igst_rate": str(igst),
        "po_vat_rate": str(vat),
        "po_cess_rate": str(cess),
        "rate_difference": str(diff),
    }
    return rate_match, details
