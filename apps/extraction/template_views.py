"""Standalone Invoice Extraction Workbench — template views.

Provides a self-contained UI for running the extraction pipeline
(OCR → LLM → Parse → Normalize → Validate → Persist) without
triggering the full AP Case / reconciliation orchestration flow.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.enums import (
    AuditEventType,
    DocumentType,
    ExtractionApprovalStatus,
    FileProcessingState,
    InvoiceStatus,
    UserRole,
)
from apps.core.decorators import observed_action
from apps.core.permissions import permission_required_code
from apps.documents.models import DocumentUpload, Invoice, InvoiceLineItem
from apps.extraction.models import ExtractionResult

logger = logging.getLogger(__name__)

# ── Cache keys & invalidation ──────────────────────────────────
CACHE_KEY_APPROVAL_ANALYTICS = "extraction_approval_analytics"


def _invalidate_extraction_caches(user=None):
    """Clear cached workbench KPI stats and approval analytics.

    Called after extraction runs or approval actions to ensure
    the next page load sees fresh data.
    """
    if user:
        cache.delete(f"extraction_workbench_stats:{user.pk}")
    cache.delete(CACHE_KEY_APPROVAL_ANALYTICS)


def _scope_extractions_for_user(qs, user):
    """AP_PROCESSOR sees only extractions from their own uploads."""
    if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
        return qs
    return qs.filter(document_upload__uploaded_by=user)

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


# ────────────────────────────────────────────────────────────────
# Workbench — list + upload form
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("extraction.view_workbench", permission="invoices.view", entity_type="ExtractionResult")
def extraction_workbench(request):
    """Main workbench page: recent extractions + upload form."""
    qs = (
        ExtractionResult.objects
        .select_related(
            "document_upload", "invoice", "invoice__vendor",
            "extraction_run",  # avoid N+1 in get_execution_context()
        )
        .order_by("-created_at")
    )
    qs = _scope_extractions_for_user(qs, request.user)

    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(invoice__invoice_number__icontains=q)
            | Q(invoice__raw_vendor_name__icontains=q)
            | Q(document_upload__original_filename__icontains=q)
        )

    status_filter = request.GET.get("status")
    if status_filter == "success":
        qs = qs.filter(success=True)
    elif status_filter == "failed":
        qs = qs.filter(success=False)

    confidence_filter = request.GET.get("confidence")
    if confidence_filter == "high":
        qs = qs.filter(confidence__gte=0.8)
    elif confidence_filter == "medium":
        qs = qs.filter(confidence__gte=0.5, confidence__lt=0.8)
    elif confidence_filter == "low":
        qs = qs.filter(confidence__lt=0.5, confidence__isnull=False)

    # KPI stats (scoped to user visibility, cached 60s)
    from django.db.models import Avg, Count, Q as Qf
    cache_key_stats = f"extraction_workbench_stats:{request.user.pk}"
    cached = cache.get(cache_key_stats)
    if cached:
        stats = cached["stats"]
        stats_cached_at = cached["cached_at"]
    else:
        all_results = _scope_extractions_for_user(ExtractionResult.objects.all(), request.user)
        total = all_results.count()
        success = all_results.filter(success=True).count()
        failed = all_results.filter(success=False).count()
        stats = {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": round(success / total * 100, 1) if total else 0,
            "low_confidence": all_results.filter(
                success=True, confidence__lt=0.5, confidence__isnull=False
            ).count(),
            "avg_confidence": all_results.filter(
                success=True, confidence__isnull=False
            ).aggregate(avg=Avg("confidence"))["avg"] or 0,
            "avg_duration": all_results.filter(
                success=True, duration_ms__isnull=False
            ).aggregate(avg=Avg("duration_ms"))["avg"] or 0,
        }
        from django.utils import timezone as _tz
        stats_cached_at = _tz.now()
        cache.set(cache_key_stats, {"stats": stats, "cached_at": stats_cached_at}, 60)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Pre-load approval status for each result's invoice
    from apps.extraction.models import ExtractionApproval
    invoice_ids = [r.invoice_id for r in page_obj if r.invoice_id]
    approval_map = {}
    if invoice_ids:
        for ea in ExtractionApproval.objects.filter(invoice_id__in=invoice_ids):
            approval_map[ea.invoice_id] = ea

    # Pre-load execution context (governed pipeline or legacy fallback)
    from apps.extraction.services.execution_context import get_execution_context
    for r in page_obj:
        ctx = get_execution_context(r)
        r.review_queue = ctx.review_queue or ""
        r.extraction_source = ctx.source
        # Prefer deterministic confidence from invoice over LLM self-report
        if r.invoice and r.invoice.extraction_confidence is not None:
            r.confidence = r.invoice.extraction_confidence

    # ── Approval tab data ──
    from apps.extraction.services.approval_service import ExtractionApprovalService

    approval_status_filter = request.GET.get("approval_status", "ALL")
    approval_qs = (
        ExtractionApproval.objects
        .select_related(
            "invoice", "invoice__vendor", "invoice__document_upload",
            "extraction_result", "reviewed_by",
        )
        .order_by("-created_at")
    )
    if approval_status_filter and approval_status_filter != "ALL":
        approval_qs = approval_qs.filter(status=approval_status_filter)
    approval_q = request.GET.get("approval_q", "").strip()
    if approval_q:
        from django.db.models import Q as Qa
        approval_qs = approval_qs.filter(
            Qa(invoice__invoice_number__icontains=approval_q)
            | Qa(invoice__raw_vendor_name__icontains=approval_q)
        )
    approval_paginator = Paginator(approval_qs, 20)
    approval_page = approval_paginator.get_page(request.GET.get("approval_page"))

    # Approval analytics (cached 60s, global — not user-scoped)
    cached_analytics = cache.get(CACHE_KEY_APPROVAL_ANALYTICS)
    if cached_analytics:
        approval_analytics = cached_analytics["data"]
        analytics_cached_at = cached_analytics["cached_at"]
    else:
        approval_analytics = ExtractionApprovalService.get_approval_analytics()
        from django.utils import timezone as _tz
        analytics_cached_at = _tz.now()
        cache.set(CACHE_KEY_APPROVAL_ANALYTICS, {"data": approval_analytics, "cached_at": analytics_cached_at}, 60)

    active_tab = request.GET.get("tab", "runs")

    # ── Failed / rejected uploads (no ExtractionResult created) ──
    failed_uploads_qs = (
        DocumentUpload.objects
        .filter(processing_state=FileProcessingState.FAILED)
        .order_by("-updated_at")
    )
    if getattr(request.user, "role", None) == UserRole.AP_PROCESSOR:
        failed_uploads_qs = failed_uploads_qs.filter(uploaded_by=request.user)
    failed_uploads_paginator = Paginator(failed_uploads_qs, 20)
    failed_uploads_page = failed_uploads_paginator.get_page(request.GET.get("failed_page"))
    failed_uploads_count = failed_uploads_paginator.count

    # ── Credit usage summary for logged-in user ──
    from apps.extraction.services.credit_service import CreditService
    credit_summary = CreditService.get_usage_summary(request.user)

    return render(request, "extraction/workbench.html", {
        "results": page_obj,
        "page_obj": page_obj,
        "stats": stats,
        "stats_cached_at": stats_cached_at,
        "approval_map": approval_map,
        "approvals": approval_page,
        "approval_page_obj": approval_page,
        "approval_status_filter": approval_status_filter,
        "approval_analytics": approval_analytics,
        "analytics_cached_at": analytics_cached_at,
        "approval_statuses": ExtractionApprovalStatus.choices,
        "active_tab": active_tab,
        "credit_summary": credit_summary,
        "failed_uploads": failed_uploads_page,
        "failed_uploads_page_obj": failed_uploads_page,
        "failed_uploads_count": failed_uploads_count,
    })


# ────────────────────────────────────────────────────────────────
# Upload + Extract (standalone — no case creation)
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("invoices.create")  # Upload only — edit uses extraction.correct
@observed_action("extraction.upload_and_extract", permission="invoices.create", entity_type="DocumentUpload", audit_event="INVOICE_UPLOADED")
def extraction_upload(request):
    """Handle file upload and run extraction pipeline (standalone)."""
    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        messages.error(request, "No file selected.")
        return redirect("extraction:workbench")

    if uploaded_file.content_type not in ALLOWED_CONTENT_TYPES:
        messages.error(request, "Unsupported file type. Please upload a PDF, PNG, JPG, or TIFF.")
        return redirect("extraction:workbench")

    if uploaded_file.size > MAX_UPLOAD_SIZE:
        messages.error(request, "File too large. Maximum size is 20 MB.")
        return redirect("extraction:workbench")

    # ── Credit check: reserve 1 credit before proceeding ──
    from apps.extraction.services.credit_service import CreditService
    credit_check = CreditService.check_can_reserve(request.user, credits=1)
    if not credit_check.allowed:
        messages.error(request, credit_check.message)
        return redirect("extraction:workbench")

    reserve_result = CreditService.reserve(
        request.user, credits=1,
        reference_type="document_upload",
        remarks=f"Reserved for upload: {uploaded_file.name}",
    )
    if not reserve_result.allowed:
        messages.error(request, reserve_result.message)
        return redirect("extraction:workbench")

    # Compute SHA-256 hash
    sha256 = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        sha256.update(chunk)
    file_hash = sha256.hexdigest()
    uploaded_file.seek(0)

    # Create DocumentUpload record
    try:
        doc_upload = DocumentUpload.objects.create(
            original_filename=uploaded_file.name,
            file_size=uploaded_file.size,
            file_hash=file_hash,
            content_type=uploaded_file.content_type,
            document_type=DocumentType.INVOICE,
            processing_state=FileProcessingState.PROCESSING,
            uploaded_by=request.user,
        )
    except Exception as exc:
        # Refund the reserved credit — setup failed before processing
        CreditService.refund(
            request.user, credits=1,
            reference_type="document_upload",
            remarks=f"Refund: DocumentUpload creation failed — {exc}",
        )
        raise

    # Try blob upload first — required for async Celery path
    _try_blob_upload(doc_upload, uploaded_file)

    # If blob upload succeeded, dispatch via Celery task (async on server)
    # Credit consume/refund happens inside the Celery task
    if doc_upload.blob_path:
        from apps.extraction.tasks import process_invoice_upload_task
        from apps.core.utils import dispatch_task
        dispatch_task(process_invoice_upload_task, upload_id=doc_upload.pk)
        _invalidate_extraction_caches(request.user)
        messages.success(
            request,
            f"'{uploaded_file.name}' uploaded successfully. "
            f"Extraction agent is processing — refresh to see results."
        )
        return redirect("extraction:workbench")

    # Fallback: no blob storage — run extraction synchronously with local file
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=os.path.splitext(uploaded_file.name)[1]
    )
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_f:
            for chunk in uploaded_file.chunks():
                tmp_f.write(chunk)

        result = _run_extraction_pipeline(doc_upload, tmp_path)

        if result["success"]:
            # Consume the reserved credit — extraction succeeded
            CreditService.consume(
                request.user, credits=1,
                reference_type="document_upload",
                reference_id=str(doc_upload.pk),
                remarks=f"Consumed for successful extraction: {uploaded_file.name}",
            )
            _invalidate_extraction_caches(request.user)
            messages.success(
                request,
                f"Extraction completed for '{uploaded_file.name}' — "
                f"confidence {result['confidence']:.0%}."
            )
            return redirect("extraction:console", pk=result["extraction_result_id"])
        else:
            # Extraction attempted but failed — refund credit (OCR failure)
            CreditService.refund(
                request.user, credits=1,
                reference_type="document_upload",
                reference_id=str(doc_upload.pk),
                remarks=f"Refund (extraction failed): {uploaded_file.name}",
            )
            _invalidate_extraction_caches(request.user)
            messages.error(
                request,
                f"Extraction failed for '{uploaded_file.name}': {result['error']}"
            )
            return redirect("extraction:workbench")
    except Exception as exc:
        # Technical failure before pipeline truly started — refund
        try:
            CreditService.refund(
                request.user, credits=1,
                reference_type="document_upload",
                reference_id=str(doc_upload.pk),
                remarks=f"Refund: sync extraction setup failed — {exc}",
            )
        except Exception:
            logger.exception("Credit refund failed during sync extraction error")
        raise
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _try_blob_upload(doc_upload: DocumentUpload, uploaded_file) -> None:
    """Attempt Azure Blob upload — non-fatal if blob storage is not configured."""
    try:
        from apps.documents.blob_service import is_blob_storage_enabled, upload_to_blob, build_blob_path
        if not is_blob_storage_enabled():
            return

        from django.conf import settings
        from django.utils import timezone as tz
        container_name = getattr(settings, "AZURE_BLOB_CONTAINER_NAME", "")
        blob_path = build_blob_path("input", uploaded_file.name, doc_upload.pk)
        uploaded_file.seek(0)
        upload_to_blob(uploaded_file, blob_path, content_type=uploaded_file.content_type)
        doc_upload.blob_path = blob_path
        doc_upload.blob_container = container_name
        doc_upload.blob_name = blob_path
        doc_upload.blob_uploaded_at = tz.now()
        doc_upload.save(update_fields=[
            "blob_path", "blob_container", "blob_name",
            "blob_uploaded_at", "updated_at",
        ])
    except Exception as exc:
        logger.warning("Blob upload skipped (non-fatal): %s", exc)


def _run_extraction_pipeline(upload: DocumentUpload, file_path: str) -> dict:
    """Run the extraction pipeline on a local file — returns result dict.

    This is a simplified version of process_invoice_upload_task that:
    - Runs OCR + LLM extraction
    - Parses, normalizes, validates
    - Persists Invoice + ExtractionResult
    - Does NOT create AP Cases or trigger reconciliation
    """
    from apps.extraction.services.extraction_adapter import InvoiceExtractionAdapter
    from apps.extraction.services.parser_service import ExtractionParserService
    from apps.extraction.services.normalization_service import NormalizationService
    from apps.extraction.services.validation_service import ValidationService
    from apps.extraction.services.duplicate_detection_service import DuplicateDetectionService
    from apps.extraction.services.persistence_service import (
        InvoicePersistenceService,
        ExtractionResultPersistenceService,
    )

    try:
        # 1. Extract (OCR + LLM)
        adapter = InvoiceExtractionAdapter()
        extraction_resp = adapter.extract(file_path)

        if not extraction_resp.success:
            upload.processing_state = FileProcessingState.FAILED
            upload.processing_message = extraction_resp.error_message[:2000]
            upload.save(update_fields=["processing_state", "processing_message", "updated_at"])
            ExtractionResultPersistenceService.save(upload, None, extraction_resp)
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="DocumentUpload",
                entity_id=upload.pk,
                event_type=AuditEventType.EXTRACTION_FAILED,
                description=f"Extraction failed for '{upload.original_filename}': {extraction_resp.error_message[:200]}",
                user=upload.uploaded_by,
                metadata={"source": "extraction_workbench", "error": extraction_resp.error_message[:500]},
            )
            return {"success": False, "error": extraction_resp.error_message}

        # 2. Parse
        parsed = ExtractionParserService().parse(extraction_resp.raw_json)

        # 3. Normalize
        normalized = NormalizationService().normalize(parsed)

        # 4. Validate
        validation_result = ValidationService().validate(normalized)

        # 5. Duplicate check — exclude the existing invoice for this upload so a
        # reprocess does not flag the invoice as a duplicate of itself.
        _existing_inv_id = (
            Invoice.objects
            .filter(document_upload=upload)
            .order_by("-created_at")
            .values_list("pk", flat=True)
            .first()
        )
        dup_result = DuplicateDetectionService().check(normalized, exclude_invoice_id=_existing_inv_id)

        # 6. Persist (Invoice + LineItems + ExtractionResult)
        invoice = InvoicePersistenceService().save(
            normalized=normalized,
            upload=upload,
            extraction_raw_json=extraction_resp.raw_json,
            validation_result=validation_result,
            duplicate_result=dup_result,
        )
        ext_result = ExtractionResultPersistenceService.save(upload, invoice, extraction_resp)

        # 7. Finalize upload state
        upload.processing_state = FileProcessingState.COMPLETED
        upload.save(update_fields=["processing_state", "updated_at"])

        # 8. Gate through extraction approval
        from apps.extraction.services.approval_service import ExtractionApprovalService

        if validation_result.is_valid and not dup_result.is_duplicate:
            auto_approval = ExtractionApprovalService.try_auto_approve(invoice, ext_result)
            if not auto_approval:
                invoice.status = InvoiceStatus.PENDING_APPROVAL
                invoice.save(update_fields=["status", "updated_at"])
                ExtractionApprovalService.create_pending_approval(invoice, ext_result)
        else:
            # Validation issues or duplicate — still create PENDING approval
            # so the human reviewer can see and decide.
            invoice.status = InvoiceStatus.PENDING_APPROVAL
            invoice.save(update_fields=["status", "updated_at"])
            ExtractionApprovalService.create_pending_approval(invoice, ext_result)

        # Audit log
        from apps.auditlog.services import AuditService
        AuditService.log_event(
            entity_type="Invoice",
            entity_id=invoice.pk,
            event_type=AuditEventType.EXTRACTION_COMPLETED,
            description=(
                f"Standalone extraction completed for '{upload.original_filename}' → "
                f"Invoice {invoice.invoice_number} (confidence: {invoice.extraction_confidence})"
            ),
            user=upload.uploaded_by,
            metadata={
                "upload_id": upload.pk,
                "source": "extraction_workbench",
                "is_valid": validation_result.is_valid,
                "is_duplicate": dup_result.is_duplicate,
            },
        )

        return {
            "success": True,
            "invoice_id": invoice.pk,
            "extraction_result_id": ext_result.pk,
            "confidence": invoice.extraction_confidence or 0,
        }

    except Exception as exc:
        logger.exception("Standalone extraction failed for upload %s", upload.pk)
        upload.processing_state = FileProcessingState.FAILED
        upload.processing_message = str(exc)[:2000]
        upload.save(update_fields=["processing_state", "processing_message", "updated_at"])
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="DocumentUpload",
                entity_id=upload.pk,
                event_type=AuditEventType.EXTRACTION_FAILED,
                description=f"Extraction pipeline exception for '{upload.original_filename}': {str(exc)[:200]}",
                user=upload.uploaded_by,
                metadata={"source": "extraction_workbench", "error": str(exc)[:500]},
            )
        except Exception:
            pass
        return {"success": False, "error": str(exc)}


# ────────────────────────────────────────────────────────────────
# Result detail
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("extraction.view_result_detail", permission="invoices.view", entity_type="ExtractionResult")
def extraction_result_detail(request, pk):
    """Redirect to the unified console — result_detail is merged into console."""
    get_object_or_404(ExtractionResult, pk=pk)  # 404 check
    return redirect("extraction:console", pk=pk)


# ────────────────────────────────────────────────────────────────
# JSON download
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("extraction.download_json", permission="invoices.view", entity_type="ExtractionResult")
def extraction_result_json(request, pk):
    """Return extraction result raw JSON as downloadable file."""
    ext = get_object_or_404(ExtractionResult, pk=pk)
    if not ext.raw_response:
        raise Http404("No raw extraction data available.")

    response = JsonResponse(ext.raw_response, json_dumps_params={"indent": 2})
    filename = f"extraction_{ext.pk}.json"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ────────────────────────────────────────────────────────────────
# View PDF
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
def extraction_view_pdf(request, pk):
    """Redirect to the source PDF for an extraction result."""
    ext = get_object_or_404(
        ExtractionResult.objects.select_related("document_upload"),
        pk=pk,
    )
    upload = ext.document_upload
    if not upload:
        raise Http404("No upload record found.")

    # Option 1: Local file exists — serve via media URL
    if upload.file and hasattr(upload.file, 'url'):
        try:
            return redirect(upload.file.url)
        except Exception:
            pass

    # Option 2: Azure Blob — generate a time-limited SAS URL
    if upload.blob_path:
        try:
            from apps.documents.blob_service import generate_blob_sas_url
            sas_url = generate_blob_sas_url(upload.blob_path, expiry_minutes=30)
            return redirect(sas_url)
        except Exception:
            pass

    # Option 3: Direct blob_url stored on the model
    if upload.blob_url:
        return redirect(upload.blob_url)

    raise Http404("Source document is not available.")


# ────────────────────────────────────────────────────────────────
# Re-extract
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("extraction.reprocess")
@observed_action("extraction.rerun", permission="extraction.reprocess", entity_type="ExtractionResult", audit_event="EXTRACTION_STARTED")
def extraction_rerun(request, pk):
    """Re-run extraction on an existing upload's blob."""
    ext = get_object_or_404(
        ExtractionResult.objects.select_related("document_upload"),
        pk=pk,
    )
    upload = ext.document_upload
    if not upload:
        messages.error(request, "No upload record found for this extraction.")
        return redirect("extraction:workbench")

    if not upload.blob_path:
        messages.error(request, "Original document is not available for re-extraction (no blob path).")
        return redirect("extraction:console", pk=pk)

    # ── Guard: prevent reprocess if approval is already finalized ──
    if ext.invoice_id:
        from apps.extraction.models import ExtractionApproval
        from apps.core.enums import ExtractionApprovalStatus
        finalized = ExtractionApproval.objects.filter(
            invoice_id=ext.invoice_id,
            status__in=[ExtractionApprovalStatus.APPROVED, ExtractionApprovalStatus.AUTO_APPROVED],
        ).exists()
        if finalized:
            messages.error(request, "Cannot reprocess — extraction has already been approved.")
            return redirect("extraction:console", pk=pk)

    # Capture reprocess reason from modal form
    reason = request.POST.get("reason", "")
    details = request.POST.get("details", "")

    # ── Credit: reserve 1 credit for reprocess (ChargePolicy.for_reprocess -> CONSUME) ──
    # Use a unique reference_id per attempt so each reprocess is a distinct charge
    import time as _time
    _credit_ref_id = f"reprocess-{upload.pk}-{int(_time.time())}"
    from apps.extraction.services.credit_service import CreditService
    credit_check = CreditService.check_can_reserve(request.user, credits=1)
    if not credit_check.allowed:
        messages.error(request, credit_check.message)
        return redirect("extraction:console", pk=pk)

    reserve_result = CreditService.reserve(
        request.user, credits=1,
        reference_type="reprocess",
        reference_id=_credit_ref_id,
        remarks=f"Reserved for reprocess: upload_id={upload.pk}",
    )
    if not reserve_result.allowed:
        messages.error(request, reserve_result.message)
        return redirect("extraction:console", pk=pk)

    # Log reprocess reason to audit trail before dispatching
    from apps.auditlog.services import AuditService
    from apps.core.enums import AuditEventType
    AuditService.log_event(
        entity_type="ExtractionResult",
        entity_id=ext.pk,
        event_type=AuditEventType.EXTRACTION_REPROCESSED,
        description=f"Extraction reprocess triggered: {reason or 'no reason provided'}",
        user=request.user,
        invoice_id=ext.invoice_id,
        metadata={
            "reason": reason,
            "details": details[:500] if details else "",
            "previous_extraction_id": ext.pk,
        },
    )

    # Reset upload state and dispatch via Celery task
    upload.processing_state = FileProcessingState.PROCESSING
    upload.save(update_fields=["processing_state", "updated_at"])

    from apps.extraction.tasks import process_invoice_upload_task
    from apps.core.utils import dispatch_task
    dispatch_task(
        process_invoice_upload_task,
        upload_id=upload.pk,
        credit_ref_type="reprocess",
        credit_ref_id=_credit_ref_id,
    )
    _invalidate_extraction_caches(request.user)

    messages.success(
        request,
        "Re-extraction started. The agent is processing — refresh to see results."
    )
    return redirect("extraction:workbench")


