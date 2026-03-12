"""Document template views (server-side rendered)."""
import hashlib
from urllib.parse import urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import Http404, HttpResponse
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.enums import DocumentType, InvoiceStatus
from apps.documents.blob_storage import AzureBlobStorageService
from apps.documents.models import DocumentUpload, GoodsReceiptNote, Invoice, PurchaseOrder


ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}

MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


@login_required
@require_POST
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

    blob_service = AzureBlobStorageService()
    blob_metadata = {
        "uploaded_by_user_id": str(request.user.pk),
        "document_type": document_type,
        "content_type": uploaded_file.content_type or "",
    }
    blob_result = blob_service.upload_file(
        uploaded_file,
        original_filename=uploaded_file.name,
        content_type=uploaded_file.content_type or "application/octet-stream",
        metadata=blob_metadata,
    )

    doc_upload = DocumentUpload.objects.create(
        original_filename=uploaded_file.name,
        file_size=uploaded_file.size,
        file_hash=file_hash,
        content_type=uploaded_file.content_type,
        blob_name=blob_result["blob_name"],
        blob_container=blob_result["container_name"],
        blob_url=blob_result["blob_url"],
        blob_uploaded_at=timezone.now(),
        blob_metadata=blob_metadata,
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
        metadata={
            "filename": uploaded_file.name,
            "file_hash": file_hash,
            "document_type": document_type,
            "blob_name": doc_upload.blob_name,
            "blob_container": doc_upload.blob_container,
            "blob_url": doc_upload.blob_url,
        },
    )

    # Trigger extraction pipeline — run synchronously in eager mode, else async with fallback
    try:
        from apps.extraction.tasks import process_invoice_upload_task
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            result = process_invoice_upload_task.run(upload_id=doc_upload.pk)
            if isinstance(result, dict) and result.get("is_duplicate"):
                request.session["duplicate_popup"] = {
                    "invoice_number": result.get("invoice_number") or "",
                    "invoice_id": result.get("invoice_id") or "",
                }
                messages.warning(request, "Duplicate invoice found. Please review the existing invoice record.")
            else:
                messages.success(request, f"'{uploaded_file.name}' uploaded and processed successfully.")
        else:
            process_invoice_upload_task.delay(doc_upload.pk)
            messages.success(request, f"'{uploaded_file.name}' uploaded successfully. Extraction processing has started.")
    except Exception:
        # Celery broker unavailable — run extraction synchronously
        try:
            from apps.extraction.tasks import process_invoice_upload_task
            # Call the underlying function directly (bypass Celery bind=True)
            result = process_invoice_upload_task.run(upload_id=doc_upload.pk)
            if isinstance(result, dict) and result.get("is_duplicate"):
                request.session["duplicate_popup"] = {
                    "invoice_number": result.get("invoice_number") or "",
                    "invoice_id": result.get("invoice_id") or "",
                }
                messages.warning(request, "Duplicate invoice found. Please review the existing invoice record.")
            else:
                messages.success(request, f"'{uploaded_file.name}' uploaded and processed successfully.")
        except Exception as exc:
            messages.warning(request, f"'{uploaded_file.name}' uploaded, but extraction failed: {exc}")

    return redirect("documents:invoice_list")


@login_required
def invoice_list(request):
    qs = Invoice.objects.select_related("vendor").prefetch_related("ap_case").order_by("-created_at")
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    q = request.GET.get("q")
    if q:
        qs = qs.filter(invoice_number__icontains=q) | qs.filter(raw_vendor_name__icontains=q)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Pending uploads that haven't been processed into invoices yet
    from apps.core.enums import FileProcessingState
    pending_uploads = DocumentUpload.objects.filter(
        processing_state__in=[FileProcessingState.QUEUED, FileProcessingState.PROCESSING],
    ).select_related("uploaded_by").order_by("-created_at")[:10]

    duplicate_popup = request.session.pop("duplicate_popup", None)

    return render(request, "documents/invoice_list.html", {
        "invoices": page_obj,
        "page_obj": page_obj,
        "status_choices": InvoiceStatus.choices,
        "pending_uploads": pending_uploads,
        "duplicate_popup": duplicate_popup,
    })


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("vendor", "document_upload").prefetch_related("line_items"),
        pk=pk,
    )
    recon_results = invoice.recon_results.select_related("purchase_order").prefetch_related("exceptions").order_by("-created_at")

    invoice_file_embed_url = None
    if invoice.document_upload and invoice.document_upload.blob_name:
        blob_service = AzureBlobStorageService()
        invoice_file_embed_url = blob_service.generate_blob_sas_url(invoice.document_upload.blob_name)

    return render(request, "documents/invoice_detail.html", {
        "invoice": invoice,
        "recon_results": recon_results,
        "invoice_file_embed_url": invoice_file_embed_url,
    })


@login_required
def po_list(request):
    qs = PurchaseOrder.objects.select_related("vendor").order_by("-po_date")

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
def grn_list(request):
    qs = GoodsReceiptNote.objects.select_related("purchase_order", "vendor").order_by("-receipt_date")

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
def uploaded_file(request, upload_id: int):
    upload = get_object_or_404(DocumentUpload, pk=upload_id)

    if upload.blob_name:
        blob_service = AzureBlobStorageService()
        try:
            payload = blob_service.download_blob_bytes(upload.blob_name)
        except FileNotFoundError as exc:
            raise Http404(str(exc))

        content_type = upload.content_type or "application/octet-stream"
        response = HttpResponse(payload, content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{upload.original_filename}"'
        return response

    raise Http404("File not found")
