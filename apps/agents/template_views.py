"""Agent template views -- reference pages and agent run explorer."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from apps.core.prompt_registry import PromptRegistry
from apps.core.permissions import permission_required_code
from apps.core.tenant_utils import TenantQuerysetMixin, require_tenant

from apps.agents.models import AgentRun, DecisionLog, AgentRecommendation, AgentDefinition
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
_PAGE_VERSION = 4

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
    """Global platform reference page (admin / overall view)."""
    return _render_agent_reference_page(request, "agents/reference.html")


@login_required
def procurement_agent_reference(request):
    """Procurement-specific platform reference page."""
    return _render_agent_reference_page(request, "procurement/procurement_reference.html")


def _render_agent_reference_page(request, template_name):
    """Build and render agent/platform reference context."""
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

    # ---- Procurement Agents (HVAC domain -- separate from core reconciliation agents) ----
    procurement_agents = [
        {
            "name": "PerplexityMarketResearchAnalystAgent",
            "module": "apps/procurement/agents/Perplexity_Market_Research_Analyst_Agent.py",
            "flow": "Flow A",
            "flow_color": "primary",
            "icon": "bi-search-heart",
            "model": "Perplexity sonar-pro (live web search)",
            "entry_point": "agent.run(proc_request, generated_by=user)",
            "type": "LLM + Live Web Search",
            "type_color": "primary",
            "description": (
                "Primary market intelligence agent. Queries the Perplexity API (sonar-pro model) "
                "to find real, purchasable HVAC products with specifications, pricing, and verified "
                "purchase URLs. Returns structured product suggestions with source citations."
            ),
            "inputs": "ProcurementRequest (system_code, title, description, country, city, currency)",
            "outputs": "system_code, system_name, rephrased_query, ai_summary, market_context, suggestions (list), perplexity_citations (list)",
            "fallback": "FallbackWebscraperAgent (auto-triggered on any Perplexity failure or empty result)",
            "requires_key": "PERPLEXITY_API_KEY",
            "key_rules": [
                "Every suggestion must be a REAL product from live Perplexity web search",
                "citation_url must come EXACTLY from Perplexity citations[] array -- never invented",
                "5 to 7 suggestions from DIFFERENT manufacturers per run",
                "Purchase intent: buyer clicks link and lands on exact product detail page",
            ],
        },
        {
            "name": "FallbackWebscraperAgent",
            "module": "apps/procurement/agents/Fallback_Webscraper_Agent.py",
            "flow": "Flow A",
            "flow_color": "warning",
            "icon": "bi-globe2",
            "model": "Azure OpenAI GPT-4o + Playwright (headless Chromium)",
            "entry_point": "agent.run(proc_request, generated_by=user)",
            "type": "LLM + Headless Browser Scraping",
            "type_color": "warning",
            "description": (
                "Automatic fallback when Perplexity API is unavailable or fails. Step 1: "
                "Azure OpenAI suggests 6 specific commercial/vendor URLs to visit. Step 2: "
                "Playwright headless Chromium scrapes each page (30s timeout, 6000 char cap). "
                "Step 3: Azure OpenAI parses scraped text into the same product suggestion dict "
                "as PerplexityMarketResearchAnalystAgent. Returns identical output shape."
            ),
            "inputs": "ProcurementRequest (same as Perplexity agent)",
            "outputs": "Same dict as PerplexityMarketResearchAnalystAgent (system_code, suggestions, perplexity_citations)",
            "fallback": "None -- this is the final fallback. Returns partial results if scraping fails.",
            "requires_key": "AZURE_OPENAI_API_KEY + playwright installed (playwright install chromium)",
            "key_rules": [
                "playwright install chromium required -- pip install playwright && playwright install chromium",
                "Scraped page text capped at 6000 chars per site (Azure OAI cost control)",
                "GPT-4o selects 6 specific LISTING page URLs (not homepages)",
                "Scraped URLs stored in perplexity_citations for auditability",
            ],
        },
        {
            "name": "AzureDIExtractorAgent",
            "module": "apps/procurement/agents/Azure_Document_Intelligence_Extractor_Agent.py",
            "flow": "Both",
            "flow_color": "info",
            "icon": "bi-file-earmark-medical",
            "model": "Azure Document Intelligence prebuilt-layout + Azure OpenAI GPT-4o (ReAct)",
            "entry_point": "AzureDIExtractorAgent.extract(file_path=...) or .extract(file_bytes=..., mime_type=...)",
            "type": "Deterministic + ReAct Tool-Calling",
            "type_color": "info",
            "description": (
                "Universal document extractor. Treats Azure DI as an OpenAI TOOL: LLM is invoked "
                "first with a ToolSpec for extract_document_text. When the model issues that tool "
                "call, the agent runs the real DI API and returns OCR text + tables + key-value "
                "pairs back to the LLM as a tool role message. LLM synthesises final structured "
                "JSON. Works for ANY document type: invoice, quotation, PO, GRN, contract, "
                "proforma, delivery note, HVAC request form."
            ),
            "inputs": "file_path (str) OR file_bytes (bytes) + mime_type",
            "outputs": "success, doc_type, confidence, header (dict), line_items (list), commercial_terms, raw_ocr_text, tables, key_value_pairs, engine, duration_ms, error",
            "fallback": "Returns engine='error' with structured error dict -- never raises",
            "requires_key": "AZURE_DI_ENDPOINT + AZURE_DI_KEY + AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT",
            "key_rules": [
                "Supported formats: PDF, JPEG, JPG, PNG, BMP, TIFF, HEIF, DOCX, XLSX, PPTX",
                "Doc type hints registry drives LLM extraction schema per document type",
                "ReAct loop: LLM calls extract_document_text tool -> DI runs -> LLM synthesises",
                "HVAC request form support: extracts 20+ HVAC-specific fields incl. area_sqft, ambient_temp_max",
            ],
        },
        {
            "name": "HVACRecommendationAgent",
            "module": "apps/procurement/agents/hvac_recommendation_agent.py",
            "flow": "Flow A",
            "flow_color": "success",
            "icon": "bi-buildings",
            "model": "Azure OpenAI GPT-4o",
            "entry_point": "agent.recommend(attrs, no_match_context, procurement_request_pk) OR agent.explain(attrs, rule_result)",
            "type": "LLM Reasoning (dual entry points)",
            "type_color": "success",
            "description": (
                "AI-powered HVAC recommendation engine with two distinct paths. recommend(): "
                "full AI system selection called when HVACRulesEngine returns confident=False "
                "(no matching rule). Uses project attributes, available DB system types, similar "
                "store profiles, and market intelligence data. explain(): lightweight tradeoff "
                "commentary after a DB rule matched -- produces procurement-facing reasoning."
            ),
            "inputs": "attrs (dict of store parameters), no_match_context (why rules failed), rule_result (for explain path)",
            "outputs": "system_type_code, system_name, capacity_band, confidence, reasoning, top_drivers, constraints, alternate_option, standards",
            "fallback": "Returns structured fallback dict with is_fallback=True if LLM call fails",
            "requires_key": "AZURE_OPENAI_API_KEY (via LLMClient)",
            "key_rules": [
                "GCC-specific: UAE/KSA/Qatar/Kuwait/Bahrain/Oman climate and standards context",
                "recommend() path: full AI reasoning with 20+ years HVAC expertise prompt",
                "explain() path: 3-5 sentence trade-off narrative for display in workspace UI",
                "Falls back gracefully if LLM unavailable -- never crashes Flow A",
            ],
        },
        {
            "name": "ComplianceAgent",
            "module": "apps/procurement/agents/compliance_agent.py",
            "flow": "Flow A",
            "flow_color": "danger",
            "icon": "bi-shield-check",
            "model": "Azure OpenAI GPT-4o",
            "entry_point": "ComplianceAgent.check(request, context)",
            "type": "LLM Compliance Check",
            "type_color": "danger",
            "description": (
                "AI-augmented compliance checking. Invoked when rule-based ComplianceService "
                "needs extended analysis (domain-specific regulations, complex constraint sets). "
                "Validates recommended HVAC system against geography-specific standards."
            ),
            "inputs": "ProcurementRequest (domain_code, geography_country), context dict (recommendation result)",
            "outputs": "status (PASS/FAIL/PARTIAL/NOT_CHECKED), rules_checked (list), violations (list), recommendations (list)",
            "fallback": "Returns NOT_CHECKED status with error note if LLM unavailable",
            "requires_key": "AZURE_OPENAI_API_KEY (via LLMClient)",
            "key_rules": [
                "ASHRAE 55/62.1/90.1, ISO 16813/50001, UAE MoIAT, SASO, CIBSE applied",
                "Invoked only for augmented analysis -- standard checks use ComplianceService",
                "Returns NOT_CHECKED on error -- never blocks Flow A pipeline",
            ],
        },
        {
            "name": "RequestExtractionAgent",
            "module": "apps/procurement/agents/request_extraction_agent.py",
            "flow": "Both",
            "flow_color": "secondary",
            "icon": "bi-file-earmark-code",
            "model": "Azure OpenAI GPT-4o",
            "entry_point": "RequestExtractionAgent.extract(ocr_text, source_document_type, domain_hint)",
            "type": "Lightweight LLM Extraction",
            "type_color": "secondary",
            "description": (
                "Lightweight agent for extracting structured procurement request data from OCR "
                "text (RFQ, requirement note, BOQ, specification, scope document). Uses simple "
                "prompt -> response pattern (no tool-calling loop). Called by "
                "RequestDocumentPrefillService when semantic extraction is needed."
            ),
            "inputs": "ocr_text (str), source_document_type (optional hint), domain_hint (HVAC/IT/...)",
            "outputs": "confidence, document_type_detected, title, description, domain_code, schema_code, request_type, geography_country, geography_city, currency, attributes (list), scope_categories, compliance_hints",
            "fallback": "Returns low-confidence result with extracted partial data -- never raises",
            "requires_key": "AZURE_OPENAI_API_KEY (via LLMClient)",
            "key_rules": [
                "Confidence per-field: 0.0 = guess, 1.0 = explicitly stated in document",
                "Detects domain from terminology: HVAC / IT / FACILITIES / ELECTRICAL etc.",
                "No tool-calling loop -- single prompt/response for speed",
            ],
        },
        {
            "name": "ReasonSummaryAgent",
            "module": "apps/procurement/agents/reason_summary_agent.py",
            "flow": "Flow A",
            "flow_color": "info",
            "icon": "bi-journal-richtext",
            "model": "Azure OpenAI GPT-4o (LLM parts) + deterministic (structured tables)",
            "entry_point": "ReasonSummaryAgent.generate(recommendation_result)",
            "type": "Hybrid LLM + Deterministic",
            "type_color": "info",
            "description": (
                "Transforms a persisted RecommendationResult into richly-structured explanation "
                "for the workspace UI. LLM generates headline, reasoning_summary, and top_drivers "
                "for natural context-aware explanations. All structured tables (rules, conditions, "
                "alternatives, constraints) are built deterministically from stored payload for "
                "accuracy. Falls back gracefully to deterministic text if LLM is unavailable."
            ),
            "inputs": "RecommendationResult instance (with output_payload_json and reasoning_details_json)",
            "outputs": "headline, system_name, system_code, reasoning_summary, confidence_pct, compliance_status, capacity_guidance, human_validation, alternate_option, top_drivers, rules_table, conditions_table, alternatives_table, constraints, assumptions, thought_steps, standards",
            "fallback": "Full deterministic fallback -- page always renders even without LLM",
            "requires_key": "AZURE_OPENAI_API_KEY (optional -- deterministic fallback on failure)",
            "key_rules": [
                "Structured tables (rules, conditions, alternatives) are ALWAYS deterministic",
                "LLM only generates headline, reasoning_summary, top_drivers (3 narrative fields)",
                "Page always renders -- LLM failure activates deterministic fallback transparently",
            ],
        },
        {
            "name": "RFQGeneratorAgent",
            "module": "apps/procurement/agents/RFQ_Generator_Agent.py",
            "flow": "Flow A",
            "flow_color": "success",
            "icon": "bi-file-earmark-spreadsheet",
            "model": "Deterministic (no LLM) + openpyxl + reportlab",
            "entry_point": "RFQGeneratorAgent.run(proc_request, selection_mode, generated_by, save_record=True)",
            "type": "Deterministic Document Generator",
            "type_color": "success",
            "description": (
                "Generates RFQ (Request for Quotation) documents as Excel + PDF for a HVAC "
                "procurement request. When selection_mode='RECOMMENDED', fetches the latest "
                "RecommendationResult and uses its system_type_code. When selection_mode is a "
                "system code (e.g. 'VRF'), uses that directly. Scope rows come from DB "
                "HVACServiceScope table with hardcoded fallback. Blob-uploads both files."
            ),
            "inputs": "proc_request, selection_mode ('RECOMMENDED' or system code), qty_overrides (dict), generated_by (User), save_record (bool)",
            "outputs": "RFQResult dataclass: xlsx_bytes, pdf_bytes, rfq_ref, filename_xlsx, filename_pdf, system_code, system_label, selection_basis, confidence_pct, scope_rows, rfq_record, error",
            "fallback": "Returns partial RFQResult with error field -- never raises exception",
            "requires_key": "AZURE_BLOB_ACCOUNT_NAME + AZURE_BLOB_ACCOUNT_KEY (for upload) + openpyxl (Excel) + reportlab (PDF optional)",
            "key_rules": [
                "Uploads both XLSX and PDF to Azure Blob Storage (BlobStorageService)",
                "Persists GeneratedRFQ record when save_record=True",
                "scope_rows from HVACServiceScope DB table -- fallback to hardcoded defaults",
                "PDF generation is optional -- fails silently if reportlab not installed",
            ],
        },
    ]

    # ---- Benchmarking Agents ----
    benchmarking_agents = [
        {
            "name": "BenchmarkDocumentExtractorAgent",
            "module": "apps/benchmarking/services/document_extractor_agent.py",
            "flow": "Flow B",
            "flow_color": "warning",
            "icon": "bi-file-earmark-bar-graph",
            "model": "Azure DI + Azure OpenAI GPT-4o (batch classification)",
            "entry_point": "BenchmarkDocumentExtractorAgent().run(quotation_pk, bench_request_pk)",
            "type": "Deterministic 5-Stage Pipeline",
            "type_color": "warning",
            "description": (
                "Strictly-scoped deterministic pipeline agent for BenchmarkQuotation processing. "
                "No ReAct loop -- each stage is independently try/except'd so one failure does "
                "not abort the rest. Falls back to keyword ClassificationService when OpenAI "
                "unavailable. Reads CategoryMaster table at runtime -- no hardcoded categories."
            ),
            "inputs": "quotation_pk (BenchmarkQuotation PK), bench_request_pk (BenchmarkRequest PK)",
            "outputs": "success, line_items (list of BenchmarkLineItem PKs), engine (azure_di | pdfplumber_fallback), stages (dict of per-stage outcomes), error",
            "fallback": "Each stage has independent fallback. AzureDI -> pdfplumber. OpenAI -> keyword ClassificationService.",
            "requires_key": "AZURE_DI_ENDPOINT + AZURE_DI_KEY + AZURE_OPENAI_API_KEY + AZURE_BLOB_ACCOUNT_NAME",
            "pipeline_stages": [
                {"num": 1, "name": "Azure Blob Upload", "color": "secondary", "detail": "Upload PDF to Azure Blob Storage (fail-silent -- quotation is still processed if upload fails). Container: AZURE_BLOB_CONTAINER_NAME, prefix: benchmarking/quotations/<year>/<month>/"},
                {"num": 2, "name": "Azure DI Extraction", "color": "primary", "detail": "Extract text + tables using AzureDIExtractionService. Falls back to pdfplumber-based ExtractionService if DI credentials missing, SDK not installed, or API call fails. engine field records which path was used."},
                {"num": 3, "name": "OpenAI Line Classification", "color": "success", "detail": "Batch-classify every extracted line item into CategoryMaster categories using Azure OpenAI GPT-4o. Single API call with all lines. classification_source set to 'AI'. Falls back to keyword ClassificationService if OpenAI unavailable."},
                {"num": 4, "name": "Variance Threshold Application", "color": "warning", "detail": "Apply VarianceThresholdConfig per line item to compute acceptable variance bands. Reads WITHIN_RANGE_MAX (5%), MODERATE_MAX (15%), and HIGH (>15%) thresholds."},
                {"num": 5, "name": "DB Persistence", "color": "danger", "detail": "Persist BenchmarkLineItem records with classification_source='AI', confidence scores, and variance bands. Linked to BenchmarkQuotation."},
            ],
        },
    ]

    # ---- Market Intelligence Fallback Chain ----
    market_intelligence_chain = [
        {
            "step": 1,
            "name": "Perplexity Live Web Search",
            "icon": "bi-search-heart",
            "color": "primary",
            "key": "PERPLEXITY_API_KEY",
            "agent": "PerplexityMarketResearchAnalystAgent",
            "model": "sonar-pro",
            "description": (
                "Primary path. Calls Perplexity API with sonar-pro model. Agent enforces strict "
                "citation rules: every product URL must come EXACTLY from Perplexity citations[] "
                "array. Returns 5-7 product suggestions with manufacturer, model, price, and "
                "verified purchase/enquiry URLs."
            ),
            "triggers_fallback_on": [
                "PERPLEXITY_API_KEY not set or empty",
                "Any exception (network error, bad JSON, 4xx / 5xx HTTP)",
                "Result with zero suggestions (blank or empty info returned)",
            ],
        },
        {
            "step": 2,
            "name": "Fallback Playwright Web Scraper",
            "icon": "bi-globe2",
            "color": "warning",
            "key": "AZURE_OPENAI_API_KEY + Playwright (pip install playwright && playwright install chromium)",
            "agent": "FallbackWebscraperAgent",
            "model": "Azure OpenAI GPT-4o site selection + Playwright Chromium scraping",
            "description": (
                "Automatic fallback managed by MarketIntelligenceService.generate_auto(). "
                "Step 1: GPT-4o selects 6 specific LISTING page URLs. "
                "Step 2: Playwright visits each URL (30s timeout, 6000 char page cap). "
                "Step 3: GPT-4o parses scraped text into same product dict shape. "
                "Returns identical output format to Perplexity agent."
            ),
            "triggers_fallback_on": [
                "Playwright not installed",
                "Playwright fails to navigate all URLs",
                "GPT-4o unable to parse scraped content",
            ],
        },
        {
            "step": 3,
            "name": "No Market Data (Partial Result)",
            "icon": "bi-exclamation-triangle",
            "color": "secondary",
            "key": "None required",
            "agent": "None -- MarketIntelligenceService records error",
            "model": "N/A",
            "description": (
                "If both Perplexity and Playwright fallback fail, MarketIntelligenceService "
                "records a failed status on the AnalysisRun. The UI shows an error banner "
                "directing the user to retry. The procurement request remains in PENDING state."
            ),
            "triggers_fallback_on": [],
        },
    ]

    # ---- Market Intelligence Service (facade / compatibility wrapper) ----
    market_intelligence_service_info = {
        "class": "MarketIntelligenceService",
        "module": "apps/procurement/services/market_intelligence_service.py",
        "role": "Compatibility facade -- all logic delegates to PerplexityMarketResearchAnalystAgent",
        "methods": [
            {"name": "generate_auto(proc_request, generated_by)", "description": "Primary entry point. Tries Perplexity first, auto-falls back to FallbackWebscraperAgent on any failure. Called by views, Celery tasks, and management commands."},
            {"name": "generate_with_perplexity(proc_request, generated_by)", "description": "Force Perplexity path only (no automatic fallback). Raises ValueError or HTTPError on failure -- used when caller explicitly wants Perplexity or nothing."},
            {"name": "get_attrs_block(proc_request)", "description": "Returns formatted attribute block string for use in Perplexity prompt (delegates to PerplexityMarketResearchAnalystAgent)."},
            {"name": "get_rec_context(proc_request)", "description": "Returns (system_code, system_name) tuple from latest RecommendationResult for the request."},
        ],
        "important_note": "All real logic lives in PerplexityMarketResearchAnalystAgent and FallbackWebscraperAgent. This service exists solely for import compatibility -- callers do not need to change their import statements.",
    }

    # ---- Azure DI Intelligence (two distinct use cases) ----
    azure_di_use_cases = [
        {
            "use_case": "Invoice Extraction Pipeline (Core Platform)",
            "service": "apps/extraction/services/ (ExtractionAdapter + InvoicePromptComposer)",
            "icon": "bi-file-earmark-text",
            "color": "primary",
            "description": "Extracts structured invoice fields from uploaded PDFs for AP reconciliation. OCR text sent to InvoiceExtractionAgent (GPT-4o) with modular prompt composition.",
            "model": "Azure Document Intelligence prebuilt-layout -> GPT-4o (Invoice Extraction Agent)",
            "fallback": "No DI fallback -- required. Fails gracefully with EXTRACTION_FAILED status.",
            "output": "Invoice, InvoiceLineItem DB records + ExtractionResult with confidence score",
            "settings": "AZURE_DI_ENDPOINT, AZURE_DI_KEY",
            "char_limit": "60,000 chars OCR text limit",
        },
        {
            "use_case": "Quotation Extraction -- Benchmarking (Flow B)",
            "service": "apps/benchmarking/services/azure_di_extraction_service.py (AzureDIExtractionService)",
            "icon": "bi-file-earmark-bar-graph",
            "color": "success",
            "description": "Extracts text + tables from uploaded quotation PDFs for should-cost benchmarking. Returns structured line items with UOM, qty, unit rate, total.",
            "model": "Azure Document Intelligence prebuilt-layout (Heuristic table parsing -> line items)",
            "fallback": "pdfplumber-based ExtractionService -- activated when DI credentials missing, SDK not installed, or API call fails. engine field records 'azure_di' or 'pdfplumber_fallback'.",
            "output": "text (str), tables (list), line_items (list of parsed line item dicts), raw_json, error, engine",
            "settings": "AZURE_DI_ENDPOINT, AZURE_DI_KEY",
            "char_limit": "No explicit limit -- full document processed",
        },
        {
            "use_case": "Universal Document Extractor (Procurement Flow A/B)",
            "service": "apps/procurement/agents/Azure_Document_Intelligence_Extractor_Agent.py (AzureDIExtractorAgent)",
            "icon": "bi-file-earmark-medical",
            "color": "info",
            "description": "Universal ReAct-style agent treating Azure DI as an OpenAI tool. Supports invoice, quotation, PO, GRN, contract, proforma, HVAC request form, and any other doc type.",
            "model": "Azure DI prebuilt-layout (as tool) + GPT-4o (synthesis via tool-calling ReAct loop)",
            "fallback": "Returns engine='error' with structured error dict on any failure -- never raises.",
            "output": "success, doc_type, confidence, header, line_items, commercial_terms, raw_ocr_text, tables, key_value_pairs, engine, duration_ms, error",
            "settings": "AZURE_DI_ENDPOINT, AZURE_DI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT",
            "char_limit": "No explicit limit -- full document processed",
        },
    ]

    # ---- Azure Blob Storage ----
    azure_blob_info = {
        "class": "BlobStorageService",
        "module": "apps.benchmarking.services.blob_storage_service",
        "container": "finance-agents",
        "account": "bradblob.blob.core.windows.net",
        "prefix": "benchmarking/quotations/<year>/<month>/",
        "auth": "AZURE_BLOB_CONNECTION_STRING (parsed for AccountName + AccountKey). Set AZURE_BLOB_CONTAINER_NAME for container override.",
        "failsafe_description": "Fully fail-silent -- ImportError (SDK missing) and ValueError (not configured) both return ('', ''). No exception ever propagates to the main pipeline.",
        "methods": [
            {
                "name": "upload_quotation(source, filename, request_ref)",
                "description": "Upload a quotation PDF. source can be a file path (str), raw bytes, or file-like object. Builds blob path as benchmarking/quotations/<year>/<month>/<ref>_<ts>_<filename>.",
                "returns": "(blob_name, blob_url) -- both empty on failure",
            },
            {
                "name": "delete_blob(blob_name)",
                "description": "Delete a blob by its full path. No-op if blob_name is empty. Deletes all snapshots.",
                "returns": "bool -- True if deleted, False on any failure",
            },
            {
                "name": "get_sas_url(blob_name, expiry_hours=24)",
                "description": "Generate a time-limited read-only SAS URL for a stored blob. Default expiry is 24 hours.",
                "returns": "str -- SAS URL, empty on failure",
            },
        ],
        # IMPORTANT: must be a list -- iterating a string yields individual characters in Django templates
        "also_used_by": [
            "RFQGeneratorAgent -- uploads XLSX + PDF to rfq/<safe_title>/ when save_record=True (apps.documents.blob_service.upload_to_blob)",
            "DocumentUpload pipeline -- stores uploaded invoices/documents at input/<YYYY>/<MM>/<id>_<filename> (apps.documents.blob_service)",
            "BulkExtractionService -- same input/ path pattern for bulk-uploaded document sets",
        ],
        "blob_folders": [
            {
                "folder": "input/<YYYY>/<MM>/<upload_id>_<filename>",
                "service": "apps.documents.blob_service",
                "used_by": "All uploaded documents (invoices, quotations, HVAC forms). Set on DocumentUpload.blob_name + blob_url.",
                "color": "primary",
            },
            {
                "folder": "processed/<YYYY>/<MM>/<upload_id>_<filename>",
                "service": "apps.documents.blob_service",
                "used_by": "Documents moved after successful extraction and processing.",
                "color": "success",
            },
            {
                "folder": "exception/<YYYY>/<MM>/<upload_id>_<filename>",
                "service": "apps.documents.blob_service",
                "used_by": "Documents that failed extraction or processing (quarantined).",
                "color": "danger",
            },
            {
                "folder": "benchmarking/quotations/<YYYY>/<MM>/<ref>_<ts>_<filename>",
                "service": "apps.benchmarking.services.blob_storage_service.BlobStorageService",
                "used_by": "Quotation PDFs uploaded via benchmarking flow. Stored on BenchmarkQuotation.blob_name + blob_url.",
                "color": "warning",
            },
            {
                "folder": "rfq/<safe_title>/RFQ-<pk>-<YYYYMMDD>_<safe_title>.xlsx|.pdf",
                "service": "apps.procurement.agents.RFQ_Generator_Agent.RFQGeneratorAgent",
                "used_by": "Auto-generated RFQ documents (Excel + PDF). Stored on GeneratedRFQ.xlsx_blob_path + pdf_blob_path.",
                "color": "info",
            },
        ],
    }

    # ---- Benchmarking Pipeline Stages (BenchmarkDocumentExtractorAgent) ----
    benchmarking_pipeline_stages = [
        {
            "number": 1,
            "code": "BLOB_UPLOAD",
            "label": "Azure Blob Upload",
            "icon": "bi-cloud-arrow-up",
            "color": "secondary",
            "description": "Upload quotation PDF to Azure Blob Storage (fail-silent). Container: AZURE_BLOB_CONTAINER_NAME. Path: benchmarking/quotations/<year>/<month>/. Returns (blob_name, blob_url) -- both empty if credentials missing or upload fails.",
            "class": "BlobStorageService",
            "fallback": "Fail-silent: pipeline continues without blob reference",
        },
        {
            "number": 2,
            "code": "AZURE_DI_EXTRACT",
            "label": "Azure DI Extraction",
            "icon": "bi-eye",
            "color": "primary",
            "description": "Azure Document Intelligence prebuilt-layout call to extract text + tables from the PDF. Heuristic table row parser identifies UOM / qty / unit rate / total line items. Returns raw_json for auditability.",
            "class": "AzureDIExtractionService",
            "fallback": "pdfplumber-based ExtractionService: activated when DI credentials missing, SDK not installed, or API call fails. engine field stores 'azure_di' or 'pdfplumber_fallback'.",
        },
        {
            "number": 3,
            "code": "AI_CLASSIFICATION",
            "label": "OpenAI Batch Classification",
            "icon": "bi-tags",
            "color": "success",
            "description": "Single Azure OpenAI GPT-4o call classifying ALL extracted line items into CategoryMaster categories (EQUIPMENT, CONTROLS, DUCTING, INSULATION, ACCESSORIES, INSTALLATION, T&C, etc.). Returns line_number, category, confidence per item. classification_source set to 'AI'.",
            "class": "BenchmarkDocumentExtractorAgent (internal)",
            "fallback": "Keyword ClassificationService: rule-based category matching when OpenAI unavailable or classification_source set to 'KEYWORD'.",
        },
        {
            "number": 4,
            "code": "VARIANCE_THRESHOLD",
            "label": "Variance Threshold Application",
            "icon": "bi-calculator",
            "color": "warning",
            "description": "Apply VarianceThresholdConfig per line item. WITHIN_RANGE: |variance%| < 5%. MODERATE: 5% to <15%. HIGH: >= 15%. NEEDS_REVIEW: no benchmark available. Thresholds configurable via DB VarianceThresholdConfig records.",
            "class": "BenchmarkDocumentExtractorAgent (internal) + VarianceThresholdConfig model",
            "fallback": "Hardcoded constants if no DB config found: WITHIN_RANGE_MAX=5, MODERATE_MAX=15.",
        },
        {
            "number": 5,
            "code": "DB_PERSIST",
            "label": "DB Persistence",
            "icon": "bi-database-fill-up",
            "color": "danger",
            "description": "Persist BenchmarkLineItem records with classification_source='AI', category, confidence scores, unit_rate, total, and computed variance bands. All linked to the parent BenchmarkQuotation.",
            "class": "BenchmarkLineItem model (apps/benchmarking/models.py)",
            "fallback": "Transaction rolls back on integrity error -- logs error but does not crash caller.",
        },
    ]

    # ---- Web Search Service (last-resort benchmark fallback) ----
    web_search_info = {
        "service": "apps/procurement/services/web_search_service.py (WebSearchService)",
        "purpose": "Last-resort fallback for benchmark price lookup when no internal DB data is available for a line item",
        "resolution_chain": [
            {"step": 1, "source": "DuckDuckGo Instant Answer API", "color": "success", "detail": "Free API, no key required. Queries DDG for product + price. Extracts AED/USD/SAR price patterns from text."},
            {"step": 2, "source": "Bing Search Scrape", "color": "warning", "detail": "Lightweight Bing results page scrape (no API key). Falls back if DDG returns no price data."},
            {"step": 3, "source": "No Data", "color": "secondary", "detail": "If both sources return no price, line is marked source='NO_DATA'. Confidence 0.0."},
        ],
        "confidence_cap": "0.35 -- all web-sourced prices marked source='WEB_SEARCH'",
        "http_backend": "requests library if installed, else urllib fallback",
        "note": "Prices from web search are INDICATIVE ESTIMATES only and require manual validation before use in negotiations.",
    }

    response = render(request, template_name, {
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
        # Procurement Agents
        "procurement_agents": procurement_agents,
        # Benchmarking Agents + Pipeline
        "benchmarking_agents": benchmarking_agents,
        "benchmarking_pipeline_stages": benchmarking_pipeline_stages,
        # Market Intelligence (Perplexity + Fallback)
        "market_intelligence_chain": market_intelligence_chain,
        "market_intelligence_service_info": market_intelligence_service_info,
        # Azure DI Intelligence
        "azure_di_use_cases": azure_di_use_cases,
        # Azure Blob Storage
        "azure_blob_info": azure_blob_info,
        # Web Search Service
        "web_search_info": web_search_info,
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
    procurement_benchmark_type = getattr(AgentType, "PROCUREMENT_BENCHMARK", "PROCUREMENT_BENCHMARK")
    procurement_validation_type = getattr(AgentType, "PROCUREMENT_VALIDATION", "PROCUREMENT_VALIDATION")
    procurement_agent_types = {
        AgentType.PROCUREMENT_RECOMMENDATION,
        procurement_validation_type,
        AgentType.PROCUREMENT_COMPLIANCE,
        AgentType.PROCUREMENT_MARKET_INTELLIGENCE,
        AgentType.PROCUREMENT_REASON_SUMMARY,
        AgentType.PROCUREMENT_AZURE_DI_EXTRACTION,
        AgentType.PROCUREMENT_RFQ_GENERATOR,
    }
    benchmark_agent_types = {
        procurement_benchmark_type,
    }

    def _classify_run_type(agent_type_value: str) -> str:
        if agent_type_value in benchmark_agent_types or "BENCHMARK" in str(agent_type_value):
            return "benchmarking"
        if agent_type_value in procurement_agent_types:
            return "procurement"
        return "ap"

    tenant = require_tenant(request)
    base_qs = AgentRun.objects.select_related(
        "reconciliation_result", "reconciliation_result__invoice",
        "agent_definition", "document_upload", "parent_run",
    ).order_by("-created_at")
    if tenant is not None:
        base_qs = base_qs.filter(tenant=tenant)

    qs = base_qs

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
    domain = request.GET.get("domain", request.GET.get("run_scope", "")).strip().lower()
    domain_aliases = {
        "benchmark": "benchmarking",
        "bench": "benchmarking",
        "procumrent": "procurement",
    }
    domain = domain_aliases.get(domain, domain)

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
    if domain:
        from django.db.models import Q
        benchmark_q = Q(agent_type__in=benchmark_agent_types) | Q(agent_type__icontains="BENCHMARK")
        procurement_q = Q(agent_type__in=procurement_agent_types)
        if domain == "benchmarking":
            qs = qs.filter(benchmark_q)
        elif domain == "procurement":
            qs = qs.filter(procurement_q)
        elif domain == "ap":
            qs = qs.exclude(benchmark_q | procurement_q)

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Annotate recovery trigger codes for template access
    # (Django templates cannot access dict keys starting with underscore)
    for run in page_obj:
        payload = run.input_payload or {}
        meta = payload.get("_recovery_meta") or {}
        run.recovery_trigger_codes = meta.get("trigger_codes", [])

        # Live usage hydration (best-effort): backfill model/tokens from output payload
        # so cost visibility improves for historical rows that already carry usage metadata.
        output_payload = run.output_payload or {}
        usage = output_payload.get("llm_usage") or output_payload.get("usage") or {}
        changed_fields = []

        inferred_model = run.llm_model_used or ""
        if not inferred_model:
            inferred_model = (
                output_payload.get("llm_model_used")
                or output_payload.get("model_used")
                or output_payload.get("model")
                or usage.get("model")
                or ""
            )
            if not inferred_model and run.agent_type == AgentType.PROCUREMENT_MARKET_INTELLIGENCE:
                if output_payload.get("source_reference_label") == "Perplexity Source References":
                    inferred_model = getattr(settings, "PERPLEXITY_MODEL", "sonar-pro")
            if inferred_model:
                run.llm_model_used = str(inferred_model)
                changed_fields.append("llm_model_used")

        def _to_int(value):
            try:
                if value is None:
                    return None
                parsed = int(value)
                return parsed if parsed >= 0 else None
            except (TypeError, ValueError):
                return None

        if run.prompt_tokens is None:
            prompt_tokens = _to_int(output_payload.get("prompt_tokens"))
            if prompt_tokens is None:
                prompt_tokens = _to_int(usage.get("prompt_tokens"))
            if prompt_tokens is not None:
                run.prompt_tokens = prompt_tokens
                changed_fields.append("prompt_tokens")

        if run.completion_tokens is None:
            completion_tokens = _to_int(output_payload.get("completion_tokens"))
            if completion_tokens is None:
                completion_tokens = _to_int(usage.get("completion_tokens"))
            if completion_tokens is not None:
                run.completion_tokens = completion_tokens
                changed_fields.append("completion_tokens")

        if run.total_tokens is None:
            total_tokens = _to_int(output_payload.get("total_tokens"))
            if total_tokens is None:
                total_tokens = _to_int(usage.get("total_tokens"))
            if total_tokens is None and (run.prompt_tokens is not None or run.completion_tokens is not None):
                total_tokens = (run.prompt_tokens or 0) + (run.completion_tokens or 0)
            if total_tokens is not None:
                run.total_tokens = total_tokens
                changed_fields.append("total_tokens")

        if run.actual_cost_usd is None and run.llm_model_used and run.total_tokens:
            try:
                from apps.agents.services.base_agent import BaseAgent
                BaseAgent._calculate_actual_cost(run)
                changed_fields.append("actual_cost_usd")
            except Exception:
                pass

        if changed_fields:
            try:
                run.save(update_fields=list(dict.fromkeys(changed_fields + ["updated_at"])))
            except Exception:
                pass

        run.model_display = run.llm_model_used or ""
        run.cost_live_ready = bool(run.llm_model_used and run.total_tokens)

        run.agent_display_name = run.get_agent_type_display()
        if run.agent_type == AgentType.PROCUREMENT_MARKET_INTELLIGENCE:
            if output_payload.get("source_reference_label") == "Perplexity Source References":
                run.agent_display_name = "Perplexity Market Research Agent"
            else:
                run.agent_display_name = "Market Intelligence (Fallback Webscraper)"
        elif run.agent_type == AgentType.PROCUREMENT_RECOMMENDATION:
            inv = (run.invocation_reason or "").lower()
            if "reasonsummaryagent" in inv:
                run.agent_display_name = "ReasonSummaryAgent"
            elif "recommendation" in inv:
                run.agent_display_name = "HVACRecommendationAgent"
        elif run.agent_type == procurement_benchmark_type:
            inv = (run.invocation_reason or "").lower()
            if "benchmarkingmarketdataanalyzer" in inv:
                run.agent_display_name = "BenchmarkingMarketDataAnalyzer"
            elif "benchmarkingcomplianceagent" in inv:
                run.agent_display_name = "BenchmarkingComplianceAgent"
            elif "benchmarkingvendorrecommendationagent" in inv:
                run.agent_display_name = "BenchmarkingVendorRecommendationAgent"
            elif "benchmarkinganalystagent" in inv:
                run.agent_display_name = "BenchmarkingAnalystAgent"
            elif "benchmark_item_market_data_analyzer" in inv:
                run.agent_display_name = "BenchmarkingMarketDataAnalyzer"
            elif "benchmark_item_compliance_agent" in inv:
                run.agent_display_name = "BenchmarkingComplianceAgent"
            elif "benchmark_item_vendor_recommendation" in inv:
                run.agent_display_name = "BenchmarkingVendorRecommendationAgent"
            elif "benchmark_item_analyst_agent" in inv:
                run.agent_display_name = "BenchmarkingAnalystAgent"
            elif "decision_maker" in inv or "benchmarkingdecisionmakeragent" in inv:
                run.agent_display_name = "BenchmarkingDecisionMakerAgent"
            else:
                run.agent_display_name = "BenchmarkAgent"
        elif run.agent_type == AgentType.PROCUREMENT_COMPLIANCE:
            run.agent_display_name = "ComplianceAgent (Procurement)"
        elif run.agent_type == procurement_validation_type:
            run.agent_display_name = "ValidationAgentService"

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

    # Dropdown choices (DB-driven): keep in sync with performance dashboard
    used_agent_type_values = set(
        base_qs.exclude(agent_type="")
        .values_list("agent_type", flat=True)
        .distinct()
    )

    defs_qs = AgentDefinition.objects.all()
    if tenant is not None:
        defs_qs = defs_qs.filter(tenant=tenant) | AgentDefinition.objects.filter(tenant__isnull=True)
        defs_qs = defs_qs.distinct()

    configured_agent_type_values = set(
        defs_qs.exclude(agent_type="").values_list("agent_type", flat=True).distinct()
    )
    definition_name_map = {
        row["agent_type"]: row["name"]
        for row in defs_qs.exclude(agent_type="").values("agent_type", "name")
    }

    available_agent_types = used_agent_type_values | configured_agent_type_values
    agent_type_choices = [
        (
            value,
            definition_name_map.get(value)
            or value.replace("_", " ").title(),
        )
        for value in sorted(available_agent_types)
        if value
    ]
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

    # LLM agent coverage (tenant-scoped, not filter-scoped)
    deterministic_agent_types = {
        AgentType.SYSTEM_REVIEW_ROUTING,
        AgentType.SYSTEM_CASE_SUMMARY,
        AgentType.SYSTEM_BULK_EXTRACTION_INTAKE,
        AgentType.SYSTEM_CASE_INTAKE,
        AgentType.SYSTEM_POSTING_PREPARATION,
    }
    type_counts = {
        row["agent_type"]: row["count"]
        for row in base_qs.values("agent_type").annotate(count=Count("id"))
    }
    llm_agent_catalog = []
    for agent_value, agent_label in AgentType.choices:
        if agent_value in deterministic_agent_types:
            continue
        llm_agent_catalog.append({
            "value": agent_value,
            "label": agent_label,
            "count": int(type_counts.get(agent_value, 0) or 0),
        })
    missing_llm_agents = [row for row in llm_agent_catalog if row["count"] == 0]

    # Full LLM inventory
    # - AP rows remain explicit.
    # - Procurement + Benchmarking rows are discovered dynamically from their agent folders.
    llm_component_catalog = [
        {"name": "SupervisorAgent", "group": "AP", "tracked_agent_type": AgentType.SUPERVISOR, "tracked": True, "functionality": "Coordinates agent sequence, delegates specialist tasks, and consolidates final recommendation."},
        {"name": "ExceptionAnalysisAgent", "group": "AP", "tracked_agent_type": AgentType.EXCEPTION_ANALYSIS, "tracked": True, "functionality": "Analyzes reconciliation exceptions, identifies root cause patterns, and proposes next actions."},
        {"name": "InvoiceExtractionAgent", "group": "AP", "tracked_agent_type": AgentType.INVOICE_EXTRACTION, "tracked": True, "functionality": "Extracts structured invoice header and line-item fields from OCR text."},
        {"name": "InvoiceUnderstandingAgent", "group": "AP", "tracked_agent_type": AgentType.INVOICE_UNDERSTANDING, "tracked": True, "functionality": "Improves low-confidence extraction by reasoning over ambiguous invoice fields."},
        {"name": "PORetrievalAgent", "group": "AP", "tracked_agent_type": AgentType.PO_RETRIEVAL, "tracked": True, "functionality": "Finds and validates candidate purchase orders for the invoice context."},
        {"name": "GRNRetrievalAgent", "group": "AP", "tracked_agent_type": AgentType.GRN_RETRIEVAL, "tracked": True, "functionality": "Finds and validates goods receipt notes for three-way matching."},
        {"name": "ReviewRoutingAgent", "group": "AP", "tracked_agent_type": AgentType.REVIEW_ROUTING, "tracked": True, "functionality": "Routes cases to the correct review queue based on risk and exception type."},
        {"name": "CaseSummaryAgent", "group": "AP", "tracked_agent_type": AgentType.CASE_SUMMARY, "tracked": True, "functionality": "Generates concise case summary for reviewers with key findings and evidence."},
        {"name": "ReconciliationAssistAgent", "group": "AP", "tracked_agent_type": AgentType.RECONCILIATION_ASSIST, "tracked": True, "functionality": "Provides reconciliation guidance and suggested remediation for partial or unmatched cases."},
        {"name": "ComplianceAgent (AP)", "group": "AP", "tracked_agent_type": AgentType.COMPLIANCE_AGENT, "tracked": True, "functionality": "Checks AP transaction against policy/compliance signals and flags control risks."},
    ]

    def _normalised_keywords(file_stem: str) -> list:
        raw = (file_stem or "").replace(".py", "")
        lowered = raw.lower()
        compact = lowered.replace("_", "")
        without_bm = lowered.replace("_bm", "")
        without_agent = without_bm.replace("_agent", "")
        return list(dict.fromkeys([lowered, compact, without_bm, without_agent]))

    def _extract_module_summary(file_path: Path) -> str:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            lines = text.splitlines()
            if not lines:
                return "No description available."
            first = lines[0].strip().strip('"')
            if first:
                return first[:160]
        except Exception:
            pass
        return "No description available."

    def _count_component_runs(*, tracked_type, keywords: list) -> int:
        from django.db.models import Q
        run_qs = base_qs
        if tracked_type:
            run_qs = run_qs.filter(agent_type=tracked_type)
        query = Q()
        for kw in keywords:
            if kw:
                query |= Q(invocation_reason__icontains=kw)
        if query:
            count = run_qs.filter(query).count()
            if count > 0:
                return count
        if tracked_type:
            return run_qs.count()
        return 0

    procurement_file_map = {
        "hvac_recommendation_agent": {"tracked_agent_type": AgentType.PROCUREMENT_RECOMMENDATION, "priority": "MANDATORY"},
        "compliance_agent": {"tracked_agent_type": AgentType.PROCUREMENT_COMPLIANCE, "priority": "MANDATORY"},
        "perplexity_market_research_analyst_agent": {"tracked_agent_type": AgentType.PROCUREMENT_MARKET_INTELLIGENCE, "priority": "MANDATORY"},
        "fallback_webscraper_agent": {"tracked_agent_type": AgentType.PROCUREMENT_MARKET_INTELLIGENCE, "priority": "MANDATORY"},
        "reason_summary_agent": {"tracked_agent_type": AgentType.PROCUREMENT_REASON_SUMMARY, "priority": "MANDATORY"},
        "azure_document_intelligence_extractor_agent": {"tracked_agent_type": AgentType.PROCUREMENT_AZURE_DI_EXTRACTION, "priority": "MANDATORY"},
        "rfq_generator_agent": {"tracked_agent_type": AgentType.PROCUREMENT_RFQ_GENERATOR, "priority": "MANDATORY"},
    }

    procurement_agents_dir = Path(settings.BASE_DIR) / "apps" / "procurement" / "agents"
    if procurement_agents_dir.exists():
        for file_path in sorted(procurement_agents_dir.glob("*.py")):
            if file_path.name.startswith("__"):
                continue
            stem = file_path.stem
            key = stem.lower()
            config = procurement_file_map.get(key, {})
            tracked_type = config.get("tracked_agent_type")
            keywords = _normalised_keywords(stem)
            run_count = _count_component_runs(tracked_type=tracked_type, keywords=keywords)
            llm_component_catalog.append({
                "name": stem.replace("_", " ").strip(),
                "group": "Procurement",
                "tracked_agent_type": tracked_type,
                "tracked": config.get("tracked", True),
                "priority": config.get("priority", "OPTIONAL"),
                "functionality": _extract_module_summary(file_path),
                "run_count": run_count,
                "is_active": run_count > 0,
                "run_section_label": "Procurement Agent Runs",
            })

    benchmarking_agents_dir = Path(settings.BASE_DIR) / "apps" / "benchmarking" / "agents"
    if benchmarking_agents_dir.exists():
        for file_path in sorted(benchmarking_agents_dir.glob("*.py")):
            if file_path.name.startswith("__"):
                continue
            stem = file_path.stem
            keywords = _normalised_keywords(stem)
            run_count = _count_component_runs(tracked_type=procurement_benchmark_type, keywords=keywords)
            llm_component_catalog.append({
                "name": stem.replace("_", " ").strip(),
                "group": "Benchmarking",
                "tracked_agent_type": procurement_benchmark_type,
                "tracked": True,
                "priority": "OPTIONAL",
                "functionality": _extract_module_summary(file_path),
                "run_count": run_count,
                "is_active": run_count > 0,
                "run_section_label": "Benchmark Agent Runs",
            })

    llm_component_catalog.append({
        "name": "ReasoningPlanner",
        "group": "Platform",
        "tracked_agent_type": AgentType.PLATFORM_REASONING_PLANNER,
        "tracked": True,
        "priority": "MANDATORY",
        "functionality": "Plans which agents to run and in what order for each orchestration request.",
    })
    mandatory_agent_types = {
        AgentType.PROCUREMENT_RECOMMENDATION,
        AgentType.PROCUREMENT_COMPLIANCE,
        AgentType.PROCUREMENT_MARKET_INTELLIGENCE,
        AgentType.PROCUREMENT_REASON_SUMMARY,
        AgentType.PROCUREMENT_AZURE_DI_EXTRACTION,
        AgentType.PROCUREMENT_RFQ_GENERATOR,
        AgentType.PLATFORM_REASONING_PLANNER,
    }

    # DB-driven catalog flags (enabled/lifecycle + optional mandatory config).
    agent_def_map = {
        row.agent_type: row
        for row in AgentDefinition.objects.all()
    }

    for row in llm_component_catalog:
        tracked_type = row.get("tracked_agent_type")
        row["tracked_label"] = dict(AgentType.choices).get(tracked_type, tracked_type) if tracked_type else "--"
        if row.get("group") == "Benchmarking":
            row["tracked_label"] = "Benchmarking"
            row["run_section_label"] = "Benchmark Agent Runs"
        elif row.get("group") == "Procurement":
            row["run_section_label"] = "Procurement Agent Runs"
        elif row.get("group") == "AP":
            row["run_section_label"] = "AP Agent Runs"
        else:
            row["run_section_label"] = "Platform / System"
        alias_types = row.get("alias_agent_types") or []
        all_types = [tracked_type] + list(alias_types) if tracked_type else []
        if row.get("run_count") is None:
            row["run_count"] = sum(int(type_counts.get(t, 0) or 0) for t in all_types) if all_types else None
        if tracked_type:
            row["cost_ready_count"] = base_qs.filter(
                agent_type__in=all_types,
            ).exclude(
                llm_model_used="",
            ).exclude(
                total_tokens__isnull=True,
            ).count()
        else:
            row["cost_ready_count"] = None
        agent_def = agent_def_map.get(tracked_type) if tracked_type else None

        # Active status: DB definition first; fallback to process defaults.
        if agent_def is not None:
            row["is_active"] = bool(agent_def.enabled and agent_def.lifecycle_status == "active")
        else:
            if row.get("group") in {"Procurement", "Benchmarking"} and tracked_type:
                row["is_active"] = True
            else:
                row["is_active"] = bool(row.get("is_active") or ((row.get("run_count") or 0) > 0))

        # Mandatory priority: DB config_json flag first, then fallback defaults.
        if agent_def is not None and isinstance(agent_def.config_json, dict):
            cfg = agent_def.config_json
            if "mandatory" in cfg or "is_mandatory" in cfg:
                row["priority"] = "MANDATORY" if bool(cfg.get("mandatory", cfg.get("is_mandatory"))) else "OPTIONAL"
            elif tracked_type in mandatory_agent_types:
                row["priority"] = "MANDATORY"
            elif row.get("group") in {"Procurement", "Benchmarking"}:
                row["priority"] = "MANDATORY"
            else:
                row["priority"] = row.get("priority", "OPTIONAL")
        else:
            if tracked_type in mandatory_agent_types or row.get("group") in {"Procurement", "Benchmarking"}:
                row["priority"] = "MANDATORY"
            else:
                row["priority"] = row.get("priority", "OPTIONAL")

    # Show only AP + dynamic folder-backed Procurement/Benchmarking + mandatory Platform rows.
    llm_component_catalog = [
        row for row in llm_component_catalog
        if row.get("group") in {"AP", "Procurement", "Benchmarking", "Platform"}
    ]

    def _first_nonempty(*values):
        for value in values:
            if value is not None and str(value).strip() != "":
                return value
        return ""

    for run in page_obj:
        input_payload = run.input_payload or {}
        output_payload = run.output_payload or {}

        run.procurement_request_ref = str(_first_nonempty(
            input_payload.get("procurement_request_id"),
            input_payload.get("procurement_request_pk"),
            input_payload.get("request_id"),
            input_payload.get("request_pk"),
            output_payload.get("procurement_request_id"),
            output_payload.get("request_id"),
        ))
        run.quotation_ref = str(_first_nonempty(
            input_payload.get("quotation_id"),
            input_payload.get("supplier_quotation_id"),
            input_payload.get("benchmark_quotation_id"),
            output_payload.get("quotation_id"),
            output_payload.get("supplier_quotation_id"),
            output_payload.get("benchmark_quotation_id"),
        ))

        run.has_confidence = run.confidence is not None
        run.has_role = bool((run.actor_primary_role or "").strip())
        run.has_model = bool((run.llm_model_used or "").strip())
        run.has_trigger = bool((run.invocation_reason or "").strip())
        run.missing_mandatory_count = int(not run.has_confidence) + int(not run.has_role) + int(not run.has_model) + int(not run.has_trigger)
        run.classification = _classify_run_type(run.agent_type)

    return render(request, "agents/agent_runs_list.html", {
        "page_obj": page_obj,
        "runs": page_obj,
        # Filter choices
        "agent_type_choices": agent_type_choices,
        "status_choices": status_choices,
        "roles": roles,
        "models_used": models_used,
        "domain_choices": [
            ("", "All Runs"),
            ("ap", "AP"),
            ("procurement", "Procurement"),
            ("benchmarking", "Benchmarking"),
        ],
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
        "current_domain": domain,
        "current_run_scope": domain,
        # KPIs
        "total_count": total_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        "avg_confidence": avg_confidence,
        "total_tokens": total_tokens,
        "llm_agent_catalog": llm_agent_catalog,
        "missing_llm_agents": missing_llm_agents,
        "llm_component_catalog": llm_component_catalog,
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

    # Child runs (spawned by supervisor delegation tools)
    child_runs = AgentRun.objects.filter(
        parent_run=run,
    ).select_related("agent_definition").order_by("created_at")

    # Parent run (if this run was spawned by a supervisor)
    parent_run_obj = run.parent_run if run.parent_run_id else None

    return render(request, "agents/agent_run_detail.html", {
        "run": run,
        "steps": steps,
        "agent_messages": agent_messages,
        "decisions": decisions,
        "recommendations": recommendations,
        "linked_invoice": linked_invoice,
        "eval_field_outcomes": eval_field_outcomes,
        "child_runs": child_runs,
        "parent_run": parent_run_obj,
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