# ────────────────────────────────────────────────────────────────
# AJAX filter endpoint (returns table HTML partial)
# ────────────────────────────────────────────────────────────────
@login_required
@require_GET
@permission_required_code("invoices.view")
@observed_action("extraction.ajax_filter", permission="invoices.view", entity_type="ExtractionResult")
def extraction_ajax_filter(request):
    """Return filtered extraction results as JSON for AJAX table refresh."""
    qs = (
        ExtractionResult.objects
        .select_related(
            "document_upload", "invoice", "invoice__vendor",
            "extraction_run",
        )
        .order_by("-created_at")
    )
    qs = _scope_extractions_for_user(qs, request.user)

    # Text search
    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(invoice__invoice_number__icontains=q)
            | Q(invoice__raw_vendor_name__icontains=q)
            | Q(document_upload__original_filename__icontains=q)
        )

    # Status filter
    status_filter = request.GET.get("status")
    if status_filter == "success":
        qs = qs.filter(success=True)
    elif status_filter == "failed":
        qs = qs.filter(success=False)

    # Confidence filters
    confidence_filter = request.GET.get("confidence")
    if confidence_filter == "high":
        qs = qs.filter(confidence__gte=0.8)
    elif confidence_filter == "medium":
        qs = qs.filter(confidence__gte=0.5, confidence__lt=0.8)
    elif confidence_filter == "low":
        qs = qs.filter(confidence__lt=0.5, confidence__isnull=False)

    # Custom confidence threshold (slider value 0-100)
    min_conf = request.GET.get("min_confidence")
    max_conf = request.GET.get("max_confidence")
    if min_conf:
        try:
            qs = qs.filter(confidence__gte=int(min_conf) / 100.0)
        except (ValueError, TypeError):
            pass
    if max_conf:
        try:
            qs = qs.filter(confidence__lte=int(max_conf) / 100.0)
        except (ValueError, TypeError):
            pass

    # Date range
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    if date_from:
        try:
            qs = qs.filter(created_at__date__gte=datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError:
            pass
    if date_to:
        try:
            qs = qs.filter(created_at__date__lte=datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError:
            pass

    # Build JSON response
    rows = []
    for r in qs[:200]:
        inv = r.invoice
        rows.append({
            "pk": r.pk,
            "filename": r.document_upload.original_filename if r.document_upload else "—",
            "invoice_number": inv.invoice_number if inv else "",
            "vendor": (inv.vendor.name if inv and inv.vendor else inv.raw_vendor_name if inv else ""),
            "currency": inv.currency if inv else "",
            "total_amount": str(inv.total_amount) if inv and inv.total_amount else "",
            "confidence": round(r.confidence * 100) if r.confidence is not None else None,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "engine_name": r.engine_name or "azure_di_gpt4o",
            "invoice_status": inv.status if inv else "",
            "created_at": r.created_at.strftime("%d %b %Y %H:%M") if r.created_at else "",
        })

    return JsonResponse({"results": rows, "total": len(rows)})


# ────────────────────────────────────────────────────────────────
# CSV Export
# ────────────────────────────────────────────────────────────────
@login_required
@require_GET
@permission_required_code("invoices.view")
@observed_action("extraction.export_csv", permission="invoices.view", entity_type="ExtractionResult")
def extraction_export_csv(request):
    """Export extraction results to CSV."""
    qs = (
        ExtractionResult.objects
        .select_related(
            "document_upload", "invoice", "invoice__vendor",
            "extraction_run",
        )
        .order_by("-created_at")
    )

    # Apply same filters as workbench
    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(invoice__invoice_number__icontains=q)
            | Q(invoice__raw_vendor_name__icontains=q)
            | Q(document_upload__original_filename__icontains=q)
        )

    status_filter = request.GET.get("status")
    if status_filter == "success":
        qs = qs.filter(success=True)
    elif status_filter == "failed":
        qs = qs.filter(success=False)

    confidence_filter = request.GET.get("confidence")
    if confidence_filter == "high":
        qs = qs.filter(confidence__gte=0.8)
    elif confidence_filter == "medium":
        qs = qs.filter(confidence__gte=0.5, confidence__lt=0.8)
    elif confidence_filter == "low":
        qs = qs.filter(confidence__lt=0.5, confidence__isnull=False)

    min_conf = request.GET.get("min_confidence")
    max_conf = request.GET.get("max_confidence")
    if min_conf:
        try:
            qs = qs.filter(confidence__gte=int(min_conf) / 100.0)
        except (ValueError, TypeError):
            pass
    if max_conf:
        try:
            qs = qs.filter(confidence__lte=int(max_conf) / 100.0)
        except (ValueError, TypeError):
            pass

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="extraction_results.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "ID", "Filename", "Invoice Number", "Vendor", "Currency",
        "Subtotal", "Tax", "Total Amount", "PO Number",
        "Confidence %", "Status", "Duration (ms)", "Engine",
        "Extracted At",
    ])

    for r in qs:
        inv = r.invoice
        writer.writerow([
            r.pk,
            r.document_upload.original_filename if r.document_upload else "",
            inv.invoice_number if inv else "",
            (inv.vendor.name if inv and inv.vendor else inv.raw_vendor_name if inv else ""),
            inv.currency if inv else "",
            str(inv.subtotal or "") if inv else "",
            str(inv.tax_amount or "") if inv else "",
            str(inv.total_amount or "") if inv else "",
            inv.po_number if inv else "",
            f"{r.confidence * 100:.0f}" if r.confidence is not None else "",
            "OK" if r.success else "FAIL",
            r.duration_ms or "",
            r.engine_name or "",
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        ])

    return response


