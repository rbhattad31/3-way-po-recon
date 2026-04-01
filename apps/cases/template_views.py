"""AP Cases template views (server-side rendered)."""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.cases.models import APCase
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.core.enums import CasePriority, CaseStatus, MatchStatus, ProcessingPath, ReconciliationMode, UserRole
from apps.core.permissions import permission_required_code, _has_permission_code

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


def _build_agent_timeline(agent_runs, decisions, show_full_trace):
    """Build a chronological timeline mixing agent cards and case decisions.

    Returns a list of dicts sorted by timestamp. Each dict has a ``kind``
    key: ``"agent"`` (with grouped children) or ``"decision"``.
    """
    entries = []

    for run in agent_runs:
        agent_name = (
            run.agent_definition.name if run.agent_definition else run.agent_type
        )
        children = []
        for step in run.steps.all():
            children.append({
                "type": "step",
                "timestamp": step.created_at,
                "obj": step,
            })
        for tc in run.tool_calls.all():
            children.append({
                "type": "tool_call",
                "timestamp": tc.created_at,
                "obj": tc,
            })
        for dec in run.decisions.all():
            children.append({
                "type": "decision",
                "timestamp": dec.created_at,
                "obj": dec,
            })
        for rec in run.recommendations.all():
            children.append({
                "type": "recommendation",
                "timestamp": rec.created_at,
                "obj": rec,
            })
        children.sort(key=lambda c: c["timestamp"])
        entries.append({
            "kind": "agent",
            "timestamp": run.started_at or run.created_at,
            "run": run,
            "agent_name": agent_name,
            "children": children,
            "step_count": sum(1 for c in children if c["type"] == "step"),
            "tool_count": sum(1 for c in children if c["type"] == "tool_call"),
        })

    # Interleave case-level decisions
    for d in decisions:
        entries.append({
            "kind": "decision",
            "timestamp": d.created_at,
            "decision": d,
        })

    entries.sort(key=lambda e: e["timestamp"])
    return entries


def _build_copilot_context(case, invoice, po, grns, stages, decisions,
                           exceptions, validation_issues, agent_runs, summary,
                           timeline=None):
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

    # Audit / timeline events
    ctx["audit_events"] = []
    ctx["system_actions"] = []
    for ev in (timeline or []):
        cat = ev.get("event_category", "")
        entry = {
            "category": cat,
            "type": ev.get("event_type", ""),
            "description": ev.get("description", ""),
            "actor": ev.get("actor", "system"),
            "timestamp": str(ev.get("timestamp", "")),
        }
        if ev.get("status_change"):
            entry["status_change"] = ev["status_change"]
        if cat == "audit":
            ctx["audit_events"].append(entry)
        elif cat in ("mode_resolution", "case", "stage"):
            ctx["system_actions"].append(entry)

    return ctx


