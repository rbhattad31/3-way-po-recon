"""Celery tasks for the extraction pipeline.

EXECUTION OWNERSHIP
───────────────────
The authoritative execution record is ExtractionRun (apps.extraction_core).
This task creates DocumentUpload records and triggers extraction, but the
ExtractionRun model is the runtime source of truth once extraction starts.
Credit lifecycle: reserve (view) → consume (task, on OCR success) or
refund (task, on OCR failure). reference_type="document_upload",
reference_id=<DocumentUpload.pk>.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.db import transaction

from apps.core.enums import FileProcessingState, InvoiceStatus
from apps.core.decorators import observed_task
from apps.core.metrics import MetricsService
from apps.documents.models import DocumentUpload

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
@observed_task("extraction.process_invoice_upload", audit_event="EXTRACTION_STARTED", entity_type="DocumentUpload")
def process_invoice_upload_task(self, upload_id: int) -> dict:
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

    try:
        upload = DocumentUpload.objects.get(pk=upload_id)
    except DocumentUpload.DoesNotExist:
        logger.error("DocumentUpload %s not found", upload_id)
        return {"status": "error", "message": f"Upload {upload_id} not found"}

    upload.processing_state = FileProcessingState.PROCESSING
    upload.save(update_fields=["processing_state", "updated_at"])

    try:
        # 1. Extract
        adapter = InvoiceExtractionAdapter()
        # Download from Azure Blob Storage
        if not upload.blob_path:
            _fail_upload(upload, "No blob_path set — document not in Azure Blob Storage")
            return {"status": "error", "message": "No blob_path set on upload"}

        from apps.documents.blob_service import download_blob_to_tempfile
        file_path = download_blob_to_tempfile(upload.blob_path)

        try:
            extraction_resp: ExtractionResponse = adapter.extract(
                file_path,
                actor_user_id=upload.uploaded_by_id,
            )
        finally:
            import os
            try:
                os.unlink(file_path)
            except OSError:
                pass

        if not extraction_resp.success:
            _fail_upload(upload, extraction_resp.error_message)
            ExtractionResultPersistenceService.save(upload, None, extraction_resp)
            _refund_credit_for_upload(upload)
            return {"status": "error", "message": extraction_resp.error_message}

        # 1a. Document type classification — reject non-invoices early
        doc_type_result = _classify_document(extraction_resp.ocr_text)
        if doc_type_result and doc_type_result.document_type not in ("INVOICE", "CREDIT_NOTE", "DEBIT_NOTE") \
                and doc_type_result.confidence >= 0.60 and not doc_type_result.is_ambiguous:
            reject_msg = (
                f"Document classified as {doc_type_result.document_type} "
                f"(confidence: {doc_type_result.confidence:.0%}), not an invoice. "
                f"Please upload this document through the appropriate channel."
            )
            _fail_upload(upload, reject_msg)
            _refund_credit_for_upload(upload)
            logger.info(
                "Upload %s rejected: classified as %s (confidence=%.2f, keywords=%s)",
                upload_id, doc_type_result.document_type,
                doc_type_result.confidence, doc_type_result.matched_keywords,
            )
            return {"status": "rejected", "message": reject_msg, "document_type": doc_type_result.document_type}

        # 1b. Run governed extraction pipeline (enrichment)
        _run_governed_pipeline(upload, extraction_resp)

        # 2. Parse
        parser = ExtractionParserService()
        parsed = parser.parse(extraction_resp.raw_json)

        # 3. Normalise
        normalizer = NormalizationService()
        normalized = normalizer.normalize(parsed)

        # 3a. Field-level confidence scoring
        field_conf_result = None
        try:
            from apps.extraction.services.field_confidence_service import FieldConfidenceService
            repair_actions = (extraction_resp.raw_json.get("_repair") or {}).get("repair_actions", [])
            field_conf_result = FieldConfidenceService.score(
                normalized,
                extraction_resp.raw_json,
                repair_actions,
            )
            # Attach to normalized so ValidationService can read it
            normalized.field_confidence = {
                "header": field_conf_result.header,
                "lines": field_conf_result.lines,
            }
        except Exception as fc_exc:
            logger.warning("FieldConfidenceService failed (non-fatal): %s", fc_exc)

        # 4. Validate
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
        decision_codes: list = []
        try:
            from apps.extraction.decision_codes import derive_codes
            _prompt_source_type = (
                (extraction_resp.raw_json.get("_prompt_meta") or {}).get("prompt_source_type", "")
            )
            decision_codes = derive_codes(
                validation_result=validation_result,
                recon_val_result=recon_val_result,
                field_conf_result=field_conf_result,
                prompt_source_type=_prompt_source_type,
            )
            extraction_resp.raw_json["_decision_codes"] = decision_codes
        except Exception as dc_exc:
            logger.warning("derive_codes failed (non-fatal): %s", dc_exc)

        # 4c. Recovery lane — invoke only for named failure modes
        recovery_result = None
        try:
            from apps.extraction.services.recovery_lane_service import RecoveryLaneService
            recovery_decision = RecoveryLaneService.evaluate(decision_codes)
            if recovery_decision.should_invoke:
                logger.info(
                    "Recovery lane triggered for upload %s — codes: %s",
                    upload_id, recovery_decision.trigger_codes,
                )
                recovery_result = RecoveryLaneService.invoke(
                    recovery_decision,
                    invoice_id=0,  # invoice not persisted yet; agent uses invoice_details tool
                    validation_result=validation_result,
                    field_conf_result=field_conf_result,
                    actor_user_id=upload.uploaded_by_id,
                )
                extraction_resp.raw_json["_recovery"] = recovery_result.to_serializable()
        except Exception as rl_exc:
            logger.warning("RecoveryLaneService failed (non-fatal): %s", rl_exc)

        # 5. Duplicate check — exclude the existing invoice for this upload so a
        # reprocess does not flag the invoice as a duplicate of itself.
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

        # 6. Persist
        persistence = InvoicePersistenceService()
        invoice = persistence.save(
            normalized=normalized,
            upload=upload,
            extraction_raw_json=extraction_resp.raw_json,
            validation_result=validation_result,
            duplicate_result=dup_result,
        )
        ext_result = ExtractionResultPersistenceService.save(upload, invoice, extraction_resp)

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

        # If valid and not duplicate, gate through extraction approval
        if validation_result.is_valid and not dup_result.is_duplicate:
            from apps.extraction.services.approval_service import ExtractionApprovalService

            # Critical field failures force human review even if confidence passes threshold
            review_forced = getattr(validation_result, "requires_review_override", False)

            # Try auto-approve first (disabled by default — threshold = 1.1)
            # Skip auto-approval entirely when critical field review is forced
            auto_approval = None if review_forced else ExtractionApprovalService.try_auto_approve(invoice, ext_result)
            if not auto_approval:
                # Human approval required — set PENDING_APPROVAL
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

        # Audit: extraction completed
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        _audit_meta = {
            "upload_id": upload_id,
            "is_duplicate": dup_result.is_duplicate,
            "is_valid": validation_result.is_valid,
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
        _consume_credit_for_upload(upload)

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
        # ── Credit: refund reserved credit — extraction failed (OCR/pipeline error) ──
        _refund_credit_for_upload(upload)
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


def _consume_credit_for_upload(upload: DocumentUpload) -> None:
    """Consume reserved credit after successful extraction.

    Policy: ChargePolicy.for_extraction_success() → CONSUME.
    reference_type='document_upload', reference_id=upload.pk.
    Idempotent — CreditService.consume() skips if already consumed.
    """
    if not upload.uploaded_by_id:
        return
    try:
        from apps.extraction.services.credit_service import CreditService
        from apps.extraction.credit_models import UserCreditAccount
        if not UserCreditAccount.objects.filter(user_id=upload.uploaded_by_id, reserved_credits__gt=0).exists():
            return
        CreditService.consume(
            upload.uploaded_by, credits=1,
            reference_type="document_upload",
            reference_id=str(upload.pk),
            remarks=f"Consumed for extraction task upload_id={upload.pk}",
        )
    except Exception as credit_exc:
        logger.warning("Credit consume failed for upload %s: %s", upload.pk, credit_exc)


def _refund_credit_for_upload(upload: DocumentUpload) -> None:
    """Refund reserved credit when extraction fails (OCR/pipeline error).

    Policy: ChargePolicy.for_ocr_failure() / for_pipeline_failure() / for_non_invoice_document() → REFUND.
    Idempotent — CreditService.refund() skips if already refunded.
    """
    if not upload.uploaded_by_id:
        return
    try:
        from apps.extraction.services.credit_service import CreditService
        from apps.extraction.credit_models import UserCreditAccount
        if not UserCreditAccount.objects.filter(user_id=upload.uploaded_by_id, reserved_credits__gt=0).exists():
            return
        CreditService.refund(
            upload.uploaded_by, credits=1,
            reference_type="document_upload",
            reference_id=str(upload.pk),
            remarks=f"Refund for failed extraction task upload_id={upload.pk}",
        )
    except Exception as credit_exc:
        logger.warning("Credit refund failed for upload %s: %s", upload.pk, credit_exc)



