"""Deterministic text-matching helpers for invoice-line to PO-line pairing.

All functions are pure, stateless, and independently testable.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import FrozenSet, Optional, Set, Tuple

from rapidfuzz import fuzz as rf_fuzz

# ------------------------------------------------------------------
# Stopwords -- low-information filler removed during normalisation.
# Domain-important words (frozen, boneless, maintenance ...) are
# intentionally absent.
# ------------------------------------------------------------------
_STOP_WORDS: FrozenSet[str] = frozenset({
    "the", "and", "for", "of", "item", "charge", "charges",
    "service", "services", "goods", "material", "materials",
    "monthly", "supply", "supplies", "per", "unit", "units",
    "total", "net", "nos", "qty", "number", "no",
})

# ------------------------------------------------------------------
# UOM equivalence map (lowercase canonical -> set of aliases)
# ------------------------------------------------------------------
_UOM_EQUIVALENTS: dict[str, set[str]] = {
    "ea":     {"ea", "each", "nos", "no", "pcs", "pieces", "piece", "pc", "unit", "units"},
    "kg":     {"kg", "kgs", "kilogram", "kilograms", "kilo", "kilos"},
    "g":      {"g", "gm", "gms", "gram", "grams"},
    "l":      {"l", "lt", "ltr", "ltrs", "litre", "litres", "liter", "liters"},
    "ml":     {"ml", "mls", "milliliter", "milliliters", "millilitre", "millilitres"},
    "m":      {"m", "mtr", "mtrs", "meter", "meters", "metre", "metres"},
    "cm":     {"cm", "cms", "centimeter", "centimeters"},
    "ft":     {"ft", "feet", "foot"},
    "in":     {"in", "inch", "inches"},
    "box":    {"box", "boxes", "bx"},
    "pkt":    {"pkt", "pkts", "packet", "packets", "pack", "packs"},
    "bag":    {"bag", "bags"},
    "roll":   {"roll", "rolls", "rl"},
    "pair":   {"pair", "pairs", "pr", "prs"},
    "set":    {"set", "sets"},
    "doz":    {"doz", "dozen", "dozens"},
    "case":   {"case", "cases", "cs"},
    "drum":   {"drum", "drums"},
    "ton":    {"ton", "tons", "tonne", "tonnes", "mt"},
    "sqm":    {"sqm", "sq m", "square meter", "square meters"},
    "sqft":   {"sqft", "sq ft", "square foot", "square feet"},
    "hr":     {"hr", "hrs", "hour", "hours"},
    "day":    {"day", "days"},
    "month":  {"month", "months", "mth", "mths"},
    "lot":    {"lot", "lots", "ls", "lump sum", "lumpsum"},
}

# Reverse index: alias -> canonical
_UOM_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in _UOM_EQUIVALENTS.items():
    for _a in _aliases:
        _UOM_ALIAS_TO_CANONICAL[_a] = _canon

ZERO = Decimal("0")


# ===================================================================
# Text normalisation
# ===================================================================

def normalize_line_text(text: Optional[str]) -> str:
    """Normalise a line description for comparison.

    - lowercase
    - strip punctuation (keep alphanumeric + space)
    - collapse whitespace
    - remove stopwords
    """
    if not text:
        return ""
    t = text.lower()
    # Replace separators with space
    t = re.sub(r"[-/,.:;|\\]", " ", t)
    # Strip remaining non-alphanum (keep spaces)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_meaningful_tokens(text: Optional[str]) -> Set[str]:
    """Return a set of meaningful tokens after normalisation and stopword removal."""
    norm = normalize_line_text(text)
    if not norm:
        return set()
    tokens = set(norm.split())
    return tokens - _STOP_WORDS


# ===================================================================
# Similarity helpers
# ===================================================================

def token_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Jaccard-style token overlap ratio in [0.0, 1.0]."""
    tokens_a = extract_meaningful_tokens(a)
    tokens_b = extract_meaningful_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0


def token_containment(a: Optional[str], b: Optional[str]) -> float:
    """Shorter-set recall: fraction of shorter token set found in longer set.

    Returns 0.0-1.0.  Values near 1.0 mean the shorter description (often the
    PO line) is fully contained within the longer description (invoice).
    This complements Jaccard which penalises length differences heavily.
    """
    tokens_a = extract_meaningful_tokens(a)
    tokens_b = extract_meaningful_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    shorter = tokens_a if len(tokens_a) <= len(tokens_b) else tokens_b
    longer = tokens_b if len(tokens_a) <= len(tokens_b) else tokens_a
    intersection = shorter & longer
    return len(intersection) / len(shorter) if shorter else 0.0


def fuzzy_similarity(a: Optional[str], b: Optional[str]) -> float:
    """RapidFuzz token-set ratio in [0, 100].

    Uses ``token_set_ratio`` instead of ``token_sort_ratio`` so that
    abbreviated PO descriptions (e.g. "RPA") that appear as a subset of
    a longer invoice description score highly.  ``token_set_ratio``
    isolates the intersection tokens before comparing, which handles
    length asymmetry gracefully.
    """
    na = normalize_line_text(a)
    nb = normalize_line_text(b)
    if not na or not nb:
        return 0.0
    return rf_fuzz.token_set_ratio(na, nb)


# ===================================================================
# Numeric proximity helpers
# ===================================================================

