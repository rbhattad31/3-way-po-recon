"""
Benchmarking template views.
Covers: All Requests, New Request (upload), Detail/Results, Quotations, Reports, Configurations.
"""
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from apps.core.tenant_utils import require_tenant

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    BenchmarkResult,
    BenchmarkRunLog,
    CategoryMaster,
    Geography,
    LineCategory,
    PricingType,
    ScopeType,
    VarianceThresholdConfig,
    VarianceStatus,
)
from apps.benchmarking.services.benchmark_service import BenchmarkEngine
from apps.benchmarking.services.blob_storage_service import BlobStorageService
from apps.benchmarking.services.document_recovery_service import BenchmarkDocumentRecoveryService
from apps.benchmarking.services.export_service import ExportService
from apps.benchmarking.services.negotiation_assistant_service import BenchmarkNegotiationAssistantService
from apps.benchmarking.agents.Vendor_Recommendation_Agent_BM import BenchmarkVendorRecommendationAgent
from apps.documents.blob_service import build_blob_url, delete_blob, upload_to_blob

try:
    from apps.procurement.models import GeneratedRFQ as _GeneratedRFQ
except ImportError:
    _GeneratedRFQ = None


try:
    from apps.procurement.models import HVACServiceScope as _HVACServiceScope
except ImportError:
    _HVACServiceScope = None

logger = logging.getLogger(__name__)


BENCHMARK_ZIP_MAX_FILES = int(getattr(settings, "BENCHMARK_ZIP_MAX_FILES", 50))


def _is_pdf_name(file_name: str) -> bool:
    return file_name.lower().endswith(".pdf")


def _extract_pdf_files_from_upload(uploaded_file):
    """
    Return a list of (file_name, ContentFile) for either:
      - a single uploaded PDF
      - a ZIP containing one or more PDFs
    """
    file_name = (uploaded_file.name or "").strip()
    lower_name = file_name.lower()

    if lower_name.endswith(".pdf"):
        uploaded_file.seek(0)
        return [(file_name, ContentFile(uploaded_file.read()))], None

    if not lower_name.endswith(".zip"):
        return [], "Upload must be a PDF or ZIP file."

    uploaded_file.seek(0)
    data = uploaded_file.read()

    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return [], "The uploaded ZIP file is invalid."

    pdf_entries = [
        info for info in zf.infolist()
        if not info.is_dir() and _is_pdf_name(info.filename)
    ]

    if not pdf_entries:
        return [], "ZIP file does not contain any PDF files."

    if len(pdf_entries) > BENCHMARK_ZIP_MAX_FILES:
        return [], (
            f"ZIP contains {len(pdf_entries)} PDFs, which exceeds the maximum allowed "
            f"{BENCHMARK_ZIP_MAX_FILES}."
        )

    extracted_files = []
    for info in pdf_entries:
        with zf.open(info, "r") as fp:
            pdf_bytes = fp.read()
        base_name = os.path.basename(info.filename) or "quotation.pdf"
        extracted_files.append((base_name, ContentFile(pdf_bytes)))

    return extracted_files, None


def _extract_pdf_files_from_uploads(uploaded_files):
    """Flatten one or more PDF/ZIP uploads into a single PDF file list."""
    if not uploaded_files:
        return [], "Quotation file is required (PDF or ZIP)."

    all_pdf_files = []
    for uploaded_file in uploaded_files:
        extracted_files, upload_error = _extract_pdf_files_from_upload(uploaded_file)
        if upload_error:
            return [], upload_error
        all_pdf_files.extend(extracted_files)

    if not all_pdf_files:
        return [], "No PDF files found in uploaded quotation input."

    if len(all_pdf_files) > BENCHMARK_ZIP_MAX_FILES:
        return [], (
            f"Total uploaded PDFs ({len(all_pdf_files)}) exceed maximum allowed "
            f"{BENCHMARK_ZIP_MAX_FILES}."
        )

    return all_pdf_files, None


def _get_quotation_uploads(request):
    """Return quotation uploads from multi-file or legacy single-file form inputs."""
    uploads = [f for f in request.FILES.getlist("quotation_pdfs") if f]
    if uploads:
        return uploads
    legacy_upload = request.FILES.get("quotation_pdf")
    if legacy_upload:
        return [legacy_upload]
    return []


def _is_platform_admin(user) -> bool:
    return bool(getattr(user, "is_platform_admin", False) or getattr(user, "is_superuser", False))


def _scope_requests(request, qs=None):
    tenant = require_tenant(request)
    scoped = qs if qs is not None else BenchmarkRequest.objects.all()
    if tenant is not None and not _is_platform_admin(request.user):
        scoped = scoped.filter(tenant=tenant)
    return scoped


def _scope_quotations(request, qs=None):
    tenant = require_tenant(request)
    scoped = qs if qs is not None else BenchmarkQuotation.objects.all()
    if tenant is not None and not _is_platform_admin(request.user):
        scoped = scoped.filter(tenant=tenant)
    return scoped


def _create_quotations_from_upload(*, bench_request, upload, quotation_ref: str, user):
    """Create one or many BenchmarkQuotation rows from PDF/ZIP upload.

    Blob is the source of truth. Local file storage is not used for new uploads.
    """
    return _create_quotations_from_uploads(
        bench_request=bench_request,
        uploads=[upload],
        quotation_ref=quotation_ref,
        user=user,
    )


def _create_quotations_from_uploads(*, bench_request, uploads, quotation_ref: str, user):
    """Create one or many BenchmarkQuotation rows from PDF/ZIP upload list.

    Blob is the source of truth. Local file storage is not used for new uploads.
    """
    extracted_files, upload_error = _extract_pdf_files_from_uploads(uploads)
    if upload_error:
        return [], upload_error

    created = []
    for idx, (pdf_name, pdf_content) in enumerate(extracted_files, start=1):
        per_file_ref = quotation_ref
        if len(extracted_files) > 1:
            per_file_ref = f"{quotation_ref or 'ZIP'}-{idx}"

        pdf_content.seek(0)
        pdf_bytes = pdf_content.read()
        request_ref = (bench_request.title or "benchmark").strip()[:40].replace(" ", "_")
        blob_name, blob_url = BlobStorageService.upload_quotation(
            pdf_bytes,
            filename=pdf_name,
            request_ref=request_ref,
        )
        if not blob_url:
            return [], f"Failed to upload quotation '{pdf_name}' to Azure Blob Storage."

        quotation = BenchmarkQuotation(
            request=bench_request,
            supplier_name="",
            quotation_ref=per_file_ref,
            blob_name=blob_name,
            blob_url=blob_url,
            tenant=bench_request.tenant,
            created_by=user,
        )
        quotation.save()
        created.append(quotation)

    return created, None


def _run_benchmark_with_live(*, bench_request, user, tenant, force_reextract=False):
    """Run core benchmark + best-effort live enrichment."""
    result = BenchmarkEngine.run(
        bench_request.pk,
        user=user,
        tenant=tenant,
        force_reextract=bool(force_reextract),
    )
    live_result = None
    if result.get("success"):
        try:
            live_result = BenchmarkEngine.run_live_enrichment(
                request_pk=bench_request.pk,
                user=user,
                tenant=tenant,
            )
        except Exception as exc:
            logger.warning("Live enrichment failed for benchmark request %s: %s", bench_request.pk, exc)
    return result, live_result


# --------------------------------------------------------------------------- #
# Helper context
# --------------------------------------------------------------------------- #

def _base_ctx(**extra):
    ctx = {
        "geography_choices": Geography.CHOICES,
        "scope_type_choices": ScopeType.CHOICES,
        "line_category_choices": LineCategory.CHOICES,
    }
    ctx.update(extra)
    return ctx


def _get_generated_rfqs(tenant=None):
    """Return list of all generated RFQ records for the create-request dropdown."""
    if _GeneratedRFQ is None:
        return []
    try:
        rfq_qs = _GeneratedRFQ.objects.select_related("request")
        if tenant is not None:
            rfq_qs = rfq_qs.filter(request__tenant=tenant)
        return list(
            rfq_qs
            .order_by("-created_at")
            .values("rfq_ref", "system_label", "request__title")
        )
    except Exception:
        return []


def _build_rfq_scope_rows_from_benchmark_lines(*, line_items):
    """Fallback RFQ scope built from extracted quotation line items.

    Used only when a benchmark request carries an RFQ reference but no
    generated/uploaded RFQ scope rows can be resolved from upstream sources.
    """
    rows = []
    seen = set()
    for item in line_items or []:
        description = str(getattr(item, "description", "") or "").strip()
        if not description:
            continue
        normalized = " ".join(description.lower().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append({
            "line_no": len(rows) + 1,
            "category": getattr(item, "category", "") or "UNCATEGORIZED",
            "description": description,
            "unit": getattr(item, "uom", "") or "LS",
            "quantity": getattr(item, "quantity", None) or 1,
        })
    return rows


def _build_rfq_scope_rows(*, bench_request, rfq_mode, rfq_ref, line_items=None):
    """Build extracted RFQ scope rows for display in benchmarking request detail."""
    if not rfq_ref:
        return []

    if _GeneratedRFQ is None or _HVACServiceScope is None:
        return _build_rfq_scope_rows_from_benchmark_lines(line_items=line_items)

    try:
        rfq_qs = _GeneratedRFQ.objects.filter(rfq_ref=rfq_ref)
        if getattr(bench_request, "tenant_id", None):
            rfq_qs = rfq_qs.filter(request__tenant_id=bench_request.tenant_id)
        generated_rfq = rfq_qs.order_by("-created_at").first()
        if not generated_rfq:
            return _build_rfq_scope_rows_from_benchmark_lines(line_items=line_items)

        system_code = (generated_rfq.system_code or "").strip()
        if not system_code:
            return _build_rfq_scope_rows_from_benchmark_lines(line_items=line_items)

        db_scope = _HVACServiceScope.objects.filter(system_type__iexact=system_code, is_active=True).first()
        if not db_scope:
            return _build_rfq_scope_rows_from_benchmark_lines(line_items=line_items)

        quantity_overrides = {}
        raw_qty_json = generated_rfq.qty_json or {}
        if isinstance(raw_qty_json, dict):
            for key, value in raw_qty_json.items():
                try:
                    quantity_overrides[int(key)] = value
                except (TypeError, ValueError):
                    continue

        raw_rows = []
        for category, field_text in [
            ("Equipment", db_scope.equipment_scope),
            ("Installation", db_scope.installation_services),
            ("Piping/Ducting", db_scope.piping_ducting),
            ("Electrical", db_scope.electrical_works),
            ("Controls", db_scope.controls_accessories),
            ("Testing", db_scope.testing_commissioning),
        ]:
            line_count = 0
            for line in (field_text or "").splitlines():
                clean_line = line.strip().lstrip("-*. ").strip()
                if clean_line:
                    raw_rows.append((category, clean_line, "LS", 1))
                    line_count += 1
            if line_count == 0:
                raw_rows.append((category, "(As per site conditions)", "LS", 1))

        rows = []
        for index, (category, description, unit, quantity) in enumerate(raw_rows, start=1):
            rows.append({
                "line_no": index,
                "category": category,
                "description": description,
                "unit": unit,
                "quantity": quantity_overrides.get(index - 1, quantity),
            })
        return rows
    except Exception:
        logger.exception("Failed to build RFQ scope rows for benchmark request %s", bench_request.pk)
        return _build_rfq_scope_rows_from_benchmark_lines(line_items=line_items)


# --------------------------------------------------------------------------- #
# 1. All Requests
# --------------------------------------------------------------------------- #

@login_required
def request_list(request):
    """List all benchmarking requests with filters."""
    qs = _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)).order_by("created_at", "id")

    # Filters
    status_filter = request.GET.get("status", "")
    geography_filter = request.GET.get("geography", "")
    search = request.GET.get("q", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if geography_filter:
        qs = qs.filter(geography=geography_filter)
    if search:
        qs = qs.filter(title__icontains=search)

    # KPI counts
    scoped_base = _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True))
    total_count = scoped_base.count()
    completed_count = scoped_base.filter(status="COMPLETED").count()
    processing_count = scoped_base.filter(status="PROCESSING").count()
    failed_count = scoped_base.filter(status="FAILED").count()

    ctx = _base_ctx(
        bench_requests=qs,
        total_count=total_count,
        completed_count=completed_count,
        processing_count=processing_count,
        failed_count=failed_count,
        status_filter=status_filter,
        geography_filter=geography_filter,
        search=search,
        status_choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("COMPLETED", "Completed"), ("FAILED", "Failed")],
        page_title="All Benchmarking Requests",
        active_menu="request_list",
    )
    return render(request, "benchmarking/request_list.html", ctx)

@login_required
def dashboard(request):
    """Benchmarking dashboard with tenant-scoped KPIs and recent activity."""
    tenant = require_tenant(request)
    scoped_base = _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True))

    total_count = scoped_base.count()
    completed_count = scoped_base.filter(status="COMPLETED").count()
    processing_count = scoped_base.filter(status="PROCESSING").count()
    failed_count = scoped_base.filter(status="FAILED").count()
    pending_count = scoped_base.filter(status="PENDING").count()

    recent_requests = scoped_base.select_related("submitted_by").order_by("-created_at", "-id")[:10]

    company_name = "Bradsol Group"
    if tenant is not None and getattr(tenant, "name", None):
        company_name = tenant.name
    elif getattr(request.user, "company", None) and getattr(request.user.company, "name", None):
        company_name = request.user.company.name

    ctx = _base_ctx(
        total_count=total_count,
        completed_count=completed_count,
        processing_count=processing_count,
        failed_count=failed_count,
        pending_count=pending_count,
        recent_requests=recent_requests,
        company_name=company_name,
        page_title="Benchmarking Dashboard",
        active_menu="benchmarking_dashboard",
    )
    return render(request, "benchmarking/dashboard.html", ctx)

# --------------------------------------------------------------------------- #
# 2. New Request (upload form)
# --------------------------------------------------------------------------- #


