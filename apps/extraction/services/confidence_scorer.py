"""Deterministic confidence scorer for the legacy extraction pipeline.

Replaces the LLM's self-reported confidence with a score computed from
what was actually extracted.  The score is a weighted sum of three
dimensions:

  1. **Field coverage** (50%) — were critical / important / optional
     header fields extracted?
  2. **Line-item quality** (30%) — how complete are the extracted line
     items?
  3. **Cross-field consistency** (20%) — do the numbers add up?

The result is a float in [0.0, 1.0] that is fully deterministic and
auditable — no LLM self-assessment involved.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

logger = logging.getLogger(__name__)


# ── Weights per dimension ────────────────────────────────────────────────
_W_FIELD_COVERAGE = 0.50
_W_LINE_QUALITY = 0.30
_W_CONSISTENCY = 0.20

# ── Field importance tiers ───────────────────────────────────────────────
# Each field maps to a weight within the coverage dimension.
# Weights are normalised at runtime so they sum to 1.0.
_HEADER_FIELDS = {
    # critical — missing any of these is a big deal
    "total_amount":    5.0,
    "invoice_number":  5.0,
    "vendor_name":     4.0,
    # important
    "invoice_date":    3.0,
    "currency":        2.0,
    # useful but not essential
    "po_number":       2.0,
    "subtotal":        1.5,
    "tax_amount":      1.5,
}

# Per-line-item field weights (normalised internally)
_LINE_ITEM_FIELDS = {
    "description":  3.0,
    "quantity":     3.0,
    "unit_price":   3.0,
    "line_amount":  2.0,
    "tax_amount":   1.0,
}


@dataclass
class ConfidenceBreakdown:
    """Transparent breakdown of the computed confidence."""
    overall: float = 0.0
    field_coverage: float = 0.0
    line_item_quality: float = 0.0
    consistency: float = 0.0
    penalties: List[str] = field(default_factory=list)
    llm_original: float = 0.0  # preserved for audit / comparison


class ExtractionConfidenceScorer:
    """Compute extraction confidence deterministically from extracted data."""

    @classmethod
    def score(cls, inv, llm_confidence: float = 0.0) -> ConfidenceBreakdown:
        """Score a NormalizedInvoice.

        Parameters
        ----------
        inv : NormalizedInvoice
            The normalised extraction output.
        llm_confidence : float
            The original LLM-reported confidence (preserved for audit
            only — does NOT influence the score).

        Returns
        -------
        ConfidenceBreakdown with overall in [0.0, 1.0].
        """
        breakdown = ConfidenceBreakdown(llm_original=llm_confidence)

        # ── 1. Field coverage ────────────────────────────────────────
        coverage = cls._field_coverage(inv, breakdown)

        # ── 2. Line-item quality ─────────────────────────────────────
        line_quality = cls._line_item_quality(inv, breakdown)

        # ── 3. Cross-field consistency ───────────────────────────────
        consistency = cls._consistency(inv, breakdown)

        overall = (
            _W_FIELD_COVERAGE * coverage
            + _W_LINE_QUALITY * line_quality
            + _W_CONSISTENCY * consistency
        )
        breakdown.field_coverage = round(coverage, 4)
        breakdown.line_item_quality = round(line_quality, 4)
        breakdown.consistency = round(consistency, 4)
        breakdown.overall = round(min(max(overall, 0.0), 1.0), 4)
        return breakdown

    # ------------------------------------------------------------------
    # Dimension 1 — Header field coverage
    # ------------------------------------------------------------------
    @classmethod
    def _field_coverage(cls, inv, breakdown: ConfidenceBreakdown) -> float:
        total_weight = sum(_HEADER_FIELDS.values())
        earned = 0.0

        checks = {
            "total_amount":   inv.total_amount is not None,
            "invoice_number": bool(inv.normalized_invoice_number),
            "vendor_name":    bool(inv.vendor_name_normalized),
            "invoice_date":   inv.invoice_date is not None,
            "currency":       bool(inv.currency and inv.currency != "USD"),  # USD is default fallback
            "po_number":      bool(inv.normalized_po_number),
            "subtotal":       inv.subtotal is not None,
            "tax_amount":     inv.tax_amount is not None,
        }

        for field_name, present in checks.items():
            weight = _HEADER_FIELDS[field_name]
            if present:
                earned += weight
            else:
                breakdown.penalties.append(f"missing:{field_name}")

        # Currency gets partial credit even for USD (it was parsed)
        if inv.currency == "USD" and inv.raw_currency:
            earned += _HEADER_FIELDS["currency"] * 0.5

        return earned / total_weight if total_weight > 0 else 0.0

    # ------------------------------------------------------------------
    # Dimension 2 — Line-item quality
    # ------------------------------------------------------------------
    @classmethod
    def _line_item_quality(cls, inv, breakdown: ConfidenceBreakdown) -> float:
        if not inv.line_items:
            breakdown.penalties.append("no_line_items")
            return 0.0

        total_weight = sum(_LINE_ITEM_FIELDS.values())
        line_scores = []

        for li in inv.line_items:
            earned = 0.0
            checks = {
                "description":  bool(li.description),
                "quantity":     li.quantity is not None,
                "unit_price":   li.unit_price is not None,
                "line_amount":  li.line_amount is not None,
                "tax_amount":   li.tax_amount is not None,
            }
            for field_name, present in checks.items():
                if present:
                    earned += _LINE_ITEM_FIELDS[field_name]
            line_scores.append(earned / total_weight if total_weight > 0 else 0.0)

        avg = sum(line_scores) / len(line_scores)

        # Bonus: having any line items at all is good (partial credit
        # even when lines are sparse)
        return avg

    # ------------------------------------------------------------------
    # Dimension 3 — Cross-field consistency (do the numbers add up?)
    # ------------------------------------------------------------------
    @classmethod
    def _consistency(cls, inv, breakdown: ConfidenceBreakdown) -> float:
        checks_passed = 0
        checks_total = 0

        # Check 1: subtotal + tax ≈ total
        if inv.subtotal is not None and inv.tax_amount is not None and inv.total_amount is not None:
            checks_total += 1
            expected = inv.subtotal + inv.tax_amount
            if inv.total_amount > 0:
                diff_pct = abs(float(expected - inv.total_amount)) / float(inv.total_amount)
                if diff_pct < 0.02:  # within 2%
                    checks_passed += 1
                else:
                    breakdown.penalties.append(
                        f"total_mismatch:{float(expected):.2f}!={float(inv.total_amount):.2f}"
                    )

        # Check 2: sum of line amounts ≈ subtotal (or total if no subtotal)
        if inv.line_items:
            line_sum = sum(
                (li.line_amount or Decimal("0"))
                for li in inv.line_items
            )
            reference = inv.subtotal if inv.subtotal is not None else inv.total_amount
            if reference is not None and reference > 0 and line_sum > 0:
                checks_total += 1
                diff_pct = abs(float(line_sum - reference)) / float(reference)
                if diff_pct < 0.05:  # within 5%
                    checks_passed += 1
                else:
                    breakdown.penalties.append(
                        f"line_sum_mismatch:{float(line_sum):.2f}!={float(reference):.2f}"
                    )

        # Check 3: line qty × unit_price ≈ line_amount for each line
        for li in inv.line_items:
            if li.quantity is not None and li.unit_price is not None and li.line_amount is not None:
                checks_total += 1
                expected_amt = li.quantity * li.unit_price
                if li.line_amount > 0:
                    diff_pct = abs(float(expected_amt - li.line_amount)) / float(li.line_amount)
                    if diff_pct < 0.02:
                        checks_passed += 1
                    # Don't add per-line penalties to avoid noise

        if checks_total == 0:
            # No consistency checks possible -- neutral (not penalised)
            return 0.5

        raw_score = checks_passed / checks_total

        # Hard penalty: when subtotal + tax != total by a large margin
        # the raw_score may still be high (diluted by many passing per-line
        # checks).  Apply a cap so a hard total mismatch cannot hide.
        if inv.subtotal is not None and inv.tax_amount is not None and inv.total_amount is not None:
            if inv.total_amount > 0:
                expected = inv.subtotal + inv.tax_amount
                diff_pct = abs(float(expected - inv.total_amount)) / float(inv.total_amount)
                if diff_pct >= 0.05:  # >= 5% mismatch
                    cap = 0.30
                    if raw_score > cap:
                        breakdown.penalties.append(
                            f"hard_total_mismatch_cap:{diff_pct:.1%}"
                        )
                        raw_score = cap

        return raw_score
