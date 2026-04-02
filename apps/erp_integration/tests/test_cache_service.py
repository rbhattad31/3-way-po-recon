"""Tests for ERPCacheService -- TTL-based ERP reference caching."""
from __future__ import annotations

import pytest
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from apps.erp_integration.enums import ERPResolutionType, ERPSourceType


@pytest.fixture
def _cache(db):
    """Provide access to the cache service."""
    from apps.erp_integration.services.cache_service import ERPCacheService
    return ERPCacheService


@pytest.fixture
def _make_result():
    """Return a factory for ERPResolutionResult objects."""
    from apps.erp_integration.services.connectors.base import ERPResolutionResult

    def _make(**overrides):
        defaults = {
            "resolved": True,
            "value": {"vendor_code": "V001", "vendor_name": "Test"},
            "source_type": ERPSourceType.API,
            "connector_name": "test-conn",
            "reason": "Lookup success",
        }
        defaults.update(overrides)
        return ERPResolutionResult(**defaults)
    return _make


class TestBuildCacheKey:
    """CK-01 to CK-04: Cache key generation."""

    def test_deterministic(self, _cache):
        """CK-01: Same inputs produce same key."""
        key1 = _cache.build_cache_key("VENDOR", vendor_code="V001")
        key2 = _cache.build_cache_key("VENDOR", vendor_code="V001")
        assert key1 == key2

    def test_different_type_different_key(self, _cache):
        """CK-02: Different resolution type produces different key."""
        key_v = _cache.build_cache_key("VENDOR", vendor_code="V001")
        key_i = _cache.build_cache_key("ITEM", vendor_code="V001")
        assert key_v != key_i

    def test_different_params_different_key(self, _cache):
        """CK-03: Different params produce different key."""
        key1 = _cache.build_cache_key("VENDOR", vendor_code="V001")
        key2 = _cache.build_cache_key("VENDOR", vendor_code="V002")
        assert key1 != key2

    def test_empty_params_ignored(self, _cache):
        """CK-04: Empty/None params are excluded from key."""
        key1 = _cache.build_cache_key("VENDOR", vendor_code="V001", vendor_name="")
        key2 = _cache.build_cache_key("VENDOR", vendor_code="V001")
        assert key1 == key2


class TestPutAndGet:
    """PG-01 to PG-05: Cache put + get lifecycle."""

    @pytest.mark.django_db
    def test_put_then_get(self, _cache, _make_result):
        """PG-01: Stored result is retrievable."""
        result = _make_result()
        _cache.put("VENDOR", result, vendor_code="V001")
        cached = _cache.get("VENDOR", vendor_code="V001")
        assert cached is not None
        assert cached.resolved is True
        assert cached.source_type == ERPSourceType.CACHE
        assert cached.value["vendor_code"] == "V001"

    @pytest.mark.django_db
    def test_miss_returns_none(self, _cache):
        """PG-02: Cache miss returns None."""
        cached = _cache.get("VENDOR", vendor_code="NONEXISTENT")
        assert cached is None

    @pytest.mark.django_db
    def test_expired_returns_none(self, _cache, _make_result):
        """PG-03: Expired entry is treated as a miss."""
        from apps.erp_integration.models import ERPReferenceCacheRecord
        result = _make_result()
        _cache.put("VENDOR", result, ttl_seconds=1, vendor_code="V-EXPIRE")
        # Force expiry
        key = _cache.build_cache_key("VENDOR", vendor_code="V-EXPIRE")
        ERPReferenceCacheRecord.objects.filter(cache_key=key).update(
            expires_at=timezone.now() - timedelta(seconds=10)
        )
        cached = _cache.get("VENDOR", vendor_code="V-EXPIRE")
        assert cached is None

    @pytest.mark.django_db
    def test_unresolved_not_stored(self, _cache, _make_result):
        """PG-04: Unresolved result is not cached."""
        result = _make_result(resolved=False, value=None)
        _cache.put("VENDOR", result, vendor_code="V-FAIL")
        cached = _cache.get("VENDOR", vendor_code="V-FAIL")
        assert cached is None

    @pytest.mark.django_db
    def test_custom_ttl(self, _cache, _make_result):
        """PG-05: Custom TTL is honoured."""
        from apps.erp_integration.models import ERPReferenceCacheRecord
        result = _make_result()
        _cache.put("VENDOR", result, ttl_seconds=7200, vendor_code="V-TTL")
        key = _cache.build_cache_key("VENDOR", vendor_code="V-TTL")
        record = ERPReferenceCacheRecord.objects.get(cache_key=key)
        delta = record.expires_at - timezone.now()
        assert delta.total_seconds() > 7100  # ~2 hours


class TestInvalidation:
    """INV-01 to INV-03: Cache invalidation."""

    @pytest.mark.django_db
    def test_invalidate_by_type(self, _cache, _make_result):
        """INV-01: invalidate_by_type deletes matching entries only."""
        _cache.put("VENDOR", _make_result(), vendor_code="V1")
        _cache.put("ITEM", _make_result(), item_code="I1")
        deleted = _cache.invalidate_by_type("VENDOR")
        assert deleted >= 1
        assert _cache.get("VENDOR", vendor_code="V1") is None
        # ITEM entry should survive
        assert _cache.get("ITEM", item_code="I1") is not None

    @pytest.mark.django_db
    def test_invalidate_all(self, _cache, _make_result):
        """INV-02: invalidate_all deletes everything."""
        _cache.put("VENDOR", _make_result(), vendor_code="V1")
        _cache.put("ITEM", _make_result(), item_code="I1")
        deleted = _cache.invalidate_all()
        assert deleted >= 2
        assert _cache.get("VENDOR", vendor_code="V1") is None
        assert _cache.get("ITEM", item_code="I1") is None

    @pytest.mark.django_db
    def test_invalidate_empty_returns_zero(self, _cache):
        """INV-03: Invalidation on empty table returns 0."""
        deleted = _cache.invalidate_by_type("VENDOR")
        assert deleted == 0