@login_required
def case_inbox(request):
    """AP Cases inbox — main listing of all cases with filters."""
    vendor_id = request.GET.get("vendor", "")
    assigned_to_id = request.GET.get("assigned_to", "")
    qs = CaseSelectors.inbox(
        processing_path=request.GET.get("processing_path", ""),
        status=request.GET.get("status", ""),
        priority=request.GET.get("priority", ""),
        search=request.GET.get("q", ""),
        match_status=request.GET.get("match_status", ""),
        reconciliation_mode=request.GET.get("reconciliation_mode", ""),
        date_from=request.GET.get("date_from", ""),
        date_to=request.GET.get("date_to", ""),
        processing_type=request.GET.get("processing_type", ""),
        vendor_id=int(vendor_id) if vendor_id else None,
        assigned_to_id=int(assigned_to_id) if assigned_to_id and assigned_to_id != "unassigned" else None,
    )
    # Handle "unassigned" filter
    if assigned_to_id == "unassigned":
        qs = qs.filter(assigned_to__isnull=True)
    qs = CaseSelectors.scope_for_user(qs, request.user)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    stats = CaseSelectors.stats(user=request.user)

    # Build vendor choices scoped for user
    from apps.vendors.models import Vendor
    vendor_qs = Vendor.objects.filter(is_active=True).order_by("name")
    from apps.vendors.template_views import _scope_vendors_for_user
    vendor_qs = _scope_vendors_for_user(vendor_qs, request.user)
    vendor_choices = list(vendor_qs.values_list("id", "name"))

    # Resolve selected vendor name for filter chip display
    selected_vendor_name = ""
    if vendor_id:
        selected_vendor_name = next(
            (name for vid, name in vendor_choices if vid == int(vendor_id)), ""
        )

    # Reviewer choices for assignment filter (visible to users with cases.assign)
    reviewer_choices = []
    selected_assignee_name = ""
    if _has_permission_code(request.user, "cases.assign"):
        from apps.accounts.models import User
        reviewer_choices = list(
            User.objects.filter(role=UserRole.REVIEWER, is_active=True)
            .order_by("first_name", "last_name")
            .values_list("id", "first_name", "last_name")
        )
        if assigned_to_id and assigned_to_id != "unassigned":
            selected_assignee_name = next(
                (f"{fn} {ln}" for rid, fn, ln in reviewer_choices if rid == int(assigned_to_id)), ""
            )
        elif assigned_to_id == "unassigned":
            selected_assignee_name = "Unassigned"

    # ----- Pending invoices: approved / READY_FOR_RECON but no active case -----
    from apps.documents.models import Invoice
    from apps.core.enums import InvoiceStatus
    pending_invoices = (
        Invoice.objects.filter(
            status=InvoiceStatus.READY_FOR_RECON,
            is_active=True,
        )
        .exclude(
            pk__in=APCase.objects.filter(is_active=True).values_list("invoice_id", flat=True)
        )
        .select_related("document_upload__uploaded_by", "vendor")
        .order_by("-created_at")[:50]
    )

    return render(request, "cases/case_inbox.html", {
        "cases": page_obj,
        "page_obj": page_obj,
        "stats": stats,
        "pending_invoices": pending_invoices,
        "status_choices": CaseStatus.choices,
        "path_choices": ProcessingPath.choices,
        "priority_choices": CasePriority.choices,
        "match_status_choices": MatchStatus.choices,
        "reconciliation_mode_choices": ReconciliationMode.choices,
        "vendor_choices": vendor_choices,
        "selected_vendor_name": selected_vendor_name,
        "reviewer_choices": reviewer_choices,
        "selected_assignee_name": selected_assignee_name,
    })


@login_required
def case_console(request, pk):
    """Case console — redirect to new agent view."""
    return redirect("cases:case_agent_view", pk=pk)


@login_required
@permission_required_code("cases.edit")
def reprocess_case(request, pk):
    """Reprocess a case from a specific stage."""
    if request.method != "POST":
        return redirect("cases:case_console", pk=pk)

    scoped_qs = CaseSelectors.scope_for_user(APCase.objects.filter(is_active=True), request.user)
    case = get_object_or_404(scoped_qs, pk=pk)
    stage = request.POST.get("stage", "")

    redirect_view = "cases:case_agent_view" if request.POST.get("next") == "agent" else "cases:case_console"

    if not stage:
        messages.warning(request, "No stage specified for reprocessing.")
        return redirect(redirect_view, pk=pk)

    from apps.cases.tasks import reprocess_case_from_stage_task
    from apps.core.utils import dispatch_task

    try:
        dispatch_task(reprocess_case_from_stage_task, case_id=case.pk, stage=stage)
        messages.success(request, f"Case {case.case_number} reprocessed from {stage}.")
    except Exception as exc:
        messages.error(request, f"Reprocessing failed: {exc}")

    return redirect(redirect_view, pk=pk)