@login_required
@require_http_methods(["POST"])
def request_delete(request, pk):
    """Soft-delete a benchmarking request and its active child records."""
    bench_request = get_object_or_404(
        _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)),
        pk=pk,
    )

    active_quotations = bench_request.quotations.filter(is_active=True)
    quotation_ids = list(active_quotations.values_list("id", flat=True))
    quotation_blob_names = [
        name for name in active_quotations.values_list("blob_name", flat=True)
        if name
    ]
    rfq_blob_path = (bench_request.rfq_blob_path or "").strip()
    request_title = bench_request.title

    with transaction.atomic():
        if quotation_ids:
            BenchmarkLineItem.objects.filter(
                quotation_id__in=quotation_ids,
                is_active=True,
            ).update(is_active=False)
        active_quotations.update(is_active=False)
        BenchmarkResult.objects.filter(request=bench_request, is_active=True).update(is_active=False)
        bench_request.is_active = False
        bench_request.save(update_fields=["is_active", "updated_at"])

    for blob_name in quotation_blob_names:
        BlobStorageService.delete_blob(blob_name)

    if rfq_blob_path:
        try:
            delete_blob(rfq_blob_path)
        except Exception:
            logger.exception(
                "Failed to delete RFQ blob '%s' while deleting benchmark request %s",
                rfq_blob_path,
                bench_request.pk,
            )

    messages.success(request, f"Benchmark request '{request_title}' deleted.")
    return redirect("benchmarking:request_list")

@login_required
def request_create(request):
    """Create a new benchmarking request; quotation upload is optional."""
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        project_name = request.POST.get("project_name", "").strip()
        geography = request.POST.get("geography", "UAE")
        scope_type = request.POST.get("scope_type", "SITC")
        store_type = request.POST.get("store_type", "").strip()
        rfq_source = request.POST.get("rfq_source", "manual")
        rfq_manual_ref = request.POST.get("rfq_manual_ref", "").strip()
        if rfq_source == "system":
            rfq_ref = request.POST.get("rfq_system_ref", "").strip()
        elif rfq_source == "upload":
            rfq_upload_file = request.FILES.get("rfq_upload_file")
            if rfq_upload_file:
                rfq_ref = os.path.splitext(rfq_upload_file.name)[0].strip() or "uploaded-rfq"
            else:
                rfq_ref = request.POST.get("rfq_upload_ref", "").strip() or rfq_manual_ref
        else:
            rfq_ref = rfq_manual_ref
        quotation_batch_ref = request.POST.get("quotation_batch_ref", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        if not title:
            errors.append("Request title is required.")
        if rfq_source == "system" and not rfq_ref:
            errors.append("Please select a system RFQ reference.")
        if rfq_source == "upload" and not request.FILES.get("rfq_upload_file"):
            errors.append("Please upload an RFQ PDF file.")
        if rfq_source == "manual" and not rfq_ref:
            errors.append("Manual RFQ reference is required.")

        uploads = _get_quotation_uploads(request)
        if uploads:
            extracted_files, upload_validation_error = _extract_pdf_files_from_uploads(uploads)
            if upload_validation_error:
                errors.append(upload_validation_error)
            elif len(extracted_files) < 4:
                errors.append("Please upload at least 4 quotation PDFs for benchmarking.")
        else:
            errors.append("Please upload quotation files (PDF or ZIP).")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "benchmarking/request_create.html", _base_ctx(
                posted=request.POST,
                page_title="New Benchmarking Request",
                generated_rfq_list=_get_generated_rfqs(getattr(request, "tenant", None)),
            ))

        # Create request
        bench_request = BenchmarkRequest.objects.create(
            title=title,
            project_name=project_name,
            geography=geography,
            scope_type=scope_type,
            store_type=store_type,
            notes=notes,
            status="PENDING",
            submitted_by=request.user,
            tenant=getattr(request, "tenant", None),
            created_by=request.user,
            rfq_source=rfq_source,
            rfq_ref=rfq_ref,
        )

        # Save uploaded RFQ document (rfq_source == "upload")
        if rfq_source == "upload":
            rfq_upload_file = request.FILES.get("rfq_upload_file")
            if rfq_upload_file:
                try:
                    safe_name = os.path.basename(rfq_upload_file.name).replace(" ", "_")
                    stamp = datetime.now(timezone.utc).strftime("%Y/%m")
                    rfq_blob_path = f"benchmarking/rfq/{stamp}/{uuid4().hex}_{safe_name}"
                    rfq_upload_file.seek(0)
                    upload_to_blob(rfq_upload_file, rfq_blob_path, content_type=getattr(rfq_upload_file, "content_type", "application/pdf"))
                    bench_request.rfq_blob_path = rfq_blob_path
                    bench_request.rfq_blob_url = build_blob_url(rfq_blob_path)
                    bench_request.save(update_fields=["rfq_blob_path", "rfq_blob_url", "updated_at"])
                except Exception as _rfq_exc:
                    bench_request.delete()
                    messages.error(request, f"Failed to upload RFQ document to Azure Blob Storage: {_rfq_exc}")
                    return render(request, "benchmarking/request_create.html", _base_ctx(
                        posted=request.POST,
                        page_title="New Benchmarking Request",
                        generated_rfq_list=_get_generated_rfqs(getattr(request, "tenant", None)),
                    ))

        if uploads:
            created_quotes, upload_error = _create_quotations_from_uploads(
                bench_request=bench_request,
                uploads=uploads,
                quotation_ref=quotation_batch_ref,
                user=request.user,
            )
            if upload_error:
                messages.error(request, upload_error)
                return redirect("benchmarking:request_detail", pk=bench_request.pk)

            result, live_result = _run_benchmark_with_live(
                bench_request=bench_request,
                user=request.user,
                tenant=getattr(request, "tenant", None),
            )

            if result.get("success"):
                if live_result and live_result.get("success"):
                    messages.success(
                        request,
                        f"Benchmark + live market analysis completed for {len(created_quotes)} quotation file(s).",
                    )
                else:
                    messages.success(
                        request,
                        f"Benchmark analysis completed for {len(created_quotes)} quotation file(s).",
                    )
            else:
                messages.warning(
                    request,
                    f"Request saved but analysis failed: {result.get('error')}. You can reprocess from the detail page."
                )
        else:
            messages.success(
                request,
                "Benchmark request created. Upload vendor quotation(s) from the detail page to start AI benchmarking.",
            )

        return redirect("benchmarking:request_detail", pk=bench_request.pk)

    return render(request, "benchmarking/request_create.html", _base_ctx(
        page_title="New Benchmarking Request",
        active_menu="request_list",
        generated_rfq_list=_get_generated_rfqs(getattr(request, "tenant", None)),
    ))


# --------------------------------------------------------------------------- #
# 3. Request Detail / Results
# --------------------------------------------------------------------------- #


def _quotation_has_document_source(quotation) -> bool:
    return BenchmarkDocumentRecoveryService.quotation_has_document_source(quotation)


def _ensure_quotation_document_source(quotation) -> bool:
    return BenchmarkDocumentRecoveryService.ensure_document_source(quotation)


def _build_quotation_preview_url(quotation) -> str:
    if not _ensure_quotation_document_source(quotation):
        return ""

    blob_name = (quotation.blob_name or "").strip()
    if blob_name:
        sas_url = (BlobStorageService.get_sas_url(blob_name, expiry_hours=24) or "").strip()
        if sas_url:
            return sas_url

    blob_url = (quotation.blob_url or "").strip()
    if blob_url:
        return blob_url

    return reverse("benchmarking:quotation_document_preview", args=[quotation.pk])


def _build_inline_pdf_response(pdf_bytes: bytes, filename: str) -> HttpResponse:
    safe_filename = os.path.basename(filename or "quotation.pdf").replace('"', "")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_http_methods(["GET"])
def quotation_document_preview(request, pk):
    quotation = get_object_or_404(
        _scope_quotations(
            request,
            BenchmarkQuotation.objects.filter(is_active=True).select_related("request"),
        ),
        pk=pk,
    )

    base_filename = (
        (quotation.quotation_ref or "").strip()
        or (quotation.supplier_name or "").strip()
        or f"quotation_{quotation.pk}"
    )
    if not base_filename.lower().endswith(".pdf"):
        base_filename = f"{base_filename}.pdf"

    _ensure_quotation_document_source(quotation)

    if (quotation.blob_name or "").strip():
        try:
            pdf_bytes = BlobStorageService.download_blob_bytes(quotation.blob_name)
            return _build_inline_pdf_response(pdf_bytes, base_filename)
        except Exception:
            logger.exception(
                "Failed to stream benchmarking blob document for quotation_id=%s blob_name=%s",
                quotation.pk,
                quotation.blob_name,
            )

    if quotation.document:
        try:
            quotation.document.open("rb")
            response = FileResponse(quotation.document.file, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'inline; filename="{os.path.basename(quotation.document.name) or base_filename}"'
            )
            response["Cache-Control"] = "private, max-age=300"
            response["X-Content-Type-Options"] = "nosniff"
            return response
        except Exception:
            logger.exception(
                "Failed to stream local benchmarking document for quotation_id=%s",
                quotation.pk,
            )

    if (quotation.blob_url or "").strip():
        return redirect(quotation.blob_url)

    return HttpResponse("Quotation document not found.", status=404)


