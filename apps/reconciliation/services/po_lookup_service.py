"""PO lookup service — resolves an invoice's PO reference to a PurchaseOrder."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from apps.core.utils import normalize_po_number
from apps.documents.models import Invoice, PurchaseOrder

logger = logging.getLogger(__name__)


@dataclass
class POLookupResult:
    found: bool = False
    purchase_order: Optional[PurchaseOrder] = None
    lookup_method: str = ""  # "exact" | "normalized" | "not_found"


class POLookupService:
    """Resolve the PO number on an invoice to a PurchaseOrder record.

    Lookup strategy (in order):
      1. Exact match on ``po_number``
      2. Normalized match on ``normalized_po_number``
    """

    def lookup(self, invoice: Invoice) -> POLookupResult:
        # Try raw PO number (exact)
        if invoice.po_number:
            po = PurchaseOrder.objects.filter(po_number=invoice.po_number).first()
            if po:
                logger.info("PO found (exact) for invoice %s: PO %s", invoice.pk, po.po_number)
                return POLookupResult(found=True, purchase_order=po, lookup_method="exact")

        # Try normalized
        norm = invoice.normalized_po_number or normalize_po_number(invoice.po_number)
        if norm:
            po = PurchaseOrder.objects.filter(normalized_po_number=norm).first()
            if po:
                logger.info("PO found (normalized) for invoice %s: PO %s", invoice.pk, po.po_number)
                return POLookupResult(found=True, purchase_order=po, lookup_method="normalized")

        logger.warning("PO not found for invoice %s (po_number=%s)", invoice.pk, invoice.po_number)
        return POLookupResult(found=False, lookup_method="not_found")