@login_required
@permission_required_code("cases.edit")
def create_case_for_invoice(request, invoice_pk):
    """Create an AP Case for an invoice that doesn't have one yet, then start processing."""
    if request.method != "POST":
        return redirect("documents:invoice_detail", pk=invoice_pk)

    from apps.documents.models import Invoice
    invoice = get_object_or_404(Invoice, pk=invoice_pk)

    # Guard: check if case already exists
    existing = APCase.objects.filter(invoice=invoice, is_active=True).first()
    if existing:
        messages.info(request, f"Case {existing.case_number} already exists for this invoice.")
        return redirect("cases:case_console", pk=existing.pk)

    from apps.cases.services.case_creation_service import CaseCreationService
    case = CaseCreationService.create_from_upload(invoice, uploaded_by=request.user)

    # Kick off processing
    from apps.cases.tasks import process_case_task
    from apps.core.utils import dispatch_task
    try:
        dispatch_task(process_case_task, case_id=case.pk)
        messages.success(request, f"Case {case.case_number} created and processing started.")
    except Exception as exc:
        messages.warning(request, f"Case {case.case_number} created but processing failed to start: {exc}")

    return redirect("cases:case_console", pk=case.pk)


@login_required
def case_agent_view(request, pk):
    """Agentic case view — ChatGPT-style conversation feed for case investigation."""
    base_qs = APCase.objects.select_related(
        "invoice", "invoice__vendor", "invoice__document_upload",
        "vendor", "purchase_order", "reconciliation_result",
        "assigned_to",
    ).prefetch_related(
        "stages", "artifacts", "decisions",
        "assignments", "comments", "activities",
    ).filter(is_active=True)
    base_qs = CaseSelectors.scope_for_user(base_qs, request.user)
    case = get_object_or_404(base_qs, pk=pk)

    invoice = case.invoice
    po = case.purchase_order
    stages = list(case.stages.order_by("-created_at"))
    decisions = list(case.decisions.order_by("-created_at"))
    comments = list(case.comments.select_related("author").order_by("-created_at"))

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

    # Non-PO validation issues
    validation_issues = []
    validation_artifact = case.artifacts.filter(artifact_type="VALIDATION_RESULT").order_by("-version", "-created_at").first()
    if validation_artifact and isinstance(validation_artifact.payload, dict):
        checks = validation_artifact.payload.get("checks", {})
        if isinstance(checks, dict):
            for check_name, check_data in checks.items():
                status = check_data.get("status", "")
                if status in ("FAIL", "WARNING"):
                    validation_issues.append({
                        "check_name": check_name.replace("_", " ").title(),
                        "status": status,
                        "message": check_data.get("message", ""),
                    })
        elif isinstance(checks, list):
            for check_data in checks:
                if isinstance(check_data, dict):
                    status = check_data.get("status", "")
                    if status in ("FAIL", "WARNING"):
                        validation_issues.append({
                            "check_name": check_data.get("check_name", check_data.get("name", "Unknown")).replace("_", " ").title(),
                            "status": status,
                            "message": check_data.get("message", ""),
                        })

    # Agent runs
    from apps.agents.models import AgentRun
    from django.db.models import Q

    agent_run_q = Q()
    if recon_result:
        agent_run_q |= Q(reconciliation_result=recon_result)
    # Include orphaned runs (e.g. PO_RETRIEVAL before reconciliation)
    agent_run_q |= Q(reconciliation_result__isnull=True, input_payload__invoice_id=invoice.pk)
    # Include runs linked via case stages
    stage_run_ids = list(
        case.stages.filter(performed_by_agent__isnull=False)
        .values_list("performed_by_agent_id", flat=True)
    )
    if stage_run_ids:
        agent_run_q |= Q(pk__in=stage_run_ids)

    agent_runs = list(
        AgentRun.objects.filter(agent_run_q)
        .select_related("agent_definition")
        .prefetch_related("steps", "tool_calls", "decisions", "recommendations")
        .distinct()
        .order_by("created_at")
    )

    # Summary
    summary = getattr(case, "summary", None)
    if not summary:
        built_summary = _build_fallback_summary(case, decisions, validation_issues)
        if built_summary:
            summary = built_summary

    # Timeline (from audit/governance service)
    from apps.auditlog.timeline_service import CaseTimelineService
    timeline = CaseTimelineService.get_case_timeline(invoice.pk)

    # Role-based trace visibility
    from apps.core.enums import UserRole
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    # Build unified agent timeline — agents + decisions interleaved chronologically
    agent_timeline = _build_agent_timeline(agent_runs, decisions, show_full_trace)

    # Copilot context
    copilot_context = _build_copilot_context(
        case, invoice, po, grns, stages, decisions,
        exceptions, validation_issues, agent_runs, summary, timeline,
    )

    # Get active review assignment for approve/reject actions
    review_assignment = None
    if recon_result:
        review_assignment = (
            recon_result.review_assignments
            .filter(status__in=["PENDING", "ASSIGNED", "IN_REVIEW"])
            .first()
        )
        # Auto-create assignment if case needs review but none exists
        if not review_assignment and case.status in ("READY_FOR_REVIEW", "IN_REVIEW"):
            from apps.reviews.models import ReviewAssignment
            review_assignment = ReviewAssignment.objects.create(
                reconciliation_result=recon_result,
                assigned_to=request.user,
                status="IN_REVIEW",
                priority=5,
            )
        # Fall back to the most recent completed/decided assignment for history
        if not review_assignment:
            review_assignment = (
                recon_result.review_assignments
                .order_by("-created_at")
                .first()
            )

    # Review comments and actions for the embedded review panel
    review_comments = []
    review_actions = []
    review_decision = None
    if review_assignment:
        review_comments = list(
            review_assignment.comments
            .select_related("author")
            .order_by("created_at")
        )
        review_actions = list(
            review_assignment.actions
            .select_related("performed_by")
            .order_by("-created_at")
        )
        try:
            review_decision = review_assignment.decision
        except Exception:
            review_decision = None

    # For Non-PO cases (or cases without review assignment), use case comments
    # Merge them into review_comments so the panel always has content
    case_comments = list(
        case.comments.select_related("author").order_by("created_at")
    )
    if not review_assignment:
        # No review assignment — show case comments in the review panel
        review_comments = case_comments

    # Determine if actions should be shown (for both PO and Non-PO paths)
    show_actions = case.status in (
        "READY_FOR_REVIEW", "IN_REVIEW", "REVIEW_COMPLETED",
        "READY_FOR_APPROVAL", "APPROVAL_IN_PROGRESS",
    )

    # Count open (unresolved) exceptions and failed validations
    open_exceptions_count = sum(1 for e in exceptions if not getattr(e, 'resolved', False))
    failed_validations_count = sum(1 for v in validation_issues if v.get('status') == 'FAIL')
    failed_stages_count = sum(1 for s in stages if s.stage_status == 'FAILED')
    has_open_issues = (open_exceptions_count + failed_validations_count + failed_stages_count) > 0

    # Reviewers list for assignment dropdown (only for users with cases.assign)
    reviewers = []
    if _has_permission_code(request.user, "cases.assign"):
        from apps.accounts.models import User
        reviewers = list(
            User.objects.filter(
                role=UserRole.REVIEWER, is_active=True,
            ).order_by("first_name", "last_name").values_list("id", "first_name", "last_name", "email")
        )

    return render(request, "cases/case_agent_view.html", {
        "case": case,
        "invoice": invoice,
        "po": po,
        "stages": stages,
        "decisions": decisions,
        "comments": comments,
        "grns": grns,
        "exceptions": exceptions,
        "validation_issues": validation_issues,
        "total_issues_count": len(exceptions) + len(validation_issues),
        "agent_runs": agent_runs,
        "agent_timeline": agent_timeline,
        "summary": summary,
        "timeline": timeline,
        "show_full_trace": show_full_trace,
        "review_assignment": review_assignment,
        "review_comments": review_comments,
        "review_actions": review_actions,
        "review_decision": review_decision,
        "show_actions": show_actions,
        "has_open_issues": has_open_issues,
        "open_exceptions_count": open_exceptions_count,
        "failed_validations_count": failed_validations_count,
        "failed_stages_count": failed_stages_count,
        "copilot_context_json": json.dumps(copilot_context, default=str),
        "reviewers": reviewers,
    })


