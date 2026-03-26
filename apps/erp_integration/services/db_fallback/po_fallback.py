"""PO DB fallback — wraps existing PurchaseOrder model lookups."""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class PODBFallback:
    """DB fallback for PO lookups using the documents.PurchaseOrder model."""

    @staticmethod
    def lookup(po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        """Look up PO from local database.

        Tries exact → normalized → contains matching (same as POLookupTool).
        """
        from apps.core.utils import normalize_po_number
        from apps.documents.models import PurchaseOrder

        if not po_number:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason="po_number is required",
            )

        po = PurchaseOrder.objects.filter(po_number=po_number).first()
        if not po:
            norm = normalize_po_number(po_number)
            po = PurchaseOrder.objects.filter(normalized_po_number=norm).first()
        if not po:
            po = PurchaseOrder.objects.filter(po_number__icontains=po_number).first()

        if not po:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason=f"PO '{po_number}' not found in database",
            )

        lines = list(po.line_items.all().values(
            "line_number", "item_code", "description", "quantity",
            "unit_price", "tax_amount", "line_amount", "unit_of_measure",
        ))

        return ERPResolutionResult(
            resolved=True,
            value={
                "po_id": po.pk,
                "po_number": po.po_number,
                "vendor_name": po.vendor.name if po.vendor else None,
                "vendor_id": po.vendor_id,
                "po_date": str(po.po_date) if po.po_date else None,
                "currency": po.currency,
                "total_amount": str(po.total_amount) if po.total_amount else None,
                "tax_amount": str(po.tax_amount) if po.tax_amount else None,
                "status": po.status,
                "line_items": json.loads(json.dumps(lines, default=_decimal_default)),
            },
            source_type=ERPSourceType.DB_FALLBACK,
            confidence=1.0,
            reason=f"PO found in database: {po.po_number}",
        )
