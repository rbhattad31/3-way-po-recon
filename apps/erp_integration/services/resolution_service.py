"""Shared ERP Resolution Service — single entry point for all ERP lookups.

Architecture
------------
This module provides the ONE shared resolution contract used by both the
reconciliation engine and the posting pipeline. Neither module should
invent its own ERP lookup chain.

Resolution order (highest priority first)
------------------------------------------
1. Cache (ERPReferenceCacheRecord, TTL-controlled)
2. Internal ERP mirror tables (documents.PurchaseOrder, documents.GoodsReceiptNote)
   source_type = MIRROR_DB
3. Live ERP API connector (when connector is available and capable)
   source_type = API
4. Reference import snapshots (ERPVendorReference, ERPItemReference, etc.)
   source_type = DB_FALLBACK
5. Not resolved -> source_type = NONE

Freshness
---------
After any DB resolution, the service checks ``synced_at`` against the
configured staleness threshold (ERP_TRANSACTIONAL_FRESHNESS_HOURS for
PO/GRN; ERP_MASTER_FRESHNESS_HOURS for vendor/item/tax/cost-center).
Stale results have ``is_stale=True`` and ``stale_reason`` populated but
are still returned -- staleness is a warning, not a hard failure.

Live refresh on stale data is controlled by ERP_ENABLE_LIVE_REFRESH_ON_STALE.
Live refresh on miss is controlled by ERP_ENABLE_LIVE_REFRESH_ON_MISS.

Usage
-----
    # Reconciliation PO lookup
    svc = ERPResolutionService.with_default_connector()
    result = svc.resolve_po(po_number="PO-2601", invoice_id=invoice.pk)

    # Posting vendor resolution
    svc = ERPResolutionService.with_default_connector()
    result = svc.resolve_vendor(vendor_code="V001", posting_run_id=run.pk)

    # Agent tool
    svc = ERPResolutionService.with_default_connector()
    result = svc.resolve_grn(po_number="PO-2601")
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

from apps.erp_integration.enums import ERPDataDomain, ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)


class ERPResolutionService:
    """Centralised ERP resolution service.

    Both reconciliation and posting use this class as their single
    gateway to ERP data.  The matching logic (TwoWayMatchService,
    ThreeWayMatchService) and the mapping engine (PostingMappingEngine)
    must not query ERP data directly.

    Parameters
    ----------
    connector:
        An active BaseERPConnector instance, or None when live API calls
        are not available.  When None, the service falls through to the
        internal mirror and reference imports only.
    """

    def __init__(self, connector=None):
        self._connector = connector

    # ------------------------------------------------------------------
    # Internal Langfuse helper
    # ------------------------------------------------------------------

    @staticmethod
    def _trace_resolve(resolution_name: str, resolve_fn, safe_meta: dict, lf_parent_span):
        """Wrap a resolver call with an optional Langfuse child span.

        Creates a child span under ``lf_parent_span`` (when provided), calls
        ``resolve_fn()``, then closes the span with safe provenance metadata.
        Never raises -- Langfuse errors are swallowed so resolution always
        proceeds regardless of observability state.

        The output recorded in Langfuse is limited to non-sensitive metadata:
        resolved, source_type, cache_hit, fallback_used, confidence, is_stale.
        Full ERP payloads are never logged.
        """
        _lf_span = None
        try:
            if lf_parent_span is not None:
                from apps.core.langfuse_client import start_span
                _lf_span = start_span(
                    lf_parent_span,
                    f"erp_{resolution_name}",
                    metadata=safe_meta,
                )
        except Exception:
            pass

        result = None
        try:
            result = resolve_fn()
            return result
        finally:
            try:
                if _lf_span is not None:
                    from apps.core.langfuse_client import end_span
                    from apps.erp_integration.enums import ERPSourceType as _ST
                    _out = {"resolved": False}
                    if result is not None:
                        _out = {
                            "resolved": bool(result.resolved),
                            "source_type": str(result.source_type) if result.source_type else "",
                            "cache_hit": str(result.source_type) == str(_ST.CACHE),
                            "fallback_used": bool(result.fallback_used),
                            "confidence": float(result.confidence or 0.0),
                            "is_stale": bool(getattr(result, "is_stale", False)),
                        }
                    end_span(
                        _lf_span,
                        output=_out,
                        level="DEFAULT" if (result is not None and result.resolved) else "WARNING",
                    )
            except Exception:
                pass

    @classmethod
    def with_default_connector(cls) -> "ERPResolutionService":
        """Create a service instance bound to the default active ERP connector.

        Returns a connector-less instance when no active ERPConnection exists.
        """
        connector = None
        try:
            from apps.erp_integration.services.connector_factory import ConnectorFactory
            connector = ConnectorFactory.get_default_connector()
        except Exception:
            logger.debug("Could not load default ERP connector", exc_info=True)
        return cls(connector=connector)

    # ------------------------------------------------------------------
    # Transactional data (PO, GRN)
    # ------------------------------------------------------------------

    def resolve_po(
        self,
        po_number: str,
        vendor_code: str = "",
        *,
        invoice_id: Optional[int] = None,
        reconciliation_result_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Resolve a Purchase Order.

        Uses the shared resolution chain:
          cache -> MIRROR_DB (documents.PurchaseOrder)
                -> live API (if connector available)
                -> DB_FALLBACK (ERPPOReference snapshot)

        Returns an ERPResolutionResult with provenance metadata.
        """
        from apps.erp_integration.services.resolution.po_resolver import POResolver
        resolver = POResolver()

        def _resolve():
            result = resolver.resolve(
                self._connector,
                po_number=po_number,
                vendor_code=vendor_code,
                invoice_id=invoice_id,
                reconciliation_result_id=reconciliation_result_id,
                posting_run_id=posting_run_id,
            )
            return self._apply_freshness(result, ERPDataDomain.TRANSACTIONAL)

        return self._trace_resolve(
            "resolve_po",
            _resolve,
            {"po_number": po_number, "vendor_code": vendor_code, "invoice_id": invoice_id},
            lf_parent_span,
        )

    def resolve_grn(
        self,
        po_number: str = "",
        grn_number: str = "",
        *,
        invoice_id: Optional[int] = None,
        reconciliation_result_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Resolve Goods Receipt Notes for a PO.

        The resolved ``value`` dict contains:
          - ``grn_ids``: list of GoodsReceiptNote PKs so callers can hydrate
            ORM objects for GRN line-level matching.
          - ``grns``: serialised GRN data for display/audit.
          - ``grn_count``: number of GRNs found.
        """
        from apps.erp_integration.services.resolution.grn_resolver import GRNResolver
        resolver = GRNResolver()

        def _resolve():
            result = resolver.resolve(
                self._connector,
                po_number=po_number,
                grn_number=grn_number,
                invoice_id=invoice_id,
                reconciliation_result_id=reconciliation_result_id,
            )
            return self._apply_freshness(result, ERPDataDomain.TRANSACTIONAL)

        return self._trace_resolve(
            "resolve_grn",
            _resolve,
            {"po_number": po_number, "grn_number": grn_number, "invoice_id": invoice_id},
            lf_parent_span,
        )

    def refresh_po(
        self,
        po_number: str,
        vendor_code: str = "",
        *,
        posting_run_id: Optional[int] = None,
    ) -> ERPResolutionResult:
        """Force a live ERP refresh for a PO, bypassing cache and mirror.

        Only effective when a live connector is available. Falls back to
        the standard resolution chain when no connector is configured.
        """
        if self._connector is None:
            logger.info(
                "refresh_po called without a live connector, falling back to standard resolve"
            )
            return self.resolve_po(po_number, vendor_code, posting_run_id=posting_run_id)
        from apps.erp_integration.services.resolution.po_resolver import POResolver
        resolver = POResolver()
        resolver.use_cache = False  # bypass cache for forced refresh
        result = resolver.resolve(
            self._connector,
            po_number=po_number,
            vendor_code=vendor_code,
            posting_run_id=posting_run_id,
        )
        return self._apply_freshness(result, ERPDataDomain.TRANSACTIONAL)

    def refresh_grn(
        self,
        po_number: str,
        *,
        reconciliation_result_id: Optional[int] = None,
    ) -> ERPResolutionResult:
        """Force a live ERP refresh for GRNs, bypassing cache and mirror."""
        if self._connector is None:
            logger.info(
                "refresh_grn called without a live connector, falling back to standard resolve"
            )
            return self.resolve_grn(
                po_number=po_number,
                reconciliation_result_id=reconciliation_result_id,
            )
        from apps.erp_integration.services.resolution.grn_resolver import GRNResolver
        resolver = GRNResolver()
        resolver.use_cache = False
        result = resolver.resolve(
            self._connector,
            po_number=po_number,
            reconciliation_result_id=reconciliation_result_id,
        )
        return self._apply_freshness(result, ERPDataDomain.TRANSACTIONAL)

    # ------------------------------------------------------------------
    # Master / reference data (vendor, item, tax, cost center)
    # ------------------------------------------------------------------

    def resolve_vendor(
        self,
        vendor_code: str = "",
        vendor_name: str = "",
        *,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Resolve a vendor from the ERP reference data."""
        from apps.erp_integration.services.resolution.vendor_resolver import VendorResolver
        resolver = VendorResolver()

        def _resolve():
            result = resolver.resolve(
                self._connector,
                vendor_code=vendor_code,
                vendor_name=vendor_name,
                invoice_id=invoice_id,
                posting_run_id=posting_run_id,
            )
            return self._apply_freshness(result, ERPDataDomain.MASTER)

        return self._trace_resolve(
            "resolve_vendor",
            _resolve,
            {"vendor_code": vendor_code, "invoice_id": invoice_id},
            lf_parent_span,
        )

    def resolve_item(
        self,
        item_code: str = "",
        description: str = "",
        *,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Resolve an item/service from the ERP reference data."""
        from apps.erp_integration.services.resolution.item_resolver import ItemResolver
        resolver = ItemResolver()

        def _resolve():
            result = resolver.resolve(
                self._connector,
                item_code=item_code,
                description=description,
                invoice_id=invoice_id,
                posting_run_id=posting_run_id,
            )
            return self._apply_freshness(result, ERPDataDomain.MASTER)

        return self._trace_resolve(
            "resolve_item",
            _resolve,
            {"item_code": item_code, "invoice_id": invoice_id},
            lf_parent_span,
        )

    def resolve_tax_code(
        self,
        tax_code: str = "",
        rate: float = 0.0,
        *,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Resolve a tax code from the ERP reference data."""
        from apps.erp_integration.services.resolution.tax_resolver import TaxResolver
        resolver = TaxResolver()

        def _resolve():
            result = resolver.resolve(
                self._connector,
                tax_code=tax_code,
                rate=rate,
                posting_run_id=posting_run_id,
            )
            return self._apply_freshness(result, ERPDataDomain.MASTER)

        return self._trace_resolve(
            "resolve_tax_code",
            _resolve,
            {"tax_code": tax_code},
            lf_parent_span,
        )

    def resolve_cost_center(
        self,
        cost_center_code: str = "",
        *,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Resolve a cost center from the ERP reference data."""
        from apps.erp_integration.services.resolution.cost_center_resolver import CostCenterResolver
        resolver = CostCenterResolver()

        def _resolve():
            result = resolver.resolve(
                self._connector,
                cost_center_code=cost_center_code,
                posting_run_id=posting_run_id,
            )
            return self._apply_freshness(result, ERPDataDomain.MASTER)

        return self._trace_resolve(
            "resolve_cost_center",
            _resolve,
            {"cost_center_code": cost_center_code},
            lf_parent_span,
        )

    def check_invoice_duplicate(
        self,
        invoice_number: str,
        vendor_code: str = "",
        *,
        invoice_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
    ) -> ERPResolutionResult:
        """Check whether an invoice number has already been posted in the ERP."""
        from apps.erp_integration.services.resolution.duplicate_invoice_resolver import DuplicateInvoiceResolver
        resolver = DuplicateInvoiceResolver()

        def _resolve():
            return resolver.resolve(
                self._connector,
                invoice_number=invoice_number,
                vendor_code=vendor_code,
                invoice_id=invoice_id,
                posting_run_id=posting_run_id,
            )

        return self._trace_resolve(
            "check_duplicate",
            _resolve,
            {"invoice_id": invoice_id},
            lf_parent_span,
        )

    # ------------------------------------------------------------------
    # Freshness checking
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_freshness(
        result: ERPResolutionResult,
        domain: str,
    ) -> ERPResolutionResult:
        """Tag the result with staleness info based on the configured threshold.

        Runs after every DB-based resolution (MIRROR_DB, DB_FALLBACK).
        Cache and live API results are considered always-fresh.

        Modifies result in-place and returns it.
        """
        if not result.resolved:
            return result

        # Cache hits and live API results are inherently fresh
        if result.source_type in (ERPSourceType.CACHE, ERPSourceType.API):
            return result

        if domain == ERPDataDomain.TRANSACTIONAL:
            max_age_hours = getattr(settings, "ERP_TRANSACTIONAL_FRESHNESS_HOURS", 24)
        else:
            max_age_hours = getattr(settings, "ERP_MASTER_FRESHNESS_HOURS", 168)

        # Use synced_at (preferred) or freshness_timestamp (legacy) for the check
        check_ts = result.synced_at or result.freshness_timestamp
        if check_ts is None:
            # No timestamp available — we cannot assess freshness
            return result

        threshold = timezone.now() - datetime.timedelta(hours=max_age_hours)
        if check_ts < threshold:
            result.is_stale = True
            result.stale_reason = (
                f"Data synced at {check_ts.isoformat()} exceeds "
                f"{max_age_hours}h freshness threshold for domain '{domain}'"
            )
            result.warnings.append(result.stale_reason)
            logger.info(
                "ERP resolution stale: domain=%s source=%s synced=%s threshold=%s",
                domain, result.source_type, check_ts.isoformat(), threshold.isoformat(),
            )

            # Optionally trigger live refresh (non-blocking log only in sync path)
            if getattr(settings, "ERP_ENABLE_LIVE_REFRESH_ON_STALE", False):
                logger.info(
                    "ERP_ENABLE_LIVE_REFRESH_ON_STALE=True but live refresh is async-only. "
                    "Schedule a background refresh task for this record."
                )

        return result
