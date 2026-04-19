"""Entity linking service for AP and Procurement references inside email content."""
from __future__ import annotations

import re


class EntityLinkingService:
    """Deterministic extraction of business entity references from text."""

    _CASE_PATTERN = re.compile(r"\bCASE[-_\s]?(\d{3,10})\b", re.IGNORECASE)
    _REQUEST_PATTERN = re.compile(r"\bPR[-_\s]?(\d{3,12})\b", re.IGNORECASE)
    _QUOTE_PATTERN = re.compile(r"\bQUOT[-_\s]?(\d{3,12})\b", re.IGNORECASE)

    @classmethod
    def infer_entity(cls, subject: str, body_text: str) -> dict:
        haystack = f"{subject or ''}\n{body_text or ''}"
        case_match = cls._CASE_PATTERN.search(haystack)
        if case_match:
            return {"entity_type": "AP_CASE", "entity_id": int(case_match.group(1))}

        request_match = cls._REQUEST_PATTERN.search(haystack)
        if request_match:
            return {"entity_type": "PROCUREMENT_REQUEST", "entity_id": int(request_match.group(1))}

        quote_match = cls._QUOTE_PATTERN.search(haystack)
        if quote_match:
            return {"entity_type": "SUPPLIER_QUOTATION", "entity_id": int(quote_match.group(1))}

        return {"entity_type": "", "entity_id": None}
