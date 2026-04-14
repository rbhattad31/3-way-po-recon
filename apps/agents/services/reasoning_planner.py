"""LLM-backed agent execution planner.

CONTRACT:
  - Activated via ``AGENT_REASONING_ENGINE_ENABLED=true`` in settings.
  - When enabled, the LLM planner runs for every non-clean result.
  - PolicyEngine always runs first as a baseline (fast, no LLM).
  - If the LLM call fails for any reason, the deterministic PolicyEngine
    result is returned as a safe fallback (plan_source = "deterministic").
  - When disabled, the orchestrator uses PolicyEngine directly.

Reflection (insertion of extra agents after each run) is part of the
orchestrator, not the planner. It is always active and requires no flag.

LLM planner is responsible for:
  - Choosing which agents to run and in what order
  - Validating the chosen agent sequence
  - Returning plan_source="llm" and plan_confidence from the LLM response

PolicyEngine fallback is responsible for:
  - Rule-based agent selection (no LLM)
  - post-run should_auto_close() and should_escalate() checks
    (delegated from ReasoningPlanner.should_auto_close/should_escalate)
"""
from __future__ import annotations

import json
import logging
import time
from typing import List

from django.utils import timezone

from apps.agents.models import AgentRun
from apps.agents.services.base_agent import BaseAgent
from apps.core.enums import AgentRunStatus
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
        planner_run = self._start_planner_run(result, quick_plan)
        start_ts = time.monotonic()

        # Skip agent execution for clean matches or auto-close cases.
        if quick_plan.skip_agents:
            self._complete_planner_run(
                planner_run,
                plan=quick_plan,
                duration_ms=int((time.monotonic() - start_ts) * 1000),
                planner_error="",
            )
            return quick_plan

        # Attempt LLM-driven plan; fall back to deterministic on any error.
        try:
            llm_plan = self._llm_plan(result)
            self._complete_planner_run(
                planner_run,
                plan=llm_plan,
                duration_ms=int((time.monotonic() - start_ts) * 1000),
                planner_error="",
            )
            return llm_plan
        except Exception as exc:
            logger.warning(
                "ReasoningPlanner LLM plan failed for result %s (%s); "
                "falling back to deterministic plan.",
                getattr(result, "pk", "?"),
                exc,
            )
            self._complete_planner_run(
                planner_run,
                plan=quick_plan,
                duration_ms=int((time.monotonic() - start_ts) * 1000),
                planner_error=str(exc),
            )
            return quick_plan

    def _start_planner_run(self, result, quick_plan: AgentPlan):
        """Best-effort AgentRun creation for planner observability."""
        try:
            return AgentRun.objects.create(
                tenant=getattr(result, "tenant", None),
                agent_type=AgentType.PLATFORM_REASONING_PLANNER,
                reconciliation_result=result,
                status=AgentRunStatus.RUNNING,
                confidence=0.0,
                llm_model_used=(self._llm.model or "unknown"),
                input_payload={
                    "source": "reasoning_planner",
                    "result_id": getattr(result, "pk", None),
                    "match_status": str(getattr(result, "match_status", "") or ""),
                    "reconciliation_mode": str(getattr(result, "reconciliation_mode", "") or ""),
                    "deterministic_fallback_agents": list(quick_plan.agents or []),
                    "deterministic_fallback_reason": quick_plan.reason,
                },
                invocation_reason="ReasoningPlanner.plan",
                started_at=timezone.now(),
            )
        except Exception:
            return None

    def _complete_planner_run(self, planner_run, *, plan: AgentPlan, duration_ms: int, planner_error: str) -> None:
        """Best-effort completion update for the planner AgentRun."""
        if not planner_run:
            return
        try:
            output_payload = {
                "agents": list(plan.agents or []),
                "reason": plan.reason,
                "skip_agents": bool(plan.skip_agents),
                "auto_close": bool(plan.auto_close),
                "reconciliation_mode": plan.reconciliation_mode,
                "plan_source": plan.plan_source,
                "plan_confidence": plan.plan_confidence,
            }
            if planner_error:
                output_payload["planner_error"] = planner_error

            planner_run.status = AgentRunStatus.COMPLETED
            planner_run.output_payload = output_payload
            planner_run.summarized_reasoning = BaseAgent._sanitise_text(plan.reason or "")[:2000]
            planner_run.confidence = max(0.0, min(1.0, float(plan.plan_confidence or 0.0)))
            planner_run.error_message = planner_error or ""
            planner_run.completed_at = timezone.now()
            planner_run.duration_ms = duration_ms
            planner_run.save(update_fields=[
                "status",
                "output_payload",
                "summarized_reasoning",
                "confidence",
                "error_message",
                "completed_at",
                "duration_ms",
                "updated_at",
            ])
        except Exception:
            logger.debug("ReasoningPlanner AgentRun update failed", exc_info=True)

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
