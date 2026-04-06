"""Base Resolver — common resolution pattern for all ERP lookups.

Resolution flow:
  1. Check connector capability
  2. Check cache (if enabled)
  3. Call ERP API via connector
  4. Fallback to DB adapter (if API fails or unsupported)
  5. Return structured ERPResolutionResult with source metadata
  6. Log resolution to ERPResolutionLog + audit
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from apps.erp_integration.enums import ERPResolutionType, ERPSourceType
from apps.erp_integration.models import ERPResolutionLog
from apps.erp_integration.services.audit_service import ERPAuditService
from apps.erp_integration.services.cache_service import ERPCacheService
from apps.erp_integration.services.connectors.base import (
    BaseERPConnector,
    ERPResolutionResult,
)

logger = logging.getLogger(__name__)


class BaseResolver:
    """Abstract base class for ERP resolvers.

    Subclasses must implement:
      - resolution_type: ERPResolutionType value
      - _check_capability(connector) -> bool
      - _api_lookup(connector, **params) -> ERPResolutionResult
      - _db_fallback(**params) -> ERPResolutionResult
    """

    resolution_type: str = ""
    use_cache: bool = True

    def resolve(
        self,
        connector: Optional[BaseERPConnector],
        *,
        invoice_id: Optional[int] = None,
        reconciliation_result_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
        lf_parent_span=None,
        **lookup_params,
    ) -> ERPResolutionResult:
        """Run the full resolution chain: cache -> API -> DB fallback.

        When ``lf_parent_span`` is provided, per-stage child spans
        (erp_cache_lookup, erp_live_lookup, erp_db_fallback) are
        created under it for Langfuse visibility.
        """
        start = time.monotonic()
        lookup_key = self._build_lookup_key(**lookup_params)

        # 1. Cache check
        if self.use_cache:
            cached = self._cache_check_traced(lf_parent_span, **lookup_params)
            if cached is not None:
                self._log_resolution(
                    cached, lookup_key, start,
                    invoice_id=invoice_id,
                    reconciliation_result_id=reconciliation_result_id,
                    posting_run_id=posting_run_id,
                )
                return cached

        # 2. API lookup (if connector available and capable)
        api_result = None
        if connector is not None and self._check_capability(connector):
            api_result = self._api_lookup_traced(
                connector, lf_parent_span, **lookup_params,
            )
            if api_result is not None and api_result.resolved:
                api_result.source_type = ERPSourceType.API
                if self.use_cache:
                    ERPCacheService.put(
                        self.resolution_type, api_result, **lookup_params
                    )
                self._log_resolution(
                    api_result, lookup_key, start,
                    invoice_id=invoice_id,
                    reconciliation_result_id=reconciliation_result_id,
                    posting_run_id=posting_run_id,
                )
                return api_result

        # 3. DB fallback
        db_result = self._db_fallback_traced(lf_parent_span, **lookup_params)
        if db_result is not None:
            db_result.fallback_used = True
            if not db_result.source_type or db_result.source_type == ERPSourceType.NONE:
                db_result.source_type = ERPSourceType.DB_FALLBACK
            self._log_resolution(
                db_result, lookup_key, start,
                invoice_id=invoice_id,
                reconciliation_result_id=reconciliation_result_id,
                posting_run_id=posting_run_id,
            )
            return db_result

        duration = int((time.monotonic() - start) * 1000)
        return ERPResolutionResult(
            resolved=False,
            source_type=ERPSourceType.NONE,
            reason=f"Both API and DB fallback failed for {self.resolution_type}",
            metadata={"duration_ms": duration},
        )

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    def _check_capability(self, connector: BaseERPConnector) -> bool:
        raise NotImplementedError

    def _api_lookup(self, connector: BaseERPConnector, **params) -> ERPResolutionResult:
        raise NotImplementedError

    def _db_fallback(self, **params) -> ERPResolutionResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Per-stage traced wrappers (Langfuse child spans)
    # ------------------------------------------------------------------

    def _cache_check_traced(self, lf_parent_span, **lookup_params):
        """Cache lookup with optional Langfuse span."""
        cache_key = ERPCacheService.build_cache_key(self.resolution_type, **lookup_params)

        def _do_cache():
            return ERPCacheService.get(self.resolution_type, **lookup_params)

        if lf_parent_span is None:
            return _do_cache()
        try:
            from apps.erp_integration.services.langfuse_helpers import trace_erp_cache_lookup
            return trace_erp_cache_lookup(
                lf_parent_span, _do_cache,
                cache_key=cache_key,
                resolution_type=self.resolution_type,
            )
        except Exception:
            return _do_cache()

    def _api_lookup_traced(self, connector, lf_parent_span, **lookup_params):
        """API lookup with optional Langfuse span."""
        def _do_api():
            return self._api_lookup(connector, **lookup_params)

        if lf_parent_span is None:
            try:
                return _do_api()
            except Exception:
                logger.exception(
                    "API lookup failed for %s", self.resolution_type,
                )
                return None
        try:
            from apps.erp_integration.services.langfuse_helpers import trace_erp_live_lookup
            return trace_erp_live_lookup(
                lf_parent_span, _do_api,
                connector_name=getattr(connector, "connector_name", ""),
                capability=self.resolution_type,
                resolution_type=self.resolution_type,
            )
        except Exception:
            logger.exception(
                "API lookup failed for %s", self.resolution_type,
            )
            return None

    def _db_fallback_traced(self, lf_parent_span, **lookup_params):
        """DB fallback with optional Langfuse span."""
        def _do_fallback():
            return self._db_fallback(**lookup_params)

        if lf_parent_span is None:
            try:
                return _do_fallback()
            except Exception:
                logger.exception(
                    "DB fallback failed for %s", self.resolution_type,
                )
                return None
        try:
            from apps.erp_integration.services.langfuse_helpers import trace_erp_db_fallback
            return trace_erp_db_fallback(
                lf_parent_span, _do_fallback,
                fallback_source_name=self.resolution_type,
                resolution_type=self.resolution_type,
            )
        except Exception:
            logger.exception(
                "DB fallback failed for %s", self.resolution_type,
            )
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_lookup_key(self, **params) -> str:
        """Build a human-readable lookup key from params."""
        parts = [f"{k}={v}" for k, v in sorted(params.items()) if v]
        return f"{self.resolution_type}:{','.join(parts)}" if parts else self.resolution_type

    def _log_resolution(
        self,
        result: ERPResolutionResult,
        lookup_key: str,
        start_time: float,
        *,
        invoice_id: Optional[int] = None,
        reconciliation_result_id: Optional[int] = None,
        posting_run_id: Optional[int] = None,
    ) -> None:
        """Persist resolution to ERPResolutionLog and audit service."""
        duration_ms = int((time.monotonic() - start_time) * 1000)
        try:
            ERPResolutionLog.objects.create(
                resolution_type=self.resolution_type,
                lookup_key=lookup_key[:500],
                source_type=result.source_type or ERPSourceType.NONE,
                resolved=result.resolved,
                fallback_used=result.fallback_used,
                confidence=result.confidence,
                connector_name=result.connector_name,
                reason=result.reason or "",
                value_json=result.value or {},
                metadata_json=result.metadata or {},
                freshness_timestamp=result.freshness_timestamp,
                duration_ms=duration_ms,
                related_invoice_id=invoice_id,
                related_reconciliation_result_id=reconciliation_result_id,
                related_posting_run_id=posting_run_id,
            )
        except Exception:
            logger.exception("Failed to persist ERPResolutionLog for %s", lookup_key)

        ERPAuditService.log_resolution(
            event_type="ERP_RESOLUTION",
            description=f"{self.resolution_type} lookup: {lookup_key[:100]}",
            resolution_type=self.resolution_type,
            lookup_key=lookup_key[:200],
            source_type=result.source_type or ERPSourceType.NONE,
            resolved=result.resolved,
            invoice_id=invoice_id,
            reconciliation_result_id=reconciliation_result_id,
            posting_run_id=posting_run_id,
            connector_name=result.connector_name,
            duration_ms=duration_ms,
        )
