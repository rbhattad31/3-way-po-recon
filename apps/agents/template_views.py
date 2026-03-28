"""Agent template views -- reference pages for end users."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.core.prompt_registry import PromptRegistry

from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
from apps.agents.services.guardrails_service import (
    AGENT_PERMISSIONS,
    TOOL_PERMISSIONS,
    RECOMMENDATION_PERMISSIONS,
    ACTION_PERMISSIONS,
    ORCHESTRATE_PERMISSION,
    SYSTEM_AGENT_EMAIL,
    SYSTEM_AGENT_ROLE_CODE,
)
from apps.cases.orchestrators.case_orchestrator import PATH_STAGES, STAGE_TO_STATUS
from apps.cases.state_machine.case_state_machine import CASE_TRANSITIONS, TERMINAL_STATES
from apps.core.enums import (
    AgentType,
    AuditEventType,
    CaseStageType,
    CaseStatus,
    InvoicePostingStatus,
    PerformedByType,
    PostingReviewQueue,
    PostingRunStatus,
    PostingStage,
    ProcessingPath,
    RecommendationType,
)
from apps.erp_integration.enums import (
    ERPConnectorType,
    ERPResolutionType,
    ERPSubmissionType,
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
            "required_permission": AGENT_PERMISSIONS.get(agent_type_val, ""),
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
            "required_permission": TOOL_PERMISSIONS.get(tool.name, ""),
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

    # ---- Agent RBAC Guardrails ----
    guardrail_flow = [
        {
            "step": 1, "name": "Actor Resolution",
            "icon": "bi-person-badge", "color": "primary",
            "description": "Identify the actor: human user from request context, or SYSTEM_AGENT service account for Celery/autonomous runs.",
        },
        {
            "step": 2, "name": "Orchestration Authorization",
            "icon": "bi-play-circle", "color": "info",
            "description": f"Check the actor holds the '{ORCHESTRATE_PERMISSION}' permission before starting the agent pipeline.",
        },
        {
            "step": 3, "name": "Data-Scope Authorization",
            "icon": "bi-funnel", "color": "secondary",
            "description": "Verify the actor's data scope (business unit, vendor) covers the reconciliation result. Checked once per pipeline run after orchestration auth. Admins and SYSTEM_AGENT are always unrestricted.",
        },
        {
            "step": 4, "name": "Per-Agent Authorization",
            "icon": "bi-robot", "color": "success",
            "description": "Before each agent runs, verify the actor has the agent-specific permission (e.g. agents.run_extraction).",
        },
        {
            "step": 5, "name": "Per-Tool Authorization",
            "icon": "bi-wrench", "color": "warning",
            "description": "When an agent invokes a tool, verify the actor has the tool's required permission (e.g. purchase_orders.view).",
        },
        {
            "step": 6, "name": "Recommendation Authorization",
            "icon": "bi-lightbulb", "color": "danger",
            "description": "When an agent produces a recommendation, verify the actor may issue that recommendation type.",
        },
        {
            "step": 7, "name": "Post-Policy Authorization",
            "icon": "bi-shield-check", "color": "dark",
            "description": "Auto-close and escalation actions are authorized separately after the policy engine decides.",
        },
    ]

    guardrail_events = [
        {"value": "GUARDRAIL_GRANTED", "label": "Guardrail Granted", "description": "Permission check passed — agent/tool/action authorized."},
        {"value": "GUARDRAIL_DENIED", "label": "Guardrail Denied", "description": "Permission check failed — agent/tool/action blocked."},
        {"value": "TOOL_CALL_AUTHORIZED", "label": "Tool Call Authorized", "description": "Agent tool invocation passed RBAC check."},
        {"value": "TOOL_CALL_DENIED", "label": "Tool Call Denied", "description": "Agent tool invocation blocked by RBAC."},
        {"value": "RECOMMENDATION_ACCEPTED", "label": "Recommendation Accepted", "description": "Agent recommendation passed RBAC authorization."},
        {"value": "RECOMMENDATION_DENIED", "label": "Recommendation Denied", "description": "Agent recommendation blocked by RBAC."},
        {"value": "AUTO_CLOSE_AUTHORIZED", "label": "Auto-Close Authorized", "description": "Policy engine auto-close action authorized."},
        {"value": "AUTO_CLOSE_DENIED", "label": "Auto-Close Denied", "description": "Policy engine auto-close action blocked."},
        {"value": "SYSTEM_AGENT_USED", "label": "System Agent Used", "description": "No human context — SYSTEM_AGENT identity was resolved."},
    ]

    system_agent_info = {
        "email": SYSTEM_AGENT_EMAIL,
        "role_code": SYSTEM_AGENT_ROLE_CODE,
        "rank": 100,
        "description": (
            "A dedicated service account used when no human user context is available "
            "(e.g. Celery async tasks, system-triggered pipelines). The SYSTEM_AGENT role "
            "carries 22 permissions covering all agent, tool, and recommendation operations."
        ),
    }

    recommendation_perms = [
        {"type": rec_type, "permission": perm_code}
        for rec_type, perm_code in RECOMMENDATION_PERMISSIONS.items()
    ]

    action_perms = [
        {"action": action, "permission": perm_code}
        for action, perm_code in ACTION_PERMISSIONS.items()
    ]

    # ---- Hardening: tool-failure runtime guards ----
    # Derived from catalog: agents with requires_tool_grounding=True
    from apps.agents.models import AgentDefinition as _AD
    tool_grounded_agents = sorted(
        _AD.objects.filter(requires_tool_grounding=True, enabled=True)
        .values_list("agent_type", flat=True)
    )

    # ---- Hardening: data-scope authorization dimensions ----
    data_scope_dimensions = [
        {
            "dimension": "Business Unit",
            "scope_key": "allowed_business_units",
            "source_actor": "UserRole.scope_json[\"allowed_business_units\"] — list[str]",
            "source_result": "ReconciliationPolicy.business_unit (via result.policy_applied)",
            "status": "enforced",
        },
        {
            "dimension": "Vendor",
            "scope_key": "allowed_vendor_ids",
            "source_actor": "UserRole.scope_json[\"allowed_vendor_ids\"] — list[int]",
            "source_result": "result.invoice.vendor_id",
            "status": "enforced",
        },
        {
            "dimension": "Country / Legal Entity",
            "scope_key": "—",
            "source_actor": "Not yet supported",
            "source_result": "No country_code field on Invoice or PurchaseOrder",
            "status": "pending",
        },
        {
            "dimension": "Cost Centre",
            "scope_key": "—",
            "source_actor": "Not yet supported",
            "source_result": "No cost_centre field on Invoice or PurchaseOrder",
            "status": "pending",
        },
    ]

    # ---- Posting Pipeline ----
    posting_pipeline_stages = [
        {
            "number": 1,
            "code": "ELIGIBILITY_CHECK",
            "label": "Eligibility Check",
            "icon": "bi-check2-circle",
            "color": "secondary",
            "description": (
                "Verify the invoice is RECONCILED, not already posting, and not a duplicate "
                "posting attempt. Raises ValueError if ineligible; logs POSTING_ELIGIBILITY_FAILED."
            ),
        },
        {
            "number": 2,
            "code": "SNAPSHOT_BUILD",
            "label": "Snapshot Build",
            "icon": "bi-camera",
            "color": "info",
            "description": (
                "Capture a point-in-time JSON snapshot of the invoice header and all line items "
                "to ensure consistency throughout the pipeline run."
            ),
        },
        {
            "number": 3,
            "code": "MAPPING",
            "label": "Reference Resolution + Mapping",
            "icon": "bi-search",
            "color": "primary",
            "description": (
                "PostingMappingEngine resolves vendor, item, tax code, and cost-center codes. "
                "Strategy per field: exact code -> alias -> name -> fuzzy. Live ERP connector "
                "used when available (ConnectorFactory). ERP source metadata stored per field."
            ),
        },
        {
            "number": 4,
            "code": "VALIDATION",
            "label": "Validation",
            "icon": "bi-shield-check",
            "color": "warning",
            "description": (
                "Run 10+ rules: required field presence, amount consistency, tax reasonability, "
                "PO cross-check, business rule compliance. Generates PostingIssue records per failure."
            ),
        },
        {
            "number": 5,
            "code": "CONFIDENCE",
            "label": "Confidence Scoring",
            "icon": "bi-percent",
            "color": "success",
            "description": (
                "5-dimensional weighted score: header completeness (15%), vendor mapping (25%), "
                "line item mapping (30%), tax completeness (15%), reference freshness (15%). "
                "is_touchless=True when no review needed."
            ),
        },
        {
            "number": 6,
            "code": "REVIEW_ROUTING",
            "label": "Review Routing",
            "icon": "bi-signpost-split",
            "color": "danger",
            "description": (
                "Assign to the appropriate review queue when confidence is low or issues exist. "
                "Queues: VENDOR_MAPPING_REVIEW, ITEM_MAPPING_REVIEW, TAX_REVIEW, "
                "COST_CENTER_REVIEW, PO_REVIEW, POSTING_OPS."
            ),
        },
        {
            "number": 7,
            "code": "PAYLOAD_BUILD",
            "label": "Payload Build",
            "icon": "bi-file-earmark-code",
            "color": "primary",
            "description": (
                "PostingPayloadBuilder assembles the canonical ERP posting payload "
                "(header + line items + tax + accounting codes), stored as JSON in posting_payload_json."
            ),
        },
        {
            "number": 8,
            "code": "FINALIZATION",
            "label": "Finalization + Duplicate Check",
            "icon": "bi-check-circle",
            "color": "success",
            "description": (
                "Stage 9b: Duplicate invoice check via the ERP integration layer "
                "(DuplicateInvoiceResolver). Persist all run artifacts: PostingFieldValue, "
                "PostingLineItem, PostingIssue, PostingEvidence, PostingApprovalRecord."
            ),
        },
        {
            "number": 9,
            "code": "STATUS",
            "label": "Status Update",
            "icon": "bi-flag",
            "color": "dark",
            "description": (
                "Update InvoicePosting status: MAPPING_REVIEW_REQUIRED (issues found) or "
                "READY_TO_SUBMIT (touchless). ERP resolution provenance stored in "
                "erp_source_metadata_json."
            ),
        },
    ]

    posting_confidence_dimensions = [
        {"name": "Header Completeness", "weight": 15, "icon": "bi-card-text",
         "description": "Invoice number, date, currency, vendor all present and valid."},
        {"name": "Vendor Mapping", "weight": 25, "icon": "bi-building",
         "description": "Vendor resolved with high confidence; exact/alias match preferred over fuzzy."},
        {"name": "Line Item Mapping", "weight": 30, "icon": "bi-list-ul",
         "description": "All line items have resolved item codes; PO cross-reference confirmed."},
        {"name": "Tax Completeness", "weight": 15, "icon": "bi-percent",
         "description": "Tax codes resolved; rate and amount consistent with jurisdiction."},
        {"name": "Reference Freshness", "weight": 15, "icon": "bi-clock",
         "description": "ERP reference data imported within POSTING_REFERENCE_FRESHNESS_HOURS (default 168h / 7 days)."},
    ]

    posting_statuses = [{"value": v, "label": l} for v, l in InvoicePostingStatus.choices]
    posting_review_queues = [{"value": v, "label": l} for v, l in PostingReviewQueue.choices]

    # ---- ERP Integration ----
    erp_connector_info = [
        {
            "type": "CUSTOM",
            "label": "Custom ERP",
            "icon": "bi-code-slash",
            "description": "Flexible REST connector with custom endpoint configuration and any authentication scheme.",
            "capabilities": ["vendor_lookup", "po_lookup", "invoice_create"],
            "auth": "Bearer / API Key",
        },
        {
            "type": "SQLSERVER",
            "label": "SQL Server",
            "icon": "bi-server",
            "description": "Direct SQL Server or Azure SQL database access. Queries ERP data without REST overhead.",
            "capabilities": ["vendor_lookup", "item_lookup", "po_lookup", "grn_lookup"],
            "auth": "Connection String",
        },
        {
            "type": "MYSQL",
            "label": "MySQL / MariaDB",
            "icon": "bi-database",
            "description": "Direct MySQL or MariaDB database access for ERP data lookup.",
            "capabilities": ["vendor_lookup", "item_lookup", "po_lookup"],
            "auth": "Connection String",
        },
        {
            "type": "DYNAMICS",
            "label": "Microsoft Dynamics 365",
            "icon": "bi-microsoft",
            "description": "OAuth2-authenticated Dynamics 365 Business Central REST API with full invoice lifecycle support.",
            "capabilities": ["vendor_lookup", "item_lookup", "po_lookup", "invoice_create", "invoice_park"],
            "auth": "OAuth 2.0 (tenant_id + client_id + client_secret)",
        },
        {
            "type": "ZOHO",
            "label": "Zoho",
            "icon": "bi-globe2",
            "description": "Zoho Books / Zoho Inventory REST API connector for vendor and item data.",
            "capabilities": ["vendor_lookup", "item_lookup", "po_lookup"],
            "auth": "OAuth 2.0",
        },
        {
            "type": "SALESFORCE",
            "label": "Salesforce",
            "icon": "bi-cloud",
            "description": "Salesforce REST API connector for vendor account and PO data.",
            "capabilities": ["vendor_lookup", "po_lookup"],
            "auth": "OAuth 2.0",
        },
    ]

    erp_resolution_chain = [
        {
            "step": 1,
            "name": "Cache Check",
            "icon": "bi-lightning-charge",
            "color": "warning",
            "description": (
                "Check ERPReferenceCacheRecord for a fresh entry. TTL controlled by "
                "ERP_CACHE_TTL_SECONDS env var (default 3600 s). Cache hit returns immediately."
            ),
        },
        {
            "step": 2,
            "name": "ERP API Connector",
            "icon": "bi-plug",
            "color": "primary",
            "description": (
                "Call the live ERP API via ConnectorFactory.get_default_connector(). Only invoked "
                "if the connector supports the resolution type (capability flag check)."
            ),
        },
        {
            "step": 3,
            "name": "DB Fallback",
            "icon": "bi-database-gear",
            "color": "success",
            "description": (
                "Query local ERP reference tables (ERPVendorReference, ERPItemReference, etc.) "
                "populated by ExcelImportOrchestrator. Always available as last resort."
            ),
        },
    ]

    erp_resolution_types_info = [
        {"type": "VENDOR", "label": "Vendor Lookup", "db_model": "ERPVendorReference", "fallback": "VendorDBFallbackAdapter"},
        {"type": "ITEM", "label": "Item Lookup", "db_model": "ERPItemReference", "fallback": "ItemDBFallbackAdapter"},
        {"type": "TAX", "label": "Tax Code Lookup", "db_model": "ERPTaxCodeReference", "fallback": "TaxDBFallbackAdapter"},
        {"type": "COST_CENTER", "label": "Cost Center Lookup", "db_model": "ERPCostCenterReference", "fallback": "CostCenterDBFallbackAdapter"},
        {"type": "PO", "label": "PO Lookup", "db_model": "ERPPOReference", "fallback": "PODBFallbackAdapter"},
        {"type": "GRN", "label": "GRN Lookup", "db_model": "GoodsReceiptNote (local)", "fallback": "GRNDBFallbackAdapter"},
        {"type": "DUPLICATE_INVOICE", "label": "Duplicate Invoice Check", "db_model": "Invoice (local)", "fallback": "DuplicateInvoiceDBFallbackAdapter"},
    ]

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
        # Agent RBAC Guardrails
        "guardrail_flow": guardrail_flow,
        "guardrail_events": guardrail_events,
        "system_agent_info": system_agent_info,
        "recommendation_perms": recommendation_perms,
        "action_perms": action_perms,
        "orchestrate_permission": ORCHESTRATE_PERMISSION,
        # Hardening
        "tool_grounded_agents": tool_grounded_agents,
        "data_scope_dimensions": data_scope_dimensions,
        # Posting Pipeline
        "posting_pipeline_stages": posting_pipeline_stages,
        "posting_confidence_dimensions": posting_confidence_dimensions,
        "posting_statuses": posting_statuses,
        "posting_review_queues": posting_review_queues,
        # ERP Integration
        "erp_connector_info": erp_connector_info,
        "erp_resolution_chain": erp_resolution_chain,
        "erp_resolution_types_info": erp_resolution_types_info,
    })
