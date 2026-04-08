"""
Flush invoice-related transactional data only.

Deletes: invoices, invoice line items, document uploads, cases (and all
         children), reconciliation runs/results/exceptions, extraction
         results/approvals, agents, reviews, tools, audit events, eval runs,
         learning signals, credit transactions, copilot sessions.

Preserves: POs, GRNs, vendors, users, RBAC, config, agent/tool definitions,
           reconciliation config/policies, prompt templates, extraction configs,
           control center settings, posting reference data, ERP connections,
           credit accounts (reset to seed defaults).

Usage:
    python manage.py flush_invoices
    python manage.py flush_invoices --confirm   # skip interactive prompt
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = "Delete invoices and related case/recon/agent data, keeping POs, GRNs, vendors, config"

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Skip the interactive confirmation prompt",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            answer = input(
                "This will DELETE all invoices, cases, reconciliation results,\n"
                "agent runs, reviews, extraction results, and audit events.\n"
                "POs, GRNs, vendors, users, and config will be preserved.\n"
                "Type 'yes' to continue: "
            )
            if answer.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        self.stdout.write(self.style.WARNING("Flushing invoice-related data..."))

        with transaction.atomic():
            self._flush()

        self.stdout.write(self.style.SUCCESS(
            "Flush complete. POs, GRNs, vendors, config & users preserved."
        ))

    def _flush(self):
        flushed_models = []

        def _delete(model):
            count = model.objects.all().delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")
            flushed_models.append(model)

        # --- Audit / Observability ---
        from apps.auditlog.models import AuditEvent, ProcessingLog, FileProcessingStatus
        _delete(ProcessingLog)
        _delete(FileProcessingStatus)
        _delete(AuditEvent)

        # --- Cases (children first) ---
        from apps.cases.models import (
            APCase, APCaseActivity, APCaseArtifact, APCaseAssignment,
            APCaseComment, APCaseDecision, APCaseStage, APCaseSummary,
        )
        for model in [
            APCaseActivity, APCaseComment, APCaseSummary,
            APCaseAssignment, APCaseArtifact, APCaseDecision, APCaseStage,
        ]:
            _delete(model)

        # --- Reviews ---
        from apps.reviews.models import (
            ReviewAssignment, ReviewComment, ReviewDecision, ManualReviewAction,
        )
        for model in [ReviewDecision, ManualReviewAction, ReviewComment, ReviewAssignment]:
            _delete(model)

        # --- Agents ---
        from apps.agents.models import (
            AgentRun, AgentRecommendation, AgentStep,
            AgentMessage, DecisionLog, AgentEscalation,
        )
        for model in [
            AgentEscalation, AgentRecommendation, DecisionLog,
            AgentMessage, AgentStep, AgentRun,
        ]:
            _delete(model)

        # --- Tools ---
        from apps.tools.models import ToolCall
        _delete(ToolCall)

        # --- Cases (top-level, after children) ---
        _delete(APCase)

        # --- Reconciliation ---
        from apps.reconciliation.models import (
            ReconciliationException, ReconciliationResult,
            ReconciliationResultLine, ReconciliationRun,
        )
        for model in [
            ReconciliationException, ReconciliationResultLine,
            ReconciliationResult, ReconciliationRun,
        ]:
            _delete(model)

        # --- Extraction ---
        from apps.extraction.models import (
            ExtractionApproval, ExtractionFieldCorrection, ExtractionResult,
        )
        _delete(ExtractionFieldCorrection)
        _delete(ExtractionApproval)
        _delete(ExtractionResult)

        # --- Credits: delete transactions, reset balances ---
        try:
            from apps.extraction.credit_models import CreditTransaction, UserCreditAccount
            _delete(CreditTransaction)
            updated = UserCreditAccount.objects.all().update(
                balance_credits=100, reserved_credits=0, monthly_used=0,
            )
            self.stdout.write(f"  UserCreditAccount reset: {updated}")
        except ImportError:
            pass

        # --- Bulk extraction ---
        try:
            from apps.extraction.bulk_models import BulkExtractionItem, BulkExtractionJob
            _delete(BulkExtractionItem)
            _delete(BulkExtractionJob)
        except ImportError:
            pass

        # --- Copilot / Chat ---
        from apps.copilot.models import CopilotSessionArtifact, CopilotMessage, CopilotSession
        for model in [CopilotSessionArtifact, CopilotMessage, CopilotSession]:
            _delete(model)

        # --- Invoices + document uploads (NOT POs, GRNs, vendors) ---
        from apps.documents.models import InvoiceLineItem, Invoice, DocumentUpload
        _delete(InvoiceLineItem)
        _delete(Invoice)
        _delete(DocumentUpload)

        # --- Eval & Learning ---
        from apps.core_eval.models import (
            EvalFieldOutcome, EvalMetric, EvalRun, LearningAction, LearningSignal,
        )
        for model in [
            LearningAction, LearningSignal, EvalFieldOutcome, EvalMetric, EvalRun,
        ]:
            _delete(model)

        # --- Reset auto-increment IDs ---
        reset_count = 0
        with connection.cursor() as cursor:
            for model in flushed_models:
                table = model._meta.db_table
                try:
                    cursor.execute(f"ALTER TABLE `{table}` AUTO_INCREMENT = 1")
                    reset_count += 1
                except Exception:
                    pass
        self.stdout.write(f"  Auto-increment reset for {reset_count} tables.")
