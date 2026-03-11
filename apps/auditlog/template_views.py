"""Audit & Governance template views — server-rendered UI for audit trail and case timelines."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from apps.auditlog.models import AuditEvent
from apps.auditlog.timeline_service import CaseTimelineService
from apps.agents.models import AgentRecommendation, AgentRun
from apps.agents.services.agent_trace_service import AgentTraceService
from apps.core.enums import UserRole
from apps.documents.models import Invoice


@login_required
def audit_event_list(request):
    """Browsable audit event log with filtering."""
    qs = AuditEvent.objects.select_related("performed_by").order_by("-created_at")

    # Filters
    entity_type = request.GET.get("entity_type")
    event_type = request.GET.get("event_type")
    entity_id = request.GET.get("entity_id")

    if entity_type:
        qs = qs.filter(entity_type=entity_type)
    if event_type:
        qs = qs.filter(event_type=event_type)
    if entity_id:
        qs = qs.filter(entity_id=entity_id)

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Distinct values for filter dropdowns
    entity_types = (
        AuditEvent.objects.order_by("entity_type")
        .values_list("entity_type", flat=True)
        .distinct()
    )
    event_types = (
        AuditEvent.objects.exclude(event_type="")
        .order_by("event_type")
        .values_list("event_type", flat=True)
        .distinct()
    )

    return render(request, "governance/audit_event_list.html", {
        "events": page_obj,
        "page_obj": page_obj,
        "entity_types": entity_types,
        "event_types": event_types,
        "current_entity_type": entity_type or "",
        "current_event_type": event_type or "",
        "current_entity_id": entity_id or "",
    })


@login_required
def invoice_governance(request, invoice_id):
    """Full governance view for a single invoice — audit trail, agent trace, timeline."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("vendor"),
        pk=invoice_id,
    )

    # Timeline (all events merged)
    timeline = CaseTimelineService.get_case_timeline(invoice_id)

    # Agent trace
    trace = AgentTraceService.get_trace_for_invoice(invoice_id)

    # Recommendations
    recommendations = (
        AgentRecommendation.objects
        .filter(invoice_id=invoice_id)
        .select_related("agent_run", "accepted_by")
        .order_by("-confidence")
    )

    # Audit events for this invoice
    audit_events = (
        AuditEvent.objects
        .filter(entity_type="Invoice", entity_id=invoice_id)
        .select_related("performed_by")
        .order_by("-created_at")
    )

    # Security: only admins/auditors see full agent trace
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    return render(request, "governance/invoice_governance.html", {
        "invoice": invoice,
        "timeline": timeline,
        "trace": trace,
        "recommendations": recommendations,
        "audit_events": audit_events,
        "show_full_trace": show_full_trace,
    })
