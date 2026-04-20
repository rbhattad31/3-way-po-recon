"""
TEST 02 -- Dashboard UI
=======================
Checks every major dashboard link, KPI panel, and analytics endpoint.
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestDashboardPages:
    """Main dashboard pages and sub-views."""

    def test_dashboard_home(self, admin_client):
        r = admin_client.get("/dashboard/")
        assert r.status_code == 200
        content = r.content.decode()
        assert "dashboard" in content.lower() or "reconcil" in content.lower(), \
            "Dashboard should contain dashboard or reconciliation text"

    def test_dashboard_no_500(self, admin_client):
        r = admin_client.get("/dashboard/")
        assert r.status_code != 500, "Dashboard must not crash"


class TestDashboardAnalyticsAPI:
    """Dashboard analytics REST endpoints."""

    ANALYTICS_URLS = [
        "/api/v1/governance/permission-denials/",
        "/api/v1/governance/rbac-activity/",
        "/api/v1/governance/agent-performance/",
        "/api/v1/governance/agent-performance/",
    ]

    def test_analytics_endpoints_return_data(self, admin_client):
        for url in self.ANALYTICS_URLS:
            r = admin_client.get(url)
            assert r.status_code in (200, 204), \
                f"Analytics API {url} returned {r.status_code}"

    def test_analytics_api_returns_json(self, admin_client):
        r = admin_client.get("/api/v1/governance/agent-performance/")
        assert r.status_code in (200, 204)
        if r.status_code == 200:
            ct = r.get("Content-Type", "")
            assert "json" in ct, f"Expected JSON, got Content-Type: {ct}"


class TestNavigationLinks:
    """All top-level navigation links must respond without 500."""

    NAV_URLS = [
        "/invoices/",
        "/purchase-orders/",
        "/grns/",
        "/vendors/",
        "/reconciliation/",
        "/cases/",
        "/extraction/",
        "/reports/",
        "/posting/",
        "/erp-connections/",
        "/governance/",
        "/eval/",
        "/procurement/",
        "/agents/",
        "/benchmarking/",
        "/email/",
    ]

    def test_nav_links_no_500(self, admin_client):
        failures = []
        for url in self.NAV_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These pages returned 500: {failures}"

    def test_nav_links_accessible(self, admin_client):
        failures = []
        for url in self.NAV_URLS:
            r = admin_client.get(url)
            if r.status_code not in (200, 302, 301, 404):
                failures.append(f"{url} -> {r.status_code}")
        assert not failures, f"Unexpected status codes: {failures}"
