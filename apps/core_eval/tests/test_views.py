"""Tests for core_eval template views -- RBAC scenarios."""
import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.tests.factories import (
    PermissionFactory,
    RoleFactory,
    RolePermissionFactory,
    UserFactory,
    UserRoleFactory,
)
from apps.accounts.models import CompanyProfile
from apps.core_eval.models import EvalRun, LearningAction, LearningSignal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tenant(db):
    return CompanyProfile.objects.create(
        name="Test Tenant", slug="test-eval-tenant", is_active=True,
    )


@pytest.fixture()
def client():
    return Client()


@pytest.fixture()
def user(db, tenant):
    """Regular user with no eval permissions."""
    return UserFactory(role="AP_PROCESSOR", company=tenant)


@pytest.fixture()
def admin_user(db):
    """Superuser -- bypasses all permission checks."""
    return UserFactory(is_staff=True, is_superuser=True, role="ADMIN")


@pytest.fixture()
def eval_viewer(db, tenant):
    """User with eval.view permission via RBAC."""
    u = UserFactory(role="REVIEWER", company=tenant)
    role = RoleFactory(code="EVAL_VIEWER")
    perm = PermissionFactory(code="eval.view", module="eval", action="view")
    RolePermissionFactory(role=role, permission=perm)
    UserRoleFactory(user=u, role=role, is_primary=True)
    return u


@pytest.fixture()
def eval_run(db):
    """A single EvalRun record."""
    return EvalRun.objects.create(
        app_module="extraction",
        entity_type="invoice",
        entity_id=1,
        status=EvalRun.Status.COMPLETED,
    )


@pytest.fixture()
def learning_signal(db, eval_run):
    """A single LearningSignal record."""
    return LearningSignal.objects.create(
        eval_run=eval_run,
        app_module="extraction",
        signal_type="field_correction",
        field_name="invoice_number",
        old_value="INV-001",
        new_value="INV-0001",
    )


@pytest.fixture()
def learning_action(db):
    """A single LearningAction record."""
    return LearningAction.objects.create(
        app_module="extraction",
        action_type="field_normalization_candidate",
        target_description="invoice_number normalization",
        rationale="High correction rate on invoice_number field.",
        status=LearningAction.Status.PROPOSED,
    )


# ===================================================================
# Eval Run List
# ===================================================================

@pytest.mark.django_db
class TestEvalRunListView:
    url = reverse("core_eval:eval_run_list")

    def test_anonymous_redirects_to_login(self, client):
        resp = client.get(self.url)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_no_permission_returns_403(self, client, user):
        client.force_login(user)
        resp = client.get(self.url)
        assert resp.status_code == 403

    def test_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_eval_viewer_can_access(self, client, eval_viewer):
        client.force_login(eval_viewer)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_list_shows_records(self, client, admin_user, eval_run):
        client.force_login(admin_user)
        resp = client.get(self.url)
        assert resp.status_code == 200
        assert b"extraction" in resp.content

    def test_filter_by_status(self, client, admin_user, eval_run):
        client.force_login(admin_user)
        resp = client.get(self.url, {"status": "COMPLETED"})
        assert resp.status_code == 200

    def test_filter_by_app_module(self, client, admin_user, eval_run):
        client.force_login(admin_user)
        resp = client.get(self.url, {"app_module": "extraction"})
        assert resp.status_code == 200


# ===================================================================
# Eval Run Detail
# ===================================================================

@pytest.mark.django_db
class TestEvalRunDetailView:
    def _url(self, pk):
        return reverse("core_eval:eval_run_detail", args=[pk])

    def test_anonymous_redirects_to_login(self, client, eval_run):
        resp = client.get(self._url(eval_run.pk))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_no_permission_returns_403(self, client, user, eval_run):
        client.force_login(user)
        resp = client.get(self._url(eval_run.pk))
        assert resp.status_code == 403

    def test_admin_can_access(self, client, admin_user, eval_run):
        client.force_login(admin_user)
        resp = client.get(self._url(eval_run.pk))
        assert resp.status_code == 200

    def test_eval_viewer_can_access(self, client, eval_viewer, eval_run):
        client.force_login(eval_viewer)
        resp = client.get(self._url(eval_run.pk))
        assert resp.status_code == 200

    def test_404_for_missing_run(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(self._url(99999))
        assert resp.status_code == 404


# ===================================================================
# Learning Signal List
# ===================================================================

@pytest.mark.django_db
class TestLearningSignalListView:
    url = reverse("core_eval:learning_signal_list")

    def test_anonymous_redirects_to_login(self, client):
        resp = client.get(self.url)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_no_permission_returns_403(self, client, user):
        client.force_login(user)
        resp = client.get(self.url)
        assert resp.status_code == 403

    def test_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_eval_viewer_can_access(self, client, eval_viewer):
        client.force_login(eval_viewer)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_filter_by_signal_type(self, client, admin_user, learning_signal):
        client.force_login(admin_user)
        resp = client.get(self.url, {"signal_type": "field_correction"})
        assert resp.status_code == 200

    def test_filter_by_field_name(self, client, admin_user, learning_signal):
        client.force_login(admin_user)
        resp = client.get(self.url, {"field_name": "invoice_number"})
        assert resp.status_code == 200


# ===================================================================
# Learning Action List
# ===================================================================

@pytest.mark.django_db
class TestLearningActionListView:
    url = reverse("core_eval:learning_action_list")

    def test_anonymous_redirects_to_login(self, client):
        resp = client.get(self.url)
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_no_permission_returns_403(self, client, user):
        client.force_login(user)
        resp = client.get(self.url)
        assert resp.status_code == 403

    def test_admin_can_access(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_eval_viewer_can_access(self, client, eval_viewer):
        client.force_login(eval_viewer)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_filter_by_status(self, client, admin_user, learning_action):
        client.force_login(admin_user)
        resp = client.get(self.url, {"status": "PROPOSED"})
        assert resp.status_code == 200

    def test_filter_by_action_type(self, client, admin_user, learning_action):
        client.force_login(admin_user)
        resp = client.get(self.url, {"action_type": "field_normalization_candidate"})
        assert resp.status_code == 200


# ===================================================================
# Learning Action Detail
# ===================================================================

@pytest.mark.django_db
class TestLearningActionDetailView:
    def _url(self, pk):
        return reverse("core_eval:learning_action_detail", args=[pk])

    def test_anonymous_redirects_to_login(self, client, learning_action):
        resp = client.get(self._url(learning_action.pk))
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url

    def test_no_permission_returns_403(self, client, user, learning_action):
        client.force_login(user)
        resp = client.get(self._url(learning_action.pk))
        assert resp.status_code == 403

    def test_admin_can_access(self, client, admin_user, learning_action):
        client.force_login(admin_user)
        resp = client.get(self._url(learning_action.pk))
        assert resp.status_code == 200

    def test_eval_viewer_can_access(self, client, eval_viewer, learning_action):
        client.force_login(eval_viewer)
        resp = client.get(self._url(learning_action.pk))
        assert resp.status_code == 200

    def test_404_for_missing_action(self, client, admin_user):
        client.force_login(admin_user)
        resp = client.get(self._url(99999))
        assert resp.status_code == 404
