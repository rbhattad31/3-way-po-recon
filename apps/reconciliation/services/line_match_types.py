"""Typed dataclasses for the deterministic line-matching scorer.

These are the service-layer contracts consumed by LineMatchService,
TwoWayMatchService, ReconciliationResultService, and ExceptionBuilderService.

Backward compatibility: The legacy ``LineMatchPair`` and ``LineMatchResult``
shapes are still produced by LineMatchService so that callers that have not
yet been updated keep working. New rich data is exposed via
``LineMatchDecision`` / ``LineCandidateScore`` on the updated result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from apps.documents.models import InvoiceLineItem, PurchaseOrderLineItem


# ------------------------------------------------------------------
# Confidence bands
# ------------------------------------------------------------------
BAND_HIGH = "HIGH"          # >= 0.85
BAND_GOOD = "GOOD"          # 0.75 .. <0.85
BAND_MODERATE = "MODERATE"  # 0.62 .. <0.75
BAND_LOW = "LOW"            # 0.50 .. <0.62
BAND_NONE = "NONE"          # < 0.50


def confidence_band(score: float) -> str:
    if score >= 0.85:
        return BAND_HIGH
    if score >= 0.75:
        return BAND_GOOD
    if score >= 0.62:
        return BAND_MODERATE
    if score >= 0.50:
        return BAND_LOW
    return BAND_NONE


# ------------------------------------------------------------------
# Match methods
# ------------------------------------------------------------------
METHOD_EXACT = "EXACT"
METHOD_DETERMINISTIC = "DETERMINISTIC"
METHOD_LLM_FALLBACK = "LLM_FALLBACK"
METHOD_NONE = "NONE"

# ------------------------------------------------------------------
# Decision statuses
# ------------------------------------------------------------------
STATUS_MATCHED = "MATCHED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_UNRESOLVED = "UNRESOLVED"


# ------------------------------------------------------------------
# Thresholds (named constants, single source of truth)
# ------------------------------------------------------------------
STRONG_MATCH_SCORE = 0.75
STRONG_MATCH_GAP = 0.10

MODERATE_MATCH_SCORE = 0.62
MODERATE_MATCH_GAP = 0.08

WEAK_THRESHOLD = 0.50

AMBIGUITY_GAP = 0.08
AMBIGUITY_CLOSE_RANGE = 0.05
AMBIGUITY_CLOSE_MIN_SCORE = 0.55


# ------------------------------------------------------------------
# Penalty constants
# ------------------------------------------------------------------
PENALTY_SERVICE_STOCK_CONTRADICTION = -0.10
PENALTY_SEVERE_QTY_CONTRADICTION = -0.08
PENALTY_SEVERE_PRICE_CONTRADICTION = -0.08
PENALTY_DESCRIPTION_CONTRADICTION = -0.05


@dataclass
class LineCandidateScore:
    """Score breakdown for a single invoice-line vs PO-line candidate."""

    po_line: PurchaseOrderLineItem
    total_score: float = 0.0

    # Individual signal scores
    item_code_score: float = 0.0
    description_exact_score: float = 0.0
    description_token_score: float = 0.0
    description_fuzzy_score: float = 0.0
    quantity_score: float = 0.0
    unit_price_score: float = 0.0
    amount_score: float = 0.0
    uom_score: float = 0.0
    category_score: float = 0.0
    service_stock_score: float = 0.0
    line_number_score: float = 0.0

    # Raw similarity values (for explainability)
    token_similarity_raw: float = 0.0
    fuzzy_similarity_raw: float = 0.0
    qty_variance_pct: Optional[float] = None
    price_variance_pct: Optional[float] = None
    amount_variance_pct: Optional[float] = None

    # Penalties applied
    penalties: float = 0.0

    # Flags
    hard_filters_passed: bool = True
    disqualifiers: List[str] = field(default_factory=list)
    matched_signals: List[str] = field(default_factory=list)
    decision_notes: List[str] = field(default_factory=list)

    # Tokens matched (for reviewer explainability)
    matched_tokens: List[str] = field(default_factory=list)


@dataclass
class LineMatchDecision:
    """Final pairing decision for a single invoice line."""

    invoice_line: InvoiceLineItem
    selected_po_line: Optional[PurchaseOrderLineItem] = None
    status: str = STATUS_UNRESOLVED   # MATCHED / AMBIGUOUS / UNRESOLVED
    match_method: str = METHOD_NONE   # EXACT / DETERMINISTIC / LLM_FALLBACK / NONE
    total_score: float = 0.0
    confidence_band_val: str = BAND_NONE
    candidate_count: int = 0
    best_score: float = 0.0
    second_best_score: float = 0.0
    top_gap: float = 0.0
    is_ambiguous: bool = False
    matched_signals: List[str] = field(default_factory=list)
    rejected_signals: List[str] = field(default_factory=list)
    explanation: str = ""
    candidate_scores: List[LineCandidateScore] = field(default_factory=list)

    def to_result_line_metadata(self) -> Dict[str, Any]:
        """Serialise rich metadata for ``ReconciliationResultLine.line_match_meta``."""
        return {
            "top_gap": round(self.top_gap, 4),
            "second_best_score": round(self.second_best_score, 4),
            "candidate_count": self.candidate_count,
            "match_method": self.match_method,
            "status": self.status,
            "is_ambiguous": self.is_ambiguous,
            "matched_tokens": (
                self.candidate_scores[0].matched_tokens
                if self.candidate_scores else []
            ),
            "po_candidate_ids_considered": [
                cs.po_line.pk for cs in self.candidate_scores
            ],
            "decision_notes": (
                self.candidate_scores[0].decision_notes
                if self.candidate_scores else []
            ),
        }


@dataclass
class LLMFallbackResult:
    """Structured result from the optional LLM fallback resolver."""

    selected_po_line_id: Optional[int] = None
    confidence: float = 0.0
    rationale: str = ""
    alternative_candidates: List[int] = field(default_factory=list)
    recommended_action: str = ""
    matched_signals: List[str] = field(default_factory=list)
    rejected_signals: List[str] = field(default_factory=list)