# ────────────────────────────────────────────────────────────────
# Inline edit extracted invoice fields
# ────────────────────────────────────────────────────────────────
EDITABLE_HEADER_FIELDS = {
    "invoice_number", "po_number", "invoice_date", "due_date", "currency",
    "vendor_tax_id", "buyer_name",
    "subtotal", "tax_percentage", "tax_amount", "total_amount",
}
EDITABLE_LINE_FIELDS = {
    "description", "quantity", "unit_price", "tax_percentage", "tax_amount", "line_amount",
}


@login_required
@require_POST
@permission_required_code("extraction.correct")
@observed_action(
    "extraction.edit_extracted_values",
    permission="extraction.correct",
    entity_type="Invoice",
)
def extraction_edit_values(request, pk):
    """Accept corrected values for a low-confidence extraction result."""
    ext = get_object_or_404(
        ExtractionResult.objects.select_related("invoice"),
        pk=pk,
    )
    if not ext.invoice:
        return JsonResponse({"ok": False, "error": "No invoice linked to this extraction."}, status=400)

    invoice = ext.invoice

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    # Fetch linked approval (if any) for field-correction tracking
    from apps.extraction.models import ExtractionApproval
    from apps.extraction.models import ExtractionFieldCorrection

    approval = ExtractionApproval.objects.filter(invoice=invoice).first()
    correction_records = []

    # ── Update header fields ───────────────────────────────
    header = payload.get("header", {})
    changed_fields = []
    update_fields = ["updated_at"]

    for field_name, value in header.items():
        if field_name not in EDITABLE_HEADER_FIELDS:
            continue
        old_value = str(getattr(invoice, field_name, "") or "")
        value = str(value).strip()

        if old_value == value:
            continue

        if field_name == "invoice_date":
            try:
                parsed_date = datetime.strptime(value, "%Y-%m-%d").date() if value else None
                setattr(invoice, field_name, parsed_date)
            except ValueError:
                continue
        elif field_name == "due_date":
            try:
                parsed_date = datetime.strptime(value, "%Y-%m-%d").date() if value else None
                setattr(invoice, field_name, parsed_date)
            except ValueError:
                continue
        elif field_name in ("subtotal", "tax_percentage", "tax_amount", "total_amount"):
            try:
                setattr(invoice, field_name, Decimal(value) if value else None)
            except InvalidOperation:
                continue
        else:
            setattr(invoice, field_name, value)

        changed_fields.append(field_name)
        update_fields.append(field_name)

        if approval:
            correction_records.append(ExtractionFieldCorrection(
                approval=approval,
                entity_type="header",
                entity_id=None,
                field_name=field_name,
                original_value=old_value,
                corrected_value=value,
                corrected_by=request.user,
            ))

    if changed_fields:
        invoice.save(update_fields=update_fields)

    # ── Update line items ──────────────────────────────────
    lines_payload = payload.get("lines", [])
    lines_changed = 0
    for line_data in lines_payload:
        line_pk = line_data.get("pk")
        if not line_pk:
            continue
        try:
            line_item = InvoiceLineItem.objects.get(pk=line_pk, invoice=invoice)
        except InvoiceLineItem.DoesNotExist:
            continue

        line_update_fields = ["updated_at"]
        for field_name, value in line_data.items():
            if field_name in ("pk",):
                continue
            if field_name not in EDITABLE_LINE_FIELDS:
                continue
            old_value = str(getattr(line_item, field_name, "") or "")
            value = str(value).strip()

            if old_value == value:
                continue

            if field_name in ("quantity", "unit_price", "tax_percentage", "tax_amount", "line_amount"):
                try:
                    setattr(line_item, field_name, Decimal(value) if value else None)
                except InvalidOperation:
                    continue
            else:
                setattr(line_item, field_name, value)

            line_update_fields.append(field_name)

            if approval:
                correction_records.append(ExtractionFieldCorrection(
                    approval=approval,
                    entity_type="line_item",
                    entity_id=line_item.pk,
                    field_name=field_name,
                    original_value=old_value,
                    corrected_value=value,
                    corrected_by=request.user,
                ))

        if len(line_update_fields) > 1:
            line_item.save(update_fields=line_update_fields)
            lines_changed += 1

    # ── Persist field correction records ───────────────────
    if correction_records:
        ExtractionFieldCorrection.objects.bulk_create(correction_records)

    # Build field change details for audit
    field_changes = []
    for cr in correction_records:
        field_changes.append({
            "field": cr.field_name,
            "entity": cr.entity_type,
            "old": cr.original_value,
            "new": cr.corrected_value,
        })

    # ── Audit log ──────────────────────────────────────────
    from apps.auditlog.services import AuditService
    AuditService.log_event(
        entity_type="Invoice",
        entity_id=invoice.pk,
        event_type=AuditEventType.FIELD_CORRECTED,
        description=(
            f"Manual correction of extracted values for Invoice {invoice.invoice_number}: "
            f"header fields={changed_fields}, lines changed={lines_changed}"
        ),
        user=request.user,
        invoice_id=invoice.pk,
        metadata={
            "source": "extraction_console",
            "action": "manual_edit",
            "header_fields_changed": changed_fields,
            "lines_changed": lines_changed,
            "corrections_tracked": len(correction_records),
            "extraction_result_id": ext.pk,
            "field_changes": field_changes,
        },
    )

    return JsonResponse({
        "ok": True,
        "header_fields_changed": changed_fields,
        "lines_changed": lines_changed,
        "corrections_tracked": len(correction_records),
    })


