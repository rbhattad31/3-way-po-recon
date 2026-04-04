"""Celery tasks for the extraction pipeline.

EXECUTION OWNERSHIP
-------------------
The authoritative execution record is ExtractionRun (apps.extraction_core).
This task creates DocumentUpload records and triggers extraction, but the
ExtractionRun model is the runtime source of truth once extraction starts.
Credit lifecycle: reserve (view) -> consume (task, on OCR success) or
refund (task, on OCR failure). reference_type="document_upload",
reference_id=<DocumentUpload.pk>.
"""
from __future__ import annotations

import logging
import uuid

from celery import shared_task
from django.db import transaction

from apps.core.enums import FileProcessingState, InvoiceStatus
from apps.core.decorators import observed_task
from apps.core.evaluation_constants import (
    EXTRACTION_APPROVAL_CONFIDENCE,
    EXTRACTION_APPROVAL_DECISION,
    EXTRACTION_AUTO_APPROVE_CONFIDENCE,
    EXTRACTION_CONFIDENCE,
    EXTRACTION_CORRECTIONS_COUNT,
    EXTRACTION_DECISION_CODE_COUNT,
    EXTRACTION_DOC_TYPE_CONFIDENCE,
    EXTRACTION_IS_DUPLICATE,
    EXTRACTION_IS_DUPLICATE_OBS,
    EXTRACTION_IS_VALID,
    EXTRACTION_OCR_CHAR_COUNT,
    EXTRACTION_QR_DETECTED,
    EXTRACTION_RECOVERY_INVOKED,
    EXTRACTION_REQUIRES_HUMAN_REVIEW,
    EXTRACTION_REQUIRES_REVIEW,
    EXTRACTION_RESPONSE_REPAIRED,
    EXTRACTION_SUCCESS,
    EXTRACTION_VALIDATION_IS_VALID,
    EXTRACTION_WEAKEST_CRITICAL_FIELD_SCORE,
    EXTRACTION_WEAKEST_CRITICAL_SCORE,
    TRACE_EXTRACTION_PIPELINE,
)
from apps.core.metrics import MetricsService
from apps.documents.models import DocumentUpload

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Langfuse helpers -- fail-silent wrappers
# ---------------------------------------------------------------------------

def _lf_start_trace(trace_id, name, **kwargs):
    try:
        from apps.core.langfuse_client import start_trace
        return start_trace(trace_id, name, **kwargs)
    except Exception:
        return None


def _lf_span(parent, name, **kwargs):
    try:
        from apps.core.langfuse_client import start_span
        return start_span(parent, name, **kwargs) if parent else None
    except Exception:
        return None


def _lf_end(span, **kwargs):
    try:
        from apps.core.langfuse_client import end_span
        end_span(span, **kwargs)
    except Exception:
        pass


def _lf_score_trace(trace_id, name, value, span=None, **kwargs):
    try:
        from apps.core.langfuse_client import score_trace
        score_trace(trace_id, name, value, span=span, **kwargs)
    except Exception:
        pass


def _lf_score_obs(obs, name, value, **kwargs):
    try:
        from apps.core.langfuse_client import score_observation
        score_observation(obs, name, value, **kwargs)
    except Exception:
        pass


