"""
DocumentIntelligenceService — Pre-extraction document analysis.

Orchestrates the document intelligence layer that runs *before* field
extraction.  Produces a structured ``DocumentIntelligenceResult``
containing:

    - Document type classification (invoice, credit note, etc.)
    - Cross-references (PO, GRN, contract, shipment)
    - Party information (supplier, buyer, ship-to, bill-to)

The result is attached to the ``ExtractionResult`` and persisted with
the ``ExtractionDocument``.

Design principles:
    - Country-agnostic: no hardcoded country logic
    - Jurisdiction-aware: accepts country/regime context for downstream use
    - Modular: each sub-service is independently testable
    - Deterministic-first: regex/heuristic based, no LLM dependency
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.utils import timezone

from apps.extraction_core.services.document_classifier import (
    ClassificationResult,
    DocumentTypeClassifier,
)
from apps.extraction_core.services.party_extractor import (
    PartyExtractionResult,
    PartyExtractor,
)
from apps.extraction_core.services.relationship_extractor import (
    RelationshipExtractor,
    RelationshipResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class DocumentIntelligenceResult:
    """
    Structured metadata block produced by the document intelligence layer.

    Attached to ExtractionResult and persisted in extracted_data_json.
    """

    classification: ClassificationResult = field(
        default_factory=ClassificationResult,
    )
    relationships: RelationshipResult = field(
        default_factory=RelationshipResult,
    )
    parties: PartyExtractionResult = field(
        default_factory=PartyExtractionResult,
    )
    #: Country code from jurisdiction resolution (passed through)
    country_code: str = ""
    #: Regime code from jurisdiction resolution (passed through)
    regime_code: str = ""
    #: Duration of the intelligence layer in milliseconds
    duration_ms: int = 0
    #: Any warnings produced during analysis
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "classification": self.classification.to_dict(),
            "relationships": self.relationships.to_dict(),
            "parties": self.parties.to_dict(),
            "country_code": self.country_code,
            "regime_code": self.regime_code,
            "duration_ms": self.duration_ms,
            "warnings": self.warnings,
        }

    @property
    def detected_document_type(self) -> str:
        """Best-match document type from classification."""
        return self.classification.document_type

    @property
    def primary_po_number(self) -> Optional[str]:
        """Highest-confidence PO number, if any."""
        return self.relationships.primary_po

    @property
    def primary_grn_reference(self) -> Optional[str]:
        """Highest-confidence GRN reference, if any."""
        return self.relationships.primary_grn

    @property
    def supplier_name(self) -> str:
        """Primary supplier name, empty if not found."""
        p = self.parties.primary_supplier
        return p.name if p else ""

    @property
    def buyer_name(self) -> str:
        """Primary buyer name, empty if not found."""
        p = self.parties.primary_buyer
        return p.name if p else ""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class DocumentIntelligenceService:
    """
    Pre-extraction document intelligence layer.

    Runs classification, relationship extraction, and party extraction
    in sequence and returns a unified ``DocumentIntelligenceResult``.

    Integrates with the jurisdiction-aware pipeline — accepts
    ``country_code`` and ``regime_code`` from the upstream jurisdiction
    resolution so downstream consumers have full context.

    All sub-services are country-agnostic and reusable across
    jurisdictions.
    """

    @classmethod
    def analyze(
        cls,
        ocr_text: str,
        *,
        country_code: str = "",
        regime_code: str = "",
    ) -> DocumentIntelligenceResult:
        """
        Run the full document intelligence analysis.

        Args:
            ocr_text:       Raw OCR text of the document.
            country_code:   Resolved country code (from jurisdiction layer).
            regime_code:    Resolved regime code (from jurisdiction layer).

        Returns:
            DocumentIntelligenceResult with classification, relationships,
            and party information.
        """
        start = timezone.now()
        result = DocumentIntelligenceResult(
            country_code=country_code,
            regime_code=regime_code,
        )

        if not ocr_text or not ocr_text.strip():
            result.warnings.append("Empty OCR text — skipping intelligence")
            return result

        # ── 1. Document type classification ───────────────────────────
        try:
            result.classification = DocumentTypeClassifier.classify(ocr_text)
            if result.classification.is_ambiguous:
                result.warnings.append(
                    f"Ambiguous document type: top scores are close. "
                    f"Best guess: {result.classification.document_type}"
                )
            logger.info(
                "Document classified as %s (confidence=%.2f)",
                result.classification.document_type,
                result.classification.confidence,
            )
        except Exception:
            logger.exception("Document classification failed")
            result.warnings.append("Document classification failed")

        # ── 2. Relationship extraction ────────────────────────────────
        try:
            result.relationships = RelationshipExtractor.extract(ocr_text)
            logger.info(
                "Found %d cross-references (PO=%d, GRN=%d, Contract=%d, "
                "Shipment=%d)",
                result.relationships.total_found,
                len(result.relationships.po_numbers),
                len(result.relationships.grn_references),
                len(result.relationships.contract_references),
                len(result.relationships.shipment_references),
            )
        except Exception:
            logger.exception("Relationship extraction failed")
            result.warnings.append("Relationship extraction failed")

        # ── 3. Party extraction ───────────────────────────────────────
        try:
            result.parties = PartyExtractor.extract(ocr_text)
            total_parties = len(result.parties.all_parties)
            logger.info(
                "Found %d parties (suppliers=%d, buyers=%d, "
                "ship_to=%d, bill_to=%d)",
                total_parties,
                len(result.parties.suppliers),
                len(result.parties.buyers),
                len(result.parties.ship_to),
                len(result.parties.bill_to),
            )
        except Exception:
            logger.exception("Party extraction failed")
            result.warnings.append("Party extraction failed")

        elapsed = (timezone.now() - start).total_seconds() * 1000
        result.duration_ms = int(elapsed)

        return result
