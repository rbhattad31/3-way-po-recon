"""Template views for Bulk Extraction Intake (Phase 1).

Provides:
- bulk_job_list: list of bulk extraction jobs with start-new form
- bulk_job_start: create and dispatch a new bulk job
- bulk_job_detail: job summary + item-level detail table
- bulk_source_list: list + manage all source connections
- bulk_source_edit: edit an existing source connection
- bulk_source_delete: deactivate (soft-delete) a source connection
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from django.utils.html import escape

from apps.core.decorators import observed_action
from apps.core.enums import BulkItemStatus, BulkJobStatus, BulkSourceType
from apps.core.permissions import permission_required_code
from apps.extraction.bulk_models import (
    BulkExtractionItem,
    BulkExtractionJob,
    BulkSourceConnection,
)

logger = logging.getLogger(__name__)

# Config keys required per source type
_SOURCE_CONFIG_KEYS = {
    BulkSourceType.LOCAL_FOLDER: ["folder_path"],
    BulkSourceType.GOOGLE_DRIVE: ["folder_id", "service_account_json"],
    BulkSourceType.ONEDRIVE: [
        "tenant_id", "client_id", "client_secret", "drive_id", "folder_path",
    ],
}


@login_required
@permission_required_code("extraction.bulk_view")
@observed_action(
    "extraction.bulk_job_list",
    permission="extraction.bulk_view",
    entity_type="BulkExtractionJob",
)
def bulk_job_list(request):
    """List all bulk extraction jobs with summary stats."""
    jobs_qs = (
        BulkExtractionJob.objects
        .select_related("source_connection", "started_by")
        .order_by("-created_at")
    )

    # Scope to current tenant; superusers see all jobs
    tenant = getattr(request, "tenant", None)
    if tenant is not None:
        jobs_qs = jobs_qs.filter(tenant=tenant)

    status_filter = request.GET.get("status")
    if status_filter and status_filter in dict(BulkJobStatus.choices):
        jobs_qs = jobs_qs.filter(status=status_filter)

    paginator = Paginator(jobs_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    sources = BulkSourceConnection.objects.filter(is_active=True).order_by("name")

    return render(request, "extraction/bulk_job_list.html", {
        "jobs": page_obj,
        "page_obj": page_obj,
        "sources": sources,
        "status_choices": BulkJobStatus.choices,
        "status_filter": status_filter or "",
        "source_types": BulkSourceType.choices,
    })


@login_required
@require_POST
@permission_required_code("extraction.bulk_create")
@observed_action(
    "extraction.bulk_job_start",
    permission="extraction.bulk_create",
    entity_type="BulkExtractionJob",
    audit_event="BULK_JOB_CREATED",
)
def bulk_job_start(request):
    """Create a new bulk extraction job and dispatch it."""
    source_id = request.POST.get("source_id")
    if not source_id:
        messages.error(request, "Please select a source connection.")
        return redirect("extraction:bulk_job_list")

    try:
        source = BulkSourceConnection.objects.get(pk=source_id, is_active=True)
    except BulkSourceConnection.DoesNotExist:
        messages.error(request, "Source connection not found or inactive.")
        return redirect("extraction:bulk_job_list")

    from apps.extraction.services.bulk_service import BulkExtractionService

    job = BulkExtractionService.create_job(
        source_connection=source,
        started_by=request.user,
        tenant=getattr(request, "tenant", None),
    )

    # Dispatch via Celery (or sync fallback)
    from apps.extraction.bulk_tasks import run_bulk_job_task
    from apps.core.utils import dispatch_task

    dispatch_task(run_bulk_job_task, job_id=job.pk)

    messages.success(
        request,
        f"Bulk extraction job started for '{source.name}'. "
        f"Job ID: {job.job_id}",
    )
    return redirect("extraction:bulk_job_detail", job_id=job.pk)


@login_required
@require_POST
@permission_required_code("extraction.bulk_create")
@observed_action(
    "extraction.bulk_source_create",
    permission="extraction.bulk_create",
    entity_type="BulkSourceConnection",
)
def bulk_source_create(request):
    """Create a new BulkSourceConnection from the inline form."""
    name = (request.POST.get("name") or "").strip()
    source_type = request.POST.get("source_type", "")

    if not name:
        messages.error(request, "Source name is required.")
        return redirect("extraction:bulk_job_list")

    if source_type not in dict(BulkSourceType.choices):
        messages.error(request, "Invalid source type.")
        return redirect("extraction:bulk_job_list")

    # Build config_json from posted fields
    config = {}
    for key in _SOURCE_CONFIG_KEYS.get(source_type, []):
        val = (request.POST.get(f"config_{key}") or "").strip()
        if not val:
            messages.error(request, f"Missing required config field: {key}")
            return redirect("extraction:bulk_job_list")
        config[key] = val

    BulkSourceConnection.objects.create(
        name=name,
        source_type=source_type,
        config_json=config,
        is_active=True,
    )
    messages.success(request, f"Source connection '{escape(name)}' created.")
    return redirect("extraction:bulk_job_list")


@login_required
@require_POST
@permission_required_code("extraction.bulk_create")
def bulk_source_test(request):
    """AJAX endpoint: test a source connection config without saving it."""
    source_type = request.POST.get("source_type", "")
    if source_type not in dict(BulkSourceType.choices):
        return JsonResponse({"ok": False, "message": "Please select a valid source type."})

    config = {}
    for key in _SOURCE_CONFIG_KEYS.get(source_type, []):
        config[key] = (request.POST.get(f"config_{key}") or "").strip()

    try:
        from apps.extraction.services.bulk_source_adapters import get_adapter
        adapter = get_adapter(source_type, config)
        result = adapter.test_connection()
        return JsonResponse({"ok": result.valid, "message": result.message})
    except Exception as exc:
        logger.exception("Connection test error for source_type=%s", source_type)
        return JsonResponse({"ok": False, "message": f"Test failed: {exc}"})


@login_required
@permission_required_code("extraction.bulk_view")
@observed_action(
    "extraction.bulk_job_detail",
    permission="extraction.bulk_view",
    entity_type="BulkExtractionJob",
)
def bulk_job_detail(request, job_id: int):
    """Show bulk job summary and item-level details."""
    tenant = getattr(request, "tenant", None)
    job_qs = BulkExtractionJob.objects.select_related("source_connection", "started_by")
    if tenant is not None:
        job_qs = job_qs.filter(tenant=tenant)
    job = get_object_or_404(job_qs, pk=job_id)

    items_qs = job.items.select_related(
        "document_upload", "extraction_run",
    ).order_by("created_at")

    item_status_filter = request.GET.get("item_status")
    if item_status_filter and item_status_filter in dict(BulkItemStatus.choices):
        items_qs = items_qs.filter(status=item_status_filter)

    paginator = Paginator(items_qs, 50)
    items_page = paginator.get_page(request.GET.get("page"))

    # Summary stats
    all_items = job.items.all()
    item_stats = {
        "total": all_items.count(),
        "discovered": all_items.filter(status=BulkItemStatus.DISCOVERED).count(),
        "processed": all_items.filter(status=BulkItemStatus.PROCESSED).count(),
        "failed": all_items.filter(status=BulkItemStatus.FAILED).count(),
        "skipped": all_items.filter(status__in=[
            BulkItemStatus.SKIPPED, BulkItemStatus.UNSUPPORTED,
        ]).count(),
        "duplicate": all_items.filter(status=BulkItemStatus.DUPLICATE).count(),
        "credit_blocked": all_items.filter(status=BulkItemStatus.CREDIT_BLOCKED).count(),
        "processing": all_items.filter(status__in=[
            BulkItemStatus.REGISTERED, BulkItemStatus.PROCESSING,
        ]).count(),
    }

    return render(request, "extraction/bulk_job_detail.html", {
        "job": job,
        "items": items_page,
        "items_page_obj": items_page,
        "item_stats": item_stats,
        "item_status_choices": BulkItemStatus.choices,
        "item_status_filter": item_status_filter or "",
    })


# ---------------------------------------------------------------------------
# Source connection management
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("extraction.bulk_view")
@observed_action(
    "extraction.bulk_source_list",
    permission="extraction.bulk_view",
    entity_type="BulkSourceConnection",
)
def bulk_source_list(request):
    """List all source connections with job counts."""
    sources = (
        BulkSourceConnection.objects
        .prefetch_related("jobs")
        .order_by("-created_at")
    )
    return render(request, "extraction/bulk_source_list.html", {
        "sources": sources,
        "source_types": BulkSourceType.choices,
        "source_config_keys": _SOURCE_CONFIG_KEYS,
    })


@login_required
@permission_required_code("extraction.bulk_create")
@observed_action(
    "extraction.bulk_source_edit",
    permission="extraction.bulk_create",
    entity_type="BulkSourceConnection",
)
def bulk_source_edit(request, pk: int):
    """Edit an existing BulkSourceConnection.

    BulkSourceConnection is platform-level configuration. Only platform admins
    and superusers should be able to edit connections.
    """
    if not (request.user.is_superuser or getattr(request.user, "is_platform_admin", False)):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only platform admins can edit source connections.")
    source = get_object_or_404(BulkSourceConnection, pk=pk)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        source_type = request.POST.get("source_type", "")

        if not name:
            messages.error(request, "Source name is required.")
            return redirect("extraction:bulk_source_list")

        if source_type not in dict(BulkSourceType.choices):
            messages.error(request, "Invalid source type.")
            return redirect("extraction:bulk_source_list")

        config = {}
        for key in _SOURCE_CONFIG_KEYS.get(source_type, []):
            val = (request.POST.get(f"config_{key}") or "").strip()
            # For password fields, keep the old value if nothing new was entered
            if not val and key in source.config_json:
                config[key] = source.config_json[key]
            elif not val:
                messages.error(request, f"Missing required config field: {key}")
                return redirect("extraction:bulk_source_list")
            else:
                config[key] = val

        source.name = name
        source.source_type = source_type
        source.config_json = config
        source.save(update_fields=["name", "source_type", "config_json", "updated_at"])
        messages.success(request, f"Source connection '{escape(name)}' updated.")
        return redirect("extraction:bulk_source_list")

    # GET — render the edit page
    return render(request, "extraction/bulk_source_edit.html", {
        "source": source,
        "source_types": BulkSourceType.choices,
        "source_config_keys": _SOURCE_CONFIG_KEYS,
    })


@login_required
@require_POST
@permission_required_code("extraction.bulk_create")
def bulk_source_delete(request, pk: int):
    """Deactivate (soft-delete) a source connection.

    BulkSourceConnection is platform-level configuration. Only platform admins
    and superusers should be able to deactivate connections.
    """
    if not (request.user.is_superuser or getattr(request.user, "is_platform_admin", False)):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Only platform admins can delete source connections.")
    source = get_object_or_404(BulkSourceConnection, pk=pk)
    source.is_active = False
    source.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"Source connection '{escape(source.name)}' deactivated.")
    return redirect("extraction:bulk_source_list")


@login_required
@require_POST
@permission_required_code("extraction.bulk_create")
def bulk_job_retry(request, job_id: int):
    """Reset a stuck/failed bulk job and re-queue it."""
    from apps.core.utils import dispatch_task
    from apps.extraction.bulk_tasks import run_bulk_job_task
    from django.utils import timezone

    tenant = getattr(request, "tenant", None)
    job_qs = BulkExtractionJob.objects.all()
    if tenant is not None:
        job_qs = job_qs.filter(tenant=tenant)
    job = get_object_or_404(job_qs, pk=job_id)

    # Only allow retry for non-running states
    if job.status in (BulkJobStatus.SCANNING, BulkJobStatus.PROCESSING):
        # Treat as stuck — reset it
        pass
    elif job.status == BulkJobStatus.COMPLETED:
        messages.warning(request, "Job already completed successfully.")
        return redirect("extraction:bulk_job_detail", job_id=job.pk)

    # Reset job counters and status
    job.status = BulkJobStatus.QUEUED
    job.started_at = None
    job.completed_at = None
    job.error_message = ""
    job.total_found = 0
    job.total_registered = 0
    job.total_success = 0
    job.total_failed = 0
    job.total_skipped = 0
    job.total_credit_blocked = 0
    job.save(update_fields=[
        "status", "started_at", "completed_at", "error_message",
        "total_found", "total_registered", "total_success", "total_failed",
        "total_skipped", "total_credit_blocked", "updated_at",
    ])

    # Remove existing items so the scan is clean
    job.items.all().delete()

    dispatch_task(run_bulk_job_task, job_id=job.pk)

    messages.success(request, f"Job re-queued. Scanning source '{job.source_connection.name}'...")
    return redirect("extraction:bulk_job_detail", job_id=job.pk)
