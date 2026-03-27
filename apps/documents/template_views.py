"""Document template views (server-side rendered)."""
import hashlib
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import filesizeformat, timesince
from django.views.decorators.http import require_POST

from apps.core.enums import DocumentType, InvoiceStatus, UserRole
from apps.core.decorators import observed_action
from apps.core.permissions import permission_required_code
from apps.core.utils import normalize_category, parse_percentage, resolve_line_tax_percentage, resolve_tax_percentage
from apps.documents.models import DocumentUpload, GoodsReceiptNote, Invoice, PurchaseOrder


def _is_scoped_ap_processor(user):
    """Return True if user is AP_PROCESSOR and config restricts their view."""
    if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
        return False
    from apps.reconciliation.models import ReconciliationConfig
    config = ReconciliationConfig.objects.filter(is_default=True).first()
    if config and config.ap_processor_sees_all_cases:
        return False
    return True


def _scope_invoices_for_user(qs, user):
    """Restrict invoice queryset for AP_PROCESSOR based on config toggle."""
    if not _is_scoped_ap_processor(user):
        return qs
    return qs.filter(document_upload__uploaded_by=user)


def _scope_pos_for_user(qs, user):
    """Restrict PO queryset — AP_PROCESSOR sees only POs linked to their invoices."""
    if not _is_scoped_ap_processor(user):
        return qs
    user_po_numbers = (
        Invoice.objects.filter(document_upload__uploaded_by=user)
        .exclude(po_number="")
        .values_list("po_number", flat=True)
    )
    return qs.filter(po_number__in=user_po_numbers)


def _scope_grns_for_user(qs, user):
    """Restrict GRN queryset — AP_PROCESSOR sees only GRNs linked to their POs."""
    if not _is_scoped_ap_processor(user):
        return qs
    user_po_numbers = (
        Invoice.objects.filter(document_upload__uploaded_by=user)
        .exclude(po_number="")
        .values_list("po_number", flat=True)
    )
    return qs.filter(purchase_order__po_number__in=user_po_numbers)


ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


def _resolve_display_category(line, raw_line: dict) -> str:
    """Resolve the UI category label for an invoice line."""
    return (
        normalize_category(line.item_category)
        or normalize_category(raw_line.get("item_category") or raw_line.get("category"))
        or ("Service" if line.is_service_item else "")
        or ("Stock" if line.is_stock_item else "")
        or "Other"
    )


def _build_invoice_line_display(invoice):
    """Return line items enriched for UI rendering."""
    raw_json = invoice.extraction_raw_json or {}
    raw_line_items = raw_json.get("line_items") or []
    line_items = []
    has_tax_details = False

    for idx, line in enumerate(invoice.line_items.all(), start=1):
        raw_line = raw_line_items[idx - 1] if idx - 1 < len(raw_line_items) and isinstance(raw_line_items[idx - 1], dict) else {}
        tax_percentage = resolve_line_tax_percentage(
            raw_percentage=raw_line.get("tax_percentage"),
            tax_amount=line.tax_amount,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_amount=line.line_amount,
        )
        if tax_percentage is not None or (line.tax_amount is not None and line.tax_amount != Decimal("0.00")):
            has_tax_details = True
        line_items.append({
            "line_number": line.line_number,
            "description": line.description,
            "raw_description": line.raw_description,
            "item_category": _resolve_display_category(line, raw_line),
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "tax_percentage": tax_percentage,
            "tax_amount": line.tax_amount,
            "line_amount": line.line_amount,
            "is_service_item": line.is_service_item,
            "is_stock_item": line.is_stock_item,
        })

    return line_items, has_tax_details


