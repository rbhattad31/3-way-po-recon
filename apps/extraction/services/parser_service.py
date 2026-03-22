"""Extraction parser — converts raw extraction JSON into structured data objects."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)


@dataclass
class ParsedLineItem:
    line_number: int = 1
    raw_description: str = ""
    raw_item_category: str = ""
    raw_quantity: str = ""
    raw_unit_price: str = ""
    raw_tax_amount: str = ""
    raw_line_amount: str = ""


@dataclass
class ParsedInvoice:
    raw_vendor_name: str = ""
    raw_invoice_number: str = ""
    raw_invoice_date: str = ""
    raw_po_number: str = ""
    raw_currency: str = ""
    raw_subtotal: str = ""
    raw_tax_amount: str = ""
    raw_total_amount: str = ""
    confidence: float = 0.0
    line_items: List[ParsedLineItem] = field(default_factory=list)


class ExtractionParserService:
    """Parse raw extraction JSON into a ``ParsedInvoice`` dataclass."""

    @observed_service("extraction.parse", entity_type="ExtractionResult")
    def parse(self, raw_json: Dict[str, Any]) -> ParsedInvoice:
        if not raw_json:
            raise ValueError("Empty extraction payload")

        lines: List[ParsedLineItem] = []
        for idx, item in enumerate(raw_json.get("line_items", []) or [], start=1):
            lines.append(ParsedLineItem(
                line_number=idx,
                raw_description=self._safe_str(item.get("item_description") or item.get("description")),
                raw_item_category=self._safe_str(item.get("item_category") or item.get("category")),
                raw_quantity=self._safe_str(item.get("quantity")),
                raw_unit_price=self._safe_str(item.get("unit_price")),
                raw_tax_amount=self._safe_str(item.get("tax_amount")),
                raw_line_amount=self._safe_str(item.get("line_amount") or item.get("amount")),
            ))

        parsed = ParsedInvoice(
            raw_vendor_name=self._safe_str(raw_json.get("vendor_name")),
            raw_invoice_number=self._safe_str(raw_json.get("invoice_number")),
            raw_invoice_date=self._safe_str(raw_json.get("invoice_date")),
            raw_po_number=self._safe_str(raw_json.get("po_number")),
            raw_currency=self._safe_str(raw_json.get("currency")),
            raw_subtotal=self._safe_str(raw_json.get("subtotal")),
            raw_tax_amount=self._safe_str(raw_json.get("tax_amount")),
            raw_total_amount=self._safe_str(raw_json.get("total_amount")),
            confidence=float(raw_json.get("confidence", 0) or 0),
            line_items=lines,
        )
        logger.info(
            "Parsed invoice: vendor=%s inv_num=%s po=%s lines=%d",
            parsed.raw_vendor_name, parsed.raw_invoice_number,
            parsed.raw_po_number, len(lines),
        )
        return parsed

    @staticmethod
    def _safe_str(value) -> str:
        """Convert value to string, treating None/null as empty."""
        if value is None:
            return ""
        return str(value)
