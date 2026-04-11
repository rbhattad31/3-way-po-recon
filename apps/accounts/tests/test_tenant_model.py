"""
Tests for CompanyProfile tenant fields and TenantInvitation model.

Covers:
- CompanyProfile slug uniqueness and required-for-new-records behaviour
- CompanyProfile plan_type choices
- CompanyProfile.get_default() correctness
- CompanyProfile.save() single-default invariant
- TenantInvitation.is_expired / is_usable property logic
- TenantInvitation unique_together (tenant, email) constraint
"""
from __future__ import annotations

import pytest
from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone

from apps.accounts.models import CompanyProfile, TenantInvitation
from apps.accounts.tests.factories import UserFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tenant(slug, name=None, is_default=False, plan_type="trial"):
    """Create and return a CompanyProfile."""
    return CompanyProfile.objects.create(
        name=name or slug,
        slug=slug,
        is_default=is_default,
        plan_type=plan_type,
        is_active=True,
    )


def make_invitation(tenant, email, hours_offset=72, accepted=False):
    """Create a TenantInvitation expiring *hours_offset* hours from now."""
    import secrets
    inv = TenantInvitation.objects.create(
        tenant=tenant,
        email=email,
        token=secrets.token_urlsafe(32),
        accepted=accepted,
        expires_at=timezone.now() + timedelta(hours=hours_offset),
    )
    return inv


# ============================================================================
# CompanyProfile — slug field
# ============================================================================

@pytest.mark.django_db
class TestCompanyProfileSlug:

    def test_slug_stored_correctly(self):
        """TM-01: slug field is saved verbatim."""
        tenant = make_tenant("acme-corp")
        assert tenant.slug == "acme-corp"

    def test_slug_unique_constraint_enforced(self):
        """TM-02: Creating a second CompanyProfile with the same slug raises IntegrityError."""
        make_tenant("duplicate-slug")
        with pytest.raises(IntegrityError):
            make_tenant("duplicate-slug")

    def test_blank_slug_allowed_by_field(self):
        """TM-03: slug=blank=True means an empty slug can be stored (no DB-level NOT NULL error).

        In practice the application layer (seed / forms) always supplies a slug,
        but the field definition allows blank so existing rows without slugs survive
        a migration.  Two blank rows are NOT allowed (unique constraint would fire).
        """
        t = CompanyProfile.objects.create(name="No Slug", slug="", is_active=True)
        assert t.slug == ""


# ============================================================================
# CompanyProfile — plan_type
# ============================================================================

@pytest.mark.django_db
class TestCompanyProfilePlanType:

    def test_valid_plan_types_are_accepted(self):
        """TM-04: Each defined plan_type choice is accepted by the model."""
        valid_plans = ["trial", "starter", "professional", "enterprise"]
        for i, plan in enumerate(valid_plans):
            t = CompanyProfile.objects.create(
                name=f"Tenant {plan}",
                slug=f"tenant-{plan}-{i}",
                plan_type=plan,
            )
            assert t.plan_type == plan

    def test_default_plan_type_is_trial(self):
        """TM-05: New CompanyProfile defaults to plan_type='trial'."""
        t = CompanyProfile.objects.create(name="Default Plan", slug="default-plan-slug")
        assert t.plan_type == "trial"


# ============================================================================
# CompanyProfile — get_default()
# ============================================================================

@pytest.mark.django_db
class TestCompanyProfileGetDefault:

    def test_get_default_returns_default_active_profile(self):
        """TM-06: get_default() returns the is_default=True, is_active=True profile."""
        tenant = make_tenant("default-tenant", is_default=True)
        result = CompanyProfile.get_default()
        assert result is not None
        assert result.pk == tenant.pk

    def test_get_default_returns_none_when_no_default(self):
        """TM-07: get_default() returns None when no default profile exists."""
        # Ensure no default profiles exist
        CompanyProfile.objects.filter(is_default=True).update(is_default=False)
        assert CompanyProfile.get_default() is None

    def test_get_default_ignores_inactive_default(self):
        """TM-08: get_default() skips profiles with is_active=False even if is_default=True."""
        # Use update() to bypass the save() guard that resets other is_default flags
        CompanyProfile.objects.filter(is_default=True).update(is_default=False)
        CompanyProfile.objects.create(
            name="Inactive Default",
            slug="inactive-default",
            is_default=True,
            is_active=False,
        )
        assert CompanyProfile.get_default() is None