def _lf_update(span, **kwargs):
    try:
        from apps.core.langfuse_client import update_trace
        update_trace(span, **kwargs)
    except Exception:
        pass


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("extraction.process_invoice_upload", audit_event="EXTRACTION_STARTED", entity_type="DocumentUpload")
def process_invoice_upload_task(self, upload_id: int, credit_ref_type: str = "document_upload", credit_ref_id: str = "") -> dict:
    """End-to-end extraction pipeline for a single DocumentUpload.

    Steps executed sequentially:
      1. Extract raw data (adapter)
      2. Parse into structured dataclass
      3. Normalise fields
      4. Validate mandatory fields & thresholds
      5. Duplicate detection
      6. Persist invoice + line items + extraction result
      7. Transition upload & invoice status
    """
    from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter, ExtractionResponse
    from apps.extraction.services.parser_service import ExtractionParserService
    from apps.extraction.services.normalization_service import NormalizationService
    from apps.extraction.services.validation_service import ValidationService
    from apps.extraction.services.duplicate_detection_service import DuplicateDetectionService
    from apps.extraction.services.persistence_service import (
        InvoicePersistenceService,
        ExtractionResultPersistenceService,
    )

    # ── Langfuse root trace ──────────────────────────────────────────────
    _trace_id = uuid.uuid4().hex
    _session_id = f"extraction-upload-{upload_id}"
    _lf_root = None
    _celery_task_id = getattr(getattr(self, "request", None), "id", None)

    try:
        upload = DocumentUpload.objects.get(pk=upload_id)
    except DocumentUpload.DoesNotExist:
        logger.error("DocumentUpload %s not found", upload_id)
        return {"status": "error", "message": f"Upload {upload_id} not found"}

    # Start Langfuse root trace with upload metadata
    _lf_root = _lf_start_trace(
        _trace_id,
        TRACE_EXTRACTION_PIPELINE,
        user_id=upload.uploaded_by_id,
        session_id=_session_id,
        metadata={
            "upload_id": upload_id,
            "filename": upload.original_filename or "",
            "celery_task_id": _celery_task_id,
            "blob_path": upload.blob_path or "",
        },
    )

    upload.processing_state = FileProcessingState.PROCESSING
    upload.save(update_fields=["processing_state", "updated_at"])

    try:
        # 1. Extract (OCR + LLM)
        _s_ocr = _lf_span(_lf_root, "ocr_extraction", metadata={"upload_id": upload_id})
        adapter = InvoiceExtractionAdapter()
        # Download from Azure Blob Storage
        if not upload.blob_path:
            _fail_upload(upload, "No blob_path set -- document not in Azure Blob Storage")
            _lf_end(_s_ocr, output={"error": "no_blob_path"}, level="ERROR")
            _lf_end(_lf_root, output={"status": "error"}, level="ERROR", is_root=True)
            return {"status": "error", "message": "No blob_path set on upload"}

        from apps.documents.blob_service import download_blob_to_tempfile
        file_path = download_blob_to_tempfile(upload.blob_path)

        try:
            extraction_resp: ExtractionResponse = adapter.extract(
                file_path,
                actor_user_id=upload.uploaded_by_id,
                document_upload_id=upload.pk,
                langfuse_trace=_lf_root,
                trace_id=_trace_id,
            )
        finally:
            import os
            try:
                os.unlink(file_path)
            except OSError:
                pass

        # Score: OCR stage
        _lf_end(_s_ocr, output={
            "success": extraction_resp.success,
            "ocr_char_count": extraction_resp.ocr_char_count,
            "ocr_page_count": extraction_resp.ocr_page_count,
            "ocr_duration_ms": extraction_resp.ocr_duration_ms,
            "engine": extraction_resp.engine_name,
            "was_repaired": extraction_resp.was_repaired,
        })
        if extraction_resp.ocr_char_count:
            _lf_score_obs(_s_ocr, EXTRACTION_OCR_CHAR_COUNT, float(extraction_resp.ocr_char_count),
                          comment=f"pages={extraction_resp.ocr_page_count}")

        if not extraction_resp.success:
            _fail_upload(upload, extraction_resp.error_message)
            ExtractionResultPersistenceService.save(upload, None, extraction_resp)
            _refund_credit_for_upload(upload, credit_ref_type=credit_ref_type, credit_ref_id=credit_ref_id)
            _lf_score_trace(_trace_id, EXTRACTION_SUCCESS, 0.0, span=_lf_root, comment="OCR/LLM failed")
            _lf_end(_lf_root, output={"status": "error", "error": extraction_resp.error_message[:200]}, level="ERROR", is_root=True)
            return {"status": "error", "message": extraction_resp.error_message}

        # 1a. Document type classification -- reject non-invoices early
        _s_doctype = _lf_span(_lf_root, "document_type_classification", metadata={"upload_id": upload_id})
        doc_type_result = _classify_document(extraction_resp.ocr_text)
        _doc_type_str = doc_type_result.document_type if doc_type_result else "UNKNOWN"
        _doc_type_conf = doc_type_result.confidence if doc_type_result else 0.0
        _lf_end(_s_doctype, output={
            "document_type": _doc_type_str,
            "confidence": _doc_type_conf,
            "matched_keywords": list(doc_type_result.matched_keywords) if doc_type_result else [],
        })
        _lf_score_obs(_s_doctype, EXTRACTION_DOC_TYPE_CONFIDENCE, _doc_type_conf,
                      comment=f"type={_doc_type_str}")

        if doc_type_result and doc_type_result.document_type not in ("INVOICE", "CREDIT_NOTE", "DEBIT_NOTE") \
                and doc_type_result.confidence >= 0.60 and not doc_type_result.is_ambiguous:
            reject_msg = (
                f"Document classified as {doc_type_result.document_type} "
                f"(confidence: {doc_type_result.confidence:.0%}), not an invoice. "
                f"Please upload this document through the appropriate channel."
            )
            _fail_upload(upload, reject_msg)
            _refund_credit_for_upload(upload, credit_ref_type=credit_ref_type, credit_ref_id=credit_ref_id)
            logger.info(
                "Upload %s rejected: classified as %s (confidence=%.2f, keywords=%s)",
                upload_id, doc_type_result.document_type,
                doc_type_result.confidence, doc_type_result.matched_keywords,
            )
            _lf_score_trace(_trace_id, EXTRACTION_SUCCESS, 0.0, span=_lf_root, comment=f"rejected: {_doc_type_str}")
            _lf_end(_lf_root, output={"status": "rejected", "document_type": _doc_type_str}, level="WARNING", is_root=True)
            return {"status": "rejected", "message": reject_msg, "document_type": doc_type_result.document_type}

        # 1b. Run governed extraction pipeline (enrichment)
        _s_governed = _lf_span(_lf_root, "governed_pipeline", metadata={"upload_id": upload_id})
        _run_governed_pipeline(upload, extraction_resp)
        _lf_end(_s_governed, output={"status": "completed"})

        # 2. Parse
        _s_parse = _lf_span(_lf_root, "parsing", metadata={"upload_id": upload_id})
        parser = ExtractionParserService()
        parsed = parser.parse(extraction_resp.raw_json)
        _lf_end(_s_parse, output={
            "line_items_count": len(parsed.line_items),
            "has_vendor": bool(parsed.raw_vendor_name),
            "has_invoice_number": bool(parsed.raw_invoice_number),
        })

        # 3. Normalise
        _s_norm = _lf_span(_lf_root, "normalization", metadata={"upload_id": upload_id})
        normalizer = NormalizationService()
        normalized = normalizer.normalize(parsed)
        _lf_end(_s_norm, output={
            "vendor_normalized": normalized.vendor_name_normalized or "",
            "invoice_number_normalized": normalized.normalized_invoice_number or "",
            "currency": normalized.currency or "",
            "total_amount": str(normalized.total_amount) if normalized.total_amount else "",
            "line_items_count": len(normalized.line_items),
        })

        # 3a. Field-level confidence scoring
        _s_field_conf = _lf_span(_lf_root, "field_confidence", metadata={"upload_id": upload_id})
        field_conf_result = None
        try:
            from apps.extraction.services.field_confidence_service import FieldConfidenceService
            repair_actions = (extraction_resp.raw_json.get("_repair") or {}).get("repair_actions", [])
            # Build evidence_context — include QR-verified fields when available
            _evidence_context: dict = {}
            if extraction_resp.qr_data is not None:
                try:
                    _evidence_context.update(extraction_resp.qr_data.to_evidence_context())
                except Exception:
                    pass
            field_conf_result = FieldConfidenceService.score(
                normalized,
                extraction_resp.raw_json,
                repair_actions,
                ocr_text=extraction_resp.ocr_text,
                evidence_context=_evidence_context if _evidence_context else None,
            )
            # Attach to normalized so ValidationService can read it
            normalized.field_confidence = {
                "header": field_conf_result.header,
                "lines": field_conf_result.lines,
            }
        except Exception as fc_exc:
            logger.warning("FieldConfidenceService failed (non-fatal): %s", fc_exc)
        _lf_end(_s_field_conf, output={
            "weakest_critical_field": field_conf_result.weakest_critical_field if field_conf_result else "",
            "weakest_critical_score": field_conf_result.weakest_critical_score if field_conf_result else 1.0,
            "low_confidence_fields": field_conf_result.low_confidence_fields if field_conf_result else [],
        })
        if field_conf_result:
            _lf_score_obs(_s_field_conf, EXTRACTION_WEAKEST_CRITICAL_SCORE,
                          field_conf_result.weakest_critical_score,
                          comment=f"field={field_conf_result.weakest_critical_field}")

        # 4. Validate
        _s_validate = _lf_span(_lf_root, "validation", metadata={"upload_id": upload_id})
        validator = ValidationService()
        validation_result = validator.validate(normalized)

        # 4a. Hard reconciliation validation (math checks)
        recon_val_result = None
        try:
            from apps.extraction.services.reconciliation_validator import ReconciliationValidatorService
            recon_val_result = ReconciliationValidatorService.validate(normalized)
            if recon_val_result.errors:
                # Surface reconciliation ERRORs as validation warnings (non-blocking)
                for ri in recon_val_result.errors:
                    validation_result.add_warning(
                        f"recon.{ri.check_name}",
                        f"[{ri.issue_code}] {ri.message}",
                    )
        except Exception as rv_exc:
            logger.warning("ReconciliationValidatorService failed (non-fatal): %s", rv_exc)
        _lf_end(_s_validate, output={
            "is_valid": validation_result.is_valid,
            "error_count": len(validation_result.errors),
            "warning_count": len(validation_result.warnings),
            "requires_review_override": getattr(validation_result, "requires_review_override", False),
            "recon_errors": len(recon_val_result.errors) if recon_val_result else 0,
            "recon_checks_passed": recon_val_result.checks_passed if recon_val_result else 0,
        })
        _lf_score_obs(_s_validate, EXTRACTION_VALIDATION_IS_VALID, 1.0 if validation_result.is_valid else 0.0,
                      comment=f"errors={len(validation_result.errors)}")

        # Embed field confidence and reconciliation metadata into raw_json for persistence
        if field_conf_result is not None:
            try:
                from apps.extraction.services.field_confidence_service import FieldConfidenceService as _FCS
                extraction_resp.raw_json["_field_confidence"] = _FCS.to_serializable(field_conf_result)
            except Exception:
                pass
        if recon_val_result is not None:
            try:
                from apps.extraction.services.reconciliation_validator import ReconciliationValidatorService as _RVS
                extraction_resp.raw_json["_validation"] = _RVS.to_serializable(recon_val_result)
            except Exception:
                pass

        # 4b. Derive machine-readable decision codes from all pipeline outputs
        _s_decision = _lf_span(_lf_root, "decision_code_derivation", metadata={"upload_id": upload_id})
        decision_codes: list = []
        try:
            from apps.extraction.decision_codes import derive_codes
            _prompt_source_type = (
                (extraction_resp.raw_json.get("_prompt_meta") or {}).get("prompt_source_type", "")
            )
            _repair_meta = extraction_resp.raw_json.get("_repair") or {}
            decision_codes = derive_codes(
                validation_result=validation_result,
                recon_val_result=recon_val_result,
                field_conf_result=field_conf_result,
                prompt_source_type=_prompt_source_type,
                qr_data=extraction_resp.qr_data,
                repair_metadata=_repair_meta,
            )
            extraction_resp.raw_json["_decision_codes"] = decision_codes
        except Exception as dc_exc:
            logger.warning("derive_codes failed (non-fatal): %s", dc_exc)
        _lf_end(_s_decision, output={"decision_codes": decision_codes})
        _lf_score_obs(_s_decision, EXTRACTION_DECISION_CODE_COUNT, float(len(decision_codes)),
                      comment=", ".join(decision_codes[:5]) if decision_codes else "none")

        # 4c. Recovery lane -- evaluate trigger (invocation deferred to after persistence)
        _s_recovery = _lf_span(_lf_root, "recovery_lane", metadata={"upload_id": upload_id})
        recovery_result = None
        recovery_decision = None
        try:
            from apps.extraction.services.recovery_lane_service import RecoveryLaneService
            recovery_decision = RecoveryLaneService.evaluate(decision_codes)
        except Exception as rl_exc:
            logger.warning("RecoveryLaneService.evaluate failed (non-fatal): %s", rl_exc)
        # Invocation is deferred to after step 6 (persistence) so the agent
        # can access the real invoice via invoice_details tool.
        # _s_recovery span will be closed after invocation below.

        # 5. Duplicate check -- exclude the existing invoice for this upload so a
        # reprocess does not flag the invoice as a duplicate of itself.
        _s_dup = _lf_span(_lf_root, "duplicate_detection", metadata={"upload_id": upload_id})
        from apps.documents.models import Invoice as _InvoiceModel
        _existing_inv = (
            _InvoiceModel.objects
            .filter(document_upload=upload)
            .order_by("-created_at")
            .values_list("pk", flat=True)
            .first()
        )
        dup_service = DuplicateDetectionService()
        dup_result = dup_service.check(normalized, exclude_invoice_id=_existing_inv)
        _lf_end(_s_dup, output={
            "is_duplicate": dup_result.is_duplicate,
            "duplicate_invoice_id": dup_result.duplicate_invoice_id,
            "reason": dup_result.reason,
        })
        _lf_score_obs(_s_dup, EXTRACTION_IS_DUPLICATE_OBS, 1.0 if dup_result.is_duplicate else 0.0,
                      comment=dup_result.reason or "unique")

        # 6. Persist
        _s_persist = _lf_span(_lf_root, "persistence", metadata={"upload_id": upload_id})
        persistence = InvoicePersistenceService()
        invoice = persistence.save(
            normalized=normalized,
            upload=upload,
            extraction_raw_json=extraction_resp.raw_json,
            validation_result=validation_result,
            duplicate_result=dup_result,
        )
        ext_result = ExtractionResultPersistenceService.save(upload, invoice, extraction_resp)
        # Persist Langfuse trace ID on the extraction result for cross-referencing
        if ext_result and _trace_id:
            try:
                ext_result.langfuse_trace_id = _trace_id
                ext_result.save(update_fields=["langfuse_trace_id", "updated_at"])
            except Exception:
                pass
        _lf_end(_s_persist, output={
            "invoice_id": invoice.pk,
            "invoice_number": invoice.invoice_number or "",
            "invoice_status": invoice.status,
            "extraction_confidence": invoice.extraction_confidence,
        })

        # 7. Finalise upload state
        upload.processing_state = FileProcessingState.COMPLETED
        upload.save(update_fields=["processing_state", "updated_at"])

        # Move blob from input/ to processed/
        if upload.blob_path and upload.blob_path.startswith("input/"):
            try:
                from apps.documents.blob_service import move_blob
                new_path = upload.blob_path.replace("input/", "processed/", 1)
                move_blob(upload.blob_path, new_path)
                upload.blob_path = new_path
                upload.save(update_fields=["blob_path", "updated_at"])
            except Exception as mv_err:
                logger.warning("Blob move to processed/ failed: %s", mv_err)

        # 7b. Recovery lane invocation (deferred from step 4c -- now invoice is persisted)
        try:
            if recovery_decision and recovery_decision.should_invoke:
                logger.info(
                    "Recovery lane triggered for upload %s -- codes: %s",
                    upload_id, recovery_decision.trigger_codes,
                )
                from apps.extraction.services.recovery_lane_service import RecoveryLaneService
                recovery_result = RecoveryLaneService.invoke(
                    recovery_decision,
                    invoice_id=invoice.pk,
                    validation_result=validation_result,
                    field_conf_result=field_conf_result,
                    actor_user_id=upload.uploaded_by_id,
                    document_upload_id=upload.pk,
                    trace_id=_trace_id,
                )
                # Persist recovery data into the extraction result
                if ext_result and recovery_result:
                    try:
                        raw = ext_result.raw_response or {}
                        raw["_recovery"] = recovery_result.to_serializable()
                        ext_result.raw_response = raw
                        ext_result.save(update_fields=["raw_response", "updated_at"])
                    except Exception as persist_exc:
                        logger.warning("Failed to persist recovery data: %s", persist_exc)
        except Exception as rl_exc:
            logger.warning("RecoveryLaneService.invoke failed (non-fatal): %s", rl_exc)
        # Close recovery lane span
        _recovery_invoked = recovery_result.invoked if recovery_result else False
        _recovery_succeeded = recovery_result.succeeded if recovery_result else False
        _lf_end(_s_recovery, output={
            "invoked": _recovery_invoked,
            "succeeded": _recovery_succeeded,
            "trigger_codes": recovery_decision.trigger_codes if recovery_decision else [],
        })
        _lf_score_obs(_s_recovery, EXTRACTION_RECOVERY_INVOKED, 1.0 if _recovery_invoked else 0.0,
                      comment=f"succeeded={_recovery_succeeded}")

        # If valid and not duplicate, gate through extraction approval
        _s_approval = _lf_span(_lf_root, "approval_gate", metadata={
            "upload_id": upload_id, "invoice_id": invoice.pk,
        })
        if validation_result.is_valid and not dup_result.is_duplicate:
            from apps.extraction.services.approval_service import ExtractionApprovalService

            # Critical field failures force human review even if confidence passes threshold
            review_forced = getattr(validation_result, "requires_review_override", False)

            # Try auto-approve first (disabled by default — threshold = 1.1)
            # Skip auto-approval entirely when critical field review is forced
            auto_approval = None if review_forced else ExtractionApprovalService.try_auto_approve(
                invoice, ext_result, lf_trace_id=_trace_id, lf_span=_lf_root,
            )
            if not auto_approval:
                # Human approval required -- set PENDING_APPROVAL
                invoice.status = InvoiceStatus.PENDING_APPROVAL
                invoice.save(update_fields=["status", "updated_at"])
                ExtractionApprovalService.create_pending_approval(invoice, ext_result)

                from apps.auditlog.services import AuditService as _AS
                from apps.core.enums import AuditEventType as _AET
                _AS.log_event(
                    entity_type="Invoice",
                    entity_id=invoice.pk,
                    event_type=_AET.EXTRACTION_APPROVAL_PENDING,
                    description=f"Extraction pending human approval for invoice {invoice.invoice_number}",
                    metadata={"upload_id": upload_id, "confidence": invoice.extraction_confidence},
                )

        # Close approval gate span
        _approval_outcome = "skipped"
        if validation_result.is_valid and not dup_result.is_duplicate:
            if 'auto_approval' in dir() and auto_approval:
                _approval_outcome = "auto_approved"
            elif getattr(validation_result, "requires_review_override", False):
                _approval_outcome = "review_forced"
            else:
                _approval_outcome = "pending_human"
        elif dup_result.is_duplicate:
            _approval_outcome = "duplicate"
        elif not validation_result.is_valid:
            _approval_outcome = "invalid"
        _lf_end(_s_approval, output={"outcome": _approval_outcome, "invoice_status": invoice.status})
        _lf_score_obs(_s_approval, EXTRACTION_REQUIRES_HUMAN_REVIEW,
                      0.0 if _approval_outcome == "auto_approved" else 1.0,
                      comment=_approval_outcome)
        _lf_score_trace(_trace_id, EXTRACTION_REQUIRES_REVIEW,
                        0.0 if _approval_outcome == "auto_approved" else 1.0,
                        span=_lf_root, comment=_approval_outcome)

        # ── Trace-level scores ──────────────────────────────────────
        _lf_score_trace(_trace_id, EXTRACTION_CONFIDENCE,
                        float(invoice.extraction_confidence or 0.0),
                        span=_lf_root, comment=f"invoice={invoice.pk}")
        _lf_score_trace(_trace_id, EXTRACTION_SUCCESS, 1.0,
                        span=_lf_root, comment=f"status={invoice.status}")
        _lf_score_trace(_trace_id, EXTRACTION_IS_VALID,
                        1.0 if validation_result.is_valid else 0.0,
                        span=_lf_root, comment=f"errors={len(validation_result.errors)}")
        _lf_score_trace(_trace_id, EXTRACTION_IS_DUPLICATE,
                        1.0 if dup_result.is_duplicate else 0.0,
                        span=_lf_root, comment=dup_result.reason or "unique")
        if field_conf_result:
            _lf_score_trace(_trace_id, EXTRACTION_WEAKEST_CRITICAL_FIELD_SCORE,
                            field_conf_result.weakest_critical_score,
                            span=_lf_root, comment=f"field={field_conf_result.weakest_critical_field}")
        if decision_codes:
            _lf_score_trace(_trace_id, EXTRACTION_DECISION_CODE_COUNT,
                            float(len(decision_codes)),
                            span=_lf_root, comment=", ".join(decision_codes[:5]))
        if extraction_resp.was_repaired:
            _lf_score_trace(_trace_id, EXTRACTION_RESPONSE_REPAIRED, 1.0,
                            span=_lf_root, comment=f"actions={len(extraction_resp.repair_actions)}")
        if extraction_resp.qr_data is not None:
            _lf_score_trace(_trace_id, EXTRACTION_QR_DETECTED, 1.0,
                            span=_lf_root, comment=f"strategy={extraction_resp.qr_data.decode_strategy}")

        # ── core_eval: persist extraction eval run + metrics + field outcomes ──
        try:
            from apps.extraction.services.eval_adapter import ExtractionEvalAdapter
            ExtractionEvalAdapter.sync_for_extraction_result(
                ext_result,
                invoice,
                validation_result=validation_result,
                field_conf_result=field_conf_result,
                dup_result=dup_result,
                decision_codes=decision_codes,
                extraction_resp=extraction_resp,
                trace_id=_trace_id,
            )
        except Exception:
            logger.debug("core_eval sync failed (non-fatal)")

        # Audit: extraction completed
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        _audit_meta = {
            "upload_id": upload_id,
            "is_duplicate": dup_result.is_duplicate,
            "is_valid": validation_result.is_valid,
            "langfuse_trace_id": _trace_id,
        }
        if field_conf_result is not None:
            _audit_meta["weakest_critical_field"] = field_conf_result.weakest_critical_field
            _audit_meta["weakest_critical_score"] = field_conf_result.weakest_critical_score
            _audit_meta["low_confidence_fields_count"] = len(field_conf_result.low_confidence_fields)
        if recon_val_result is not None:
            _audit_meta["recon_errors"] = len(recon_val_result.errors)
            _audit_meta["recon_warnings"] = len(recon_val_result.warnings)
            _audit_meta["recon_checks_passed"] = recon_val_result.checks_passed
        if getattr(validation_result, "requires_review_override", False):
            _audit_meta["review_forced_by"] = validation_result.critical_failures
        if decision_codes:
            _audit_meta["decision_codes"] = decision_codes
        if extraction_resp.qr_data is not None:
            _audit_meta["qr_irn"] = extraction_resp.qr_data.irn
            _audit_meta["qr_doc_type"] = extraction_resp.qr_data.doc_type
            _audit_meta["qr_decode_strategy"] = extraction_resp.qr_data.decode_strategy
        if recovery_result is not None:
            _audit_meta["recovery_lane_invoked"] = recovery_result.invoked
            _audit_meta["recovery_lane_succeeded"] = recovery_result.succeeded
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=invoice.pk,
            event_type=AuditEventType.EXTRACTION_COMPLETED,
            description=f"Extraction completed for invoice {invoice.invoice_number} (confidence: {invoice.extraction_confidence})",
            metadata=_audit_meta,
        )

        # --- Auto-create AP Case only if invoice is READY_FOR_RECON (auto-approved) ---
        case_id = None
        if invoice.status == InvoiceStatus.READY_FOR_RECON:
            try:
                from apps.cases.services.case_creation_service import CaseCreationService
                case = CaseCreationService.create_from_upload(
                    invoice=invoice,
                    uploaded_by=upload.uploaded_by,
                )
                case_id = case.pk
                logger.info("Created AP Case %s for invoice %s", case.case_number, invoice.invoice_number)

                # Trigger case orchestration
                from apps.cases.tasks import process_case_task
                from apps.core.utils import dispatch_task
                dispatch_task(process_case_task, case_id=case.pk)
            except Exception as case_exc:
                logger.exception(
                    "AP Case creation/processing failed for invoice %s: %s",
                    invoice.pk, case_exc,
                )

        logger.info(
            "Extraction pipeline completed for upload %s -> invoice %s (status=%s)",
            upload_id, invoice.pk, invoice.status,
        )

        # ── Credit: consume reserved credit on successful extraction ──
        _consume_credit_for_upload(upload, credit_ref_type=credit_ref_type, credit_ref_id=credit_ref_id)

        # ── Close Langfuse root trace ──
        _lf_update(_lf_root, is_root=True, metadata={
            "invoice_id": invoice.pk,
            "invoice_number": invoice.invoice_number or "",
            "invoice_status": invoice.status,
            "langfuse_trace_id": _trace_id,
        })
        _lf_end(_lf_root, is_root=True, output={
            "status": "ok",
            "invoice_id": invoice.pk,
            "invoice_status": invoice.status,
            "confidence": invoice.extraction_confidence,
            "is_duplicate": dup_result.is_duplicate,
            "is_valid": validation_result.is_valid,
            "case_id": case_id,
        })

        return {
            "status": "ok",
            "upload_id": upload_id,
            "invoice_id": invoice.pk,
            "invoice_status": invoice.status,
            "is_duplicate": dup_result.is_duplicate,
            "is_valid": validation_result.is_valid,
            "case_id": case_id,
        }

    except Exception as exc:
        logger.exception("Extraction pipeline failed for upload %s", upload_id)
        _fail_upload(upload, str(exc))
        # ── Credit: refund reserved credit -- extraction failed (OCR/pipeline error) ──
        _refund_credit_for_upload(upload, credit_ref_type=credit_ref_type, credit_ref_id=credit_ref_id)
        # ── Close Langfuse root trace on error ──
        _lf_score_trace(_trace_id, EXTRACTION_SUCCESS, 0.0,
                        span=_lf_root, comment=f"exception: {str(exc)[:100]}")
        _lf_end(_lf_root, output={"status": "error", "error": str(exc)[:300]}, level="ERROR", is_root=True)
        # Audit: extraction failed
        try:
            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType
            AuditService.log_event(
                entity_type="DocumentUpload",
                entity_id=upload_id,
                event_type=AuditEventType.EXTRACTION_FAILED,
                description=f"Extraction failed: {str(exc)[:200]}",
                metadata={"error": str(exc)[:500]},
            )
        except Exception:
            pass
        try:
            raise self.retry(exc=exc)
        except (AttributeError, TypeError):
            # Running outside Celery context (sync fallback) — re-raise directly
            raise exc


