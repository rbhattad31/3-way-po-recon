"""HVAC quotation ingestion service.

Uploads an attached quotation, extracts OCR text via Azure Document Intelligence,
and maps core fields to procurement request + supplier quotation payloads.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from apps.core.decorators import observed_service
from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter
from apps.extraction.services.upload_service import InvoiceUploadService

logger = logging.getLogger(__name__)


class HVACQuotationIngestionService:
    """Ingest a quotation file and return mapped data for request creation."""

    CURRENCY_PATTERN = re.compile(r"\b(AED|SAR|OMR|QAR|KWD|BHD|USD)\b", re.IGNORECASE)
    AMOUNT_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|\d+(?:\.\d{1,2}))(?!\d)")
    QUOTE_NO_PATTERN = re.compile(
        r"(?:quote|quotation)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]+)",
        re.IGNORECASE,
    )
    DATE_PATTERNS = [
        re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
        re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),
        re.compile(r"\b(\d{2}-\d{2}-\d{4})\b"),
    ]
    AREA_PATTERN = re.compile(
        r"(?:area|conditioned\s*area)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(?:sqm|m2|m²|sq\.?\s?m)",
        re.IGNORECASE,
    )
    TR_PATTERN = re.compile(r"(?:cooling\s*load\s*[:\-]?\s*)?(\d+(?:\.\d+)?)\s*(?:tr|ton(?:s)?\s*refrigeration)", re.IGNORECASE)
    BRAND_PATTERN = re.compile(
        r"\b(Daikin|Mitsubishi|Carrier|Trane|York|McQuay|LG|Samsung|Gree|Hitachi)\b",
        re.IGNORECASE,
    )

    @classmethod
    @observed_service("procurement.hvac.quotation_ingest")
    def ingest_uploaded_quotation(cls, *, uploaded_file, uploaded_by=None) -> Dict[str, Any]:
        """Upload + OCR + map quotation data for HVAC request creation."""
        document_upload = InvoiceUploadService.upload(uploaded_file, uploaded_by=uploaded_by)

        file_path = document_upload.file.path
        ocr_text = InvoiceExtractionAdapter._ocr_document(file_path)
        if not ocr_text.strip():
            raise ValueError("No readable text found in uploaded quotation")

        lines = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()]
        vendor_name = cls._extract_vendor_name(lines)
        quotation_number = cls._extract_quotation_number(ocr_text)
        quotation_date = cls._extract_date(ocr_text)
        currency = cls._extract_currency(ocr_text)
        total_amount = cls._extract_total_amount(lines)
        area_sqm = cls._extract_area_sqm(ocr_text)
        cooling_load_tr = cls._extract_cooling_load_tr(ocr_text)
        brands = cls._extract_brands(ocr_text)
        store_type = cls._infer_store_type(ocr_text)
        line_items = cls._extract_line_items(lines)

        default_title_parts = ["HVAC Quotation Intake"]
        if vendor_name:
            default_title_parts.append(vendor_name)
        if quotation_number:
            default_title_parts.append(quotation_number)

        attrs = []
        if area_sqm is not None:
            attrs.append(cls._attr("area_sqm", "Conditioned Area (sqm)", "NUMBER", str(area_sqm)))
        if cooling_load_tr is not None:
            attrs.append(cls._attr("cooling_load_tr", "Cooling Load (TR)", "NUMBER", str(cooling_load_tr)))
        if brands:
            attrs.append(cls._attr("brand_preference", "Preferred Brand(s)", "TEXT", ", ".join(brands)))
        if store_type:
            attrs.append(cls._attr("store_type", "Store / Facility Type", "SELECT", store_type))
        if total_amount is not None:
            attrs.append(cls._attr("budget_aed", "Budget (AED)", "NUMBER", str(total_amount)))

        return {
            "document_upload": document_upload,
            "ocr_text": ocr_text,
            "mapped_request": {
                "title": " - ".join(default_title_parts),
                "description": "Auto-created from uploaded quotation using Azure DI OCR.",
                "currency": currency,
            },
            "mapped_attributes": attrs,
            "quotation_payload": {
                "vendor_name": vendor_name or "Unknown Vendor",
                "quotation_number": quotation_number,
                "quotation_date": quotation_date,
                "total_amount": total_amount,
                "currency": currency,
            },
            "line_items": line_items,
            "confidence": 0.75,
        }

    @staticmethod
    def _attr(code: str, label: str, data_type: str, value: str) -> Dict[str, Any]:
        return {
            "attribute_code": code,
            "attribute_label": label,
            "data_type": data_type,
            "value_text": value,
            "is_required": False,
        }

    @classmethod
    def _extract_vendor_name(cls, lines: List[str]) -> str:
        for line in lines[:25]:
            lower = line.lower()
            if lower.startswith("vendor") or lower.startswith("supplier") or lower.startswith("company"):
                parts = re.split(r"[:\-]", line, maxsplit=1)
                if len(parts) == 2 and parts[1].strip():
                    return parts[1].strip()[:300]
        for line in lines[:6]:
            if len(line) > 4 and len(line) < 120 and not cls.AMOUNT_PATTERN.search(line):
                return line[:300]
        return ""

    @classmethod
    def _extract_quotation_number(cls, text: str) -> str:
        match = cls.QUOTE_NO_PATTERN.search(text)
        return match.group(1).strip()[:100] if match else ""

    @classmethod
    def _extract_date(cls, text: str):
        for pattern in cls.DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            value = match.group(1)
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    continue
        return None

    @classmethod
    def _extract_currency(cls, text: str) -> str:
        match = cls.CURRENCY_PATTERN.search(text)
        if match:
            return match.group(1).upper()
        return "AED"

    @classmethod
    def _extract_total_amount(cls, lines: List[str]):
        keywords = ("grand total", "total", "net total", "amount due")
        for line in reversed(lines):
            if any(k in line.lower() for k in keywords):
                amount = cls._last_amount_from_text(line)
                if amount is not None:
                    return amount
        for line in reversed(lines[-20:]):
            amount = cls._last_amount_from_text(line)
            if amount is not None:
                return amount
        return None

    @classmethod
    def _extract_area_sqm(cls, text: str):
        match = cls.AREA_PATTERN.search(text)
        if not match:
            return None
        return cls._to_decimal(match.group(1))

    @classmethod
    def _extract_cooling_load_tr(cls, text: str):
        match = cls.TR_PATTERN.search(text)
        if not match:
            return None
        return cls._to_decimal(match.group(1))

    @classmethod
    def _extract_brands(cls, text: str) -> List[str]:
        matches = cls.BRAND_PATTERN.findall(text)
        seen = []
        for brand in matches:
            normalized = brand.strip().title()
            if normalized not in seen:
                seen.append(normalized)
        return seen

    @staticmethod
    def _infer_store_type(text: str) -> str:
        lower = text.lower()
        if "warehouse" in lower or "logistics" in lower:
            return "WAREHOUSE"
        if "mall" in lower:
            return "MALL"
        if "office" in lower:
            return "OFFICE"
        if "data center" in lower or "datacenter" in lower:
            return "DATA_CENTER"
        if "restaurant" in lower or "kitchen" in lower:
            return "RESTAURANT"
        if "store" in lower or "retail" in lower or "shop" in lower:
            return "STANDALONE"
        return ""

    @classmethod
    def _extract_line_items(cls, lines: List[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for line in lines:
            lower = line.lower()
            if any(word in lower for word in ("total", "subtotal", "tax", "vat", "amount due")):
                continue

            numbers = cls.AMOUNT_PATTERN.findall(line)
            if len(numbers) < 1:
                continue

            total = cls._to_decimal(numbers[-1])
            if total is None or total <= 0:
                continue

            qty = Decimal("1")
            if len(numbers) >= 2:
                maybe_qty = cls._to_decimal(numbers[0])
                if maybe_qty is not None and maybe_qty > 0 and maybe_qty < Decimal("100000"):
                    qty = maybe_qty

            try:
                unit_rate = (total / qty).quantize(Decimal("0.0001")) if qty > 0 else total
            except (InvalidOperation, ZeroDivisionError):
                unit_rate = total

            description = re.sub(r"\s+", " ", re.sub(r"\d[\d,\.]*", "", line)).strip(" -:")
            if len(description) < 4:
                description = f"HVAC item {len(items) + 1}"

            items.append({
                "line_number": len(items) + 1,
                "description": description[:500],
                "quantity": qty,
                "unit": "EA",
                "unit_rate": unit_rate,
                "total_amount": total,
            })

            if len(items) >= 25:
                break
        return items

    @classmethod
    def _last_amount_from_text(cls, text: str):
        numbers = cls.AMOUNT_PATTERN.findall(text)
        if not numbers:
            return None
        return cls._to_decimal(numbers[-1])

    @staticmethod
    def _to_decimal(value: str):
        normalized = (value or "").replace(",", "").strip()
        if not normalized:
            return None
        try:
            return Decimal(normalized)
        except InvalidOperation:
            logger.debug("Could not parse decimal value: %s", value)
            return None
