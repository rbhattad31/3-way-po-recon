"""AzureDIExtractorAgent -- universal document extractor powered by Azure Document Intelligence + Azure OpenAI.

Architecture
------------
The agent treats Azure Document Intelligence (DI) as an OpenAI *tool*.  The LLM is invoked
first with a ToolSpec for ``extract_document_text``.  When the model issues that tool call the
agent runs the real DI API, returns the raw OCR text + tables + key-value pairs back to the LLM
as a ``tool`` role message, and lets the model synthesise a final structured JSON response.

This gives the agent the full ReAct-style "observe then reason" capability while keeping the
implementation stateless and reusable for *any* uploaded document type
(invoice, quotation, PO, GRN, contract, proforma, delivery note, etc.).

Supported input formats: PDF, JPEG, JPG, PNG, BMP, TIFF, HEIF, DOCX, XLSX, PPTX (via DI).

Usage
-----
    from apps.procurement.agents.Azure_Document_Intelligence_Extractor_Agent import AzureDIExtractorAgent

    # From a file path
    result = AzureDIExtractorAgent.extract(file_path="/media/invoices/inv_001.pdf")

    # From raw bytes + explicit mime type
    result = AzureDIExtractorAgent.extract(file_bytes=b"...", mime_type="application/pdf")

    # result["success"]          -> bool
    # result["doc_type"]         -> e.g. "invoice" | "quotation" | "purchase_order" | ...
    # result["confidence"]       -> 0.0-1.0 overall extraction confidence
    # result["header"]           -> dict of top-level fields (vendor_name, date, total, etc.)
    # result["line_items"]       -> list of line item dicts
    # result["commercial_terms"] -> dict of payment_terms, warranty_terms, etc.
    # result["raw_ocr_text"]     -> concatenated plain text from DI
    # result["tables"]           -> list of table dicts extracted by DI
    # result["key_value_pairs"]  -> list of {key, value, confidence} from DI
    # result["engine"]           -> "azure_di_gpt4o" | "error"
    # result["duration_ms"]      -> total wall-clock time in ms
    # result["error"]            -> None or error string

Required settings (set via .env)
---------------------------------
    AZURE_DI_ENDPOINT   -- e.g. https://<resource>.cognitiveservices.azure.com
    AZURE_DI_KEY        -- 32-char API key
    AZURE_OPENAI_ENDPOINT
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_DEPLOYMENT  -- deployment name, e.g. "gpt-4o"
"""
from __future__ import annotations

