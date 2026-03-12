"""Management command to seed PromptTemplate records from hardcoded defaults.

Usage:
    python manage.py seed_prompts          # create missing, skip existing
    python manage.py seed_prompts --force  # overwrite all with defaults
"""
from django.core.management.base import BaseCommand

from apps.core.prompt_registry import _DEFAULTS


class Command(BaseCommand):
    help = "Seed PromptTemplate records from hardcoded defaults in prompt_registry.py"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing prompts with hardcoded defaults",
        )

    def handle(self, *args, **options):
        from apps.core.models import PromptTemplate

        force = options["force"]
        created = updated = skipped = 0

        for slug, content in _DEFAULTS.items():
            category = slug.split(".")[0] if "." in slug else "misc"
            name = slug.replace(".", " — ").replace("_", " ").title()

            existing = PromptTemplate.objects.filter(slug=slug).first()
            if existing:
                if force:
                    existing.content = content
                    existing.category = category
                    existing.name = name
                    existing.version = existing.version + 1
                    existing.save()
                    updated += 1
                    self.stdout.write(f"  Updated: {slug}")
                else:
                    skipped += 1
            else:
                PromptTemplate.objects.create(
                    slug=slug,
                    name=name,
                    category=category,
                    content=content,
                    description=f"Auto-seeded from prompt_registry defaults",
                    is_active=True,
                    version=1,
                )
                created += 1
                self.stdout.write(f"  Created: {slug}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone — {created} created, {updated} updated, {skipped} skipped"
            )
        )
