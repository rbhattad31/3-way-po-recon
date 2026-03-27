"""
RelationshipExtractor — Extract document cross-references.

Finds PO numbers, GRN references, contract numbers, and shipment
references from OCR text.  Country-agnostic — uses pattern banks
with common international formats.

Runs as part of the document intelligence pre-processing layer,
before field-level extraction.
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
class DocumentReference:
    """A single cross-reference found in the document."""

    ref_type: str          # PO | GRN | CONTRACT | SHIPMENT
    ref_value: str         # the extracted reference number/ID
    confidence: float = 0.0
    source_snippet: str = ""  # context around the match
    page_number: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "ref_type": self.ref_type,
            "ref_value": self.ref_value,
            "confidence": round(self.confidence, 4),
        }
        if self.source_snippet:
            d["source_snippet"] = self.source_snippet
        if self.page_number is not None:
            d["page_number"] = self.page_number
        return d


@dataclass
class RelationshipResult:
    """Aggregated cross-reference extraction result."""

    po_numbers: list[DocumentReference] = field(default_factory=list)
    grn_references: list[DocumentReference] = field(default_factory=list)
    contract_references: list[DocumentReference] = field(default_factory=list)
    shipment_references: list[DocumentReference] = field(default_factory=list)
    total_found: int = 0

    def to_dict(self) -> dict:
        return {
            "po_numbers": [r.to_dict() for r in self.po_numbers],
            "grn_references": [r.to_dict() for r in self.grn_references],
            "contract_references": [
                r.to_dict() for r in self.contract_references
            ],
            "shipment_references": [
                r.to_dict() for r in self.shipment_references
            ],
            "total_found": self.total_found,
        }

    @property
    def primary_po(self) -> str | None:
        """Return the highest-confidence PO number, or None."""
        if self.po_numbers:
            best = max(self.po_numbers, key=lambda r: r.confidence)
            return best.ref_value
        return None

    @property
    def primary_grn(self) -> str | None:
        """Return the highest-confidence GRN reference, or None."""
        if self.grn_references:
            best = max(self.grn_references, key=lambda r: r.confidence)
            return best.ref_value
        return None


# ---------------------------------------------------------------------------
# Pattern banks
# ---------------------------------------------------------------------------

# (label_pattern, value_capture_pattern, confidence)
# label_pattern matches the keyword/label; value_capture is applied
# to the text immediately after the label.

_VALUE_PATTERN = r"[\s:;#\-]*([A-Za-z0-9][\w\-/\.]{2,30})"

_PO_PATTERNS: list[tuple[str, str, float]] = [
    # Strong signals — dedicated PO label
    (r"\bP\.?O\.?\s*(?:No|Number|Num|#|Ref)", _VALUE_PATTERN, 0.95),
    (r"\bPurchase\s*Order\s*(?:No|Number|Num|#|Ref)?", _VALUE_PATTERN, 0.95),
    (r"\bOrder\s*(?:No|Number|Num|#|Ref)", _VALUE_PATTERN, 0.85),
    (r"\bBuyer['']?s?\s*Order\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.85),
    (r"\bYour\s*(?:Order|Ref)\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.80),
    # Arabic / Hindi
    (r"\bأمر\s*(?:شراء|الشراء)\s*(?:رقم)?", _VALUE_PATTERN, 0.90),
    (r"\bक्रय\s*आदेश\s*(?:संख्या)?", _VALUE_PATTERN, 0.90),
    # German / French / Spanish
    (r"\bBestellnummer\b", _VALUE_PATTERN, 0.90),
    (r"\bCommande\s*(?:No|N°|Numéro)?", _VALUE_PATTERN, 0.90),
    (r"\bOrden\s*de\s*Compra\s*(?:No)?", _VALUE_PATTERN, 0.90),
    # Standalone PO reference (weaker)
    (r"\bPO\b", _VALUE_PATTERN, 0.70),
]

_GRN_PATTERNS: list[tuple[str, str, float]] = [
    (r"\bGRN\s*(?:No|Number|Num|#|Ref)?", _VALUE_PATTERN, 0.95),
    (r"\bGoods\s*Receip?t\s*(?:Note|Number|No|#)", _VALUE_PATTERN, 0.95),
    (r"\bMaterial\s*Receip?t\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.90),
    (r"\bGR\s*(?:No|Number|#)", _VALUE_PATTERN, 0.85),
    (r"\bReceiving\s*(?:Report|No|Number|#)", _VALUE_PATTERN, 0.85),
    (r"\bInward\s*(?:No|Number|#|Entry)", _VALUE_PATTERN, 0.80),
    (r"\bDelivery\s*(?:Receipt|No|Number|#)", _VALUE_PATTERN, 0.80),
    # Arabic
    (r"\bإيصال\s*(?:استلام|البضائع)\s*(?:رقم)?", _VALUE_PATTERN, 0.90),
]

_CONTRACT_PATTERNS: list[tuple[str, str, float]] = [
    (r"\bContract\s*(?:No|Number|Num|#|Ref|Id)?", _VALUE_PATTERN, 0.90),
    (r"\bAgreement\s*(?:No|Number|#|Ref)?", _VALUE_PATTERN, 0.85),
    (r"\bFrame(?:work)?\s*(?:Agreement|Contract)\s*(?:No|#)?", _VALUE_PATTERN, 0.90),
    (r"\bBlanket\s*(?:Order|Agreement)\s*(?:No|#)?", _VALUE_PATTERN, 0.85),
    (r"\bScheduling\s*Agreement\s*(?:No|#)?", _VALUE_PATTERN, 0.85),
    (r"\bSA\s*(?:No|Number|#)", _VALUE_PATTERN, 0.80),
    # Arabic
    (r"\bعقد\s*(?:رقم)?", _VALUE_PATTERN, 0.85),
    # German / French
    (r"\bVertragsnummer\b", _VALUE_PATTERN, 0.90),
    (r"\bContrat\s*(?:No|N°)?", _VALUE_PATTERN, 0.90),
]

_SHIPMENT_PATTERNS: list[tuple[str, str, float]] = [
    (r"\bShipment\s*(?:No|Number|#|Ref|Id)?", _VALUE_PATTERN, 0.90),
    (r"\bBill\s*of\s*Lading\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.90),
    (r"\bB/?L\s*(?:No|Number|#)", _VALUE_PATTERN, 0.90),
    (r"\bAWB\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.90),
    (r"\bAir\s*Way\s*Bill\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.90),
    (r"\bTracking\s*(?:No|Number|#|Id)", _VALUE_PATTERN, 0.85),
    (r"\bConsignment\s*(?:No|Number|#|Note)", _VALUE_PATTERN, 0.85),
    (r"\bWaybill\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.85),
    (r"\bLR\s*(?:No|Number|#)", _VALUE_PATTERN, 0.80),
    (r"\bLorry\s*Receipt\s*(?:No|Number|#)?", _VALUE_PATTERN, 0.85),
    # Arabic
    (r"\bرقم\s*(?:الشحنة|الشحن)\b", _VALUE_PATTERN, 0.85),
    # German / French
    (r"\bFrachtbrief(?:nummer)?\b", _VALUE_PATTERN, 0.85),
    (r"\bConnaissement\s*(?:No|N°)?", _VALUE_PATTERN, 0.90),
]


class RelationshipExtractor:
    """
    Extracts document cross-references from OCR text.

    Finds PO numbers, GRN references, contract numbers, and shipment
    references using regex pattern banks.  No country-specific logic —
    multilingual patterns cover common international formats.
    """

    # Context window around a match for the source_snippet
    _SNIPPET_RADIUS = 60

    @classmethod
    def extract(cls, ocr_text: str) -> RelationshipResult:
        """
        Extract all cross-references from OCR text.

        Returns RelationshipResult with deduplicated references per type.
        """
        if not ocr_text or not ocr_text.strip():
            return RelationshipResult()

        result = RelationshipResult()

        result.po_numbers = cls._scan(ocr_text, "PO", _PO_PATTERNS)
        result.grn_references = cls._scan(ocr_text, "GRN", _GRN_PATTERNS)
        result.contract_references = cls._scan(
            ocr_text, "CONTRACT", _CONTRACT_PATTERNS,
        )
        result.shipment_references = cls._scan(
            ocr_text, "SHIPMENT", _SHIPMENT_PATTERNS,
        )

        result.total_found = (
            len(result.po_numbers)
            + len(result.grn_references)
            + len(result.contract_references)
            + len(result.shipment_references)
        )

        return result

    @classmethod
    def _scan(
        cls,
        text: str,
        ref_type: str,
        patterns: list[tuple[str, str, float]],
    ) -> list[DocumentReference]:
        """
        Scan text with a pattern bank and return deduplicated references.
        """
        seen_values: set[str] = set()
        refs: list[DocumentReference] = []

        for label_rx, value_rx, base_confidence in patterns:
            combined = label_rx + value_rx
            for m in re.finditer(combined, text, re.IGNORECASE):
                # The value is in the first capture group
                if m.lastindex and m.lastindex >= 1:
                    raw_value = m.group(1).strip()
                else:
                    continue

                # Clean up trailing punctuation
                raw_value = raw_value.rstrip(".,;:)")

                # Skip if too short or looks like a generic word
                if len(raw_value) < 2:
                    continue
                norm_val = raw_value.upper()
                if norm_val in seen_values:
                    continue
                seen_values.add(norm_val)

                # Build snippet
                start = max(0, m.start() - cls._SNIPPET_RADIUS)
                end = min(len(text), m.end() + cls._SNIPPET_RADIUS)
                snippet = text[start:end].replace("\n", " ").strip()

                refs.append(
                    DocumentReference(
                        ref_type=ref_type,
                        ref_value=raw_value,
                        confidence=base_confidence,
                        source_snippet=snippet,
                    ),
                )

        # Sort by confidence descending
        refs.sort(key=lambda r: -r.confidence)
        return refs
