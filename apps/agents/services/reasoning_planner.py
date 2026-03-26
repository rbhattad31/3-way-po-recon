"""LLM-backed agent execution planner.

ReasoningPlanner wraps PolicyEngine and always uses the LLM to decide
which agents to run and in what order. The deterministic PolicyEngine always
runs first as a baseline; the LLM then produces the final plan and falls
back to the deterministic plan on any error.

Usage::

    planner = ReasoningPlanner()
    plan = planner.plan(reconciliation_result)
"""
from __future__ import annotations

import json
import logging
from typing import List

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.agents.services.policy_engine import AgentPlan, PolicyEngine
from apps.core.enums import AgentType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Valid AgentType values that the planner may include in a plan.
_VALID_AGENT_TYPES = {at.value for at in AgentType}

_PLANNER_SYSTEM_PROMPT = (
    "You are an expert AP reconciliation pipeline planner. "
    "Your job is to decide which AI agents should investigate a reconciliation result "
    "and in what order. Choose only from the agents listed below.\n\n"
    "Available agents:\n"
    "  PO_RETRIEVAL          - Searches for the correct Purchase Order when the PO "
    "number on the invoice does not match any open PO, or is missing entirely.\n"
    "  GRN_RETRIEVAL         - Investigates Goods Receipt Notes when goods have not "
    "been confirmed received or the GRN is missing. "
    "IMPORTANT: Do NOT include this agent when the reconciliation mode is TWO_WAY.\n"
    "  INVOICE_UNDERSTANDING - Re-analyses extracted invoice fields when extraction "
    "confidence is low or key fields are ambiguous.\n"
    "  RECONCILIATION_ASSIST - Investigates partial-match discrepancies in quantities, "
    "unit prices, or line amounts.\n"
    "  EXCEPTION_ANALYSIS    - Performs root-cause analysis on all reconciliation "
    "exceptions and recommends a resolution action.\n"
    "  REVIEW_ROUTING        - Determines the correct review queue, team, and priority "
    "for the case based on the findings of earlier agents.\n"
    "  CASE_SUMMARY          - Produces a concise human-readable case summary for the "
    "AP reviewer. Should almost always be the last agent.\n\n"
    "Rules:\n"
    "  1. GRN_RETRIEVAL must never appear when reconciliation_mode is TWO_WAY.\n"
    "  2. CASE_SUMMARY should be last.\n"
    "  3. Use the minimum set of agents needed to resolve the case.\n"
    "  4. Assign each step a unique integer priority starting from 1 (lower = earlier).\n"
    "  5. Respond ONLY with valid JSON -- no markdown, no code fences."
)


class ReasoningPlanner:
    """Wraps PolicyEngine and enhances the plan using an LLM.

    The LLM plan is always attempted. On any LLM error the deterministic
    PolicyEngine result is used as a safe fallback.
    """

    def __init__(self) -> None:
        self._fallback = PolicyEngine()
        self._llm = LLMClient(temperature=0.0, max_tokens=1024)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, result) -> AgentPlan:
        """Return an AgentPlan for result.

        Args:
            result: A ReconciliationResult model instance.

        Returns:
            AgentPlan with the ordered list of agents to execute.
        """
        quick_plan = self._fallback.plan(result)

        # Skip agent execution for clean matches or auto-close cases.
        if quick_plan.skip_agents:
            return quick_plan

        # Attempt LLM-driven plan; fall back to deterministic on any error.
        try:
            return self._llm_plan(result)
        except Exception as exc:
            logger.warning(
                "ReasoningPlanner LLM plan failed for result %s (%s); "
                "falling back to deterministic plan.",
                getattr(result, "pk", "?"),
                exc,
            )
            return quick_plan

    def should_auto_close(self, recommendation_type, confidence: float) -> bool:
        """Delegate to the underlying PolicyEngine."""
        return self._fallback.should_auto_close(recommendation_type, confidence)

    def should_escalate(self, recommendation_type, confidence: float) -> bool:
        """Delegate to the underlying PolicyEngine."""
        return self._fallback.should_escalate(recommendation_type, confidence)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _llm_plan(self, result) -> AgentPlan:
        """Call the LLM to produce a structured agent execution plan.

        Args:
            result: A ReconciliationResult model instance.

        Returns:
            AgentPlan built from the LLM response.

        Raises:
            Any exception from the LLM client or JSON parsing propagates
            up to plan() which will catch it and use the fallback.
        """
        # Gather context from the result.
        match_status = getattr(result, "match_status", "UNKNOWN")
        recon_mode = getattr(result, "reconciliation_mode", "") or "UNKNOWN"
        det_confidence = getattr(result, "deterministic_confidence", None) or 0.0
        extraction_confidence = getattr(result, "extraction_confidence", None) or 0.0

        exc_types: List[str] = list(
            result.exceptions.values_list("exception_type", flat=True)
        )

        user_message = (
            f"Reconciliation result details:\n"
            f"  match_status: {match_status}\n"
            f"  reconciliation_mode: {recon_mode}\n"
            f"  deterministic_confidence: {det_confidence:.4f}\n"
            f"  extraction_confidence: {extraction_confidence:.4f}\n"
            f"  exception_types: {json.dumps(exc_types)}\n\n"
            "Respond ONLY with valid JSON in this schema: "
            "{\"overall_reasoning\": \"...\", \"confidence\": 0.9, "
            "\"steps\": [{\"agent_type\": \"PO_RETRIEVAL\", \"rationale\": \"...\", "
            "\"priority\": 1}]}"
        )

        messages = [
            LLMMessage(role="system", content=_PLANNER_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_message),
        ]

        response = self._llm.chat(
            messages=messages,
            response_format={"type": "json_object"},
        )

        raw = response.content or "{}"
        payload = json.loads(raw)

        overall_reasoning = str(payload.get("overall_reasoning", "LLM planner reasoning unavailable"))
        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            steps = []

        # Validate agent types and drop unknown values.
        valid_steps = [
            s for s in steps
            if isinstance(s, dict) and s.get("agent_type") in _VALID_AGENT_TYPES
        ]

        # Sort by priority ascending (lower number = earlier in pipeline).
        valid_steps.sort(key=lambda s: int(s.get("priority", 999)))

        if not valid_steps:
            raise ValueError(
                "LLM planner returned no valid agent steps from payload: "
                + json.dumps(list(payload.get("steps", [])))[:200]
            )

        # Require CASE_SUMMARY to be the last step if present.
        agent_names = [s["agent_type"] for s in valid_steps]
        if "CASE_SUMMARY" in agent_names and agent_names[-1] != "CASE_SUMMARY":
            raise ValueError(
                "LLM planner placed CASE_SUMMARY out of position: " + str(agent_names)
            )

        # GRN_RETRIEVAL must not appear in TWO_WAY plans.
        if recon_mode == "TWO_WAY" and "GRN_RETRIEVAL" in agent_names:
            raise ValueError(
                "LLM planner included GRN_RETRIEVAL for a TWO_WAY reconciliation."
            )

        agents = [s["agent_type"] for s in valid_steps]
        plan_confidence = float(payload.get("confidence", 0.0))

        return AgentPlan(
            agents=agents,
            reason=overall_reasoning,
            skip_agents=False,
            auto_close=False,
            reconciliation_mode=recon_mode,
            plan_source="llm",
            plan_confidence=plan_confidence,
        )
