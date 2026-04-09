"""Dashboard template views."""
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.agents.models import AgentRun
from apps.cases.models import APCase, APCaseStage
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.core.enums import (
    AgentRunStatus,
    AgentType,
    CasePriority,
    CaseStatus,
    ProcessingPath,
    StageStatus,
    UserRole,
)
from apps.dashboard.services import DashboardService
from apps.core.tenant_utils import get_tenant_or_none


@login_required
def command_center(request):
    """Agentic AP Command Center — AI Operations dashboard."""
    user_role = getattr(request.user, "role", "")
    return render(request, "dashboard/agentic_command_center.html", {
        "user_role": user_role,
    })


@login_required
def analytics(request):
    summary = DashboardService.get_summary(user=request.user)
    recent_activity = DashboardService.get_recent_activity(limit=15, user=request.user)
    return render(request, "dashboard/index.html", {
        "summary": summary,
        "recent_activity": recent_activity,
    })


# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------
_STATUS_GROUPS = {
    "in_flight": [
        CaseStatus.NEW,
        CaseStatus.INTAKE_IN_PROGRESS,
        CaseStatus.EXTRACTION_IN_PROGRESS,
        CaseStatus.EXTRACTION_COMPLETED,
        CaseStatus.PATH_RESOLUTION_IN_PROGRESS,
        CaseStatus.TWO_WAY_IN_PROGRESS,
        CaseStatus.THREE_WAY_IN_PROGRESS,
        CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
        CaseStatus.GRN_ANALYSIS_IN_PROGRESS,
        CaseStatus.EXCEPTION_ANALYSIS_IN_PROGRESS,
    ],
    "review": [
        CaseStatus.READY_FOR_REVIEW,
        CaseStatus.IN_REVIEW,
        CaseStatus.REVIEW_COMPLETED,
    ],
    "approval": [
        CaseStatus.READY_FOR_APPROVAL,
        CaseStatus.APPROVAL_IN_PROGRESS,
        CaseStatus.READY_FOR_GL_CODING,
        CaseStatus.READY_FOR_POSTING,
    ],
    "closed": [CaseStatus.CLOSED],
    "exception": [CaseStatus.FAILED, CaseStatus.ESCALATED, CaseStatus.REJECTED],
}


@login_required
def agent_monitor(request):
    """Case Operations Dashboard — case-centric view with agent activity."""

    # --- Filters ---
    path_filter = request.GET.get("path")
    status_filter = request.GET.get("status")
    priority_filter = request.GET.get("priority")

    case_qs = APCase.objects.select_related(
        "invoice", "vendor", "purchase_order", "assigned_to",
    )
    tenant = get_tenant_or_none(request)
    if tenant is not None:
        case_qs = case_qs.filter(tenant=tenant)
    case_qs = CaseSelectors.scope_for_user(case_qs, request.user)

    if path_filter:
        case_qs = case_qs.filter(processing_path=path_filter)
    if status_filter:
        case_qs = case_qs.filter(status=status_filter)
    if priority_filter:
        case_qs = case_qs.filter(priority=priority_filter)

    # ---- KPI aggregates ----
    total_cases = case_qs.count()
    kpis = case_qs.aggregate(
        in_flight=Count("id", filter=Q(status__in=_STATUS_GROUPS["in_flight"])),
        review=Count("id", filter=Q(status__in=_STATUS_GROUPS["review"])),
        approval=Count("id", filter=Q(status__in=_STATUS_GROUPS["approval"])),
        closed=Count("id", filter=Q(status__in=_STATUS_GROUPS["closed"])),
        exception=Count("id", filter=Q(status__in=_STATUS_GROUPS["exception"])),
        avg_risk=Avg("risk_score"),
        needs_human=Count("id", filter=Q(requires_human_review=True)),
    )

    # ---- By processing path ----
    path_breakdown = (
        case_qs.values("processing_path")
        .annotate(
            count=Count("id"),
            closed=Count("id", filter=Q(status=CaseStatus.CLOSED)),
            in_review=Count("id", filter=Q(status__in=_STATUS_GROUPS["review"])),
            failed=Count("id", filter=Q(status=CaseStatus.FAILED)),
            avg_risk=Avg("risk_score"),
        )
        .order_by("processing_path")
    )

    # ---- By status ----
    status_breakdown = (
        case_qs.values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    # ---- By priority ----
    priority_breakdown = (
        case_qs.values("priority")
        .annotate(count=Count("id"))
        .order_by("priority")
    )

    # ---- Agent activity summary (over same case set) ----
    # Get invoice IDs from the filtered cases, then find agent runs via reconciliation_result
    case_invoice_ids = case_qs.values_list("invoice_id", flat=True)
    agent_qs = AgentRun.objects.filter(
        reconciliation_result__invoice_id__in=case_invoice_ids,
    )
    agent_stats = agent_qs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
        failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
        total_tokens=Sum("total_tokens"),
        avg_duration=Avg("duration_ms"),
        avg_confidence=Avg("confidence"),
    )

    # per-type breakdown
    agent_type_breakdown = (
        agent_qs.values("agent_type")
        .annotate(
            count=Count("id"),
            completed=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
            failed=Count("id", filter=Q(status=AgentRunStatus.FAILED)),
            avg_confidence=Avg("confidence"),
        )
        .order_by("agent_type")
    )

    # ---- Recent cases ----
    recent_cases = case_qs.order_by("-created_at")[:50]

    return render(request, "dashboard/agent_monitor.html", {
        "total_cases": total_cases,
        "kpis": kpis,
        "path_breakdown": path_breakdown,
        "status_breakdown": status_breakdown,
        "priority_breakdown": priority_breakdown,
        "agent_stats": agent_stats,
        "agent_type_breakdown": agent_type_breakdown,
        "recent_cases": recent_cases,
        # Filter support
        "processing_paths": ProcessingPath.choices,
        "case_statuses": CaseStatus.choices,
        "priorities": CasePriority.choices,
        "selected_path": path_filter or "",
        "selected_status": status_filter or "",
        "selected_priority": priority_filter or "",
    })


