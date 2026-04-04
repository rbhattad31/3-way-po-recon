"""
Unified seed command -- runs all platform seed commands in the correct order.

Usage:
    python manage.py seed_all                # full seed (idempotent)
    python manage.py seed_all --flush        # flush config + reset credits before seeding
    python manage.py seed_all --skip STEP    # skip one or more steps (repeatable)

Steps (in order):
    1. seed_config       -- users, agent defs, tool defs, recon config, policies
    2. seed_rbac         -- roles, permissions, matrix, user sync
    3. seed_prompts      -- prompt templates
    4. seed_agent_contracts -- agent catalog/contract metadata
    5. seed_extraction_config -- jurisdictions, schemas, tax fields
    6. seed_control_center -- runtime settings, country packs, routing rules
    7. seed_credits      -- user credit accounts
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


STEPS = [
    {
        "name": "seed_config",
        "label": "Config (users, agents, tools, recon)",
        "flush_args": ["--flush"],
        "default_args": [],
    },
    {
        "name": "seed_rbac",
        "label": "RBAC (roles, permissions, matrix, user sync)",
        "flush_args": ["--sync-users"],
        "default_args": ["--sync-users"],
    },
    {
        "name": "seed_prompts",
        "label": "Prompts (templates)",
        "flush_args": ["--force"],
        "default_args": [],
    },
    {
        "name": "seed_agent_contracts",
        "label": "Agent contracts (catalog metadata)",
        "flush_args": [],
        "default_args": [],
    },
    {
        "name": "seed_extraction_config",
        "label": "Extraction config (jurisdictions, schemas)",
        "flush_args": [],
        "default_args": [],
    },
    {
        "name": "seed_control_center",
        "label": "Control center (runtime settings, country packs)",
        "flush_args": [],
        "default_args": [],
    },
    {
        "name": "seed_credits",
        "label": "Credits (user credit accounts)",
        "flush_args": [],
        "default_args": [],
    },
]


class Command(BaseCommand):
    help = "Run all platform seed commands in the correct order."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Flush config data and force-overwrite prompts before seeding.",
        )
        parser.add_argument(
            "--skip",
            action="append",
            default=[],
            metavar="STEP",
            help=(
                "Skip a step by command name (e.g. --skip seed_credits). "
                "Can be repeated."
            ),
        )

    def handle(self, *args, **options):
        flush = options["flush"]
        skip = set(options["skip"])

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== seed_all: Full Platform Seed%s ===" % (" (flush mode)" if flush else "")
        ))

        available = ", ".join(s["name"] for s in STEPS)
        if skip:
            invalid = skip - {s["name"] for s in STEPS}
            if invalid:
                self.stderr.write(self.style.ERROR(
                    "Unknown step(s) to skip: %s\nAvailable: %s" % (", ".join(invalid), available)
                ))
                return

        total = len(STEPS)
        for idx, step in enumerate(STEPS, 1):
            name = step["name"]
            label = step["label"]

            if name in skip:
                self.stdout.write("  [%d/%d] SKIP  %s" % (idx, total, label))
                continue

            self.stdout.write(self.style.HTTP_INFO(
                "  [%d/%d] Running %s ..." % (idx, total, label)
            ))

            cmd_args = step["flush_args"] if flush else step["default_args"]
            try:
                call_command(name, *cmd_args, stdout=self.stdout, stderr=self.stderr)
                self.stdout.write(self.style.SUCCESS(
                    "         %s -- OK" % name
                ))
            except Exception as exc:
                self.stderr.write(self.style.ERROR(
                    "         %s -- FAILED: %s" % (name, exc)
                ))

        self.stdout.write(self.style.SUCCESS("\n=== seed_all complete ===\n"))
