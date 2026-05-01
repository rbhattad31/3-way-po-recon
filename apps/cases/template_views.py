"""AP Cases template views (server-side rendered)."""

import json
import logging

from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from apps.cases.models import APCase
from apps.cases.selectors.case_selectors import CaseSelectors
from apps.core.enums import CasePriority, CaseStatus, MatchStatus, ProcessingPath, ReconciliationMode, UserRole
from apps.core.permissions import permission_required_code, _has_permission_code
from apps.core.tenant_utils import TenantQuerysetMixin, require_tenant
from apps.core.utils import build_case_remarks

logger = logging.getLogger(__name__)


def _scoped_case_queryset(request):
    tenant = require_tenant(request)
    qs = CaseSelectors.scope_for_user(APCase.objects.filter(is_active=True), request.user)
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    return qs


def _build_fallback_summary(case, decisions, validation_issues):
    """Build a lightweight summary dict when APCaseSummary doesn't exist."""
    parts = []
    invoice = case.invoice

    # Basic case info
    vendor_name = (
        invoice.vendor.name if invoice.vendor
        else invoice.raw_vendor_name or "unknown vendor"
    )
    parts.append(
        f"Case {case.case_number} for invoice {invoice.invoice_number or 'N/A'}"
        f" from {vendor_name}."
    )
    parts.append(f"Processing path: {case.get_processing_path_display()}.")

    # Path decision
    path_decision = next(
        (d for d in decisions if d.decision_type == "PATH_SELECTED"), None
    )
    if path_decision and path_decision.rationale:
        parts.append(f"Path rationale: {path_decision.rationale}.")

    # Validation outcome -- list specific issues, not just counts
    if validation_issues:
        fails = [i for i in validation_issues if i["status"] == "FAIL"]
        warns = [i for i in validation_issues if i["status"] == "WARNING"]
        if fails:
            fail_names = [f['check_name'] for f in fails]
            fail_msgs = [f['message'] for f in fails if f.get('message')]
            parts.append(f"Failed checks: {', '.join(fail_names)}.")
            for msg in fail_msgs[:3]:
                parts.append(f"  - {msg}")
        if warns:
            warn_msgs = [w['message'] for w in warns if w.get('message')]
            for msg in warn_msgs[:3]:
                parts.append(f"  - {msg}")

    # Reconciliation exceptions (specific details)
    recon_result = case.reconciliation_result
    if recon_result:
        exceptions = list(
            recon_result.exceptions.filter(resolved=False)
            .values("exception_type", "severity", "message", "details")
            .order_by("-severity")[:5]
        )
        if exceptions:
            parts.append(f"Reconciliation: {len(exceptions)} unresolved exception(s):")
            for exc in exceptions:
                msg = exc.get("message", exc["exception_type"])
                sev = exc.get("severity", "MEDIUM")
                parts.append(f"  - [{sev}] {msg}")

    # Match decision
    match_decision = next(
        (d for d in decisions if d.decision_type == "MATCH_DETERMINED"), None
    )
    if match_decision and match_decision.rationale:
        parts.append(match_decision.rationale)

    # Recommendation with specific detail
    recommendation = None
    if case.status == "FAILED":
        recommendation = "Case processing failed. Review exceptions and consider reprocessing."
    elif validation_issues:
        fails = [i for i in validation_issues if i["status"] == "FAIL"]
        if fails:
            recommendation = "Resolve: " + "; ".join(
                f"{i['check_name']} -- {i['message']}" for i in fails[:3]
            ) + "."

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
    tenant = require_tenant(request)
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
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    qs = CaseSelectors.scope_for_user(qs, request.user)

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Add a human-readable remark for why a case is closed/open.
    from django.db.models import Count
    result_ids = [
        c.reconciliation_result_id
        for c in page_obj
        if getattr(c, "reconciliation_result_id", None)
    ]
    exception_counts_by_result = {}
    review_decision_by_result = {}
    if result_ids:
        from apps.reconciliation.models import ReconciliationException
        from apps.cases.models import ReviewDecision
        for row in (
            ReconciliationException.objects
            .filter(result_id__in=result_ids, resolved=False)
            .values("result_id")
            .annotate(count=Count("id"))
        ):
            exception_counts_by_result[row["result_id"]] = row["count"]

        # Keep the latest review decision per reconciliation result.
        for decision in (
            ReviewDecision.objects
            .filter(assignment__reconciliation_result_id__in=result_ids)
            .select_related("assignment")
            .order_by("assignment__reconciliation_result_id", "-decided_at")
        ):
            result_id = decision.assignment.reconciliation_result_id
            if result_id and result_id not in review_decision_by_result:
                review_decision_by_result[result_id] = decision.decision

    for c in page_obj:
        unresolved_exceptions = exception_counts_by_result.get(
            getattr(c, "reconciliation_result_id", None), 0,
        )
        c.closure_remark = build_case_remarks(
            invoice_status=(getattr(getattr(c, "invoice", None), "status", "") or ""),
            case_status=(getattr(c, "status", "") or ""),
            match_status=(getattr(getattr(c, "reconciliation_result", None), "match_status", "") or ""),
            unresolved_exceptions=unresolved_exceptions,
            has_case=True,
            policy_applied=(getattr(getattr(c, "reconciliation_result", None), "policy_applied", "") or ""),
            review_decision=review_decision_by_result.get(
                getattr(c, "reconciliation_result_id", None),
                "",
            ),
        )

    # KPI cards should reflect the same scoped + filtered dataset as the table.
    stats = CaseSelectors.stats_from_queryset(qs)

    # Build vendor choices scoped for user
    from apps.vendors.models import Vendor
    vendor_qs = Vendor.objects.filter(is_active=True).order_by("name")
    if tenant is not None:
        vendor_qs = vendor_qs.filter(tenant=tenant)
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

    # ----- In-progress cases (currently being processed in background) -----
    in_progress_statuses = [
        s.value for s in CaseStatus if "IN_PROGRESS" in s.value
    ] + [CaseStatus.NEW]
    in_progress_qs = APCase.objects.filter(
        status__in=in_progress_statuses,
        is_active=True,
    ).select_related("invoice", "vendor", "assigned_to")
    if tenant is not None:
        in_progress_qs = in_progress_qs.filter(tenant=tenant)
    in_progress_cases = (
        CaseSelectors.scope_for_user(
            in_progress_qs.order_by("-updated_at"),
            request.user,
        )[:20]
    )

    # ----- Pending invoices: approved / READY_FOR_RECON but no active case -----
    from apps.documents.models import Invoice
    from apps.core.enums import InvoiceStatus
    pending_invoices_qs = (
        Invoice.objects.filter(
            status=InvoiceStatus.READY_FOR_RECON,
        )
        .exclude(
            pk__in=APCase.objects.filter(is_active=True).values_list("invoice_id", flat=True)
        )
        .select_related("document_upload__uploaded_by", "vendor")
        .order_by("-created_at")
    )
    if tenant is not None:
        pending_invoices_qs = pending_invoices_qs.filter(tenant=tenant)
    from apps.documents.template_views import _scope_invoices_for_user
    pending_invoices_qs = _scope_invoices_for_user(pending_invoices_qs, request.user)
    pending_invoices = pending_invoices_qs[:50]

    return render(request, "cases/case_inbox.html", {
        "cases": page_obj,
        "page_obj": page_obj,
        "stats": stats,
        "pending_invoices": pending_invoices,
        "in_progress_cases": in_progress_cases,
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

    # Assign the case to the reviewer who triggered reprocessing
    if not case.assigned_to:
        case.assigned_to = request.user
        case.save(update_fields=["assigned_to", "updated_at"])

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
    case.assigned_to = request.user
    case.save(update_fields=["assigned_to", "updated_at"])

    # Kick off processing
    from apps.cases.tasks import process_case_task
    from apps.core.utils import dispatch_task
    try:
        dispatch_task(process_case_task, getattr(case, 'tenant_id', None), case.pk)
        messages.success(request, f"Case {case.case_number} created and processing started.")
    except Exception as exc:
        messages.warning(request, f"Case {case.case_number} created but processing failed to start: {exc}")

    return redirect("cases:case_console", pk=case.pk)


@login_required
def case_agent_view(request, pk):
    """Agentic case view — ChatGPT-style conversation feed for case investigation."""
    tenant = require_tenant(request)
    base_qs = APCase.objects.select_related(
        "invoice", "invoice__vendor", "invoice__document_upload",
        "vendor", "purchase_order", "reconciliation_result",
        "assigned_to",
    ).prefetch_related(
        "stages", "artifacts", "decisions",
        "assignments", "comments", "activities",
    ).filter(is_active=True)
    if tenant is not None:
        base_qs = base_qs.filter(tenant=tenant)
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

    # Non-PO validation issues — skip when reconciliation exceptions already
    # exist for this case because _create_non_po_recon_result() converts
    # failed validation checks into ReconciliationException records.
    # Showing both would double-count.
    validation_issues = []
    validation_artifact = (
        case.artifacts.filter(artifact_type="VALIDATION_RESULT")
        .order_by("-version", "-created_at")
        .first()
        if not exceptions else None
    )
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
    agent_run_q = Q()
    if recon_result:
        agent_run_q |= Q(reconciliation_result=recon_result)
    # Include extraction agent runs linked via document_upload
    if invoice.document_upload_id:
        agent_run_q |= Q(document_upload_id=invoice.document_upload_id)
    # Include orphaned runs (e.g. PO_RETRIEVAL before reconciliation)
    agent_run_q |= Q(reconciliation_result__isnull=True, input_payload__invoice_id=invoice.pk)
    # Include runs linked via case stages
    stage_run_ids = list(
        case.stages.filter(performed_by_agent__isnull=False)
        .values_list("performed_by_agent_id", flat=True)
    )
    if stage_run_ids:
        agent_run_q |= Q(pk__in=stage_run_ids)

    agent_run_qs = AgentRun.objects.filter(agent_run_q)
    if tenant is not None:
        agent_run_qs = agent_run_qs.filter(Q(tenant=tenant) | Q(tenant__isnull=True))
    agent_runs = list(
        agent_run_qs
        .select_related(
            "agent_definition",
            "parent_run",
            "parent_run__agent_definition",
            "reconciliation_result",
            "reconciliation_result__run",
        )
        .prefetch_related(
            "steps", "tool_calls", "decisions", "recommendations",
            "messages",
        )
        .distinct()
        .order_by("created_at")
    )

    # Second pass: include child runs delegated by any supervisor/parent run
    # found above (e.g. PORetrievalAgent, GRNRetrievalAgent spawned by
    # SupervisorAgent). Child runs only carry parent_run_id, not the case-
    # linking fields, so they are missed by the initial Q filter.
    _found_pks = {r.pk for r in agent_runs}
    if _found_pks:
        child_q = Q(parent_run_id__in=_found_pks)
        if tenant is not None:
            child_q &= Q(tenant=tenant) | Q(tenant__isnull=True)
        child_runs = list(
            AgentRun.objects.filter(child_q)
            .exclude(pk__in=_found_pks)
            .select_related(
                "agent_definition",
                "parent_run",
                "parent_run__agent_definition",
                "reconciliation_result",
                "reconciliation_result__run",
            )
            .prefetch_related(
                "steps", "tool_calls", "decisions", "recommendations",
                "messages",
            )
        )
        if child_runs:
            agent_runs = sorted(
                agent_runs + child_runs,
                key=lambda r: r.created_at,
            )

    # Hide deterministic system agents from UI tabs that are intended to
    # show only LLM-based activity and costs.
    llm_agent_runs = [
        run for run in agent_runs
        if "SYSTEM_" not in (getattr(run, "agent_type", "") or "")
    ]

    # ── Attach eval field outcomes per agent run ──
    _agent_run_ids = [r.pk for r in agent_runs]
    _eval_field_map: dict = {}  # agent_run_pk -> list[EvalFieldOutcome]
    if _agent_run_ids:
        try:
            from apps.core_eval.models import EvalRun, EvalFieldOutcome
            _eval_runs = list(
                EvalRun.objects.filter(
                    app_module="agents",
                    entity_type="AgentRun",
                    entity_id__in=[str(pk) for pk in _agent_run_ids],
                ).prefetch_related("field_outcomes")
            )
            for er in _eval_runs:
                _eval_field_map[int(er.entity_id)] = list(er.field_outcomes.all())
        except Exception:
            pass  # fail-silent

    for run in agent_runs:
        run.eval_field_outcomes = _eval_field_map.get(run.pk, [])

    # Summary
    summary = getattr(case, "summary", None)
    if not summary:
        built_summary = _build_fallback_summary(case, decisions, validation_issues)
        if built_summary:
            summary = built_summary

    # Timeline (from audit/governance service)
    from apps.auditlog.timeline_service import CaseTimelineService
    timeline = CaseTimelineService.get_case_timeline(invoice.pk, tenant=getattr(request, 'tenant', None))

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

    # Reconciliation match status
    recon_match_status = None
    recon_mode = None
    if recon_result:
        recon_match_status = recon_result.match_status
        recon_mode = getattr(recon_result, "reconciliation_mode", None)

    review_decision_value = ""
    if review_decision is not None:
        review_decision_value = getattr(review_decision, "decision", "") or ""

    case_closure_remark = build_case_remarks(
        invoice_status=(getattr(invoice, "status", "") if invoice else ""),
        case_status=(getattr(case, "status", "") or ""),
        match_status=(recon_match_status or ""),
        unresolved_exceptions=open_exceptions_count,
        has_case=True,
        policy_applied=(getattr(recon_result, "policy_applied", "") if recon_result else ""),
        review_decision=review_decision_value,
    )

    # Reviewers list for assignment dropdown (only for users with cases.assign)
    reviewers = []
    if _has_permission_code(request.user, "cases.assign"):
        from apps.accounts.models import User
        reviewers = list(
            User.objects.filter(
                role=UserRole.REVIEWER, is_active=True,
            ).order_by("first_name", "last_name").values_list("id", "first_name", "last_name", "email")
        )

    # ── Cost & Token data (aggregated across all agent runs for this case) ──
    cost_token_data = None
    cost_run_history = []
    cost_reconciliation_history = []
    if llm_agent_runs:
        try:
            from decimal import Decimal
            from django.db.models import Sum

            run_pks = [r.pk for r in llm_agent_runs]
            totals = AgentRun.objects.filter(pk__in=run_pks).aggregate(
                prompt_tk=Sum("prompt_tokens"),
                completion_tk=Sum("completion_tokens"),
                total_tk=Sum("total_tokens"),
                dur_ms=Sum("duration_ms"),
            )
            prompt_tk = totals["prompt_tk"] or 0
            completion_tk = totals["completion_tk"] or 0
            total_tk = totals["total_tk"] or 0
            total_duration_ms = totals["dur_ms"] or 0

            if total_tk > 0 or total_duration_ms > 0:
                llm_cost = Decimal(str(prompt_tk * 5 / 1_000_000 + completion_tk * 15 / 1_000_000))
                total_cost = llm_cost.quantize(Decimal("0.000001"))
                latest_run = llm_agent_runs[-1]  # ordered by created_at ASC
                cost_token_data = {
                    "prompt_tokens": prompt_tk,
                    "completion_tokens": completion_tk,
                    "total_tokens": total_tk,
                    "total_duration_ms": total_duration_ms,
                    "llm_cost": llm_cost.quantize(Decimal("0.000001")),
                    "cost_estimate": total_cost,
                    "llm_model": getattr(latest_run, "llm_model_used", None) or "gpt-4o",
                    "run_count": len(llm_agent_runs),
                }

                for _cr in llm_agent_runs:
                    _cr_prompt = _cr.prompt_tokens or 0
                    _cr_compl = _cr.completion_tokens or 0
                    _cr_total = _cr.total_tokens or 0
                    _cr_llm = Decimal(str(_cr_prompt * 5 / 1_000_000 + _cr_compl * 15 / 1_000_000))
                    cost_run_history.append({
                        "run_id": _cr.pk,
                        "agent_type": _cr.get_agent_type_display() if hasattr(_cr, "get_agent_type_display") else str(_cr.agent_type),
                        "status": _cr.status,
                        "llm_model": getattr(_cr, "llm_model_used", None) or "gpt-4o",
                        "prompt_tokens": _cr_prompt,
                        "completion_tokens": _cr_compl,
                        "total_tokens": _cr_total,
                        "llm_cost": _cr_llm.quantize(Decimal("0.000001")),
                        "total_cost": _cr_llm.quantize(Decimal("0.000001")),
                        "duration_ms": getattr(_cr, "duration_ms", None),
                        "started_at": _cr.started_at if hasattr(_cr, "started_at") else _cr.created_at,
                        "confidence": _cr.confidence,
                    })

                    _rr = getattr(_cr, "reconciliation_result", None)
                    _rr_key = _rr.pk if _rr else 0
                    _row = None
                    for _existing in cost_reconciliation_history:
                        if _existing["reconciliation_result_id"] == _rr_key:
                            _row = _existing
                            break
                    if _row is None:
                        _row = {
                            "reconciliation_result_id": _rr.pk if _rr else None,
                            "reconciliation_run_id": (_rr.run_id if _rr and getattr(_rr, "run_id", None) else None),
                            "match_status": (_rr.match_status if _rr else "ORPHAN"),
                            "run_count": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                            "llm_cost": Decimal("0"),
                            "latest_started_at": _cr.started_at if hasattr(_cr, "started_at") else _cr.created_at,
                        }
                        cost_reconciliation_history.append(_row)

                    _row["run_count"] += 1
                    _row["prompt_tokens"] += _cr_prompt
                    _row["completion_tokens"] += _cr_compl
                    _row["total_tokens"] += _cr_total
                    _row["llm_cost"] += _cr_llm
                    _row["latest_started_at"] = max(
                        _row["latest_started_at"],
                        _cr.started_at if hasattr(_cr, "started_at") else _cr.created_at,
                    )

                for _row in cost_reconciliation_history:
                    _row["llm_cost"] = _row["llm_cost"].quantize(Decimal("0.000001"))

                cost_reconciliation_history = sorted(
                    cost_reconciliation_history,
                    key=lambda x: x["latest_started_at"] or timezone.now(),
                    reverse=True,
                )
        except Exception:
            logger.debug("Cost run history build failed for case context (non-fatal)", exc_info=True)

    # Invoice deep-dive panel (embedded extraction console subset)
    extraction_result = None
    extraction_payload = {}
    extraction = {}
    ext = None
    header_fields = {}
    tax_fields = {}
    line_items = []
    line_items_raw = []
    line_items_totals = {"quantity": 0, "tax_amount": 0, "total": 0}
    invoice_tax_breakdown = {}
    validation_field_issues = {}
    corrections = []
    correction_count = 0
    parties = {}
    enrichment = None
    qr_date_match = None
    qr_amount_match = None
    qr_data = None
    qr_decision_codes = []
    raw_json_pretty = ""
    if invoice and getattr(invoice, "document_upload_id", None):
        try:
            from apps.extraction.models import ExtractionResult

            extraction_result = (
                ExtractionResult.objects
                .select_related("extraction_run")
                .filter(document_upload_id=invoice.document_upload_id)
                .order_by("-created_at")
                .first()
            )
            if extraction_result:
                ext = extraction_result
                extraction_payload = extraction_result.raw_response or {}
                if not extraction_payload and extraction_result.extraction_run:
                    extraction_payload = extraction_result.extraction_run.extracted_data_json or {}

                # Extraction metadata expected by extraction console partials.
                _run = extraction_result.extraction_run
                extraction = {
                    "resolved_jurisdiction": {
                        "country_code": getattr(_run, "country_code", ""),
                        "regime_code": getattr(_run, "regime_code", ""),
                    } if _run and (getattr(_run, "country_code", "") or getattr(_run, "regime_code", "")) else None,
                    "jurisdiction_source": getattr(_run, "jurisdiction_source", "") if _run else "",
                    "jurisdiction_confidence": getattr(_run, "jurisdiction_confidence", None) if _run else None,
                    "jurisdiction_warning": getattr(_run, "jurisdiction_warning", "") if _run else "",
                }

                # Header/tax/line context aligned to extraction console rendering.
                _header_map = [
                    ("invoice_number", "Invoice Number", True),
                    ("po_number", "PO Number", False),
                    ("invoice_date", "Invoice Date", True),
                    ("due_date", "Due Date", False),
                    ("vendor_tax_id", "Vendor Tax ID (GSTIN/VAT)", False),
                    ("buyer_name", "Buyer / Bill To", False),
                    ("currency", "Currency", True),
                    ("subtotal", "Subtotal", False),
                    ("tax_percentage", "Tax Rate %", False),
                    ("tax_amount", "Tax Amount", False),
                    ("total_amount", "Total Amount", True),
                ]
                _inv_conf = invoice.extraction_confidence if invoice.extraction_confidence is not None else 0.0
                for _attr, _display, _mandatory in _header_map:
                    _val = getattr(invoice, _attr, None)
                    _raw_attr = f"raw_{_attr}" if hasattr(invoice, f"raw_{_attr}") else None
                    _raw_val = getattr(invoice, _raw_attr) if _raw_attr else None
                    _has_value = _val is not None and str(_val).strip() != ""
                    header_fields[_attr] = {
                        "display_name": _display,
                        "value": str(_val) if _val is not None else "",
                        "raw_value": str(_raw_val) if _raw_val else None,
                        "confidence": _inv_conf if _has_value else None,
                        "method": "LLM" if _has_value else None,
                        "is_mandatory": _mandatory,
                        "evidence": _has_value,
                    }

                for _attr, _display in [("tax_percentage", "Tax Rate %"), ("tax_amount", "Tax Amount"), ("currency", "Currency")]:
                    _val = getattr(invoice, _attr, None)
                    _has_value = _val is not None and str(_val).strip() != ""
                    tax_fields[f"tax_{_attr}"] = {
                        "display_name": _display,
                        "value": str(_val) if _val is not None else "",
                        "confidence": _inv_conf if _has_value else None,
                        "method": "LLM" if _has_value else None,
                        "is_mandatory": False,
                        "evidence": _has_value,
                    }

                _invoice_lines = list(invoice.line_items.order_by("line_number"))
                line_items_raw = _invoice_lines
                for _li in _invoice_lines:
                    line_items.append({
                        "description": _li.description,
                        "quantity": _li.quantity,
                        "unit_price": _li.unit_price,
                        "tax_percentage": _li.tax_percentage,
                        "tax_amount": _li.tax_amount,
                        "total": _li.line_amount,
                        "confidence": _inv_conf,
                        "fields": {
                            "Line Number": _li.line_number,
                            "Item Category": _li.item_category or "",
                        },
                    })

                from decimal import Decimal as _Dec, InvalidOperation as _InvalidOperation

                def _to_dec(_v):
                    if _v is None:
                        return _Dec(0)
                    try:
                        return _Dec(str(_v))
                    except (_InvalidOperation, ValueError):
                        return _Dec(0)

                line_items_totals = {
                    "quantity": sum(_to_dec(_li.get("quantity")) for _li in line_items),
                    "tax_amount": sum(_to_dec(_li.get("tax_amount")) for _li in line_items),
                    "total": sum(_to_dec(_li.get("total")) for _li in line_items),
                }

                if invoice.tax_breakdown and isinstance(invoice.tax_breakdown, dict):
                    invoice_tax_breakdown = invoice.tax_breakdown

                # Corrections tab data
                from apps.extraction.models import ExtractionApproval, ExtractionFieldCorrection

                _approval = ExtractionApproval.objects.filter(invoice=invoice).first()
                if _approval:
                    _field_corrections = (
                        ExtractionFieldCorrection.objects
                        .filter(approval=_approval)
                        .select_related("corrected_by")
                        .order_by("-created_at")
                    )
                    for _fc in _field_corrections:
                        corrections.append({
                            "field_code": _fc.field_name,
                            "original_value": _fc.original_value,
                            "corrected_value": _fc.corrected_value,
                            "correction_reason": f"{_fc.entity_type} correction",
                            "corrected_by": _fc.corrected_by,
                            "created_at": _fc.created_at,
                        })
                    correction_count = len(corrections)

                if isinstance(extraction_payload, dict):
                    _qr_raw = extraction_payload.get("_qr")
                    if not isinstance(_qr_raw, dict):
                        _meta = extraction_payload.get("meta")
                        if isinstance(_meta, dict):
                            _qr_raw = _meta.get("qr_data")
                            qr_decision_codes = _meta.get("decision_codes") or []

                    if isinstance(_qr_raw, dict) and _qr_raw:
                        qr_data = _qr_raw
                    if not qr_decision_codes:
                        qr_decision_codes = extraction_payload.get("_decision_codes") or []

                if qr_data and invoice and invoice.invoice_date:
                    _qr_date_str = qr_data.get("doc_date", "")
                    if _qr_date_str:
                        import datetime as _dt
                        _parsed_qr_date = None
                        for _fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
                            try:
                                _parsed_qr_date = _dt.datetime.strptime(_qr_date_str, _fmt).date()
                                break
                            except ValueError:
                                continue
                        if _parsed_qr_date is not None:
                            qr_date_match = (_parsed_qr_date == invoice.invoice_date)

                if qr_data and invoice and invoice.total_amount is not None:
                    _qr_total = qr_data.get("total_value")
                    if _qr_total is not None:
                        try:
                            from decimal import Decimal as _QDec
                            qr_amount_match = (abs(_QDec(str(_qr_total)) - _QDec(str(invoice.total_amount))) < _QDec("0.01"))
                        except Exception:
                            pass

                raw_json_pretty = json.dumps(extraction_payload, indent=2, default=str) if extraction_payload else ""
        except Exception:
            extraction_result = None
            extraction_payload = {}
            extraction = {}
            ext = None
            header_fields = {}
            tax_fields = {}
            line_items = []
            line_items_raw = []
            line_items_totals = {"quantity": 0, "tax_amount": 0, "total": 0}
            invoice_tax_breakdown = {}
            validation_field_issues = {}
            corrections = []
            correction_count = 0
            parties = {}
            enrichment = None
            qr_date_match = None
            qr_amount_match = None
            qr_data = None
            qr_decision_codes = []
            raw_json_pretty = ""

    # ── Line-level matching details (reconciliation result lines) ──
    line_matching_details = []
    line_matching_summary = {
        "total_lines": 0,
        "matched_lines": 0,
        "partial_lines": 0,
        "unmatched_lines": 0,
        "ai_used_lines": 0,
        "rule_based_lines": 0,
    }
    matching_method_overview = []
    deterministic_agent_steps = []
    if recon_result:
        try:
            from apps.reconciliation.models import ReconciliationResultLine
            line_results = list(
                ReconciliationResultLine.objects.filter(result=recon_result)
                .select_related("invoice_line", "po_line")
                .order_by("id")
            )
            for line_result in line_results:
                method = (line_result.match_method or "").upper()
                match_status = (line_result.match_status or "").upper()

                line_matching_summary["total_lines"] += 1
                if match_status == "MATCHED":
                    line_matching_summary["matched_lines"] += 1
                elif match_status == "PARTIAL_MATCH":
                    line_matching_summary["partial_lines"] += 1
                else:
                    line_matching_summary["unmatched_lines"] += 1

                if method == "LLM_FALLBACK":
                    line_matching_summary["ai_used_lines"] += 1
                    method_label = "AI Assisted"
                else:
                    line_matching_summary["rule_based_lines"] += 1
                    method_label = "Rule Based"

                reason_bits = []
                if (line_result.description_match_score or 0) >= 0.75:
                    reason_bits.append("description")
                if (line_result.quantity_match_score or 0) >= 0.75:
                    reason_bits.append("quantity")
                if (line_result.price_match_score or 0) >= 0.75:
                    reason_bits.append("price")
                if (line_result.amount_match_score or 0) >= 0.75:
                    reason_bits.append("amount")

                if reason_bits:
                    basis = ", ".join(reason_bits)
                else:
                    basis = "overall closest commercial fit"

                if method == "LLM_FALLBACK":
                    business_reason = f"AI compared candidate PO lines and selected the best fit using {basis}."
                elif match_status == "MATCHED":
                    business_reason = f"Rule-based matching found a strong fit on {basis}."
                elif match_status == "PARTIAL_MATCH":
                    business_reason = f"Potential match found on {basis}, but differences remain for review."
                else:
                    business_reason = "No reliable PO line match was found from available evidence."

                line_matching_details.append({
                    "id": line_result.id,
                    "match_status": line_result.match_status,
                    "match_method": line_result.match_method,
                    "match_method_label": method_label,
                    "match_confidence": float(line_result.match_confidence or 0) * 100,  # as percentage
                    "business_reason": business_reason,
                    "invoice_line_number": line_result.invoice_line.line_number if line_result.invoice_line else None,
                    "invoice_description": line_result.invoice_line.description if line_result.invoice_line else None,
                    "po_line_number": line_result.po_line.line_number if line_result.po_line else None,
                    "po_description": line_result.po_line.description if line_result.po_line else None,
                })
        except Exception as e:
            logger.debug(f"Line matching details build failed (non-fatal): {e}")
            line_matching_details = []

    # ── Mode decision analysis (policy + precedence + evaluated attributes) ──
    mode_decision_analysis = None
    if recon_result:
        policy_code = (getattr(recon_result, "policy_applied", "") or "").strip()
        policy_obj = None
        if policy_code:
            try:
                from apps.reconciliation.models import ReconciliationPolicy

                policy_obj = (
                    ReconciliationPolicy.objects
                    .filter(policy_code__iexact=policy_code, is_active=True, tenant=tenant)
                    .order_by("priority", "policy_code")
                    .first()
                )
                if policy_obj is None:
                    policy_obj = (
                        ReconciliationPolicy.objects
                        .filter(policy_code__iexact=policy_code, is_active=True, tenant__isnull=True)
                        .order_by("priority", "policy_code")
                        .first()
                    )
            except Exception:
                logger.debug("Policy lookup failed for case %s", case.pk, exc_info=True)

        item_categories = []
        try:
            item_categories = sorted({
                (li.item_category or "").strip()
                for li in invoice.line_items.all()
                if (li.item_category or "").strip()
            })
        except Exception:
            item_categories = []

        service_flag = None
        stock_flag = None
        try:
            if po is not None:
                _po_lines = list(po.line_items.all())
                if _po_lines:
                    service_flag = any(getattr(li, "is_service_item", False) for li in _po_lines)
                    stock_flag = any(getattr(li, "is_stock_item", False) for li in _po_lines)
        except Exception:
            service_flag = None
            stock_flag = None

        def _display(v, fallback="Any"):
            if v is None:
                return fallback
            s = str(v).strip()
            return s if s else fallback

        mode_decision_analysis = {
            "policy_code": policy_code or "Tenant Default",
            "policy_name": _display(getattr(policy_obj, "policy_name", ""), "Applied tenant-level default mode" if not policy_code else "--"),
            "priority": getattr(policy_obj, "priority", None),
            "resolved_mode": _display(getattr(recon_result, "reconciliation_mode", ""), "--"),
            "resolution_reason": _display(getattr(recon_result, "mode_resolution_reason", ""), "Mode set at tenant level -- no per-invoice policy rules active."),
            "precedence_note": "Mode is configured at tenant level. All invoices use the same mode unless a custom policy is added.",
            "match_inputs": [
                {"label": "Vendor", "value": _display(invoice.vendor.name if invoice and invoice.vendor else getattr(invoice, "raw_vendor_name", ""), "--")},
                {"label": "Item Category", "value": ", ".join(item_categories) if item_categories else "--"},
                {"label": "Location", "value": _display(getattr(invoice, "location_code", ""), "--")},
                {"label": "Business Unit", "value": _display(getattr(invoice, "business_unit", ""), "--")},
                {"label": "Service Invoice", "value": "Yes" if service_flag is True else "No" if service_flag is False else "--"},
                {"label": "Stock Invoice", "value": "Yes" if stock_flag is True else "No" if stock_flag is False else "--"},
            ],
            "policy_criteria": [
                {"label": "Vendor Rule", "value": _display(getattr(getattr(policy_obj, "vendor", None), "name", ""))},
                {"label": "Invoice Type Rule", "value": _display(getattr(policy_obj, "invoice_type", ""))},
                {"label": "Item Category Rule", "value": _display(getattr(policy_obj, "item_category", ""))},
                {"label": "Location Rule", "value": _display(getattr(policy_obj, "location_code", ""))},
                {"label": "Business Unit Rule", "value": _display(getattr(policy_obj, "business_unit", ""))},
                {"label": "Service Rule", "value": "Yes" if getattr(policy_obj, "is_service_invoice", None) is True else "No" if getattr(policy_obj, "is_service_invoice", None) is False else "Any"},
                {"label": "Stock Rule", "value": "Yes" if getattr(policy_obj, "is_stock_invoice", None) is True else "No" if getattr(policy_obj, "is_stock_invoice", None) is False else "Any"},
            ],
        }

    if recon_result:
        # Build an at-a-glance method summary so users can clearly see
        # whether each matching decision came from rules or AI assistance.
        _tool_calls = [
            tc for run in llm_agent_runs
            for tc in run.tool_calls.all()
        ]

        vendor_ai_used = any(
            (tc.tool_name or "").lower() == "vendor_search"
            and (tc.status or "").upper() == "SUCCESS"
            for tc in _tool_calls
        )
        po_ai_used = any(
            (getattr(run, "agent_type", "") or "").upper() == "PO_RETRIEVAL"
            and (getattr(run, "status", "") or "").upper() == "COMPLETED"
            for run in llm_agent_runs
        )
        grn_ai_used = any(
            (getattr(run, "agent_type", "") or "").upper() == "GRN_RETRIEVAL"
            and (getattr(run, "status", "") or "").upper() == "COMPLETED"
            for run in llm_agent_runs
        )

        vendor_matched = getattr(recon_result, "vendor_match", None)
        po_matched = bool(getattr(recon_result, "purchase_order_id", None))
        grn_required = bool(getattr(recon_result, "grn_required_flag", False))
        grn_matched = bool(getattr(recon_result, "grn_available", False))

        line_method_label = "Rule Based"
        line_method_class = "info"
        if line_matching_summary["ai_used_lines"] > 0 and line_matching_summary["rule_based_lines"] > 0:
            line_method_label = "Mixed (Rule + AI)"
            line_method_class = "primary"
        elif line_matching_summary["ai_used_lines"] > 0:
            line_method_label = "AI Assisted"
            line_method_class = "primary"

        matching_method_overview = [
            {
                "dimension": "Vendor",
                "method_label": "AI Assisted" if vendor_ai_used else "Rule Based",
                "method_class": "primary" if vendor_ai_used else "info",
                "status_label": (
                    "Matched" if vendor_matched is True
                    else "Mismatch" if vendor_matched is False
                    else "Not Evaluated"
                ),
                "status_class": (
                    "success" if vendor_matched is True
                    else "danger" if vendor_matched is False
                    else "secondary"
                ),
                "explanation": (
                    "Vendor was resolved via agent-assisted vendor search before reconciliation."
                    if vendor_ai_used else
                    "Vendor was matched using deterministic master-data checks."
                ),
            },
            {
                "dimension": "PO",
                "method_label": "AI Assisted" if po_ai_used else "Rule Based",
                "method_class": "primary" if po_ai_used else "info",
                "status_label": "Matched" if po_matched else "Not Matched",
                "status_class": "success" if po_matched else "danger",
                "explanation": (
                    "PO was retrieved by PO Retrieval agent before match execution."
                    if po_ai_used else
                    "PO was resolved directly from PO number / deterministic document lookup rules, so PO Retrieval agent was not invoked."
                ),
            },
            {
                "dimension": "GRN",
                "method_label": (
                    "AI Assisted" if grn_ai_used else "Rule Based"
                ) if grn_required else "Not Required",
                "method_class": (
                    "primary" if grn_ai_used else "info"
                ) if grn_required else "secondary",
                "status_label": (
                    "Matched" if grn_matched else "Not Matched"
                ) if grn_required else "Skipped",
                "status_class": (
                    "success" if grn_matched else "danger"
                ) if grn_required else "secondary",
                "explanation": (
                    "GRN was retrieved by GRN Retrieval agent before 3-way comparison."
                    if grn_required and grn_ai_used and grn_matched else
                    "GRN Retrieval agent was invoked, but no matching GRN was found for this PO."
                    if grn_required and grn_ai_used and not grn_matched else
                    "GRN matching was performed using deterministic receipt lookup rules."
                    if grn_required else
                    "GRN check was not required for this reconciliation mode."
                ),
            },
            {
                "dimension": "Line Items",
                "method_label": line_method_label,
                "method_class": line_method_class,
                "status_label": (
                    "Matched" if line_matching_summary["matched_lines"] == line_matching_summary["total_lines"] and line_matching_summary["total_lines"] > 0
                    else "Partial" if line_matching_summary["partial_lines"] > 0
                    else "Not Matched" if line_matching_summary["unmatched_lines"] > 0
                    else "Not Evaluated"
                ),
                "status_class": (
                    "success" if line_matching_summary["matched_lines"] == line_matching_summary["total_lines"] and line_matching_summary["total_lines"] > 0
                    else "warning" if line_matching_summary["partial_lines"] > 0
                    else "danger" if line_matching_summary["unmatched_lines"] > 0
                    else "secondary"
                ),
                "explanation": (
                    "All lines matched with deterministic scoring rules."
                    if line_matching_summary["ai_used_lines"] == 0 else
                    "Rule-based scorer ran first; AI was used only for low-confidence or ambiguous lines."
                ),
            },
        ]

        # Explicit deterministic steps for Agent tab visibility, even when no
        # dedicated agent run exists for vendor or line-item matching.
        deterministic_agent_steps = [
            {
                "name": "Vendor resolution step",
                "type_label": "DETERMINISTIC",
                "status_label": (
                    "Matched" if vendor_matched is True
                    else "Mismatch" if vendor_matched is False
                    else "Not Evaluated"
                ),
                "status_class": (
                    "success" if vendor_matched is True
                    else "danger" if vendor_matched is False
                    else "secondary"
                ),
                "confidence": None,
                "tokens": "--",
                "duration": "--",
                "recommendation": "--",
                "started": "Deterministic (reconciliation stage)",
            },
            {
                "name": "Line matching step",
                "type_label": "DETERMINISTIC",
                "status_label": (
                    "Matched"
                    if line_matching_summary["matched_lines"] == line_matching_summary["total_lines"]
                    and line_matching_summary["total_lines"] > 0
                    else "Partial" if line_matching_summary["partial_lines"] > 0
                    else "Not Matched" if line_matching_summary["unmatched_lines"] > 0
                    else "Not Evaluated"
                ),
                "status_class": (
                    "success"
                    if line_matching_summary["matched_lines"] == line_matching_summary["total_lines"]
                    and line_matching_summary["total_lines"] > 0
                    else "warning" if line_matching_summary["partial_lines"] > 0
                    else "danger" if line_matching_summary["unmatched_lines"] > 0
                    else "secondary"
                ),
                "confidence": None,
                "tokens": "--",
                "duration": "--",
                "recommendation": "--",
                "started": "Deterministic (reconciliation stage)",
            },
        ]

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
        "llm_agent_runs": llm_agent_runs,
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
        "case_closure_remark": case_closure_remark,
        "cost_token_data": cost_token_data,
        "cost_run_history": cost_run_history,
        "cost_reconciliation_history": cost_reconciliation_history,
        "recon_match_status": recon_match_status,
        "recon_mode": recon_mode,
        "extraction_result": extraction_result,
        "extraction_payload": extraction_payload,
        "extraction": extraction,
        "ext": ext,
        "header_fields": header_fields,
        "tax_fields": tax_fields,
        "line_items": line_items,
        "line_items_raw": line_items_raw,
        "line_items_totals": line_items_totals,
        "invoice_tax_breakdown": invoice_tax_breakdown,
        "validation_field_issues": validation_field_issues,
        "corrections": corrections,
        "correction_count": correction_count,
        "parties": parties,
        "enrichment": enrichment,
        "qr_data": qr_data,
        "qr_decision_codes": qr_decision_codes,
        "qr_date_match": qr_date_match,
        "qr_amount_match": qr_amount_match,
        "raw_json_pretty": raw_json_pretty,
        "can_correct_extraction": _has_permission_code(request.user, "extraction.correct"),
        "copilot_context_json": json.dumps(copilot_context, default=str),
        "reviewers": reviewers,
        "activities": list(case.activities.select_related("actor").order_by("-created_at")),
        "line_matching_details": line_matching_details,
        "line_matching_summary": line_matching_summary,
        "matching_method_overview": matching_method_overview,
        "deterministic_agent_steps": deterministic_agent_steps,
        "mode_decision_analysis": mode_decision_analysis,
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

    # Permission gate: reprocess/escalate needs cases.edit, approve/reject needs reviews.decide
    if decision in ("REPROCESSED", "ESCALATED"):
        if not _has_permission_code(request.user, "cases.edit"):
            raise PermissionDenied
    else:
        if not _has_permission_code(request.user, "reviews.decide"):
            raise PermissionDenied

    scoped_qs = _scoped_case_queryset(request)
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
            from apps.cases.services.review_workflow_service import ReviewWorkflowService
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
            dispatch_task(reprocess_case_from_stage_task, case_id=case.pk, stage="PATH_RESOLUTION")
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
        "ESCALATED": CaseStatus.ESCALATED,
    }
    new_status = status_map.get(decision)
    if new_status:
        old_status = case.status
        case.status = new_status
        case.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Case {case.case_number} marked as {case.get_status_display()}.")

        # When the case is approved/closed, mark the invoice as RECONCILED
        if new_status == CaseStatus.CLOSED and case.invoice:
            from apps.core.enums import InvoiceStatus
            if case.invoice.status != InvoiceStatus.RECONCILED:
                case.invoice.status = InvoiceStatus.RECONCILED
                case.invoice.save(update_fields=["status", "updated_at"])

        # Audit: case status change from decision
        from apps.auditlog.services import AuditService
        from apps.core.enums import AuditEventType
        event_map = {
            CaseStatus.CLOSED: AuditEventType.CASE_CLOSED,
            CaseStatus.REJECTED: AuditEventType.CASE_REJECTED,
            CaseStatus.ESCALATED: AuditEventType.CASE_ESCALATED,
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

    case = get_object_or_404(_scoped_case_queryset(request), pk=pk)
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
        from apps.cases.services.review_workflow_service import ReviewWorkflowService
        ReviewWorkflowService.add_comment(assignment, request.user, body)
    else:
        # For Non-PO cases without review assignment, store as case comment
        from apps.cases.models import APCaseComment
        APCaseComment.objects.create(
            case=case,
            author=request.user,
            body=body,
            tenant=getattr(case, 'tenant', None),
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

    case = get_object_or_404(_scoped_case_queryset(request), pk=pk)
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


# ---------------------------------------------------------------------------
# Review template views (merged from apps.reviews)
# ---------------------------------------------------------------------------

def _scope_reviews_for_ap_processor(user, qs):
    """Filter review assignments so AP_PROCESSOR only sees own invoices."""
    if getattr(user, "role", None) != UserRole.AP_PROCESSOR:
        return qs
    from apps.reconciliation.models import ReconciliationConfig
    tenant = getattr(user, "company", None)
    config = ReconciliationConfig.objects.filter(is_default=True, tenant=tenant).first()
    if config is None:
        config = ReconciliationConfig.objects.filter(is_default=True, tenant__isnull=True).first()
    if config and config.ap_processor_sees_all_cases:
        return qs
    return qs.filter(
        reconciliation_result__invoice__document_upload__uploaded_by=user
    )


def _scoped_review_queryset(request):
    from apps.cases.models import ReviewAssignment

    tenant = require_tenant(request)
    qs = ReviewAssignment.objects.all()
    if tenant is not None:
        qs = qs.filter(tenant=tenant)
    return _scope_reviews_for_ap_processor(request.user, qs)


@login_required
def review_assignment_list(request):
    from apps.cases.models import ReviewAssignment
    from apps.core.enums import ReviewStatus
    from apps.reconciliation.models import ReconciliationResult

    qs = (
        _scoped_review_queryset(request)
        .select_related("reconciliation_result", "reconciliation_result__invoice", "assigned_to")
        .order_by("priority", "-created_at")
    )
    tenant = require_tenant(request)
    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Results that need review but have no assignment yet
    assigned_result_ids = _scoped_review_queryset(request).values_list("reconciliation_result_id", flat=True)
    unassigned_qs = ReconciliationResult.objects.filter(match_status=MatchStatus.REQUIRES_REVIEW)
    if tenant is not None:
        unassigned_qs = unassigned_qs.filter(tenant=tenant)
    unassigned_results = (
        unassigned_qs
        .exclude(pk__in=assigned_result_ids)
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .order_by("-created_at")
    )

    return render(request, "reviews/assignment_list.html", {
        "assignments": page_obj,
        "page_obj": page_obj,
        "review_status_choices": ReviewStatus.choices,
        "unassigned_results": unassigned_results,
    })


@login_required
@permission_required_code("reviews.assign")
def review_create_assignments(request):
    """Create review assignments for selected reconciliation results."""
    from apps.cases.models import ReviewAssignment
    from apps.cases.services.review_workflow_service import ReviewWorkflowService
    from apps.reconciliation.models import ReconciliationResult

    if request.method != "POST":
        return redirect("reviews:assignment_list")

    result_ids = request.POST.getlist("result_ids")
    if not result_ids:
        messages.warning(request, "No results selected.")
        return redirect("reviews:assignment_list")

    tenant = require_tenant(request)
    results = ReconciliationResult.objects.filter(pk__in=[int(i) for i in result_ids])
    if tenant is not None:
        results = results.filter(tenant=tenant)
    count = 0
    for result in results:
        if not ReviewAssignment.objects.filter(reconciliation_result=result).exists():
            ReviewWorkflowService.create_assignment(result=result)
            count += 1

    messages.success(request, f"Created {count} review assignment(s).")
    return redirect("reviews:assignment_list")


@login_required
def review_assignment_detail(request, pk):
    from apps.cases.models import ReviewAssignment

    assignment = get_object_or_404(
        _scoped_review_queryset(request).select_related(
            "reconciliation_result",
            "reconciliation_result__invoice",
            "reconciliation_result__invoice__vendor",
            "assigned_to",
        ).prefetch_related("comments__author", "actions__performed_by"),
        pk=pk,
    )
    try:
        decision = assignment.decision
    except ReviewAssignment.decision.RelatedObjectDoesNotExist:
        decision = None

    return render(request, "reviews/assignment_detail.html", {
        "assignment": assignment,
        "comments": assignment.comments.all(),
        "actions": assignment.actions.all(),
    })


@login_required
@permission_required_code("reviews.decide")
def review_decide(request, pk):
    from apps.cases.models import ReviewAssignment
    from apps.cases.services.review_workflow_service import ReviewWorkflowService

    if request.method != "POST":
        return redirect("reviews:assignment_detail", pk=pk)
    assignment = get_object_or_404(_scoped_review_queryset(request), pk=pk)
    decision = request.POST.get("decision")
    reason = request.POST.get("reason", "")
    decision_map = {
        "APPROVED": ReviewWorkflowService.approve,
        "REJECTED": ReviewWorkflowService.reject,
        "REPROCESSED": ReviewWorkflowService.request_reprocess,
    }
    handler = decision_map.get(decision)
    if handler:
        handler(assignment, request.user, reason)

    # Update AP Case status if one exists
    from apps.cases.models import APCase
    ap_case = APCase.objects.filter(
        reconciliation_result=assignment.reconciliation_result, is_active=True
    ).first()
    if ap_case:
        old_status = ap_case.status
        if decision == "APPROVED":
            ap_case.status = "CLOSED"
            ap_case.save(update_fields=["status", "updated_at"])
        elif decision == "REJECTED":
            ap_case.status = "REJECTED"
            ap_case.save(update_fields=["status", "updated_at"])

        # Audit: case status change
        if ap_case.status != old_status:
            from apps.auditlog.services import AuditService
            from apps.core.enums import AuditEventType
            event_map = {"CLOSED": AuditEventType.CASE_CLOSED, "REJECTED": AuditEventType.CASE_REJECTED}
            AuditService.log_event(
                entity_type="APCase",
                entity_id=ap_case.pk,
                event_type=event_map.get(ap_case.status, decision),
                description=f"Case {ap_case.case_number} {old_status} -> {ap_case.status} via review decision",
                user=request.user,
                case_id=ap_case.pk,
                invoice_id=ap_case.invoice_id,
                status_before=old_status,
                status_after=ap_case.status,
                metadata={"review_assignment_id": assignment.pk, "decision": decision, "reason": reason[:300]},
            )

        return redirect("cases:case_agent_view", pk=ap_case.pk)

    return redirect("reviews:assignment_detail", pk=pk)


@login_required
@permission_required_code("reviews.decide")
def review_add_comment(request, pk):
    from apps.cases.models import ReviewAssignment
    from apps.cases.services.review_workflow_service import ReviewWorkflowService

    if request.method != "POST":
        return redirect("reviews:assignment_detail", pk=pk)
    assignment = get_object_or_404(_scoped_review_queryset(request), pk=pk)
    body = request.POST.get("body", "").strip()
    if body:
        ReviewWorkflowService.add_comment(assignment, request.user, body)
    return redirect("reviews:assignment_detail", pk=pk)


# ---------------------------------------------------------------------------
# Agent eval field correction (from Case Agent tab)
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("eval.manage")
def submit_eval_correction(request, case_pk, agent_run_pk):
    """Record a human ground-truth correction on an agent eval field outcome.

    POST params:
        field_outcome_id  -- PK of the EvalFieldOutcome to correct
        ground_truth      -- the correct value
        new_status        -- CORRECT / INCORRECT / MISSING / EXTRA / SKIPPED
    """
    from django.http import JsonResponse
    from apps.core_eval.models import EvalFieldOutcome

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    tenant = require_tenant(request)
    scoped_qs = _scoped_case_queryset(request)
    case = get_object_or_404(scoped_qs, pk=case_pk)

    fo_id = request.POST.get("field_outcome_id", "").strip()
    ground_truth = request.POST.get("ground_truth", "").strip()
    new_status = request.POST.get("new_status", "").strip().upper()

    if not fo_id:
        return JsonResponse({"error": "field_outcome_id required"}, status=400)

    valid_statuses = {c.value for c in EvalFieldOutcome.Status}
    if new_status and new_status not in valid_statuses:
        return JsonResponse(
            {"error": "Invalid status. Must be one of: %s" % ", ".join(sorted(valid_statuses))},
            status=400,
        )

    try:
        fo = EvalFieldOutcome.objects.select_related("eval_run").get(pk=int(fo_id))
    except (EvalFieldOutcome.DoesNotExist, ValueError):
        return JsonResponse({"error": "EvalFieldOutcome not found"}, status=404)

    # Verify this outcome belongs to agent runs for this case
    from apps.agents.models import AgentRun
    if not AgentRun.objects.filter(pk=agent_run_pk).exists():
        return JsonResponse({"error": "Agent run not found"}, status=404)

    # Update the field outcome
    update_fields = ["updated_at"]
    if ground_truth:
        fo.ground_truth_value = ground_truth
        update_fields.append("ground_truth_value")
    if new_status:
        fo.status = new_status
        update_fields.append("status")
    fo.save(update_fields=update_fields)

    # Record a learning signal for this correction
    try:
        from apps.core_eval.services.learning_signal_service import LearningSignalService
        LearningSignalService.record(
            eval_run=fo.eval_run,
            signal_type="human_correction",
            signal_key=fo.field_name,
            signal_value=ground_truth or new_status,
            detail_json={
                "field_outcome_id": fo.pk,
                "original_predicted": fo.predicted_value,
                "corrected_status": new_status or fo.status,
                "corrected_by": request.user.email,
                "agent_run_id": agent_run_pk,
                "case_id": case.pk,
            },
            tenant=tenant,
        )
    except Exception:
        logger.debug("Learning signal for eval correction failed (non-fatal)", exc_info=True)

    return JsonResponse({
        "ok": True,
        "field_outcome_id": fo.pk,
        "ground_truth_value": fo.ground_truth_value,
        "status": fo.status,
    })
