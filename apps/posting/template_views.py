"""Posting template views — posting workbench, detail, and ERP reference imports."""
from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import observed_action
from apps.core.enums import InvoicePostingStatus
from apps.core.permissions import permission_required_code
from apps.core.tenant_utils import TenantQuerysetMixin, require_tenant
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
    tenant = require_tenant(request)
    qs = (
        InvoicePosting.objects
        .select_related("invoice", "reviewed_by")
        .order_by("-created_at")
    )
    if tenant is not None:
        qs = qs.filter(tenant=tenant)

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
    kpi_base = InvoicePosting.objects.all()
    if tenant is not None:
        kpi_base = kpi_base.filter(tenant=tenant)
    total = kpi_base.count()
    review_required = kpi_base.filter(
        status=InvoicePostingStatus.MAPPING_REVIEW_REQUIRED,
    ).count()
    ready = kpi_base.filter(
        status=InvoicePostingStatus.READY_TO_SUBMIT,
    ).count()
    posted = kpi_base.filter(
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

    # ERP resolution data from latest run
    field_values = []
    line_items = []
    erp_source_metadata = {}
    header_fields = []
    erp_ref_fields = []
    if latest_run:
        field_values = list(latest_run.field_values.order_by("category", "field_code"))
        line_items = list(latest_run.line_items.order_by("line_index"))
        erp_source_metadata = latest_run.erp_source_metadata_json or {}
        # Split field values into header vs ERP-sourced for structured display
        for fv in field_values:
            if fv.source_type in (
                "VENDOR_REF", "ITEM_REF", "TAX_REF",
                "COST_CENTER_REF", "PO_REF",
            ):
                erp_ref_fields.append(fv)
            else:
                header_fields.append(fv)

    return render(request, "posting/detail.html", {
        "posting": posting,
        "latest_run": latest_run,
        "run_history": run_history,
        "corrections": corrections,
        "field_values": field_values,
        "header_fields": header_fields,
        "erp_ref_fields": erp_ref_fields,
        "line_items": line_items,
        "erp_source_metadata": erp_source_metadata,
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
    from apps.core.tenant_utils import get_tenant_or_none
    from apps.erp_integration.models import ERPConnection
    from apps.erp_integration.enums import ERPConnectionStatus
    from django.db.models import Q
    
    tenant = get_tenant_or_none(request)

    qs = (
        ERPReferenceImportBatch.objects
        .select_related("imported_by")
        .order_by("-created_at")
    )
    if tenant is not None:
        qs = qs.filter(tenant=tenant)

    batch_type = request.GET.get("batch_type", "")
    if batch_type:
        qs = qs.filter(batch_type=batch_type.upper())

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    # Get available ERP connections (both tenant-specific and global)
    erp_query = Q(status=ERPConnectionStatus.ACTIVE, is_active=True)
    if tenant is not None:
        erp_query = erp_query & (Q(tenant=tenant) | Q(tenant=None))
    
    erp_conns = (
        ERPConnection.objects
        .filter(erp_query)
        .values_list("name", flat=True)
        .order_by("name")
    )

    return render(request, "posting/reference_imports.html", {
        "page_obj": page,
        "batch_type": batch_type,
        "erp_connections": erp_conns,
    })


# ────────────────────────────────────────────────────────────────
# Direct ERP Import — trigger real-time sync
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@require_POST
@observed_action("posting.trigger_direct_import", permission="invoices.view", entity_type="ERPReferenceImportBatch")
def trigger_direct_erp_import(request):
    """Trigger a direct ERP reference data import.

    POST parameters:
    - connector_name: Name of the ERPConnection to import from
    - batch_types: List of batch types (VENDOR, ITEM, TAX_CODE, COST_CENTER, OPEN_PO)
    - async: "true" to run async (default), "false" to wait for completion
    """
    from apps.core.tenant_utils import get_tenant_or_none
    from apps.posting_core.services.direct_erp_importer import DirectERPImportOrchestrator
    from apps.posting.tasks import import_reference_direct_task

    tenant = get_tenant_or_none(request)
    connector_name = request.POST.get("connector_name", "").strip()
    batch_types = request.POST.getlist("batch_types")  # getlist for multiple checkboxes
    is_async = request.POST.get("async", "true").lower() != "false"

    if not connector_name:
        messages.error(request, "Connector name is required.")
        return redirect("posting:posting-imports")

    batch_types = [t.strip().upper() for t in batch_types if t.strip()]
    if not batch_types:
        messages.error(request, "At least one batch type is required.")
        return redirect("posting:posting-imports")

    # Validate batch types
    from apps.core.enums import ERPReferenceBatchType
    invalid_types = [t for t in batch_types if t not in ERPReferenceBatchType.values]
    if invalid_types:
        messages.error(request, f"Invalid batch types: {', '.join(invalid_types)}")
        return redirect("posting:posting-imports")

    # Check if connector exists
    from apps.erp_integration.models import ERPConnection
    from apps.erp_integration.enums import ERPConnectionStatus
    try:
        conn = ERPConnection.objects.get(
            name=connector_name,
            status=ERPConnectionStatus.ACTIVE,
            is_active=True,
        )
        if tenant is not None and conn.tenant is not None and conn.tenant != tenant:
            messages.error(request, f"Connector '{connector_name}' is not available for your organization.")
            return redirect("posting:posting-imports")
    except ERPConnection.DoesNotExist:
        messages.error(request, f"ERP Connection '{connector_name}' not found or not active.")
        return redirect("posting:posting-imports")

    # Enqueue imports
    batch_pks = []
    for batch_type in batch_types:
        try:
            queued_batch = ERPReferenceImportBatch.objects.create(
                batch_type=batch_type,
                source_file_name=f"direct_erp_{batch_type}_{timezone.now().isoformat()}",
                source_file_path=connector_name,
                checksum="direct_erp",
                status="PENDING",
                imported_by=request.user,
                metadata_json={
                    "source": "direct_erp_connector",
                    "connector_name": connector_name,
                    "queued_at": timezone.now().isoformat(),
                },
                tenant=tenant,
            )

            if is_async:
                if settings.CELERY_TASK_ALWAYS_EAGER:
                    # In eager mode Celery executes inline and blocks the request.
                    # Run import on a daemon thread so UI returns immediately.
                    def _run_background_import(
                        _batch_type: str,
                        _tenant_id: int | None,
                        _user_id: int,
                        _batch_id: int,
                    ) -> None:
                        from django.contrib.auth import get_user_model
                        from apps.accounts.models import CompanyProfile

                        User = get_user_model()
                        _tenant = CompanyProfile.objects.filter(pk=_tenant_id).first() if _tenant_id else None
                        _user = User.objects.filter(pk=_user_id).first()
                        try:
                            DirectERPImportOrchestrator.run_import(
                                batch_type=_batch_type,
                                connector_name=connector_name,
                                tenant=_tenant,
                                user=_user,
                                existing_batch_id=_batch_id,
                            )
                        except ValueError as exc:
                            # ValueError is raised for known failure modes such as
                            # connector not found or transient connectivity failure.
                            # Log as WARNING without traceback to avoid noise.
                            logger.warning(
                                "Background direct ERP import failed for %s (%s): %s",
                                connector_name,
                                _batch_type,
                                exc,
                            )
                        except Exception:
                            logger.exception(
                                "Background direct ERP import failed unexpectedly for %s (%s)",
                                connector_name,
                                _batch_type,
                            )

                    threading.Thread(
                        target=_run_background_import,
                        args=(batch_type, tenant.pk if tenant else None, request.user.pk, queued_batch.pk),
                        daemon=True,
                    ).start()
                    batch_pks.append(f"{batch_type} (batch: {queued_batch.pk}, background)")
                else:
                    task = import_reference_direct_task.delay(
                        tenant_id=tenant.pk if tenant else None,
                        batch_type=batch_type,
                        connector_name=connector_name,
                        user_id=request.user.pk,
                        batch_id=queued_batch.pk,
                    )
                    batch_pks.append(f"{batch_type} (batch: {queued_batch.pk}, task: {task.id[:12]}...)")
            else:
                # Synchronous (CELERY_TASK_ALWAYS_EAGER=True in dev)
                result = import_reference_direct_task.apply_async(
                    args=(
                        tenant.pk if tenant else None,
                        batch_type,
                        connector_name,
                        request.user.pk,
                        None,
                        queued_batch.pk,
                    ),
                    wait_result=True,
                )
                if result and isinstance(result, dict):
                    batch_pks.append(f"{batch_type} (batch: {result.get('batch_id', '?')})")
                else:
                    batch_pks.append(f"{batch_type} (batch: {queued_batch.pk})")
        except Exception:
            logger.exception("Failed to start direct ERP import for type %s", batch_type)
            messages.error(
                request,
                f"Could not start {batch_type} import right now. Please retry in a minute.",
            )
            continue

    if batch_pks:
        msg = f"Enqueued {len(batch_pks)} import(s): {', '.join(batch_pks[:3])}"
        if len(batch_pks) > 3:
            msg += f" + {len(batch_pks) - 3} more"
        messages.success(request, msg)
    else:
        messages.error(request, "No imports were enqueued.")

    return redirect("posting:posting-imports")
