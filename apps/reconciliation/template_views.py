"""Reconciliation template views (server-side rendered)."""
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.agents.models import AgentRecommendation, AgentRun
from apps.auditlog.timeline_service import CaseTimelineService
from apps.core.enums import InvoiceStatus, MatchStatus, UserRole
from apps.documents.models import GoodsReceiptNote, Invoice, PurchaseOrder
from apps.reconciliation.models import ReconciliationConfig, ReconciliationResult
from apps.reviews.models import ReviewAssignment
from apps.tools.models import ToolCall


@login_required
def result_list(request):
    qs = (
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .prefetch_related("exceptions")
        .order_by("-created_at")
    )
    match_status = request.GET.get("match_status")
    if match_status:
        qs = qs.filter(match_status=match_status)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    ready_invoices = (
        Invoice.objects
        .filter(status=InvoiceStatus.READY_FOR_RECON)
        .select_related("vendor")
        .order_by("-created_at")
    )

    return render(request, "reconciliation/result_list.html", {
        "results": page_obj,
        "page_obj": page_obj,
        "match_status_choices": MatchStatus.choices,
        "ready_invoices": ready_invoices,
    })


@login_required
def result_detail(request, pk):
    result = get_object_or_404(
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .prefetch_related("exceptions", "line_results"),
        pk=pk,
    )
    # Deduplicate recommendations: keep the highest-confidence entry per type
    all_recs = AgentRecommendation.objects.filter(reconciliation_result=result).order_by("-confidence")
    seen_types = set()
    recommendations = []
    for rec in all_recs:
        if rec.recommendation_type not in seen_types:
            seen_types.add(rec.recommendation_type)
            recommendations.append(rec)

    # Governance: Agent decision flow
    agent_runs = (
        AgentRun.objects.filter(reconciliation_result=result)
        .select_related("agent_definition")
        .prefetch_related("steps", "tool_calls", "decisions")
        .order_by("created_at")
    )

    # Governance: Case timeline
    timeline = CaseTimelineService.get_case_timeline(result.invoice_id)

    # Security: only admins/auditors see full trace; reviewers see summary only
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    return render(request, "reconciliation/result_detail.html", {
        "result": result,
        "exceptions": result.exceptions.all(),
        "line_results": result.line_results.all(),
        "recommendations": recommendations,
        "agent_runs": agent_runs,
        "timeline": timeline,
        "show_full_trace": show_full_trace,
    })


@login_required
def start_reconciliation(request):
    """Trigger reconciliation for selected invoices."""
    if request.method != "POST":
        return redirect("reconciliation:result_list")

    invoice_ids = request.POST.getlist("invoice_ids")
    if not invoice_ids:
        messages.warning(request, "No invoices selected for reconciliation.")
        return redirect("reconciliation:result_list")

    invoice_ids = [int(i) for i in invoice_ids]

    from django.conf import settings as django_settings

    if getattr(django_settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # Run synchronously — no broker needed
        from apps.reconciliation.services.runner_service import ReconciliationRunnerService
        from apps.documents.models import Invoice as InvoiceModel

        invoices = list(
            InvoiceModel.objects.filter(pk__in=invoice_ids)
            .select_related("vendor", "document_upload")
        )
        runner = ReconciliationRunnerService()
        run = runner.run(invoices=invoices, triggered_by=request.user)

        # Run agent pipeline for non-matched results
        from apps.agents.services.orchestrator import AgentOrchestrator
        from apps.reconciliation.models import ReconciliationResult as ReconResult

        agent_count = 0
        results_needing_agents = ReconResult.objects.filter(
            run=run,
        ).exclude(match_status=MatchStatus.MATCHED).select_related(
            "invoice", "invoice__vendor", "purchase_order",
        )
        orchestrator = AgentOrchestrator()
        for recon_result in results_needing_agents:
            try:
                orchestrator.execute(recon_result)
                agent_count += 1
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "Agent pipeline failed for result %s", recon_result.pk
                )

        agent_msg = f" Agent analysis ran on {agent_count} result(s)." if agent_count else ""
        messages.success(
            request,
            f"Reconciliation complete for {run.total_invoices} invoice(s): "
            f"{run.matched_count} matched, {run.partial_count} partial, "
            f"{run.unmatched_count} unmatched.{agent_msg}",
        )
    else:
        from apps.reconciliation.tasks import run_reconciliation_task
        run_reconciliation_task.delay(
            invoice_ids=invoice_ids,
            triggered_by_id=request.user.pk,
        )
        messages.success(
            request,
            f"Reconciliation started for {len(invoice_ids)} invoice(s). Results will appear shortly.",
        )

    return redirect("reconciliation:result_list")


