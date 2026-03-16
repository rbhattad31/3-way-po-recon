"""Normalization service — cleans and standardises extracted invoice values."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional
from datetime import date

from apps.core.utils import (
    normalize_category,
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
    raw_item_category: str = ""
    raw_quantity: str = ""
    raw_unit_price: str = ""
    raw_tax_amount: str = ""
    raw_line_amount: str = ""
    description: str = ""
    item_category: str = ""
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
            raw_item_category=li.raw_item_category,
            raw_quantity=li.raw_quantity,
            raw_unit_price=li.raw_unit_price,
            raw_tax_amount=li.raw_tax_amount,
            raw_line_amount=li.raw_line_amount,
            description=desc,
            item_category=normalize_category(li.raw_item_category),
            normalized_description=normalize_string(desc),
            quantity=self._safe_decimal(li.raw_quantity, four_places=True),
            unit_price=self._safe_decimal(li.raw_unit_price, four_places=True),
            tax_amount=self._safe_decimal(li.raw_tax_amount),
            line_amount=self._safe_decimal(li.raw_line_amount),
        )

    @staticmethod
    def _safe_decimal(value: str, four_places: bool = False) -> Optional[Decimal]:
        if not value or not value.strip():
            return None
        d = to_decimal(value)
        if d == Decimal("0.00") and value.strip() not in ("0", "0.0", "0.00"):
            return None
        if four_places:
            return d.quantize(Decimal("0.0001"))
        return d
