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
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.enums import (
    AuditEventType,
    DocumentType,
    FileProcessingState,
    InvoiceStatus,
)
from apps.core.decorators import observed_action
from apps.core.permissions import permission_required_code
from apps.documents.models import DocumentUpload, Invoice, InvoiceLineItem
from apps.extraction.models import ExtractionResult

logger = logging.getLogger(__name__)

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
        .select_related("document_upload", "invoice", "invoice__vendor")
        .order_by("-created_at")
    )

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

    # KPI stats
    from django.db.models import Avg, Count, Q as Qf
    all_results = ExtractionResult.objects.all()
    stats = {
        "total": all_results.count(),
        "success": all_results.filter(success=True).count(),
        "failed": all_results.filter(success=False).count(),
        "avg_confidence": all_results.filter(
            success=True, confidence__isnull=False
        ).aggregate(avg=Avg("confidence"))["avg"] or 0,
        "avg_duration": all_results.filter(
            success=True, duration_ms__isnull=False
        ).aggregate(avg=Avg("duration_ms"))["avg"] or 0,
    }

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "extraction/workbench.html", {
        "results": page_obj,
        "page_obj": page_obj,
        "stats": stats,
    })


# ────────────────────────────────────────────────────────────────
# Upload + Extract (standalone — no case creation)
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("invoices.create")
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

    # Compute SHA-256 hash
    sha256 = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        sha256.update(chunk)
    file_hash = sha256.hexdigest()
    uploaded_file.seek(0)

    # Create DocumentUpload record
    doc_upload = DocumentUpload.objects.create(
        original_filename=uploaded_file.name,
        file_size=uploaded_file.size,
        file_hash=file_hash,
        content_type=uploaded_file.content_type,
        document_type=DocumentType.INVOICE,
        processing_state=FileProcessingState.PROCESSING,
        uploaded_by=request.user,
    )

    # Save file to a temp location for extraction
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=os.path.splitext(uploaded_file.name)[1]
    )
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_f:
            for chunk in uploaded_file.chunks():
                tmp_f.write(chunk)

        # Also try blob upload (optional — extraction workbench works without it)
        _try_blob_upload(doc_upload, uploaded_file)

        # Run extraction pipeline (standalone — no case creation)
        result = _run_extraction_pipeline(doc_upload, tmp_path)

        if result["success"]:
            messages.success(
                request,
                f"Extraction completed for '{uploaded_file.name}' — "
                f"confidence {result['confidence']:.0%}."
            )
            return redirect("extraction:result_detail", pk=result["extraction_result_id"])
        else:
            messages.error(
                request,
                f"Extraction failed for '{uploaded_file.name}': {result['error']}"
            )
            return redirect("extraction:workbench")
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

        # 5. Duplicate check
        dup_result = DuplicateDetectionService().check(normalized)

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
def extraction_result_detail(request, pk):
    """Show detailed extraction results for a single run."""
    ext = get_object_or_404(
        ExtractionResult.objects.select_related(
            "document_upload", "document_upload__uploaded_by",
            "invoice", "invoice__vendor",
        ),
        pk=pk,
    )

    invoice = ext.invoice
    line_items = []
    validation_issues = []
    has_line_tax = False

    if invoice:
        line_items = list(invoice.line_items.order_by("line_number"))
        has_line_tax = any(
            li.tax_amount and li.tax_amount != 0 for li in line_items
        )
        # Re-run validation to show issues in the UI
        try:
            from apps.extraction.services.parser_service import ExtractionParserService
            from apps.extraction.services.normalization_service import NormalizationService
            from apps.extraction.services.validation_service import ValidationService

            if ext.raw_response:
                parsed = ExtractionParserService().parse(ext.raw_response)
                normalized = NormalizationService().normalize(parsed)
                val_result = ValidationService().validate(normalized)
                validation_issues = [
                    {"field": v.field, "severity": v.severity, "message": v.message}
                    for v in val_result.issues
                ]
        except Exception:
            pass

    # Raw JSON for display
    raw_json_pretty = ""
    if ext.raw_response:
        raw_json_pretty = json.dumps(ext.raw_response, indent=2, default=str)

    return render(request, "extraction/result_detail.html", {
        "ext": ext,
        "invoice": invoice,
        "line_items": line_items,
        "has_line_tax": has_line_tax,
        "validation_issues": validation_issues,
        "raw_json_pretty": raw_json_pretty,
    })