@login_required
def case_console(request, pk):
    """Investigation console — single-page deep dive into one reconciliation case."""
    result = get_object_or_404(
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "invoice__document_upload", "purchase_order", "purchase_order__vendor")
        .prefetch_related("exceptions", "line_results", "line_results__invoice_line", "line_results__po_line"),
        pk=pk,
    )

    invoice = result.invoice
    po = result.purchase_order

    # GRNs linked to the PO
    grns = []
    grn_line_count = 0
    if po:
        grns = list(GoodsReceiptNote.objects.filter(purchase_order=po).select_related("vendor").prefetch_related("line_items"))
        for grn in grns:
            grn_line_count += grn.line_items.count()

    # Line results
    line_results = list(result.line_results.select_related("invoice_line", "po_line").all())
    mismatch_line_count = sum(1 for ln in line_results if ln.match_status != MatchStatus.MATCHED)

    # Exceptions
    exceptions = list(result.exceptions.all().order_by("-severity", "exception_type"))

    # Agent runs with prefetched relations
    agent_runs = list(
        AgentRun.objects.filter(reconciliation_result=result)
        .select_related("agent_definition")
        .prefetch_related("steps", "tool_calls", "decisions", "recommendations")
        .order_by("created_at")
    )

    # Deduplicated recommendations
    all_recs = AgentRecommendation.objects.filter(reconciliation_result=result).order_by("-confidence")
    seen_types = set()
    recommendations = []
    for rec in all_recs:
        if rec.recommendation_type not in seen_types:
            seen_types.add(rec.recommendation_type)
            recommendations.append(rec)

    # Primary recommendation (highest confidence)
    primary_recommendation = recommendations[0] if recommendations else None

    # Timeline
    timeline = CaseTimelineService.get_case_timeline(invoice.pk)

    # Review assignment
    review_assignment = (
        ReviewAssignment.objects
        .filter(reconciliation_result=result)
        .select_related("assigned_to")
        .prefetch_related("comments", "comments__author", "actions", "actions__performed_by")
        .order_by("-created_at")
        .first()
    )
    review_decision = None
    review_comments = []
    review_actions = []
    if review_assignment:
        try:
            review_decision = review_assignment.decision
        except Exception:
            pass
        review_comments = list(review_assignment.comments.all().order_by("created_at"))
        review_actions = list(review_assignment.actions.all().order_by("-created_at"))

    # Security: role-aware trace visibility
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    # AI case summary (from CASE_SUMMARY agent or result summary)
    case_summary_text = result.summary or ""
    case_summary_agent = None
    for run in agent_runs:
        if run.agent_type == "CASE_SUMMARY" and run.status == "COMPLETED":
            case_summary_agent = run
            if run.summarized_reasoning:
                case_summary_text = run.summarized_reasoning
            break

    context = {
        "result": result,
        "invoice": invoice,
        "po": po,
        "grns": grns,
        "grn_line_count": grn_line_count,
        "line_results": line_results,
        "mismatch_line_count": mismatch_line_count,
        "exceptions": exceptions,
        "agent_runs": agent_runs,
        "recommendations": recommendations,
        "primary_recommendation": primary_recommendation,
        "timeline": timeline,
        "review_assignment": review_assignment,
        "review_decision": review_decision,
        "review_comments": review_comments,
        "review_actions": review_actions,
        "show_full_trace": show_full_trace,
        "case_summary_text": case_summary_text,
        "case_summary_agent": case_summary_agent,
        "match_status_choices": MatchStatus.choices,
    }
    return render(request, "reconciliation/case_console.html", context)


