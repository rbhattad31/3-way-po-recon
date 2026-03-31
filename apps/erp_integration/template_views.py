"""ERP Integration template views -- ERPConnection list, create, detail/edit."""
from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import observed_action
from apps.core.permissions import permission_required_code
from apps.erp_integration.enums import ERPConnectionStatus, ERPConnectorType
from apps.erp_integration.forms import ERPConnectionForm
from apps.erp_integration.models import (
    ERPConnection,
    ERPReferenceCacheRecord,
    ERPResolutionLog,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# List
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("users.manage")
@observed_action("erp.view_connections", permission="users.manage", entity_type="ERPConnection")
def erp_connection_list(request):
    """List all ERP connections with filters."""
    qs = ERPConnection.objects.order_by("-is_default", "name")

    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(base_url__icontains=search))

    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)

    type_filter = request.GET.get("type", "")
    if type_filter:
        qs = qs.filter(connector_type=type_filter)

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    # Quick stats
    total = ERPConnection.objects.count()
    active = ERPConnection.objects.filter(status=ERPConnectionStatus.ACTIVE).count()
    default_conn = ERPConnection.objects.filter(is_default=True).first()
    cache_count = ERPReferenceCacheRecord.objects.count()

    return render(request, "erp_integration/connection_list.html", {
        "page_obj": page,
        "search": search,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "statuses": ERPConnectionStatus.choices,
        "connector_types": ERPConnectorType.choices,
        "kpi": {
            "total": total,
            "active": active,
            "default_name": default_conn.name if default_conn else None,
            "cache_count": cache_count,
        },
    })


# ────────────────────────────────────────────────────────────────
# Create
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("users.manage")
@observed_action("erp.create_connection", permission="users.manage", entity_type="ERPConnection")
def erp_connection_create(request):
    """Create a new ERP connection."""
    if request.method == "POST":
        form = ERPConnectionForm(request.POST)
        if form.is_valid():
            conn = form.save(commit=False)
            conn.created_by = request.user
            conn.updated_by = request.user
            conn.save()
            messages.success(request, f"ERP Connection '{conn.name}' created.")
            return redirect("erp_integration:erp_connection_detail", pk=conn.pk)
    else:
        form = ERPConnectionForm()

    return render(request, "erp_integration/connection_form.html", {
        "form": form,
        "mode": "create",
    })


# ────────────────────────────────────────────────────────────────
# Detail / Edit
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("users.manage")
@observed_action("erp.view_connection", permission="users.manage", entity_type="ERPConnection")
def erp_connection_detail(request, pk):
    """View and edit an ERP connection."""
    conn = get_object_or_404(ERPConnection, pk=pk)

    if request.method == "POST":
        form = ERPConnectionForm(request.POST, instance=conn)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.updated_by = request.user
            updated.save()
            messages.success(request, f"Connection '{updated.name}' updated.")
            return redirect("erp_integration:erp_connection_detail", pk=pk)
    else:
        form = ERPConnectionForm(instance=conn)

    # Recent resolution logs for this connector
    recent_logs = (
        ERPResolutionLog.objects
        .filter(connector_name=conn.name)
        .order_by("-created_at")[:20]
    )

    # Cache entries for this connector
    cache_entries = (
        ERPReferenceCacheRecord.objects
        .filter(connector_name=conn.name)
        .order_by("-created_at")[:20]
    )

    return render(request, "erp_integration/connection_detail.html", {
        "connection": conn,
        "form": form,
        "recent_logs": recent_logs,
        "cache_entries": cache_entries,
    })


# ────────────────────────────────────────────────────────────────
# Delete (soft-delete)
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("users.manage")
def erp_connection_delete(request, pk):
    """Soft-delete an ERP connection."""
    conn = get_object_or_404(ERPConnection, pk=pk)
    conn.status = ERPConnectionStatus.INACTIVE
    conn.updated_by = request.user
    conn.save(update_fields=["status", "updated_by", "updated_at"])
    messages.success(request, f"Connection '{conn.name}' deactivated.")
    return redirect("erp_integration:erp_connection_list")


# ────────────────────────────────────────────────────────────────
# Test Connection
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("users.manage")
def erp_connection_test(request, pk):
    """Quick connectivity test for an ERP connection."""
    conn = get_object_or_404(ERPConnection, pk=pk)
    try:
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.create_from_connection(conn)
        if connector is None:
            messages.warning(request, "Could not instantiate connector.")
        else:
            messages.success(
                request,
                f"Connector '{conn.name}' ({conn.get_connector_type_display()}) instantiated successfully.",
            )
    except Exception as exc:
        messages.error(request, f"Connection test failed: {exc}")
    return redirect("erp_integration:erp_connection_detail", pk=pk)


