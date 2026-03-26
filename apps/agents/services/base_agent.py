"""Base agent class with the ReAct-style tool-calling loop.

Every concrete agent subclasses ``BaseAgent`` and implements:
 - ``system_prompt``  — the system message for the LLM
 - ``build_user_message`` — formats the first user message from context
 - ``allowed_tools``    — list of tool names the agent may call
 - ``interpret_response`` — post-processes the final LLM answer
"""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.utils import timezone

from apps.agents.models import (
    AgentDefinition,
    AgentMessage,
    AgentRun,
    AgentStep,
    DecisionLog,
)
from apps.agents.services.agent_memory import AgentMemory
from apps.agents.services.llm_client import LLMClient, LLMMessage, LLMResponse
from apps.core.constants import AGENT_MAX_RETRIES, AGENT_TIMEOUT_SECONDS
from apps.core.enums import AgentRunStatus, AgentType
from apps.reconciliation.models import ReconciliationResult
from apps.tools.registry.base import ToolRegistry, ToolResult
from apps.tools.registry.tool_call_logger import ToolCallLogger

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6  # Safety cap on tool-call loops

# Agents that MUST call at least one tool to produce a reliable recommendation.
# If none are called, confidence is capped to signal unreliability.
_TOOL_GROUNDED_AGENT_TYPES = frozenset({
    "PO_RETRIEVAL",
    "GRN_RETRIEVAL",
    "RECONCILIATION_ASSIST",
    "INVOICE_UNDERSTANDING",
    "EXCEPTION_ANALYSIS",
})


@dataclass
class AgentContext:
    """Immutable context bag passed into an agent run."""
    reconciliation_result: Optional[ReconciliationResult]
    invoice_id: int
    po_number: Optional[str] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    reconciliation_mode: str = ""  # ReconciliationMode value (TWO_WAY / THREE_WAY)
    # RBAC context (populated by orchestrator via guardrails service)
    actor_user_id: Optional[int] = None
    actor_primary_role: str = ""
    actor_roles_snapshot: List[str] = field(default_factory=list)
    permission_checked: str = ""
    permission_source: str = ""
    access_granted: bool = False
    trace_id: str = ""
    span_id: str = ""
    # Structured in-process memory shared across all agents in the pipeline.
    memory: Optional[AgentMemory] = None


@dataclass
class AgentOutput:
    """The final structured output of an agent run."""
    reasoning: str = ""
    recommendation_type: Optional[str] = None
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    raw_content: str = ""


