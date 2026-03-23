"""Credit management service for invoice extraction usage control.

Implements a reserve → consume/refund ledger pattern with monthly limits.
All balance-mutating methods use select_for_update() under transaction.atomic()
to prevent race conditions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Union

from django.db import transaction
from django.utils import timezone

from apps.core.enums import AuditEventType, CreditTransactionType
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount

logger = logging.getLogger(__name__)

# ── Reason codes ────────────────────────────────────────────────
REASON_INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
REASON_INACTIVE_ACCOUNT = "INACTIVE_ACCOUNT"
REASON_MONTHLY_LIMIT_EXCEEDED = "MONTHLY_LIMIT_EXCEEDED"
REASON_OK = "OK"

REASON_MESSAGES = {
    REASON_INSUFFICIENT_BALANCE: "You do not have enough credits to process this invoice.",
    REASON_INACTIVE_ACCOUNT: "This account is inactive. Please contact your administrator.",
    REASON_MONTHLY_LIMIT_EXCEEDED: "Your monthly invoice processing limit has been reached.",
    REASON_OK: "",
}


class CreditAccountingError(Exception):
    """Raised when a credit operation would violate accounting invariants."""


@dataclass
class CreditCheckResult:
    allowed: bool
    reason_code: str  # OK | INACTIVE | INSUFFICIENT_BALANCE | MONTHLY_LIMIT_EXCEEDED
    message: str
    balance_credits: int = 0
    reserved_credits: int = 0
    available_credits: int = 0
    monthly_limit: int = 0
    monthly_used: int = 0
    monthly_remaining: int = 0


class CreditService:
    """Stateless service for credit account lifecycle management."""

    # ── Account management ──────────────────────────────────

    @staticmethod
    def get_or_create_account(user) -> UserCreditAccount:
        account, _ = UserCreditAccount.objects.get_or_create(
            user=user,
            defaults={"balance_credits": 0, "monthly_limit": 0},
        )
        return account

    # ── Monthly reset ───────────────────────────────────────

    @classmethod
    def reset_monthly_if_due(cls, account: UserCreditAccount) -> bool:
        """Reset monthly_used if the calendar month has changed. Idempotent."""
        now = timezone.now()
        if account.last_reset_at is None:
            account.last_reset_at = now
            account.save(update_fields=["last_reset_at", "updated_at"])
            return False

        if (account.last_reset_at.year, account.last_reset_at.month) == (now.year, now.month):
            return False

        old_used = account.monthly_used
        account.monthly_used = 0
        account.last_reset_at = now
        account.save(update_fields=["monthly_used", "last_reset_at", "updated_at"])

        CreditTransaction.objects.create(
            account=account,
            transaction_type=CreditTransactionType.MONTHLY_RESET,
            credits=0,
            balance_after=account.balance_credits,
            reserved_after=account.reserved_credits,
            monthly_used_after=0,
            reference_type="system",
            remarks=f"Monthly reset. Previous monthly_used={old_used}",
        )
        cls._audit(
            AuditEventType.CREDIT_MONTHLY_RESET,
            account,
            credits=0,
            reference_type="system",
            reason_code="MONTHLY_RESET",
            remarks=f"Reset monthly_used from {old_used} to 0",
        )
        return True

    # ── Credit check ────────────────────────────────────────

    @classmethod
    def check_can_reserve(cls, user, credits: int = 1) -> CreditCheckResult:
        account = cls.get_or_create_account(user)
        cls.reset_monthly_if_due(account)
        account.refresh_from_db()

        if not account.is_active:
            result = CreditCheckResult(
                allowed=False,
                reason_code=REASON_INACTIVE_ACCOUNT,
                message=REASON_MESSAGES[REASON_INACTIVE_ACCOUNT],
                balance_credits=account.balance_credits,
                reserved_credits=account.reserved_credits,
                available_credits=account.available_credits,
                monthly_limit=account.monthly_limit,
                monthly_used=account.monthly_used,
                monthly_remaining=cls._monthly_remaining(account),
            )
            cls._audit(
                AuditEventType.CREDIT_CHECKED, account,
                credits=credits, reason_code=REASON_INACTIVE_ACCOUNT,
            )
            return result

        if account.available_credits < credits:
            result = CreditCheckResult(
                allowed=False,
                reason_code=REASON_INSUFFICIENT_BALANCE,
                message=REASON_MESSAGES[REASON_INSUFFICIENT_BALANCE],
                balance_credits=account.balance_credits,
                reserved_credits=account.reserved_credits,
                available_credits=account.available_credits,
                monthly_limit=account.monthly_limit,
                monthly_used=account.monthly_used,
                monthly_remaining=cls._monthly_remaining(account),
            )
            cls._audit(
                AuditEventType.CREDIT_LIMIT_EXCEEDED, account,
                credits=credits, reason_code=REASON_INSUFFICIENT_BALANCE,
            )
            return result

        if account.monthly_limit > 0:
            effective_used = account.monthly_used + account.reserved_credits
            if effective_used + credits > account.monthly_limit:
                result = CreditCheckResult(
                    allowed=False,
                    reason_code=REASON_MONTHLY_LIMIT_EXCEEDED,
                    message=REASON_MESSAGES[REASON_MONTHLY_LIMIT_EXCEEDED],
                    balance_credits=account.balance_credits,
                    reserved_credits=account.reserved_credits,
                    available_credits=account.available_credits,
                    monthly_limit=account.monthly_limit,
                    monthly_used=account.monthly_used,
                    monthly_remaining=cls._monthly_remaining(account),
                )
                cls._audit(
                    AuditEventType.CREDIT_LIMIT_EXCEEDED, account,
                    credits=credits, reason_code=REASON_MONTHLY_LIMIT_EXCEEDED,
                )
                return result

        result = CreditCheckResult(
            allowed=True,
            reason_code=REASON_OK,
            message="",
            balance_credits=account.balance_credits,
            reserved_credits=account.reserved_credits,
            available_credits=account.available_credits,
            monthly_limit=account.monthly_limit,
            monthly_used=account.monthly_used,
            monthly_remaining=cls._monthly_remaining(account),
        )
        cls._audit(
            AuditEventType.CREDIT_CHECKED, account,
            credits=credits, reason_code=REASON_OK,
        )
        return result

    # ── Reserve ─────────────────────────────────────────────

    @classmethod
    def reserve(
        cls,
        user,
        credits: int = 1,
        reference_type: str = "document_upload",
        reference_id: str = "",
        remarks: str = "",
    ) -> CreditCheckResult:
        with transaction.atomic():
            account = UserCreditAccount.objects.select_for_update().get(user=user)
            cls.reset_monthly_if_due(account)
            account.refresh_from_db()

            # Idempotency: if a RESERVED transaction already exists for this
            # reference_type + reference_id, return it without creating a duplicate
            if reference_id:
                existing = CreditTransaction.objects.filter(
                    account=account,
                    transaction_type=CreditTransactionType.RESERVE,
                    reference_type=reference_type,
                    reference_id=str(reference_id),
                ).first()
                if existing:
                    return CreditCheckResult(
                        allowed=True, reason_code=REASON_OK, message="",
                        balance_credits=account.balance_credits,
                        reserved_credits=account.reserved_credits,
                        available_credits=account.available_credits,
                        monthly_limit=account.monthly_limit,
                        monthly_used=account.monthly_used,
                        monthly_remaining=cls._monthly_remaining(account),
                    )

            # Re-validate under lock
            if not account.is_active:
                return CreditCheckResult(
                    allowed=False, reason_code=REASON_INACTIVE_ACCOUNT,
                    message=REASON_MESSAGES[REASON_INACTIVE_ACCOUNT],
                    balance_credits=account.balance_credits,
                    reserved_credits=account.reserved_credits,
                    available_credits=account.available_credits,
                    monthly_limit=account.monthly_limit,
                    monthly_used=account.monthly_used,
                    monthly_remaining=cls._monthly_remaining(account),
                )

            if account.available_credits < credits:
                return CreditCheckResult(
                    allowed=False, reason_code=REASON_INSUFFICIENT_BALANCE,
                    message=REASON_MESSAGES[REASON_INSUFFICIENT_BALANCE],
                    balance_credits=account.balance_credits,
                    reserved_credits=account.reserved_credits,
                    available_credits=account.available_credits,
                    monthly_limit=account.monthly_limit,
                    monthly_used=account.monthly_used,
                    monthly_remaining=cls._monthly_remaining(account),
                )

            if account.monthly_limit > 0:
                effective_used = account.monthly_used + account.reserved_credits
                if effective_used + credits > account.monthly_limit:
                    return CreditCheckResult(
                        allowed=False, reason_code=REASON_MONTHLY_LIMIT_EXCEEDED,
                        message=REASON_MESSAGES[REASON_MONTHLY_LIMIT_EXCEEDED],
                        balance_credits=account.balance_credits,
                        reserved_credits=account.reserved_credits,
                        available_credits=account.available_credits,
                        monthly_limit=account.monthly_limit,
                        monthly_used=account.monthly_used,
                        monthly_remaining=cls._monthly_remaining(account),
                    )

            account.reserved_credits += credits
            # Enforce invariants
            if account.balance_credits < account.reserved_credits:
                raise CreditAccountingError(
                    f"Reserve would violate invariant: balance ({account.balance_credits}) "
                    f"< reserved ({account.reserved_credits})"
                )
            account.save(update_fields=["reserved_credits", "updated_at"])

            CreditTransaction.objects.create(
                account=account,
                transaction_type=CreditTransactionType.RESERVE,
                credits=credits,
                balance_after=account.balance_credits,
                reserved_after=account.reserved_credits,
                monthly_used_after=account.monthly_used,
                reference_type=reference_type,
                reference_id=str(reference_id),
                remarks=remarks,
                created_by=user,
            )

        cls._audit(
            AuditEventType.CREDIT_RESERVED, account,
            credits=credits, reference_type=reference_type,
            reference_id=str(reference_id), user=user,
        )
        return CreditCheckResult(
            allowed=True, reason_code=REASON_OK, message="",
            balance_credits=account.balance_credits,
            reserved_credits=account.reserved_credits,
            available_credits=account.available_credits,
            monthly_limit=account.monthly_limit,
            monthly_used=account.monthly_used,
            monthly_remaining=cls._monthly_remaining(account),
        )

    # ── Consume ─────────────────────────────────────────────

    @classmethod
    def consume(
        cls,
        account_or_user,
        credits: int = 1,
        reference_type: str = "document_upload",
        reference_id: str = "",
        remarks: str = "",
    ) -> None:
        user = cls._resolve_user(account_or_user)
        with transaction.atomic():
            account = UserCreditAccount.objects.select_for_update().get(user=user)

            # Idempotency: skip if already consumed for this reference
            if reference_id:
                already = CreditTransaction.objects.filter(
                    account=account,
                    transaction_type=CreditTransactionType.CONSUME,
                    reference_type=reference_type,
                    reference_id=str(reference_id),
                ).exists()
                if already:
                    return

            if account.reserved_credits < credits:
                raise CreditAccountingError(
                    f"Cannot consume {credits} credits: only {account.reserved_credits} reserved."
                )
            if account.balance_credits < credits:
                raise CreditAccountingError(
                    f"Cannot consume {credits} credits: balance is {account.balance_credits}."
                )

            account.reserved_credits -= credits
            account.balance_credits -= credits
            account.monthly_used += credits

            # Enforce invariants
            if account.balance_credits < 0 or account.reserved_credits < 0 or account.monthly_used < 0:
                raise CreditAccountingError(
                    f"Consume would violate invariant: balance={account.balance_credits}, "
                    f"reserved={account.reserved_credits}, monthly_used={account.monthly_used}"
                )

            account.save(update_fields=[
                "reserved_credits", "balance_credits", "monthly_used", "updated_at",
            ])

            CreditTransaction.objects.create(
                account=account,
                transaction_type=CreditTransactionType.CONSUME,
                credits=-credits,
                balance_after=account.balance_credits,
                reserved_after=account.reserved_credits,
                monthly_used_after=account.monthly_used,
                reference_type=reference_type,
                reference_id=str(reference_id),
                remarks=remarks,
                created_by=user,
            )

        cls._audit(
            AuditEventType.CREDIT_CONSUMED, account,
            credits=credits, reference_type=reference_type,
            reference_id=str(reference_id), user=user,
        )

    # ── Refund ──────────────────────────────────────────────

    @classmethod
    def refund(
        cls,
        account_or_user,
        credits: int = 1,
        reference_type: str = "document_upload",
        reference_id: str = "",
        remarks: str = "",
    ) -> None:
        user = cls._resolve_user(account_or_user)
        with transaction.atomic():
            account = UserCreditAccount.objects.select_for_update().get(user=user)

            # Idempotency: skip if already refunded for this reference
            if reference_id:
                already = CreditTransaction.objects.filter(
                    account=account,
                    transaction_type=CreditTransactionType.REFUND,
                    reference_type=reference_type,
                    reference_id=str(reference_id),
                ).exists()
                if already:
                    return

            if account.reserved_credits < credits:
                raise CreditAccountingError(
                    f"Cannot refund {credits} credits: only {account.reserved_credits} reserved."
                )

            account.reserved_credits -= credits

            # Enforce invariants
            if account.reserved_credits < 0:
                raise CreditAccountingError(
                    f"Refund would violate invariant: reserved={account.reserved_credits}"
                )

            account.save(update_fields=["reserved_credits", "updated_at"])

            CreditTransaction.objects.create(
                account=account,
                transaction_type=CreditTransactionType.REFUND,
                credits=credits,
                balance_after=account.balance_credits,
                reserved_after=account.reserved_credits,
                monthly_used_after=account.monthly_used,
                reference_type=reference_type,
                reference_id=str(reference_id),
                remarks=remarks,
                created_by=user,
            )

        cls._audit(
            AuditEventType.CREDIT_REFUNDED, account,
            credits=credits, reference_type=reference_type,
            reference_id=str(reference_id), user=user,
        )

    # ── Allocate ────────────────────────────────────────────

    @classmethod
    def allocate(
        cls,
        account_or_user,
        credits: int,
        actor=None,
        remarks: str = "",
    ) -> None:
        if credits <= 0:
            raise ValueError("Allocation credits must be positive.")
        user = cls._resolve_user(account_or_user)
        with transaction.atomic():
            account = UserCreditAccount.objects.select_for_update().get(user=user)
            account.balance_credits += credits
            account.save(update_fields=["balance_credits", "updated_at"])

            CreditTransaction.objects.create(
                account=account,
                transaction_type=CreditTransactionType.ALLOCATE,
                credits=credits,
                balance_after=account.balance_credits,
                reserved_after=account.reserved_credits,
                monthly_used_after=account.monthly_used,
                reference_type="admin",
                remarks=remarks,
                created_by=actor,
            )

        cls._audit(
            AuditEventType.CREDIT_ALLOCATION_UPDATED, account,
            credits=credits, reference_type="admin",
            user=actor, remarks=f"Allocated {credits} credits. {remarks}",
        )

    # ── Adjust ──────────────────────────────────────────────

    @classmethod
    def adjust(
        cls,
        account_or_user,
        delta: int,
        actor=None,
        remarks: str = "",
    ) -> None:
        """Manual admin correction. delta may be positive or negative."""
        user = cls._resolve_user(account_or_user)
        with transaction.atomic():
            account = UserCreditAccount.objects.select_for_update().get(user=user)
            new_balance = account.balance_credits + delta
            if new_balance < 0:
                raise ValueError(
                    f"Adjustment would result in negative balance ({new_balance})."
                )
            if new_balance < account.reserved_credits:
                raise ValueError(
                    f"Adjustment would result in balance ({new_balance}) "
                    f"below reserved credits ({account.reserved_credits})."
                )

            account.balance_credits = new_balance
            account.save(update_fields=["balance_credits", "updated_at"])

            CreditTransaction.objects.create(
                account=account,
                transaction_type=CreditTransactionType.ADJUST,
                credits=delta,
                balance_after=account.balance_credits,
                reserved_after=account.reserved_credits,
                monthly_used_after=account.monthly_used,
                reference_type="admin",
                remarks=remarks,
                created_by=actor,
            )

        cls._audit(
            AuditEventType.CREDIT_ALLOCATION_UPDATED, account,
            credits=delta, reference_type="admin",
            user=actor, remarks=f"Adjusted by {delta:+d}. {remarks}",
        )

    # ── Query helpers ───────────────────────────────────────

    @classmethod
    def get_usage_summary(cls, user) -> dict:
        account = cls.get_or_create_account(user)
        cls.reset_monthly_if_due(account)
        account.refresh_from_db()

        monthly_remaining = cls._monthly_remaining(account)
        usage_percent = 0
        if account.monthly_limit > 0:
            usage_percent = round(account.monthly_used / account.monthly_limit * 100, 1)

        return {
            "balance_credits": account.balance_credits,
            "reserved_credits": account.reserved_credits,
            "available_credits": account.available_credits,
            "monthly_limit": account.monthly_limit,
            "monthly_used": account.monthly_used,
            "monthly_remaining": monthly_remaining,
            "usage_percent": usage_percent,
            "is_active": account.is_active,
        }

    @classmethod
    def get_recent_transactions(cls, user, limit: int = 10):
        account = cls.get_or_create_account(user)
        return CreditTransaction.objects.filter(account=account).order_by("-created_at")[:limit]

    # ── Internal helpers ────────────────────────────────────

    @staticmethod
    def _resolve_user(account_or_user):
        """Accept either a User instance or UserCreditAccount and return user."""
        if isinstance(account_or_user, UserCreditAccount):
            return account_or_user.user
        return account_or_user

    @staticmethod
    def _monthly_remaining(account: UserCreditAccount) -> int:
        if account.monthly_limit == 0:
            return -1  # unlimited
        remaining = account.monthly_limit - account.monthly_used - account.reserved_credits
        return max(remaining, 0)

    @staticmethod
    def _audit(
        event_type: str,
        account: UserCreditAccount,
        credits: int = 0,
        reference_type: str = "",
        reference_id: str = "",
        user=None,
        reason_code: str = "",
        remarks: str = "",
    ) -> None:
        """Log a credit audit event using the existing AuditService."""
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="UserCreditAccount",
                entity_id=account.pk,
                event_type=event_type,
                description=remarks or f"Credit event {event_type}",
                user=user,
                metadata={
                    "credits": credits,
                    "balance_after": account.balance_credits,
                    "reserved_after": account.reserved_credits,
                    "monthly_used_after": account.monthly_used,
                    "reference_type": reference_type,
                    "reference_id": reference_id,
                    "target_user_id": account.user_id,
                },
                reason_code=reason_code,
            )
        except Exception:
            logger.exception("Failed to log credit audit event %s", event_type)