@login_required
def request_detail(request, pk):
    """Detailed results view for a benchmarking request."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)

    # Gather all line items across all quotations
    quotations = bench_request.quotations.filter(is_active=True).prefetch_related("line_items")
    line_items = []
    quotation_summaries = []
    vendor_cards = []
    for idx, q in enumerate(quotations, start=1):
        q_items = list(q.line_items.filter(is_active=True))
        line_items.extend(q_items)
        fallback_vendor_label = f"Vendor {chr(64 + idx)}" if idx <= 26 else f"Vendor {idx}"
        supplier_name = (q.supplier_name or "").strip() or fallback_vendor_label
        quotation_document_url = _build_quotation_preview_url(q)
        q_total = 0.0
        q_total_bench_covered = 0.0
        q_bench = 0.0
        benchmarked_line_count = 0
        status_counts = {
            "WITHIN_RANGE": 0,
            "MODERATE": 0,
            "HIGH": 0,
            "NEEDS_REVIEW": 0,
        }
        live_reference_count = 0
        for li in q_items:
            line_amt = float(li.line_amount or 0)
            q_total += line_amt
            if li.benchmark_mid is not None and li.quantity is not None:
                q_total_bench_covered += line_amt
                q_bench += float(li.benchmark_mid) * float(li.quantity)
                benchmarked_line_count += 1
            elif li.benchmark_mid is not None:
                q_total_bench_covered += line_amt
                q_bench += float(li.benchmark_mid)
                benchmarked_line_count += 1
            status_counts[li.variance_status] = status_counts.get(li.variance_status, 0) + 1
            lp_json = li.live_price_json or {}
            live_reference_count += len(lp_json.get("citations", []) or [])
        q_dev = None
        if q_bench > 0:
            q_dev = ((q_total_bench_covered - q_bench) / q_bench) * 100
        q_status = "NEEDS_REVIEW"
        if q_dev is not None:
            q_status = "WITHIN_RANGE" if abs(q_dev) < 5 else ("MODERATE" if abs(q_dev) < 15 else "HIGH")
        quotation_summaries.append({
            "quotation": q,
            "total_quoted": q_total,
            "total_quoted_benchmark_covered": q_total_bench_covered if q_bench > 0 else None,
            "total_benchmark": q_bench if q_bench > 0 else None,
            "deviation_pct": q_dev,
            "status": q_status,
            "line_count": len(q_items),
            "benchmarked_line_count": benchmarked_line_count,
        })
        vendor_cards.append({
            "quotation_id": q.pk,
            "supplier_name": supplier_name,
            "quotation_ref": q.quotation_ref,
            "quotation_document_url": quotation_document_url,
            "line_items": q_items,
            "line_count": len(q_items),
            "benchmarked_line_count": benchmarked_line_count,
            "total_quoted": q_total,
            "total_quoted_benchmark_covered": q_total_bench_covered if q_bench > 0 else None,
            "total_benchmark": q_bench if q_bench > 0 else None,
            "deviation_pct": q_dev,
            "status": q_status,
            "status_counts": status_counts,
            "live_reference_count": live_reference_count,
        })

    vendor_recommendation = BenchmarkVendorRecommendationAgent.recommend(vendor_cards)
    best_vendor_summary = None
    if vendor_recommendation.get("recommended"):
        best_q_id = vendor_recommendation.get("quotation_id")
        best_vendor_summary = next(
            (s for s in quotation_summaries if s["quotation"].pk == best_q_id),
            None,
        )
    no_vendor_recommendation = not bool(vendor_recommendation.get("recommended"))

    result = None
    category_summary = {}
    negotiation_notes = []
    try:
        result = bench_request.result
        category_summary = result.category_summary_json or {}
        negotiation_notes = result.negotiation_notes_json or []
    except Exception:
        pass

    quotation_count = len(vendor_cards)
    quotation_mode = "MULTI_VENDOR" if quotation_count > 1 else ("SINGLE_VENDOR" if quotation_count == 1 else "NO_QUOTATION")
    quotation_mode_label_map = {
        "MULTI_VENDOR": "Multi Vendor",
        "SINGLE_VENDOR": "Single Vendor",
        "NO_QUOTATION": "No Quotation",
    }
    quotation_mode_label = quotation_mode_label_map.get(quotation_mode, quotation_mode.replace("_", " ").title())

    rfq_source = (bench_request.rfq_source or "").strip().lower()
    rfq_ref = (bench_request.rfq_ref or "").strip()
    rfq_present = bool(
        rfq_ref
        or bench_request.rfq_blob_path
        or bench_request.rfq_blob_url
        or bench_request.rfq_document
    )
    if rfq_present:
        if rfq_source == "system":
            rfq_mode = "SYSTEM_RFQ"
            rfq_label = "System RFQ"
        elif rfq_source == "upload":
            rfq_mode = "UPLOADED_RFQ"
            rfq_label = "Uploaded RFQ"
        else:
            rfq_mode = "MANUAL_RFQ"
            rfq_label = "Manual RFQ Reference"
    else:
        rfq_mode = "NO_RFQ"
        rfq_label = "No RFQ"

    rfq_scope_rows = _build_rfq_scope_rows(
        bench_request=bench_request,
        rfq_mode=rfq_mode,
        rfq_ref=rfq_ref,
        line_items=line_items,
    )

    rfq_scope_source = "none"
    if rfq_scope_rows:
        if _GeneratedRFQ is not None and rfq_ref:
            try:
                _rfq_exists = _GeneratedRFQ.objects.filter(rfq_ref=rfq_ref).exists()
            except Exception:
                _rfq_exists = False
        else:
            _rfq_exists = False
        rfq_scope_source = "generated_rfq" if _rfq_exists else "quotation_fallback"

    total_line_count = len(line_items)
    db_line_count = len([li for li in line_items if li.benchmark_source == "CORRIDOR_DB"])
    market_line_count = len([li for li in line_items if li.benchmark_source == "PERPLEXITY_LIVE"])
    no_benchmark_line_count = len(
        [
            li for li in line_items
            if li.benchmark_source not in {"CORRIDOR_DB", "PERPLEXITY_LIVE"} or li.benchmark_mid is None
        ]
    )
    if total_line_count > 0:
        db_coverage_pct = round((db_line_count / float(total_line_count)) * 100.0, 1)
        market_coverage_pct = round((market_line_count / float(total_line_count)) * 100.0, 1)
        gap_pct = round((no_benchmark_line_count / float(total_line_count)) * 100.0, 1)
    else:
        db_coverage_pct = 0.0
        market_coverage_pct = 0.0
        gap_pct = 0.0

    flow_explanations = []
    if quotation_mode == "SINGLE_VENDOR":
        flow_explanations.append(
            "Single vendor quotation uploaded. Decision support is benchmark-vs-quote and RFQ alignment only."
        )
    elif quotation_mode == "MULTI_VENDOR":
        flow_explanations.append(
            "Multiple vendor quotations uploaded. Benchmarked line coverage is used for cross-vendor ranking and recommendation."
        )
    else:
        flow_explanations.append(
            "No quotations are currently uploaded. Upload one or more vendor files to run benchmarking."
        )

    if rfq_mode == "SYSTEM_RFQ":
        flow_explanations.append("RFQ context is sourced from procurement request records and used as the baseline scope.")
    elif rfq_mode == "UPLOADED_RFQ":
        flow_explanations.append("RFQ context is from an uploaded RFQ file and used as the baseline scope.")
    elif rfq_mode == "MANUAL_RFQ":
        flow_explanations.append("RFQ context is a manual reference string and used for traceability in review.")
    else:
        flow_explanations.append("No RFQ was provided. Benchmarking is performed directly from quotation line items.")

    flow_explanations.append(
        f"DB-first coverage: {db_line_count}/{total_line_count} lines ({db_coverage_pct}%) matched benchmark corridor rules."
    )
    flow_explanations.append(
        f"Market fallback coverage: {market_line_count}/{total_line_count} lines ({market_coverage_pct}%) used live market research when DB corridor data was missing."
    )
    if no_benchmark_line_count > 0:
        flow_explanations.append(
            f"Remaining gap: {no_benchmark_line_count}/{total_line_count} lines ({gap_pct}%) still need manual review because benchmark values were not resolved."
        )

    benchmark_flow_summary = {
        "quotation_mode": quotation_mode,
        "quotation_mode_label": quotation_mode_label,
        "quotation_count": quotation_count,
        "rfq_mode": rfq_mode,
        "rfq_label": rfq_label,
        "rfq_ref": rfq_ref,
        "rfq_present": rfq_present,
        "rfq_scope_source": rfq_scope_source,
        "total_line_count": total_line_count,
        "db_line_count": db_line_count,
        "market_line_count": market_line_count,
        "no_benchmark_line_count": no_benchmark_line_count,
        "db_coverage_pct": db_coverage_pct,
        "market_coverage_pct": market_coverage_pct,
        "gap_pct": gap_pct,
        "details": flow_explanations,
    }

    # Category filter
    # ------------------------------------------------------------------ #
    # Unfiltered KPI values (computed BEFORE applying URL filters)         #
    # ------------------------------------------------------------------ #
    all_line_items_unfiltered = []
    for q in quotations:
        all_line_items_unfiltered.extend(list(q.line_items.filter(is_active=True)))

    total_line_items_count = len(all_line_items_unfiltered)
    high_variance_items_count = sum(
        1 for li in all_line_items_unfiltered if li.variance_status == "HIGH"
    )
    potential_savings = 0.0
    variance_values = []
    for li in all_line_items_unfiltered:
        try:
            if (
                li.benchmark_mid is not None
                and li.quoted_unit_rate is not None
                and li.quantity is not None
            ):
                diff = (float(li.quoted_unit_rate) - float(li.benchmark_mid)) * float(li.quantity)
                if diff > 0:
                    potential_savings += diff
        except (TypeError, ValueError):
            pass
        if li.variance_pct is not None:
            try:
                variance_values.append(float(li.variance_pct))
            except (TypeError, ValueError):
                pass
    avg_variance_pct = (
        round(sum(variance_values) / len(variance_values), 1) if variance_values else None
    )

    # AI insights: source from AI_Insights_Analyzer AgentRun payload
    insight_items = []
    try:
        from apps.agents.models import AgentRun
        _latest_ai_run = (
            AgentRun.objects.filter(
                input_payload__benchmark_request_pk=bench_request.pk,
                input_payload__agent_stage="AI_Insights_Analyzer",
            )
            .order_by("-started_at", "-pk")
            .first()
        )
        if _latest_ai_run:
            _payload = _latest_ai_run.output_payload or {}
            _insights = _payload.get("insights") or []
            insight_items = [str(x).strip() for x in _insights if str(x).strip()]
            if not insight_items:
                _summary = str(_payload.get("summary") or "").strip()
                if _summary:
                    insight_items = [_summary]
    except Exception:
        pass

    # Negotiation talking points: prefer persisted result notes, fallback to Negotiation_Talking_Points AgentRun payload
    if not negotiation_notes:
        try:
            from apps.agents.models import AgentRun
            _latest_negotiation_run = (
                AgentRun.objects.filter(
                    input_payload__benchmark_request_pk=bench_request.pk,
                    input_payload__agent_stage="Negotiation_Talking_Points",
                )
                .order_by("-started_at", "-pk")
                .first()
            )
            if _latest_negotiation_run:
                _payload = _latest_negotiation_run.output_payload or {}
                _notes = _payload.get("talking_points") or []
                negotiation_notes = [str(x).strip() for x in _notes if str(x).strip()]
                if not negotiation_notes:
                    _summary = str(_payload.get("summary") or "").strip()
                    if _summary:
                        negotiation_notes = [_summary]
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Vendor names ordered (stable across pivot & filter dropdowns)        #
    # ------------------------------------------------------------------ #
    vendor_names_ordered = [c["supplier_name"] for c in vendor_cards]

    # ------------------------------------------------------------------ #
    # Pivot table: group lines by normalised description across vendors    #
    # ------------------------------------------------------------------ #
    def _normalise(text):
        return " ".join((text or "").lower().split())[:120]

    pivot_map = {}
    for card in vendor_cards:
        vname = card["supplier_name"]
        for li in card.get("line_items", []):
            key = _normalise(li.description)
            if key not in pivot_map:
                pivot_map[key] = {
                    "description": (li.description or "").strip(),
                    "category": li.category or "UNCATEGORIZED",
                    "vendor_rates": {},
                    "benchmark_mid": li.benchmark_mid,
                    "status_votes": [],
                }
            pivot_map[key]["vendor_rates"][vname] = li
            pivot_map[key]["status_votes"].append(li.variance_status)
            if pivot_map[key]["benchmark_mid"] is None and li.benchmark_mid is not None:
                pivot_map[key]["benchmark_mid"] = li.benchmark_mid

    _STATUS_RANK = {"HIGH": 3, "MODERATE": 2, "WITHIN_RANGE": 1, "NEEDS_REVIEW": 0}
    pivot_lines = []
    for _norm_desc, row_data in pivot_map.items():
        vendor_rate_map = row_data["vendor_rates"]
        rates = [
            float(li.quoted_unit_rate)
            for li in vendor_rate_map.values()
            if li.quoted_unit_rate is not None
        ]
        best_rate = min(rates) if rates else None
        worst_rate = max(rates) if rates else None

        rate_list = []
        for vname in vendor_names_ordered:
            li = vendor_rate_map.get(vname)
            if li is None:
                rate_list.append(
                    {"vendor_name": vname, "rate": None, "variance_pct": None, "is_best": False, "is_worst": False}
                )
            else:
                rate = float(li.quoted_unit_rate) if li.quoted_unit_rate is not None else None
                rate_list.append({
                    "vendor_name": vname,
                    "rate": rate,
                    "variance_pct": float(li.variance_pct) if li.variance_pct is not None else None,
                    "is_best": (
                        rate is not None
                        and best_rate is not None
                        and rate == best_rate
                        and len(rates) > 1
                    ),
                    "is_worst": (
                        rate is not None
                        and worst_rate is not None
                        and rate == worst_rate
                        and len(rates) > 1
                        and best_rate != worst_rate
                    ),
                })

        votes = row_data["status_votes"]
        pivot_variance_status = (
            max(votes, key=lambda s: _STATUS_RANK.get(s, 0)) if votes else "NEEDS_REVIEW"
        )
        pivot_lines.append({
            "description": row_data["description"],
            "category": row_data["category"],
            "rate_list": rate_list,
            "benchmark_mid": row_data["benchmark_mid"],
            "pivot_variance_status": pivot_variance_status,
        })

    # Category filter
    cat_filter = request.GET.get("category", "")
    if cat_filter:
        line_items = [i for i in line_items if i.category == cat_filter]
        pivot_lines = [pl for pl in pivot_lines if pl["category"] == cat_filter]

    # Variance filter
    var_filter = request.GET.get("variance", "")
    if var_filter:
        line_items = [i for i in line_items if i.variance_status == var_filter]
        pivot_lines = [pl for pl in pivot_lines if pl["pivot_variance_status"] == var_filter]

    # Download history
    run_logs = list(
        BenchmarkRunLog.objects.filter(request=bench_request).order_by("-created_at")[:50]
    )
    show_history = request.GET.get("show_history") == "1"
    analysis_run_count = BenchmarkRunLog.objects.filter(
        request=bench_request, run_type=BenchmarkRunLog.RunType.ANALYSIS
    ).count()

    # ---------------------------------------------------------------------- #
    # Developer trace -- full end-to-end pipeline traceability               #
    # ---------------------------------------------------------------------- #
    from apps.benchmarking.services.benchmark_service import BenchmarkEngine  # noqa: E402
    from apps.benchmarking.models import BenchmarkCorridorRule  # noqa: E402

    _geo_for_trace = bench_request.geography or "UAE"
    _threshold_cache: dict = {}  # category -> {within, moderate}

    _ROUTING_LABEL = {
        "CORRIDOR_DB": "DB_BENCHMARK",
        "PERPLEXITY_LIVE": "MARKET_DATA",
        "MANUAL": "MANUAL",
        "NONE": "NEEDS_REVIEW",
    }

    # Real agent stage order and metadata
    _AGENT_STAGE_META = [
        {
            "stage_key": "Azure_DI_Extraction",
            "display_name": "AzureDocumentIntelligenceAgentBM",
            "short_name": "Azure DI Extraction",
            "description": "Extracts raw tables, KV pairs, and text from uploaded PDF via Azure Document Intelligence API",
            "api_call": "Azure AI Document Intelligence -- POST /documentModels/prebuilt-layout:analyze",
            "color": "#0891b2",
            "badge_bg": "#0c4a6e",
            "badge_fg": "#7dd3fc",
            "icon": "bi-file-earmark-text",
        },
        {
            "stage_key": "Line_Item_Understanding",
            "display_name": "BenchmarkLineItemUnderstandingAgentBM",
            "short_name": "Line Item Understanding",
            "description": "LLM normalizes raw DI rows: filters noise (totals/VAT rows), infers supplier name, standardizes descriptions/qty/rates",
            "api_call": "Azure OpenAI GPT-4o -- chat completions",
            "color": "#7c3aed",
            "badge_bg": "#4c1d95",
            "badge_fg": "#c4b5fd",
            "icon": "bi-magic",
        },
        {
            "stage_key": "Decision_Maker",
            "display_name": "BenchmarkDecisionMakerAgentBM",
            "short_name": "Decision Maker Agent",
            "description": "Routes each line item: MARKET_DATA (live Perplexity) or DB_BENCHMARK (corridor table) based on pricing_type + corridor availability",
            "api_call": "Azure OpenAI GPT-4o -- chat completions + BenchmarkCorridorRule DB query",
            "color": "#d97706",
            "badge_bg": "#78350f",
            "badge_fg": "#fcd34d",
            "icon": "bi-signpost-split",
        },
        {
            "stage_key": "Market_Data_Analyzer",
            "display_name": "BenchmarkMarketDataAnalyzerAgentBM",
            "short_name": "Market Data Analyzer",
            "description": "Fetches live market pricing for MARKET_DATA-routed lines from Perplexity API; writes live_price_json to each line item",
            "api_call": "Perplexity API -- POST /chat/completions (sonar model)",
            "color": "#059669",
            "badge_bg": "#064e3b",
            "badge_fg": "#6ee7b7",
            "icon": "bi-broadcast",
        },
        {
            "stage_key": "Benchmarking_Analyst",
            "display_name": "BenchmarkingAnalystAgentBM",
            "short_name": "Benchmarking Analyst",
            "description": "Synthesizes corridor + market data; computes variance%, classifies WITHIN_RANGE/MODERATE/HIGH using VarianceThresholdConfig from DB",
            "api_call": "Internal -- BenchmarkCorridorRule + VarianceThresholdConfig DB queries",
            "color": "#1d4ed8",
            "badge_bg": "#1e3a8a",
            "badge_fg": "#93c5fd",
            "icon": "bi-bar-chart-line",
        },
        {
            "stage_key": "Compliance_Agent",
            "display_name": "BenchmarkComplianceAgentBM",
            "short_name": "Compliance Agent",
            "description": "Validates benchmarked lines against compliance rules; flags HIGH deviation lines; generates compliance notes",
            "api_call": "Azure OpenAI GPT-4o -- chat completions",
            "color": "#be185d",
            "badge_bg": "#831843",
            "badge_fg": "#f9a8d4",
            "icon": "bi-shield-check",
        },
        {
            "stage_key": "Vendor_Recommendation",
            "display_name": "BenchmarkVendorRecommendationAgent",
            "short_name": "Vendor Recommendation",
            "description": "Ranks vendors by benchmarked deviation, coverage, and compliance",
            "api_call": "Azure OpenAI GPT-4o -- chat completions",
            "color": "#0f766e",
            "badge_bg": "#134e4a",
            "badge_fg": "#5eead4",
            "icon": "bi-trophy",
        },
        {
            "stage_key": "AI_Insights_Analyzer",
            "display_name": "BenchmarkAIAnalyzerAgentBM",
            "short_name": "AI Insights Analyzer",
            "description": "Generates detailed AI insights, risk flags, and recommended next actions from full pipeline output",
            "api_call": "Azure OpenAI GPT-4o -- chat completions",
            "color": "#9333ea",
            "badge_bg": "#581c87",
            "badge_fg": "#e9d5ff",
            "icon": "bi-stars",
        },
        {
            "stage_key": "Negotiation_Talking_Points",
            "display_name": "BenchmarkNegotiationTalkingPointsAgentBM",
            "short_name": "Negotiation Talking Points",
            "description": "Builds vendor-facing negotiation talking points, fallback positions, and red flags from benchmark outcomes",
            "api_call": "Azure OpenAI GPT-4o -- chat completions",
            "color": "#b45309",
            "badge_bg": "#78350f",
            "badge_fg": "#fcd34d",
            "icon": "bi-chat-left-dots",
        },
    ]

    _AGENT_STAGE_META_BY_KEY = {
        _meta["stage_key"]: _meta for _meta in _AGENT_STAGE_META
    }

    def _actor_label(_user) -> str:
        if not _user:
            return "System"
        try:
            return _user.get_short_name() or _user.get_full_name() or _user.email or "System"
        except Exception:
            return getattr(_user, "email", "") or "System"

    def _duration_display(_duration_ms):
        if _duration_ms is None:
            return ""
        return f"{_duration_ms}ms" if _duration_ms < 1000 else f"{_duration_ms/1000:.2f}s"

    def _status_tone(_status: str) -> str:
        _norm = (_status or "").upper()
        if _norm in {"COMPLETED", "SUCCESS", "DONE"}:
            return "success"
        if _norm in {"FAILED", "ERROR"}:
            return "danger"
        if _norm in {"RUNNING", "PROCESSING", "STARTED", "PENDING"}:
            return "warning"
        return "secondary"

    # Query real AgentRun records for this benchmark request
    _agent_runs_by_stage: dict = {}
    try:
        from apps.agents.models import AgentRun
        _all_agent_runs = list(
            AgentRun.objects.filter(
                input_payload__benchmark_request_pk=bench_request.pk,
            ).order_by("started_at", "pk")
        )
        for _ar in _all_agent_runs:
            _ip = _ar.input_payload or {}
            _stage_key = _ip.get("agent_stage", "")
            if _stage_key:
                # Keep the most recent run for each stage key
                _agent_runs_by_stage[_stage_key] = _ar
    except Exception:
        _all_agent_runs = []

    # Attach real AgentRun data to each stage meta dict
    dev_pipeline_stages = []
    for _smeta in _AGENT_STAGE_META:
        _ar = _agent_runs_by_stage.get(_smeta["stage_key"])
        _run_data = None
        if _ar:
            _op = _ar.output_payload or {}
            _ip = _ar.input_payload or {}
            _dur = _duration_display(_ar.duration_ms)
            _run_data = {
                "pk": _ar.pk,
                "status": _ar.status,
                "started_at": _ar.started_at,
                "completed_at": _ar.completed_at,
                "duration_display": _dur,
                "duration_ms": _ar.duration_ms,
                "confidence": _ar.confidence,
                "llm_model_used": _ar.llm_model_used or "N/A",
                "invocation_reason": _ar.invocation_reason or "",
                "trace_id": _ar.trace_id or "",
                "input_payload": _ip,
                "output_payload": _op,
                "output_summary": _op.get("summary") or _op.get("message") or "",
                "error_message": getattr(_ar, "error_message", "") or "",
            }
        dev_pipeline_stages.append({**_smeta, "run": _run_data})

    observed_stage_keys = set()
    dev_agent_runs = []
    for _seq, _ar in enumerate(_all_agent_runs, start=1):
        _ip = _ar.input_payload or {}
        _op = _ar.output_payload or {}
        _stage_key = (_ip.get("agent_stage") or "").strip()
        if not _stage_key:
            _invocation = (_ar.invocation_reason or "").strip()
            _stage_key = _invocation.split(":", 1)[0] if ":" in _invocation else (_ar.agent_type or "UNKNOWN_STAGE")
        _meta = _AGENT_STAGE_META_BY_KEY.get(
            _stage_key,
            {
                "stage_key": _stage_key or "UNKNOWN_STAGE",
                "display_name": _ar.agent_type or "Unknown Agent",
                "short_name": _stage_key.replace("_", " ") if _stage_key else "Unknown Stage",
                "description": _ar.invocation_reason or "Observed from AgentRun record",
                "api_call": _ar.llm_model_used or "Internal agent call",
                "color": "#64748b",
                "badge_bg": "#334155",
                "badge_fg": "#cbd5e1",
                "icon": "bi-robot",
            },
        )
        observed_stage_keys.add(_stage_key)
        dev_agent_runs.append({
            "sequence": _seq,
            "stage_key": _stage_key,
            "display_name": _meta["display_name"],
            "short_name": _meta["short_name"],
            "description": _meta["description"],
            "api_call": _meta["api_call"],
            "color": _meta["color"],
            "badge_bg": _meta["badge_bg"],
            "badge_fg": _meta["badge_fg"],
            "icon": _meta["icon"],
            "pk": _ar.pk,
            "status": _ar.status,
            "tone": _status_tone(_ar.status),
            "started_at": _ar.started_at,
            "completed_at": _ar.completed_at,
            "duration_display": _duration_display(_ar.duration_ms),
            "duration_ms": _ar.duration_ms,
            "confidence": _ar.confidence,
            "llm_model_used": _ar.llm_model_used or "N/A",
            "trace_id": _ar.trace_id or "",
            "invocation_reason": _ar.invocation_reason or "",
            "input_payload": _ip,
            "output_payload": _op,
            "output_summary": _op.get("summary") or _op.get("message") or "",
            "error_message": getattr(_ar, "error_message", "") or "",
            "timestamp": _ar.started_at or _ar.completed_at or _ar.created_at,
        })

    dev_expected_stages = [
        _meta for _meta in _AGENT_STAGE_META if _meta["stage_key"] not in observed_stage_keys
    ]
    dev_pipeline_summary = {
        "expected_stages": len(_AGENT_STAGE_META),
        "observed_runs": len(dev_agent_runs),
        "completed_runs": sum(1 for _run in dev_agent_runs if (_run["status"] or "").upper() == "COMPLETED"),
        "failed_runs": sum(1 for _run in dev_agent_runs if (_run["status"] or "").upper() == "FAILED"),
        "pending_runs": len(dev_expected_stages),
    }

    dev_timeline_events = []

    def _push_timeline_event(**kwargs):
        dev_timeline_events.append(kwargs)

    _push_timeline_event(
        source="BenchmarkRequest",
        title="Request created",
        detail="Benchmark request created for {} / {}".format(
            bench_request.geography or "Unknown geography",
            bench_request.scope_type or "Unknown scope",
        ),
        timestamp=bench_request.created_at,
        tone="primary",
        icon="bi-plus-circle-fill",
        actor=_actor_label(bench_request.submitted_by),
        badge=bench_request.status,
        extra={
            "request_pk": bench_request.pk,
            "store_type": bench_request.store_type or "",
            "rfq_ref": bench_request.rfq_ref or "",
        },
    )

    for _q in bench_request.quotations.filter(is_active=True).order_by("created_at"):
        _blob_name = (_q.blob_name or "").strip()
        _blob_url = (_q.blob_url or "").strip()
        _blob_uploaded = bool(_blob_name or _blob_url)
        _push_timeline_event(
            source="BenchmarkQuotation",
            title="Quotation uploaded -- {}".format((_q.supplier_name or "Unknown supplier").strip() or "Unknown supplier"),
            detail="Blob: {} | Extraction: {} | Ref: {}".format(
                "uploaded" if _blob_uploaded else "missing",
                _q.extraction_status,
                _q.quotation_ref or "N/A",
            ),
            timestamp=_q.created_at,
            tone="success" if _blob_uploaded else "warning",
            icon="bi-cloud-upload-fill" if _blob_uploaded else "bi-exclamation-triangle-fill",
            actor="Upload flow",
            badge=_q.extraction_status,
            extra={
                "quotation_pk": _q.pk,
                "blob_name": _blob_name or "",
                "blob_url": _blob_url or "",
                "local_file": bool(_q.document),
            },
        )

    for _log in sorted(run_logs, key=lambda _item: _item.created_at):
        _log_title = {
            BenchmarkRunLog.RunType.ANALYSIS: "Analysis run logged",
            BenchmarkRunLog.RunType.EXPORT_CSV: "CSV export downloaded",
            BenchmarkRunLog.RunType.EXPORT_PDF: "PDF export downloaded",
        }.get(_log.run_type, _log.run_type)
        _push_timeline_event(
            source="BenchmarkRunLog",
            title=_log_title,
            detail="Run type: {} | Status: {} | Run #: {}".format(
                _log.run_type,
                _log.status,
                _log.run_number or "-",
            ),
            timestamp=_log.created_at,
            tone=_status_tone(_log.status),
            icon="bi-clock-history",
            actor=_actor_label(_log.triggered_by),
            badge=_log.status,
            extra={
                "notes": _log.notes or "",
                "run_number": _log.run_number,
            },
        )

    for _run in dev_agent_runs:
        _push_timeline_event(
            source="AgentRun",
            title="{} {}".format(_run["display_name"], (_run["status"] or "").title()),
            detail="{} | Model: {} | Duration: {}".format(
                _run["short_name"],
                _run["llm_model_used"],
                _run["duration_display"] or "n/a",
            ),
            timestamp=_run["timestamp"],
            tone=_run["tone"],
            icon=_run["icon"],
            actor=_run["display_name"],
            badge=_run["status"],
            extra={
                "agent_run_pk": _run["pk"],
                "trace_id": _run["trace_id"],
                "summary": _run["output_summary"],
                "error": _run["error_message"],
            },
        )

    if result:
        _push_timeline_event(
            source="BenchmarkResult",
            title="Benchmark result generated",
            detail="Overall status: {} | Deviation: {}".format(
                result.overall_status,
                "{:+.2f}%".format(result.overall_deviation_pct) if result.overall_deviation_pct is not None else "N/A",
            ),
            timestamp=result.created_at or bench_request.updated_at or bench_request.created_at,
            tone=_status_tone(result.overall_status),
            icon="bi-graph-up-arrow",
            actor="Benchmark Engine",
            badge=result.overall_status,
            extra={
                "lines_within_range": result.lines_within_range,
                "lines_moderate": result.lines_moderate,
                "lines_high": result.lines_high,
                "lines_needs_review": result.lines_needs_review,
            },
        )

    dev_timeline_events.sort(
        key=lambda _event: (
            _event.get("timestamp") is None,
            _event.get("timestamp") or bench_request.created_at,
            _event.get("title") or "",
        )
    )

    # Per-quotation trace (real data, no fallbacks to "--")
    dev_trace_quotations = []
    for _q in bench_request.quotations.filter(is_active=True).order_by("created_at"):
        _q_line_traces = []
        for _li in _q.line_items.filter(is_active=True).order_by("line_number"):
            _cat = _li.category or "UNCATEGORIZED"
            if _cat not in _threshold_cache:
                try:
                    _w, _m = BenchmarkEngine._resolve_variance_thresholds(_cat, _geo_for_trace)
                except Exception:
                    _w, _m = BenchmarkEngine._resolve_variance_thresholds("ALL", "ALL")
                _threshold_cache[_cat] = {"within": _w, "moderate": _m}
            _thresholds = _threshold_cache[_cat]

            _routing = _ROUTING_LABEL.get(_li.benchmark_source or "NONE", "NEEDS_REVIEW")

            # Exact variance formula string
            _variance_formula = None
            if _li.quoted_unit_rate is not None and _li.benchmark_mid is not None:
                try:
                    _bm = float(_li.benchmark_mid)
                    _qr = float(_li.quoted_unit_rate)
                    if _bm != 0:
                        _cpct = ((_qr - _bm) / _bm) * 100
                        _variance_formula = (
                            f"({_qr:.2f} - {_bm:.2f}) / {_bm:.2f} * 100 = {_cpct:+.2f}%"
                        )
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            # Market data
            _lp = _li.live_price_json or {}
            _market_price_used = None
            _market_citations = []
            if _routing == "MARKET_DATA" and _lp:
                _market_price_used = _lp.get("price") or _lp.get("mid_rate") or _lp.get("market_mid")
                _market_citations = (_lp.get("citations") or [])[:5]

            # Full corridor detail from DB
            _corridor_detail = None
            if _li.corridor_rule_code:
                try:
                    _cr = BenchmarkCorridorRule.objects.filter(
                        rule_code=_li.corridor_rule_code, is_active=True
                    ).first()
                    if _cr:
                        _corridor_detail = {
                            "rule_code": _cr.rule_code,
                            "name": _cr.name,
                            "scope_type": _cr.scope_type,
                            "geography": _cr.geography,
                            "uom": _cr.uom,
                            "min_rate": float(_cr.min_rate),
                            "mid_rate": float(_cr.mid_rate),
                            "max_rate": float(_cr.max_rate),
                            "currency": _cr.currency,
                            "keywords": _cr.keywords,
                            "priority": _cr.priority,
                        }
                except Exception:
                    pass

            _q_line_traces.append({
                "line_number": _li.line_number,
                "description": _li.description,
                "uom": _li.uom or "",
                "quantity": float(_li.quantity) if _li.quantity is not None else None,
                "quoted_unit_rate": float(_li.quoted_unit_rate) if _li.quoted_unit_rate is not None else None,
                "line_amount": float(_li.line_amount) if _li.line_amount is not None else None,
                "category": _li.category,
                "classification_source": _li.classification_source,
                "classification_confidence": _li.classification_confidence,
                "routing": _routing,
                "benchmark_source_raw": _li.benchmark_source,
                "corridor_rule_code": _li.corridor_rule_code,
                "benchmark_min": float(_li.benchmark_min) if _li.benchmark_min is not None else None,
                "benchmark_mid": float(_li.benchmark_mid) if _li.benchmark_mid is not None else None,
                "benchmark_max": float(_li.benchmark_max) if _li.benchmark_max is not None else None,
                "corridor_detail": _corridor_detail,
                "thresholds": _thresholds,
                "variance_formula": _variance_formula,
                "variance_pct": _li.variance_pct,
                "variance_status": _li.variance_status,
                "variance_note": _li.variance_note or "",
                "market_price_used": _market_price_used,
                "market_citations": _market_citations,
                "live_price_json_preview": json.dumps(_lp, default=str)[:500] if _lp else "",
            })

        # DI extraction metadata from the real di_extraction_json stored on quotation
        _di = _q.di_extraction_json or {}
        _di_pages = len(_di.get("pages", [])) if isinstance(_di, dict) else 0
        _di_tables = len(_di.get("tables", [])) if isinstance(_di, dict) else 0
        _di_kv_count = len(_di.get("keyValuePairs", [])) if isinstance(_di, dict) else 0

        # Blob status -- show real values, never mask with "--"
        _blob_name = (_q.blob_name or "").strip()
        _blob_url = (_q.blob_url or "").strip()
        _blob_uploaded = bool(_blob_name or _blob_url)
        _blob_missing_reason = ""
        if not _blob_uploaded:
            if _q.document:
                _blob_missing_reason = "File stored locally (pre-blob-storage era upload, no Azure blob path)"
            else:
                _blob_missing_reason = "No file found -- quotation may have been created without a document"

        dev_trace_quotations.append({
            "quotation_id": _q.pk,
            "supplier_name": (_q.supplier_name or "").strip() or "(not yet inferred)",
            "quotation_ref": _q.quotation_ref or "(no ref)",
            "created_at": _q.created_at,
            "blob_uploaded": _blob_uploaded,
            "blob_name": _blob_name or None,
            "blob_url": _blob_url or None,
            "blob_missing_reason": _blob_missing_reason,
            "has_local_file": bool(_q.document),
            "extraction_status": _q.extraction_status,
            "extraction_error": (_q.extraction_error or "").strip(),
            "di_page_count": _di_pages,
            "di_table_count": _di_tables,
            "di_kv_count": _di_kv_count,
            "raw_text_length": len(_q.extracted_text or ""),
            "line_count": len(_q_line_traces),
            "line_traces": _q_line_traces,
        })

    # Story mode summary for developer panel (simple, chronological, concrete)
    _quotation_count = len(dev_trace_quotations)
    _blob_count = sum(1 for _q in dev_trace_quotations if _q.get("blob_uploaded"))
    _di_done_count = sum(1 for _q in dev_trace_quotations if (_q.get("extraction_status") or "").upper() == "DONE")
    _di_failed_count = sum(1 for _q in dev_trace_quotations if (_q.get("extraction_status") or "").upper() == "FAILED")
    _line_total = sum(int(_q.get("line_count") or 0) for _q in dev_trace_quotations)

    dev_story_steps = [
        {
            "title": "Request created",
            "detail": "Benchmark request {} was created for geography {} and scope {}.".format(
                bench_request.pk,
                bench_request.geography or "Unknown",
                bench_request.scope_type or "Unknown",
            ),
            "tone": "primary",
        },
        {
            "title": "Files uploaded",
            "detail": "{} quotation file(s) uploaded by users.".format(_quotation_count),
            "tone": "primary",
        },
        {
            "title": "Azure Blob storage",
            "detail": "{} of {} file(s) stored in Azure Blob (blob_name/blob_url present).".format(
                _blob_count,
                _quotation_count,
            ),
            "tone": "success" if _quotation_count and _blob_count == _quotation_count else "warning",
        },
        {
            "title": "Azure Document Intelligence extraction",
            "detail": "DONE: {} file(s), FAILED: {} file(s), Pending/other: {} file(s).".format(
                _di_done_count,
                _di_failed_count,
                max(_quotation_count - _di_done_count - _di_failed_count, 0),
            ),
            "tone": "success" if _di_failed_count == 0 else "warning",
        },
        {
            "title": "Line items extracted",
            "detail": "Total extracted line items across all files: {}.".format(_line_total),
            "tone": "primary",
        },
        {
            "title": "Agent pipeline",
            "detail": "Observed agent runs: {} (completed {}, failed {}, pending expected stages {}).".format(
                dev_pipeline_summary["observed_runs"],
                dev_pipeline_summary["completed_runs"],
                dev_pipeline_summary["failed_runs"],
                dev_pipeline_summary["pending_runs"],
            ),
            "tone": "success" if dev_pipeline_summary["failed_runs"] == 0 else "warning",
        },
    ]

    if result:
        _deviation = (
            "{:+.2f}%".format(result.overall_deviation_pct)
            if result.overall_deviation_pct is not None
            else "N/A"
        )
        dev_story_steps.append({
            "title": "Final benchmark result",
            "detail": "Overall status: {}. Overall deviation: {}. Vendors analyzed: {}.".format(
                result.overall_status,
                _deviation,
                len(vendor_cards),
            ),
            "tone": "success" if (result.overall_status or "").upper() == "WITHIN_RANGE" else "primary",
        })

    for _idx, _q in enumerate(dev_trace_quotations, start=1):
        _supplier = _q.get("supplier_name") or "Unknown supplier"
        _ref = _q.get("quotation_ref") or "(no ref)"
        _blob_text = "stored in Azure Blob" if _q.get("blob_uploaded") else "not stored in Azure Blob"
        _extract_text = (_q.get("extraction_status") or "PENDING").upper()
        _line_count = int(_q.get("line_count") or 0)
        dev_story_steps.append({
            "title": "File {} trace".format(_idx),
            "detail": "Supplier {} (ref {}) -> {}. Document Intelligence status: {}. Extracted line items: {}.".format(
                _supplier,
                _ref,
                _blob_text,
                _extract_text,
                _line_count,
            ),
            "tone": "success" if _extract_text == "DONE" else ("danger" if _extract_text == "FAILED" else "warning"),
        })

    ctx = _base_ctx(
        bench_request=bench_request,
        quotations=quotations,
        line_items=line_items,
        result=result,
        category_summary=category_summary,
        negotiation_notes=negotiation_notes,
        cat_filter=cat_filter,
        var_filter=var_filter,
        category_choices=LineCategory.CHOICES,
        variance_choices=[
            ("WITHIN_RANGE", "Within Range"),
            ("MODERATE", "Moderate"),
            ("HIGH", "High"),
            ("NEEDS_REVIEW", "Needs Review"),
        ],
        page_title=f"Results: {bench_request.title}",
        active_menu="request_list",
        category_summary_json=json.dumps(category_summary),
        quotation_summaries=quotation_summaries,
        vendor_cards=vendor_cards,
        vendor_recommendation=vendor_recommendation,
        best_vendor_summary=best_vendor_summary,
        no_vendor_recommendation=no_vendor_recommendation,
        benchmark_flow_summary=benchmark_flow_summary,
        rfq_scope_rows=rfq_scope_rows,
        # KPI cards
        total_line_items_count=total_line_items_count,
        high_variance_items_count=high_variance_items_count,
        potential_savings=potential_savings,
        avg_variance_pct=avg_variance_pct,
        # Pivot table
        pivot_lines=pivot_lines,
        vendor_names_ordered=vendor_names_ordered,
        # AI insights
        insight_items=insight_items,
        negotiation_assistant_url=reverse("benchmarking:request_negotiation_assistant", kwargs={"pk": bench_request.pk}),
        # History
        run_logs=run_logs,
        show_history=show_history,
        analysis_run_count=analysis_run_count,
        # Developer trace
        dev_trace_quotations=dev_trace_quotations,
        dev_pipeline_stages=dev_pipeline_stages,
        dev_agent_runs=dev_agent_runs,
        dev_expected_stages=dev_expected_stages,
        dev_pipeline_summary=dev_pipeline_summary,
        dev_story_steps=dev_story_steps,
        dev_timeline_events=dev_timeline_events,
        dev_request_geography=_geo_for_trace,
    )
    return render(request, "benchmarking/request_detail.html", ctx)


@login_required
@require_http_methods(["POST"])
def request_negotiation_assistant(request, pk):
    """Return a dynamic LLM-backed negotiation answer for this benchmark request."""
    bench_request = get_object_or_404(
        _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)),
        pk=pk,
    )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"success": False, "message": "Invalid JSON payload."}, status=400)

    question = str(payload.get("question") or "").strip()
    if not question:
        return JsonResponse({"success": False, "message": "Question is required."}, status=400)

    result = BenchmarkNegotiationAssistantService.answer_prompt(
        bench_request=bench_request,
        user_prompt=question,
    )

    if not result.get("success"):
        return JsonResponse({"success": False, "message": result.get("error") or "Unable to generate answer."}, status=400)

    return JsonResponse(result)


@login_required
@require_http_methods(["POST"])
def request_add_quotations(request, pk):
    """Add one or many vendor quotations (PDF/ZIP) to an existing benchmark request."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)

    quotation_ref = request.POST.get("quotation_ref", "").strip()
    uploads = _get_quotation_uploads(request)

    if not uploads:
        messages.error(request, "Quotation file is required (PDF or ZIP).")
        return redirect("benchmarking:request_detail", pk=pk)

    created_quotes, upload_error = _create_quotations_from_uploads(
        bench_request=bench_request,
        uploads=uploads,
        quotation_ref=quotation_ref,
        user=request.user,
    )
    if upload_error:
        messages.error(request, upload_error)
        return redirect("benchmarking:request_detail", pk=pk)

    result, live_result = _run_benchmark_with_live(
        bench_request=bench_request,
        user=request.user,
        tenant=getattr(request, "tenant", None),
        force_reextract=True,
    )
    if result.get("success"):
        if live_result and live_result.get("success"):
            messages.success(request, f"Added {len(created_quotes)} quotation(s). Benchmark + live market update completed.")
        else:
            messages.success(request, f"Added {len(created_quotes)} quotation(s). Benchmark update completed.")
    else:
        messages.warning(request, f"Quotations uploaded but benchmark failed: {result.get('error')}")

    return redirect("benchmarking:request_detail", pk=pk)


