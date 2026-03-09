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
from apps.agents.services.llm_client import LLMClient, LLMMessage, LLMResponse
from apps.core.constants import AGENT_MAX_RETRIES, AGENT_TIMEOUT_SECONDS
from apps.core.enums import AgentRunStatus, AgentType
from apps.reconciliation.models import ReconciliationResult
from apps.tools.registry.base import ToolRegistry, ToolResult
from apps.tools.registry.tool_call_logger import ToolCallLogger

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6  # Safety cap on tool-call loops


@dataclass
class AgentContext:
    """Immutable context bag passed into an agent run."""
    reconciliation_result: ReconciliationResult
    invoice_id: int
    po_number: Optional[str] = None
    exceptions: List[Dict[str, Any]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    """The final structured output of an agent run."""
    reasoning: str = ""
    recommendation_type: Optional[str] = None
    confidence: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    raw_content: str = ""


class BaseAgent(ABC):
    """Abstract base for all reconciliation agents."""

    agent_type: str = ""  # Must match AgentType enum value

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
        )

        start = time.monotonic()
        step_counter = 0

        try:
            messages = self._init_messages(ctx, agent_run)
            tool_specs = ToolRegistry.get_specs(self.allowed_tools)

            for round_idx in range(MAX_TOOL_ROUNDS):
                # LLM call
                step_counter += 1
                llm_resp = self.llm.chat(
                    messages=[LLMMessage(role=m["role"], content=m["content"]) for m in messages],
                    tools=tool_specs if tool_specs else None,
                )

                # Track token usage
                agent_run.prompt_tokens = (agent_run.prompt_tokens or 0) + llm_resp.prompt_tokens
                agent_run.completion_tokens = (agent_run.completion_tokens or 0) + llm_resp.completion_tokens
                agent_run.total_tokens = (agent_run.total_tokens or 0) + llm_resp.total_tokens

                # Log assistant message
                self._save_message(agent_run, "assistant", llm_resp.content or "", len(messages))

                # If no tool calls, we're done
                if not llm_resp.tool_calls:
                    output = self.interpret_response(llm_resp.content or "", ctx)
                    self._finalise_run(agent_run, output, start)
                    return agent_run

                # Process tool calls
                messages.append({"role": "assistant", "content": llm_resp.content or ""})
                for tc in llm_resp.tool_calls:
                    step_counter += 1
                    tool_result = self._execute_tool(tc.name, tc.arguments, agent_run, step_counter)
                    tool_msg = json.dumps(tool_result.data if tool_result.success else {"error": tool_result.error})
                    messages.append({"role": "tool", "content": tool_msg})
                    self._save_message(agent_run, "tool", tool_msg, len(messages), name=tc.name)

            # Exhausted rounds — use last content
            output = self.interpret_response(llm_resp.content or "", ctx)
            self._finalise_run(agent_run, output, start)

        except Exception as exc:
            logger.exception("Agent %s failed for result %s", self.agent_type, ctx.reconciliation_result.pk)
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

    def _finalise_run(self, agent_run: AgentRun, output: AgentOutput, start: float) -> None:
        agent_run.status = AgentRunStatus.COMPLETED
        agent_run.completed_at = timezone.now()
        agent_run.duration_ms = int((time.monotonic() - start) * 1000)
        agent_run.output_payload = {
            "reasoning": output.reasoning,
            "recommendation_type": output.recommendation_type,
            "confidence": output.confidence,
            "evidence": output.evidence,
        }
        agent_run.summarized_reasoning = output.reasoning[:2000]
        agent_run.confidence = output.confidence
        agent_run.save()

        # Persist decisions
        for d in output.decisions:
            DecisionLog.objects.create(
                agent_run=agent_run,
                decision=d.get("decision", "")[:500],
                rationale=d.get("rationale", ""),
                confidence=d.get("confidence"),
                evidence_refs=d.get("evidence"),
            )

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
            "reconciliation_result_id": ctx.reconciliation_result.pk,
            "invoice_id": ctx.invoice_id,
            "po_number": ctx.po_number,
            "exception_count": len(ctx.exceptions),
        }
