"""Tests for CreditService — reserve/consume/refund ledger, monthly limits, edge cases."""
import pytest
from datetime import datetime
from dateutil.relativedelta import relativedelta
from unittest.mock import patch

from django.utils import timezone

from apps.core.enums import CreditTransactionType
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount
from apps.extraction.services.credit_service import CreditService


# ────────────────────────────────────────────────────────────────
# Account auto-creation
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetOrCreateAccount:
    def test_creates_account_for_new_user(self, user):
        assert not UserCreditAccount.objects.filter(user=user).exists()
        account = CreditService.get_or_create_account(user)
        assert account.pk is not None
        assert account.balance_credits == 0
        assert account.reserved_credits == 0
        assert account.monthly_limit == 0

    def test_returns_existing_account(self, user, credit_account):
        account = CreditService.get_or_create_account(user)
        assert account.pk == credit_account.pk
        assert account.balance_credits == 10


# ────────────────────────────────────────────────────────────────
# Check can reserve
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestCheckCanReserve:
    def test_ok_with_sufficient_balance(self, user, credit_account):
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is True
        assert result.reason_code == "OK"

    def test_blocked_insufficient_balance(self, user, credit_account):
        credit_account.balance_credits = 0
        credit_account.save()
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is False
        assert result.reason_code == "INSUFFICIENT_BALANCE"

    def test_blocked_inactive_account(self, user, credit_account):
        credit_account.is_active = False
        credit_account.save()
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is False
        assert result.reason_code == "INACTIVE"

    def test_blocked_monthly_limit(self, user, limited_account):
        limited_account.monthly_used = 5  # at the limit
        limited_account.save()
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is False
        assert result.reason_code == "MONTHLY_LIMIT_EXCEEDED"

    def test_ok_within_monthly_limit(self, user, limited_account):
        limited_account.monthly_used = 3
        limited_account.save()
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is True

    def test_monthly_limit_considers_reserved(self, user, limited_account):
        """reserved_credits count towards effective usage for monthly limit check."""
        limited_account.monthly_used = 3
        limited_account.reserved_credits = 2  # effective = 3 + 2 = 5, at limit
        limited_account.save()
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is False
        assert result.reason_code == "MONTHLY_LIMIT_EXCEEDED"

    def test_unlimited_monthly(self, user, credit_account):
        """monthly_limit=0 means unlimited."""
        credit_account.monthly_used = 999
        credit_account.save()
        result = CreditService.check_can_reserve(user, credits=1)
        assert result.allowed is True


