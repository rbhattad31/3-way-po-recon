"""ValidationAgentService — lightweight LLM augmentation for ambiguity resolution.

This agent is only invoked when deterministic validation identifies:
- High ambiguity count (>= threshold)
- Bundled scope descriptions needing clarification
- Uncertain category mappings

It does NOT replace deterministic checks — it augments them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from apps.agents.services.base_agent import BaseAgent
from apps.core.enums import ValidationItemStatus, ValidationSeverity, ValidationSourceType
from apps.procurement.models import AnalysisRun, ProcurementRequest

logger = logging.getLogger(__name__)


class ValidationAgentService:
    """Invoke the LLM to resolve ambiguities and generate explanations."""

    @staticmethod
    def augment_findings(
        request: ProcurementRequest,
        run: AnalysisRun,
        findings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Augment deterministic findings with LLM analysis for ambiguous items.

        - Sends ambiguous items to the LLM for classification
        - Updates findings with agent-resolved statuses
        - Adds explanation text
        - Logs agent usage to AgentRun/AgentStep

        Returns the updated findings list.
        """
        from apps.agents.models import AgentRun as AgentRunModel, AgentStep
        from apps.agents.services.llm_client import LLMClient, LLMMessage
        from apps.core.enums import AgentRunStatus, AgentType
        from apps.core.trace import TraceContext

        ambiguous_items = [f for f in findings if f["status"] == ValidationItemStatus.AMBIGUOUS]
        if not ambiguous_items:
            return findings

        ctx = TraceContext.get_current()

        # Create AgentRun for traceability
        agent_run = AgentRunModel.objects.create(
            tenant=getattr(request, "tenant", None) or getattr(run, "tenant", None),
            agent_type=AgentType.PROCUREMENT_VALIDATION,
            status=AgentRunStatus.RUNNING,
            input_payload={
                "request_id": str(request.request_id),
                "analysis_run_id": str(getattr(run, "run_id", "")),
                "ambiguous_count": len(ambiguous_items),
            },
            trace_id=ctx.trace_id if ctx else "",
            invocation_reason="Ambiguity resolution for validation",
            actor_user_id=getattr(run, "triggered_by_id", None),
            started_at=run.started_at,
        )

        try:
            client = LLMClient()

            prompt = _build_prompt(request, ambiguous_items)
            messages = [
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(role="user", content=prompt),
            ]

            response = client.chat(messages=messages)

            # Parse response
            resolved = _parse_response(response.content)

            # Update findings with agent resolutions
            findings = _apply_resolutions(findings, resolved)

            # Log agent step
            AgentStep.objects.create(
                agent_run=agent_run,
                step_number=1,
                action="ambiguity_resolution",
                input_data={"ambiguous_items": [i["item_code"] for i in ambiguous_items]},
                output_data={"resolved_count": len(resolved)},
                success=True,
            )

            agent_run.status = AgentRunStatus.COMPLETED
            agent_run.output_payload = {"resolved_count": len(resolved)}
            agent_run.llm_model_used = client.model
            agent_run.prompt_tokens = response.prompt_tokens
            agent_run.completion_tokens = response.completion_tokens
            agent_run.total_tokens = response.total_tokens
            BaseAgent._calculate_actual_cost(agent_run)
            agent_run.save(
                update_fields=[
                    "status",
                    "output_payload",
                    "llm_model_used",
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "actual_cost_usd",
                    "updated_at",
                ]
            )
            try:
                from apps.agents.services.eval_adapter import AgentEvalAdapter
                AgentEvalAdapter.sync_for_agent_run(agent_run)
            except Exception:
                logger.debug("ValidationAgent: AgentEvalAdapter sync failed (non-fatal)", exc_info=True)

        except Exception:
            logger.warning(
                "ValidationAgent failed for request %s",
                request.request_id,
                exc_info=True,
            )
            agent_run.status = AgentRunStatus.FAILED
            agent_run.save(update_fields=["status", "updated_at"])
            try:
                from apps.agents.services.eval_adapter import AgentEvalAdapter
                AgentEvalAdapter.sync_for_agent_run(agent_run)
            except Exception:
                logger.debug("ValidationAgent: AgentEvalAdapter sync failed after failure", exc_info=True)

        return findings


SYSTEM_PROMPT = """You are a procurement validation assistant. You analyze ambiguous or vague descriptions
in procurement requests and quotation line items. Your job is to:
1. Classify whether each ambiguous item is genuinely vague or acceptable
2. Suggest clarification if truly ambiguous
3. Provide a brief explanation

Respond with a JSON array of objects:
[
  {
    "item_code": "...",
    "resolved_status": "AMBIGUOUS" or "WARNING" or "PRESENT",
    "explanation": "Brief explanation",
    "suggested_clarification": "What to ask for, if any"
  }
]

Only return the JSON array, no other text."""


def _build_prompt(
    request: ProcurementRequest,
    ambiguous_items: List[Dict[str, Any]],
) -> str:
    """Build user prompt for the agent."""
    items_text = "\n".join(
        f"- {item['item_code']}: {item.get('remarks', '')}"
        for item in ambiguous_items
    )
    return (
        f"Procurement Request: {request.title}\n"
        f"Domain: {request.domain_code}\n"
        f"Description: {request.description[:500]}\n\n"
        f"The following items were flagged as ambiguous:\n{items_text}\n\n"
        f"Please classify each item and provide explanations."
    )


def _parse_response(content: str) -> List[Dict[str, Any]]:
    """Parse LLM JSON response."""
    try:
        # Try to extract JSON from response
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse ValidationAgent response")
        return []


def _apply_resolutions(
    findings: List[Dict[str, Any]],
    resolved: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply agent resolutions back to findings."""
    resolution_map = {r["item_code"]: r for r in resolved if "item_code" in r}

    for finding in findings:
        if finding["item_code"] in resolution_map:
            res = resolution_map[finding["item_code"]]
            new_status = res.get("resolved_status", finding["status"])
            if new_status in (ValidationItemStatus.PRESENT, ValidationItemStatus.WARNING,
                              ValidationItemStatus.AMBIGUOUS):
                finding["status"] = new_status
            explanation = res.get("explanation", "")
            if explanation:
                safe_explanation = BaseAgent._sanitise_text(str(explanation))
                finding["remarks"] = f"{finding.get('remarks', '')} [Agent: {safe_explanation}]"
            finding["source_type"] = ValidationSourceType.AGENT

    return findings
