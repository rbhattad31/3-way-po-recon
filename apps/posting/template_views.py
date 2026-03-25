"""Posting template views — posting workbench, detail, and ERP reference imports."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import observed_action
from apps.core.enums import InvoicePostingStatus
from apps.core.permissions import permission_required_code
from apps.posting.models import InvoicePosting
from apps.posting.services.posting_action_service import PostingActionService
from apps.posting_core.models import (
    ERPReferenceImportBatch,
    PostingRun,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Posting Workbench — list view
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("posting.view_workbench", permission="invoices.view", entity_type="InvoicePosting")
def posting_workbench(request):
    """Posting workbench — list of all invoice postings."""
    qs = (
        InvoicePosting.objects
        .select_related("invoice", "reviewed_by")
        .order_by("-created_at")
    )

    # Filters
    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)

    queue_filter = request.GET.get("review_queue", "")
    if queue_filter:
        qs = qs.filter(review_queue=queue_filter)

    search = request.GET.get("q", "")
    if search:
        qs = qs.filter(invoice__invoice_number__icontains=search)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    # KPI stats
    total = InvoicePosting.objects.count()
    review_required = InvoicePosting.objects.filter(
        status=InvoicePostingStatus.MAPPING_REVIEW_REQUIRED,
    ).count()
    ready = InvoicePosting.objects.filter(
        status=InvoicePostingStatus.READY_TO_SUBMIT,
    ).count()
    posted = InvoicePosting.objects.filter(
        status=InvoicePostingStatus.POSTED,
    ).count()

    return render(request, "posting/workbench.html", {
        "page_obj": page,
        "status_filter": status_filter,
        "queue_filter": queue_filter,
        "search": search,
        "statuses": InvoicePostingStatus.choices,
        "kpi": {
            "total": total,
            "review_required": review_required,
            "ready": ready,
            "posted": posted,
        },
    })


# ────────────────────────────────────────────────────────────────
# Posting Detail
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("posting.view_detail", permission="invoices.view", entity_type="InvoicePosting")
def posting_detail(request, pk):
    """Detailed view of a single invoice posting with proposal data."""
    posting = get_object_or_404(
        InvoicePosting.objects.select_related("invoice", "reviewed_by"),
        pk=pk,
    )

    latest_run = (
        PostingRun.objects
        .filter(invoice=posting.invoice)
        .order_by("-created_at")
        .first()
    )

    run_history = (
        PostingRun.objects
        .filter(invoice=posting.invoice)
        .order_by("-created_at")[:10]
    )

    corrections = posting.field_corrections.order_by("-created_at")[:20]

    return render(request, "posting/detail.html", {
        "posting": posting,
        "latest_run": latest_run,
        "run_history": run_history,
        "corrections": corrections,
    })


# ────────────────────────────────────────────────────────────────
# Approval / Reject / Submit actions
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("invoices.view")
def posting_approve(request, pk):
    """Approve a posting from the UI."""
    try:
        PostingActionService.approve_posting(
            posting_id=pk,
            user=request.user,
        )
        messages.success(request, "Posting approved successfully.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("posting:posting-detail", pk=pk)


@login_required
@require_POST
@permission_required_code("invoices.view")
def posting_reject(request, pk):
    """Reject a posting from the UI."""
    reason = request.POST.get("reason", "")
    try:
        PostingActionService.reject_posting(
            posting_id=pk,
            user=request.user,
            reason=reason,
        )
        messages.success(request, "Posting rejected.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("posting:posting-detail", pk=pk)


@login_required
@require_POST
@permission_required_code("invoices.view")
def posting_submit(request, pk):
    """Submit a posting to ERP from the UI."""
    try:
        PostingActionService.submit_posting(
            posting_id=pk,
            user=request.user,
        )
        messages.success(request, "Posting submitted successfully.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("posting:posting-detail", pk=pk)


@login_required
@require_POST
@permission_required_code("invoices.view")
def posting_retry(request, pk):
    """Retry a failed posting from the UI."""
    try:
        PostingActionService.retry_posting(
            posting_id=pk,
            user=request.user,
        )
        messages.success(request, "Posting preparation retried.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("posting:posting-detail", pk=pk)


# ────────────────────────────────────────────────────────────────
# ERP Reference Imports — list view
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("posting.view_imports", permission="invoices.view", entity_type="ERPReferenceImportBatch")
def reference_import_list(request):
    """List ERP reference import batches."""
    qs = (
        ERPReferenceImportBatch.objects
        .select_related("imported_by")
        .order_by("-created_at")
    )

    batch_type = request.GET.get("batch_type", "")
    if batch_type:
        qs = qs.filter(batch_type=batch_type.upper())

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "posting/reference_imports.html", {
        "page_obj": page,
        "batch_type": batch_type,
    })