# ────────────────────────────────────────────────────────────────
# Reserve
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestReserve:
    def test_reserve_success(self, user, credit_account):
        result = CreditService.reserve(user, credits=1, reference_id="DOC-1")
        assert result.allowed is True
        credit_account.refresh_from_db()
        assert credit_account.reserved_credits == 1
        assert credit_account.balance_credits == 10  # balance unchanged on reserve

    def test_reserve_creates_transaction(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        txn = CreditTransaction.objects.filter(
            account=credit_account,
            transaction_type=CreditTransactionType.RESERVE,
        ).first()
        assert txn is not None
        assert txn.credits == 1
        assert txn.balance_after == 10
        assert txn.reserved_after == 1

    def test_reserve_blocked_insufficient(self, user, credit_account):
        credit_account.balance_credits = 0
        credit_account.save()
        result = CreditService.reserve(user, credits=1)
        assert result.allowed is False
        assert result.reason_code == "INSUFFICIENT_BALANCE"

    def test_reserve_blocked_inactive(self, user, credit_account):
        credit_account.is_active = False
        credit_account.save()
        result = CreditService.reserve(user, credits=1)
        assert result.allowed is False

    def test_multiple_reserves(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        CreditService.reserve(user, credits=1)
        credit_account.refresh_from_db()
        assert credit_account.reserved_credits == 2
        assert credit_account.available_credits == 8


# ────────────────────────────────────────────────────────────────
# Consume
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestConsume:
    def test_consume_success(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        CreditService.consume(user, credits=1, reference_id="DOC-1")
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 9
        assert credit_account.reserved_credits == 0
        assert credit_account.monthly_used == 1

    def test_consume_creates_negative_transaction(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        CreditService.consume(user, credits=1)
        txn = CreditTransaction.objects.filter(
            account=credit_account,
            transaction_type=CreditTransactionType.CONSUME,
        ).first()
        assert txn is not None
        assert txn.credits == -1
        assert txn.balance_after == 9

    def test_consume_fails_without_reservation(self, user, credit_account):
        with pytest.raises(ValueError, match="only 0 reserved"):
            CreditService.consume(user, credits=1)

    def test_consume_fails_over_reserved(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        with pytest.raises(ValueError, match="only 1 reserved"):
            CreditService.consume(user, credits=2)

    def test_consume_accepts_account_instance(self, user, credit_account):
        """consume() resolves account_or_user correctly."""
        CreditService.reserve(user, credits=1)
        CreditService.consume(credit_account, credits=1)
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 9


# ────────────────────────────────────────────────────────────────
# Refund
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestRefund:
    def test_refund_success(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        CreditService.refund(user, credits=1, reference_id="DOC-1")
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 10  # unchanged
        assert credit_account.reserved_credits == 0

    def test_refund_creates_transaction(self, user, credit_account):
        CreditService.reserve(user, credits=1)
        CreditService.refund(user, credits=1)
        txn = CreditTransaction.objects.filter(
            account=credit_account,
            transaction_type=CreditTransactionType.REFUND,
        ).first()
        assert txn is not None
        assert txn.credits == 1  # positive (credits returned)

    def test_refund_fails_without_reservation(self, user, credit_account):
        with pytest.raises(ValueError, match="only 0 reserved"):
            CreditService.refund(user, credits=1)


# ────────────────────────────────────────────────────────────────
# Allocate
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAllocate:
    def test_allocate_positive(self, user, credit_account):
        CreditService.allocate(user, credits=50, actor=user, remarks="Top-up")
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 60

    def test_allocate_rejects_zero(self, user, credit_account):
        with pytest.raises(ValueError, match="positive"):
            CreditService.allocate(user, credits=0)

    def test_allocate_rejects_negative(self, user, credit_account):
        with pytest.raises(ValueError, match="positive"):
            CreditService.allocate(user, credits=-5)

    def test_allocate_creates_transaction(self, user, credit_account):
        CreditService.allocate(user, credits=20, actor=user)
        txn = CreditTransaction.objects.filter(
            account=credit_account,
            transaction_type=CreditTransactionType.ALLOCATE,
        ).first()
        assert txn is not None
        assert txn.credits == 20
        assert txn.balance_after == 30


# ────────────────────────────────────────────────────────────────
# Adjust
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestAdjust:
    def test_adjust_positive(self, user, credit_account):
        CreditService.adjust(user, delta=5, actor=user)
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 15

    def test_adjust_negative(self, user, credit_account):
        CreditService.adjust(user, delta=-3, actor=user)
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 7

    def test_adjust_blocked_negative_balance(self, user, credit_account):
        with pytest.raises(ValueError, match="negative balance"):
            CreditService.adjust(user, delta=-100)

    def test_adjust_blocked_below_reserved(self, user, credit_account):
        CreditService.reserve(user, credits=5)
        with pytest.raises(ValueError, match="below reserved"):
            CreditService.adjust(user, delta=-8)  # would leave 2 < 5 reserved


# ────────────────────────────────────────────────────────────────
# Monthly reset
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestMonthlyReset:
    def test_reset_clears_monthly_used(self, user, credit_account):
        credit_account.monthly_used = 42
        credit_account.last_reset_at = timezone.now() - relativedelta(months=1)
        credit_account.save()

        was_reset = CreditService.reset_monthly_if_due(credit_account)
        assert was_reset is True
        credit_account.refresh_from_db()
        assert credit_account.monthly_used == 0

    def test_no_reset_within_same_month(self, user, credit_account):
        credit_account.monthly_used = 10
        credit_account.last_reset_at = timezone.now()
        credit_account.save()

        was_reset = CreditService.reset_monthly_if_due(credit_account)
        assert was_reset is False
        credit_account.refresh_from_db()
        assert credit_account.monthly_used == 10

    def test_reset_creates_transaction(self, user, credit_account):
        credit_account.monthly_used = 5
        credit_account.last_reset_at = timezone.now() - relativedelta(months=2)
        credit_account.save()

        CreditService.reset_monthly_if_due(credit_account)
        txn = CreditTransaction.objects.filter(
            account=credit_account,
            transaction_type=CreditTransactionType.MONTHLY_RESET,
        ).first()
        assert txn is not None
        assert txn.credits == 0
        assert "Previous monthly_used=5" in txn.remarks


# ────────────────────────────────────────────────────────────────
# Usage summary
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestGetUsageSummary:
    def test_summary_keys(self, user, credit_account):
        summary = CreditService.get_usage_summary(user)
        required_keys = {
            "balance_credits", "reserved_credits", "available_credits",
            "monthly_limit", "monthly_used", "monthly_remaining",
            "usage_percent", "is_active",
        }
        assert required_keys.issubset(summary.keys())

    def test_summary_values(self, user, credit_account):
        summary = CreditService.get_usage_summary(user)
        assert summary["balance_credits"] == 10
        assert summary["available_credits"] == 10
        assert summary["is_active"] is True

    def test_unlimited_monthly_remaining(self, user, credit_account):
        """monthly_remaining is -1 when limit is 0 (unlimited)."""
        summary = CreditService.get_usage_summary(user)
        assert summary["monthly_remaining"] == -1

    def test_limited_monthly_remaining(self, user, limited_account):
        limited_account.monthly_used = 2
        limited_account.save()
        summary = CreditService.get_usage_summary(user)
        assert summary["monthly_remaining"] == 3
        assert summary["usage_percent"] == 40.0


# ────────────────────────────────────────────────────────────────
# Full lifecycle: reserve → consume
# ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
class TestFullLifecycle:
    def test_reserve_then_consume(self, user, credit_account):
        CreditService.reserve(user, credits=1, reference_id="DOC-1")
        CreditService.consume(user, credits=1, reference_id="DOC-1")
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 9
        assert credit_account.reserved_credits == 0
        assert credit_account.monthly_used == 1

    def test_reserve_then_refund(self, user, credit_account):
        CreditService.reserve(user, credits=1, reference_id="DOC-2")
        CreditService.refund(user, credits=1, reference_id="DOC-2")
        credit_account.refresh_from_db()
        assert credit_account.balance_credits == 10
        assert credit_account.reserved_credits == 0
        assert credit_account.monthly_used == 0

    def test_transaction_ledger_integrity(self, user, credit_account):
        """All operations create exactly the expected ledger entries."""
        CreditService.reserve(user, credits=1)
        CreditService.consume(user, credits=1)
        CreditService.reserve(user, credits=1)
        CreditService.refund(user, credits=1)

        txns = list(
            CreditTransaction.objects
            .filter(account=credit_account)
            .order_by("created_at")
            .values_list("transaction_type", flat=True)
        )
        assert txns == [
            CreditTransactionType.RESERVE,
            CreditTransactionType.CONSUME,
            CreditTransactionType.RESERVE,
            CreditTransactionType.REFUND,
        ]