@login_required
@require_POST
@permission_required_code("invoices.create")
@observed_action("documents.upload_invoice", permission="documents.upload", entity_type="DocumentUpload", audit_event="DOCUMENT_UPLOADED")
def upload_invoice(request):
    """Handle invoice file upload from the modal form."""
    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        messages.error(request, "No file selected.")
        return redirect(request.META.get("HTTP_REFERER", "dashboard:index"))

    if uploaded_file.content_type not in ALLOWED_CONTENT_TYPES:
        messages.error(request, "Unsupported file type. Please upload a PDF, PNG, JPG, or TIFF.")
        return redirect(request.META.get("HTTP_REFERER", "dashboard:index"))

    if uploaded_file.size > MAX_UPLOAD_SIZE:
        messages.error(request, "File too large. Maximum size is 20 MB.")
        return redirect(request.META.get("HTTP_REFERER", "dashboard:index"))

    document_type = request.POST.get("document_type", DocumentType.INVOICE)
    if document_type not in DocumentType.values:
        document_type = DocumentType.INVOICE

    # Compute SHA-256 hash
    sha256 = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        sha256.update(chunk)
    file_hash = sha256.hexdigest()
    uploaded_file.seek(0)

    # Upload to Azure Blob Storage — this is the primary storage
    from apps.documents.blob_service import is_blob_storage_enabled
    if not is_blob_storage_enabled():
        messages.error(request, "Azure Blob Storage is not configured. Cannot upload.")
        return redirect(request.META.get("HTTP_REFERER", "dashboard:index"))

    doc_upload = DocumentUpload.objects.create(
        original_filename=uploaded_file.name,
        file_size=uploaded_file.size,
        file_hash=file_hash,
        content_type=uploaded_file.content_type,
        document_type=document_type,
        uploaded_by=request.user,
    )

    try:
        from django.conf import settings as django_settings
        from django.utils import timezone as tz
        from apps.documents.blob_service import build_blob_path, upload_to_blob
        container_name = getattr(django_settings, "AZURE_BLOB_CONTAINER_NAME", "")
        blob_path = build_blob_path("input", uploaded_file.name, doc_upload.pk)
        uploaded_file.seek(0)
        upload_to_blob(uploaded_file, blob_path, content_type=uploaded_file.content_type)
        doc_upload.blob_path = blob_path
        doc_upload.blob_container = container_name
        doc_upload.blob_name = blob_path
        doc_upload.blob_url = f"https://bradblob.blob.core.windows.net/{container_name}/{blob_path}"
        doc_upload.blob_uploaded_at = tz.now()
        doc_upload.save(update_fields=[
            "blob_path", "blob_container", "blob_name", "blob_url",
            "blob_uploaded_at", "updated_at",
        ])
    except Exception as blob_exc:
        import logging
        logging.getLogger(__name__).exception("Blob upload failed for upload %s", doc_upload.pk)
        doc_upload.delete()
        messages.error(request, f"Upload to Azure Blob failed: {blob_exc}")
        return redirect(request.META.get("HTTP_REFERER", "dashboard:index"))

    # Audit: invoice uploaded
    from apps.auditlog.services import AuditService
    from apps.core.enums import AuditEventType
    AuditService.log_event(
        entity_type="DocumentUpload",
        entity_id=doc_upload.pk,
        event_type=AuditEventType.INVOICE_UPLOADED,
        description=f"File '{uploaded_file.name}' uploaded ({uploaded_file.size} bytes)",
        user=request.user,
        metadata={"filename": uploaded_file.name, "file_hash": file_hash, "document_type": document_type},
    )

    # Trigger extraction pipeline — try async first, fall back to sync
    try:
        from apps.extraction.tasks import process_invoice_upload_task
        process_invoice_upload_task.delay(doc_upload.pk)
        messages.success(request, f"'{uploaded_file.name}' uploaded successfully. Extraction processing has started.")
    except Exception:
        # Celery broker unavailable — run extraction synchronously
        try:
            from apps.extraction.tasks import process_invoice_upload_task
            # Call the underlying function directly (bypass Celery bind=True)
            process_invoice_upload_task.run(upload_id=doc_upload.pk)
            messages.success(request, f"'{uploaded_file.name}' uploaded and processed successfully.")
        except Exception as exc:
            messages.warning(request, f"'{uploaded_file.name}' uploaded, but extraction failed: {exc}")

    return redirect("documents:invoice_list")