# --------------------------------------------------------------------------- #
# 4. Reprocess (AJAX / POST)
# --------------------------------------------------------------------------- #

@login_required
@require_http_methods(["POST"])
def request_reprocess(request, pk):
    """Re-run the benchmark engine for a request."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)
    result, live_result = _run_benchmark_with_live(
        bench_request=bench_request,
        user=request.user,
        tenant=getattr(request, "tenant", None),
    )
    run_success = bool(result.get("success"))
    BenchmarkRunLog.objects.create(
        request=bench_request,
        tenant=getattr(request, "tenant", None),
        run_type=BenchmarkRunLog.RunType.ANALYSIS,
        status=BenchmarkRunLog.RunStatus.SUCCESS if run_success else BenchmarkRunLog.RunStatus.FAILED,
        triggered_by=request.user,
        notes=(result.get("error") or "")[:500],
    )
    if run_success:
        if live_result and live_result.get("success"):
            messages.success(request, "Reprocessing + live market enrichment completed successfully.")
        else:
            messages.success(request, "Reprocessing completed successfully.")
    else:
        messages.error(request, f"Reprocessing failed: {result.get('error')}")
    from django.urls import reverse
    return redirect(reverse("benchmarking:request_detail", kwargs={"pk": pk}) + "?show_history=1")


# --------------------------------------------------------------------------- #
# 5. Export CSV
# --------------------------------------------------------------------------- #

@login_required
def request_export(request, pk):
    """Download CSV export of benchmarking results."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)
    csv_bytes = ExportService.export_request_csv(bench_request)
    BenchmarkRunLog.objects.create(
        request=bench_request,
        tenant=getattr(request, "tenant", None),
        run_type=BenchmarkRunLog.RunType.EXPORT_CSV,
        status=BenchmarkRunLog.RunStatus.SUCCESS,
        triggered_by=request.user,
    )
    slug = bench_request.title.lower().replace(" ", "_")[:40]
    response = HttpResponse(csv_bytes, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="benchmark_{slug}.csv"'
    return response


@login_required
def request_export_pdf(request, pk):
    """Download a ReportLab PDF report for a benchmarking request."""
    bench_request = get_object_or_404(
        _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk
    )
    try:
        from apps.benchmarking.services.pdf_export_service import BenchmarkPDFExportService
        pdf_bytes = BenchmarkPDFExportService.generate(bench_request)
    except Exception:
        logger.exception("PDF export failed for BenchmarkRequest pk=%s", pk)
        messages.error(request, "PDF generation failed. Please try again.")
        return redirect("benchmarking:request_detail", pk=pk)

    BenchmarkRunLog.objects.create(
        request=bench_request,
        tenant=getattr(request, "tenant", None),
        run_type=BenchmarkRunLog.RunType.EXPORT_PDF,
        status=BenchmarkRunLog.RunStatus.SUCCESS,
        triggered_by=request.user,
    )
    slug = bench_request.title.lower().replace(" ", "_")[:40]
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="benchmark_{slug}.pdf"'
    return response


# --------------------------------------------------------------------------- #
# 6. Quotations list
# --------------------------------------------------------------------------- #

@login_required
def quotation_list(request):
    """List all uploaded quotations across all requests."""
    qs = _scope_quotations(
        request,
        BenchmarkQuotation.objects.filter(is_active=True).select_related("request")
    ).order_by("-created_at")

    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(supplier_name__icontains=search)

    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(extraction_status=status_filter)

    quotations = list(qs)
    request_folder_map = {}
    done_count = 0
    pending_count = 0
    failed_count = 0

    for quotation in quotations:
        if quotation.extraction_status == "DONE":
            done_count += 1
        elif quotation.extraction_status == "FAILED":
            failed_count += 1
        else:
            pending_count += 1

        request_id = quotation.request_id
        folder = request_folder_map.get(request_id)
        if folder is None:
            folder = {
                "request": quotation.request,
                "quotations": [],
                "file_count": 0,
                "done_count": 0,
                "pending_count": 0,
                "failed_count": 0,
            }
            request_folder_map[request_id] = folder

        folder["quotations"].append(quotation)
        folder["file_count"] += 1
        if quotation.extraction_status == "DONE":
            folder["done_count"] += 1
        elif quotation.extraction_status == "FAILED":
            folder["failed_count"] += 1
        else:
            folder["pending_count"] += 1

    request_folders = sorted(
        request_folder_map.values(),
        key=lambda folder: (folder["request"].created_at, folder["request"].id),
        reverse=True,
    )

    ctx = _base_ctx(
        quotations=quotations,
        request_folders=request_folders,
        total_folders=len(request_folders),
        done_count=done_count,
        pending_count=pending_count,
        failed_count=failed_count,
        search=search,
        status_filter=status_filter,
        extraction_status_choices=[("PENDING", "Pending"), ("DONE", "Done"), ("FAILED", "Failed")],
        page_title="All Quotations",
        active_menu="quotation_list",
    )
    return render(request, "benchmarking/quotation_list.html", ctx)


# --------------------------------------------------------------------------- #
# 6b. Quotation Detail
# --------------------------------------------------------------------------- #

@login_required
def quotation_detail(request, pk):
    """Detailed view for a single quotation -- shows all extracted line items."""
    quotation = get_object_or_404(_scope_quotations(request, BenchmarkQuotation.objects.filter(is_active=True)), pk=pk)
    bench_request = quotation.request

    line_items = quotation.line_items.filter(is_active=True).order_by("line_number")

    # Category filter
    cat_filter = request.GET.get("category", "")
    if cat_filter:
        line_items = line_items.filter(category=cat_filter)

    # Variance filter
    var_filter = request.GET.get("variance", "")
    if var_filter:
        line_items = line_items.filter(variance_status=var_filter)

    ctx = _base_ctx(
        quotation=quotation,
        bench_request=bench_request,
        line_items=line_items,
        cat_filter=cat_filter,
        var_filter=var_filter,
        category_choices=LineCategory.CHOICES,
        variance_choices=[
            ("WITHIN_RANGE", "Within Range"),
            ("MODERATE", "Moderate"),
            ("HIGH", "High"),
            ("NEEDS_REVIEW", "Needs Review"),
        ],
        page_title=f"Quotation: {quotation.supplier_name or 'Unknown'} -- {bench_request.title}",
        active_menu="quotation_list",
    )
    return render(request, "benchmarking/quotation_detail.html", ctx)


# --------------------------------------------------------------------------- #
# 7. Reports
# --------------------------------------------------------------------------- #

@login_required
def reports(request):
    """Reports overview -- aggregated stats across all completed requests."""
    from django.db.models import Avg, Count, Q

    completed_qs = _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True, status="COMPLETED"))

    # Counts by status
    total_requests = _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)).count()
    completed_count = completed_qs.count()
    high_variance_count = completed_qs.filter(result__overall_status="HIGH").count() if completed_qs.exists() else 0

    # Counts by geography
    geo_breakdown = (
        _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True))
        .values("geography")
        .annotate(count=Count("id"))
        .order_by("geography")
    )

    # Counts by scope
    scope_breakdown = (
        _scope_requests(request, BenchmarkRequest.objects.filter(is_active=True))
        .values("scope_type")
        .annotate(count=Count("id"))
        .order_by("scope_type")
    )

    # Recent completed requests
    recent = completed_qs.select_related("result").order_by("-updated_at")[:10]

    ctx = _base_ctx(
        total_requests=total_requests,
        completed_count=completed_count,
        high_variance_count=high_variance_count,
        geo_breakdown=list(geo_breakdown),
        scope_breakdown=list(scope_breakdown),
        recent_requests=recent,
        page_title="Benchmarking Reports",
        active_menu="reports",
    )
    return render(request, "benchmarking/reports.html", ctx)