# ────────────────────────────────────────────────────────────────
# Extraction Approval Queue
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
def extraction_approval_queue(request):
    """Redirect to workbench Approvals tab (backward-compatible URL)."""
    from django.urls import reverse
    from urllib.parse import urlencode

    params = {"tab": "approvals"}
    # Forward approval-specific query params
    for key in ("approval_status", "approval_q", "approval_page"):
        val = request.GET.get(key)
        if val:
            params[key] = val
    # Also map old param names for backward compat
    old_status = request.GET.get("status")
    if old_status and "approval_status" not in params:
        params["approval_status"] = old_status
    old_q = request.GET.get("q")
    if old_q and "approval_q" not in params:
        params["approval_q"] = old_q

    return redirect(f"{reverse('extraction:workbench')}?{urlencode(params)}")


# ────────────────────────────────────────────────────────────────
# Extraction Approval Detail — review + approve/reject
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("extraction.view_approval_detail", permission="invoices.view", entity_type="ExtractionApproval")
def extraction_approval_detail(request, pk):
    """Detail view for reviewing a single extraction before approval."""
    from apps.extraction.models import ExtractionApproval

    approval = get_object_or_404(
        ExtractionApproval.objects.select_related(
            "invoice", "invoice__vendor", "invoice__document_upload",
            "extraction_result", "reviewed_by",
        ),
        pk=pk,
    )
    invoice = approval.invoice
    line_items = list(invoice.line_items.order_by("line_number")) if invoice else []
    corrections = list(approval.corrections.order_by("entity_type", "field_name"))

    has_line_tax = any(
        li.tax_amount and li.tax_amount != 0 for li in line_items
    )

    # Re-run validation for display
    validation_issues = []
    ext = approval.extraction_result
    if ext and ext.raw_response:
        try:
            from apps.extraction.services.parser_service import ExtractionParserService
            from apps.extraction.services.normalization_service import NormalizationService
            from apps.extraction.services.validation_service import ValidationService

            parsed = ExtractionParserService().parse(ext.raw_response)
            normalized = NormalizationService().normalize(parsed)
            val_result = ValidationService().validate(normalized)
            validation_issues = [
                {"field": v.field, "severity": v.severity, "message": v.message}
                for v in val_result.issues
            ]
        except Exception:
            pass

    raw_json_pretty = ""
    if ext and ext.raw_response:
        raw_json_pretty = json.dumps(ext.raw_response, indent=2, default=str)

    return render(request, "extraction/approval_detail.html", {
        "approval": approval,
        "invoice": invoice,
        "line_items": line_items,
        "has_line_tax": has_line_tax,
        "corrections": corrections,
        "validation_issues": validation_issues,
        "raw_json_pretty": raw_json_pretty,
        "is_pending": approval.status == ExtractionApprovalStatus.PENDING,
    })


