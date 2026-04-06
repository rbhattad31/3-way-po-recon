"""HVACRecommendationAgent — optional AI reasoning for HVAC tradeoffs."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from apps.agents.services.llm_client import LLMClient, LLMMessage

logger = logging.getLogger(__name__)


class HVACRecommendationAgent:
    """Invoke AI only for tie-break/tradeoff reasoning in HVAC recommendations."""

    SYSTEM_PROMPT = (
        "You are an HVAC solution advisor for procurement pre-design. "
        "You receive deterministic recommendation output and request attributes. "
        "Return JSON with concise reasoning, tradeoffs, decision_drivers, and alternate_option."
        "\n\nReturn ONLY valid JSON:\n"
        "{\n"
        '  "reasoning_summary": "...",\n'
        '  "tradeoffs": ["..."],\n'
        '  "decision_drivers": ["..."],\n'
        '  "alternate_option": {"system_type": "...", "reason": "..."}\n'
        "}\n"
        "No markdown. No extra keys."
    )

    @staticmethod
    def explain(*, attrs: Dict[str, Any], rule_result: Dict[str, Any]) -> Dict[str, Any]:
        llm = LLMClient()
        payload = {
            "attributes": attrs,
            "rule_result": rule_result,
            "instruction": "Provide procurement-facing HVAC reasoning only."
        }

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=HVACRecommendationAgent.SYSTEM_PROMPT),
                    LLMMessage(role="user", content=json.dumps(payload, default=str)),
                ],
            )
            text = (response.content or "").strip()
            fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()

            if not text.startswith("{"):
                object_match = re.search(r"\{.*\}", text, re.DOTALL)
                if object_match:
                    text = object_match.group(0)

            parsed = json.loads(text)
            return {
                "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
                "tradeoffs": parsed.get("tradeoffs") or [],
                "decision_drivers": parsed.get("decision_drivers") or [],
                "alternate_option": parsed.get("alternate_option") or {},
                "reasoning_details": {
                    "ai_reasoning_used": True,
                    "tradeoffs": parsed.get("tradeoffs") or [],
                },
            }
        except Exception as exc:
            logger.warning("HVACRecommendationAgent failed: %s", exc)
            return {
                "reasoning_summary": "AI tradeoff reasoning unavailable; deterministic recommendation retained.",
                "tradeoffs": [],
                "decision_drivers": [],
                "alternate_option": {},
                "reasoning_details": {
                    "ai_reasoning_used": False,
                    "error": str(exc),
                },
            }
