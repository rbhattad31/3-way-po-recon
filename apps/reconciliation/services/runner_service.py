"""Reconciliation runner — orchestrates the deterministic match pipeline.

Flow for each invoice:
  1. PO Lookup
  2. Mode Resolution (TWO_WAY vs THREE_WAY)
  3. Execution Router dispatch (header + line ± GRN)
  4. Mode-aware Classification
  5. Mode-aware Exception Building
  6. Result Persistence (with mode metadata)
  7. Review assignment (if needed)
  8. Invoice status transition
"""
from __future__ import annotations

import logging
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.core.enums import (
    AuditEventType,
    InvoiceStatus,
    MatchStatus,
    ReconciliationMode,
    ReconciliationRunStatus,
)
from apps.documents.models import Invoice
from apps.reconciliation.models import ReconciliationConfig, ReconciliationRun
from apps.reconciliation.services.classification_service import ClassificationService
from apps.reconciliation.services.exception_builder_service import ExceptionBuilderService
from apps.reconciliation.services.execution_router import ReconciliationExecutionRouter
from apps.reconciliation.services.mode_resolver import ModeResolutionResult, ReconciliationModeResolver
from apps.reconciliation.services.po_lookup_service import POLookupService
from apps.reconciliation.services.result_service import ReconciliationResultService
from apps.reconciliation.services.tolerance_engine import ToleranceEngine
from apps.core.decorators import observed_service
from apps.core.metrics import MetricsService

logger = logging.getLogger(__name__)