@login_required
def invoice_list(request):
    from django.db.models import Count, Sum, Q

    qs = Invoice.objects.select_related("vendor").prefetch_related("ap_case").order_by("-created_at")
    qs = _scope_invoices_for_user(qs, request.user)

    # --- Primary filters ---
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    q = request.GET.get("q")
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q)
            | Q(raw_vendor_name__icontains=q)
            | Q(vendor__name__icontains=q)
            | Q(po_number__icontains=q)
        )

    # --- Advanced filters ---
    has_case = request.GET.get("has_case")
    if has_case == "yes":
        qs = qs.filter(ap_case__isnull=False)
    elif has_case == "no":
        qs = qs.filter(ap_case__isnull=True)

    confidence = request.GET.get("confidence")
    if confidence == "low":
        qs = qs.filter(extraction_confidence__lt=0.75, extraction_confidence__isnull=False)
    elif confidence == "high":
        qs = qs.filter(extraction_confidence__gte=0.75)

    is_dup = request.GET.get("duplicate")
    if is_dup == "yes":
        qs = qs.filter(is_duplicate=True)
    elif is_dup == "no":
        qs = qs.filter(is_duplicate=False)

    has_po = request.GET.get("has_po")
    if has_po == "yes":
        qs = qs.exclude(po_number="")
    elif has_po == "no":
        qs = qs.filter(Q(po_number="") | Q(po_number__isnull=True))

    date_from = request.GET.get("date_from")
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    date_to = request.GET.get("date_to")
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Pending uploads that haven't been processed into invoices yet
    from apps.core.enums import FileProcessingState
    pending_uploads = DocumentUpload.objects.filter(
        processing_state__in=[FileProcessingState.QUEUED, FileProcessingState.PROCESSING],
        document_type=DocumentType.INVOICE,
    ).select_related("uploaded_by").order_by("-created_at")[:10]

    # KPI stats (scoped to same visibility as the listing)
    from django.db.models import Count, Sum, Q
    base_inv = _scope_invoices_for_user(Invoice.objects.all(), request.user)
    total_invoices = base_inv.count()
    by_status = dict(
        base_inv.values_list("status").annotate(c=Count("id")).values_list("status", "c")
    )
    total_amount = base_inv.aggregate(s=Sum("total_amount"))["s"] or 0
    with_case = base_inv.filter(ap_case__isnull=False).count()
    low_confidence = base_inv.filter(extraction_confidence__lt=0.75, extraction_confidence__isnull=False).count()

    invoice_stats = {
        "total": total_invoices,
        "uploaded": by_status.get("UPLOADED", 0),
        "extracted": by_status.get("EXTRACTED", 0) + by_status.get("VALIDATED", 0),
        "reconciled": by_status.get("RECONCILED", 0),
        "failed": by_status.get("FAILED", 0) + by_status.get("INVALID", 0),
        "with_case": with_case,
        "low_confidence": low_confidence,
        "total_amount": f"{total_amount:,.2f}",
    }

    return render(request, "documents/invoice_list.html", {
        "invoices": page_obj,
        "page_obj": page_obj,
        "status_choices": InvoiceStatus.choices,
        "pending_uploads": pending_uploads,
        "stats": invoice_stats,
    })


@login_required
def pending_uploads_status(request):
    """Return pending uploads as JSON for AJAX polling."""
    from apps.core.enums import FileProcessingState

    pending = DocumentUpload.objects.filter(
        processing_state__in=[FileProcessingState.QUEUED, FileProcessingState.PROCESSING],
    ).select_related("uploaded_by").order_by("-created_at")[:10]

    uploads = []
    for u in pending:
        uploads.append({
            "id": u.pk,
            "filename": u.original_filename,
            "document_type": u.document_type,
            "file_size": filesizeformat(u.file_size),
            "state": u.processing_state,
            "uploaded_ago": timesince(u.created_at) + " ago",
        })

    return JsonResponse({"pending_uploads": uploads, "count": len(uploads)})


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("vendor", "document_upload", "document_upload__uploaded_by", "duplicate_of", "reprocessed_from").prefetch_related("line_items"),
        pk=pk,
    )
    recon_results = invoice.recon_results.select_related("purchase_order").prefetch_related("exceptions").order_by("-created_at")

    # Check if an AP Case already exists for this invoice
    ap_case = None
    try:
        ap_case = invoice.ap_case
    except Exception:
        pass

    raw_invoice_tax_percentage = parse_percentage((invoice.extraction_raw_json or {}).get("tax_percentage"))
    invoice_tax_percentage = resolve_tax_percentage(
        raw_percentage=raw_invoice_tax_percentage,
        tax_amount=invoice.tax_amount,
        base_amount=invoice.subtotal,
    )
    line_items_for_display, has_line_tax = _build_invoice_line_display(invoice)

    return render(request, "documents/invoice_detail.html", {
        "invoice": invoice,
        "recon_results": recon_results,
        "ap_case": ap_case,
        "has_line_tax": has_line_tax,
        "invoice_tax_percentage": invoice_tax_percentage,
        "raw_invoice_tax_percentage": raw_invoice_tax_percentage,
        "line_items_for_display": line_items_for_display,
    })


