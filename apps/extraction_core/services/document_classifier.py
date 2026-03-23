"""
DocumentTypeClassifier — Deterministic document type classification.

Classifies OCR text into document types (invoice, credit note, debit note,
delivery note, statement) using weighted keyword matching.  Country-agnostic
by design — uses multilingual keyword banks with no hardcoded country logic.

The classifier runs *before* field extraction so that downstream services
(schema selection, prompt building) can adapt to the document type.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Result of document type classification."""

    document_type: str = "INVOICE"  # best-match type
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)  # type → score
    matched_keywords: list[str] = field(default_factory=list)
    is_ambiguous: bool = False  # top-2 score gap < 0.15

    def to_dict(self) -> dict:
        return {
            "document_type": self.document_type,
            "confidence": round(self.confidence, 4),
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "matched_keywords": self.matched_keywords,
            "is_ambiguous": self.is_ambiguous,
        }


# ---------------------------------------------------------------------------
# Keyword banks — weighted, multilingual
# ---------------------------------------------------------------------------

# Each entry: (pattern, weight)
# Patterns are case-insensitive.  Higher weight → stronger signal.
# Multilingual keywords ensure cross-country coverage without hardcoding.

_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "CREDIT_NOTE": [
        # English
        (r"\bcredit\s*note\b", 3.0),
        (r"\bcredit\s*memo\b", 3.0),
        (r"\bcredit\s*memorandum\b", 3.0),
        (r"\bcr\.?\s*note\b", 2.5),
        (r"\bcredited\s+to\b", 1.5),
        (r"\breturn\s+(?:of\s+)?goods\b", 1.0),
        (r"\brefund\b", 0.8),
        # Arabic
        (r"\bإشعار\s*(?:دائن|ائتمان)\b", 3.0),
        # Hindi
        (r"\bक्रेडिट\s*नोट\b", 3.0),
        # French / German / Spanish
        (r"\bavoir\b", 2.5),
        (r"\bgutschrift\b", 2.5),
        (r"\bnota\s*(?:de\s*)?cr[eé]dito\b", 3.0),
    ],
    "DEBIT_NOTE": [
        (r"\bdebit\s*note\b", 3.0),
        (r"\bdebit\s*memo\b", 3.0),
        (r"\bdebit\s*memorandum\b", 3.0),
        (r"\bdr\.?\s*note\b", 2.5),
        (r"\bsupplementary\s+invoice\b", 2.0),
        (r"\badditional\s+charge\b", 1.5),
        # Arabic
        (r"\bإشعار\s*مدين\b", 3.0),
        # French / German / Spanish
        (r"\bnote\s*de\s*d[eé]bit\b", 3.0),
        (r"\blastschrift\b", 2.5),
        (r"\bnota\s*(?:de\s*)?d[eé]bito\b", 3.0),
    ],
    "DELIVERY_NOTE": [
        (r"\bdelivery\s*note\b", 3.0),
        (r"\bdelivery\s*challan\b", 3.0),
        (r"\bpacking\s*(?:list|slip)\b", 2.5),
        (r"\bshipment\s*(?:note|advice)\b", 2.5),
        (r"\bgoods\s*(?:received|dispatched)\s*note\b", 2.5),
        (r"\bway\s*bill\b", 2.0),
        (r"\bdispatch\s*(?:note|advice)\b", 2.5),
        (r"\bconsignment\s*note\b", 2.5),
        # Arabic
        (r"\bإشعار\s*(?:تسليم|شحن)\b", 3.0),
        # French / German
        (r"\bbon\s*de\s*livraison\b", 3.0),
        (r"\blieferschein\b", 3.0),
    ],
    "STATEMENT": [
        (r"\baccount\s*statement\b", 3.0),
        (r"\bstatement\s*of\s*account\b", 3.0),
        (r"\bbalance\s*(?:due|forward|brought)\b", 2.0),
        (r"\baging\s*(?:report|summary)\b", 2.5),
        (r"\bopen\s*items?\s*(?:list|report)\b", 2.0),
        (r"\bpayment\s*(?:due|reminder|overdue)\b", 1.5),
        (r"\boutstanding\s*(?:balance|amount)\b", 1.5),
        # Arabic
        (r"\bكشف\s*حساب\b", 3.0),
        # French / German
        (r"\brelevé\s*de\s*compte\b", 3.0),
        (r"\bkontoauszug\b", 3.0),
    ],
    "INVOICE": [
        (r"\btax\s*invoice\b", 3.0),
        (r"\binvoice\b", 2.0),
        (r"\bbill\s*(?:of\s*supply|to)\b", 1.5),
        (r"\bproforma\s*invoice\b", 2.5),
        (r"\bcommercial\s*invoice\b", 2.5),
        (r"\binv[\.\s]*(?:no|number|#)\b", 1.5),
        (r"\binvoice\s*(?:no|number|date|#)\b", 2.0),
        (r"\bamount\s*(?:due|payable)\b", 1.0),
        (r"\btotal\s*(?:amount|due|payable)\b", 1.0),
        # Arabic
        (r"\bفاتورة\b", 2.5),
        (r"\bفاتورة\s*ضريبية\b", 3.0),
        # Hindi / French / German / Spanish
        (r"\bबीजक|चालान\b", 2.5),
        (r"\bfacture\b", 2.5),
        (r"\brechnung\b", 2.5),
        (r"\bfactura\b", 2.5),
    ],
}

