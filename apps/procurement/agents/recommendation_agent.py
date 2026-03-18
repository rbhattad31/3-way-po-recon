"""RecommendationAgent — AI-powered product/solution recommendation."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.procurement.models import ProcurementRequest

logger = logging.getLogger(__name__)


class RecommendationAgent:
    """Lightweight agent for generating product/solution recommendations.

    Called only when deterministic rules are insufficient.
    Uses a simple prompt → response pattern (no tool-calling loop needed).
    """

    SYSTEM_PROMPT = (
        "You are a procurement intelligence assistant. Given a set of requirements "
        "and domain context, recommend the best product or solution.\n\n"
        "Respond ONLY with valid JSON in this format:\n"
        "{\n"
        '  "recommended_option": "...",\n'
        '  "reasoning_summary": "...",\n'
        '  "reasoning_details": { ... },\n'
        '  "confidence": 0.0-1.0,\n'
        '  "constraints": [...],\n'
        '  "confident": true/false\n'
        "}\n\n"
        "Be specific, practical, and concise. Base recommendations on the provided attributes."
    )

    @staticmethod
    def execute(
        request: ProcurementRequest,
        attributes: Dict[str, Any],
        rule_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run AI recommendation and return structured result dict."""
        llm = LLMClient()

        user_msg = (
            f"Domain: {request.domain_code}\n"
            f"Title: {request.title}\n"
            f"Description: {request.description}\n"
            f"Geography: {request.geography_country}, {request.geography_city}\n"
            f"Currency: {request.currency}\n\n"
            f"Requirements:\n{json.dumps(attributes, indent=2, default=str)}\n\n"
            f"Rule engine result: {json.dumps(rule_result, indent=2, default=str)}\n\n"
            "Provide your recommendation."
        )

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=RecommendationAgent.SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ],
            )
            return json.loads(response.content)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("RecommendationAgent LLM call failed: %s", exc)
            return {
                "recommended_option": "",
                "reasoning_summary": f"AI analysis failed: {exc}",
                "confident": False,
                "confidence": 0.0,
            }