def _classify_document(ocr_text: str):
    """Run document type classification on OCR text.

    Returns a ClassificationResult or None if classification is unavailable.
    Non-invoice types (GRN, PURCHASE_ORDER, DELIVERY_NOTE, STATEMENT) trigger
    early rejection in the extraction task.
    """
    if not ocr_text or not ocr_text.strip():
        return None
    try:
        from apps.extraction_core.services.document_classifier import DocumentTypeClassifier
        return DocumentTypeClassifier.classify(ocr_text)
    except Exception as exc:
        logger.warning("Document classification failed, proceeding as INVOICE: %s", exc)
        return None


def _run_governed_pipeline(upload: DocumentUpload, extraction_resp) -> None:
    """Run the governed extraction pipeline as enrichment.

    Creates an ExtractionDocument linked to this upload, then runs the
    ExtractionPipeline to produce an ExtractionRun with jurisdiction,
    schema, and review-routing metadata.  This is additive — the legacy
    adapter result is still used for Invoice persistence.

    Gracefully degrades on any failure (missing jurisdiction profiles,
    schema configs, etc.) — the upload continues as "Legacy".
    """
    try:
        from apps.extraction_documents.models import ExtractionDocument
        from apps.extraction_core.services.extraction_pipeline import ExtractionPipeline

        ext_doc = ExtractionDocument.objects.create(
            document_upload=upload,
            file_name=upload.original_filename,
            file_path=upload.blob_path or "",
            file_hash=upload.file_hash or "",
            page_count=getattr(extraction_resp, "ocr_page_count", 0) or 0,
            ocr_text=extraction_resp.ocr_text or "",
        )

        run = ExtractionPipeline.run(
            extraction_document_id=ext_doc.pk,
            ocr_text=extraction_resp.ocr_text or "",
            document_type="INVOICE",
            vendor_id=None,
            enable_llm=False,
            user=upload.uploaded_by,
        )
        logger.info(
            "Governed pipeline completed for upload %s: run=%s status=%s confidence=%.2f",
            upload.pk, run.pk, run.status,
            run.overall_confidence or 0.0,
        )
    except Exception as exc:
        logger.warning(
            "Governed pipeline skipped for upload %s (falling back to Legacy): %s",
            upload.pk, exc,
        )


