"""PO lookup service — resolves an invoice's PO reference to a PurchaseOrder."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from apps.core.utils import normalize_po_number, normalize_string, within_tolerance
from apps.documents.models import Invoice, PurchaseOrder

logger = logging.getLogger(__name__)

# Tolerance for vendor+amount discovery (percentage).
# Deliberately tight — only unambiguous matches should auto-link.
_DISCOVERY_AMOUNT_TOLERANCE_PCT = 1.0


@dataclass
class POLookupResult:
    found: bool = False
    purchase_order: Optional[PurchaseOrder] = None
    lookup_method: str = ""  # "exact" | "normalized" | "vendor_amount" | "not_found"


class POLookupService:
    """Resolve the PO number on an invoice to a PurchaseOrder record.

    Lookup strategy (in order):
      1. Exact match on ``po_number``
      2. Normalized match on ``normalized_po_number``
      3. Vendor + amount discovery (resolves vendor from raw name/alias
         if FK is missing, then matches open POs by amount)
    """

    def lookup(self, invoice: Invoice, skip_vendor_amount: bool = False) -> POLookupResult:
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

        # Fallback: deterministic vendor + amount discovery.
        # Only attempt this when the invoice has NO extracted PO number at all.
        # If the invoice *has* a PO number but it didn't match (OCR noise,
        # transposed digits, etc.), we deliberately return not_found so the
        # PO_RETRIEVAL agent can handle the fuzzy matching.
        has_po_reference = bool(invoice.po_number or invoice.raw_po_number)
        if not skip_vendor_amount and not has_po_reference:
            result = self._discover_by_vendor_amount(invoice)
            if result.found:
                return result

        logger.warning("PO not found for invoice %s (po_number=%s)", invoice.pk, invoice.po_number)
        return POLookupResult(found=False, lookup_method="not_found")

    # ------------------------------------------------------------------
    # Deterministic PO discovery
    # ------------------------------------------------------------------
    def _discover_by_vendor_amount(self, invoice: Invoice) -> POLookupResult:
        """Find a PO by matching vendor + total amount when PO number lookup failed.

        If ``invoice.vendor`` FK is missing but ``raw_vendor_name`` is
        populated, the method first attempts to resolve the vendor
        deterministically via normalised name and alias lookup.  When the
        vendor is resolved the FK is back-filled on the invoice.

        Only returns a match when exactly one open PO for the vendor has a
        total_amount within tolerance of the invoice total.  If zero or
        multiple candidates match the result is ``not_found`` — the ambiguous
        case is left for the AI agent.
        """
        if not invoice.total_amount:
            return POLookupResult(found=False, lookup_method="not_found")

        # Resolve vendor FK if missing
        vendor_id = invoice.vendor_id
        if not vendor_id:
            vendor = self._resolve_vendor(invoice.raw_vendor_name)
            if vendor:
                invoice.vendor = vendor
                invoice.save(update_fields=["vendor", "updated_at"])
                vendor_id = vendor.pk
                logger.info(
                    "Vendor resolved from raw name for invoice %s: vendor %s (%s)",
                    invoice.pk, vendor.pk, vendor.code,
                )
            else:
                return POLookupResult(found=False, lookup_method="not_found")

        candidates: List[PurchaseOrder] = list(
            PurchaseOrder.objects.filter(
                vendor_id=vendor_id,
                status="OPEN",
            ).exclude(total_amount__isnull=True)
        )

        if not candidates:
            return POLookupResult(found=False, lookup_method="not_found")

        inv_total = invoice.total_amount
        matches = [
            po for po in candidates
            if within_tolerance(inv_total, po.total_amount, _DISCOVERY_AMOUNT_TOLERANCE_PCT)
        ]

        if len(matches) == 1:
            po = matches[0]
            logger.info(
                "PO discovered (vendor+amount) for invoice %s: PO %s "
                "(vendor=%s, inv_total=%s, po_total=%s)",
                invoice.pk, po.po_number, vendor_id,
                inv_total, po.total_amount,
            )
            return POLookupResult(found=True, purchase_order=po, lookup_method="vendor_amount")

        if len(matches) > 1:
            logger.info(
                "PO discovery ambiguous for invoice %s: %d candidates for vendor %s amount %s — "
                "deferring to agent",
                invoice.pk, len(matches), vendor_id, inv_total,
            )

        return POLookupResult(found=False, lookup_method="not_found")

    # ------------------------------------------------------------------
    # Vendor resolution (deterministic alias lookup)
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_vendor(raw_vendor_name: str):
        """Resolve a raw vendor name to a Vendor via normalised name or alias."""
        from apps.vendors.models import Vendor, VendorAlias

        if not raw_vendor_name:
            return None

        norm = normalize_string(raw_vendor_name)
        if not norm:
            return None

        vendor = Vendor.objects.filter(normalized_name=norm, is_active=True).first()
        if vendor:
            return vendor

        alias = VendorAlias.objects.filter(
            normalized_alias=norm,
        ).select_related("vendor").first()
        if alias:
            return alias.vendor

        return None
