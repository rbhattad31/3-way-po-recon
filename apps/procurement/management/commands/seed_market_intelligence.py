"""Management command: seed_market_intelligence

Calls the real LLM (same code path as clicking Refresh on the page) for every
existing ProcurementRequest that does not yet have a MarketIntelligenceSuggestion
and stores the result in the DB so the page loads instantly.

Usage examples
--------------
# Back-fill all existing requests that have no MI data yet (via Celery):
    python manage.py seed_market_intelligence

# Back-fill synchronously in this process (no Celery required):
    python manage.py seed_market_intelligence --sync

# Regenerate even if data already exists:
    python manage.py seed_market_intelligence --force

# Only a specific request:
    python manage.py seed_market_intelligence --pk 42 --sync

# Only HVAC domain:
    python manage.py seed_market_intelligence --domain HVAC --sync

# Just list which requests are missing MI data:
    python manage.py seed_market_intelligence --list
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate and store AI market intelligence for requests that are missing it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pk", type=int, default=None,
            help="Process only the ProcurementRequest with this primary key.",
        )
        parser.add_argument(
            "--domain", type=str, default=None,
            help="Filter by domain_code (e.g. HVAC).",
        )
        parser.add_argument(
            "--force", action="store_true", default=False,
            help="Regenerate even if a MarketIntelligenceSuggestion already exists.",
        )
        parser.add_argument(
            "--sync", action="store_true", default=False,
            help="Run the LLM calls synchronously here instead of queuing Celery tasks.",
        )
        parser.add_argument(
            "--perplexity", action="store_true", default=False,
            help="Use Perplexity sonar-pro (live web search) instead of Azure OpenAI.",
        )
        parser.add_argument(
            "--list", action="store_true", default=False,
            help="List requests missing MI data and exit without generating anything.",
        )
        parser.add_argument(
            "--replace", action="store_true", default=False,
            help="Delete all existing MarketIntelligenceSuggestion records for the "
                 "targeted requests before regenerating (implies --force). "
                 "Use this to fully replace stale/bad-URL data with fresh Perplexity results.",
        )

    def handle(self, *args, **options):
        from apps.procurement.models import MarketIntelligenceSuggestion, ProcurementRequest

        qs = ProcurementRequest.objects.all().order_by("pk")

        if options["pk"]:
            qs = qs.filter(pk=options["pk"])
        if options["domain"]:
            qs = qs.filter(domain_code__iexact=options["domain"])

        # --replace implies --force; delete all existing MI records for targeted requests first
        if options["replace"]:
            options["force"] = True
            target_pks = list(qs.values_list("pk", flat=True))
            deleted_count, _ = MarketIntelligenceSuggestion.objects.filter(
                request_id__in=target_pks
            ).delete()
            if deleted_count:
                self.stdout.write(self.style.WARNING(
                    f"  Deleted {deleted_count} existing MarketIntelligenceSuggestion record(s) "
                    f"for {len(target_pks)} request(s) -- will regenerate fresh data."
                ))

        if not options["force"]:
            already_done = MarketIntelligenceSuggestion.objects.filter(
                request_id__in=qs.values_list("pk", flat=True)
            ).values_list("request_id", flat=True)
            qs = qs.exclude(pk__in=already_done)

        total = qs.count()

        # --list mode: just print and exit
        if options["list"]:
            if total == 0:
                self.stdout.write(self.style.SUCCESS(
                    "All matching requests already have market intelligence data."
                ))
                return
            self.stdout.write(self.style.WARNING(
                f"{total} request(s) missing market intelligence:\n"
            ))
            for r in qs:
                self.stdout.write(
                    f"  pk={r.pk:<6}  domain={r.domain_code or '-':<10}  {r.title[:70]}"
                )
            return

        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to do -- every matching request already has market intelligence data. "
                "Use --force to regenerate."
            ))
            return

        mode = "synchronously (LLM called here)" if options["sync"] else "via Celery background tasks"
        provider = " with Perplexity (live web search)" if options["perplexity"] else " (Azure OpenAI)"
        self.stdout.write(self.style.NOTICE(
            f"Generating market intelligence for {total} request(s) {mode}{provider} ..."
        ))

        ok = 0
        failed = 0

        if options["sync"]:
            # Call the real LLM/Perplexity directly in this process -- same code as clicking Refresh
            from apps.procurement.services.market_intelligence_service import (
                MarketIntelligenceService,
            )
            
            # Choose which generation method to use
            if options["perplexity"]:
                gen_method = MarketIntelligenceService.generate_with_perplexity
                method_name = "Perplexity (sonar-pro, forced)"
            else:
                # generate_auto routes to Perplexity if PERPLEXITY_API_KEY is set,
                # otherwise falls back to Azure OpenAI automatically.
                gen_method = MarketIntelligenceService.generate_auto
                method_name = "Perplexity (sonar) via generate_auto"
            
            for req in qs.iterator():
                label = f"pk={req.pk}  {req.title[:55]}"
                self.stdout.write(f"  -> {label} ... ", ending="")
                self.stdout.flush()
                try:
                    result = gen_method(req, generated_by=None)
                    n = len(result.get("suggestions", []))
                    self.stdout.write(self.style.SUCCESS(f"OK  ({n} suggestions via {method_name})"))
                    ok += 1
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"FAILED  {exc}"))
                    failed += 1
        else:
            # Queue a Celery task per request -- workers call the same LLM
            from apps.procurement.tasks import generate_market_intelligence_task

            for req in qs.iterator():
                try:
                    generate_market_intelligence_task.delay(req.pk)
                    self.stdout.write(f"  -> pk={req.pk}  queued")
                    ok += 1
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(
                        f"  -> pk={req.pk}  could not queue: {exc}"
                    ))
                    failed += 1

        summary = f"\nDone -- {ok} {'dispatched to Celery' if not options['sync'] else 'completed'}"
        if failed:
            summary += f", {failed} failed"
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

        if not options["sync"] and ok:
            self.stdout.write(self.style.NOTICE(
                "  Celery tasks queued. Make sure a worker is running, "
                "or re-run with --sync to call the LLM directly."
            ))
