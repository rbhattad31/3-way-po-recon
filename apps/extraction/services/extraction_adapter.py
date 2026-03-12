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
# Extraction prompt for the LLM — loaded from PromptRegistry at runtime
# ---------------------------------------------------------------------------
# Hardcoded fallback removed; see apps/core/prompt_registry.py for defaults.


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
                {"role": "system", "content": InvoiceExtractionAdapter._get_extraction_prompt()},
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

    @staticmethod
    def _get_extraction_prompt() -> str:
        """Load the extraction system prompt from the PromptRegistry."""
        from apps.core.prompt_registry import PromptRegistry
        return PromptRegistry.get("extraction.invoice_system")
