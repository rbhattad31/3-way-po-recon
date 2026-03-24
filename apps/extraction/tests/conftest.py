"""Shared fixtures for extraction credit tests."""
import pytest
from django.contrib.auth import get_user_model

from apps.extraction.credit_models import UserCreditAccount
from apps.extraction.services.credit_service import CreditService

User = get_user_model()


@pytest.fixture
def user(db):
    """A basic active user."""
    return User.objects.create_user(
        email="testuser@example.com",
        password="testpass123",
        first_name="Test",
        last_name="User",
    )


@pytest.fixture
def admin_user(db):
    """An admin / staff user for credit management views."""
    return User.objects.create_superuser(
        email="admin@example.com",
        password="adminpass123",
        first_name="Admin",
        last_name="User",
    )


@pytest.fixture
def credit_account(user):
    """A credit account with 10 credits, no monthly limit."""
    account = CreditService.get_or_create_account(user)
    account.balance_credits = 10
    account.monthly_limit = 0
    account.save()
    return account


@pytest.fixture
def limited_account(user):
    """A credit account with 100 credits and monthly limit of 5."""
    account = CreditService.get_or_create_account(user)
    account.balance_credits = 100
    account.monthly_limit = 5
    account.monthly_used = 0
    account.save()
    return account
