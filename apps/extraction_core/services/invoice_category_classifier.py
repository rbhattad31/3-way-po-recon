"""InvoiceCategoryClassifier — lightweight rule-based invoice category detection.

Classifies invoice OCR text into one of three categories:
  - goods    : physical goods, materials, products
  - service  : professional services, fees, subscriptions, consulting
  - travel   : hotel, airfare, itinerary, booking-based invoices

Runs before prompt composition so the extraction prompt can be tailored
to the invoice type. Falls back gracefully — callers must tolerate None.

Design follows DocumentTypeClassifier in document_classifier.py:
  weighted keyword heuristics, title-zone priority, structured result.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class InvoiceCategoryResult:
    """Result of invoice category classification."""
    category: str = "service"          # best-match category
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)   # matched keyword evidence
    is_ambiguous: bool = False          # True when top-2 gap < threshold


# ---------------------------------------------------------------------------
# Keyword banks
# Each entry: (pattern, weight).  Patterns are case-insensitive.
# ---------------------------------------------------------------------------

_TRAVEL_SIGNALS: list[tuple[str, float]] = [
    # Strong title-level signals
    (r"\bhotel\b", 3.0),
    (r"\bairfare\b", 3.0),
    (r"\bflight\b", 2.5),
    (r"\bitinerary\b", 3.0),
    (r"\bpassenger\s+name\b", 3.0),
    (r"\bbooking\s+(?:id|reference|confirmation|no)\b", 3.0),
    (r"\bhotel\s+booking\b", 3.5),
    (r"\btravel\s+solutions?\b", 2.5),
    (r"\btravel\s+(?:agency|management|portal)\b", 2.5),
    # Fare/stay patterns
    (r"\bbasic\s+fare\b", 2.5),
    (r"\btotal\s+fare\b", 2.0),
    (r"\bbase\s+fare\b", 2.5),
    (r"\bhotel\s+tax(?:es)?\b", 2.5),
    (r"\broom\s+(?:rate|charge|tariff)\b", 2.5),
    (r"\bcheck[-\s]?in\b", 1.5),
    (r"\bcheck[-\s]?out\b", 1.5),
    (r"\bnight\s+stay\b", 2.0),
    (r"\bper\s+night\b", 2.0),
    (r"\bairline\b", 2.0),
    (r"\bboarding\s+pass\b", 2.5),
    (r"\bpnr\b", 2.5),
    (r"\bseat\s+(?:no|number|class)\b", 2.0),
    (r"\bcabin\b", 1.5),
    (r"\bdeparture\b", 1.5),
    (r"\barrival\b", 1.5),
    (r"\bservice\s+charge\b", 1.0),  # common in travel invoices too
    (r"\bconvenience\s+fee\b", 1.5),
    (r"\bcart\s+ref\b", 2.0),
    # CART / booking reference patterns used by travel aggregators
    (r"\bcart\s+ref(?:erence)?\.?\s*no\.?\b", 2.5),
]

_GOODS_SIGNALS: list[tuple[str, float]] = [
    (r"\bhsn\b", 3.0),            # HSN code mandatory on goods GST invoices
    (r"\bhsn\s+(?:code|no)\b", 3.5),
    (r"\bsac\s+code\b", 1.0),     # SAC = services, but sometimes mis-labelled
    (r"\bqty\b", 2.5),
    (r"\bquantity\b", 2.0),
    (r"\bpcs\b", 2.5),
    (r"\bunits?\b", 1.5),
    (r"\brate\s+per\s+(?:unit|pcs|kg|m|ltr)\b", 2.5),
    (r"\bmaterial\b", 2.0),
    (r"\braw\s+material\b", 3.0),
    (r"\bpacking\b", 1.5),
    (r"\bproduct\s+(?:code|id|no)\b", 2.5),
    (r"\bsku\b", 2.5),
    (r"\bpart\s+(?:no|number|code)\b", 2.0),
    (r"\bitem\s+(?:code|no)\b", 2.0),
    (r"\bbatch\s+(?:no|number)\b", 2.0),
    (r"\bdelivery\s+challan\b", 2.0),
    (r"\bgoods\b", 1.5),
    (r"\bsupply\s+of\s+goods\b", 3.0),
    (r"\be[-\s]?way\s+bill\b", 2.5),
    (r"\bmanufacturer\b", 2.0),
    (r"\bserial\s+no\b", 1.0),
]

_SERVICE_SIGNALS: list[tuple[str, float]] = [
    (r"\bprofessional\s+(?:fee|fees|charges)\b", 3.0),
    (r"\bconsulting\s+(?:fee|fees|charges)\b", 3.0),
    (r"\bconsultancy\b", 2.5),
    (r"\bmaintenance\s+(?:fee|charges)\b", 2.5),
    (r"\bsubscription\b", 3.0),
    (r"\blicense\s+fee\b", 2.5),
    (r"\bsupport\s+(?:fee|charges|services)\b", 2.5),
    (r"\bannual\s+(?:maintenance|support|subscription)\b", 2.5),
    (r"\bservice\s+(?:fee|charges|agreement)\b", 2.0),
    (r"\bmanagement\s+fee\b", 2.5),
    (r"\badvisory\b", 2.5),
    (r"\bsac\b", 2.0),            # SAC = Services Accounting Code (India GST)
    (r"\boutsourc\w+\b", 2.0),
    (r"\bstaff(?:ing)?\s+(?:fee|charges)\b", 2.0),
    (r"\brental\b", 1.5),
    (r"\bsoftware\s+(?:license|subscription|fee)\b", 2.5),
    (r"\bcloud\s+(?:service|subscription|hosting)\b", 2.5),
    (r"\bprocessing\s+fee\b", 2.0),
    (r"\bfinance\s+charge\b", 2.0),
    (r"\binterest\s+charge\b", 2.0),
    (r"\bsupply\s+of\s+service\b", 3.0),
]


class InvoiceCategoryClassifier:
    """
    Classify an invoice into goods / service / travel using deterministic
    keyword heuristics.

    Mirrors DocumentTypeClassifier:
      - Title zone (first 3000 chars) scanned at full weight
      - Body scanned at reduced weight (0.4×)
      - Confidence = best_score / total_score
      - Ambiguous if gap between top-2 < threshold
    """

    _TITLE_ZONE = 3000
    _BODY_WEIGHT = 0.4
    _AMBIGUITY_GAP = 0.20     # tighter than document classifier — categories overlap more
    _MIN_CONFIDENCE = 0.20    # below this → default to "service"

    _BANKS: dict[str, list[tuple[str, float]]] = {
        "travel": _TRAVEL_SIGNALS,
        "goods": _GOODS_SIGNALS,
        "service": _SERVICE_SIGNALS,
    }

    @classmethod
    def classify(cls, ocr_text: str) -> InvoiceCategoryResult:
        """Classify OCR text into goods / service / travel.

        Returns InvoiceCategoryResult with category, confidence, signals, is_ambiguous.
        Defaults to 'service' on empty input or low confidence.
        """
        if not ocr_text or not ocr_text.strip():
            return InvoiceCategoryResult(category="service", confidence=0.0)

        title = ocr_text[: cls._TITLE_ZONE]
        body = ocr_text[cls._TITLE_ZONE:]

        scores: dict[str, float] = {}
        all_signals: list[str] = []

        for cat, patterns in cls._BANKS.items():
            score = 0.0
            for pattern, weight in patterns:
                # Title zone — full weight
                hits = re.findall(pattern, title, re.IGNORECASE)
                if hits:
                    score += weight + (len(hits) - 1) * weight * 0.3
                    all_signals.append(hits[0])
                # Body — reduced weight
                if body:
                    body_hits = re.findall(pattern, body, re.IGNORECASE)
                    if body_hits:
                        score += len(body_hits) * weight * cls._BODY_WEIGHT
            scores[cat] = max(score, 0.0)

        max_score = max(scores.values()) if scores else 0.0
        if max_score <= 0:
            return InvoiceCategoryResult(category="service", confidence=0.0)

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        best_cat, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        total_score = sum(scores.values())
        confidence = best_score / total_score if total_score > 0 else 0.0

        gap = (best_score - second_score) / max_score if max_score > 0 else 1.0
        is_ambiguous = gap < cls._AMBIGUITY_GAP and confidence < 0.7

        if confidence < cls._MIN_CONFIDENCE:
            best_cat = "service"

        return InvoiceCategoryResult(
            category=best_cat,
            confidence=round(confidence, 4),
            signals=list(dict.fromkeys(all_signals))[:10],  # deduplicate, cap at 10
            is_ambiguous=is_ambiguous,
        )
