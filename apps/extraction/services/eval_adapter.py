"""ExtractionEvalAdapter -- bridges extraction data into core_eval.

This adapter is the ONLY place that maps extraction-domain objects
(ExtractionResult, ExtractionApproval, ExtractionFieldCorrection,
ExecutionContext) into the generic core_eval persistence layer.

All methods are fail-silent: errors are logged but never propagate.
The adapter is safe to call on every extraction run and every approval --
it uses upsert / idempotent writes internally.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_MODULE = "extraction"
ENTITY_TYPE_RESULT = "ExtractionResult"
ENTITY_TYPE_APPROVAL = "ExtractionApproval"

# Learning signal types
SIG_FIELD_CORRECTION = "field_correction"
SIG_APPROVAL_OUTCOME = "approval_outcome"
SIG_AUTO_APPROVE_OUTCOME = "auto_approve_outcome"
SIG_VALIDATION_FAILURE = "validation_failure"
SIG_REVIEW_OVERRIDE = "review_override"
SIG_PROMPT_REVIEW_CANDIDATE = "prompt_review_candidate"


class ExtractionEvalAdapter:
    """Maps extraction pipeline outputs into core_eval records.

    Public API:
        sync_for_extraction_result(ext_result, invoice, ...)
        sync_for_approval(approval, user, correction_records)
    """

    # ------------------------------------------------------------------
    # Entry point: after extraction persistence (tasks.py step 6+)
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_extraction_result(
        cls,
        ext_result,
        invoice,
        *,
        validation_result=None,
        field_conf_result=None,
        dup_result=None,
        decision_codes: Optional[list] = None,
        extraction_resp=None,
        trace_id: str = "",
    ) -> None:
        """Persist an EvalRun + metrics + field outcomes for one extraction.

        Called once after ExtractionResult is saved.  Safe for reruns --
        uses upsert on EvalRun and metrics; replaces field outcomes.
        """
        try:
            cls._sync_for_extraction_result_inner(
                ext_result,
                invoice,
                validation_result=validation_result,
                field_conf_result=field_conf_result,
                dup_result=dup_result,
                decision_codes=decision_codes,
                extraction_resp=extraction_resp,
                trace_id=trace_id,
            )
        except Exception:
            logger.exception(
                "ExtractionEvalAdapter.sync_for_extraction_result failed "
                "for ext_result=%s (non-fatal)",
                getattr(ext_result, "pk", "?"),
            )

    @classmethod
    def _sync_for_extraction_result_inner(
        cls,
        ext_result,
        invoice,
        *,
        validation_result,
        field_conf_result,
        dup_result,
        decision_codes,
        extraction_resp,
        trace_id,
    ) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.models import EvalRun

        entity_id = str(ext_result.pk)

        # -- Resolve execution context for prompt provenance --
        prompt_hash = ""
        prompt_slug = ""
        ctx = None
        try:
            from apps.extraction.services.execution_context import get_execution_context
            ctx = get_execution_context(ext_result)
            prompt_hash = ctx.prompt_hash or ""
            prompt_slug = ctx.prompt_source or ""
        except Exception:
            pass

        # -- Build input snapshot --
        input_snap = {
            "invoice_id": getattr(invoice, "pk", None),
            "invoice_number": getattr(invoice, "invoice_number", ""),
            "document_upload_id": getattr(ext_result, "document_upload_id", None),
            "extraction_confidence": float(getattr(invoice, "extraction_confidence", 0) or 0),
        }
        if ctx and ctx.source == "governed":
            input_snap["governed"] = True
            input_snap["country_code"] = ctx.country_code
            input_snap["regime_code"] = ctx.regime_code
            input_snap["schema_code"] = ctx.schema_code

        # -- Upsert EvalRun --
        now = timezone.now()
        eval_run, _created = EvalRunService.create_or_update(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_RESULT,
            entity_id=entity_id,
            run_key=f"extraction-{ext_result.pk}",
            status=EvalRun.Status.COMPLETED,
            prompt_hash=prompt_hash,
            prompt_slug=prompt_slug,
            trace_id=trace_id,
            input_snapshot_json=input_snap,
        )
        # Populate timing fields that create_or_update doesn't set
        _timing_dirty = False
        if not eval_run.started_at:
            eval_run.started_at = now
            _timing_dirty = True
        if not eval_run.completed_at:
            eval_run.completed_at = now
            _timing_dirty = True
        if eval_run.duration_ms is None and eval_run.started_at and eval_run.completed_at:
            eval_run.duration_ms = max(
                0, int((eval_run.completed_at - eval_run.started_at).total_seconds() * 1000)
            )
            _timing_dirty = True
        if _timing_dirty:
            eval_run.save(update_fields=["started_at", "completed_at", "duration_ms", "updated_at"])

        # -- Metrics --
        def _m(name, value, **kw):
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name=name,
                metric_value=value,
                **kw,
            )

        # Core extraction metrics
        confidence = float(getattr(invoice, "extraction_confidence", 0) or 0)
        _m("extraction_success", 1.0)
        _m("extraction_confidence", confidence, unit="ratio")

        # Validation
        if validation_result is not None:
            _m("extraction_is_valid", 1.0 if validation_result.is_valid else 0.0)
            _m("extraction_validation_error_count",
               float(len(getattr(validation_result, "errors", []))),
               unit="count")

        # Duplicate
        if dup_result is not None:
            _m("extraction_is_duplicate", 1.0 if dup_result.is_duplicate else 0.0)

        # Field confidence
        if field_conf_result is not None:
            wcs = getattr(field_conf_result, "weakest_critical_score", None)
            if wcs is not None:
                _m("weakest_critical_field_score", float(wcs), unit="ratio")
            low_fields = getattr(field_conf_result, "low_confidence_fields", [])
            _m("low_confidence_field_count", float(len(low_fields)), unit="count")

        # Decision codes
        if decision_codes:
            _m("decision_code_count", float(len(decision_codes)), unit="count")
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="decision_codes",
                json_value=decision_codes,
            )

        # Response repair
        if extraction_resp is not None:
            repaired = getattr(extraction_resp, "was_repaired", False)
            _m("response_was_repaired", 1.0 if repaired else 0.0)
            qr = getattr(extraction_resp, "qr_data", None)
            if qr is not None:
                _m("qr_detected", 1.0)

        # OCR char count
        ocr_chars = getattr(ext_result, "ocr_char_count", None)
        if ocr_chars is not None:
            _m("ocr_char_count", float(ocr_chars), unit="count")

        # Recovery lane
        if ctx is not None and ctx.recovery_lane_invoked:
            _m("recovery_invoked", 1.0)

        # Governed-layer metrics
        if ctx is not None and ctx.source == "governed":
            overall = ctx.overall_confidence
            if overall is not None:
                _m("governed_overall_confidence", float(overall), unit="ratio")
            _m("requires_review", 1.0 if ctx.requires_review else 0.0)

        # -- Field outcomes (from governed ExtractionFieldValue when available) --
        cls._sync_field_outcomes_for_result(eval_run, ext_result, ctx, invoice)

        # -- Learning signals: validation failures --
        if validation_result is not None and not validation_result.is_valid:
            cls._emit_validation_failure_signals(
                eval_run, ext_result, validation_result,
            )

        # -- Learning signal: prompt review candidate --
        if ctx and ctx.decision_codes:
            cls._emit_prompt_review_signal(eval_run, ext_result, ctx)

    # ------------------------------------------------------------------
    # Entry point: after approval / rejection
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_approval(
        cls,
        approval,
        user=None,
        correction_records=None,
    ) -> None:
        """Persist learning signals from an approval or rejection event.

        Called after _record_governance_trail() in approve() / reject()
        and after try_auto_approve() success.
        """
        try:
            cls._sync_for_approval_inner(approval, user, correction_records)
        except Exception:
            logger.exception(
                "ExtractionEvalAdapter.sync_for_approval failed "
                "for approval=%s (non-fatal)",
                getattr(approval, "pk", "?"),
            )

    @classmethod
    def _sync_for_approval_inner(cls, approval, user, correction_records):
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        from apps.core_eval.models import EvalRun

        # Find the extraction EvalRun for the related ExtractionResult
        ext_result = getattr(approval, "extraction_result", None)
        eval_run = None
        if ext_result:
            runs = list(
                EvalRun.objects.filter(
                    app_module=APP_MODULE,
                    entity_type=ENTITY_TYPE_RESULT,
                    entity_id=str(ext_result.pk),
                ).order_by("-created_at")[:1]
            )
            eval_run = runs[0] if runs else None

        invoice = getattr(approval, "invoice", None)
        invoice_pk = getattr(invoice, "pk", None)
        status = getattr(approval, "status", "")

        # -- Approval outcome signal --
        is_auto = status == "AUTO_APPROVED"
        signal_type = SIG_AUTO_APPROVE_OUTCOME if is_auto else SIG_APPROVAL_OUTCOME
        outcome_value = "approved" if status in ("APPROVED", "AUTO_APPROVED") else "rejected"

        LearningSignalService.record(
            app_module=APP_MODULE,
            signal_type=signal_type,
            entity_type="Invoice",
            entity_id=str(invoice_pk) if invoice_pk else "",
            aggregation_key=f"approval-{approval.pk}",
            confidence=float(getattr(approval, "confidence_at_review", 0) or 0),
            actor=user,
            payload_json={
                "approval_id": approval.pk,
                "status": status,
                "is_touchless": getattr(approval, "is_touchless", False),
                "confidence_at_review": float(
                    getattr(approval, "confidence_at_review", 0) or 0
                ),
            },
            eval_run=eval_run,
        )

        # -- Approval metrics on the extraction EvalRun --
        if eval_run:
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="extraction_approval_decision",
                metric_value=1.0 if outcome_value == "approved" else 0.0,
            )
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="extraction_approval_confidence",
                metric_value=float(
                    getattr(approval, "confidence_at_review", 0) or 0
                ),
                unit="ratio",
            )

        # -- Field correction signals --
        if correction_records:
            if eval_run:
                EvalMetricService.upsert(
                    eval_run=eval_run,
                    metric_name="extraction_corrections_count",
                    metric_value=float(len(correction_records)),
                    unit="count",
                )
            for corr in correction_records:
                LearningSignalService.record(
                    app_module=APP_MODULE,
                    signal_type=SIG_FIELD_CORRECTION,
                    entity_type="Invoice",
                    entity_id=str(invoice_pk) if invoice_pk else "",
                    aggregation_key=f"approval-{approval.pk}",
                    actor=user,
                    field_name=getattr(corr, "field_name", ""),
                    old_value=getattr(corr, "original_value", ""),
                    new_value=getattr(corr, "corrected_value", ""),
                    payload_json={
                        "entity_type": getattr(corr, "entity_type", ""),
                        "entity_id": getattr(corr, "entity_id", None),
                        "approval_id": approval.pk,
                    },
                    eval_run=eval_run,
                )

            # Update field outcomes to INCORRECT for corrected fields
            cls._update_field_outcomes_from_corrections(
                eval_run, correction_records,
            )

        # -- Confirm ground truth for non-corrected fields --
        # When a human approves, non-corrected fields are implicitly
        # confirmed correct.  Set ground_truth = predicted_value so the
        # eval dashboard shows meaningful ground truth after approval.
        if eval_run and status in ("APPROVED", "AUTO_APPROVED"):
            corrected_field_names = set()
            if correction_records:
                corrected_field_names = {
                    getattr(c, "field_name", "") for c in correction_records
                }
            cls._confirm_ground_truth_on_approval(
                eval_run, corrected_field_names,
            )

        # -- Review override signal --
        if (
            status == "APPROVED"
            and not getattr(approval, "is_touchless", True)
            and correction_records
        ):
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_REVIEW_OVERRIDE,
                entity_type="Invoice",
                entity_id=str(invoice_pk) if invoice_pk else "",
                aggregation_key=f"approval-{approval.pk}",
                confidence=float(
                    getattr(approval, "confidence_at_review", 0) or 0
                ),
                actor=user,
                payload_json={
                    "approval_id": approval.pk,
                    "fields_corrected": len(correction_records),
                    "corrected_field_names": [
                        getattr(c, "field_name", "") for c in correction_records
                    ],
                },
                eval_run=eval_run,
            )

    # ------------------------------------------------------------------
    # Field outcomes: extraction-time (from governed or legacy data)
    # ------------------------------------------------------------------
    @classmethod
    def _sync_field_outcomes_for_result(cls, eval_run, ext_result, ctx, invoice=None):
        """Populate EvalFieldOutcome from extraction data.

        Predicted value = final LLM-extracted value from raw_response
        (what the full pipeline actually produced and persisted to the
        Invoice model).  The governed deterministic value is stored in
        detail_json for diagnostic comparison.

        Ground truth is left EMPTY at extraction time.  It is only
        populated later when a human corrects a field during approval
        (via _update_field_outcomes_from_corrections) or confirms the
        extraction (via _confirm_ground_truth_on_approval).
        """
        from apps.core_eval.services.eval_field_outcome_service import (
            EvalFieldOutcomeService,
        )
        from apps.core_eval.models import EvalFieldOutcome

        # LLM extraction values = the actual predicted output
        legacy_values = cls._build_legacy_value_map(ext_result)

        outcomes = []

        if ctx and ctx.source == "governed" and ctx.extraction_run_id:
            outcomes = cls._field_outcomes_from_governed(
                ctx.extraction_run_id,
                legacy_values=legacy_values,
            )
        else:
            outcomes = cls._field_outcomes_from_legacy(ext_result)

        if not outcomes:
            return

        # Replace existing outcomes for this run (idempotent on rerun)
        EvalFieldOutcomeService.replace_for_run(eval_run=eval_run, outcomes=outcomes)

    @classmethod
    def _build_invoice_truth_map(cls, invoice) -> dict:
        """Build a {field_code: str_value} map from the persisted Invoice.

        This serves as ground truth: values that were saved to the Invoice
        model after extraction (and possibly after human correction).
        """
        if invoice is None:
            return {}

        def _str(v):
            if v is None:
                return ""
            return str(v).strip()

        return {
            "invoice_number": _str(getattr(invoice, "invoice_number", None)),
            "invoice_date": _str(getattr(invoice, "invoice_date", None)),
            "due_date": _str(getattr(invoice, "due_date", None)),
            "total_amount": _str(getattr(invoice, "total_amount", None)),
            "grand_total": _str(getattr(invoice, "grand_total", None)),
            "total_taxable_amount": _str(getattr(invoice, "total_taxable_amount", None)),
            "total_tax_amount": _str(getattr(invoice, "total_tax_amount", None)),
            "total_cgst": _str(getattr(invoice, "total_cgst", None)),
            "total_sgst": _str(getattr(invoice, "total_sgst", None)),
            "total_igst": _str(getattr(invoice, "total_igst", None)),
            "total_cess": _str(getattr(invoice, "total_cess", None)),
            "supplier_name": _str(getattr(invoice, "supplier_name", None)),
            "supplier_gstin": _str(getattr(invoice, "supplier_gstin", None)),
            "supplier_address": _str(getattr(invoice, "supplier_address", None)),
            "buyer_name": _str(getattr(invoice, "buyer_name", None)),
            "buyer_gstin": _str(getattr(invoice, "buyer_gstin", None)),
            "buyer_address": _str(getattr(invoice, "buyer_address", None)),
            "currency": _str(getattr(invoice, "currency", None)),
            "po_number": _str(getattr(invoice, "po_number", None)),
            "place_of_supply": _str(getattr(invoice, "place_of_supply", None)),
            "supply_type": _str(getattr(invoice, "supply_type", None)),
            "is_reverse_charge": _str(getattr(invoice, "is_reverse_charge", None)),
            "amount_in_words": _str(getattr(invoice, "amount_in_words", None)),
        }

    @classmethod
    def _field_outcomes_from_governed(
        cls,
        extraction_run_id: int,
        *,
        legacy_values: dict | None = None,
    ) -> list[dict]:
        """Build field outcome dicts from governed + LLM extraction data.

        Predicted = LLM-extracted value (from ``legacy_values``).  When LLM
        did not extract a field, falls back to the deterministic value.
        Ground truth is left empty -- populated later during human approval.
        The deterministic value is preserved in ``detail_json`` for diagnostics.
        """
        try:
            from apps.extraction_core.models import ExtractionFieldValue
        except ImportError:
            return []

        if legacy_values is None:
            legacy_values = {}

        field_values = ExtractionFieldValue.objects.filter(
            extraction_run_id=extraction_run_id,
        ).values(
            "field_code", "value", "corrected_value",
            "confidence", "is_corrected", "category",
        )

        outcomes = []
        for fv in field_values:
            deterministic_value = fv["value"] or ""

            # Predicted = LLM value first, deterministic fallback
            llm_confidence = None
            if fv["field_code"] in legacy_values:
                lv = legacy_values[fv["field_code"]]
                predicted = lv["value"]
                llm_confidence = lv["confidence"]
                source = "llm"
            elif deterministic_value:
                predicted = deterministic_value
                source = "deterministic"
            else:
                predicted = ""
                source = "deterministic"

            # Ground truth is empty at extraction time.
            # Populated by _update_field_outcomes_from_corrections (corrected)
            # or _confirm_ground_truth_on_approval (confirmed).
            ground_truth = ""

            if fv["is_corrected"]:
                corrected = fv["corrected_value"] or ""
                status = "INCORRECT"
                ground_truth = corrected
            elif not predicted:
                status = "MISSING"
            else:
                status = "CORRECT"

            # Use LLM confidence when predicted came from LLM,
            # otherwise use deterministic confidence.
            effective_confidence = fv["confidence"]
            if source == "llm" and llm_confidence is not None:
                effective_confidence = llm_confidence

            outcomes.append({
                "field_name": fv["field_code"],
                "status": status,
                "predicted_value": predicted,
                "ground_truth_value": ground_truth,
                "confidence": effective_confidence,
                "detail_json": {
                    "category": fv["category"],
                    "source": source,
                    "deterministic_value": deterministic_value,
                    "deterministic_confidence": fv["confidence"],
                },
            })
        return outcomes

    @classmethod
    def _build_legacy_value_map(cls, ext_result) -> dict:
        """Build {field_code: {value, confidence}} from raw_response.

        Maps the flat top-level keys in raw_response (produced by the
        legacy LLM extraction agent) to the governed field_code namespace.
        Returns ``{field_code: {"value": str, "confidence": float}}``.
        """
        raw = getattr(ext_result, "raw_response", None) or {}
        if not isinstance(raw, dict):
            return {}

        def _str(v):
            if v is None:
                return ""
            return str(v).strip()

        # LLM per-field confidence from _field_confidence.header
        fc = raw.get("_field_confidence") or {}
        header_conf = fc.get("header") or {} if isinstance(fc, dict) else {}

        # Map raw_response keys -> governed field_codes
        mapping = {
            "invoice_number": "invoice_number",
            "invoice_date": "invoice_date",
            "due_date": "due_date",
            "po_number": "po_number",
            "currency": "currency",
            "total_amount": "total_amount",
            "subtotal": "total_taxable_amount",
            "tax_amount": "total_tax_amount",
            "vendor_name": "supplier_name",
            "vendor_tax_id": "supplier_gstin",
            "buyer_name": "buyer_name",
        }

        result = {}
        for raw_key, field_code in mapping.items():
            val = _str(raw.get(raw_key))
            if val:
                conf = header_conf.get(raw_key)
                result[field_code] = {
                    "value": val,
                    "confidence": float(conf) if conf is not None else None,
                }
        return result

    @classmethod
    def _field_outcomes_from_legacy(cls, ext_result) -> list[dict]:
        """Build field outcome dicts from raw_response.

        Predicted = LLM-extracted value from raw_response top-level keys.
        Ground truth is left empty -- populated during human approval.
        """
        raw = getattr(ext_result, "raw_response", None) or {}
        if not isinstance(raw, dict):
            return []

        field_conf = raw.get("_field_confidence") or {}
        if not isinstance(field_conf, dict):
            return []

        # Build predicted values from raw_response
        legacy_values = cls._build_legacy_value_map(ext_result)

        outcomes = []
        for field_name, conf_value in field_conf.items():
            conf = None
            if isinstance(conf_value, (int, float)):
                conf = float(conf_value)
            elif isinstance(conf_value, dict):
                conf = conf_value.get("confidence")
                if conf is not None:
                    conf = float(conf)

            lv = legacy_values.get(field_name)
            predicted = lv["value"] if lv else ""
            if lv and lv["confidence"] is not None:
                conf = lv["confidence"]
            status = "CORRECT" if predicted else "MISSING"

            outcomes.append({
                "field_name": field_name,
                "status": status,
                "predicted_value": predicted,
                "ground_truth_value": "",
                "confidence": conf,
                "detail_json": {"source": "legacy"},
            })
        return outcomes

    # ------------------------------------------------------------------
    # Update field outcomes after approval corrections
    # ------------------------------------------------------------------
    @classmethod
    def _update_field_outcomes_from_corrections(cls, eval_run, correction_records):
        """Mark corrected fields as INCORRECT in the existing field outcomes."""
        if not eval_run:
            return
        from apps.core_eval.models import EvalFieldOutcome

        for corr in correction_records:
            field_name = getattr(corr, "field_name", "")
            if not field_name:
                continue
            updated = EvalFieldOutcome.objects.filter(
                eval_run=eval_run,
                field_name=field_name,
            ).update(
                status="INCORRECT",
                ground_truth_value=getattr(corr, "corrected_value", ""),
            )
            if not updated:
                # Field was not tracked at extraction time -- create outcome
                EvalFieldOutcome.objects.create(
                    eval_run=eval_run,
                    field_name=field_name,
                    status="INCORRECT",
                    predicted_value=getattr(corr, "original_value", ""),
                    ground_truth_value=getattr(corr, "corrected_value", ""),
                    detail_json={
                        "source": "approval_correction",
                        "entity_type": getattr(corr, "entity_type", ""),
                    },
                )

    # ------------------------------------------------------------------
    # Confirm ground truth on approval
    # ------------------------------------------------------------------
    @classmethod
    def _confirm_ground_truth_on_approval(cls, eval_run, corrected_field_names):
        """Set ground_truth = predicted for non-corrected fields after approval.

        When a human approves an extraction (with or without corrections),
        non-corrected fields are implicitly confirmed correct.  This sets
        their ground_truth_value so the eval dashboard shows meaningful
        data post-approval.
        """
        if not eval_run:
            return
        from apps.core_eval.models import EvalFieldOutcome

        outcomes = EvalFieldOutcome.objects.filter(eval_run=eval_run)
        for outcome in outcomes:
            if outcome.field_name in corrected_field_names:
                continue  # already handled by _update_field_outcomes_from_corrections
            if not outcome.predicted_value:
                continue  # nothing to confirm
            if outcome.ground_truth_value:
                continue  # already has ground truth
            outcome.ground_truth_value = outcome.predicted_value
            outcome.status = "CORRECT"
            outcome.save(update_fields=["ground_truth_value", "status", "updated_at"])

    # ------------------------------------------------------------------
    # Learning signals: validation failures
    # ------------------------------------------------------------------
    @classmethod
    def _emit_validation_failure_signals(cls, eval_run, ext_result, validation_result):
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        errors = getattr(validation_result, "errors", [])
        for err in errors[:20]:  # cap to avoid spam
            err_str = str(err) if not isinstance(err, str) else err
            LearningSignalService.record(
                app_module=APP_MODULE,
                signal_type=SIG_VALIDATION_FAILURE,
                entity_type=ENTITY_TYPE_RESULT,
                entity_id=str(ext_result.pk),
                aggregation_key=f"extraction-{ext_result.pk}",
                payload_json={"error": err_str[:500]},
                eval_run=eval_run,
            )

    # ------------------------------------------------------------------
    # Learning signals: prompt review candidate
    # ------------------------------------------------------------------
    @classmethod
    def _emit_prompt_review_signal(cls, eval_run, ext_result, ctx):
        """Emit a signal when decision codes suggest the prompt may need tuning."""
        from apps.core_eval.services.learning_signal_service import LearningSignalService

        # Heuristic: if recovery was invoked or there are many decision codes,
        # the prompt may need review.
        trigger = False
        if ctx.recovery_lane_invoked:
            trigger = True
        if len(ctx.decision_codes) >= 4:
            trigger = True
        if not trigger:
            return

        LearningSignalService.record(
            app_module=APP_MODULE,
            signal_type=SIG_PROMPT_REVIEW_CANDIDATE,
            entity_type=ENTITY_TYPE_RESULT,
            entity_id=str(ext_result.pk),
            aggregation_key=f"extraction-{ext_result.pk}",
            confidence=float(ctx.overall_confidence or 0),
            payload_json={
                "decision_codes": ctx.decision_codes,
                "prompt_hash": ctx.prompt_hash,
                "prompt_source": ctx.prompt_source,
                "recovery_lane_invoked": ctx.recovery_lane_invoked,
            },
            eval_run=eval_run,
        )
