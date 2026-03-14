"""Audit & Governance template views — server-rendered UI for audit trail and case timelines."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from apps.auditlog.models import AuditEvent
from apps.auditlog.timeline_service import CaseTimelineService
from apps.agents.models import AgentRecommendation, AgentRun
from apps.agents.services.agent_trace_service import AgentTraceService
from apps.core.enums import UserRole
from apps.core.permissions import permission_required_code
from apps.documents.models import Invoice


@login_required
@permission_required_code("governance.view")
def audit_event_list(request):
    """Browsable audit event log with filtering (including RBAC filters)."""
    qs = AuditEvent.objects.select_related("performed_by").order_by("-created_at")

    # Filters
    entity_type = request.GET.get("entity_type")
    event_type = request.GET.get("event_type")
    entity_id = request.GET.get("entity_id")
    role = request.GET.get("role")
    trace_id = request.GET.get("trace_id", "").strip()
    denied_only = request.GET.get("denied_only")

    if entity_type:
        qs = qs.filter(entity_type=entity_type)
    if event_type:
        qs = qs.filter(event_type=event_type)
    if entity_id:
        qs = qs.filter(entity_id=entity_id)
    if role:
        qs = qs.filter(actor_primary_role=role)
    if trace_id:
        qs = qs.filter(trace_id=trace_id)
    if denied_only:
        qs = qs.filter(access_granted=False)

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
    roles = (
        AuditEvent.objects.exclude(actor_primary_role="")
        .order_by("actor_primary_role")
        .values_list("actor_primary_role", flat=True)
        .distinct()
    )

    return render(request, "governance/audit_event_list.html", {
        "events": page_obj,
        "page_obj": page_obj,
        "entity_types": entity_types,
        "event_types": event_types,
        "roles": roles,
        "current_entity_type": entity_type or "",
        "current_event_type": event_type or "",
        "current_entity_id": entity_id or "",
        "current_role": role or "",
        "current_trace_id": trace_id,
        "current_denied_only": bool(denied_only),
    })


@login_required
@permission_required_code("governance.view")
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

    # Audit events for this invoice (with RBAC cross-reference)
    from django.db.models import Q
    audit_events = (
        AuditEvent.objects
        .filter(
            Q(entity_type="Invoice", entity_id=invoice_id) |
            Q(invoice_id=invoice_id)
        )
        .select_related("performed_by")
        .order_by("-created_at")
        .distinct()
    )

    # Access history (events with permission_checked)
    access_events = audit_events.exclude(permission_checked="").exclude(permission_checked__isnull=True)

    # Security: only admins/auditors see full agent trace
    user_role = getattr(request.user, "role", None)
    show_full_trace = user_role in (UserRole.ADMIN, UserRole.AUDITOR)

    # Linked AP Case (for Case Console button)
    from apps.cases.models import APCase
    ap_case = APCase.objects.filter(invoice=invoice).first()

    return render(request, "governance/invoice_governance.html", {
        "invoice": invoice,
        "timeline": timeline,
        "trace": trace,
        "recommendations": recommendations,
        "audit_events": audit_events,
        "access_events": access_events,
        "show_full_trace": show_full_trace,
        "ap_case": ap_case,
    })
