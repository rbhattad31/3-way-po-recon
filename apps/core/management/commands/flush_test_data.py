"""
Flush all transactional / test data while preserving configuration.

Deletes: invoices, POs, GRNs, documents, cases, reconciliation, agents,
         reviews, tools, audit events, extraction results, vendors, bulk jobs,
         eval runs, learning signals, learning actions, credit transactions.
         Resets credit account balances to 100.

Preserves: users, roles, permissions, RBAC, agent definitions, tool definitions,
           reconciliation config/policies, prompt templates, extraction configs,
           control center settings, credit accounts (reset to seed defaults).
Also deletes all Langfuse traces and scores (if Langfuse is configured).

Usage:
    python manage.py flush_test_data
    python manage.py flush_test_data --confirm   # skip interactive prompt
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = "Delete all transactional/test data, keeping config, users, RBAC"

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Skip the interactive confirmation prompt",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            answer = input(
                "This will DELETE all transactional data "
                "(invoices, POs, GRNs, cases, agents, reviews, vendors, etc.).\n"
                "Configuration, users, and RBAC will be preserved.\n"
                "Type 'yes' to continue: "
            )
            if answer.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        self.stdout.write(self.style.WARNING("Flushing transactional data..."))

        with transaction.atomic():
            self._flush()

        self.stdout.write(self.style.SUCCESS("Flush complete. Config & users preserved."))

    def _flush(self):
        # --- Audit / Observability ---
        from apps.auditlog.models import AuditEvent, ProcessingLog

        count = ProcessingLog.objects.all().delete()[0]
        self.stdout.write(f"  ProcessingLog: {count}")
        count = AuditEvent.objects.all().delete()[0]
        self.stdout.write(f"  AuditEvent: {count}")

        # --- Cases ---
        from apps.cases.models import (
            APCase, APCaseActivity, APCaseArtifact, APCaseAssignment,
            APCaseComment, APCaseDecision, APCaseStage, APCaseSummary,
        )

        for model in [
            APCaseActivity, APCaseComment, APCaseSummary,
            APCaseAssignment, APCaseArtifact, APCaseDecision, APCaseStage,
        ]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Reviews ---
        from apps.cases.models import (
            ReviewAssignment, ReviewComment, ReviewDecision, ManualReviewAction,
        )

        for model in [ReviewDecision, ManualReviewAction, ReviewComment, ReviewAssignment]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Agents ---
        from apps.agents.models import (
            AgentRun, AgentRecommendation, AgentStep,
            AgentMessage, DecisionLog, AgentEscalation,
        )

        for model in [
            AgentEscalation, AgentRecommendation, DecisionLog,
            AgentMessage, AgentStep, AgentRun,
        ]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Tools ---
        from apps.tools.models import ToolCall

        count = ToolCall.objects.all().delete()[0]
        self.stdout.write(f"  ToolCall: {count}")

        # --- Cases (top-level, after children) ---
        count = APCase.objects.all().delete()[0]
        self.stdout.write(f"  APCase: {count}")

        # --- Reconciliation ---
        from apps.reconciliation.models import (
            ReconciliationException, ReconciliationResult,
            ReconciliationResultLine, ReconciliationRun,
        )

        for model in [
            ReconciliationException, ReconciliationResultLine,
            ReconciliationResult, ReconciliationRun,
        ]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Extraction ---
        from apps.extraction.models import (
            ExtractionApproval, ExtractionFieldCorrection, ExtractionResult,
        )

        count = ExtractionFieldCorrection.objects.all().delete()[0]
        self.stdout.write(f"  ExtractionFieldCorrection: {count}")
        count = ExtractionApproval.objects.all().delete()[0]
        self.stdout.write(f"  ExtractionApproval: {count}")
        count = ExtractionResult.objects.all().delete()[0]
        self.stdout.write(f"  ExtractionResult: {count}")

        # Credit accounts: delete transactions, reset balances to seed default
        try:
            from apps.extraction.credit_models import CreditTransaction, UserCreditAccount
            count = CreditTransaction.objects.all().delete()[0]
            self.stdout.write(f"  CreditTransaction: {count}")
            updated = UserCreditAccount.objects.all().update(
                balance_credits=100,
                reserved_credits=0,
                monthly_used=0,
            )
            self.stdout.write(f"  UserCreditAccount reset: {updated}")
        except ImportError:
            pass

        # Bulk extraction
        try:
            from apps.extraction.bulk_models import BulkExtractionItem, BulkExtractionJob
            count = BulkExtractionItem.objects.all().delete()[0]
            self.stdout.write(f"  BulkExtractionItem: {count}")
            count = BulkExtractionJob.objects.all().delete()[0]
            self.stdout.write(f"  BulkExtractionJob: {count}")
        except ImportError:
            pass

        # --- Copilot / Chat ---
        from apps.copilot.models import CopilotSessionArtifact, CopilotMessage, CopilotSession

        for model in [CopilotSessionArtifact, CopilotMessage, CopilotSession]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Documents ---
        from apps.documents.models import (
            GRNLineItem, GoodsReceiptNote, InvoiceLineItem,
            Invoice, PurchaseOrderLineItem, PurchaseOrder, DocumentUpload,
        )

        for model in [
            GRNLineItem, GoodsReceiptNote,
            InvoiceLineItem, Invoice,
            PurchaseOrderLineItem, PurchaseOrder,
            DocumentUpload,
        ]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Vendors ---
        from apps.vendors.models import Vendor

        count = Vendor.objects.all().delete()[0]
        self.stdout.write(f"  Vendor: {count}")

        # --- Eval & Learning ---
        from apps.core_eval.models import (
            EvalFieldOutcome, EvalMetric, EvalRun, LearningAction, LearningSignal,
        )

        for model in [
            LearningAction, LearningSignal, EvalFieldOutcome, EvalMetric, EvalRun,
        ]:
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")

        # --- Langfuse traces & scores ---
        self._flush_langfuse_traces()

        # --- Reset auto-increment IDs ---
        self._reset_auto_increments(flushed_models=[
            ProcessingLog, AuditEvent,
            APCaseActivity, APCaseComment, APCaseSummary,
            APCaseAssignment, APCaseArtifact, APCaseDecision, APCaseStage,
            APCase,
            ReviewDecision, ManualReviewAction, ReviewComment, ReviewAssignment,
            AgentEscalation, AgentRecommendation, DecisionLog,
            AgentMessage, AgentStep, AgentRun,
            ToolCall,
            ReconciliationException, ReconciliationResultLine,
            ReconciliationResult, ReconciliationRun,
            ExtractionFieldCorrection, ExtractionApproval, ExtractionResult,
            CopilotSessionArtifact, CopilotMessage, CopilotSession,
            GRNLineItem, GoodsReceiptNote,
            InvoiceLineItem, Invoice,
            PurchaseOrderLineItem, PurchaseOrder,
            DocumentUpload,
            Vendor,
            LearningAction, LearningSignal, EvalFieldOutcome, EvalMetric, EvalRun,
        ])

    def _reset_auto_increments(self, flushed_models):
        """Reset AUTO_INCREMENT to 1 for all flushed tables."""
        # Conditionally add models that may not be importable
        try:
            from apps.extraction.bulk_models import BulkExtractionItem, BulkExtractionJob
            flushed_models.extend([BulkExtractionItem, BulkExtractionJob])
        except ImportError:
            pass

        reset_count = 0
        with connection.cursor() as cursor:
            for model in flushed_models:
                table = model._meta.db_table
                try:
                    # Table name cannot be parameterised in MySQL; validate it
                    # against Django's known table registry before interpolating.
                    if not table.replace("_", "").isalnum():
                        raise ValueError(f"Refusing to reset unsafe table name: {table!r}")
                    cursor.execute("ALTER TABLE `%s` AUTO_INCREMENT = 1" % table)  # nosec B608 – validated above
                    reset_count += 1
                except Exception as exc:
                    self.stdout.write(
                        self.style.WARNING(f"  Could not reset {table}: {exc}")
                    )
        self.stdout.write(f"  Auto-increment reset for {reset_count} tables.")

    def _flush_langfuse_traces(self):
        """Delete all Langfuse traces (and their scores/observations).

        Uses the Langfuse API client if configured. Silently skips if
        Langfuse is not set up or the SDK is unavailable.
        """
        import os

        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        if not pk or not sk:
            self.stdout.write("  Langfuse: skipped (not configured)")
            return

        try:
            from langfuse.api.client import LangfuseAPI

            api = LangfuseAPI(
                base_url=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
                username=pk,
                password=sk,
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(f"  Langfuse: skipped (client init failed: {exc})")
            )
            return

        total_deleted = 0
        page = 1
        batch_size = 100

        try:
            while True:
                traces = api.trace.list(page=page, limit=batch_size)
                trace_ids = [t.id for t in (traces.data or [])]
                if not trace_ids:
                    break

                api.trace.delete_multiple(trace_ids=trace_ids)
                total_deleted += len(trace_ids)
                self.stdout.write(f"  Langfuse: deleted batch of {len(trace_ids)} traces...")

                # Always fetch page 1 since previous traces were deleted
                page = 1

        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(f"  Langfuse: error during flush ({exc})")
            )

        self.stdout.write(f"  Langfuse traces deleted: {total_deleted}")
