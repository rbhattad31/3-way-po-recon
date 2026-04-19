"""LLM-based line item understanding agent for benchmarking quotations.

Purpose:
- normalize Azure DI extracted rows
- filter footer/summary/noise rows
- infer supplier name from quotation text
- persist cleaned line items before Decision Maker routing
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.benchmarking.models import BenchmarkLineItem, BenchmarkQuotation


logger = logging.getLogger(__name__)


class BenchmarkLineItemUnderstandingAgentBM:
    """Understand and normalize extracted quotation rows before benchmarking."""

    SUMMARY_ROW_RE = re.compile(
        r"\b(total|subtotal|grand\s*total|vat|tax|amount\s*due|net\s*amount|balance\s*due)\b",
        re.IGNORECASE,
    )
    CURRENCY_ONLY_RE = re.compile(r"(?i)^\s*(AED|USD|SAR|QAR)\s*[\d,]+(?:\.\d+)?\s*$")
    SUPPLIER_STOPWORDS = {
        "quotation",
        "quote",
        "invoice",
        "rfq",
        "project",
        "client",
        "subject",
        "validity",
        "delivery",
        "payment",
        "subtotal",
        "grand total",
        "vat",
        "amount",
        "description",
        "qty",
        "quantity",
    }

    @classmethod
    def understand_request(cls, *, quotations: list[BenchmarkQuotation]) -> dict:
        quotation_outputs = []
        total_kept = 0
        total_dropped = 0

        for quotation in quotations:
            output = cls.understand_quotation(quotation=quotation)
            quotation_outputs.append(output)
            total_kept += int(output.get("kept_lines", 0) or 0)
            total_dropped += int(output.get("dropped_lines", 0) or 0)

        summary = (
            f"Line item understanding completed for {len(quotations)} quotation(s): "
            f"kept {total_kept} cleaned line(s), dropped {total_dropped} noise line(s)."
        )
        confidence = 0.9 if total_kept > 0 else 0.5
        return {
            "confidence": confidence,
            "summary": summary,
            "quotation_count": len(quotations),
            "kept_lines": total_kept,
            "dropped_lines": total_dropped,
            "details": quotation_outputs,
        }

    @classmethod
    def understand_quotation(cls, *, quotation: BenchmarkQuotation) -> dict:
        raw_items = list(quotation.line_items.filter(is_active=True).order_by("line_number", "id"))
        if not raw_items:
            return {
                "quotation_id": quotation.pk,
                "supplier_name": quotation.supplier_name or "",
                "kept_lines": 0,
                "dropped_lines": 0,
                "used_llm": False,
                "status": "NO_LINES",
            }

        llm_payload = cls._understand_with_llm(quotation=quotation, raw_items=raw_items)
        if not llm_payload:
            llm_payload = cls._fallback_understanding(quotation=quotation, raw_items=raw_items)
            used_llm = False
        else:
            used_llm = True

        kept, dropped = cls._persist_understanding(
            quotation=quotation,
            raw_items=raw_items,
            understanding_payload=llm_payload,
        )
        supplier_name = str(llm_payload.get("supplier_name") or "").strip()
        if supplier_name and supplier_name != (quotation.supplier_name or ""):
            quotation.supplier_name = supplier_name[:255]
            quotation.save(update_fields=["supplier_name", "updated_at"])

        return {
            "quotation_id": quotation.pk,
            "supplier_name": quotation.supplier_name or "",
            "kept_lines": kept,
            "dropped_lines": dropped,
            "used_llm": used_llm,
            "status": "DONE",
        }

    @classmethod
    def _understand_with_llm(cls, *, quotation: BenchmarkQuotation, raw_items: list[BenchmarkLineItem]) -> Optional[dict]:
        extracted_text = (quotation.extracted_text or "")[:12000]
        item_payload = []
        for item in raw_items:
            item_payload.append(
                {
                    "line_pk": item.pk,
                    "line_number": item.line_number,
                    "description": (item.description or "")[:300],
                    "uom": item.uom or "",
                    "quantity": str(item.quantity or ""),
                    "quoted_unit_rate": str(item.quoted_unit_rate or ""),
                    "line_amount": str(item.line_amount or ""),
                }
            )

        prompt = {
            "task": "Understand quotation line items from OCR extraction.",
            "rules": [
                "Drop footer, total, VAT, tax, amount-due, and currency-only rows.",
                "Keep only actual purchasable line items/services.",
                "Normalize description, uom, quantity, quoted_unit_rate, and line_amount where possible.",
                "Infer supplier_name from quotation text if present.",
                "Do not invent extra commercial lines that are not in the source.",
                "Return strict JSON only.",
            ],
            "quotation_ref": quotation.quotation_ref or "",
            "document_text_excerpt": extracted_text,
            "raw_line_items": item_payload,
            "required_output_schema": {
                "supplier_name": "string",
                "normalized_lines": [
                    {
                        "line_pk": "int",
                        "keep": "bool",
                        "normalized_description": "string",
                        "uom": "string",
                        "quantity": "number_or_empty",
                        "quoted_unit_rate": "number_or_empty",
                        "line_amount": "number_or_empty",
                        "confidence": "float_0_to_1",
                        "drop_reason": "string",
                    }
                ],
            },
        }

        try:
            llm = LLMClient(temperature=0.0, max_tokens=3000)
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You are a procurement quotation line-item understanding agent. "
                            "Return only valid JSON."
                        ),
                    ),
                    LLMMessage(role="user", content=json.dumps(prompt)),
                ],
                response_format={"type": "json_object"},
            )
            parsed = json.loads(response.content or "")
            if not isinstance(parsed, dict):
                return None
            normalized_lines = parsed.get("normalized_lines")
            if not isinstance(normalized_lines, list):
                return None
            return parsed
        except Exception:
            logger.exception("Line item understanding LLM path failed; using fallback")
            return None

    @classmethod
    def _fallback_understanding(cls, *, quotation: BenchmarkQuotation, raw_items: list[BenchmarkLineItem]) -> dict:
        supplier_name = cls._infer_supplier_name((quotation.extracted_text or "")) or (quotation.supplier_name or "")
        normalized_lines = []
        for item in raw_items:
            description = (item.description or "").strip()
            keep = not cls._looks_like_noise(description)
            normalized_lines.append(
                {
                    "line_pk": item.pk,
                    "keep": keep,
                    "normalized_description": description,
                    "uom": (item.uom or "").strip(),
                    "quantity": item.quantity,
                    "quoted_unit_rate": item.quoted_unit_rate,
                    "line_amount": item.line_amount,
                    "confidence": 0.6,
                    "drop_reason": "noise_footer_row" if not keep else "",
                }
            )
        return {
            "supplier_name": supplier_name,
            "normalized_lines": normalized_lines,
        }

    @classmethod
    def _persist_understanding(
        cls,
        *,
        quotation: BenchmarkQuotation,
        raw_items: list[BenchmarkLineItem],
        understanding_payload: dict,
    ) -> tuple[int, int]:
        by_pk = {item.pk: item for item in raw_items}
        normalized_lines = understanding_payload.get("normalized_lines") or []
        kept = 0
        dropped = 0

        seen_pks = set()
        for row in normalized_lines:
            if not isinstance(row, dict):
                continue
            try:
                line_pk = int(row.get("line_pk"))
            except Exception:
                continue
            item = by_pk.get(line_pk)
            if item is None:
                continue
            seen_pks.add(line_pk)

            keep = bool(row.get("keep"))
            normalized_description = str(row.get("normalized_description") or item.description or "").strip()
            if cls._looks_like_noise(normalized_description):
                keep = False

            if not keep:
                if item.is_active:
                    item.is_active = False
                    item.updated_by = quotation.updated_by
                    item.save(update_fields=["is_active", "updated_by", "updated_at"])
                dropped += 1
                continue

            item.description = normalized_description[:2000] or item.description
            item.uom = str(row.get("uom") or item.uom or "").strip()[:50]
            item.quantity = cls._to_decimal(row.get("quantity"), default=item.quantity)
            item.quoted_unit_rate = cls._to_decimal(row.get("quoted_unit_rate"), default=item.quoted_unit_rate, quantize_places=2)
            item.line_amount = cls._to_decimal(row.get("line_amount"), default=item.line_amount, quantize_places=2)
            item.extraction_confidence = cls._to_float(row.get("confidence"), default=item.extraction_confidence or 0.6)
            item.updated_by = quotation.updated_by
            item.save(
                update_fields=[
                    "description",
                    "uom",
                    "quantity",
                    "quoted_unit_rate",
                    "line_amount",
                    "extraction_confidence",
                    "updated_by",
                    "updated_at",
                ]
            )
            kept += 1

        for item in raw_items:
            if item.pk in seen_pks:
                continue
            if cls._looks_like_noise(item.description or "") and item.is_active:
                item.is_active = False
                item.updated_by = quotation.updated_by
                item.save(update_fields=["is_active", "updated_by", "updated_at"])
                dropped += 1
            elif item.is_active:
                kept += 1

        return kept, dropped

    @classmethod
    def _looks_like_noise(cls, description: str) -> bool:
        text = re.sub(r"\s+", " ", (description or "").strip())
        if not text:
            return True
        lower = text.lower()
        if cls.SUMMARY_ROW_RE.search(text):
            return True
        if cls.CURRENCY_ONLY_RE.match(text):
            return True
        if lower in {"amount", "rate", "total", "subtotal", "vat"}:
            return True
        alpha_chars = sum(1 for ch in text if ch.isalpha())
        digit_chars = sum(1 for ch in text if ch.isdigit())
        if alpha_chars <= 3 and digit_chars >= 1:
            return True
        return False

    @classmethod
    def _infer_supplier_name(cls, extracted_text: str) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in (extracted_text or "").splitlines() if line.strip()]
        header_lines = lines[:20]
        candidates = []
        for line in header_lines:
            lower = line.lower()
            if any(stop in lower for stop in cls.SUPPLIER_STOPWORDS):
                continue
            if len(line) < 4 or len(line) > 80:
                continue
            if sum(1 for ch in line if ch.isalpha()) < 4:
                continue
            score = len(line)
            if any(token in lower for token in ["llc", "l.l.c", "trading", "services", "solutions", "technologies", "mep", "contracting", "company"]):
                score += 25
            if not any(ch.isdigit() for ch in line):
                score += 10
            candidates.append((score, line))
        if not candidates:
            return ""
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return candidates[0][1][:255]

    @staticmethod
    def _to_decimal(value: Any, *, default=None, quantize_places: Optional[int] = None):
        if value in (None, "", "None"):
            return default
        try:
            parsed = Decimal(str(value).replace(",", "").strip())
            if quantize_places is not None:
                parsed = parsed.quantize(Decimal("1." + ("0" * quantize_places)))
            return parsed
        except (InvalidOperation, ValueError):
            return default

    @staticmethod
    def _to_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return default