import io
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Document-type hint registry
# Used to guide the LLM towards the most relevant extraction schema
# ---------------------------------------------------------------------------
_DOC_TYPE_HINTS: Dict[str, str] = {
    "invoice": (
        "invoice_number, invoice_date, due_date, vendor_name, vendor_gstin, vendor_address, "
        "buyer_name, buyer_gstin, buyer_address, po_number, currency, subtotal, tax_amount, "
        "total_amount, line_items (description, hsn_code, quantity, unit, unit_rate, total_amount, tax_rate)"
    ),
    "quotation": (
        "quotation_number, quotation_date, valid_until, vendor_name, currency, subtotal, "
        "taxes, total_amount, warranty_terms, payment_terms, delivery_terms, lead_time, "
        "line_items (description, brand, model, quantity, unit, unit_rate, total_amount)"
    ),
    "purchase_order": (
        "po_number, po_date, buyer_name, buyer_address, vendor_name, vendor_address, "
        "delivery_address, currency, subtotal, total_amount, payment_terms, delivery_date, "
        "line_items (item_code, description, quantity, unit, unit_rate, total_amount)"
    ),
    "delivery_note": (
        "grn_number, delivery_date, vendor_name, po_reference, delivery_address, "
        "line_items (description, ordered_qty, delivered_qty, unit, batch_number)"
    ),
    "contract": (
        "contract_number, contract_date, effective_date, expiry_date, party_a, party_b, "
        "contract_value, currency, payment_terms, penalty_terms, renewal_terms"
    ),
    "proforma_invoice": (
        "proforma_number, issue_date, valid_until, vendor_name, buyer_name, currency, "
        "subtotal, taxes, total_amount, payment_terms, "
        "line_items (description, quantity, unit, unit_rate, total_amount)"
    ),
    "hvac_request_form": (
        "title (request title), priority (HIGH/MEDIUM/LOW/CRITICAL), request_type (RECOMMENDATION), "
        "description (project background), currency (AED/SAR/OMR/QAR/KWD/BHD/USD), "
        "store_id (facility/store identifier code), brand (retail brand name), "
        "country (UAE/KSA/QATAR/OMAN/KUWAIT/BAHRAIN), city, "
        "store_type (MALL/STANDALONE/WAREHOUSE/OFFICE/DATA_CENTER/RESTAURANT), "
        "store_format (RETAIL/HYPERMARKET/FURNITURE/ELECTRONICS/FOOD_BEVERAGE), "
        "area_sqft (total floor area in square feet as number only), "
        "ceiling_height_ft (ceiling height in feet as number only), operating_hours, "
        "footfall_category (HIGH/MEDIUM/LOW), ambient_temp_max (max outside temperature in Celsius as number only), "
        "humidity_level (HIGH/MEDIUM/LOW), dust_exposure (HIGH/MEDIUM/LOW), "
        "heat_load_category (HIGH/MEDIUM/LOW), fresh_air_requirement (HIGH/MEDIUM/LOW), "
        "budget_level (LOW/MEDIUM/HIGH - from Section 5 Commercial Parameters), "
        "energy_efficiency_priority (LOW/MEDIUM/HIGH - from Section 5 Commercial Parameters), "
        "landlord_constraint (any restrictions from landlord/building management)"
    ),
}

# ---------------------------------------------------------------------------
# Tool name constant (must be a valid Python identifier)
# ---------------------------------------------------------------------------
_TOOL_NAME = "extract_document_text"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are an expert document intelligence specialist. "
    "Your job is to extract ALL structured information from business documents. "
    "You have access to a tool called `extract_document_text` that calls Azure Document Intelligence "
    "and returns raw OCR text, tables, and key-value pairs extracted from the document.\n\n"
    "Workflow:\n"
    "1. Call `extract_document_text` with the document source to retrieve raw content.\n"
    "2. Analyse the raw content carefully -- pay attention to tables, key-value pairs, and text sections.\n"
    "3. Respond ONLY with a single valid JSON object in the schema below.\n\n"
    "Output JSON schema:\n"
    "{\n"
    '  "doc_type": "invoice|quotation|purchase_order|delivery_note|contract|proforma_invoice|unknown",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "header": {\n'
    '    "vendor_name":       {"value": "...", "confidence": 0.9},\n'
    '    "vendor_address":    {"value": "...", "confidence": 0.8},\n'
    '    "buyer_name":        {"value": "...", "confidence": 0.9},\n'
    '    "buyer_address":     {"value": "...", "confidence": 0.8},\n'
    '    "document_number":   {"value": "...", "confidence": 0.9},\n'
    '    "document_date":     {"value": "YYYY-MM-DD", "confidence": 0.9},\n'
    '    "due_date":          {"value": "YYYY-MM-DD or null", "confidence": 0.7},\n'
    '    "currency":          {"value": "USD", "confidence": 0.9},\n'
    '    "subtotal":          {"value": "1000.00", "confidence": 0.8},\n'
    '    "tax_amount":        {"value": "100.00", "confidence": 0.8},\n'
    '    "total_amount":      {"value": "1100.00", "confidence": 0.9},\n'
    '    "po_reference":      {"value": "...", "confidence": 0.8},\n'
    '    "payment_terms":     {"value": "...", "confidence": 0.7},\n'
    '    "delivery_terms":    {"value": "...", "confidence": 0.6},\n'
    '    "notes":             {"value": "...", "confidence": 0.5}\n'
    "  },\n"
    '  "line_items": [\n'
    "    {\n"
    '      "line_number":   1,\n'
    '      "description":   "Item description",\n'
    '      "item_code":     "SKU-001",\n'
    '      "quantity":      10,\n'
    '      "unit":          "EA",\n'
    '      "unit_rate":     100.00,\n'
    '      "total_amount":  1000.00,\n'
    '      "tax_rate":      10.0,\n'
    '      "tax_amount":    100.00,\n'
    '      "brand":         "Brand Name",\n'
    '      "model":         "Model Number",\n'
    '      "hsn_code":      "8415",\n'
    '      "confidence":    0.85\n'
    "    }\n"
    "  ],\n"
    '  "commercial_terms": {\n'
    '    "warranty_terms":      "...",\n'
    '    "installation_terms":  "...",\n'
    '    "support_terms":       "...",\n'
    '    "penalty_terms":       "...",\n'
    '    "lead_time":           "..."\n'
    "  }\n"
    "}\n\n"
    "Rules:\n"
    "- Always call `extract_document_text` first -- never guess content without DI data.\n"
    "- Classify doc_type from the detected document structure.\n"
    "- Extract ALL line items from tables, BOQ sections, pricing schedules, and cost breakdowns.\n"
    "- Monetary values are plain numbers (no currency symbols).\n"
    "- Dates are YYYY-MM-DD; null if not found.\n"
    "- Per-field confidence reflects extraction certainty (1.0 = verbatim, 0.5 = inferred).\n"
    "- Omit optional fields that cannot be extracted -- do not invent values.\n"
    "- ASCII only in all field values.\n"
    "- vendor_name in English characters (transliterate if non-English)."
)


