"""QuotationDocumentPrefillService — extract structured data from supplier proposal/quotation PDFs."""
from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings

from apps.core.decorators import observed_service
from apps.core.enums import ExtractionStatus, PrefillStatus
from apps.procurement.models import SupplierQuotation
from apps.procurement.services.prefill.attribute_mapping_service import AttributeMappingService
from apps.procurement.services.prefill.prefill_status_service import PrefillStatusService

logger = logging.getLogger(__name__)


class QuotationDocumentPrefillService:
    """Accept a proposal / quotation PDF, run OCR + LLM extraction, return editable quotation draft payload."""

    @staticmethod
    @observed_service("procurement.quotation_prefill")
    def run_prefill(quotation: SupplierQuotation) -> dict[str, Any]:
        """Execute the full prefill pipeline for a supplier quotation.

        Steps:
          1. OCR the uploaded document
          2. LLM-based structured extraction (header + line items + commercial terms)
          3. Map extracted fields to quotation schema
          4. Classify field confidence
          5. Store prefill payload on the quotation

        Returns the prefill payload dict suitable for UI rendering.
        """
        if not quotation.uploaded_document or not quotation.uploaded_document.file:
            raise ValueError("No source document attached to the quotation")

        PrefillStatusService.mark_quotation_in_progress(quotation)

        # Also update extraction status
        quotation.extraction_status = ExtractionStatus.IN_PROGRESS
        quotation.save(update_fields=["extraction_status", "updated_at"])

        try:
            start = time.time()

            # Step 1: OCR
            file_path = quotation.uploaded_document.file.path
            ocr_text = QuotationDocumentPrefillService._ocr_document(file_path)

            if not ocr_text.strip():
                PrefillStatusService.mark_quotation_failed(quotation)
                quotation.extraction_status = ExtractionStatus.FAILED
                quotation.save(update_fields=["extraction_status", "updated_at"])
                return {"success": False, "error": "OCR returned no text from the document"}

            logger.info(
                "Quotation %s: OCR completed, %d chars extracted",
                quotation.pk, len(ocr_text),
            )

            # Step 2: LLM extraction
            raw_extraction = QuotationDocumentPrefillService._extract_quotation_data(ocr_text)
            overall_confidence = float(raw_extraction.get("confidence", 0.5))

            # Step 3: Map fields
            mapped = AttributeMappingService.map_quotation_fields(raw_extraction)

            # Step 4: Classify confidence
            confidence_breakdown = AttributeMappingService.classify_confidence(
                mapped["header_fields"],
            )

            elapsed_ms = int((time.time() - start) * 1000)

            # Step 5: Build prefill payload
            prefill_payload = {
                "success": True,
                "header_fields": mapped["header_fields"],
                "commercial_terms": mapped["commercial_terms"],
                "line_items": mapped["line_items"],
                "unmapped": mapped["unmapped"],
                "confidence_breakdown": confidence_breakdown,
                "overall_confidence": overall_confidence,
                "extraction_duration_ms": elapsed_ms,
                "header_field_count": len(mapped["header_fields"]),
                "line_item_count": len(mapped["line_items"]),
                "commercial_terms_count": len(mapped["commercial_terms"]),
                "low_confidence_count": len(confidence_breakdown.get("low_confidence", [])),
            }

            PrefillStatusService.mark_quotation_completed(
                quotation, confidence=overall_confidence, payload=prefill_payload,
            )

            # Update extraction status
            quotation.extraction_status = ExtractionStatus.COMPLETED
            quotation.save(update_fields=["extraction_status", "updated_at"])

            logger.info(
                "Quotation %s: prefill completed in %dms, confidence=%.2f, "
                "header_fields=%d, line_items=%d",
                quotation.pk, elapsed_ms, overall_confidence,
                len(mapped["header_fields"]), len(mapped["line_items"]),
            )
            return prefill_payload

        except Exception as exc:
            logger.exception("Quotation %s: prefill failed", quotation.pk)
            PrefillStatusService.mark_quotation_failed(quotation)
            quotation.extraction_status = ExtractionStatus.FAILED
            quotation.save(update_fields=["extraction_status", "updated_at"])
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _ocr_document(file_path: str) -> str:
        """Reuse the existing Azure Document Intelligence OCR."""
        from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter
        return InvoiceExtractionAdapter._ocr_document(file_path)

    @staticmethod
    def _extract_quotation_data(ocr_text: str) -> dict:
        """Use LLM to extract structured quotation data from OCR text."""
        from apps.agents.services.llm_client import LLMClient, LLMMessage

        system_prompt = (
            "You are a procurement quotation extraction assistant. "
            "Given OCR text from a supplier proposal or quotation document, "
            "extract structured quotation data including header, line items, and commercial terms.\n\n"
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
            '      "description": "...",\n'
            '      "category_code": "...",\n'
            '      "quantity": 1,\n'
            '      "unit": "EA",\n'
            '      "unit_rate": 100.00,\n'
            '      "total_amount": 100.00,\n'
            '      "brand": "...",\n'
            '      "model": "...",\n'
            '      "confidence": 0.8\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Extract vendor name EXACTLY as it appears (English transliteration if non-English)\n"
            "- Parse dates as YYYY-MM-DD format\n"
            "- Extract ALL line items with quantities, rates, and amounts\n"
            "- Line items may appear in pricing tables, BOQ sections, licensing tables, cost breakdowns, or commercial schedules — scan the ENTIRE document\n"
            "- For service proposals, extract each service/license/resource as a separate line item with its rate and quantity\n"
            "- Include brand/model if identifiable\n"
            "- Extract commercial terms (warranty, payment, delivery) when present\n"
            "- Set confidence 0.0-1.0 per field based on extraction certainty\n"
            "- Omit fields that cannot be extracted\n"
            "- Monetary values should be numeric (no currency symbols)"
        )

        user_msg = (
            "Extract structured quotation data from the following document text.\n"
            "Pay special attention to pricing tables, licensing sections, BOQ, and cost breakdowns anywhere in the document.\n\n"
            f"--- DOCUMENT TEXT ---\n{ocr_text[:60000]}\n--- END ---"
        )

        import json
        import re as _re
        llm = LLMClient(max_tokens=8192)
        response = llm.chat(
            messages=[
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_msg),
            ],
        )
        text = (response.content or "").strip()
        # Strip markdown code fences if present
        fence_match = _re.search(r"```(?:json)?\s*\n(.*?)\n?```", text, _re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        return json.loads(text)
