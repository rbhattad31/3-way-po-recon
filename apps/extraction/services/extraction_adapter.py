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

from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)

_VENDOR_ENGLISH_ENFORCEMENT = (
    "\n\nMANDATORY vendor_name rule:\n"
    "- vendor_name must be in English characters only.\n"
    "- If source text is Arabic/Urdu/non-English, return translated/transliterated English vendor name.\n"
    "- Never return vendor_name in non-English script."
)


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
    agent_run_id: Optional[int] = None
    ocr_page_count: int = 0
    ocr_duration_ms: int = 0
    ocr_char_count: int = 0
    # Invoice category classification (new)
    invoice_category: str = ""
    category_confidence: float = 0.0
    category_signals: Any = field(default_factory=list)
    # Prompt composition metadata (new)
    prompt_components: Any = field(default_factory=dict)
    prompt_hash: str = ""
    # Response repair metadata (new)
    was_repaired: bool = False
    repair_actions: Any = field(default_factory=list)
    repair_warnings: Any = field(default_factory=list)
    # QR code data — populated when an Indian e-invoice QR is detected
    qr_data: Any = None  # Optional[QRInvoiceData] — avoid circular import at module level


# ---------------------------------------------------------------------------
# Extraction prompt for the LLM — loaded from PromptRegistry at runtime
# ---------------------------------------------------------------------------
# Hardcoded fallback removed; see apps/core/prompt_registry.py for defaults.


