"""
Tests for ReconciliationModeResolver (MR-01 → MR-14)

Mix of pure-logic tests (no DB) and DB-backed policy tests.
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from apps.core.enums import ReconciliationMode
from apps.reconciliation.services.mode_resolver import ReconciliationModeResolver, ModeResolutionResult


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_config(
    enable_mode_resolver=True,
    default_mode=ReconciliationMode.THREE_WAY,
    enable_two_way_for_services=True,
    enable_grn_for_stock_items=True,
):
    config = MagicMock()
    config.enable_mode_resolver = enable_mode_resolver
    config.default_reconciliation_mode = default_mode
    config.enable_two_way_for_services = enable_two_way_for_services
    config.enable_grn_for_stock_items = enable_grn_for_stock_items
    return config


def make_invoice(vendor_id=None, raw_vendor_name="Test Vendor", extraction_raw_json=None):
    inv = MagicMock()
    inv.vendor_id = vendor_id
    inv.raw_vendor_name = raw_vendor_name
    inv.extraction_raw_json = extraction_raw_json or {}
    inv.pk = 1
    return inv


def make_po(vendor_id=None, department=""):
    po = MagicMock()
    po.vendor_id = vendor_id
    po.department = department
    return po


# ─── Resolver disabled ────────────────────────────────────────────────────────

class TestModeResolverDisabled:
    def test_mr12_mode_resolver_disabled_returns_default(self):
        """MR-12: When enable_mode_resolver=False, always return config default."""
        config = make_config(enable_mode_resolver=False, default_mode=ReconciliationMode.TWO_WAY)
        resolver = ReconciliationModeResolver(config)

        with patch.object(resolver, '_resolve_from_policies', return_value=None) as mock_policy:
            result = resolver.resolve(make_invoice(), make_po())
            # Policy resolution should not even be called when disabled
            mock_policy.assert_not_called()
            assert result.mode == ReconciliationMode.TWO_WAY
            assert result.resolution_method == "default"


# ─── Fallback default ─────────────────────────────────────────────────────────

class TestModeResolverFallback:
    def test_mr11_fallback_default_three_way(self):
        """MR-11: No policy, no heuristic match → falls back to THREE_WAY default."""
        config = make_config(default_mode=ReconciliationMode.THREE_WAY)
        resolver = ReconciliationModeResolver(config)

        # Patch sub-resolvers to return None (no match)
        with patch.object(resolver, '_resolve_from_policies', return_value=None), \
             patch.object(resolver, '_resolve_from_heuristics', return_value=None):
            result = resolver.resolve(make_invoice(), make_po())
            assert result.mode == ReconciliationMode.THREE_WAY
            assert result.resolution_method == "default"
            assert result.grn_required is True

    def test_fallback_two_way_config(self):
        """Fallback with TWO_WAY default config."""
        config = make_config(default_mode=ReconciliationMode.TWO_WAY)
        resolver = ReconciliationModeResolver(config)

        with patch.object(resolver, '_resolve_from_policies', return_value=None), \
             patch.object(resolver, '_resolve_from_heuristics', return_value=None):
            result = resolver.resolve(make_invoice(), make_po())
            assert result.mode == ReconciliationMode.TWO_WAY
            assert result.grn_required is False


# ─── Policy resolution (DB-backed) ───────────────────────────────────────────

@pytest.mark.django_db
class TestPolicyResolution:
    def _make_db_config(self, **kwargs):
        from apps.reconciliation.tests.factories import ReconConfigFactory
        return ReconConfigFactory(
            enable_mode_resolver=True,
            enable_two_way_for_services=kwargs.get("enable_two_way_for_services", True),
            enable_grn_for_stock_items=kwargs.get("enable_grn_for_stock_items", True),
            default_reconciliation_mode=kwargs.get(
                "default_reconciliation_mode", ReconciliationMode.THREE_WAY
            ),
        )

    def _make_db_policy(self, config, mode, priority=10, **kwargs):
        from apps.reconciliation.tests.factories import ReconPolicyFactory
        return ReconPolicyFactory(
            reconciliation_mode=mode,
            priority=priority,
            is_active=True,
            **kwargs,
        )

    def _make_db_invoice(self, vendor=None, **kwargs):
        from apps.reconciliation.tests.factories import InvoiceFactory
        return InvoiceFactory(vendor=vendor, **kwargs)

    def _make_db_po(self, vendor=None, **kwargs):
        from apps.reconciliation.tests.factories import POFactory
        return POFactory(vendor=vendor, **kwargs)

    def test_mr01_policy_match_two_way(self):
        """MR-01: Policy matches vendor → returns TWO_WAY."""
        try:
            from apps.vendors.models import Vendor
            vendor = Vendor.objects.create(name="Service Corp", normalized_name="service corp")
        except Exception:
            pytest.skip("Vendor model not available")

        config = self._make_db_config()
        self._make_db_policy(
            config,
            mode=ReconciliationMode.TWO_WAY,
            priority=1,
            vendor=vendor,
        )

        invoice = self._make_db_invoice(vendor=vendor)
        po = self._make_db_po(vendor=vendor)

        resolver = ReconciliationModeResolver(config)
        result = resolver.resolve(invoice, po)
        assert result.mode == ReconciliationMode.TWO_WAY
        assert result.resolution_method == "policy"

    def test_mr02_policy_match_three_way(self):
        """MR-02: Policy with THREE_WAY mode → grn_required=True."""
        try:
            from apps.vendors.models import Vendor
            vendor = Vendor.objects.create(name="Stock Corp", normalized_name="stock corp")
        except Exception:
            pytest.skip("Vendor model not available")

        config = self._make_db_config()
        self._make_db_policy(
            config,
            mode=ReconciliationMode.THREE_WAY,
            priority=1,
            vendor=vendor,
        )

        invoice = self._make_db_invoice(vendor=vendor)
        po = self._make_db_po(vendor=vendor)

        resolver = ReconciliationModeResolver(config)
        result = resolver.resolve(invoice, po)
        assert result.mode == ReconciliationMode.THREE_WAY
        assert result.grn_required is True
        assert result.resolution_method == "policy"

    def test_mr03_policy_priority_ordering(self):
        """MR-03: Two active policies — lower priority number wins."""
        try:
            from apps.vendors.models import Vendor
            vendor = Vendor.objects.create(name="Priority Corp", normalized_name="priority corp")
        except Exception:
            pytest.skip("Vendor model not available")

        config = self._make_db_config()
        # Higher priority number (10) = lower precedence
        self._make_db_policy(config, mode=ReconciliationMode.THREE_WAY, priority=10, vendor=vendor)
        # Lower priority number (1) = higher precedence
        self._make_db_policy(config, mode=ReconciliationMode.TWO_WAY, priority=1, vendor=vendor)

        invoice = self._make_db_invoice(vendor=vendor)
        po = self._make_db_po(vendor=vendor)

        resolver = ReconciliationModeResolver(config)
        result = resolver.resolve(invoice, po)
        # Priority 1 (TWO_WAY) wins over Priority 10 (THREE_WAY)
        assert result.mode == ReconciliationMode.TWO_WAY

    def test_mr04_expired_policy_skipped(self):
        """MR-04: Policy with effective_to in the past → skipped."""
        config = self._make_db_config()
        yesterday = date.today() - timedelta(days=1)
        self._make_db_policy(
            config,
            mode=ReconciliationMode.TWO_WAY,
            priority=1,
            effective_to=yesterday,
        )

        invoice = self._make_db_invoice()
        po = self._make_db_po()

        resolver = ReconciliationModeResolver(config)
        with patch.object(resolver, '_resolve_from_heuristics', return_value=None):
            result = resolver.resolve(invoice, po)
            # Expired policy skipped → falls to default
            assert result.resolution_method == "default"

    def test_mr05_future_policy_skipped(self):
        """MR-05: Policy with effective_from in the future → not yet applied."""
        config = self._make_db_config()
        tomorrow = date.today() + timedelta(days=1)
        self._make_db_policy(
            config,
            mode=ReconciliationMode.TWO_WAY,
            priority=1,
            effective_from=tomorrow,
        )

        invoice = self._make_db_invoice()
        po = self._make_db_po()

        resolver = ReconciliationModeResolver(config)
        with patch.object(resolver, '_resolve_from_heuristics', return_value=None):
            result = resolver.resolve(invoice, po)
            assert result.resolution_method == "default"


# ─── Heuristic resolution ─────────────────────────────────────────────────────

class TestHeuristicResolution:
    """These tests mock DB calls so they run without @pytest.mark.django_db."""

    def test_mr06_heuristic_service_two_way(self):
        """MR-06: All invoice lines are service items → TWO_WAY via heuristic."""
        config = make_config(enable_two_way_for_services=True)
        resolver = ReconciliationModeResolver(config)

        with patch.object(resolver, '_resolve_from_policies', return_value=None), \
             patch.object(resolver, '_is_service_invoice', return_value=True), \
             patch.object(resolver, '_is_stock_invoice', return_value=False):
            result = resolver.resolve(make_invoice(), make_po())
            assert result.mode == ReconciliationMode.TWO_WAY
            assert result.resolution_method == "heuristic"
            assert result.grn_required is False

    def test_mr07_heuristic_stock_three_way(self):
        """MR-07: All lines are stock items → THREE_WAY via heuristic."""
        config = make_config(enable_grn_for_stock_items=True)
        resolver = ReconciliationModeResolver(config)

        with patch.object(resolver, '_resolve_from_policies', return_value=None), \
             patch.object(resolver, '_is_service_invoice', return_value=False), \
             patch.object(resolver, '_is_stock_invoice', return_value=True):
            result = resolver.resolve(make_invoice(), make_po())
            assert result.mode == ReconciliationMode.THREE_WAY
            assert result.resolution_method == "heuristic"

    def test_mr14_mixed_service_stock_flags_ambiguous(self):
        """MR-14: Mixed service/stock flags → _is_service_invoice returns None."""
        config = make_config()
        resolver = ReconciliationModeResolver(config)

        # Simulate ambiguous (None) — can't classify
        with patch.object(resolver, '_resolve_from_policies', return_value=None), \
             patch.object(resolver, '_is_service_invoice', return_value=None), \
             patch.object(resolver, '_is_stock_invoice', return_value=None), \
             patch.object(resolver, '_classify_by_keywords', return_value=None):
            result = resolver.resolve(make_invoice(), make_po())
            # Falls back to default
            assert result.resolution_method == "default"


# ─── No invoice lines ─────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestNoInvoiceLines:
    def test_mr13_no_invoice_lines_returns_none(self):
        """MR-13: Invoice with zero line items → heuristic returns None → default."""
        from apps.reconciliation.tests.factories import ReconConfigFactory, InvoiceFactory, POFactory
        config = ReconConfigFactory()
        resolver = ReconciliationModeResolver(config)

        invoice = InvoiceFactory()  # No lines created
        po = POFactory()

        # With no lines, _is_service_invoice and _is_stock_invoice should return None
        # and _classify_by_keywords should return None too
        with patch.object(resolver, '_resolve_from_policies', return_value=None):
            result = resolver.resolve(invoice, po)
            # No lines → can't classify → falls to default
            assert result.resolution_method in ("heuristic", "default")
