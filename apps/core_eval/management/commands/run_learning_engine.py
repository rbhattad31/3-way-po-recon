"""
Run the controlled learning engine to aggregate signals and propose actions.

Scans LearningSignal records within a time window, applies deterministic
pattern-detection rules, and creates LearningAction proposals for human review.
No changes are auto-applied to production behavior.

Usage:
    python manage.py run_learning_engine
    python manage.py run_learning_engine --module extraction
    python manage.py run_learning_engine --days 14
    python manage.py run_learning_engine --dry-run
    python manage.py run_learning_engine --module extraction --days 30 --dry-run
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Run the learning engine: aggregate signals, detect patterns, "
        "and propose LearningAction records for human review."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--module",
            type=str,
            default="",
            help="Restrict to signals from this app_module (e.g. extraction).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Time window in days (default: 7).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Preview proposed actions without writing to DB.",
        )
        parser.add_argument(
            "--min-confidence",
            type=float,
            default=0.0,
            help="Ignore signals below this confidence (0.0-1.0, default: 0.0).",
        )
        parser.add_argument(
            "--cooldown-days",
            type=int,
            default=3,
            help="Cooldown period before re-proposing the same action (default: 3).",
        )

    def handle(self, *args, **options):
        from apps.core_eval.services.learning_engine import LearningEngine

        module = options["module"]
        days = options["days"]
        dry_run = options["dry_run"]
        min_confidence = options["min_confidence"]
        cooldown_days = options["cooldown_days"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN -- no actions will be written"))

        self.stdout.write(
            f"Running learning engine: "
            f"module={module or '(all)'}, days={days}, "
            f"min_confidence={min_confidence}, cooldown={cooldown_days}d"
        )

        engine = LearningEngine(
            days=days,
            min_confidence=min_confidence,
            cooldown_days=cooldown_days,
        )
        summary = engine.run(module=module, dry_run=dry_run)

        self.stdout.write("")
        self.stdout.write(summary.log_summary())

        if summary.actions_proposed:
            style = self.style.WARNING if dry_run else self.style.SUCCESS
            self.stdout.write(style(
                f"\n{summary.actions_proposed} action(s) "
                f"{'would be proposed' if dry_run else 'proposed'}."
            ))
        else:
            self.stdout.write(self.style.NOTICE(
                "\nNo new actions proposed."
            ))

        if summary.details:
            self.stdout.write("\nDetails:")
            for d in summary.details:
                self.stdout.write(f"  {d}")
