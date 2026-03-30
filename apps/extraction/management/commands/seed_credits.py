"""Reset every active user's credit balance to exactly 100 (or --target).

For each active user:
  - Creates a UserCreditAccount if one does not exist.
  - Writes an ADJUST ledger entry to zero out the old balance.
  - Writes an ALLOCATE ledger entry to set the new balance to the target.
  - reserved_credits and monthly_used are also reset to 0.

Usage:
    python manage.py seed_credits
    python manage.py seed_credits --target 200   # reset to a different amount
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.enums import CreditTransactionType
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount

User = get_user_model()

DEFAULT_TARGET = 100


class Command(BaseCommand):
    help = "Reset every active user's credit account to the target balance and record ledger entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--target",
            type=int,
            default=DEFAULT_TARGET,
            help=f"Target credit balance per user (default: {DEFAULT_TARGET})",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        target = options["target"]
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Resetting credits -- target balance: {target} per user"
            )
        )

        admin = User.objects.filter(is_superuser=True).first()
        users = User.objects.filter(is_active=True).order_by("email")
        today = timezone.now().date().isoformat()

        created_accounts = 0
        reset_count = 0

        for user in users:
            account, was_created = UserCreditAccount.objects.get_or_create(
                user=user,
                defaults={
                    "balance_credits": 0,
                    "reserved_credits": 0,
                    "monthly_limit": 0,
                    "monthly_used": 0,
                    "is_active": True,
                },
            )
            if was_created:
                created_accounts += 1

            old_balance = account.balance_credits

            # Step 1: zero out old balance with an ADJUST entry (if non-zero)
            if old_balance != 0:
                CreditTransaction.objects.create(
                    account=account,
                    transaction_type=CreditTransactionType.ADJUST,
                    credits=-old_balance,
                    balance_after=0,
                    reserved_after=0,
                    monthly_used_after=0,
                    reference_type="system",
                    reference_id="seed_credits_reset",
                    remarks=f"Reset: zeroed old balance of {old_balance} on {today}",
                    created_by=admin,
                )

            # Step 2: allocate the target balance
            account.balance_credits = target
            account.reserved_credits = 0
            account.monthly_used = 0
            account.save(update_fields=["balance_credits", "reserved_credits", "monthly_used", "updated_at"])

            CreditTransaction.objects.create(
                account=account,
                transaction_type=CreditTransactionType.ALLOCATE,
                credits=target,
                balance_after=target,
                reserved_after=0,
                monthly_used_after=0,
                reference_type="system",
                reference_id="seed_credits_allocate",
                remarks=f"Reset: allocated {target} credits on {today}",
                created_by=admin,
            )

            self.stdout.write(
                f"  RESET {user.email}: {old_balance} -> {target}"
            )
            reset_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone -- accounts created: {created_accounts}, reset: {reset_count}"
            )
        )