# ────────────────────────────────────────────────────────────────
# JSON download
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
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
        return redirect("extraction:result_detail", pk=pk)

    try:
        from apps.documents.blob_service import download_blob_to_tempfile
        tmp_path = download_blob_to_tempfile(upload.blob_path)
    except Exception as exc:
        messages.error(request, f"Failed to download document: {exc}")
        return redirect("extraction:result_detail", pk=pk)

    try:
        result = _run_extraction_pipeline(upload, tmp_path)
        if result["success"]:
            messages.success(request, "Re-extraction completed successfully.")
            return redirect("extraction:result_detail", pk=result["extraction_result_id"])
        else:
            messages.error(request, f"Re-extraction failed: {result['error']}")
            return redirect("extraction:result_detail", pk=pk)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ────────────────────────────────────────────────────────────────
# AJAX filter endpoint (returns table HTML partial)
# ────────────────────────────────────────────────────────────────
@login_required
@require_GET
@permission_required_code("invoices.view")
def extraction_ajax_filter(request):
    """Return filtered extraction results as JSON for AJAX table refresh."""
    qs = (
        ExtractionResult.objects
        .select_related("document_upload", "invoice", "invoice__vendor")
        .order_by("-created_at")
    )

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
        .select_related("document_upload", "invoice", "invoice__vendor")
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
    "invoice_number", "po_number", "invoice_date", "currency",
    "subtotal", "tax_amount", "total_amount",
}
EDITABLE_LINE_FIELDS = {
    "description", "quantity", "unit_price", "tax_amount", "line_amount",
}


@login_required
@require_POST
@permission_required_code("invoices.create")
@observed_action(
    "extraction.edit_extracted_values",
    permission="invoices.create",
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

    # ── Update header fields ───────────────────────────────
    header = payload.get("header", {})
    changed_fields = []
    update_fields = ["updated_at"]

    for field_name, value in header.items():
        if field_name not in EDITABLE_HEADER_FIELDS:
            continue
        value = str(value).strip()

        if field_name == "invoice_date":
            try:
                parsed_date = datetime.strptime(value, "%Y-%m-%d").date() if value else None
                setattr(invoice, field_name, parsed_date)
            except ValueError:
                continue
        elif field_name in ("subtotal", "tax_amount", "total_amount"):
            try:
                setattr(invoice, field_name, Decimal(value) if value else None)
            except InvalidOperation:
                continue
        else:
            setattr(invoice, field_name, value)

        changed_fields.append(field_name)
        update_fields.append(field_name)

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
            value = str(value).strip()

            if field_name in ("quantity", "unit_price"):
                try:
                    setattr(line_item, field_name, Decimal(value) if value else None)
                except InvalidOperation:
                    continue
            elif field_name in ("tax_amount", "line_amount"):
                try:
                    setattr(line_item, field_name, Decimal(value) if value else None)
                except InvalidOperation:
                    continue
            else:
                setattr(line_item, field_name, value)
            line_update_fields.append(field_name)

        if len(line_update_fields) > 1:
            line_item.save(update_fields=line_update_fields)
            lines_changed += 1

    # ── Audit log ──────────────────────────────────────────
    from apps.auditlog.services import AuditService
    AuditService.log_event(
        entity_type="Invoice",
        entity_id=invoice.pk,
        event_type=AuditEventType.EXTRACTION_COMPLETED,
        description=(
            f"Manual correction of extracted values for Invoice {invoice.invoice_number}: "
            f"header fields={changed_fields}, lines changed={lines_changed}"
        ),
        user=request.user,
        metadata={
            "source": "extraction_workbench",
            "action": "manual_edit",
            "header_fields_changed": changed_fields,
            "lines_changed": lines_changed,
            "extraction_result_id": ext.pk,
        },
    )

    return JsonResponse({
        "ok": True,
        "header_fields_changed": changed_fields,
        "lines_changed": lines_changed,
    })
