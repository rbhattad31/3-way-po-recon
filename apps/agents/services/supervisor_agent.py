"""SupervisorAgent -- full AP lifecycle orchestrator.

Extends BaseAgent with a larger tool budget and dynamic skill-based prompt
assembly. Owns the full invoice lifecycle: UNDERSTAND -> VALIDATE -> MATCH
-> INVESTIGATE -> DECIDE.

Enhanced with:
  - Smart query routing (CASE_ANALYSIS / AP_INSIGHTS / HYBRID modes)
  - AP insights tools for system-wide analytics questions
  - Dashboard-enriched context for hybrid queries

This agent uses the existing ReAct loop from BaseAgent but overrides:
  - system_prompt -- assembled from skills via supervisor_prompt_builder
  - allowed_tools -- merged from all active skills (incl. ap_insights)
  - build_user_message -- rich context from supervisor_context_builder
  - interpret_response -- supervisor-specific output parsing
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from apps.agents.services.base_agent import (
    MAX_TOOL_ROUNDS,
    AgentContext,
    AgentOutput,
    BaseAgent,
)
from apps.core.enums import AgentType

logger = logging.getLogger(__name__)

# Supervisor gets a larger tool budget than standard agents
SUPERVISOR_MAX_TOOL_ROUNDS = 15


class SupervisorAgent(BaseAgent):
    """Full AP lifecycle supervisor agent.

    Orchestrates invoice processing through five non-linear phases:
    UNDERSTAND, VALIDATE, MATCH, INVESTIGATE, DECIDE.

    Also handles system-wide AP insights queries via the query router.
    When the query is classified as AP_INSIGHTS, the agent skips case
    analysis phases and uses analytics tools to answer directly.

    Uses skills to dynamically compose its prompt and toolset.
    Reuses existing deterministic services as tools -- the LLM reasons
    on tool outputs rather than recomputing deterministic logic.
    """

    agent_type = AgentType.SUPERVISOR
    enforce_json_response = True

    def __init__(self, skill_names: Optional[List[str]] = None, query_mode: Optional[str] = None):
        super().__init__()
        # Lazy import at init to avoid circular imports at module level
        from apps.agents.services.supervisor_prompt_builder import (
            DEFAULT_SKILLS,
            build_supervisor_prompt,
        )
        from apps.agents.skills.base import SkillRegistry

        self._skill_names = skill_names or list(DEFAULT_SKILLS)

        # Always include ap_insights skill
        if "ap_insights" not in self._skill_names:
            self._skill_names.append("ap_insights")

        self._query_mode = query_mode  # Set externally or via route_and_run()
        self._system_prompt_cache: Optional[str] = None
        self._tools_cache: Optional[List[str]] = None

    # ------------------------------------------------------------------
    # Abstract interface implementation
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        if self._system_prompt_cache is None:
            from apps.agents.services.supervisor_prompt_builder import build_supervisor_prompt

            # Ensure skills are registered before building prompt
            _ensure_skills_loaded()

            self._system_prompt_cache = build_supervisor_prompt(
                skill_names=self._skill_names,
                max_tool_rounds=SUPERVISOR_MAX_TOOL_ROUNDS,
            )
        return self._system_prompt_cache

    @property
    def allowed_tools(self) -> List[str]:
        if self._tools_cache is None:
            from apps.agents.skills.base import SkillRegistry

            # Ensure skills + tools are registered before querying
            _ensure_skills_loaded()

            # Merge tools from all active skills + the 6 existing tools
            skill_tools = SkillRegistry.all_tools(self._skill_names)
            # Always include the original tools that agents already have
            existing = [
                "po_lookup", "grn_lookup", "vendor_search",
                "invoice_details", "exception_list", "reconciliation_summary",
            ]
            seen = set(skill_tools)
            merged = list(skill_tools)
            for t in existing:
                if t not in seen:
                    merged.append(t)
                    seen.add(t)
            self._tools_cache = merged
        return self._tools_cache

    def build_user_message(self, ctx: AgentContext) -> str:
        """Build a rich user message from the supervisor context.

        Mode-aware: adjusts content based on query routing mode.
        """
        parts = []

        # If a user query was passed (e.g. from chat), include it prominently
        user_query = ctx.extra.get("user_query", "")
        if user_query:
            parts.append(f"User Query: {user_query}")

        # Routing mode context
        mode = self._query_mode
        if mode == "AP_INSIGHTS":
            parts.append(
                "\n[MODE: AP_INSIGHTS] Answer the user's analytics/performance "
                "question using the AP insights tools. You do NOT need to "
                "process a specific invoice or call submit_recommendation."
            )
        elif mode == "HYBRID":
            parts.append(
                "\n[MODE: HYBRID] The user's question involves both system-wide "
                "analytics AND a specific case. Answer both aspects."
            )

        # Include pre-loaded dashboard data if available
        dashboard = ctx.extra.get("dashboard")
        if dashboard and mode in ("AP_INSIGHTS", "HYBRID"):
            parts.append("\n--- Pre-loaded Dashboard Context ---")
            ap_summary = dashboard.get("ap_summary")
            if ap_summary:
                parts.append(
                    f"AP Summary: {ap_summary.get('total_invoices', 0)} invoices, "
                    f"{ap_summary.get('matched_pct', 0)}% matched, "
                    f"{ap_summary.get('pending_reviews', 0)} pending reviews, "
                    f"{ap_summary.get('open_exceptions', 0)} open exceptions, "
                    f"avg confidence {ap_summary.get('avg_confidence', 0)}%"
                )
            extraction = dashboard.get("extraction_analytics")
            if extraction:
                parts.append(
                    f"Extraction: {extraction.get('touchless_rate', 0):.1f}% touchless rate"
                )
            parts.append("--- End Dashboard Context ---\n")

        # Reconciliation mode context (for case analysis)
        recon_mode = ctx.reconciliation_mode or ctx.extra.get("reconciliation_mode", "")
        if recon_mode and mode != "AP_INSIGHTS":
            if recon_mode == "TWO_WAY":
                parts.append(
                    "Reconciliation Mode: 2-WAY (Invoice vs PO only -- GRN/receipt data "
                    "is NOT part of this reconciliation. Do NOT flag GRN-related issues.)"
                )
            elif recon_mode == "THREE_WAY":
                parts.append("Reconciliation Mode: 3-WAY (Invoice vs PO vs GRN)")
            elif recon_mode == "NON_PO":
                parts.append(
                    "Reconciliation Mode: NON-PO (No PO matching -- focus on validation "
                    "and vendor verification only.)"
                )

        # Invoice context (for case analysis and hybrid)
        extraction_done = False
        reconciliation_done = False
        if ctx.invoice_id and mode != "AP_INSIGHTS":
            parts.append(f"\nInvoice ID: {ctx.invoice_id}")
            if ctx.po_number:
                parts.append(f"PO Number (from invoice): {ctx.po_number}")
            if ctx.document_upload_id:
                parts.append(f"Document Upload ID: {ctx.document_upload_id}")

            # Memory facts
            extraction_done = False
            reconciliation_done = False
            if ctx.memory and ctx.memory.facts:
                facts = ctx.memory.facts
                extraction_done = facts.get("extraction_done", False)
                reconciliation_done = facts.get("reconciliation_done", False)
                if facts.get("invoice_number"):
                    parts.append(f"Invoice Number: {facts['invoice_number']}")
                if facts.get("vendor_name"):
                    parts.append(f"Vendor Name: {facts['vendor_name']}")
                if facts.get("total_amount"):
                    parts.append(
                        f"Total Amount: {facts.get('currency', '')} {facts['total_amount']}"
                    )
                if facts.get("extraction_confidence"):
                    parts.append(
                        f"Extraction Confidence: {facts['extraction_confidence']:.2f}"
                    )
                if facts.get("invoice_status"):
                    parts.append(f"Current Status: {facts['invoice_status']}")
                if facts.get("match_status"):
                    parts.append(f"Match Status: {facts['match_status']}")
                if extraction_done:
                    parts.append(
                        "[EXTRACTION ALREADY COMPLETE -- skip the UNDERSTAND phase. "
                        "Do NOT call get_ocr_text, classify_document, or "
                        "extract_invoice_fields. Proceed directly to VALIDATE.]"
                    )
                if reconciliation_done:
                    parts.append(
                        "[RECONCILIATION ALREADY COMPLETE -- skip the MATCH phase. "
                        "Proceed directly to INVESTIGATE with the existing match results.]"
                    )

            # Existing exceptions
            if ctx.exceptions:
                parts.append(f"\nExisting Exceptions ({len(ctx.exceptions)}):")
                for exc in ctx.exceptions[:10]:
                    parts.append(
                        f"  - [{exc.get('severity', 'MEDIUM')}] "
                        f"{exc.get('exception_type', 'UNKNOWN')}: "
                        f"{exc.get('message', '')[:100]}"
                    )

        # Mode-specific instruction
        if mode == "AP_INSIGHTS":
            parts.append(
                "\nAnswer the analytics question above using the available "
                "AP insights tools. Provide specific numbers and actionable observations."
            )
        elif mode == "HYBRID":
            parts.append(
                "\nAddress both the system-wide question and the case-specific "
                "analysis. Use insights tools for system context, then case tools "
                "for the specific invoice."
            )
        elif mode == "CASE_ANALYSIS" or not mode:
            if extraction_done and reconciliation_done:
                parts.append(
                    "\nExtraction and reconciliation are already complete. "
                    "Skip UNDERSTAND and MATCH phases. You MUST still call tools "
                    "to investigate:\n"
                    "1. Call get_invoice_details to review the extracted data.\n"
                    "2. Call verify_vendor to confirm vendor identity (re-check against latest vendor master).\n"
                    "3. Call get_tolerance_config to load tolerance thresholds.\n"
                    "4. Call get_reconciliation_summary to review match results.\n"
                    "5. Call get_exception_list to review exceptions.\n"
                    "6. Analyze findings and DECIDE.\n"
                    "7. Call submit_recommendation with your decision.\n"
                    "You MUST call at least steps 1-5 before deciding."
                )
            elif extraction_done:
                parts.append(
                    "\nExtraction is already complete (skip UNDERSTAND). "
                    "You MUST still call tools for the remaining phases:\n"
                    "1. Call get_invoice_details to review the extracted data.\n"
                    "2. Call validate_extraction to check data quality.\n"
                    "3. Call verify_vendor to confirm vendor identity (re-check against latest vendor master).\n"
                    "4. Call get_tolerance_config to load tolerance thresholds.\n"
                    "5. Call lookup_po to verify PO matching.\n"
                    "6. Call get_reconciliation_summary if reconciliation exists.\n"
                    "7. Analyze findings, then call submit_recommendation.\n"
                    "Do NOT skip tool calls -- you need tool evidence to decide."
                )
            else:
                parts.append(
                    "\nProcess this invoice through the full lifecycle. "
                    "Start with UNDERSTAND phase by extracting data, then VALIDATE, "
                    "MATCH, INVESTIGATE (if needed), and DECIDE."
                )

        return "\n".join(parts)

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        """Parse the supervisor's final response.

        Mode-aware: AP_INSIGHTS queries do not require submit_recommendation.
        """
        from apps.agents.services.supervisor_output_interpreter import (
            interpret_supervisor_output,
        )

        output = interpret_supervisor_output(content)

        # For AP_INSIGHTS mode, the recommendation constraint is relaxed
        if self._query_mode == "AP_INSIGHTS":
            if not output.recommendation_type:
                output.recommendation_type = "SEND_TO_AP_REVIEW"
            output.evidence["_query_mode"] = "AP_INSIGHTS"
            output.evidence["_recommendation_submitted"] = True
            return output

        # Enforce: must have called submit_recommendation
        if not output.evidence.get("_recommendation_submitted"):
            # Check if the recommendation was set via the structured output
            if output.recommendation_type:
                output.evidence["_recommendation_submitted"] = True
            else:
                output.recommendation_type = "SEND_TO_AP_REVIEW"
                output.confidence = min(output.confidence, 0.3)
                output.evidence["_recommendation_submitted"] = False
                output.evidence["_warning"] = (
                    "Supervisor did not call submit_recommendation tool"
                )

        if self._query_mode:
            output.evidence["_query_mode"] = self._query_mode

        return output

    # ------------------------------------------------------------------
    # Override run() to use larger tool budget
    # ------------------------------------------------------------------

    def run(self, ctx: AgentContext, progress_callback=None):
        """Execute supervisor with expanded tool budget.

        Temporarily patches MAX_TOOL_ROUNDS for this run, then delegates
        to the BaseAgent.run() which handles the full ReAct loop.
        """
        import apps.agents.services.base_agent as _ba

        # Ensure skills are loaded (import triggers registration)
        _ensure_skills_loaded()

        original_max = _ba.MAX_TOOL_ROUNDS
        try:
            _ba.MAX_TOOL_ROUNDS = SUPERVISOR_MAX_TOOL_ROUNDS
            return super().run(ctx, progress_callback=progress_callback)
        finally:
            _ba.MAX_TOOL_ROUNDS = original_max

    @classmethod
    def route_and_run(
        cls,
        ctx: AgentContext,
        *,
        user_query: str = "",
        user: Any = None,
        progress_callback=None,
    ):
        """Convenience method: route the query, enrich context, then run.

        This is the primary entry point for callers that want the supervisor
        to intelligently handle both case-specific and system-wide queries.

        Args:
            ctx: Pre-built AgentContext (from build_supervisor_context).
            user_query: The user's natural language query/instruction.
            user: Django user for RBAC-scoped dashboard queries.
            progress_callback: Optional progress callback for UI updates.

        Returns:
            AgentRun instance from the executed run.
        """
        from apps.agents.services.supervisor_query_router import classify_query
        from apps.agents.services.supervisor_context_builder import enrich_context_with_dashboard

        # Route the query
        routing = classify_query(
            user_query,
            has_invoice_id=bool(ctx.invoice_id),
            has_reconciliation_result=ctx.reconciliation_result is not None,
        )

        logger.info(
            "Supervisor query routed: mode=%s confidence=%.2f reason=%s",
            routing.mode.value, routing.confidence, routing.reason,
        )

        # Store the user query in context for the LLM
        if user_query:
            ctx.extra["user_query"] = user_query
        ctx.extra["routing"] = {
            "mode": routing.mode.value,
            "confidence": routing.confidence,
            "reason": routing.reason,
        }

        # Enrich with dashboard data for insights/hybrid modes
        if routing.mode.value in ("AP_INSIGHTS", "HYBRID"):
            enrich_context_with_dashboard(
                ctx, user=user, tenant=ctx.tenant,
            )

        # Create agent with the determined mode
        agent = cls(query_mode=routing.mode.value)
        return agent.run(ctx, progress_callback=progress_callback)


def _ensure_skills_loaded():
    """Import skill modules to trigger registration with SkillRegistry.

    If modules are already imported (Python cache), check whether the
    SkillRegistry actually has entries.  If empty (e.g. after a test cleared
    it), reload the modules so ``register_skill`` re-executes.
    """
    import importlib

    try:
        import apps.agents.skills.invoice_extraction as _ie  # noqa: F401
        import apps.agents.skills.ap_validation as _av  # noqa: F401
        import apps.agents.skills.ap_matching as _am  # noqa: F401
        import apps.agents.skills.ap_investigation as _ai  # noqa: F401
        import apps.agents.skills.ap_review_routing as _ar  # noqa: F401
        import apps.agents.skills.ap_insights as _ins  # noqa: F401

        from apps.agents.skills.base import SkillRegistry
        if not SkillRegistry.get_all():
            importlib.reload(_ie)
            importlib.reload(_av)
            importlib.reload(_am)
            importlib.reload(_ai)
            importlib.reload(_ar)
            importlib.reload(_ins)
    except ImportError:
        logger.debug("Skill import failed (non-fatal)", exc_info=True)

    # Ensure supervisor tools are registered
    try:
        import apps.tools.registry.supervisor_tools  # noqa: F401
    except ImportError:
        logger.debug("Supervisor tools import failed (non-fatal)", exc_info=True)

    # Ensure AP insights tools are registered
    try:
        import apps.tools.registry.ap_insights_tools  # noqa: F401
    except ImportError:
        logger.debug("AP insights tools import failed (non-fatal)", exc_info=True)
