"""QuotationExtractionAgent — parse supplier proposal PDFs into structured quotation data."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from apps.agents.services.llm_client import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


class QuotationExtractionAgent:
    """Lightweight agent for extracting structured quotation data from OCR text.

    Uses a simple prompt → response pattern (no tool-calling loop needed).
    Called by QuotationDocumentPrefillService when semantic extraction is needed.
    """

    SYSTEM_PROMPT = (
        "You are a procurement quotation extraction specialist. "
        "Given OCR text from a supplier proposal or quotation document, "
        "extract all identifiable quotation data including header, line items, and commercial terms.\n\n"
        "Respond ONLY with valid JSON in this format:\n"
        "{\n"
        '  "confidence": 0.0-1.0,\n'
        '  "vendor_name": {"value": "...", "confidence": 0.9},\n'
        '  "quotation_number": {"value": "...", "confidence": 0.9},\n'
        '  "quotation_date": {"value": "YYYY-MM-DD", "confidence": 0.8},\n'
        '  "currency": {"value": "USD", "confidence": 0.9},\n'
        '  "total_amount": {"value": "12345.67", "confidence": 0.8},\n'
        '  "subtotal": {"value": "11000.00", "confidence": 0.7},\n'
        '  "taxes": {"value": "1345.67", "confidence": 0.7},\n'
        '  "warranty_terms": {"value": "...", "confidence": 0.6},\n'
        '  "payment_terms": {"value": "...", "confidence": 0.7},\n'
        '  "delivery_terms": {"value": "...", "confidence": 0.6},\n'
        '  "lead_time": {"value": "...", "confidence": 0.6},\n'
        '  "exclusions": {"value": "...", "confidence": 0.5},\n'
        '  "installation_terms": {"value": "...", "confidence": 0.5},\n'
        '  "support_terms": {"value": "...", "confidence": 0.5},\n'
        '  "testing_terms": {"value": "...", "confidence": 0.5},\n'
        '  "line_items": [\n'
        "    {\n"
        '      "line_number": 1,\n'
        '      "description": "Item description",\n'
        '      "category_code": "CATEGORY",\n'
        '      "quantity": 1,\n'
        '      "unit": "EA",\n'
        '      "unit_rate": 100.00,\n'
        '      "total_amount": 100.00,\n'
        '      "brand": "Brand Name",\n'
        '      "model": "Model Number",\n'
        '      "confidence": 0.8\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Extract vendor name in English characters (transliterate if non-English)\n"
        "- Parse all dates as YYYY-MM-DD\n"
        "- Monetary values should be numeric (no currency symbols)\n"
        "- Extract ALL line items from tables, BOQ sections, pricing schedules, licensing tables, and cost breakdowns — scan the ENTIRE document\n"
        "- For service proposals, extract each service/license/resource as a separate line item with its rate and quantity\n"
        "- Detect brand and model from line item descriptions where possible\n"
        "- Extract commercial terms verbatim from term sections\n"
        "- Set confidence per field based on extraction certainty\n"
        "- Omit fields that cannot be extracted from the document"
    )

    @staticmethod
    def extract(ocr_text: str) -> Dict[str, Any]:
        """Extract structured quotation data from OCR text.

        Returns:
            Structured extraction dict with header fields, line items, and commercial terms.
        """
        llm = LLMClient()

        user_msg = (
            "Extract complete structured quotation data from this supplier document.\n"
            "Pay special attention to pricing tables, licensing sections, BOQ, and cost breakdowns anywhere in the document.\n\n"
            f"--- DOCUMENT TEXT ---\n{ocr_text[:60000]}\n--- END ---"
        )

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=QuotationExtractionAgent.SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ],
            )
            result = json.loads(response.content)
            line_count = len(result.get("line_items", []))
            logger.info(
                "QuotationExtractionAgent: extracted %d line items, confidence=%.2f",
                line_count, result.get("confidence", 0),
            )
            return result
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("QuotationExtractionAgent LLM call failed: %s", exc)
            return {
                "confidence": 0,
                "error": str(exc),
            }
