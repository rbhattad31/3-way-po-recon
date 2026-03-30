"""Tests for credit management views — list, detail, adjust."""
import pytest
from django.test import Client
from django.urls import reverse

from apps.extraction.credit_models import CreditTransaction, UserCreditAccount
from apps.extraction.services.credit_service import CreditService


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_client(admin_user):
    c = Client()
    c.force_login(admin_user)
    return c


@pytest.fixture
def user_client(user):
    c = Client()
    c.force_login(user)
    return c


# ────────────────────────────────────────────────────────────────
# Credit list view
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCreditAccountListView:
    def test_anonymous_redirect(self, client):
        url = reverse("extraction:credit_account_list")
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_admin_can_access(self, admin_client, credit_account):
        url = reverse("extraction:credit_account_list")
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert b"Credit" in resp.content

    def test_search_filters(self, admin_client, credit_account):
        url = reverse("extraction:credit_account_list")
        resp = admin_client.get(url, {"q": "testuser"})
        assert resp.status_code == 200


# ────────────────────────────────────────────────────────────────
# Credit detail view
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCreditAccountDetailView:
    def test_detail_renders(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_account_detail", kwargs={"user_id": user.pk})
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_detail_shows_balance(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_account_detail", kwargs={"user_id": user.pk})
        resp = admin_client.get(url)
        assert b"10" in resp.content  # balance_credits = 10


# ────────────────────────────────────────────────────────────────
# Credit adjust view — POST actions
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCreditAccountAdjustView:
    def test_add_credits(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.post(url, {
            "action_type": "add",
            "credits": 25,
            "remarks": "Test allocation",
        })
        assert resp.status_code == 302
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 35

    def test_subtract_credits(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.post(url, {
            "action_type": "subtract",
            "credits": 3,
            "remarks": "Correction",
        })
        assert resp.status_code == 302
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 7

    def test_subtract_blocked_negative(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.post(url, {
            "action_type": "subtract",
            "credits": 999,
            "remarks": "Too much",
        })
        assert resp.status_code == 302  # redirects with error message
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 10  # unchanged

    def test_set_monthly_limit(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.post(url, {
            "action_type": "set_limit",
            "monthly_limit": 50,
            "remarks": "Set monthly cap",
        })
        assert resp.status_code == 302
        credit_account.refresh_from_db()
        assert credit_account.monthly_limit == 50

    def test_toggle_active(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.post(url, {
            "action_type": "toggle_active",
            "is_active": False,
            "remarks": "Deactivating",
        })
        assert resp.status_code == 302
        credit_account.refresh_from_db()
        assert credit_account.is_active is False

    def test_get_redirects(self, admin_client, user, credit_account):
        """GET on adjust URL redirects to detail."""
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.get(url)
        assert resp.status_code == 302

    def test_invalid_form_shows_error(self, admin_client, user, credit_account):
        url = reverse("extraction:credit_adjust", kwargs={"user_id": user.pk})
        resp = admin_client.post(url, {
            "action_type": "add",
            "credits": 5,
            # missing remarks
        })
        assert resp.status_code == 302  # redirects with error


# ────────────────────────────────────────────────────────────────
# Workbench credit context
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestWorkbenchCreditContext:
    def test_workbench_includes_credit_summary(self, admin_client, admin_user):
        """Workbench view includes credit_summary in context."""
        CreditService.get_or_create_account(admin_user)
        url = reverse("extraction:workbench")
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert "credit_summary" in resp.context
        summary = resp.context["credit_summary"]
        assert "balance_credits" in summary
        assert "available_credits" in summary