# --------------------------------------------------------------------------- #
# 8. Configurations (Corridor Rules CRUD)
# --------------------------------------------------------------------------- #

@login_required
def configurations(request):
    """Benchmark Configurations -- Category Master, Benchmark Table, Variance Thresholds."""
    category_items = _build_category_config_items()
    category_codes = [item["code"] for item in category_items]
    stats = {
        "category_total": len(category_items),
        "category_active": len([item for item in category_items if item.get("is_active")]),
        "corridor_total": BenchmarkCorridorRule.objects.count(),
        "corridor_active": BenchmarkCorridorRule.objects.filter(is_active=True).count(),
        "threshold_total": VarianceThresholdConfig.objects.count(),
        "threshold_active": VarianceThresholdConfig.objects.filter(is_active=True).count(),
    }
    scope_choices_with_all = ScopeType.CHOICES + [("ALL", "All Scopes")]
    geo_choices_with_all = Geography.CHOICES + [("ALL", "All Geographies")]
    ctx = _base_ctx(
        stats=stats,
        category_options_json=json.dumps([
            {"code": item["code"], "label": item["label"], "is_active": item["is_active"]}
            for item in category_items
        ]),
        scope_choices_with_all=scope_choices_with_all,
        geo_choices_with_all=geo_choices_with_all,
        page_title="Benchmark Configurations",
        active_menu="configurations",
        active_tab="categories",
    )
    return render(request, "benchmarking/configurations.html", ctx)


