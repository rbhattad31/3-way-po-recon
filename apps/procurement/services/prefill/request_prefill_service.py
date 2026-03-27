"""RequestDocumentPrefillService — extract structured data from RFQ/requirement PDFs."""
from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings

from apps.core.decorators import observed_service
from apps.core.enums import PrefillStatus
from apps.procurement.models import ProcurementRequest
from apps.procurement.services.prefill.attribute_mapping_service import AttributeMappingService
from apps.procurement.services.prefill.prefill_status_service import PrefillStatusService

logger = logging.getLogger(__name__)


class RequestDocumentPrefillService:
    """Accept an RFQ / requirement PDF, run OCR + LLM extraction, return editable prefill payload."""

    @staticmethod
    @observed_service("procurement.request_prefill")
    def run_prefill(request: ProcurementRequest) -> dict[str, Any]:
        """Execute the full prefill pipeline for a procurement request.

        Steps:
          1. OCR the uploaded document
          2. LLM-based structured extraction
          3. Map extracted fields to request core fields + dynamic attributes
          4. Classify field confidence
          5. Store prefill payload on the request

        Returns the prefill payload dict suitable for UI rendering.
        """
        if not request.uploaded_document or not request.uploaded_document.file:
            raise ValueError("No source document attached to the request")

        PrefillStatusService.mark_request_in_progress(request)

        try:
            start = time.time()

            # Step 1: OCR
            file_path = request.uploaded_document.file.path
            ocr_text = RequestDocumentPrefillService._ocr_document(file_path)

            if not ocr_text.strip():
                PrefillStatusService.mark_request_failed(request)
                return {"success": False, "error": "OCR returned no text from the document"}

            logger.info(
                "Request %s: OCR completed, %d chars extracted",
                request.request_id, len(ocr_text),
            )

            # Step 2: LLM extraction
            raw_extraction = RequestDocumentPrefillService._extract_request_data(
                ocr_text, request.source_document_type,
            )
            overall_confidence = float(raw_extraction.get("confidence", 0.5))

            # Step 3: Map fields
            mapped = AttributeMappingService.map_request_fields(raw_extraction)

            # Step 4: Classify confidence
            confidence_breakdown = AttributeMappingService.classify_confidence(
                mapped["core_fields"],
            )

            elapsed_ms = int((time.time() - start) * 1000)

            # Step 5: Build prefill payload
            prefill_payload = {
                "success": True,
                "core_fields": mapped["core_fields"],
                "attributes": mapped["attributes"],
                "unmapped": mapped["unmapped"],
                "confidence_breakdown": confidence_breakdown,
                "overall_confidence": overall_confidence,
                "extraction_duration_ms": elapsed_ms,
                "field_count": len(mapped["core_fields"]) + len(mapped["attributes"]),
                "low_confidence_count": len(confidence_breakdown.get("low_confidence", [])),
            }

            PrefillStatusService.mark_request_completed(
                request, confidence=overall_confidence, payload=prefill_payload,
            )

            logger.info(
                "Request %s: prefill completed in %dms, confidence=%.2f, fields=%d",
                request.request_id, elapsed_ms, overall_confidence,
                prefill_payload["field_count"],
            )
            return prefill_payload

        except Exception as exc:
            logger.exception("Request %s: prefill failed", request.request_id)
            PrefillStatusService.mark_request_failed(request)
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _ocr_document(file_path: str) -> str:
        """Reuse the existing Azure Document Intelligence OCR."""
        from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter
        return InvoiceExtractionAdapter._ocr_document(file_path)

    @staticmethod
    def _extract_request_data(ocr_text: str, source_doc_type: str = "") -> dict:
        """Use LLM to extract structured procurement request data from OCR text."""
        from apps.agents.services.llm_client import LLMClient, LLMMessage

        system_prompt = (
            "You are a procurement document extraction assistant. "
            "Given OCR text from a procurement document (RFQ, requirement note, BOQ, specification), "
            "extract structured procurement request data.\n\n"
            "Respond ONLY with valid JSON in this format:\n"
            "{\n"
            '  "confidence": 0.0-1.0,\n'
            '  "title": "short project/RFQ title",\n'
            '  "description": "project scope summary (2-3 sentences)",\n'
            '  "domain_code": "SUGGESTED_DOMAIN",\n'
            '  "geography_country": "...",\n'
            '  "geography_city": "...",\n'
            '  "currency": "USD",\n'
            '  "requirements": [\n'
            '    {"key": "technical_requirement", "value": "Must support 240V 3-phase", "confidence": 0.9},\n'
            '    {"key": "specifications", "value": "IP65 rated, stainless steel", "confidence": 0.85},\n'
            '    {"key": "compliance", "value": "ISO 9001 certified vendors only", "confidence": 0.8}\n'
            "  ],\n"
            '  "attributes": [\n'
            '    {"key": "budget", "value": "50000 USD", "confidence": 0.8},\n'
            '    {"key": "deadline", "value": "2025-06-30", "confidence": 0.7},\n'
            '    {"key": "quantity", "value": "100", "confidence": 0.9},\n'
            '    {"key": "delivery_date", "value": "...", "confidence": 0.7},\n'
            '    {"key": "warranty", "value": "...", "confidence": 0.6}\n'
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Extract ALL identifiable fields with confidence scores (0.0-1.0)\n"
            "- Suggest a domain_code from: HVAC, IT, FACILITIES, ELECTRICAL, PLUMBING, "
            "CIVIL, MECHANICAL, FURNITURE, SECURITY, TELECOM, MEDICAL, GENERAL\n"
            "- Put technical requirements, specifications, compliance/standards, "
            "scope items, and acceptance criteria in the 'requirements' array\n"
            "- Put budget, timeline, quantity, delivery, warranty, and other "
            "commercial/logistical details in the 'attributes' array\n"
            "- Each requirement/attribute MUST have: key (snake_case label), value (extracted text), confidence (0.0-1.0)\n"
            "- Extract as many requirements and attributes as the document contains\n"
            "- Set low confidence for inferred/uncertain values\n"
            "- Set high confidence for clearly stated values\n"
            "- If a field cannot be extracted, omit it\n"
            "- All monetary values should include the currency if identifiable"
        )

        doc_type_hint = f"\nDocument type: {source_doc_type}" if source_doc_type else ""
        user_msg = (
            f"Extract structured procurement request data from the following document text.{doc_type_hint}\n\n"
            f"--- DOCUMENT TEXT ---\n{ocr_text[:12000]}\n--- END ---"
        )

        import json
        import re as _re
        llm = LLMClient()
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
