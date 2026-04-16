"""
Benchmarking template views.
Covers: All Requests, New Request (upload), Detail/Results, Quotations, Reports, Configurations.
"""
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from apps.core.tenant_utils import require_tenant

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    BenchmarkResult,
    Geography,
    LineCategory,
    ScopeType,
    VarianceStatus,
)
from apps.benchmarking.services.benchmark_service import BenchmarkEngine
from apps.benchmarking.services.blob_storage_service import BlobStorageService
from apps.benchmarking.services.export_service import ExportService
from apps.benchmarking.agents.Vendor_Recommendation_Agent_BM import BenchmarkVendorRecommendationAgent
from apps.documents.blob_service import build_blob_url, delete_blob, upload_to_blob

try:
    from apps.procurement.models import GeneratedRFQ as _GeneratedRFQ
except ImportError:
    _GeneratedRFQ = None

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
    extracted_files, upload_error = _extract_pdf_files_from_upload(upload)
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


def _run_benchmark_with_live(*, bench_request, user, tenant):
    """Run core benchmark + best-effort live enrichment."""
    result = BenchmarkEngine.run(bench_request.pk, user=user, tenant=tenant)
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
        if rfq_source == "system":
            quotation_ref = request.POST.get("rfq_system_ref", "").strip()
        elif rfq_source == "upload":
            rfq_upload_file = request.FILES.get("rfq_upload_file")
            if rfq_upload_file:
                quotation_ref = os.path.splitext(rfq_upload_file.name)[0].strip() or "uploaded-rfq"
            else:
                quotation_ref = request.POST.get("rfq_upload_ref", "").strip() or request.POST.get("quotation_ref", "").strip()
        else:
            quotation_ref = request.POST.get("quotation_ref", "").strip()
        notes = request.POST.get("notes", "").strip()

        errors = []
        if not title:
            errors.append("Request title is required.")
        upload = request.FILES.get("quotation_pdf")
        if upload:
            _, upload_validation_error = _extract_pdf_files_from_upload(upload)
            if upload_validation_error:
                errors.append(upload_validation_error)

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "benchmarking/request_create.html", _base_ctx(
                posted=request.POST,
                page_title="New Benchmarking Request",
                generated_rfq_list=_get_generated_rfqs(getattr(request, "tenant", None)),
            ))

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
            rfq_ref=quotation_ref,
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

        if upload:
            created_quotes, upload_error = _create_quotations_from_upload(
                bench_request=bench_request,
                upload=upload,
                quotation_ref=quotation_ref,
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

@login_required
def request_detail(request, pk):
    """Detailed results view for a benchmarking request."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)

    # Gather all line items across all quotations
    quotations = bench_request.quotations.filter(is_active=True).prefetch_related("line_items")
    line_items = []
    quotation_summaries = []
    vendor_cards = []
    for q in quotations:
        q_items = list(q.line_items.filter(is_active=True))
        line_items.extend(q_items)
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
            "supplier_name": q.supplier_name or "Unnamed Vendor",
            "quotation_ref": q.quotation_ref,
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

    # Category filter
    cat_filter = request.GET.get("category", "")
    if cat_filter:
        line_items = [i for i in line_items if i.category == cat_filter]

    # Variance filter
    var_filter = request.GET.get("variance", "")
    if var_filter:
        line_items = [i for i in line_items if i.variance_status == var_filter]

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
    )
    return render(request, "benchmarking/request_detail.html", ctx)


@login_required
@require_http_methods(["POST"])
def request_add_quotations(request, pk):
    """Add one or many vendor quotations (PDF/ZIP) to an existing benchmark request."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)

    quotation_ref = request.POST.get("quotation_ref", "").strip()
    upload = request.FILES.get("quotation_pdf")

    if not upload:
        messages.error(request, "Quotation file is required (PDF or ZIP).")
        return redirect("benchmarking:request_detail", pk=pk)

    created_quotes, upload_error = _create_quotations_from_upload(
        bench_request=bench_request,
        upload=upload,
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
    if result.get("success"):
        if live_result and live_result.get("success"):
            messages.success(request, "Reprocessing + live market enrichment completed successfully.")
        else:
            messages.success(request, "Reprocessing completed successfully.")
    else:
        messages.error(request, f"Reprocessing failed: {result.get('error')}")
    return redirect("benchmarking:request_detail", pk=pk)


# --------------------------------------------------------------------------- #
# 5. Export CSV
# --------------------------------------------------------------------------- #

@login_required
def request_export(request, pk):
    """Download CSV export of benchmarking results."""
    bench_request = get_object_or_404(_scope_requests(request, BenchmarkRequest.objects.filter(is_active=True)), pk=pk)
    csv_bytes = ExportService.export_request_csv(bench_request)
    slug = bench_request.title.lower().replace(" ", "_")[:40]
    response = HttpResponse(csv_bytes, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="benchmark_{slug}.csv"'
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
    stats = {
        "category_total": len(LineCategory.CHOICES),
        "category_active": len(LineCategory.CHOICES),
        "corridor_total": BenchmarkCorridorRule.objects.count(),
        "corridor_active": BenchmarkCorridorRule.objects.filter(is_active=True).count(),
        "threshold_total": 4,
        "threshold_active": 4,
    }
    scope_choices_with_all = ScopeType.CHOICES + [("ALL", "All Scopes")]
    geo_choices_with_all = Geography.CHOICES + [("ALL", "All Geographies")]
    ctx = _base_ctx(
        stats=stats,
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
    "CONTROLS":      "BMS/DDC controls, control panels, cabling, sensors, actuators",
    "DUCTING":       "GI ductwork, flexible ducts, grilles, diffusers, louvres",
    "INSULATION":    "Pipe insulation (Armaflex/NBR), duct insulation (glass wool, foam)",
    "ACCESSORIES":   "Pipes, fittings, valves, dampers, supports, hangers, drain trays",
    "INSTALLATION":  "Labour for mechanical installation, fix & fit, pipework, electrical works",
    "TC":            "Testing, balancing, commissioning, startup, handover",
    "UNCATEGORIZED": "Items not yet classified into a specific category",
}


@login_required
def api_bench_categories(request):
    """Return all LineCategory enum values with active corridor rule counts."""
    from django.db.models import Count
    q = request.GET.get("q", "").lower()
    counts = dict(
        BenchmarkCorridorRule.objects.filter(is_active=True)
        .values_list("category")
        .annotate(cnt=Count("id"))
        .values_list("category", "cnt")
    )
    items = []
    for code, label in LineCategory.CHOICES:
        if q and q not in code.lower() and q not in label.lower():
            continue
        sample_kw_qs = list(
            BenchmarkCorridorRule.objects.filter(category=code, is_active=True)
            .exclude(keywords="")
            .values_list("keywords", flat=True)[:3]
        )
        # flatten first ~6 keywords
        all_kw = []
        for kws in sample_kw_qs:
            all_kw.extend([k.strip() for k in kws.split(",") if k.strip()])
        sample_keywords = ", ".join(all_kw[:6]) if all_kw else ""
        items.append({
            "code": code,
            "label": label,
            "description": _CAT_DESCRIPTIONS.get(code, ""),
            "rule_count": counts.get(code, 0),
            "sample_keywords": sample_keywords,
        })
    return JsonResponse({"items": items})


# --------------------------------------------------------------------------- #
# 8b. Configurations -- Benchmark Table API (CRUD)
# --------------------------------------------------------------------------- #

def _corridor_to_dict(r):
    # Build display labels manually (model uses plain str constants, not TextChoices)
    cat_map = dict(LineCategory.CHOICES)
    geo_map = dict(Geography.CHOICES + [("ALL", "All Geographies")])
    scope_map = dict(ScopeType.CHOICES + [("ALL", "All Scopes")])
    return {
        "id": r.pk,
        "rule_code": r.rule_code,
        "name": r.name,
        "category": r.category,
        "category_display": cat_map.get(r.category, r.category),
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
    if request.method == "POST":
        try:
            body = _json.loads(request.body)
        except Exception:
            return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
        try:
            rule = BenchmarkCorridorRule.objects.create(
                rule_code=body["rule_code"].strip(),
                name=body["name"].strip(),
                category=body["category"],
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
    cat_filter = request.GET.get("category", "")
    geo_filter = request.GET.get("geography", "")
    from django.db.models import Q
    qs = BenchmarkCorridorRule.objects.all().order_by("category", "geography", "priority")
    if q:
        qs = qs.filter(Q(rule_code__icontains=q) | Q(name__icontains=q) | Q(keywords__icontains=q))
    if cat_filter:
        qs = qs.filter(category=cat_filter)
    if geo_filter:
        qs = qs.filter(geography=geo_filter)
    return JsonResponse({"items": [_corridor_to_dict(r) for r in qs]})


@login_required
def api_bench_corridor_detail(request, pk):
    """GET: single rule detail. POST (JSON): update / toggle / delete."""
    import json as _json
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
                rule.rule_code = (body.get("rule_code") or rule.rule_code).strip()
                rule.name = (body.get("name") or rule.name).strip()
                rule.category = body.get("category", rule.category)
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
    """Return the 4 variance bands with live line-item counts."""
    from django.db.models import Count
    counts = dict(
        BenchmarkLineItem.objects.values_list("variance_status")
        .annotate(cnt=Count("id"))
        .values_list("variance_status", "cnt")
    )
    bands = [
        {
            "code": "WITHIN_RANGE",
            "label": "Within Range",
            "description": "Quoted rate is within benchmark corridor",
            "range": "< 5% above mid",
            "color": "success",
            "count": counts.get("WITHIN_RANGE", 0),
        },
        {
            "code": "MODERATE",
            "label": "Moderate Variance",
            "description": "Quoted rate slightly exceeds benchmark",
            "range": "5% - 15% above mid",
            "color": "warning",
            "count": counts.get("MODERATE", 0),
        },
        {
            "code": "HIGH",
            "label": "High Variance",
            "description": "Quoted rate significantly exceeds benchmark",
            "range": "> 15% above mid",
            "color": "danger",
            "count": counts.get("HIGH", 0),
        },
        {
            "code": "NEEDS_REVIEW",
            "label": "Needs Review",
            "description": "No benchmark corridor found for this line item",
            "range": "No benchmark",
            "color": "secondary",
            "count": counts.get("NEEDS_REVIEW", 0),
        },
    ]
    return JsonResponse({"items": bands})


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
