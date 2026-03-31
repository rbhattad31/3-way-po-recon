"""PO DB fallback — wraps existing PurchaseOrder model lookups.

Resolution chain
----------------
  Tier 1 (MIRROR_DB):  documents.PurchaseOrder
    The canonical internal mirror of transactional ERP PO data.
    Confidence 1.0; source_type MIRROR_DB.

  Tier 2 (DB_FALLBACK): posting_core.ERPPOReference
    Imported ERP open-PO reference snapshot (Excel/CSV batch import).
    Confidence 0.75; source_type DB_FALLBACK.
    Adds ``_source_tier: "erp_reference_snapshot"`` and a warning to the
    result so callers can alert users that the PO has not been loaded as a
    full transactional document.

Both reconciliation (via ERPResolutionService) and posting (via
PostingMappingEngine) use this fallback through the shared resolution layer.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class PODBFallback:
    """DB fallback for PO lookups.

    Tier 1 (MIRROR_DB)  -- documents.PurchaseOrder (confidence 1.0)
    Tier 2 (DB_FALLBACK) -- posting_core.ERPPOReference (confidence 0.75)
    """

    @staticmethod
    def lookup(po_number: str = "", vendor_code: str = "", **kwargs) -> ERPResolutionResult:
        """Resolve a PO via the internal two-tier DB strategy."""
        if not po_number:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.NONE,
                reason="po_number is required",
            )

        result = PODBFallback._lookup_mirror(po_number)
        if result.resolved:
            return result

        return PODBFallback._lookup_erp_reference(po_number, vendor_code)

    @staticmethod
    def _lookup_mirror(po_number: str) -> ERPResolutionResult:
        """Tier 1: documents.PurchaseOrder (canonical MIRROR_DB)."""
        try:
            from apps.core.utils import normalize_po_number
            from apps.documents.models import PurchaseOrder
        except ImportError:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.NONE,
                reason="documents app not available",
            )

        po = (
            PurchaseOrder.objects.filter(po_number=po_number).first()
            or PurchaseOrder.objects.filter(
                normalized_po_number=normalize_po_number(po_number)
            ).first()
            or PurchaseOrder.objects.filter(po_number__icontains=po_number).first()
        )
        if not po:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.MIRROR_DB,
                reason=f"PO '{po_number}' not found in internal mirror",
            )

        lines = list(po.line_items.all().values(
            "id", "line_number", "item_code", "description", "quantity",
            "unit_price", "tax_amount", "line_amount", "unit_of_measure",
        ))

        return ERPResolutionResult(
            resolved=True,
            value={
                "po_id": po.pk,
                "po_number": po.po_number,
                "vendor_name": po.vendor.name if po.vendor else None,
                "vendor_id": po.vendor_id,
                "vendor_code": (
                    po.vendor.vendor_code
                    if po.vendor and hasattr(po.vendor, "vendor_code") else None
                ),
                "po_date": str(po.po_date) if po.po_date else None,
                "currency": po.currency,
                "total_amount": str(po.total_amount) if po.total_amount is not None else None,
                "tax_amount": str(po.tax_amount) if po.tax_amount is not None else None,
                "status": po.status,
                "line_items": json.loads(json.dumps(lines, default=_decimal_default)),
                "_source_tier": "mirror",
            },
            source_type=ERPSourceType.MIRROR_DB,
            confidence=1.0,
            synced_at=getattr(po, "updated_at", None) or getattr(po, "created_at", None),
            source_keys={"po_id": str(po.pk), "po_number": po.po_number},
            reason=f"PO '{po.po_number}' resolved from internal mirror",
        )

    @staticmethod
    def _lookup_erp_reference(po_number: str, vendor_code: str = "") -> ERPResolutionResult:
        """Tier 2: posting_core.ERPPOReference (DB_FALLBACK snapshot)."""
        try:
            from apps.posting_core.models import ERPPOReference
        except ImportError:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.NONE,
                reason="posting_core app not available",
            )

        refs = list(ERPPOReference.objects.filter(po_number=po_number).select_related("batch"))
        if vendor_code:
            refs = [r for r in refs if r.vendor_code == vendor_code] or refs

        if not refs:
            return ERPResolutionResult(
                resolved=False, source_type=ERPSourceType.DB_FALLBACK,
                reason=f"PO '{po_number}' not found in internal mirror or ERP reference snapshots",
            )

        first = refs[0]
        batch = getattr(first, "batch", None)
        total = sum((r.line_amount or 0) for r in refs if r.line_amount is not None)
        lines = [
            {
                "line_number": r.po_line_number,
                "item_code": r.item_code,
                "description": r.description,
                "quantity": str(r.quantity) if r.quantity is not None else None,
                "unit_price": str(r.unit_price) if r.unit_price is not None else None,
                "line_amount": str(r.line_amount) if r.line_amount is not None else None,
                "unit_of_measure": None,
                "tax_amount": None,
            }
            for r in refs
        ]

        warning = (
            "PO resolved from imported ERP reference snapshot only. "
            "Full transactional record not available; GRN linkage and "
            "line-level tax detail may be missing."
        )
        return ERPResolutionResult(
            resolved=True,
            value={
                "po_number": po_number,
                "vendor_code": first.vendor_code,
                "currency": first.currency,
                "total_amount": str(total) if total else None,
                "status": first.status,
                "line_items": lines,
                "_source_tier": "erp_reference_snapshot",
            },
            source_type=ERPSourceType.DB_FALLBACK,
            confidence=0.75,
            source_as_of=getattr(batch, "source_as_of", None) if batch else None,
            synced_at=getattr(batch, "imported_at", None) if batch else None,
            source_keys={
                "batch_id": str(batch.pk) if batch else "",
                "po_number": po_number,
            },
            warnings=[warning],
            reason=f"PO '{po_number}' resolved from ERP reference snapshot ({len(refs)} lines)",
        )
