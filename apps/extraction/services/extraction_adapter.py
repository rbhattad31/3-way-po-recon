"""Invoice extraction adapter — Azure Document Intelligence OCR + Azure OpenAI LLM.

Pipeline:
  1. Azure Document Intelligence reads the PDF/image and returns raw OCR text.
  2. The OCR text is sent to Azure OpenAI GPT-4o with a structured extraction
     prompt that returns a JSON object with header fields and line items.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResponse:
    """Standardised output from the extraction pipeline."""
    success: bool = False
    raw_json: Optional[Dict[str, Any]] = None
    confidence: float = 0.0
    engine_name: str = "azure_di_gpt4o"
    engine_version: str = "1.0"
    duration_ms: int = 0
    error_message: str = ""
    ocr_text: str = ""


# ---------------------------------------------------------------------------
# Extraction prompt for the LLM
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = """You are an expert invoice data extraction system. You will receive OCR text from an invoice document.
Extract ALL relevant fields and return a JSON object with EXACTLY this structure:

{
  "confidence": <float 0.0-1.0 representing your overall confidence>,
  "vendor_name": "<vendor/supplier company name>",
  "invoice_number": "<invoice number/ID>",
  "invoice_date": "<invoice date in YYYY-MM-DD format>",
  "po_number": "<purchase order number referenced on the invoice>",
  "currency": "<3-letter ISO currency code e.g. USD, EUR, INR>",
  "subtotal": "<subtotal amount before tax as a number>",
  "tax_amount": "<total tax amount as a number>",
  "total_amount": "<grand total amount as a number>",
  "line_items": [
    {
      "item_description": "<description of the line item>",
      "quantity": "<quantity as a number>",
      "unit_price": "<unit price as a number>",
      "tax_amount": "<tax for this line as a number or null>",
      "line_amount": "<total amount for this line as a number>"
    }
  ]
}

Rules:
- Extract EVERY line item visible in the invoice.
- For amounts, return numeric values only (no currency symbols or commas).
- If a field is not found, return an empty string for text fields or null for numeric fields.
- Parse dates into YYYY-MM-DD format.
- If the PO number is referenced anywhere (header, footer, reference fields), extract it.
- Return ONLY valid JSON, no markdown or explanation."""


class InvoiceExtractionAdapter:
    """Two-step extraction: Azure Document Intelligence OCR -> Azure OpenAI LLM."""

    def extract(self, file_path: str) -> ExtractionResponse:
        """Run OCR + LLM extraction on *file_path* and return structured output."""
        start = time.time()
        try:
            # Step 1: OCR via Azure Document Intelligence
            ocr_text = self._ocr_document(file_path)
            if not ocr_text.strip():
                return ExtractionResponse(
                    success=False,
                    error_message="OCR returned no text from the document",
                    duration_ms=int((time.time() - start) * 1000),
                )

            logger.info("OCR completed: %d characters extracted from %s", len(ocr_text), file_path)

            # Step 2: LLM structured extraction
            raw_json = self._llm_extract(ocr_text)
            elapsed = int((time.time() - start) * 1000)

            return ExtractionResponse(
                success=True,
                raw_json=raw_json,
                confidence=float(raw_json.get("confidence", 0.0)),
                engine_name="azure_di_gpt4o",
                engine_version="1.0",
                duration_ms=elapsed,
                ocr_text=ocr_text,
            )
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            logger.exception("Extraction failed for %s", file_path)
            return ExtractionResponse(
                success=False,
                error_message=str(exc),
                duration_ms=elapsed,
            )

    # ------------------------------------------------------------------
    # Step 1: Azure Document Intelligence OCR
    # ------------------------------------------------------------------
    @staticmethod
    def _ocr_document(file_path: str) -> str:
        """Use Azure Document Intelligence to extract text from a document."""
        from azure.ai.formrecognizer import DocumentAnalysisClient
        from azure.core.credentials import AzureKeyCredential

        endpoint = getattr(settings, "AZURE_DI_ENDPOINT", "")
        key = getattr(settings, "AZURE_DI_KEY", "")

        if not endpoint or not key:
            raise ValueError("Azure Document Intelligence credentials not configured (AZURE_DI_ENDPOINT / AZURE_DI_KEY)")

        client = DocumentAnalysisClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )

        with open(file_path, "rb") as f:
            poller = client.begin_analyze_document("prebuilt-read", document=f)

        result = poller.result()

        # Concatenate all pages' text
        lines = []
        for page in result.pages:
            for line in page.lines:
                lines.append(line.content)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Step 2: Azure OpenAI LLM structured extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _llm_extract(ocr_text: str) -> Dict[str, Any]:
        """Send OCR text to Azure OpenAI GPT-4o for structured field extraction."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_key=getattr(settings, "AZURE_OPENAI_API_KEY", ""),
            api_version=getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-02-01"),
            azure_endpoint=getattr(settings, "AZURE_OPENAI_ENDPOINT", ""),
        )

        deployment = getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or getattr(settings, "LLM_MODEL_NAME", "gpt-4o")

        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract invoice data from the following OCR text:\n\n{ocr_text}"},
            ],
            temperature=0.0,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        logger.info(
            "LLM extraction completed: tokens=%d/%d",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

        return json.loads(content)