# --------------------------------------------------------------------------- #
# 8a. Configurations -- Category Master API
# --------------------------------------------------------------------------- #

_CAT_DESCRIPTIONS = {
    "EQUIPMENT":     "Main HVAC units: VRF/VRV, Chillers, Split ACs, Packaged Units, FCUs, AHUs",
    "PIPING":        "Copper piping, CHW piping, refrigerant piping, fittings, supports, valves and accessories",
    "ELECTRICAL":    "Power cabling, MCC/DB interfaces, isolators, control wiring, panels and electrical accessories",
    "CONTROLS":      "BMS/DDC controls, control panels, cabling, sensors, actuators",
    "DUCTING":       "GI ductwork, flexible ducts, grilles, diffusers, louvres",
    "AIR_DISTRIBUTION": "Diffusers, grilles, dampers, louvers, VAV terminals and air-side distribution accessories",
    "INSULATION":    "Pipe insulation (Armaflex/NBR), duct insulation (glass wool, foam)",
    "ACCESSORIES":   "Accessories such as dampers, louvers, volume control parts and related HVAC fittings",
    "INSTALLATION":  "Labour for mechanical installation, fix & fit, pipework, electrical works",
    "TC":            "Testing, balancing, commissioning, startup, handover",
    "UNCATEGORIZED": "Items not yet classified into a specific category",
}

_CATEGORY_MASTER_DEFAULTS = [
    {"code": "EQUIPMENT", "name": "Equipment", "pricing_type": PricingType.MARKET, "sort_order": 1},
    {"code": "DUCTING", "name": "Ducting", "pricing_type": PricingType.HYBRID, "sort_order": 2},
    {"code": "PIPING", "name": "Piping", "pricing_type": PricingType.HYBRID, "sort_order": 3},
    {"code": "ELECTRICAL", "name": "Electrical", "pricing_type": PricingType.BENCHMARK, "sort_order": 4},
    {"code": "CONTROLS", "name": "Controls", "pricing_type": PricingType.MARKET, "sort_order": 5},
    {"code": "AIR_DISTRIBUTION", "name": "Air Distribution", "pricing_type": PricingType.BENCHMARK, "sort_order": 6},
    {"code": "INSTALLATION", "name": "Installation", "pricing_type": PricingType.BENCHMARK, "sort_order": 7},
    {"code": "TC", "name": "Testing & Commissioning", "pricing_type": PricingType.BENCHMARK, "sort_order": 8},
    {"code": "ACCESSORIES", "name": "Accessories (Dampers, Louvers, etc.)", "pricing_type": PricingType.MARKET, "sort_order": 9},
    {"code": "INSULATION", "name": "Insulation", "pricing_type": PricingType.HYBRID, "sort_order": 10},
]

_CATEGORY_TYPE_ACTION_MAP = {
    PricingType.MARKET: "Fetch prices from marketplace or distributor sources and compute min/avg/max range for comparison.",
    PricingType.HYBRID: "Use benchmark ranges with optional AI-based regional and material trend adjustments.",
    PricingType.BENCHMARK: "Use predefined benchmark corridor rates and apply only controlled project-scale adjustments.",
}


def _category_defaults_by_code():
    return {
        row["code"]: {
            "code": row["code"],
            "name": row["name"],
            "description": _CAT_DESCRIPTIONS.get(row["code"], ""),
            "pricing_type": row["pricing_type"],
            "keywords_csv": "",
            "sort_order": row["sort_order"],
            "is_active": True,
        }
        for row in _CATEGORY_MASTER_DEFAULTS
    }


def _default_category_sort_order() -> int:
    existing_max = CategoryMaster.objects.order_by("-sort_order").values_list("sort_order", flat=True).first()
    if existing_max is None:
        return 100
    return int(existing_max) + 1


def _validate_category_payload(code: str, name: str, description: str, keywords_csv: str, pricing_type: str, *, exclude_pk=None):
    if not code:
        return "Category code is required."
    if not re.fullmatch(r"[A-Z0-9_]{2,30}", code):
        return "Category code must use only letters, numbers, or underscore and be 2-30 characters long."
    if not name:
        return "Category name is required."

    qs = CategoryMaster.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    if qs.filter(code__iexact=code).exists():
        return "Category code already exists. Please use a unique code."
    if qs.filter(name__iexact=name).exists():
        return "Category name already exists. Please use a different name."

    normalized_description = " ".join((description or "").split()).strip().lower()
    normalized_keywords = ",".join(
        sorted({part.strip().lower() for part in (keywords_csv or "").split(",") if part.strip()})
    )
    normalized_pricing_type = str(pricing_type or "").strip().upper()

    for row in qs.only("code", "name", "description", "keywords_csv", "pricing_type"):
        row_description = " ".join((row.description or "").split()).strip().lower()
        row_keywords = ",".join(
            sorted({part.strip().lower() for part in (row.keywords_csv or "").split(",") if part.strip()})
        )
        if (
            row.name.strip().lower() == name.strip().lower()
            and row_description == normalized_description
            and row_keywords == normalized_keywords
            and str(row.pricing_type or "").strip().upper() == normalized_pricing_type
        ):
            return f"Same category details already exist under code {row.code}."
    return None


def _build_category_config_items(query_text=""):
    from django.db.models import Count

    q = (query_text or "").strip().lower()
    defaults = _category_defaults_by_code()
    db_rows = {
        row.code: row
        for row in CategoryMaster.objects.all().order_by("sort_order", "code")
    }
    corridor_counts = dict(
        BenchmarkCorridorRule.objects.filter(is_active=True)
        .values("category")
        .annotate(cnt=Count("id"))
        .values_list("category", "cnt")
    )
    corridor_keywords = {}
    for category, keywords in BenchmarkCorridorRule.objects.filter(is_active=True).exclude(keywords="").values_list("category", "keywords"):
        corridor_keywords.setdefault(category, [])
        corridor_keywords[category].extend([k.strip() for k in keywords.split(",") if k.strip()])

    items = []
    ordered_codes = list(defaults.keys())
    ordered_codes.extend([code for code in db_rows.keys() if code not in defaults])

    for code in ordered_codes:
        default_row = defaults.get(code, {})
        db_row = db_rows.get(code)
        if not db_row and not default_row:
            continue
        label = db_row.name if db_row and db_row.name else default_row.get("name", code.replace("_", " ").title())
        description = db_row.description if db_row and db_row.description else default_row.get("description", "")
        pricing_type = str(db_row.pricing_type if db_row and db_row.pricing_type else default_row.get("pricing_type", PricingType.BENCHMARK)).upper()
        if pricing_type not in {PricingType.MARKET, PricingType.HYBRID, PricingType.BENCHMARK}:
            pricing_type = PricingType.BENCHMARK
        sort_order = db_row.sort_order if db_row else default_row.get("sort_order", 100)
        is_active = db_row.is_active if db_row else default_row.get("is_active", True)
        own_keywords = db_row.keywords_csv if db_row and db_row.keywords_csv else ""
        merged_keywords = [k.strip() for k in own_keywords.split(",") if k.strip()]
        for keyword in corridor_keywords.get(code, []):
            if keyword not in merged_keywords:
                merged_keywords.append(keyword)
        sample_keywords = ", ".join(merged_keywords[:6])
        haystack = " ".join([
            code,
            label,
            description,
            own_keywords,
            sample_keywords,
            pricing_type,
        ]).lower()
        if q and q not in haystack:
            continue
        items.append({
            "code": code,
            "label": label,
            "description": description,
            "keywords_csv": own_keywords,
            "type": pricing_type,
            "rule_count": corridor_counts.get(code, 0),
            "sample_keywords": sample_keywords,
            "what_it_should_do": _CATEGORY_TYPE_ACTION_MAP.get(pricing_type, ""),
            "is_active": is_active,
            "sort_order": sort_order,
        })
    items.sort(key=lambda item: (item["sort_order"], item["label"], item["code"]))
    return items


