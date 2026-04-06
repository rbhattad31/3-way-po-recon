"""Tests for BaseResolver -- cache -> API -> DB fallback resolution chain."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from apps.erp_integration.enums import ERPResolutionType, ERPSourceType
from apps.erp_integration.services.connectors.base import ERPResolutionResult
from apps.erp_integration.services.resolution.base import BaseResolver


# ---------------------------------------------------------------------------
# Concrete test-only resolver subclass
# ---------------------------------------------------------------------------
class _TestResolver(BaseResolver):
    resolution_type = ERPResolutionType.VENDOR

    def __init__(self, *, capable=True, api_result=None, db_result=None):
        self._capable = capable
        self._api_result = api_result
        self._db_result = db_result

    def _check_capability(self, connector):
        return self._capable

    def _api_lookup(self, connector, **params):
        return self._api_result

    def _db_fallback(self, **params):
        return self._db_result


def _resolved(source=ERPSourceType.API, **kwargs):
    return ERPResolutionResult(
        resolved=True,
        value={"code": "V001"},
        source_type=source,
        confidence=0.95,
        **kwargs,
    )


def _unresolved():
    return ERPResolutionResult(resolved=False, source_type=ERPSourceType.NONE)


# =========================================================================
# Resolution chain
# =========================================================================
@pytest.mark.django_db
class TestResolutionChain:
    """BR-01 to BR-08: verify cache -> API -> DB fallback order."""

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get")
    def test_cache_hit_returns_immediately(self, mock_get, mock_log):
        """BR-01: Cached result short-circuits API and DB fallback."""
        cached = _resolved(source=ERPSourceType.CACHE)
        mock_get.return_value = cached

        resolver = _TestResolver(api_result=_resolved(), db_result=_resolved(source=ERPSourceType.DB_FALLBACK))
        connector = MagicMock()

        result = resolver.resolve(connector, vendor_code="V001")
        assert result.resolved is True
        assert result.source_type == ERPSourceType.CACHE

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.put")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get", return_value=None)
    def test_cache_miss_falls_to_api(self, mock_get, mock_put, mock_log):
        """BR-02: Cache miss calls API; result is cached."""
        api = _resolved(source=ERPSourceType.API)
        resolver = _TestResolver(api_result=api)
        connector = MagicMock()

        result = resolver.resolve(connector, vendor_code="V001")
        assert result.resolved is True
        assert result.source_type == ERPSourceType.API
        mock_put.assert_called_once()

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get", return_value=None)
    def test_api_failure_falls_to_db(self, mock_get, mock_log):
        """BR-03: API returns unresolved; DB fallback is attempted."""
        db = _resolved(source=ERPSourceType.DB_FALLBACK)
        resolver = _TestResolver(api_result=_unresolved(), db_result=db)
        connector = MagicMock()

        result = resolver.resolve(connector, vendor_code="V001")
        assert result.resolved is True
        assert result.source_type == ERPSourceType.DB_FALLBACK
        assert result.fallback_used is True

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get", return_value=None)
    def test_both_fail_returns_unresolved(self, mock_get, mock_log):
        """BR-04: Both API and DB fail -> unresolved."""
        resolver = _TestResolver(api_result=_unresolved(), db_result=None)
        connector = MagicMock()

        result = resolver.resolve(connector, vendor_code="V001")
        assert result.resolved is False
        assert result.source_type == ERPSourceType.NONE

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get", return_value=None)
    def test_no_connector_skips_api(self, mock_get, mock_log):
        """BR-05: connector=None skips API, goes straight to DB fallback."""
        db = _resolved(source=ERPSourceType.DB_FALLBACK)
        resolver = _TestResolver(db_result=db)

        result = resolver.resolve(None, vendor_code="V001")
        assert result.resolved is True
        assert result.fallback_used is True

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get", return_value=None)
    def test_incapable_connector_skips_api(self, mock_get, mock_log):
        """BR-06: Incapable connector skips API, goes straight to DB fallback."""
        db = _resolved(source=ERPSourceType.DB_FALLBACK)
        resolver = _TestResolver(capable=False, api_result=_resolved(), db_result=db)
        connector = MagicMock()

        result = resolver.resolve(connector, vendor_code="V001")
        assert result.resolved is True
        assert result.fallback_used is True

    @patch.object(BaseResolver, "_log_resolution")
    @patch("apps.erp_integration.services.cache_service.ERPCacheService.get", return_value=None)
    def test_cache_disabled(self, mock_get, mock_log):
        """BR-07: use_cache=False skips cache check entirely."""
        resolver = _TestResolver(api_result=_resolved())
        resolver.use_cache = False
        connector = MagicMock()

        result = resolver.resolve(connector, vendor_code="V001")
        assert result.resolved is True
        mock_get.assert_not_called()


class TestBuildLookupKey:
    """BLK-01 to BLK-03."""

    def test_key_with_params(self):
        """BLK-01: Key includes sorted params."""
        resolver = _TestResolver()
        key = resolver._build_lookup_key(vendor_code="V001", company="ACME")
        assert "company=ACME" in key
        assert "vendor_code=V001" in key

    def test_key_without_params(self):
        """BLK-02: Key is just resolution_type when no params."""
        resolver = _TestResolver()
        key = resolver._build_lookup_key()
        assert key == ERPResolutionType.VENDOR

    def test_empty_values_excluded(self):
        """BLK-03: None/empty values are excluded from key."""
        resolver = _TestResolver()
        key = resolver._build_lookup_key(vendor_code="V001", company=None, name="")
        assert "company" not in key
        assert "name" not in key
        assert "vendor_code=V001" in key
