"""GRN lookup service — retrieves Goods Receipt Notes linked to a PO.

Thin wrapper over ERPResolutionService.resolve_grn().

The reconciliation engine must call this service; it must NOT query
documents.GoodsReceiptNote directly. All source selection (MIRROR_DB
vs API) is handled by the shared resolution layer. The ORM objects
required for GRN line-level matching are hydrated here from the
``grn_ids`` returned by the resolution layer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from apps.documents.models import (
    GoodsReceiptNote,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.resolution_service import ERPResolutionService

logger = logging.getLogger(__name__)


@dataclass
class GRNSummary:
    """Aggregated GRN data for a single PO."""

    grn_available: bool = False
    grns: List[GoodsReceiptNote] = field(default_factory=list)
    total_received_by_po_line: Dict[int, Decimal] = field(default_factory=dict)
    fully_received: bool = False
    latest_receipt_date: Optional[date] = None
    grn_count: int = 0
    # ERP provenance (populated by GRNLookupService from ERPResolutionResult)
    erp_source_type: str = ERPSourceType.NONE
    erp_provenance: Dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False
    warnings: List[str] = field(default_factory=list)


class GRNLookupService:
    """Look up and aggregate GRN data for a Purchase Order.

    Delegates to ERPResolutionService for all ERP data access, then
    hydrates GoodsReceiptNote ORM objects from the resolved GRN IDs so
    that GRNMatchService can perform line-level quantity comparisons.

    Resolution chain (managed by ERPResolutionService):
      cache -> MIRROR_DB (documents.GoodsReceiptNote)
            -> live API (if connector available and capable)
    """

    def __init__(self, erp_service: Optional[ERPResolutionService] = None):
        self._erp = erp_service or ERPResolutionService.with_default_connector()

    def lookup(self, purchase_order: PurchaseOrder) -> GRNSummary:
        """Resolve GRNs for a PO and return an aggregated GRNSummary.

        Args:
            purchase_order: The PO whose GRNs are being looked up.

        Returns:
            GRNSummary with hydrated GoodsReceiptNote objects and ERP
            provenance metadata.
        """
        result = self._erp.resolve_grn(po_number=purchase_order.po_number)

        if not result.resolved:
            logger.info(
                "No GRNs resolved for PO %s via %s: %s",
                purchase_order.po_number, result.source_type, result.reason,
            )
            return GRNSummary(
                grn_available=False,
                erp_source_type=result.source_type,
                erp_provenance=result.to_provenance_dict(),
                is_stale=result.is_stale,
                warnings=result.warnings,
            )

        # Hydrate ORM objects from the GRN PKs returned by the resolution layer.
        # This is a PK-based prefetch — efficient even for large datasets.
        grn_ids: List[int] = (result.value or {}).get("grn_ids", [])
        grns: List[GoodsReceiptNote] = list(
            GoodsReceiptNote.objects.filter(pk__in=grn_ids)
            .prefetch_related("line_items")
            .order_by("receipt_date")
        )

        if not grns:
            logger.warning(
                "GRN resolution returned %d IDs for PO %s but none found in DB",
                len(grn_ids), purchase_order.po_number,
            )
            return GRNSummary(
                grn_available=False,
                erp_source_type=result.source_type,
                erp_provenance=result.to_provenance_dict(),
                is_stale=result.is_stale,
                warnings=result.warnings,
            )

        # Aggregate received quantities per PO line item
        received_map: Dict[int, Decimal] = {}
        for grn in grns:
            for grn_line in grn.line_items.all():
                if grn_line.po_line_id:
                    received_map.setdefault(grn_line.po_line_id, Decimal("0"))
                    received_map[grn_line.po_line_id] += (
                        grn_line.quantity_accepted
                        if grn_line.quantity_accepted is not None
                        else grn_line.quantity_received or Decimal("0")
                    )

        # Determine if fully received
        po_lines = list(PurchaseOrderLineItem.objects.filter(purchase_order=purchase_order))
        fully = True
        for pol in po_lines:
            if received_map.get(pol.pk, Decimal("0")) < pol.quantity:
                fully = False
                break

        receipt_dates = [g.receipt_date for g in grns if g.receipt_date]
        latest_date = max(receipt_dates) if receipt_dates else None

        summary = GRNSummary(
            grn_available=True,
            grns=grns,
            total_received_by_po_line=received_map,
            fully_received=fully,
            latest_receipt_date=latest_date,
            grn_count=len(grns),
            erp_source_type=result.source_type,
            erp_provenance=result.to_provenance_dict(),
            is_stale=result.is_stale,
            warnings=result.warnings,
        )
        logger.info(
            "GRN lookup for PO %s: %d GRNs, fully_received=%s, source=%s, stale=%s",
            purchase_order.po_number, len(grns), fully,
            result.source_type, result.is_stale,
        )
        return summary