@login_required
def case_decide(request, pk):
    """Handle approve/reject/reprocess directly on a case.

    Approve/reject require `reviews.decide`.
    Reprocess requires `cases.edit`.
    """
    if request.method != "POST":
        return redirect("cases:case_agent_view", pk=pk)

    decision = request.POST.get("decision", "").upper()

    # Permission gate: reprocess needs cases.edit, approve/reject needs reviews.decide
    if decision == "REPROCESSED":
        if not _has_permission_code(request.user, "cases.edit"):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
    else:
        if not _has_permission_code(request.user, "reviews.decide"):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

    scoped_qs = CaseSelectors.scope_for_user(APCase.objects.filter(is_active=True), request.user)
    case = get_object_or_404(scoped_qs, pk=pk)

    # Block approval if there are open exceptions or failed validations
    if decision == "APPROVED":
        open_exc = 0
        recon_res = case.reconciliation_result
        if recon_res:
            open_exc = recon_res.exceptions.filter(resolved=False).count()

        failed_val = 0
        val_artifact = case.artifacts.filter(
            artifact_type="VALIDATION_RESULT"
        ).order_by("-version", "-created_at").first()
        if val_artifact and isinstance(val_artifact.payload, dict):
            for cd in val_artifact.payload.get("checks", {}).values():
                if cd.get("status") == "FAIL":
                    failed_val += 1

        failed_stg = case.stages.filter(stage_status="FAILED").count()

        if open_exc + failed_val + failed_stg > 0:
            parts = []
            if open_exc:
                parts.append(f"{open_exc} unresolved exception(s)")
            if failed_val:
                parts.append(f"{failed_val} failed validation(s)")
            if failed_stg:
                parts.append(f"{failed_stg} failed stage(s)")
            messages.error(
                request,
                f"Cannot approve: {', '.join(parts)}. Resolve all issues before approving.",
            )
            return redirect("cases:case_agent_view", pk=pk)

    # If there's a review assignment, delegate to the review workflow
    assignment = None
    recon_result = case.reconciliation_result
    if recon_result:
        assignment = (
            recon_result.review_assignments
            .filter(status__in=["PENDING", "ASSIGNED", "IN_REVIEW"])
            .first()
        )
        if assignment:
            from apps.reviews.services import ReviewWorkflowService
            reason = request.POST.get("reason", "")
            if decision == "APPROVED":
                ReviewWorkflowService.approve(assignment, request.user, reason)
            elif decision == "REJECTED":
                ReviewWorkflowService.reject(assignment, request.user, reason)
            elif decision == "REPROCESSED":
                ReviewWorkflowService.request_reprocess(assignment, request.user, reason)

    # Handle reprocessing
    if decision == "REPROCESSED":
        from apps.cases.tasks import reprocess_case_from_stage_task
        from apps.core.utils import dispatch_task
        try:
            dispatch_task(reprocess_case_from_stage_task, case_id=case.pk, stage="INTAKE")
            messages.success(request, f"Case {case.case_number} submitted for reprocessing.")
        except Exception as exc:
            messages.error(request, f"Reprocessing failed: {exc}")

        # Audit: case reprocessed
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        AuditService.log_event(
            entity_type="APCase",
            entity_id=case.pk,
            event_type=AuditEventType.CASE_REPROCESSED,
            description=f"Case {case.case_number} submitted for reprocessing by {request.user}",
            user=request.user,
            case_id=case.pk,
            invoice_id=case.invoice_id,
            metadata={
                "reason": request.POST.get("reason", "")[:300],
                "review_assignment_id": assignment.pk if assignment else None,
            },
        )

        return redirect("cases:case_agent_view", pk=pk)

    # Update case status
    status_map = {
        "APPROVED": CaseStatus.CLOSED,
        "REJECTED": CaseStatus.REJECTED,
    }
    new_status = status_map.get(decision)
    if new_status:
        old_status = case.status
        case.status = new_status
        case.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Case {case.case_number} marked as {case.get_status_display()}.")

        # Audit: case status change from decision
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        event_map = {
            CaseStatus.CLOSED: AuditEventType.CASE_CLOSED,
            CaseStatus.REJECTED: AuditEventType.CASE_REJECTED,
        }
        AuditService.log_event(
            entity_type="APCase",
            entity_id=case.pk,
            event_type=event_map.get(new_status, decision),
            description=f"Case {case.case_number} {old_status} -> {new_status} via case decision",
            user=request.user,
            case_id=case.pk,
            invoice_id=case.invoice_id,
            status_before=old_status,
            status_after=new_status,
            metadata={
                "decision": decision,
                "reason": request.POST.get("reason", "")[:300],
                "review_assignment_id": assignment.pk if assignment else None,
            },
        )
    else:
        messages.warning(request, f"Unknown decision: {decision}")

    return redirect("cases:case_agent_view", pk=pk)


