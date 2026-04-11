"""Agent template views -- reference pages and agent run explorer."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from apps.core.prompt_registry import PromptRegistry
from apps.core.permissions import permission_required_code
from apps.core.tenant_utils import TenantQuerysetMixin, require_tenant

from apps.agents.models import AgentRun, DecisionLog, AgentRecommendation
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
    AgentRunStatus,
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

# ---------------------------------------------------------------------------
# Agent contract metadata (mirrors seed_agent_contracts.py -- kept in sync)
# ---------------------------------------------------------------------------
_AGENT_CONTRACTS = {
    "INVOICE_EXTRACTION": {
        "purpose": "Extract structured invoice data from OCR text using LLM",
        "entry_conditions": "Called immediately after OCR completes on a new invoice document",
        "success_criteria": "Returns full JSON with vendor, PO number, line items, and confidence >= 0.7",
        "prohibited_actions": ["AUTO_CLOSE", "ESCALATE_TO_MANAGER"],
        "requires_tool_grounding": False,
        "min_tool_calls": 0,
        "tool_failure_confidence_cap": None,
        "allowed_recs": None,
        "default_fallback_rec": "REPROCESS_EXTRACTION",
        "is_pipeline": True,
        "trigger": "Pipeline-only -- runs in ExtractionAdapter directly after Azure DI OCR. Never added to AgentOrchestrator plan.",
        "dynamic_adds": "",
        "skip_conditions": "Never scheduled by PolicyEngine -- pipeline-only.",
        "human_review_required_conditions": "confidence < 0.6 or key fields missing",
    },
    "INVOICE_UNDERSTANDING": {
        "purpose": "Validate and clarify invoice extraction quality when confidence is low",
        "entry_conditions": "extraction_confidence < 0.75 (post-OCR) OR < 0.70 (post-reconciliation)",
        "success_criteria": "Determines whether extraction is reliable; may trigger RECONCILIATION_ASSIST via _reflect()",
        "prohibited_actions": ["AUTO_CLOSE"],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.5,
        "allowed_recs": ["REPROCESS_EXTRACTION", "SEND_TO_AP_REVIEW"],
        "default_fallback_rec": "REPROCESS_EXTRACTION",
        "is_pipeline": False,
        "trigger": "PolicyEngine: extraction_conf < 0.70. Also fired by stage_executor when post-OCR confidence < 0.75.",
        "dynamic_adds": "If own output confidence < 0.5 after tools, _reflect() adds RECONCILIATION_ASSIST to the plan.",
        "skip_conditions": "Skipped when extraction_conf >= 0.70.",
        "human_review_required_conditions": "confidence < 0.5 after tool grounding",
    },
    "PO_RETRIEVAL": {
        "purpose": "Find the correct Purchase Order when deterministic lookup failed",
        "entry_conditions": "match_status = PO_NOT_FOUND or po_number missing on invoice",
        "success_criteria": "PO number confirmed via tool call and present in evidence",
        "prohibited_actions": ["AUTO_CLOSE"],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.4,
        "allowed_recs": ["SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT"],
        "default_fallback_rec": "SEND_TO_AP_REVIEW",
        "is_pipeline": False,
        "trigger": "PolicyEngine: PO_NOT_FOUND exception raised after deterministic PO lookup fails.",
        "dynamic_adds": "If PO found in a THREE_WAY case, _reflect() adds GRN_RETRIEVAL to the plan.",
        "skip_conditions": "Skipped when PO is found via deterministic lookup.",
        "human_review_required_conditions": "no PO found after all search strategies exhausted",
    },
    "GRN_RETRIEVAL": {
        "purpose": "Investigate goods receipt status when GRN is missing or partial",
        "entry_conditions": "reconciliation_mode = THREE_WAY AND exception_type = GRN_NOT_FOUND or GRN_PARTIAL",
        "success_criteria": "GRN status confirmed via tool call with quantity comparison",
        "prohibited_actions": ["AUTO_CLOSE"],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.4,
        "allowed_recs": ["SEND_TO_PROCUREMENT", "SEND_TO_AP_REVIEW"],
        "default_fallback_rec": "SEND_TO_PROCUREMENT",
        "is_pipeline": False,
        "trigger": "PolicyEngine: GRN_NOT_FOUND/GRN_PARTIAL in THREE_WAY mode. Also added dynamically by _reflect() after PO_RETRIEVAL succeeds.",
        "dynamic_adds": "",
        "skip_conditions": "Never runs in TWO_WAY or NON_PO mode -- suppressed by PolicyEngine.",
        "human_review_required_conditions": "goods not yet received or quantity rejected",
    },
    "RECONCILIATION_ASSIST": {
        "purpose": "Investigate partial match discrepancies at line level",
        "entry_conditions": "match_status = PARTIAL_MATCH with qty/price/amount discrepancies",
        "success_criteria": "Explains root cause of discrepancies and recommends resolution",
        "prohibited_actions": [],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.5,
        "allowed_recs": ["AUTO_CLOSE", "SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT", "SEND_TO_VENDOR_CLARIFICATION"],
        "default_fallback_rec": "SEND_TO_AP_REVIEW",
        "is_pipeline": False,
        "trigger": "PolicyEngine: PARTIAL_MATCH outside auto-close tolerance band. Also added by _reflect() after INVOICE_UNDERSTANDING confidence < 0.5.",
        "dynamic_adds": "",
        "skip_conditions": "If PARTIAL_MATCH falls within auto-close band (qty: 5%, price: 3%, amount: 3%), PolicyEngine adds AUTO_CLOSE recommendation and skips this agent.",
        "human_review_required_conditions": "discrepancy > tolerance AND confidence < 0.7",
    },
    "EXCEPTION_ANALYSIS": {
        "purpose": "Analyse reconciliation exceptions, determine root causes, recommend resolution",
        "entry_conditions": "exceptions present on result after matching",
        "success_criteria": "All exceptions categorised with root cause and recommendation",
        "prohibited_actions": [],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.5,
        "allowed_recs": ["AUTO_CLOSE", "SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT", "SEND_TO_VENDOR_CLARIFICATION", "REPROCESS_EXTRACTION", "ESCALATE_TO_MANAGER"],
        "default_fallback_rec": "SEND_TO_AP_REVIEW",
        "is_pipeline": False,
        "trigger": "PolicyEngine: any exceptions present after reconciliation. Additive -- runs alongside retrieval/assist agents.",
        "dynamic_adds": "",
        "skip_conditions": "Skipped only if there are no reconciliation exceptions at all.",
        "human_review_required_conditions": "HIGH severity exceptions or ESCALATE_TO_MANAGER recommendation",
    },
    "REVIEW_ROUTING": {
        "purpose": "Determine correct review queue, team, and priority for the case",
        "entry_conditions": "exception analysis complete, routing decision needed",
        "success_criteria": "Routing decision made with high confidence based on prior analysis",
        "prohibited_actions": ["AUTO_CLOSE", "REPROCESS_EXTRACTION"],
        "requires_tool_grounding": False,
        "min_tool_calls": 0,
        "tool_failure_confidence_cap": None,
        "allowed_recs": ["SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT", "SEND_TO_VENDOR_CLARIFICATION", "ESCALATE_TO_MANAGER"],
        "default_fallback_rec": "SEND_TO_AP_REVIEW",
        "is_pipeline": False,
        "trigger": "Always appended by PolicyEngine (alongside CASE_SUMMARY) if any other agents ran. Final synthesis step.",
        "dynamic_adds": "",
        "skip_conditions": "Skipped only if the entire agent pipeline is skipped (e.g., MATCHED + high confidence).",
        "human_review_required_conditions": "always -- this agent assigns human review",
    },
    "CASE_SUMMARY": {
        "purpose": "Produce human-readable case summary for AP reviewers",
        "entry_conditions": "all preceding agents have completed for this pipeline run",
        "success_criteria": "Clear summary produced covering invoice, PO, GRN, exceptions, recommendation",
        "prohibited_actions": ["AUTO_CLOSE", "REPROCESS_EXTRACTION"],
        "requires_tool_grounding": False,
        "min_tool_calls": 0,
        "tool_failure_confidence_cap": None,
        "allowed_recs": ["SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT", "SEND_TO_VENDOR_CLARIFICATION", "ESCALATE_TO_MANAGER"],
        "default_fallback_rec": "SEND_TO_AP_REVIEW",
        "is_pipeline": False,
        "trigger": "Always appended by PolicyEngine (alongside REVIEW_ROUTING) if any other agents ran.",
        "dynamic_adds": "",
        "skip_conditions": "Skipped only if the entire agent pipeline is skipped (e.g., MATCHED + high confidence).",
        "human_review_required_conditions": "always -- summary is produced for human reviewer",
    },
    "SUPERVISOR": {
        "purpose": "Full AP lifecycle orchestrator -- owns the entire invoice processing lifecycle from document receipt to final decision in a single ReAct loop",
        "entry_conditions": "Invoice exists, reconciliation mode determined, RBAC access granted (agents.run_supervisor)",
        "success_criteria": "submit_recommendation tool called with valid recommendation type and confidence score",
        "prohibited_actions": ["Fabricate tool outputs", "Bypass RBAC/tenant restrictions", "Auto-close without checking ALL lines against tolerance"],
        "requires_tool_grounding": True,
        "min_tool_calls": 3,
        "tool_failure_confidence_cap": 0.3,
        "allowed_recs": ["AUTO_CLOSE", "SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT", "SEND_TO_VENDOR_CLARIFICATION", "REPROCESS_EXTRACTION", "ESCALATE_TO_MANAGER"],
        "default_fallback_rec": "SEND_TO_AP_REVIEW",
        "is_pipeline": False,
        "trigger": "Direct invocation via SupervisorAgent(skill_names=[...]).run(ctx). Not dispatched by PolicyEngine.",
        "dynamic_adds": "Non-linear phase progression: can backtrack from INVESTIGATE to UNDERSTAND/MATCH based on findings.",
        "skip_conditions": "N/A -- supervisor is invoked directly, not via the orchestrator pipeline.",
        "human_review_required_conditions": "confidence < 0.6 or critical exceptions found or vendor verification failed",
    },
}

# Increment this whenever the reference page template or view data changes.
# Passed into the template so the browser sees a new ETag / detects staleness.
_PAGE_VERSION = 5

# PolicyEngine dispatch rules -- shown as a table in the reference page
_POLICY_ENGINE_RULES = [
    {
        "condition": "match_status = MATCHED AND confidence >= threshold",
        "mode": "Any",
        "agents": [],
        "notes": "Pipeline skipped entirely -- no agents run",
    },
    {
        "condition": "PARTIAL_MATCH within auto-close band",
        "mode": "TWO_WAY / THREE_WAY",
        "agents": [],
        "notes": "Auto-close band: qty 5%, price 3%, amount 3% -- within band = AUTO_CLOSE, skip agents. Blocked by GRN_NOT_FOUND in 3-way or first-partial invoices (self-comparison always passes).",
    },
    {
        "condition": "NON_PO mode + low extraction confidence",
        "mode": "NON_PO only",
        "agents": ["INVOICE_UNDERSTANDING"],
        "notes": "No PO/GRN retrieval or reconciliation assist in NON_PO mode. Focus on exception analysis + vendor verification.",
    },
    {
        "condition": "NON_PO mode + normal confidence",
        "mode": "NON_PO only",
        "agents": ["EXCEPTION_ANALYSIS"],
        "notes": "Skip PO/GRN agents entirely. Route directly to exception analysis and review.",
    },
    {
        "condition": "extraction_conf < 0.70",
        "mode": "TWO_WAY / THREE_WAY",
        "agents": ["INVOICE_UNDERSTANDING"],
        "notes": "May extend plan with RECONCILIATION_ASSIST via _reflect() if own confidence < 0.5",
    },
    {
        "condition": "PO_NOT_FOUND exception",
        "mode": "TWO_WAY / THREE_WAY",
        "agents": ["PO_RETRIEVAL"],
        "notes": "May extend plan with GRN_RETRIEVAL via _reflect() if PO found in THREE_WAY case",
    },
    {
        "condition": "GRN_NOT_FOUND or GRN_PARTIAL exception",
        "mode": "THREE_WAY only",
        "agents": ["GRN_RETRIEVAL"],
        "notes": "Suppressed in TWO_WAY and NON_PO modes. GRN_NOT_FOUND also blocks auto-close in rule 1b.",
    },
    {
        "condition": "match_status = PARTIAL_MATCH (outside auto-close band)",
        "mode": "TWO_WAY / THREE_WAY",
        "agents": ["RECONCILIATION_ASSIST"],
        "notes": "First-partial invoices (no prior invoices on PO) always route here -- tolerance self-comparison bypassed.",
    },
    {
        "condition": "Any exceptions present after matching",
        "mode": "Any",
        "agents": ["EXCEPTION_ANALYSIS"],
        "notes": "Additive -- runs alongside retrieval/assist agents, not instead of them",
    },
    {
        "condition": "Any agents above were scheduled",
        "mode": "Any",
        "agents": ["REVIEW_ROUTING", "CASE_SUMMARY"],
        "notes": "Always appended as final synthesis steps",
    },
]


@login_required
def agent_reference(request):
    """Shows agents, tools, case lifecycle, prompts, and how they work."""
    agents_info = []
    for agent_type_val, agent_cls in AGENT_CLASS_REGISTRY.items():
        instance = agent_cls()
        label = AgentType(agent_type_val).label
        contract = _AGENT_CONTRACTS.get(agent_type_val, {})
        agents_info.append({
            "type": agent_type_val,
            "label": label,
            "description": agent_cls.__doc__ or "",
            "system_prompt": instance.system_prompt,
            "allowed_tools": instance.allowed_tools,
            "required_permission": AGENT_PERMISSIONS.get(agent_type_val, ""),
            # Contract / guardrail fields
            "purpose": contract.get("purpose", ""),
            "entry_conditions": contract.get("entry_conditions", ""),
            "success_criteria": contract.get("success_criteria", ""),
            "prohibited_actions": contract.get("prohibited_actions", []),
            "requires_tool_grounding": contract.get("requires_tool_grounding", False),
            "min_tool_calls": contract.get("min_tool_calls", 0),
            "tool_failure_confidence_cap": contract.get("tool_failure_confidence_cap"),
            "allowed_recs": contract.get("allowed_recs"),
            "default_fallback_rec": contract.get("default_fallback_rec", "SEND_TO_AP_REVIEW"),
            "is_pipeline": contract.get("is_pipeline", False),
            "trigger": contract.get("trigger", ""),
            "dynamic_adds": contract.get("dynamic_adds", ""),
            "skip_conditions": contract.get("skip_conditions", ""),
            "human_review_required_conditions": contract.get("human_review_required_conditions", ""),
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
    # Extraction: monolithic fallback + modular composition parts (InvoicePromptComposer)
    _extraction_prompts = [
        (
            "Invoice Extraction (Monolithic Fallback)",
            "extraction.invoice_system",
            "Monolithic fallback used by ExtractionAdapter directly. "
            "InvoicePromptComposer reads extraction.invoice_base first and falls back here if absent. "
            "After Azure Document Intelligence OCR, instructs GPT-4o to extract structured invoice data.",
            "apps/extraction/services/extraction_adapter.py + invoice_prompt_composer.py",
        ),
        (
            "Invoice Base (Modular)",
            "extraction.invoice_base",
            "Base extraction prompt versioned independently of the monolithic fallback. "
            "InvoicePromptComposer reads this first. Category and country overlays are appended after it.",
            "apps/extraction/services/invoice_prompt_composer.py (step 1 of 3)",
        ),
        (
            "Category Overlay -- Goods",
            "extraction.invoice_category_goods",
            "Appended by InvoicePromptComposer when invoice_category='goods'. "
            "Rules for HSN codes, qty/unit/rate columns, and subtotal for physical goods invoices.",
            "apps/extraction/services/invoice_prompt_composer.py (step 2 -- goods)",
        ),
        (
            "Category Overlay -- Service",
            "extraction.invoice_category_service",
            "Appended by InvoicePromptComposer when invoice_category='service'. "
            "Rules for SAC codes, fee/charge line items, and lump-sum service invoices.",
            "apps/extraction/services/invoice_prompt_composer.py (step 2 -- service)",
        ),
        (
            "Category Overlay -- Travel",
            "extraction.invoice_category_travel",
            "Appended by InvoicePromptComposer when invoice_category='travel'. "
            "Rules for booking invoice numbers vs cart refs, base fare vs taxes, and traveller name.",
            "apps/extraction/services/invoice_prompt_composer.py (step 2 -- travel)",
        ),
        (
            "Country Overlay -- India GST",
            "extraction.country_india_gst",
            "Appended for IN/GST invoices. Rules for GSTIN format, IRN (not the invoice number), "
            "CGST+SGST vs IGST breakdown, HSN/SAC codes, and standard GST rate tiers.",
            "apps/extraction/services/invoice_prompt_composer.py (step 3 -- IN/GST)",
        ),
        (
            "Country Overlay -- Generic VAT",
            "extraction.country_generic_vat",
            "Appended for AE/VAT, SA/ZATCA, GB/VAT, DE/VAT invoices. "
            "Rules for VAT registration number, VAT rate, and net vs gross amount extraction.",
            "apps/extraction/services/invoice_prompt_composer.py (step 3 -- VAT regimes)",
        ),
    ]
    prompts = []
    for name, slug, description, used_in in _extraction_prompts:
        text = PromptRegistry.get_or_default(slug)
        if text:
            prompts.append({
                "name": name,
                "category": "Extraction",
                "icon": "bi-file-earmark-text",
                "color": "primary",
                "description": description,
                "used_in": used_in,
                "model": "Azure OpenAI GPT-4o (temperature: 0.0)",
                "prompt_text": text,
            })

    # ReAct reasoning agents only -- exclude INVOICE_EXTRACTION because it is a
    # single-shot pipeline agent (no tools, no ReAct loop) whose prompt content is
    # already shown in the Extraction group above.
    _PIPELINE_AGENT_TYPES = {AgentType.INVOICE_EXTRACTION.value}
    for agent in agents_info:
        if agent["type"] in _PIPELINE_AGENT_TYPES:
            continue
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
         "Analyses exceptions/validation issues -- determines root cause, severity, and remediation path."),
        ("Non-PO Validation", "case.non_po_validation",
         "Reasons over 9 deterministic check results for invoices without a Purchase Order."),
        ("Reviewer Copilot", "case.reviewer_copilot",
         "Advisory assistant for human reviewers -- answers case questions using tools but never commits actions."),
    ]
    for name, slug, description in case_prompts:
        text = PromptRegistry.get_or_default(slug)
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
            "description": "Invoice PDF uploaded via the Documents UI or API. A DocumentUpload record is created and the file is stored in Azure Blob Storage.",
        },
        {
            "step": 2,
            "name": "OCR (Azure Document Intelligence)",
            "icon": "bi-eye",
            "color": "info",
            "performer": "Azure Document Intelligence (PyPDF2 fallback)",
            "description": "The uploaded PDF is sent to Azure Document Intelligence for OCR. Raw text and layout data are returned. PyPDF2 fallback for text-based PDFs. QR code detection for e-invoices (IN).",
        },
        {
            "step": 3,
            "name": "Category Classification",
            "icon": "bi-tags",
            "color": "info",
            "performer": "System (CategoryClassifier)",
            "description": "Classify the invoice as goods, service, or travel based on OCR text analysis. Determines which category overlay prompt to apply in step 5.",
        },
        {
            "step": 4,
            "name": "Prompt Composition",
            "icon": "bi-puzzle",
            "color": "info",
            "performer": "System (InvoicePromptComposer)",
            "description": "Modular 3-step prompt assembly: (1) base extraction prompt, (2) category overlay (goods/service/travel), (3) country overlay (India GST / generic VAT). Falls back to monolithic prompt if base is absent.",
        },
        {
            "step": 5,
            "name": "LLM Extraction",
            "icon": "bi-robot",
            "color": "primary",
            "performer": "Invoice Extraction Agent (GPT-4o, temp=0)",
            "description": "The composed prompt + OCR text are sent to GPT-4o with response_format=json_object and temperature=0. Extracts structured fields: invoice number, date, vendor, line items, totals, tax details. Full AgentRun traceability recorded.",
        },
        {
            "step": 6,
            "name": "Response Repair",
            "icon": "bi-wrench",
            "color": "warning",
            "performer": "System (ResponseRepairService)",
            "description": "5 deterministic pre-parser rules fix common LLM JSON issues: strip markdown fences, fix trailing commas, repair truncated JSON, unwrap nested objects, normalize line_items array. 25 dedicated tests.",
        },
        {
            "step": 7,
            "name": "Parse",
            "icon": "bi-braces",
            "color": "success",
            "performer": "System (ParserService)",
            "description": "Parse the repaired JSON into structured domain objects. Map raw fields to the Invoice schema. Extract line items into InvoiceLineItem objects.",
        },
        {
            "step": 8,
            "name": "Normalize",
            "icon": "bi-funnel",
            "color": "success",
            "performer": "System (NormalizationService)",
            "description": "Normalize dates (dateparser), amounts (Decimal), currency codes (ISO 4217), PO numbers (strip prefixes/whitespace). Standardize vendor names for matching.",
        },
        {
            "step": 9,
            "name": "Validate",
            "icon": "bi-check2-square",
            "color": "warning",
            "performer": "System (ValidationService)",
            "description": "Field validation: required fields present, amount consistency (line totals = subtotal + tax = grand total), date reasonability, decision codes generated for each validation outcome.",
        },
        {
            "step": 10,
            "name": "Duplicate Detection & Persistence",
            "icon": "bi-files",
            "color": "warning",
            "performer": "System (DuplicateDetectionService + PersistenceService)",
            "description": "Check for duplicate invoice number + vendor + amount within 90-day window. Compute field-level confidence scores. Create Invoice + InvoiceLineItem records. Store extraction_raw_json for audit.",
        },
        {
            "step": 11,
            "name": "Approval Gate",
            "icon": "bi-shield-check",
            "color": "dark",
            "performer": "System (ApprovalService)",
            "description": f"Auto-approve if confidence >= {confidence_threshold}% and no critical validation failures. Otherwise route to human review (PENDING_APPROVAL). Credit system tracks extraction quality per vendor.",
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

    # ---- Supervisor Agent ----
    supervisor_skills = []
    supervisor_tool_list = []
    supervisor_phases = [
        {
            "name": "UNDERSTAND",
            "skill": "invoice_extraction",
            "icon": "bi-eye",
            "color": "info",
            "description": "Extract structured data from the invoice document via OCR, classification, and field extraction.",
            "tools": ["get_ocr_text", "classify_document", "extract_invoice_fields", "re_extract_field"],
        },
        {
            "name": "VALIDATE",
            "skill": "ap_validation",
            "icon": "bi-check2-square",
            "color": "warning",
            "description": "Validate extraction quality, detect duplicates, verify vendor identity by tax ID, and check tax computation.",
            "tools": ["validate_extraction", "repair_extraction", "check_duplicate", "verify_vendor", "verify_tax_computation"],
        },
        {
            "name": "MATCH",
            "skill": "ap_3way_matching",
            "icon": "bi-arrow-left-right",
            "color": "primary",
            "description": "Run deterministic header, line, and GRN matching against PO data using configurable tolerance thresholds.",
            "tools": ["po_lookup", "run_header_match", "run_line_match", "grn_lookup", "run_grn_match", "get_tolerance_config"],
        },
        {
            "name": "INVESTIGATE",
            "skill": "ap_investigation",
            "icon": "bi-search",
            "color": "danger",
            "description": "Recover from matching failures via re-extraction, PO/GRN retrieval delegation, vendor history, and case history.",
            "tools": ["re_extract_field", "invoke_po_retrieval_agent", "invoke_grn_retrieval_agent", "get_vendor_history", "get_case_history", "invoice_details"],
        },
        {
            "name": "DECIDE",
            "skill": "ap_review_routing",
            "icon": "bi-signpost-split",
            "color": "success",
            "description": "Persist results, create cases, submit recommendation, route to review queues, auto-close, or escalate.",
            "tools": ["persist_invoice", "create_case", "submit_recommendation", "assign_reviewer", "generate_case_summary", "auto_close_case", "escalate_case", "exception_list", "reconciliation_summary"],
        },
    ]

    try:
        from apps.agents.skills.base import SkillRegistry
        from apps.agents.services.supervisor_agent import SUPERVISOR_MAX_TOOL_ROUNDS, _ensure_skills_loaded
        _ensure_skills_loaded()
        for skill_name in ["invoice_extraction", "ap_validation", "ap_3way_matching", "ap_investigation", "ap_review_routing"]:
            skill = SkillRegistry.get(skill_name)
            if skill:
                supervisor_skills.append({
                    "name": skill.name,
                    "description": skill.description,
                    "tools": skill.tools,
                    "tool_count": len(skill.tools),
                    "decision_hints": skill.decision_hints,
                    "prompt_extension": skill.prompt_extension,
                })
        supervisor_tool_list = SkillRegistry.all_tools(
            ["invoice_extraction", "ap_validation", "ap_3way_matching", "ap_investigation", "ap_review_routing"]
        )
    except Exception:
        SUPERVISOR_MAX_TOOL_ROUNDS = 15

    supervisor_erp_routable = ["po_lookup", "grn_lookup", "vendor_search", "verify_vendor", "check_duplicate"]

    supervisor_decision_rules = [
        {"rule": "Must call submit_recommendation", "enforcement": "Code (interpret_response)", "icon": "bi-exclamation-triangle", "color": "danger"},
        {"rule": "Never auto-close without checking ALL lines against tolerance", "enforcement": "Prompt instruction", "icon": "bi-x-octagon", "color": "danger"},
        {"rule": "Verify vendor by tax ID, not name alone", "enforcement": "Prompt + verify_vendor tool", "icon": "bi-person-check", "color": "warning"},
        {"rule": "Attempt re-extraction before PO_NOT_FOUND escalation", "enforcement": "Prompt instruction", "icon": "bi-arrow-repeat", "color": "info"},
        {"rule": "Never hardcode tolerance values", "enforcement": "Prompt + get_tolerance_config tool", "icon": "bi-sliders", "color": "primary"},
        {"rule": "No fabricated tool outputs", "enforcement": "Prompt instruction", "icon": "bi-shield-check", "color": "secondary"},
        {"rule": "Max 15 tool rounds per session", "enforcement": "Code (MAX_TOOL_ROUNDS patch)", "icon": "bi-speedometer2", "color": "dark"},
    ]

    supervisor_confidence_routing = [
        {"threshold": ">= 0.9", "condition": "All lines match within tolerance", "recommendation": "AUTO_CLOSE", "color": "success"},
        {"threshold": ">= 0.6", "condition": "Some deviations exist", "recommendation": "SEND_TO_AP_REVIEW", "color": "warning"},
        {"threshold": "< 0.6", "condition": "Critical exceptions found", "recommendation": "ESCALATE_TO_MANAGER", "color": "danger"},
    ]

    # ---- Langfuse Evaluation Score Catalog ----
    langfuse_score_domains = [
        {
            "domain": "Extraction",
            "icon": "bi-file-earmark-text",
            "color": "primary",
            "count": 21,
            "examples": [
                "extraction_success", "extraction_confidence", "extraction_is_valid",
                "extraction_is_duplicate", "response_was_repaired", "qr_detected",
                "decision_code_count", "weakest_critical_field_score", "ocr_char_count",
                "extraction_auto_approve_confidence", "extraction_approval_decision",
                "extraction_corrections_count", "bulk_job_success_rate",
            ],
        },
        {
            "domain": "Reconciliation",
            "icon": "bi-arrow-left-right",
            "color": "info",
            "count": 37,
            "examples": [
                "recon_final_success", "recon_final_status_matched", "recon_po_found",
                "recon_grn_found", "recon_auto_close_eligible", "recon_exception_count_final",
                "recon_header_match_ratio", "recon_line_match_ratio", "recon_grn_match_ratio",
                "recon_po_lookup_authoritative", "recon_match_status_correct",
                "recon_auto_close_correct", "recon_review_outcome",
            ],
        },
        {
            "domain": "Agents",
            "icon": "bi-robot",
            "color": "success",
            "count": 12,
            "examples": [
                "agent_pipeline_final_confidence", "agent_pipeline_recommendation_present",
                "agent_pipeline_auto_close_candidate", "agent_confidence",
                "agent_tool_success_rate", "tool_call_success",
                "agent_feedback_triggered_rerun", "agent_feedback_improved_outcome",
            ],
        },
        {
            "domain": "Case / Review",
            "icon": "bi-briefcase",
            "color": "warning",
            "count": 20,
            "examples": [
                "case_processing_success", "case_closed", "case_auto_closed",
                "case_stages_executed", "case_routed_to_review",
                "review_approved", "review_rejected", "review_had_corrections",
                "review_fields_corrected_count", "case_non_po_risk_score",
            ],
        },
        {
            "domain": "Posting",
            "icon": "bi-send",
            "color": "dark",
            "count": 17,
            "examples": [
                "posting_confidence", "posting_touchless", "posting_ready_to_submit",
                "posting_vendor_mapping_success", "posting_item_mapping_success_rate",
                "posting_validation_error_count", "posting_payload_build_success",
            ],
        },
        {
            "domain": "ERP",
            "icon": "bi-cloud",
            "color": "secondary",
            "count": 23,
            "examples": [
                "erp_resolution_success", "erp_resolution_fresh", "erp_cache_hit",
                "erp_live_lookup_success", "erp_live_lookup_timeout",
                "erp_db_fallback_used", "erp_submission_success",
                "erp_duplicate_found", "erp_retry_success",
            ],
        },
        {
            "domain": "Cross-Cutting / Decision Quality",
            "icon": "bi-intersect",
            "color": "danger",
            "count": 13,
            "examples": [
                "latency_ok", "fallback_used", "rbac_guardrail", "rbac_data_scope",
                "copilot_session_length", "decision_confidence_alignment",
                "review_required_correctly_triggered", "stale_data_accepted",
            ],
        },
        {
            "domain": "Supervisor Agent",
            "icon": "bi-diagram-3",
            "color": "purple",
            "count": 6,
            "examples": [
                "supervisor_confidence", "supervisor_recommendation_present",
                "supervisor_tools_used_count", "supervisor_recovery_used",
                "supervisor_auto_close_candidate",
            ],
        },
        {
            "domain": "System Agents",
            "icon": "bi-gear",
            "color": "secondary",
            "count": 7,
            "examples": [
                "system_agent_success", "system_agent_decision_count",
                "system_review_routing_success", "system_case_summary_success",
                "system_bulk_intake_success", "system_posting_preparation_success",
            ],
        },
    ]

    langfuse_latency_thresholds = [
        {"operation": "OCR (Document Intelligence)", "threshold_ms": 30000, "threshold_label": "30s"},
        {"operation": "LLM Generation", "threshold_ms": 20000, "threshold_label": "20s"},
        {"operation": "Agent Tool Call", "threshold_ms": 10000, "threshold_label": "10s"},
        {"operation": "ERP API Call", "threshold_ms": 5000, "threshold_label": "5s"},
        {"operation": "Reconciliation Sub-Stage", "threshold_ms": 5000, "threshold_label": "5s"},
        {"operation": "Posting Sub-Stage", "threshold_ms": 5000, "threshold_label": "5s"},
        {"operation": "Database Query / Fallback", "threshold_ms": 2000, "threshold_label": "2s"},
    ]

    langfuse_root_traces = [
        "extraction_pipeline", "reconciliation_pipeline", "agent_pipeline",
        "case_pipeline", "posting_pipeline", "erp_submission_pipeline",
        "review_workflow", "copilot_session", "supervisor_pipeline",
        "system_agent", "system_bulk_intake", "system_case_intake",
        "system_posting_preparation",
    ]

    # ---- Celery Task Inventory ----
    celery_tasks = [
        {
            "task": "extraction.tasks.run_extraction_task",
            "trigger": "On-demand (document upload)",
            "max_retries": "3",
            "retry_delay": "60s",
            "acks_late": "No",
            "description": "11-stage extraction pipeline: OCR, classify, compose, LLM, repair, parse, normalize, validate, duplicate, persist, approve.",
        },
        {
            "task": "reconciliation.tasks.run_reconciliation_task",
            "trigger": "On-demand (post-extraction approval)",
            "max_retries": "5",
            "retry_delay": "60s",
            "acks_late": "No",
            "description": "Batch reconciliation run: mode resolution, PO/GRN lookup, header/line/GRN matching, tolerance, classification, exception building.",
        },
        {
            "task": "reconciliation.tasks.reconcile_single_invoice_task",
            "trigger": "On-demand (convenience wrapper)",
            "max_retries": "5",
            "retry_delay": "60s",
            "acks_late": "No",
            "description": "Single-invoice convenience wrapper around run_reconciliation_task.",
        },
        {
            "task": "agents.tasks.run_agent_pipeline_task",
            "trigger": "Auto-chained from reconciliation (non-MATCHED results)",
            "max_retries": "1",
            "retry_delay": "30s",
            "acks_late": "No",
            "description": "Agent orchestrator pipeline: PolicyEngine plan, execute agents in sequence, feedback loop for PO recovery.",
        },
        {
            "task": "cases.tasks.process_case_task",
            "trigger": "On-demand (case creation)",
            "max_retries": "3",
            "retry_delay": "30s",
            "acks_late": "Yes",
            "description": "Case orchestrator: drives the APCase through its processing path stages via CaseOrchestrator.run().",
        },
        {
            "task": "cases.tasks.reprocess_case_from_stage_task",
            "trigger": "On-demand (reprocessing request)",
            "max_retries": "2",
            "retry_delay": "10s",
            "acks_late": "Yes",
            "description": "Resume case processing from a specific stage (e.g., after human review or extraction correction).",
        },
        {
            "task": "core_eval.tasks.process_approved_learning_actions",
            "trigger": "Celery Beat (every 30 min)",
            "max_retries": "1",
            "retry_delay": "—",
            "acks_late": "No",
            "description": "Process approved LearningAction records from the core_eval framework. Only scheduled Beat task.",
        },
    ]

    celery_task_chain = {
        "description": "reconciliation_task -> (auto-chain) -> run_agent_pipeline_task for non-MATCHED results",
        "actor_propagation": "actor_user_id passed through all Celery task signatures via .as_celery_headers()",
    }

    # ---- Review Action Types ----
    review_action_types = [
        {"action": "APPROVE", "icon": "bi-check-circle", "color": "success", "description": "Accept the reconciliation result as-is. Invoice proceeds to posting."},
        {"action": "APPROVE_WITH_FIXES", "icon": "bi-pencil-square", "color": "primary", "description": "Accept with field corrections. Reviewer-corrected values are persisted and a LearningSignal is emitted."},
        {"action": "REJECT", "icon": "bi-x-circle", "color": "danger", "description": "Reject the invoice. Case moves to REJECTED terminal state."},
        {"action": "NEEDS_INFO", "icon": "bi-question-circle", "color": "warning", "description": "Request additional information. Case remains in IN_REVIEW, pending vendor clarification."},
        {"action": "ESCALATE", "icon": "bi-arrow-up-circle", "color": "dark", "description": "Escalate to Finance Manager. Case moves to ESCALATED state with priority bump."},
    ]

    response = render(request, "agents/reference.html", {
        "page_version": _PAGE_VERSION,
        "agents_info": agents_info,
        "react_agent_count": len(agents_info) - len(_PIPELINE_AGENT_TYPES),
        "policy_engine_rules": _POLICY_ENGINE_RULES,
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
        # Supervisor Agent
        "supervisor_phases": supervisor_phases,
        "supervisor_skills": supervisor_skills,
        "supervisor_tool_list": supervisor_tool_list,
        "supervisor_tool_count": len(supervisor_tool_list),
        "supervisor_max_tool_rounds": SUPERVISOR_MAX_TOOL_ROUNDS,
        "supervisor_erp_routable": supervisor_erp_routable,
        "supervisor_decision_rules": supervisor_decision_rules,
        "supervisor_confidence_routing": supervisor_confidence_routing,
        # Langfuse Evaluation Score Catalog
        "langfuse_score_domains": langfuse_score_domains,
        "langfuse_total_scores": sum(d["count"] for d in langfuse_score_domains),
        "langfuse_latency_thresholds": langfuse_latency_thresholds,
        "langfuse_root_traces": langfuse_root_traces,
        # Celery Tasks
        "celery_tasks": celery_tasks,
        "celery_task_chain": celery_task_chain,
        # Review Actions
        "review_action_types": review_action_types,
    })
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# Agent Runs -- browsable list + detail
# ---------------------------------------------------------------------------

@login_required
@permission_required_code("agents.view")
def agent_runs_list(request):
    """Browsable agent run log with filtering."""
    tenant = require_tenant(request)
    qs = AgentRun.objects.select_related(
        "reconciliation_result", "reconciliation_result__invoice",
        "agent_definition", "document_upload",
    ).order_by("-created_at")
    if tenant is not None:
        qs = qs.filter(tenant=tenant)

    # ---- Filters ----
    agent_type = request.GET.get("agent_type", "").strip()
    status = request.GET.get("status", "").strip()
    trace_id = request.GET.get("trace_id", "").strip()
    role = request.GET.get("role", "").strip()
    model_used = request.GET.get("model_used", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    min_conf = request.GET.get("min_confidence", "").strip()
    max_conf = request.GET.get("max_confidence", "").strip()
    invoice_number = request.GET.get("invoice_number", "").strip()

    if agent_type:
        qs = qs.filter(agent_type=agent_type)
    if status:
        qs = qs.filter(status=status)
    if trace_id:
        qs = qs.filter(trace_id=trace_id)
    if role:
        qs = qs.filter(actor_primary_role=role)
    if model_used:
        qs = qs.filter(llm_model_used=model_used)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if min_conf:
        try:
            qs = qs.filter(confidence__gte=float(min_conf) / 100.0)
        except (ValueError, TypeError):
            pass
    if max_conf:
        try:
            qs = qs.filter(confidence__lte=float(max_conf) / 100.0)
        except (ValueError, TypeError):
            pass
    if invoice_number:
        qs = qs.filter(
            reconciliation_result__invoice__invoice_number__icontains=invoice_number,
        )

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Annotate recovery trigger codes for template access
    # (Django templates cannot access dict keys starting with underscore)
    for run in page_obj:
        payload = run.input_payload or {}
        meta = payload.get("_recovery_meta") or {}
        run.recovery_trigger_codes = meta.get("trigger_codes", [])

    # Resolve invoice for runs that lack reconciliation_result (e.g. extraction/case runs)
    upload_ids = [
        r.document_upload_id for r in page_obj
        if r.document_upload_id and not (r.reconciliation_result and r.reconciliation_result.invoice)
    ]
    _upload_invoice_map: dict = {}
    if upload_ids:
        try:
            from apps.extraction.models import ExtractionResult
            for ext in (
                ExtractionResult.objects
                .filter(document_upload_id__in=upload_ids)
                .select_related("document_upload")
                .order_by("document_upload_id", "-created_at")
            ):
                _inv = ext.invoice
                if _inv:
                    _upload_invoice_map.setdefault(ext.document_upload_id, _inv)
        except Exception:
            pass
    # Pre-load invoice lookups from input_payload for runs without recon_result/upload
    _payload_invoice_map = {}
    _payload_inv_ids = set()
    for run in page_obj:
        if not (run.reconciliation_result and run.reconciliation_result.invoice) and not run.document_upload_id:
            _inv_id = (run.input_payload or {}).get("invoice_id")
            if _inv_id:
                _payload_inv_ids.add(_inv_id)
    if _payload_inv_ids:
        try:
            from apps.documents.models import Invoice
            for inv in Invoice.objects.filter(pk__in=_payload_inv_ids).select_related("vendor"):
                _payload_invoice_map[inv.pk] = inv
        except Exception:
            pass

    for run in page_obj:
        if run.reconciliation_result and run.reconciliation_result.invoice:
            run.resolved_invoice = run.reconciliation_result.invoice
        elif run.document_upload_id:
            run.resolved_invoice = _upload_invoice_map.get(run.document_upload_id)
        else:
            _inv_id = (run.input_payload or {}).get("invoice_id")
            run.resolved_invoice = _payload_invoice_map.get(_inv_id) if _inv_id else None

    # Dropdown choices
    agent_type_choices = AgentType.choices
    status_choices = AgentRunStatus.choices
    roles = (
        AgentRun.objects.exclude(actor_primary_role="")
        .order_by("actor_primary_role")
        .values_list("actor_primary_role", flat=True)
        .distinct()
    )
    models_used = (
        AgentRun.objects.exclude(llm_model_used="")
        .order_by("llm_model_used")
        .values_list("llm_model_used", flat=True)
        .distinct()
    )

    # KPI summary (scoped to *filtered* queryset for relevance)
    from django.db.models import Avg, Count, Q, Sum

    kpi_qs = qs  # re-use filtered queryset
    total_count = paginator.count
    completed_count = kpi_qs.filter(status=AgentRunStatus.COMPLETED).count()
    failed_count = kpi_qs.filter(status=AgentRunStatus.FAILED).count()
    avg_confidence = kpi_qs.filter(confidence__isnull=False).aggregate(
        avg=Avg("confidence"),
    )["avg"]
    total_tokens = kpi_qs.aggregate(tokens=Sum("total_tokens"))["tokens"] or 0

    return render(request, "agents/agent_runs_list.html", {
        "page_obj": page_obj,
        "runs": page_obj,
        # Filter choices
        "agent_type_choices": agent_type_choices,
        "status_choices": status_choices,
        "roles": roles,
        "models_used": models_used,
        # Current filter values
        "current_agent_type": agent_type,
        "current_status": status,
        "current_trace_id": trace_id,
        "current_role": role,
        "current_model_used": model_used,
        "current_date_from": date_from,
        "current_date_to": date_to,
        "current_min_confidence": min_conf,
        "current_max_confidence": max_conf,
        "current_invoice_number": invoice_number,
        # KPIs
        "total_count": total_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "avg_confidence": avg_confidence,
        "total_tokens": total_tokens,
    })


@login_required
@permission_required_code("agents.view")
def agent_run_detail(request, pk):
    """Detail view for a single agent run with steps, messages, decisions, and recommendations."""
    run = get_object_or_404(
        AgentRun.objects.select_related(
            "reconciliation_result", "reconciliation_result__invoice",
            "agent_definition", "document_upload",
        ),
        pk=pk,
    )
    steps = run.steps.order_by("step_number")
    agent_messages = run.messages.order_by("message_index")
    decisions = run.decisions.order_by("-created_at")
    recommendations = run.recommendations.select_related(
        "reconciliation_result", "invoice",
    ).order_by("-created_at")

    # Resolve invoice: via reconciliation_result, or via document_upload -> extraction -> invoice,
    # or via input_payload.invoice_id (system agents store it there)
    linked_invoice = None
    if run.reconciliation_result and run.reconciliation_result.invoice:
        linked_invoice = run.reconciliation_result.invoice
    elif run.document_upload:
        try:
            from apps.extraction.models import ExtractionResult
            ext = ExtractionResult.objects.filter(
                document_upload=run.document_upload,
            ).select_related("invoice").order_by("-created_at").first()
            if ext and ext.invoice:
                linked_invoice = ext.invoice
        except Exception:
            pass
    if not linked_invoice:
        _inv_id = (run.input_payload or {}).get("invoice_id")
        if _inv_id:
            try:
                from apps.documents.models import Invoice
                linked_invoice = Invoice.objects.select_related("vendor").get(pk=_inv_id)
            except Exception:
                pass

    # ── Eval field outcomes ──
    eval_field_outcomes = []
    try:
        from apps.core_eval.models import EvalRun
        _er = (
            EvalRun.objects.filter(
                app_module="agents",
                entity_type="AgentRun",
                entity_id=str(run.pk),
            )
            .prefetch_related("field_outcomes")
            .first()
        )
        if _er:
            eval_field_outcomes = list(_er.field_outcomes.all())
    except Exception:
        pass

    return render(request, "agents/agent_run_detail.html", {
        "run": run,
        "steps": steps,
        "agent_messages": agent_messages,
        "decisions": decisions,
        "recommendations": recommendations,
        "linked_invoice": linked_invoice,
        "eval_field_outcomes": eval_field_outcomes,
    })


@login_required
@permission_required_code("eval.manage")
def agent_run_eval_correct(request, pk):
    """Record a human ground-truth correction on an EvalFieldOutcome for an agent run.

    POST params:
        field_outcome_id  -- PK of the EvalFieldOutcome
        ground_truth      -- correct value
        new_status        -- CORRECT / INCORRECT / MISSING / EXTRA / SKIPPED
    """
    from django.http import JsonResponse
    from apps.core_eval.models import EvalFieldOutcome

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    tenant = require_tenant(request)

    run = get_object_or_404(AgentRun, pk=pk)

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

    # Verify this outcome belongs to this agent run
    if fo.eval_run.entity_id != str(run.pk) or fo.eval_run.entity_type != "AgentRun":
        return JsonResponse({"error": "Outcome does not belong to this agent run"}, status=403)

    update_fields = ["updated_at"]
    if ground_truth:
        fo.ground_truth_value = ground_truth
        update_fields.append("ground_truth_value")
    if new_status:
        fo.status = new_status
        update_fields.append("status")
    fo.save(update_fields=update_fields)

    # Record learning signal
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
                "agent_run_id": run.pk,
            },
            tenant=tenant,
        )
    except Exception:
        pass

    return JsonResponse({
        "ok": True,
        "field_outcome_id": fo.pk,
        "ground_truth_value": fo.ground_truth_value,
        "status": fo.status,
    })
