"""
EvidenceCaptureService — Extracts and persists provenance evidence for fields.

Captures:
- Text snippets from OCR output
- Page numbers
- Bounding boxes (when available from OCR engine)
- Extraction method attribution

Maps evidence to field_code and persists as ExtractionEvidence records.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from apps.extraction_core.models import ExtractionEvidence, ExtractionRun
from apps.extraction_core.services.output_contract import (
    EvidenceRecord,
    ExtractionOutputContract,
    FieldValue,
)

logger = logging.getLogger(__name__)

# Maximum snippet length
MAX_SNIPPET_LEN = 200


class EvidenceCaptureService:
    """
    Captures and persists extraction evidence for audit and traceability.
    """

    @classmethod
    def capture_from_output(
        cls,
        extraction_run: ExtractionRun,
        output: ExtractionOutputContract,
        ocr_text: str = "",
        page_boundaries: Optional[list[tuple[int, int]]] = None,
    ) -> list[ExtractionEvidence]:
        """
        Walk the extraction output and create ExtractionEvidence records
        for every field that has evidence snippets.

        Parameters
        ----------
        extraction_run : ExtractionRun
            The run to attach evidence to.
        output : ExtractionOutputContract
            The extraction output with field values.
        ocr_text : str
            Full OCR text for snippet lookup.
        page_boundaries : list of (start_offset, end_offset)
            Character offset boundaries per page for page_number mapping.

        Returns
        -------
        list[ExtractionEvidence]
            Created evidence records.
        """
        evidence_items: list[dict] = []

        # Header fields
        for field_code, fv in output.header.items():
            evidence_items.extend(
                cls._build_evidence_dicts(
                    field_code, fv, ocr_text, page_boundaries,
                )
            )

        # Reference fields
        for field_code, fv in output.references.items():
            evidence_items.extend(
                cls._build_evidence_dicts(
                    field_code, fv, ocr_text, page_boundaries,
                )
            )

        # Commercial terms
        for field_code, fv in output.commercial_terms.items():
            evidence_items.extend(
                cls._build_evidence_dicts(
                    field_code, fv, ocr_text, page_boundaries,
                )
            )

        # Tax fields
        for field_code, fv in output.tax.tax_fields.items():
            evidence_items.extend(
                cls._build_evidence_dicts(
                    field_code, fv, ocr_text, page_boundaries,
                )
            )

        # Party fields
        for role, party_data in [
            ("supplier", output.parties.supplier),
            ("buyer", output.parties.buyer),
            ("ship_to", output.parties.ship_to),
            ("bill_to", output.parties.bill_to),
        ]:
            for field_code, fv in party_data.items():
                evidence_items.extend(
                    cls._build_evidence_dicts(
                        f"{role}.{field_code}", fv, ocr_text, page_boundaries,
                    )
                )

        # Line items
        for li in output.line_items:
            for field_code, fv in li.fields.items():
                evidence_items.extend(
                    cls._build_evidence_dicts(
                        field_code,
                        fv,
                        ocr_text,
                        page_boundaries,
                        line_item_index=li.index,
                    )
                )

        # Also collect from output.evidence list
        for er in output.evidence:
            evidence_items.append({
                "field_code": er.field_code,
                "snippet": er.snippet[:MAX_SNIPPET_LEN],
                "page_number": er.page_number,
                "bounding_box": er.bounding_box,
                "extraction_method": er.extraction_method,
                "confidence": er.confidence,
                "line_item_index": None,
            })

        # Deduplicate by (field_code, snippet)
        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for item in evidence_items:
            key = (item["field_code"], item.get("snippet", "")[:50])
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        # Bulk create
        records = [
            ExtractionEvidence(
                extraction_run=extraction_run,
                field_code=item["field_code"],
                page_number=item.get("page_number"),
                snippet=item.get("snippet", ""),
                bounding_box=item.get("bounding_box"),
                extraction_method=item.get("extraction_method", ""),
                confidence=item.get("confidence"),
                line_item_index=item.get("line_item_index"),
            )
            for item in deduped
        ]

        if records:
            ExtractionEvidence.objects.bulk_create(records)
            logger.info(
                "Captured %d evidence records for ExtractionRun #%s",
                len(records),
                extraction_run.pk,
            )

        return records

    @classmethod
    def _build_evidence_dicts(
        cls,
        field_code: str,
        fv: FieldValue,
        ocr_text: str,
        page_boundaries: Optional[list[tuple[int, int]]],
        line_item_index: int | None = None,
    ) -> list[dict]:
        """Build evidence dict(s) from a FieldValue."""
        results = []
        snippet = fv.evidence or ""
        page_number = fv.page_number

        # If we have the snippet but no page number, try to locate it
        if snippet and not page_number and ocr_text and page_boundaries:
            page_number = cls._find_page_for_snippet(
                snippet, ocr_text, page_boundaries,
            )

        # If we have a value but no snippet, try to find it in the OCR text
        if not snippet and fv.value and ocr_text:
            snippet = cls._find_snippet_in_text(str(fv.value), ocr_text)
            if snippet and not page_number and page_boundaries:
                page_number = cls._find_page_for_snippet(
                    snippet, ocr_text, page_boundaries,
                )

        if snippet or fv.value:
            results.append({
                "field_code": field_code,
                "snippet": snippet[:MAX_SNIPPET_LEN],
                "page_number": page_number,
                "bounding_box": None,
                "extraction_method": fv.extraction_method,
                "confidence": fv.confidence,
                "line_item_index": line_item_index,
            })

        return results

    @classmethod
    def _find_snippet_in_text(cls, value: str, ocr_text: str) -> str:
        """Find a context snippet surrounding the value in OCR text."""
        if not value or not ocr_text:
            return ""

        escaped = re.escape(str(value).strip())
        match = re.search(escaped, ocr_text, re.IGNORECASE)
        if not match:
            return ""

        start = max(0, match.start() - 30)
        end = min(len(ocr_text), match.end() + 30)
        return ocr_text[start:end].strip()

    @classmethod
    def _find_page_for_snippet(
        cls,
        snippet: str,
        ocr_text: str,
        page_boundaries: list[tuple[int, int]],
    ) -> int | None:
        """Determine which page a snippet comes from based on char offsets."""
        if not snippet or not ocr_text or not page_boundaries:
            return None

        idx = ocr_text.find(snippet[:50])
        if idx < 0:
            return None

        for page_num, (start, end) in enumerate(page_boundaries, start=1):
            if start <= idx < end:
                return page_num

        return None