@login_required
def agent_performance(request):
    """Agent Performance Dashboard -- operational metrics & observability."""
    user_role = getattr(request.user, "role", "")

    start_date = timezone.now() - datetime.timedelta(days=7)
    agent_run_qs = AgentRun.objects.filter(started_at__gte=start_date)
    tenant = get_tenant_or_none(request)
    if tenant is not None:
        agent_run_qs = agent_run_qs.filter(tenant=tenant)
    plan_rows = (
        agent_run_qs
        .filter(input_payload__plan_source__isnull=False)
        .values("input_payload__plan_source")
        .annotate(
            count=Count("id"),
            avg_confidence=Avg("confidence"),
        )
        .order_by("input_payload__plan_source")
    )

    return render(request, "dashboard/agent_performance.html", {
        "user_role": user_role,
        "agent_types": AgentType.choices,
        "agent_statuses": AgentRunStatus.choices,
        "plan_comparison": list(plan_rows),
    })


@login_required
def agent_governance(request):
    """Agent Governance Dashboard — RBAC, authorization & compliance monitoring."""
    user_role = getattr(request.user, "role", "")
    is_full_governance = user_role in (UserRole.ADMIN, UserRole.AUDITOR)
    is_summary_governance = user_role in (
        UserRole.ADMIN, UserRole.AUDITOR, UserRole.FINANCE_MANAGER,
    )

    if not is_summary_governance:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Insufficient permissions.")

    return render(request, "dashboard/agent_governance.html", {
        "user_role": user_role,
        "is_full_governance": is_full_governance,
        "is_summary_governance": is_summary_governance,
        "agent_types": AgentType.choices,
        "agent_statuses": AgentRunStatus.choices,
    })


# ---------------------------------------------------------------------------
# Invoice Pipeline -- Kanban Board
# ---------------------------------------------------------------------------

