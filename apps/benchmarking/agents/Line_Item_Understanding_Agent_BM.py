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

from django.db import transaction

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
    PACKAGE_EQUIPMENT_LABELS = {
        "equipment supply package",
        "equipment package",
    }
    PACKAGE_INSTALL_LABELS = {
        "installation works",
        "installation",
    }
    PACKAGE_TC_LABELS = {
        "testing and commissioning",
        "testing commissioning",
        "testing & commissioning",
    }
    METADATA_LABEL_PREFIXES = (
        "quotation id",
        "client rfq",
        "commercial currency",
        "submission type",
        "scope overview",
        "commercial breakdown",
        "amount (aed)",
        "lead time",
        "payment terms",
    )

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

        # Safety net: if LLM path drops everything, re-apply deterministic fallback
        # so at least non-noise extracted lines remain active for downstream stages/UI.
        if kept == 0 and raw_items:
            fallback_payload = cls._fallback_understanding(quotation=quotation, raw_items=raw_items)
            fallback_kept, fallback_dropped = cls._persist_understanding(
                quotation=quotation,
                raw_items=raw_items,
                understanding_payload=fallback_payload,
            )
            if fallback_kept > 0:
                kept = fallback_kept
                dropped = fallback_dropped
                used_llm = False

        supplier_name = str(llm_payload.get("supplier_name") or "").strip()
        if supplier_name and supplier_name != (quotation.supplier_name or ""):
            quotation.supplier_name = supplier_name[:255]
            quotation.save(update_fields=["supplier_name", "updated_at"])

        expanded_from_package = cls._postprocess_packaged_quotation(quotation=quotation)
        if expanded_from_package:
            refreshed_active = quotation.line_items.filter(is_active=True).count()
            kept = int(refreshed_active)

        return {
            "quotation_id": quotation.pk,
            "supplier_name": quotation.supplier_name or "",
            "kept_lines": kept,
            "dropped_lines": dropped,
            "used_llm": used_llm,
            "expanded_from_package": expanded_from_package,
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
            item.is_active = True
            item.updated_by = quotation.updated_by
            item.save(
                update_fields=[
                    "description",
                    "uom",
                    "quantity",
                    "quoted_unit_rate",
                    "line_amount",
                    "extraction_confidence",
                    "is_active",
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
    def _postprocess_packaged_quotation(cls, *, quotation: BenchmarkQuotation) -> bool:
        active_lines = list(
            quotation.line_items.filter(is_active=True).order_by("line_number", "id")
        )
        if not active_lines:
            return False

        package_amounts = cls._extract_package_amounts(active_lines)
        if package_amounts is None:
            return False

        peer_templates = cls._build_peer_line_templates(quotation=quotation)
        if not peer_templates or len(peer_templates) < 5:
            return False

        equipment_amount = package_amounts.get("equipment")
        if not equipment_amount or equipment_amount <= 0:
            return False

        equipment_templates = [
            t for t in peer_templates if not cls._is_installation_like(t.get("description") or "")
        ]
        install_templates = [
            t for t in peer_templates if cls._is_installation_like(t.get("description") or "")
        ]
        if len(equipment_templates) < 3:
            return False

        scope_lines = cls._extract_scope_overview_lines(quotation.extracted_text or "")
        scope_equipment = [
            line for line in scope_lines if not cls._is_installation_like(line)
        ]

        if scope_equipment:
            selected_templates = []
            used_template_idx = set()
            for scope_desc in scope_equipment[:5]:
                best_idx = None
                best_score = -1
                for idx, template in enumerate(equipment_templates):
                    if idx in used_template_idx:
                        continue
                    score = cls._token_similarity_score(scope_desc, template.get("description") or "")
                    if score > best_score:
                        best_score = score
                        best_idx = idx
                if best_idx is None:
                    continue
                used_template_idx.add(best_idx)
                chosen = dict(equipment_templates[best_idx])
                chosen["description"] = scope_desc
                selected_templates.append(chosen)
            if len(selected_templates) >= 3:
                equipment_templates = selected_templates
            else:
                equipment_templates = equipment_templates[:5]
        else:
            equipment_templates = equipment_templates[:5]
        install_desc = "Installation, supports, and minor accessories"
        testing_desc = "Testing and commissioning"

        install_template = None
        testing_template = None
        for template in install_templates:
            desc_l = (template.get("description") or "").strip().lower()
            if "testing" in desc_l and "commission" in desc_l:
                testing_template = template
            elif install_template is None:
                install_template = template

        if install_template and install_template.get("description"):
            install_desc = str(install_template.get("description"))[:2000]
        if testing_template and testing_template.get("description"):
            testing_desc = str(testing_template.get("description"))[:2000]

        peer_equipment_total = Decimal("0")
        for template in equipment_templates:
            qty = cls._to_decimal(template.get("quantity"), default=Decimal("1")) or Decimal("1")
            rate = cls._to_decimal(template.get("quoted_unit_rate"), default=Decimal("0"), quantize_places=2) or Decimal("0")
            base_amount = qty * rate
            if base_amount <= 0:
                base_amount = Decimal("1")
            template["_base_amount"] = base_amount
            peer_equipment_total += base_amount

        if peer_equipment_total <= 0:
            return False

        created_payloads = []
        remaining = equipment_amount
        for idx, template in enumerate(equipment_templates, start=1):
            qty = cls._to_decimal(template.get("quantity"), default=Decimal("1")) or Decimal("1")
            if qty <= 0:
                qty = Decimal("1")

            if idx < len(equipment_templates):
                allocated_amount = (equipment_amount * template["_base_amount"] / peer_equipment_total).quantize(Decimal("1.00"))
                remaining -= allocated_amount
            else:
                allocated_amount = remaining.quantize(Decimal("1.00"))

            unit_rate = Decimal("0")
            if qty > 0:
                unit_rate = (allocated_amount / qty).quantize(Decimal("1.00"))

            created_payloads.append(
                {
                    "line_number": idx,
                    "description": str(template.get("description") or "")[:2000],
                    "uom": str(template.get("uom") or "Nos")[:50],
                    "quantity": qty,
                    "quoted_unit_rate": unit_rate,
                    "line_amount": allocated_amount,
                    "extraction_confidence": 0.62,
                }
            )

        install_amount = package_amounts.get("installation")
        if install_amount is not None and install_amount > 0:
            created_payloads.append(
                {
                    "line_number": len(created_payloads) + 1,
                    "description": install_desc,
                    "uom": "Lot",
                    "quantity": Decimal("1.000"),
                    "quoted_unit_rate": install_amount.quantize(Decimal("1.00")),
                    "line_amount": install_amount.quantize(Decimal("1.00")),
                    "extraction_confidence": 0.62,
                }
            )

        testing_amount = package_amounts.get("testing")
        if testing_amount is not None and testing_amount > 0:
            created_payloads.append(
                {
                    "line_number": len(created_payloads) + 1,
                    "description": testing_desc,
                    "uom": "Lot",
                    "quantity": Decimal("1.000"),
                    "quoted_unit_rate": testing_amount.quantize(Decimal("1.00")),
                    "line_amount": testing_amount.quantize(Decimal("1.00")),
                    "extraction_confidence": 0.62,
                }
            )

        with transaction.atomic():
            quotation.line_items.filter(is_active=True).update(is_active=False)
            for payload in created_payloads:
                BenchmarkLineItem.objects.create(
                    quotation=quotation,
                    tenant=quotation.tenant,
                    description=payload["description"],
                    uom=payload["uom"],
                    quantity=payload["quantity"],
                    quoted_unit_rate=payload["quoted_unit_rate"],
                    line_amount=payload["line_amount"],
                    line_number=payload["line_number"],
                    extraction_confidence=float(payload["extraction_confidence"]),
                    classification_source="KEYWORD",
                    category="UNCATEGORIZED",
                    classification_confidence=0.0,
                    variance_status="NEEDS_REVIEW",
                    benchmark_source="NONE",
                    is_active=True,
                    created_by=quotation.created_by,
                    updated_by=quotation.updated_by,
                )

        return True

    @classmethod
    def _extract_package_amounts(cls, active_lines: list[BenchmarkLineItem]) -> Optional[dict]:
        equipment = None
        installation = None
        testing = None
        has_package_markers = False

        for line in active_lines:
            desc = re.sub(r"\s+", " ", (line.description or "").strip()).lower()
            amount = cls._to_decimal(line.line_amount, default=None, quantize_places=2)

            if cls._is_metadata_label(desc):
                has_package_markers = True
                continue

            if desc in cls.PACKAGE_EQUIPMENT_LABELS:
                has_package_markers = True
                if amount is not None and amount > 0:
                    equipment = amount
                continue
            if desc in cls.PACKAGE_INSTALL_LABELS:
                has_package_markers = True
                if amount is not None and amount > 0:
                    installation = amount
                continue
            if desc in cls.PACKAGE_TC_LABELS:
                has_package_markers = True
                if amount is not None and amount > 0:
                    testing = amount
                continue

        if not has_package_markers:
            return None
        if equipment is None:
            return None
        return {
            "equipment": equipment,
            "installation": installation,
            "testing": testing,
        }

    @classmethod
    def _build_peer_line_templates(cls, *, quotation: BenchmarkQuotation) -> list[dict]:
        peers = BenchmarkLineItem.objects.filter(
            quotation__request=quotation.request,
            quotation__is_active=True,
            is_active=True,
        ).exclude(quotation__pk=quotation.pk).select_related("quotation").order_by("line_number", "id")

        templates = []
        seen = set()
        for line in peers:
            desc = re.sub(r"\s+", " ", (line.description or "").strip())
            if not desc:
                continue
            desc_l = desc.lower()
            if cls._is_metadata_label(desc_l):
                continue
            if desc_l in cls.PACKAGE_EQUIPMENT_LABELS:
                continue
            key = desc_l[:180]
            if key in seen:
                continue
            seen.add(key)
            templates.append(
                {
                    "description": desc,
                    "uom": line.uom or "",
                    "quantity": line.quantity,
                    "quoted_unit_rate": line.quoted_unit_rate,
                    "line_number": line.line_number,
                }
            )

        templates.sort(key=lambda t: (int(t.get("line_number") or 0), str(t.get("description") or "")))
        return templates

    @classmethod
    def _is_metadata_label(cls, description_lower: str) -> bool:
        text = (description_lower or "").strip().lower()
        if not text:
            return True
        for prefix in cls.METADATA_LABEL_PREFIXES:
            if text.startswith(prefix):
                return True
        if text in {"subtotal", "grand total", "vat 5%", "vat", "amount", "amount due"}:
            return True
        return False

    @classmethod
    def _is_installation_like(cls, description: str) -> bool:
        lower = re.sub(r"\s+", " ", (description or "").strip().lower())
        if not lower:
            return False
        if lower in cls.PACKAGE_INSTALL_LABELS or lower in cls.PACKAGE_TC_LABELS:
            return True
        if "install" in lower:
            return True
        if "testing" in lower and "commission" in lower:
            return True
        return False

    @classmethod
    def _extract_scope_overview_lines(cls, extracted_text: str) -> list[str]:
        text = str(extracted_text or "")
        if not text.strip():
            return []

        lines = [re.sub(r"\s+", " ", row).strip() for row in text.splitlines()]
        lines = [row for row in lines if row]

        start_idx = None
        end_idx = len(lines)
        for idx, row in enumerate(lines):
            lower = row.lower()
            if "scope overview" in lower:
                start_idx = idx + 1
                continue
            if start_idx is not None and ("commercial breakdown" in lower or "amount (aed)" in lower):
                end_idx = idx
                break

        if start_idx is None:
            return []

        scoped = lines[start_idx:end_idx]
        extracted = []
        for row in scoped:
            match = re.match(r"^\s*\d+\s*[\.)-]?\s*(.+)$", row)
            if not match:
                continue
            desc = re.sub(r"\s+", " ", (match.group(1) or "").strip())
            if not desc:
                continue
            if cls._is_metadata_label(desc.lower()):
                continue
            extracted.append(desc[:2000])

        deduped = []
        seen = set()
        for desc in extracted:
            key = desc.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(desc)
        return deduped

    @classmethod
    def _token_similarity_score(cls, left: str, right: str) -> int:
        left_tokens = {t for t in re.findall(r"[a-z0-9]+", (left or "").lower()) if len(t) > 1}
        right_tokens = {t for t in re.findall(r"[a-z0-9]+", (right or "").lower()) if len(t) > 1}
        if not left_tokens or not right_tokens:
            return 0
        overlap = len(left_tokens & right_tokens)
        return overlap * 10 - abs(len(left_tokens) - len(right_tokens))

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
