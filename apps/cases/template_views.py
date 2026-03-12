"""AP Cases template views (server-side rendered)."""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.cases.models import APCase
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.core.enums import CasePriority, CaseStatus, ProcessingPath

logger = logging.getLogger(__name__)


def _build_fallback_summary(case, decisions, validation_issues):
    """Build a lightweight summary dict when APCaseSummary doesn't exist."""
    parts = []
    invoice = case.invoice

    # Basic case info
    parts.append(
        f"Case {case.case_number} for invoice {invoice.invoice_number or 'N/A'}"
        f" from {invoice.raw_vendor_name or 'unknown vendor'}."
    )
    parts.append(f"Processing path: {case.get_processing_path_display()}.")

    # Path decision
    path_decision = next(
        (d for d in decisions if d.decision_type == "PATH_SELECTED"), None
    )
    if path_decision and path_decision.rationale:
        parts.append(f"Path rationale: {path_decision.rationale}.")

    # Validation outcome
    if validation_issues:
        fails = [i for i in validation_issues if i["status"] == "FAIL"]
        warns = [i for i in validation_issues if i["status"] == "WARNING"]
        issue_parts = []
        if fails:
            issue_parts.append(f"{len(fails)} failed check(s)")
        if warns:
            issue_parts.append(f"{len(warns)} warning(s)")
        parts.append(f"Non-PO validation: {', '.join(issue_parts)}.")

    # Match decision
    match_decision = next(
        (d for d in decisions if d.decision_type == "MATCH_DETERMINED"), None
    )
    if match_decision and match_decision.rationale:
        parts.append(match_decision.rationale)

    # Recommendation
    recommendation = None
    if case.status == "FAILED":
        recommendation = "Case processing failed. Review exceptions and consider reprocessing."
    elif validation_issues:
        fails = [i for i in validation_issues if i["status"] == "FAIL"]
        if fails:
            recommendation = f"Resolve failed checks: {', '.join(i['check_name'] for i in fails)}."

    if not parts:
        return None

    return {
        "latest_summary": " ".join(parts),
        "recommendation": recommendation,
        "reviewer_summary": None,
        "is_fallback": True,
    }


def _build_copilot_context(case, invoice, po, grns, stages, decisions,
                           exceptions, validation_issues, agent_runs, summary):
    """Build a structured dict of case data for the copilot panel JS."""
    ctx = {
        "case_number": case.case_number,
        "status": case.get_status_display(),
        "processing_path": case.get_processing_path_display(),
        "priority": case.get_priority_display() if hasattr(case, "get_priority_display") else str(case.priority),
        "created_at": str(case.created_at),
        "assigned_to": case.assigned_to.get_short_name() if case.assigned_to else None,
    }

    # Invoice details
    ctx["invoice"] = {
        "invoice_number": invoice.invoice_number or "N/A",
        "vendor_name": invoice.raw_vendor_name or (invoice.vendor.name if invoice.vendor else "Unknown"),
        "total_amount": str(invoice.total_amount) if invoice.total_amount else None,
        "currency": invoice.currency or "",
        "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
        "po_number": invoice.po_number or None,
        "extraction_confidence": float(invoice.extraction_confidence) if invoice.extraction_confidence else None,
        "status": invoice.get_status_display() if hasattr(invoice, "get_status_display") else str(invoice.status),
    }

    # Line items
    line_items = []
    for li in invoice.line_items.all()[:20]:
        line_items.append({
            "description": li.description or "",
            "quantity": str(li.quantity) if li.quantity else None,
            "unit_price": str(li.unit_price) if li.unit_price else None,
            "amount": str(li.line_amount) if li.line_amount else None,
        })
    ctx["invoice"]["line_items"] = line_items

    # PO
    if po:
        ctx["purchase_order"] = {
            "po_number": po.po_number,
            "vendor_name": po.vendor.name if po.vendor else "Unknown",
            "total_amount": str(po.total_amount) if po.total_amount else None,
            "status": str(po.status) if hasattr(po, "status") else None,
        }
    else:
        ctx["purchase_order"] = None

    # GRNs
    ctx["grns"] = [
        {"grn_number": g.grn_number, "receipt_date": str(g.receipt_date) if g.receipt_date else None}
        for g in grns[:10]
    ]

    # Stages
    ctx["stages"] = [
        {"name": s.get_stage_name_display(), "status": s.stage_status,
         "notes": s.notes[:200] if s.notes else ""}
        for s in stages
    ]

    # Decisions
    ctx["decisions"] = [
        {"type": d.get_decision_type_display(), "value": d.decision_value,
         "rationale": d.rationale[:200] if d.rationale else ""}
        for d in decisions
    ]

    # Exceptions
    ctx["exceptions"] = [
        {"type": e.exception_type, "severity": e.severity,
         "description": e.message[:200] if e.message else ""}
        for e in exceptions[:20]
    ]

    # Validation issues
    ctx["validation_issues"] = validation_issues

    # Summary
    if summary:
        if hasattr(summary, "latest_summary"):
            ctx["summary"] = summary.latest_summary
        elif isinstance(summary, dict):
            ctx["summary"] = summary.get("latest_summary", "")
    else:
        ctx["summary"] = None

    # Agent runs
    ctx["agent_runs"] = [
        {"agent": r.agent_definition.name if r.agent_definition else r.agent_type,
         "status": r.status, "confidence": float(r.confidence) if r.confidence else None,
         "reasoning": (r.summarized_reasoning or "")[:200]}
        for r in agent_runs
    ]

    return ctx


