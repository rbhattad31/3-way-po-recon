"""Supervisor output interpreter -- parse and validate LLM response."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from apps.agents.services.base_agent import AgentOutput

logger = logging.getLogger(__name__)


def parse_supervisor_response(content: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from LLM response content.

    Handles markdown fences and partial JSON.
    """
    content = (content or "").strip()
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
        # Try to find JSON object in content
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


def interpret_supervisor_output(
    content: str,
    tool_results: Optional[Dict[str, Any]] = None,
) -> AgentOutput:
    """Parse supervisor LLM response into a structured AgentOutput.

    Applies validation and defaults for missing fields.

    Args:
        content: Raw LLM response content.
        tool_results: Aggregated tool call results (for evidence enrichment).

    Returns:
        Validated AgentOutput instance.
    """
    data = parse_supervisor_response(content)

    # Validate through the existing output schema
    try:
        from apps.agents.services.agent_output_schema import AgentOutputSchema
        validated = AgentOutputSchema.model_validate(data)
        output = AgentOutput(
            reasoning=validated.reasoning or content[:500],
            recommendation_type=validated.recommendation_type,
            confidence=validated.confidence,
            evidence=validated.evidence,
            decisions=[d.model_dump() for d in validated.decisions],
            tools_used=validated.tools_used,
            raw_content=content,
        )
    except Exception as exc:
        logger.warning(
            "Supervisor output schema validation failed (%s) -- using defaults", exc
        )
        output = AgentOutput(
            reasoning=data.get("reasoning", content[:500]) if data else content[:500],
            recommendation_type=data.get("recommendation_type", "SEND_TO_AP_REVIEW"),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.3)))) if data else 0.3,
            evidence=data.get("evidence", {}) if data else {},
            decisions=data.get("decisions", []) if data else [],
            tools_used=data.get("tools_used", []) if data else [],
            raw_content=content,
        )

    # Enrich evidence with case_summary if present
    case_summary = data.get("case_summary", "")
    if case_summary and isinstance(output.evidence, dict):
        output.evidence["case_summary"] = case_summary[:2000]

    # Enrich with tool results if provided
    if tool_results and isinstance(output.evidence, dict):
        output.evidence["_supervisor_tool_results"] = tool_results

    # Enforce: recommendation must be present
    if not output.recommendation_type:
        output.recommendation_type = "SEND_TO_AP_REVIEW"
        output.confidence = min(output.confidence, 0.3)
        logger.warning("Supervisor produced no recommendation -- defaulting to SEND_TO_AP_REVIEW")

    return output


def extract_recommendation_from_tools(tool_calls: list) -> Optional[Dict[str, Any]]:
    """Extract the submit_recommendation call data from tool call history.

    The supervisor is required to call submit_recommendation before finishing.
    This extracts that data for persistence.
    """
    for call in reversed(tool_calls or []):
        if isinstance(call, dict) and call.get("tool_name") == "submit_recommendation":
            result = call.get("result", {})
            if isinstance(result, dict) and result.get("submitted"):
                return {
                    "recommendation_type": result.get("recommendation_type", ""),
                    "confidence": float(result.get("confidence", 0)),
                    "reasoning": result.get("reasoning", ""),
                }
    return None
