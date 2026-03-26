"""ERP Reference Cache Service — TTL-based caching with invalidation."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone

from apps.erp_integration.enums import ERPResolutionType, ERPSourceType
from apps.erp_integration.models import ERPReferenceCacheRecord
from apps.erp_integration.services.connectors.base import ERPResolutionResult

logger = logging.getLogger(__name__)

# Default TTL in seconds (1 hour)
DEFAULT_CACHE_TTL = getattr(settings, "ERP_CACHE_TTL_SECONDS", 3600)


class ERPCacheService:
    """TTL-based cache for ERP reference lookups.

    Never permanently suppresses API calls — expired entries are ignored.
    Invalidation is triggered on batch reference imports.
    """

    @staticmethod
    def build_cache_key(resolution_type: str, **lookup_params) -> str:
        """Build a deterministic cache key from resolution type + params."""
        sorted_params = json.dumps(
            {k: v for k, v in sorted(lookup_params.items()) if v},
            sort_keys=True,
            default=str,
        )
        param_hash = hashlib.sha256(sorted_params.encode()).hexdigest()[:16]
        return f"erp:{resolution_type}:{param_hash}"

    @staticmethod
    def get(resolution_type: str, **lookup_params) -> Optional[ERPResolutionResult]:
        """Look up a cached resolution result. Returns None on miss/expiry."""
        cache_key = ERPCacheService.build_cache_key(resolution_type, **lookup_params)
        try:
            record = ERPReferenceCacheRecord.objects.filter(
                cache_key=cache_key,
                expires_at__gt=timezone.now(),
            ).first()
            if record is None:
                return None
            return ERPResolutionResult(
                resolved=True,
                value=record.value_json,
                source_type=ERPSourceType.CACHE,
                connector_name=record.connector_name,
                reason="Cache hit",
                metadata={"cache_key": cache_key},
            )
        except Exception:
            logger.exception("Cache lookup failed for key=%s", cache_key)
            return None

    @staticmethod
    def put(
        resolution_type: str,
        result: ERPResolutionResult,
        ttl_seconds: int | None = None,
        **lookup_params,
    ) -> None:
        """Store a successful resolution result in cache."""
        if not result.resolved or result.value is None:
            return
        cache_key = ERPCacheService.build_cache_key(resolution_type, **lookup_params)
        ttl = ttl_seconds or DEFAULT_CACHE_TTL
        expires_at = timezone.now() + timedelta(seconds=ttl)
        try:
            ERPReferenceCacheRecord.objects.update_or_create(
                cache_key=cache_key,
                defaults={
                    "resolution_type": resolution_type,
                    "connector_name": result.connector_name,
                    "value_json": result.value or {},
                    "expires_at": expires_at,
                    "source_type": result.source_type,
                },
            )
        except Exception:
            logger.exception("Cache write failed for key=%s", cache_key)

    @staticmethod
    def invalidate_by_type(resolution_type: str) -> int:
        """Invalidate all cached entries for a given resolution type.

        Called when a new reference batch is imported.
        Returns the number of records deleted.
        """
        try:
            count, _ = ERPReferenceCacheRecord.objects.filter(
                resolution_type=resolution_type,
            ).delete()
            logger.info(
                "Invalidated %d cache entries for type=%s", count, resolution_type
            )
            return count
        except Exception:
            logger.exception("Cache invalidation failed for type=%s", resolution_type)
            return 0

    @staticmethod
    def invalidate_all() -> int:
        """Invalidate all cached ERP reference entries."""
        try:
            count, _ = ERPReferenceCacheRecord.objects.all().delete()
            logger.info("Invalidated all %d ERP cache entries", count)
            return count
        except Exception:
            logger.exception("Full cache invalidation failed")
            return 0

    @staticmethod
    def cleanup_expired() -> int:
        """Remove expired cache records. Intended for periodic cleanup."""
        try:
            count, _ = ERPReferenceCacheRecord.objects.filter(
                expires_at__lte=timezone.now(),
            ).delete()
            if count:
                logger.info("Cleaned up %d expired ERP cache entries", count)
            return count
        except Exception:
            logger.exception("Expired cache cleanup failed")
            return 0
