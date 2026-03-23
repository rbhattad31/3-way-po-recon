"""Credit management models for invoice extraction usage control."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.enums import CreditTransactionType
from apps.core.models import TimestampMixin


class UserCreditAccount(TimestampMixin):
    """Per-user credit account for invoice extraction processing.

    Each user has at most one credit account. Balance tracks available
    credits; reserved_credits tracks credits held for in-flight uploads
    that haven't completed yet.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="credit_account",
    )
    balance_credits = models.PositiveIntegerField(default=0)
    reserved_credits = models.PositiveIntegerField(default=0)
    monthly_limit = models.PositiveIntegerField(
        default=0,
        help_text="Maximum credits consumable per month. 0 = unlimited.",
    )
    monthly_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    last_reset_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "extraction_user_credit_account"
        verbose_name = "User Credit Account"
        verbose_name_plural = "User Credit Accounts"

    def __str__(self) -> str:
        return f"CreditAccount({self.user}) bal={self.balance_credits} res={self.reserved_credits}"

    @property
    def available_credits(self) -> int:
        return self.balance_credits - self.reserved_credits

    def has_available_credits(self, required: int = 1) -> bool:
        return self.available_credits >= required

    def can_consume_monthly(self, required: int = 1) -> bool:
        """Check monthly limit. Returns True if no limit or within limit."""
        if self.monthly_limit == 0:
            return True
        effective_used = self.monthly_used + self.reserved_credits
        return (effective_used + required) <= self.monthly_limit

    def clean(self):
        super().clean()
        if self.reserved_credits > self.balance_credits:
            raise ValidationError("Reserved credits cannot exceed balance.")


class CreditTransaction(models.Model):
    """Immutable ledger entry for all credit movements.

    Once created, transactions must never be edited or deleted.
    """

    account = models.ForeignKey(
        UserCreditAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=CreditTransactionType.choices,
        db_index=True,
    )
    credits = models.IntegerField(
        help_text="Positive for allocate/refund, negative for consume/adjust-down.",
    )
    balance_after = models.IntegerField()
    reserved_after = models.IntegerField(default=0)
    monthly_used_after = models.IntegerField(default=0)
    reference_type = models.CharField(
        max_length=50, blank=True, default="",
        help_text="Context: upload, system, admin",
    )
    reference_id = models.CharField(max_length=100, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_transactions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "extraction_credit_transaction"
        ordering = ["-created_at"]
        verbose_name = "Credit Transaction"
        verbose_name_plural = "Credit Transactions"

    def __str__(self) -> str:
        return (
            f"{self.transaction_type} {self.credits:+d} → "
            f"bal={self.balance_after} (account {self.account_id})"
        )