def _allowed_category_codes():
    return {
        item["code"]
        for item in _build_category_config_items()
        if item.get("is_active")
    }


@login_required
def api_bench_categories(request):
    """Return and update DB-backed CategoryMaster values used by the configurations page."""
    import json as _json

    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

        code = str(body.get("code") or "").strip().upper()
        pricing_type = str(body.get("type") or "").strip().upper()
        defaults = _category_defaults_by_code()
        valid_types = {PricingType.MARKET, PricingType.HYBRID, PricingType.BENCHMARK}
        if pricing_type not in valid_types:
            return JsonResponse({"success": False, "message": "Invalid pricing type."}, status=400)

        has_name = "name" in body
        has_description = "description" in body
        has_keywords = "keywords_csv" in body
        custom_name = str(body.get("name") or "").strip()
        custom_description = str(body.get("description") or "").strip()
        custom_keywords = str(body.get("keywords_csv") or "").strip()
        is_active = bool(body.get("is_active", True))

        existing_row = CategoryMaster.objects.filter(code=code).first()
        effective_name = custom_name or (existing_row.name if existing_row and existing_row.name else defaults.get(code, {}).get("name", code.replace("_", " ").title()))
        effective_description = custom_description if has_description else (existing_row.description if existing_row else defaults.get(code, {}).get("description", ""))
        effective_keywords = custom_keywords if has_keywords else (existing_row.keywords_csv if existing_row else "")

        validation_error = _validate_category_payload(
            code,
            effective_name,
            effective_description,
            effective_keywords,
            pricing_type,
            exclude_pk=getattr(existing_row, "pk", None),
        )
        if validation_error:
            return JsonResponse({"success": False, "message": validation_error}, status=400)

        row, created = CategoryMaster.objects.get_or_create(
            code=code,
            defaults={
                "name": effective_name,
                "description": effective_description,
                "keywords_csv": effective_keywords,
                "pricing_type": pricing_type,
                "sort_order": defaults.get(code, {}).get("sort_order", _default_category_sort_order()),
                "is_active": is_active,
                "created_by": request.user,
            },
        )
        row.pricing_type = pricing_type
        row.name = effective_name
        row.description = effective_description
        row.keywords_csv = effective_keywords
        if created and not getattr(row, "sort_order", None):
            row.sort_order = defaults.get(code, {}).get("sort_order", _default_category_sort_order())
        row.is_active = is_active
        if created and not getattr(row, "created_by_id", None):
            row.created_by = request.user
        row.updated_by = request.user
        row.save()
        return JsonResponse({
            "success": True,
            "message": "Category master saved.",
            "type": row.pricing_type,
            "what_it_should_do": _CATEGORY_TYPE_ACTION_MAP.get(row.pricing_type, ""),
        })

    items = _build_category_config_items(request.GET.get("q", ""))
    return JsonResponse({"items": items})


# --------------------------------------------------------------------------- #
# 8b. Configurations -- Benchmark Table API (CRUD)
# --------------------------------------------------------------------------- #

def _corridor_to_dict(r):
    # Build display labels manually (model uses plain str constants, not TextChoices)
    cat_map = dict(LineCategory.CHOICES)
    db_names = dict(
        CategoryMaster.objects.values_list("code", "name")
    )
    geo_map = dict(Geography.CHOICES + [("ALL", "All Geographies")])
    scope_map = dict(ScopeType.CHOICES + [("ALL", "All Scopes")])
    return {
        "id": r.pk,
        "rule_code": r.rule_code,
        "name": r.name,
        "category": r.category,
        "category_display": db_names.get(r.category) or cat_map.get(r.category, r.category),
        "geography": r.geography,
        "geography_display": geo_map.get(r.geography, r.geography),
        "scope_type": r.scope_type,
        "scope_display": scope_map.get(r.scope_type, r.scope_type),
        "uom": r.uom,
        "min_rate": str(r.min_rate),
        "mid_rate": str(r.mid_rate),
        "max_rate": str(r.max_rate),
        "currency": r.currency,
        "keywords": r.keywords,
        "notes": r.notes,
        "priority": r.priority,
        "is_active": r.is_active,
    }


@login_required
def api_bench_corridors(request):
    """GET: list corridor rules. POST (JSON): create a new rule."""
    import json as _json
    allowed_categories = _allowed_category_codes()
    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
        category = str(body.get("category") or "").strip().upper()
        if category not in allowed_categories:
            return JsonResponse({"success": False, "message": "Category is not enabled in Category Master."}, status=400)
        try:
            rule = BenchmarkCorridorRule.objects.create(
                rule_code=body["rule_code"].strip(),
                name=body["name"].strip(),
                category=category,
                scope_type=body.get("scope_type", "ALL"),
                geography=body.get("geography", "ALL"),
                uom=body.get("uom", "").strip(),
                min_rate=body.get("min_rate") or 0,
                mid_rate=body.get("mid_rate") or 0,
                max_rate=body.get("max_rate") or 0,
                currency=(body.get("currency") or "AED").strip(),
                keywords=body.get("keywords", "").strip(),
                notes=body.get("notes", "").strip(),
                priority=int(body.get("priority") or 100),
                is_active=bool(body.get("is_active", True)),
                created_by=request.user,
            )
            return JsonResponse({"success": True, "message": "Corridor rule created.", "id": rule.pk})
        except Exception as exc:
            return JsonResponse({"success": False, "message": str(exc)}, status=400)

    # GET -- list with optional filters
    q = request.GET.get("q", "").lower()
    cat_filter = request.GET.get("category", "").strip().upper()
    geo_filter = request.GET.get("geography", "")
    from django.db.models import Q
    qs = BenchmarkCorridorRule.objects.filter(category__in=allowed_categories).order_by("category", "geography", "priority")
    if q:
        qs = qs.filter(Q(rule_code__icontains=q) | Q(name__icontains=q) | Q(keywords__icontains=q))
    if cat_filter:
        if cat_filter not in allowed_categories:
            return JsonResponse({"items": []})
        qs = qs.filter(category=cat_filter)
    if geo_filter:
        qs = qs.filter(geography=geo_filter)
    return JsonResponse({"items": [_corridor_to_dict(r) for r in qs]})


@login_required
def api_bench_corridor_detail(request, pk):
    """GET: single rule detail. POST (JSON): update / toggle / delete."""
    import json as _json
    allowed_categories = _allowed_category_codes()
    rule = get_object_or_404(BenchmarkCorridorRule, pk=pk)
    if request.method == "GET":
        return JsonResponse(_corridor_to_dict(rule))
    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
        action = body.get("_action", "update")
        if action == "toggle":
            rule.is_active = not rule.is_active
            rule.save(update_fields=["is_active"])
            return JsonResponse({"success": True, "message": f"Rule {'enabled' if rule.is_active else 'disabled'}."})
        if action == "delete":
            rule.is_active = False
            rule.save(update_fields=["is_active"])
            return JsonResponse({"success": True, "message": f"Rule '{rule.rule_code}' deactivated."})
        if action == "update":
            try:
                category = str(body.get("category", rule.category) or rule.category).strip().upper()
                if category not in allowed_categories:
                    return JsonResponse({"success": False, "message": "Category is not enabled in Category Master."}, status=400)
                rule.rule_code = (body.get("rule_code") or rule.rule_code).strip()
                rule.name = (body.get("name") or rule.name).strip()
                rule.category = category
                rule.scope_type = body.get("scope_type", rule.scope_type)
                rule.geography = body.get("geography", rule.geography)
                rule.uom = (body.get("uom") or "").strip()
                rule.min_rate = body.get("min_rate") or rule.min_rate
                rule.mid_rate = body.get("mid_rate") or rule.mid_rate
                rule.max_rate = body.get("max_rate") or rule.max_rate
                rule.currency = ((body.get("currency") or "AED")).strip()
                rule.keywords = (body.get("keywords") or "").strip()
                rule.notes = (body.get("notes") or "").strip()
                rule.priority = int(body.get("priority") or rule.priority)
                rule.is_active = bool(body.get("is_active", rule.is_active))
                rule.updated_by = request.user
                rule.save()
                return JsonResponse({"success": True, "message": "Corridor rule updated."})
            except Exception as exc:
                return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return JsonResponse({"error": "Method not allowed"}, status=405)


# --------------------------------------------------------------------------- #
# 8c. Configurations -- Variance Thresholds API
# --------------------------------------------------------------------------- #

@login_required
def api_bench_thresholds(request):
    """Return DB-backed VarianceThresholdConfig rows for the configuration screen."""
    import json as _json

    category_map = dict(LineCategory.CHOICES + [("ALL", "All Categories")])
    category_name_map = {item["code"]: item["label"] for item in _build_category_config_items()}
    category_name_map["ALL"] = "All Categories"
    geography_map = dict(Geography.CHOICES + [("ALL", "All Geographies")])
    ordered_allowed_categories = [item["code"] for item in _build_category_config_items() if item.get("is_active")]
    status_label_map = {
        "WITHIN_RANGE": "Optimal",
        "MODERATE": "Moderate",
        "HIGH": "High",
    }

    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

        notes = str(body.get("notes") or "").strip()
        category = "ALL"
        geography = "ALL"
        variance_status = str(body.get("variance_status") or "").strip().upper()

        if variance_status not in {"WITHIN_RANGE", "MODERATE", "HIGH"}:
            return JsonResponse({"success": False, "message": "Variance status is required."}, status=400)

        try:
            min_range_pct = float(body.get("within_range_max_pct"))
            max_range_pct = float(body.get("moderate_max_pct"))
        except (TypeError, ValueError):
            return JsonResponse({"success": False, "message": "Threshold values must be valid numbers."}, status=400)

        if min_range_pct < 0 or max_range_pct < 0:
            return JsonResponse({"success": False, "message": "Threshold values must be zero or greater."}, status=400)
        if max_range_pct < min_range_pct:
            return JsonResponse({"success": False, "message": "Max range must be greater than or equal to min range."}, status=400)

        defaults = {
            "within_range_max_pct": min_range_pct,
            "moderate_max_pct": max_range_pct,
            "notes": notes,
            "is_active": bool(body.get("is_active", True)),
            "updated_by": request.user,
        }
        row = VarianceThresholdConfig.objects.filter(
            category="ALL",
            geography="ALL",
            variance_status=variance_status,
        ).first()
        created = False
        if row:
            row.category = "ALL"
            row.geography = "ALL"
            row.variance_status = variance_status
            for field_name, field_value in defaults.items():
                setattr(row, field_name, field_value)
            row.save()
        else:
            row = VarianceThresholdConfig.objects.create(
                category="ALL",
                geography="ALL",
                variance_status=variance_status,
                created_by=request.user,
                **defaults,
            )
            created = True
        if created and not getattr(row, "created_by_id", None):
            row.created_by = request.user
            row.save(update_fields=["created_by"])

        return JsonResponse({
            "success": True,
            "message": "Global variance threshold saved.",
            "created": created,
            "id": row.pk,
        })

    items = []
    for row in VarianceThresholdConfig.objects.all().order_by("category", "geography"):
        items.append({
            "id": row.pk,
            "category": row.category,
            "category_display": category_name_map.get(row.category) or category_map.get(row.category, row.category),
            "geography": row.geography,
            "geography_display": geography_map.get(row.geography, row.geography),
            "within_range_max_pct": row.within_range_max_pct,
            "moderate_max_pct": row.moderate_max_pct,
            "variance_status": row.variance_status,
            "variance_status_display": status_label_map.get(row.variance_status, row.variance_status),
            "notes": row.notes,
            "is_active": row.is_active,
        })

    ordered_categories_with_custom = ordered_allowed_categories

    category_order_map = {code: idx for idx, code in enumerate(ordered_categories_with_custom)}
    category_order_map["ALL"] = len(ordered_categories_with_custom) + 1
    status_order_map = {"WITHIN_RANGE": 1, "MODERATE": 2, "HIGH": 3}
    items.sort(
        key=lambda item: (
            category_order_map.get(item["category"], 999),
            item.get("geography") or "",
            status_order_map.get(item.get("variance_status") or "", 99),
        )
    )
    return JsonResponse({"items": items})


@login_required
def api_bench_threshold_detail(request, pk):
    """GET: threshold detail. POST: update / toggle / deactivate."""
    import json as _json

    row = get_object_or_404(VarianceThresholdConfig, pk=pk)
    if request.method == "GET":
        return JsonResponse({
            "id": row.pk,
            "category": row.category,
            "geography": row.geography,
            "variance_status": row.variance_status,
            "within_range_max_pct": row.within_range_max_pct,
            "moderate_max_pct": row.moderate_max_pct,
            "notes": row.notes,
            "is_active": row.is_active,
        })

    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

        action = body.get("_action", "update")
        if action == "toggle":
            row.is_active = not row.is_active
            row.updated_by = request.user
            row.save(update_fields=["is_active", "updated_by", "updated_at"])
            return JsonResponse({"success": True, "message": f"Threshold {'enabled' if row.is_active else 'disabled'}."})

        if action == "delete":
            row.is_active = False
            row.updated_by = request.user
            row.save(update_fields=["is_active", "updated_by", "updated_at"])
            return JsonResponse({"success": True, "message": "Threshold deactivated."})

        if action == "update":
            notes = str(body.get("notes") or "").strip()

            variance_status = str(body.get("variance_status") or row.variance_status).strip().upper()
            if variance_status not in {"WITHIN_RANGE", "MODERATE", "HIGH"}:
                return JsonResponse({"success": False, "message": "Variance status is required."}, status=400)

            try:
                min_range_pct = float(body.get("within_range_max_pct"))
                max_range_pct = float(body.get("moderate_max_pct"))
            except (TypeError, ValueError):
                return JsonResponse({"success": False, "message": "Threshold values must be valid numbers."}, status=400)

            if min_range_pct < 0 or max_range_pct < 0:
                return JsonResponse({"success": False, "message": "Threshold values must be zero or greater."}, status=400)
            if max_range_pct < min_range_pct:
                return JsonResponse({"success": False, "message": "Max range must be greater than or equal to min range."}, status=400)

            row.category = "ALL"
            row.geography = "ALL"
            row.variance_status = variance_status
            row.within_range_max_pct = min_range_pct
            row.moderate_max_pct = max_range_pct
            row.notes = notes
            row.is_active = bool(body.get("is_active", row.is_active))
            row.updated_by = request.user
            row.save()
            return JsonResponse({"success": True, "message": "Global variance threshold updated."})

    return JsonResponse({"error": "Method not allowed"}, status=405)


