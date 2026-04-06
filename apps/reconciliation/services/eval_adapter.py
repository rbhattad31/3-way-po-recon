"""ReconciliationEvalAdapter -- bridges reconciliation data into core_eval.

This adapter is the ONLY place that maps reconciliation-domain objects
(ReconciliationResult, ReconciliationRun, ReviewAssignment, ReviewDecision,
ManualReviewAction) into the generic core_eval persistence layer.

All methods are fail-silent: errors are logged but never propagate.
The adapter is safe to call on every reconciliation result and every
review event -- it uses upsert / idempotent writes internally.

Predicted values come from deterministic reconciliation pipeline outputs.
Actual values come from persisted review/workflow business state.
If actual is not yet knowable, it is left blank/null -- never forced to false.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_MODULE = "reconciliation"
ENTITY_TYPE = "ReconciliationResult"

# Learning signal types (mirror evaluation_constants for consistency)
SIG_WRONG_MATCH_STATUS = "wrong_match_status_prediction"
SIG_WRONG_AUTO_CLOSE = "wrong_auto_close_prediction"
SIG_WRONG_REVIEW_ROUTE = "wrong_review_route_prediction"
SIG_MISSING_PO = "missing_po_prediction_issue"
SIG_MISSING_GRN = "missing_grn_prediction_issue"
SIG_REVIEW_OVERRIDE = "review_override"
SIG_REPROCESS = "reprocess_signal"
SIG_TOLERANCE_REVIEW = "tolerance_or_rule_review_candidate"


def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_bool(v: Any) -> Optional[bool]:
    """Return bool or None if value is truly unknown."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return bool(v)


def _bool_to_score(v: Optional[bool]) -> Optional[float]:
    """Convert Optional[bool] to 1.0/0.0/None."""
    if v is None:
        return None
    return 1.0 if v else 0.0