def _safe_decimal(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _pct_variance(a: Optional[Decimal], b: Optional[Decimal]) -> Optional[float]:
    """Return absolute percentage variance between two decimals, or None if either is missing."""
    if a is None or b is None:
        return None
    if b == ZERO and a == ZERO:
        return 0.0
    base = max(abs(a), abs(b))
    if base == ZERO:
        return 100.0
    diff = abs(a - b)
    return float(diff / base * 100)


def quantity_proximity(inv_qty, po_qty) -> Tuple[Optional[float], float]:
    """Return (pct_variance_or_None, score_contribution).

    Score weights (out of 0.10):
    - exact -> 0.10
    - within 2% -> 0.08
    - within 5% -> 0.05
    - within 10% -> 0.02
    - else -> 0.00
    """
    a = _safe_decimal(inv_qty)
    b = _safe_decimal(po_qty)
    pv = _pct_variance(a, b)
    if pv is None:
        return None, 0.0
    if pv == 0.0:
        return pv, 0.10
    if pv <= 2.0:
        return pv, 0.08
    if pv <= 5.0:
        return pv, 0.05
    if pv <= 10.0:
        return pv, 0.02
    return pv, 0.0


def price_proximity(inv_price, po_price) -> Tuple[Optional[float], float]:
    """Return (pct_variance_or_None, score_contribution).

    Score weights (out of 0.07):
    - exact or within 1% -> 0.07
    - within 3% -> 0.05
    - within 5% -> 0.03
    - partial invoice (inv < po, ratio >= 10%) -> 0.02
    - else -> 0.00
    """
    a = _safe_decimal(inv_price)
    b = _safe_decimal(po_price)
    pv = _pct_variance(a, b)
    if pv is None:
        return None, 0.0
    if pv <= 1.0:
        return pv, 0.07
    if pv <= 3.0:
        return pv, 0.05
    if pv <= 5.0:
        return pv, 0.03
    # Partial invoice: invoice price less than PO price is plausible
    # partial billing, not a contradiction.
    if a is not None and b is not None and a > 0 and b > 0 and a < b:
        ratio = float(a / b)
        if ratio >= 0.10:
            return pv, 0.02
    return pv, 0.0


def amount_proximity(inv_amount, po_amount) -> Tuple[Optional[float], float]:
    """Return (pct_variance_or_None, score_contribution).

    Score weights (out of 0.03):
    - within 1% -> 0.03
    - within 3% -> 0.02
    - within 5% -> 0.01
    - partial invoice (inv < po, ratio >= 10%) -> 0.01
    - else -> 0.00
    """
    a = _safe_decimal(inv_amount)
    b = _safe_decimal(po_amount)
    pv = _pct_variance(a, b)
    if pv is None:
        return None, 0.0
    if pv <= 1.0:
        return pv, 0.03
    if pv <= 3.0:
        return pv, 0.02
    if pv <= 5.0:
        return pv, 0.01
    # Partial invoice: invoiced amount less than PO amount is plausible.
    if a is not None and b is not None and a > 0 and b > 0 and a < b:
        ratio = float(a / b)
        if ratio >= 0.10:
            return pv, 0.01
    return pv, 0.0


# ===================================================================
# UOM / category / service-stock compatibility
# ===================================================================

def _normalise_uom(uom: Optional[str]) -> str:
    if not uom:
        return ""
    return uom.strip().lower()


def uom_compatibility(inv_uom: Optional[str], po_uom: Optional[str]) -> Tuple[str, float]:
    """Return (reason, score).

    Scores (out of 0.02):
    - exact normalised match -> 0.02
    - known equivalent -> 0.015
    - one side missing -> 0.005
    - incompatible -> 0.00
    """
    a = _normalise_uom(inv_uom)
    b = _normalise_uom(po_uom)

    if not a or not b:
        return "one_side_missing", 0.005

    if a == b:
        return "exact", 0.02

    ca = _UOM_ALIAS_TO_CANONICAL.get(a, a)
    cb = _UOM_ALIAS_TO_CANONICAL.get(b, b)
    if ca == cb:
        return "equivalent", 0.015

    return "incompatible", 0.0


def category_compatibility(
    inv_category: Optional[str], po_category: Optional[str],
) -> Tuple[str, float]:
    """Return (reason, score).

    Scores (out of 0.01):
    - same -> 0.01
    - one missing -> 0.003
    - mismatch -> 0.00
    """
    a = (inv_category or "").strip().lower()
    b = (po_category or "").strip().lower()
    if not a or not b:
        return "one_side_missing", 0.003
    if a == b:
        return "same", 0.01
    return "mismatch", 0.0


def service_stock_compatibility(
    inv_is_service: Optional[bool],
    inv_is_stock: Optional[bool],
    po_is_service: Optional[bool],
    po_is_stock: Optional[bool],
) -> Tuple[str, float, bool]:
    """Return (reason, score, is_contradiction).

    Scores (out of 0.01):
    - both match -> 0.01
    - one unknown -> 0.003
    - explicit mismatch -> 0.00, is_contradiction=True
    """
    inv_known = inv_is_service is not None or inv_is_stock is not None
    po_known = po_is_service is not None or po_is_stock is not None

    if not inv_known or not po_known:
        return "one_side_unknown", 0.003, False

    # Determine effective type
    inv_svc = bool(inv_is_service)
    po_svc = bool(po_is_service)
    inv_stk = bool(inv_is_stock)
    po_stk = bool(po_is_stock)

    if inv_svc == po_svc and inv_stk == po_stk:
        return "compatible", 0.01, False

    # Explicit contradiction: one service, other stock
    if (inv_svc and po_stk) or (inv_stk and po_svc):
        return "contradiction", 0.0, True

    return "one_side_unknown", 0.003, False
