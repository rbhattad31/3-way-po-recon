"""Line-level matching service -- deterministic multi-signal scorer with optional LLM fallback.

Matches each invoice line to the best PO line candidate using a weighted,
explainable scoring framework. LLM fallback is invoked only for ambiguous
or unresolved lines when a fallback service is provided.

Backward-compatible: still produces ``LineMatchPair`` / ``LineMatchResult``
dataclasses consumed by downstream services, while also exposing rich
``LineMatchDecision`` objects on the result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from apps.documents.models import (
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.reconciliation.services.line_match_helpers import (
    amount_proximity,
    category_compatibility,
    extract_meaningful_tokens,
    fuzzy_similarity,
    normalize_line_text,
    price_proximity,
    quantity_proximity,
    service_stock_compatibility,
    token_similarity,
    uom_compatibility,
)
from apps.reconciliation.services.line_match_types import (
    AMBIGUITY_CLOSE_MIN_SCORE,
    AMBIGUITY_CLOSE_RANGE,
    AMBIGUITY_GAP,
    BAND_NONE,
    LineCandidateScore,
    LineMatchDecision,
    METHOD_DETERMINISTIC,
    METHOD_EXACT,
    METHOD_LLM_FALLBACK,
    METHOD_NONE,
    MODERATE_MATCH_GAP,
    MODERATE_MATCH_SCORE,
    PENALTY_DESCRIPTION_CONTRADICTION,
    PENALTY_SERVICE_STOCK_CONTRADICTION,
    PENALTY_SEVERE_PRICE_CONTRADICTION,
    PENALTY_SEVERE_QTY_CONTRADICTION,
    STATUS_AMBIGUOUS,
    STATUS_MATCHED,
    STATUS_UNRESOLVED,
    STRONG_MATCH_GAP,
    STRONG_MATCH_SCORE,
    WEAK_THRESHOLD,
    confidence_band,
)
from apps.reconciliation.services.tolerance_engine import FieldComparison, ToleranceEngine

if TYPE_CHECKING:
    from apps.reconciliation.services.line_match_llm_fallback import LineMatchLLMFallbackService
    from apps.reconciliation.services.po_balance_service import POBalance

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


# ===================================================================
# Legacy dataclasses (backward compat)
# ===================================================================

@dataclass
class LineMatchPair:
    """A matched (or unmatched) pair of invoice line <-> PO line."""

    invoice_line: InvoiceLineItem
    po_line: Optional[PurchaseOrderLineItem] = None
    qty_comparison: Optional[FieldComparison] = None
    price_comparison: Optional[FieldComparison] = None
    amount_comparison: Optional[FieldComparison] = None
    tax_invoice: Optional[Decimal] = None
    tax_po: Optional[Decimal] = None
    tax_difference: Optional[Decimal] = None
    tax_rate_match: Optional[bool] = None
    tax_rate_details: Optional[Dict] = None
    description_similarity: float = 0.0
    matched: bool = False
    # v2: rich decision attached
    decision: Optional[LineMatchDecision] = None


@dataclass
class LineMatchResult:
    """Aggregated result of line-level matching."""

    pairs: List[LineMatchPair] = field(default_factory=list)
    unmatched_invoice_lines: List[InvoiceLineItem] = field(default_factory=list)
    unmatched_po_lines: List[PurchaseOrderLineItem] = field(default_factory=list)
    all_lines_matched: bool = False
    all_within_tolerance: bool = False
    # v2: full decision list
    decisions: List[LineMatchDecision] = field(default_factory=list)


# ===================================================================
# Service
# ===================================================================

class LineMatchService:
    """Match invoice line items to PO line items using deterministic scoring.

    Matching strategy (v2):
      1. Score every candidate PO line against each invoice line using 11
         weighted signals (item code, descriptions, qty, price, amount,
         UOM, category, service/stock, line number).
      2. Apply penalties for contradictions.
      3. Rank candidates and determine match confidence band.
      4. Detect ambiguity and avoid force-matching weak pairs.
      5. Optionally invoke LLM fallback for unresolved/ambiguous lines.
    """

    def __init__(
        self,
        tolerance_engine: ToleranceEngine,
        llm_fallback: Optional["LineMatchLLMFallbackService"] = None,
    ):
        self.engine = tolerance_engine
        self.llm_fallback = llm_fallback

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(
        self,
        invoice: Invoice,
        po: PurchaseOrder,
        po_balance: Optional["POBalance"] = None,
    ) -> LineMatchResult:
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

        used_po_lines: set = set()
        pairs: List[LineMatchPair] = []
        decisions: List[LineMatchDecision] = []

        for inv_line in inv_lines:
            decision = self._decide(inv_line, po_lines, used_po_lines, po_balance)
            decisions.append(decision)

            pair = self._decision_to_pair(inv_line, decision, po_balance)
            if pair.matched and pair.po_line:
                used_po_lines.add(pair.po_line.pk)
            pairs.append(pair)

        unmatched_inv = [p.invoice_line for p in pairs if not p.matched]
        unmatched_po = [pl for pl in po_lines if pl.pk not in used_po_lines]

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
            "Line match for invoice %s vs PO %s: %d pairs, %d unmatched_inv, %d unmatched_po, %d ambiguous",
            invoice.pk,
            po.po_number,
            len(pairs),
            len(unmatched_inv),
            len(unmatched_po),
            sum(1 for d in decisions if d.is_ambiguous),
        )

        return LineMatchResult(
            pairs=pairs,
            unmatched_invoice_lines=unmatched_inv,
            unmatched_po_lines=unmatched_po,
            all_lines_matched=all_matched,
            all_within_tolerance=all_tol,
            decisions=decisions,
        )

    # ------------------------------------------------------------------
    # Core scoring pipeline
    # ------------------------------------------------------------------

    def _decide(
        self,
        inv_line: InvoiceLineItem,
        po_lines: List[PurchaseOrderLineItem],
        used: set,
        po_balance: Optional["POBalance"] = None,
    ) -> LineMatchDecision:
        """Score all candidates, rank, detect ambiguity, and decide."""
        candidates = self._score_all_candidates(inv_line, po_lines, used, po_balance)
        candidates = self._apply_penalties(inv_line, candidates)
        candidates.sort(key=lambda c: (-c.total_score, c.po_line.pk))

        if not candidates:
            return LineMatchDecision(
                invoice_line=inv_line,
                status=STATUS_UNRESOLVED,
                match_method=METHOD_NONE,
                explanation="No PO-line candidates available",
            )

        best = candidates[0]
        second_best_score = candidates[1].total_score if len(candidates) > 1 else 0.0
        top_gap = best.total_score - second_best_score
        close_count = sum(
            1 for c in candidates
            if abs(c.total_score - best.total_score) <= AMBIGUITY_CLOSE_RANGE
        )

        is_ambig = self._detect_ambiguity(
            best.total_score, second_best_score, top_gap, close_count, best,
        )

        # Determine decision
        has_contradiction = bool(best.disqualifiers)
        status, method = self._classify(
            best.total_score, top_gap, has_contradiction, is_ambig,
        )

        # LLM fallback for ambiguous / unresolved
        if status in (STATUS_AMBIGUOUS, STATUS_UNRESOLVED) and self.llm_fallback:
            llm_result = self._try_llm_fallback(inv_line, candidates)
            if llm_result and llm_result.selected_po_line_id:
                for c in candidates:
                    if c.po_line.pk == llm_result.selected_po_line_id:
                        best = c
                        status = STATUS_MATCHED
                        method = METHOD_LLM_FALLBACK
                        is_ambig = False
                        break

        band = confidence_band(best.total_score) if status == STATUS_MATCHED else BAND_NONE
        matched_signals = best.matched_signals if status == STATUS_MATCHED else []
        rejected_signals = best.disqualifiers if best.disqualifiers else []

        explanation = self._build_explanation(
            status, method, best, top_gap, close_count, is_ambig,
        )

        return LineMatchDecision(
            invoice_line=inv_line,
            selected_po_line=best.po_line if status == STATUS_MATCHED else None,
            status=status,
            match_method=method,
            total_score=round(best.total_score, 4),
            confidence_band_val=band,
            candidate_count=len(candidates),
            best_score=round(best.total_score, 4),
            second_best_score=round(second_best_score, 4),
            top_gap=round(top_gap, 4),
            is_ambiguous=is_ambig,
            matched_signals=matched_signals,
            rejected_signals=rejected_signals,
            explanation=explanation,
            candidate_scores=candidates,
        )

    def _score_all_candidates(
        self,
        inv_line: InvoiceLineItem,
        po_lines: List[PurchaseOrderLineItem],
        used: set,
        po_balance: Optional["POBalance"] = None,
    ) -> List[LineCandidateScore]:
        candidates: List[LineCandidateScore] = []
        for po_line in po_lines:
            if po_line.pk in used:
                continue
            cs = self._score_candidate(inv_line, po_line, po_balance)
            candidates.append(cs)
        return candidates

    def _score_candidate(
        self,
        inv_line: InvoiceLineItem,
        po_line: PurchaseOrderLineItem,
        po_balance: Optional["POBalance"] = None,
    ) -> LineCandidateScore:
        """Compute weighted composite score for one inv/po line pair."""
        cs = LineCandidateScore(po_line=po_line)
        signals: List[str] = []
        notes: List[str] = []

        # 1. Item code (weight 0.30)
        inv_item_code = getattr(inv_line, "item_code", "") or ""
        po_item_code = po_line.item_code or ""
        inv_code_norm = inv_item_code.strip().lower()
        po_code_norm = po_item_code.strip().lower()

        if inv_code_norm and po_code_norm:
            if inv_code_norm == po_code_norm:
                cs.item_code_score = 0.30
                signals.append("item_code_exact")
                notes.append(f"Item code exact match: {po_item_code}")
            else:
                notes.append(f"Item code mismatch: inv={inv_item_code} vs po={po_item_code}")
        elif not inv_code_norm and not po_code_norm:
            notes.append("Item code absent on both sides")
        else:
            notes.append("Item code present on one side only")

        # 2. Exact normalised description (weight 0.20)
        inv_desc = normalize_line_text(inv_line.description or inv_line.raw_description)
        po_desc = normalize_line_text(po_line.description)

        if inv_desc and po_desc and inv_desc == po_desc:
            cs.description_exact_score = 0.20
            signals.append("description_exact")
            notes.append("Description exact match after normalisation")

        # 3. Token-based description similarity (weight 0.15)
        tok_sim = token_similarity(
            inv_line.description or inv_line.raw_description,
            po_line.description,
        )
        cs.token_similarity_raw = tok_sim
        if tok_sim >= 0.85:
            cs.description_token_score = 0.15
        elif tok_sim >= 0.70:
            cs.description_token_score = 0.12
        elif tok_sim >= 0.55:
            cs.description_token_score = 0.08
        elif tok_sim >= 0.40:
            cs.description_token_score = 0.04

        if cs.description_token_score > 0:
            signals.append(f"token_overlap_{tok_sim:.2f}")

        # Record matched tokens for explainability
        inv_tokens = extract_meaningful_tokens(inv_line.description or inv_line.raw_description)
        po_tokens = extract_meaningful_tokens(po_line.description)
        cs.matched_tokens = sorted(inv_tokens & po_tokens)

        # 4. Fuzzy string description similarity (weight 0.10)
        fz = fuzzy_similarity(
            inv_line.description or inv_line.raw_description,
            po_line.description,
        )
        cs.fuzzy_similarity_raw = fz
        if fz >= 90:
            cs.description_fuzzy_score = 0.10
        elif fz >= 80:
            cs.description_fuzzy_score = 0.08
        elif fz >= 70:
            cs.description_fuzzy_score = 0.05
        elif fz >= 60:
            cs.description_fuzzy_score = 0.02

        if cs.description_fuzzy_score > 0:
            signals.append(f"fuzzy_{fz:.0f}")

        # 5. Quantity proximity (weight 0.10)
        compare_qty = po_line.quantity
        compare_amount = po_line.line_amount
        if po_balance and po_balance.is_partial:
            if po_balance.prior_invoice_count > 0:
                line_bal = po_balance.line_balances.get(po_line.pk)
                if line_bal:
                    compare_qty = line_bal.remaining_qty
                    compare_amount = line_bal.remaining_amount
            else:
                compare_qty = inv_line.quantity or ZERO
                compare_amount = inv_line.line_amount or ZERO

        qty_var, qty_sc = quantity_proximity(inv_line.quantity, compare_qty)
        cs.quantity_score = qty_sc
        cs.qty_variance_pct = qty_var
        if qty_sc > 0:
            signals.append(f"qty_proximity_{qty_var:.1f}pct" if qty_var is not None else "qty_exact")

        # 6. Unit price proximity (weight 0.07)
        compare_price = po_line.unit_price
        if po_balance and po_balance.is_first_partial:
            compare_price = inv_line.unit_price

        price_var, price_sc = price_proximity(inv_line.unit_price, compare_price)
        cs.unit_price_score = price_sc
        cs.price_variance_pct = price_var
        if price_sc > 0:
            signals.append(f"price_proximity_{price_var:.1f}pct" if price_var is not None else "price_exact")

        # 7. Line amount proximity (weight 0.03)
        amt_var, amt_sc = amount_proximity(inv_line.line_amount, compare_amount)
        cs.amount_score = amt_sc
        cs.amount_variance_pct = amt_var
        if amt_sc > 0:
            signals.append("amount_proximity")

        # 8. UOM compatibility (weight 0.02)
        inv_uom = getattr(inv_line, "unit_of_measure", None) or ""
        po_uom = po_line.unit_of_measure or ""
        uom_reason, uom_sc = uom_compatibility(inv_uom, po_uom)
        cs.uom_score = uom_sc
        if uom_sc > 0:
            signals.append(f"uom_{uom_reason}")

        # 9. Category compatibility (weight 0.01)
        cat_reason, cat_sc = category_compatibility(
            inv_line.item_category, po_line.item_category,
        )
        cs.category_score = cat_sc
        if cat_sc > 0:
            signals.append(f"category_{cat_reason}")

        # 10. Service/stock compatibility (weight 0.01)
        ss_reason, ss_sc, ss_contradiction = service_stock_compatibility(
            inv_line.is_service_item, inv_line.is_stock_item,
            po_line.is_service_item, po_line.is_stock_item,
        )
        cs.service_stock_score = ss_sc
        if ss_sc > 0:
            signals.append(f"service_stock_{ss_reason}")
        if ss_contradiction:
            cs.disqualifiers.append("service_stock_contradiction")

        # 11. Line number alignment (weight 0.01)
        if (
            inv_line.line_number is not None
            and po_line.line_number is not None
            and inv_line.line_number == po_line.line_number
        ):
            cs.line_number_score = 0.01
            signals.append("line_number_aligned")

        # Sum raw score (before penalties)
        cs.total_score = (
            cs.item_code_score
            + cs.description_exact_score
            + cs.description_token_score
            + cs.description_fuzzy_score
            + cs.quantity_score
            + cs.unit_price_score
            + cs.amount_score
            + cs.uom_score
            + cs.category_score
            + cs.service_stock_score
            + cs.line_number_score
        )

        cs.matched_signals = signals
        cs.decision_notes = notes
        return cs

    # ------------------------------------------------------------------
    # Penalties
    # ------------------------------------------------------------------

    def _apply_penalties(
        self,
        inv_line: InvoiceLineItem,
        candidates: List[LineCandidateScore],
    ) -> List[LineCandidateScore]:
        for cs in candidates:
            penalty = 0.0

            # A. Service vs stock contradiction (unless item_code exact match)
            if "service_stock_contradiction" in cs.disqualifiers and cs.item_code_score == 0:
                penalty += PENALTY_SERVICE_STOCK_CONTRADICTION
                cs.decision_notes.append(
                    f"Penalty {PENALTY_SERVICE_STOCK_CONTRADICTION}: service/stock contradiction"
                )

            # B. Severe quantity contradiction
            if (
                cs.qty_variance_pct is not None
                and cs.qty_variance_pct > 25
                and cs.item_code_score == 0
                and cs.description_token_score <= 0.04
            ):
                penalty += PENALTY_SEVERE_QTY_CONTRADICTION
                cs.disqualifiers.append("severe_qty_contradiction")
                cs.decision_notes.append(
                    f"Penalty {PENALTY_SEVERE_QTY_CONTRADICTION}: qty variance {cs.qty_variance_pct:.1f}%"
                )

            # C. Severe price contradiction
            if (
                cs.price_variance_pct is not None
                and cs.price_variance_pct > 20
                and cs.description_token_score <= 0.04
            ):
                penalty += PENALTY_SEVERE_PRICE_CONTRADICTION
                cs.disqualifiers.append("severe_price_contradiction")
                cs.decision_notes.append(
                    f"Penalty {PENALTY_SEVERE_PRICE_CONTRADICTION}: price variance {cs.price_variance_pct:.1f}%"
                )

            # D. Description contradiction
            if (
                cs.token_similarity_raw < 0.20
                and cs.fuzzy_similarity_raw < 50
                and cs.item_code_score == 0
            ):
                penalty += PENALTY_DESCRIPTION_CONTRADICTION
                cs.disqualifiers.append("description_contradiction")
                cs.decision_notes.append(
                    f"Penalty {PENALTY_DESCRIPTION_CONTRADICTION}: token={cs.token_similarity_raw:.2f}, fuzzy={cs.fuzzy_similarity_raw:.0f}"
                )

            cs.penalties = penalty
            cs.total_score = max(0.0, cs.total_score + penalty)

        return candidates

    # ------------------------------------------------------------------
    # Ambiguity detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_ambiguity(
        best_score: float,
        second_best_score: float,
        top_gap: float,
        close_count: int,
        best: LineCandidateScore,
    ) -> bool:
        # Rule 1: gap too small
        if second_best_score > 0 and top_gap < AMBIGUITY_GAP:
            return True

        # Rule 2: multiple close candidates above threshold
        if close_count >= 2 and best_score > AMBIGUITY_CLOSE_MIN_SCORE:
            return True

        # Rule 4: no item_code and multiple high-ish candidates
        if best.item_code_score == 0 and close_count >= 2 and best_score >= 0.50:
            return True

        return False

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify(
        score: float,
        top_gap: float,
        has_contradiction: bool,
        is_ambiguous: bool,
    ) -> tuple:
        """Return (status, method)."""
        if is_ambiguous:
            return STATUS_AMBIGUOUS, METHOD_NONE

        if score < WEAK_THRESHOLD:
            return STATUS_UNRESOLVED, METHOD_NONE

        if has_contradiction and score < STRONG_MATCH_SCORE:
            return STATUS_UNRESOLVED, METHOD_NONE

        if score >= STRONG_MATCH_SCORE and top_gap >= STRONG_MATCH_GAP:
            method = METHOD_EXACT if score >= 0.95 else METHOD_DETERMINISTIC
            return STATUS_MATCHED, method

        if score >= MODERATE_MATCH_SCORE and top_gap >= MODERATE_MATCH_GAP:
            return STATUS_MATCHED, METHOD_DETERMINISTIC

        if score >= WEAK_THRESHOLD:
            return STATUS_AMBIGUOUS, METHOD_NONE

        return STATUS_UNRESOLVED, METHOD_NONE

    # ------------------------------------------------------------------
    # LLM fallback
    # ------------------------------------------------------------------

    def _try_llm_fallback(
        self,
        inv_line: InvoiceLineItem,
        candidates: List[LineCandidateScore],
    ):
        """Wrap the LLM fallback call with error handling."""
        if not self.llm_fallback:
            return None
        try:
            return self.llm_fallback.resolve(inv_line, candidates)
        except Exception:
            logger.exception("LLM fallback failed for invoice line %s", inv_line.pk)
            return None

    # ------------------------------------------------------------------
    # Pair builder (backward compat)
    # ------------------------------------------------------------------

    def _decision_to_pair(
        self,
        inv_line: InvoiceLineItem,
        decision: LineMatchDecision,
        po_balance: Optional["POBalance"] = None,
    ) -> LineMatchPair:
        """Convert a LineMatchDecision into the legacy LineMatchPair."""
        po_line = decision.selected_po_line

        if not po_line:
            return LineMatchPair(
                invoice_line=inv_line,
                matched=False,
                decision=decision,
                description_similarity=decision.best_score * 100 if decision.candidate_scores else 0.0,
            )

        # Compute tolerance comparisons using the engine (same as before)
        compare_qty = po_line.quantity
        compare_amount = po_line.line_amount
        compare_price = po_line.unit_price

        if po_balance and po_balance.is_partial:
            if po_balance.prior_invoice_count > 0:
                line_bal = po_balance.line_balances.get(po_line.pk)
                if line_bal:
                    compare_qty = line_bal.remaining_qty
                    compare_amount = line_bal.remaining_amount
            else:
                compare_qty = inv_line.quantity or ZERO
                compare_amount = inv_line.line_amount or ZERO

        if po_balance and po_balance.is_first_partial:
            compare_price = inv_line.unit_price

        qty_cmp = self.engine.compare_quantity(inv_line.quantity, compare_qty)
        price_cmp = self.engine.compare_price(inv_line.unit_price, compare_price)
        amount_cmp = self.engine.compare_amount(inv_line.line_amount, compare_amount)

        tax_inv = inv_line.tax_amount
        tax_po = po_line.tax_amount
        if po_balance and po_balance.is_first_partial:
            tax_po = tax_inv
        tax_diff = (tax_inv - tax_po) if tax_inv is not None and tax_po is not None else None

        tax_rate_match, tax_rate_details = _compare_line_tax_rate(inv_line, po_line)

        desc_sim = 0.0
        if decision.candidate_scores:
            desc_sim = decision.candidate_scores[0].fuzzy_similarity_raw

        return LineMatchPair(
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
            matched=True,
            decision=decision,
        )

    # ------------------------------------------------------------------
    # Explainability
    # ------------------------------------------------------------------

    @staticmethod
    def _build_explanation(
        status: str,
        method: str,
        best: LineCandidateScore,
        top_gap: float,
        close_count: int,
        is_ambig: bool,
    ) -> str:
        parts = []
        parts.append(f"Status: {status}, method: {method}")
        parts.append(f"Best score: {best.total_score:.4f} (band: {confidence_band(best.total_score)})")
        parts.append(f"Top gap: {top_gap:.4f}, close candidates: {close_count}")

        if best.matched_signals:
            parts.append(f"Signals: {', '.join(best.matched_signals)}")
        if best.disqualifiers:
            parts.append(f"Disqualifiers: {', '.join(best.disqualifiers)}")
        if best.matched_tokens:
            parts.append(f"Matched tokens: {', '.join(best.matched_tokens[:10])}")
        if is_ambig:
            parts.append("AMBIGUOUS: multiple close candidates or gap too narrow")

        return " | ".join(parts)


# ===================================================================
# Tax rate comparison (unchanged from v1)
# ===================================================================

def _compare_line_tax_rate(
    inv_line: InvoiceLineItem, po_line: PurchaseOrderLineItem,
) -> tuple:
    inv_rate = inv_line.tax_percentage
    if inv_rate is None:
        return None, None

    cgst = getattr(po_line, "cgst_rate", None) or Decimal("0")
    sgst = getattr(po_line, "sgst_rate", None) or Decimal("0")
    igst = getattr(po_line, "igst_rate", None) or Decimal("0")
    vat = getattr(po_line, "vat_rate", None) or Decimal("0")
    cess = getattr(po_line, "cess_rate", None) or Decimal("0")

    po_effective_rate = cgst + sgst + igst + vat + cess

    if po_effective_rate == Decimal("0"):
        return None, None

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
