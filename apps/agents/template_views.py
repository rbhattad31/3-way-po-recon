"""Agent template views — reference pages for end users."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.core.prompt_registry import PromptRegistry

from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
from apps.cases.orchestrators.case_orchestrator import PATH_STAGES, STAGE_TO_STATUS
from apps.cases.state_machine.case_state_machine import CASE_TRANSITIONS, TERMINAL_STATES
from apps.core.enums import (
    AgentType,
    AuditEventType,
    CaseStageType,
    CaseStatus,
    PerformedByType,
    ProcessingPath,
    RecommendationType,
)


# Stage metadata: icon, colour, description, performer
_STAGE_META = {
    CaseStageType.INTAKE: {
        "icon": "bi-inbox",
        "color": "secondary",
        "performer": "System",
        "description": "Validate the uploaded document, classify it, and check basic headers.",
    },
    CaseStageType.EXTRACTION: {
        "icon": "bi-file-earmark-text",
        "color": "primary",
        "performer": "Invoice Extraction Agent (OCR + GPT-4o) + Invoice Understanding Agent (low confidence)",
        "description": "Azure Document Intelligence OCR followed by the Invoice Extraction Agent for structured field extraction. If confidence is below threshold, the Invoice Understanding Agent validates the output.",
    },
    CaseStageType.PATH_RESOLUTION: {
        "icon": "bi-signpost-split",
        "color": "info",
        "performer": "Deterministic (CaseRoutingService)",
        "description": "Determine TWO_WAY, THREE_WAY, or NON_PO path based on PO presence, vendor rules, and mode resolver.",
    },
    CaseStageType.PO_RETRIEVAL: {
        "icon": "bi-search",
        "color": "info",
        "performer": "Deterministic + Agent fallback",
        "description": "Look up the Purchase Order referenced by the invoice. Falls back to PO Retrieval Agent if direct lookup fails.",
    },
    CaseStageType.TWO_WAY_MATCHING: {
        "icon": "bi-arrow-left-right",
        "color": "primary",
        "performer": "Deterministic (ReconciliationRunnerService)",
        "description": "Invoice vs PO line-level comparison using configurable tolerances (strict: 2%/1%/1%).",
    },
    CaseStageType.THREE_WAY_MATCHING: {
        "icon": "bi-diagram-3",
        "color": "warning",
        "performer": "Deterministic (ReconciliationRunnerService)",
        "description": "Invoice vs PO vs GRN three-way comparison with quantity, price, and amount tolerances.",
    },
    CaseStageType.GRN_ANALYSIS: {
        "icon": "bi-box-seam",
        "color": "success",
        "performer": "Agent (GRN Specialist)",
        "description": "Conditional — invoked only when GRN-related exceptions exist. GRN Retrieval Agent investigates receipt discrepancies.",
    },
    CaseStageType.NON_PO_VALIDATION: {
        "icon": "bi-shield-check",
        "color": "purple",
        "performer": "Deterministic (NonPOValidationService)",
        "description": "9 deterministic checks: vendor, duplicate, mandatory fields, supporting docs, spend category, policy, cost center, tax, budget.",
    },
    CaseStageType.EXCEPTION_ANALYSIS: {
        "icon": "bi-exclamation-triangle",
        "color": "warning",
        "performer": "Agent (AgentOrchestrator)",
        "description": "LLM-powered agents analyse exceptions, determine root causes, and produce recommendations. May auto-close safe cases.",
    },
    CaseStageType.REVIEW_ROUTING: {
        "icon": "bi-people",
        "color": "danger",
        "performer": "Deterministic (CaseAssignmentService)",
        "description": "Assign the case to the appropriate reviewer based on role, queue, and workload.",
    },
    CaseStageType.CASE_SUMMARY: {
        "icon": "bi-journal-text",
        "color": "secondary",
        "performer": "Deterministic + Agent (Case Summary)",
        "description": "Build a concise narrative summary with reviewer notes and recommended next action.",
    },
}

# Non-PO validation checks metadata
_NON_PO_CHECKS = [
    {"name": "Vendor Validation", "icon": "bi-building", "description": "Vendor exists, is active, and is approved in the system."},
    {"name": "Duplicate Invoice", "icon": "bi-files", "description": "Check for duplicate invoice number + vendor + amount within 90 days."},
    {"name": "Mandatory Fields", "icon": "bi-card-checklist", "description": "All required fields present: invoice number, date, amount, currency, vendor."},
    {"name": "Supporting Documents", "icon": "bi-paperclip", "description": "Required supporting documents (contracts, approvals) are attached."},
    {"name": "Spend Category", "icon": "bi-tags", "description": "Classify spend category from invoice description and line items."},
    {"name": "Policy Compliance", "icon": "bi-shield-lock", "description": "Business rules: approval thresholds, vendor limits, category restrictions."},
    {"name": "Cost Center", "icon": "bi-geo-alt", "description": "Infer cost center / department from invoice context and vendor history."},
    {"name": "Tax / VAT", "icon": "bi-percent", "description": "Tax reasonability: correct rate for jurisdiction, amount matches line totals."},
    {"name": "Budget Availability", "icon": "bi-wallet2", "description": "Sufficient budget remaining in the relevant cost center / GL account."},
]


@login_required
def agent_reference(request):
    """Shows agents, tools, case lifecycle, prompts, and how they work."""
    agents_info = []
    for agent_type_val, agent_cls in AGENT_CLASS_REGISTRY.items():
        instance = agent_cls()
        label = AgentType(agent_type_val).label
        agents_info.append({
            "type": agent_type_val,
            "label": label,
            "description": agent_cls.__doc__ or "",
            "system_prompt": instance.system_prompt,
            "allowed_tools": instance.allowed_tools,
        })

    # Build tool info from the live registry
    from apps.tools.registry.base import ToolRegistry
    all_tools = ToolRegistry.get_all()
    tools_info = []
    for name, tool in sorted(all_tools.items()):
        tools_info.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema.get("properties", {}),
            "required": tool.parameters_schema.get("required", []),
        })

    recommendation_types = [
        {"value": val, "label": label}
        for val, label in RecommendationType.choices
    ]

    # ---- Processing paths & stages ----
    processing_paths = []
    for path_enum in [ProcessingPath.TWO_WAY, ProcessingPath.THREE_WAY, ProcessingPath.NON_PO]:
        stages = PATH_STAGES.get(path_enum, [])
        stage_items = []
        for stage in stages:
            meta = _STAGE_META.get(stage, {})
            stage_items.append({
                "value": stage.value,
                "label": stage.label,
                "icon": meta.get("icon", "bi-circle"),
                "color": meta.get("color", "secondary"),
                "performer": meta.get("performer", ""),
                "description": meta.get("description", ""),
            })
        processing_paths.append({
            "value": path_enum.value,
            "label": path_enum.label,
            "stages": stage_items,
        })

    # ---- State machine transitions ----
    transitions = []
    for from_s, to_s, triggers in CASE_TRANSITIONS:
        transitions.append({
            "from": CaseStatus(from_s).label,
            "from_value": from_s,
            "to": CaseStatus(to_s).label,
            "to_value": to_s,
            "triggers": ", ".join(PerformedByType(t).label for t in sorted(triggers)),
        })

    terminal_states = [CaseStatus(s).label for s in TERMINAL_STATES]

    # ---- Prompts (extraction + agent + case) ----
    prompts = [
        {
            "name": "Invoice Extraction",
            "category": "Extraction",
            "icon": "bi-file-earmark-text",
            "color": "primary",
            "description": (
                "Used by the extraction pipeline when processing uploaded invoice PDFs. "
                "After Azure Document Intelligence performs OCR, this prompt instructs "
                "Azure OpenAI GPT-4o to extract structured invoice data from the raw text."
            ),
            "used_in": "apps/extraction/services/extraction_adapter.py",
            "model": "Azure OpenAI GPT-4o (temperature: 0.0)",
            "prompt_text": PromptRegistry.get("extraction.invoice_system"),
        },
    ]
    for agent in agents_info:
        prompts.append({
            "name": agent["label"],
            "category": "Agent",
            "icon": "bi-robot",
            "color": "success",
            "description": agent["description"],
            "used_in": "apps/agents/services/agent_classes.py",
            "model": "Azure OpenAI GPT-4o (temperature: 0.1)",
            "prompt_text": agent["system_prompt"],
        })

    # Case-specific prompts from PromptRegistry
    case_prompts = [
        ("Case Summary", "case.case_summary",
         "Produces a reviewer-facing narrative with SUMMARY, REVIEWER NOTES, and RECOMMENDATION sections."),
        ("Exception Analysis", "case.exception_analysis",
         "Analyses exceptions/validation issues — determines root cause, severity, and remediation path."),
        ("Non-PO Validation", "case.non_po_validation",
         "Reasons over 9 deterministic check results for invoices without a Purchase Order."),
        ("Reviewer Copilot", "case.reviewer_copilot",
         "Advisory assistant for human reviewers — answers case questions using tools but never commits actions."),
    ]
    for name, slug, description in case_prompts:
        text = PromptRegistry.get(slug, "")
        if text:
            prompts.append({
                "name": name,
                "category": "Case",
                "icon": "bi-briefcase",
                "color": "info",
                "description": description,
                "used_in": f"PromptRegistry: {slug}",
                "model": "Azure OpenAI GPT-4o",
                "prompt_text": text,
            })

    # ---- Traceability context fields ----
    trace_field_groups = [
        {
            "group": "Correlation IDs",
            "icon": "bi-link-45deg",
            "fields": [
                ("trace_id", "Root ID linking all spans in a single request / task chain"),
                ("span_id", "Unique ID for one unit of work (view call, service method, Celery task)"),
                ("parent_span_id", "The span that spawned this one — forms the tree"),
                ("request_id", "HTTP-request-scoped ID (mirrors trace_id for web requests)"),
            ],
        },
        {
            "group": "Business Entity IDs",
            "icon": "bi-box",
            "fields": [
                ("invoice_id", "The invoice being processed"),
                ("case_id", "The AP Case wrapping this invoice"),
                ("reconciliation_run_id", "The reconciliation batch run"),
                ("reconciliation_result_id", "The specific matching result"),
                ("review_assignment_id", "The human-review assignment"),
                ("agent_run_id", "The AI agent execution"),
                ("task_id", "Celery async-task ID"),
            ],
        },
        {
            "group": "Processing Context",
            "icon": "bi-gear",
            "fields": [
                ("processing_path", "TWO_WAY / THREE_WAY / NON_PO"),
                ("stage_name", "Current case stage (e.g. EXTRACTION, THREE_WAY_MATCHING)"),
                ("source_service", "The service class / function emitting the trace"),
                ("source_layer", "UI · API · TASK · SERVICE · AGENT · SYSTEM"),
            ],
        },
        {
            "group": "RBAC Snapshot",
            "icon": "bi-shield-lock",
            "fields": [
                ("actor_user_id", "Authenticated user's PK"),
                ("actor_email", "User email (for audit cross-reference)"),
                ("actor_primary_role", "User's primary RBAC role at time of action"),
                ("actor_roles_snapshot", "All active roles the user held"),
                ("permission_checked", "Permission code evaluated (e.g. reconciliation.run)"),
                ("permission_source", "How it was resolved: ROLE / ADMIN_BYPASS / USER_OVERRIDE / …"),
                ("access_granted", "True / False — was the action allowed?"),
            ],
        },
    ]

    trace_propagation = [
        {
            "method": "HTTP Request",
            "how": "RequestTraceMiddleware creates a root TraceContext per request, enriches with RBAC if authenticated, stores in thread-local, sets X-Trace-ID and X-Request-ID response headers.",
            "icon": "bi-globe",
        },
        {
            "method": "Child Spans",
            "how": "@observed_service, @observed_action, @observed_task decorators call .child() to derive a child span inheriting trace_id with a new span_id.",
            "icon": "bi-diagram-3",
        },
        {
            "method": "Celery Tasks",
            "how": ".as_celery_headers() serialises minimal trace fields into task headers; .from_celery_headers() reconstructs in the worker — keeps the same trace_id across async boundaries.",
            "icon": "bi-arrow-repeat",
        },
        {
            "method": "Thread-Local",
            "how": "TraceContext.get_current() / .set_current() stores context on the current thread, ensuring every log line and audit event within the same request shares the same trace.",
            "icon": "bi-cpu",
        },
    ]

    # ---- Observability info ----
    observed_entries = [
        {"decorator": "@observed_service", "location": "reconciliation/services/runner_service.py", "name": "reconciliation.runner.run", "event": "RECONCILIATION_STARTED"},
        {"decorator": "@observed_service", "location": "reconciliation/services/agent_feedback_service.py", "name": "reconciliation.agent_feedback.apply_found_po", "event": "AGENT_FEEDBACK_APPLIED"},
        {"decorator": "@observed_service", "location": "agents/services/orchestrator.py", "name": "agents.orchestrator.execute", "event": "AGENT_PIPELINE_STARTED"},
        {"decorator": "@observed_service", "location": "reviews/services.py", "name": "reviews.approve", "event": "REVIEW_APPROVED"},
        {"decorator": "@observed_service", "location": "reviews/services.py", "name": "reviews.reject", "event": "REVIEW_REJECTED"},
        {"decorator": "@observed_service", "location": "reviews/services.py", "name": "reviews.request_reprocess", "event": "RECONCILIATION_RERUN"},
        {"decorator": "@observed_action", "location": "documents/template_views.py", "name": "documents.upload_invoice", "event": "perm: documents.upload"},
        {"decorator": "@observed_action", "location": "reconciliation/template_views.py", "name": "reconciliation.start_reconciliation", "event": "perm: reconciliation.run"},
        {"decorator": "@observed_task", "location": "extraction/tasks.py", "name": "extraction.process_invoice_upload", "event": "EXTRACTION_STARTED"},
    ]

    metrics_categories = [
        {"category": "RBAC", "icon": "bi-shield-lock", "counters": "permission_checks_total, permission_granted, permission_denied, eval_duration_ms, role_assignment_changes, role_matrix_changes, unauthorized_sensitive_action"},
        {"category": "Extraction", "icon": "bi-file-earmark-text", "counters": "invoices_uploaded, extraction_runs, extraction_failures, extraction_duration_ms, extraction_confidence_avg"},
        {"category": "Reconciliation", "icon": "bi-arrow-left-right", "counters": "reconciliation_runs, reconciliation_failures, duration_ms, mode_resolution, match_status, po_lookup_miss, grn_lookup_miss, reprocess"},
        {"category": "Reviews", "icon": "bi-people", "counters": "reviews_created, reviews_completed, review_duration_ms, manual_field_corrections"},
        {"category": "Agents", "icon": "bi-robot", "counters": "agent_runs, agent_failures, agent_duration_ms, agent_token_usage, recommendation_total"},
        {"category": "Cases / System", "icon": "bi-briefcase", "counters": "cases_created, stage_duration_ms, stage_retry, task_failures, task_retries"},
    ]

    audit_event_types = [
        {"value": val, "label": label}
        for val, label in AuditEventType.choices
    ]

    # Split business vs RBAC events
    rbac_event_values = {
        "ROLE_ASSIGNED", "ROLE_REMOVED", "ROLE_PERMISSION_CHANGED",
        "USER_PERMISSION_OVERRIDE", "USER_ACTIVATED", "USER_DEACTIVATED",
        "ROLE_CREATED", "ROLE_UPDATED", "PRIMARY_ROLE_CHANGED",
    }
    business_events = [e for e in audit_event_types if e["value"] not in rbac_event_values]
    rbac_events = [e for e in audit_event_types if e["value"] in rbac_event_values]

    # ---- RBAC info ----
    rbac_permission_classes = [
        {"name": "HasPermissionCode", "usage": 'required_permission = "invoices.view"', "description": "Single RBAC permission code check"},
        {"name": "HasAnyPermission", "usage": 'required_permissions = ["invoices.view", "reconciliation.view"]', "description": "Any of multiple permission codes"},
        {"name": "HasRole", "usage": 'required_role = "FINANCE_MANAGER"', "description": "Single role code check"},
        {"name": "HasAnyRole", "usage": 'allowed_roles = ["ADMIN", "AUDITOR"]', "description": "Any of multiple role codes"},
        {"name": "IsReviewAssignee", "usage": "(object-level)", "description": "Checks if user is the assigned reviewer or Admin/FM"},
    ]

    rbac_cbv_mixins = [
        {"name": "PermissionRequiredMixin", "attribute": 'required_permission = "invoices.view"'},
        {"name": "AnyPermissionRequiredMixin", "attribute": 'required_permissions = [...]'},
        {"name": "RoleRequiredMixin", "attribute": 'required_roles = [...]'},
    ]

    rbac_template_tags = [
        {"tag": '{% has_permission "invoices.view" as can_view %}', "description": "Check a single permission code"},
        {"tag": '{% has_role "ADMIN" as is_admin %}', "description": "Check if user has a role"},
        {"tag": '{% has_any_permission "invoices.view,reconciliation.view" as can_see %}', "description": "Check any of multiple permissions"},
        {"tag": '{% if_can "reconciliation.run" %}...{% end_if_can %}', "description": "Block tag — renders content only if permission granted"},
    ]

    rbac_precedence = [
        {"step": "1", "rule": "ADMIN Bypass", "description": "Users with the ADMIN role skip all permission checks — always granted.", "icon": "bi-lightning-charge", "color": "danger"},
        {"step": "2", "rule": "User DENY Override", "description": "Explicit per-user DENY overrides block access regardless of roles.", "icon": "bi-x-octagon", "color": "dark"},
        {"step": "3", "rule": "User ALLOW Override", "description": "Explicit per-user ALLOW overrides grant access even if no role provides it.", "icon": "bi-check-circle", "color": "success"},
        {"step": "4", "rule": "Role Permissions", "description": "Permissions granted through any of the user's active, non-expired roles.", "icon": "bi-person-badge", "color": "primary"},
        {"step": "5", "rule": "Default Deny", "description": "If no rule above matched, the action is denied.", "icon": "bi-slash-circle", "color": "secondary"},
    ]

    # ---- Invoice extraction pipeline ----
    confidence_threshold_raw = getattr(settings, "EXTRACTION_CONFIDENCE_THRESHOLD", 0.75)
    confidence_threshold = int(confidence_threshold_raw * 100)  # Display as percentage
    extraction_pipeline = [
        {
            "step": 1,
            "name": "Document Upload",
            "icon": "bi-cloud-arrow-up",
            "color": "secondary",
            "performer": "User / System",
            "description": "Invoice PDF uploaded via the Documents UI or API. A DocumentUpload record is created and the file is stored.",
        },
        {
            "step": 2,
            "name": "OCR (Azure Document Intelligence)",
            "icon": "bi-eye",
            "color": "info",
            "performer": "Azure Document Intelligence",
            "description": "The uploaded PDF is sent to Azure Document Intelligence for optical character recognition. Raw text and layout data are returned.",
        },
        {
            "step": 3,
            "name": "Invoice Extraction Agent",
            "icon": "bi-robot",
            "color": "primary",
            "performer": "Invoice Extraction Agent (GPT-4o)",
            "description": "The OCR text is passed to the Invoice Extraction Agent which uses GPT-4o with response_format=json_object and temperature=0 to extract structured fields (invoice number, date, vendor, line items, totals). Full agent traceability is recorded.",
        },
        {
            "step": 4,
            "name": "Parse & Normalize",
            "icon": "bi-funnel",
            "color": "success",
            "performer": "System (extraction_adapter)",
            "description": "The agent's JSON output is parsed into a structured dict. Dates, amounts, and currency codes are normalized. Line items are mapped to a standard schema.",
        },
        {
            "step": 5,
            "name": "Validation & Persistence",
            "icon": "bi-check2-square",
            "color": "warning",
            "performer": "System (extraction tasks)",
            "description": "Extracted data is validated (required fields, amount consistency). An Invoice record and InvoiceLineItems are created in the database. Extraction confidence score is computed.",
        },
        {
            "step": 6,
            "name": "AP Case Creation",
            "icon": "bi-briefcase",
            "color": "dark",
            "performer": "System (CaseCreationService)",
            "description": "An AP Case is created linking the invoice. The case orchestrator begins driving the invoice through its processing path.",
        },
        {
            "step": 7,
            "name": "Invoice Understanding Agent",
            "icon": "bi-lightbulb",
            "color": "purple",
            "performer": "Invoice Understanding Agent (conditional)",
            "description": f"Only triggered when extraction confidence is below {confidence_threshold}%. Uses tools (invoice_details, vendor_search) to validate and cross-check the extracted data. Runs within the case orchestrator's EXTRACTION stage.",
        },
    ]

    # ---- Governance API endpoints ----
    governance_endpoints = [
        {"method": "GET", "path": "/api/v1/governance/invoices/<id>/audit-history/", "description": "Full audit trail for an invoice — every event from upload to close.", "access": "ADMIN, AUDITOR, FINANCE_MANAGER"},
        {"method": "GET", "path": "/api/v1/governance/invoices/<id>/agent-trace/", "description": "All agent runs, steps, tool calls, and decisions for an invoice.", "access": "ADMIN, AUDITOR"},
        {"method": "GET", "path": "/api/v1/governance/invoices/<id>/recommendations/", "description": "Agent recommendations with acceptance tracking.", "access": "ADMIN, AUDITOR, FINANCE_MANAGER"},
        {"method": "GET", "path": "/api/v1/governance/invoices/<id>/timeline/", "description": "Unified chronological timeline with 8 event categories and RBAC badges.", "access": "ADMIN, AUDITOR, FINANCE_MANAGER"},
        {"method": "GET", "path": "/api/v1/governance/invoices/<id>/access-history/", "description": "Who accessed this invoice, when, and what permission was checked.", "access": "ADMIN, AUDITOR"},
        {"method": "GET", "path": "/api/v1/governance/cases/<id>/stage-timeline/", "description": "Case stage-by-stage timeline with durations and performers.", "access": "ADMIN, AUDITOR, FINANCE_MANAGER"},
        {"method": "GET", "path": "/api/v1/governance/permission-denials/", "description": "Platform-wide list of permission denials for security monitoring.", "access": "ADMIN, AUDITOR"},
        {"method": "GET", "path": "/api/v1/governance/rbac-activity/", "description": "All role/permission changes: assignments, removals, overrides.", "access": "ADMIN, AUDITOR"},
        {"method": "GET", "path": "/api/v1/governance/agent-performance/", "description": "Aggregate agent performance: run counts, success rates, avg duration.", "access": "ADMIN, AUDITOR"},
    ]

    # ---- Case management audit events ----
    case_event_values = {
        "CASE_ASSIGNED", "CASE_CLOSED", "CASE_REJECTED", "CASE_REPROCESSED",
        "CASE_ESCALATED", "CASE_FAILED", "CASE_STATUS_CHANGED",
        "COMMENT_ADDED", "REVIEWER_ASSIGNED", "REVIEW_STARTED",
    }
    case_events = [e for e in audit_event_types if e["value"] in case_event_values]

    return render(request, "agents/reference.html", {
        "agents_info": agents_info,
        "tools_info": tools_info,
        "recommendation_types": recommendation_types,
        "prompts": prompts,
        "processing_paths": processing_paths,
        "transitions": transitions,
        "terminal_states": terminal_states,
        "non_po_checks": _NON_PO_CHECKS,
        "max_tool_rounds": 6,
        # Invoice pipeline
        "extraction_pipeline": extraction_pipeline,
        "confidence_threshold": confidence_threshold,
        # Traceability
        "trace_field_groups": trace_field_groups,
        "trace_propagation": trace_propagation,
        # Observability
        "observed_entries": observed_entries,
        "metrics_categories": metrics_categories,
        "business_events": business_events,
        "rbac_events": rbac_events,
        "case_events": case_events,
        # Governance
        "governance_endpoints": governance_endpoints,
        # RBAC
        "rbac_permission_classes": rbac_permission_classes,
        "rbac_cbv_mixins": rbac_cbv_mixins,
        "rbac_template_tags": rbac_template_tags,
        "rbac_precedence": rbac_precedence,
    })