@login_required
@permission_required_code("cases.add_comment")
def case_add_comment(request, pk):
    """Add a review comment from the agent view."""
    if request.method != "POST":
        return redirect("cases:case_agent_view", pk=pk)

    case = get_object_or_404(APCase, pk=pk, is_active=True)
    body = request.POST.get("body", "").strip()
    if not body:
        messages.warning(request, "Comment cannot be empty.")
        return redirect("cases:case_agent_view", pk=pk)

    # Find or create review assignment
    recon_result = case.reconciliation_result
    assignment = None
    if recon_result:
        assignment = (
            recon_result.review_assignments
            .filter(status__in=["PENDING", "ASSIGNED", "IN_REVIEW"])
            .first()
        )
        if not assignment:
            assignment = recon_result.review_assignments.order_by("-created_at").first()

    if assignment:
        from apps.reviews.services import ReviewWorkflowService
        ReviewWorkflowService.add_comment(assignment, request.user, body)
    else:
        # For Non-PO cases without review assignment, store as case comment
        from apps.cases.models import APCaseComment
        APCaseComment.objects.create(
            case=case,
            author=request.user,
            body=body,
        )

    messages.success(request, "Comment added.")

    # Audit: track comment
    from apps.auditlog.services import AuditService
    from apps.core.enums import AuditEventType
    AuditService.log_event(
        entity_type="APCase",
        entity_id=case.pk,
        event_type=AuditEventType.COMMENT_ADDED,
        description=f"Comment added on case {case.case_number} by {request.user.get_full_name()}",
        user=request.user,
        case_id=case.pk,
        invoice_id=case.invoice_id,
        metadata={
            "case_number": case.case_number,
            "comment_preview": body[:100],
            "via_review_assignment": assignment.pk if assignment else None,
        },
    )

    return redirect("cases:case_agent_view", pk=pk)


