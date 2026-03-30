"""Management command: push all PromptRegistry defaults to Langfuse prompt management.

Usage:
    python manage.py push_prompts_to_langfuse
    python manage.py push_prompts_to_langfuse --label staging
    python manage.py push_prompts_to_langfuse --slug agent.exception_analysis
    python manage.py push_prompts_to_langfuse --dry-run
    python manage.py push_prompts_to_langfuse --purge   # delete all then re-seed

After running this command, open Langfuse -> Prompts to see all agent prompts.
Edit them there and they will be served automatically (within 60s cache TTL).
"""
from __future__ import annotations

import base64

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Push all PromptRegistry default prompts to Langfuse prompt management."

    def add_arguments(self, parser):
        parser.add_argument(
            "--slug",
            default="",
            help="Push only this specific slug (e.g. agent.exception_analysis). "
                 "Omit to push all prompts.",
        )
        parser.add_argument(
            "--label",
            default="production",
            help="Langfuse label to attach (default: production). "
                 "Use 'staging' for testing before promoting to production.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be pushed without actually sending.",
        )
        parser.add_argument(
            "--purge",
            action="store_true",
            help="Delete ALL existing prompts from Langfuse before pushing. "
                 "Use this to clean up misnamed prompts.",
        )

    def _get_auth_header(self, public_key: str, secret_key: str) -> str:
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        return f"Basic {token}"

    def _list_langfuse_prompts(self, host: str, auth: str) -> list:
        """Return list of all prompt names in Langfuse via REST API."""
        import requests as _req
        names = []
        page = 1
        while True:
            resp = _req.get(
                f"{host.rstrip('/')}/api/public/v2/prompts",
                headers={"Authorization": auth},
                params={"page": page, "limit": 100},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", [])
            names.extend(item["name"] for item in items)
            if len(names) >= data.get("meta", {}).get("totalItems", 0):
                break
            page += 1
        return names

    def _delete_langfuse_prompt(self, host: str, auth: str, name: str) -> bool:
        """Delete a single prompt by name via REST API."""
        import requests as _req
        try:
            resp = _req.delete(
                f"{host.rstrip('/')}/api/public/v2/prompts/{name}",
                headers={"Authorization": auth},
                timeout=15,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            self.stderr.write(f"    Delete failed for '{name}': {exc}")
            return False

    def handle(self, *args, **options):
        import os
        from apps.core.langfuse_client import (
            get_client, push_prompt, slug_to_langfuse_name,
        )
        from apps.core.prompt_registry import _DEFAULTS  # noqa: protected

        target_slug = options["slug"]
        label = options["label"]
        dry_run = options["dry_run"]
        purge = options["purge"]

        # Verify Langfuse is configured
        lf = get_client()
        if not dry_run and not lf:
            self.stderr.write(self.style.ERROR(
                "Langfuse is not configured. "
                "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env"
            ))
            return

        # ── Purge existing prompts ──────────────────────────────────────
        if purge and not dry_run:
            public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
            secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
            host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
            # Strip surrounding quotes that some .env parsers leave
            secret_key = secret_key.strip('"').strip("'")
            public_key = public_key.strip('"').strip("'")
            host = host.strip('"').strip("'")
            auth = self._get_auth_header(public_key, secret_key)

            self.stdout.write(self.style.WARNING("Fetching existing prompts from Langfuse..."))
            try:
                existing = self._list_langfuse_prompts(host, auth)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Failed to list prompts: {exc}"))
                return

            if existing:
                self.stdout.write(self.style.WARNING(
                    f"Deleting {len(existing)} existing prompt(s)..."
                ))
                for name in existing:
                    ok = self._delete_langfuse_prompt(host, auth, name)
                    self.stdout.write(
                        f"  {'deleted' if ok else 'FAILED'}: {name}"
                    )
            else:
                self.stdout.write("  No existing prompts found.")
            self.stdout.write("")

        # ── Push prompts ────────────────────────────────────────────────
        prompts = (
            {target_slug: _DEFAULTS[target_slug]}
            if target_slug and target_slug in _DEFAULTS
            else _DEFAULTS
        )

        if not prompts:
            self.stderr.write(self.style.WARNING(
                f"Slug '{target_slug}' not found in PromptRegistry defaults."
            ))
            return

        self.stdout.write(
            f"{'[DRY RUN] ' if dry_run else ''}"
            f"Pushing {len(prompts)} prompt(s) to Langfuse (label={label})..."
        )
        self.stdout.write("")

        ok = 0
        fail = 0
        for slug, content in prompts.items():
            lf_name = slug_to_langfuse_name(slug)
            # Unescape Python format-string double braces {{ }} -> { }
            # so Langfuse stores clean JSON and the playground works correctly.
            content = content.replace("{{", "{").replace("}}", "}")
            preview = content[:80].replace("\n", " ")
            self.stdout.write(f"  {slug}")
            self.stdout.write(f"    langfuse name : {lf_name}")
            self.stdout.write(f"    length        : {len(content)} chars")
            self.stdout.write(f"    preview       : {preview}...")

            if dry_run:
                self.stdout.write(self.style.WARNING("    [DRY RUN -- skipped]"))
                ok += 1
            else:
                success = push_prompt(lf_name, content, labels=[label])
                if success:
                    self.stdout.write(self.style.SUCCESS("    pushed OK"))
                    ok += 1
                else:
                    self.stdout.write(self.style.ERROR("    FAILED"))
                    fail += 1
            self.stdout.write("")

        self.stdout.write("-" * 60)
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"Dry run complete. {ok} prompt(s) would be pushed."
            ))
        elif fail == 0:
            self.stdout.write(self.style.SUCCESS(
                f"Done. {ok} prompt(s) pushed to Langfuse (label={label})."
            ))
            self.stdout.write("")
            self.stdout.write("Next steps:")
            self.stdout.write("  1. Open Langfuse -> Prompts")
            self.stdout.write("  2. Edit any prompt and click Save")
            self.stdout.write("  3. Set its label to 'production' to make it active")
            self.stdout.write("  4. Django will pick it up automatically within 60s")
        else:
            self.stdout.write(self.style.ERROR(
                f"Completed with errors: {ok} pushed, {fail} failed."
            ))