class ReconciliationEvalAdapter:
    """Maps reconciliation pipeline outputs and review outcomes into core_eval.

    Public API:
        sync_for_result(reconciliation_result, ...)
        sync_for_review_assignment(review_assignment, ...)
        sync_for_review_outcome(review_assignment, ...)
        sync_for_reprocess(reconciliation_result, ...)
        sync_all(reconciliation_result=None, review_assignment=None, ...)
    """

    # ------------------------------------------------------------------
    # Entry point: after reconciliation result persistence
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_result(
        cls,
        reconciliation_result,
        *,
        tenant_id: str = "",
        run_key: str = "",
        trace_id: str = "",
        span_id: str = "",
    ) -> None:
        """Persist EvalRun + predicted metrics + initial learning signals.

        Called once after ReconciliationResult is saved. Safe for reruns.
        """
        try:
            cls._sync_for_result_inner(
                reconciliation_result,
                tenant_id=tenant_id,
                run_key=run_key,
                trace_id=trace_id,
                span_id=span_id,
            )
        except Exception:
            logger.exception(
                "ReconciliationEvalAdapter.sync_for_result failed "
                "for result=%s (non-fatal)",
                getattr(reconciliation_result, "pk", "?"),
            )

    # ------------------------------------------------------------------
    # Entry point: after ReviewAssignment creation
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_review_assignment(
        cls,
        review_assignment,
        *,
        tenant_id: str = "",
    ) -> None:
        """Update EvalRun with review assignment context."""
        try:
            cls._sync_for_review_assignment_inner(
                review_assignment, tenant_id=tenant_id,
            )
        except Exception:
            logger.exception(
                "ReconciliationEvalAdapter.sync_for_review_assignment failed "
                "for assignment=%s (non-fatal)",
                getattr(review_assignment, "pk", "?"),
            )

    # ------------------------------------------------------------------
    # Entry point: after review finalization (approve/reject/reprocess)
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_review_outcome(
        cls,
        review_assignment,
        *,
        tenant_id: str = "",
    ) -> None:
        """Update EvalRun with actual outcomes from review decision."""
        try:
            cls._sync_for_review_outcome_inner(
                review_assignment, tenant_id=tenant_id,
            )
        except Exception:
            logger.exception(
                "ReconciliationEvalAdapter.sync_for_review_outcome failed "
                "for assignment=%s (non-fatal)",
                getattr(review_assignment, "pk", "?"),
            )

    # ------------------------------------------------------------------
    # Entry point: after reprocess
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_reprocess(
        cls,
        reconciliation_result,
        *,
        tenant_id: str = "",
    ) -> None:
        """Record reprocess signal against eval run."""
        try:
            cls._sync_for_reprocess_inner(
                reconciliation_result, tenant_id=tenant_id,
            )
        except Exception:
            logger.exception(
                "ReconciliationEvalAdapter.sync_for_reprocess failed "
                "for result=%s (non-fatal)",
                getattr(reconciliation_result, "pk", "?"),
            )

    # ------------------------------------------------------------------
    # Convenience: call all relevant syncs
    # ------------------------------------------------------------------
    @classmethod
    def sync_all(
        cls,
        reconciliation_result=None,
        review_assignment=None,
        *,
        tenant_id: str = "",
        run_key: str = "",
        trace_id: str = "",
        span_id: str = "",
    ) -> None:
        """Run all applicable syncs for a given result and/or review."""
        if reconciliation_result is not None:
            cls.sync_for_result(
                reconciliation_result,
                tenant_id=tenant_id,
                run_key=run_key,
                trace_id=trace_id,
                span_id=span_id,
            )
        if review_assignment is not None:
            cls.sync_for_review_assignment(
                review_assignment, tenant_id=tenant_id,
            )
            # If review already has a decision, also sync outcome
            try:
                from apps.reviews.models import ReviewDecision
                if ReviewDecision.objects.filter(assignment=review_assignment).exists():
                    cls.sync_for_review_outcome(
                        review_assignment, tenant_id=tenant_id,
                    )
            except Exception:
                pass

    # ======================================================================
    # INTERNAL: sync_for_result
    # ======================================================================
    @classmethod
    def _sync_for_result_inner(
        cls,
        result,
        *,
        tenant_id: str = "",
        run_key: str = "",
        trace_id: str = "",
        span_id: str = "",
    ) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        from apps.core_eval.models import EvalRun

        now = timezone.now()
        result_pk = str(result.pk)

        # Resolve context
        ctx = cls._resolve_context(result)
        predicted = cls._resolve_predicted_outcomes(result, ctx)

        # Resolve trace_id
        _trace_id = trace_id
        if not _trace_id:
            _run = getattr(result, "run", None)
            if _run:
                _trace_id = getattr(_run, "langfuse_trace_id", "") or ""
        if not _trace_id:
            _trace_id = ""

        # Resolve run_key
        _run_key = run_key or f"reconciliation_result::{result_pk}"

        # Build config_json
        config_json = cls._build_config_json(result, ctx)

        # Build input_snapshot_json
        input_snapshot = cls._build_input_snapshot(result, ctx, predicted)

        # Build result_json (predicted only at this stage)
        result_json = {
            "predicted": predicted,
            "actual": {},  # populated later on review outcome
            "reconciliation_run_id": ctx.get("run_pk"),
            "exception_count": ctx.get("exception_count", 0),
        }

        # Derive timing from run
        _started = None
        _completed = None
        _duration = None
        _run_obj = getattr(result, "run", None)
        if _run_obj:
            _started = getattr(_run_obj, "started_at", None)
            _completed = getattr(_run_obj, "completed_at", None)
            if _started and _completed:
                try:
                    _duration = int((_completed - _started).total_seconds() * 1000)
                except Exception:
                    _duration = None

        # Determine status
        _status = EvalRun.Status.COMPLETED

        # Upsert EvalRun
        eval_run, _created = EvalRunService.create_or_update(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE,
            entity_id=result_pk,
            run_key=_run_key,
            tenant_id=tenant_id,
            status=_status,
            trace_id=_trace_id,
            config_json=config_json,
            input_snapshot_json=input_snapshot,
        )

        # Set timing if not already set
        if not eval_run.started_at and _started:
            eval_run.started_at = _started
        if not eval_run.completed_at:
            eval_run.completed_at = _completed or now
        if _duration and not eval_run.duration_ms:
            eval_run.duration_ms = _duration

        eval_run.result_json = result_json
        eval_run.save(update_fields=[
            "started_at", "completed_at", "duration_ms",
            "result_json", "updated_at",
        ])

        # Store predicted metrics
        cls._store_predicted_metrics(eval_run, predicted, tenant_id=tenant_id)

        # Store runtime mirror metrics
        cls._store_runtime_metrics(eval_run, result, ctx, tenant_id=tenant_id)

    # ======================================================================
    # INTERNAL: sync_for_review_assignment
    # ======================================================================
    @classmethod
    def _sync_for_review_assignment_inner(
        cls,
        assignment,
        *,
        tenant_id: str = "",
    ) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService

        result = getattr(assignment, "reconciliation_result", None)
        if result is None:
            return

        result_pk = str(result.pk)
        _run_key = f"reconciliation_result::{result_pk}"

        eval_run = EvalRunService.get_latest(
            APP_MODULE, ENTITY_TYPE, result_pk, run_key=_run_key,
        )
        if eval_run is None:
            return

        # Update actual_review_created metric
        from apps.core.evaluation_constants import RECON_ACTUAL_REVIEW_CREATED
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_ACTUAL_REVIEW_CREATED,
            metric_value=1.0,
            tenant_id=tenant_id,
            dimension_json={"scope": "business_outcome"},
        )

        # Update result_json with assignment info
        rj = eval_run.result_json or {}
        actual = rj.get("actual", {})
        actual["review_created"] = True
        actual["review_assignment_id"] = assignment.pk
        rj["actual"] = actual
        eval_run.result_json = rj
        eval_run.save(update_fields=["result_json", "updated_at"])

    # ======================================================================
    # INTERNAL: sync_for_review_outcome
    # ======================================================================
    @classmethod
    def _sync_for_review_outcome_inner(
        cls,
        assignment,
        *,
        tenant_id: str = "",
    ) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        from apps.core.evaluation_constants import (
            RECON_ACTUAL_MATCH_STATUS,
            RECON_MATCH_STATUS_CORRECT,
            RECON_REVIEW_OUTCOME,
            RECON_CORRECTED_BY_REVIEWER,
            RECON_ACTUAL_AUTO_CLOSE,
            RECON_AUTO_CLOSE_CORRECT,
            RECON_REVIEW_ROUTE_CORRECT,
            RECON_ACTUAL_FINAL_ROUTE,
            RECON_REPROCESSED,
        )

        result = getattr(assignment, "reconciliation_result", None)
        if result is None:
            return

        result_pk = str(result.pk)
        _run_key = f"reconciliation_result::{result_pk}"

        eval_run = EvalRunService.get_latest(
            APP_MODULE, ENTITY_TYPE, result_pk, run_key=_run_key,
        )
        if eval_run is None:
            return

        # Derive review outcome
        review_outcome = cls._derive_review_outcome(assignment)
        if review_outcome is None:
            return

        decision_status = review_outcome["decision"]
        corrections_count = review_outcome["corrections_count"]
        is_reprocessed = decision_status == "REPROCESSED"

        # Determine actual match status after review
        # result.match_status has already been updated by _finalise()
        result.refresh_from_db()
        actual_match_status = _str(result.match_status)

        # Get predicted values from result_json
        rj = eval_run.result_json or {}
        predicted = rj.get("predicted", {})
        predicted_match = predicted.get("match_status", "")
        predicted_requires_review = predicted.get("requires_review")
        predicted_auto_close = predicted.get("auto_close_eligible")

        # Derive actual auto_close: if review resulted in APPROVED with no
        # corrections, and predicted was auto_close_eligible, the actual is
        # effectively auto-close-correct
        actual_auto_close = (
            decision_status == "APPROVED"
            and actual_match_status == "MATCHED"
            and corrections_count == 0
        )

        # Derive actual final route
        if is_reprocessed:
            actual_final_route = "reprocess"
        elif decision_status == "APPROVED":
            actual_final_route = "review"
        elif decision_status == "REJECTED":
            actual_final_route = "review"
        else:
            actual_final_route = "unresolved"

        # Store actual metrics
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_ACTUAL_MATCH_STATUS,
            string_value=actual_match_status,
            tenant_id=tenant_id,
            dimension_json={"scope": "business_outcome"},
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_REVIEW_OUTCOME,
            string_value=decision_status,
            tenant_id=tenant_id,
            dimension_json={"scope": "human_feedback"},
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_CORRECTED_BY_REVIEWER,
            metric_value=1.0 if corrections_count > 0 else 0.0,
            tenant_id=tenant_id,
            dimension_json={"scope": "human_feedback"},
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_REPROCESSED,
            metric_value=1.0 if is_reprocessed else 0.0,
            tenant_id=tenant_id,
            dimension_json={"scope": "business_outcome"},
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_ACTUAL_AUTO_CLOSE,
            metric_value=_bool_to_score(actual_auto_close),
            tenant_id=tenant_id,
            dimension_json={"scope": "business_outcome"},
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_ACTUAL_FINAL_ROUTE,
            string_value=actual_final_route,
            tenant_id=tenant_id,
            dimension_json={"scope": "business_outcome"},
        )

        # Correctness metrics (only when both predicted and actual are known)
        if predicted_match and actual_match_status:
            _match_correct = predicted_match == actual_match_status
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name=RECON_MATCH_STATUS_CORRECT,
                metric_value=1.0 if _match_correct else 0.0,
                tenant_id=tenant_id,
                dimension_json={"scope": "business_outcome"},
            )

        if predicted_requires_review is not None:
            _review_correct = bool(predicted_requires_review) == True  # noqa: E712
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name=RECON_REVIEW_ROUTE_CORRECT,
                metric_value=1.0 if _review_correct else 0.0,
                tenant_id=tenant_id,
                dimension_json={"scope": "business_outcome"},
            )

        if predicted_auto_close is not None:
            _auto_correct = bool(predicted_auto_close) == actual_auto_close
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name=RECON_AUTO_CLOSE_CORRECT,
                metric_value=1.0 if _auto_correct else 0.0,
                tenant_id=tenant_id,
                dimension_json={"scope": "business_outcome"},
            )

        # Update result_json with actual
        actual_section = rj.get("actual", {})
        actual_section.update({
            "match_status": actual_match_status,
            "review_outcome": decision_status,
            "corrections_count": corrections_count,
            "auto_closed": actual_auto_close,
            "final_route": actual_final_route,
            "reprocessed": is_reprocessed,
        })
        rj["actual"] = actual_section
        eval_run.result_json = rj
        eval_run.save(update_fields=["result_json", "updated_at"])

        # Generate learning signals
        cls._emit_review_learning_signals(
            eval_run=eval_run,
            result=result,
            predicted=predicted,
            actual_match_status=actual_match_status,
            actual_auto_close=actual_auto_close,
            actual_final_route=actual_final_route,
            decision_status=decision_status,
            corrections_count=corrections_count,
            is_reprocessed=is_reprocessed,
            tenant_id=tenant_id,
        )

        # Store structured field outcomes for reviewer corrections
        cls._store_review_field_outcomes(
            eval_run=eval_run,
            assignment=assignment,
            tenant_id=tenant_id,
        )

    # ======================================================================
    # INTERNAL: sync_for_reprocess
    # ======================================================================
    @classmethod
    def _sync_for_reprocess_inner(
        cls,
        result,
        *,
        tenant_id: str = "",
    ) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        from apps.core.evaluation_constants import RECON_REPROCESSED

        result_pk = str(result.pk)
        _run_key = f"reconciliation_result::{result_pk}"

        eval_run = EvalRunService.get_latest(
            APP_MODULE, ENTITY_TYPE, result_pk, run_key=_run_key,
        )
        if eval_run is None:
            return

        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_REPROCESSED,
            metric_value=1.0,
            tenant_id=tenant_id,
            dimension_json={"scope": "business_outcome"},
        )

        ctx = cls._resolve_context(result)

        LearningSignalService.record(
            app_module=APP_MODULE,
            signal_type=SIG_REPROCESS,
            entity_type=ENTITY_TYPE,
            entity_id=result_pk,
            aggregation_key=f"reprocess::{ctx.get('reconciliation_mode', 'unknown')}",
            confidence=0.8,
            eval_run=eval_run,
            tenant_id=tenant_id,
            payload_json={
                "reconciliation_mode": ctx.get("reconciliation_mode"),
                "match_status": _str(result.match_status),
                "invoice_id": ctx.get("invoice_id"),
                "exception_count": ctx.get("exception_count", 0),
            },
        )

    # ======================================================================
    # Helpers: context resolution
    # ======================================================================
    @classmethod
    def _resolve_context(cls, result) -> Dict[str, Any]:
        """Build a context dict from a ReconciliationResult and its relations."""
        ctx: Dict[str, Any] = {}

        ctx["result_pk"] = result.pk
        ctx["match_status"] = _str(result.match_status)
        ctx["requires_review"] = getattr(result, "requires_review", False)
        ctx["reconciliation_mode"] = _str(result.reconciliation_mode)

        # Invoice context
        invoice = getattr(result, "invoice", None)
        if invoice:
            ctx["invoice_id"] = invoice.pk
            ctx["invoice_number"] = getattr(invoice, "invoice_number", "")
            ctx["vendor_id"] = getattr(invoice, "vendor_id", None)
            ctx["vendor_name"] = ""
            try:
                if invoice.vendor:
                    ctx["vendor_name"] = _str(invoice.vendor.name)[:60]
                elif getattr(invoice, "raw_vendor_name", ""):
                    ctx["vendor_name"] = _str(invoice.raw_vendor_name)[:60]
            except Exception:
                pass
            ctx["extraction_confidence"] = _safe_float(
                getattr(invoice, "extraction_confidence", None)
            )
            ctx["total_amount"] = _str(getattr(invoice, "total_amount", ""))
        else:
            ctx["invoice_id"] = None

        # PO context
        po = getattr(result, "purchase_order", None)
        ctx["po_id"] = getattr(po, "pk", None) if po else None
        ctx["po_found"] = po is not None

        # GRN context
        ctx["grn_available"] = getattr(result, "grn_available", False)
        ctx["grn_fully_received"] = getattr(result, "grn_fully_received", None)

        # Amount deltas
        ctx["total_amount_difference"] = _str(
            getattr(result, "total_amount_difference", "")
        )
        ctx["total_amount_difference_pct"] = _str(
            getattr(result, "total_amount_difference_pct", "")
        )

        # Run context
        _run = getattr(result, "run", None)
        ctx["run_pk"] = getattr(_run, "pk", None) if _run else None

        # Exception count (query-based, fail-safe)
        try:
            ctx["exception_count"] = result.exceptions.count()
        except Exception:
            ctx["exception_count"] = 0

        # Policy
        ctx["policy_applied"] = _str(getattr(result, "policy_applied", ""))
        ctx["mode_resolution_reason"] = _str(
            getattr(result, "mode_resolution_reason", "")
        )

        # ERP provenance
        ctx["po_erp_source_type"] = _str(
            getattr(result, "po_erp_source_type", "")
        )
        ctx["data_is_stale"] = getattr(result, "data_is_stale", False)

        # Deterministic confidence
        ctx["deterministic_confidence"] = _safe_float(
            getattr(result, "deterministic_confidence", None)
        )

        return ctx

    @classmethod
    def _resolve_predicted_outcomes(
        cls, result, ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract predicted outcomes from the reconciliation result."""
        match_status = ctx.get("match_status", "")
        is_auto_close = match_status == "MATCHED"
        is_routed_to_review = ctx.get("requires_review", False)
        is_routed_to_agents = (
            not is_auto_close
            and match_status != "MATCHED"
            and not is_routed_to_review
        )

        return {
            "match_status": match_status,
            "requires_review": is_routed_to_review,
            "auto_close_eligible": is_auto_close,
            "po_found": ctx.get("po_found", False),
            "grn_found": ctx.get("grn_available", False),
            "exception_count": ctx.get("exception_count", 0),
            "reconciliation_mode": ctx.get("reconciliation_mode", ""),
            "routed_to_review": is_routed_to_review,
            "routed_to_agents": is_routed_to_agents,
        }

    # ======================================================================
    # Helpers: build payloads
    # ======================================================================
    @classmethod
    def _build_config_json(cls, result, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Build compact config snapshot for the EvalRun."""
        config: Dict[str, Any] = {
            "reconciliation_mode": ctx.get("reconciliation_mode", ""),
            "policy_applied": ctx.get("policy_applied", ""),
        }
        _run = getattr(result, "run", None)
        if _run:
            _cfg = getattr(_run, "config", None)
            if _cfg:
                config["config_name"] = _str(getattr(_cfg, "name", ""))
                config["qty_tolerance_pct"] = getattr(_cfg, "quantity_tolerance_pct", None)
                config["price_tolerance_pct"] = getattr(_cfg, "price_tolerance_pct", None)
                config["amount_tolerance_pct"] = getattr(_cfg, "amount_tolerance_pct", None)
        return config

    @classmethod
    def _build_input_snapshot(
        cls, result, ctx: Dict[str, Any], predicted: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build compact input snapshot for the EvalRun."""
        return {
            "invoice_id": ctx.get("invoice_id"),
            "po_id": ctx.get("po_id"),
            "vendor_id": ctx.get("vendor_id"),
            "vendor_name": ctx.get("vendor_name", ""),
            "total_amount": ctx.get("total_amount", ""),
            "total_amount_difference": ctx.get("total_amount_difference", ""),
            "total_amount_difference_pct": ctx.get("total_amount_difference_pct", ""),
            "grn_available": ctx.get("grn_available", False),
            "grn_fully_received": ctx.get("grn_fully_received"),
            "requires_review": ctx.get("requires_review", False),
            "extraction_confidence": ctx.get("extraction_confidence"),
            "reconciliation_mode": ctx.get("reconciliation_mode", ""),
            "exception_count": ctx.get("exception_count", 0),
            "po_erp_source_type": ctx.get("po_erp_source_type", ""),
            "data_is_stale": ctx.get("data_is_stale", False),
        }

    # ======================================================================
    # Helpers: store metrics
    # ======================================================================
    @classmethod
    def _store_predicted_metrics(
        cls, eval_run, predicted: Dict[str, Any], *, tenant_id: str = "",
    ) -> None:
        """Store predicted-outcome metrics as EvalMetric records."""
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core.evaluation_constants import (
            RECON_PREDICTED_MATCH_STATUS,
            RECON_PREDICTED_REQUIRES_REVIEW,
            RECON_PREDICTED_AUTO_CLOSE,
            RECON_PREDICTED_PO_FOUND,
            RECON_PREDICTED_GRN_FOUND,
        )

        _dim = {"scope": "business_outcome"}

        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_PREDICTED_MATCH_STATUS,
            string_value=predicted.get("match_status", ""),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_PREDICTED_REQUIRES_REVIEW,
            metric_value=_bool_to_score(predicted.get("requires_review")),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_PREDICTED_AUTO_CLOSE,
            metric_value=_bool_to_score(predicted.get("auto_close_eligible")),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_PREDICTED_PO_FOUND,
            metric_value=_bool_to_score(predicted.get("po_found")),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_PREDICTED_GRN_FOUND,
            metric_value=_bool_to_score(predicted.get("grn_found")),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )

    @classmethod
    def _store_runtime_metrics(
        cls, eval_run, result, ctx: Dict[str, Any], *, tenant_id: str = "",
    ) -> None:
        """Mirror key runtime reconciliation scores as EvalMetric records."""
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core.evaluation_constants import (
            RECON_RECONCILIATION_MATCH,
            RECON_PO_FOUND,
            RECON_GRN_FOUND,
            RECON_AUTO_CLOSE_ELIGIBLE,
            RECON_EXCEPTION_COUNT_FINAL,
        )

        _dim = {"scope": "trace"}
        match_status = ctx.get("match_status", "")

        _score_map = {
            "MATCHED": 1.0,
            "PARTIAL_MATCH": 0.5,
            "REQUIRES_REVIEW": 0.3,
            "UNMATCHED": 0.0,
        }
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_RECONCILIATION_MATCH,
            metric_value=_score_map.get(match_status, 0.0),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_PO_FOUND,
            metric_value=_bool_to_score(ctx.get("po_found")),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_GRN_FOUND,
            metric_value=_bool_to_score(ctx.get("grn_available")),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_AUTO_CLOSE_ELIGIBLE,
            metric_value=_bool_to_score(match_status == "MATCHED"),
            tenant_id=tenant_id,
            dimension_json=_dim,
        )
        EvalMetricService.upsert(
            eval_run=eval_run,
            metric_name=RECON_EXCEPTION_COUNT_FINAL,
            metric_value=float(ctx.get("exception_count", 0)),
            unit="count",
            tenant_id=tenant_id,
            dimension_json=_dim,
        )

    # ======================================================================
    # Helpers: review outcome
    # ======================================================================
    @classmethod
    def _derive_review_outcome(cls, assignment) -> Optional[Dict[str, Any]]:
        """Extract review decision and correction count from assignment."""
        try:
            from apps.reviews.models import ReviewDecision, ManualReviewAction
            from apps.core.enums import ReviewActionType
        except ImportError:
            return None

        try:
            decision = ReviewDecision.objects.get(assignment=assignment)
        except ReviewDecision.DoesNotExist:
            return None

        corrections_count = ManualReviewAction.objects.filter(
            assignment=assignment,
            action_type=ReviewActionType.CORRECT_FIELD,
        ).count()

        return {
            "decision": _str(decision.decision),
            "decided_by": getattr(decision.decided_by, "pk", None),
            "corrections_count": corrections_count,
            "reason": _str(decision.reason)[:200],
        }

    # ======================================================================
    # Helpers: learning signals (post-review)
    # ======================================================================
    @classmethod
    def _emit_review_learning_signals(
        cls,
        *,
        eval_run,
        result,
        predicted: Dict[str, Any],
        actual_match_status: str,
        actual_auto_close: bool,
        actual_final_route: str,
        decision_status: str,
        corrections_count: int,
        is_reprocessed: bool,
        tenant_id: str = "",
    ) -> None:
        """Generate deterministic learning signals based on predicted/actual diff."""
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        result_pk = str(result.pk)
        recon_mode = predicted.get("reconciliation_mode", "")

        # Common payload base
        _base_payload = {
            "reconciliation_mode": recon_mode,
            "invoice_id": getattr(result, "invoice_id", None),
            "review_outcome": decision_status,
            "exception_count": predicted.get("exception_count", 0),
        }

        # 1. wrong_match_status_prediction
        predicted_match = predicted.get("match_status", "")
        if predicted_match and actual_match_status and predicted_match != actual_match_status:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_WRONG_MATCH_STATUS,
                entity_type=ENTITY_TYPE,
                entity_id=result_pk,
                aggregation_key=f"wrong_match_status::{predicted_match}::{actual_match_status}",
                confidence=0.9,
                eval_run=eval_run,
                tenant_id=tenant_id,
                payload_json={
                    **_base_payload,
                    "predicted": predicted_match,
                    "actual": actual_match_status,
                },
            )

        # 2. wrong_auto_close_prediction
        predicted_auto_close = predicted.get("auto_close_eligible", False)
        if predicted_auto_close != actual_auto_close:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_WRONG_AUTO_CLOSE,
                entity_type=ENTITY_TYPE,
                entity_id=result_pk,
                aggregation_key=f"wrong_auto_close::{recon_mode}",
                confidence=0.85,
                eval_run=eval_run,
                tenant_id=tenant_id,
                payload_json={
                    **_base_payload,
                    "predicted": predicted_auto_close,
                    "actual": actual_auto_close,
                },
            )

        # 3. wrong_review_route_prediction
        predicted_review = predicted.get("requires_review", False)
        actual_review_created = True  # by definition, review exists
        if bool(predicted_review) != actual_review_created:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_WRONG_REVIEW_ROUTE,
                entity_type=ENTITY_TYPE,
                entity_id=result_pk,
                aggregation_key=f"wrong_review_route::{recon_mode}",
                confidence=0.85,
                eval_run=eval_run,
                tenant_id=tenant_id,
                payload_json={
                    **_base_payload,
                    "predicted_requires_review": predicted_review,
                    "actual_review_created": actual_review_created,
                },
            )

        # 4. review_override
        if corrections_count > 0:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_REVIEW_OVERRIDE,
                entity_type=ENTITY_TYPE,
                entity_id=result_pk,
                aggregation_key=f"review_override::{recon_mode}",
                confidence=0.9,
                eval_run=eval_run,
                tenant_id=tenant_id,
                payload_json={
                    **_base_payload,
                    "corrections_count": corrections_count,
                    "predicted_match_status": predicted_match,
                    "actual_match_status": actual_match_status,
                    "review_assignment_id": getattr(
                        result, "review_assignments", None
                    ) and None,
                },
            )

        # 5. reprocess_signal
        if is_reprocessed:
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_REPROCESS,
                entity_type=ENTITY_TYPE,
                entity_id=result_pk,
                aggregation_key=f"reprocess::{recon_mode}",
                confidence=0.8,
                eval_run=eval_run,
                tenant_id=tenant_id,
                payload_json={
                    **_base_payload,
                    "predicted_match_status": predicted_match,
                },
            )

        # 6. tolerance_or_rule_review_candidate
        # Emit when partial match required review AND was overridden
        if (
            predicted_match in ("PARTIAL_MATCH", "REQUIRES_REVIEW")
            and corrections_count > 0
        ):
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_TOLERANCE_REVIEW,
                entity_type=ENTITY_TYPE,
                entity_id=result_pk,
                aggregation_key=f"tolerance_review::{recon_mode}",
                confidence=0.7,
                eval_run=eval_run,
                tenant_id=tenant_id,
                payload_json={
                    **_base_payload,
                    "predicted_match_status": predicted_match,
                    "actual_match_status": actual_match_status,
                    "corrections_count": corrections_count,
                    "auto_close_candidate": predicted.get("auto_close_eligible", False),
                },
            )

    # ======================================================================
    # Helpers: field outcomes from reviewer corrections
    # ======================================================================
    @classmethod
    def _store_review_field_outcomes(
        cls,
        *,
        eval_run,
        assignment,
        tenant_id: str = "",
    ) -> None:
        """Store EvalFieldOutcome for structured reviewer corrections.

        Only creates outcomes when ManualReviewAction records with
        action_type=CORRECT_FIELD and field_name are available.
        """
        try:
            from apps.reviews.models import ManualReviewAction
            from apps.core.enums import ReviewActionType
            from apps.core_eval.services.eval_field_outcome_service import (
                EvalFieldOutcomeService,
            )
            from apps.core_eval.models import EvalFieldOutcome
        except ImportError:
            return

        corrections = ManualReviewAction.objects.filter(
            assignment=assignment,
            action_type=ReviewActionType.CORRECT_FIELD,
        ).exclude(field_name="")

        if not corrections.exists():
            return

        outcomes = []
        for correction in corrections:
            outcomes.append({
                "field_name": _str(correction.field_name),
                "status": EvalFieldOutcome.Status.INCORRECT,
                "predicted_value": _str(correction.old_value),
                "ground_truth_value": _str(correction.new_value),
                "detail_json": {
                    "source": "reviewer_correction",
                    "review_assignment_id": assignment.pk,
                    "reason": _str(getattr(correction, "reason", ""))[:200],
                },
            })

        if outcomes:
            EvalFieldOutcomeService.replace_for_run(
                eval_run=eval_run,
                outcomes=outcomes,
                tenant_id=tenant_id,
            )