# ────────────────────────────────────────────────────────────────
# AJAX Test Connection (before save)
# ────────────────────────────────────────────────────────────────
@login_required
@require_POST
@permission_required_code("users.manage")
def erp_connection_test_ajax(request):
    """Test connectivity from form data without saving the record.

    Accepts JSON body with the form field values, builds a connector
    from that config, and runs ``test_connectivity()``.
    Returns JSON: ``{"success": bool, "message": str}``
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"success": False, "message": "Invalid request body."}, status=400)

    connector_type = data.get("connector_type", "")
    if not connector_type:
        return JsonResponse({"success": False, "message": "Connector type is required."})

    # Parse metadata_json if it's a string
    metadata_json = data.get("metadata_json", {})
    if isinstance(metadata_json, str):
        try:
            metadata_json = json.loads(metadata_json) if metadata_json.strip() else {}
        except json.JSONDecodeError:
            metadata_json = {}

    config = {
        "connector_type": connector_type,
        "base_url": data.get("base_url", ""),
        "timeout_seconds": int(data.get("timeout_seconds", 30) or 30),
        "auth_config_json": {},
        "metadata_json": metadata_json,
        "auth_type": data.get("auth_type", ""),
        "api_key_env": data.get("api_key_env", ""),
        "connection_string_env": data.get("connection_string_env", ""),
        "database_name": data.get("database_name", ""),
        "tenant_id": data.get("tenant_id", ""),
        "client_id_env": data.get("client_id_env", ""),
        "client_secret_env": data.get("client_secret_env", ""),
        # Builder fields for SQL Server (password sent as plaintext for
        # the test call only -- it is encrypted before DB storage).
        "db_host": data.get("db_host", ""),
        "db_port": int(data.get("db_port") or 0) or None,
        "db_username": data.get("db_username", ""),
        "db_driver": data.get("db_driver", ""),
        "db_trust_cert": bool(data.get("db_trust_cert", False)),
    }

    # For the test call, encrypt the plaintext password so the connector
    # can decrypt it via the normal path.
    raw_password = data.get("db_password", "")
    if raw_password:
        from apps.erp_integration.crypto import encrypt_value
        config["db_password_encrypted"] = encrypt_value(raw_password)
    else:
        config["db_password_encrypted"] = ""

    try:
        from apps.erp_integration.services.connector_factory import ConnectorFactory
        connector = ConnectorFactory.create_from_config(config)
        success, message = connector.test_connectivity()
        return JsonResponse({"success": success, "message": message})
    except Exception as exc:
        return JsonResponse({"success": False, "message": str(exc)})


# ────────────────────────────────────────────────────────────────
# ERP Reference Data Browser
# ────────────────────────────────────────────────────────────────
@login_required
@permission_required_code("invoices.view")
@observed_action("erp.view_reference_data", permission="invoices.view", entity_type="ERPReferenceData")
def erp_reference_data(request):
    """Browse all imported ERP reference data (vendors, items, tax codes, cost centers, PO refs).

    Each tab shows the live contents of the corresponding posting_core reference table
    so operators can verify what master data the posting mapping engine will resolve against.
    """
    from apps.posting_core.models import (
        ERPCostCenterReference,
        ERPItemReference,
        ERPPOReference,
        ERPReferenceImportBatch,
        ERPTaxCodeReference,
        ERPVendorReference,
    )

    VALID_TABS = ("vendors", "items", "tax", "cost_centers", "po_refs")
    active_tab = request.GET.get("tab", "vendors")
    if active_tab not in VALID_TABS:
        active_tab = "vendors"
    search = request.GET.get("q", "").strip()

    # KPI counts (active/open records only)
    vendor_count = ERPVendorReference.objects.filter(is_active=True).count()
    item_count = ERPItemReference.objects.filter(is_active=True).count()
    tax_count = ERPTaxCodeReference.objects.filter(is_active=True).count()
    cost_center_count = ERPCostCenterReference.objects.filter(is_active=True).count()
    po_ref_count = ERPPOReference.objects.filter(is_open=True).count()
    last_batch = ERPReferenceImportBatch.objects.order_by("-created_at").first()
    total_batches = ERPReferenceImportBatch.objects.count()

    # Build paginated queryset for the active tab
    page_obj = None
    if active_tab == "vendors":
        qs = ERPVendorReference.objects.select_related("batch").order_by("vendor_code")
        if search:
            qs = qs.filter(
                Q(vendor_code__icontains=search)
                | Q(vendor_name__icontains=search)
                | Q(vendor_group__icontains=search)
            )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))

    elif active_tab == "items":
        qs = ERPItemReference.objects.select_related("batch").order_by("item_code")
        if search:
            qs = qs.filter(
                Q(item_code__icontains=search)
                | Q(item_name__icontains=search)
                | Q(category__icontains=search)
            )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))

    elif active_tab == "tax":
        qs = ERPTaxCodeReference.objects.select_related("batch").order_by("tax_code")
        if search:
            qs = qs.filter(
                Q(tax_code__icontains=search)
                | Q(tax_label__icontains=search)
                | Q(country_code__icontains=search)
            )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))

    elif active_tab == "cost_centers":
        qs = ERPCostCenterReference.objects.select_related("batch").order_by("cost_center_code")
        if search:
            qs = qs.filter(
                Q(cost_center_code__icontains=search)
                | Q(cost_center_name__icontains=search)
                | Q(department__icontains=search)
                | Q(business_unit__icontains=search)
            )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))

    elif active_tab == "po_refs":
        qs = ERPPOReference.objects.select_related("batch").order_by("po_number", "po_line_number")
        if search:
            qs = qs.filter(
                Q(po_number__icontains=search)
                | Q(vendor_code__icontains=search)
                | Q(item_code__icontains=search)
                | Q(description__icontains=search)
            )
        page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))

    return render(request, "erp_integration/reference_data.html", {
        "active_tab": active_tab,
        "search": search,
        "page_obj": page_obj,
        "kpi": {
            "vendor_count": vendor_count,
            "item_count": item_count,
            "tax_count": tax_count,
            "cost_center_count": cost_center_count,
            "po_ref_count": po_ref_count,
            "total_batches": total_batches,
        },
        "last_batch": last_batch,
    })