# ---------------------------------------------------------------------------
# ToolSpec-compatible definition (serialised manually to avoid cyclic import)
# ---------------------------------------------------------------------------
_DI_TOOL_SPEC: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": (
            "Call Azure Document Intelligence to extract raw OCR text, structured tables, "
            "and key-value pairs from the uploaded document. "
            "Returns a JSON object with keys: text (str), tables (list), key_value_pairs (list), "
            "page_count (int), error (str or null)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short reason why DI extraction is needed (logged for observability).",
                }
            },
            "required": [],
        },
    },
}


# ---------------------------------------------------------------------------
# Azure DI runner
# ---------------------------------------------------------------------------

def _run_azure_di(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
    model_id: str = "prebuilt-layout",
) -> Dict[str, Any]:
    """Run Azure Document Intelligence and return a structured result dict.

    Falls back gracefully with error message when credentials are missing or the
    azure-ai-formrecognizer SDK is not installed.

    Args:
        file_path: Absolute path to the document file on disk.
        file_bytes: Raw bytes of the document (alternative to file_path).
        mime_type: MIME type hint for byte streams (e.g. 'application/pdf').
        model_id: DI model to use.  'prebuilt-layout' handles all supported formats.

    Returns:
        {
          "text": str,           -- full concatenated OCR text (max 60K chars)
          "tables": list,        -- each table as {table_index, row_count, col_count, rows}
          "key_value_pairs": list, -- each as {key, value, confidence}
          "page_count": int,
          "error": str or None,
        }
    """
    from django.conf import settings

    endpoint = getattr(settings, "AZURE_DI_ENDPOINT", "")
    key = getattr(settings, "AZURE_DI_KEY", "")

    if not endpoint or not key:
        return {
            "text": "",
            "tables": [],
            "key_value_pairs": [],
            "page_count": 0,
            "error": "Azure DI credentials not configured (AZURE_DI_ENDPOINT / AZURE_DI_KEY).",
        }

    try:
        from azure.ai.formrecognizer import DocumentAnalysisClient
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        return {
            "text": "",
            "tables": [],
            "key_value_pairs": [],
            "page_count": 0,
            "error": "azure-ai-formrecognizer SDK not installed. Run: pip install azure-ai-formrecognizer",
        }

    try:
        client = DocumentAnalysisClient(
            endpoint=endpoint.rstrip("/"),
            credential=AzureKeyCredential(key),
        )

        # -- Determine how to feed the document to DI --
        if file_path:
            with open(file_path, "rb") as fh:
                document_bytes = fh.read()
            poller = client.begin_analyze_document(model_id, document_bytes)
        elif file_bytes is not None:
            buf = io.BytesIO(file_bytes)
            poller = client.begin_analyze_document(model_id, buf)
        else:
            return {
                "text": "",
                "tables": [],
                "key_value_pairs": [],
                "page_count": 0,
                "error": "No document source provided (file_path or file_bytes required).",
            }

        result = poller.result()

        # -- Text --
        paragraphs: List[str] = []
        for page in result.pages or []:
            for line in page.lines or []:
                paragraphs.append(line.content or "")
        full_text = "\n".join(paragraphs)[:60000]  # Hard cap matches extraction pipeline

        # -- Tables --
        tables: List[Dict[str, Any]] = []
        for t_idx, table in enumerate(result.tables or []):
            row_count = table.row_count
            col_count = table.column_count
            grid: List[List[str]] = [[""] * col_count for _ in range(row_count)]
            for cell in table.cells:
                r, c = cell.row_index, cell.column_index
                if r < row_count and c < col_count:
                    grid[r][c] = cell.content or ""
            tables.append({
                "table_index": t_idx,
                "row_count": row_count,
                "col_count": col_count,
                "rows": grid,
            })

        # -- Key-value pairs --
        kv_pairs: List[Dict[str, Any]] = []
        for kv in result.key_value_pairs or []:
            key_text = kv.key.content if kv.key else ""
            val_text = kv.value.content if kv.value else ""
            conf = kv.confidence or 0.0
            if key_text:
                kv_pairs.append({"key": key_text, "value": val_text, "confidence": round(conf, 3)})

        page_count = len(result.pages or [])
        logger.info(
            "AzureDI: pages=%d chars=%d tables=%d kv_pairs=%d",
            page_count, len(full_text), len(tables), len(kv_pairs),
        )

        return {
            "text": full_text,
            "tables": tables,
            "key_value_pairs": kv_pairs,
            "page_count": page_count,
            "error": None,
        }

    except Exception as exc:
        logger.warning("AzureDI extraction failed: %s", exc)
        return {
            "text": "",
            "tables": [],
            "key_value_pairs": [],
            "page_count": 0,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AzureDIExtractorAgent:
    """Universal document extractor: Azure DI as tool + Azure OpenAI for synthesis.

    The LLM drives the extraction via a single-tool ReAct loop:
      1. LLM issues a ``extract_document_text`` tool call.
      2. Agent executes the real Azure DI API and feeds results back as a
         ``tool`` role message.
      3. LLM synthesises the final structured JSON response.

    This is a lightweight, stateless agent -- it does NOT extend BaseAgent
    to avoid reconciliation-model dependencies.  It mirrors the pattern of
    QuotationExtractionAgent but adds full tool-calling support.
    """

    # Max tool rounds (safety cap -- DI is a single-call tool so 1 is sufficient)
    MAX_TOOL_ROUNDS: int = 3

    def __init__(self, max_tokens: int = 8192):
        from apps.agents.services.llm_client import LLMClient
        self._llm = LLMClient(max_tokens=max_tokens)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @classmethod
    def extract(
        cls,
        file_path: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
        doc_type_hint: Optional[str] = None,
        lf_parent_span: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Extract structured data from any business document.

        Args:
            file_path: Absolute path to the document on disk.
            file_bytes: Raw document bytes (alternative to file_path).
            mime_type: MIME type hint when using file_bytes (e.g. 'application/pdf').
            doc_type_hint: Optional hint to guide the LLM ('invoice', 'quotation', etc.).
                           When None the agent auto-detects the document type.
            lf_parent_span: Optional Langfuse parent span for distributed tracing.

        Returns:
            Extraction result dict (see module docstring for full schema).
        """
        agent = cls()
        return agent._run(
            file_path=file_path,
            file_bytes=file_bytes,
            mime_type=mime_type,
            doc_type_hint=doc_type_hint,
            lf_parent_span=lf_parent_span,
        )

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _run(
        self,
        file_path: Optional[str],
        file_bytes: Optional[bytes],
        mime_type: Optional[str],
        doc_type_hint: Optional[str],
        lf_parent_span: Optional[Any],
    ) -> Dict[str, Any]:
        from apps.agents.services.llm_client import LLMMessage

        start_ts = time.time()
        _lf_span = None
        _di_raw: Optional[Dict[str, Any]] = None  # Store DI output for final return

        # -- Langfuse span --
        try:
            from apps.core.langfuse_client import start_span, end_span
            _lf_span = start_span(
                lf_parent_span,
                name="azure_di_extractor_agent",
                metadata={
                    "doc_type_hint": doc_type_hint or "auto",
                    "has_file_path": bool(file_path),
                    "has_file_bytes": bool(file_bytes),
                },
            ) if lf_parent_span else None
        except Exception:
            _lf_span = None

        try:
            # ---- Build initial user message ----
            hint_section = ""
            if doc_type_hint and doc_type_hint in _DOC_TYPE_HINTS:
                if doc_type_hint == "hvac_request_form":
                    # Provide a full HVAC-specific header schema so the model uses
                    # domain fields rather than the generic invoice example fields.
                    hint_section = (
                        "\n\nThis document is an HVAC Request Form (not an invoice). "
                        "Populate the 'header' object using these domain-specific fields:\n"
                        "  title, priority (HIGH/MEDIUM/LOW/CRITICAL), "
                        "request_type (RECOMMENDATION), description, "
                        "currency (AED/SAR/OMR/QAR/KWD/BHD/USD), "
                        "store_id, brand, country (UAE/KSA/QATAR/OMAN/KUWAIT/BAHRAIN), city, "
                        "store_type (MALL/STANDALONE/WAREHOUSE/OFFICE/DATA_CENTER/RESTAURANT), "
                        "store_format (RETAIL/HYPERMARKET/FURNITURE/ELECTRONICS/FOOD_BEVERAGE), "
                        "area_sqft (number), ceiling_height_ft (number), operating_hours, "
                        "footfall_category (HIGH/MEDIUM/LOW), ambient_temp_max (number), "
                        "humidity_level (HIGH/MEDIUM/LOW), dust_exposure (HIGH/MEDIUM/LOW), "
                        "heat_load_category (HIGH/MEDIUM/LOW), "
                        "fresh_air_requirement (HIGH/MEDIUM/LOW), "
                        "budget_level (LOW/MEDIUM/HIGH - Section 5 Commercial Parameters), "
                        "energy_efficiency_priority (LOW/MEDIUM/HIGH - Section 5 Commercial Parameters), "
                        "landlord_constraint.\n"
                        "Each header field should follow the format: "
                        "{\"value\": \"...\", \"confidence\": 0.9}. "
                        "Set line_items to []."
                    )
                else:
                    hint_section = (
                        f"\nDocument type hint: '{doc_type_hint}'. "
                        f"Key fields to extract: {_DOC_TYPE_HINTS[doc_type_hint]}."
                    )

            user_content = (
                "Please extract all structured data from the attached business document.\n"
                "Call `extract_document_text` first to retrieve the raw content, "
                "then return the complete structured JSON as specified in your instructions."
                + hint_section
            )

            messages: List[LLMMessage] = [
                LLMMessage(role="system", content=_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_content),
            ]

            # ---- Tool-calling ReAct loop ----
            rounds = 0
            while rounds < self.MAX_TOOL_ROUNDS:
                rounds += 1

                # Call LLM with the DI tool spec
                response = self._llm.chat(
                    messages=messages,
                    tools=None,  # We pass raw tool spec manually below
                )

                # Manually add tool spec to kwargs via _chat_with_tools helper
                response = self._chat_with_tools(messages)

                if not response.tool_calls:
                    # Model returned final content directly
                    break

                # Process all tool calls in this round
                any_di_called = False
                for tc in response.tool_calls:
                    if tc.name == _TOOL_NAME:
                        any_di_called = True
                        reason = tc.arguments.get("reason", "initial extraction")
                        logger.info("DI tool call (round %d): reason=%s", rounds, reason)

                        # -- Run Azure DI --
                        di_lf_span = None
                        try:
                            from apps.core.langfuse_client import start_span, end_span
                            di_lf_span = start_span(
                                _lf_span, name="azure_di_api_call",
                                metadata={"round": rounds, "reason": reason},
                            ) if _lf_span else None
                        except Exception:
                            pass

                        _di_raw = _run_azure_di(
                            file_path=file_path,
                            file_bytes=file_bytes,
                            mime_type=mime_type,
                        )

                        try:
                            from apps.core.langfuse_client import end_span
                            if di_lf_span:
                                end_span(di_lf_span, output={
                                    "page_count": _di_raw.get("page_count", 0),
                                    "text_chars": len(_di_raw.get("text", "")),
                                    "table_count": len(_di_raw.get("tables", [])),
                                    "kv_count": len(_di_raw.get("key_value_pairs", [])),
                                    "error": _di_raw.get("error"),
                                })
                        except Exception:
                            pass

                        # Append: assistant tool_call message + tool response message
                        messages.append(LLMMessage(
                            role="assistant",
                            content=None,
                            tool_calls=[{
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }],
                        ))
                        di_response_payload = json.dumps({
                            "text": _di_raw["text"][:30000],  # Truncate to keep within context window
                            "tables": _di_raw["tables"],
                            "key_value_pairs": _di_raw["key_value_pairs"],
                            "page_count": _di_raw["page_count"],
                            "error": _di_raw["error"],
                        }, ensure_ascii=True)
                        messages.append(LLMMessage(
                            role="tool",
                            content=di_response_payload,
                            tool_call_id=tc.id,
                            name=_TOOL_NAME,
                        ))
                    else:
                        logger.warning("Unknown tool call from model: %s -- ignored", tc.name)

                if not any_di_called:
                    # No recognised tool was called -- force a final synthesis pass
                    break

            # After loop: get final response text
            # If current response already has content (no tool_calls), use it
            # Otherwise do one more pass
            final_content = response.content
            if response.tool_calls and not final_content:
                final_response = self._chat_with_tools(messages, force_text=True)
                final_content = final_response.content

            # ---- Parse final JSON ----
            structured = self._parse_llm_output(final_content or "")

            elapsed_ms = int((time.time() - start_ts) * 1000)
            structured["engine"] = "azure_di_gpt4o"
            structured["duration_ms"] = elapsed_ms
            structured["raw_ocr_text"] = (_di_raw or {}).get("text", "")
            structured["tables"] = (_di_raw or {}).get("tables", [])
            structured["key_value_pairs"] = (_di_raw or {}).get("key_value_pairs", [])
            structured["success"] = True
            structured.setdefault("error", None)

            line_count = len(structured.get("line_items", []))
            doc_type = structured.get("doc_type", "unknown")
            confidence = structured.get("confidence", 0.0)
            logger.info(
                "AzureDIExtractorAgent: doc_type=%s confidence=%.2f line_items=%d elapsed=%dms",
                doc_type, confidence, line_count, elapsed_ms,
            )

            # Langfuse end span
            try:
                from apps.core.langfuse_client import end_span, score_trace
                if _lf_span:
                    end_span(_lf_span, output={
                        "doc_type": doc_type,
                        "confidence": confidence,
                        "line_items": line_count,
                    })
            except Exception:
                pass

            return structured

        except Exception as exc:
            elapsed_ms = int((time.time() - start_ts) * 1000)
            logger.exception("AzureDIExtractorAgent failed: %s", exc)
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span, output={"error": str(exc)}, level="ERROR")
            except Exception:
                pass
            return {
                "success": False,
                "doc_type": "unknown",
                "confidence": 0.0,
                "header": {},
                "line_items": [],
                "commercial_terms": {},
                "raw_ocr_text": (_di_raw or {}).get("text", ""),
                "tables": (_di_raw or {}).get("tables", []),
                "key_value_pairs": (_di_raw or {}).get("key_value_pairs", []),
                "engine": "error",
                "duration_ms": elapsed_ms,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # LLM wrapper that injects the DI tool spec
    # ------------------------------------------------------------------
    def _chat_with_tools(self, messages, force_text: bool = False):
        """Invoke LLMClient with the DI tool spec injected via raw kwargs.

        When force_text=True (synthesis pass after tool results are in), we
        set tool_choice='none' to prevent another tool call loop.
        """
        from apps.agents.services.llm_client import LLMMessage

        # Build raw messages list (mirror LLMClient._build_messages)
        api_messages = []
        for m in messages:
            d: Dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            if m.tool_calls:
                d["tool_calls"] = m.tool_calls
                d["content"] = None  # assistant tool_call messages have null content
            api_messages.append(d)

        kwargs: Dict[str, Any] = {
            "model": self._llm.model,
            "messages": api_messages,
            "temperature": self._llm.temperature,
            "max_tokens": self._llm.max_tokens,
            "tools": [_DI_TOOL_SPEC],
            "tool_choice": "none" if force_text else "auto",
        }

        raw = self._llm._client.chat.completions.create(**kwargs)
        return self._llm._parse_response(raw)

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_llm_output(content: str) -> Dict[str, Any]:
        """Parse LLM JSON output, applying fallbacks if malformed."""
        # Strip markdown code fences if present
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(
                ln for ln in lines
                if not ln.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(stripped)
            # Ensure required top-level keys are present
            data.setdefault("doc_type", "unknown")
            data.setdefault("confidence", 0.0)
            data.setdefault("header", {})
            data.setdefault("line_items", [])
            data.setdefault("commercial_terms", {})
            # Clamp confidence
            try:
                data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
            except (TypeError, ValueError):
                data["confidence"] = 0.0
            return data
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("AzureDIExtractorAgent: JSON parse failed (%s) -- returning empty scaffold", exc)
            return {
                "doc_type": "unknown",
                "confidence": 0.0,
                "header": {},
                "line_items": [],
                "commercial_terms": {},
                "_parse_error": str(exc),
                "_raw_content": content[:500],
            }


# ---------------------------------------------------------------------------
# Convenience wrapper for Django views / services
# ---------------------------------------------------------------------------

def extract_document(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    mime_type: Optional[str] = None,
    doc_type_hint: Optional[str] = None,
    lf_parent_span: Optional[Any] = None,
) -> Dict[str, Any]:
    """Module-level helper -- delegates to AzureDIExtractorAgent.extract().

    Ideal for calling from views or services without instantiating the class.

    Example::

        from apps.procurement.agents.Azure_Document_Intelligence_Extractor_Agent import extract_document

        result = extract_document(file_path="/media/invoices/inv_001.pdf")
        if result["success"]:
            print(result["doc_type"], result["confidence"])
            for item in result["line_items"]:
                print(item["description"], item["unit_rate"])
    """
    return AzureDIExtractorAgent.extract(
        file_path=file_path,
        file_bytes=file_bytes,
        mime_type=mime_type,
        doc_type_hint=doc_type_hint,
        lf_parent_span=lf_parent_span,
    )
