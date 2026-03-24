"""
Flush all transactional / test data while preserving configuration.

Deletes: invoices, POs, GRNs, documents, cases, reconciliation, agents,
         reviews, tools, audit events, extraction results, vendors, bulk jobs.

Preserves: users, roles, permissions, RBAC, agent definitions, tool definitions,
           reconciliation config/policies, prompt templates, extraction configs,
           control center settings, credit accounts.

Usage:
    python manage.py flush_test_data
    python manage.py flush_test_data --confirm   # skip interactive prompt
"""
from django.core.management.base import BaseCommand
from django.db import transaction


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
        from apps.auditlog.models import AuditEvent, ProcessingLog, FileProcessingStatus

        count = ProcessingLog.objects.all().delete()[0]
        self.stdout.write(f"  ProcessingLog: {count}")
        count = FileProcessingStatus.objects.all().delete()[0]
        self.stdout.write(f"  FileProcessingStatus: {count}")
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
        from apps.reviews.models import (
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
        from apps.extraction.models import ExtractionResult

        count = ExtractionResult.objects.all().delete()[0]
        self.stdout.write(f"  ExtractionResult: {count}")

        # Extraction approvals
        try:
            from apps.extraction.models import ExtractionApproval, ExtractionFieldCorrection
            count = ExtractionFieldCorrection.objects.all().delete()[0]
            self.stdout.write(f"  ExtractionFieldCorrection: {count}")
            count = ExtractionApproval.objects.all().delete()[0]
            self.stdout.write(f"  ExtractionApproval: {count}")
        except ImportError:
            pass

        # Credit transactions (keep accounts, flush transactions)
        try:
            from apps.extraction.credit_models import CreditTransaction
            count = CreditTransaction.objects.all().delete()[0]
            self.stdout.write(f"  CreditTransaction: {count}")
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
        from apps.vendors.models import VendorAlias, Vendor

        count = VendorAlias.objects.all().delete()[0]
        self.stdout.write(f"  VendorAlias: {count}")
        count = Vendor.objects.all().delete()[0]
        self.stdout.write(f"  Vendor: {count}")
