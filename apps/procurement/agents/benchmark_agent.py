"""BenchmarkAgent — ReAct agent with web-search tools for market research.

Uses a tool-calling loop to gather real pricing data from OEM catalogues,
GCC market sources, commodity indices, and compliance references before
synthesising a benchmark price range.  This replaces the earlier single-shot
approach that suffered from anchoring bias (LLM echoing the quoted price).

Key design decisions:
  • Quoted price is deliberately withheld during the research phase so the
    LLM forms an independent market view.
  • The agent picks tools dynamically based on item category (equipment →
    OEM + GCC, materials → commodity, etc.).
  • Final synthesis returns min/avg/max, source citations, confidence, and
    reasoning — all persisted to ``BenchmarkResultLine``.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from apps.agents.services.llm_client import LLMClient, LLMMessage, ToolSpec
from apps.procurement.models import QuotationLineItem
from apps.tools.registry.base import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4  # Cap: 4 research iterations per line item

# Tool names used by this agent — must be registered in ToolRegistry
BENCHMARK_TOOLS = [
    "oem_catalogue_search",
    "gcc_market_search",
    "commodity_reference",
    "compliance_context_search",
]


class BenchmarkAgent:
    """ReAct-style agent for resolving benchmark price ranges.

    Runs a multi-turn tool-calling loop:
      1. LLM receives item metadata (NO quoted price) + available tools
      2. LLM calls tools to gather market data
      3. Tool results are fed back to LLM
      4. LLM synthesises a final JSON benchmark (or gives up gracefully)

    Called per line item by ``BenchmarkService._resolve_benchmark()``.
    """

    SYSTEM_PROMPT = (
        "You are a procurement cost analyst specialising in MEP (Mechanical, "
        "Electrical, Plumbing) and HVAC equipment procurement in the GCC region.\n\n"
        "## Task\n"
        "Research the market price for the item described below using the search "
        "tools available to you.  You MUST use at least one tool before giving "
        "your final answer.\n\n"
        "## Tool Selection Guide\n"
        "• **oem_catalogue_search** — Use for branded equipment (VRF, chillers, "
        "AHU, FCU). Search by brand + model + category.\n"
        "• **gcc_market_search** — Use to find regional pricing context in UAE, "
        "KSA, Qatar etc.  Good for understanding local mark-ups.\n"
        "• **commodity_reference** — Use for raw materials (copper, steel, "
        "aluminium, refrigerant). Search by material + form.\n"
        "• **compliance_context_search** — Use when the item description "
        "mentions fire-rating, energy efficiency, or specific standards. "
        "Helps identify compliance-driven cost components.\n\n"
        "## Rules\n"
        "1. Do NOT guess prices — only use data you found via tools.\n"
        "2. If search results contain pricing data, extract and cite it.\n"
        "3. If no pricing data is found after research, say so honestly.\n"
        "4. Prices must be in the target currency specified.\n"
        "5. Provide min / avg / max as a range when possible.\n\n"
        "## Final Answer Format\n"
        "When you have gathered enough data, respond with ONLY valid JSON:\n"
        "```json\n"
        "{\n"
        '  "min": <number or null>,\n'
        '  "avg": <number or null>,\n'
        '  "max": <number or null>,\n'
        '  "source_type": "oem_catalogue | gcc_market | commodity_derived '
        '| blended | ai_estimate",\n'
        '  "source_urls": ["url1", "url2"],\n'
        '  "source_confidence": 0.0 to 1.0,\n'
        '  "reasoning": "Explain how you derived the range from search data"\n'
        "}\n"
        "```\n"
        "If you cannot find data, return nulls with source_type='ai_estimate' "
        "and explain in reasoning."
    )

    def __init__(self):
        self.llm = LLMClient(max_tokens=4096)

    def resolve_benchmark_for_item(self, item: QuotationLineItem) -> Dict[str, Any]:
        """Run the ReAct loop for a single line item and return benchmark data."""
        # Ensure benchmark tools are registered
        self._ensure_tools_registered()

        tool_specs = ToolRegistry.get_specs(BENCHMARK_TOOLS)
        currency = item.quotation.currency if item.quotation else "USD"

        # Build user message WITHOUT the quoted price (anti-anchoring)
        user_msg = self._build_user_message(item, currency)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        try:
            return self._react_loop(messages, tool_specs, item, currency)
        except Exception as exc:
            logger.warning("BenchmarkAgent failed for line %s: %s", item.pk, exc)
            return self._error_result(str(exc))

    # ------------------------------------------------------------------
    # ReAct loop
    # ------------------------------------------------------------------
    def _react_loop(
        self,
        messages: List[Dict[str, Any]],
        tool_specs: List[ToolSpec],
        item: QuotationLineItem,
        currency: str,
    ) -> Dict[str, Any]:
        """Execute the tool-calling loop and return the final benchmark."""
        last_content = ""

        for round_idx in range(MAX_TOOL_ROUNDS):
            llm_resp = self.llm.chat(
                messages=[
                    LLMMessage(
                        role=m["role"],
                        content=m.get("content", ""),
                        tool_call_id=m.get("tool_call_id"),
                        name=m.get("name"),
                        tool_calls=m.get("tool_calls"),
                    )
                    for m in messages
                ],
                tools=tool_specs if tool_specs else None,
            )

            last_content = llm_resp.content or ""

            # No tool calls → LLM is done, parse final answer
            if not llm_resp.tool_calls:
                return self._parse_final_answer(last_content)

            # Append assistant message with tool_calls
            messages.append({
                "role": "assistant",
                "content": last_content,
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

            # Execute each tool and append results
            for tc in llm_resp.tool_calls:
                tool_result = self._execute_tool(tc.name, tc.arguments)
                tool_msg = json.dumps(
                    tool_result.data if tool_result.success
                    else {"error": tool_result.error}
                )
                messages.append({
                    "role": "tool",
                    "content": tool_msg,
                    "tool_call_id": tc.id,
                    "name": tc.name,
                })
                logger.debug(
                    "BenchmarkAgent tool %s for item %s: success=%s",
                    tc.name, item.pk, tool_result.success,
                )

        # Exhausted rounds — try to parse whatever we have
        return self._parse_final_answer(last_content)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------
    def _execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Execute a single tool call."""
        if tool_name not in BENCHMARK_TOOLS:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not allowed")

        tool = ToolRegistry.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found")

        return tool.execute(**arguments)

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------
    @staticmethod
    def _build_user_message(item: QuotationLineItem, currency: str) -> str:
        """Build the user prompt — deliberately excludes the quoted price."""
        parts = [
            f"Research the market price for this item:",
            f"  Description: {item.description}",
        ]
        if item.normalized_description:
            parts.append(f"  Normalized: {item.normalized_description}")
        if item.category_code:
            parts.append(f"  Category: {item.category_code}")
        if item.brand:
            parts.append(f"  Brand: {item.brand}")
        if item.model:
            parts.append(f"  Model: {item.model}")
        parts.append(f"  Quantity: {item.quantity} {item.unit}")
        parts.append(f"  Target currency: {currency}")
        parts.append("")
        parts.append(
            "Use the search tools to find current market pricing data. "
            "Provide your benchmark estimate as a price range (min/avg/max) "
            "per unit in the target currency."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_final_answer(content: str) -> Dict[str, Any]:
        """Extract the JSON benchmark from the LLM's final response."""
        content = content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            # Remove opening fence (```json or ```)
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3].strip()

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON in the response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    result = json.loads(content[start:end])
                except json.JSONDecodeError:
                    return BenchmarkAgent._error_result(
                        "Could not parse LLM response as JSON"
                    )
            else:
                return BenchmarkAgent._error_result(
                    "No JSON found in LLM response"
                )

        # Convert price fields to Decimal
        for key in ("min", "avg", "max"):
            val = result.get(key)
            if val is not None:
                try:
                    result[key] = Decimal(str(val))
                except (InvalidOperation, ValueError):
                    result[key] = None

        # Normalise confidence
        conf = result.get("source_confidence")
        if conf is not None:
            try:
                result["source_confidence"] = max(0.0, min(1.0, float(conf)))
            except (ValueError, TypeError):
                result["source_confidence"] = None

        # Ensure required keys exist
        result.setdefault("source_type", "ai_estimate")
        result.setdefault("source_urls", [])
        result.setdefault("source_confidence", None)
        result.setdefault("reasoning", "")

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _error_result(reason: str) -> Dict[str, Any]:
        return {
            "min": None, "avg": None, "max": None,
            "source_type": "error",
            "source_urls": [],
            "source_confidence": None,
            "reasoning": reason,
        }

    @staticmethod
    def _ensure_tools_registered():
        """Make sure the 4 benchmark tools are in the registry."""
        if not ToolRegistry.get("oem_catalogue_search"):
            from apps.procurement.tools.benchmark_tools import register_benchmark_tools
            register_benchmark_tools()