# Negative signals — reduce score for a type
_NEGATIVE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "INVOICE": [
        (r"\bcredit\s*note\b", -2.0),
        (r"\bdebit\s*note\b", -2.0),
        (r"\bdelivery\s*note\b", -1.5),
        (r"\bstatement\s*of\s*account\b", -1.5),
    ],
}


class DocumentTypeClassifier:
    """
    Classifies document text into one of:
    INVOICE, CREDIT_NOTE, DEBIT_NOTE, DELIVERY_NOTE, STATEMENT.

    Uses weighted keyword matching on the first 5000 chars (title zone)
    and a broader scan of the full text with reduced weight.
    """

    # Only scan the first N chars with full weight (title zone)
    _TITLE_ZONE = 5000
    # Weight multiplier for matches outside the title zone
    _BODY_WEIGHT = 0.4
    # Minimum confidence to accept
    _MIN_CONFIDENCE = 0.25
    # Ambiguity threshold (gap between top-1 and top-2)
    _AMBIGUITY_GAP = 0.15

    @classmethod
    def classify(cls, ocr_text: str) -> ClassificationResult:
        """
        Classify document type from OCR text.

        Returns ClassificationResult with best type, confidence, and
        per-type scores.
        """
        if not ocr_text or not ocr_text.strip():
            return ClassificationResult(
                document_type="INVOICE",
                confidence=0.0,
            )

        title_zone = ocr_text[: cls._TITLE_ZONE]
        body_zone = ocr_text[cls._TITLE_ZONE:]

        scores: dict[str, float] = {}
        all_matched: list[str] = []

        for doc_type, patterns in _KEYWORDS.items():
            score = 0.0
            for pattern, weight in patterns:
                # Title zone — full weight
                matches = re.findall(pattern, title_zone, re.IGNORECASE)
                if matches:
                    # Count first match at full weight, subsequent at 0.3×
                    score += weight + (len(matches) - 1) * weight * 0.3
                    all_matched.append(matches[0])

                # Body zone — reduced weight
                if body_zone:
                    body_matches = re.findall(
                        pattern, body_zone, re.IGNORECASE,
                    )
                    if body_matches:
                        score += (
                            len(body_matches) * weight * cls._BODY_WEIGHT
                        )

            # Apply negative signals
            for pattern, neg_weight in _NEGATIVE_SIGNALS.get(doc_type, []):
                neg_hits = len(
                    re.findall(pattern, title_zone, re.IGNORECASE),
                )
                if neg_hits:
                    score += neg_weight * neg_hits

            scores[doc_type] = max(score, 0.0)

        # Normalize scores to 0-1 confidence
        max_score = max(scores.values()) if scores else 0.0
        if max_score <= 0:
            return ClassificationResult(
                document_type="INVOICE",
                confidence=0.0,
                scores=scores,
            )

        # Sort by score descending
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        best_type, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        # Confidence: based on score magnitude and separation
        total_score = sum(scores.values())
        confidence = best_score / total_score if total_score > 0 else 0.0

        # Check ambiguity
        gap = (best_score - second_score) / max_score if max_score > 0 else 1.0
        is_ambiguous = gap < cls._AMBIGUITY_GAP and confidence < 0.7

        # If below minimum confidence, default to INVOICE
        if confidence < cls._MIN_CONFIDENCE:
            best_type = "INVOICE"

        return ClassificationResult(
            document_type=best_type,
            confidence=confidence,
            scores={k: v / max_score for k, v in scores.items()},
            matched_keywords=all_matched[:10],  # cap at 10
            is_ambiguous=is_ambiguous,
        )
