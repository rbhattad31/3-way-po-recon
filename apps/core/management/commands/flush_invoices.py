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
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=None,
            help="Flush invoice-related data only for a specific tenant (CompanyProfile ID)",
        )

    def handle(self, *args, **options):
        tenant_id = options.get("tenant_id")

        if not options["confirm"]:
            if tenant_id is None:
                answer = input(
                    "This will DELETE all invoices, cases, reconciliation results,\n"
                    "agent runs, reviews, extraction results, and audit events.\n"
                    "POs, GRNs, vendors, users, and config will be preserved.\n"
                    "Type 'yes' to continue: "
                )
            else:
                answer = input(
                    f"This will DELETE invoice-related data for tenant_id={tenant_id}.\n"
                    "POs, GRNs, vendors, users, and config will be preserved.\n"
                    "Type 'yes' to continue: "
                )
            if answer.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        if tenant_id is None:
            self.stdout.write(self.style.WARNING("Flushing invoice-related data..."))
        else:
            self.stdout.write(self.style.WARNING(
                f"Flushing invoice-related data for tenant_id={tenant_id}..."
            ))

        with transaction.atomic():
            self._flush(tenant_id=tenant_id)

        if tenant_id is None:
            self.stdout.write(self.style.SUCCESS(
                "Flush complete. POs, GRNs, vendors, config & users preserved."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Tenant flush complete for tenant_id={tenant_id}. "
                "POs, GRNs, vendors, config & users preserved."
            ))

    def _flush(self, tenant_id=None):
        flushed_models = []
        skipped_models = []

        def _scoped_queryset(model):
            qs = model.objects.all()
            if tenant_id is None:
                return qs

            concrete_fields = {
                f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)
            }
            if "tenant" in concrete_fields:
                return qs.filter(tenant_id=tenant_id)

            key = f"{model._meta.app_label}.{model.__name__}"
            relation_filters = {
                "extraction.CreditTransaction": "account__user__company_id",
                "extraction.UserCreditAccount": "user__company_id",
                "extraction.BulkExtractionItem": "job__tenant_id",
                "extraction_core.ExtractionOCRText": "extraction_run__tenant_id",
            }
            rel_filter = relation_filters.get(key)
            if rel_filter:
                return qs.filter(**{rel_filter: tenant_id})

            skipped_models.append(key)
            return qs.none()

        def _delete(model):
            qs = _scoped_queryset(model)
            count = qs.delete()[0]
            self.stdout.write(f"  {model.__name__}: {count}")
            if tenant_id is None:
                flushed_models.append(model)

        # --- Audit / Observability ---
        from apps.auditlog.models import AuditEvent, ProcessingLog
        _delete(ProcessingLog)
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
        from apps.cases.models import (
            ReviewAssignment, ReviewComment, ReviewDecision, ManualReviewAction,
        )
        for model in [ReviewDecision, ManualReviewAction, ReviewComment, ReviewAssignment]:
            _delete(model)

        # --- Agents ---
        from apps.agents.models import (
            AgentOrchestrationRun, AgentRun, AgentRecommendation, AgentStep,
            AgentMessage, DecisionLog, AgentEscalation,
        )
        for model in [
            AgentEscalation, AgentRecommendation, DecisionLog,
            AgentMessage, AgentStep, AgentRun, AgentOrchestrationRun,
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
            credit_qs = UserCreditAccount.objects.all()
            if tenant_id is not None:
                credit_qs = credit_qs.filter(user__company_id=tenant_id)
            updated = credit_qs.update(balance_credits=100, reserved_credits=0, monthly_used=0)
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

        # --- Posting (children first) ---
        try:
            from apps.posting.models import InvoicePostingFieldCorrection, InvoicePosting
            _delete(InvoicePostingFieldCorrection)
            _delete(InvoicePosting)
        except ImportError:
            pass

        # --- Posting Core (children first) ---
        try:
            from apps.posting_core.models import (
                PostingApprovalRecord, PostingEvidence, PostingIssue,
                PostingLineItem, PostingFieldValue, PostingRun,
            )
            for model in [
                PostingApprovalRecord, PostingEvidence, PostingIssue,
                PostingLineItem, PostingFieldValue, PostingRun,
            ]:
                _delete(model)
        except ImportError:
            pass

        # --- ERP Integration (logs + cache, NOT connections) ---
        try:
            from apps.erp_integration.models import (
                ERPSubmissionLog, ERPResolutionLog, ERPReferenceCacheRecord,
            )
            for model in [ERPSubmissionLog, ERPResolutionLog, ERPReferenceCacheRecord]:
                _delete(model)
        except ImportError:
            pass

        # --- Extraction Core (children first) ---
        try:
            from apps.extraction_core.models import (
                ExtractionCorrection, ExtractionApprovalRecord,
                ExtractionAnalyticsSnapshot, ExtractionEvidence,
                ExtractionIssue, ExtractionLineItem, ExtractionFieldValue,
                ExtractionOCRText, ExtractionRun,
            )
            for model in [
                ExtractionCorrection, ExtractionApprovalRecord,
                ExtractionAnalyticsSnapshot, ExtractionEvidence,
                ExtractionIssue, ExtractionLineItem, ExtractionFieldValue,
                ExtractionOCRText, ExtractionRun,
            ]:
                _delete(model)
        except ImportError:
            pass

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

        # --- Reset auto-increment IDs (full flush only) ---
        reset_count = 0
        if tenant_id is None:
            with connection.cursor() as cursor:
                for model in flushed_models:
                    table = model._meta.db_table
                    try:
                        # Table name cannot be parameterised in MySQL; validate it
                        # against Django's known table registry before interpolating.
                        if not table.replace("_", "").isalnum():
                            raise ValueError(f"Refusing to reset unsafe table name: {table!r}")
                        cursor.execute("ALTER TABLE `%s` AUTO_INCREMENT = 1" % table)  # nosec B608 - validated above
                        reset_count += 1
                    except Exception:
                        pass
            self.stdout.write(f"  Auto-increment reset for {reset_count} tables.")
        elif skipped_models:
            skipped = ", ".join(sorted(set(skipped_models)))
            self.stdout.write(self.style.WARNING(
                f"  Skipped tenant scoping for models without tenant relation: {skipped}"
            ))