@login_required
@permission_required_code("cases.assign")
def case_assign(request, pk):
    """Assign or unassign a case to a reviewer."""
    if request.method != "POST":
        return redirect("cases:case_agent_view", pk=pk)

    case = get_object_or_404(APCase, pk=pk, is_active=True)
    assignee_id = request.POST.get("assigned_to", "").strip()
    previous_assignee = case.assigned_to

    if assignee_id:
        from apps.accounts.models import User
        assignee = get_object_or_404(User, pk=int(assignee_id), is_active=True)
        case.assigned_to = assignee
        case.save(update_fields=["assigned_to", "updated_at"])
        messages.success(request, f"Case {case.case_number} assigned to {assignee.get_full_name()}.")
    else:
        case.assigned_to = None
        case.save(update_fields=["assigned_to", "updated_at"])
        messages.success(request, f"Case {case.case_number} unassigned.")

    # Audit: track assignment change
    from apps.auditlog.services import AuditService
    from apps.core.enums import AuditEventType
    prev_name = previous_assignee.get_full_name() if previous_assignee else "Unassigned"
    new_name = case.assigned_to.get_full_name() if case.assigned_to else "Unassigned"
    AuditService.log_event(
        entity_type="APCase",
        entity_id=case.pk,
        event_type=AuditEventType.CASE_ASSIGNED,
        description=f"Case {case.case_number} assignment changed: {prev_name} -> {new_name}",
        user=request.user,
        case_id=case.pk,
        invoice_id=case.invoice_id,
        status_before=prev_name,
        status_after=new_name,
        metadata={
            "previous_assignee_id": previous_assignee.pk if previous_assignee else None,
            "new_assignee_id": case.assigned_to_id,
            "case_number": case.case_number,
        },
    )

    return redirect("cases:case_agent_view", pk=pk)
