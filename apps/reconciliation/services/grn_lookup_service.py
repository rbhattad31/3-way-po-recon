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
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.conf import settings

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
        if erp_service is not None:
            self._erp = erp_service
        else:
            use_mirror_primary = getattr(settings, "ERP_RECON_USE_MIRROR_AS_PRIMARY", True)
            if use_mirror_primary:
                # Reconciliation path should not block on live ERP when mirror data
                # exists; run cache + DB mirror/fallback only.
                self._erp = ERPResolutionService(connector=None)
            else:
                self._erp = ERPResolutionService.with_default_connector()

    def lookup(self, purchase_order: PurchaseOrder,
               lf_parent_span=None) -> GRNSummary:
        """Resolve GRNs for a PO and return an aggregated GRNSummary.

        Args:
            purchase_order: The PO whose GRNs are being looked up.
            lf_parent_span: Optional Langfuse span for ERP resolution tracing.

        Returns:
            GRNSummary with hydrated GoodsReceiptNote objects and ERP
            provenance metadata.
        """
        result = self._erp.resolve_grn(
            po_number=purchase_order.po_number,
            lf_parent_span=lf_parent_span,
        )

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
        value = result.value or {}
        grn_ids: List[int] = value.get("grn_ids", [])
        if grn_ids:
            grns: List[GoodsReceiptNote] = list(
                GoodsReceiptNote.objects.filter(pk__in=grn_ids)
                .prefetch_related("line_items")
                .order_by("receipt_date")
            )

            if grns:
                # Aggregate received quantities per PO line item.
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

            logger.warning(
                "GRN resolution returned %d IDs for PO %s but none found in DB",
                len(grn_ids), purchase_order.po_number,
            )

        # API path: build aggregate quantities directly from resolver rows.
        rows = value.get("results") or []
        if not rows and value.get("grn_number"):
            rows = [value]

        api_summary = self._summarize_api_rows(
            purchase_order=purchase_order,
            rows=rows,
            result=result,
        )
        if api_summary is not None:
            return api_summary

        return GRNSummary(
            grn_available=False,
            erp_source_type=result.source_type,
            erp_provenance=result.to_provenance_dict(),
            is_stale=result.is_stale,
            warnings=result.warnings,
        )

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if value in (None, ""):
            return Decimal("0")
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _to_date(value: Any) -> Optional[date]:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).date()
            except Exception:
                continue
        return None

    def _summarize_api_rows(
        self,
        *,
        purchase_order: PurchaseOrder,
        rows: List[Dict[str, Any]],
        result,
    ) -> Optional[GRNSummary]:
        if not rows:
            return None

        po_lines = list(PurchaseOrderLineItem.objects.filter(purchase_order=purchase_order))
        if not po_lines:
            return None

        po_line_by_number: Dict[int, int] = {}
        po_line_by_item_code: Dict[str, List[int]] = {}
        for po_line in po_lines:
            po_line_by_number[int(po_line.line_number)] = po_line.pk
            item_code = (po_line.item_code or "").strip().upper()
            if item_code:
                po_line_by_item_code.setdefault(item_code, []).append(po_line.pk)

        received_map: Dict[int, Decimal] = {}
        grn_numbers = set()
        receipt_dates: List[date] = []
        warnings = list(result.warnings)

        for row in rows:
            po_line_id = None

            po_line_num = row.get("po_line_number") or row.get("POrderLineNum")
            if po_line_num not in (None, ""):
                try:
                    po_line_id = po_line_by_number.get(int(str(po_line_num)))
                except Exception:
                    po_line_id = None

            if po_line_id is None:
                item_code = (row.get("item_code") or row.get("ItemCode") or "").strip().upper()
                if item_code:
                    candidates = po_line_by_item_code.get(item_code, [])
                    if len(candidates) == 1:
                        po_line_id = candidates[0]
                    elif len(candidates) > 1:
                        warnings.append(
                            f"Ambiguous API GRN row mapping for item_code='{item_code}'"
                        )

            if po_line_id is None and len(po_lines) == 1:
                po_line_id = po_lines[0].pk

            if po_line_id is None:
                warnings.append("Unmapped API GRN row ignored")
                continue

            qty = self._to_decimal(row.get("grn_qty") or row.get("GRNQTY"))
            if qty <= Decimal("0"):
                continue

            received_map.setdefault(po_line_id, Decimal("0"))
            received_map[po_line_id] += qty

            grn_number = row.get("grn_number") or row.get("GRNNO")
            if grn_number:
                grn_numbers.add(str(grn_number))

            parsed_date = self._to_date(row.get("receipt_date") or row.get("GRNDATE"))
            if parsed_date:
                receipt_dates.append(parsed_date)

        if not received_map:
            return None

        fully = True
        for po_line in po_lines:
            if received_map.get(po_line.pk, Decimal("0")) < po_line.quantity:
                fully = False
                break

        latest_date = max(receipt_dates) if receipt_dates else None
        return GRNSummary(
            grn_available=True,
            grns=[],
            total_received_by_po_line=received_map,
            fully_received=fully,
            latest_receipt_date=latest_date,
            grn_count=len(grn_numbers) if grn_numbers else len(rows),
            erp_source_type=result.source_type,
            erp_provenance=result.to_provenance_dict(),
            is_stale=result.is_stale,
            warnings=warnings,
        )
