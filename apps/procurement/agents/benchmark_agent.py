"""BenchmarkAgent — AI-powered should-cost benchmark resolution."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.procurement.models import QuotationLineItem

logger = logging.getLogger(__name__)


class BenchmarkAgent:
    """Lightweight agent for resolving benchmark price ranges.

    Called per line item when no deterministic benchmark data is available.
    """

    SYSTEM_PROMPT = (
        "You are a procurement cost analyst. Given a line item description, category, "
        "and quantity, estimate a market benchmark price range.\n\n"
        "Respond ONLY with valid JSON:\n"
        "{\n"
        '  "min": <number or null>,\n'
        '  "avg": <number or null>,\n'
        '  "max": <number or null>,\n'
        '  "source": "ai_estimate",\n'
        '  "reasoning": "..."\n'
        "}\n\n"
        "If you cannot estimate, return nulls with an explanation in reasoning. "
        "Prices should be in the same currency as the quoted value."
    )

    @staticmethod
    def resolve_benchmark_for_item(item: QuotationLineItem) -> Dict[str, Any]:
        """Resolve benchmark data for a single quotation line item."""
        llm = LLMClient()

        user_msg = (
            f"Item: {item.description}\n"
            f"Normalized: {item.normalized_description}\n"
            f"Category: {item.category_code}\n"
            f"Brand: {item.brand}\n"
            f"Model: {item.model}\n"
            f"Quantity: {item.quantity} {item.unit}\n"
            f"Quoted unit rate: {item.unit_rate}\n"
            f"Currency: {item.quotation.currency}\n\n"
            "Provide your benchmark estimate."
        )

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(role="system", content=BenchmarkAgent.SYSTEM_PROMPT),
                    LLMMessage(role="user", content=user_msg),
                ],
            )
            result = json.loads(response.content)
            # Convert to Decimal-friendly values
            for key in ("min", "avg", "max"):
                if result.get(key) is not None:
                    result[key] = float(result[key])
            result["source"] = "ai_estimate"
            return result
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("BenchmarkAgent failed for line %s: %s", item.pk, exc)
            return {"min": None, "avg": None, "max": None, "source": "error", "reasoning": str(exc)}
