"""
TEST 01 -- Health endpoints, Authentication & Authorization
===========================================================
Checks:
  - /health/ endpoints respond 200
  - Login page renders
  - Authenticated user reaches dashboard
  - Unauthenticated user is redirected to login
  - Wrong credentials rejected
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestHealthEndpoints:
    """Platform health check endpoints."""

    def test_health_check_200(self, admin_client):
        r = admin_client.get("/health/")
        assert r.status_code == 200, f"/health/ returned {r.status_code}"

    def test_health_live_200(self, admin_client):
        r = admin_client.get("/health/live/")
        assert r.status_code == 200, f"/health/live/ returned {r.status_code}"

    def test_health_ready_200(self, admin_client):
        r = admin_client.get("/health/ready/")
        assert r.status_code in (200, 503), f"/health/ready/ returned {r.status_code}"

    def test_health_check_anonymous(self, anon_client):
        """Health check must NOT require login (for load-balancer probes)."""
        r = anon_client.get("/health/")
        assert r.status_code == 200, "Health check should be public"


class TestAuthentication:
    """Login / logout / redirect flows."""

    def test_login_page_renders(self, anon_client):
        r = anon_client.get("/accounts/login/")
        assert r.status_code == 200
        content = r.content.decode()
        assert "login" in content.lower() or "sign in" in content.lower(), \
            "Login page should contain login form"

    def test_unauthenticated_redirects_to_login(self, anon_client):
        r = anon_client.get("/dashboard/")
        # Must redirect (302) or return 200 for the login page itself
        assert r.status_code in (302, 301), \
            "Dashboard should redirect anonymous users to login"

    def test_authenticated_admin_reaches_dashboard(self, admin_client):
        r = admin_client.get("/dashboard/")
        assert r.status_code == 200, "Authenticated admin should load dashboard"

    def test_authenticated_ap_user_reaches_dashboard(self, ap_client):
        r = ap_client.get("/dashboard/")
        assert r.status_code in (200, 302), \
            "AP user should reach dashboard or be redirected within app"

    def test_wrong_credentials_rejected(self, anon_client):
        r = anon_client.post("/accounts/login/", {
            "username": "nobody@notexist.com",
            "password": "wrongpassword",
        })
        # Must NOT redirect to dashboard (stay on login or show error)
        assert r.status_code in (200, 400), \
            "Wrong credentials must not succeed"

    def test_logout_works(self, admin_client):
        r = admin_client.post("/accounts/logout/")
        assert r.status_code in (200, 302), "Logout should succeed"

    def test_admin_panel_accessible_for_superuser(self, admin_client):
        r = admin_client.get("/admin/")
        assert r.status_code == 200, "Admin panel should be accessible to superuser"

    def test_admin_panel_blocked_for_ap_user(self, ap_client):
        r = ap_client.get("/admin/")
        assert r.status_code in (302, 403), \
            "Admin panel should be blocked for non-staff users"


class TestAPIAuthentication:
    """API endpoints must reject unauthenticated requests."""

    PROTECTED_APIS = [
        "/api/v1/governance/invoices/1/audit-history/",
        "/api/v1/cases/",
    ]

    def test_api_rejects_anonymous(self, anon_client):
        for url in self.PROTECTED_APIS:
            r = anon_client.get(url)
            assert r.status_code in (401, 403, 302), \
                f"API {url} should reject anonymous -- got {r.status_code}"