@login_required
def invoice_pipeline(request):
    """Invoice Pipeline -- kanban board showing invoices across lifecycle stages."""
    from apps.documents.models import Invoice
    from apps.extraction.models import ExtractionResult
    from apps.reconciliation.models import ReconciliationResult, ReconciliationException
    from apps.cases.models import APCase, ReviewAssignment
    from apps.posting.models import InvoicePosting
    from apps.agents.models import AgentRun
    from apps.core.enums import InvoiceStatus, AgentRunStatus

    STAGES = [
        {"key": "intake", "label": "Intake", "icon": "bi-inbox", "color": "#6c757d", "bg": "#f8f9fa"},
        {"key": "reconciliation", "label": "Reconciliation", "icon": "bi-check2-all", "color": "#6f42c1", "bg": "#f3e8ff"},
        {"key": "human_review", "label": "Review", "icon": "bi-person-check", "color": "#ff9800", "bg": "#fff8e1"},
        {"key": "posting", "label": "Posting", "icon": "bi-send", "color": "#2196f3", "bg": "#e3f2fd"},
        {"key": "completed", "label": "Completed", "icon": "bi-check-circle", "color": "#198754", "bg": "#e8f5e9"},
    ]

    invoices = Invoice.objects.select_related("vendor").order_by("-created_at")
    tenant = get_tenant_or_none(request)
    if tenant is not None:
        invoices = invoices.filter(tenant=tenant)
    invoice_ids = list(invoices.values_list("id", flat=True))

    ext_map = {}
    for er in ExtractionResult.objects.filter(document_upload__invoices__pk__in=invoice_ids).select_related("document_upload"):
        inv = er.invoice
        if inv:
            ext_map[inv.pk] = er.id

    case_map = {}
    for c in APCase.objects.filter(invoice_id__in=invoice_ids).only(
        "id", "invoice_id", "status", "case_number", "processing_path",
    ):
        case_map[c.invoice_id] = c

    recon_map = {}
    for rr in ReconciliationResult.objects.filter(invoice_id__in=invoice_ids).only(
        "id", "invoice_id", "match_status", "reconciliation_mode",
        "total_amount_difference", "total_amount_difference_pct",
    ):
        recon_map[rr.invoice_id] = rr

    # Exception counts per reconciliation result
    exception_count_map = {}  # invoice_id -> count
    recon_ids = [rr.id for rr in recon_map.values()]
    if recon_ids:
        from django.db.models import Count
        exc_qs = (
            ReconciliationException.objects
            .filter(result_id__in=recon_ids, resolved=False)
            .values("result__invoice_id")
            .annotate(cnt=Count("id"))
        )
        for row in exc_qs:
            exception_count_map[row["result__invoice_id"]] = row["cnt"]

    review_map = {}
    for ra in (
        ReviewAssignment.objects
        .filter(reconciliation_result__invoice_id__in=invoice_ids)
        .select_related("reconciliation_result", "assigned_to")
        .only("id", "status", "priority", "reconciliation_result__invoice_id",
              "assigned_to__email", "assigned_to__first_name")
    ):
        review_map[ra.reconciliation_result.invoice_id] = ra

    posting_map = {}
    for p in InvoicePosting.objects.filter(invoice_id__in=invoice_ids).only("id", "invoice_id", "status"):
        posting_map[p.invoice_id] = p

    # Build agent-type map: invoice_id -> list of distinct agent types that ran
    AGENT_ICON_MAP = {
        "INVOICE_EXTRACTION": {"short": "Ext", "icon": "bi-file-earmark-text", "title": "Invoice Extraction"},
        "INVOICE_UNDERSTANDING": {"short": "Und", "icon": "bi-lightbulb", "title": "Invoice Understanding"},
        "PO_RETRIEVAL": {"short": "PO", "icon": "bi-cart-check", "title": "PO Retrieval"},
        "GRN_RETRIEVAL": {"short": "GRN", "icon": "bi-truck", "title": "GRN Retrieval"},
        "RECONCILIATION_ASSIST": {"short": "Recon", "icon": "bi-check2-all", "title": "Reconciliation Assist"},
        "EXCEPTION_ANALYSIS": {"short": "Exc", "icon": "bi-exclamation-triangle", "title": "Exception Analysis"},
        "REVIEW_ROUTING": {"short": "Rev", "icon": "bi-signpost-split", "title": "Review Routing"},
        "CASE_SUMMARY": {"short": "Sum", "icon": "bi-journal-text", "title": "Case Summary"},
    }
    agent_map = {}  # invoice_id -> [{short, icon, title, status}]

    def _add_agent(inv_id, agent_type, run_status):
        if inv_id not in agent_map:
            agent_map[inv_id] = []
        info = AGENT_ICON_MAP.get(agent_type, {"short": agent_type[:3], "icon": "bi-robot", "title": agent_type})
        existing = [a for a in agent_map[inv_id] if a["title"] == info["title"]]
        if existing:
            existing[0]["status"] = run_status
        else:
            agent_map[inv_id].append({**info, "status": run_status})

    # Agents linked via reconciliation_result -> invoice
    for inv_id, agent_type, run_status in (
        AgentRun.objects
        .filter(reconciliation_result__invoice_id__in=invoice_ids)
        .values_list("reconciliation_result__invoice_id", "agent_type", "status")
        .order_by("created_at")
    ):
        _add_agent(inv_id, agent_type, run_status)

    # Agents linked via document_upload -> invoice (extraction agents)
    upload_inv_map = dict(
        Invoice.objects
        .filter(id__in=invoice_ids, document_upload_id__isnull=False)
        .values_list("document_upload_id", "id")
    )
    if upload_inv_map:
        for upload_id, agent_type, run_status in (
            AgentRun.objects
            .filter(document_upload_id__in=upload_inv_map.keys())
            .values_list("document_upload_id", "agent_type", "status")
            .order_by("created_at")
        ):
            inv_id = upload_inv_map.get(upload_id)
            if inv_id:
                _add_agent(inv_id, agent_type, run_status)

    stage_buckets = {s["key"]: [] for s in STAGES}

    for inv in invoices:
        posting = posting_map.get(inv.id)
        review = review_map.get(inv.id)
        case = case_map.get(inv.id)
        recon = recon_map.get(inv.id)
        ext_id = ext_map.get(inv.id)

        card = {
            "id": inv.id,
            "invoice_number": inv.invoice_number or "N/A",
            "vendor": inv.raw_vendor_name or (inv.vendor.name if inv.vendor_id else "Unknown"),
            "amount": inv.total_amount,
            "currency": inv.currency or "INR",
            "date": inv.invoice_date,
            "status": inv.status,
            "confidence": inv.extraction_confidence,
            "po_number": inv.po_number,
            "ext_id": ext_id,
            "case": case,
            "recon": recon,
            "agents": agent_map.get(inv.id, []),
            # Stage-specific enrichment
            "recon_mode": getattr(recon, "reconciliation_mode", None),
            "match_status": getattr(recon, "match_status", None),
            "amount_diff": getattr(recon, "total_amount_difference", None),
            "amount_diff_pct": getattr(recon, "total_amount_difference_pct", None),
            "exception_count": exception_count_map.get(inv.id, 0),
            "processing_path": getattr(case, "processing_path", None),
            "review": review,
            "reviewer": (
                review.assigned_to.first_name or review.assigned_to.email.split("@")[0]
                if review and review.assigned_to_id else None
            ),
            "review_priority": getattr(review, "priority", None),
            "posting": posting,
        }

        if posting and posting.status == "POSTED":
            card["sub_status"] = "Posted"
            stage_buckets["completed"].append(card)
        elif posting and posting.status not in ("POSTED", "REJECTED", "SKIPPED"):
            card["sub_status"] = posting.status.replace("_", " ").title()
            stage_buckets["posting"].append(card)
        elif review and review.status in ("PENDING", "ASSIGNED", "IN_REVIEW"):
            card["sub_status"] = review.status.replace("_", " ").title()
            stage_buckets["human_review"].append(card)
        elif case and case.status in ("READY_FOR_REVIEW", "IN_REVIEW", "REVIEW_COMPLETED"):
            card["sub_status"] = case.status.replace("_", " ").title()
            stage_buckets["human_review"].append(card)
        elif case and case.status in ("NEW", "INTAKE_IN_PROGRESS", "AGENT_IN_PROGRESS"):
            card["sub_status"] = "Agent Processing"
            stage_buckets["human_review"].append(card)
        elif inv.status == InvoiceStatus.RECONCILED:
            card["sub_status"] = recon.match_status.replace("_", " ").title() if recon else "Reconciled"
            stage_buckets["reconciliation"].append(card)
        elif inv.status == InvoiceStatus.READY_FOR_RECON:
            card["sub_status"] = "Awaiting Reconciliation"
            stage_buckets["reconciliation"].append(card)
        elif inv.status == InvoiceStatus.PENDING_APPROVAL:
            card["sub_status"] = "Pending Approval"
            stage_buckets["intake"].append(card)
        elif inv.status in (InvoiceStatus.EXTRACTED, InvoiceStatus.VALIDATED, InvoiceStatus.EXTRACTION_IN_PROGRESS):
            card["sub_status"] = inv.status.replace("_", " ").title()
            stage_buckets["intake"].append(card)
        elif inv.status == InvoiceStatus.UPLOADED:
            card["sub_status"] = "Uploaded"
            stage_buckets["intake"].append(card)
        elif review and review.status == "APPROVED":
            card["sub_status"] = "Review Approved"
            stage_buckets["completed"].append(card)
        else:
            card["sub_status"] = inv.status.replace("_", " ").title() if inv.status else "Unknown"
            stage_buckets["intake"].append(card)

    for stage in STAGES:
        stage["cards"] = stage_buckets[stage["key"]]
        stage["count"] = len(stage["cards"])

    total = sum(s["count"] for s in STAGES)

    return render(request, "dashboard/invoice_pipeline.html", {
        "stages": STAGES,
        "total": total,
    })
