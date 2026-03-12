"""Concrete agent implementations for the PO Reconciliation agentic layer.

Each agent is specialised for one phase of the reconciliation intelligence pipeline.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from apps.agents.services.base_agent import AgentOutput, BaseAgent, AgentContext
from apps.core.enums import AgentType, RecommendationType
from apps.core.prompt_registry import PromptRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared prompt fragments
# ---------------------------------------------------------------------------
_JSON_OUTPUT_INSTRUCTION = (
    "\n\nRESPOND ONLY with valid JSON in this exact schema:\n"
    '{"reasoning": "<concise explanation>", '
    '"recommendation_type": "<one of: AUTO_CLOSE, SEND_TO_AP_REVIEW, SEND_TO_PROCUREMENT, '
    'SEND_TO_VENDOR_CLARIFICATION, REPROCESS_EXTRACTION, ESCALATE_TO_MANAGER or null>", '
    '"confidence": <0.0-1.0>, '
    '"decisions": [{"decision": "<text>", "rationale": "<text>", "confidence": <0-1>}], '
    '"evidence": {<any supporting key-value pairs>}}'
)


def _mode_context(ctx: AgentContext) -> str:
    """Return a short reconciliation-mode context string for user messages."""
    mode = ctx.reconciliation_mode or ctx.extra.get("reconciliation_mode", "")
    if mode == "TWO_WAY":
        return (
            "Reconciliation Mode: 2-WAY (Invoice vs PO only — GRN/receipt data is NOT part of this reconciliation. "
            "Do NOT flag GRN-related issues.)\n"
        )
    return "Reconciliation Mode: 3-WAY (Invoice vs PO vs GRN)\n"


def _parse_agent_json(content: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from LLM content."""
    content = content.strip()
    # Strip markdown fences
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
    if content.endswith("```"):
        content = content.rsplit("```", 1)[0]
    content = content.strip()
    if content.startswith("json"):
        content = content[4:].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def _to_agent_output(data: Dict[str, Any], raw: str) -> AgentOutput:
    return AgentOutput(
        reasoning=data.get("reasoning", raw[:500]),
        recommendation_type=data.get("recommendation_type"),
        confidence=float(data.get("confidence", 0.0)),
        evidence=data.get("evidence", {}),
        decisions=data.get("decisions", []),
        raw_content=raw,
    )


