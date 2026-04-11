"""SupervisorAgent -- full AP lifecycle orchestrator.

Extends BaseAgent with a larger tool budget and dynamic skill-based prompt
assembly. Owns the full invoice lifecycle: UNDERSTAND -> VALIDATE -> MATCH
-> INVESTIGATE -> DECIDE.

This agent uses the existing ReAct loop from BaseAgent but overrides:
  - system_prompt -- assembled from skills via supervisor_prompt_builder
  - allowed_tools -- merged from all active skills
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

    Uses skills to dynamically compose its prompt and toolset.
    Reuses existing deterministic services as tools -- the LLM reasons
    on tool outputs rather than recomputing deterministic logic.
    """

    agent_type = AgentType.SUPERVISOR
    enforce_json_response = True

    def __init__(self, skill_names: Optional[List[str]] = None):
        super().__init__()
        # Lazy import at init to avoid circular imports at module level
        from apps.agents.services.supervisor_prompt_builder import (
            DEFAULT_SKILLS,
            build_supervisor_prompt,
        )
        from apps.agents.skills.base import SkillRegistry

        self._skill_names = skill_names or list(DEFAULT_SKILLS)
        self._system_prompt_cache: Optional[str] = None
        self._tools_cache: Optional[List[str]] = None

    # ------------------------------------------------------------------
    # Abstract interface implementation
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        if self._system_prompt_cache is None:
            from apps.agents.services.supervisor_prompt_builder import build_supervisor_prompt
            self._system_prompt_cache = build_supervisor_prompt(
                skill_names=self._skill_names,
                max_tool_rounds=SUPERVISOR_MAX_TOOL_ROUNDS,
            )
        return self._system_prompt_cache

    @property
    def allowed_tools(self) -> List[str]:
        if self._tools_cache is None:
            from apps.agents.skills.base import SkillRegistry
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
        """Build a rich user message from the supervisor context."""
        parts = []

        # Reconciliation mode context
        mode = ctx.reconciliation_mode or ctx.extra.get("reconciliation_mode", "")
        if mode == "TWO_WAY":
            parts.append(
                "Reconciliation Mode: 2-WAY (Invoice vs PO only -- GRN/receipt data "
                "is NOT part of this reconciliation. Do NOT flag GRN-related issues.)"
            )
        elif mode == "THREE_WAY":
            parts.append("Reconciliation Mode: 3-WAY (Invoice vs PO vs GRN)")
        elif mode == "NON_PO":
            parts.append(
                "Reconciliation Mode: NON-PO (No PO matching -- focus on validation "
                "and vendor verification only.)"
            )

        # Invoice context
        parts.append(f"\nInvoice ID: {ctx.invoice_id}")
        if ctx.po_number:
            parts.append(f"PO Number (from invoice): {ctx.po_number}")
        if ctx.document_upload_id:
            parts.append(f"Document Upload ID: {ctx.document_upload_id}")

        # Memory facts
        if ctx.memory and ctx.memory.facts:
            facts = ctx.memory.facts
            if facts.get("invoice_number"):
                parts.append(f"Invoice Number: {facts['invoice_number']}")
            if facts.get("vendor_name"):
                parts.append(f"Vendor Name: {facts['vendor_name']}")
            if facts.get("extraction_confidence"):
                parts.append(
                    f"Extraction Confidence: {facts['extraction_confidence']:.2f}"
                )
            if facts.get("invoice_status"):
                parts.append(f"Current Status: {facts['invoice_status']}")

        # Existing exceptions
        if ctx.exceptions:
            parts.append(f"\nExisting Exceptions ({len(ctx.exceptions)}):")
            for exc in ctx.exceptions[:10]:
                parts.append(
                    f"  - [{exc.get('severity', 'MEDIUM')}] "
                    f"{exc.get('exception_type', 'UNKNOWN')}: "
                    f"{exc.get('description', '')[:100]}"
                )

        parts.append(
            "\nProcess this invoice through the full lifecycle. "
            "Start with UNDERSTAND phase by extracting data, then VALIDATE, "
            "MATCH, INVESTIGATE (if needed), and DECIDE."
        )

        return "\n".join(parts)

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:
        """Parse the supervisor's final response."""
        from apps.agents.services.supervisor_output_interpreter import (
            interpret_supervisor_output,
        )

        output = interpret_supervisor_output(content)

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

        from apps.agents.skills.base import SkillRegistry
        if not SkillRegistry.get_all():
            importlib.reload(_ie)
            importlib.reload(_av)
            importlib.reload(_am)
            importlib.reload(_ai)
            importlib.reload(_ar)
    except ImportError:
        logger.debug("Skill import failed (non-fatal)", exc_info=True)

    # Ensure supervisor tools are registered
    try:
        import apps.tools.registry.supervisor_tools  # noqa: F401
    except ImportError:
        logger.debug("Supervisor tools import failed (non-fatal)", exc_info=True)