def _fail_upload(upload: DocumentUpload, message: str) -> None:
    upload.processing_state = FileProcessingState.FAILED
    upload.processing_message = message[:2000]
    upload.save(update_fields=["processing_state", "processing_message", "updated_at"])

    # Move blob from input/ to exception/
    if upload.blob_path and upload.blob_path.startswith("input/"):
        try:
            from apps.documents.blob_service import move_blob
            new_path = upload.blob_path.replace("input/", "exception/", 1)
            move_blob(upload.blob_path, new_path)
            upload.blob_path = new_path
            upload.save(update_fields=["blob_path", "updated_at"])
        except Exception as mv_err:
            logger.warning("Blob move to exception/ failed: %s", mv_err)


def _consume_credit_for_upload(upload: DocumentUpload, credit_ref_type: str = "document_upload", credit_ref_id: str = "") -> None:
    """Consume reserved credit after successful extraction.

    Policy: ChargePolicy.for_extraction_success() -> CONSUME.
    Uses credit_ref_type/credit_ref_id to match the reservation made by the view.
    Idempotent -- CreditService.consume() skips if already consumed for that reference.
    """
    if not upload.uploaded_by_id:
        return
    ref_id = credit_ref_id or str(upload.pk)
    try:
        from apps.extraction.services.credit_service import CreditService
        from apps.extraction.credit_models import UserCreditAccount
        if not UserCreditAccount.objects.filter(user_id=upload.uploaded_by_id, reserved_credits__gt=0).exists():
            return
        CreditService.consume(
            upload.uploaded_by, credits=1,
            reference_type=credit_ref_type,
            reference_id=ref_id,
            remarks=f"Consumed for extraction task upload_id={upload.pk}",
        )
    except Exception as credit_exc:
        logger.warning("Credit consume failed for upload %s: %s", upload.pk, credit_exc)


def _refund_credit_for_upload(upload: DocumentUpload, credit_ref_type: str = "document_upload", credit_ref_id: str = "") -> None:
    """Refund reserved credit when extraction fails (OCR/pipeline error).

    Policy: ChargePolicy.for_ocr_failure() / for_pipeline_failure() / for_non_invoice_document() -> REFUND.
    Uses credit_ref_type/credit_ref_id to match the reservation made by the view.
    Idempotent -- CreditService.refund() skips if already refunded for that reference.
    """
    if not upload.uploaded_by_id:
        return
    ref_id = credit_ref_id or str(upload.pk)
    try:
        from apps.extraction.services.credit_service import CreditService
        from apps.extraction.credit_models import UserCreditAccount
        if not UserCreditAccount.objects.filter(user_id=upload.uploaded_by_id, reserved_credits__gt=0).exists():
            return
        CreditService.refund(
            upload.uploaded_by, credits=1,
            reference_type=credit_ref_type,
            reference_id=ref_id,
            remarks=f"Refund for failed extraction task upload_id={upload.pk}",
        )
    except Exception as credit_exc:
        logger.warning("Credit refund failed for upload %s: %s", upload.pk, credit_exc)



