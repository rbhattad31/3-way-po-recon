"""Bootstrap credit accounts for all eligible users.

Creates zero-balance credit accounts for users who don't have one yet.
Optionally allocates initial credits.

Usage:
    python manage.py bootstrap_credit_accounts
    python manage.py bootstrap_credit_accounts --initial-credits 100 --monthly-limit 50
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.extraction.credit_models import UserCreditAccount

User = get_user_model()


class Command(BaseCommand):
    help = "Create zero-balance credit accounts for all users without one"

    def add_arguments(self, parser):
        parser.add_argument(
            "--initial-credits", type=int, default=0,
            help="Initial balance to allocate (default: 0)",
        )
        parser.add_argument(
            "--monthly-limit", type=int, default=0,
            help="Monthly limit to set (default: 0 = unlimited)",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Also update existing accounts that have 0 balance.",
        )

    def handle(self, *args, **options):
        initial = options["initial_credits"]
        limit = options["monthly_limit"]
        force = options["force"]
        created_count = 0
        updated_count = 0

        users = User.objects.filter(is_active=True)
        for user in users:
            account, created = UserCreditAccount.objects.get_or_create(
                user=user,
                defaults={
                    "balance_credits": initial,
                    "monthly_limit": limit,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created account for {user.email}")
            elif force and account.balance_credits == 0 and initial > 0:
                account.balance_credits = initial
                if limit:
                    account.monthly_limit = limit
                account.save(update_fields=["balance_credits", "monthly_limit", "updated_at"])
                updated_count += 1
                self.stdout.write(f"  Updated account for {user.email}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created {created_count}, updated {updated_count} credit accounts "
            f"(initial={initial}, limit={limit})."
        ))