# ────────────────────────────────────────────────────────────────
# Approve extraction
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("extraction.approve")
@observed_action(
    "extraction.approve_extraction",
    permission="extraction.approve",
    entity_type="ExtractionApproval",
)
def extraction_approve(request, pk):
    """Approve an extraction, optionally with field corrections."""
    from apps.extraction.models import ExtractionApproval
    from apps.extraction.services.approval_service import ExtractionApprovalService

    approval = get_object_or_404(ExtractionApproval, pk=pk)

    try:
        payload = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}

    corrections = None
    if payload.get("header") or payload.get("lines"):
        corrections = payload

    try:
        ExtractionApprovalService.approve(approval, request.user, corrections)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    # If this came from the full pipeline, trigger case creation now
    invoice = approval.invoice
    if invoice.status == InvoiceStatus.READY_FOR_RECON:
        try:
            from apps.cases.services.case_creation_service import CaseCreationService
            from apps.cases.tasks import process_case_task
            from apps.core.utils import dispatch_task

            case = CaseCreationService.create_from_upload(
                invoice=invoice,
                uploaded_by=invoice.document_upload.uploaded_by if invoice.document_upload else None,
            )
            dispatch_task(process_case_task, case_id=case.pk)
            logger.info("Created AP Case %s after extraction approval for invoice %s", case.case_number, invoice.invoice_number)
        except Exception as exc:
            logger.exception("AP Case creation failed after approval for invoice %s: %s", invoice.pk, exc)

    _invalidate_extraction_caches(request.user)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "status": "APPROVED"})

    messages.success(request, f"Extraction approved for Invoice {invoice.invoice_number}.")
    return redirect("extraction:approval_queue")


# ────────────────────────────────────────────────────────────────
# Reject extraction
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("extraction.reject")
@observed_action(
    "extraction.reject_extraction",
    permission="extraction.reject",
    entity_type="ExtractionApproval",
)
def extraction_reject(request, pk):
    """Reject an extraction."""
    from apps.extraction.models import ExtractionApproval
    from apps.extraction.services.approval_service import ExtractionApprovalService

    approval = get_object_or_404(ExtractionApproval, pk=pk)

    try:
        payload = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}

    reason = payload.get("reason", request.POST.get("reason", ""))

    try:
        ExtractionApprovalService.reject(approval, request.user, reason)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    _invalidate_extraction_caches(request.user)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "status": "REJECTED"})

    messages.info(request, f"Extraction rejected for Invoice {approval.invoice.invoice_number}.")
    return redirect("extraction:approval_queue")


# ────────────────────────────────────────────────────────────────
# Extraction Approval Analytics API (JSON)
# ────────────────────────────────────────────────────────────────
@login_required
@require_GET
@permission_required_code("invoices.view")
@observed_action("extraction.view_approval_analytics", permission="invoices.view", entity_type="ExtractionApproval")
def extraction_approval_analytics(request):
    """Return extraction approval analytics as JSON."""
    from apps.extraction.services.approval_service import ExtractionApprovalService

    analytics = ExtractionApprovalService.get_approval_analytics()
    return JsonResponse(analytics)