# ============================================================================
# 1. Exception Analysis Agent
# ============================================================================
class ExceptionAnalysisAgent(BaseAgent):
    """Analyses reconciliation exceptions, determines root causes, and recommends actions."""

    agent_type = AgentType.EXCEPTION_ANALYSIS

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.exception_analysis")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Extraction Confidence: {ctx.reconciliation_result.extraction_confidence}\n"
            f"Exceptions ({len(ctx.exceptions)}):\n"
            + json.dumps(ctx.exceptions, indent=2, default=str)
            + "\n\nAnalyse these exceptions. Use tools if you need more context."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["po_lookup", "grn_lookup", "invoice_details", "exception_list", "reconciliation_summary"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 2. Invoice Understanding Agent
# ============================================================================
class InvoiceUnderstandingAgent(BaseAgent):
    """Deep-dives into invoice data to resolve ambiguity or extraction issues."""

    agent_type = AgentType.INVOICE_UNDERSTANDING

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.invoice_understanding")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Extraction Confidence: {ctx.reconciliation_result.extraction_confidence}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            "\nRetrieve invoice details using the invoice_details tool, then analyse quality."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["invoice_details", "po_lookup", "vendor_search"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 3. PO Retrieval Agent
# ============================================================================
class PORetrievalAgent(BaseAgent):
    """Attempts to find the correct PO when deterministic lookup failed."""

    agent_type = AgentType.PO_RETRIEVAL

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.po_retrieval")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            f"Invoice ID: {ctx.invoice_id}\n"
            f"Extracted PO Number (failed lookup): {ctx.po_number or 'MISSING'}\n"
            f"Vendor on invoice: {ctx.extra.get('vendor_name', 'unknown')}\n"
            f"Invoice total: {ctx.extra.get('total_amount', 'unknown')}\n"
            "\nTry to find the correct PO using the available tools."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["po_lookup", "vendor_search", "invoice_details"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 4. GRN Retrieval Agent
# ============================================================================
class GRNRetrievalAgent(BaseAgent):
    """Investigates GRN data when GRN is missing or has receipt issues.

    NOTE: This agent is only invoked in 3-way mode. The PolicyEngine
    suppresses it when the reconciliation mode is TWO_WAY.
    """

    agent_type = AgentType.GRN_RETRIEVAL

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.grn_retrieval")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"GRN Available: {ctx.extra.get('grn_available', 'unknown')}\n"
            f"GRN Fully Received: {ctx.extra.get('grn_fully_received', 'unknown')}\n"
            "\nInvestigate GRN data for this PO."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["grn_lookup", "po_lookup", "invoice_details"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 5. Review Routing Agent
# ============================================================================
class ReviewRoutingAgent(BaseAgent):
    """Determines the best review queue/team and priority for a reconciliation case."""

    agent_type = AgentType.REVIEW_ROUTING

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.review_routing")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Exceptions: {json.dumps(ctx.exceptions, indent=2, default=str)}\n"
            f"Prior agent reasoning: {ctx.extra.get('prior_reasoning', 'N/A')}\n"
            "\nDetermine the best routing for this case."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return ["reconciliation_summary", "exception_list"]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 6. Case Summary Agent
# ============================================================================
class CaseSummaryAgent(BaseAgent):
    """Produces a human-readable case summary for reviewers."""

    agent_type = AgentType.CASE_SUMMARY

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.case_summary")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Prior analysis: {ctx.extra.get('prior_reasoning', 'N/A')}\n"
            f"Recommendation: {ctx.extra.get('recommendation_type', 'N/A')}\n"
            "\nGather data using tools, then produce a clear case summary."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return [
            "invoice_details", "po_lookup", "grn_lookup",
            "reconciliation_summary", "exception_list",
        ]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# 7. Reconciliation Assist Agent
# ============================================================================
class ReconciliationAssistAgent(BaseAgent):
    """General-purpose assistant that helps resolve partial matches."""

    agent_type = AgentType.RECONCILIATION_ASSIST

    @property
    def system_prompt(self) -> str:
        return PromptRegistry.get("agent.reconciliation_assist")

    def build_user_message(self, ctx: AgentContext) -> str:
        return (
            _mode_context(ctx)
            + f"Reconciliation Result ID: {ctx.reconciliation_result.pk}\n"
            f"Invoice ID: {ctx.invoice_id}\n"
            f"PO Number: {ctx.po_number or 'N/A'}\n"
            f"Match Status: {ctx.reconciliation_result.match_status}\n"
            f"Exceptions: {json.dumps(ctx.exceptions, indent=2, default=str)}\n"
            "\nInvestigate and advise."
        )

    @property
    def allowed_tools(self) -> List[str]:
        return [
            "invoice_details", "po_lookup", "grn_lookup",
            "reconciliation_summary", "exception_list",
        ]

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        data = _parse_agent_json(content)
        return _to_agent_output(data, content)


# ============================================================================
# Agent class registry
# ============================================================================
AGENT_CLASS_REGISTRY: Dict[str, type] = {
    AgentType.EXCEPTION_ANALYSIS: ExceptionAnalysisAgent,
    AgentType.INVOICE_UNDERSTANDING: InvoiceUnderstandingAgent,
    AgentType.PO_RETRIEVAL: PORetrievalAgent,
    AgentType.GRN_RETRIEVAL: GRNRetrievalAgent,
    AgentType.REVIEW_ROUTING: ReviewRoutingAgent,
    AgentType.CASE_SUMMARY: CaseSummaryAgent,
    AgentType.RECONCILIATION_ASSIST: ReconciliationAssistAgent,
}


# Need the import for the type hint in AGENT_CLASS_REGISTRY
from typing import Dict  # noqa: E402
