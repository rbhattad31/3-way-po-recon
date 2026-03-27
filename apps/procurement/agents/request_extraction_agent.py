"""RequestExtractionAgent — parse RFQ / requirement PDFs into structured procurement request data."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from apps.agents.services.llm_client import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


class RequestExtractionAgent:
    """Lightweight agent for extracting structured procurement request data from OCR text.

    Uses a simple prompt → response pattern (no tool-calling loop needed).
    Called by RequestDocumentPrefillService when semantic extraction is needed.
    """

    SYSTEM_PROMPT = (
        "You are a procurement document extraction specialist. "
        "Given OCR text from a procurement document (RFQ, requirement note, BOQ, specification, scope document), "
        "extract all identifiable structured data.\n\n"
        "Respond ONLY with valid JSON containing these fields (omit if not found):\n"
        "{\n"
        '  "confidence": 0.0-1.0 (overall extraction confidence),\n'
        '  "document_type_detected": "RFQ|REQUIREMENT_NOTE|SPECIFICATION|BOQ|OTHER",\n'
        '  "title": "...",\n'
        '  "description": "...",\n'
        '  "domain_code": "HVAC|IT|FACILITIES|ELECTRICAL|PLUMBING|CIVIL|MECHANICAL|FURNITURE|SECURITY|TELECOM|MEDICAL|GENERAL",\n'
        '  "schema_code": "suggested schema identifier if detectable",\n'
        '  "request_type": "RECOMMENDATION|BENCHMARK|BOTH",\n'
        '  "geography_country": "...",\n'
        '  "geography_city": "...",\n'
        '  "currency": "USD",\n'
        '  "attributes": [\n'
        '    {"key": "budget", "value": "...", "confidence": 0.8},\n'
        '    {"key": "deadline", "value": "YYYY-MM-DD", "confidence": 0.7},\n'
        '    {"key": "quantity", "value": "...", "confidence": 0.9},\n'
        '    {"key": "specifications", "value": "...", "confidence": 0.8}\n'
        "  ],\n"
        '  "scope_categories": ["category1", "category2"],\n'
        '  "compliance_hints": ["hint1", "hint2"]\n'
        "}\n\n"
        "Rules:\n"
        "- Set confidence per field (0.0 = guess, 1.0 = explicitly stated in document)\n"
        "- Extract budget, timeline, quantity, technical specifications as attributes\n"
        "- Detect domain from terminology and context\n"
        "- Geography from addresses, location references, project sites\n"
        "- Include compliance/regulatory hints if mentioned\n"
        "- Include scope categories from section headings or BOQ divisions\n"
        "- All text output must be in English"
    )

    @staticmethod
    def extract(
        ocr_text: str,
        source_document_type: str = "",
        domain_hint: str = "",
    ) -> Dict[str, Any]:
        """Extract structured request data from OCR text.

        Args:
            ocr_text: Raw OCR text from the document.
            source_document_type: Hint about document type (RFQ, BOQ, etc.).
            domain_hint: Optional domain hint from the user.

        Returns:
            Structured extraction dict.
        """
        llm = LLMClient()

        context_parts = []
        if source_document_type:
            context_parts.append(f"Document type: {source_document_type}")
        if domain_hint:
            context_parts.append(f"Domain hint: {domain_hint}")

        context_line = "\n".join(context_parts)
        user_msg = (
            f"Extract structured procurement request data from this document.\n"
            f"{context_line}\n\n"
            f"--- DOCUMENT TEXT ---\n{ocr_text[:12000]}\n--- END ---"
        )

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=RequestExtractionAgent.SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ],
            )
            result = json.loads(response.content)
            logger.info(
                "RequestExtractionAgent: extracted %d fields, confidence=%.2f",
                len(result.get("attributes", [])) + 6,  # core + attributes
                result.get("confidence", 0),
            )
            return result
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("RequestExtractionAgent LLM call failed: %s", exc)
            return {
                "confidence": 0,
                "error": str(exc),
            }