# ────────────────────────────────────────────────────────────────
# Extraction Review Console — agentic deep-dive UI
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("extraction.view_console", permission="invoices.view", entity_type="ExtractionResult")
def extraction_console(request, pk):
    """Agentic extraction review console — full inspection UI."""
    ext = get_object_or_404(
        ExtractionResult.objects.select_related(
            "document_upload", "document_upload__uploaded_by",
            "invoice", "invoice__vendor",
        ),
        pk=pk,
    )

    invoice = ext.invoice
    line_items = []
    header_fields = {}
    tax_fields = {}
    parties = {}
    enrichment = None
    evidence_entries = []
    reasoning_blocks = []
    audit_events = []
    validation_issues = []
    errors = []
    warnings = []
    passed_checks = []
    pipeline_stages = []

    extracted_data = ext.raw_response or {}

    # Prefer deterministic confidence from invoice over LLM self-report
    if invoice and invoice.extraction_confidence is not None:
        ext.confidence = invoice.extraction_confidence

    # ── Header / tax / line items from invoice ──
    if invoice:
        line_items_qs = list(invoice.line_items.order_by("line_number"))

        # Build header fields dict
        _header_map = [
            ("invoice_number", "Invoice Number", True),
            ("po_number", "PO Number", False),
            ("invoice_date", "Invoice Date", True),
            ("due_date", "Due Date", False),
            ("vendor_tax_id", "Vendor Tax ID (GSTIN/VAT)", False),
            ("buyer_name", "Buyer / Bill To", False),
            ("currency", "Currency", True),
            ("subtotal", "Subtotal", False),
            ("tax_percentage", "Tax Rate %", False),
            ("tax_amount", "Tax Amount", False),
            ("total_amount", "Total Amount", True),
        ]
        for attr, display, mandatory in _header_map:
            val = getattr(invoice, attr, None)
            raw_attr = f"raw_{attr}" if hasattr(invoice, f"raw_{attr}") else None
            raw_val = getattr(invoice, raw_attr) if raw_attr else None
            header_fields[attr] = {
                "display_name": display,
                "value": str(val) if val is not None else "",
                "raw_value": str(raw_val) if raw_val else None,
                "confidence": ext.confidence,
                "method": "LLM",
                "is_mandatory": mandatory,
                "evidence": True,
            }

        # Tax fields
        _tax_breakdown = getattr(invoice, "tax_breakdown", None) or {}
        _tax_map = [
            ("tax_percentage", "Tax Rate %"),
            ("tax_amount", "Tax Amount"),
            ("currency", "Currency"),
        ]
        for attr, display in _tax_map:
            val = getattr(invoice, attr, None)
            tax_fields[f"tax_{attr}"] = {
                "display_name": display,
                "value": str(val) if val is not None else "",
                "confidence": ext.confidence,
                "method": "LLM",
                "is_mandatory": False,
                "evidence": True,
            }
        # Add individual breakdown components
        _breakdown_labels = {
            "cgst": "CGST",
            "sgst": "SGST",
            "igst": "IGST",
            "vat": "VAT",
        }
        for key, label in _breakdown_labels.items():
            bval = _tax_breakdown.get(key, 0)
            if bval:
                tax_fields[f"breakdown_{key}"] = {
                    "display_name": f"  {label}",
                    "value": str(bval),
                    "confidence": ext.confidence,
                    "method": "LLM",
                    "is_mandatory": False,
                    "evidence": False,
                }

        # Build line items list for template
        line_items = []
        for li in line_items_qs:
            line_items.append({
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "tax_percentage": li.tax_percentage,
                "tax_amount": li.tax_amount,
                "total": li.line_amount,
                "confidence": ext.confidence,
                "fields": {
                    "HSN/SAC": getattr(li, "hsn_sac_code", ""),
                    "UOM": getattr(li, "uom", ""),
                    "Line Number": li.line_number,
                },
            })

    # ── Line item totals for template footer ──
    from decimal import Decimal, InvalidOperation
    def _to_dec(v):
        if v is None:
            return Decimal(0)
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            return Decimal(0)

    line_items_totals = {
        "quantity": sum(_to_dec(li.get("quantity")) for li in line_items),
        "tax_amount": sum(_to_dec(li.get("tax_amount")) for li in line_items),
        "total": sum(_to_dec(li.get("total")) for li in line_items),
    }

    # ── Enrichment data from raw_response or invoice context ──
    if isinstance(extracted_data, dict):
        enrichment = extracted_data.get("enrichment")
    # Fallback: build enrichment from invoice's linked vendor + PO
    if not enrichment and invoice:
        vendor = getattr(invoice, "vendor", None)
        po_num = invoice.po_number
        vendor_match = {"match_type": "NOT_FOUND"}
        if vendor:
            vendor_match = {
                "match_type": "EXACT",
                "entity_name": vendor.name if hasattr(vendor, "name") else str(vendor),
                "entity_code": getattr(vendor, "vendor_code", ""),
            }
        elif invoice.raw_vendor_name:
            vendor_match = {
                "match_type": "RAW",
                "entity_name": invoice.raw_vendor_name,
                "entity_code": "",
            }
        po_lookup = {"found": False}
        if po_num:
            from apps.documents.models import PurchaseOrder
            po = PurchaseOrder.objects.filter(po_number=po_num).first()
            if po:
                po_lookup = {
                    "found": True,
                    "po_number": po.po_number,
                    "po_status": getattr(po, "status", ""),
                    "currency": getattr(po, "currency", ""),
                    "total_amount": float(po.total_amount) if po.total_amount else 0,
                }
        enrichment = {
            "vendor_match": vendor_match,
            "customer_match": {"match_type": "NOT_FOUND"},
            "po_lookup": po_lookup,
        }

    # ── Parties from document intelligence or raw_response fallback ──
    if isinstance(extracted_data, dict):
        intelligence = extracted_data.get("document_intelligence", {})
        if isinstance(intelligence, dict):
            raw_parties = intelligence.get("parties", {})
            if isinstance(raw_parties, dict):
                parties = raw_parties
    # Fallback: build parties from raw_response vendor_name
    if not parties and isinstance(extracted_data, dict):
        vendor_name = extracted_data.get("vendor_name") or (
            invoice.raw_vendor_name if invoice else None
        )
        buyer = extracted_data.get("buyer_name") or (
            invoice.buyer_name if invoice else None
        )
        vendor_tax = extracted_data.get("vendor_tax_id") or (
            invoice.vendor_tax_id if invoice else None
        )
        if vendor_name:
            parties = {
                "supplier": [{
                    "name": vendor_name,
                    "tax_id": vendor_tax or "",
                    "confidence": ext.confidence,
                }],
            }
        if buyer:
            parties.setdefault("buyer", []).append({
                "name": buyer,
                "confidence": ext.confidence,
            })

    # ── Validation re-run ──
    validated_fields = set()
    if ext.raw_response:
        try:
            from apps.extraction.services.parser_service import ExtractionParserService
            from apps.extraction.services.normalization_service import NormalizationService
            from apps.extraction.services.validation_service import ValidationService

            parsed = ExtractionParserService().parse(ext.raw_response)
            normalized = NormalizationService().normalize(parsed)
            val_result = ValidationService().validate(normalized)

            flagged_fields = set()
            for v in val_result.issues:
                issue = {
                    "title": v.field or "General",
                    "message": v.message,
                    "rule_code": getattr(v, "rule_code", ""),
                    "affected_fields": [v.field] if v.field else [],
                }
                if v.field:
                    flagged_fields.add(v.field)
                if v.severity == "error":
                    errors.append(issue)
                else:
                    warnings.append(issue)

            # Build passed_checks: fields that were checked but have no issues
            _all_checkable = [
                ("invoice_number", "Invoice number present"),
                ("invoice_date", "Invoice date valid"),
                ("total_amount", "Total amount present"),
                ("currency", "Currency code valid"),
                ("po_number", "PO number format valid"),
                ("subtotal", "Subtotal present"),
                ("tax_amount", "Tax amount present"),
                ("line_items", "Line items structure valid"),
            ]
            for field_key, title in _all_checkable:
                if field_key not in flagged_fields:
                    val = extracted_data.get(field_key) if isinstance(extracted_data, dict) else None
                    if val is None and invoice:
                        val = getattr(invoice, field_key, None)
                    if val is not None and val != "" and val != []:
                        passed_checks.append({"title": title})
        except Exception:
            pass

    error_count = len(errors)
    warning_count = len(warnings)

    # ── Build validation field issues map ──
    validation_field_issues = {}
    for issue in errors + warnings:
        for f in issue.get("affected_fields", []):
            validation_field_issues[f] = True

    # ── Evidence entries from extracted fields ──
    if isinstance(extracted_data, dict):
        _field_display = {
            "invoice_number": "Invoice Number",
            "invoice_date": "Invoice Date",
            "due_date": "Due Date",
            "vendor_name": "Vendor Name",
            "vendor_tax_id": "Vendor Tax ID",
            "buyer_name": "Buyer / Bill To",
            "po_number": "PO Number",
            "currency": "Currency",
            "subtotal": "Subtotal",
            "tax_percentage": "Tax Rate %",
            "tax_amount": "Tax Amount",
            "total_amount": "Total Amount",
            "confidence": "Confidence Score",
        }
        for field_key, display_name in _field_display.items():
            val = extracted_data.get(field_key)
            if val is not None and val != "":
                evidence_entries.append({
                    "field_key": field_key,
                    "field_name": display_name,
                    "value": str(val),
                    "confidence": ext.confidence,
                    "method": "LLM",
                    "source_text": None,
                    "page_number": 1,
                    "table_index": None,
                    "row_index": None,
                    "bbox": None,
                })
        # Add line item evidence
        raw_lines = extracted_data.get("line_items", [])
        if raw_lines:
            evidence_entries.append({
                "field_key": "line_items",
                "field_name": f"Line Items ({len(raw_lines)} rows)",
                "value": f"{len(raw_lines)} line items extracted",
                "confidence": ext.confidence,
                "method": "LLM",
                "source_text": None,
                "page_number": 1,
                "table_index": 0,
                "row_index": None,
                "bbox": None,
            })

    # ── Reasoning blocks from pipeline metadata ──
    # Synthesize reasoning from the extraction pipeline steps
    reasoning_blocks.append({
        "title": "Document Upload",
        "category": "Ingestion",
        "badge_class": "info",
        "summary": f"Document uploaded: {ext.document_upload.original_filename if ext.document_upload else 'Unknown'}",
        "decision": None,
        "details": None,
        "duration_ms": None,
        "related_fields": [],
    })
    reasoning_blocks.append({
        "title": "OCR & Text Extraction",
        "category": "OCR",
        "badge_class": "info",
        "summary": "Azure Document Intelligence processed the document and extracted raw text.",
        "decision": None,
        "details": None,
        "duration_ms": None,
        "related_fields": [],
    })
    reasoning_blocks.append({
        "title": "LLM Field Extraction",
        "category": "Extraction",
        "badge_class": "primary",
        "summary": f"GPT-4o extracted {len(evidence_entries)} fields with {ext.confidence:.0%} overall confidence.",
        "decision": f"Extracted invoice {invoice.invoice_number}" if invoice else "Extraction completed",
        "details": None,
        "duration_ms": None,
        "related_fields": list(extracted_data.keys()) if isinstance(extracted_data, dict) else [],
    })
    if invoice and invoice.raw_vendor_name:
        reasoning_blocks.append({
            "title": "Vendor Identification",
            "category": "Enrichment",
            "badge_class": "success",
            "summary": f"Identified vendor: {invoice.raw_vendor_name}",
            "decision": f"Matched vendor from extracted data",
            "details": None,
            "duration_ms": None,
            "related_fields": ["vendor_name"],
        })
    reasoning_blocks.append({
        "title": "Normalization",
        "category": "Processing",
        "badge_class": "secondary",
        "summary": "Field values normalized (dates, amounts, PO number formatting).",
        "decision": None,
        "details": None,
        "duration_ms": None,
        "related_fields": ["invoice_date", "total_amount", "po_number"],
    })
    reasoning_blocks.append({
        "title": "Validation",
        "category": "QA",
        "badge_class": "warning" if (errors or warnings) else "success",
        "summary": f"Validation complete: {error_count} errors, {warning_count} warnings, {len(passed_checks)} passed.",
        "decision": "Requires review" if errors else "Passed validation",
        "details": None,
        "duration_ms": None,
        "related_fields": [],
    })

    # ── Audit events from AuditEvent model ──
    from apps.auditlog.models import AuditEvent
    from django.db.models import Q
    audit_qs = AuditEvent.objects.filter(
        Q(invoice_id=invoice.pk if invoice else 0)
        | Q(entity_type="DocumentUpload", entity_id=ext.document_upload_id)
        | Q(entity_type="ExtractionResult", entity_id=ext.pk)
        | Q(entity_type="Invoice", entity_id=invoice.pk if invoice else 0)
    ).order_by("-created_at")

    _event_badge_map = {
        "EXTRACTION_COMPLETED": "success",
        "EXTRACTION_STARTED": "info",
        "INVOICE_UPLOADED": "primary",
        "EXTRACTION_FAILED": "danger",
        "FIELD_CORRECTED": "warning",
        "GUARDRAIL_GRANTED": "success",
        "GUARDRAIL_DENIED": "danger",
    }
    for evt in audit_qs:
        performer = evt.actor_email
        if not performer and evt.performed_by:
            performer = evt.performed_by.email
        metadata = {}
        if evt.metadata_json and isinstance(evt.metadata_json, dict):
            metadata = evt.metadata_json
        audit_events.append({
            "action": evt.action or evt.event_type,
            "event_type": evt.event_type or evt.action,
            "badge_class": _event_badge_map.get(evt.event_type, "secondary"),
            "timestamp": evt.created_at,
            "actor": performer or "System",
            "actor_role": evt.actor_primary_role or "",
            "description": evt.event_description or "",
            "before": evt.status_before or "",
            "after": evt.status_after or "",
            "metadata": metadata,
        })

    # ── Pipeline stages ──
    _stage_defs = [
        ("upload", "Upload"),
        ("ocr", "OCR"),
        ("jurisdiction", "Jurisdiction"),
        ("schema", "Schema"),
        ("extraction", "Extraction"),
        ("normalize", "Normalize"),
        ("validate", "Validate"),
        ("enrich", "Enrich"),
        ("confidence", "Confidence"),
        ("review", "Review"),
    ]
    for key, label in _stage_defs:
        state = "completed" if ext.success else "pending"
        if key == "review" and ext.success:
            state = "active"
        pipeline_stages.append({"key": key, "label": label, "state": state})

    # ── Extraction context for template ──
    # Use invoice status as the canonical status when available
    if invoice:
        display_status = invoice.status
    else:
        display_status = "EXTRACTED" if ext.success else "FAILED"

    extraction_ctx = {
        "id": ext.pk,
        "file_name": ext.document_upload.original_filename if ext.document_upload else "Unknown",
        "status": display_status,
        "extraction_confidence": invoice.extraction_confidence if invoice else ext.confidence,
        "created_at": ext.created_at,
        "resolved_jurisdiction": extracted_data.get("jurisdiction") if isinstance(extracted_data, dict) else None,
        "jurisdiction_source": extracted_data.get("jurisdiction_source") if isinstance(extracted_data, dict) else None,
        "jurisdiction_confidence": extracted_data.get("jurisdiction_confidence") if isinstance(extracted_data, dict) else None,
        "jurisdiction_warning": extracted_data.get("jurisdiction_warning") if isinstance(extracted_data, dict) else None,
    }

    # Approval state
    approval = None
    if invoice:
        from apps.extraction.models import ExtractionApproval
        approval = ExtractionApproval.objects.filter(invoice=invoice).first()
        # Auto-create PENDING approval if missing for a validated invoice
        if not approval and invoice.status in (InvoiceStatus.VALIDATED, InvoiceStatus.EXTRACTED):
            from apps.extraction.services.approval_service import ExtractionApprovalService
            try:
                approval = ExtractionApprovalService.create_pending_approval(invoice, ext)
            except Exception:
                logger.warning("Could not auto-create approval for invoice %s", invoice.pk)

    # Permissions context
    duplicate_blocks_approval = False
    if invoice and invoice.is_duplicate and invoice.duplicate_of_id:
        from apps.extraction.models import ExtractionApproval as _EA
        from apps.core.enums import ExtractionApprovalStatus as _EAS
        duplicate_blocks_approval = _EA.objects.filter(
            invoice_id=invoice.duplicate_of_id,
            status__in=[_EAS.APPROVED, _EAS.AUTO_APPROVED],
        ).exists()

    permissions = {
        "can_approve": (
            request.user.has_permission("extraction.approve")
            if hasattr(request.user, "has_permission") else False
        ) and not duplicate_blocks_approval,
        "can_reprocess": request.user.has_permission("extraction.reprocess") if hasattr(request.user, "has_permission") else False,
        "can_escalate": request.user.has_permission("cases.escalate") if hasattr(request.user, "has_permission") else False,
    }

    # Assignable users for escalation
    from django.contrib.auth import get_user_model
    User = get_user_model()
    assignable_users = User.objects.filter(is_active=True).order_by("email")[:50]

    # ── Execution context (governed pipeline or legacy fallback) ──
    from apps.extraction.services.execution_context import get_execution_context
    exec_ctx = get_execution_context(ext)

    # Use execution context to load ExtractionRun + corrections (single lookup)
    extraction_run = None
    corrections = []
    correction_count = 0
    if exec_ctx.extraction_run_id:
        try:
            from apps.extraction_core.models import ExtractionRun
            extraction_run = (
                ExtractionRun.objects
                .select_related("jurisdiction", "schema")
                .filter(pk=exec_ctx.extraction_run_id)
                .first()
            )
            if extraction_run:
                corrections = list(
                    extraction_run.corrections
                    .select_related("corrected_by")
                    .order_by("-created_at")
                )
                correction_count = len(corrections)
        except Exception:
            pass

    # Populate extraction_ctx from ExecutionContext
    extraction_ctx["review_queue"] = exec_ctx.review_queue
    extraction_ctx["schema_code"] = exec_ctx.schema_code
    extraction_ctx["schema_version"] = exec_ctx.schema_version
    extraction_ctx["extraction_method"] = exec_ctx.extraction_method
    extraction_ctx["requires_review"] = exec_ctx.requires_review
    extraction_ctx["extraction_source"] = exec_ctx.source

    # ── Also include ExtractionFieldCorrection records (from Edit Values / Approval) ──
    if approval:
        from apps.extraction.models import ExtractionFieldCorrection
        field_corrections = (
            ExtractionFieldCorrection.objects
            .filter(approval=approval)
            .select_related("corrected_by")
            .order_by("-created_at")
        )
        for fc in field_corrections:
            corrections.append({
                "field_code": fc.field_name,
                "original_value": fc.original_value,
                "corrected_value": fc.corrected_value,
                "correction_reason": f"{fc.entity_type} correction",
                "corrected_by": fc.corrected_by,
                "created_at": fc.created_at,
            })
        correction_count = len(corrections)

    # ── Merged from result_detail: raw JSON, line item models, has_line_tax ──
    raw_json_pretty = ""
    if ext.raw_response:
        raw_json_pretty = json.dumps(ext.raw_response, indent=2, default=str)

    has_line_tax = False
    has_line_tax_pct = False
    line_items_raw = []
    if invoice:
        line_items_raw = list(invoice.line_items.order_by("line_number"))
        has_line_tax = any(
            li.tax_amount and li.tax_amount != 0 for li in line_items_raw
        )
        has_line_tax_pct = any(
            li.tax_percentage and li.tax_percentage != 0 for li in line_items_raw
        )

    # Build tax_breakdown context from invoice
    invoice_tax_breakdown = {}
    if invoice and invoice.tax_breakdown and isinstance(invoice.tax_breakdown, dict):
        invoice_tax_breakdown = invoice.tax_breakdown

    # Build evidence map keyed by field_key for inline display
    evidence_map = {e["field_key"]: e for e in evidence_entries}

    # ── Cost & Token Usage from AgentRun (totalled across all runs for this upload) ──
    cost_token_data = None
    try:
        from apps.agents.models import AgentRun
        from apps.core.enums import AgentType as _AT
        from django.db.models import Sum

        # Collect ALL extraction runs for this upload via the new document_upload FK
        all_runs_qs = AgentRun.objects.none()
        if ext.document_upload_id:
            all_runs_qs = AgentRun.objects.filter(
                document_upload_id=ext.document_upload_id,
                agent_type=_AT.INVOICE_EXTRACTION,
            ).order_by("-created_at")

        # Fallback: if FK not yet populated (old runs before migration), use stored agent_run_id only
        if not all_runs_qs.exists() and ext.agent_run_id:
            all_runs_qs = AgentRun.objects.filter(pk=ext.agent_run_id)

        if all_runs_qs.exists():
            totals = all_runs_qs.aggregate(
                prompt_tk=Sum("prompt_tokens"),
                completion_tk=Sum("completion_tokens"),
                total_tk=Sum("total_tokens"),
            )
            prompt_tk = totals["prompt_tk"] or 0
            completion_tk = totals["completion_tk"] or 0
            total_tk = totals["total_tk"] or 0

            if total_tk:
                # Latest run for metadata (model name, duration, run id, status)
                latest_run = all_runs_qs.first()
                run_count = all_runs_qs.count()

                # GPT-4o pricing: $5/1M input, $15/1M output
                llm_cost = Decimal(str(prompt_tk * 5 / 1_000_000 + completion_tk * 15 / 1_000_000))
                # Azure Document Intelligence (Read model): $1.50 per 1,000 pages
                # OCR is per-run; use ExtractionResult page count (latest / best available)
                ocr_pages = ext.ocr_page_count or 0
                # For total OCR cost, multiply by number of runs (each reprocess re-runs OCR)
                ocr_cost = Decimal(str(ocr_pages * run_count * 1.5 / 1_000)) if ocr_pages else Decimal("0")
                total_cost = (llm_cost + ocr_cost).quantize(Decimal("0.000001"))
                cost_token_data = {
                    "prompt_tokens": prompt_tk,
                    "completion_tokens": completion_tk,
                    "total_tokens": total_tk,
                    "llm_cost": llm_cost.quantize(Decimal("0.000001")),
                    "ocr_cost": ocr_cost.quantize(Decimal("0.000001")),
                    "cost_estimate": total_cost,
                    "ocr_page_count": ocr_pages,
                    "ocr_duration_ms": ext.ocr_duration_ms or 0,
                    "ocr_char_count": ext.ocr_char_count or 0,
                    "llm_model": latest_run.llm_model_used or "gpt-4o",
                    "duration_ms": latest_run.duration_ms,
                    "agent_run_id": latest_run.pk,
                    "agent_type": latest_run.get_agent_type_display(),
                    "status": latest_run.status,
                    "started_at": latest_run.started_at,
                    "completed_at": latest_run.completed_at,
                    "run_count": run_count,
                }
    except Exception:
        logger.debug("Could not load cost/token data for extraction %s", ext.pk)

    response = render(request, "extraction/console/console.html", {
        "extraction": extraction_ctx,
        "ext": ext,
        "invoice": invoice,
        "header_fields": header_fields,
        "tax_fields": tax_fields,
        "parties": parties,
        "enrichment": enrichment,
        "line_items": line_items,
        "line_items_raw": line_items_raw,
        "line_items_totals": line_items_totals,
        "has_line_tax": has_line_tax,
        "has_line_tax_pct": has_line_tax_pct,
        "invoice_tax_breakdown": invoice_tax_breakdown,
        "evidence_entries": evidence_entries,
        "evidence_map": evidence_map,
        "reasoning_blocks": reasoning_blocks,
        "audit_events": audit_events,
        "validation_issues": errors + warnings,
        "errors": errors,
        "warnings": warnings,
        "passed_checks": passed_checks,
        "passed_count": len(passed_checks),
        "error_count": error_count,
        "warning_count": warning_count,
        "validation_field_issues": validation_field_issues,
        "pipeline_stages": pipeline_stages,
        "approval": approval,
        "approval_status": approval.status if approval else None,
        "is_duplicate": invoice.is_duplicate if invoice else False,
        "duplicate_of_id": invoice.duplicate_of_id if invoice else None,
        "duplicate_blocks_approval": duplicate_blocks_approval,
        "invoice_status": invoice.status if invoice else None,
        "permissions": permissions,
        "assignable_users": assignable_users,
        "corrections": corrections,
        "correction_count": correction_count,
        "raw_json_pretty": raw_json_pretty,
        "cost_token_data": cost_token_data,
    })
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
