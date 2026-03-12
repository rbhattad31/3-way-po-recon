"""
Management command: seed_ap_data

Seeds realistic McDonald's Saudi Arabia AP case data for dev/demo/QA/UI testing.

Modes:
  --mode=demo   → 30 deterministic scenarios (default)
  --mode=qa     → 30 deterministic + 50 generated scenarios
  --mode=large  → 30 deterministic + 200 generated scenarios

Usage:
    python manage.py seed_ap_data
    python manage.py seed_ap_data --mode=demo
    python manage.py seed_ap_data --mode=qa
    python manage.py seed_ap_data --mode=large
    python manage.py seed_ap_data --reset           # flush seeded data first
    python manage.py seed_ap_data --seed=42          # random seed for QA/large
    python manage.py seed_ap_data --summary          # print scenario summary table

Prerequisites:
    Run `python manage.py seed_config` first to create users, agent definitions,
    tool definitions, and reconciliation config/policies.
"""
from __future__ import annotations

import logging
import time
from io import StringIO

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Seed McDonald's Saudi Arabia AP case data for dev/demo/QA testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            type=str,
            default="demo",
            choices=["demo", "qa", "large"],
            help="Seed mode: demo (30 scenarios), qa (+50), large (+200)",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all seeded AP data before re-creating",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for generated (non-deterministic) scenarios",
        )
        parser.add_argument(
            "--summary",
            action="store_true",
            help="Print scenario summary table after seeding",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        do_reset = options["reset"]
        rand_seed = options["seed"]
        show_summary = options["summary"]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'='*60}\n"
            f"  McDonald's Saudi Arabia AP Seed Data\n"
            f"  Mode: {mode.upper()} | Reset: {do_reset} | Seed: {rand_seed}\n"
            f"{'='*60}\n"
        ))

        start = time.time()

        if do_reset:
            self._reset_data()

        with transaction.atomic():
            self._seed(mode, rand_seed)

        elapsed = time.time() - start
        self.stdout.write(self.style.SUCCESS(
            f"\nSeeding completed in {elapsed:.1f}s"
        ))

        if show_summary or mode == "demo":
            self._print_summary()

    # ----------------------------------------------------------------
    # Reset
    # ----------------------------------------------------------------

    def _reset_data(self):
        self.stdout.write(self.style.WARNING("Resetting seeded AP data..."))

        from apps.auditlog.models import AuditEvent
        from apps.cases.models import (
            APCase,
            APCaseActivity,
            APCaseArtifact,
            APCaseAssignment,
            APCaseComment,
            APCaseDecision,
            APCaseStage,
            APCaseSummary,
        )
        from apps.reviews.models import ReviewAssignment, ReviewComment, ReviewDecision, ManualReviewAction
        from apps.agents.models import AgentRun, AgentRecommendation, AgentStep, AgentMessage, DecisionLog
        from apps.reconciliation.models import (
            ReconciliationException,
            ReconciliationResult,
            ReconciliationResultLine,
            ReconciliationRun,
        )
        from apps.documents.models import (
            GRNLineItem,
            GoodsReceiptNote,
            InvoiceLineItem,
            Invoice,
            PurchaseOrderLineItem,
            PurchaseOrder,
        )

        # Delete in dependency order
        APCaseActivity.objects.all().delete()
        APCaseComment.objects.all().delete()
        APCaseSummary.objects.all().delete()
        APCaseAssignment.objects.all().delete()
        APCaseArtifact.objects.all().delete()
        APCaseDecision.objects.all().delete()
        APCaseStage.objects.all().delete()

        ReviewDecision.objects.all().delete()
        ManualReviewAction.objects.all().delete()
        ReviewComment.objects.all().delete()
        ReviewAssignment.objects.all().delete()

        AgentRecommendation.objects.all().delete()
        DecisionLog.objects.all().delete()
        AgentMessage.objects.all().delete()
        AgentStep.objects.all().delete()
        AgentRun.objects.all().delete()

        AuditEvent.objects.filter(entity_type="APCase").delete()

        APCase.objects.all().delete()

        ReconciliationException.objects.all().delete()
        ReconciliationResultLine.objects.all().delete()
        ReconciliationResult.objects.all().delete()
        ReconciliationRun.objects.filter(celery_task_id__startswith="seed-").delete()

        GRNLineItem.objects.all().delete()
        GoodsReceiptNote.objects.all().delete()
        InvoiceLineItem.objects.all().delete()
        Invoice.objects.all().delete()
        PurchaseOrderLineItem.objects.all().delete()
        PurchaseOrder.objects.all().delete()

        from apps.vendors.models import VendorAlias, Vendor
        VendorAlias.objects.all().delete()
        Vendor.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("  Reset complete."))

    # ----------------------------------------------------------------
    # Seed
    # ----------------------------------------------------------------

    def _seed(self, mode: str, rand_seed: int):
        from .seed_helpers.constants import SCENARIOS
        from .seed_helpers.master_data import seed_users, seed_vendors, seed_vendor_aliases
        from .seed_helpers.transactional_data import create_transactional_data
        from .seed_helpers.case_builder import create_cases_and_recon
        from .seed_helpers.agent_review_data import seed_agent_review_data

        # 1. Master data
        self.stdout.write("  [1/5] Seeding users...")
        users = seed_users()
        admin = users.get("admin") or list(users.values())[0]
        self.stdout.write(self.style.SUCCESS(f"        {len(users)} users ready"))

        self.stdout.write("  [2/5] Seeding vendors & aliases...")
        vendors = seed_vendors(admin)
        n_aliases = seed_vendor_aliases(vendors, admin)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(vendors)} vendors, {n_aliases} aliases"
        ))

        # 2. Transactional data
        self.stdout.write("  [3/5] Creating POs, GRNs, Invoices (30 scenarios)...")
        scenario_data = create_transactional_data(SCENARIOS, vendors, admin)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(scenario_data)} scenario record sets created"
        ))

        # 3. Cases & reconciliation
        self.stdout.write("  [4/5] Creating AP Cases, Recon Results, Exceptions...")
        case_data = create_cases_and_recon(scenario_data, admin)
        self.stdout.write(self.style.SUCCESS(
            f"        {len(case_data)} AP Cases created"
        ))

        # 4. Agent, Review, Audit data
        self.stdout.write("  [5/5] Creating Agent runs, Reviews, Summaries, Audit events...")
        stats = seed_agent_review_data(scenario_data, case_data, users, admin)
        self.stdout.write(self.style.SUCCESS(
            f"        {stats['agent_runs']} agent runs, "
            f"{stats['recommendations']} recommendations, "
            f"{stats['assignments']} review assignments, "
            f"{stats['comments']} comments, "
            f"{stats['summaries']} summaries"
        ))

        # 5. QA/Large mode — generate additional random scenarios
        if mode in ("qa", "large"):
            extra = 50 if mode == "qa" else 200
            self.stdout.write(f"  [+] Generating {extra} additional random scenarios (seed={rand_seed})...")
            from .seed_helpers.bulk_generator import generate_bulk_scenarios
            bulk_stats = generate_bulk_scenarios(
                start_num=31,
                count=extra,
                vendors=vendors,
                users=users,
                admin=admin,
                rand_seed=rand_seed,
            )
            self.stdout.write(self.style.SUCCESS(
                f"        {bulk_stats['cases']} additional cases created"
            ))

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------

    def _print_summary(self):
        from apps.cases.models import APCase

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'='*110}\n"
            f"  SEEDED SCENARIO SUMMARY\n"
            f"{'='*110}"
        ))
        self.stdout.write(
            f"{'#':>3} {'Case':12} {'Invoice':14} {'Vendor':32} {'Path':10} "
            f"{'Status':28} {'Priority':9} {'Review':7}"
        )
        self.stdout.write("-" * 110)

        cases = APCase.objects.select_related("invoice", "vendor").order_by("case_number")
        for case in cases:
            vendor_name = case.vendor.name[:30] if case.vendor else "Unknown"
            inv_num = case.invoice.invoice_number if case.invoice else "N/A"
            self.stdout.write(
                f"{case.case_number[3:]:>3} {case.case_number:12} {inv_num:14} "
                f"{vendor_name:32} {case.processing_path:10} "
                f"{case.status:28} {case.priority:9} "
                f"{'Yes' if case.requires_human_review else 'No':7}"
            )

        self.stdout.write(f"\nTotal: {cases.count()} cases")

        # Distribution summary
        from django.db.models import Count
        self.stdout.write(self.style.MIGRATE_HEADING("\n  Distribution:"))

        by_path = APCase.objects.values("processing_path").annotate(c=Count("id")).order_by("processing_path")
        self.stdout.write("  By Path:   " + "  |  ".join(f"{r['processing_path']}: {r['c']}" for r in by_path))

        by_status = APCase.objects.values("status").annotate(c=Count("id")).order_by("-c")
        self.stdout.write("  By Status: " + "  |  ".join(f"{r['status']}: {r['c']}" for r in by_status[:8]))

        by_priority = APCase.objects.values("priority").annotate(c=Count("id")).order_by("priority")
        self.stdout.write("  By Priority: " + "  |  ".join(f"{r['priority']}: {r['c']}" for r in by_priority))
