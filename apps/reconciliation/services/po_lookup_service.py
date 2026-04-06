"""PO lookup service — resolves an invoice's PO reference to a PurchaseOrder.

Thin wrapper over ERPResolutionService.resolve_po().

The reconciliation engine should call this service; it should NOT query
documents.PurchaseOrder or posting_core.ERPPOReference directly. All ERP
source selection (MIRROR_DB vs DB_FALLBACK vs API) is handled by the
shared resolution layer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from apps.core.utils import normalize_po_number, normalize_string, within_tolerance
from apps.documents.models import Invoice, PurchaseOrder
from apps.erp_integration.enums import ERPSourceType
from apps.erp_integration.services.resolution_service import ERPResolutionService

logger = logging.getLogger(__name__)

# Tolerance for vendor+amount discovery (percentage).
# Deliberately tight — only unambiguous matches should auto-link.
_DISCOVERY_AMOUNT_TOLERANCE_PCT = 1.0


@dataclass
class POLookupResult:
    """Result of a PO resolution attempt.

    Carries the hydrated PurchaseOrder ORM object alongside ERP provenance
    metadata so that downstream services (result_service, runner_service)
    can store the resolution source in ReconciliationResult.
    """

    found: bool = False
    purchase_order: Optional[PurchaseOrder] = None
    # lookup_method is now aligned with ERPSourceType values for consistency.
    # Legacy values: "exact" | "normalized" | "vendor_amount" | "not_found"
    lookup_method: str = ""
    erp_source_type: str = ERPSourceType.NONE
    erp_confidence: float = 0.0
    is_stale: bool = False
    warnings: List[str] = field(default_factory=list)
    erp_provenance: Dict[str, Any] = field(default_factory=dict)


class POLookupService:
    """Resolve the PO number on an invoice to a PurchaseOrder record.

    Delegates to ERPResolutionService for all ERP data access. Does NOT
    query documents.PurchaseOrder directly (except to hydrate an ORM object
    from a resolved PO ID, which requires a single PK lookup).

    Lookup strategy (in order):
      1. ERPResolutionService.resolve_po() — shared chain:
            cache -> MIRROR_DB (documents.PurchaseOrder)
                  -> live API
                  -> DB_FALLBACK (ERPPOReference snapshot)
      2. Vendor + amount discovery when the invoice carries no PO number
         (deterministic; only attempted for unambiguous single matches).
    """

    def __init__(self, erp_service: Optional[ERPResolutionService] = None):
        self._erp = erp_service or ERPResolutionService.with_default_connector()

    def lookup(self, invoice: Invoice, skip_vendor_amount: bool = False,
               lf_parent_span=None) -> POLookupResult:
        """Resolve PO for a single invoice.

        Args:
            invoice: The invoice whose ``po_number`` (or vendor+amount) is
                     used to find the matching PurchaseOrder.
            lf_parent_span: Optional Langfuse span for ERP resolution tracing.

        Returns:
            POLookupResult with the hydrated PurchaseOrder and provenance info.
        """
        po_number = invoice.po_number or invoice.normalized_po_number or ""

        if po_number:
            vendor_code = ""
            if invoice.vendor and hasattr(invoice.vendor, "vendor_code"):
                vendor_code = invoice.vendor.vendor_code or ""

            result = self._erp.resolve_po(
                po_number=po_number,
                vendor_code=vendor_code,
                invoice_id=invoice.pk,
                lf_parent_span=lf_parent_span,
            )

            if result.resolved:
                po = self._hydrate_po(result)
                if po:
                    logger.info(
                        "PO resolved for invoice %s: PO %s via %s (stale=%s)",
                        invoice.pk, po.po_number, result.source_type, result.is_stale,
                    )
                    return POLookupResult(
                        found=True,
                        purchase_order=po,
                        lookup_method=result.source_type,
                        erp_source_type=result.source_type,
                        erp_confidence=result.confidence,
                        is_stale=result.is_stale,
                        warnings=result.warnings,
                        erp_provenance=result.to_provenance_dict(),
                    )

        # Fallback: deterministic vendor + amount discovery.
        # Only when the invoice has NO PO number reference at all (not just a failed lookup).
        has_po_reference = bool(invoice.po_number or invoice.raw_po_number)
        if not has_po_reference and not skip_vendor_amount:
            discovery = self._discover_by_vendor_amount(invoice)
            if discovery.found:
                return discovery

        logger.warning(
            "PO not found for invoice %s (po_number=%s)", invoice.pk, invoice.po_number
        )
        return POLookupResult(found=False, lookup_method="not_found")

    # ------------------------------------------------------------------
    # PO hydration
    # ------------------------------------------------------------------

    @staticmethod
    def _hydrate_po(result) -> Optional[PurchaseOrder]:
        """Hydrate a PurchaseOrder ORM object from an ERPResolutionResult.

        When the result comes from MIRROR_DB, po_id is available directly.
        When from DB_FALLBACK (ERPPOReference snapshot), we normalise and
        search by po_number — the PO may not exist as a full document.
        """
        value = result.value or {}
        po_id = value.get("po_id")
        if po_id:
            return PurchaseOrder.objects.filter(pk=po_id).first()

        # DB_FALLBACK path: try to find the PO by number
        po_number = value.get("po_number", "")
        if po_number:
            return (
                PurchaseOrder.objects.filter(po_number=po_number).first()
                or PurchaseOrder.objects.filter(
                    normalized_po_number=normalize_po_number(po_number)
                ).first()
            )
        return None

    # ------------------------------------------------------------------
    # Deterministic vendor + amount discovery
    # ------------------------------------------------------------------

    def _discover_by_vendor_amount(self, invoice: Invoice) -> POLookupResult:
        """Find a PO by matching vendor + total amount when PO number is absent.

        Only returns a match when exactly one open PO for the vendor has a
        total_amount within tolerance of the invoice total. Zero or multiple
        candidates return not_found (ambiguous, left for the AI agent).
        """
        if not invoice.total_amount:
            return POLookupResult(found=False, lookup_method="not_found")

        vendor_id = invoice.vendor_id
        if not vendor_id:
            vendor = self._resolve_vendor_from_name(invoice.raw_vendor_name)
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

        candidates = list(
            PurchaseOrder.objects.filter(vendor_id=vendor_id, status="OPEN")
            .exclude(total_amount__isnull=True)
        )
        if not candidates:
            return POLookupResult(found=False, lookup_method="not_found")

        matches = [
            po for po in candidates
            if within_tolerance(invoice.total_amount, po.total_amount, _DISCOVERY_AMOUNT_TOLERANCE_PCT)
        ]

        if len(matches) == 1:
            po = matches[0]
            logger.info(
                "PO discovered (vendor+amount) for invoice %s: PO %s "
                "(vendor=%s, inv=%s, po=%s)",
                invoice.pk, po.po_number, vendor_id,
                invoice.total_amount, po.total_amount,
            )
            return POLookupResult(
                found=True,
                purchase_order=po,
                lookup_method="vendor_amount",
                erp_source_type=ERPSourceType.MIRROR_DB,
                erp_confidence=0.85,
                erp_provenance={
                    "source_type": ERPSourceType.MIRROR_DB,
                    "lookup_method": "vendor_amount_discovery",
                    "vendor_id": vendor_id,
                    "invoice_total": str(invoice.total_amount),
                    "po_total": str(po.total_amount),
                },
            )

        logger.info(
            "Vendor+amount discovery ambiguous for invoice %s: %d candidates",
            invoice.pk, len(matches),
        )
        return POLookupResult(found=False, lookup_method="not_found")

    @staticmethod
    def _resolve_vendor_from_name(raw_name: str) -> Optional[Any]:
        """Attempt to resolve a vendor from a raw name string via VendorAliasMapping."""
        if not raw_name:
            return None
        try:
            from apps.vendors.models import Vendor
            from apps.posting_core.models import VendorAliasMapping
            norm = normalize_string(raw_name)
            vendor = (
                Vendor.objects.filter(normalized_name=norm, is_active=True).first()
                or Vendor.objects.filter(name__iexact=raw_name, is_active=True).first()
            )
            if vendor:
                return vendor
            alias = VendorAliasMapping.objects.filter(
                normalized_alias=norm, is_active=True
            ).select_related("vendor").first()
            return alias.vendor if alias and alias.vendor else None
        except Exception:
            return None
