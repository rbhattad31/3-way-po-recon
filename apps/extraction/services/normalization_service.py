"""Normalization service — cleans and standardises extracted invoice values."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, List, Optional
from datetime import date

from apps.core.utils import (
    normalize_invoice_number,
    normalize_po_number,
    normalize_string,
    parse_date,
    to_decimal,
)
from apps.extraction.services.parser_service import ParsedInvoice, ParsedLineItem

logger = logging.getLogger(__name__)


@dataclass
class NormalizedLineItem:
    line_number: int = 1
    raw_description: str = ""
    raw_quantity: str = ""
    raw_unit_price: str = ""
    raw_tax_amount: str = ""
    raw_line_amount: str = ""
    description: str = ""
    normalized_description: str = ""
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    line_amount: Optional[Decimal] = None


@dataclass
class NormalizedInvoice:
    # Raw values preserved
    raw_vendor_name: str = ""
    raw_invoice_number: str = ""
    raw_invoice_date: str = ""
    raw_po_number: str = ""
    raw_currency: str = ""
    raw_subtotal: str = ""
    raw_tax_amount: str = ""
    raw_total_amount: str = ""
    confidence: float = 0.0

    # Normalized values
    vendor_name_normalized: str = ""
    invoice_number: str = ""
    normalized_invoice_number: str = ""
    invoice_date: Optional[date] = None
    po_number: str = ""
    normalized_po_number: str = ""
    currency: str = "USD"
    subtotal: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None

    line_items: List[NormalizedLineItem] = field(default_factory=list)


class NormalizationService:
    """Normalise a ParsedInvoice into a NormalizedInvoice."""

    def normalize(self, parsed: ParsedInvoice) -> NormalizedInvoice:
        lines = [self._normalize_line(li) for li in parsed.line_items]

        currency = parsed.raw_currency.strip().upper() or "USD"
        if len(currency) != 3:
            currency = "USD"

        result = NormalizedInvoice(
            raw_vendor_name=parsed.raw_vendor_name,
            raw_invoice_number=parsed.raw_invoice_number,
            raw_invoice_date=parsed.raw_invoice_date,
            raw_po_number=parsed.raw_po_number,
            raw_currency=parsed.raw_currency,
            raw_subtotal=parsed.raw_subtotal,
            raw_tax_amount=parsed.raw_tax_amount,
            raw_total_amount=parsed.raw_total_amount,
            confidence=parsed.confidence,
            # Normalized
            vendor_name_normalized=normalize_string(parsed.raw_vendor_name),
            invoice_number=parsed.raw_invoice_number.strip(),
            normalized_invoice_number=normalize_invoice_number(parsed.raw_invoice_number),
            invoice_date=parse_date(parsed.raw_invoice_date),
            po_number=parsed.raw_po_number.strip(),
            normalized_po_number=normalize_po_number(parsed.raw_po_number),
            currency=currency,
            subtotal=self._safe_decimal(parsed.raw_subtotal),
            tax_amount=self._safe_decimal(parsed.raw_tax_amount),
            total_amount=self._safe_decimal(parsed.raw_total_amount),
            line_items=lines,
        )

        logger.info(
            "Normalized invoice: inv=%s po=%s total=%s lines=%d",
            result.normalized_invoice_number, result.normalized_po_number,
            result.total_amount, len(lines),
        )
        return result

    def _normalize_line(self, li: ParsedLineItem) -> NormalizedLineItem:
        desc = li.raw_description.strip()
        return NormalizedLineItem(
            line_number=li.line_number,
            raw_description=li.raw_description,
            raw_quantity=li.raw_quantity,
            raw_unit_price=li.raw_unit_price,
            raw_tax_amount=li.raw_tax_amount,
            raw_line_amount=li.raw_line_amount,
            description=desc,
            normalized_description=normalize_string(desc),
            quantity=self._safe_decimal(li.raw_quantity, four_places=True),
            unit_price=self._safe_decimal(li.raw_unit_price, four_places=True),
            tax_amount=self._safe_decimal(li.raw_tax_amount),
            line_amount=self._safe_decimal(li.raw_line_amount),
        )

    @staticmethod
    def _safe_decimal(value: Any, four_places: bool = False) -> Optional[Decimal]:
        if value is None:
            return None

        text_value = str(value).strip()
        if not text_value:
            return None

        normalized_numeric = NormalizationService._coerce_numeric_string(text_value)
        if not normalized_numeric:
            return None

        try:
            Decimal(normalized_numeric)
        except Exception:
            return None

        d = to_decimal(normalized_numeric)
        if four_places:
            return d.quantize(Decimal("0.0001"))
        return d

    @staticmethod
    def _coerce_numeric_string(text_value: str) -> Optional[str]:
        candidate = re.sub(r"[^0-9,.-]", "", text_value)
        if not candidate or not re.search(r"\d", candidate):
            return None

        if "," in candidate and "." in candidate:
            if candidate.rfind(",") > candidate.rfind("."):
                candidate = candidate.replace(".", "").replace(",", ".")
            else:
                candidate = candidate.replace(",", "")
        elif "," in candidate:
            parts = candidate.split(",")
            if len(parts) > 2:
                candidate = "".join(parts)
            elif len(parts[-1]) in (2, 4):
                candidate = f"{parts[0]}.{parts[1]}"
            else:
                candidate = "".join(parts)

        if candidate.count("-") > 1:
            return None
        if "-" in candidate and not candidate.startswith("-"):
            candidate = candidate.replace("-", "")

        return candidate
