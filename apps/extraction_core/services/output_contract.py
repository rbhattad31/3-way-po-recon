"""
Standard Extraction Output Contract — Structured JSON output for all extractions.

Defines the canonical output shape that every extraction pipeline run MUST
produce, regardless of document type, jurisdiction, or extraction method.

The contract ensures downstream consumers (APIs, reconciliation, review UI)
receive a uniform structure with confidence and evidence per field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MetaBlock:
    """Extraction run metadata."""
    extraction_run_id: int | None = None
    document_id: int | None = None
    document_type: str = ""
    extraction_method: str = ""
    schema_code: str = ""
    schema_version: str = ""
    prompt_code: str = ""
    prompt_version: str = ""
    country_code: str = ""
    regime_code: str = ""
    jurisdiction_source: str = ""
    timestamp: str = ""
    duration_ms: int | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class FieldValue:
    """Single extracted field with confidence and evidence."""
    value: Any = None
    confidence: float | None = None
    evidence: str = ""
    extraction_method: str = ""
    page_number: int | None = None
    is_corrected: bool = False

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "evidence": self.evidence,
            "extraction_method": self.extraction_method,
            "page_number": self.page_number,
            "is_corrected": self.is_corrected,
        }


@dataclass
class PartiesBlock:
    """Extracted business parties."""
    supplier: dict[str, FieldValue] = field(default_factory=dict)
    buyer: dict[str, FieldValue] = field(default_factory=dict)
    ship_to: dict[str, FieldValue] = field(default_factory=dict)
    bill_to: dict[str, FieldValue] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "supplier": {k: v.to_dict() for k, v in self.supplier.items()},
            "buyer": {k: v.to_dict() for k, v in self.buyer.items()},
            "ship_to": {k: v.to_dict() for k, v in self.ship_to.items()},
            "bill_to": {k: v.to_dict() for k, v in self.bill_to.items()},
        }


@dataclass
class TaxBlock:
    """Extracted tax information."""
    tax_fields: dict[str, FieldValue] = field(default_factory=dict)
    tax_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "fields": {k: v.to_dict() for k, v in self.tax_fields.items()},
            "summary": self.tax_summary,
        }


@dataclass
class LineItemRow:
    """Single line item with per-field values."""
    index: int = 0
    fields: dict[str, FieldValue] = field(default_factory=dict)
    confidence: float | None = None
    page_number: int | None = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "page_number": self.page_number,
        }


@dataclass
class EvidenceRecord:
    """Evidence for a single field."""
    field_code: str = ""
    snippet: str = ""
    page_number: int | None = None
    bounding_box: list | None = None
    extraction_method: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict:
        d = {
            "field_code": self.field_code,
            "snippet": self.snippet,
            "page_number": self.page_number,
            "extraction_method": self.extraction_method,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
        }
        if self.bounding_box:
            d["bounding_box"] = self.bounding_box
        return d


@dataclass
class WarningItem:
    """A warning or info message from extraction."""
    code: str = ""
    message: str = ""
    field_code: str = ""
    severity: str = "WARNING"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ExtractionOutputContract:
    """
    Standard output contract for all extraction results.

    Every extraction pipeline run MUST produce this structure. Consumers
    can rely on the shape being consistent across jurisdictions and
    document types.

    Shape::

        {
            "meta": {...},
            "header": {field_code: FieldValue, ...},
            "parties": {...},
            "references": {field_code: FieldValue, ...},
            "commercial_terms": {field_code: FieldValue, ...},
            "tax": {...},
            "line_items": [...],
            "derived_flags": {...},
            "evidence": [...],
            "warnings": [...]
        }
    """

    meta: MetaBlock = field(default_factory=MetaBlock)
    header: dict[str, FieldValue] = field(default_factory=dict)
    parties: PartiesBlock = field(default_factory=PartiesBlock)
    references: dict[str, FieldValue] = field(default_factory=dict)
    commercial_terms: dict[str, FieldValue] = field(default_factory=dict)
    tax: TaxBlock = field(default_factory=TaxBlock)
    line_items: list[LineItemRow] = field(default_factory=list)
    derived_flags: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    warnings: list[WarningItem] = field(default_factory=list)
    # Overall confidence
    overall_confidence: float = 0.0
    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    # Errors (extraction failures)
    errors: list[str] = field(default_factory=list)
    resolved: bool = False
    # Backward-compatible QR payload used by extraction console (_qr block)
    qr_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to the canonical JSON structure."""
        return {
            "meta": self.meta.to_dict(),
            "header": {k: v.to_dict() for k, v in self.header.items()},
            "parties": self.parties.to_dict(),
            "references": {k: v.to_dict() for k, v in self.references.items()},
            "commercial_terms": {k: v.to_dict() for k, v in self.commercial_terms.items()},
            "tax": self.tax.to_dict(),
            "line_items": [li.to_dict() for li in self.line_items],
            "derived_flags": self.derived_flags,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": [w.to_dict() for w in self.warnings],
            "overall_confidence": round(self.overall_confidence, 4),
            "requires_review": self.requires_review,
            "review_reasons": self.review_reasons,
            "errors": self.errors,
            "resolved": self.resolved,
            **({"_qr": self.qr_data} if self.qr_data else {}),
        }

    def get_field_value(self, field_code: str) -> FieldValue | None:
        """Look up a header/reference/commercial_terms field by code."""
        for section in (self.header, self.references, self.commercial_terms):
            if field_code in section:
                return section[field_code]
        for k, v in self.tax.tax_fields.items():
            if k == field_code:
                return v
        return None

    def get_all_field_codes(self) -> set[str]:
        """Return all field codes present in the output."""
        codes: set[str] = set()
        codes.update(self.header.keys())
        codes.update(self.references.keys())
        codes.update(self.commercial_terms.keys())
        codes.update(self.tax.tax_fields.keys())
        for party_section in (
            self.parties.supplier,
            self.parties.buyer,
            self.parties.ship_to,
            self.parties.bill_to,
        ):
            codes.update(party_section.keys())
        for li in self.line_items:
            codes.update(li.fields.keys())
        return codes