class BaseAgent(ABC):
    """Abstract base for all reconciliation agents."""

    agent_type: str = ""  # Must match AgentType enum value
    # Subclasses may override to False (e.g. for free-text streaming agents).
    enforce_json_response: bool = True

    def __init__(self):
        self.llm = LLMClient()

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    @abstractmethod
    def build_user_message(self, ctx: AgentContext) -> str: ...

    @property
    @abstractmethod
    def allowed_tools(self) -> List[str]: ...

    @abstractmethod
    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput: ...

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext) -> AgentRun:
        """Execute the full agent loop and return the persisted AgentRun."""
        agent_def = AgentDefinition.objects.filter(
            agent_type=self.agent_type, enabled=True
        ).first()

        agent_run = AgentRun.objects.create(
            agent_definition=agent_def,
            agent_type=self.agent_type,
            reconciliation_result=ctx.reconciliation_result,
            status=AgentRunStatus.RUNNING,
            input_payload=self._serialise_context(ctx),
            started_at=timezone.now(),
            llm_model_used=self.llm.model,
            # RBAC metadata from context
            actor_user_id=ctx.actor_user_id,
            actor_primary_role=ctx.actor_primary_role,
            actor_roles_snapshot_json=ctx.actor_roles_snapshot or None,
            permission_checked=ctx.permission_checked,
            permission_source=ctx.permission_source,
            access_granted=ctx.access_granted,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
        )

        # Stamp the current prompt version on the run for auditability.
        # Always persist prompt_version (including empty string) so the field
        # is never silently missing from the audit trail.
        from apps.core.prompt_registry import PromptRegistry
        pv = PromptRegistry.version_for(self.agent_type) or ""
        agent_run.prompt_version = pv
        if not pv:
            logger.warning(
                "No prompt version available for agent %s -- prompt_version stored as empty",
                self.agent_type,
            )
        agent_run.save(update_fields=["prompt_version"])

        # Resolve actor for tool-level authorization
        self._actor_user = self._resolve_actor(ctx)
        start = time.monotonic()
        step_counter = 0
        timeout_s = (
            agent_def.timeout_seconds
            if agent_def and agent_def.timeout_seconds
            else AGENT_TIMEOUT_SECONDS
        )
        max_retries = (
            agent_def.max_retries
            if agent_def and agent_def.max_retries is not None
            else AGENT_MAX_RETRIES
        )

        try:
            messages = self._init_messages(ctx, agent_run)
            tool_specs = ToolRegistry.get_specs(self.allowed_tools)

            failed_tool_count = 0
            total_tool_calls = 0
            _called_tool_names: List[str] = []
            # Grounding cap flag -- reset each run; set inside the two exit branches.
            _grounding_cap_active = False
            for round_idx in range(MAX_TOOL_ROUNDS):
                # Deadline check before each LLM call
                if self._elapsed_seconds(start) > timeout_s:
                    raise TimeoutError(
                        f"Agent {self.agent_type} exceeded timeout of {timeout_s}s after "
                        f"{step_counter} steps"
                    )
                # LLM call (with retry on transient errors)
                step_counter += 1
                response_format = {"type": "json_object"} if self.enforce_json_response else None
                llm_resp = self._call_llm_with_retry(
                    self.llm,
                    [
                        LLMMessage(
                            role=m["role"],
                            content=m["content"],
                            tool_call_id=m.get("tool_call_id"),
                            name=m.get("name"),
                            tool_calls=m.get("tool_calls"),
                        )
                        for m in messages
                    ],
                    tool_specs if tool_specs else None,
                    response_format=response_format,
                    max_retries=max_retries,
                )

                # Track token usage
                agent_run.prompt_tokens = (agent_run.prompt_tokens or 0) + llm_resp.prompt_tokens
                agent_run.completion_tokens = (agent_run.completion_tokens or 0) + llm_resp.completion_tokens
                agent_run.total_tokens = (agent_run.total_tokens or 0) + llm_resp.total_tokens

                # Log assistant message
                self._save_message(agent_run, "assistant", llm_resp.content or "", len(messages))

                # If no tool calls, we're done
                if not llm_resp.tool_calls:
                    # Check 1: Catalog tool grounding -- flag if grounding required but no tools called.
                    if agent_def and agent_def.requires_tool_grounding and total_tool_calls == 0:
                        logger.warning(
                            "Agent %s: requires_tool_grounding=True but no tools were called. "
                            "Capping confidence at 0.4.",
                            self.agent_type,
                        )
                        _grounding_cap_active = True
                    else:
                        _grounding_cap_active = False
                    output = self.interpret_response(llm_resp.content or "", ctx)
                    # Override tools_used from runtime tracking (authoritative over LLM-reported).
                    if _called_tool_names:
                        output.tools_used = list(_called_tool_names)
                    # Replace the simple penalty block with composite confidence.
                    composite = self._compute_composite_confidence(
                        llm_confidence=output.confidence,
                        failed_tool_count=failed_tool_count,
                        total_tool_calls=total_tool_calls,
                        evidence=output.evidence or {},
                    )
                    if composite != output.confidence:
                        logger.info(
                            "Agent %s: composite confidence %.2f (llm=%.2f tools=%d/%d evidence=%s)",
                            self.agent_type, composite, output.confidence,
                            total_tool_calls - failed_tool_count, total_tool_calls,
                            bool(output.evidence),
                        )
                    output.confidence = composite
                    # Apply grounding cap (Check 1).
                    if _grounding_cap_active:
                        output.confidence = min(output.confidence, 0.4)
                    # Check 2: Catalog-defined confidence cap on tool failure.
                    if (
                        agent_def
                        and agent_def.tool_failure_confidence_cap is not None
                        and failed_tool_count > 0
                    ):
                        cap = float(agent_def.tool_failure_confidence_cap)
                        if output.confidence > cap:
                            logger.info(
                                "Agent %s: applying catalog tool_failure_confidence_cap=%.2f "
                                "(composite was %.2f)",
                                self.agent_type, cap, output.confidence,
                            )
                            output.confidence = cap
                    output = self._apply_tool_failure_guards(
                        output, failed_tool_count, total_tool_calls
                    )
                    # Enforce output-level evidence after guards are applied.
                    if not output.evidence:
                        output.evidence = {"_provenance": "no_evidence_supplied"}
                        output.confidence = min(output.confidence, 0.5)
                        logger.warning(
                            "Agent %s returned no evidence in output -- confidence capped at 0.5",
                            self.agent_type,
                        )
                    self._finalise_run(agent_run, output, start, agent_def=agent_def)
                    return agent_run

                # Process tool calls — include tool_calls on the assistant msg
                # and tool_call_id on each tool response (required by OpenAI API)
                messages.append({
                    "role": "assistant",
                    "content": llm_resp.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in llm_resp.tool_calls
                    ],
                })
                for tc in llm_resp.tool_calls:
                    step_counter += 1
                    tool_result = self._execute_tool(tc.name, tc.arguments, agent_run, step_counter)
                    if not tool_result.success:
                        failed_tool_count += 1
                    total_tool_calls += 1
                    _called_tool_names.append(tc.name)
                    tool_msg = json.dumps(tool_result.data if tool_result.success else {"error": tool_result.error})
                    messages.append({"role": "tool", "content": tool_msg, "tool_call_id": tc.id, "name": tc.name})
                    self._save_message(agent_run, "tool", tool_msg, len(messages), name=tc.name)

            # Exhausted rounds -- use last content
            # Check 1: Catalog tool grounding -- flag if grounding required but no tools called.
            if agent_def and agent_def.requires_tool_grounding and total_tool_calls == 0:
                logger.warning(
                    "Agent %s: requires_tool_grounding=True but no tools were called. "
                    "Capping confidence at 0.4.",
                    self.agent_type,
                )
                _grounding_cap_active = True
            else:
                _grounding_cap_active = False
            output = self.interpret_response(llm_resp.content or "", ctx)
            # Override tools_used from runtime tracking (authoritative over LLM-reported).
            if _called_tool_names:
                output.tools_used = list(_called_tool_names)
            # Replace the simple penalty block with composite confidence.
            composite = self._compute_composite_confidence(
                llm_confidence=output.confidence,
                failed_tool_count=failed_tool_count,
                total_tool_calls=total_tool_calls,
                evidence=output.evidence or {},
            )
            if composite != output.confidence:
                logger.info(
                    "Agent %s: composite confidence %.2f (llm=%.2f tools=%d/%d evidence=%s)",
                    self.agent_type, composite, output.confidence,
                    total_tool_calls - failed_tool_count, total_tool_calls,
                    bool(output.evidence),
                )
            output.confidence = composite
            # Apply grounding cap (Check 1).
            if _grounding_cap_active:
                output.confidence = min(output.confidence, 0.4)
            # Check 2: Catalog-defined confidence cap on tool failure.
            if (
                agent_def
                and agent_def.tool_failure_confidence_cap is not None
                and failed_tool_count > 0
            ):
                cap = float(agent_def.tool_failure_confidence_cap)
                if output.confidence > cap:
                    logger.info(
                        "Agent %s: applying catalog tool_failure_confidence_cap=%.2f "
                        "(composite was %.2f)",
                        self.agent_type, cap, output.confidence,
                    )
                    output.confidence = cap
            output = self._apply_tool_failure_guards(
                output, failed_tool_count, total_tool_calls
            )
            # Enforce output-level evidence after guards are applied.
            if not output.evidence:
                output.evidence = {"_provenance": "no_evidence_supplied"}
                output.confidence = min(output.confidence, 0.5)
                logger.warning(
                    "Agent %s returned no evidence in output -- confidence capped at 0.5",
                    self.agent_type,
                )
            self._finalise_run(agent_run, output, start, agent_def=agent_def)

        except Exception as exc:
            rr_pk = ctx.reconciliation_result.pk if ctx.reconciliation_result else None
            logger.exception("Agent %s failed for result %s", self.agent_type, rr_pk)
            agent_run.status = AgentRunStatus.FAILED
            agent_run.error_message = str(exc)[:2000]
            agent_run.duration_ms = int((time.monotonic() - start) * 1000)
            agent_run.completed_at = timezone.now()
            agent_run.save()

        return agent_run

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _init_messages(self, ctx: AgentContext, agent_run: AgentRun) -> List[Dict[str, str]]:
        sys_msg = self.system_prompt
        user_msg = self.build_user_message(ctx)
        self._save_message(agent_run, "system", sys_msg, 0)
        self._save_message(agent_run, "user", user_msg, 1)
        return [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ]

    def _execute_tool(
        self, tool_name: str, arguments: Dict[str, Any], agent_run: AgentRun, step: int
    ) -> ToolResult:
        tool = ToolRegistry.get(tool_name)
        if not tool:
            result = ToolResult(success=False, error=f"Unknown tool: {tool_name}")
        else:
            # RBAC check: authorize tool invocation via guardrails
            actor = getattr(self, "_actor_user", None)
            if actor:
                from apps.agents.services.guardrails_service import AgentGuardrailsService
                if not AgentGuardrailsService.authorize_tool(actor, tool_name):
                    perm = tool.required_permission or "unknown"
                    AgentGuardrailsService.log_guardrail_decision(
                        user=actor,
                        action=f"tool_call:{tool_name}",
                        permission_code=perm,
                        granted=False,
                        entity_type="AgentRun",
                        entity_id=agent_run.pk,
                    )
                    result = ToolResult(
                        success=False,
                        error=f"Permission denied for tool '{tool_name}' (requires {perm})",
                    )
                    ToolCallLogger.log(agent_run, tool_name, arguments, result)
                    AgentStep.objects.create(
                        agent_run=agent_run,
                        step_number=step,
                        action=f"tool_call:{tool_name}:denied",
                        input_data=arguments,
                        output_data={"error": result.error, "permission_denied": True},
                        success=False,
                    )
                    return result

            result = tool.execute(**arguments)

        # Audit log
        ToolCallLogger.log(agent_run, tool_name, arguments, result)
        AgentStep.objects.create(
            agent_run=agent_run,
            step_number=step,
            action=f"tool_call:{tool_name}",
            input_data=arguments,
            output_data=result.data if result.success else {"error": result.error},
            success=result.success,
            duration_ms=result.duration_ms,
        )
        return result

    def _finalise_run(self, agent_run: AgentRun, output: AgentOutput, start: float, agent_def=None) -> None:
        # Check 4: Apply catalog default fallback when output has no recommendation.
        if (
            output.recommendation_type is None
            and agent_def
            and agent_def.default_fallback_recommendation
        ):
            logger.info(
                "Agent %s: recommendation_type is None, applying catalog fallback '%s'",
                self.agent_type, agent_def.default_fallback_recommendation,
            )
            output.recommendation_type = agent_def.default_fallback_recommendation

        # Check 3: Validate recommendation_type against the catalog allowed list.
        allowed = None
        if agent_def and agent_def.allowed_recommendation_types:
            allowed = list(agent_def.allowed_recommendation_types)
        if allowed and output.recommendation_type and output.recommendation_type not in allowed:
            logger.warning(
                "Agent %s: recommendation_type '%s' not in allowed list %s. "
                "Falling back to default_fallback_recommendation.",
                self.agent_type, output.recommendation_type, allowed,
            )
            fallback = (
                agent_def.default_fallback_recommendation
                if agent_def and agent_def.default_fallback_recommendation
                else "SEND_TO_AP_REVIEW"
            )
            output.recommendation_type = fallback
            output.confidence = min(output.confidence, 0.6)

        # Check 5: Reject recommendations that are explicitly prohibited in the catalog.
        if (
            agent_def
            and agent_def.prohibited_actions
            and output.recommendation_type in agent_def.prohibited_actions
        ):
            logger.warning(
                "Agent %s: recommendation_type '%s' is in prohibited_actions. "
                "Overriding with fallback.",
                self.agent_type, output.recommendation_type,
            )
            fallback = (
                agent_def.default_fallback_recommendation
                or "SEND_TO_AP_REVIEW"
            )
            output.recommendation_type = fallback
            output.confidence = min(output.confidence, 0.5)

        # Phase 2: Normalise required evidence structure keys (_tools_used, _grounding,
        # _uncertainties). Non-destructive -- existing LLM-supplied values are preserved.
        self._enforce_evidence_keys(output)

        # Phase 6: Guard reasoning quality. Replace vague/empty reasoning with a
        # minimal factual fallback derived from the structured output fields.
        output.reasoning = self._guard_reasoning_quality(output, self.agent_type)

        agent_run.status = AgentRunStatus.COMPLETED
        agent_run.completed_at = timezone.now()
        agent_run.duration_ms = int((time.monotonic() - start) * 1000)
        agent_run.output_payload = {
            "reasoning": output.reasoning,
            "recommendation_type": output.recommendation_type,
            "confidence": output.confidence,
            "evidence": output.evidence,
            "tools_used": output.tools_used,
        }
        agent_run.summarized_reasoning = self._sanitise_text(output.reasoning)[:2000]
        agent_run.confidence = output.confidence
        agent_run.save()

        # Persist decisions
        for d in output.decisions:
            evidence = d.get("evidence") or {}
            decision_conf = d.get("confidence")

            # Downgrade confidence and flag if no evidence is attached.
            if not evidence:
                if decision_conf is not None:
                    decision_conf = min(float(decision_conf), 0.5)
                logger.warning(
                    "Agent %s produced decision with no evidence_refs: '%s'",
                    self.agent_type,
                    str(d.get("decision", ""))[:100],
                )
                evidence = {"_provenance": "no_evidence_supplied"}

            DecisionLog.objects.create(
                agent_run=agent_run,
                decision=d.get("decision", "")[:500],
                rationale=d.get("rationale", ""),
                confidence=decision_conf,
                evidence_refs=evidence,
            )

    @staticmethod
    def _sanitise_text(text: str) -> str:
        """Replace common Unicode characters with ASCII equivalents and
        strip any remaining non-ASCII characters."""
        replacements = {
            "\u2018": "'",   # left single curly quote
            "\u2019": "'",   # right single curly quote
            "\u201c": '"',   # left double curly quote
            "\u201d": '"',   # right double curly quote
            "\u2014": "--",  # em-dash
            "\u2013": "-",   # en-dash
            "\u2026": "...", # horizontal ellipsis
            "\u2192": "->",  # right arrow
            "\u2190": "<-",  # left arrow
            "\u21d2": "=>",  # double right arrow
            "\u2022": "-",   # bullet point
        }
        for char, ascii_eq in replacements.items():
            text = text.replace(char, ascii_eq)
        return re.sub(r"[^\x00-\x7F]", "", text)

    @staticmethod
    def _enforce_evidence_keys(output: "AgentOutput") -> None:
        """Normalise the three required evidence keys for all agent outputs.

        Adds _tools_used, _grounding, and _uncertainties to the evidence dict
        if they are absent. Existing LLM-supplied values are never overwritten.

        - _tools_used  : copied from runtime-tracked output.tools_used
        - _grounding   : "full" when tools were called, "partial" when evidence
                         has substantive non-underscore keys but no tool calls,
                         "none" otherwise. "none" triggers an additional
                         confidence cap to signal weak grounding.
        - _uncertainties: defaults to [] (empty list = no unresolved questions).

        If any key was added automatically, a "_evidence_keys_auto_added" marker
        is stored so the audit trail can surface LLM omissions.
        """
        if not isinstance(output.evidence, dict):
            output.evidence = {}

        keys_added = []

        # _tools_used -- authoritative from runtime tracking
        if "_tools_used" not in output.evidence:
            output.evidence["_tools_used"] = list(output.tools_used or [])
            keys_added.append("_tools_used")

        # _grounding -- derived from tool call history and evidence content
        if "_grounding" not in output.evidence:
            if output.tools_used:
                grounding = "full"
            elif any(not k.startswith("_") for k in output.evidence):
                grounding = "partial"
            else:
                grounding = "none"
            output.evidence["_grounding"] = grounding
            keys_added.append("_grounding")

        # _uncertainties -- default to empty list
        if "_uncertainties" not in output.evidence:
            output.evidence["_uncertainties"] = []
            keys_added.append("_uncertainties")

        if keys_added:
            output.evidence.setdefault("_evidence_keys_auto_added", keys_added)
            # If grounding is "none", confidence signals weak reliability
            if output.evidence.get("_grounding") == "none":
                output.confidence = min(output.confidence, 0.5)
                logger.debug(
                    "Evidence grounding=none: confidence capped at 0.5 "
                    "(auto-added keys: %s)",
                    keys_added,
                )

    @staticmethod
    def _guard_reasoning_quality(output: "AgentOutput", agent_type: str) -> str:
        """Check reasoning quality and return a safe factual fallback if too weak.

        Heuristics (simple, deterministic -- no NLP required):
          1. Fewer than 40 characters -> weak.
          2. Starts with a known vague filler phrase AND contains no
             domain-specific marker word -> weak.

        If weak, derives a minimal factual summary from the structured output
        fields (recommendation, confidence, evidence keys, tools used).
        The fallback is always ASCII-safe and kept under 500 characters.
        """
        _VAGUE_OPENERS = (
            "based on analysis",
            "upon review",
            "the data suggests",
            "based on the available",
            "after reviewing",
            "upon analysis",
            "the analysis shows",
            "based on my analysis",
        )
        _SPECIFICITY_MARKERS = (
            "invoice", "po", "grn", "vendor", "amount", "total", "exception",
            "difference", "match", "quantity", "price", "tax", "confidence",
            "number", "line item",
        )

        reasoning = (output.reasoning or "").strip()

        is_weak = False
        if len(reasoning) < 40:
            is_weak = True
        else:
            lower = reasoning.lower()
            is_vague_opener = any(lower.startswith(p) for p in _VAGUE_OPENERS)
            has_specifics = any(m in lower for m in _SPECIFICITY_MARKERS)
            if is_vague_opener and not has_specifics:
                is_weak = True

        if not is_weak:
            return reasoning

        # Build a safe factual fallback from structured output
        parts = []
        if output.recommendation_type:
            parts.append("Recommendation: " + output.recommendation_type)
        conf_pct = int(output.confidence * 100)
        parts.append("Confidence: " + str(conf_pct) + "%")

        ev = output.evidence or {}
        specifics = []
        for key in (
            "invoice_number", "po_number", "vendor", "total_amount",
            "match_status", "exception_type", "found_po", "grn_count",
        ):
            val = ev.get(key)
            if val not in (None, "", [], {}):
                specifics.append(key + "=" + str(val))
        if specifics:
            parts.append("Evidence: " + ", ".join(specifics[:4]))

        if output.tools_used:
            parts.append("Tools called: " + ", ".join(output.tools_used))

        fallback = "[auto-summary agent=" + agent_type + "] " + ". ".join(parts) + "."
        logger.warning(
            "Agent %s produced weak reasoning (%d chars, original='%s...'); "
            "replaced with auto-summary.",
            agent_type, len(reasoning or ""), (reasoning or "")[:60],
        )
        return fallback[:500]

    @staticmethod
    def _truncate_exceptions(
        exceptions: list,
        max_exceptions: int = 20,
    ) -> list:
        """Return a severity- and recency-ordered subset of exceptions.

        Keeps HIGH severity first, then MEDIUM, then LOW. Within each band,
        preserves the original order (most recent first from the DB query).
        If the list is within max_exceptions it is returned unchanged.

        Args:
            exceptions: List of exception dicts from the DB values() query.
            max_exceptions: Hard upper limit. Default 20 covers typical GPT-4o
                            context limits without truncation risk.
        Returns:
            Possibly shortened list, highest priority exceptions first.
        """
        if len(exceptions) <= max_exceptions:
            return exceptions

        _SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
        sorted_excs = sorted(
            exceptions,
            key=lambda e: _SEVERITY_ORDER.get(str(e.get("severity", "LOW")).upper(), 4),
        )
        truncated = sorted_excs[:max_exceptions]
        logger.warning(
            "Exception list truncated from %d to %d for token budget "
            "(dropped %d LOW/INFO exceptions)",
            len(exceptions),
            max_exceptions,
            len(exceptions) - max_exceptions,
        )
        return truncated

    @staticmethod
    def _save_message(
        agent_run: AgentRun, role: str, content: str, index: int, name: str = ""
    ) -> AgentMessage:
        return AgentMessage.objects.create(
            agent_run=agent_run,
            role=role,
            content=content,
            message_index=index,
        )

    @staticmethod
    def _serialise_context(ctx: AgentContext) -> dict:
        return {
            "reconciliation_result_id": ctx.reconciliation_result.pk if ctx.reconciliation_result else None,
            "invoice_id": ctx.invoice_id,
            "po_number": ctx.po_number,
            "exception_count": len(ctx.exceptions),
            "reconciliation_mode": ctx.reconciliation_mode,
            "actor_user_id": ctx.actor_user_id,
            "actor_primary_role": ctx.actor_primary_role,
            "permission_checked": ctx.permission_checked,
        }

    @staticmethod
    def _elapsed_seconds(start: float) -> float:
        return time.monotonic() - start

    @staticmethod
    def _call_llm_with_retry(llm, messages, tools, max_retries=3, base_delay=2, response_format=None):
        """Call llm.chat() with exponential-backoff retry on transient OpenAI errors.

        Retries on: RateLimitError, APIConnectionError, InternalServerError.
        All other exceptions (AuthenticationError, BadRequestError, etc.) propagate
        immediately without retry.
        """
        import time as _time
        import openai
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return llm.chat(
                    messages=messages,
                    tools=tools if tools else None,
                    response_format=response_format,
                )
            except (
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ) as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "LLM transient error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, max_retries, delay, exc,
                    )
                    _time.sleep(delay)
        raise last_exc

    def _apply_tool_failure_guards(
        self, output: "AgentOutput", failed_tool_count: int, total_tool_calls: int
    ) -> "AgentOutput":
        """Centralised runtime safety enforcement based on tool call results.

        Rules enforced (in priority order):
        1. If any tools FAILED: cap confidence at 0.5.  AUTO_CLOSE is also
           downgraded to SEND_TO_AP_REVIEW because a failed tool path must
           never produce a high-confidence close action.  Stricter routing
           (e.g. ESCALATE_TO_MANAGER) is preserved -- only confidence is capped.
        2. If the agent is tool-grounded (expected to call at least one tool)
           but called NO tools at all: cap confidence at 0.6 and record a
           provenance marker so downstream audit is clear.

        Deterministic agents that never go through BaseAgent.run() are
        unaffected by this method.
        """
        if failed_tool_count > 0:
            output.confidence = min(output.confidence, 0.5)
            if output.recommendation_type == "AUTO_CLOSE":
                output.recommendation_type = "SEND_TO_AP_REVIEW"
                logger.warning(
                    "Agent %s: AUTO_CLOSE downgraded to SEND_TO_AP_REVIEW due to %d tool failure(s); "
                    "confidence capped at 0.5",
                    self.agent_type, failed_tool_count,
                )
            else:
                logger.warning(
                    "Agent %s: confidence capped at 0.5 due to %d tool failure(s) "
                    "(recommendation=%s preserved)",
                    self.agent_type, failed_tool_count, output.recommendation_type,
                )
            if not output.evidence:
                output.evidence = {"_provenance": "tool_failures"}
            else:
                output.evidence.setdefault("_provenance", "tool_failures_partial")

        if self.agent_type in _TOOL_GROUNDED_AGENT_TYPES and total_tool_calls == 0:
            output.confidence = min(output.confidence, 0.6)
            if not output.evidence:
                output.evidence = {"_provenance": "no_tools_called"}
            else:
                output.evidence.setdefault("_provenance", "no_tools_called")
            logger.warning(
                "Agent %s is tool-grounded but called no tools -- confidence capped at 0.6",
                self.agent_type,
            )

        return output

    @staticmethod
    def _compute_composite_confidence(
        llm_confidence: float,
        failed_tool_count: int,
        total_tool_calls: int,
        evidence: dict,
    ) -> float:
        """Blend LLM confidence with tool-success and evidence scores.

        Formula:
            tool_score    = 1.0 if no tools called, else (successes / total)
            evidence_score = 0.5 if evidence is empty or only has _provenance key,
                             else 1.0
            composite = (llm_confidence * 0.6) + (tool_score * 0.25) + (evidence_score * 0.15)

        Clamped to [0.0, 1.0].
        """
        # Tool success score
        if total_tool_calls == 0:
            tool_score = 1.0
        else:
            successful = max(0, total_tool_calls - failed_tool_count)
            tool_score = successful / total_tool_calls

        # Evidence quality score
        if not evidence or list(evidence.keys()) == ["_provenance"]:
            evidence_score = 0.5
        else:
            evidence_score = 1.0

        composite = (
            float(llm_confidence) * 0.6
            + tool_score * 0.25
            + evidence_score * 0.15
        )
        return max(0.0, min(1.0, composite))

    @staticmethod
    def _resolve_actor(ctx: AgentContext):
        """Resolve actor user from context for tool authorization."""
        if ctx.actor_user_id:
            from apps.accounts.models import User
            return User.objects.filter(pk=ctx.actor_user_id).first()
        return None