@login_required
def case_export_csv(request, pk):
    """Export reconciliation case data as CSV."""
    result = get_object_or_404(
        ReconciliationResult.objects
        .select_related("invoice", "invoice__vendor", "purchase_order")
        .prefetch_related(
            "exceptions",
            "line_results", "line_results__invoice_line", "line_results__po_line",
        ),
        pk=pk,
    )

    invoice = result.invoice
    po = result.purchase_order

    filename = f"recon_case_{result.pk}_{invoice.invoice_number}.csv"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    # --- Header summary ---
    writer.writerow(["RECONCILIATION CASE REPORT"])
    writer.writerow([])
    writer.writerow(["Case ID", result.pk])
    writer.writerow(["Match Status", result.get_match_status_display()])
    writer.writerow(["Confidence", f"{result.deterministic_confidence * 100:.0f}%" if result.deterministic_confidence else "N/A"])
    writer.writerow(["Summary", result.summary or ""])
    writer.writerow([])

    # --- Invoice info ---
    writer.writerow(["INVOICE DETAILS"])
    writer.writerow(["Invoice Number", invoice.invoice_number])
    writer.writerow(["Vendor", invoice.vendor.name if invoice.vendor else invoice.raw_vendor_name])
    writer.writerow(["Invoice Date", invoice.invoice_date or ""])
    writer.writerow(["Currency", invoice.currency])
    writer.writerow(["Subtotal", invoice.subtotal or ""])
    writer.writerow(["Tax", invoice.tax_amount or ""])
    writer.writerow(["Total", invoice.total_amount or ""])
    writer.writerow([])

    # --- PO info ---
    writer.writerow(["PURCHASE ORDER DETAILS"])
    if po:
        writer.writerow(["PO Number", po.po_number])
        writer.writerow(["Vendor", po.vendor.name if po.vendor else ""])
        writer.writerow(["PO Date", po.po_date or ""])
        writer.writerow(["Total", po.total_amount or ""])
        writer.writerow(["Status", po.status])
    else:
        writer.writerow(["PO Number", invoice.po_number or "NOT FOUND"])
    writer.writerow([])

    # --- Header-level checks ---
    writer.writerow(["HEADER-LEVEL CHECKS"])
    writer.writerow(["Check", "Result"])
    writer.writerow(["Vendor Match", "Yes" if result.vendor_match else "No" if result.vendor_match is False else "N/A"])
    writer.writerow(["Currency Match", "Yes" if result.currency_match else "No" if result.currency_match is False else "N/A"])
    writer.writerow(["PO Total Match", "Yes" if result.po_total_match else "No" if result.po_total_match is False else "N/A"])
    writer.writerow(["GRN Available", "Yes" if result.grn_available else "No"])
    writer.writerow(["GRN Fully Received", "Yes" if result.grn_fully_received else "No" if result.grn_fully_received is False else "N/A"])
    writer.writerow(["Amount Difference", result.total_amount_difference or "0.00"])
    writer.writerow(["Amount Difference %", f"{result.total_amount_difference_pct or 0}%"])
    writer.writerow([])

    # --- Line-level comparison ---
    line_results = list(result.line_results.select_related("invoice_line", "po_line").all())
    writer.writerow(["LINE-LEVEL COMPARISON"])
    writer.writerow([
        "#", "Item", "Inv Qty", "PO Qty", "GRN Qty",
        "Inv Price", "PO Price", "Inv Amt", "PO Amt",
        "Variance", "Status",
    ])
    for i, ln in enumerate(line_results, 1):
        desc = ""
        if ln.invoice_line:
            desc = ln.invoice_line.description
        elif ln.po_line:
            desc = ln.po_line.description
        writer.writerow([
            i, desc,
            ln.qty_invoice or "", ln.qty_po or "", ln.qty_received or "",
            ln.price_invoice or "", ln.price_po or "",
            ln.amount_invoice or "", ln.amount_po or "",
            ln.amount_difference or "0.00",
            ln.get_match_status_display(),
        ])
    writer.writerow([])

    # --- Exceptions ---
    exceptions = list(result.exceptions.all().order_by("-severity", "exception_type"))
    writer.writerow(["EXCEPTIONS"])
    writer.writerow(["Severity", "Type", "Message", "Resolved"])
    for exc in exceptions:
        writer.writerow([
            exc.severity,
            exc.get_exception_type_display(),
            exc.message,
            "Yes" if exc.resolved else "No",
        ])
    if not exceptions:
        writer.writerow(["No exceptions"])
    writer.writerow([])

    # --- Agent runs ---
    agent_runs = list(
        AgentRun.objects.filter(reconciliation_result=result)
        .select_related("agent_definition")
        .prefetch_related("decisions")
        .order_by("created_at")
    )
    writer.writerow(["AGENT DECISION FLOW"])
    writer.writerow(["Agent", "Status", "Confidence", "Reasoning"])
    for run in agent_runs:
        writer.writerow([
            run.agent_definition.name if run.agent_definition else run.agent_type,
            run.status,
            f"{run.confidence * 100:.0f}%" if run.confidence else "",
            run.summarized_reasoning or "",
        ])
    if not agent_runs:
        writer.writerow(["No agent runs"])

    return response