@login_required
@permission_required_code("purchase_orders.view")
def po_list(request):
    qs = PurchaseOrder.objects.select_related("vendor").order_by("-po_date")
    qs = _scope_pos_for_user(qs, request.user)

    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    vendor_filter = request.GET.get("vendor")
    if vendor_filter:
        qs = qs.filter(vendor_id=vendor_filter)

    q = request.GET.get("q")
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(po_number__icontains=q)
            | Q(vendor__name__icontains=q)
            | Q(buyer_name__icontains=q)
            | Q(department__icontains=q)
        )

    # Distinct status values for the filter dropdown
    status_choices = (
        PurchaseOrder.objects.order_by("status")
        .values_list("status", flat=True)
        .distinct()
    )
    # Vendors that have POs
    from apps.vendors.models import Vendor
    vendor_choices = (
        Vendor.objects.filter(purchase_orders__isnull=False)
        .distinct()
        .order_by("name")
        .values_list("pk", "name")
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "documents/po_list.html", {
        "purchase_orders": page_obj,
        "page_obj": page_obj,
        "status_choices": status_choices,
        "vendor_choices": vendor_choices,
    })


@login_required
@permission_required_code("purchase_orders.view")
def po_detail(request, pk):
    po = get_object_or_404(
        PurchaseOrder.objects.select_related("vendor").prefetch_related(
            "line_items", "grns__line_items",
        ),
        pk=pk,
    )
    recon_results = po.recon_results.select_related("invoice").prefetch_related("exceptions").order_by("-created_at")
    return render(request, "documents/po_detail.html", {
        "po": po,
        "recon_results": recon_results,
    })


@login_required
@permission_required_code("grns.view")
def grn_list(request):
    qs = GoodsReceiptNote.objects.select_related("purchase_order", "vendor").order_by("-receipt_date")
    qs = _scope_grns_for_user(qs, request.user)

    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)

    vendor_filter = request.GET.get("vendor")
    if vendor_filter:
        qs = qs.filter(vendor_id=vendor_filter)

    q = request.GET.get("q")
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(grn_number__icontains=q)
            | Q(purchase_order__po_number__icontains=q)
            | Q(vendor__name__icontains=q)
            | Q(warehouse__icontains=q)
            | Q(receiver_name__icontains=q)
        )

    status_choices = (
        GoodsReceiptNote.objects.order_by("status")
        .values_list("status", flat=True)
        .distinct()
    )
    from apps.vendors.models import Vendor
    vendor_choices = (
        Vendor.objects.filter(grns__isnull=False)
        .distinct()
        .order_by("name")
        .values_list("pk", "name")
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "documents/grn_list.html", {
        "grns": page_obj,
        "page_obj": page_obj,
        "status_choices": status_choices,
        "vendor_choices": vendor_choices,
    })


@login_required
@permission_required_code("grns.view")
def grn_detail(request, pk):
    grn = get_object_or_404(
        GoodsReceiptNote.objects.select_related(
            "purchase_order", "purchase_order__vendor", "vendor",
        ).prefetch_related("line_items"),
        pk=pk,
    )
    return render(request, "documents/grn_detail.html", {
        "grn": grn,
    })


@login_required
def document_download(request, pk):
    """Serve the original uploaded document from Azure Blob Storage."""
    upload = get_object_or_404(DocumentUpload, pk=pk)

    if not upload.blob_path:
        raise Http404("No document available — blob_path not set.")

    from apps.documents.blob_service import is_blob_storage_enabled, generate_blob_sas_url
    if not is_blob_storage_enabled():
        raise Http404("Azure Blob Storage is not configured.")

    try:
        sas_url = generate_blob_sas_url(upload.blob_path, expiry_minutes=15)
        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(sas_url)
    except Exception:
        raise Http404("Failed to generate download URL.")