# ============================================================================
# CompanyProfile — single-default invariant
# ============================================================================

@pytest.mark.django_db
class TestCompanyProfileSingleDefault:

    def test_save_clears_previous_default(self):
        """TM-09: Setting a new profile as default clears the previous default."""
        first = make_tenant("first-default", is_default=True)
        assert CompanyProfile.objects.filter(is_default=True).count() == 1

        second = make_tenant("second-default", is_default=True)

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.is_default is True
        assert first.is_default is False
        assert CompanyProfile.objects.filter(is_default=True).count() == 1

    def test_non_default_save_does_not_affect_other_defaults(self):
        """TM-10: Saving a non-default profile leaves existing defaults untouched."""
        default = make_tenant("still-default", is_default=True)
        make_tenant("non-default", is_default=False)

        default.refresh_from_db()
        assert default.is_default is True


# ============================================================================
# TenantInvitation — is_expired
# ============================================================================

@pytest.mark.django_db
class TestTenantInvitationIsExpired:

    def test_is_expired_true_when_past(self):
        """TM-11: is_expired returns True when expires_at is in the past."""
        import secrets
        tenant = make_tenant("exp-tenant-past")
        inv = TenantInvitation.objects.create(
            tenant=tenant,
            email="past@example.com",
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() - timedelta(hours=1),
        )
        assert inv.is_expired is True

    def test_is_expired_false_when_future(self):
        """TM-12: is_expired returns False when expires_at is in the future."""
        tenant = make_tenant("exp-tenant-future")
        inv = make_invitation(tenant, "future@example.com", hours_offset=72)
        assert inv.is_expired is False


# ============================================================================
# TenantInvitation — is_usable
# ============================================================================

@pytest.mark.django_db
class TestTenantInvitationIsUsable:

    def test_is_usable_false_when_accepted(self):
        """TM-13: is_usable returns False if accepted=True (even if not expired)."""
        tenant = make_tenant("usable-tenant-accepted")
        inv = make_invitation(tenant, "accepted@example.com", accepted=True)
        assert inv.is_usable is False

    def test_is_usable_false_when_expired(self):
        """TM-14: is_usable returns False if invitation has expired."""
        import secrets
        tenant = make_tenant("usable-tenant-expired")
        inv = TenantInvitation.objects.create(
            tenant=tenant,
            email="expired@example.com",
            token=secrets.token_urlsafe(32),
            accepted=False,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        assert inv.is_usable is False

    def test_is_usable_true_when_active_and_not_accepted(self):
        """TM-15: is_usable returns True when not accepted and not expired."""
        tenant = make_tenant("usable-tenant-ok")
        inv = make_invitation(tenant, "valid@example.com", accepted=False)
        assert inv.is_usable is True


# ============================================================================
# TenantInvitation — unique_together
# ============================================================================

@pytest.mark.django_db
class TestTenantInvitationUniqueTogether:

    def test_unique_together_tenant_email_enforced(self):
        """TM-16: Two invitations for the same (tenant, email) raises IntegrityError."""
        import secrets
        tenant = make_tenant("uq-together-tenant")
        TenantInvitation.objects.create(
            tenant=tenant,
            email="dup@example.com",
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=24),
        )
        with pytest.raises(IntegrityError):
            TenantInvitation.objects.create(
                tenant=tenant,
                email="dup@example.com",
                token=secrets.token_urlsafe(32),
                expires_at=timezone.now() + timedelta(hours=24),
            )

    def test_same_email_different_tenants_is_allowed(self):
        """TM-17: Same email can be invited by two different tenants."""
        import secrets
        tenant_a = make_tenant("unique-ta")
        tenant_b = make_tenant("unique-tb")
        TenantInvitation.objects.create(
            tenant=tenant_a,
            email="shared@example.com",
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=24),
        )
        # Should NOT raise
        inv_b = TenantInvitation.objects.create(
            tenant=tenant_b,
            email="shared@example.com",
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(hours=24),
        )
        assert inv_b.pk is not None