# --------------------------------------------------------------------------- #
# 9. Status check API (JSON)
# --------------------------------------------------------------------------- #

@login_required
def request_status(request, pk):
    """Return JSON status of a BenchmarkRequest (for polling)."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)
    return JsonResponse({
        "pk": bench_request.pk,
        "status": bench_request.status,
        "error_message": bench_request.error_message,
    })


# --------------------------------------------------------------------------- #
# 10. Live pricing enrichment via Perplexity
# --------------------------------------------------------------------------- #

@login_required
@require_http_methods(["POST"])
def request_live_enrich(request, pk):
    """
    POST: Fetch live market pricing from Perplexity sonar-pro and re-benchmark
    all line items for this request.  Redirects back to request_detail.
    """
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)

    if bench_request.status == "PROCESSING":
        messages.warning(request, "Benchmarking is already in progress. Please wait.")
        return redirect("benchmarking:request_detail", pk=pk)

    result = BenchmarkEngine.run_live_enrichment(
        request_pk=pk,
        user=request.user,
        tenant=getattr(request, "tenant", None),
    )

    if result["success"]:
        messages.success(
            request,
            f"Live pricing updated: {result['enriched']} of {result['total']} lines "
            f"enriched with real market data from Perplexity.",
        )
    else:
        messages.error(
            request,
            f"Live pricing failed: {result.get('error', 'Unknown error')}.",
        )

    return redirect("benchmarking:request_detail", pk=pk)


# --------------------------------------------------------------------------- #
# 11. Live pricing enrichment -- AJAX status/result
# --------------------------------------------------------------------------- #

@login_required
def request_live_enrich_ajax(request, pk):
    """
    POST (AJAX): Run Perplexity live enrichment and return JSON for in-page update.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)

    if bench_request.status == "PROCESSING":
        return JsonResponse({"success": False, "error": "Processing already in progress"})

    result = BenchmarkEngine.run_live_enrichment(
        request_pk=pk,
        user=request.user,
        tenant=getattr(request, "tenant", None),
    )
    return JsonResponse(result)


# --------------------------------------------------------------------------- #
# 12. End-to-End Lifecycle Timeline
# --------------------------------------------------------------------------- #

@login_required
def benchmark_e2e_timeline(request, pk):
    """
    End-to-end lifecycle timeline for a single BenchmarkRequest.
    Shows every event: request created, form details, quotations uploaded,
    document extraction (OCR + DI), benchmark engine run, AI classification,
    corridor matching, vendor recommendation agent, overall result.
    """
    bench_request = get_object_or_404(
        _scope_requests(request, BenchmarkRequest.objects.select_related("submitted_by").filter(is_active=True)),
        pk=pk,
    )

    def _label(user):
        if not user:
            return ""
        try:
            return user.get_short_name() or user.get_full_name() or user.email or ""
        except Exception:
            return getattr(user, "email", "") or ""

    actor_label = _label(bench_request.submitted_by)

    _events = []

    # REQUEST CREATED
    _events.append({
        "stage": "REQUEST_CREATED",
        "icon": "bi-person-plus-fill",
        "tone": "primary",
        "title": "Benchmark Request Submitted",
        "actor": actor_label or "System",
        "timestamp": bench_request.created_at,
        "detail": "Geography: {} | Scope: {} | Store Type: {} | Status: {}".format(
            bench_request.geography,
            bench_request.scope_type,
            bench_request.store_type or "N/A",
            bench_request.status,
        ),
        "extra": {
            "geography": bench_request.geography,
            "scope_type": bench_request.scope_type,
            "store_type": bench_request.store_type or "",
            "notes": bench_request.notes or "",
            "project_name": bench_request.project_name or "",
        },
    })

    # RFQ document upload
    if bench_request.rfq_blob_path or bench_request.rfq_document or bench_request.rfq_ref:
        _events.append({
            "stage": "RFQ_UPLOADED",
            "icon": "bi-file-earmark-text-fill",
            "tone": "secondary",
            "title": "RFQ Document Provided ({})".format(bench_request.rfq_source or "upload"),
            "actor": actor_label or "System",
            "timestamp": bench_request.created_at,
            "detail": "RFQ Ref: {} | Source: {}".format(
                bench_request.rfq_ref or "N/A",
                bench_request.rfq_source or "N/A",
            ),
            "extra": None,
        })

    # QUOTATIONS
    quotations = list(
        bench_request.quotations.filter(is_active=True)
        .prefetch_related("line_items")
        .order_by("created_at")
    )

    for q in quotations:
        q_ts = getattr(q, "created_at", bench_request.created_at) or bench_request.created_at
        q_items = list(q.line_items.filter(is_active=True))
        # Quotation uploaded
        _events.append({
            "stage": "QUOTATION_UPLOADED",
            "icon": "bi-file-earmark-arrow-up-fill",
            "tone": "info",
            "title": "Quotation Uploaded -- {}".format(q.supplier_name or "Unknown Supplier"),
            "actor": actor_label or "System",
            "timestamp": q_ts,
            "detail": "Ref: {} | Extraction: {} | {} line(s) extracted".format(
                q.quotation_ref or "N/A",
                q.extraction_status,
                len(q_items),
            ),
            "extra": {
                "supplier": q.supplier_name or "",
                "ref": q.quotation_ref or "",
                "extraction_status": q.extraction_status,
                "line_count": len(q_items),
            },
        })

        # OCR/DI extraction event
        if q.extraction_status in ("DONE", "FAILED"):
            _di = q.di_extraction_json or {}
            _tone = "success" if q.extraction_status == "DONE" else "danger"
            _events.append({
                "stage": "OCR_EXTRACTED",
                "icon": "bi-file-earmark-richtext-fill",
                "tone": _tone,
                "title": "Document Intelligence (OCR) -- {}".format(q.extraction_status),
                "actor": "Azure Document Intelligence",
                "timestamp": q_ts,
                "detail": "{} table(s) detected | {} kv pair(s)".format(
                    len(_di.get("tables", []) or []),
                    len(_di.get("key_value_pairs", []) or []),
                ),
                "extra": {
                    "error": q.extraction_error or "",
                    "tables_count": len(_di.get("tables", []) or []),
                    "kv_count": len(_di.get("key_value_pairs", []) or []),
                },
            })

        # Line item classification
        if q_items:
            ai_classified = [i for i in q_items if i.classification_source == "AI"]
            keyword_classified = [i for i in q_items if i.classification_source == "KEYWORD"]
            _events.append({
                "stage": "LINES_CLASSIFIED",
                "icon": "bi-tags-fill",
                "tone": "info",
                "title": "Line Items Classified ({} lines)".format(len(q_items)),
                "actor": "AI Classification Engine",
                "timestamp": q_ts,
                "detail": "AI classified: {} | Keyword rules: {} | Avg confidence: {:.0%}".format(
                    len(ai_classified),
                    len(keyword_classified),
                    sum(i.classification_confidence for i in q_items) / len(q_items) if q_items else 0,
                ),
                "extra": {
                    "lines": [
                        {
                            "description": i.description[:80],
                            "category": i.category,
                            "source": i.classification_source,
                            "confidence": i.classification_confidence,
                            "quoted_rate": str(i.quoted_unit_rate or ""),
                            "line_amount": str(i.line_amount or ""),
                        }
                        for i in q_items[:20]
                    ]
                },
            })

        # Corridor benchmark matching
        corr_matched = [i for i in q_items if i.benchmark_source == "CORRIDOR_DB"]
        perp_matched = [i for i in q_items if i.benchmark_source == "PERPLEXITY_LIVE"]
        if corr_matched or perp_matched:
            _events.append({
                "stage": "BENCHMARK_MATCHED",
                "icon": "bi-bar-chart-steps",
                "tone": "primary",
                "title": "Benchmark Corridors Applied ({}/{} lines)".format(
                    len(corr_matched) + len(perp_matched), len(q_items),
                ),
                "actor": "Benchmark Engine",
                "timestamp": q_ts,
                "detail": "Corridor DB: {} lines | Perplexity Live: {} lines | No benchmark: {} lines".format(
                    len(corr_matched),
                    len(perp_matched),
                    len([i for i in q_items if i.benchmark_source == "NONE"]),
                ),
                "extra": None,
            })

    # BENCHMARK PROCESSING START
    if bench_request.status in ("PROCESSING", "COMPLETED", "FAILED"):
        _events.append({
            "stage": "BENCHMARK_STARTED",
            "icon": "bi-play-circle-fill",
            "tone": "primary",
            "title": "Benchmark Engine Triggered",
            "actor": "System / Celery Task",
            "timestamp": bench_request.updated_at or bench_request.created_at,
            "detail": "Should-cost model running for {} quotation(s)".format(len(quotations)),
            "extra": None,
        })

    # VENDOR RECOMMENDATION AGENT
    if quotations:
        _events.append({
            "stage": "VENDOR_RECOMMENDATION",
            "icon": "bi-robot",
            "tone": "info",
            "title": "Vendor Recommendation Agent Invoked",
            "actor": "BenchmarkVendorRecommendationAgent",
            "timestamp": bench_request.updated_at or bench_request.created_at,
            "detail": "Comparing {} supplier(s) on price, deviation, and compliance".format(len(quotations)),
            "extra": None,
        })

    # LIVE PRICING ENRICHMENT
    result = None
    try:
        result = bench_request.result
    except Exception:
        pass

    if result and result.live_enriched_at:
        _live = result.live_enrichment_json or {}
        _events.append({
            "stage": "LIVE_ENRICHMENT",
            "icon": "bi-globe2",
            "tone": "warning",
            "title": "Perplexity Live Pricing Enrichment",
            "actor": "Perplexity AI",
            "timestamp": result.live_enriched_at,
            "detail": "Market live prices fetched and applied to line items",
            "extra": {
                "enrichment_metadata": _live,
            },
        })

    # BENCHMARK RESULT
    if result:
        _deviation = result.overall_deviation_pct
        _res_tone = {"WITHIN_RANGE": "success", "MODERATE": "warning", "HIGH": "danger"}.get(result.overall_status, "secondary")
        _events.append({
            "stage": "BENCHMARK_RESULT",
            "icon": "bi-graph-up-arrow",
            "tone": _res_tone,
            "title": "Benchmark Result: {} ({})".format(
                result.overall_status.replace("_", " ").title(),
                "{:+.1f}%".format(_deviation) if _deviation is not None else "N/A",
            ),
            "actor": "Benchmark Engine",
            "timestamp": result.created_at or bench_request.updated_at,
            "detail": "Total Quoted: {} | Benchmark Mid: {} | Deviation: {} | "
                      "Within Range: {} | Moderate: {} | High: {} | Needs Review: {}".format(
                str(result.total_quoted or "N/A"),
                str(result.total_benchmark_mid or "N/A"),
                "{:+.1f}%".format(_deviation) if _deviation is not None else "N/A",
                result.lines_within_range,
                result.lines_moderate,
                result.lines_high,
                result.lines_needs_review,
            ),
            "extra": {
                "overall_status": result.overall_status,
                "overall_deviation_pct": _deviation,
                "category_summary": result.category_summary_json or {},
                "negotiation_notes": result.negotiation_notes_json or [],
                "lines_within_range": result.lines_within_range,
                "lines_moderate": result.lines_moderate,
                "lines_high": result.lines_high,
                "lines_needs_review": result.lines_needs_review,
            },
        })

    # ERROR
    if bench_request.status == "FAILED" and bench_request.error_message:
        _events.append({
            "stage": "ERROR",
            "icon": "bi-x-octagon-fill",
            "tone": "danger",
            "title": "Benchmark Run Failed",
            "actor": "System",
            "timestamp": bench_request.updated_at or bench_request.created_at,
            "detail": bench_request.error_message[:200],
            "extra": None,
        })

    # CURRENT STATUS
    _st_tone = {"PENDING": "secondary", "PROCESSING": "info", "COMPLETED": "success", "FAILED": "danger"}.get(bench_request.status, "secondary")
    _events.append({
        "stage": "CURRENT_STATUS",
        "icon": "bi-flag-fill",
        "tone": _st_tone,
        "title": "Current Status: {}".format(bench_request.status.title()),
        "actor": "System",
        "timestamp": bench_request.updated_at or bench_request.created_at,
        "detail": bench_request.title,
        "extra": None,
    })

    _events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or bench_request.created_at))

    _total_duration_s = max(
        0,
        ((bench_request.updated_at or bench_request.created_at) - bench_request.created_at).total_seconds(),
    )
    _total_lines = sum(q.line_items.filter(is_active=True).count() for q in quotations)

    return render(request, "benchmarking/e2e_timeline.html", {
        "bench_request": bench_request,
        "timeline_events": _events,
        "total_duration_s": _total_duration_s,
        "quotations": quotations,
        "total_lines": _total_lines,
        "result": result,
    })
