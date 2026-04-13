"""BenchmarkAgent
=================
Lightweight benchmark resolver used by BenchmarkService when AI-assisted
resolution is enabled.

This module intentionally keeps logic deterministic-first and fail-safe so
benchmark flows never hard-fail if external intelligence is unavailable.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict


class BenchmarkAgent:
    """Resolve indicative benchmark pricing for a quotation line item.

    Notes:
    - The service calling this agent already wraps execution in the procurement
      orchestrator and applies governance/audit hooks.
    - This agent returns a simple normalized payload that can be merged with
      deterministic and web-search fallback sources.
    """

    @staticmethod
    def resolve_benchmark_for_item(item: Any) -> Dict[str, Any]:
        """Return min/avg/max benchmark estimate for one line item.

        Current behavior is intentionally conservative:
        1. Use the quoted unit rate as baseline.
        2. Build a symmetric +/-10% envelope.
        3. Mark source as ai_estimate so downstream evidence is explicit.
        """
        unit_rate = item.unit_rate
        if unit_rate is None:
            return {
                "min": None,
                "avg": None,
                "max": None,
                "source": "ai_estimate_unavailable",
            }

        avg = Decimal(unit_rate)
        spread = (avg * Decimal("0.10")).quantize(Decimal("0.01"))
        return {
            "min": (avg - spread).quantize(Decimal("0.01")),
            "avg": avg.quantize(Decimal("0.01")),
            "max": (avg + spread).quantize(Decimal("0.01")),
            "source": "ai_estimate",
        }