@login_required
def recon_settings(request):
    """View and edit reconciliation config profiles. Admin-only for writes."""
    configs = ReconciliationConfig.objects.all().order_by("-is_default", "name")
    user_role = getattr(request.user, "role", None)
    is_admin = user_role == UserRole.ADMIN

    if request.method == "POST" and is_admin:
        config_id = request.POST.get("config_id")
        action = request.POST.get("action")

        if action == "delete" and config_id:
            config = get_object_or_404(ReconciliationConfig, pk=config_id)
            if config.is_default:
                messages.error(request, "Cannot delete the default config profile.")
            else:
                config.delete()
                messages.success(request, f"Config '{config.name}' deleted.")
            return redirect("reconciliation:recon_settings")

        if action == "set_default" and config_id:
            ReconciliationConfig.objects.filter(is_default=True).update(is_default=False)
            ReconciliationConfig.objects.filter(pk=config_id).update(is_default=True)
            messages.success(request, "Default config updated.")
            return redirect("reconciliation:recon_settings")

        # Create or update
        if config_id:
            config = get_object_or_404(ReconciliationConfig, pk=config_id)
        else:
            config = ReconciliationConfig()

        config.name = request.POST.get("name", "").strip()
        if not config.name:
            messages.error(request, "Config name is required.")
            return redirect("reconciliation:recon_settings")

        try:
            config.quantity_tolerance_pct = float(request.POST.get("quantity_tolerance_pct", 2.0))
            config.price_tolerance_pct = float(request.POST.get("price_tolerance_pct", 1.0))
            config.amount_tolerance_pct = float(request.POST.get("amount_tolerance_pct", 1.0))
            config.auto_close_qty_tolerance_pct = float(request.POST.get("auto_close_qty_tolerance_pct", 5.0))
            config.auto_close_price_tolerance_pct = float(request.POST.get("auto_close_price_tolerance_pct", 3.0))
            config.auto_close_amount_tolerance_pct = float(request.POST.get("auto_close_amount_tolerance_pct", 3.0))
            config.extraction_confidence_threshold = float(request.POST.get("extraction_confidence_threshold", 0.75))
        except (ValueError, TypeError):
            messages.error(request, "Invalid numeric value in form.")
            return redirect("reconciliation:recon_settings")

        config.auto_close_on_match = request.POST.get("auto_close_on_match") == "on"
        config.enable_agents = request.POST.get("enable_agents") == "on"

        config.save()
        verb = "updated" if config_id else "created"
        messages.success(request, f"Config '{config.name}' {verb}.")
        return redirect("reconciliation:recon_settings")

    return render(request, "reconciliation/settings.html", {
        "configs": configs,
        "is_admin": is_admin,
    })