class InvoiceExtractionAdapter:
    """Two-step extraction: Azure Document Intelligence OCR -> Azure OpenAI LLM."""

    @observed_service("extraction.extract", entity_type="DocumentUpload", audit_event="EXTRACTION_STARTED")
    def extract(self, file_path: str, *, actor_user_id: Optional[int] = None, document_upload_id: Optional[int] = None, langfuse_trace: Any = None, trace_id: str = "", tenant: Any = None) -> ExtractionResponse:
        """Run OCR + LLM extraction on *file_path* and return structured output."""
        start = time.time()
        try:
            # Check runtime setting for OCR mode
            ocr_enabled = self._is_ocr_enabled()

            if ocr_enabled:
                # Step 1a: OCR via Azure Document Intelligence (also returns raw QR strings)
                ocr_text, ocr_page_count, ocr_duration_ms, qr_texts = self._ocr_document(file_path)
            else:
                # Step 1b: Native PDF text extraction (no Azure DI cost)
                logger.info("OCR disabled -- using native PDF text extraction for %s", file_path)
                ocr_text, ocr_page_count, ocr_duration_ms = self._extract_text_native(file_path)
                qr_texts = []

            ocr_char_count = len(ocr_text)
            if not ocr_text.strip():
                return ExtractionResponse(
                    success=False,
                    error_message="OCR returned no text from the document",
                    duration_ms=int((time.time() - start) * 1000),
                    ocr_page_count=ocr_page_count,
                    ocr_duration_ms=ocr_duration_ms,
                )

            logger.info("OCR completed: %d characters, %d pages from %s", ocr_char_count, ocr_page_count, file_path)

            # Update progress for copilot polling
            if document_upload_id:
                self._update_progress(document_upload_id, "Scanning the document layout...")

            # Step 1c: QR code decode -- Indian e-invoice IRN / GSTIN data (fail-silent)
            qr_data = self._decode_qr(file_path, ocr_text, qr_texts)
            if qr_data:
                logger.info(
                    "e-invoice QR decoded (strategy=%s): IRN=%s... DocNo=%s TotVal=%s",
                    qr_data.decode_strategy,
                    qr_data.irn[:16],
                    qr_data.doc_number,
                    qr_data.total_value,
                )

            # Step 2a: Invoice category classification (new)
            category_result = self._classify_category(ocr_text)

            # Step 2b: Compose extraction prompt from modular parts (new)
            composition = self._compose_prompt(category_result)

            # Step 2c: LLM structured extraction via Invoice Extraction Agent
            if document_upload_id:
                self._update_progress(document_upload_id, "Pulling out invoice details with AI...")
            raw_json, agent_run_id = self._agent_extract(
                ocr_text,
                actor_user_id=actor_user_id,
                composed_prompt=composition.final_prompt,
                prompt_metadata=self._build_prompt_metadata(category_result, composition),
                document_upload_id=document_upload_id,
                langfuse_trace=langfuse_trace,
                trace_id=trace_id,
                tenant=tenant,
            )
            logger.info("Agent extraction completed (agent_run_id=%s)", agent_run_id)

            if document_upload_id:
                self._update_progress(document_upload_id, "Verifying the extracted data...")

            # Step 2d: Deterministic response repair (new)
            repair_result = self._repair_response(raw_json, ocr_text, category_result)
            raw_json = repair_result.repaired_json
            if repair_result.was_repaired:
                logger.info(
                    "Response repair applied (%d actions): %s",
                    len(repair_result.repair_actions),
                    "; ".join(repair_result.repair_actions[:3]),
                )

            # Embed repair metadata inside raw_json for persistence (stored in ExtractionResult.raw_response)
            if repair_result.repair_actions or repair_result.warnings:
                raw_json["_repair"] = {
                    "was_repaired": repair_result.was_repaired,
                    "repair_actions": repair_result.repair_actions,
                    "warnings": repair_result.warnings,
                }

            # Embed QR metadata in raw_json for persistence
            if qr_data is not None:
                try:
                    raw_json["_qr"] = qr_data.to_serializable()
                except Exception:
                    pass

            elapsed = int((time.time() - start) * 1000)

            return ExtractionResponse(
                success=True,
                raw_json=raw_json,
                confidence=float(raw_json.get("confidence", 0.0)),
                engine_name="azure_di_gpt4o_agent" if ocr_enabled else "native_pdf_gpt4o_agent",
                engine_version="2.0",
                duration_ms=elapsed,
                ocr_text=ocr_text,
                agent_run_id=agent_run_id,
                ocr_page_count=ocr_page_count,
                ocr_duration_ms=ocr_duration_ms,
                ocr_char_count=ocr_char_count,
                # Category metadata
                invoice_category=category_result.category if category_result else "",
                category_confidence=category_result.confidence if category_result else 0.0,
                category_signals=list(category_result.signals) if category_result else [],
                # Prompt composition metadata
                prompt_components=dict(composition.components),
                prompt_hash=composition.prompt_hash,
                # Repair metadata
                was_repaired=repair_result.was_repaired,
                repair_actions=list(repair_result.repair_actions),
                repair_warnings=list(repair_result.warnings),
                # QR data
                qr_data=qr_data,
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
    # Runtime settings helper
    # ------------------------------------------------------------------
    @staticmethod
    def _update_progress(upload_id: int, message: str):
        """Update DocumentUpload.processing_message for copilot polling."""
        try:
            from apps.documents.models import DocumentUpload
            DocumentUpload.objects.filter(pk=upload_id).update(processing_message=message)
        except Exception:
            pass

    @staticmethod
    def _is_ocr_enabled() -> bool:
        """Check ExtractionRuntimeSettings.ocr_enabled flag.

        Falls back to settings.EXTRACTION_OCR_ENABLED (default True) if no
        runtime settings record exists.
        """
        try:
            from apps.extraction_core.models import ExtractionRuntimeSettings
            active = ExtractionRuntimeSettings.get_active()
            if active is not None:
                return active.ocr_enabled
        except Exception:
            logger.debug("Could not read ExtractionRuntimeSettings; using settings fallback")
        return getattr(settings, "EXTRACTION_OCR_ENABLED", True)

    # ------------------------------------------------------------------
    # Step 1a: Azure Document Intelligence OCR
    # ------------------------------------------------------------------
    @staticmethod
    def _ocr_document(file_path: str) -> tuple:
        """Use Azure Document Intelligence to extract text + barcodes from a document.

        Returns:
            (ocr_text, page_count, duration_ms, qr_texts)
            where qr_texts is a list of decoded QR code strings from Azure DI's
            barcodes API (empty list if no barcodes detected or SDK version
            doesn't support barcodes).
        """
        from azure.ai.formrecognizer import DocumentAnalysisClient
        from azure.core.credentials import AzureKeyCredential

        endpoint = getattr(settings, "AZURE_DI_ENDPOINT", "")
        key = getattr(settings, "AZURE_DI_KEY", "")

        if not endpoint or not key:
            raise ValueError("Azure Document Intelligence credentials not configured (AZURE_DI_ENDPOINT / AZURE_DI_KEY)")

        from azure.ai.formrecognizer import AnalysisFeature

        client = DocumentAnalysisClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
        )

        ocr_start = time.time()
        with open(file_path, "rb") as f:
            poller = client.begin_analyze_document(
                "prebuilt-read",
                document=f,
                # Request barcode add-on to get QR code values from Indian e-invoices.
                # AnalysisFeature.BARCODES = 'barcodes' — available from API version
                # 2023-07-31 (azure-ai-formrecognizer >= 3.3.0).
                # Without this flag, page.barcodes is always an empty list even when
                # the document contains QR codes; the decoder falls back to OCR-text
                # and pyzbar strategies automatically.
                features=[AnalysisFeature.BARCODES],
            )

        result = poller.result()
        ocr_duration_ms = int((time.time() - ocr_start) * 1000)

        # Concatenate all pages' text lines
        page_count = len(result.pages) if result.pages else 0
        lines = []
        qr_texts: list = []
        for page in result.pages:
            for line in page.lines:
                lines.append(line.content)
            # Extract QR code strings from Azure DI barcodes add-on.
            # Azure DI returns kind="QRCode" (PascalCase); .upper() normalises to
            # "QRCODE" for a case-insensitive comparison.
            for barcode in getattr(page, "barcodes", []):
                kind = str(getattr(barcode, "kind", "")).upper()
                if kind == "QRCODE":
                    val = getattr(barcode, "value", "")
                    if val:
                        qr_texts.append(val)

        if qr_texts:
            logger.info("Azure DI returned %d QR code(s) for %s", len(qr_texts), file_path)

        return "\n".join(lines), page_count, ocr_duration_ms, qr_texts

    @staticmethod
    def _decode_qr(
        file_path: str,
        ocr_text: str,
        qr_texts: Optional[list] = None,
    ):
        """Decode e-invoice QR data. Fail-silent — returns None on any failure.

        Returns QRInvoiceData or None.
        """
        try:
            from apps.extraction.services.qr_decoder_service import QRCodeDecoderService
            return QRCodeDecoderService.decode(
                file_path,
                ocr_text=ocr_text,
                qr_texts=qr_texts or [],
            )
        except Exception as exc:
            logger.debug("QR decode failed (non-fatal): %s", exc)
            return None

    # ------------------------------------------------------------------
    # Step 1b: Native PDF text extraction (no OCR cost)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_text_native(file_path: str) -> tuple:
        """Extract text from a PDF using PyPDF2 (native text layer).

        Returns:
            (text, page_count, duration_ms) — same shape as _ocr_document.
        """
        import PyPDF2

        native_start = time.time()
        lines = []
        page_count = 0
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            page_count = len(reader.pages)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    lines.append(text)
        duration_ms = int((time.time() - native_start) * 1000)
        return "\n".join(lines), page_count, duration_ms

    # ------------------------------------------------------------------
    # Step 2: Azure OpenAI LLM structured extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_category(ocr_text: str):
        """Classify OCR text into goods/service/travel. Fail-silent — returns None on error."""
        try:
            from apps.extraction_core.services.invoice_category_classifier import InvoiceCategoryClassifier
            return InvoiceCategoryClassifier.classify(ocr_text)
        except Exception as exc:
            logger.warning("InvoiceCategoryClassifier failed (using no category): %s", exc)
            return None

    @staticmethod
    def _compose_prompt(category_result):
        """Compose modular extraction prompt. Fail-silent — falls back to base prompt."""
        try:
            from apps.extraction.services.invoice_prompt_composer import InvoicePromptComposer
            category = category_result.category if category_result else None
            return InvoicePromptComposer.compose(invoice_category=category)
        except Exception as exc:
            logger.warning("InvoicePromptComposer failed (using fallback prompt): %s", exc)
            # Return an empty composition — agent will use its own system_prompt property
            from apps.extraction.services.invoice_prompt_composer import PromptComposition
            return PromptComposition()

    @staticmethod
    def _repair_response(raw_json, ocr_text: str, category_result):
        """Apply deterministic repair to LLM output. Fail-silent — returns original on error."""
        try:
            from apps.extraction.services.response_repair_service import ResponseRepairService
            category = category_result.category if category_result else None
            return ResponseRepairService.repair(
                raw_json,
                ocr_text=ocr_text,
                invoice_category=category,
            )
        except Exception as exc:
            logger.warning("ResponseRepairService failed (pass-through): %s", exc)
            from apps.extraction.services.response_repair_service import RepairResult
            return RepairResult(repaired_json=raw_json or {})

    @staticmethod
    def _build_prompt_metadata(category_result, composition) -> dict:
        """Build the prompt_metadata dict passed to the agent via ctx.extra."""
        components = composition.components if composition else {}
        # Extract individual component keys for Langfuse
        base_key = next((k for k in components if "base" in k or "system" in k), "")
        cat_key = next((k for k in components if "category" in k), "")
        country_key = next((k for k in components if "country" in k), "")
        return {
            "invoice_category": category_result.category if category_result else "",
            "invoice_category_confidence": category_result.confidence if category_result else 0.0,
            "base_prompt_key": base_key,
            "base_prompt_version": components.get(base_key, "") if base_key else "",
            "category_prompt_key": cat_key,
            "category_prompt_version": components.get(cat_key, "") if cat_key else "",
            "country_prompt_key": country_key,
            "country_prompt_version": components.get(country_key, "") if country_key else "",
            "prompt_hash": composition.prompt_hash if composition else "",
            "components": dict(components),
        }

    @staticmethod
    def _agent_extract(
        ocr_text: str,
        *,
        actor_user_id: Optional[int] = None,
        composed_prompt: str = "",
        prompt_metadata: Optional[Dict[str, Any]] = None,
        document_upload_id: Optional[int] = None,
        langfuse_trace: Any = None,
        trace_id: str = "",
        tenant: Any = None,
    ) -> tuple:
        """Run the Invoice Extraction Agent on OCR text.

        Returns:
            (raw_json_dict, agent_run_id) -- extracted data and the AgentRun PK
            for traceability.
        """
        from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
        from apps.agents.services.base_agent import AgentContext
        from apps.core.enums import AgentRunStatus, AgentType

        agent_cls = AGENT_CLASS_REGISTRY.get(AgentType.INVOICE_EXTRACTION)
        if not agent_cls:
            raise RuntimeError("Invoice Extraction Agent not found in registry")

        extra: Dict[str, Any] = {"ocr_text": ocr_text}
        if composed_prompt:
            extra["composed_prompt"] = composed_prompt
        if prompt_metadata:
            extra["prompt_metadata"] = prompt_metadata

        # Resolve RBAC metadata from the actor user
        _actor_role = ""
        _actor_roles_snapshot: list = []
        if actor_user_id:
            try:
                from apps.accounts.models import User
                _user = User.objects.get(pk=actor_user_id)
                _actor_role = getattr(_user, "role", "") or ""
                _actor_roles_snapshot = list(
                    _user.user_roles.filter(is_active=True)
                    .values_list("role__code", flat=True)
                ) if hasattr(_user, "user_roles") else []
            except Exception:
                pass

        ctx = AgentContext(
            reconciliation_result=None,
            invoice_id=0,  # Invoice not yet created
            actor_user_id=actor_user_id,
            actor_primary_role=_actor_role,
            actor_roles_snapshot=_actor_roles_snapshot,
            permission_checked="invoices.upload",
            permission_source="extraction_pipeline",
            access_granted=True,
            document_upload_id=document_upload_id,
            trace_id=trace_id,
            extra=extra,
            _langfuse_trace=langfuse_trace,
            tenant=tenant,
        )

        agent = agent_cls()
        agent_run = agent.run(ctx)

        if agent_run.status != AgentRunStatus.COMPLETED:
            raise RuntimeError(
                f"Invoice Extraction Agent failed: {agent_run.error_message or agent_run.status}"
            )

        # The extracted JSON is in output_payload.evidence
        output = agent_run.output_payload or {}
        raw_json = output.get("evidence", {})

        if not raw_json:
            raise RuntimeError("Invoice Extraction Agent returned empty extraction data")

        return raw_json, agent_run.pk

    @staticmethod
    def _llm_extract(ocr_text: str) -> Dict[str, Any]:
        """Direct LLM extraction fallback (without agent framework)."""
        from openai import AzureOpenAI

        client = AzureOpenAI(
            api_key=getattr(settings, "AZURE_OPENAI_API_KEY", ""),
            api_version=getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-02-01"),
            azure_endpoint=getattr(settings, "AZURE_OPENAI_ENDPOINT", ""),
        )

        deployment = getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or getattr(settings, "LLM_MODEL_NAME", "gpt-4o")
        system_prompt = InvoiceExtractionAdapter._get_extraction_prompt()

        import uuid as _uuid
        _trace_id = _uuid.uuid4().hex
        _lf_trace = None
        _lf_span = None
        _lf_prompt = None
        try:
            from apps.core.langfuse_client import start_trace, start_span, log_generation, end_span, get_prompt, slug_to_langfuse_name
            _lf_trace = start_trace(
                _trace_id,
                "llm_extract_fallback",
                session_id=f"extraction-fallback-{_trace_id[:12]}",
                metadata={"ocr_char_count": len(ocr_text)},
            )
            _lf_span = start_span(_lf_trace, "LLM_EXTRACT_FALLBACK") if _lf_trace else None
            _lf_prompt = get_prompt(slug_to_langfuse_name("extraction.invoice_system"))
        except Exception:
            pass

        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract invoice data from the following OCR text:\n\n{ocr_text}"},
            ],
            temperature=0.0,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens

        logger.info(
            "LLM extraction completed: tokens=%d/%d",
            prompt_tokens,
            completion_tokens,
        )

        if _lf_span is not None:
            try:
                from apps.core.langfuse_client import log_generation, end_span
                log_generation(
                    span=_lf_span,
                    name="llm_extract_fallback_chat",
                    model=deployment,
                    prompt_messages=[
                        {"role": "system", "content": system_prompt.replace("{{", "{").replace("}}", "}")},
                        {"role": "user", "content": f"Extract invoice data from the following OCR text:\n\n{ocr_text}"},
                    ],
                    completion=content,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    prompt=_lf_prompt,
                )
                end_span(_lf_span, output={"completion_length": len(content or "")})
                if _lf_trace:
                    end_span(_lf_trace)
            except Exception:
                pass

        return json.loads(content)

    @staticmethod
    def _get_extraction_prompt() -> str:
        """Load the extraction system prompt from the PromptRegistry."""
        from apps.core.prompt_registry import PromptRegistry
        base_prompt = PromptRegistry.get("extraction.invoice_system")
        if "vendor_name must be in English" in base_prompt:
            return base_prompt
        return base_prompt + _VENDOR_ENGLISH_ENFORCEMENT