@login_required
def case_inbox(request):
    """AP Cases inbox — main listing of all cases with filters."""
    qs = CaseSelectors.inbox(
        processing_path=request.GET.get("processing_path", ""),
        status=request.GET.get("status", ""),
        priority=request.GET.get("priority", ""),
        search=request.GET.get("q", ""),
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    stats = CaseSelectors.stats()

    return render(request, "cases/case_inbox.html", {
        "cases": page_obj,
        "page_obj": page_obj,
        "stats": stats,
        "status_choices": CaseStatus.choices,
        "path_choices": ProcessingPath.choices,
        "priority_choices": CasePriority.choices,
    })


@login_required
def case_console(request, pk):
    """Case console — deep-dive investigation view for a single AP Case."""
    case = get_object_or_404(
        APCase.objects.select_related(
            "invoice", "invoice__vendor", "invoice__document_upload",
            "vendor", "purchase_order", "reconciliation_result",
            "assigned_to",
        ).prefetch_related(
            "stages", "artifacts", "decisions",
            "assignments", "comments", "activities",
        ),
        pk=pk, is_active=True,
    )

    invoice = case.invoice
    po = case.purchase_order
    stages = list(case.stages.order_by("created_at"))
    decisions = list(case.decisions.order_by("created_at"))
    artifacts = list(case.artifacts.order_by("-created_at"))
    comments = list(case.comments.select_related("author").order_by("created_at"))

    # GRNs linked to PO
    grns = []
    if po:
        from apps.documents.models import GoodsReceiptNote
        grns = list(
            GoodsReceiptNote.objects.filter(purchase_order=po)
            .select_related("vendor")
            .prefetch_related("line_items")
        )

    # Reconciliation exceptions
    exceptions = []
    recon_result = case.reconciliation_result
    if recon_result:
        exceptions = list(recon_result.exceptions.all().order_by("-severity", "exception_type"))

    # Non-PO validation issues (from VALIDATION_RESULT artifact)
    validation_issues = []
    validation_artifact = case.artifacts.filter(artifact_type="VALIDATION_RESULT").order_by("-version").first()
    if validation_artifact and isinstance(validation_artifact.payload, dict):
        checks = validation_artifact.payload.get("checks", {})
        for check_name, check_data in checks.items():
            status = check_data.get("status", "")
            if status in ("FAIL", "WARNING"):
                validation_issues.append({
                    "check_name": check_name.replace("_", " ").title(),
                    "status": status,
                    "message": check_data.get("message", ""),
                })

    # Agent runs — check via recon result, then fall back to case's invoice
    agent_runs = []
    if recon_result:
        from apps.agents.models import AgentRun
        agent_runs = list(
            AgentRun.objects.filter(reconciliation_result=recon_result)
            .select_related("agent_definition")
            .prefetch_related("steps", "tool_calls", "decisions", "recommendations")
            .order_by("created_at")
        )

    # Summary — use APCaseSummary if available, otherwise build from decisions/artifacts
    summary = getattr(case, "summary", None)
    if not summary:
        # Build a lightweight summary dict from available stage/decision data
        built_summary = _build_fallback_summary(case, decisions, validation_issues)
        if built_summary:
            summary = built_summary

    # Timeline
    from apps.auditlog.timeline_service import CaseTimelineService
    timeline = CaseTimelineService.get_case_timeline(invoice.pk)

    # Security: role-aware trace visibility
    from apps.core.enums import UserRole
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    # Build copilot context — structured case data for client-side Q&A
    copilot_context = _build_copilot_context(
        case, invoice, po, grns, stages, decisions,
        exceptions, validation_issues, agent_runs, summary,
    )

    return render(request, "cases/case_console.html", {
        "case": case,
        "invoice": invoice,
        "po": po,
        "stages": stages,
        "decisions": decisions,
        "artifacts": artifacts,
        "comments": comments,
        "grns": grns,
        "exceptions": exceptions,
        "validation_issues": validation_issues,
        "total_issues_count": len(exceptions) + len(validation_issues),
        "agent_runs": agent_runs,
        "summary": summary,
        "timeline": timeline,
        "show_full_trace": show_full_trace,
        "copilot_context_json": json.dumps(copilot_context, default=str),
    })


@login_required
def reprocess_case(request, pk):
    """Reprocess a case from a specific stage."""
    if request.method != "POST":
        return redirect("cases:case_console", pk=pk)

    case = get_object_or_404(APCase, pk=pk, is_active=True)
    stage = request.POST.get("stage", "")

    if not stage:
        messages.warning(request, "No stage specified for reprocessing.")
        return redirect("cases:case_console", pk=pk)

    from apps.cases.tasks import reprocess_case_from_stage_task
    from apps.core.utils import dispatch_task

    try:
        dispatch_task(reprocess_case_from_stage_task, case_id=case.pk, stage=stage)
        messages.success(request, f"Case {case.case_number} reprocessed from {stage}.")
    except Exception as exc:
        messages.error(request, f"Reprocessing failed: {exc}")

    return redirect("cases:case_console", pk=pk)