class ReconciliationRunnerService:
    """High-level orchestrator for a batch reconciliation run."""

    def __init__(self, config: Optional[ReconciliationConfig] = None):
        self.config = config or self._default_config()
        self.tolerance = ToleranceEngine(self.config)

        # Sub-services
        self.po_lookup = POLookupService()
        self.mode_resolver = ReconciliationModeResolver(self.config)
        self.router = ReconciliationExecutionRouter(self.tolerance)
        self.classifier = ClassificationService()
        self.exception_builder = ExceptionBuilderService()
        self.result_service = ReconciliationResultService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @observed_service("reconciliation.runner.run", audit_event="RECONCILIATION_STARTED", entity_type="ReconciliationRun")
    def run(
        self,
        invoices: Optional[List[Invoice]] = None,
        triggered_by=None,
        lf_trace=None,
        lf_trace_id=None,
    ) -> ReconciliationRun:
        """Execute reconciliation for a set of invoices.

        If *invoices* is None, all invoices with status READY_FOR_RECON are
        selected automatically.
        """
        if invoices is None:
            invoices = list(
                Invoice.objects.filter(status=InvoiceStatus.READY_FOR_RECON)
                .select_related("vendor", "document_upload")
            )

        recon_run = ReconciliationRun.objects.create(
            status=ReconciliationRunStatus.RUNNING,
            config=self.config,
            started_at=timezone.now(),
            total_invoices=len(invoices),
            triggered_by=triggered_by,
        )

        # Langfuse: open a "reconciliation_run" span.
        # If a task-level parent trace was provided (lf_trace), create this
        # span as a child so the hierarchy in Langfuse is:
        #   reconciliation_task (task root)
        #     -- reconciliation_run (this span)
        #          -- per-invoice pipeline spans
        # If no parent trace exists, open a standalone root trace.
        _trace_id = lf_trace_id or getattr(recon_run, "trace_id", None) or str(recon_run.pk)
        _lf_run_trace = None
        _lf_run_trace_is_mine = False
        try:
            from apps.core.langfuse_client import start_span_safe, start_trace_safe
            if lf_trace is not None:
                _lf_run_trace = start_span_safe(
                    lf_trace,
                    "reconciliation_run",
                    metadata={
                        "run_pk": recon_run.pk,
                        "total_invoices": len(invoices),
                        "config": self.config.name,
                    },
                )
                _lf_run_trace_is_mine = True
            else:
                _lf_run_trace = start_trace_safe(
                    _trace_id,
                    "reconciliation_run",
                    metadata={
                        "run_pk": recon_run.pk,
                        "total_invoices": len(invoices),
                        "config": self.config.name,
                    },
                )
                _lf_run_trace_is_mine = True
        except Exception:
            pass

        # Audit: reconciliation started
        from apps.auditlog.services import AuditService
        for inv in invoices:
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=inv.pk,
                event_type=AuditEventType.RECONCILIATION_STARTED,
                description=f"Reconciliation run #{recon_run.pk} started",
                user=triggered_by,
                metadata={"run_id": recon_run.pk, "config": self.config.name},
            )

        logger.info(
            "Starting reconciliation run %s for %d invoices (config=%s)",
            recon_run.pk, len(invoices), self.config.name,
        )

        matched = partial = unmatched = errors = review = 0

        for invoice in invoices:
            try:
                status = self._reconcile_single(recon_run, invoice, lf_trace=_lf_run_trace, lf_trace_id=_trace_id)
                if status == MatchStatus.MATCHED:
                    matched += 1
                elif status == MatchStatus.PARTIAL_MATCH:
                    partial += 1
                elif status == MatchStatus.UNMATCHED:
                    unmatched += 1
                elif status == MatchStatus.REQUIRES_REVIEW:
                    review += 1
                else:
                    errors += 1
            except Exception:
                logger.exception("Error reconciling invoice %s", invoice.pk)
                errors += 1

        # Finalise run
        recon_run.status = ReconciliationRunStatus.COMPLETED
        recon_run.completed_at = timezone.now()
        recon_run.matched_count = matched
        recon_run.partial_count = partial
        recon_run.unmatched_count = unmatched
        recon_run.error_count = errors
        recon_run.review_count = review
        recon_run.save()

        # Langfuse: close root trace
        try:
            if _lf_run_trace_is_mine and _lf_run_trace:
                from apps.core.langfuse_client import end_span_safe
                end_span_safe(
                    _lf_run_trace,
                    output={
                        "matched": matched,
                        "partial": partial,
                        "unmatched": unmatched,
                        "errors": errors,
                        "review": review,
                    },
                )
        except Exception:
            pass

        # Audit: reconciliation completed for each invoice
        for inv in invoices:
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=inv.pk,
                event_type=AuditEventType.RECONCILIATION_COMPLETED,
                description=f"Reconciliation run #{recon_run.pk} completed",
                user=triggered_by,
                metadata={
                    "run_id": recon_run.pk, "matched": matched,
                    "partial": partial, "unmatched": unmatched, "errors": errors,
                },
            )

        logger.info(
            "Reconciliation run %s completed: matched=%d partial=%d unmatched=%d errors=%d review=%d",
            recon_run.pk, matched, partial, unmatched, errors, review,
        )
        return recon_run

    # ------------------------------------------------------------------
    # Single-invoice pipeline
    # ------------------------------------------------------------------
    def _reconcile_single(
        self, run: ReconciliationRun, invoice: Invoice,
        lf_trace=None, lf_trace_id=None,
    ) -> MatchStatus:
        """Run the deterministic match for one invoice (2-way or 3-way).

        This method creates per-stage Langfuse spans under ``lf_trace`` and
        emits both observation-level and trace-level scores suitable for
        later deterministic and human evaluation.
        """
        from apps.core.langfuse_client import (
            start_span_safe, end_span_safe, score_trace_safe, score_observation_safe,
            update_trace_safe,
        )

        _tid = lf_trace_id or getattr(run, "trace_id", "") or str(run.pk)

        # ---------------------------------------------------------------
        # 1. PO Lookup
        # ---------------------------------------------------------------
        _lf_po = start_span_safe(lf_trace, "po_lookup", metadata={
            "invoice_id": invoice.pk,
            "po_number": invoice.po_number or "",
        })
        po_result = self.po_lookup.lookup(invoice)
        _po_meta = {
            "po_found": po_result.found,
            "po_number": getattr(po_result.purchase_order, "po_number", "") if po_result.found else "",
            "lookup_source": po_result.lookup_method or "not_found",
            "erp_source_type": po_result.erp_source_type,
            "is_stale": po_result.is_stale,
            "line_count": (
                po_result.purchase_order.line_items.count()
                if po_result.found and po_result.purchase_order else 0
            ),
        }
        end_span_safe(_lf_po, output=_po_meta)
        score_observation_safe(_lf_po, "recon_po_lookup_success", 1.0 if po_result.found else 0.0)
        score_observation_safe(_lf_po, "recon_po_lookup_fresh", 0.0 if po_result.is_stale else 1.0)
        score_observation_safe(
            _lf_po, "recon_po_lookup_authoritative",
            1.0 if po_result.erp_source_type in ("MIRROR_DB", "API") else 0.0,
        )

        # 1b. If PO was discovered (not by PO number), backfill invoice.po_number
        if po_result.found and po_result.lookup_method == "vendor_amount":
            invoice.po_number = po_result.purchase_order.po_number
            invoice.save(update_fields=["po_number", "updated_at"])

            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="Invoice",
                entity_id=invoice.pk,
                event_type=AuditEventType.RECONCILIATION_COMPLETED,
                description=(
                    f"PO {po_result.purchase_order.po_number} discovered deterministically "
                    f"via vendor+amount match (vendor={invoice.vendor_id}, "
                    f"amount={invoice.total_amount})"
                ),
                metadata={
                    "lookup_method": "vendor_amount",
                    "discovered_po": po_result.purchase_order.po_number,
                    "vendor_id": invoice.vendor_id,
                    "invoice_total": str(invoice.total_amount),
                    "po_total": str(po_result.purchase_order.total_amount),
                },
            )
        po_for_resolver = po_result.purchase_order if po_result.found else None

        # ---------------------------------------------------------------
        # 2. Mode Resolution
        # ---------------------------------------------------------------
        _lf_mode = start_span_safe(lf_trace, "mode_resolution", metadata={
            "invoice_id": invoice.pk,
            "po_found": po_result.found,
        })
        mode_resolution = self.mode_resolver.resolve(invoice, po_for_resolver)
        _mode_meta = {
            "resolved_mode": mode_resolution.mode,
            "policy_source": mode_resolution.policy_code or "",
            "resolution_method": mode_resolution.resolution_method,
            "fallback_used": mode_resolution.resolution_method == "default",
            "grn_required": mode_resolution.grn_required,
        }
        end_span_safe(_lf_mode, output=_mode_meta)

        # Audit: mode resolved
        from apps.auditlog.services import AuditService
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=invoice.pk,
            event_type=AuditEventType.RECONCILIATION_MODE_RESOLVED,
            description=(
                f"Mode resolved to {mode_resolution.mode} "
                f"via {mode_resolution.resolution_method}: {mode_resolution.reason}"
            ),
            metadata={
                "mode": mode_resolution.mode,
                "policy_code": mode_resolution.policy_code,
                "resolution_method": mode_resolution.resolution_method,
                "grn_required": mode_resolution.grn_required,
            },
        )

        # ---------------------------------------------------------------
        # 3. Match Execution (router -> header + line + optional GRN)
        # ---------------------------------------------------------------
        reconciliation_mode = mode_resolution.mode
        _lf_match = start_span_safe(lf_trace, "match_execution", metadata={
            "invoice_id": invoice.pk,
            "match_type": reconciliation_mode,
        })
        routed = self.router.execute(invoice, po_result, mode_resolution)

        # Collect rich match metadata for the span
        _header = routed.header_result
        _lines = routed.line_result
        _grn = routed.grn_result
        _header_ratio = 0.0
        if _header:
            _checks = [_header.vendor_match, _header.currency_match, _header.po_total_match]
            _header_ratio = sum(1 for c in _checks if c) / max(len(_checks), 1)
        _line_ratio = 0.0
        _tolerance_passed = True
        if _lines:
            _matched_count = len([p for p in (_lines.line_pairs or []) if p.matched])
            _total_count = max(_lines.total_invoice_lines or 1, 1)
            _line_ratio = _matched_count / _total_count
            _tolerance_passed = all(
                getattr(p, "within_tolerance", True) for p in (_lines.line_pairs or []) if p.matched
            )
        _grn_ratio = 0.0
        if _grn and hasattr(_grn, "fully_received"):
            _grn_ratio = 1.0 if _grn.fully_received else 0.5

        _amount_delta = 0.0
        if _header and hasattr(_header, "total_difference"):
            _amount_delta = float(_header.total_difference or 0)

        _match_meta = {
            "match_type": reconciliation_mode,
            "header_match_ratio": round(_header_ratio, 3),
            "line_match_ratio": round(_line_ratio, 3),
            "tolerance_passed": _tolerance_passed,
            "grn_match_ratio": round(_grn_ratio, 3) if reconciliation_mode == "THREE_WAY" else None,
            "grn_checked": routed.grn_checked,
            "amount_delta": round(_amount_delta, 2),
        }
        end_span_safe(_lf_match, output=_match_meta)
        score_observation_safe(_lf_match, "recon_header_match_ratio", _header_ratio)
        score_observation_safe(_lf_match, "recon_line_match_ratio", _line_ratio)
        score_observation_safe(_lf_match, "recon_tolerance_passed", 1.0 if _tolerance_passed else 0.0)
        if reconciliation_mode == "THREE_WAY":
            score_observation_safe(_lf_match, "recon_grn_match_ratio", _grn_ratio)

        # ---------------------------------------------------------------
        # 3b. GRN Lookup span (if 3-way, emit separate observation)
        # ---------------------------------------------------------------
        if reconciliation_mode == "THREE_WAY":
            _lf_grn = start_span_safe(lf_trace, "grn_lookup", metadata={
                "invoice_id": invoice.pk,
                "po_number": _po_meta.get("po_number", ""),
            })
            _grn_found = bool(_grn and getattr(_grn, "grn_available", False))
            _grn_meta = {
                "grn_found": _grn_found,
                "grn_count": getattr(_grn, "grn_count", 0) if _grn else 0,
                "lookup_source": getattr(_grn, "erp_source_type", "") if _grn else "",
                "is_stale": getattr(_grn, "is_stale", False) if _grn else False,
            }
            end_span_safe(_lf_grn, output=_grn_meta)
            score_observation_safe(_lf_grn, "recon_grn_lookup_success", 1.0 if _grn_found else 0.0)
            score_observation_safe(
                _lf_grn, "recon_grn_lookup_fresh",
                0.0 if (_grn and getattr(_grn, "is_stale", False)) else 1.0,
            )
            score_observation_safe(
                _lf_grn, "recon_grn_lookup_authoritative",
                1.0 if (_grn and getattr(_grn, "erp_source_type", "") in ("MIRROR_DB", "API")) else 0.0,
            )

        # ---------------------------------------------------------------
        # 4. Classification
        # ---------------------------------------------------------------
        _lf_class = start_span_safe(lf_trace, "classification", metadata={
            "invoice_id": invoice.pk,
            "reconciliation_mode": reconciliation_mode,
        })
        match_status = self.classifier.classify(
            po_result=routed.po_result,
            header_result=routed.header_result,
            line_result=routed.line_result,
            grn_result=routed.grn_result,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
            reconciliation_mode=reconciliation_mode,
            invoice=invoice,
        )
        _requires_review = match_status == MatchStatus.REQUIRES_REVIEW
        _is_auto_close = match_status == MatchStatus.MATCHED
        _class_meta = {
            "final_match_status": str(match_status),
            "requires_review": _requires_review,
            "is_auto_close_candidate": _is_auto_close,
        }
        end_span_safe(_lf_class, output=_class_meta)
        score_observation_safe(_lf_class, "recon_classified_requires_review", 1.0 if _requires_review else 0.0)
        score_observation_safe(_lf_class, "recon_classified_auto_close_candidate", 1.0 if _is_auto_close else 0.0)

        # ---------------------------------------------------------------
        # 5. Result Persistence
        # ---------------------------------------------------------------
        _lf_persist = start_span_safe(lf_trace, "result_persist", metadata={
            "invoice_id": invoice.pk,
            "match_status": str(match_status),
        })
        result = self.result_service.save(
            run=run,
            invoice=invoice,
            match_status=match_status,
            po_result=routed.po_result,
            header_result=routed.header_result,
            line_result=routed.line_result,
            grn_result=routed.grn_result,
            exceptions=[],  # Build separately below to get result_line references
            reconciliation_mode=reconciliation_mode,
            mode_resolution=mode_resolution,
        )

        # Build result_line map from saved result
        result_line_map = {
            rl.invoice_line_id: rl
            for rl in result.line_results.all()
            if rl.invoice_line_id
        }
        end_span_safe(_lf_persist, output={
            "result_id": result.pk,
            "reconciliation_result_saved": True,
        })

        # ---------------------------------------------------------------
        # 6. Exception Building
        # ---------------------------------------------------------------
        _lf_exc = start_span_safe(lf_trace, "exception_build", metadata={
            "invoice_id": invoice.pk,
            "result_id": result.pk,
        })
        exceptions = self.exception_builder.build(
            result=result,
            po_result=routed.po_result,
            header_result=routed.header_result,
            line_result=routed.line_result,
            grn_result=routed.grn_result,
            result_line_map=result_line_map,
            extraction_confidence=invoice.extraction_confidence,
            confidence_threshold=self.config.extraction_confidence_threshold,
            reconciliation_mode=reconciliation_mode,
        )
        if exceptions:
            from apps.reconciliation.models import ReconciliationException
            ReconciliationException.objects.bulk_create(exceptions)

        _blocking = sum(1 for e in exceptions if getattr(e, "severity", "") == "HIGH")
        _warning = sum(1 for e in exceptions if getattr(e, "severity", "") in ("MEDIUM", "LOW"))
        _exc_codes = list(set(getattr(e, "exception_type", "") for e in exceptions))[:15]
        _exc_meta = {
            "exception_count": len(exceptions),
            "blocking_exception_count": _blocking,
            "warning_exception_count": _warning,
            "exception_codes": _exc_codes,
        }
        end_span_safe(_lf_exc, output=_exc_meta)
        score_observation_safe(_lf_exc, "recon_blocking_exception_count", float(_blocking))
        score_observation_safe(_lf_exc, "recon_warning_exception_count", float(_warning))

        # ---------------------------------------------------------------
        # 7. Review Workflow Trigger
        # ---------------------------------------------------------------
        _lf_review = start_span_safe(lf_trace, "review_workflow_trigger", metadata={
            "invoice_id": invoice.pk,
            "match_status": str(match_status),
        })
        _review_created = False
        if match_status == MatchStatus.REQUIRES_REVIEW:
            from apps.reviews.services import ReviewWorkflowService
            ReviewWorkflowService.create_assignment(
                result=result,
                priority=3 if exceptions else 5,
                notes=f"Auto-created: {len(exceptions)} exception(s) found during reconciliation.",
            )
            _review_created = True
        end_span_safe(_lf_review, output={"review_assignment_created": _review_created})
        score_observation_safe(_lf_review, "recon_review_assignment_created", 1.0 if _review_created else 0.0)

        # ---------------------------------------------------------------
        # 8. Invoice status transition
        # ---------------------------------------------------------------
        self._transition_invoice(invoice, match_status)

        # ---------------------------------------------------------------
        # Trace-level scores (per invoice within the run)
        # ---------------------------------------------------------------
        _score_value = {
            MatchStatus.MATCHED: 1.0,
            MatchStatus.PARTIAL_MATCH: 0.5,
            MatchStatus.UNMATCHED: 0.0,
            MatchStatus.REQUIRES_REVIEW: 0.3,
        }.get(match_status, 0.0)
        score_trace_safe(
            _tid, "reconciliation_match", _score_value,
            comment=f"mode={reconciliation_mode} match_status={match_status} invoice={invoice.pk}",
        )
        score_trace_safe(_tid, "recon_final_status_matched", 1.0 if match_status == MatchStatus.MATCHED else 0.0)
        score_trace_safe(_tid, "recon_final_status_partial_match", 1.0 if match_status == MatchStatus.PARTIAL_MATCH else 0.0)
        score_trace_safe(_tid, "recon_final_status_requires_review", 1.0 if match_status == MatchStatus.REQUIRES_REVIEW else 0.0)
        score_trace_safe(_tid, "recon_final_status_unmatched", 1.0 if match_status == MatchStatus.UNMATCHED else 0.0)
        score_trace_safe(_tid, "recon_po_found", 1.0 if po_result.found else 0.0)
        _grn_found_flag = bool(
            reconciliation_mode == "THREE_WAY" and _grn and getattr(_grn, "grn_available", False)
        )
        score_trace_safe(_tid, "recon_grn_found", 1.0 if _grn_found_flag else 0.0)
        score_trace_safe(_tid, "recon_auto_close_eligible", 1.0 if _is_auto_close else 0.0)
        score_trace_safe(_tid, "recon_routed_to_review", 1.0 if _review_created else 0.0)
        score_trace_safe(_tid, "recon_exception_count_final", float(len(exceptions)))

        # Update root trace metadata with eval-ready summary
        _eval_meta = {
            "invoice_id": invoice.pk,
            "reconciliation_result_id": result.pk,
            "reconciliation_run_id": run.pk,
            "po_number": _po_meta.get("po_number", ""),
            "reconciliation_mode": reconciliation_mode,
            "po_found": po_result.found,
            "grn_found": _grn_found_flag,
            "final_match_status": str(match_status),
            "exception_count": len(exceptions),
            "requires_review": _requires_review,
            "auto_close_eligible": _is_auto_close,
            "routed_to_agents": not _is_auto_close and match_status != MatchStatus.MATCHED,
            "routed_to_review": _review_created,
            "vendor_id": invoice.vendor_id,
            "vendor_name": (
                invoice.vendor.name[:60] if invoice.vendor else (invoice.raw_vendor_name or "")[:60]
            ),
            "source": "deterministic",
        }
        update_trace_safe(lf_trace, metadata=_eval_meta, is_root=True)

        return match_status

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _transition_invoice(invoice: Invoice, status: MatchStatus) -> None:
        invoice.status = InvoiceStatus.RECONCILED
        invoice.save(update_fields=["status", "updated_at"])

    @staticmethod
    def _default_config() -> ReconciliationConfig:
        """Get or create a default ReconciliationConfig."""
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config:
            return config
        return ReconciliationConfig.objects.create(
            name="Default",
            quantity_tolerance_pct=2.0,
            price_tolerance_pct=1.0,
            amount_tolerance_pct=1.0,
            is_default=True,
        )
