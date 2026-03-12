"""Agent template views — reference pages for end users."""
import os

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
from apps.cases.orchestrators.case_orchestrator import PATH_STAGES, STAGE_TO_STATUS
from apps.cases.state_machine.case_state_machine import CASE_TRANSITIONS, TERMINAL_STATES
from apps.core.enums import (
    AgentType,
    CaseStageType,
    CaseStatus,
    PerformedByType,
    ProcessingPath,
    RecommendationType,
)


def _load_case_prompt(filename):
    """Load a case prompt .txt file from apps/cases/prompts/."""
    path = os.path.join(os.path.dirname(__file__), os.pardir, "cases", "prompts", filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


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
        "performer": "System (Azure DI + GPT-4o)",
        "description": "Azure Document Intelligence OCR followed by GPT-4o structured extraction.",
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
    from apps.extraction.services.extraction_adapter import EXTRACTION_SYSTEM_PROMPT
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
            "prompt_text": EXTRACTION_SYSTEM_PROMPT,
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

    # Case-specific prompts from apps/cases/prompts/
    case_prompt_files = [
        ("Case Summary", "case_summary_prompt.txt",
         "Produces a reviewer-facing narrative with SUMMARY, REVIEWER NOTES, and RECOMMENDATION sections."),
        ("Exception Analysis", "exception_analysis_prompt.txt",
         "Analyses exceptions/validation issues — determines root cause, severity, and remediation path."),
        ("Non-PO Validation", "non_po_validation_prompt.txt",
         "Reasons over 9 deterministic check results for invoices without a Purchase Order."),
        ("Reviewer Copilot", "reviewer_copilot_prompt.txt",
         "Advisory assistant for human reviewers — answers case questions using tools but never commits actions."),
    ]
    for name, filename, description in case_prompt_files:
        text = _load_case_prompt(filename)
        if text:
            prompts.append({
                "name": name,
                "category": "Case",
                "icon": "bi-briefcase",
                "color": "info",
                "description": description,
                "used_in": f"apps/cases/prompts/{filename}",
                "model": "Azure OpenAI GPT-4o",
                "prompt_text": text,
            })

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
    })
